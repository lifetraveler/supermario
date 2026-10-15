"""
银行存取任务（Bank Save）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击 global_entry_greatvalue_activity 进入超值活动选择界面
  3. 在 global_sss_activity_head 区域 bbox 内灰度查找头部入口：
     - 找到 → 点击，弹出存期选择界面
     - 未找到 → 向左滑 1/4 屏，反复循环匹配，最多 6 次
     - 耗尽仍未找到 → 任务失败（不重试）
  4. 按参数 bankdays（1/7/15/30，默认 1）点击对应存期标签
  5. 点击 activity_tag_bank_button_save 确认存储
  6. 退回主界面

调度参数：
  - 不占军队队列（requires_march_queue=False，纯 UI 操作）
  - 每日一次（next_trigger_delay=86400）
  - 不可并行：活动界面同一时刻只能打开一个（count=1）
"""
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.activity.bank import Bank

from src.sg.scene.scene_type import SceneType


class BankSaveTask(SGBaseTask):
    """
    银行存取。

    =========================================================
    职责边界：
      - 只做业务编排：进超值活动 → 滑动找银行入口 → 选存期
        → 确认存储 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Bank Save"
        self.description = "Daily bank deposit via greatvalue activity."

        # ====================================================
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 存期天数：1 / 7 / 15 / 30
        self.bankdays = 1

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
        self.bank = Bank(self)

    # ========================================================
    # 步骤 1：回到主界面（城市 / 世界地图，统一使用父级方法）
    # ========================================================



    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始银行存取 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        success,scene = self._ensure_main_scene()
        if not success:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)  

        # ---- 同步参数到领域对象 ----
        self.bank.bankdays = int(self.bankdays)

        # ---- 进入超值活动 ----
        if not self._step(self.bank.open_activity, "进入超值活动"):
            self.last_error = "进入超值活动失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 灰度滑动查找银行头部入口 ----
        if not self._step(self.bank.find_bank_head, "查找银行入口"):
            self.last_error = "滑动查找银行入口失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 选择存期 ----
        if not self._step(self.bank.pick_save_days, "选择存期",with_recovery=False):
            self.last_error = "选择存期失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 确认存储 ----
        if not self._step(self.bank.confirm_save, "确认存储"):
            self.last_error = "确认存储失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 退回主界面（失败不影响存入结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响存入结果")

        self.log_info("========== 银行存取完成 ==========")
        return (InteractionResult.SUCCESS, 0)

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
        self.log_info("========== 银行存取（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"银行存取失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 银行存取完成 ==========")
        return True
