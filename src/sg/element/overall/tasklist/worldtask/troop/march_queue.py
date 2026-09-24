from enum import Enum
import time


class MarchQueueNature(Enum):
    """行军队列性质"""
    IDLE = "idle"        # 空闲
    GATHER = "gather"    # 采集
    HUNT = "hunt"        # 打怪
    WAR = "war"          # 战争
    UNKNOWN = "unknown"  # 未识别


class MarchQueueSlot:
    """单个行军队列槽位。"""

    def __init__(self, index: int):
        self.index = index
        self.nature = MarchQueueNature.UNKNOWN
        self.rally_seconds = 0        # 集结时间
        self.remaining_seconds = 0    # 剩余时间
        self.box = None               # 识别到的 box（用于点击）

    @property
    def is_idle(self) -> bool:
        return self.nature == MarchQueueNature.IDLE

    @property
    def is_busy(self) -> bool:
        return self.nature in (
            MarchQueueNature.GATHER,
            MarchQueueNature.HUNT,
            MarchQueueNature.WAR,
        )

    def __repr__(self):
        return (
            f"MarchQueueSlot(index={self.index}, "
            f"nature={self.nature.value}, "
            f"rally={self.rally_seconds}s, "
            f"remain={self.remaining_seconds}s)"
        )


class MarchQueue:
    """
    行军队列集合。
    职责：
      - 识别并维护每个队列槽位状态
      - 回答"有没有空闲队列"、"最短剩余时间"
      - 提供召回入口
    不负责点击具体业务。
    """

    def __init__(self, task, max_slots: int = 5):
        self.task = task
        self.max_slots = max_slots
        self.slots = [MarchQueueSlot(i) for i in range(max_slots)]

        # 用户配置：空闲队列特征名列表
        self.idle_queue_features = []

        # 用户配置：性质 -> 特征名（可选）
        # self.nature_features = {
        #     MarchQueueNature.GATHER: "march_queue_gather",
        #     MarchQueueNature.HUNT: "march_queue_hunt",
        #     MarchQueueNature.WAR: "march_queue_war",
        # }

        self.last_refresh_time = 0.0

    # --------------------------------------------------------
    # 识别
    # --------------------------------------------------------

    def refresh(self):
        """扫描屏幕，刷新所有队列槽位状态。"""
        for slot in self.slots:
            slot.nature = MarchQueueNature.UNKNOWN
            slot.rally_seconds = 0
            slot.remaining_seconds = 0
            slot.box = None

        idle_index = 0
        for feature_name in self.idle_queue_features:
            box = self.task.find_one(
                feature_name=feature_name, threshold=0.8
            )
            if box is not None and idle_index < self.max_slots:
                self.slots[idle_index].nature = MarchQueueNature.IDLE
                self.slots[idle_index].box = box
                idle_index += 1

        # TODO: 识别已用队列的性质和剩余时间
        # for feature_name, nature in self.nature_features.items():
        #     box = self.task.find_one(feature_name=feature_name)
        #     if box:
        #         slot = self._find_unknown_slot()
        #         slot.nature = nature
        #         slot.box = box
        #         slot.remaining_seconds = self._read_remaining_time(box)

        self.last_refresh_time = time.time()
        self.task.log_info(
            f"队列刷新: 空闲 {self.idle_count()}/{self.total}"
        )

    def _find_unknown_slot(self):
        for slot in self.slots:
            if slot.nature == MarchQueueNature.UNKNOWN:
                return slot
        return None

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.slots)

    @property
    def idle_slots(self):
        return [s for s in self.slots if s.is_idle]

    @property
    def busy_slots(self):
        return [s for s in self.slots if s.is_busy]

    def idle_count(self) -> int:
        return len(self.idle_slots)

    def busy_count(self) -> int:
        return len(self.busy_slots)

    def has_idle(self) -> bool:
        return self.idle_count() > 0

    def min_remaining_seconds(self) -> int:
        remaining = [
            s.remaining_seconds for s in self.busy_slots
            if s.remaining_seconds > 0
        ]
        return min(remaining) if remaining else 0

    def find_slot_by_nature(self, nature: MarchQueueNature):
        for s in self.slots:
            if s.nature == nature:
                return s
        return None

    # --------------------------------------------------------
    # 召回
    # --------------------------------------------------------

    def recall_all(self) -> bool:
        """召回所有已用队列。TODO: 用户补充。"""
        self.task.log_info("召回所有队列未实现")
        return False

    def recall_slot(self, slot: MarchQueueSlot) -> bool:
        """召回单个队列。TODO: 用户补充。"""
        self.task.log_info(f"召回队列 {slot.index} 未实现")
        return False