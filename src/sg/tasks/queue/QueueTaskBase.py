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
#   SGBaseTask      ← 提供 log_info / sleep / paused / exit_is_set / info_set
#     └── QueueTaskBase ← 本类
#           └── UnifiedQueue ← JSON 配置驱动的统一入口
#
# 配置源钩子：
#   _queue_global_params() 默认读 ok-script GUI 配置；
#   JSON 驱动的子类（UnifiedQueue）覆写为读 task_config.json。
# =============================================================================
class QueueTaskBase(SGBaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ---------------------------------------------------------------------
        # 通用配置：所有走队列模式的任务都共享这几项。
        # 业务特有的配置（等级、资源类型……）由子类自己追加。
        # ---------------------------------------------------------------------
        self.default_config.update({
            "Tick Interval": 1.0,
            "Continue After Failure": True,
        })
        self.config_description.update({
            "Tick Interval": "调度间隔秒数。Scheduler tick interval.",
            "Continue After Failure": (
                "有任务失败时是否继续跑完剩余次数；"
                "关闭则该类型队列立即停止。"
                "Continue running remaining count after a failure; "
                "off = stop that task type."
            ),
        })
        self.name = "Queue Task"
        self.description = "队列模式任务基类。"

        # ---------------------------------------------------------------------
        # 运行时状态：
        #   self.queue      ：TaskQueue 实例，run() 开始时创建
        #   self._factories ：子类通过 _build_factories() 填充的工厂列表
        # ---------------------------------------------------------------------
        self.queue = None
        self._factories = []

    def _queue_global_params(self):
        """
        队列全局参数（tick_interval / continue_after_failure）的配置源钩子。

        默认实现读 ok-script GUI 配置（老行为，薄壳任务不受影响）；
        JSON 驱动的子类覆写本方法，从 TaskConfigLoader 的解析结果取值。
        返回 dict：{"tick_interval": float, "continue_after_failure": bool}
        """
        return {
            "tick_interval": float(self.config.get("Tick Interval", 1.0)),
            "continue_after_failure": bool(
                self.config.get("Continue After Failure", True)
            ),
        }

    # =========================================================================
    # 子类接口
    # =========================================================================

    def _reload_config(self):
        """
        每次 run() 启动前的配置刷新钩子，默认空操作。

        子类配置源是文件（如 UnifiedQueue 的 task_config.json）时覆写：
        从磁盘重读并更新内部状态，保证 webui 改完配置、
        不重启程序、直接重跑任务即生效。
        抛出的异常（配置结构非法等）会在任务启动时直接失败，
        与 __init__ 时解析失败的语义一致。
        """

    def _build_factories(self):
        """
        子类实现：把 TaskFactory 追加到 self._factories。

        典型实现（UnifiedQueue 里）：
            把 task_config.json 中启用的类型转成 TaskFactory。
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
        # 全局失败策略 / 调度间隔：默认读 GUI 配置；
        # 子类可覆写 _queue_global_params() 换配置源（如 JSON 文件）。
        params = self._queue_global_params()
        self.queue.continue_after_failure = bool(params["continue_after_failure"])
        self.queue.start()

        # ---------------------------------------------------------------------
        # 1.5 配置重载钩子：每次 run 都给子类机会刷新配置源。
        #     JSON 驱动的子类（UnifiedQueue）覆写为从磁盘重读，
        #     保证 webui 改完配置后不重启程序、直接重跑任务即生效。
        # ---------------------------------------------------------------------
        self._reload_config()

        # ---------------------------------------------------------------------
        # 2. 让子类填充工厂
        # ---------------------------------------------------------------------
        self._factories.clear()
        self._build_factories()
        for factory in self._factories:
            self.queue.add_factory(factory)
        self._log_factories_summary()

        tick_interval = float(self._queue_global_params()["tick_interval"])

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
            - 与快照字段自动扩展的理念保持一致
        """
        if self.queue is None:
            return
        snapshot = self.queue.get_snapshot()
        for key, value in snapshot.items():
            label = key.replace("_", " ").title()
            self.info_set(label, value)

    def _log_factories_summary(self):
        """
        启动首行：所有工厂的单行简览（key/count/max_active/mode），
        便于一眼核对本次调度计划。
        逐条详情由 add_factory / [Factory]（如子类有）在后续行输出。
        """
        if not self._factories:
            self.log_info("[Factories] 0 个: 未启用任何任务类型")
            return
        summary = " | ".join(
            f"{f.name_prefix}(x{f.count},active{f.max_active}"
            f"{',one_shot' if f.one_shot else ''}"
            f"{f',cron={f.cron}' if f.cron else ''}"
            f"{',march' if f.requires_march_queue else ''})"
            for f in self._factories
        )
        self.log_info(f"[Factories] {len(self._factories)} 个: {summary}")