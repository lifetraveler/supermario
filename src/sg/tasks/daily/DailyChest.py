from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
# from src.sg.tasks.daily.OpenChestTask import OpenChestTask


# 日常免费宝箱：无参数、一次即可，纯粹演示"非部队业务"。
GenericQueueTask.register_task_type(
    key="Daily Chest",
    # task_class=OpenChestTask,
    name_prefix="Daily Chest",
    default_count=1,
    default_max_active=1,
    default_kwargs={},
    description="日常免费宝箱。Open free daily chests.",
    extra_config={},   # 无额外参数
)