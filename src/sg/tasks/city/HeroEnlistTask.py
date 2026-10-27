"""
英雄招募任务（Hero Enlist）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击 global_tag_hero 进入英雄列表界面
  3. 点击 hero_tag_enlist_entry 进入招募界面
  4. 匹配 hero_add_mask_scene：
     - 命中 → 处于招募场景，继续
     - 未命中 → 结束本次招募，返回失败
  5. 招募方式二选一（enlist_mode，默认"免费"）：
     a) 免费招募：
        - 全屏灰度匹配 city_hero_button_enlist_1（可能命中 2 个）
        - 找到 → 逐个点击对应区域进入招募成功界面，
          鼠标间隔 1s 点击 hero_add_back，每点 3 次做一次
          hero_add_mask_scene 特征匹配，命中即停止，回到招募场景
        - 未找到 → 本次无免费次数，读取下次免费时间：
          匹配 hero_add_free_next_time（可能多个），每个命中区域
          组一个 OCR 框——y/height 用命中区域的，x/width 用
          hero_time_area 的——OCR 下次免费时间，解析成功 →
          SUCCESS + 动态等待秒数；失败 → SUCCESS + 兜底 86400
     b) 批量招募：
        - 循环 batch_normal_times 次：bbox 直接点击"英雄招募普通10次"
        - 循环 batch_legend_times 次：bbox 直接点击"英雄招募传奇10次"
        - 每次点击后若出现"英雄招募批量提醒界面"：
          点"英雄招募批量不再提醒" → 点"英雄招募批量确认"
        - 领取动画期间每隔 1s 按 ESC，直到 hero_add_mask_scene 重新命中
        - 次数循环完成 → 退出到主堡界面，SUCCESS + 0

调度参数：
  - 不占军队队列（requires_march_queue=False，纯 UI 操作）
  - next_trigger_delay=0：下次触发时间全部由 SUCCESS 返回的
    wait_seconds 动态决定（免费模式 OCR 到的时间 / 批量 0）
  - 不可并行：招募界面同一时刻只能打开一个（count=1）
"""

import re
import types

from ok.feature.Box import Box

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_TAG_HERO,
    HERO_TAG_ENLIST_ENTRY,
    HERO_ADD_MASK_SCENE,
    CITY_HERO_BUTTON_ENLIST_1,
    HERO_ADD_BACK,
    HERO_ADD_FREE_NEXT_TIME,
    HERO_TIME_AREA,
    HERO_RECRUIT_NORMAL_TEN,
    HERO_RECRUIT_LEGEND_TEN,
    HERO_RECRUIT_BATCH_TIP_SCENE,
    HERO_RECRUIT_BATCH_NO_REMIND,
    HERO_RECRUIT_BATCH_CONFIRM,
)


def parse_time_text(text):
    """
    把 OCR 文本解析成秒。

    兼容格式（优先级从高到低）：
      - 中文单位词: "8小时30分" / "1小时23分45秒" / "7小时"
      - 冒号分隔: "01:23:45" → 时:分:秒, "12:34" → 分:秒
      - 裸数字: "90" → 秒
    解析失败返回 None。
    """
    if not text:
        return None

    text = text.strip()

    # 中文单位词：按 数字+单位 配对累加（"8小时30分" 常见漏"秒"）
    unit_pairs = re.findall(r"(\d+)\s*(小时|时|分|秒)", text)
    if unit_pairs:
        total = 0
        for num, unit in unit_pairs:
            n = int(num)
            if unit in ("小时", "时"):
                total += n * 3600
            elif unit == "分":
                total += n * 60
            else:
                total += n
        return total

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


class HeroEnlistTask(SGBaseTask):
    """
    英雄招募。

    =========================================================
    职责边界：
      - 只做业务编排：进英雄列表 → 进招募界面 → 按模式招募 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # ========================================================
    # 招募方式与节奏常量
    # ========================================================
    ENLIST_MODE_FREE = "免费"
    ENLIST_MODE_BATCH = "批量"
    ENLIST_MODES = [ENLIST_MODE_FREE, ENLIST_MODE_BATCH]

    # 免费按钮点击后进入成功界面的前置等待
    free_click_settle = 1.0
    # 返回按钮点击间隔（需求原文：鼠标间隔 1s）
    back_click_interval = 1.0
    # 每点 back 3 次做一次招募场景校验（需求原文）
    back_clicks_per_check = 3
    # back 点击轮数上限（防特征误检死循环）
    back_rounds_max = 10
    # ESC 返回招募场景的重试上限
    esc_return_max = 20
    # OCR 下次免费时间失败时的兜底等待（每天一次免费刷新）
    default_free_wait_seconds = 86400

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Hero Enlist"
        self.description = (
            "Hero enlist: free enlist with dynamic next-free-time OCR "
            "wait, or batch (normal/legend) ten-draw enlist."
        )

        # ====================================================
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 招募方式：免费 / 批量（下拉）
        self.enlist_mode = self.ENLIST_MODE_FREE
        # 批量模式：普通 10 连次数
        self.batch_normal_times = 1
        # 批量模式：传奇 10 连次数
        self.batch_legend_times = 1

        # 个性化参数容器
        self.extra_config = types.SimpleNamespace()

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2
        self.recover_interval = 3.0

        # 领域对象
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

        # ====================================================
        # 运行时状态（next_wait_seconds 回传调度）
        # ====================================================
        self.next_wait_seconds = None
        self.last_free_time_text = None

    # ========================================================
    # 通用助手
    # ========================================================

    def _full_screen(self):
        return self.box_of_screen(0, 0, 1, 1)

    def _in_enlist_scene(self) -> bool:
        """匹配 hero_add_mask_scene，命中 = 处于招募场景。"""
        return self._find(
            HERO_ADD_MASK_SCENE, box=self._full_screen(),
        ) is not None

    def _wait_enlist_scene(self) -> bool:
        """等待招募场景特征出现。"""
        if self._wait_element(
            HERO_ADD_MASK_SCENE,
            timeout=5.0, box=self._full_screen(), with_recovery=False,
        ) is None:
            self.log_error("未匹配到招募场景特征（hero_add_mask_scene）")
            return False
        self.log_info("已进入招募场景")
        return True

    # ========================================================
    # 步骤 1：进入英雄列表
    # ========================================================

    def _open_hero_list(self) -> bool:
        """
        主城界面 → 点击 global_tag_hero → 确认招募入口出现。
        """
        full = self._full_screen()

        if not self._wait_and_click(
            GLOBAL_TAG_HERO, timeout=2.0, box=full,
        ):
            self.log_error("未找到英雄入口（global_tag_hero）")
            return False
        self._sleep(1.0)

        if self._wait_element(
            HERO_TAG_ENLIST_ENTRY,
            timeout=2.0, box=full, with_recovery=False,
        ) is None:
            self.log_error("未进入英雄列表（招募入口未出现）")
            return False
        return True

    # ========================================================
    # 步骤 2：进入招募界面并确认招募场景
    # ========================================================

    def _enter_enlist_scene(self) -> bool:
        """
        点击招募入口 → 匹配 hero_add_mask_scene 确认招募场景。
        未命中 → 结束本次招募（返回 False → FAILED）。
        """
        full = self._full_screen()

        if not self._wait_and_click(
            HERO_TAG_ENLIST_ENTRY, timeout=2.0, box=full,
        ):
            self.log_error("未找到招募入口（hero_tag_enlist_entry）")
            return False
        self._sleep(1.0)

        return self._wait_enlist_scene()

    # ========================================================
    # 步骤 3：免费模式
    # ========================================================

    def _run_free_mode(self) -> bool:
        """
        全屏灰度匹配 city_hero_button_enlist_1：
          - 找到（可能 2 个）→ 逐个点击，每个走 back 返回循环
          - 未找到 → 本次无免费次数 → 读取下次免费时间
        """
        full = self._full_screen()

        buttons = self.find_feature(
            CITY_HERO_BUTTON_ENLIST_1.resource_id,
            threshold=0.8, box=full, use_gray_scale=True,
        )
        if not buttons:
            self.log_info("未找到免费招募按钮，本次无免费次数")
            return self._read_next_free_time()

        self.log_info(f"灰度匹配到 {len(buttons)} 个免费招募按钮")
        for index, btn in enumerate(buttons, start=1):
            self.log_info(f"点击免费招募按钮 {index}/{len(buttons)}")
            if not self.click(btn):
                self.log_error(f"免费招募按钮点击失败 #{index}")
                return False
            self._sleep(self.free_click_settle)
            if not self._back_to_enlist_scene():
                return False
        return True

    def _back_to_enlist_scene(self) -> bool:
        """
        招募成功界面 → 间隔 1s 点击 hero_add_back，
        每点 3 次做一次 hero_add_mask_scene 匹配，命中即停止。
        """
        full = self._full_screen()
        clicks = 0

        for _round in range(self.back_rounds_max):
            back_box = self._find(HERO_ADD_BACK, box=full)
            if back_box is None:
                # back 消失：可能已回到招募场景
                if self._in_enlist_scene():
                    self.log_info("招募场景已恢复（back 按钮消失）")
                    return True
                self._sleep(self.back_click_interval)
                continue

            if not self.click(back_box):
                self.log_error("返回按钮点击失败")
                return False
            clicks += 1
            self._sleep(self.back_click_interval)

            if (clicks % self.back_clicks_per_check == 0
                    and self._in_enlist_scene()):
                self.log_info(f"累计点击 back {clicks} 次后回到招募场景")
                return True

        self.log_error(f"点击 back {clicks} 次仍未回到招募场景")
        return False

    # ========================================================
    # 步骤 4：OCR 下次免费时间
    # ========================================================

    def _read_next_free_time(self) -> bool:
        """
        匹配 hero_add_free_next_time（可能多个），每个命中区域组一个
        OCR 框：y/height 用命中区域的，x/width 用 hero_time_area 的。
        任一区域解析出时间 → 动态等待秒数；全部失败 → 兜底 86400。
        读取失败不构成任务失败（统一返回 True）。
        """
        full = self._full_screen()

        marks = self.find_feature(
            HERO_ADD_FREE_NEXT_TIME.resource_id,
            threshold=0.8, box=full,
        )
        area = self._safe_box(HERO_TIME_AREA)

        if not marks:
            self.log_info("未匹配到下次免费时间文字，使用兜底等待")
            self.next_wait_seconds = self.default_free_wait_seconds
            return True
        if area is None:
            self.log_info("未找到时间区域 bbox（hero_time_area），使用兜底等待")
            self.next_wait_seconds = self.default_free_wait_seconds
            return True

        # 多个命中时从上往下逐个尝试
        marks.sort(key=lambda b: (b.y, b.x))

        for index, mark in enumerate(marks, start=1):
            ocr_box = Box(
                area.x, mark.y,
                width=area.width, height=mark.height,
                name=f"hero_next_free_time_ocr_{index}",
            )
            self.log_info(
                f"下次免费时间 OCR 区域#{index}: "
                f"x={ocr_box.x}, y={ocr_box.y}, "
                f"w={ocr_box.width}, h={ocr_box.height}"
            )

            text = self._ocr_text(ocr_box)
            if not text:
                self.log_info(f"OCR 区域#{index} 无结果，尝试下一个")
                continue

            self.last_free_time_text = text
            self.log_info(f"下次免费时间 OCR: {text}")
            seconds = parse_time_text(text)
            if seconds is not None and seconds > 0:
                self.next_wait_seconds = seconds
                return True
            self.log_info(f"OCR 区域#{index} 时间解析失败，尝试下一个")

        self.log_info("所有命中区域均未解析出时间，使用兜底等待")
        self.next_wait_seconds = self.default_free_wait_seconds
        return True

    # ========================================================
    # 步骤 5：批量模式
    # ========================================================

    def _run_batch_mode(self) -> bool:
        """
        先普通后传奇：按配置次数循环点击 10 连按钮。
        """
        plans = (
            ("普通", self.batch_normal_times, HERO_RECRUIT_NORMAL_TEN),
            ("传奇", self.batch_legend_times, HERO_RECRUIT_LEGEND_TEN),
        )
        for kind, times, element in plans:
            rounds = max(0, int(times or 0))
            for i in range(1, rounds + 1):
                self.log_info(f"批量招募[{kind}] 第 {i}/{rounds} 轮")
                if not self._batch_once(element, kind):
                    return False
        return True

    def _batch_once(self, button_element, kind: str) -> bool:
        """
        单轮批量：bbox 直接点击 10 连按钮 → 批量提醒界面处理
        （不再提醒 → 确认）→ ESC 返回招募场景。
        """
        full = self._full_screen()

        box = self._safe_box(button_element)
        if box is None:
            self.log_error(f"未找到 英雄招募{kind}10次 bbox")
            return False
        self.log_info(f"点击 英雄招募{kind}10次")
        if not self.click(box):
            return False
        self._sleep(1.0)

        # 批量提醒界面：勾选不再提醒 → 确认
        tip_box = self._wait_element(
            HERO_RECRUIT_BATCH_TIP_SCENE,
            timeout=2.0, box=full, with_recovery=False,
        )
        if tip_box is not None:
            self.log_info("出现批量招募提醒界面")
            no_remind = self._safe_box(HERO_RECRUIT_BATCH_NO_REMIND)
            if no_remind is not None:
                self.click(no_remind)
                self._sleep(0.5)
            confirm = self._wait_element(
                HERO_RECRUIT_BATCH_CONFIRM,
                timeout=2.0, box=full, with_recovery=False,
            )
            if confirm is None:
                self.log_error("未找到批量确认按钮")
                return False
            self.click(confirm)
            self._sleep(1.0)

        # 领取动画：每隔 1s 按 ESC，直到回到招募场景
        return self._esc_back_to_enlist_scene()

    def _esc_back_to_enlist_scene(self) -> bool:
        """领取动画期间每隔 1s 按 ESC，直到 hero_add_mask_scene 命中。"""
        for attempt in range(self.esc_return_max):
            if self._in_enlist_scene():
                self.log_info("已回到招募场景")
                return True
            self.log_info(
                f"领取动画中，按 ESC 返回 ({attempt + 1}/{self.esc_return_max})"
            )
            try:
                self.send_key("esc")
            except Exception as e:
                self.log_info(f"按 ESC 异常: {e}")
            self._sleep(1.0)
        self.log_error("多次 ESC 后仍未回到招募场景")
        return False

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始英雄招募 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        success, _scene = self._ensure_main_scene()
        if not success:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        self.next_wait_seconds = None
        self.last_free_time_text = None

        # ---- 进入英雄列表 ----
        if not self._step(self._open_hero_list, "进入英雄列表"):
            self.last_error = "进入英雄列表失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入招募界面（未匹配到招募场景 → FAILED）----
        if not self._step(self._enter_enlist_scene, "进入招募界面"):
            self.last_error = "进入招募界面失败（未匹配到招募场景）"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 按模式招募 ----
        mode = str(self.enlist_mode or self.ENLIST_MODE_FREE).strip()
        if mode not in self.ENLIST_MODES:
            mode = self.ENLIST_MODE_FREE

        if mode == self.ENLIST_MODE_BATCH:
            ok = self._step(self._run_batch_mode, "批量招募")
        else:
            ok = self._step(self._run_free_mode, "免费招募")

        if not ok:
            self.last_error = self.last_error or f"{mode}招募失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        wait_seconds = int(self.next_wait_seconds or 0)
        self.log_info(f"招募完成（{mode}），下次触发 {wait_seconds}s 后")

        # ---- 退回主堡界面（失败不影响招募结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响招募结果")

        self.log_info("========== 英雄招募完成 ==========")
        return (InteractionResult.SUCCESS, wait_seconds)

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
        self.log_info("========== 英雄招募（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"英雄招募失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 英雄招募完成 ==========")
        return True
