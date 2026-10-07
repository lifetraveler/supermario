from src.sg.element.world.resource.base_searcher import (
    BaseResourceSearcher,
)
from src.sg.scene.elements import (
    GIANT_BEAST,
)


class ScareWolfSearcher(BaseResourceSearcher):
    """
    恐狼搜索。

    滑动查找能力（查找 → 滑动 → 再查找 → 点击）由父类
    BaseResourceSearcher.select_resource_and_click() 统一提供，
    端点锚点与滑动配置也在父类中维护，子类按需覆写。
    """

    def __init__(self, task):
        super().__init__(task)

        # {级别: SceneElement}，由用户在外部注入
        self.level_elements = {}

    def get_resource_element(self):
        return GIANT_BEAST

    def get_level_elements(self):
        return self.level_elements
