import datetime
import time
import uuid

from croniter import croniter

from ok import Logger


from src.scheduler.task_status import (
    InteractionResult,
    TaskStatus,
)

# TaskRestartRequested 定义在 SGBaseTask 所在模块，
# 只做异常类型的引用，不会形成循环 import。
from src.sg.tasks.SGBaseTask import TaskRestartRequested

logger = Logger.get_logger(__name__)


class TaskFactory:
    """
    任务工厂。

    =========================================================
    关键字段
    =========================================================
    count                 总提交次数，0 = 无限
    max_active            占军队队列型任务同时 IN_PROGRESS 的上限
    requires_march_queue  该任务类型是否占用游戏军队队列
    next_trigger_delay    下一个同类任务的触发延迟（秒）
                          - 0      → 本任务完成时立即触发下一个
                          - 86400  → 本任务完成后 24h 触发下一个
    one_shot              一次性任务：整个工厂只建 1 个实例，
                          终态后按剩余次数自重排，不走 _create_follower
    cron                  定时任务 cron 表达式：trigger_time 由 cron 计算
                          下一次触发点，与 next_trigger_delay 互斥

    =========================================================
    建立策略（配合 TaskQueue）
    =========================================================
    - 初始：TaskQueue 按下面的"初始数量"建第一批任务
        占军队队列型：min(max_active, count)，count=0 时取 max_active
        非占用型   ：1
    - 后续：每有一个任务 SUCCESS，立即建下一个同类任务，
            触发时间 = 本任务完成时间 + next_trigger_delay
    =========================================================
    """

    def __init__(
        self,
        task_class,
        count=1,
        max_active=1,
        requires_march_queue=False,
        next_trigger_delay=0.0,
        kwargs=None,
        name_prefix=None,
        one_shot=False,
        cron=None,
    ):
        self.task_class = task_class
        self.count = count
        self.max_active = max_active
        self.requires_march_queue = requires_march_queue
        self.next_trigger_delay = next_trigger_delay
        self.kwargs = kwargs or {}
        self.name_prefix = name_prefix or task_class.__name__
        self.one_shot = bool(one_shot)
        self.cron = cron
        self.exhausted = False
        self.submitted = 0        # 已 create 的任务实例数（原语义）
        self.finished_count = 0   # 单实例复用型已消耗的执行次数
        self.initial_filled = False
        self.key = uuid.uuid4().hex[:8]

    def can_submit_more(self):
        """
        是否还能继续提交新任务（只看 count 上限与 exhausted 标记）。

        submitted：已 create 的任务实例数（原语义，所有工厂一致）。
        finished_count：已消耗的执行次数，仅单实例复用型（one_shot /
        cron）使用——它们复用同一个实例，不产生新 create，每执行完
        一次终态 +1；submitted == count 只说明实例已建，不代表次数
        用完，因此单实例复用型以 finished_count 判断收敛。
        """
        if self.exhausted:
            return False
        if self.one_shot or self.cron:
            return self.finished_count < max(1,self.count)
        if self.count > 0 and self.submitted >= self.count:
            return False
        return True

    def create(self, executor, app, scene):
        """
        创建一个具体任务实例。
        流程：
          1. 用 executor / app 构造任务对象
          2. 调用 after_init，完成 config 加载和 scene 绑定
          3. 把 kwargs 中的属性逐个 setattr 到任务上
          4. 如果 kwargs 没显式设置 next_trigger_delay，用工厂的
          5. 递增 submitted 并给任务一个可读名字
          6. 打上 factory_key，方便 TaskQueue 按工厂分组
        """
        task = self.task_class(executor=executor, app=app)
        task.after_init(executor=executor, scene=scene)


        for k, v in self.kwargs.items():
            setattr(task, k, v)
            setattr(task.extra_config, k, v)

        # 并发上限注入任务实例（kwargs 显式设置时以 kwargs 为准）。
        # RallyConfig.select_team 依据它决定是否切换省体力队伍：
        # 并发=1 选队，并发>1 跳过（省体力队伍只有一支，多路共用会冲突）。
        if "max_active" not in self.kwargs:
            task.max_active = self.max_active

        # 工厂级别的 next_trigger_delay 覆盖任务默认值，
        # 但如果用户通过 kwargs 显式设了就不覆盖
        if "next_trigger_delay" not in self.kwargs:
            task.next_trigger_delay = self.next_trigger_delay

        self.submitted += 1
        task.name = f"{self.name_prefix} #{self.submitted}"
        task.factory_key = self.key
        return task


class TaskQueue:
    """
    任务队列：纯逻辑对象，不继承 BaseTask。
    由外部调用 tick() 推进。

    =========================================================
    状态流转
    =========================================================
    SCHEDULED  --(trigger_time ≤ now + 有名额)--> PENDING
    PENDING    --(被 pick)--> RUNNING
    RUNNING    --(SUCCESS)--> IN_PROGRESS --(时间到)--> DONE
               --(RETRY)--> PENDING
               --(FAILED)--> FAILED

    =========================================================
    核心概念
    =========================================================
    trigger_time：最早可执行时间（硬约束）
        - now < trigger_time  → 绝对不执行，留在 SCHEDULED
        - now ≥ trigger_time  → 可以执行，等待被 pick

    Max Active：占军队队列型任务同时 IN_PROGRESS 的上限
        - 只对 requires_march_queue=True 的工厂生效
        - 非占用型任务不检查

    FIFO：到点的多个任务按插入顺序排队
        - 插入顺序天然等于触发时间顺序
        - 初始填充同时插入的 trigger 都是 0
        - 后续 follower 按上一个任务的 finish 顺序插入

    =========================================================
    建立时机
    =========================================================
    初始填充：每个工厂启动时按"初始数量"建第一批
    后续补建：占军队队列型每进一个 IN_PROGRESS 立即补建
              （非占用型也补建，靠 count 上限控制）
    =========================================================
    """

    def __init__(self, executor, app, scene):
        self.executor = executor
        self.app = app
        self.scene = scene

        self.tasks = []
        self.factories = []
        self.current_interaction = None

        self.started = False
        self.paused = False

        # ====================================================
        # 任务重排参数
        # ====================================================
        # 单个任务因 TaskRestartRequested 被重排的次数上限。
        # 超过后不再重排，直接标记 FAILED 推进到下一个任务，
        # 避免环境问题让同一个任务无限循环。
        # 目前是队列级默认值，后续可下沉到 TaskFactory
        # 做成 per-factory 配置。
        self.max_restart_count = 2

        # 重排后到下次执行的等待秒数。
        # 退出游戏弹窗刚被 ESC 关掉，界面可能还没稳定；
        # 短暂等待避免下一轮立刻在旧帧上误判。
        self.restart_delay = 1.0

        # --------------------------------------------------------
        # ====================================================
        # 失败策略（队列级全局参数）
        # ====================================================
        # True（默认）：某工厂的全部任务都进入终态（DONE/FAILED）但
        #   提交数还没用完 count 时，继续补建下一个任务——失败的次数
        #   也算在 count 里，跑满为止。
        # False：出现上述"卡死"局面时把工厂标记 exhausted，剩余次数
        #   作废，该类型停止。
        # 该值由 QueueTaskBase.run() 在构造队列时从 GUI 配置注入。
        self.continue_after_failure = True
    # --------------------------------------------------------

    def start(self):
        self.started = True
        self.paused = False
        logger.info("TaskQueue started")

    def stop(self):
        self.started = False
        logger.info("TaskQueue stopped")

    def pause(self):
        self.paused = True
        logger.info("TaskQueue paused")

    def resume(self):
        self.paused = False
        logger.info("TaskQueue resumed")

    # --------------------------------------------------------
    # 添加 / 移除
    # --------------------------------------------------------

    def add(self, task):
        """
        加入队列。根据 trigger_time 决定初始状态：
          - trigger_time ≤ now → PENDING（立即可以执行）
          - trigger_time > now → SCHEDULED（等触发时间）
        """
        if getattr(task, "task_id", None) is None:
            task.task_id = uuid.uuid4().hex[:8]

        if not hasattr(task, "trigger_time"):
            task.trigger_time = 0.0
        if not hasattr(task, "next_trigger_delay"):
            task.next_trigger_delay = 0.0

        now = time.time()
        if getattr(task, "status", None) == TaskStatus.FAILED:
            # 调用方（cron 预置失败等）已判定终态，不要覆盖为 PENDING，
            # 否则 all_done() 因非终态任务永远不收敛。
            pass
        elif task.trigger_time <= now:
            task.status = TaskStatus.PENDING
        else:
            task.status = TaskStatus.SCHEDULED

        task.estimated_finish_time = None
        task.finished_at = None
        task.next_retry_time = 0.0
        task.last_error = None
        if not hasattr(task, "paused"):
            task.paused = False

        # 重排计数器：从 0 起算，只在 _handle_restart_requested 里 +1。
        # 每次进入队列都重置，follower 是全新实例自然从 0 开始。
        task.restart_count = 0
        
        self.tasks.append(task)
        logger.info(
            f"TaskQueue add: {task.name} ({task.task_id}) "
            f"status={task.status.value}"
        )
        return task.task_id

    def add_factory(self, factory: TaskFactory):
        self.factories.append(factory)
        logger.info(
            f"TaskQueue add factory: {factory.name_prefix} "
            f"x {factory.count}, max_active={factory.max_active}, "
            f"march_queue={factory.requires_march_queue}, "
            f"delay={factory.next_trigger_delay}"
        )

    def remove_task(self, task_id):
        for i, t in enumerate(self.tasks):
            if t.task_id == task_id:
                self.tasks.pop(i)
                logger.info(f"TaskQueue remove: {t.name} ({task_id})")
                if self.current_interaction is t:
                    self.current_interaction = None
                return True
        return False

    def clear(self):
        self.tasks.clear()
        self.factories.clear()
        self.current_interaction = None
        logger.info("TaskQueue cleared")

    def pause_task(self, task_id):
        for t in self.tasks:
            if t.task_id == task_id:
                t.paused = True
                logger.info(f"TaskQueue pause_task: {t.name}")
                return True
        return False

    def resume_task(self, task_id):
        for t in self.tasks:
            if t.task_id == task_id:
                t.paused = False
                logger.info(f"TaskQueue resume_task: {t.name}")
                return True
        return False

    # --------------------------------------------------------
    # 主 tick
    # --------------------------------------------------------

    def tick(self):
        """
        外部需要定时调用本方法（例如每秒一次）。

        顺序：
          1. 检查 IN_PROGRESS 是否到期
          2. 若已有任务在交互，直接返回，保证同屏互斥
          3. SCHEDULED → PENDING（trigger_time 到 + 有名额）
          4. 挑一个 PENDING 做交互
          5. 初始填充（每个工厂首次启动时执行一次）
          6. 失败策略扫描：全部终态但次数未用完的工厂，按策略补建或停止
        """

        now = time.time()

        # 1. IN_PROGRESS 到期处理
        self._check_in_progress(now)

        # 2. 屏幕互斥：同一时刻只允许一个交互
        if self.current_interaction is not None:
            return

        # 3. SCHEDULED → PENDING
        self._activate_scheduled(now)

        # 4. 挑一个 PENDING 交互
        candidate = self._pick_next(now)
        if candidate is not None:
            self._do_interaction(candidate)

        # 5. 初始填充
        self._replenish()

        # 6. 失败策略：补建 / 停止
        self._check_stalled_factories(now)


    # --------------------------------------------------------
    # 内部逻辑
    # --------------------------------------------------------

    def _check_in_progress(self, now):
        """
        检查所有 IN_PROGRESS 任务：
          - 还没到 estimated_finish_time：跳过
          - 到时间了：调用 task.check_completed() 二次校验
              - 通过 → DONE
              - 不通过 → PENDING，next_retry_time 延后 5s
        """
        for task in self.tasks:
            if task.paused:
                continue
            if task.status != TaskStatus.IN_PROGRESS:
                continue
            if task.estimated_finish_time is None:
                continue
            if now < task.estimated_finish_time:
                continue

            try:
                ok = task.check_completed()
            except Exception as e:
                ok = False
                task.last_error = str(e)

            if ok:
                task.status = TaskStatus.DONE
                task.finished_at = now
                # one_shot / cron：终态即触发下一次排程（次数在这里扣减）
                self._reschedule_for_next_run(task, now)
                logger.info(f"TaskQueue done: {task.name}")
            else:
                task.status = TaskStatus.PENDING
                task.next_retry_time = now + 5
                logger.info(f"TaskQueue recheck failed: {task.name}")

    def _activate_scheduled(self, now):
        """
        把 trigger_time 已到、且有名额的 SCHEDULED 任务转 PENDING。

        trigger_time 是硬约束：
          - trigger_time > now  → 无论有多少空闲名额都不激活
          - trigger_time ≤ now  → 才开始考虑名额

        名额只对占军队队列型任务检查：
          IN_PROGRESS 数 < factory.max_active 才允许激活。
        非占用型任务不检查名额（max_active 对它无意义）。
        """
        for task in self.tasks:
            if task.paused:
                continue
            if task.status != TaskStatus.SCHEDULED:
                continue
            if task.trigger_time > now:
                continue

            factory = self._find_factory(task)
            if factory is not None and factory.requires_march_queue:
                in_progress = self._count_in_progress(factory.key)
                if in_progress >= factory.max_active:
                    continue

            logger.info(
                f"TaskQueue activate: {task.name} "
                f"(trigger={task.trigger_time:.0f})"
            )
            task.status = TaskStatus.PENDING

    def _pick_next(self, now):
        """
        按插入顺序（FIFO）找第一个可执行的 PENDING 任务。

        为什么 FIFO 就够了：
          任务的插入顺序天然与 trigger_time 顺序一致。
          - 初始填充时同时插入的几个任务 trigger 都是 0
          - 后续 follower 按"上一个任务的 finish"顺序插入
            所以先插入的 trigger 一定更早
          因此 FIFO 就是"最早到点的先跑"。

        占军队队列型任务额外检查 IN_PROGRESS < max_active；
        不占队列型任务不检查。
        """
        for task in self.tasks:
            if task.paused:
                continue
            if task.status != TaskStatus.PENDING:
                continue
            if task.next_retry_time > now:
                continue

            factory = self._find_factory(task)
            if factory is not None and factory.requires_march_queue:
                in_progress = self._count_in_progress(factory.key)
                if in_progress >= factory.max_active:
                    continue

            return task
        return None

    def _do_interaction(self, task):
        """
        执行一次交互：
          - 切成 RUNNING
          - 调 task.run_interaction() 拿 (InteractionResult, wait_seconds)
          - 按结果更新状态
          - SUCCESS：常规任务立即建立同类的下一个任务；
            one_shot / cron 任务不建 follower（"一次"要等
            check_completed() 确认，排程在终态路径里做）
          - FAILED：先问 _reschedule_for_next_run（one_shot / cron
            自重排），常规任务交由 tick 的 stalled 扫描按失败策略处理
        异常处理：
        - TaskRestartRequested → 走重排流程（见 _handle_restart_requested）
        - 其它异常            → 记 FAILED，不拖垮整个 tick
        """
        self.current_interaction = task
        task.status = TaskStatus.RUNNING
        logger.info(f"TaskQueue interaction: {task.name}")

        try:
            result, wait_seconds = task.run_interaction()
        except TaskRestartRequested as e:
            # 现场已不可信（例如恢复层 ESC 误触发退出游戏弹窗）。
            # 交给 _handle_restart_requested 决定重排还是失败。
            self.current_interaction = None
            self._handle_restart_requested(task, e)
            return
        except Exception as e:
            logger.error(f"TaskQueue exception: {task.name}", e)
            task.status = TaskStatus.FAILED
            task.last_error = str(e)
            self.current_interaction = None
            # one_shot / cron 也可能在这里终态，统一交给重排判定
            self._reschedule_for_next_run(task, time.time())
            return

        self.current_interaction = None

        if result == InteractionResult.SUCCESS:
            # 统一进 IN_PROGRESS，wait=0 的任务下一 tick 会转 DONE
            task.status = TaskStatus.IN_PROGRESS
            factory = self._find_factory(task)
            # 如果是一次性任务，则清空下一次任务触发时间,这两种由_reschedule_for_next_run控制
            if factory is not None and (factory.one_shot or factory.cron):
                task.estimated_finish_time = time.time()
            else:
                task.estimated_finish_time = time.time() + max(0.0, wait_seconds)

            # one_shot / cron 的下一次排程不在这里做：
            # "一次"要等 check_completed() 确认真正完成才算数。
            if not self._is_special_task(task):
                # 立即建立同类的下一个任务
                self._create_follower(task)

            logger.info(
                f"TaskQueue wait: {task.name} in {task.estimated_finish_time:.1f}s"
            )
            return

        if result == InteractionResult.RETRY:
            task.status = TaskStatus.PENDING
            task.next_retry_time = time.time() + max(1.0, wait_seconds)
            logger.info(
                f"TaskQueue retry: {task.name} in {wait_seconds:.1f}s"
            )
            return

        # 默认终态失败；one_shot / cron 由 _reschedule_for_next_run 改写去向
        task.status = TaskStatus.FAILED
        if not self._reschedule_for_next_run(task, time.time()):
            logger.info(
                f"TaskQueue failed: {task.name} {task.last_error}"
            )
            return
        # one_shot / cron 已由 _reschedule_for_next_run 决定去向；
        # 常规任务的失败由 tick 的 stalled 扫描按失败策略补建或停止。

    def _is_special_task(self, task):
        """
        任务是否属于 one_shot / cron 工厂。
        这两类任务的下一次排程由 _reschedule_for_next_run 负责，
        与常规 follower / stalled 路径互斥。
        """
        factory = self._find_factory(task)
        return factory is not None and (
            factory.one_shot or factory.cron
        )

    def _handle_restart_requested(self, task, exc):
        """
        处理 TaskRestartRequested：决定重排还是标记失败。

        =========================================================
        为什么不用通用 except 统一判 FAILED
        =========================================================
        TaskRestartRequested 表达的是"现场不可信，从头再来"，
        与"这次没做成"不同：
        - 前者值得有限次重排，游戏可能只是弹了个错框；
        - 后者应该遵循任务自身的 RETRY / FAILED 策略。
        混在一起会丢掉重排能力，也会让临时环境问题误判为终态失败。

        =========================================================
        重排策略（当前）
        =========================================================
        - task.restart_count 累计（从 0 起算，本方法入口 +1）
        - ≤ max_restart_count → 状态回到 PENDING，
                                next_retry_time = now + restart_delay
        - > max_restart_count → 状态 FAILED，调度推进到下一个任务

        restart_count 在任务实例上累计，不会因中途 RETRY 清零。
        如果需要"成功一次后再重置"，那是 SUCCESS 路径的事，不在这里动。
        """
        task.restart_count = getattr(task, "restart_count", 0) + 1

        if task.restart_count > self.max_restart_count:
            task.status = TaskStatus.FAILED
            # one_shot / cron 在这里终态同样要决定下一次去向
            self._reschedule_for_next_run(task, time.time())
            logger.info(
                f"TaskQueue restart exceeded: {task.name} "
                f"已重排 {self.max_restart_count} 次仍失败，标记 FAILED: {exc}"
            )
            return

        task.status = TaskStatus.PENDING
        task.next_retry_time = time.time() + self.restart_delay
        logger.info(
            f"TaskQueue restart: {task.name} "
            f"({task.restart_count}/{self.max_restart_count}) "
            f"in {self.restart_delay:.1f}s: {exc}"
        )
        
    def _create_follower(self, finished_task):
        """
        任务 SUCCESS 后立即建立同类的下一个任务。

        下一个任务的触发时间 = 本任务完成时间 + next_trigger_delay。

        "本任务完成时间"取值优先级：
          1. estimated_finish_time（IN_PROGRESS 时有值）
          2. finished_at（兜底）
          3. now（最后兜底）

        典型效果：
          - 巨兽 (delay=0)：
              巨兽1 finish=12:05 → 巨兽3 trigger=12:05
              巨兽2 finish=12:06 → 巨兽4 trigger=12:06
          - 宝箱 (delay=86400)：
              宝箱1 DONE=12:00 → 宝箱2 trigger=次日 12:00
              24 小时内宝箱2 留在 SCHEDULED 不会被执行
        """
        factory = self._find_factory(finished_task)
        if factory is None:
            return

        if not factory.can_submit_more():
            logger.info(
                f"TaskQueue follower skipped: "
                f"{factory.name_prefix} 已达 count={factory.count}"
            )
            return

        next_task = factory.create(self.executor, self.app, self.scene)

        base_time = (
            finished_task.estimated_finish_time
            or finished_task.finished_at
            or time.time()
        )
        delay = getattr(next_task, "next_trigger_delay", 0.0)
        next_task.trigger_time = base_time + delay

        self.add(next_task)
        logger.info(
            f"TaskQueue follower: {next_task.name}, "
            f"trigger in {next_task.trigger_time - time.time():.1f}s"
        )

    # --------------------------------------------------------
    # 失败策略 / 自重排
    # --------------------------------------------------------

    def _check_stalled_factories(self, now):
        """
        失败策略扫描：某工厂的全部任务都进入终态（DONE/FAILED），
        但提交数还没用完 count → 该工厂"卡死"了。

        背景：补任务只有两条路径——SUCCESS 后的 _create_follower
        和一次性的 _replenish。任务 FAILED 后两条路都不触发，
        剩余次数永远建不出来，且 all_done() 因 can_submit_more()
        恒 True 而永远不满足，run() 主循环死转。

        按 self.continue_after_failure 二选一：
          True  → 补建下一个任务（trigger = now + next_trigger_delay），
                  失败的次数也算在 count 里，跑满为止。
          False → 标记 factory.exhausted = True，剩余次数作废，
                  该类型停止；can_submit_more() 由此返回 False，
                  all_done() 得以收敛。

        判定细节：
          - 只看本工厂名下的任务（factory_key 匹配）。
          - 有 PENDING / RUNNING / IN_PROGRESS / SCHEDULED 在身
            就不算卡死——SCHEDULED 覆盖 cron 下一次触发与
            next_trigger_delay 延迟两种情形。
          - exhausted / can_submit_more 已 False 的工厂直接跳过。
          - one_shot / cron 工厂不走这里：它们的重排在
            _reschedule_for_next_run 里做，且终态即"次数已减"，
            不存在卡死形态。
        """
        for factory in self.factories:
            if factory.exhausted or not factory.can_submit_more():
                continue
            if factory.one_shot or factory.cron:
                continue

            own = [
                t for t in self.tasks
                if getattr(t, "factory_key", None) == factory.key
            ]
            # 还没有任何任务（例如全部被手动移除）不算卡死，
            # 交给初始填充 / follower 的正常路径处理。
            if not own:
                continue
            if any(
                t.status not in (TaskStatus.DONE, TaskStatus.FAILED)
                for t in own
            ):
                continue

            if self.continue_after_failure:
                next_task = factory.create(
                    self.executor, self.app, self.scene
                )
                next_task.trigger_time = now + factory.next_trigger_delay
                self.add(next_task)
                logger.info(
                    f"TaskQueue continue after failure: {next_task.name}, "
                    f"trigger in {factory.next_trigger_delay:.1f}s"
                )
            else:
                factory.exhausted = True
                logger.info(
                    f"TaskQueue factory exhausted: {factory.name_prefix} "
                    f"存在失败任务且失败后停止，剩余次数作废 "
                    f"(submitted={factory.submitted}/{factory.count})"
                )

    def _reschedule_for_next_run(self, task, finished_at):
        """
        one_shot / cron 工厂的任务终态后的下一次排程。

        返回 True 表示该任务由本方法处理（调用方不要再走
        _create_follower / stalled 扫描）；False 表示与一次性 /
        定时无关，走常规路径。

        - one_shot：剩余次数记在 task.one_shot_remaining 上，
          每次终态 -1；仍 >0 时重置为 PENDING（next_retry_time 按
          next_trigger_delay 延后），同一实例循环执行。
          次数收敛由 can_submit_more()（finished_count < count）兜住。
          表达式非法时按 exhausted 处理并告警，绝不吞成无限循环。
        """
        factory = self._find_factory(task)
        if factory is None:
            return False

        if factory.one_shot:
            remaining = getattr(task, "one_shot_remaining", 1) - 1
            task.one_shot_remaining = remaining
            # one_shot 复用单实例、不再 create，把已执行的次数记到
            # finished_count 上（submitted 保持"已 create 数"原语义），
            # can_submit_more() 用 finished_count < count 判断收敛。
            factory.finished_count += 1
            if remaining > 0:
                task.status = TaskStatus.PENDING
                task.next_retry_time = finished_at + max(
                    0.0, getattr(task, "next_trigger_delay", 0.0)
                )
                logger.info(
                    f"TaskQueue one shot: {task.name} "
                    f"剩 {remaining} 次"
                )
            else:
                logger.info(
                    f"TaskQueue one shot: {task.name} 次数用完"
                )
            return True

        if factory.cron:
            # cron 复用单实例，每执行一次消耗一个名额；
            # 先解析表达式再扣名额，保证非法表达式一定留下 exhausted 标记。
            # 时区处理见 _next_cron_time：float 会被 croniter 按 UTC 解析。
            try:
                next_time = self._next_cron_time(
                    factory.cron, finished_at
                )
            except (KeyError, ValueError) as e:
                factory.exhausted = True
                logger.error(
                    f"TaskQueue cron invalid: {factory.name_prefix} "
                    f"expr={factory.cron!r}: {e}"
                )
                return True
            factory.finished_count += 1
            if not factory.can_submit_more():
                logger.info(
                    f"TaskQueue cron: {factory.name_prefix} "
                    f"次数用完 ({factory.count})，不再排下一次"
                )
                return True
            task.status = TaskStatus.SCHEDULED
            task.trigger_time = next_time
            logger.info(
                f"TaskQueue cron: {task.name} 下次触发 "
                f"{self._fmt_clock(next_time, time.time())}"
            )
            return True

        return False

    def _next_cron_time(self, expr, base_ts):
        """
        cron 表达式在 base_ts 之后的下一次触发点（本地纪元秒）。

        croniter 传 float 时间戳会按 UTC 解析（差时区），必须用
        本地 datetime 进出，再转回本地纪元秒。
        表达式非法抛 ValueError / KeyError，由调用方决定降级方式。
        """
        itr = croniter(expr, datetime.datetime.fromtimestamp(base_ts))
        return itr.get_next(datetime.datetime).timestamp()

    def _replenish(self):
        """
        初始填充：每个工厂启动时按"初始数量"一次性建满。
        只执行一次，用 factory.initial_filled 标记。

        为什么一次性建满，而不是每 tick 建一个：
        - 用户预期"并行=2 时队列里立刻出现巨兽1、巨兽2"
        - 每 tick 建一个会延迟一个 tick_interval，看起来只建了 1 个
        - 更关键的是：_create_follower 也会消耗 factory.submitted 名额，
            如果用 submitted 判断初始填充是否完成，会被 follower 抢跑，
            导致本该初始建的巨兽2 永远建不出来。
        用独立标记 initial_filled 就完全避开这个问题。

        初始数量：
        - 一次性任务   ：1（整个工厂共用一个实例循环执行）
        - 占军队队列型：min(max_active, count)，count=0 时取 max_active
        - 非占用型   ：1
        """
        for factory in self.factories:
            if factory.initial_filled:
                continue

            target = self._initial_count(factory)
            built = 0
            for _ in range(target):
                if factory.exhausted:
                    break
                if factory.count > 0 and factory.submitted >= factory.count:
                    break
                task = factory.create(self.executor, self.app, self.scene)
                if factory.one_shot:
                    # 一次性任务的剩余次数记在实例上，
                    # 终态时由 _reschedule_for_next_run 扣减。
                    # count=0（无限）对一次性任务无意义，按 1 次处理，
                    # 与 can_submit_more() 的 max(1, count) 口径一致。
                    task.one_shot_remaining = max(1, factory.count)
                if factory.cron:
                    # 首次执行也等 cron 到点：建任务即按 cron 预置
                    # trigger_time（SCHEDULED），而不是立即执行。
                    # 表达式非法则标 exhausted，任务不会被激活。
                    try:
                        task.trigger_time = self._next_cron_time(
                            factory.cron, time.time()
                        )
                    except (KeyError, ValueError) as e:
                        factory.exhausted = True
                        # 任务永远不会被激活，标 FAILED 让 all_done() 收敛
                        task.status = TaskStatus.FAILED
                        task.last_error = f"invalid cron expr: {factory.cron!r}"
                        logger.error(
                            f"TaskQueue cron invalid: "
                            f"{factory.name_prefix} "
                            f"expr={factory.cron!r}: {e}"
                        )
                self.add(task)
                built += 1

            factory.initial_filled = True
            logger.info(
                f"TaskQueue initial fill: {factory.name_prefix} "
                f"built {built}/{target}"
            )

    def _initial_count(self, factory):
        # 一次性任务整个工厂只建 1 个实例
        if factory.one_shot:
            return 1
        if factory.requires_march_queue:
            if factory.count == 0:
                return factory.max_active
            return min(factory.max_active, factory.count)
        return 1

    # --------------------------------------------------------
    # 计数辅助
    # --------------------------------------------------------

    def _find_factory(self, task):
        key = getattr(task, "factory_key", None)
        if key is None:
            return None
        for f in self.factories:
            if f.key == key:
                return f
        return None

    def _count_in_progress(self, factory_key):
        """
        某工厂当前 IN_PROGRESS 的任务数。
        IN_PROGRESS = 军队已派出，占用一个军队队列。
        """
        return sum(
            1 for t in self.tasks
            if getattr(t, "factory_key", None) == factory_key
            and t.status == TaskStatus.IN_PROGRESS
        )

    # --------------------------------------------------------
    # 快照 / 终止
    # --------------------------------------------------------

    def get_snapshot(self):
        """
        返回队列当前状态快照，用于 UI / 日志展示。

        计数字段：
          - total            任务总数
          - scheduled        等待触发时间
          - pending          等待交互
          - running          正在交互
          - in_progress      等待游戏内完成
          - done             已完成
          - failed           失败
          - current          当前正在交互的任务名

        任务明细列表（空列表不放进快照，避免 UI 空行）：
          - scheduled_tasks   等待触发的任务，按 trigger_time 升序 = 触发顺序，
                              每项 "任务名 预计执行时刻"
          - pending_tasks     等待交互的任务名，按执行顺序（FIFO）
          - in_progress_tasks 已交互完成、等待游戏内确认的任务，
                              每项 "任务名 预计完成时刻"
          - failed_tasks      失败任务名

        DONE 不出明细列表：任务对象不会被移除，无限 count 下列表会无限增长。
        """
        now = time.time()
        scheduled = pending = running = in_progress = 0
        done = failed = 0

        scheduled_items = []
        pending_tasks = []
        in_progress_items = []
        failed_tasks = []

        for t in self.tasks:
            name = t.name
            if t.status == TaskStatus.SCHEDULED:
                scheduled += 1
                scheduled_items.append(
                    (t.trigger_time,
                     f"{name} {self._fmt_clock(t.trigger_time, now)}")
                )
            elif t.status == TaskStatus.PENDING:
                pending += 1
                pending_tasks.append(name)
            elif t.status == TaskStatus.RUNNING:
                running += 1
            elif t.status == TaskStatus.IN_PROGRESS:
                in_progress += 1
                if t.estimated_finish_time is not None:
                    in_progress_items.append(
                        (t.estimated_finish_time,
                         f"{name} {self._fmt_clock(t.estimated_finish_time, now)}")
                    )
                else:
                    in_progress_items.append((now, name))
            elif t.status == TaskStatus.DONE:
                done += 1
            elif t.status == TaskStatus.FAILED:
                failed += 1
                failed_tasks.append(name)

        # 插入序跨工厂时可能偏离触发序，这里显式按 trigger_time 排。
        scheduled_items.sort(key=lambda item: item[0])

        snapshot = {
            "total": len(self.tasks),
            "scheduled": scheduled,
            "pending": pending,
            "running": running,
            "in_progress": in_progress,
            "done": done,
            "failed": failed,
            "current": (
                self.current_interaction.name
                if self.current_interaction else "-"
            ),
        }
        if scheduled_items:
            snapshot["scheduled_tasks"] = [s for _, s in scheduled_items]
        if pending_tasks:
            snapshot["pending_tasks"] = pending_tasks
        if in_progress_items:
            snapshot["in_progress_tasks"] = [s for _, s in in_progress_items]
        if failed_tasks:
            snapshot["failed_tasks"] = failed_tasks
        return snapshot

    @staticmethod
    def _fmt_clock(ts, now):
        """
        时间戳 → 界面可读的预计时刻：
          - 今天   → "14:32"
          - 非今天 → "09-01 14:32"
        """
        lt = time.localtime(ts)
        if time.localtime(now).tm_yday == lt.tm_yday:
            return time.strftime("%H:%M:%S", lt)
        return time.strftime("%m-%d %H:%M:%S", lt)

    def all_done(self):
        """
        队列是否全部结束：
          - 没有任何任务且没有任何工厂 → 视为未运行，返回 False
          - 有任何任务未到 DONE / FAILED → False
          - 有任何工厂还能继续提交 → False
          - 否则 True

        失败策略下的收敛：
          - continue_after_failure=True 时，卡死工厂会在 tick 的
            stalled 扫描里补建下一个任务，can_submit_more 随 count
            用尽而变 False，这里自然收敛。
          - =False 时卡死工厂被标 exhausted，can_submit_more 直接
            返回 False。修复了旧实现中"存在失败任务时本方法永远
            不满足、run() 主循环死转"的问题。
          - one_shot / cron 工厂：submitted 只记已 create 的实例数
            （通常为 1），执行次数收敛看 finished_count——每终态 +1，
            finished_count >= count 时 can_submit_more=False。
        """
        if not self.tasks and not self.factories:
            return False

        for t in self.tasks:
            if t.status not in (TaskStatus.DONE, TaskStatus.FAILED):
                return False

        for f in self.factories:
            if f.can_submit_more():
                return False

        return True