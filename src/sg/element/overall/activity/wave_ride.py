"""
踏浪活动领域对象（WaveRide）。

职责：
  - 从主界面进入超值活动界面（复用 BankSaveTask 同款入口）
  - 在滑动区域（GLOBAL_SSS_ACTIVITY_HEAD bbox）内灰度查找踏浪标签：
    未找到先向左滑 1/4 屏，最多 left_max 次；
    再向右滑，最多 right_max 次；耗尽返回 False
  - 点击踏浪标签，切换到踏浪活动界面
  - 分支处理：
      a) 匹配"踏浪-一键领取" → 点击领取 → 2s 后点"踏浪-一键派遣" → +8h
      b) 匹配"踏浪-探索"     → 灰度匹配"踏浪-一键派遣"，命中即点 → +8h
      c) 都未命中            → 在"踏浪-剩余时间"区域 OCR 剩余时间，
        解析失败兜底 8h

不负责：场景判定兜底、弹窗清理、队列调度。
"""

import re


from src.sg.scene.elements import (
    GLOBAL_ENTRY_GREATVALUE_ACTIVITY,
    GLOBAL_SSS_ACTIVITY_HEAD,
    ACTIVITY_TAG_WAVE_RIDE,
    ACTIVITY_TAG_WAVE_RIDE_CLAIM_ALL,
    ACTIVITY_TAG_WAVE_RIDE_EXPLORE,
    ACTIVITY_TAG_WAVE_RIDE_DISPATCH,
    ACTIVITY_TAG_WAVE_RIDE_REMAIN_TIME,
)


class WaveRide:
    """
    踏浪。

    职责：双向滑动找踏浪标签 + 领取/派遣/OCR 三分支交互。
    不负责：调度时间决策（wait_seconds 由 Task 计算返回）。
    """

    def __init__(self, task):
        self.task = task

        # ---- 参数 ----
        # 派遣后的下次触发间隔（秒），队列注入 dispatch_wait_hours 后同步
        self.dispatch_wait_seconds = 8 * 3600
        # 向左滑动查找次数上限
        self.scroll_left_max = 6
        # 向右滑动查找次数上限
        self.scroll_right_max = 6
        # 每次滑动的距离比例（1/4 屏）
        self.scroll_step = 0.25
        # 滑动后等待动画时间
        self.scroll_settle_time = 1.0
        # 入口/按钮等待超时
        self.entry_timeout = 2.0
        # 灰度匹配阈值
        self.head_threshold = 0.8
        # 一键领取到一键派遣的间隔
        self.claim_to_dispatch_wait = 2.0
        # OCR 不到剩余时间时的兜底（秒）
        self.default_remain_seconds = 8 * 3600

        # ---- 运行时状态 ----
        # 本轮执行的动作（claim / dispatch / ocr），诊断用
        self.last_action = None
        # 本轮计算的下次触发秒数（Task 直接取用）
        self.next_wait_seconds = None

    # ========================================================
    # 步骤 1：进入超值活动界面
    # ========================================================

    def open_activity(self) -> bool:
        """点击主界面超值活动入口，进入活动选择界面。"""
        if not self.task._wait_and_click(
            GLOBAL_ENTRY_GREATVALUE_ACTIVITY,
            timeout=self.entry_timeout,
        ):
            self.task.log_info("未找到超值活动入口")
            return False
        self.task._sleep(1.0)
        return True

    # ========================================================
    # 步骤 2：双向灰度滑动查找踏浪标签
    # ========================================================

    def find_wave_ride_tag(self) -> bool:
        """
        在 global_sss_activity_head 区域 bbox 内灰度查找踏浪标签。

        未找到 → 向左滑 1/4 屏，最多 scroll_left_max 次；
        仍未找到 → 向右滑，最多 scroll_right_max 次；
        全部耗尽返回 False。找到后点击标签，等待踏浪界面出现。
        """
        # 先试当前位置
        tag_box = self._find_tag_in_area()
        if tag_box is not None:
            self.task.log_info("当前位置找到踏浪标签")
            return self._click_tag(tag_box)

        # 向左找
        for attempt in range(self.scroll_left_max):
            self.task.log_info(
                f"未找到踏浪标签，向左滑动 1/4 屏 ({attempt + 1})"
            )
            self._swipe_by_area(left=True)
            tag_box = self._find_tag_in_area()
            if tag_box is not None:
                self.task.log_info(f"向左滑动 {attempt + 1} 次后找到踏浪标签")
                return self._click_tag(tag_box)

        # 向右找（回到起点附近再向右：左滑 scroll_left_max 次后
        # 再向右滑 2*scroll_left_max 次的距离即可越过起点）
        self.task.log_info("向左滑动耗尽，向右滑动继续查找")
        for attempt in range(self.scroll_right_max):
            self.task.log_info(
                f"未找到踏浪标签，向右滑动 1/4 屏 ({attempt + 1})"
            )
            self._swipe_by_area(left=False)
            tag_box = self._find_tag_in_area()
            if tag_box is not None:
                self.task.log_info(f"向右滑动 {attempt + 1} 次后找到踏浪标签")
                return self._click_tag(tag_box)

        self.task.log_error(
            f"左右各滑 {self.scroll_left_max}/{self.scroll_right_max} 次"
            "仍未找到踏浪标签"
        )
        return False

    def _find_tag_in_area(self):
        """
        区域 bbox 内灰度查找踏浪标签，返回 Box / None。

        区域 bbox 不可用（当前屏没有该标注）时返回 None，
        交由上层继续滑动重试。
        """
        area_box = self.task._safe_box(GLOBAL_SSS_ACTIVITY_HEAD)
        if area_box is None:
            self.task.log_info("未找到活动头部区域 bbox")
            return None

        return self.task._find(
            ACTIVITY_TAG_WAVE_RIDE,
            threshold=self.head_threshold,
            box=area_box,
            use_gray_scale=True,
        )

    def _swipe_by_area(self, left: bool):
        """
        以滑动区域 bbox 为基准滑 1/4 屏（left=True 向左，否则向右）。

        水平滑动的 y 线取区域中心 y（换算为屏幕相对 0~1）；
        x 跨度 = 区域宽度 × scroll_step，同样换算为屏幕相对值。
        区域 bbox 不可用时退回屏幕中心相对坐标（y=0.5）。
        """
        area_box = self.task._safe_box(GLOBAL_SSS_ACTIVITY_HEAD)
        if area_box is None:
            self.task.log_info("滑动区域 bbox 不可用，退回屏幕中心滑动")
            from_x, to_x = (0.625, 0.375) if left else (0.375, 0.625)
            self.task.swipe_relative(
                from_x, 0.5, to_x, 0.5,
                duration=0.3, settle_time=self.scroll_settle_time,
            )
            return

        screen_w = self.task.width
        screen_h = self.task.height
        center_x, center_y = area_box.center()
        rel_cx = (center_x + (area_box.width / 4)) / screen_w
        rel_cy = center_y / screen_h
        rel_span = (area_box.width * self.scroll_step) / screen_w
        from_x = rel_cx - rel_span / 2
        to_x = rel_cx + rel_span / 2
        if left:
            from_x, to_x = to_x, from_x
        self.task.log_info(
            f"按区域滑动({'左' if left else '右'}): y={rel_cy:.3f}, "
            f"x {from_x:.3f} -> {to_x:.3f} (跨度 {rel_span:.3f})"
        )
        self.task.swipe_relative(
            from_x, rel_cy, to_x, rel_cy,
            duration=0.3, settle_time=self.scroll_settle_time,
        )

    def _click_tag(self, tag_box) -> bool:
        """点击踏浪标签，确认踏浪界面切换成功。"""
        if not self.task.click(tag_box):
            return False
        self.task._sleep(1.0)

        # 任一踏浪界面元素可见即确认切换成功
        if not self.task._wait_element(
            [
                ACTIVITY_TAG_WAVE_RIDE_CLAIM_ALL,
                ACTIVITY_TAG_WAVE_RIDE_EXPLORE,
                ACTIVITY_TAG_WAVE_RIDE_DISPATCH,
                ACTIVITY_TAG_WAVE_RIDE_REMAIN_TIME,
            ],
            timeout=self.entry_timeout,
            threshold=self.head_threshold,
            with_recovery=False,
        ):
            self.task.log_info("点击标签后未进入踏浪界面")
            return False
        return True

    # ========================================================
    # 步骤 3：三分支处理，结果记入 self.next_wait_seconds
    # ========================================================

    def handle_activity(self) -> bool:
        """
        踏浪界面三分支：
          a) 一键领取可见 → 领取 → 2s 后一键派遣 → wait=dispatch_wait_seconds
          b) 一键领取不可见但"探索"可见 → 灰度匹配一键派遣，命中即点
             → wait=dispatch_wait_seconds；未命中 → OCR 剩余时间
          c) 两者都不可见 → OCR 剩余时间
        处理成功返回 True（OCR 兜底也算成功），失败返回 False。
        """
        self.next_wait_seconds = None

        # 分支 a：一键领取
        claim_box = self.task._find(
            ACTIVITY_TAG_WAVE_RIDE_CLAIM_ALL,
            threshold=self.head_threshold,
        )
        if claim_box is not None:
            self.task.log_info("匹配到一键领取")
            return self._claim_then_dispatch(claim_box)

        # 分支 b：探索标志
        explore_box = self.task._find(
            ACTIVITY_TAG_WAVE_RIDE_EXPLORE,
            threshold=self.head_threshold,
        )
        if explore_box is not None:
            self.task.log_info("未匹配到一键领取，匹配到探索")
            dispatch_box = self.task._find(
                ACTIVITY_TAG_WAVE_RIDE_DISPATCH,
                threshold=self.head_threshold,
                use_gray_scale=True,
            )
            if dispatch_box is not None:
                self.task.log_info("灰度匹配到一键派遣")
                if not self._click_dispatch(dispatch_box):
                    return False
                self.last_action = "dispatch"
                self.next_wait_seconds = self.dispatch_wait_seconds
                return True
            self.task.log_info("未匹配到一键派遣，转 OCR 剩余时间")
            return self._ocr_remain_time()

        # 分支 c：都不可见
        self.task.log_info("未匹配到一键领取与探索，转 OCR 剩余时间")
        return self._ocr_remain_time()

    def _claim_then_dispatch(self, claim_box) -> bool:
        """一键领取 → 等 2s → 一键派遣 → wait=dispatch_wait_seconds。"""
        if not self.task.click(claim_box):
            return False
        self.task._sleep(self.claim_to_dispatch_wait)

        dispatch_box = self.task._find(
            ACTIVITY_TAG_WAVE_RIDE_DISPATCH,
            threshold=self.head_threshold,
            use_gray_scale=True,
        )
        if dispatch_box is None:
            self.task.log_info("领取后未匹配到一键派遣按钮")
            return False
        if not self._click_dispatch(dispatch_box):
            return False

        self.last_action = "claim+dispatch"
        self.next_wait_seconds = self.dispatch_wait_seconds
        return True

    def _click_dispatch(self, dispatch_box) -> bool:
        """点击一键派遣并等待界面稳定。"""
        if not self.task.click(dispatch_box):
            return False
        self.task._sleep(1.0)
        return True

    # ========================================================
    # 步骤 4：OCR 剩余时间
    # ========================================================

    def _ocr_remain_time(self) -> bool:
        """
        在"踏浪-剩余时间"区域 OCR 剩余时间。

        成功 → next_wait_seconds = 解析秒数；
        失败 → 兜底 default_remain_seconds。
        均返回 True（剩余时间读取失败不构成任务失败）。
        """
        self.last_action = "ocr"

        area_box = self.task._safe_box(ACTIVITY_TAG_WAVE_RIDE_REMAIN_TIME)
        if area_box is None:
            self.task.log_info("未找到剩余时间区域 bbox，使用兜底时间")
            self.next_wait_seconds = self.default_remain_seconds
            return True

        try:
            results = self.task.ocr(box=area_box)
        except Exception as e:
            self.task.log_info(f"OCR 剩余时间异常: {e}")
            self.next_wait_seconds = self.default_remain_seconds
            return True

        if not results:
            self.task.log_info("剩余时间区域 OCR 无结果，使用兜底时间")
            self.next_wait_seconds = self.default_remain_seconds
            return True

        text = (results[0].name or "").strip()
        self.task.log_info(f"剩余时间 OCR 原始文本: {text}")
        seconds = self._parse_time_text(text)
        if seconds is None or seconds <= 0:
            self.task.log_info("剩余时间解析失败，使用兜底时间")
            self.next_wait_seconds = self.default_remain_seconds
            return True

        self.next_wait_seconds = seconds
        return True

    def _parse_time_text(self, text):
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
