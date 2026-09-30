# TaskQueue 调度行为回归测试
#
# 覆盖三项队列层变更（对应 automation-framework-arch 架构变更记录）：
#   1. 失败策略 continue_after_failure：
#      True  → 卡死工厂（全部任务终态但 count 未用完）补建下一个
#      False → 卡死工厂标 exhausted，all_done() 收敛（旧实现死循环）
#   2. 一次性任务 one_shot：单实例自重排 Count 次，不建 follower
#   3. 定时任务 cron：trigger_time 由 croniter 计算，终态后排下一次
#
# 全部使用假任务类直测 TaskQueue 纯调度逻辑，不依赖游戏画面。
#
# tick 语义备忘（来自实现）：
#   tick 内顺序 = check_in_progress → pick → interact → replenish → stalled。
#   因此 wait=0 的 SUCCESS 在本 tick 进 IN_PROGRESS，下个 tick 转 DONE，
#   且 DONE 后的重排（follower/one_shot/cron）可能在本 tick 就被 pick。
import time
import unittest
from unittest import mock

from src.scheduler.task_queue import TaskFactory, TaskQueue
from src.scheduler.task_status import InteractionResult, TaskStatus


class FakeTask:
    """最小任务：run_interaction 返回 make_queue 注入的 (结果, wait)。"""

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
    构造一个挂了单工厂的队列。
    结果序列是全局的：每次 run_interaction 按执行次序消耗一项，
    耗尽后重复最后一项。这样能精确控制"第 N 次交互"的结果，
    与第几个任务实例无关（follower / 自重排都适用）。
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


class TestContinueAfterFailure(unittest.TestCase):
    """场景：count=3, max_active=1，#1 成功 #2 失败（用户报告的原始 bug）。"""

    def _make(self):
        queue, factory, seq = make_queue(
            [(InteractionResult.SUCCESS, 0), (InteractionResult.FAILED, 0)],
            count=3,
            max_active=1,
        )
        return queue, factory

    def test_stop_on_failure_marks_exhausted_and_all_done(self):
        queue, factory = self._make()
        queue.continue_after_failure = False
        queue.start()

        # 真实时序：t1 建实例 → PENDING；t2 pick → SUCCESS → IN_PROGRESS；
        # t3 #1 DONE；同 tick follower #2 被建出并 pick → FAILED；
        #         stalled 扫描（同 tick 末尾）发现全终态 → exhausted
        queue.tick()
        self.assertEqual(queue.tasks[0].status, TaskStatus.PENDING)
        queue.tick()
        self.assertEqual(queue.tasks[0].status, TaskStatus.IN_PROGRESS)
        queue.tick()
        self.assertEqual(queue.tasks[0].status, TaskStatus.DONE)
        self.assertEqual(queue.tasks[1].status, TaskStatus.FAILED)
        self.assertEqual(factory.submitted, 2)
        self.assertTrue(factory.exhausted)
        self.assertTrue(queue.all_done())  # 旧实现此处永不收敛

    def test_continue_on_failure_rebuilds_until_count_exhausted(self):
        queue, factory = self._make()
        queue.continue_after_failure = True
        queue.start()

        # tick#1：#1 SUCCESS → IN_PROGRESS
        # 真实时序：t1 建 → PENDING；t2 pick → IN_PROGRESS；
        # t3 #1 DONE → follower #2 建立 → pick #2 → FAILED；
        #         tick 末尾 stalled 扫描补建 #3
        queue.tick()
        queue.tick()
        queue.tick()
        self.assertEqual(factory.submitted, 3)

        # t4：pick #3 → FAILED；count 用完，不再补建
        queue.tick()
        self.assertEqual(factory.submitted, 3)
        self.assertFalse(factory.exhausted)
        self.assertTrue(queue.all_done())


class TestOneShot(unittest.TestCase):
    """一次性任务：单实例循环执行 count 次。"""

    def test_one_shot_runs_same_instance_count_times(self):
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 0)], count=3, one_shot=True
        )
        queue.start()

        # 真实时序（_replenish 在 pick 之后，首个任务 t1 建、t2 才 pick）：
        #   t1: 建实例 → PENDING（rem=3，submitted=1）
        #   t2: pick → IN_PROGRESS
        #   t3: DONE+重排(rem=2) → 同 tick 再 pick → IN_PROGRESS（fin=1）
        #   t4: 同上（rem=1，fin=2）
        #   t5: DONE，rem=0，fin=3=count → all_done
        for _ in range(8):
            queue.tick()
            if queue.all_done():
                break
        self.assertTrue(queue.all_done())
        # submitted 保持"已 create 数"原语义：只建了 1 个实例
        self.assertEqual(factory.submitted, 1)
        self.assertEqual(factory.finished_count, 3)
        self.assertEqual(queue.tasks[0].one_shot_remaining, 0)
        self.assertEqual(queue.tasks[0].status, TaskStatus.DONE)
        # 全程同一个实例：tasks 列表里始终只有它
        self.assertEqual(len(queue.tasks), 1)

    def test_one_shot_failed_still_counts_and_requeues(self):
        # 第 1 次失败，第 2 次成功 → 失败也消耗次数，共 count=2 次
        queue, factory, _ = make_queue(
            [(InteractionResult.FAILED, 0), (InteractionResult.SUCCESS, 0)],
            count=2,
            one_shot=True,
        )
        queue.start()

        # t1：建实例 → PENDING；t2：pick → FAILED → 重排 PENDING（rem 2→1）
        queue.tick()
        queue.tick()
        self.assertEqual(queue.tasks[0].status, TaskStatus.PENDING)
        self.assertEqual(queue.tasks[0].one_shot_remaining, 1)

        # t3：pick → SUCCESS → IN_PROGRESS；t4：DONE rem=0 → 收敛
        queue.tick()
        queue.tick()
        self.assertEqual(queue.tasks[0].one_shot_remaining, 0)
        self.assertEqual(queue.tasks[0].status, TaskStatus.DONE)
        self.assertTrue(queue.all_done())


class TestCron(unittest.TestCase):
    """定时任务：trigger 由 cron 表达式决定。"""

    def test_cron_first_run_waits_for_trigger_time(self):
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 0)], count=3, cron="* * * * *"
        )
        queue.start()

        # t1：建实例 → trigger 预置为下一次分钟边界 → SCHEDULED，
        # 不立即执行（首次也等 cron）
        queue.tick()
        self.assertEqual(len(queue.tasks), 1)
        self.assertEqual(queue.tasks[0].status, TaskStatus.SCHEDULED)
        self.assertEqual(factory.submitted, 1)
        self.assertEqual(factory.finished_count, 0)
        self.assertFalse(queue.all_done())

        # 时区守护：触发点秒/微秒字段必须为 0（对齐分钟边界，
        # croniter 传 float 会按 UTC 解析差 8h，秒字段会漂移）
        import datetime as _dt
        trigger = _dt.datetime.fromtimestamp(queue.tasks[0].trigger_time)
        self.assertEqual(trigger.second, 0)
        self.assertEqual(trigger.microsecond, 0)

        # trigger 在未来 → 不激活；拨到现在 → 激活执行
        queue.tasks[0].trigger_time = time.time() - 1
        queue.tick()
        self.assertEqual(queue.tasks[0].status, TaskStatus.IN_PROGRESS)

        # DONE → 排下一次（SCHEDULED）；重复两轮直到 count=3 用完
        for _ in range(6):
            queue.tick()
            if queue.tasks[0].status == TaskStatus.SCHEDULED:
                queue.tasks[0].trigger_time = time.time() - 1
            if queue.all_done():
                break
        self.assertTrue(queue.all_done())
        self.assertEqual(factory.finished_count, 3)
        self.assertEqual(queue.tasks[0].status, TaskStatus.DONE)

    def test_cron_invalid_expression_marks_exhausted(self):
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 0)], count=2, cron="not a cron"
        )
        queue.start()
        # 新口径：t1 建任务预置 trigger 时就解析失败 → exhausted，
        # 任务不会被激活执行
        queue.tick()
        self.assertTrue(factory.exhausted)
        self.assertTrue(queue.all_done())
        self.assertEqual(factory.finished_count, 0)


class TestFactoryQuota(unittest.TestCase):
    """can_submit_more 与 exhausted 的组合语义。"""

    def test_exhausted_blocks_even_with_quota(self):
        factory = TaskFactory(task_class=FakeTask, count=10)
        factory.exhausted = True
        self.assertFalse(factory.can_submit_more())

    def test_count_zero_means_unlimited(self):
        factory = TaskFactory(task_class=FakeTask, count=0)
        factory.submitted = 2  # 无限模式下模拟已提交
        self.assertTrue(factory.can_submit_more())
        factory.exhausted = True
        self.assertFalse(factory.can_submit_more())

    def test_fresh_limited_factory_can_submit(self):
        factory = TaskFactory(task_class=FakeTask, count=3)
        self.assertTrue(factory.can_submit_more())

    def test_oneshot_quota_decays_to_zero(self):
        # one_shot / cron 的执行次数收敛看 finished_count（独立于
        # submitted）：初始 0 可提交，每次终态 +1，到 count 收敛。
        factory = TaskFactory(task_class=FakeTask, count=3, one_shot=True)
        self.assertTrue(factory.can_submit_more())
        factory.finished_count = 3
        self.assertFalse(factory.can_submit_more())
        factory.finished_count = 2
        self.assertTrue(factory.can_submit_more())

    def test_oneshot_count_zero_treated_as_one(self):
        # count=0 常规语义是"无限"，但一次性/定时任务复用单实例、
        # 无法用 count=0 表达无限（会变成 finished_count < 0 恒 False，
        # 任务建不出来）。约定：count=0 按 1 次处理。
        factory = TaskFactory(task_class=FakeTask, count=0, one_shot=True)
        self.assertTrue(factory.can_submit_more())
        factory.finished_count = 1
        self.assertFalse(factory.can_submit_more())

    def test_oneshot_count_zero_runs_exactly_once_end_to_end(self):
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 0)], count=0, one_shot=True
        )
        queue.start()
        for _ in range(8):
            queue.tick()
            if queue.all_done():
                break
        self.assertTrue(queue.all_done())
        self.assertEqual(factory.finished_count, 1)  # 只执行了 1 次
        self.assertEqual(queue.tasks[0].status, TaskStatus.DONE)

    def test_fresh_one_shot_queue_not_all_done_before_first_fill(self):
        # run() 主循环入口守护：刚 start、还没建任何任务时，
        # all_done() 必须为 False，否则 while 一次都不进、任务永远建不出来。
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 0)], count=3, one_shot=True
        )
        queue.start()
        self.assertFalse(queue.all_done())
        self.assertTrue(factory.can_submit_more())

    def test_fresh_cron_queue_not_all_done_before_first_fill(self):
        queue, factory, _ = make_queue(
            [(InteractionResult.SUCCESS, 0)], count=3, cron="* * * * *"
        )
        queue.start()
        self.assertFalse(queue.all_done())


if __name__ == "__main__":
    unittest.main()
