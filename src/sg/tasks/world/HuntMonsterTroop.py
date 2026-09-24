from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.HuntMonsterTask import HuntMonsterTask


# =============================================================================
# 巨兽出击部队 —— 业务注册
# =============================================================================
# 这是"业务侧"文件，可以说业务术语（部队、巨兽、出击）。
# 它只做两件事：
#   1. 把业务参数注册进 GenericQueueTask
#   2. 提供一个薄壳类，用于在 GUI 任务列表里单独显示
#
# 模块被 import 时注册即生效。
# =============================================================================
GenericQueueTask.register_task_type(
    key="Hunt Monster Troop",
    task_class=HuntMonsterTask,
    name_prefix="Hunt Monster",

    # 默认提交 5 次，最多同时跑 3 个
    default_count=5,
    default_max_active=3,

    # 传给 HuntMonsterTask 的固定参数（不随用户配置变化的写这里）
    default_kwargs={
        "monster_level": 1,
        "auto_recall": False,
    },

    description="巨兽出击部队。Dispatch troops to hunt monsters.",

    # 用户可调的业务参数，GUI 会自动展开成四项
    extra_config={
        "Monster Level": {
            "default": 1,
            "desc": "巨兽级别。Monster level.",
            "type": {"type": "drop_down", "options": ["1", "2", "3", "4", "5"]},
        },
        "Auto Recall": {
            "default": False,
            "desc": "满员时是否召回。Auto recall when queue is full.",
        },
    },
)


class HuntMonsterTroop(GenericQueueTask):
    """
    巨兽出击部队。

    用队列模式并发出击巨兽：队列会按 Max Active 自动补任务，
    直到 Count 次跑完（Count=0 则无限）。

    设计上它只是一层"业务命名壳子"——
    真正的调度逻辑全部继承自 GenericQueueTask / QueueTaskBase，
    所以以后再冒出别的部队，只需复制本文件的注册段即可。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 覆盖父类的中性名称，UI 上呈现游戏术语。
        self.name = "Hunt Monster Troop"
        self.description = "派部队并发出击巨兽。"