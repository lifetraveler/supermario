from src.sg.scene.elements import (
    BUTTON_CLOSE_1,
    BUTTON_CLOSE_2,
    BUTTON_CLOSE_3,
)


class ActivityPopup:
    """
    活动弹窗处理。
    职责：扫描已知的关闭按钮，逐个关闭弹窗。
    """

    def __init__(self, task):
        self.task = task
        self.close_elements = [
            BUTTON_CLOSE_1,
            BUTTON_CLOSE_2,
            BUTTON_CLOSE_3,
        ]

    def close_all(self) -> int:
        """
        循环扫描并关闭所有已知弹窗。
        每次关闭一个就重新扫描，直到三个都没有。
        返回关闭的弹窗数量。
        """
        self.task.log_info("开始检查活动弹窗")
        closed_count = 0

        while True:
            closed = False
            for element in self.close_elements:
                box = self.task._find(element)
                if box is None:
                    continue
                self.task.log_info(f"发现活动弹窗: {element.name}")
                if self.task.click(
                    box, name=f"关闭弹窗: {element.name}"
                ):
                    closed = True
                    closed_count += 1
                    self.task._sleep(0.5)
                    break
            if not closed:
                break

        self.task.log_info(
            f"活动弹窗检查完成，共关闭 {closed_count} 个"
        )
        return closed_count

    def try_close_one(self) -> bool:
        """
        尝试关闭一个已知弹窗，命中即返回 True。
        供 RecoveryHelper 之类的恢复逻辑复用。
        """
        for element in self.close_elements:
            box = self.task._find(element)
            if box is None:
                continue
            self.task.log_info(f"关闭弹窗 {element.name}")
            self.task.click(box, name=f"关闭弹窗: {element.name}")
            self.task._sleep(0.5)
            return True
        return False