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

    def test_enabled_type_expands_all_configs(self):
        enabled = [k for k in GenericQueueTask.TASK_REGISTRY
                   if self.task.config.get(f"{k}: Enabled")]
        if not enabled:
            self.skipTest("no enabled task type in current persisted config")
        vis = config_visibility(self.task.config, self.task.config_type)
        for key in enabled:
            for k in self.task.default_config:
                if k.startswith(f"{key}: "):
                    self.assertTrue(vis(k), f"{k} must be visible while enabled")


if __name__ == '__main__':
    unittest.main()
