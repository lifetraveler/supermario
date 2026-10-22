# -*- coding: utf-8 -*-
"""
多 user 轮转端到端仿真：假 ADB 设备 + 假任务类 + 双 user 配置。

验证链路：run() → _reload_config（读盘多链）→ 圈1 user0 跑队列 →
让位 → 圈2 切 user10（stop + am start 调用序）→ 跑队列 → done。

不依赖真机；TaskQueue 真实推进（tick 循环），只假设备与任务交互。
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from src.sg.tasks.queue import UnifiedQueue as uq_mod
from src.sg.tasks.queue.UnifiedQueue import UnifiedQueue
from src.sg.tasks.queue.QueueTaskBase import QueueTaskBase
from src.scheduler.task_status import InteractionResult, TaskStatus

TMP = Path(tempfile.mkdtemp(prefix="rotation_e2e_"))
CFG = TMP / "task_config.json"
TYPES = TMP / "task_type.json"
import src.sg.tasks.config_loader.task_config_loader as tcl
tcl.DEFAULT_TYPE_PATH = TYPES
uq_mod.CONFIG_PATH = CFG  # 必须在 stub 之后赋值（读磁盘时机在 run 内）


def write_cfg():
    TYPES.write_text(json.dumps([{
        "package": "com.got.china",
        "task_types": [{
            "type": "Hunt Monster", "id": "hunt_monster",
            "name_prefix": "HuntMonster", "description": "d",
            "estimated_seconds": 2,
            "defaults": {"count": 1, "max_active": 1,
                         "requires_march_queue": False,
                         "next_trigger_delay": 0, "one_shot": False,
                         "cron": ""},
            "params": [],
        }],
    }], ensure_ascii=False), encoding="utf-8")

    def user(uid):
        return {"user_id": uid, "enabled": True,
                "queue": {"tick_interval": 0.01,
                          "continue_after_failure": True},
                "tasks": [{"id": "hunt_monster", "enabled": True}]}

    CFG.write_text(json.dumps({
        "version": 1,
        "machines": [{"name": "m1", "enabled": True, "adb_serial": "",
                      "apps": [{"package": "com.got.china", "enabled": True,
                                "users": [user(0), user(10)]}]}],
    }, ensure_ascii=False), encoding="utf-8")


write_cfg()

uq = object.__new__(UnifiedQueue)
uq._factories = []
uq.logs = []
uq.log_info = lambda msg: (uq.logs.append(str(msg)),
                           print(f"[UQ] {msg}"))
uq.sleep = lambda s: time.sleep(min(s, 0.01))
uq.exit_is_set = lambda: False
uq.info_set = lambda *a, **k: None
uq.name = "任务调度"  # 跳过 __init__ 的最小桩
uq.queue = None
uq_mod.TASK_TYPE_MAP = {"Hunt Monster": object}  # 预载/重载校验用（占位，稍后换真类）
uq._task_config = uq_mod.load(uq_mod.CONFIG_PATH)  # 模拟 __init__ 预载（params 在 reload 前读）
uq._paused = False  # paused 只读 property 的后备字段
uq._rotation_users = []
uq._rotation_index = 0
uq._rotation_started_at = 0.0
uq._rotation_deadline = None
uq._executor = mock.Mock()
uq._app = mock.Mock()
uq.scene = mock.Mock()

# ---- 假 ADB：user0 已在位，切 user10 成功 ----
dm = mock.Mock()
dm.user_id = 0
dm.adb_start_package.return_value = True
uq._executor.device_manager = dm

# ---- 假任务类：每次交互立即 SUCCESS、check_completed 立即通过 ----
class FakeTask:
    def __init__(self, executor=None, app=None):
        self.extra_config = mock.Mock()
        self.next_trigger_delay = 0.0

    def after_init(self, executor=None, scene=None):
        pass

    def run_interaction(self):
        return InteractionResult.SUCCESS, 0.0

    def check_completed(self):
        return True


uq_mod.TASK_TYPE_MAP = {"Hunt Monster": FakeTask}  # 真假任务类（工厂 create 用）

# 跑整个 run()：两圈（user0 → user10）
t0 = time.time()
result = UnifiedQueue.run(uq)
elapsed = time.time() - t0

print(f"\n=== run() -> {result}, {elapsed:.1f}s ===")
print("--- 切换调用序 ---")
for c in dm.mock_calls:
    print(" ", c)

# ---- 断言 ----
rotation_logs = [m for m in uq.logs if "[Rotation] 圈" in m]
assert len(rotation_logs) == 2, f"应跑 2 圈，实际 {len(rotation_logs)}:\n" + "\n".join(rotation_logs)
assert "user0" in rotation_logs[0] and "user10" in rotation_logs[1], rotation_logs
# user0 在位 → 不重启；user10 → stop + start
dm.adb_stop_package.assert_called_once()
dm.adb_start_package.assert_called_once()
args, kwargs = dm.adb_start_package.call_args
assert args[0] == "com.got.china" and kwargs.get("user_id") == 10, (args, kwargs)
assert dm.user_id == 10
# 工厂链标记正确（_factories 每圈重建，保留的是最后一圈 user10 的）
marks = [(f.user_id, f.package, f.machine) for f in uq._factories]
assert marks == [(10, "com.got.china", "m1")], marks
# 跑完切下家的协议发生（done_yield；否则圈 2 不会出现）
handoff_logs = [m for m in uq.logs if "cycle_done" in m or "跑完" in m]
assert len(rotation_logs) == 2, rotation_logs  # 圈 2 出现即协议已生效
assert result is True

shutil.rmtree(TMP, ignore_errors=True)
print("\nE2E PASS: 2 圈轮转 + 切换调用序 + 链标记 + 让位 + done")
