from src.sg.element.world.resource.base_searcher import (
    BaseResourceSearcher,
)
from src.sg.scene.elements import (
    GIANT_BEAST,
    WORLD_ICON_RESOURCE_STONE,
    WORLD_ICON_RESOURCE_IRON,
)


class ScareWolfSearcher(BaseResourceSearcher):
    """
    恐狼搜索。

    =========================================================
    在父类基础上增加了"滑动查找"能力
    =========================================================
    资源栏是横向排列的，巨兽不一定总在初始视图中。
    流程：
      1. 先尝试在当前视图中找巨兽
      2. 找到 → 点击
      3. 找不到 → 用石头/铁矿锚点判断是否已在资源栏右端
      4. 用锚点的 y 坐标作为滑动线，从屏幕中间向右滑一小段
         （游戏有惯性动画，滑一小段实际会多走一段）
      5. 重复 1~4，最多滑动 scroll_max_attempts 次

    =========================================================
    滑动方向说明
    =========================================================
    当前能看到石头/铁矿 = 视图已经在资源栏最右端，
    需要让内容右移、露出左侧的巨兽。
    即手指从屏幕中间滑向右侧，from_x < to_x。
    =========================================================
    """

    def __init__(self, task):
        super().__init__(task)

        # {级别: SceneElement}，由用户在外部注入
        self.level_elements = {}

        # ====================================================
        # 滑动查找配置
        # ====================================================

        # 最大滑动次数，防止无限滑动。
        # 资源栏通常有 6~8 个图标，一次滑半个屏，8 次足够。
        self.scroll_max_attempts = 8

        # 滑动 x 坐标（屏幕相对）。
        # 从屏幕中间开始，只滑一小段（0.5 → 0.7）。
        # 因为游戏滑动有惯性动画，手指停下来后内容还会继续走一段，
        # 所以不要滑满屏，否则会冲过头。
        self.scroll_from_x = 0.5
        self.scroll_to_x = 0.7

        # 滑动时长（秒）。短一点模拟"快速滑动"，让惯性生效。
        # 太短（<0.15）可能被判为点击；
        # 太长（>0.5）惯性不明显，变成精确拖拽。
        self.scroll_duration = 0.2

        # 滑动后稳定等待（秒），等惯性动画完全停下来。
        self.scroll_settle_time = 1.0

        # 滑动线的兜底 y 值。
        # 万一石头/铁矿都没匹配到，用屏幕底部 1/3 处作滑动线。
        self.scroll_default_y = 0.85

        # 资源栏右端锚点元素。
        # 命中任一说明当前视图已经在资源栏最右侧。
        self.right_edge_elements = [
            WORLD_ICON_RESOURCE_STONE,
            WORLD_ICON_RESOURCE_IRON,
        ]

    # ========================================================
    # 子类必须实现
    # ========================================================

    def get_resource_element(self):
        return GIANT_BEAST

    def get_level_elements(self):
        return self.level_elements

    # ========================================================
    # 覆写父类：选择资源时增加滑动查找
    # ========================================================

    def select_resource(self) -> bool:
        """
        选择巨兽。

        策略：
          1. 第一次先等 2 秒，避免界面刚打开动画未完成
          2. 找不到就滑动资源栏，再尝试
          3. 最多滑动 scroll_max_attempts 次
        """
        element = self.get_resource_element()

        for attempt in range(self.scroll_max_attempts + 1):
            # 第一次用短等待，捕获动画延迟的情况；
            # 后续直接扫一帧，因为滑动后界面已经稳定
            if attempt == 0:
                box = self.task._wait_element(
                    element,
                    timeout=2.0,
                    threshold=self.get_resource_threshold(),
                )
            else:
                box = self.task._find(
                    element,
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
            if self._find_right_edge_anchor() is not None:
                self.task.log_info(
                    "搜索: 检测到资源栏右端锚点，向右滑动查找"
                )
            else:
                self.task.log_info(
                    "搜索: 未匹配到巨兽，尝试向右滑动查找"
                )

            self._scroll_right()

        return False

    #