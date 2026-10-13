"""
城堡黄金炼制任务（Golden Refine）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击 GLOBAL_BUTTON_TASKLIST 打开任务列表
  3. 在列表中检索 GLOBAL_TASK_LIST_ICON_GOLDEN：
     - 找到 → 点击（全屏匹配）GLOBAL_TASK_LIST_ICON_GOLDEN_ENTRY，
       进入黄金炼制界面
     - 未找到 → 手指向上滑 1/4 屏，露出下方条目后继续检索
     - 检索到 GLOBAL_TASK_LIST_ICON_ADVENTURE 说明已滑到列表底部，
       此时仍未找到 golden → 任务已完成，正常结束
  4. 黄金炼制界面：根据 CITY_DAILY_TASK_BUTTON_GOLDEN_GET_TIMES 区域
     OCR 剩余次数，循环对应次数点击 CITY_DAILY_TASK_BUTTON_GOLDEN_GET
  5. 退回主界面

调度参数：
  - 不占军队队列（requires_march_queue=False，纯 UI 操作）
  - 每日一次（next_trigger_delay=86400）
  - 不可并行：任务列表 / 炼制界面同一时刻只能打开一个（count=1）
"""
import re
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_BUTTON_TASKLIST,
    GLOBAL_TASK_LIST_ICON_GOLDEN,
    GLOBAL_TASK_LIST_ICON_ADVENTURE,
    GLOBAL_TASK_LIST_ICON_GOLDEN_ENTRY,
    CITY_DAILY_TASK_BUTTON_GOLDEN_GET_TIMES,
    CITY_DAILY_TASK_BUTTON_GOLDEN_GET,
)
from src.sg.scene.scene_type import SceneType


class GoldenRefineTask(SGBaseTask):
    """
    城堡黄金炼制。

    =========================================================
    职责边界：
      - 只做业务编排：开任务列表 → 滚动检索入口 → 进炼制界面
        → OCR 次数 → 循环领取 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 任务列表向上滑动次数上限（每次 1/4 屏，参考宠物寻宝）
    task_list_scroll_max = 10

    # 任务列表图标匹配阈值（固定样式）
    list_icon_threshold = 0.8

    # 领取按钮出现等待
    get_button_timeout = 2.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Golden Refine"
        self.description = "Refine gold in castle daily tasks."

        # ====================================================
        # 队列注入字段（赋默认值，注册侧不下发个性化参数）
        # ====================================================
        self.extra_config = types.SimpleNamespace()

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

        # 内部状态
        self.remain_times = 0
        # 任务列表已滑到底仍未见 golden → 任务已完成（非失败）
        self.task_list_bottom_reached = False

    # ========================================================
    # 步骤 1：回到主界面（城市 / 世界地图）
    # ========================================================

    def _ensure_main_scene(self) -> bool:
        """
        确保处于主界面。主界面定义为 CITY 或 WORLD_MAP。
        否则连按 ESC 直到回到主界面。
        """
        for _ in range(5):
            scene = self.scene_detector.detect()
            self.log_info(f"当前场景: {scene.type.value}")

            if scene.type in (SceneType.CITY, SceneType.WORLD_MAP):
                self.log_info("已在主界面")
                return True

            self.log_info("不在主界面，按 ESC 返回")
            self.recovery._try_press_esc()
            self._sleep(1.0)

        self.log_error("无法返回主界面")
        return False

    # ========================================================
    # 步骤 2：展开任务列表 → 滚动检索黄金炼制入口
    # ========================================================

    def _open_task_list(self) -> bool:
        """点击 GLOBAL_BUTTON_TASKLIST，展开任务列表。"""
        if not self._wait_and_click_all_screen(
            GLOBAL_BUTTON_TASKLIST, timeout=2.0,
        ):
            self.log_info("未找到任务列表按钮")
            return False
        self._sleep(1.0)
        return True

    def _scroll_find_golden_icon(self) -> bool:
        """
        在任务列表中向上滑动（手指上滑 1/4 屏），
        直到出现 GLOBAL_TASK_LIST_ICON_GOLDEN。

        返回 True  ：列表中出现 golden 图标，可以点入口
        返回 False ：滑动次数耗尽仍未出现。
                     若期间发现 adventure 图标（列表到底），
                     置 task_list_bottom_reached=True，属任务已完成。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)
        self.task_list_bottom_reached = False

        for attempt in range(self.task_list_scroll_max + 1):
            golden_box = self._find(
                GLOBAL_TASK_LIST_ICON_GOLDEN,
                threshold=self.list_icon_threshold, box=full_screen,
            )
            if golden_box is not None:
                self.log_info(
                    f"任务列表检索到黄金炼制图标（滑动 {attempt} 次）"
                )
                return True

            # 冒险图标出现 → 列表已到底，golden 不存在 → 任务已完成
            adventure_box = self._find(
                GLOBAL_TASK_LIST_ICON_ADVENTURE,
                threshold=self.list_icon_threshold, box=full_screen,
            )
            if adventure_box is not None:
                self.task_list_bottom_reached = True
                self.log_info(
                    "任务列表已到底（发现冒险图标），无黄金炼制，"
                    "视为任务已完成"
                )
                return False

            # 未找到 → 手指向上滑 1/4 屏，露出下方条目
            self.swipe_relative(0.3, 0.6, 0.3, 0.3, duration=0.3,
                                settle_time=1.0)
            self._sleep(1.5)

        self.log_error("滚动查找黄金炼制图标失败")
        return False

    def _click_golden_entry(self) -> bool:
        """
        全屏匹配点击黄金炼制入口，并等待炼制界面特征出现确认进入。
        """
        if not self._wait_and_click_all_screen(
            GLOBAL_TASK_LIST_ICON_GOLDEN_ENTRY, timeout=2.0,
        ):
            self.log_info("未找到黄金炼制入口按钮")
            return False
        self._sleep(2.0)

        if not self._wait_element(
            CITY_DAILY_TASK_BUTTON_GOLDEN_GET_TIMES,
            timeout=2.0,
            threshold=0.8,
            with_recovery=False,
        ):
            self.log_info("点击入口后未进入黄金炼制界面")
            return False
        return True

    def _enter_golden_page(self) -> bool:
        """开列表 → 滚动检索 → 点入口。

        列表到底且无 golden → 无事可做，返回 True（跳过领取）。
        """
        if not self._open_task_list():
            return False

        if not self._scroll_find_golden_icon():
            return self.task_list_bottom_reached

        return self._click_golden_entry()

    def _in_golden_page(self) -> bool:
        """判断当前是否在炼制界面：次数区域特征可见。"""
        box = self._find(
            CITY_DAILY_TASK_BUTTON_GOLDEN_GET_TIMES,
            threshold=0.8,
            box=self.box_of_screen(0, 0, 1, 1),
        )
        return box is not None

    # ========================================================
    # 步骤 3：OCR 剩余次数 → 循环领取
    # ========================================================

    def _to_int(self, text):
        """'1,200' -> 1200；'1.5' -> 1（次数均为整数语义）。"""
        return int(float(str(text).replace(",", "")))

    def _parse_remain_times(self, text):
        """
        解析剩余次数文本。

        兼容 '3/5'、'3'、'1,200/2,000' 等格式：斜杠格式取斜杠
        前的第一个数字作为剩余次数；纯数字直接取值。
        无数字返回 None。
        """
        if text is None:
            return None
        text = str(text).strip()

        num = r"(\d[\d,]*(?:\.\d+)?)"
        m = re.search(num + r"\s*/\s*" + num, text)
        if m:
            return self._to_int(m.group(1))

        m = re.search(num, text)
        if m:
            return self._to_int(m.group(1))
        return None

    def _read_remain_times(self) -> bool:
        """根据次数区域 bbox OCR 剩余次数，写入 self.remain_times。"""
        box = self._safe_box(CITY_DAILY_TASK_BUTTON_GOLDEN_GET_TIMES)
        if not box:
            self.log_error("未找到剩余次数区域 bbox")
            return False

        text = self._ocr_text(box)
        if text is None:
            self.log_info("剩余次数区域无 OCR 结果，按 0 处理")
            self.remain_times = 0
            return True

        self.log_info(f"OCR 剩余次数原文: {text}")
        times = self._parse_remain_times(text)
        if times is None:
            self.log_info(f"OCR 文本无法解析为次数，按 0 处理: {text}")
            times = 0

        self.remain_times = max(0, times)
        self.log_info(f"黄金炼制剩余次数: {self.remain_times}")
        return True

    def _claim_all(self) -> bool:
        """循环 remain_times 次点击领取按钮。"""
        if self.remain_times < 1:
            self.log_info("无剩余次数，无需领取")
            return True

        for i in range(self.remain_times):
            self.log_info(f"领取黄金第 {i + 1}/{self.remain_times} 次")
            if not self._wait_and_click(
                CITY_DAILY_TASK_BUTTON_GOLDEN_GET,
                timeout=self.get_button_timeout,
                with_recovery=False,
            ):
                self.log_error(f"第 {i + 1} 次领取失败")
                return False
            self._sleep(1.5)

        self.log_info("黄金炼制领取完成")
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始城堡黄金炼制 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入炼制界面（列表到底无 golden 时视为完成）----
        if not self._step(self._enter_golden_page, "进入炼制界面"):
            self.last_error = "进入炼制界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- OCR 次数 + 循环领取（仅当真正进入炼制界面）----
        if self._in_golden_page():
            if not self._step(self._read_remain_times, "读取剩余次数"):
                self.last_error = "读取剩余次数失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)

            if not self._step(self._claim_all, "循环领取黄金"):
                self.last_error = "领取黄金失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)
        else:
            self.log_info("不在炼制界面，跳过领取（任务已完成或无入口）")

        # ---- 退回主界面（失败不影响领取结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响领取结果")

        self.log_info("========== 城堡黄金炼制完成 ==========")
        return (InteractionResult.SUCCESS, 0)

    # ========================================================
    # 兼容旧接口
    # ========================================================

    def _run_once(self):
        result, wait_seconds = self.run_interaction()
        success = (result == InteractionResult.SUCCESS)
        return (success, wait_seconds)

    # ========================================================
    # 独立运行入口
    # ========================================================

    def run(self):
        self.log_info("========== 城堡黄金炼制（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"城堡黄金炼制失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 城堡黄金炼制完成 ==========")
        return True
