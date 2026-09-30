# 野兽挑战 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.HuntBeastTask import HuntBeastTask

GenericQueueTask.register_task_type(
    key="Hunt Beast Troop",
    task_class=HuntBeastTask,
    name_prefix="Hunt Beast狩猎野怪",
    default_count=6,
    default_max_active=2,                   # 可并行
    default_requires_march_queue=True,      # 占军队队列（挑战发起进攻）
    default_next_trigger_delay=0.0,         # 本任务完成时立即触发下一个
    default_kwargs={
        "min_stamina": 0,
    },
    description="野兽挑战。Search the beast icon and challenge it.",
    extra_config={
        "Min Stamina": {
            "attr": "min_stamina",
            "default": 0,
            "desc": "体力下限，低于则停止补任务。0 表示不检查。",
        },
    },
)
