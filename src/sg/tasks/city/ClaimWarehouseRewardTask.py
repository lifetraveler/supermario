"""
自动领取仓库奖励任务（Claim Warehouse Reward）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击 GLOBAL_BUTTON_TASKLIST 打开任务列表
  3. 在任务列表中向上滑动（每次 1/4 屏），循环检索
     CITY_TASK_LIST_WAREHOUSE_ENTRY：
     - 检索到 → 点击特征（点击即领取），跳转领取成功界面
     - 滑动到底（出现 GLOBAL_TASK_LIST_DOWN_EDGE）仍未检索到
       → 说明不允许领取，返回失败，不重试
  4. 领取成功界面：OCR CITY_TASK_LIST_WAREHOUSE_NEXT_REWARD_TIME
     区域，得到下一次可领取的时间
  5. 退回主界面，任务返回成功以及下一次任务时间

调度语义（与 ClaimStamina 对齐，用户确认）：
  - run_interaction() 返回 (SUCCESS, wait_seconds)，wait_seconds =
    距下次可领取的秒数。队列把任务置 IN_PROGRESS，到点后
    check_completed() 放行 DONE，follower 按 next_trigger_delay=0
    在同一时刻触发下一次领取。
  - 注册为 count=0（无限）+ next_trigger_delay=0，循环节奏完全由
    本任务的动态等待驱动。
  - 滑到底未见入口 → FAILED（需求原文：不重试）。队列 stalled
    扫描按 next_trigger_delay=0 立即补建重扫，由调度层负责节奏。
"""

import re
import time
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_BUTTON_TASKLIST,
    CITY_TASK_LIST_WAREHOUSE_ENTRY,
    CITY_TASK_LIST_WAREHOUSE_NEXT_REWARD_TIME,
    GLOBAL_TASK_LIST_DOWN_EDGE,
    GLOBAL_TASK_LIST_CLICK_JUMP_POTION_NEED_HANDLE
)
from src.sg.scene.scene_type import SceneType


class ClaimWarehouseRewardTask(SGBaseTask):
    """
    自动领取仓库奖励。

    =========================================================
    职责边界：
      - 只做业务编排：开任务列表 → 滚动检索仓库入口 → 点击领取
        → OCR 下次领取时间 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 任务列表向上滑动次数上限（每次 1/4 屏，参考黄金炼制/士兵训练）
    task_list_scroll_max = 10

    # 任务列表图标匹配阈值（固定样式）
    list_icon_threshold = 0.8

    # 领取成功界面 OCR 区域出现等待
    reward_time_timeout = 2.0

    # 倒计时 OCR 的兜底间隔：识别失败时按此间隔重试（秒）
    default_wait_seconds = 3600.0

    # 动态等待下限：避免刚领完立刻重跑空转（秒）
    min_wait_seconds = 60.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Claim Warehouse Reward"
        self.description = (
            "自动领取仓库奖励。Claim warehouse reward via task list "
            "entry and wait for next reward time."
        )

        # ====================================================
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 个性化参数容器
        self.extra_config = types.SimpleNamespace()

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # ====================================================
        # 内部状态：下次可领取的时间戳（本地纪元秒）
        # ====================================================
        self.next_reward_time = None

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

    # ========================================================
    # 步骤 1：回到主界面（城市 / 世界地图，统一使用父级方法）父级方法
    # ========================================================



    # ========================================================
    # 步骤 2：展开任务列表 → 滚动检索仓库入口
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

    def _scroll_find_warehouse_entry(self) -> bool:
        """
        在任务列表中向上滑动（手指上滑 1/4 屏），
        直到出现 CITY_TASK_LIST_WAREHOUSE_ENTRY。

        返回 True  ：列表中出现仓库入口，可以点击领取
        返回 False ：滑动次数耗尽仍未出现。
                     若期间发现 down_edge（列表到底），
                     说明不允许领取（需求原文：不重试）。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        for attempt in range(self.task_list_scroll_max + 1):
            entry_box = self._find(
                CITY_TASK_LIST_WAREHOUSE_ENTRY,
                threshold=self.list_icon_threshold, box=full_screen,
            )
            if entry_box is not None:
                self.log_info(
                    f"任务列表检索到仓库奖励入口（滑动 {attempt} 次）"
                )
                return True

            # 滑到底标志出现 → 列表已到底，入口不存在 → 不允许领取
            edge_box = self._find(
                GLOBAL_TASK_LIST_DOWN_EDGE,
                threshold=self.list_icon_threshold, box=full_screen,
            )
            if edge_box is not None:
                self.log_info(
                    "任务列表已到底（发现 down_edge），无仓库奖励入口，"
                    "不允许领取"
                )
                return False

            # 未找到 → 手指向上滑 1/4 屏，露出下方条目
            self.swipe_relative(0.3, 0.6, 0.3, 0.3, duration=0.3,
                                settle_time=1.0)
            self._sleep(1.5)

        self.log_error("滚动查找仓库奖励入口失败")
        return False

    def _claim_via_entry(self,scene) -> bool:
        """点击仓库入口（点击即领取），等待领取成功界面出现。"""
        if not self._wait_and_click_all_screen(
            CITY_TASK_LIST_WAREHOUSE_ENTRY, timeout=2.0,
        ):
            self.log_info("未找到仓库奖励入口按钮")
            return False
        self._sleep(2.0)
        if scene.type=='world_map':
            if not self._wait_element(
                CITY_TASK_LIST_WAREHOUSE_NEXT_REWARD_TIME,
                timeout=self.reward_time_timeout,
                threshold=0.8,
                with_recovery=False,
            ):
                self.log_info("点击入口后未进入领取成功界面")
                return False
        if scene.type=='city':
            self._wait_and_click_direct(
                GLOBAL_TASK_LIST_CLICK_JUMP_POTION_NEED_HANDLE
            )
            self.sleep(1.5)
            if not self._wait_element(
                CITY_TASK_LIST_WAREHOUSE_NEXT_REWARD_TIME,
                timeout=self.reward_time_timeout,
                threshold=0.8,
                with_recovery=False,
            ):
                self.log_info("点击入口后未进入领取成功界面")
                return False
        return True

    def _enter_and_claim(self,scene) -> bool:
        """开列表 → 滚动检索 → 点入口领取。

        列表到底且无入口 → 不允许领取，返回 False（不重试）。
        """
        if not self._open_task_list():
            return False

        if not self._scroll_find_warehouse_entry():
            return False

        return self._claim_via_entry(scene)

    # ========================================================
    # 步骤 3：OCR 下次领取时间
    # ========================================================

    def _parse_countdown_text(self, text: str):
        """
        把倒计时文本解析成秒数。

        兼容格式：
          - "01:23:45" / "1:23:45"   → 时:分:秒
          - "12:34"                  → 分:秒
          - "1小时23分45秒" 等混杂文本 → 按数字组个数推断
        解析失败返回 None。
        """
        if not text:
            return None

        text = text.strip()
        self.log_info(f"倒计时 OCR 原始文本: {text}")

        # 冒号分隔：按段数直接映射
        if ":" in text:
            parts = [p for p in text.split(":") if p.strip().isdigit()]
            numbers = [int(p) for p in parts]
            if len(numbers) >= 3:
                h, m, s = numbers[-3], numbers[-2], numbers[-1]
                return h * 3600 + m * 60 + s
            if len(numbers) == 2:
                m, s = numbers
                return m * 60 + s
            if len(numbers) == 1:
                return numbers[0]
            return None

        # 无冒号：按数字组个数推断（3 组=时分秒，2 组=分秒，1 组=秒）
        numbers = [int(n) for n in re.findall(r"\d+", text)]
        if len(numbers) >= 3:
            h, m, s = numbers[-3], numbers[-2], numbers[-1]
            return h * 3600 + m * 60 + s
        if len(numbers) == 2:
            m, s = numbers
            return m * 60 + s
        if len(numbers) == 1:
            return numbers[0]
        return None

    def _read_next_wait_seconds(self):
        """
        OCR 下次领取时间区域，返回距下次可领取的秒数。
        识别 / 解析失败返回 None。
        """
        box = self.get_box_by_name(
            CITY_TASK_LIST_WAREHOUSE_NEXT_REWARD_TIME.resource_id
        )
        if box is None:
            self.log_info("未找到下次领取时间区域 bbox")
            return None

        try:
            results = self.ocr(box=box)
        except Exception as e:
            self.log_info(f"OCR 下次领取时间异常: {e}")
            return None

        if not results:
            self.log_info("下次领取时间区域 OCR 无结果")
            return None

        text = "".join(r.name for r in results)
        seconds = self._parse_countdown_text(text)
        if seconds is None:
            self.log_info(f"倒计时文本解析失败: {text}")
        return seconds

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)，
        wait_seconds = 距下次可领取的秒数。
        """
        self.log_info("========== 开始领取仓库奖励 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        success,scene = self._ensure_main_scene()
        if not success:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)  

        # ---- 开列表 → 滚动检索 → 点入口领取 ----
        # 失败不重试：滑到底未见入口属"不允许领取"，重扫无意义；
        # _step 的恢复重试只会导致重复整轮滑动，这里直接判 FAILED。
        if not self._enter_and_claim(scene):
            self.last_error = "任务列表未检索到仓库奖励入口或领取失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- OCR 下次领取时间 ----
        wait = self._read_next_wait_seconds()
        if wait is None:
            self.log_warning(
                f"下次领取时间识别失败，"
                f"使用默认间隔 {self.default_wait_seconds:.0f}s"
            )
            wait = self.default_wait_seconds
        wait = max(self.min_wait_seconds, float(wait))

        self.next_reward_time = time.time() + wait
        self.info_set("Claimed", True)
        self.info_set("Next Reward In", f"{wait / 60:.0f} min")

        # ---- 退回主界面（失败不影响领取结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响领取结果")

        self.log_info(
            f"========== 领取仓库奖励完成，下次领取 "
            f"{wait:.0f}s 后 =========="
        )
        return (InteractionResult.SUCCESS, wait)

    def check_completed(self) -> bool:
        """
        IN_PROGRESS 到点后的二次校验：
        到达下次可领取时间才算完成，让 follower 精准接续。
        """
        if self.next_reward_time is None:
            return True
        return time.time() >= self.next_reward_time - 1.0

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
        self.log_info("========== 领取仓库奖励（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"领取仓库奖励失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info(
            f"========== 领取仓库奖励完成，"
            f"下次领取 {wait_seconds:.0f}s 后 =========="
        )
        return True
