"""
野外资源采集搜索领域对象。

职责：
  1. detect_large_resource：滑动区域内 OCR 检测"大型"资源并识别类型
     （大型伐木场 / 大型面包 / 大型石头 / 大型铁矿），
     结果缓存到 self.large_resource_type，同一任务实例内不重复检测。
  2. build_gather_plan：根据有效队列数 + 日期余数，
     计算本轮要采集的资源列表（大型资源优先，普通资源按
     面包 → 木材 → 石头 → 铁矿 循环，跳过与大型资源同类型的矿）。
  3. search_for(resource_key)：在世界地图打开搜索面板，选中指定
     资源图标（滑动区域内查找，找不到则滑动继续找），确认搜索。

不负责：军队队列读取（Troop）、等级选择（游戏默认已选）、
        采集时间 OCR（Task 内处理）、弹窗 / 恢复。
"""

import datetime

from src.sg.scene.elements import (
    BUTTON_SEARCH_RESOURCES,
    WORLD_ICON_RESOURCE_SLIDE_AREA,
    WORLD_ICON_RESOURCE_BREAD,
    WORLD_ICON_RESOURCE_WOOD,
    WORLD_ICON_RESOURCE_STONE,
    WORLD_ICON_RESOURCE_IRON,
)

# 普通资源 key → SceneElement。
# 顺序即"余数 0 起始"的循环顺序：面包 → 木材 → 石头 → 铁矿。
NORMAL_RESOURCES = {
    "bread": WORLD_ICON_RESOURCE_BREAD,
    "wood": WORLD_ICON_RESOURCE_WOOD,
    "stone": WORLD_ICON_RESOURCE_STONE,
    "iron": WORLD_ICON_RESOURCE_IRON,
}

# 大型资源 OCR 关键词 → 与普通资源共用的 key。
# 大型伐木场 → wood；大型面包 → bread；大型石头 → stone；大型铁矿 → iron。
LARGE_KEYWORDS = {
    "伐木场": "wood",
    "面包": "bread",
    "石头": "stone",
    "铁矿": "iron",
}

# 普通资源展示名，用于日志。
RESOURCE_NAMES = {
    "bread": "面包",
    "wood": "木材",
    "stone": "石头",
    "iron": "铁矿",
}


class ResourceGatherSearcher:
    """
    职责：大型资源检测 + 采集资源规划 + 搜索面板选资源。
    不负责：队列数量、等级选择、时间 OCR、恢复。
    """

    def __init__(self, task):
        self.task = task


        # ====================================================
        # 滑动查找配置（与 BeastSearcher 同构）
        # ====================================================
        # 最大滑动次数，防止无限滑动。
        self.scroll_max_attempts = 8

        # 滑动 x 坐标（屏幕相对）。短滑触发惯性，不滑满屏。
        self.scroll_from_x = 0.5
        self.scroll_to_x = 0.7

        # 滑动时长（秒）。短一点模拟"快速滑动"，让惯性生效。
        self.scroll_duration = 0.2

        # 滑动后稳定等待（秒），等惯性动画完全停下来。
        self.scroll_settle_time = 1.0
        # 滑动线的兜底 y 值（资源栏典型位置，屏幕相对）。
        self.scroll_default_y = 0.85

        # 资源图标匹配阈值。图标形态多变，用宽松阈值。
        self.resource_threshold = 0.5

        # ====================================================
        # 内部状态
        # ====================================================
        # 本次检测到的大型资源 key（"wood"/"stone"/…）；None = 无大型矿。
        # 同一任务实例只检测一次（大型矿当天不变）。
        self.large_resource_type = None

        # 计划生成回调：Task 注入；首轮面板打开后由 run_for 触发一次。
        # 回调签名 plan_provider(large_key) -> plan list。
        self.plan_provider = None
        self.plan_provided = False
        self.plan = []

    # ========================================================
    # 1. 大型资源检测
    # ========================================================

    def detect_large_resource(self):
        """
        在滑动区域内 OCR 检测"大型"资源，识别具体类型。

        返回 large_resource_type：大型资源对应的资源 key；
        没有大型矿返回 None。结果缓存，重复调用不重复 OCR。
        """
        if self.large_resource_type is not None:
            return self.large_resource_type

        box = self.task._find(
            WORLD_ICON_RESOURCE_SLIDE_AREA,
            box=self.task.box_of_screen(0, 0, 1, 1),
        )
        if box is None:
            self.task.log_info("未找到资源滑动区域，按无大型矿处理")
            return None

        try:
            results = self.task.ocr(box=box)
        except Exception as e:
            self.task.log_info(f"OCR 大型资源区域异常: {e}")
            results = None

        if not results:
            self.task.log_info("滑动区域 OCR 无结果，按无大型矿处理")
            return None

        texts = " ".join(
            (b.name or "") for b in results
        )
        self.task.log_info(f"滑动区域 OCR 原文: {texts}")

        if "大型" not in texts:
            self.task.log_info("未检测到大型资源")
            return None

        for keyword, key in LARGE_KEYWORDS.items():
            if keyword in texts:
                self.large_resource_type = key
                self.task.log_info(
                    f"检测到大型资源: 大型{keyword} → {key}"
                )
                return key

        # 含"大型"但未匹配到已知类型，按无大型矿处理。
        self.task.log_info("检测到大型但未识别类型，按无大型矿处理")
        return None

    # ========================================================
    # 2. 采集资源规划
    # ========================================================

    def build_gather_plan(self, slot_count: int):
        """
        根据空闲队列数计算本轮要采集的资源 key 列表。

        规则（对齐需求样例）：
          - slot_count <= 0 → 空列表
          - 普通资源按 面包 → 木材 → 石头 → 铁矿 循环，
            起始位置由"当天的日期 % 4"决定：
              0 → 面包，1 → 木材，2 → 石头，3 → 铁矿
          - 大型资源优先占用一个队列；
            如果当天有大型矿，普通资源的循环起点移到大型资源类型的
            下一种（样例：大矿是石头，余数 0 对应铁矿）；
            没有大型矿时起点就是余数对应的资源（样例：0 → 面包）
          - 大型矿同类型的普通矿跳过（大型伐木场存在时不再采普通木材）
          - 最多 4 种，每种一个队列
        """
        if slot_count <= 0:
            return []

        plan = []
        large = self.large_resource_type
        normal_order = list(NORMAL_RESOURCES.keys())  # 面包→木材→石头→铁矿
        n = len(normal_order)

        # 大型资源优先占用一个队列。
        if large is not None:
            plan.append(large)

        # 普通资源循环起点：
        #   无大型矿 → 日期余数直接对应；
        #   有大型矿 → 从大型资源类型的下一种开始。
        day_offset = datetime.date.today().day % n
        if large is not None:
            start = (normal_order.index(large) + 1) % n
        else:
            start = day_offset

        remaining = min(slot_count, 4) - len(plan)
        for i in range(n):
            if remaining <= 0:
                break
            key = normal_order[(start + i) % n]
            # 跳过与大型资源同类型的矿（大型伐木场存在时不再采普通木材）。
            if large is not None and key == large:
                continue
            plan.append(key)
            remaining -= 1

        self.task.log_info(
            f"采集计划: 空闲 {slot_count} 队列, 大型={large}, "
            f"日期余数={day_offset}, "
            f"计划={[RESOURCE_NAMES[k] for k in plan]}"
        )
        return plan

    # ========================================================
    # 3. 搜索面板选择资源
    # ========================================================

    def open_search_panel(self) -> bool:
        """在世界地图点击搜索资源按钮，打开搜索面板。"""
        self.task.log_info("搜索: 点击搜索资源")
        return self.task._wait_and_click(
            BUTTON_SEARCH_RESOURCES,
            timeout=8.0,
        )

    def select_resource(self, key: str) -> bool:
        """
        在滑动区域内查找并点击指定资源图标。
        找不到则滑动资源栏继续找，最多 scroll_max_attempts 次。
        """
        element = NORMAL_RESOURCES[key]

        for attempt in range(self.scroll_max_attempts + 1):
            if attempt == 0:
                # 第一次用短等待，兼容界面动画未完成。
                box = self.task._wait_element(
                    element,
                    timeout=2.0,
                    box=self.task.box_of_screen(0, 0, 1, 1),
                    threshold=self.resource_threshold,
                )
            else:
                box = self.task._find(
                    element,
                    box=self.task.box_of_screen(0, 0, 1, 1),
                    threshold=self.resource_threshold,
                )

            if box is not None:
                self.task.log_info(f"搜索: 找到 {element.name}，点击")
                self.task.click(box)
                self.task._sleep(0.3)
                return True

            if attempt >= self.scroll_max_attempts:
                self.task.log_info(
                    f"搜索: 已滑动 {self.scroll_max_attempts} 次"
                    f"仍未找到 {element.name}"
                )
                return False

            self.task.log_info(
                f"搜索: 未匹配到 {element.name}，滑动资源栏继续查找"
            )
            self._scroll_right()

        return False

    def confirm_search(self) -> bool:
        """
        确认搜索（等级选择 TODO 待实现：使用游戏默认已选等级）。
        """
        self.task.log_info("搜索: 确认搜索（等级使用游戏默认）")
        return self.task._wait_and_click(
            self.get_confirm_button(),
            timeout=8.0,
            threshold=self.resource_threshold,
        )

    def get_confirm_button(self):
        from src.sg.scene.elements import BUTTON_RESOURCESEARCH
        return BUTTON_RESOURCESEARCH

    def run_for(self, key: str) -> bool:
        """
        执行一次完整搜索：
          打开面板 → （首轮：检测大型资源）→ 选资源 → 确认。

        大型资源检测必须在面板打开后进行
        （滑动区域是搜索面板内元素）。
        （等级选择 TODO：游戏默认已选，暂不实现）
        """
        if not self.open_search_panel():
            return False
        # 面板已打开，首次调用时检测大型资源并生成采集计划。
        if self.plan_provider is not None and not self.plan_provided:
            self.detect_large_resource()
            self.plan = self.plan_provider(self.large_resource_type)
            self.plan_provided = True
        if not self.select_resource(key):
            return False
        if not self.confirm_search():
            return False
        return True

    # ========================================================
    # 滑动
    # ========================================================

    def _scroll_right(self):
        """
        手指从屏幕中间滑向右侧，让资源栏内容右移、露出左侧图标。

        资源图标不匹配锚点时无法确定滑动线 y，
        用资源图标典型位置的兜底 y 值。
        """
        self.task.swipe_relative(
            from_x=self.scroll_from_x,
            from_y=self.scroll_default_y,
            to_x=self.scroll_to_x,
            to_y=self.scroll_default_y,
            duration=self.scroll_duration,
            settle_time=self.scroll_settle_time,
        )
