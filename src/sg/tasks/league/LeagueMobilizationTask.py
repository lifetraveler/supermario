"""
联盟总动员任务（OCR 文字匹配版）。

流程：
  1. 确保前台，清理弹窗，回主界面
  2. 进入联盟总动员界面（常规活动入口 → 页签滑动灰度查找）
  3. 次数状态判断（一次只接一个，无轮末重读循环）：
     OCR 剩余可接次数 x；x + 剩余购买次数 y = 0 → FAILED（无多余次数）
     y > 0 且 x = 0 时走购买流程（第一次免费 / 消耗钻石提交）
  4. 专属任务（优先）：每个卡槽先 OCR 预检——解析出刷新倒计时 =
     正在刷新，跳过点击并记入下次触发候选；可点击则进描述窗口匹配
     专属目标 → 接受 / 刷新
  5. 公共任务（门控）：
     - 专属目标未配置或仍有卡槽可接 → 本轮不跑公共任务
     - 两个专属卡槽都在刷新且刷新完成时刻已跨天 → 放行公共任务
     - 公共任务目标未配置 → 不接受；公共任务刷新关闭 → 不刷新；
       刷新开启但刷新目标未勾选 → 不做逐行清扫

需求文档：docs/PROJECT_REQUIREMENTS.md（2026-10-29 联盟总动员一节）。
"""

import types
from datetime import datetime, timedelta

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper
from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.activity.mobilization import Mobilization

from src.sg.scene.elements import (
    LEAGUE_MOB_EXCLUSIVE_SLOT_1,
    LEAGUE_MOB_EXCLUSIVE_SLOT_2,
    LEAGUE_MOB_SCROLL_WINDOW,
    LEAGUE_MOB_SCROLL_TOP_EDGE,
)


class LeagueMobilizationTask(SGBaseTask):
    """
    联盟总动员。

    =========================================================
    职责边界：
      - 只做业务编排：进界面 → 次数判断/购买 → 专属 → 公共
      - 刷新倒计时 OCR 与特征检索细节由 Mobilization 承担
      - 弹窗处理交给 ActivityPopup
    =========================================================
    """

    # 无刷新记录时的兜底触发间隔（30 分钟，活动内任务会持续刷新）
    default_next_wait_seconds = 1800

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "League Mobilization"
        self.description = (
            "联盟总动员：刷新高效指定-ocr文字匹配版。"
            "接取专属/公共任务，支持购买次数与公共任务刷新。"
        )

        # ====================================================
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 专属任务目标（多选，值 = MOBILITY_GOALS 键）。
        self.exclusive_goals = ["训练12万"]
        # 公共任务刷新目标（多选，特征匹配用）。
        self.public_refresh_goals = ["训练12万"]
        # 公共任务目标（多选，特征匹配 + 描述校验）。
        self.public_goals = ["训练12万"]
        # 购买当日次数（剩余额度，每次购买后递减）。
        self.buy_times = 1
        # 公共任务刷新开关。
        self.public_refresh = False
        # 公共任务滚动幅度（相对值 0-1）。
        self.public_scroll_step = 0.3

        # ====================================================
        # 个性化参数容器（factory 会把 params 再注入一份到这里）
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
        self.mob = Mobilization(self)

        # ====================================================
        # 运行时状态（next_wait_seconds 回传调度）
        # ====================================================
        self.next_wait_seconds = None
        self.buy_left = 0

    # ========================================================
    # 内部步骤
    # ========================================================

    def _ensure_times(self) -> bool:
        """
        次数状态判断 + 按需购买。

        x = OCR 剩余可接次数；y = 剩余购买额度。
        x + y == 0 → False（无多余次数，由上层判 FAILED）；
        x == 0 且 y > 0 → 逐次购买（免费优先，之后钻石面板提交）。
        购买后重新 OCR 校验；OCR 失败按 0 处理（保守不接任务）。
        """
        x = self.mob.read_remain_times()
        if x < 0:
            self.log_error("剩余可接次数 OCR 失败")
            return False

        y = self.buy_left
        self.log_info(f"次数状态: 可接 x={x}, 剩余购买 y={y}")
        if x + y <= 0:
            self.last_error = "没有多余次数（可接 0 且无购买额度）"
            self.log_error(self.last_error)
            return False

        while x <= 0 and y > 0:
            if not self._step(self.mob.purchase_times, "购买次数"):
                self.last_error = "购买次数失败"
                self.log_error(self.last_error)
                return False
            y -= 1
            self.buy_left = y
            x = self.mob.read_remain_times()
            if x < 0:
                self.log_error("购买后次数 OCR 失败，按 0 处理")
                x = 0
            self.log_info(f"购买后次数: x={x}, 剩余购买 y={y}")

        return True

    def _run_exclusive_slots(self):
        """
        依次处理两个专属卡槽，记录更近的刷新倒计时。

        返回 ('busy'|'free', bool)：
          'busy' — 两个卡槽都在刷新中（专属暂不可接）
          'free' — 至少有一个卡槽被接受/刚刷新（专属可继续接）
        刷新倒计时写入 next_wait_seconds（取更近者）。
        """
        goals = [g for g in (self.exclusive_goals or [])]
        if not goals:
            self.log_info("未配置专属任务目标，跳过专属卡槽")
            return ("free", True)

        all_refreshing = True
        for slot in (LEAGUE_MOB_EXCLUSIVE_SLOT_1, LEAGUE_MOB_EXCLUSIVE_SLOT_2):
            action, seconds = self.mob.run_exclusive_slot(slot, goals)
            if action == "failed":
                self.last_error = f"专属卡槽处理失败: {slot.name}"
                self.log_error(self.last_error)
                return ("free", False)
            if action in ("refreshing", "refreshed"):
                if seconds is not None:
                    current = self.next_wait_seconds
                    if current is None or seconds < current:
                        self.next_wait_seconds = seconds
            if action != "refreshing":
                all_refreshing = False
        return ("busy" if all_refreshing else "free", True)

    def _run_public_tasks(self) -> bool:
        """
        公共任务主流程：检索 → 校验 → 接受/刷新/关闭。

        门控（参数）：
          - 公共任务目标 未配置 → 跳过接受阶段
          - 公共任务刷新 关闭   → 不做任何刷新
          - 刷新开启且 公共任务刷新目标 未勾选任何值 → 仍不做清扫
            （清扫的本质是刷新未接卡片；无保留目标时刷新无差别）

        刷新开启时追加阶段 B：回滚到上沿后按基准点行高逐行清扫，
        未接卡片按 公共任务刷新目标 决定 刷新 / 保留。
        """
        goals = [g for g in (self.public_goals or [])]
        if not goals:
            self.log_info("未配置公共任务目标，跳过公共任务接受")
            return True

        result = self.mob.run_public_tasks(
            goals=goals,
            can_refresh=bool(self.public_refresh),
            scroll_step=self.public_scroll_step,
        )
        self.log_info(f"公共任务处理结果: {result}")
        if result == "failed":
            self.last_error = "公共任务处理失败"
            self.log_error(self.last_error)
            return False

        refresh_goals = [g for g in (self.public_refresh_goals or [])]
        if self.public_refresh and refresh_goals:
            sweep = self.mob.run_refresh_sweep(
                refresh_goals=refresh_goals,
                scroll_step=self.public_scroll_step,
            )
            self.log_info(f"刷新清扫结果: {sweep}")
            if sweep == "failed":
                self.last_error = "公共任务刷新清扫失败"
                self.log_error(self.last_error)
                return False
        return True

    def _public_ready(self) -> bool:
        """
        专属 → 公共 的放行判定。

        两个专属卡槽的预检 OCR 均解析出刷新倒计时，且换算后的
        刷新完成时刻已到明天（now + seconds 跨过当日 24 点），
        视为"专属今天接不上了"，放行公共任务；否则专属优先，
        公共任务本轮不跑。
        """
        now = datetime.now()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        # 任一卡槽能在今天之内刷完 → 专属仍可接
        for slot in (LEAGUE_MOB_EXCLUSIVE_SLOT_1, LEAGUE_MOB_EXCLUSIVE_SLOT_2):
            seconds = self.mob.check_slot_refreshing(slot)
            if seconds is None:
                return False
            if now + timedelta(seconds=seconds) < midnight:
                return False
        self.log_info("两个专属卡槽刷新均跨天，转公共任务")
        return True


    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始联盟总动员 ==========")

        self.ensure_in_front()
        self.popup.close_all()

        # ---- 回到主界面 ----
        ok, _scene = self._ensure_main_scene()
        if not ok:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入联盟总动员界面 ----
        if not self._step(self.mob.open_activity, "进入联盟总动员界面"):
            self.last_error = "进入联盟总动员界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 运行时状态复位 ----
        self.next_wait_seconds = None
        self.buy_left = int(self.buy_times or 0)

        # ---- 次数状态判断（一次只接一个，无轮末重读循环）----
        if not self._step(self._ensure_times, "次数状态判断"):
            # x+y=0 的"无多余次数"与 OCR 失败都在这里返回 FAILED
            return (InteractionResult.FAILED, 0)

        # ---- 专属任务（优先）----
        state, ok = self._run_exclusive_slots()
        if not ok:
            return (InteractionResult.FAILED, 0)

        # ---- 公共任务：仅当两个专属卡槽刷新均跨天才放行 ----
        if state == "busy" or self._public_ready():
            if not self._step(self._run_public_tasks, "公共任务"):
                return (InteractionResult.FAILED, 0)
        else:
            self.log_info("专属任务仍有可接卡槽，本轮不处理公共任务")

        wait_seconds = int(
            self.next_wait_seconds or self.default_next_wait_seconds
        )
        self.log_info(
            f"========== 联盟总动员完成，下次触发 {wait_seconds}s 后 =========="
        )
        return (InteractionResult.SUCCESS, wait_seconds)

    # ========================================================
    # 兼容旧接口
    # ========================================================

    def check_completed(self) -> bool:
        return True

    def _run_once(self):
        result, wait_seconds = self.run_interaction()
        success = (result == InteractionResult.SUCCESS)
        return (success, wait_seconds)

    # ========================================================
    # 独立运行入口
    # ========================================================

    def run(self):
        self.log_info("========== 联盟总动员（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"联盟总动员失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        if wait_seconds > 0:
            self._sleep(wait_seconds)
        return True
