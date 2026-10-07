from src.sg.element.world.resource.base_searcher import (
    BaseResourceSearcher,
)
from src.sg.scene.elements import (
    WORLD_ICON_RESOURCE_BEAST,
)


class BeastSearcher(BaseResourceSearcher):
    """
    野兽搜索。

    滑动查找能力（查找 → 滑动 → 再查找）由父类
    BaseResourceSearcher.select_resource() 统一提供，
    端点锚点与滑动配置也在父类中维护，子类按需覆写。
    """

    def __init__(self, task):
        super().__init__(task)

        # 无级别概念，保持空 dict（父类 select_level 会跳过）
        self.level_elements = {}

    def get_resource_element(self):
        return WORLD_ICON_RESOURCE_BEAST

    def get_level_elements(self):
        return self.level_elements
