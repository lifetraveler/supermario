"""
竞技场挑战任务（Arena Challenge）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击 GLOBAL_BUTTON_TASKLIST 打开任务列表
  3. 灰度匹配 global_task_list_city_tag 确认在"城市"分类页
  4. 上滑检索"训练士兵-弓"条目（滑到底仍未找到 → 任务已完成，正常结束）
  5. 点击"训练士兵-弓"跳转弓兵营
  6. 点击"弓兵兵营旁边-竞技场"区域（bbox 直接点击，不需要匹配）
  7. 进入挑战界面，点击"竞技场挑战"按钮，弹出挑战列表
  8. 购买次数（可选，buy_times > 0 时执行）：
     - 点击"竞技场增加次数"进入购买次数流程
     - OCR"竞技场-购买次数-识别"得到当前购买次数
     - 当前值 < buy_times → 点"竞技场-购买次数-增加"差量次
     - 当前值 > buy_times → 点"竞技场-购买次数-减少"差量次
     - 相等或调整完 → 点击"竞技场购买次数消耗钻石"确认
  9. OCR"竞技场挑战剩余次数"得到挑战次数
 10. 循环挑战（challenge_limit > 0 时以较小者为准）：
     - 在"竞技场挑战对手列表"特征区域内检索绿色 RGB(3,208,111) 区域
     - 命中多个取第一个点击，进入布阵界面
     - 点击"竞技场一键上阵" → 点击"竞技场挑战-战斗" → 等 2s
     - 匹配"竞技场快速通过1"：命中 → 点击进入快速挑战流程
       → 点击"竞技场快速通过2" → 新界面等 2s → 点击"竞技场快速通过3"
     - 未命中快速通过1 → 等待战斗结算，回挑战列表继续
 11. 挑战次数用尽（或达到 challenge_limit）→ 退出到主堡界面

调度参数：
  - 不占军队队列（requires_march_queue=False，纯 UI 操作）
  - next_trigger_delay=86400（挑战次数每日刷新）
  - 不可并行：任务列表 / 兵营 / 竞技场同一时刻只能打开一个（count=1）
"""
import re
import types

from ok.feature.Box import Box
from ok.util.color import find_color_rectangles

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_BUTTON_TASKLIST,
    GLOBAL_TASK_LIST_CITY_TAG,
    GLOBAL_TASK_LIST_SOLDIER_ARCHER_ENTRY,
    CITY_BARRACKS_ARCANE_AREA,
    ARENA_BUTTON_CHALLENGE,
    ARENA_BUTTON_ADD_TIMES,
    ARENA_BUY_TIMES_OCR_AREA,
    ARENA_BUY_TIMES_BUTTON_ADD,
    ARENA_BUY_TIMES_BUTTON_SUB,
    ARENA_BUY_TIMES_BUTTON_CONFIRM,
    ARENA_CHALLENGE_REMAIN_TIMES,
    ARENA_OPPONENT_LIST_AREA,
    ARENA_BUTTON_ONE_KEY_DEPLOY,
    ARENA_BUTTON_FIGHT,
    ARENA_QUICK_PASS_1,
    ARENA_QUICK_PASS_2,
    ARENA_QUICK_PASS_3,
    ARENA_FIGHT
)

from src.sg.scene.scene_type import SceneType


def parse_times_text(text):
    """
    解析次数/购买次数文本，返回 int 或 None。

    兼容 '3/5'、'3'、'1,200' 等格式：斜杠格式取斜杠前的第一个数字。
    """
    if text is None:
        return None
    text = str(text).strip()

    num = r"(\d[\d,]*)"
    m = re.search(num + r"\s*/\s*" + num, text)
    if m:
        return int(m.group(1).replace(",", ""))

    m = re.search(num, text)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


class ArenaChallengeTask(SGBaseTask):
    """
    竞技场挑战。

    =========================================================
    职责边界：
      - 只做业务编排：开任务列表 → 找弓兵营 → 进竞技场 →
        （可选购买次数）→ OCR 剩余次数 → 循环挑战 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 任务列表向上滑动次数上限（每次 1/4 屏，参考黄金炼制）
    task_list_scroll_max = 10

    # 城市标签灰度匹配阈值
    city_tag_threshold = 0.8

    # 列表条目匹配阈值
    list_entry_threshold = 0.8

    # 绿色对手颜色范围：RGB(3,208,111)，容差 ±30
    green_color_range = {"r": [0, 33], "g": [178, 238], "b": [81, 141]}

    # 绿色对手块的最小尺寸（低于此视为噪点）
    green_min_width = 10
    green_min_height = 10

    # 战斗后回列表的等待上限（秒）
    battle_settle_timeout = 10.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Arena Challenge"
        self.description = (
            "Arena challenge: open task list, find archer barracks, "
            "enter arena, optionally buy challenge times, then loop "
            "green-opponent challenges until remain times exhausted."
        )

        # ====================================================
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 每次挑战次数：0 = 不限制，以 OCR 剩余次数为准
        self.challenge_limit = 0
        # 无免费次数后购买次数：0 = 不购买
        self.buy_times = 0

        # 个性化参数容器
        self.extra_config = types.SimpleNamespace()

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2
        self.recover_interval = 3.0

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

        # ====================================================
        # 内部状态
        # ====================================================
        # 本次实际执行的挑战次数
        self.challenged = 0
        # OCR 读到的剩余挑战次数
        self.remain_times = 0
        # 任务列表已滑到底仍未见"训练士兵-弓" → 任务已完成（非失败）
        self.task_list_bottom_reached = False

    # ========================================================
    # 通用助手
    # ========================================================

    def _full_screen(self):
        return self.box_of_screen(0, 0, 1, 1)

    # ========================================================
    # 步骤 1：展开任务列表 → 确认城市标签 → 滚动检索训练士兵-弓
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

    def _confirm_city_tag(self) -> bool:
        """灰度匹配 global_task_list_city_tag，确认当前在城市分类页。"""
        full = self._full_screen()
        box = self._find(
            GLOBAL_TASK_LIST_CITY_TAG,
            threshold=self.city_tag_threshold,
            box=full,
            use_gray_scale=True,
        )
        if box is None:
            self.log_info("未匹配到任务列表城市标签")
            return False
        self.click(box)
        self.log_info("已确认任务列表城市标签")
        return True

    def _scroll_find_archer_entry(self) -> bool:
        """
        上滑检索"训练士兵-弓"条目。

        返回 True  ：列表中出现条目，可以点击
        返回 False ：滑动次数耗尽仍未出现。
                     若期间滑到底（列表不再移动），置
                     task_list_bottom_reached=True，属任务已完成。
        """
        full = self._full_screen()
        self.task_list_bottom_reached = False
        last_content_box = None

        for attempt in range(self.task_list_scroll_max + 1):
            entry_box = self._find(
                GLOBAL_TASK_LIST_SOLDIER_ARCHER_ENTRY,
                threshold=self.list_entry_threshold,
                box=full,
            )
            if entry_box is not None:
                self.log_info(
                    f"任务列表检索到训练士兵-弓（滑动 {attempt} 次）"
                )
                return True

            # 上滑 1/4 屏，露出下方条目
            self.swipe_relative(0.3, 0.6, 0.3, 0.3, duration=0.3,
                                settle_time=1.0)
            self._sleep(1.5)

            # 列表内容未移动 = 已滑到底
            content_box = self._find(
                GLOBAL_TASK_LIST_CITY_TAG,
                threshold=self.city_tag_threshold,
                box=full,
                use_gray_scale=True,
            )
            if (content_box is not None and last_content_box is not None
                    and content_box.center() == last_content_box.center()):
                self.task_list_bottom_reached = True
                self.log_info(
                    "任务列表已滑到底（内容不再移动），"
                    "无训练士兵-弓，视为任务已完成"
                )
                return False
            last_content_box = content_box

        self.log_error("滚动查找训练士兵-弓失败")
        return False

    def _click_archer_entry(self) -> bool:
        """点击"训练士兵-弓"跳转弓兵营，并等待竞技场区域 bbox 可用确认进入。"""
        full = self._full_screen()
        if not self._wait_and_click(
            GLOBAL_TASK_LIST_SOLDIER_ARCHER_ENTRY,
            timeout=2.0,
            box=full,
        ):
            self.log_info("点击训练士兵-弓失败")
            return False
        self._sleep(2.0)

        # 弓兵营界面：竞技场区域 bbox 应可用（COCO 标注基于固定布局）
        if self._safe_box(CITY_BARRACKS_ARCANE_AREA) is None:
            self.log_info("点击条目后未进入弓兵营（竞技场区域 bbox 不可用）")
            return False
        return True

    def _enter_arena(self) -> bool:
        """开列表 → 确认城市标签 → 滚动检索 → 点条目进弓兵营。"""
        if not self._open_task_list():
            return False

        if not self._confirm_city_tag():
            return False

        if not self._scroll_find_archer_entry():
            return self.task_list_bottom_reached

        return self._click_archer_entry()

    # ========================================================
    # 步骤 2：兵营旁竞技场区域 → 挑战按钮 → 弹出挑战列表
    # ========================================================

    def _open_challenge_list(self) -> bool:
        """
        点击"弓兵兵营旁边-竞技场"（bbox 直接点击），
        再点击"竞技场挑战"弹出挑战列表。
        """
        full = self._full_screen()

        area_box = self._safe_box(CITY_BARRACKS_ARCANE_AREA)
        if area_box is None:
            self.log_error("未找到 弓兵兵营旁边-竞技场 bbox")
            return False
        self.log_info("点击 弓兵兵营旁边-竞技场")
        if not self.click(area_box):
            return False
        self._sleep(1.0)

        if not self._wait_and_click(
            ARENA_BUTTON_CHALLENGE, timeout=2.0, box=full,
        ):
            self.log_info("未找到竞技场挑战按钮")
            return False
        self._sleep(1.0)
        return True

    # ========================================================
    # 步骤 3：购买次数（可选）
    # ========================================================

    def _read_buy_times(self):
        """OCR 购买次数界面当前次数，返回 int 或 None。"""
        box = self._safe_box(ARENA_BUY_TIMES_OCR_AREA)
        if box is None:
            self.log_info("未找到购买次数 OCR 区域 bbox")
            return None

        text = self._ocr_text(box)
        if text is None:
            self.log_info("购买次数区域无 OCR 结果")
            return None

        self.log_info(f"OCR 购买次数原文: {text}")
        return parse_times_text(text)

    def _click_buy_times_button(self, element, count: int, label: str) -> bool:
        """点击购买次数界面的增/减按钮 count 次。"""
        full = self._full_screen()
        for i in range(count):
            if not self._wait_and_click(
                element, timeout=2.0, box=full, with_recovery=False,
            ):
                self.log_error(f"点击购买次数[{label}]第 {i + 1} 次失败")
                return False
            self._sleep(0.3)
        return True

    def _buy_challenge_times(self) -> bool:
        """
        购买次数流程：
          进入购买界面 → OCR 当前值 → 与 buy_times 对比点增/减差量次
          → 点击消耗钻石确认。
        """
        full = self._full_screen()

        if not self._wait_and_click(
            ARENA_BUTTON_ADD_TIMES, timeout=2.0, box=full,
        ):
            self.log_info("未找到竞技场增加次数按钮")
            return False
        self._sleep(1.0)

        current = self._read_buy_times()
        target = max(0, int(self.buy_times or 0))

        if current is not None:
            diff = target - current
            if diff > 0:
                self.log_info(
                    f"当前购买次数 {current} < 目标 {target}，增加 {diff} 次"
                )
                if not self._click_buy_times_button(
                    ARENA_BUY_TIMES_BUTTON_ADD, diff, "增加",
                ):
                    return False
            elif diff < 0:
                self.log_info(
                    f"当前购买次数 {current} > 目标 {target}，减少 {-diff} 次"
                )
                if not self._click_buy_times_button(
                    ARENA_BUY_TIMES_BUTTON_SUB, -diff, "减少",
                ):
                    return False
            else:
                self.log_info(f"当前购买次数已等于目标 {target}")
        else:
            self.log_info("购买次数 OCR 失败，不做增减，直接确认购买")

        if not self._wait_and_click(
            ARENA_BUY_TIMES_BUTTON_CONFIRM, timeout=2.0, box=full,
        ):
            self.log_error("未找到竞技场购买次数消耗钻石按钮")
            return False
        self._sleep(1.5)
        return True

    # ========================================================
    # 步骤 4：OCR 剩余挑战次数
    # ========================================================

    def _read_remain_times(self) -> bool:
        """OCR 挑战剩余次数，写入 self.remain_times。"""
        box = self._safe_box(ARENA_CHALLENGE_REMAIN_TIMES)
        if box is None:
            self.log_error("未找到挑战剩余次数区域 bbox")
            return False

        text = self._ocr_text(box)
        if text is None:
            self.log_error("挑战剩余次数区域无 OCR 结果")
            return False

        self.log_info(f"OCR 挑战剩余次数原文: {text}")
        times = parse_times_text(text)
        if times is None:
            self.log_error(f"OCR 文本无法解析为次数: {text}")
            return False

        self.remain_times = max(0, times)
        self.log_info(f"竞技场挑战剩余次数: {self.remain_times}")
        return True

    # ========================================================
    # 步骤 5：循环挑战
    # ========================================================

    def _challenge_rounds(self) -> int:
        """本次要挑战的轮数：challenge_limit>0 时取较小值，否则 OCR 值。"""
        rounds = self.remain_times
        limit = max(0, int(self.challenge_limit or 0))
        if limit > 0:
            rounds = min(rounds, limit)
        return rounds

    def _find_green_opponent(self):
        """
        在"竞技场挑战对手列表"特征区域内检索绿色 RGB(3,208,111) 区域。

        返回第一个命中的 Box（多个取第一个），无命中返回 None。
        """
        area = self._safe_box(ARENA_OPPONENT_LIST_AREA)
        if area is None:
            self.log_info("未找到挑战对手列表区域 bbox")
            return None

        frame = self.frame
        if frame is None:
            self.log_info("当前帧不可用")
            return None

        rects = find_color_rectangles(
            frame,
            self.green_color_range,
            min_width=self.green_min_width,
            min_height=self.green_min_height,
            box=area,
            threshold=0.55
        )
        if not rects:
            self.log_info("对手列表未匹配到绿色可挑战区域")
            return None

        self.log_info(f"匹配到 {len(rects)} 个绿色对手区域，取第一个")
        return rects[0]

    def _click_green_opponent(self) -> bool:
        """点击第一个绿色对手，进入布阵界面。"""
        opponent = self._find_green_opponent()
        if opponent is None:
            return False

        self.log_info(
            f"点击绿色对手: x={opponent.x}, y={opponent.y}, "
            f"w={opponent.width}, h={opponent.height}"
        )
        # 需要偏移处理，用 这个色块的 y坐标+高度的一半，用 竞技场-战斗 操作按钮的 x跟宽度。
        arena_fight=self.get_box_by_name(ARENA_FIGHT.resource_id)
        bfb=Box(arena_fight.x,opponent.y,arena_fight.width,opponent.height/2)
        if not self.click(bfb):
            self.log_error("绿色对手点击失败")
            return False
        self._sleep(1.0)
        return True

    def _deploy_and_fight(self) -> bool:
        """布阵界面：一键上阵 → 战斗。"""
        full = self._full_screen()

        if not self._wait_and_click(
            ARENA_BUTTON_ONE_KEY_DEPLOY, timeout=2.0, box=full,
        ):
            self.log_info("未找到竞技场一键上阵按钮")
            return False
        self._sleep(0.5)

        if not self._wait_and_click(
            ARENA_BUTTON_FIGHT, timeout=2.0, box=full,
        ):
            self.log_info("未找到竞技场挑战-战斗按钮")
            return False
        # 需求原文：等待 2s
        self._sleep(2.0)
        return True

    def _quick_pass(self) -> bool:
        """
        快速挑战流程：快速通过1 → 快速通过2（新界面等 2s）→ 快速通过3。
        """
        full = self._full_screen()

        if not self._wait_and_click(
            ARENA_QUICK_PASS_1, timeout=2.0, box=full, with_recovery=False,
        ):
            self.log_info("未匹配到竞技场快速通过1（可能无快速挑战）")
            return False

        if not self._wait_and_click(
            ARENA_QUICK_PASS_2, timeout=2.0, box=full, with_recovery=False,
        ):
            self.log_error("未找到竞技场快速通过2")
            return False
        # 需求原文：新界面等待 2s
        self._sleep(2.0)

        if not self._wait_and_click(
            ARENA_QUICK_PASS_3, timeout=2.0, box=full, with_recovery=False,
        ):
            self.log_error("未找到竞技场快速通过3")
            return False
        self._sleep(1.5)
        return True

    def _wait_back_to_challenge_list(self) -> bool:
        """战斗/快速挑战后等挑战列表恢复（剩余次数区域重新可见）。"""
        full = self._full_screen()
        if self._wait_element(
            ARENA_CHALLENGE_REMAIN_TIMES,
            timeout=self.battle_settle_timeout,
            interval=1.0,
            box=full,
            with_recovery=False,
        ) is not None:
            return True
        self.log_info("等待挑战列表恢复超时")
        return False

    def _challenge_loop(self) -> bool:
        """
        主挑战循环：找绿色对手 → 布阵战斗 → 快速挑战 → 回列表。

        轮数 = min(OCR 剩余次数, challenge_limit)（challenge_limit=0 时不限）。
        单轮找不到绿色对手 = 无可挑战对手，提前结束（不算失败）。
        """
        rounds = self._challenge_rounds()
        if rounds < 1:
            self.log_info("无剩余挑战次数，跳过挑战循环")
            return True

        self.challenged = 0
        for i in range(1, rounds + 1):
            self.log_info(f"===== 竞技场挑战 第 {i}/{rounds} 轮 =====")

            if not self._click_green_opponent():
                self.log_info("无可挑战的绿色对手，提前结束挑战循环")
                return True

            if not self._deploy_and_fight():
                self.last_error = "布阵或战斗失败"
                return False

            # 快速挑战流程（可选路径：无快速通过1 则等战斗结算）
            if self._quick_pass():
                self.log_info("快速挑战流程完成")
            else:
                self.log_info("走常规战斗结算，等待回列表")

            if not self._wait_back_to_challenge_list():
                self.last_error = "挑战后未回到挑战列表"
                return False

            self.challenged = i

        self.log_info(f"竞技场挑战循环完成，共 {self.challenged} 轮")
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始竞技场挑战 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        success, _scene = self._ensure_main_scene()
        if not success:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        self.challenged = 0

        # ---- 进入弓兵营 + 竞技场（列表到底无条目时视为完成）----
        if not self._step(self._enter_arena, "进入竞技场入口"):
            self.last_error = "进入竞技场入口失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        in_arena = self._safe_box(CITY_BARRACKS_ARCANE_AREA) is not None

        # ---- 打开挑战列表（仅当真正进入弓兵营）----
        challenge_list_open = False
        if in_arena:
            if not self._step(self._open_challenge_list, "打开挑战列表"):
                self.last_error = "打开挑战列表失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)
            challenge_list_open = True
        else:
            self.log_info("不在弓兵营，跳过挑战（任务已完成或无入口）")

        if challenge_list_open:
            # ---- 购买次数（可选）----
            if int(self.buy_times or 0) > 0:
                if not self._step(self._buy_challenge_times, "购买挑战次数"):
                    self.last_error = "购买挑战次数失败"
                    self.log_error(self.last_error)
                    return (InteractionResult.FAILED, 0)

            # ---- OCR 剩余次数 ----
            if not self._step(self._read_remain_times, "读取剩余挑战次数"):
                self.last_error = "读取剩余挑战次数失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)

            # ---- 循环挑战 ----
            if not self._step(self._challenge_loop, "循环挑战"):
                self.last_error = self.last_error or "循环挑战失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)

        # ---- 退回主堡界面（失败不影响挑战结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响挑战结果")

        self.log_info(
            f"========== 竞技场挑战完成（本次挑战 {self.challenged} 轮） =========="
        )
        return (InteractionResult.SUCCESS, 0)

    def check_completed(self) -> bool:
        return True

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
        self.log_info("========== 竞技场挑战（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"竞技场挑战失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 竞技场挑战完成 ==========")
        return True
