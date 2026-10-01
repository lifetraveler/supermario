"""
统帅每日免费奖励领取任务（Leader Reward）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗
  2. city 主界面根据 CITY_DAILY_FREE_LEADER_ENTRY 进入统帅界面
  3. 特征匹配 CITY_DAILY_FREE_LEADER_BOX 点击（选中宝箱）
     → 弹出窗口匹配 CITY_DAILY_FREE_LEADER_PAGE_BUTTON_GETED
     （已领取标志）后点击退出（ESC 回主界面）
  4. 匹配 CITY_DAILY_FREE_LEADER_PAGE_BUTTON_GET_REWARD 后点击领取
     → 弹出"获得奖励"窗口（GLOBAL_MARK_REWARD_GETED_QUIT_TIP）后结束
  5. 最后返回主界面

调度参数：
  - 不占军队队列（requires_march_queue=False）
  - 每日一次（next_trigger_delay=86400）
  - 不可并行：弹窗同一时刻只能打开一个
"""

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    CITY_DAILY_FREE_LEADER_ENTRY,
    CITY_DAILY_FREE_LEADER_BOX,
    CITY_DAILY_FREE_LEADER_PAGE_BUTTON_GETED,
    CITY_DAILY_FREE_LEADER_PAGE_BUTTON_GET_REWARD,
    GLOBAL_MARK_REWARD_GETED_QUIT_TIP,
)
from src.sg.scene.scene_type import SceneType


class LeaderRewardTask(SGBaseTask):
    """
    统帅每日免费奖励领取。

    =========================================================
    职责边界：
      - 只做业务编排：进统帅界面 → 逐宝箱领取 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 宝箱领取轮数上限（界面有几个宝箱就点几轮，防异常界面死循环）
    claim_rounds_max = 5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Leader Reward"
        self.description = "Claim daily free leader rewards."

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试（标准实现，勿改）
    # ========================================================

    def _step(self, step_func, step_name) -> bool:
        for attempt in range(self.max_recover_attempts + 1):
            if attempt > 0:
                self.log_info(f"步骤 [{step_name}] 第 {attempt} 次重试前恢复")
                if not self.recovery.full_recover():
                    self.log_info(f"步骤 [{step_name}] 无法恢复，停止重试")
                    return False
                self._sleep(self.recovery.recover_wait)

            if step_func():
                return True

            self.log_info(f"步骤 [{step_name}] 失败")

        return False

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
    # 步骤 2：进入统帅界面
    # ========================================================

    def _open_leader_page(self) -> bool:
        """city 主界面 → 点击统帅入口 → 确认统帅标题出现。"""
        full_screen = self.box_of_screen(0, 0, 1, 1)

        if not self._wait_and_click(
            CITY_DAILY_FREE_LEADER_ENTRY, name="统帅入口",
            timeout=6.0, box=full_screen,
        ):
            return False
        self._sleep(1.0)

        # 确认进入统帅界面（标题出现）
        if self._wait_element(
            CITY_DAILY_FREE_LEADER_TITLE,
            timeout=6.0, box=full_screen, with_recovery=False,
        ) is None:
            self.log_error("未进入统帅界面（标题未出现）")
            return False
        return True

    # ========================================================
    # 步骤 3：逐宝箱领取
    # ========================================================

    def _claim_one_box(self) -> bool:
        """
        当前宝箱的领取逻辑（按用户需求原文）：
          1. 特征匹配 CITY_DAILY_FREE_LEADER_BOX 点击（选中宝箱）
          2. 弹出窗口匹配 PAGE_BUTTON_GETED（已领取标志）：
             - 匹配到 → 该宝箱已领过，点击退出（ESC）继续下一个
          3. 匹配 PAGE_BUTTON_GET_REWARD 后点击领取
          4. 弹出"获得奖励"窗口（GLOBAL_MARK_REWARD_GETED_QUIT_TIP）→ 点击退出
        返回 True 表示本轮处理成功（含"已领取"跳过）。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        # 选中宝箱
        if not self._wait_and_click(
            CITY_DAILY_FREE_LEADER_BOX, name="统帅宝箱",
            timeout=6.0, box=full_screen,
        ):
            return False
        self._sleep(1.0)

        # 弹出窗口：先匹配"已领取"标志
        geted_box = self._wait_element(
            CITY_DAILY_FREE_LEADER_PAGE_BUTTON_GETED,
            timeout=3.0, box=full_screen, with_recovery=False,
        )
        if geted_box is not None:
            self.log_info("当前宝箱已领取，点击退出继续下一个")
            self.click(geted_box)
            self._sleep(1.0)
            return True

        # 未领取 → 点击领取奖励按钮
        if not self._wait_and_click(
            CITY_DAILY_FREE_LEADER_PAGE_BUTTON_GET_REWARD,
            name="统帅领取奖励", timeout=6.0, box=full_screen,
        ):
            self.log_info("未匹配到领取按钮，视为无可领取")
            return True

        self._sleep(1.5)

        # 等待"获得奖励"提示出现后点击退出
        quit_box = self._wait_element(
            GLOBAL_MARK_REWARD_GETED_QUIT_TIP,
            timeout=6.0, box=full_screen, with_recovery=False,
        )
        if quit_box is not None:
            self.log_info("点击获得奖励提示退出")
            self.click(quit_box)
            self._sleep(1.0)
        else:
            self.log_info("未出现获得奖励提示，跳过退出点击")
        return True

    def _claim_all_boxes(self) -> bool:
        """
        循环领取：每一轮处理一个宝箱，
        找不到宝箱图标即结束（全部处理完）。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        for round_no in range(1, self.claim_rounds_max + 1):
            box = self._find(
                CITY_DAILY_FREE_LEADER_BOX, threshold=0.8, box=full_screen
            )
            if box is None:
                self.log_info(f"第 {round_no} 轮未找到宝箱图标，领取结束")
                return True

            if not self._step(self._claim_one_box, f"领取宝箱#{round_no}"):
                self.log_info(f"宝箱#{round_no} 领取失败，继续下一个")
            self._sleep(0.5)

        self.log_info("达到轮数上限，结束领取")
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始统帅领取奖励 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入统帅界面 ----
        if not self._step(self._open_leader_page, "进入统帅界面"):
            self.last_error = "进入统帅界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 逐宝箱领取（失败不阻塞整体结果）----
        if not self._step(self._claim_all_boxes, "领取统帅奖励"):
            self.log_info("统帅奖励领取失败，不影响整体结果")

        # ---- 退回主界面（失败不影响领取结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响领取结果")

        self.log_info("========== 统帅领取奖励完成 ==========")
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
        self.log_info("========== 统帅领取奖励（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"统帅领取奖励失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 统帅领取奖励完成 ==========")
        return True
