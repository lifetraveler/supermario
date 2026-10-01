# 讨伐领取 —— 注册文件
# 每日任务：不占军队队列，不可并行，完成后 24h 再触发。
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.daily.PunishClaimTask import PunishClaimTask

GenericQueueTask.register_task_type(
    key="Punish Claim",
    task_class=PunishClaimTask,
    name_prefix="Punish Claim讨伐领取",
    default_count=1,
    default_requires_march_queue=False,     # 不占军队队列
    default_next_trigger_delay=86400.0,     # 每日一次
    default_kwargs={},
    description="讨伐奖励领取。Claim punish rewards via the march panel.",
    extra_config={},
)
