"""
踏浪活动任务（Wave Ride）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击 global_entry_greatvalue_activity 进入超值活动选择界面
  3. 在 global_sss_activity_head 区域 bbox 内灰度查找踏浪标签：
     - 找到 → 点击，切换到踏浪活动界面
     - 未找到 → 向左滑 1/4 屏，最多 6 次
     - 仍未找到 → 向右滑 1/4 屏，最多 6 次
     - 耗尽 → 任务失败（FAILED）
  4. 踏浪界面三分支：
     a) 匹配"踏浪-一键领取" → 点击领取 → 2s 后点"踏浪-一键派遣"
        → 下次触发 = 现在 + dispatch_wait_hours（默认 8h）
     b) 未匹配一键领取但匹配"踏浪-探索" → 灰度匹配"踏浪-一键派遣"：
        命中 → 点击 → 下次触发 = 现在 + dispatch_wait_hours
        未命中 → OCR"踏浪-剩余时间" → 下次触发 = 现在 + OCR 秒数
     c) 都未匹配 → OCR"踏浪-剩余时间" → 下次触发 = 现在 + OCR 秒数
        （OCR 失败兜底 dispatch_wait_hours）
  5. 退回主界面（失败不影响结果）

调度参数：
  - 不占军队队列（requires_march_queue=False，纯 UI 操作）
  - next_trigger_delay=0：下次触发时间全部由 SUCCESS 返回的
    wait_seconds 动态决定（8h / OCR 秒数），配置再叠加会双倍延时
  - 不可并行：活动界面同一时刻只能打开一个（count=1）
  - count=0：无限循环（每次 SUCCESS 后由队列建 follower）
"""
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.activity.wave_ride import WaveRide


class WaveRideTask(SGBaseTask):
    """
    踏浪。

    =========================================================
    职责边界：
      - 只做业务编排：进超值活动 → 双向滑动找踏浪标签 →
        领取/派遣/OCR 三分支 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
      - 交互细节（滑动、灰度匹配、OCR 解析）交给 WaveRide
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Wave Ride"
        self.description = (
            "Wave ride activity: claim rewards, dispatch exploration "
            "ships, and reschedule by OCR remaining time."
        )

        # ====================================================
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 派遣后的下次触发小时数（8 = 8 小时）
        self.dispatch_wait_hours = 8

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
        self.wave_ride = WaveRide(self)

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始踏浪 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        success, _scene = self._ensure_main_scene()
        if not success:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 同步参数到领域对象 ----
        self.wave_ride.dispatch_wait_seconds = (
            int(self.dispatch_wait_hours) * 3600
        )

        # ---- 进入超值活动 ----
        if not self._step(self.wave_ride.open_activity, "进入超值活动"):
            self.last_error = "进入超值活动失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 双向灰度滑动查找踏浪标签 ----
        if not self._step(
            self.wave_ride.find_wave_ride_tag, "查找踏浪标签"
        ):
            self.last_error = "滑动查找踏浪标签失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 三分支处理（领取/派遣/OCR）----
        if not self._step(self.wave_ride.handle_activity, "踏浪处理"):
            self.last_error = "踏浪界面处理失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        wait_seconds = int(self.wave_ride.next_wait_seconds or 0)
        self.log_info(
            f"踏浪处理完成（{self.wave_ride.last_action}），"
            f"下次触发 {wait_seconds}s 后"
        )

        # ---- 退回主界面（失败不影响踏浪结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响踏浪结果")

        self.log_info("========== 踏浪完成 ==========")
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
        self.log_info("========== 踏浪（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"踏浪失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 踏浪完成 ==========")
        return True
