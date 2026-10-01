# GenericQueueTask 配置收起/展开回归测试
# 契约：每个注册类型的 "{key}: Enabled" 开关必须始终可见，
# 其余配置（Count / Max Active / Next Trigger Delay / extra_config）
# 通过 config_type["sub_configs"] 挂在 Enabled 下，未启用时全部收起。
# 防回归点：新增基础配置项却忘记加进收起列表。
import unittest

from src.config import config
from ok.core.config_schema import config_visibility
from ok.test.TaskTestCase import TaskTestCase

from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.queue.UnifiedQueue import UnifiedQueue


class TestGenericQueueConfig(TaskTestCase):
    task_class = UnifiedQueue
    config = config

    def test_enabled_declares_sub_configs_for_all_types(self):
        for key in GenericQueueTask.TASK_REGISTRY:
            the_type = self.task.config_type.get(f"{key}: Enabled")
            rules = the_type.get("sub_configs") if isinstance(the_type, dict) else None
            self.assertIsInstance(rules, dict, f"{key}: Enabled missing sub_configs")
            self.assertIn(True, rules, f"{key}: Enabled sub_configs must key on True")

    def test_disabled_type_collapses_everything_except_enabled_switch(self):
        vis = config_visibility(self.task.config, self.task.config_type)
        checked = 0
        for key in GenericQueueTask.TASK_REGISTRY:
            if self.task.config.get(f"{key}: Enabled"):
                continue
            self.assertTrue(vis(f"{key}: Enabled"),
                            f"{key}: Enabled switch must stay visible")
            prefix = f"{key}: "
            for k in self.task.default_config:
                if k.startswith(prefix) and k != f"{key}: Enabled":
                    self.assertFalse(vis(k), f"{k} must be collapsed while disabled")
                    checked += 1
        self.assertGreater(checked, 0, "no collapsed config keys found")

    def test_new_base_configs_declared_and_collapsed(self):
        # One Shot / Cron 是新增基础配置：必须始终存在于 default_config，
        # 且未启用类型时收起（挂在 Enabled 的 sub_configs 下）。
        self.assertGreater(len(GenericQueueTask.TASK_REGISTRY), 0)
        for key in GenericQueueTask.TASK_REGISTRY:
            self.assertIn(f"{key}: One Shot", self.task.default_config)
            self.assertIn(f"{key}: Cron", self.task.default_config)
            self.assertIn(f"{key}: One Shot", self.task.default_config)

    def test_task_factory_builds_with_one_shot_and_cron_defaults(self):
        # _build_factories 读取 One Shot / Cron 配置不抛异常，
        # 且 Cron 空字符串归一化为 None。
        vis = config_visibility(self.task.config, self.task.config_type)
        self.task._factories.clear()
        try:
            self.task._build_factories()
        except Exception as e:
            self.fail(f"_build_factories raised: {e}")
        for factory in self.task._factories:
            self.assertIsInstance(factory.one_shot, bool)
            self.assertTrue(factory.cron is None or isinstance(factory.cron, str))

    def test_expanded_toggle_exists_and_expanded_by_default(self):
        # 每个注册类型都有 "{key}: Expanded" 开关（默认 True=展开），
        # 详情项挂在 Expanded 的 sub_configs 下，Enabled 只链控 Expanded。
        for key in GenericQueueTask.TASK_REGISTRY:
            self.assertIn(f"{key}: Expanded", self.task.default_config)
            self.assertTrue(self.task.default_config[f"{key}: Expanded"])

            expanded_type = self.task.config_type.get(f"{key}: Expanded")
            rules = expanded_type.get("sub_configs") if isinstance(expanded_type, dict) else None
            self.assertIsInstance(rules, dict, f"{key}: Expanded missing sub_configs")
            self.assertIn(True, rules, f"{key}: Expanded sub_configs must key on True")
            self.assertIn(f"{key}: Count", rules[True],
                          f"{key}: Count must hang under Expanded")

            enabled_type = self.task.config_type.get(f"{key}: Enabled")
            self.assertEqual(
                enabled_type.get("sub_configs", {}).get(True),
                [f"{key}: Expanded"],
                f"{key}: Enabled must chain-control Expanded only",
            )

    def test_expand_chain_visibility(self):
        # 链控可见性：Enabled=False → Expanded 不可见；
        # Enabled=True & Expanded=False → 详情收起；Expanded=True → 详情展开。
        vis = config_visibility(self.task.config, self.task.config_type)
        key = next(iter(GenericQueueTask.TASK_REGISTRY))
        count_key, enabled_key, expanded_key = (
            f"{key}: Count", f"{key}: Enabled", f"{key}: Expanded",
        )
        old_enabled, old_expanded = (
            self.task.config.get(enabled_key), self.task.config.get(expanded_key),
        )
        try:
            self.task.config[enabled_key] = False
            self.assertFalse(vis(expanded_key),
                             "Expanded must stay hidden while disabled")

            self.task.config[enabled_key] = True
            self.task.config[expanded_key] = False
            self.assertTrue(vis(expanded_key),
                            "Expanded switch must show once enabled")
            self.assertFalse(vis(count_key),
                             "Count must collapse while Expanded is off")

            self.task.config[expanded_key] = True
            self.assertTrue(vis(count_key),
                            "Count must show while Expanded is on")
        finally:
            self.task.config[enabled_key] = old_enabled
            self.task.config[expanded_key] = old_expanded

    def test_factories_summary_log_line(self):
        # 汇总行：每个启用工厂输出 name_prefix/count/max_active/one_shot/cron。
        self.task._factories.clear()
        try:
            self.task._build_factories()
        except Exception as e:
            self.fail(f"_build_factories raised: {e}")
        logs = []
        self.task.log_info = lambda msg, *a, **k: logs.append(str(msg))
        self.task._log_factories_summary()
        self.assertEqual(len(logs), 1, "summary must be a single log line")
        self.assertTrue(logs[0].startswith("[Factories]"),
                        f"summary must start with [Factories]: {logs[0]!r}")
        for factory in self.task._factories:
            snippet = (
                f"{factory.name_prefix}(x{factory.count},"
                f"active{factory.max_active}"
            )
            self.assertIn(snippet, logs[0],
                          f"summary missing {snippet!r}: {logs[0]!r}")
            if factory.one_shot:
                self.assertIn("one_shot", logs[0])
            if factory.cron:
                self.assertIn(f"cron={factory.cron}", logs[0])


if __name__ == '__main__':
    unittest.main()
