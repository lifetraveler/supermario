# src/sg/tasks/world/GatherTroop.py
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.GatherTask import GatherTask

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


class GatherTroop(GenericQueueTask):
    """采集部队：并发调度采集任务。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "Gather Troop"
        self.description = "派部队并发采集资源。"