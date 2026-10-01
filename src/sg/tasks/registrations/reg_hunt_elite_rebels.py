# 叛军精锐集结 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.HuntEliteRebelsTask import HuntEliteRebelsTask

GenericQueueTask.register_task_type(
    key="Hunt Elite Rebels Troop",
    task_class=HuntEliteRebelsTask,
    name_prefix="Hunt Elite Rebels叛军精锐",
    # 同恐狼：同一时刻只能打一只，不需要并行/多补
    default_count=0,
    default_requires_march_queue=True,      # 占军队队列
    default_next_trigger_delay=0.0,         # 本任务完成时立即触发下一个
    default_kwargs={
        "auto_recall": False,
        "min_stamina": 0,
    },
    description=(
        "叛军精锐集结。Use elite-rebels item and rally the elite rebels."
    ),
    extra_config={
        "Auto Recall": {
            "attr": "auto_recall",
            "default": False,
            "desc": "满员时是否召回。Auto recall when queue is full.",
        },
        "Min Stamina": {
            "attr": "min_stamina",
            "default": 0,
            "desc": "体力下限，低于则停止补任务。0 表示不检查。",
        },
    },
)
