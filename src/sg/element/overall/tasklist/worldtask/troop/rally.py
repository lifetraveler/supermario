from src.sg.element.overall.tasklist.worldtask.troop.march_queue import (
    MarchQueue,
)
from src.sg.element.overall.tasklist.worldtask.troop.rally_config import (
    RallyConfig,
)
from src.sg.element.overall.tasklist.worldtask.troop.rally_timer import (
    RallyTimer,
)

from src.sg.scene.elements import (
    BUTTON_RALLY_GIANT_BEAST,
    BUTTON_STARTRALLY,
    BUTTON_HUNT_EXPEDITION,
)


class Rally:
    """
    军队集结领域对象。
    组合：RallyConfig + MarchQueue + RallyTimer。
    对外接口：
      prepare()              -> (ready, wait_seconds)
      execute()              -> bool
      estimate_wait()        -> float
      has_enough_stamina()   -> bool
    """

    def __init__(self, task):
        self.task = task
        self.config = RallyConfig(task)
        self.queue = MarchQueue(task)
        self.timer = RallyTimer(task)

    # --------------------------------------------------------
    # 队列准备
    # --------------------------------------------------------

    def prepare(self):
        """
        检查队列是否可用。
        返回 (ready: bool, wait_seconds: float)
        """
        self.queue.refresh()

        if self.queue.has_idle():
            return (True, 0.0)

        self.task.log_info("没有空闲行军队列")

        if getattr(self.task, "auto_recall", False):
            self.task.log_info("尝试召回所有行军")
            if self.queue.recall_all():
                self.task.log_info("召回成功")
                return (True, 0.0)
            self.task.log_error("召回失败")
            return (False, 0.0)

        remain = self.queue.min_remaining_seconds()
        if remain > 0:
            self.task.log_info(f"最短队列剩余时间: {remain}s")
            return (False, float(remain))

        self.task.log_error("无法识别队列剩余时间")
        return (False, 0.0)

    # --------------------------------------------------------
    # 执行一次集结
    # --------------------------------------------------------

    def execute(self) -> bool:
        task = self.task

        # 1. 进入集结页面
        if not task._wait_and_click(
            BUTTON_RALLY_GIANT_BEAST,
            name="集结巨兽",
        ):
            return False

        # 2. 确认集结
        if not task._wait_and_click(
            BUTTON_STARTRALLY,
            name="集结确定",
        ):
            return False

        # 3. 集结配置
        if not self.config.apply():
            return False

        # 4. 发起远征（读行军时间后点击）
        if not self._hunt_expedition():
            return False

        return True

    def _hunt_expedition(self) -> bool:
        task = self.task

        box = task._wait_element(BUTTON_HUNT_EXPEDITION, timeout=8.0)
        if box is None:
            return False

        # 点击前读一次行军时间
        self.timer.read_march_time(box)

        task.log_info("点击元素: 发起巨兽远征")
        return task.click(box, name="发起巨兽远征")

    # --------------------------------------------------------
    # 等待时间
    # --------------------------------------------------------

    def estimate_wait(self) -> float:
        return self.timer.total_wait()

    # --------------------------------------------------------
    # 体力检查
    # --------------------------------------------------------

    def get_current_stamina(self):
        """
        读取当前体力值。返回 None 表示未实现。

        TODO: 用户根据实际 OCR / 特征读取实现。
        例如：
            results = self.task.ocr(match=r"\\d+/\\d+")
            ...
        返回 int 或 None。
        """
        return None

    def has_enough_stamina(self, min_stamina: int) -> bool:
        """
        判断体力是否满足下限。

        规则：
          - min_stamina <= 0       → 不检查，直接 True
          - 读取结果为 None        → 未实现，视为足够（True），并打日志
          - 读取结果 < min_stamina → False
          - 其它                   → True

        体力不足时，调用方（HuntMonsterTask）会向上抛 FAILED，
        队列据此决定是否停止补任务。
        """
        if min_stamina <= 0:
            return True

        stamina = self.get_current_stamina()
        if stamina is None:
            self.task.log_info("体力读取未实现，默认视为足够")
            return True

        self.task.log_info(
            f"当前体力: {stamina}，下限: {min_stamina}"
        )
        return stamina >= min_stamina