"""
银行存取领域对象（Bank）。

职责：
  - 从主界面进入超值活动界面
  - 在滑动区域内灰度查找银行头部入口（global_sss_activity_head），
    未找到向左滑 1/4 屏，最多 scroll_max 次
  - 点击头部入口弹出存期选择，按 bankdays 点击对应存期标签
  - 点击确认存储按钮完成存入

不负责：场景判定兜底、弹窗清理、队列调度。
"""

from src.sg.scene.elements import (
    GLOBAL_ENTRY_GREATVALUE_ACTIVITY,
    GLOBAL_SSS_ACTIVITY_HEAD,
    ACTIVITY_TAG_BANK_SAVE_1DAY,
    ACTIVITY_TAG_BANK_SAVE_7DAY,
    ACTIVITY_TAG_BANK_SAVE_15DAY,
    ACTIVITY_TAG_BANK_SAVE_30DAY,
    ACTIVITY_TAG_BANK_BUTTON_SAVE,
    ACTIVITY_TAG_BANK_ICON_NOT_SELECT,
)


class Bank:
    """
    银行存取。

    对外接口：
      open_activity()  -> bool  主界面进入超值活动界面
      find_bank_head() -> bool  灰度滑动查找银行头部入口并点击
      pick_save_days() -> bool  按 bankdays 点击对应存期标签
      confirm_save()   -> bool  点击确认存储按钮
    """

    # bankdays 参数值 → 存期标签元素
    SAVE_DAY_ELEMENTS = {
        1: ACTIVITY_TAG_BANK_SAVE_1DAY,
        7: ACTIVITY_TAG_BANK_SAVE_7DAY,
        15: ACTIVITY_TAG_BANK_SAVE_15DAY,
        30: ACTIVITY_TAG_BANK_SAVE_30DAY,
    }

    def __init__(self, task):
        self.task = task

        # ---- 参数 ----
        # 存期天数（1/7/15/30），队列注入后同步到 extra_config
        self.bankdays = 1
        # 滑动查找次数上限
        self.scroll_max = 6
        # 每次滑动的距离比例（1/4 屏）
        self.scroll_step = 0.25
        # 滑动后等待动画时间
        self.scroll_settle_time = 1.0
        # 入口/按钮等待超时
        self.entry_timeout = 6.0
        # 头部入口灰度匹配阈值
        self.head_threshold = 0.8

        # ---- 运行时状态 ----
        # 滑动查找阶段实际滑动的次数（诊断用）
        self.last_scroll_count = 0

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
    # 步骤 2：灰度滑动查找银行头部入口
    # ========================================================

    def find_bank_head(self) -> bool:
        """
        在 global_sss_activity_head 区域 bbox 内灰度查找头部入口。

        区域与特征同名：get_box_by_name 定位区域 bbox，
        _find(head, box=区域, use_gray_scale=True) 在区域内匹配。
        未找到向左滑 1/4 屏，最多 scroll_max 次；耗尽返回 False。
        找到后点击头部入口，等待存期选择界面出现。
        """
        self.last_scroll_count = 0

        for attempt in range(self.scroll_max + 1):
            head_box = self._find_head_in_area()
            if head_box is not None:
                if attempt > 0:
                    self.task.log_info(
                        f"向左滑动 {attempt} 次后找到银行头部入口"
                    )
                self.last_scroll_count = attempt
                return self._click_head(head_box)

            if attempt < self.scroll_max:
                self.task.log_info(
                    f"未找到银行头部入口，向左滑动 1/4 屏 ({attempt + 1})"
                )
                self._swipe_left_by_area()

        self.task.log_error(
            f"向左滑动 {self.scroll_max} 次后仍未找到银行头部入口"
        )
        return False

    def _find_head_in_area(self):
        """
        区域 bbox 内灰度查找头部入口，返回 Box / None。

        区域 bbox 不可用（当前屏没有该标注）时返回 None，
        交由上层继续滑动重试。
        """
        area_box = self.task._safe_box(GLOBAL_SSS_ACTIVITY_HEAD)
        if area_box is None:
            self.task.log_info("未找到银行头部区域 bbox")
            return None

        return self.task._find(
            ACTIVITY_TAG_BANK_ICON_NOT_SELECT,
            threshold=self.head_threshold,
            box=area_box,
            use_gray_scale=True,
        )

    def _swipe_left_by_area(self):
        """
        以滑动区域（GLOBAL_SSS_ACTIVITY_HEAD bbox）为基准向左滑 1/4 屏。

        水平滑动的 y 线取区域中心 y（换算为屏幕相对 0~1，不用屏幕
        中间 0.5——活动页滑动区通常偏上，固定 0.5 会滑到错误位置）；
        x 跨度 = 区域宽度 × scroll_step，同样换算为屏幕相对值。
        区域 bbox 不可用时退回屏幕中心相对坐标（y=0.5）。
        """
        area_box = self.task._safe_box(GLOBAL_SSS_ACTIVITY_HEAD)
        if area_box is None:
            self.task.log_info("滑动区域 bbox 不可用，退回屏幕中心滑动")
            self.task.swipe_relative(
                0.625, 0.5, 0.375, 0.5,
                duration=0.3, settle_time=self.scroll_settle_time,
            )
            return

        screen_w = self.task.width
        screen_h = self.task.height
        center_x, center_y = area_box.center()
        # 区域中心与跨度：像素 → 屏幕相对 0~1（swipe_relative 语义）
        rel_cx = center_x / screen_w
        rel_cy = center_y / screen_h
        rel_span = (area_box.width * self.scroll_step) / screen_w
        from_x = rel_cx + rel_span / 2
        to_x = rel_cx - rel_span / 2
        self.task.log_info(
            f"按区域滑动: y={rel_cy:.3f}, "
            f"x {from_x:.3f} -> {to_x:.3f} (跨度 {rel_span:.3f})"
        )
        self.task.swipe_relative(
            from_x, rel_cy, to_x, rel_cy,
            duration=0.3, settle_time=self.scroll_settle_time,
        )

    def _click_head(self, head_box) -> bool:
        """点击头部入口，等待存期选择界面弹出。"""
        if not self.task.click(head_box):
            return False
        self.task._sleep(1.0)

        # 存期标签任一可见即确认弹出
        if not self.task._wait_element(
            list(self.SAVE_DAY_ELEMENTS.values()),
            timeout=self.entry_timeout,
            threshold=self.head_threshold,
            with_recovery=False,
        ):
            self.task.log_info("点击头部入口后未弹出存期选择界面")
            return False
        return True

    # ========================================================
    # 步骤 3：选择存期
    # ========================================================

    def pick_save_days(self) -> bool:
        """按 bankdays 点击对应存期标签。"""
        element = self.SAVE_DAY_ELEMENTS.get(self.bankdays)
        if element is None:
            self.task.log_error(
                f"无效的存期参数 bankdays={self.bankdays}，"
                f"可选值: {sorted(self.SAVE_DAY_ELEMENTS)}"
            )
            return False

        if not self.task._wait_and_click(
            element,
            timeout=self.entry_timeout,
        ):
            self.task.log_info(f"未找到 {self.bankdays} 天存期标签")
            return False
        self.task._sleep(1.0)
        return True

    # ========================================================
    # 步骤 4：确认存储
    # ========================================================

    def confirm_save(self) -> bool:
        """点击确认存储按钮完成存入。"""
        if not self.task._wait_and_click(
            ACTIVITY_TAG_BANK_BUTTON_SAVE,
            timeout=self.entry_timeout,
        ):
            self.task.log_info("未找到确认存储按钮")
            return False
        self.task._sleep(1.5)
        return True
