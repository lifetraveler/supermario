# 宠物寻宝派遣 —— 注册文件
# 每日任务：不占军队队列（宠物派遣不占行军），不可并行，完成后 24h 再触发。
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.daily.PetTreasureHuntTask import PetTreasureHuntTask

GenericQueueTask.register_task_type(
    key="Pet Treasure Hunt",
    task_class=PetTreasureHuntTask,
    name_prefix="Pet Treasure宠物寻宝",
    default_count=1,
    default_requires_march_queue=False,     # 不占军队队列
    default_next_trigger_delay=86400.0,     # 每日一次
    default_kwargs={},
    description="宠物寻宝派遣。Dispatch pets to search treasures.",
    extra_config={},
)
