from qfluentwidgets import FluentIcon

from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask

# ⚠️ 关键：在模块导入时触发所有注册文件的执行，
#    保证 TASK_REGISTRY 在实例化前已填满。
from src.sg.tasks import registrations   # noqa: F401


class UnifiedQueue(GenericQueueTask):
    """
    统一队列入口。

    GUI 上用户只看到一个任务，但配置面板里会展开
    所有已注册的任务类型（巨兽出击 / 采集 / 日常 / 活动……），
    用户勾选哪些就调度哪些。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # UI 上显示的名字（不出现 Queue，用中性词）
        self.name = "任务调度"
        self.description = "统一调度所有已注册的任务类型"
        self.icon = FluentIcon.SYNC