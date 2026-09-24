from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
# from src.sg.tasks.daily.ClaimTaskRewardTask import ClaimTaskRewardTask


# 领取任务奖励：日常会一直刷，所以 Count=0（无限），Max Active=1。
GenericQueueTask.register_task_type(
    key="Claim Reward",
    # task_class=ClaimTaskRewardTask,
    name_prefix="Claim Reward",
    default_count=0,          # 0 = 无限
    default_max_active=1,
    default_kwargs={},
    description="领取任务奖励。Claim completed task rewards.",
    extra_config={},
)