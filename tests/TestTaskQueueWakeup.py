# TaskQueue 事件驱动改造单元测试（B1.5）
#
# 覆盖：
#   1. next_wakeup_time() 全分支（SCHEDULED / PENDING 未来与已到 /
#      IN_PROGRESS 有无 finish / RUNNING / paused / DONE / FAILED / 空队列）
#   2. wake_callback 触发计数（add / add_factory / _do_interaction 各路径）
#   3. _count_in_progress 增量计数 vs 暴力全量扫描比对（随机流转，固定种子）
#   4. stalled 扫描 user_id 过滤 bug 修复（user_id=0 工厂补建发生）
#   5. version 门控（状态无变化的 tick 不递增，有变化递增）
#
# 风格对齐 TestTaskQueueScheduling.py：FakeTask 直测纯逻辑，不依赖游戏画面。
# tick 语义备忘：tick 内顺序 = check_in_progress → pick → interact →
# replenish → stalled。
import time
import unittest
from unittest import mock

from src.scheduler.task_queue import TaskFactory, TaskQueue
from src.scheduler.task_status import InteractionResult, TaskStatus


class FakeTask:
    """最小任务：run_interaction 返回构造时注入的 (结果, wait)。"""

    def __init__(self, take, name="FakeTask", **kwargs):
        self.take = take
        self.name = name
        self.checks = 0
        self.check_ok = True

    def after_init(self, executor=None, scene=None):
        pass

    def run_interaction(self):
        return self.take()

    def check_completed(self):
        self.checks += 1
        return self.check_ok


def make_queue(results, count=3, **factory_kwargs):
    """
    构造一个挂了单工厂的队列（与 TestTaskQueueScheduling 同模式）。
    结果序列全局消耗，耗尽后重复最后一项。
    """
    seq = {"i": 0}

    def take():
        i = seq["i"]
        seq["i"] += 1
        return results[i] if i < len(results) else results[-1]

    def make(*args, **kwargs):
        return FakeTask(take, name=f"Fake #{seq['i']}")

    factory = TaskFactory(
        task_class=make, count=count, name_prefix="Fake", **factory_kwargs
    )
    queue = TaskQueue(executor=mock.Mock(), app=mock.Mock(), scene=mock.Mock())
    queue.add_factory(factory)
    return queue, factory, seq


class TestNextWakeupTime(unittest.TestCase):
    """next_wakeup_time 全分支。"""

    def _queue(self):
        return TaskQueue(executor=mock.Mock(), app=mock.Mock(),
                         scene=mock.Mock())

    def _bare_task(self):
        """无工厂裸任务：直接 add，不经过 factory.create。"""
        return FakeTask(lambda: (InteractionResult.SUCCESS, 0))

    def test_empty_queue_returns_none(self):
        self.assertIsNone(self._queue().next_wakeup_time())

    def test_scheduled_uses_trigger_time(self):
        q = self._queue()
        t = self._bare_task()
        t.trigger_time = time.time() + 42
        q.add(t)
        wake = q.next_wakeup_time()
        self.assertIsNotNone(wake)
        self.assertAlmostEqual(wake, t.trigger_time, delta=0.01)

    def test_pending_future_retry_is_candidate(self):
        q = self._queue()
        t = self._bare_task()
        t.status = TaskStatus.PENDING
        t.next_retry_time = time.time() + 7
        q.add(t)
        # add() 会按 trigger_time=0 重置为 PENDING 且 next_retry_time=0，
        # 因此先 add 再改 retry，模拟 RETRY 后的等待态。
        t.next_retry_time = time.time() + 7
        wake = q.next_wakeup_time()
        self.assertAlmostEqual(wake, t.next_retry_time, delta=0.01)

    def test_pending_expired_retry_wakes_now(self):
        q = self._queue()
        t = self._bare_task()
        q.add(t)  # trigger=0 → PENDING, next_retry_time=0（已过）
        wake = q.next_wakeup_time()
        self.assertIsNotNone(wake)
        # 已过 retry → 候选是 now，等待时长≈0
        self.assertLessEqual(wake - time.time(), 0.05)

    def test_in_progress_with_finish_time_is_candidate(self):
        q = self._queue()
        t = self._bare_task()
        q.add(t)
        t.status = TaskStatus.IN_PROGRESS
        t.estimated_finish_time = time.time() + 13
        wake = q.next_wakeup_time()
        self.assertAlmostEqual(wake, t.estimated_finish_time, delta=0.01)

    def test_in_progress_without_finish_time_is_skipped(self):
        q = self._queue()
        t = self._bare_task()
        q.add(t)
        t.status = TaskStatus.IN_PROGRESS
        t.estimated_finish_time = None
        self.assertIsNone(q.next_wakeup_time())

    def test_running_is_skipped(self):
        q = self._queue()
        t = self._bare_task()
        q.add(t)
        t.status = TaskStatus.RUNNING
        self.assertIsNone(q.next_wakeup_time())

    def test_paused_is_skipped(self):
        q = self._queue()
        t = self._bare_task()
        t.trigger_time = time.time() + 5
        q.add(t)
        q.pause_task(t.task_id)
        self.assertIsNone(q.next_wakeup_time())

    def test_done_and_failed_are_skipped(self):
        q = self._queue()
        t1 = self._bare_task()
        q.add(t1)
        t1.status = TaskStatus.DONE
        t2 = self._bare_task()
        q.add(t2)
        t2.status = TaskStatus.FAILED
        self.assertIsNone(q.next_wakeup_time())

    def test_returns_minimum_candidate(self):
        q = self._queue()
        early = self._bare_task()
        early.trigger_time = time.time() + 3
        q.add(early)
        late = self._bare_task()
        late.trigger_time = time.time() + 900
        q.add(late)
        wake = q.next_wakeup_time()
        self.assertAlmostEqual(wake, early.trigger_time, delta=0.01)


class TestWakeCallback(unittest.TestCase):
    """wake_callback 触发计数：add / add_factory / _do_interaction。"""

    def _queue_with_counter(self, results, count=3, **fk):
        queue, factory, seq = make_queue(results, count=count, **fk)
        calls = []
        queue.wake_callback = lambda: calls.append(1)
        return queue, factory, seq, calls

    def test_add_triggers_callback(self):
        queue, factory, seq, calls = self._queue_with_counter(
            [(InteractionResult.SUCCESS, 0)]
        )
        queue.factory_clear = None
        # 裸 add（工厂已 initial_filled 前置？未 tick 过，replenish 未跑）
        t = FakeTask(lambda: (InteractionResult.SUCCESS, 0))
        n = len(calls)
        queue.add(t)
        self.assertEqual(len(calls), n + 1)

    def test_add_factory_triggers_callback(self):
        queue, factory, seq, calls = self._queue_with_counter(
            [(InteractionResult.SUCCESS, 0)]
        )
        n = len(calls)
        f2 = TaskFactory(task_class=lambda **kw: FakeTask(lambda: None),
                         count=1, name_prefix="F2")
        queue.add_factory(f2)
        self.assertEqual(len(calls), n + 1)

    def test_do_interaction_success_triggers_callback(self):
        # SUCCESS 落点：PENDING→RUNNING→IN_PROGRESS 多次 _bump
        queue, factory, seq, calls = self._queue_with_counter(
            [(InteractionResult.SUCCESS, 5)]
        )
        queue.start()
        queue.tick()  # replenish 建 1 个 + pick 执行
        self.assertGreater(len(calls), 0)

    def test_do_interaction_retry_triggers_callback(self):
        queue, factory, seq, calls = self._queue_with_counter(
            [(InteractionResult.RETRY, 2)]
        )
        queue.start()
        queue.tick()
        self.assertEqual(queue.tasks[0].status, TaskStatus.PENDING)
        self.assertGreater(len(calls), 0)

    def test_callback_exception_does_not_break_tick(self):
        """callback 崩溃吞掉记 debug，绝不能拖垮状态流转。"""
        queue, factory, seq, calls = self._queue_with_counter(
            [(InteractionResult.SUCCESS, 0)]
        )

        def boom():
            raise RuntimeError("callback exploded")

        queue.wake_callback = boom
        queue.start()
        try:
            queue.tick()  # t1：建任务（PENDING）
            queue.tick()  # t2：pick → IN_PROGRESS
            queue.tick()  # t3：DONE
        except RuntimeError:
            self.fail("callback exception leaked into tick")
        self.assertEqual(queue.tasks[0].status, TaskStatus.DONE)


class TestCountInProgress(unittest.TestCase):
    """
    _count_in_progress 增量计数 vs 暴力比对。
    100 任务 × 200 次随机流转（固定种子），每次流转后计数器
    必须与 sum 全量扫描一致。
    """

    def test_incremental_matches_bruteforce(self):
        import random
        rng = random.Random(20261030)
        queue = TaskQueue(executor=mock.Mock(), app=mock.Mock(),
                          scene=mock.Mock())

        factory = TaskFactory(
            task_class=lambda **kw: FakeTask(lambda: None),
            count=0, name_prefix="Rng",
        )
        queue.add_factory(factory)

        tasks = []
        statuses = [TaskStatus.PENDING, TaskStatus.IN_PROGRESS,
                    TaskStatus.DONE, TaskStatus.SCHEDULED]
        for i in range(100):
            t = FakeTask(lambda: None, name=f"R #{i}")
            t.factory_key = factory.key
            queue.add(t)
            # add 初始为 PENDING；归入参与流转的状态池
            tasks.append(t)

        for step in range(200):
            t = rng.choice(tasks)
            new_status = rng.choice(statuses)
            queue._set_status(t, new_status)
            expected = sum(
                1 for x in queue.tasks
                if getattr(x, "factory_key", None) == factory.key
                and x.status == TaskStatus.IN_PROGRESS
            )
            self.assertEqual(
                queue._count_in_progress(factory.key),
                expected,
                msg=f"step {step}: counter drifted from brute force",
            )

        # 流转结束时无 IN_PROGRESS 残留（最后一次流转随机，这里只验证
        # 计数器与扫描一致而非具体值）
        brute = sum(
            1 for x in queue.tasks
            if getattr(x, "factory_key", None) == factory.key
            and x.status == TaskStatus.IN_PROGRESS
        )
        self.assertEqual(queue._count_in_progress(factory.key), brute)

    def test_remove_task_decrements_counter(self):
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 10)]
        )
        queue.start()
        # tick 时序备忘：t1 replenish 建任务（PENDING），t2 pick →
        # IN_PROGRESS。需要两次 tick 才进 IN_PROGRESS。
        queue.tick()
        queue.tick()
        key = factory.key
        self.assertEqual(queue._count_in_progress(key), 1)
        task_id = queue.tasks[0].task_id
        queue.remove_task(task_id)
        self.assertEqual(queue._count_in_progress(key), 0)

    def test_clear_decrements_counter(self):
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 10)]
        )
        queue.start()
        queue.tick()
        queue.tick()
        self.assertEqual(queue._count_in_progress(factory.key), 1)
        queue.clear()
        self.assertEqual(queue._count_in_progress(factory.key), 0)


class TestStalledFactoryUserFilterFix(unittest.TestCase):
    """
    user_id 过滤 bug 修复：user_id=0 工厂全 FAILED +
    continue_after_failure=True + 本 tick 未 pick 到任何任务
    → 补建必须发生（旧实现因 user_id=None 过滤被跳过）。
    """

    def test_zero_user_factory_rebuilds_without_pick(self):
        seq = {"i": 0}

        def make(*args, **kwargs):
            def take():
                return (InteractionResult.FAILED, 0)
            return FakeTask(take, name=f"U0 #{seq['i']}")

        factory = TaskFactory(
            task_class=make, count=3, name_prefix="U0",
            user_id=0,  # 旧实现里 user_id=None 的 tick 永远跳过它
        )
        queue = TaskQueue(executor=mock.Mock(), app=mock.Mock(),
                          scene=mock.Mock())
        queue.continue_after_failure = True
        queue.add_factory(factory)
        queue.start()

        # 全部跑失败：t1 建任务（replenish 建非占用型 1 个），
        # t2 pick 执行 → FAILED；无 PENDING 可 pick → 未 pick 到任何任务
        for _ in range(10):
            queue.tick()
            if queue.all_done():
                break

        # continue_after_failure=True → 每次卡死都补建，直到 count 用完
        self.assertEqual(factory.submitted, 3)
        self.assertTrue(queue.all_done())


class TestVersionGating(unittest.TestCase):
    """version 门控：状态无变化的 tick 不递增；有变化递增。"""

    def test_idle_tick_does_not_bump(self):
        # 空队列：无任何状态流转
        queue = TaskQueue(executor=mock.Mock(), app=mock.Mock(),
                          scene=mock.Mock())
        queue.start()  # start 自身 bump 一次
        v = queue.version
        queue.tick()
        queue.tick()
        self.assertEqual(queue.version, v)

    def test_status_change_bumps(self):
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 10)]
        )
        v0 = queue.version
        queue.start()
        v1 = queue.version
        self.assertGreater(v1, v0)

        # t1：replenish 建任务（PENDING）→ 有变化
        queue.tick()
        v2 = queue.version
        self.assertGreater(v2, v1)

        # t2：pick → RUNNING → IN_PROGRESS → 有变化
        queue.tick()
        v3 = queue.version
        self.assertGreater(v3, v2)
        # t3：任务 IN_PROGRESS 且无 finish 时间（等外部事件），
        # _check_in_progress 跳过、pick 不到、stalled 不算卡死
        # → 本 tick 无任何状态流转 → version 不递增
        queue.tasks[0].estimated_finish_time = None
        queue.tick()
        self.assertEqual(queue.version, v3)
        self.assertEqual(queue.tasks[0].status, TaskStatus.IN_PROGRESS)

    def test_terminal_tick_bumps_once_per_transition(self):
        # count=1：SUCCESS 时不建 follower（名额已用尽），
        # t3 的唯一流转就是 IN_PROGRESS → DONE，恰好一次 _bump。
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 0)], count=1
        )
        queue.start()
        queue.tick()   # t1：建任务（PENDING）
        queue.tick()   # t2：pick → IN_PROGRESS
        v = queue.version
        queue.tick()   # t3：IN_PROGRESS → DONE（恰好一次流转）
        self.assertEqual(queue.version, v + 1)
        self.assertEqual(queue.tasks[0].status, TaskStatus.DONE)



if __name__ == "__main__":
    unittest.main()
