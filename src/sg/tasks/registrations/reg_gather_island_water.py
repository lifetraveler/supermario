# 岛屿采水 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.island.GatherIslandWaterTask import GatherIslandWaterTask

GenericQueueTask.register_task_type(
    key="Gather Island Water",
    task_class=GatherIslandWaterTask,
    name_prefix="Gather Island Water",
    # 采水不派部队，不占军队队列；生命之泉最多积累 10 小时，
    # 同一时刻只有一个岛要采，无并发必要
    default_count=1,
    default_requires_march_queue=False,   # 不占军队队列
    default_next_trigger_delay=36000.0,   # 10 小时触发一次
    default_kwargs={},
    description=(
        "岛屿采水。Enter own island, gather water "
        "and claim the gather reward."
    ),
    extra_config={},
)
