from src.sg.scene.elements import (
    BUTTON_SEARCH_RESOURCES,
    BUTTON_RESOURCESEARCH,
)


class BaseResourceSearcher:
    """
    资源搜索统一父类。
    统一流程：
        搜索资源 → 选择资源类型 → 选择级别 → 确认位置

    子类只需覆写：
        get_resource_element()  -> 该资源的 SceneElement
        get_level_elements()    -> {级别: SceneElement}，无级别返回 {}
    可选覆写：
        get_search_button()     -> 默认 BUTTON_SEARCH_RESOURCES
        get_confirm_button()    -> 默认 BUTTON_RESOURCESEARCH
        get_resource_threshold()-> 默认 0.5
    """

    def __init__(self, task):
        self.task = task

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
        return 0.5

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
        """选择具体资源（怪兽 / 巨兽 / 矿产 / 恐狼）。"""
        element = self.get_resource_element()
        self.task.log_info(f"搜索: 选择资源 {element.name}")
        return self.task._wait_and_click(
            element,
            name=f"选择资源: {element.name}",
            timeout=8.0,
            threshold=self.get_resource_threshold(),
        )

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