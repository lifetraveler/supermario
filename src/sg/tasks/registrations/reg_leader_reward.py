# 统帅领取奖励 —— 注册文件
# 每日任务：不占军队队列，不可并行，完成后 24h 再触发。
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.daily.LeaderRewardTask import LeaderRewardTask

GenericQueueTask.register_task_type(
    key="Leader Reward",
    task_class=LeaderRewardTask,
    name_prefix="Leader Reward统帅奖励",
    default_count=1,
    default_requires_march_queue=False,     # 不占军队队列
    default_next_trigger_delay=86400.0,     # 每日一次
    default_kwargs={},
    description="统帅每日免费奖励领取。Claim daily free leader rewards.",
    extra_config={},
)
