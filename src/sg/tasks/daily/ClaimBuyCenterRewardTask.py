"""
储值中心每日免费奖励领取任务（Claim Buy Center Reward）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击主界面 city_daily_free_reward_entry 进入储值中心
  3. 循环（退出条件：CITY_DAILY_FREE_REWARD_AREA 区域内检索不到
     未领取标记 remind / remind_left，两侧均无红点才算完成）：
     a. 在 CITY_DAILY_FREE_REWARD_MASK_AREA 区域 OCR 当前礼包名
     b. OCR 文本包含"随心自选" → 点击 CITY_DAILY_FREE_REWARD_BUY_CENTER_BOX
        → 全局检索 GLOBAL_MARK_REWARD_GETED_QUIT_TIP：
          - 找到 → 已进入领取成功界面，点击一次关闭
          - 未找到 → 不处理
     c. 在 area 区域检索 remind_left / remind（多候选）：
        - 找到 → 点击，翻到下一个活动 tag，继续循环
        - 找不到 → 循环退出
  4. 退回主界面

其他礼包的领取分支：预留 register_gift_hook 扩展点，
后续补充各礼包特征后挂接，流程与随心自选一致。

调度参数：
  - 不占军队队列（requires_march_queue=False）
  - 每日一次（next_trigger_delay=86400）
  - 不可并行：储值中心界面同一时刻只能打开一个
"""

import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    CITY_DAILY_FREE_REWARD_ENTRY,
    CITY_DAILY_FREE_REWARD_BUY_CENTER,
    CITY_DAILY_FREE_REWARD_AREA,
    CITY_DAILY_FREE_REWARD_MASK_AREA,
    CITY_DAILY_FREE_REWARD_BUY_CENTER_BOX,
    CITY_DAILY_FREE_REWARD_BUY_CENTER_BOX_1,
    CITY_DAILY_FREE_REWARD_BUY_CENTER_REMIND,
    CITY_DAILY_FREE_REWARD_BUY_CENTER_REMIND_LEFT,
    GLOBAL_MARK_REWARD_GETED_QUIT_TIP,
)
from src.sg.scene.scene_type import SceneType


class ClaimBuyCenterRewardTask(SGBaseTask):
    """
    储值中心每日免费奖励领取。

    =========================================================
    职责边界：
      - 只做业务编排：进储值中心 → 循环识别礼包并领取 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 循环轮数上限（防红点特征误检/界面异常导致死循环）
    claim_rounds_max = 10

    # 礼包标题 OCR 失败时的重试间隔（秒）
    mask_ocr_retry_interval = 1.0
    mask_ocr_retry_max = 3

    # 各礼包的领取分支：OCR 关键词 → 处理方法名。
    # 新增礼包：注册一行 + 写对应分支方法（签名 (self) -> bool）。
    # 每周特惠：特征待定，暂无分支，OCR 命中后翻页跳过。
    gift_handlers = {
        "随心自选": "_claim_buy_center_box",
        "每日特惠": "_claim_daily_special_box",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Claim Buy Center Reward"
        self.description = "Claim daily free rewards in the buy center."

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # 个性化参数容器
        self.extra_config = types.SimpleNamespace()

        # 领域对象
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

    # ========================================================
    # 步骤 1：回到主界面（城市 / 世界地图，统一使用父级方法）
    # ========================================================


    # ========================================================
    # 步骤 2：进入储值中心
    # ========================================================

    def _open_buy_center(self) -> bool:
        """
        city 主界面 → 点击储值中心入口 → 确认储值中心标题出现。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        if not self._wait_and_click(
            CITY_DAILY_FREE_REWARD_ENTRY,
            timeout=2.0, box=full_screen,
        ):
            self.log_error("未找到储值中心入口")
            return False
        self._sleep(1.0)

        if self._wait_element(
            CITY_DAILY_FREE_REWARD_BUY_CENTER,
            timeout=2.0, box=full_screen, with_recovery=False,
        ) is None:
            self.log_error("未进入储值中心（标题未出现）")
            return False
        return True

    # ========================================================
    # 步骤 3：循环领取
    # ========================================================

    def _has_unclaimed_mark(self, area_box) -> bool:
        """
        在 area 区域检索未领取标记（remind / remind_left 双候选）。
        返回 True 表示当前 tag 仍有未领取礼包。
        """
        box = self._wait_element(
            [CITY_DAILY_FREE_REWARD_BUY_CENTER_REMIND,
             CITY_DAILY_FREE_REWARD_BUY_CENTER_REMIND_LEFT],
            timeout=2.0, box=area_box, with_recovery=False,
        )
        return box is not None

    def _ocr_current_gift(self, area_box):
        """
        在 mask_area 区域 OCR 当前礼包名。
        返回文本或 None（OCR 失败重试 mask_ocr_retry_max 次）。
        """
        box = self._safe_box(CITY_DAILY_FREE_REWARD_MASK_AREA)
        if not box:
            self.log_error("未找到礼包标题 OCR 区域 bbox")
            return None

        for attempt in range(1, self.mask_ocr_retry_max + 1):
            text = self._ocr_text(box)
            if text:
                self.log_info(f"当前礼包 OCR: {text}")
                return text
            self.log_info(f"礼包标题 OCR 无结果（{attempt}/{self.mask_ocr_retry_max}）")
            self._sleep(self.mask_ocr_retry_interval)
        return None

    def _claim_buy_center_box(self,*args, **kwargs) -> bool:
        """
        随心自选礼包领取分支（按用户需求原文）：
          1. 全屏检索点击 CITY_DAILY_FREE_REWARD_BUY_CENTER_BOX
             （礼包图标在 area bbox 之外，不能用 area_box 限制搜索区域）
          2. 全局检索 GLOBAL_MARK_REWARD_GETED_QUIT_TIP：
             - 找到 → 已进入领取成功界面，点击一次关闭
             - 未找到 → 不处理
        """
        return self._click_gift_and_dismiss(
            CITY_DAILY_FREE_REWARD_BUY_CENTER_BOX, "随心自选")

    def _claim_daily_special_box(self,*args, **kwargs) -> bool:
        """
        每日特惠礼包领取分支：流程与随心自选一致，
        特征用 CITY_DAILY_FREE_REWARD_BUY_CENTER_BOX_1。
        """
        return self._click_gift_and_dismiss(
            CITY_DAILY_FREE_REWARD_BUY_CENTER_BOX_1, "每日特惠")

    def _click_gift_and_dismiss(self, gift_element, gift_label) -> bool:
        """
        礼包领取公共流程：全屏检索点击礼包图标 →
        等待领取成功界面（quit_tip）出现则点击关闭，未出现不处理。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        if not self._wait_and_click_direct(
            gift_element
        ):
            self.log_info(f"未匹配到{gift_label}礼包图标")
            return False

        self._sleep(1.0)

        # 全局检索领取成功界面
        quit_box = self._wait_element(
            GLOBAL_MARK_REWARD_GETED_QUIT_TIP,
            timeout=4.0, box=full_screen, with_recovery=False,
        )
        if quit_box is not None:
            self.log_info("进入领取成功界面，点击关闭")
            self.click(quit_box)
            self._sleep(1.0)
        else:
            self.log_info("未进入领取成功界面，不处理")
        return True

    def _goto_next_tag(self, area_box) -> bool:
        """
        在 area 区域点击未领取标记（remind_left / remind 多候选），
        翻到下一个活动 tag。
        """
        if not self._wait_and_click(
            [CITY_DAILY_FREE_REWARD_BUY_CENTER_REMIND_LEFT,
             CITY_DAILY_FREE_REWARD_BUY_CENTER_REMIND],
            timeout=2.0, box=area_box, with_recovery=False,
        ):
            self.log_info("未找到未领取标记，无法翻页")
            return False
        self._sleep(1.0)
        return True

    def _claim_loop(self) -> bool:
        """
        领取主循环（按用户需求原文）：
          退出条件：area 区域检索不到 remind / remind_left。
          每轮：OCR 礼包名 → 分发到对应礼包处理分支 → 翻下一 tag。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)
        area_box = self._safe_box(CITY_DAILY_FREE_REWARD_AREA)
        if not area_box:
            self.log_error("未找到储值中心主体区域 bbox")
            return False

        for round_no in range(1, self.claim_rounds_max + 1):
            self.log_info(f"---- 领取轮次 #{round_no} ----")

            # 识别当前礼包
            text = self._ocr_current_gift(area_box)
            if text is None:
                self.last_error = "礼包标题 OCR 失败"
                return False

            # 分发到对应礼包处理分支
            handler_name = None
            for keyword, method in self.gift_handlers.items():
                if keyword in text:
                    handler_name = method
                    break

            if handler_name is not None:
                if not self._step(getattr(self, handler_name),
                                  f"领取礼包#{round_no}",
                                  area_box=area_box):
                    self.last_error = f"礼包领取分支失败: {handler_name}"
                    return False
            else:
                self.log_info(f"礼包 '{text}' 暂无领取分支，翻到下一个 tag")

            # 翻到下一个活动 tag
            if not self._step(self._goto_next_tag, f"翻页#{round_no}",
                              area_box=area_box, with_recovery=False):
                self.log_info("翻页失败（红点可能刚被领取消失），结束循环")
                return True
                        # 退出条件：两侧红点均检索不到
            if not self._has_unclaimed_mark(area_box):
                self.log_info("未领取标记已全部消失，领取完成")
                return True
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
        self.log_info("========== 开始储值中心领取 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        success,scene = self._ensure_main_scene()
        if not success:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)            
        # ---- 进入储值中心 ----
        if not self._step(self._open_buy_center, "进入储值中心"):
            self.last_error = "进入储值中心失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 循环领取（失败不阻塞退回主界面）----
        if not self._step(self._claim_loop, "循环领取"):
            self.log_info("循环领取失败，不影响整体结果")

        # ---- 退回主界面（失败不影响领取结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响领取结果")

        self.log_info("========== 储值中心领取完成 ==========")
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
        self.log_info("========== 储值中心领取（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"储值中心领取失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 储值中心领取完成 ==========")
        return True
