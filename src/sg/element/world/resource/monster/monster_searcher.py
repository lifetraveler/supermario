from src.sg.element.world.resource.base_searcher import (
    BaseResourceSearcher,
)

from src.sg.scene.elements import GIANT_BEAST


class MonsterSearcher(BaseResourceSearcher):
    """
    巨兽搜索。
    资源元素：GIANT_BEAST。
    级别元素：由 Task 在初始化时注入 self.level_elements。
    """

    def __init__(self, task):
        super().__init__(task)

        # {级别: SceneElement}，用户按项目实际元素填充
        self.level_elements = {
            # 1: MONSTER_LEVEL_1,
            # 2: MONSTER_LEVEL_2,
        }

    def get_resource_element(self):
        return GIANT_BEAST

    def get_level_elements(self):
        return self.level_elements