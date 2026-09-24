import time
import uuid

from ok import Logger

from src.scheduler.task_status import (
    InteractionResult,
    TaskStatus,
)

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
    ):
        self.task_class = task_class
        self.count = count
        self.max_active = max_active
        self.requires_march_queue = requires_march_queue
        self.next_trigger_delay = next_trigger_delay
        self.kwargs = kwargs or {}
        self.name_prefix = name_prefix or task_class.__name__
        self.submitted = 0
        self.initial_filled = False      # ← 新增
        self.key = uuid.uuid4().hex[:8]

    def can_submit_more(self):
        """是否还能继续提交新任务（只看 count 上限）"""
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

    # --------------------------------------------------------
    # 生命周期
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
        if task.trigger_time <= now:
            task.status = TaskStatus.PENDING
        else:
            task.status = TaskStatus.SCHEDULED

        task.estimated_finish_time = None
        task.finished_at = None
        task.next_retry_time = 0.0
        task.last_error = None
        if not hasattr(task, "paused"):
            task.paused = False

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
        """
        if not self.started or self.paused:
            return

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
          - SUCCESS 时立即建立同类的下一个任务

        异常安全：任务内抛异常统一记 FAILED，不拖垮整个 tick。
        """
        self.current_interaction = task
        task.status = TaskStatus.RUNNING
        logger.info(f"TaskQueue interaction: {task.name}")

        try:
            result, wait_seconds = task.run_interaction()
        except Exception as e:
            logger.error(f"TaskQueue exception: {task.name}", e)
            task.status = TaskStatus.FAILED
            task.last_error = str(e)
            self.current_interaction = None
            return

        self.current_interaction = None

        if result == InteractionResult.SUCCESS:
            # 统一进 IN_PROGRESS，wait=0 的任务下一 tick 会转 DONE
            task.status = TaskStatus.IN_PROGRESS
            task.estimated_finish_time = time.time() + max(0.0, wait_seconds)

            # 立即建立同类的下一个任务
            self._create_follower(task)

            logger.info(
                f"TaskQueue wait: {task.name} in {wait_seconds:.1f}s"
            )
            return

        if result == InteractionResult.RETRY:
            task.status = TaskStatus.PENDING
            task.next_retry_time = time.time() + max(1.0, wait_seconds)
            logger.info(
                f"TaskQueue retry: {task.name} in {wait_seconds:.1f}s"
            )
            return

        task.status = TaskStatus.FAILED
        logger.info(f"TaskQueue failed: {task.name} {task.last_error}")

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
        - 占军队队列型：min(max_active, count)，count=0 时取 max_active
        - 非占用型   ：1
        """
        for factory in self.factories:
            if factory.initial_filled:
                continue

            target = self._initial_count(factory)
            built = 0
            for _ in range(target):
                if not factory.can_submit_more():
                    break
                task = factory.create(self.executor, self.app, self.scene)
                self.add(task)
                built += 1

            factory.initial_filled = True
            logger.info(
                f"TaskQueue initial fill: {factory.name_prefix} "
                f"built {built}/{target}"
            )
        
    def _initial_count(self, factory):
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
        返回队列当前状态快照，用于 UI / 日志展示：
          - total            任务总数
          - scheduled        等待触发时间
          - pending          等待交互
          - running          正在交互
          - in_progress      等待游戏内完成
          - done             已完成
          - failed           失败
          - current          当前正在交互的任务名
          - next_finish_in   最近的预计完成剩余时间（字符串）
          - next_trigger_in  最近的预计触发剩余时间（字符串）
        """
        now = time.time()
        scheduled = pending = running = in_progress = 0
        done = failed = 0
        next_finish_in = None
        next_trigger_in = None

        for t in self.tasks:
            if t.status == TaskStatus.SCHEDULED:
                scheduled += 1
                if t.trigger_time > now:
                    remain = t.trigger_time - now
                    if next_trigger_in is None or remain < next_trigger_in:
                        next_trigger_in = remain
            elif t.status == TaskStatus.PENDING:
                pending += 1
            elif t.status == TaskStatus.RUNNING:
                running += 1
            elif t.status == TaskStatus.IN_PROGRESS:
                in_progress += 1
                if t.estimated_finish_time is not None:
                    remain = max(0, t.estimated_finish_time - now)
                    if next_finish_in is None or remain < next_finish_in:
                        next_finish_in = remain
            elif t.status == TaskStatus.DONE:
                done += 1
            elif t.status == TaskStatus.FAILED:
                failed += 1

        return {
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
            "next_finish_in": (
                f"{next_finish_in:.0f}s"
                if next_finish_in is not None else "-"
            ),
            "next_trigger_in": (
                f"{next_trigger_in:.0f}s"
                if next_trigger_in is not None else "-"
            ),
        }

    def all_done(self):
        """
        队列是否全部结束：
          - 没有任何任务且没有任何工厂 → 视为未运行，返回 False
          - 有任何任务未到 DONE / FAILED → False
          - 有任何工厂还能继续提交 → False
          - 否则 True
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