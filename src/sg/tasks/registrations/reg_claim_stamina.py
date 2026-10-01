# 领取体力 —— 注册文件
# 常驻任务：不占军队队列，不可并行（用户界面同时只能打开一个）。
# 节奏由任务动态等待驱动：run_interaction 返回距下次可领取的秒数，
# 因此这里 next_trigger_delay=0、count=0（无限），循环领取。
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.daily.ClaimStaminaTask import ClaimStaminaTask

GenericQueueTask.register_task_type(
    key="Claim Stamina",
    task_class=ClaimStaminaTask,
    name_prefix="Claim Stamina领取体力",
    default_count=0,                        # 0 = 无限，持续循环领取
    default_requires_march_queue=False,     # 不占军队队列
    default_next_trigger_delay=0.0,         # 下一次触发时机由任务动态等待决定
    default_kwargs={
        "claim_cooldown": 3600.0,           # 领取成功后到下次可领取的间隔（秒）
    },
    description="领取体力。Claim free stamina and wait for next refresh.",
    extra_config={
        "Claim Cooldown": {
            "attr": "claim_cooldown",       # ← 必须！
            "default": 3600.0,
            "desc": "领取成功后到下次可领取的间隔（秒）。Cooldown after a successful claim.",
        },
    },
)
