# 野外资源采集 —— 注册文件
# 本任务的目的就是去占用军队采集队列，不占集结判定；
# 一次跑完所有空闲采集队列，不可并行；每日一次。
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.GatherResourceTask import GatherResourceTask


GenericQueueTask.register_task_type(
    key="Gather Resource Troop",
    task_class=GatherResourceTask,
    name_prefix="Gather Resource野外采集",
    default_count=1,
    default_requires_march_queue=False,     # 本任务自身派采集队列，不占集结判定
    default_next_trigger_delay=86400.0,     # 每日一次
    default_kwargs={},
    description=(
        "野外建筑资源采集。按空闲军队队列派出采集："
        "大型资源优先（OCR 检测），普通资源按 面包/木材/石头/铁矿 "
        "以当天日期余数轮转，最多 4 个队列。"
        "Gather field resources with free march queues."
    ),
    extra_config={},
)
