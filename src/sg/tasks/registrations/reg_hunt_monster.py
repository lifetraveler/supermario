# 巨兽出击部队 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.HuntMonsterTask import HuntMonsterTask

GenericQueueTask.register_task_type(
    key="Hunt Monster Troop",
    task_class=HuntMonsterTask,
    name_prefix="Hunt Monster",
    default_count=6,
    default_max_active=2,
    default_requires_march_queue=True,      # 占军队队列
    default_next_trigger_delay=0.0,         # 本任务完成时立即触发下一个
    default_kwargs={
        "monster_level": 1,
        "auto_recall": False,
        "min_stamina": 0,
    },
    description="巨兽出击部队。Dispatch troops to hunt monsters.",
    extra_config={
        "Monster Level": {
            "attr": "monster_level",         # ← 新增：实际属性名
            "default": 1,
            "desc": "巨兽级别。Monster level.",
            "type": {"type": "drop_down", "options": ["1","2","3","4","5"]},
        },
        "Auto Recall": {
            "attr": "auto_recall",           # ← 新增
            "default": False,
            "desc": "满员时是否召回。Auto recall when queue is full.",
        },
        "Min Stamina": {
            "attr": "min_stamina",           # ← 新增
            "default": 0,
            "desc": "体力下限，低于则停止补任务。0 表示不检查。",
        },
    },
)