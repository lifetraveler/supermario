# 切萨雷征讨 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.CesareFightTask import CesareFightTask

GenericQueueTask.register_task_type(
    key="Cesare Fight",
    task_class=CesareFightTask,
    name_prefix="Cesare切萨雷",
    # 两周一次的活动，同一时刻只处理一只，不可并行
    default_count=1,
    default_requires_march_queue=True,      # 占军队队列（挑战/集结都可能出征）
    default_next_trigger_delay=0.0,         # 本任务完成时立即触发下一个
    default_kwargs={
        "auto_recall": False,
        "min_stamina": 0,
        "stamina_wait_seconds": 3600,
    },
    description=(
        "切萨雷征讨。Fight the Cesare event: "
        "reconnoitre then challenge (beast-style kill) or rally."
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
        "Stamina Wait Seconds": {
            "attr": "stamina_wait_seconds",
            "default": 3600,
            "desc": "体力不足时本次结束的等待秒数，默认 3600（1小时）。",
        },
    },
)
