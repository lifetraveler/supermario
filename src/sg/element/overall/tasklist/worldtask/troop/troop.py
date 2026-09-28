"""
军队队列管理领域对象。

通过 troop_march_team_num 位置的 bbox 直接 OCR 读取队列数量：
  - 第一个数字 = 有效数量（已占用）
  - 第二个数字 = 最大数量

职责：读取并返回军队队列的有效数量 / 最大数量。
不负责：队列槽位逐个识别（MarchQueue 负责）、召回、集结流程。
"""

import re

from src.sg.scene.elements import TROOP_MARCH_TEAM_NUM


class Troop:
    """
    军队队列管理。

    职责：OCR 读取 troop_march_team_num 位置的 "有效/最大" 数量。
    不负责：队列槽位识别、召回、集结流程。
    """

    def __init__(self, task):
        self.task = task

        # 数量显示位置的元素（bbox 定位）
        self.element = TROOP_MARCH_TEAM_NUM

        # 运行时状态：最近一次读取结果
        self.last_valid_count = None
        self.last_max_count = None

    # ========================================================
    # 读取
    # ========================================================

    def read_counts(self):
        """
        读取军队队列数量。

        返回 (valid_count, max_count)；
        读取失败返回 (None, None)。
        第一个数字是有效数量，第二个是最大数量。
        """
        try:
            box = self.task.get_box_by_name(self.element.resource_id)
        except ValueError:
            box = None

        if not box:
            self.task.log_error("未找到行军队列数量 bbox")
            return (None, None)

        try:
            results = self.task.ocr(box=box)
        except Exception as e:
            self.task.log_info(f"OCR 队列数量异常: {e}")
            return (None, None)

        if not results:
            self.task.log_info("未识别到队列数量文本")
            return (None, None)

        # 按 x 排序保证 "有效数量在前、最大数量在后"，
        # 兼容 "3/5" 单文本框与 "3" "5" 分离文本框两种渲染
        texts = [b.name or "" for b in sorted(results, key=lambda b: b.x)]
        joined = " ".join(texts)
        self.task.log_info(f"OCR 队列数量原始文本为: {joined}")

        numbers = re.findall(r"\d+", joined)
        if len(numbers) < 2:
            self.task.log_info(
                f"队列数量文本未解析出两个数字: {joined}"
            )
            return (None, None)

        valid_count = int(numbers[0])
        max_count = int(numbers[1])

        self.last_valid_count = valid_count
        self.last_max_count = max_count

        self.task.log_info(
            f"军队队列: 有效 {valid_count} / 最大 {max_count}"
        )
        return (valid_count, max_count)

    # ========================================================
    # 查询
    # ========================================================

    def free_slot_count(self):
        """
        返回空闲队列数（最大 - 有效）。
        读取失败返回 None。
        """
        valid_count, max_count = self.read_counts()
        if valid_count is None or max_count is None:
            return None
        return max(0, max_count - valid_count)

    def has_free_slot(self) -> bool:
        """
        是否有空闲队列。
        读取失败视为没有（返回 False），交由上层决定重试或失败。
        """
        free = self.free_slot_count()
        if free is None:
            return False
        return free > 0
