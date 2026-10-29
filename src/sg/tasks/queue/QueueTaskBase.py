import threading
import time

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
            # 注意："Tick Interval" 只为兼容既有 JSON/GUI 配置保留，
            # 不再驱动主循环节奏（节奏由 next_wakeup_time 事件驱动，
            # 见 _run_queue_cycle 第 3 段注释）。
            "Tick Interval": 1.0,
            "Continue After Failure": True,
            "Max Failures": 0,
            # 事件驱动等待的上/下限：
            #   Max Idle Sleep：无明确唤醒点（next_wakeup_time=None）或
            #     paused 挂起时的兜底休眠上限；
            #   Min Sleep：防止 wait=0 时空转烧 CPU 的下限。
            "Max Idle Sleep": 30.0,
            "Min Sleep": 0.05,
        })
        self.config_description.update({
            "Tick Interval": (
                "【已废弃，不再驱动节奏】保留仅为兼容旧配置文件。"
                "[Deprecated] Kept for config compatibility only; "
                "scheduling is now event-driven."
            ),
            "Continue After Failure": (
                "有任务失败时是否继续跑完剩余次数；"
                "关闭则该类型队列立即停止。"
                "Continue running remaining count after a failure; "
                "off = stop that task type."
            ),
            "Max Failures": (
                "同一任务类型最大失败次数，达到后不再补建该类型"
                "（0 = 不限）。任务级 max_failures > 0 时优先。"
                "Max failures per task type before it stops being "
                "replenished; 0 = unlimited."
            ),
            "Max Idle Sleep": (
                "队列空闲时的最长休眠秒数（无待触发任务或暂停时的兜底唤醒间隔）。"
                "Max seconds to sleep when the queue is idle or paused."
            ),
            "Min Sleep": (
                "两次调度检查之间的最小休眠秒数，防止空转。"
                "Minimum seconds between scheduling checks to avoid busy-spin."
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

        # ---------------------------------------------------------------------
        # 事件驱动唤醒基础设施（B2.2）：
        #   _wake_cond/_wake_flag：自管等待，不用 TaskExecutor.sleep——
        #   框架的 sleep 被 notify 后无法提前取消，必须用 Condition 自控。
        #   _last_info_version：UI 刷新节流（B2.4），-1 = 强制刷。
        # ---------------------------------------------------------------------
        self._wake_cond = threading.Condition()
        self._wake_flag = False
        self._last_info_version = -1
        self._max_idle_sleep = 30.0
        self._min_sleep = 0.05

    def _wait_wake(self, timeout):
        """
        挂起当前线程最多 timeout 秒；期间任一 _notify_wake() 立即唤醒。

        _wake_flag 是"醒来但还没消费"的凭据：notify 与 wait 之间存在
        竞态（notify 发生在进入 wait 前），旗标保证这种唤醒不丢失。
        wait 返回（含超时）都清旗标——超时后旗标即便为真也只是
        一次未消费的唤醒，丢弃是安全的。
        """
        with self._wake_cond:
            if self._wake_flag:
                self._wake_flag = False
                return
            self._wake_cond.wait(timeout)
            self._wake_flag = False

    def _notify_wake(self):
        """唤醒 _wait_wake（可从任意线程调用，notify_all 保险）。"""
        with self._wake_cond:
            self._wake_flag = True
            self._wake_cond.notify_all()

    def _queue_global_params(self):
        """
        队列全局参数的配置源钩子。

        默认实现读 ok-script GUI 配置（老行为，薄壳任务不受影响）；
        JSON 驱动的子类覆写本方法，从 TaskConfigLoader 的解析结果取值。
        返回 dict：{"tick_interval": float, "continue_after_failure": bool,
                    "max_failures": int, "max_idle_sleep": float,
                    "min_sleep": float}
        tick_interval 仍返回（兼容子类/日志），但主循环不再用它驱动节奏。
        """
        return {
            "tick_interval": float(self.config.get("Tick Interval", 1.0)),
            "continue_after_failure": bool(
                self.config.get("Continue After Failure", True)
            ),
            "max_failures": int(self.config.get("Max Failures", 0) or 0),
            "max_idle_sleep": float(self.config.get("Max Idle Sleep", 30.0)),
            "min_sleep": float(self.config.get("Min Sleep", 0.05)),
        }

    def _after_queue_done(self):
        """
        队列全部任务自然完成（非用户停止）后的收尾钩子，默认空操作。

        JSON 驱动的子类（UnifiedQueue）覆写：按配置把应用切到
        指定分身用户（关当前应用 -> am start --user 启动目标用户）。
        钩子在 queue.stop() 之后、run() 返回之前调用；
        异常只记日志，不影响 run() 的返回值。
        """

    def _on_queue_stopped(self):
        """
        队列停止后的收尾钩子（无论自然完成还是用户停止都会触发），
        默认空操作。

        与 _after_queue_done 的区别：后者只在"自然跑完且非用户停止"
        时调用；本钩子是所有停止路径的统一收尾点，适合做
        「运行期统计落盘」这类与停止原因无关的事。
        异常由调用方捕获记日志，不影响 run() 返回值。
        """

    def _setup_queue_hooks(self):
        """
        队列钩子装配点（TaskQueue 构造后、start 前调用），默认空操作。

        5.7 任务间流转：UnifiedQueue 覆写本方法，把「按任务 user_id
        切应用分身」挂到 queue.before_interaction；常规模式不挂任何
        钩子，调度行为与老版本完全一致。
        """

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
            1. 循环调用 _run_queue_cycle()：每圈跑完一个 user 的队列。
            2. 每圈返回 "yield" / "done_yield"（当前 user 无事可做 /
               自然跑完但还有下一个 user 待跑）→ 继续下一圈；
               "done" / "stop" → 结束。多 user 轮转（5.5）的圈数
               由子类协议控制，默认实现一圈即终（单 user）。
            3. 用户停止 / 异常随时终止整个 run。
        返回 True 表示本次激活完成（无论跑了几个 user）。
        """
        self.log_info(f"========== {self.name} 启动 ==========")
        try:
            while True:
                try:
                    result = self._run_queue_cycle()
                except Exception as e:
                    self.log_info(f"队列循环异常终止: {e}")
                    raise
                if result in ("yield", "done_yield"):
                    continue
                break
        finally:
            # finally 强制刷最终态：此时队列可能已stop但最后一次 tick 后
            # version 又变过（如 stop 路径），version 门控会跳过——
            # 重置 _last_info_version 保证 UI 显示收敛到终态。
            self._last_info_version = -1
            self._update_info()
        self.log_info(f"========== {self.name} 结束 ==========")
        return True

    def _rotation_yield_reason(self, queue_done=False):
        """
        当前 user 是否无事可做：返回让位原因字符串（记日志用），
        None = 不让位。默认 None（单 user 场景，一圈即 run 终点）。

        多 user 轮转由 UnifiedQueue 覆写。queue_done=True 表示队列
        已自然跑完（all_done），覆写方据此区分「跑完即切」与
        「中途让位」。
        """
        return None

    def _run_queue_cycle(self):
        """
        跑一圈完整队列：构造 TaskQueue → 重读配置 → 建工厂 → tick
        主循环 → 收尾钩子。

        返回值：
          "done"   队列自然跑完（all_done 且非用户停止）
          "yield"  轮转让位：队列未自然完成，但 _rotation_yield_reason()
                   判定当前 user 无事可做（多 user 轮转，5.5）；
                   单 user 默认钩子恒 None，不会返回该值
          "stop"   用户请求停止
        """
        # ---------------------------------------------------------------------
        # 1. 构造 TaskQueue
        #    executor / app / scene 由 SGBaseTask 提供，直接透传给队列。
        # ---------------------------------------------------------------------
        self.queue = TaskQueue(
            executor=self.executor,
            app=self._app,
            scene=self.scene,
        )
        params = self._queue_global_params()
        self.queue.continue_after_failure = bool(params["continue_after_failure"])
        self.queue.max_failures = int(params.get("max_failures", 0) or 0)
        # 队列级钩子装配（5.7 任务间流转：子类可在此挂 before_interaction）。
        # 默认空操作，队列行为与老版本完全一致。
        self._setup_queue_hooks()
        self.queue.start()

        # ---------------------------------------------------------------------
        # 1.2 唤醒装配（B2.2）：
        #   - wake_callback：队列状态变化时反向唤醒主循环的 _wait_wake，
        #     替代固定轮询。
        #   - exit_event.bind_condition：用户停止时 ExitEvent.set() 会
        #     notify_all 本 Condition → _wait_wake 立刻返回 → 主循环
        #     exit_is_set() 退出。getattr+hasattr 防御：测试环境 executor
        #     可能是无 exit_event 的 mock。
        # ---------------------------------------------------------------------
        self.queue.wake_callback = self._notify_wake
        exit_ev = getattr(self.executor, "exit_event", None)
        if exit_ev is not None and hasattr(exit_ev, "bind_condition"):
            exit_ev.bind_condition(self._wake_cond)

        # ---------------------------------------------------------------------
        # 1.5 配置重载钩子：每圈都给子类机会刷新配置源。
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

        # 等待时长上下限与 "Tick Interval" 同源读取（_queue_global_params），
        # 每次 cycle 开头刷新，保证 webui 改配置即生效。
        # 注意 tick_interval 不再读取：节奏改由 next_wakeup_time 驱动，
        # "Tick Interval" 键仅为 JSON/GUI 兼容保留。
        self._max_idle_sleep = max(
            0.001, float(params.get("max_idle_sleep", 30.0) or 30.0)
        )
        self._min_sleep = max(0.0, float(params.get("min_sleep", 0.05) or 0.0))

        # ---------------------------------------------------------------------
        # 3. 主循环（事件驱动，B2.3）
        #    - all_done()   ：队列中所有工厂都完成
        #    - exit_is_set()：用户点了停止
        #    - paused       ：用户暂时挂起，此时不 tick，只等唤醒或超时
        #    - _rotation_yield_reason() 非 None：当前 user 无事可做，
        #      提前结束本圈让位给下一个 user（单 user 默认恒 None）
        #
        #    节奏控制：每次 tick 后按 next_wakeup_time() 计算下一次需要
        #    调度的最早时间点，_wait_wake 等到该点或被 wake_callback /
        #    exit_event 提前唤醒。不再固定 sleep(tick_interval)。
        # ---------------------------------------------------------------------
        while not self.queue.all_done():
            if self.exit_is_set():
                self.log_info("用户请求停止")
                break
            if self.paused:
                # paused 兜底唤醒 = Max Idle Sleep：用户 unpause 走
                # executor.start() → _wake_executor，不经本类的
                # _wake_cond，无法即时唤醒；最多 Max Idle Sleep 后
                # 自然醒来重查 paused，可接受。
                self._wait_wake(self._max_idle_sleep)
                continue

            if self._rotation_yield_reason() is not None:
                self._update_info()
                break

            self.queue.tick()          # 让队列补任务、推进状态
            self._update_info()        # 刷新 UI 进度
            next_ts = self.queue.next_wakeup_time()
            if next_ts is None:
                # 无明确唤醒点：全队列忙/无可调度任务，用兜底间隔轮询
                wait = self._max_idle_sleep
            else:
                wait = next_ts - time.time()
            wait = max(self._min_sleep, min(wait, self._max_idle_sleep))
            self._wait_wake(wait)

        # ---------------------------------------------------------------------
        # 4. 收尾
        #    - 用户停止       → "stop"
        #    - 自然跑完       → "done"（并触发 _after_queue_done 收尾）；
        #                       多 user 轮转且 _rotation_yield_reason(queue_done=True)
        #                       判定还有下一家 → "done_yield"（本圈已
        #                       跑完，继续下一圈切下一个 user）
        #    - 中途让位       → "yield"（不触发 _after_queue_done：
        #                      应用切换由轮转层负责，且队列还没"完成"）
        # ---------------------------------------------------------------------
        user_stopped = self.exit_is_set()
        all_done = self.queue.all_done()
        if user_stopped:
            reason = None
        else:
            reason = self._rotation_yield_reason(queue_done=all_done)
        self.queue.stop()
        try:
            self._on_queue_stopped()
        except Exception as e:
            self.log_info(f"收尾钩子 _on_queue_stopped 异常: {e}")
        if all_done and not user_stopped:
            try:
                self._after_queue_done()
            except Exception as e:
                self.log_info(f"收尾钩子 _after_queue_done 异常: {e}")
        if user_stopped:
            return "stop"
        if all_done:
            # 自然完成：默认一圈即终；多 user 轮转覆写 reason 判定
            # 还有下一家时返回 done_yield（run() 主循环继续下一圈）
            return "done_yield" if reason is not None else "done"
        if reason is not None:
            return "yield"
        return "done"

    # =========================================================================
    # UI 信息展示
    # =========================================================================

    def _update_info(self):
        """
        把队列快照铺到 UI 上。

        采用循环展开而非逐项 info_set，是为了：
            - 快照新增字段时无需改这里
            - 与快照字段自动扩展的理念保持一致

        version 门控（B2.4）：队列状态无变化（version 不变）时跳过
        get_snapshot + info_set 全量刷新。version 属性缺失（老队列
        对象）回退为每次全量刷新，防御性兼容。
        """
        if self.queue is None:
            return
        version = getattr(self.queue, "version", None)
        if version is not None and version == self._last_info_version:
            return
        if version is not None:
            self._last_info_version = version
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
