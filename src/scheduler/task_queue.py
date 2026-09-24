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
    任务工厂：用于 TaskQueue 自动补充新任务。

    典型用法：
        factory = TaskFactory(
            task_class=HuntGiantBeastTask,
            count=5,             # 一共提交 5 个，0 表示不限次数
            max_active=3,        # 队列里同时存在的活跃任务上限
            kwargs={"giant_beast_level": 2},
            name_prefix="Hunt Giant Beast",
        )
        queue.add_factory(factory)

    说明：
      - count 是累计提交次数上限，达到后不再补新任务
      - max_active 是"活跃任务"（PENDING + RUNNING + IN_PROGRESS）的并发上限
    """

    def __init__(
        self,
        task_class,
        count=1,
        max_active=1,
        kwargs=None,
        name_prefix=None,
    ):
        self.task_class = task_class            # 要创建的任务类
        self.count = count                      # 提交总数上限，0 = 无限
        self.max_active = max_active            # 活跃任务上限
        self.kwargs = kwargs or {}              # 附加到任务实例上的属性
        self.name_prefix = name_prefix or task_class.__name__
        self.submitted = 0                      # 已经提交的数量

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
          4. 递增 submitted 并给任务一个可读名字
        """
        task = self.task_class(executor=executor, app=app)
        task.after_init(executor=executor, scene=scene)

        for k, v in self.kwargs.items():
            setattr(task, k, v)

        self.submitted += 1
        task.name = f"{self.name_prefix} #{self.submitted}"
        return task


class TaskQueue:
    """
    任务队列：纯逻辑对象，不继承 BaseTask。
    由外部调用 tick() 推进，自身不驱动。

    职责：
      - 维护任务列表（list[SGBaseTask]）
      - 通过 TaskFactory 自动补任务
      - 管理任务状态机：PENDING → RUNNING → IN_PROGRESS → DONE / FAILED
      - 保证同一时刻只有一个任务在 RUNNING（占屏幕交互）
      - 提供暂停 / 恢复 / 移除 / 单任务暂停等控制
      - 提供 get_snapshot() 给 UI 展示

    状态流转说明：
      PENDING
        ├── 到 next_retry_time 之后 → RUNNING
        └── 被 pause → 暂不调度
      RUNNING
        ├── 交互成功 → IN_PROGRESS（等待 estimated_finish_time）
        ├── 交互需重试 → PENDING（next_retry_time 后重试）
        └── 交互失败 → FAILED
      IN_PROGRESS
        ├── 到时间且 check_completed() 通过 → DONE
        └── 到时间但 check_completed() 失败 → PENDING（延后 5s 重试）
    """

    def __init__(self, executor, app, scene):
        self.executor = executor        # OK-Script Executor
        self.app = app                  # 应用实例
        self.scene = scene              # 当前场景对象

        self.tasks = []                 # 所有任务
        self.factories = []             # 所有自动补任务的工厂
        self.current_interaction = None # 当前正在交互的任务

        self.started = False            # 队列是否已 start
        self.paused = False             # 队列是否整体暂停

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------

    def start(self):
        """启动队列：开始接受 tick 调度"""
        self.started = True
        self.paused = False
        logger.info("TaskQueue started")

    def stop(self):
        """停止队列：tick 不再执行任何动作"""
        self.started = False
        logger.info("TaskQueue stopped")

    def pause(self):
        """队列级暂停：tick 直接返回，不推进任何任务"""
        self.paused = True
        logger.info("TaskQueue paused")

    def resume(self):
        """恢复队列运行"""
        self.paused = False
        logger.info("TaskQueue resumed")

    # --------------------------------------------------------
    # 添加 / 移除
    # --------------------------------------------------------

    def add(self, task):
        """
        把一个任务加到队列尾部，并重置它的队列相关字段。

        - task_id：如果没有就生成一个短 uuid
        - status：统一置为 PENDING
        - 其它调度字段统一重置，避免旧状态残留
        """
        if getattr(task, "task_id", None) is None:
            task.task_id = uuid.uuid4().hex[:8]

        task.status = TaskStatus.PENDING
        task.estimated_finish_time = None
        task.finished_at = None
        task.next_retry_time = 0.0
        task.last_error = None
        # task.paused = False

        self.tasks.append(task)
        logger.info(f"TaskQueue add: {task.name} ({task.task_id})")
        return task.task_id

    def add_factory(self, factory: TaskFactory):
        """
        注册一个任务工厂：tick 时会自动补任务。
        工厂只在 _replenish() 中被消费。
        """
        self.factories.append(factory)
        logger.info(
            f"TaskQueue add factory: {factory.name_prefix} x {factory.count}"
        )

    def remove_task(self, task_id):
        """
        从队列里移除指定任务。
        如果移除的正是当前正在交互的任务，则一并清空 current_interaction。
        """
        for i, t in enumerate(self.tasks):
            if t.task_id == task_id:
                self.tasks.pop(i)
                logger.info(f"TaskQueue remove: {t.name} ({task_id})")
                if self.current_interaction is t:
                    self.current_interaction = None
                return True
        return False

    def clear(self):
        """清空整个队列（任务 + 工厂 + 当前交互）"""
        self.tasks.clear()
        self.factories.clear()
        self.current_interaction = None
        logger.info("TaskQueue cleared")

    # --------------------------------------------------------
    # 单任务暂停 / 恢复
    # --------------------------------------------------------

    def pause_task(self, task_id):
        """
        暂停单个任务：只是把它标成 paused，调度时会被跳过。
        不影响队列整体运行，也不影响其它任务。
        """
        for t in self.tasks:
            if t.task_id == task_id:
                t.paused = True
                logger.info(f"TaskQueue pause_task: {t.name}")
                return True
        return False

    def resume_task(self, task_id):
        """恢复单个任务：paused 置回 False，下次 tick 可能被调度"""
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
          1. 检查 IN_PROGRESS 是否到期（转 DONE 或重排 PENDING）
          2. 如果已有任务在交互，直接返回，保证同屏互斥
          3. 找一个可执行 PENDING 任务做交互
          4. 自动补新任务
        """
        if not self.started or self.paused:
            return

        now = time.time()

        # 1. 处理到期任务
        self._check_in_progress(now)

        # 2. 屏幕互斥：同一时刻只允许一个交互
        if self.current_interaction is not None:
            return

        # 3. 挑下一个待执行任务
        candidate = self._pick_next(now)
        if candidate is not None:
            self._do_interaction(candidate)

        # 4. 自动补任务
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
              - 不通过 → PENDING，并把 next_retry_time 延后 5 秒
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

    def _pick_next(self, now):
        """
        挑一个可执行的任务：
          - 未 paused
          - status == PENDING
          - 已过 next_retry_time
        队列顺序即优先级（先来先执行）。
        """
        for task in self.tasks:
            if task.paused:
                continue
            if task.status != TaskStatus.PENDING:
                continue
            if task.next_retry_time > now:
                continue
            return task
        return None

    def _do_interaction(self, task):
        """
        执行一次交互：
          - 把任务切成 RUNNING
          - 调 task._run_once() 拿到 (InteractionResult, wait_seconds)
          - 根据结果更新任务状态

        异常安全：
          - 任务内部抛异常统一记为 FAILED，不会拖垮整个 tick

        NOTE：
          这里调用的是 task._run_once()。为了和已有工程代码保持一致。
          如果你后续把任务方法名改成 run_interaction()，请同步修改这里。
        """
        self.current_interaction = task
        task.status = TaskStatus.RUNNING
        logger.info(f"TaskQueue interaction: {task.name}")

        try:
            result, wait_seconds = task._run_once()
        except Exception as e:
            logger.error(f"TaskQueue exception: {task.name}", e)
            task.status = TaskStatus.FAILED
            task.last_error = str(e)
            self.current_interaction = None
            return

        self.current_interaction = None

        # 交互成功 → 进入等待阶段
        if result == InteractionResult.SUCCESS:
            task.status = TaskStatus.IN_PROGRESS
            task.estimated_finish_time = time.time() + max(0.0, wait_seconds)
            logger.info(
                f"TaskQueue wait: {task.name} in {wait_seconds:.1f}s"
            )
            return

        # 交互暂时不可执行 → 回 PENDING，过一会重试
        if result == InteractionResult.RETRY:
            task.status = TaskStatus.PENDING
            task.next_retry_time = time.time() + max(1.0, wait_seconds)
            logger.info(
                f"TaskQueue retry: {task.name} in {wait_seconds:.1f}s"
            )
            return

        # 其它情况视为失败
        task.status = TaskStatus.FAILED
        logger.info(f"TaskQueue failed: {task.name} {task.last_error}")

    def _replenish(self):
        """
        用工厂自动补任务：
          - 每个工厂独立判断能不能补
          - 统计"活跃任务"数量（PENDING + RUNNING + IN_PROGRESS）
          - 活跃数达到 factory.max_active 就不补
          - 否则调用 factory.create 创建一个，并 add 进队列

        注意：
          这里是全局统计活跃数，而不是按工厂分别统计。
          当前只有一个工厂的场景下没问题；
          如果以后要多工厂并发，建议改成按工厂分组统计。
        """
        for factory in self.factories:
            if not factory.can_submit_more():
                continue

            active = sum(
                1 for t in self.tasks
                if t.status in (
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                    TaskStatus.IN_PROGRESS,
                )
            )
            if active >= factory.max_active:
                continue

            task = factory.create(self.executor, self.app, self.scene)
            self.add(task)

    # --------------------------------------------------------
    # 快照 / 终止
    # --------------------------------------------------------

    def get_snapshot(self):
        """
        返回队列当前状态快照，用于 UI / 日志展示：
          - total        任务总数
          - pending      等待交互
          - running      正在交互
          - in_progress  等待游戏内完成
          - done         已完成
          - failed       失败
          - current      当前正在交互的任务名
          - next_finish_in  最近的预计完成剩余时间（字符串）
        """
        now = time.time()
        pending = running = in_progress = done = failed = 0
        next_finish_in = None

        for t in self.tasks:
            if t.status == TaskStatus.PENDING:
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