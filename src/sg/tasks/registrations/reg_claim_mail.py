# 消息中心领奖 —— 注册文件
# 每日任务：不占军队队列，不可并行，完成后 24h 再触发。
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.daily.ClaimMailRewardTask import ClaimMailRewardTask

GenericQueueTask.register_task_type(
    key="Claim Mail Reward",
    task_class=ClaimMailRewardTask,
    name_prefix="Claim Mail Reward",
    # 游戏限制：消息面板同一时刻只能打开一个，不支持并行
    default_count=1,
    default_requires_march_queue=False,     # 不占军队队列
    default_next_trigger_delay=86400.0,     # 每日一次
    default_kwargs={},
    description="领取消息中心的奖励。Claim rewards in the message center.",
    extra_config={},   # 无个性化参数
)
