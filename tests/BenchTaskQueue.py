# TaskQueue 微基准（B1.5，不进 CI，直接 python 运行）
#
# 对比项：
#   1. _activate_scheduled + _pick_next 每 tick 耗时（旧 O(N²) 暴力内联 vs 新 O(1) 计数）
#   2. _check_stalled_factories F=100（旧 O(F×N) 每工厂全量扫 vs 新 O(N+F) 索引）
#   3. _count_in_progress 新旧实现
#   4. get_snapshot 耗时
#
# 运行：.venv/Scripts/python.exe tests/BenchTaskQueue.py
import time
from unittest import mock

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scheduler.task_queue import TaskFactory, TaskQueue
from src.scheduler.task_status import InteractionResult, TaskStatus


class BenchTask:
    """零开销假任务：不执行任何交互，只承载状态字段。"""

    def __init__(self, take=None, name="B", **kwargs):
        self.name = name
        self.checks = 0
        self.check_ok = True
        # get_snapshot 按 user_id 前缀拼任务名，缺省会崩
        self.user_id = 0

    def run_interaction(self):
        return (InteractionResult.SUCCESS, 0)

    def check_completed(self):
        self.checks += 1
        return self.check_ok


def build_queue(n_tasks, n_factories=1, requires_march=False):
    """n_tasks 个 PENDING 裸任务 + n_factories 个工厂。"""
    queue = TaskQueue(executor=mock.Mock(), app=mock.Mock(), scene=mock.Mock())
    for i in range(n_factories):
        f = TaskFactory(task_class=BenchTask, count=0,
                        name_prefix=f"B{i}", user_id=i,
                        requires_march_queue=requires_march)
        queue.add_factory(f)
        # 给每个工厂塞 n_tasks / n_factories 个 PENDING 任务
        per = n_tasks // n_factories
        for _ in range(per):
            t = BenchTask()
            t.factory_key = f.key
            t.status = TaskStatus.PENDING
            t.trigger_time = 0.0
            t.next_retry_time = 0.0
            t.paused = False
            t.task_id = f"t{i}-{_}"
            queue.tasks.append(t)
            # 直接进计数器，模拟 add 后的状态
            queue._in_progress_count[f.key] = 0
    return queue


def _quiet_logs():
    """基准只看数字，把 task_queue 模块的 logger 换成空操作。
    ok.Logger 自带 handler，logging.disable 拦不住，直接换对象最干净。"""
    import src.scheduler.task_queue as tq

    class _Silent:
        def __getattr__(self, name):
            return lambda *a, **k: None

    tq.logger = _Silent()




# ------------------------------------------------------------
# 旧实现内联复刻（基准对照，不 import 生产代码的旧版本）
# ------------------------------------------------------------

def old_activate_scheduled(queue, now):
    """旧实现：SCHEDULED→PENDING，占队列型每个任务全量扫 IN_PROGRESS。"""
    for task in queue.tasks:
        if task.paused:
            continue
        if task.status != TaskStatus.SCHEDULED:
            continue
        if task.trigger_time > now:
            continue
        factory = queue._find_factory(task)
        if factory is not None and factory.requires_march_queue:
            in_progress = sum(
                1 for t in queue.tasks
                if getattr(t, "factory_key", None) == factory.key
                and t.status == TaskStatus.IN_PROGRESS
            )
            if in_progress >= factory.max_active:
                continue
        task.status = TaskStatus.PENDING


def old_pick_next(queue, now):
    """旧实现：FIFO 找 PENDING，占队列型每个候选全量扫 IN_PROGRESS。"""
    for task in queue.tasks:
        if task.paused:
            continue
        if task.status != TaskStatus.PENDING:
            continue
        if task.next_retry_time > now:
            continue
        factory = queue._find_factory(task)
        if factory is not None and factory.requires_march_queue:
            in_progress = sum(
                1 for t in queue.tasks
                if getattr(t, "factory_key", None) == factory.key
                and t.status == TaskStatus.IN_PROGRESS
            )
            if in_progress >= factory.max_active:
                continue
        return task
    return None


def old_check_stalled(queue, now, user_id=None):
    """旧实现：每工厂全量扫任务建 own 列表（O(F×N)）。
    这里不带 user_id 过滤（等价于最有利的旧实现）。"""
    for factory in queue.factories:
        if factory.exhausted or not factory.can_submit_more():
            continue
        if factory.one_shot or factory.cron:
            continue
        own = [
            t for t in queue.tasks
            if getattr(t, "factory_key", None) == factory.key
        ]
        if not own:
            continue
        if any(
            t.status not in (TaskStatus.DONE, TaskStatus.FAILED)
            for t in own
        ):
            continue


def old_count_in_progress(queue, factory_key):
    return sum(
        1 for t in queue.tasks
        if getattr(t, "factory_key", None) == factory_key
        and t.status == TaskStatus.IN_PROGRESS
    )


def bench(label, fn, repeat=5):
    """取 repeat 次最小值（排除调度噪声）。"""
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = min(best, dt)
    return best


def fmt_row(cells, widths):
    return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))


def main():
    _quiet_logs()
    print("=" * 72)
    print("TaskQueue 调度热点微基准（越小越好，单位 us/tick）")
    # ---- 1. _activate_scheduled + _pick_next：旧 vs 新 ----
    # 场景：march_queue 型工厂，1 个任务 IN_PROGRESS 占满 max_active=1，
    # 其余 N-1 个 SCHEDULED。两个函数都是"扫完全部候选后仍被名额挡住"，
    # 不产生状态变更 → 可重复计时。
    # 旧实现：每个候选任务全量扫 N 个任务数 IN_PROGRESS → O(N²)。
    # 新实现：O(1) 字典读 → O(N)。
    print("\n[1] _activate_scheduled + _pick_next 每 tick（march 型, 名额饱和, N 任务）")
    header = ["N", "旧 O(N²)", "新 O(N)", "加速"]
    widths = [8, 12, 12, 8]
    print(fmt_row(header, widths))
    for n in (100, 1000, 10000):
        def make():
            q = build_queue(n, requires_march=True)
            # 1 个 IN_PROGRESS 占满 max_active=1
            holder = q.tasks[0]
            holder.status = TaskStatus.IN_PROGRESS
            q._in_progress_count[q.factories[0].key] = 1
            for t in q.tasks[1:]:
                t.status = TaskStatus.SCHEDULED
            return q

        q_new = make()
        q_old = make()

        def run_new():
            now = time.time()
            q_new._activate_scheduled(now)
            q_new._pick_next(now)

        def run_old():
            now = time.time()
            old_activate_scheduled(q_old, now)
            old_pick_next(q_old, now)

        t_new = bench("new", run_new) * 1e6
        # 旧实现 O(N²)：N=10000 单次约 10s，大 N 只跑 1 次避免拖太久
        rep_old = 5 if n <= 1000 else 1
        t_old = bench("old", run_old, repeat=rep_old) * 1e6
        speedup = t_old / t_new if t_new > 0 else float("inf")
        print(fmt_row([n, f"{t_old:.1f}", f"{t_new:.1f}",
                       f"{speedup:.1f}x"], widths))

    # ---- 2. _check_stalled_factories F=100 ----
    print("\n[2] _check_stalled_factories（F=100 工厂, N 任务, 全部非终态=不卡死快速路径）")
    header = ["N", "旧 O(F×N)", "新 O(N+F)", "加速"]
    widths = [8, 12, 12, 8]
    print(fmt_row(header, widths))
    for n in (100, 1000, 10000):
        q_old = build_queue(n, n_factories=100)
        q_new = build_queue(n, n_factories=100)
        t_old = bench("old", lambda: old_check_stalled(q_old, time.time())) * 1e6
        t_new = bench(
            "new", lambda: q_new._check_stalled_factories(time.time())
        ) * 1e6
        speedup = t_old / t_new if t_new > 0 else float("inf")
        print(fmt_row([n, f"{t_old:.1f}", f"{t_new:.1f}",
                       f"{speedup:.1f}x"], widths))

    # ---- 3. _count_in_progress 新旧 ----
    print("\n[3] _count_in_progress 单次调用（N 任务，1 工厂）")
    header = ["N", "旧全量扫", "新 O(1)", "加速"]
    widths = [8, 12, 12, 8]
    print(fmt_row(header, widths))
    for n in (100, 1000, 10000):
        q = build_queue(n)
        key = q.factories[0].key
        # 一半任务 IN_PROGRESS，让计数有意义
        half = q.tasks[: n // 2]
        for t in half:
            t.status = TaskStatus.IN_PROGRESS
        q._in_progress_count[key] = len(half)
        t_old = bench("old", lambda: old_count_in_progress(q, key)) * 1e6
        t_new = bench("new", lambda: q._count_in_progress(key)) * 1e6
        speedup = t_old / t_new if t_new > 0 else float("inf")
        print(fmt_row([n, f"{t_old:.2f}", f"{t_new:.3f}",
                       f"{speedup:.0f}x"], widths))

    # ---- 4. get_snapshot ----
    print("\n[4] get_snapshot 单次调用（N 任务混合状态）")
    header = ["N", "耗时(us)"]
    widths = [8, 12]
    print(fmt_row(header, widths))
    for n in (100, 1000, 10000):
        q = build_queue(n)
        statuses = [TaskStatus.SCHEDULED, TaskStatus.PENDING,
                    TaskStatus.IN_PROGRESS, TaskStatus.DONE,
                    TaskStatus.FAILED]
        for i, t in enumerate(q.tasks):
            t.status = statuses[i % len(statuses)]
            if t.status == TaskStatus.IN_PROGRESS:
                t.estimated_finish_time = time.time() + 60
        t_snap = bench("snap", q.get_snapshot) * 1e6
        print(fmt_row([n, f"{t_snap:.1f}"], widths))

    print("\n说明：旧实现为旧算法的内联复刻（不含日志/状态机差异），")
    print("加速比反映数据结构复杂度变化，非端到端 tick 差异。")


if __name__ == "__main__":
    main()
