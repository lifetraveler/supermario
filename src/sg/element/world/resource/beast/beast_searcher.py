from src.sg.element.world.resource.base_searcher import (
    BaseResourceSearcher,
)
from src.sg.scene.elements import (
    WORLD_ICON_RESOURCE_BEAST,
    WORLD_ICON_RESOURCE_STONE,
    WORLD_ICON_RESOURCE_IRON,
)


class BeastSearcher(BaseResourceSearcher):
    """
    野兽搜索。

    =========================================================
    与 MonsterSearcher / ScareWolfSearcher 同构：
    在父类基础上增加了"滑动查找"能力
    =========================================================
    资源栏是横向排列的，野兽图标不一定总在初始视图中。
    流程：
      1. 先尝试在当前视图中找野兽图标
      2. 找到 → 点击
      3. 找不到 → 用石头/铁矿锚点判断是否已在资源栏右端
      4. 用锚点的 y 坐标作为滑动线，从屏幕中间向右滑一小段
         （游戏有惯性动画，滑一小段实际会多走一段）
      5. 重复 1~4，最多滑动 scroll_max_attempts 次

    =========================================================
    滑动方向说明
    =========================================================
    当前能看到石头/铁矿 = 视图已经在资源栏最右端，
    需要让内容右移、露出左侧的野兽图标。
    即手指从屏幕中间滑向右侧，from_x < to_x。
    =========================================================
    """

    def __init__(self, task):
        super().__init__(task)

        # 无级别概念，保持空 dict（父类 select_level 会跳过）
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
        return WORLD_ICON_RESOURCE_BEAST

    def get_level_elements(self):
        return self.level_elements

    # ========================================================
    # 覆写父类：选择资源时增加滑动查找
    # ========================================================

    def select_resource(self) -> bool:
        """
        选择野兽图标。

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
            if self._find_right_edge_anchor() is not None:
                self.task.log_info(
                    "搜索: 检测到资源栏右端锚点，向右滑动查找"
                )
            else:
                self.task.log_info(
                    "搜索: 未匹配到野兽图标，尝试向右滑动查找"
                )

            self._scroll_right()

        return False

    # ========================================================
    # 内部方法
    # ========================================================

    def _find_right_edge_anchor(self):
        """
        查找资源栏右端锚点（石头 / 铁矿）。
        找到就返回那个 Box，找不到返回 None。
        """
        for element in self.right_edge_elements:
            box = self.task._find(element,box=self.task.box_of_screen(0, 0, 1, 1),)
            if box is not None:
                return box
        return None

    def _get_scroll_y(self) -> float:
        """
        获取滑动线的 y 坐标（屏幕相对 0~1）。

        规则：
          1. 优先用石头/铁矿锚点的中心 y。
             锚点就在资源栏上，用它作为滑动线最准确。
          2. 找不到锚点 → 用兜底值 self.scroll_default_y
             （屏幕底部 1/3 处）。

        之所以不用固定 y，是因为资源栏位置可能随设备分辨率/UI 缩放变化，
        用实际识别到的锚点更稳。
        """
        box = self._find_right_edge_anchor()
        if box is not None:
            screen_h = self.task.height
            center_y = (box.y + box.height / 2) / screen_h
            self.task.log_info(
                f"搜索: 使用锚点 y={center_y:.3f} 作为滑动线"
            )
            return center_y

        self.task.log_info(
            f"搜索: 未找到锚点，使用兜底滑动线 y="
            f"{self.scroll_default_y}"
        )
        return self.scroll_default_y

    def _scroll_right(self):
        """
        手指从屏幕中间滑向右侧，让内容右移、露出左侧内容。

        用锚点 y 作为滑动线，配合短时长的快速滑动触发惯性。
        """
        y = self._get_scroll_y()

        self.task.log_info(
            f"搜索: 滑动 y={y:.3f}, "
            f"x: {self.scroll_from_x} -> {self.scroll_to_x}, "
            f"duration={self.scroll_duration}"
        )

        self.task.swipe_relative(
            from_x=self.scroll_from_x,
            from_y=y,
            to_x=self.scroll_to_x,
            to_y=y,
            duration=self.scroll_duration,
            settle_time=self.scroll_settle_time,
        )
