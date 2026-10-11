from enum import Enum

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
    BUTTON_RALLY_GIANT_BEAST_1,
    BUTTON_STARTRALLY,
    BUTTON_HUNT_EXPEDITION,
    TEAM_TIME_ON_THE_WAY,
    OVERALL_STAMINA_CURRENT,
    OVERALL_STAMINA
)


class ExpeditionType(Enum):
    """出发类型：HUNT=野怪/集结（巨兽、恐狼、叛军精锐、切萨雷），
    GATHER=资源采集（野外采集派遣）。"""

    HUNT = "hunt"
    GATHER = "gather"


class Rally:
    """
    军队集结领域对象。
    组合：RallyConfig + MarchQueue + RallyTimer。
    对外接口：
      prepare()              -> (ready, wait_seconds)
      troop_start(...)         -> bool  配置队伍并发起出征（集结/采集共用入口）
      execute()              -> bool
      estimate_wait()        -> float
      has_enough_stamina()   -> bool
    """

    def __init__(self, task):
        self.task = task
        self.config = RallyConfig(task)
        self.queue = MarchQueue(task)
        self.timer = RallyTimer(task)
        
        self.default_stamina = 200

        # 运行时状态：最近一次 troop_start 的出发类型
        self.last_expedition_type = None

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

    def execute(self, entry_elements=None) -> bool:
        task = self.task

        # 集结入口按钮：默认巨兽弹窗；切萨雷等弹窗由调用方传入
        if entry_elements is None:
            entry_elements = (BUTTON_RALLY_GIANT_BEAST_1,)

        for element in entry_elements:
            if task._wait_and_click(
                element,
                timeout=3.0,
                with_recovery=False,
                box=task.box_of_screen(0, 0, 1, 1),
                ):
                break
            else:
                return False

        # 2. 确认集结
        if not task._wait_and_click(
            BUTTON_STARTRALLY,
            name="集结确定",
        ):
            return False

        # 3-4. 集结配置 + 发起远征（封装在 troop_start，出发类型=野怪集结）
        return self.troop_start(team_task_type=ExpeditionType.HUNT)

    def _hunt_expedition(self) -> bool:
        task = self.task

        box = task._wait_element(BUTTON_HUNT_EXPEDITION, timeout=8.0)
        if box is None:
            return False
        time_box = task.get_box_by_name(TEAM_TIME_ON_THE_WAY.resource_id)
        # 点击前读一次行军时间
        self.timer.read_march_time(time_box)

        task.log_info("点击出征/远征按钮")
        return task.click(box)

    # --------------------------------------------------------
    # 配置 + 出征（采集/集结共用入口）
    # --------------------------------------------------------

    def troop_start(self, team_group_name=None, team_task_type=None,
                    team_hero=None) -> bool:
        """
        队伍配置 + 出征派遣：封装 RallyConfig.apply（队伍/英雄/兵力）
        与 _hunt_expedition（点出征按钮）为一个入口。

        参数：
          team_group_name 出征页顶部预设队伍名（如 "cj"）。OCR 匹配
                          队伍选择区域：命中且开关开启 → 点击选中；
                          未命中 / 未提供 → 沿用系统默认队伍。
          team_task_type  ExpeditionType：HUNT=野怪/集结，GATHER=资源采集。
                          本版本仅记录与日志；队列/英雄/兵力的按类型
                          差异化处理为预留分支。
          team_hero       英雄调整（本版本不生效，预留）。

        返回：True = 配置完成并已点击出征。
        """
        etype = self._normalize_expedition_type(team_task_type)
        if etype is None:
            return False
        self.last_expedition_type = etype
        self.task.log_info(
            f"出发派遣: 类型={etype.value}, "
            f"队伍={team_group_name or '系统默认'}"
        )

        # 1. 队伍 / 英雄 / 兵力配置
        if not self.config.apply(
            team_group_name=team_group_name, team_hero=team_hero
        ):
            return False

        # 2. 出征（读行军时间后点击）
        return self._hunt_expedition()

    def _normalize_expedition_type(self, team_task_type):
        """归一化出发类型：None→HUNT；ExpeditionType 原样；字符串按值匹配。"""
        if team_task_type is None:
            return ExpeditionType.HUNT
        if isinstance(team_task_type, ExpeditionType):
            return team_task_type
        try:
            return ExpeditionType(str(team_task_type).lower())
        except ValueError:
            self.task.log_error(f"未知出发类型: {team_task_type!r}")
            return None

    # --------------------------------------------------------
    # 等待时间
    # --------------------------------------------------------

    def estimate_wait(self,extra_config) -> float:
        return self.timer.total_wait(extra_config)

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
        box=self.task.get_box_by_name(OVERALL_STAMINA_CURRENT.resource_id)
        try:
            results=self.task.ocr(box)
        except Exception as e:
            self.task.log_info(f"OCR 体力异常: {e}")
            results = None

        if not results:
            self.task.log_info(
                f"未识别到体力，使用默认值满体力值: "
                f"{self.default_stamina}s"
            )
            return self.default_stamina
        text = results[0].name
        self.task.log_info(f"OCR 体力值原始文本为: {text}")
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