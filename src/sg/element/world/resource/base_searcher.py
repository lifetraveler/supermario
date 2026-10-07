from src.sg.scene.elements import (
    BUTTON_SEARCH_RESOURCES,
    BUTTON_RESOURCESEARCH,
    WORLD_ICON_RESOURCE_STONE,
    WORLD_ICON_RESOURCE_IRON,
    WORLD_ICON_RESOURCE_BEAST
)


class BaseResourceSearcher:
    """
    资源搜索统一父类。
    统一流程：
        搜索资源 → 选择资源类型（含滑动查找） → 选择级别 → 确认位置

    子类只需覆写：
        get_resource_element()  -> 该资源的 SceneElement
        get_level_elements()    -> {级别: SceneElement}，无级别返回 {}
    可选覆写：
        get_search_button()     -> 默认 BUTTON_SEARCH_RESOURCES
        get_confirm_button()    -> 默认 BUTTON_RESOURCESEARCH
        get_resource_threshold()-> 默认 0.5
        get_default_level()     -> 默认 None

    滑动查找：
        select_resource() 内置 "查找 → 滑动 → 再查找" 循环，
        最多滑动 scroll_max_attempts 次。
        滑动与端点锚点均按方向区分（direction 取 "right" / "left" /
        "up" / "down"，手指滑动方向与端点方向同名）。
        不需要滑动的子类把 scroll_max_attempts 设为 0 即可。
    """

    # direction -> 该方向列表端点锚点元素列表的属性名。
    # 锚点命中任一元素 = 当前视图已位于该方向的列表端点。
    _EDGE_ELEMENT_ATTRS = {
        "right": "right_edge_elements",
        "left": "left_edge_elements",
        "up": "top_edge_elements",
        "down": "bottom_edge_elements",
    }

    # direction -> 日志用方向名。
    _DIRECTION_NAMES = {
        "right": "右",
        "left": "左",
        "up": "上",
        "down": "下",
    }

    def __init__(self, task):
        self.task = task

        # ====================================================
        # 滑动查找配置
        # ====================================================

        # 最大滑动次数，防止无限滑动。
        # 资源栏通常有 6~8 个图标，一次滑半个屏，8 次足够。
        self.scroll_max_attempts = 8

        # 横向滑动的 x 坐标（屏幕相对）。
        # 从屏幕中间开始，只滑一小段（0.5 → 0.7）。
        # 游戏滑动有惯性动画，手指停下后内容还会继续走一段，
        # 所以不要滑满屏，否则会冲过头。
        self.scroll_from_x = 0.5
        self.scroll_to_x = 0.7

        # 纵向滑动的 y 坐标（屏幕相对），语义同上。
        # 向下滑动 0.5 → 0.7，向上滑动取镜像 0.5 → 0.3。
        self.scroll_from_y = 0.5
        self.scroll_to_y = 0.7

        # 滑动时长（秒）。短一点模拟"快速滑动"，让惯性生效。
        # 太短（<0.15）可能被判为点击；
        # 太长（>0.5）惯性不明显，变成精确拖拽。
        self.scroll_duration = 0.2

        # 滑动后稳定等待（秒），等惯性动画完全停下来。
        self.scroll_settle_time = 1.0

        # 滑动线兜底值（屏幕相对）。
        # 横向滑动（left/right）用 y 线，纵向滑动（up/down）用 x 线；
        # 对应方向锚点没匹配到时启用。
        self.scroll_default_y = 0.85
        self.scroll_default_x = 0.5

        # ====================================================
        # 各方向列表端点锚点元素
        # ====================================================
        # 命中任一元素 = 当前视图已位于该方向的列表端点。
        # 列表顺序即匹配优先级；不需要的方向保持空列表。

        # 资源栏右端锚点：看到石头/铁矿说明视图已滑到资源栏最右侧。
        self.right_edge_elements = [
            WORLD_ICON_RESOURCE_STONE,
            WORLD_ICON_RESOURCE_IRON,
        ]
        self.left_edge_elements = [WORLD_ICON_RESOURCE_BEAST]
        self.top_edge_elements = []
        self.bottom_edge_elements = []

    # ========================================================
    # 子类必须实现
    # ========================================================

    def get_resource_element(self):
        """返回该资源类型的 SceneElement（用于点击选择资源）。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} 必须实现 get_resource_element()"
        )

    def get_level_elements(self):
        """返回 {级别: SceneElement}，无级别概念返回 {}。"""
        return {}

    # ========================================================
    # 可选覆写
    # ========================================================

    def get_search_button(self):
        return BUTTON_SEARCH_RESOURCES

    def get_confirm_button(self):
        return BUTTON_RESOURCESEARCH

    def get_resource_threshold(self) -> float:
        return 0.85

    def get_default_level(self):
        return None

    # ========================================================
    # 统一流程
    # ========================================================

    def search(self) -> bool:
        """点击搜索资源按钮。"""
        self.task.log_info("搜索: 点击搜索资源")
        return self.task._wait_and_click(
            self.get_search_button(),
            name="搜索资源",
            timeout=8.0,
        )

    def select_resource(self) -> bool:
        """
        选择资源；找不到则沿资源栏滑动继续找。

        策略：
          1. 第一次先等 2 秒，避免界面刚打开动画未完成
          2. 找不到就向右滑动资源栏，再尝试
          3. 最多滑动 scroll_max_attempts 次

        资源栏是横向排列的，目标图标不一定总在初始视图中。
        游戏有惯性动画，滑一小段实际会多走一段，所以每次只滑一小段。
        """
        element = self.get_resource_element()

        for attempt in range(self.scroll_max_attempts + 1):
            # 第一次用短等待，捕获动画延迟的情况；
            # 后续直接扫一帧，因为滑动后界面已经稳定
            if attempt == 0:
                box = self.task._wait_element(
                    element,
                    timeout=2.0,
                    box=self.task.box_of_screen(0, 0, 1, 1),
                    threshold=self.get_resource_threshold(),
                )
            else:
                box = self.task._find(
                    element,
                    box=self.task.box_of_screen(0, 0, 1, 1),
                    threshold=self.get_resource_threshold(),
                )

            if box is not None:
                self.task.log_info(
                    f"搜索: 找到 {element.name}，点击"
                )
                self.task.click(
                    box, name=f"选择资源: {element.name}"
                )
                return True

            # 已达最大滑动次数 → 失败
            if attempt >= self.scroll_max_attempts:
                self.task.log_info(
                    f"搜索: 已滑动 {self.scroll_max_attempts} 次"
                    f"仍未找到 {element.name}"
                )
                return False

            # 未找到 → 判断位置并滑动
            if self._find_edge_anchor("right") is not None:
                self.task.log_info(
                    "搜索: 检测到资源栏右端锚点，向右滑动查找"
                )
            else:
                self.task.log_info(
                    f"搜索: 未匹配到 {element.name}，尝试向右滑动查找"
                )

            self._scroll("right")

        return False

    def select_level(self, level=None) -> bool:
        """选择级别。level 为 None 或无对应元素时跳过。"""
        if level is None:
            return True

        elements = self.get_level_elements()
        element = elements.get(level)
        if element is None:
            self.task.log_info(f"搜索: 未配置级别 {level} 对应元素，跳过")
            return True

        self.task.log_info(f"搜索: 选择级别 {level}")
        return self.task._wait_and_click(
            element,
            name=f"选择级别 {level}",
            timeout=6.0,
        )

    def confirm_position(self) -> bool:
        """确认位置。"""
        self.task.log_info("搜索: 确认位置")
        return self.task._wait_and_click(
            self.get_confirm_button(),
            name="确认位置",
            timeout=8.0,
            threshold=self.get_resource_threshold(),
        )

    def run(self, level=None) -> bool:
        """执行一次完整搜索。"""
        if not self.search():
            return False
        if not self.select_resource():
            return False
        if not self.select_level(level):
            return False
        if not self.confirm_position():
            return False
        return True

    # ========================================================
    # 滑动查找（按方向）
    # ========================================================

    def _get_edge_elements(self, direction):
        """返回 direction 方向的端点锚点元素列表，方向非法则抛 ValueError。"""
        attr = self._EDGE_ELEMENT_ATTRS.get(direction)
        if attr is None:
            raise ValueError(
                f"未知滑动方向 {direction!r}，"
                f"可选: {sorted(self._EDGE_ELEMENT_ATTRS)}"
            )
        return getattr(self, attr)

    def _find_edge_anchor(self, direction, elements=None, threshold=None):
        """
        查找指定方向的列表端点锚点，返回第一个命中的 Box，没有返回 None。

        direction 取 "right" / "left" / "up" / "down"，与 _scroll 的
        手指滑动方向同名：锚点命中 = 视图已位于该方向的列表端点。

        支持多元素判断：默认按 self.<direction>_edge_elements 的顺序
        逐个匹配，也可通过 elements 显式传入一组元素覆盖默认列表。

        threshold 为 None 时用 task._find 的默认阈值；图标形态多变的
        锚点可传宽松阈值（如 0.5）。
        """
        if elements is None:
            elements = self._get_edge_elements(direction)

        for element in elements:
            if threshold is None:
                box = self.task._find(
                    element,
                    box=self.task.box_of_screen(0, 0, 1, 1),
                )
            else:
                box = self.task._find(
                    element,
                    box=self.task.box_of_screen(0, 0, 1, 1),
                    threshold=threshold,
                )
            if box is not None:
                return box
        return None

    def _get_scroll_line(self, direction) -> float:
        """
        获取滑动线坐标（屏幕相对 0~1）。

        规则：
          1. 横向滑动（left/right）返回 y 线：优先用锚点中心 y；
             纵向滑动（up/down）返回 x 线：优先用锚点中心 x。
             锚点就在列表上，用它作为滑动线最准确。
          2. 找不到锚点 → 用兜底值 scroll_default_y / scroll_default_x。

        之所以不用固定值，是因为列表位置可能随设备分辨率/UI 缩放变化，
        用实际识别到的锚点更稳。
        """
        box = self._find_edge_anchor(direction)
        horizontal = direction in ("left", "right")

        if box is not None:
            if horizontal:
                center = (box.y + box.height / 2) / self.task.height
            else:
                center = (box.x + box.width / 2) / self.task.width
            self.task.log_info(
                f"搜索: 使用{self._DIRECTION_NAMES[direction]}端锚点"
                f" {center:.3f} 作为滑动线"
            )
            return center

        fallback = self.scroll_default_y if horizontal else self.scroll_default_x
        self.task.log_info(
            f"搜索: 未找到{self._DIRECTION_NAMES[direction]}端锚点，"
            f"使用兜底滑动线 {fallback}"
        )
        return fallback

    def _scroll(self, direction):
        """
        沿指定方向滑动列表：手指从屏幕中间向该方向滑一小段。

          right  手指向右滑 → 内容右移，露出左侧内容
          left   手指向左滑 → 内容左移，露出右侧内容
          up     手指向上滑 → 内容上移，露出下方内容
          down   手指向下滑 → 内容下移，露出上方内容

        滑动线取锚点坐标（_get_scroll_line），配合短时长的快速滑动
        触发惯性。滑动幅度取配置值 scroll_from/to_x/y；
        反方向滑动取 1 - 配置值 的镜像。
        """
        if direction not in self._DIRECTION_NAMES:
            raise ValueError(
                f"未知滑动方向 {direction!r}，"
                f"可选: {sorted(self._DIRECTION_NAMES)}"
            )

        if direction == "right":
            from_x, to_x = self.scroll_from_x, self.scroll_to_x
            from_y = to_y = self._get_scroll_line(direction)
        elif direction == "left":
            from_x, to_x = 1 - self.scroll_from_x, 1 - self.scroll_to_x
            from_y = to_y = self._get_scroll_line(direction)
        elif direction == "up":
            from_y, to_y = 1 - self.scroll_from_y, 1 - self.scroll_to_y
            from_x = to_x = self._get_scroll_line(direction)
        else:  # down
            from_y, to_y = self.scroll_from_y, self.scroll_to_y
            from_x = to_x = self._get_scroll_line(direction)

        self.task.log_info(
            f"搜索: 向{self._DIRECTION_NAMES[direction]}滑动 "
            f"x: {from_x:.3f} -> {to_x:.3f}, "
            f"y: {from_y:.3f} -> {to_y:.3f}, "
            f"duration={self.scroll_duration}"
        )

        self.task.swipe_relative(
            from_x=from_x,
            from_y=from_y,
            to_x=to_x,
            to_y=to_y,
            duration=self.scroll_duration,
            settle_time=self.scroll_settle_time,
        )
