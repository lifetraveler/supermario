# 瞭望塔事件 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.WatchTowerEventTask import WatchTowerEventTask

GenericQueueTask.register_task_type(
    key="Watch Tower Event",
    task_class=WatchTowerEventTask,
    name_prefix="Watch Tower",
    # 不占军队队列：事件在瞭望塔界面内直接处理（挑战类自行管理队列占用）
    default_count=0,                        # 无限循环补任务，靠体力/无事件自然终止
    default_requires_march_queue=False,
    default_next_trigger_delay=3600*8,         # 处理完一个立即触发下一个
    default_kwargs={
        "beast_queue_limit": 1,
    },
    description=(
        "瞭望塔事件。Handle watch tower events "
        "(tent rescue / conquer / beast challenge). "
        "Stops when stamina < 12 or no events left."
    ),
    extra_config={
        "Beast Queue Limit": {
            "attr": "beast_queue_limit",
            "default": 1,
            "desc": "挑战类事件最多占用的军队队列数，0 表示跳过挑战类。",
        },
    },
)
