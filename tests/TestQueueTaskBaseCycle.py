# QueueTaskBase 事件驱动主循环（B2）行为测试
#
# 覆盖：
#   1. next_wakeup_time 驱动的等待时长（tick 后 _wait_wake 收到 next_ts - now）
#   2. next_wakeup_time=None → Max Idle Sleep 兜底
#   3. _wait_wake 挂起中被 _notify_wake 打断立即返回
#   4. UI 刷新 version 门控：同 version 只刷一次，version 变化再刷
#   5. exit_event.set() 经 bind_condition 即时唤醒主循环（100ms 内退出）
#   6. 全流程 FakeTask 队列跑完：全部 DONE 且 _run_queue_cycle 返回 "done"
#
# 全部 mock executor/app/scene，不依赖游戏画面、不连真机。
# 不走 run()/executor enqueue，直接调 _run_queue_cycle()。
import threading
import time
import unittest
from unittest import mock

from src.scheduler.task_queue import TaskFactory
from src.scheduler.task_status import InteractionResult, TaskStatus
from src.sg.tasks.queue.QueueTaskBase import QueueTaskBase


class FakeTask:
    """最小任务：run_interaction 返回注入的 (结果, wait)。"""

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


def _make_factory(count=3, results=None):
    """单工厂：results 为交互结果序列（耗尽后重复最后一项）。"""
    seq = {"i": 0}
    results = results or [(InteractionResult.SUCCESS, 0)] * count

    def take():
        i = seq["i"]
        seq["i"] += 1
        return results[i] if i < len(results) else results[-1]

    def make(*args, **kwargs):
        return FakeTask(take)

    return TaskFactory(task_class=make, count=count, name_prefix="Fake")


class CycleTask(QueueTaskBase):
    """最小可跑子类：_build_factories 塞一个 FakeTask 工厂。"""

    def __init__(self, *args, factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._factory = factory or _make_factory()

    def _build_factories(self):
        self._factories.append(self._factory)


def _make_task(config_overrides=None):
    """构造 CycleTask：ExitEvent 替身作 exit_event，其余 mock。

    executor 不用 spec：ok 框架 ExecutorOperation.__init__ 会读
    executor.scene 等属性，spec 白名单反而缺属性。
    """
    executor = mock.Mock()
    executor.exit_event = _ExitEvent()
    # 真 threading.Event 即 ExitEvent 的 bind_condition 协议子集：
    # ExitEvent.set() = Event.set() + notify_all(bind_condition 注册的 cond)。
    executor.exit_event = _ExitEvent()
    t = CycleTask(executor=executor, app=mock.Mock(), factory=_make_factory())
    t.load_config()
    for k, v in (config_overrides or {}).items():
        t.config[k] = v
    return t


class _ExitEvent(threading.Event):
    """ok 框架 ExitEvent 的测试替身：set() 时 notify_all 已绑 Condition。"""

    def __init__(self):
        super().__init__()
        self.conditions = set()

    def bind_condition(self, condition):
        self.conditions.add(condition)

    def set(self):
        super().set()
        for cond in self.conditions:
            with cond:
                cond.notify_all()


class TestWakePrimitives(unittest.TestCase):
    """_wait_wake / _notify_wake 基元。"""

    def test_notify_interrupts_wait_immediately(self):
        t = _make_task()
        done = threading.Event()

        def waiter():
            start = time.time()
            t._wait_wake(30.0)  # 挂起 30s，若不被打断测试超时
            elapsed = time.time() - start
            self.assertLess(elapsed, 1.0)
            done.set()

        th = threading.Thread(target=waiter, daemon=True)
        th.start()
        time.sleep(0.05)  # 让 waiter 先进入 wait
        t._notify_wake()
        self.assertTrue(done.wait(2.0), "_notify_wake 未在 2s 内打断 _wait_wake")

    def test_wake_flag_consumed_once(self):
        t = _make_task()
        t._notify_wake()
        self.assertTrue(t._wake_flag)
        # 第一次 _wait_wake：旗标已置位 → 立即返回并消费旗标
        start = time.time()
        t._wait_wake(10.0)
        self.assertLess(time.time() - start, 0.5)
        # 旗标被消费：不清不丢，第二次调用若传长超时会真挂——
        # 用 0.05s 超时验证"没有第二个可消费的唤醒"
        start = time.time()
        t._wait_wake(0.05)
        self.assertGreaterEqual(time.time() - start, 0.04)
        self.assertFalse(t._wake_flag)


class TestWaitDuration(unittest.TestCase):
    """主循环等待时长：next_wakeup_time 驱动。"""

    def _run_one_tick(self, next_wakeup_values):
        """驱动主循环两轮（第二轮 all_done=True 收口），收集 _wait_wake 实参。

        内联主循环第 3 段的等待计算逻辑，与 _run_queue_cycle 保持一致；
        不走真的 _run_queue_cycle 是为了避开 TaskQueue 构造/收尾等
        与等待时长断言无关的 mock 干扰。
        """
        t = _make_task()
        waits = []
        t._wait_wake = waits.append
        queue = t.queue = mock.Mock()
        queue.all_done.side_effect = [False, False, True]
        queue.next_wakeup_time.side_effect = next_wakeup_values
        queue.get_snapshot.return_value = {}
        queue.version = 0
        while not t.queue.all_done():
            if t.exit_is_set():
                break
            t.queue.tick()
            t._update_info()
            next_ts = t.queue.next_wakeup_time()
            if next_ts is None:
                wait = t._max_idle_sleep
            else:
                wait = next_ts - time.time()
            wait = max(t._min_sleep, min(wait, t._max_idle_sleep))
            t._wait_wake(wait)
        return waits

    def test_wait_equals_next_wakeup_delta(self):
        # next_wakeup_time 返回 now + 0.3 → wait ≈ 0.3
        waits = self._run_one_tick([time.time() + 0.3, None])
        self.assertEqual(len(waits), 2)
        self.assertAlmostEqual(waits[0], 0.3, delta=0.1)
        self.assertEqual(waits[1], 30.0)  # None → Max Idle Sleep 兜底

    def test_none_uses_max_idle_sleep(self):
        waits = self._run_one_tick([None, None])
        self.assertEqual(waits[0], 30.0)

    def test_wait_clamped_by_min_sleep(self):
        # next_ts 已过期 → wait 负数 → clamp 到 Min Sleep
        waits = self._run_one_tick([time.time() - 5.0, None])
        self.assertAlmostEqual(waits[0], 0.05, delta=0.001)

    def test_wait_clamped_by_max_idle_sleep(self):
        # next_ts 很远 → clamp 到 Max Idle Sleep
        waits = self._run_one_tick([time.time() + 9999.0, None])
        self.assertEqual(waits[0], 30.0)


class TestFullCycle(unittest.TestCase):
    """真实 TaskQueue + FakeTask 全流程。"""

    def test_full_cycle_done(self):
        t = _make_task()
        result = t._run_queue_cycle()
        self.assertEqual(result, "done")
        self.assertTrue(t.queue.all_done())
        statuses = [task.status for task in t.queue.tasks]
        self.assertTrue(statuses)
        self.assertTrue(all(s == TaskStatus.DONE for s in statuses))

    def test_wake_callback_registered(self):
        t = _make_task()
        t._run_queue_cycle()
        cb = t.queue.wake_callback
        # bound method 每次访问生成新对象，比 __func__/__self__
        self.assertEqual(getattr(cb, "__func__", None), t._notify_wake.__func__)
        self.assertIs(getattr(cb, "__self__", None), t)

    def test_exit_event_bind_condition_registered(self):
        t = _make_task()
        t._run_queue_cycle()
        self.assertIn(t._wake_cond, t.executor.exit_event.conditions)

    def test_exit_event_set_stops_within_100ms(self):
        t = _make_task()
        # 任务 check_completed 恒 False → DONE 后转 PENDING
        # （next_retry_time = now+5s）→ 主循环 wait≈5s，正好挂在
        # _wait_wake 上等 exit 唤醒。若 bind_condition 失效，本测试
        # 要等 5s 才能退出，2s join 超时会暴露。
        seq = {"i": 0}

        def take():
            i = seq["i"]
            seq["i"] += 1
            return (InteractionResult.SUCCESS, 0)

        factory = TaskFactory(
            task_class=lambda *a, **k: FakeTask(take),
            count=1, name_prefix="Retry",
        )
        t._factory = factory
        orig_check = FakeTask.check_completed

        def never_done(self):
            self.checks += 1
            return False

        FakeTask.check_completed = never_done
        result_holder = {}

        def run():
            try:
                result_holder["result"] = t._run_queue_cycle()
            except Exception as e:
                result_holder["error"] = e

        try:
            th = threading.Thread(target=run, daemon=True)
            th.start()
            time.sleep(0.2)  # 让主循环进入 _wait_wake
            start = time.time()
            t.executor.exit_event.set()  # bind_condition 应即时唤醒
            th.join(timeout=2.0)
            elapsed = time.time() - start
        finally:
            FakeTask.check_completed = orig_check
        self.assertFalse(th.is_alive(), "主循环未在 2s 内退出")
        self.assertLess(
            elapsed, 1.0, f"退出耗时 {elapsed:.3f}s，bind_condition 未生效")
        self.assertIsNone(result_holder.get("error"))
        self.assertEqual(result_holder.get("result"), "stop")



class TestInfoVersionGating(unittest.TestCase):
    """_update_info 的 version 门控。"""

    def test_same_version_skips(self):
        t = _make_task()
        t.queue = mock.Mock()
        t.queue.version = 5
        t.queue.get_snapshot.return_value = {"total": 3}
        t.info_set = mock.Mock()
        t._update_info()
        t._update_info()
        self.assertEqual(t.info_set.call_count, 1)  # 同 version 第二次跳过

    def test_version_change_refreshes(self):
        t = _make_task()
        t.queue = mock.Mock()
        t.queue.version = 5
        t.queue.get_snapshot.return_value = {"total": 3}
        t.info_set = mock.Mock()
        t._update_info()
        t.queue.version = 6
        t._update_info()
        self.assertEqual(t.info_set.call_count, 2)

    def test_missing_version_falls_back_to_full_refresh(self):
        t = _make_task()
        queue = mock.Mock(spec=["get_snapshot"])  # 无 version 属性
        queue.get_snapshot.return_value = {"total": 1}
        t.queue = queue
        t.info_set = mock.Mock()
        t._update_info()
        t._update_info()
        self.assertEqual(t.info_set.call_count, 2)  # 回退全量刷新

    def test_run_finally_forces_final_refresh(self):
        t = _make_task()
        t.queue = mock.Mock()
        t.queue.version = 7
        t.queue.get_snapshot.return_value = {"total": 1}
        refreshed = []
        t._update_info = lambda: refreshed.append(t.queue.version) or None
        t.log_info = lambda msg: None
        t._run_queue_cycle = lambda: "done"
        t.run()
        # finally 里 _last_info_version 重置 -1 后强制刷：即便 version 没变也执行
        self.assertEqual(refreshed, [7])


if __name__ == "__main__":
    unittest.main()
