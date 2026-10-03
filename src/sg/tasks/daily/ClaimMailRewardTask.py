"""
消息中心领奖任务（每日）。

流程：
  1. 确保前台，清理弹窗
  2. 若不在主界面（城市/世界地图）→ 按 ESC 回到主界面
  3. 打开消息中心（global_icon_message_entry）
  4. 依次切换 联盟 / 系统 / 报告 标签（战斗标签无奖励，不遍历）：
     每个标签内先删除已读消息 → 检测奖励标记（message_tag_reward_icon）
     → 有标记才循环点击"领取"按钮（全屏检索）
  5. 按 ESC 关闭消息中心，回到主界面

特点：
  - 不占军队队列，每日触发一次，不可并行
  - "没有可领的奖励"不是失败，是正常结束条件
"""

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper
from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_ICON_MESSAGE_ENTRY,
    MESSAGE_BUTTON_REC,
    MESSAGE_TAG_LEAGUE,
    MESSAGE_TAG_LEAGUE_1,
    MESSAGE_TAG_SYS,
    MESSAGE_TAG_REPORT,
    MESSAGE_TAG_REWARD_ICON,
    MESSAGE_TAG_REWARD_ICON_1,
    MESSAGE_BUTTON_DEL,
    MESSAGE_BUTTON_DEL_CONFIRM,
)
from src.sg.scene.scene_type import SceneType


class ClaimMailRewardTask(SGBaseTask):
    """
    领取消息中心的奖励。

    =========================================================
    职责边界：
      - 只做业务编排：回主界面 → 开消息中心 → 逐标签领取 → 关闭
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    不负责：
      - 领取频率、哪些标签要领（全部遍历）、奖励内容解析
    =========================================================
    """

    # 标签遍历顺序（类常量：固定业务顺序，无需配置）。
    # 战斗标签（MESSAGE_TAG_WAR）无奖励，保留常量但不遍历。
    MESSAGE_TAGS = (MESSAGE_TAG_LEAGUE,MESSAGE_TAG_LEAGUE_1, MESSAGE_TAG_SYS, MESSAGE_TAG_REPORT)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Claim Mail Reward"
        self.description = "Claim rewards in the message center."

        # ====================================================
        # 队列注入字段（本任务无个性化参数，保留容器以兼容 factory）
        # ====================================================
        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # 领取按钮的特征匹配阈值：领取按钮样式固定，略提阈值减少误点
        self.rec_threshold = 0.85

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试（标准实现，逐字复制）
    # ========================================================


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
    # 步骤 2：打开消息中心
    # ========================================================

    def _open_message_center(self) -> bool:
        """
        点击消息中心入口。
        city_button_sms 与 global_icon_message_entry 是同一入口的
        两个标注（城市按钮 / 全局图标），双候选并行等待。
        """
        if not self._wait_and_click(
            [GLOBAL_ICON_MESSAGE_ENTRY],
            timeout=6.0,
        ):
            return False
        self._sleep(1.0)

        # 用"领取按钮或任一标签出现"确认消息面板已打开
        panel_ready = self._wait_element(
            [MESSAGE_TAG_SYS, MESSAGE_TAG_LEAGUE, MESSAGE_TAG_REPORT],
            timeout=6.0,
        )
        if panel_ready is None:
            self.log_error("消息面板未打开（未检测到标签或领取按钮）")
            return False
        return True

    # ========================================================
    # 步骤 3：领取当前标签下所有可领奖励
    # ========================================================

    def _claim_current_tag(self) -> bool:
        """
        循环点击当前标签下的"领取"按钮（全屏检索）。
        退出条件（任一）：
          - 连续 2 轮找不到领取按钮（奖励领完 / 本来就没有）
          - 硬上限：成功 + 未命中达到 5 次（防界面异常导致死循环）

        返回值语义：
          True —— 正常结束（可能领了 0 条，没有可领的也是正常）
        """
        claimed = 0
        miss_streak = 0

        while claimed + miss_streak < 5:
            if self._wait_and_click_all_screen(
                MESSAGE_BUTTON_REC,
                timeout=1.5,
                threshold=self.rec_threshold,
                after_click_wait=0.8,
            ):
                claimed += 1
                miss_streak = 0
                # 领取后可能出现奖励弹窗，尝试关掉
                self._sleep(2)
                self.recovery._try_press_esc()
                self._sleep(1)
                # 先删除已读消息，避免旧消息遮挡 / 干扰领取判断
                self._del_readed_msg()
            else:
                miss_streak += 1
                if miss_streak >= 2:
                    break

        self.log_info(f"当前标签领取结束，共领取 {claimed} 条")
        return True

    # ========================================================
    # 步骤 4：遍历标签：删除已读 → 有奖励标记才领取
    # ========================================================

    def _claim_all_tags(self) -> bool:
        for tag in self.MESSAGE_TAGS:
            tag_name = getattr(tag, "name", str(tag))
            if not self._wait_and_click(tag, name=f"切换标签:{tag_name}", timeout=2.0):
                self.log_info(f"标签 {tag_name} 未找到，跳过")
                continue
            self._sleep(0.5)


            # 标签下没有可领标记（message_tag_reward_icon）→ 直接下一个标签
            if not self._wait_element((MESSAGE_TAG_REWARD_ICON,MESSAGE_TAG_REWARD_ICON_1),timeout=2):
                self.log_info(f"标签 {tag_name} 无可领奖励")
                # 先删除已读消息，避免旧消息遮挡 / 干扰领取判断
                self._del_readed_msg()
                continue

            if not self._claim_current_tag():
                return False
            
            
        return True

    # ========================================================
    # 步骤 5：关闭消息中心
    # ========================================================

    def _close_message_center(self) -> bool:
        """
        按 ESC 关闭消息中心。找不到关闭按钮时 ESC 是唯一兜底，
        这里直接用 ESC（与 RecoveryHelper 的按键路径一致）。
        """
        self.recovery._try_press_esc()
        self._sleep(1.0)

        scene = self.scene_detector.detect()
        self.log_info(f"关闭后场景: {scene.type.value}")
        # 没回到主界面也不算任务失败（消息面板已无后续操作）
        return scene.type in (SceneType.CITY, SceneType.WORLD_MAP) or True

    # --------------------------------------------------------
    # 删除已读消息
    # --------------------------------------------------------

    def _del_readed_msg(self) -> bool:
        """
        循环删除当前标签下的已读消息：
        点"删除"按钮 → 点确认弹窗，直到没有删除按钮。

        返回值语义：
          True —— 正常结束（一条没删也是正常）
          
        最多删除3次吧  
        """
        i=0
        while self._wait_and_click(
            MESSAGE_BUTTON_DEL, timeout=2.0, with_recovery=False,
        ) and i<3:
            self._sleep(0.5)
            self._wait_and_click(
                MESSAGE_BUTTON_DEL_CONFIRM, timeout=2.0, with_recovery=False,
            )
            self._sleep(1.0)
            i+= 1
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始领取消息中心奖励 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 打开消息中心 ----
        if not self._step(self._open_message_center, "打开消息中心"):
            self.last_error = "打开消息中心失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 遍历标签领取 ----
        if not self._step(self._claim_all_tags, "遍历标签领取"):
            self.last_error = "遍历标签领取失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 关闭消息中心 ----
        if not self._step(self._close_message_center, "关闭消息中心"):
            self.last_error = "关闭消息中心失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        self.log_info("========== 消息中心领奖完成 ==========")
        # 每日任务：完成后 24h 再触发
        return (InteractionResult.SUCCESS, self.next_trigger_delay)

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
        self.log_info("========== 消息中心领奖（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"消息中心领奖失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        if wait_seconds > 0:
            self._sleep(wait_seconds)

        self.log_info("========== 消息中心领奖完成 ==========")
        return True
