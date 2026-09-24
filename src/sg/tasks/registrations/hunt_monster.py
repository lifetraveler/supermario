# 巨兽出击部队 —— 注册文件
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.HuntMonsterTask import HuntMonsterTask


GenericQueueTask.register_task_type(
    key="Hunt Monster Troop",
    task_class=HuntMonsterTask,
    name_prefix="Hunt Monster",
    default_count=5,
    default_max_active=3,
    default_kwargs={
        "monster_level": 1,
        "auto_recall": False,
        "min_stamina": 0,          # ← 新增：0 表示不检查
    },
    description="巨兽出击部队。Dispatch troops to hunt monsters.",
    extra_config={
        "Monster Level": {
            "default": 1,
            "desc": "巨兽级别。Monster level.",
            "type": {"type": "drop_down", "options": ["1", "2", "3", "4", "5"]},
        },
        "Auto Recall": {
            "default": False,
            "desc": "满员时是否召回。Auto recall when queue is full.",
        },
        "Min Stamina": {           # ← 新增配置项
            "default": 0,
            "desc": "体力下限，低于则停止补任务。0 表示不检查。Min stamina to stop dispatching.",
        },
    },
)