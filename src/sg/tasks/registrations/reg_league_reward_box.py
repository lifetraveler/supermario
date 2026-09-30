# 联盟宝箱领取 —— 注册文件
# 每日任务：不占军队队列，不可并行，完成后 24h 再触发。
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.league.LeagueRewardBoxTask import LeagueRewardBoxTask

GenericQueueTask.register_task_type(
    key="League Reward Box",
    task_class=LeagueRewardBoxTask,
    name_prefix="League Reward Box联盟宝箱",
    # 游戏限制：宝箱页面同一时刻只能打开一个，不支持并行
    default_count=1,
    default_requires_march_queue=False,     # 不占军队队列
    default_next_trigger_delay=86400.0,     # 每日一次
    default_kwargs={},
    description="领取联盟宝箱（战利品宝箱 + 盟友赠礼）。"
                "Claim league reward boxes (loot & friend gifts).",
    extra_config={},   # 无个性化参数
)
