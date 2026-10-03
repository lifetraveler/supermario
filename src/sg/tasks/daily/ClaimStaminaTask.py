"""
领取体力任务（Claim Stamina）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击主界面用户头像 global_player_headphoto，进入用户界面
  3. 点击 global_page_player_stamina_button_add，进入体力领取界面
  4. 找 global_page_player_stamina_button_add_next 领取按钮：
     - 找到 → 点击领取 → 弹出领取成功界面 → ESC 返回上一层
       → 下次领取时间 = 当前时间 + claim_cooldown
     - 没找到 → OCR global_page_player_stamina_add_waittime 倒计时区域
       → 下次领取时间 = 当前时间 + 倒计时
  5. 退回主界面

调度语义（与 TaskQueue 对齐）：
  - run_interaction() 返回 (SUCCESS, wait_seconds)，wait_seconds =
    距下次可领取的秒数。队列把任务置 IN_PROGRESS，到点后
    check_completed() 放行 DONE，follower 按 next_trigger_delay=0
    在同一时刻触发下一次领取。
  - 因此注册为 count=0（无限）+ next_trigger_delay=0，
    循环节奏完全由本任务的动态等待驱动。
"""

import re
import time
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_PLAYER_HEADPHOTO,
    GLOBAL_PAGE_PLAYER_STAMINA_BUTTON_ADD,
    GLOBAL_PAGE_PLAYER_STAMINA_BUTTON_ADD_NEXT,
    GLOBAL_PAGE_PLAYER_STAMINA_ADD_WAITTIME,
)
from src.sg.scene.scene_type import SceneType


class ClaimStaminaTask(SGBaseTask):
    """
    领取体力。

    =========================================================
    职责边界：
      - 只做业务编排：进用户界面 → 进体力领取界面 → 领取 /
        读倒计时 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 领取按钮等待时间：按钮可能要等界面动画结束才出现
    claim_button_timeout = 3.0

    # 倒计时 OCR 的兜底间隔：识别失败时按此间隔重试（秒）
    default_wait_seconds = 3600.0

    # 动态等待下限：避免刚领完立刻重跑空转（秒）
    min_wait_seconds = 60.0

    # 点击领取后等待领取成功弹窗的时间（秒）
    claim_popup_wait = 1.5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Claim Stamina"
        self.description = "领取体力。Claim free stamina and wait for next refresh."

        # ====================================================
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 领取成功后到下次可领取的间隔（秒），由注册 extra_config 注入
        self.claim_cooldown = 3600.0

        # 个性化参数容器
        self.extra_config = types.SimpleNamespace()

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # ====================================================
        # 内部状态：下次可领取的时间戳（本地纪元秒）
        # ====================================================
        self.next_claim_time = None

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试（标准实现，勿改）
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
    # 步骤 2：主界面 → 用户界面 → 体力领取界面
    # ========================================================

    def _enter_player_page(self) -> bool:
        """点击主界面用户头像，进入用户界面。"""
        if not self._wait_and_click(
            GLOBAL_PLAYER_HEADPHOTO,
            timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1),
        ):
            return False
        self._sleep(1.0)
        return True

    def _enter_stamina_page(self) -> bool:
        """点击用户界面的体力入口按钮，进入体力领取界面。"""
        if not self._wait_and_click(
            GLOBAL_PAGE_PLAYER_STAMINA_BUTTON_ADD,
            timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1),
        ):
            return False
        self._sleep(1.0)
        return True

    # ========================================================
    # 步骤 3：领取 / 读倒计时
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
        OCR 倒计时区域，返回距下次可领取的秒数。
        识别 / 解析失败返回 None。
        """
        box = self.get_box_by_name(
            GLOBAL_PAGE_PLAYER_STAMINA_ADD_WAITTIME.resource_id
        )
        if box is None:
            self.log_info("未找到倒计时区域 bbox")
            return None

        try:
            results = self.ocr(box=box)
        except Exception as e:
            self.log_info(f"OCR 倒计时异常: {e}")
            return None

        if not results:
            self.log_info("倒计时区域 OCR 无结果")
            return None

        text = "".join(r.name for r in results)
        seconds = self._parse_countdown_text(text)
        if seconds is None:
            self.log_info(f"倒计时文本解析失败: {text}")
        return seconds

    def _claim_once(self):
        """
        尝试领取一次体力。

        返回 (claimed, wait_seconds)：
          - claimed=True  已点击领取，wait = claim_cooldown
          - claimed=False 未到领取时间，wait = OCR 倒计时（失败用兜底值）
        """
        next_btn = self._wait_element(
            GLOBAL_PAGE_PLAYER_STAMINA_BUTTON_ADD_NEXT,
            timeout=self.claim_button_timeout,
            box=self.box_of_screen(0, 0, 1, 1),
            with_recovery=False,
        )

        if next_btn is None:
            # 未找到领取按钮 → 读倒计时
            wait = self._read_next_wait_seconds()
            if wait is None:
                self.log_warning(
                    f"未找到领取按钮且倒计时识别失败，"
                    f"使用默认间隔 {self.default_wait_seconds:.0f}s"
                )
                wait = self.default_wait_seconds
            self.log_info(f"暂不可领取，下次领取倒计时 {wait:.0f}s")
            return (False, float(wait))

        # 找到领取按钮 → 点击领取
        self.log_info("点击领取体力按钮")
        if not self.click(next_btn):
            return (False, self.default_wait_seconds)
        self._sleep(self.claim_popup_wait)

        # 领取成功弹窗出现 → ESC 返回上一层
        self.log_info("领取完成，按 ESC 返回上一层")
        self.recovery._try_press_esc()
        self._sleep(1.0)
        return (True, float(self.claim_cooldown))

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)，
        wait_seconds = 距下次可领取的秒数。
        """
        self.log_info("========== 开始领取体力 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入用户界面 ----
        if not self._step(self._enter_player_page, "进入用户界面"):
            self.last_error = "进入用户界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入体力领取界面 ----
        if not self._step(self._enter_stamina_page, "进入体力领取界面"):
            self.last_error = "进入体力领取界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 领取 / 读倒计时 ----
        if not self._step(
            lambda: self._do_claim(), "领取体力"
        ):
            self.last_error = "领取体力失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 退回主界面（失败不影响领取结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响领取结果")

        wait_seconds = max(0.0, self.next_claim_time - time.time())
        self.log_info(
            f"========== 领取体力完成，下次领取 "
            f"{wait_seconds:.0f}s 后 =========="
        )
        return (InteractionResult.SUCCESS, wait_seconds)

    def _do_claim(self) -> bool:
        """_step 包装用：执行领取并写入 self.next_claim_time。"""
        claimed, wait = self._claim_once()
        self.next_claim_time = time.time() + wait
        self.info_set("Claimed", claimed)
        self.info_set("Next Claim In", f"{wait / 60:.0f} min")
        return True

    def check_completed(self) -> bool:
        """
        IN_PROGRESS 到点后的二次校验：
        到达下次可领取时间才算完成，让 follower 精准接续。
        """
        if self.next_claim_time is None:
            return True
        return time.time() >= self.next_claim_time - 1.0

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
        self.log_info("========== 领取体力（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"领取体力失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info(
            f"========== 领取体力完成，"
            f"下次领取 {wait_seconds:.0f}s 后 =========="
        )
        return True
