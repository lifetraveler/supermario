# 采集部队 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.GatherTask import GatherTask   # 请按实际类名调整


GenericQueueTask.register_task_type(
    key="Gather Troop",
    task_class=GatherTask,
    name_prefix="Gather",
    default_count=10,
    default_max_active=2,
    default_kwargs={"resource": "wood"},
    description="采集部队。Dispatch troops to gather resources.",
    extra_config={
        "Resource": {
            "default": "wood",
            "desc": "采集资源类型。Resource type.",
            "type": {"type": "drop_down", "options": ["wood", "food", "stone"]},
        },
    },
)