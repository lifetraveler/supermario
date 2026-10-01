"""
讨伐奖励领取任务（Punish Claim）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗
  2. 若不在主界面（城市/世界地图）→ 按 ESC 回到主界面
  3. 任意主界面根据 GOBAL_TAG_MARCH 点击进入讨伐界面
  4. 直接点击 GLOBAL_TAG_MARCH_BUTTON_GET 位置按钮，弹出界面
  5. 直接点击 GLOBAL_TAG_MARCH_BUTTON_GET_NEXT 位置按钮领取奖励
  6. 接着按统一退出标志（ESC 回主界面）退出，最终返回主界面

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
    GOBAL_TAG_MARCH,
    GLOBAL_TAG_MARCH_BUTTON_GET,
    GLOBAL_TAG_MARCH_BUTTON_GET_NEXT,
)
from src.sg.scene.scene_type import SceneType


class PunishClaimTask(SGBaseTask):
    """
    讨伐奖励领取。

    =========================================================
    职责边界：
      - 只做业务编排：回主界面 → 点行军标签 → 两层领取 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Punish Claim"
        self.description = "Claim punish rewards via the march panel."

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
    # 步骤 2：点行军标签 → 两层领取按钮
    # ========================================================

    def _claim_punish_rewards(self) -> bool:
        """
        点击 GOBAL_TAG_MARCH 进入讨伐界面，
        依次点击两层领取按钮完成领取。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        # 进入讨伐界面（行军标签）
        if not self._wait_and_click(
            GOBAL_TAG_MARCH, name="行军标签", timeout=6.0
        ):
            return False
        self._sleep(1.0)

        # 第一层领取按钮
        if not self._wait_and_click(
            GLOBAL_TAG_MARCH_BUTTON_GET, name="讨伐领取按钮",
            timeout=6.0, box=full_screen,
        ):
            return False
        self._sleep(1.0)

        # 弹出界面后的下一层领取按钮
        if not self._wait_and_click(
            GLOBAL_TAG_MARCH_BUTTON_GET_NEXT, name="讨伐领取按钮-下一层",
            timeout=6.0, box=full_screen,
        ):
            return False
        self._sleep(1.0)
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始讨伐领取 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 讨伐两层领取 ----
        if not self._step(self._claim_punish_rewards, "讨伐领取"):
            self.last_error = "讨伐领取失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 退回主界面（失败不影响领取结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响领取结果")

        self.log_info("========== 讨伐领取完成 ==========")
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
        self.log_info("========== 讨伐领取（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"讨伐领取失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 讨伐领取完成 ==========")
        return True
