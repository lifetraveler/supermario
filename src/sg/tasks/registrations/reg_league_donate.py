# 联盟捐献 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.league.LeagueTechDonateTask import LeagueTechDonateTask

GenericQueueTask.register_task_type(
    key="League Tech Donate",
    task_class=LeagueTechDonateTask,
    name_prefix="League Tech Donate",
    # 游戏限制：同一时刻只能打开一个捐献界面，不可并行
    default_count=0,
    default_requires_march_queue=False,     # 不占军队队列
    default_next_trigger_delay=9000,      # 10 分钟恢复 1 次、上限 25 次，捐完需 150 分钟恢复满
    default_kwargs={},
    description="联盟捐献。Donate to league tech for league coins. "
                "10min 恢复 1 次，上限 25 次，全部捐完后 150 分钟循环一次。",
    extra_config={},
)
