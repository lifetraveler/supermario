# -*- coding: utf-8 -*-
"""
多 user 轮转调度（5.5）单元测试。

覆盖三层：
  1. loader：parse_user_chains / parse_user_configs 多链解析与链标记
  2. TaskFactory：machine/package/user_id 下发到任务实例
  3. UnifiedQueue：轮转让位判定 / 游标推进 / 切换失败跳过 /
     单链行为退化（与老版一致）/ 圈返回值协议

全部用假对象直测调度逻辑，不依赖游戏画面 / ADB 设备。
UnifiedQueue 实例通过 __new__ 跳过 __init__ 构造（跳过 Qt / 配置 IO），
依赖以属性注入，因此真实方法（含 super() 调用）全部可用。
"""
import threading
import time
import unittest
from unittest import mock

from src.scheduler.task_queue import TaskFactory
from src.scheduler.task_status import TaskStatus
from src.sg.tasks.config_loader.task_config_loader import (
    TaskConfigError, parse_user_chains, parse_user_configs, parse_document,
)


# ============================================================================
# 1. loader：多链解析
# ============================================================================
def make_doc(users):
    """构造 version+machines 实例段；users = [(user_id, enabled), …]"""
    return {
        "version": 1,
        "machines": [
            {
                "name": "m1",
                "enabled": True,
                "adb_serial": "",
                "apps": [
                    {
                        "package": "com.test.app",
                        "enabled": True,
                        "users": [
                            {"user_id": uid, "enabled": en, "tasks": []}
                            for uid, en in users
                        ],
                    }
                ],
            }
        ],
    }


def make_type_groups():
    """com.test.app 的最小合法定义段（parse_document 要求非空）。"""
    return {"com.test.app": [{
        "type": "Fake",
        "id": "fake_task",
        "name_prefix": "Fake",
        "description": "fake",
        "estimated_seconds": 0,
        "defaults": {
            "count": 1, "max_active": 1, "requires_march_queue": False,
            "next_trigger_delay": 0, "one_shot": False, "cron": "",
            "max_failures": 0,
        },
        "params": [],
    }]}


class TestParseUserChains(unittest.TestCase):
    def test_single_enabled_chain(self):
        chains = parse_user_chains(make_doc([(0, True), (10, False)]))
        self.assertEqual(len(chains), 1)
        m, a, u, trail = chains[0]
        self.assertEqual(u["user_id"], 0)
        self.assertEqual(trail, "m1/com.test.app/user0")

    def test_multiple_enabled_chains_in_order(self):
        chains = parse_user_chains(
            make_doc([(0, True), (10, True), (11, True)]))
        self.assertEqual([c[2]["user_id"] for c in chains], [0, 10, 11])

    def test_no_enabled_chain_raises(self):
        with self.assertRaises(TaskConfigError):
            parse_user_chains(make_doc([(0, False)]))

    def test_disabled_machine_skipped(self):
        doc = make_doc([(0, True)])
        doc["machines"][0]["enabled"] = False
        doc["machines"].append({
            "name": "m2", "enabled": True, "adb_serial": "",
            "apps": [{"package": "com.test.app", "enabled": True,
                      "users": [{"user_id": 5, "enabled": True,
                                 "tasks": []}]}],
        })
        chains = parse_user_chains(doc)
        self.assertEqual([c[2]["user_id"] for c in chains], [5])


class TestParseUserConfigs(unittest.TestCase):
    def test_chain_markers_attached(self):
        cfgs = parse_user_configs(
            make_doc([(0, True), (10, True)]), make_type_groups())
        self.assertEqual([c["user_id"] for c in cfgs], [0, 10])
        self.assertEqual([c["package"] for c in cfgs], ["com.test.app"] * 2)
        self.assertEqual([c["machine"] for c in cfgs], ["m1"] * 2)
        self.assertEqual(cfgs[1]["trail"], "m1/com.test.app/user10")

    def test_first_entry_is_parse_document_default(self):
        doc = make_doc([(0, True), (10, True)])
        cfgs = parse_user_configs(doc, make_type_groups())
        single = parse_document(doc, make_type_groups())
        for key in ("trail", "app", "queue", "task_types", "tasks"):
            self.assertEqual(cfgs[0][key], single[key])

# ============================================================================
# 2. TaskFactory：链标记下发
# ============================================================================
def fake_task_class():
    def make(*args, **kwargs):
        t = mock.Mock()
        t.extra_config = mock.Mock()
        return t
    return make


class TestFactoryChainMarkers(unittest.TestCase):
    def test_create_injects_chain_attrs(self):
        f = TaskFactory(task_class=fake_task_class(), machine="m1",
                        package="com.test.app", user_id=10)
        task = f.create(mock.Mock(), mock.Mock(), mock.Mock())
        self.assertEqual(task.machine, "m1")
        self.assertEqual(task.package, "com.test.app")
        self.assertEqual(task.user_id, 10)

    def test_defaults_for_legacy_callers(self):
        f = TaskFactory(task_class=fake_task_class())
        task = f.create(mock.Mock(), mock.Mock(), mock.Mock())
        self.assertIsNone(task.machine)
        self.assertIsNone(task.package)
        self.assertEqual(task.user_id, 0)


# ============================================================================
# 3. UnifiedQueue：轮转调度（真类 + __new__ 跳过 __init__）
# ============================================================================
from src.sg.tasks.queue.QueueTaskBase import QueueTaskBase
from src.sg.tasks.queue.UnifiedQueue import UnifiedQueue


def make_user_cfg(user_id, package="com.test.app", enabled_tasks=1):
    return {
        "trail": f"m1/{package}/user{user_id}",
        "app": {"package": package, "user_id": user_id,
                "after_done_user_id": 0},
        "queue": {"tick_interval": 1.0, "continue_after_failure": True},
        "task_types": {},
        "tasks": [
            {"id": "fake_task", "type": "Fake", "enabled": i < enabled_tasks,
             "name_prefix": "Fake", "estimated_seconds": 0,
             "count": 1, "max_active": 1, "requires_march_queue": False,
             "next_trigger_delay": 0, "one_shot": False, "cron": "",
             "max_failures": 0,
             "params": {}}
            for i in range(2)
        ],
        "machine": "m1",
        "package": package,
        "user_id": user_id,
    }


def make_queue(users):
    """真实 UnifiedQueue 实例（跳过 __init__），依赖全注入。"""
    q = UnifiedQueue.__new__(UnifiedQueue)
    q.queue = None
    q._task_config = users[0]
    q._rotation_users = users
    q._rotation_index = 0
    q._rotation_mode = "user_rotation"  # 老测试默认完全轮转（5.5 行为）
    q._rotation_started_at = time.time()
    q._executor = mock.Mock()          # executor 是只读 property
    q._executor.device_manager = None  # 默认非 ADB 环境
    q.logs = []
    q.log_info = q.logs.append
    return q


class FakeTask:
    def __init__(self, status=TaskStatus.PENDING, trigger=0.0, retry=0.0):
        self.name = "FakeTask"
        self.status = status
        self.trigger_time = trigger
        self.next_retry_time = retry


class TestRotationSwitch(unittest.TestCase):
    def test_single_user_delegates_to_base(self):
        q = make_queue([make_user_cfg(0)])
        with mock.patch.object(QueueTaskBase, "_run_queue_cycle",
                               return_value="done") as base_cycle:
            self.assertEqual(q._run_queue_cycle(), "done")
            base_cycle.assert_called_once_with()

    def test_multi_user_marks_rotation_started(self):
        users = [make_user_cfg(0), make_user_cfg(10)]
        q = make_queue(users)
        with mock.patch.object(QueueTaskBase, "_run_queue_cycle",
                               return_value="done"), \
             mock.patch.object(QueueTaskBase, "_reload_config"), \
             mock.patch.object(QueueTaskBase, "_build_factories"), \
             mock.patch.object(QueueTaskBase, "_log_factories_summary"), \
             mock.patch.object(QueueTaskBase, "_queue_global_params",
                               return_value={"tick_interval": 0.0,
                                             "continue_after_failure": True}):
            q._run_queue_cycle()
        self.assertIsNotNone(q._rotation_started_at)
        self.assertTrue(any("[Rotation] 圈" in m for m in q.logs))

    def test_cursor_advances_in_config_order(self):
        users = [make_user_cfg(0), make_user_cfg(10), make_user_cfg(11)]
        q = make_queue(users)
        self.assertEqual(q._next_rotation_user()["user_id"], 0)
        self.assertEqual(q._next_rotation_user()["user_id"], 10)
        self.assertEqual(q._next_rotation_user()["user_id"], 11)
        self.assertEqual(q._next_rotation_user()["user_id"], 0)  # 回绕

    def test_switch_skips_failed_users(self):
        users = [make_user_cfg(0), make_user_cfg(10), make_user_cfg(11)]
        q = make_queue(users)
        dm = mock.Mock()
        dm.user_id = 99  # 与所有目标都不同，全部走真实启动路径

        def start(package, user_id=None):
            return user_id != 10  # user10 启动失败

        dm.adb_start_package.side_effect = start
        q._executor.device_manager = dm
        q._rotation_index = 0
        self.assertEqual(q._next_rotation_user()["user_id"], 0)
        self.assertEqual(q._next_rotation_user()["user_id"], 11)  # 跳过 10
        self.assertEqual(q._next_rotation_user()["user_id"], 0)

    def test_switch_updates_dm_user_id(self):
        users = [make_user_cfg(0), make_user_cfg(10)]
        q = make_queue(users)
        dm = mock.Mock()
        dm.user_id = 0
        dm.adb_start_package.return_value = True
        q.executor.device_manager = dm
        self.assertTrue(q._switch_app_for_user(users[1]))
        self.assertEqual(dm.user_id, 10)
        # 10.22 起：切用户无需 force-stop，仅 am start --user 即可
        dm.adb_stop_package.assert_not_called()

    def test_all_switch_failures_returns_none(self):
        users = [make_user_cfg(0), make_user_cfg(10)]
        q = make_queue(users)
        dm = mock.Mock()
        dm.user_id = 99  # 所有 user 都需要真实启动，且全部失败
        dm.adb_start_package.return_value = False
        q.executor.device_manager = dm
        self.assertIsNone(q._next_rotation_user())

    def test_same_user_no_restart(self):
        users = [make_user_cfg(0)]
        q = make_queue(users)
        dm = mock.Mock()
        dm.user_id = 0
        q.executor.device_manager = dm
        self.assertTrue(q._switch_app_for_user(users[0]))
        dm.adb_start_package.assert_not_called()
        dm.adb_stop_package.assert_not_called()

    def test_non_adb_environment_treated_as_in_place(self):
        users = [make_user_cfg(0), make_user_cfg(10)]
        q = make_queue(users)  # executor.device_manager = None
        self.assertTrue(q._switch_app_for_user(users[1]))


class TestRotationYield(unittest.TestCase):
    def _q(self, tasks):
        q = make_queue([make_user_cfg(0), make_user_cfg(10)])
        queue = mock.Mock()
        queue.tasks = tasks
        q.queue = queue
        q._rotation_deadline = None
        return q

    def test_single_user_never_yields(self):
        q = make_queue([make_user_cfg(0)])
        q.queue = mock.Mock()
        q.queue.tasks = [FakeTask(trigger=time.time() + 9999)]
        self.assertIsNone(q._rotation_yield_reason())

    def test_budget_exceeded_yields(self):
        q = self._q([FakeTask(trigger=time.time() + 10)])
        q._rotation_started_at = time.time() - 100
        q._rotation_deadline = time.time() - 1  # 预算已过
        self.assertEqual(q._rotation_yield_reason(), "budget")

    def test_running_task_blocks_yield(self):
        q = self._q([
            FakeTask(status=TaskStatus.IN_PROGRESS),
            FakeTask(trigger=time.time() + 9999),
        ])
        self.assertIsNone(q._rotation_yield_reason())

    def test_far_next_task_yields(self):
        q = self._q([
            FakeTask(status=TaskStatus.SCHEDULED,
                     trigger=time.time() + 3600),  # 1 小时后
        ])
        reason = q._rotation_yield_reason()
        self.assertIsNotNone(reason)
        self.assertIn("next_task_gap", reason)

    def test_pending_retry_far_yields(self):
        # one_shot 远期自重排：PENDING + next_retry_time 远期
        q = self._q([
            FakeTask(status=TaskStatus.PENDING,
                     retry=time.time() + 86400),  # 明天
        ])
        reason = q._rotation_yield_reason()
        self.assertIsNotNone(reason)
        self.assertIn("next_task_gap", reason)

    def test_near_task_keeps_running(self):
        q = self._q([
            FakeTask(status=TaskStatus.SCHEDULED,
                     trigger=time.time() + 60),  # 1 分钟后
        ])
        self.assertIsNone(q._rotation_yield_reason())

    def test_done_failed_only_yields(self):
        q = self._q([
            FakeTask(status=TaskStatus.DONE),
            FakeTask(status=TaskStatus.FAILED),
        ])
        self.assertEqual(q._rotation_yield_reason(), "no_pending")


class TestRotationBudget(unittest.TestCase):
    def test_budget_from_estimated_seconds(self):
        users = [make_user_cfg(0), make_user_cfg(10)]
        users[0]["tasks"][0]["estimated_seconds"] = 600
        users[0]["tasks"][0]["count"] = 2
        q = make_queue(users)
        q._rotation_started_at = 1000.0
        q._rotation_deadline = None
        self.assertEqual(q._rotation_budget_deadline(), 2200.0)

    def test_disabled_tasks_excluded(self):
        users = [make_user_cfg(0, enabled_tasks=0), make_user_cfg(10)]
        users[0]["tasks"][0]["estimated_seconds"] = 600
        q = make_queue(users)
        q._rotation_started_at = 1000.0
        q._rotation_deadline = None
        self.assertIsNone(q._rotation_budget_deadline())

    def test_budget_cached_until_mark(self):
        users = [make_user_cfg(0), make_user_cfg(10)]
        users[0]["tasks"][0]["estimated_seconds"] = 600
        q = make_queue(users)
        q._rotation_started_at = 1000.0
        q._rotation_deadline = None
        d1 = q._rotation_budget_deadline()
        self.assertEqual(d1, 1600.0)
        self.assertEqual(q._rotation_budget_deadline(), d1)  # 缓存命中
        q._mark_rotation_started()  # 重置缓存 + 重打点（真实时钟）
        d2 = q._rotation_budget_deadline()
        self.assertNotEqual(d2, d1)
        self.assertAlmostEqual(d2 - q._rotation_started_at, 600.0, delta=1.0)


class TestCycleReturnCodes(unittest.TestCase):
    """基类 _run_queue_cycle 的返回值协议（让位 / 完成 / 停止）。"""

    def _base(self, reason_side_effects, all_done_side_effects):
        obj = QueueTaskBase.__new__(QueueTaskBase)
        obj.log_info = lambda msg: None
        obj.sleep = lambda s: None
        obj.exit_is_set = lambda: False  # 遮蔽真方法（读 Mock 恒真值）
        obj._paused = False  # paused 是只读 property
        obj._app = mock.Mock()
        obj._executor = mock.Mock()  # executor 是只读 property
        obj.scene = mock.Mock()
        obj.queue = None
        obj._factories = []
        # B2（事件驱动主循环）需要的 __init__ 属性：__new__ 绕过了
        # __init__，这里按最小集补齐，语义与真构造一致。
        obj._wake_cond = threading.Condition()
        obj._wake_flag = False
        obj._last_info_version = -1
        obj._queue_global_params = lambda: {
            "tick_interval": 0.0, "continue_after_failure": True}
        obj._reload_config = mock.Mock()
        obj._build_factories = mock.Mock()
        obj._after_queue_done = mock.Mock()
        obj._update_info = mock.Mock()
        obj._rotation_yield_reason = mock.Mock(
            side_effect=reason_side_effects or None,
            return_value=None)  # side_effect 耗尽后回退 None（继续跑）
        # B2 主循环会等唤醒；__new__ 构造下 stub 掉真实等待。
        obj._wait_wake = lambda timeout: None
        return obj, obj._after_queue_done

    def _run(self, obj, all_done_side_effects):
        # TaskQueue 已被 from-import 进 QueueTaskBase 命名空间，
        # 必须 patch 使用点，否则构造的是真队列（空队列 all_done
        # 恒 False → 主循环死转）
        with mock.patch(
                "src.sg.tasks.queue.QueueTaskBase.TaskQueue") as tq_cls:
            queue = mock.Mock()
            # B2：主循环 tick 后查 next_wakeup_time；None = 走兜底间隔。
            queue.next_wakeup_time.return_value = None
            queue.all_done.side_effect = (
                all_done_side_effects if all_done_side_effects else True)
            tq_cls.return_value = queue
            return obj._run_queue_cycle()

    def test_done_calls_after_done(self):
        obj, after_done = self._base(None, None)  # reason 恒 None
        # 主循环 2 查（False→True）+ 收尾 1 查（True）
        result = self._run(obj, [False, True, True])
        self.assertEqual(result, "done")
        after_done.assert_called_once()

    def test_yield_skips_after_done(self):
        obj, after_done = self._base(["idle", "idle"], None)
        # 收尾段 all_done=False（队列没跑完，只是让位）：
        # 主循环 2 查（False→False）+ 收尾 1 查（False）
        result = self._run(obj, [False, False, False])
        self.assertEqual(result, "yield")
        after_done.assert_not_called()

    def test_user_stop_wins(self):
        obj, after_done = self._base(["idle", "idle"], None)
        obj.exit_is_set = lambda: True
        result = self._run(obj, [False, True, True])
        self.assertEqual(result, "stop")
        after_done.assert_not_called()


if __name__ == "__main__":
    unittest.main()
