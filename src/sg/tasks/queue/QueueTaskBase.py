from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_queue import TaskQueue


# =============================================================================
# QueueTaskBase —— 队列模式任务的公共基类
# =============================================================================
# 设计目标：
#   把「跑一个 TaskQueue」这件事标准化，让所有走队列模式的任务都能复用同一套：
#       - 队列生命周期管理（start / stop）
#       - 统一的 tick 循环
#       - paused / exit_is_set 的统一处理
#       - 统一的进度信息展示
#
# 【与业务无关】
#   本类不知道"巨兽""部队""宝箱"是什么，它只认识 TaskQueue。
#   任何业务（出击、采集、日常、活动……）都可以继承它，
#   只需在 _build_factories() 里告诉它"要跑哪些工厂"。
#
# 继承关系：
#   SGBaseTask          ← 提供 log_info / sleep / paused / exit_is_set / info_set
#     └── QueueTaskBase ← 本类
#           └── GenericQueueTask ← 通用注册表版本
#                 └── 各业务薄壳（HuntMonsterTroop / DailyChest / ...）
# =============================================================================
class QueueTaskBase(SGBaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # name / description 是 UI 上看到的默认值。
        # 子类一般会覆盖它们；如果忘了覆盖，UI 上会显示这个中性名称。
        self.name = "Queue Task"
        self.description = "队列模式任务基类。"

        # ---------------------------------------------------------------------
        # 通用配置：所有走队列模式的任务都共享这一项。
        # 业务特有的配置（等级、资源类型……）由子类自己追加。
        # ---------------------------------------------------------------------
        self.default_config.update({
            "Tick Interval": 1.0,
        })
        self.config_description.update({
            "Tick Interval": "调度间隔秒数。Scheduler tick interval.",
        })

        # ---------------------------------------------------------------------
        # 运行时状态：
        #   self.queue      ：TaskQueue 实例，run() 开始时创建
        #   self._factories ：子类通过 _build_factories() 填充的工厂列表
        # ---------------------------------------------------------------------
        self.queue = None
        self._factories = []

    # =========================================================================
    # 子类接口
    # =========================================================================

    def _build_factories(self):
        """
        子类实现：把 TaskFactory 追加到 self._factories。

        为什么要让子类实现？
            因为"要跑哪些工厂、每个工厂什么参数"是业务信息，
            基类无法预知。把这一步交给子类，
            基类就彻底与业务解耦了。

        典型实现（GenericQueueTask 里）：
            遍历 TASK_REGISTRY，把启用的类型转成 TaskFactory。
        """
        raise NotImplementedError

    # =========================================================================
    # 通用运行逻辑
    # =========================================================================

    def run(self):
        """
        由 executor 调用一次，跑完整个队列直到结束。

        流程：
            1. 构造并启动 TaskQueue
            2. 调用子类的 _build_factories()，把工厂注册进队列
            3. 循环 tick，直到队列全部完成或用户请求停止
            4. 收尾：停止队列、刷新信息
        """
        self.log_info(f"========== {self.name} 启动 ==========")

        # ---------------------------------------------------------------------
        # 1. 构造 TaskQueue
        #    executor / app / scene 由 SGBaseTask 提供，直接透传给队列。
        # ---------------------------------------------------------------------
        self.queue = TaskQueue(
            executor=self.executor,
            app=self._app,
            scene=self.scene,
        )
        self.queue.start()

        # ---------------------------------------------------------------------
        # 2. 让子类填充工厂
        #    先 clear 是为了防止 run() 被重复调用时残留旧工厂。
        # ---------------------------------------------------------------------
        self._factories.clear()
        self._build_factories()
        for factory in self._factories:
            self.queue.add_factory(factory)

        tick_interval = float(self.config.get("Tick Interval", 1.0))

        # ---------------------------------------------------------------------
        # 3. 主循环
        #    - all_done()   ：队列中所有工厂都完成
        #    - exit_is_set()：用户点了停止
        #    - paused       ：用户暂时挂起，此时不 tick，只 sleep
        # ---------------------------------------------------------------------
        while not self.queue.all_done():
            if self.exit_is_set():
                self.log_info("用户请求停止")
                break
            if self.paused:
                self.sleep(tick_interval)
                continue

            self.queue.tick()          # 让队列补任务、推进状态
            self._update_info()        # 刷新 UI 进度
            self.sleep(tick_interval)

        # ---------------------------------------------------------------------
        # 4. 收尾
        # ---------------------------------------------------------------------
        self.queue.stop()
        self._update_info()
        self.log_info(f"========== {self.name} 结束 ==========")
        return True

    # =========================================================================
    # UI 信息展示
    # =========================================================================

    def _update_info(self):
        """
        把队列快照铺到 UI 上。

        采用循环展开而非逐项 info_set，是为了：
            - 快照新增字段时无需改这里
            - 与 GenericQueueTask 的"字段自动扩展"理念保持一致
        """
        if self.queue is None:
            return
        snapshot = self.queue.get_snapshot()
        for key, value in snapshot.items():
            label = key.replace("_", " ").title()
            self.info_set(label, value)