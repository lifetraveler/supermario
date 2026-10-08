# -*- coding: utf-8 -*-
"""验证 UnifiedQueue 每次 run 都从磁盘重读 task_config.json。

改动面：
  QueueTaskBase.run() 在构建工厂前调用 self._reload_config()（新钩子，默认空操作）
  UnifiedQueue._reload_config() 覆写为 load(CONFIG_PATH) + _resolve_task_classes()

验证策略（不依赖 ok GUI 运行时，不触碰真实 configs/task_config.json）：
  1. monkeypatch UnifiedQueue.CONFIG_PATH 指向临时 JSON
  2. init 消费 v1（count=10, level=1, tick=1.0）
  3. 磁盘改 v2 → 按 run() 的真实调用顺序执行
     _reload_config → _build_factories → _queue_global_params，断言工厂/全局参数 = v2
  4. 磁盘改 v3 → 重复 → 断言 = v3（证明不是只重载第一次）
  5. inspect 源码断言：run() 中 _reload_config 调用先于 _build_factories（防回归锚点）
  6. 坏 JSON → TaskConfigError（启动即失败的语义与 __init__ 一致）
  7. QueueTaskBase 默认 _reload_config 是空操作
"""
import inspect
import json
import logging
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---- 打桩：ok GUI 依赖在无头环境不可用 ----
fluent_stub = types.ModuleType("qfluentwidgets")
fluent_stub.FluentIcon = types.SimpleNamespace(SYNC="SYNC")
sys.modules.setdefault("qfluentwidgets", fluent_stub)

import src.sg.tasks.queue.UnifiedQueue as uq_mod
from src.sg.tasks.config_loader import TaskConfigError
from src.sg.tasks.queue.QueueTaskBase import QueueTaskBase

TMP = Path(tempfile.mkdtemp(prefix="uq_verify_"))
CFG = TMP / "task_config.json"


def write_cfg(count, level, tick=1.0, enabled=True):
    doc = {
        "version": 1,
        "task_types": [
            {
                "type": "Hunt Monster Troop",
                "name_prefix": "Hunt Monster狩猎巨兽",
                "description": "t",
                "defaults": {
                    "count": 6, "max_active": 2, "requires_march_queue": True,
                    "next_trigger_delay": 0.0, "one_shot": False, "cron": "",
                },
                "params": [
                    {"name": "Monster Level", "attr": "monster_level", "default": 1,
                     "widget": {"type": "drop_down", "options": ["1", "2", "3", "4", "5"]},
                     "desc": "d"},
                ],
            },
        ],
        "machines": [
            {
                "name": "m1", "enabled": True, "adb_serial": "",
                "apps": [
                    {
                        "package": "com.got.china", "enabled": True,
                        "users": [
                            {
                                "user_id": 0, "enabled": True,
                                "queue": {"tick_interval": tick, "continue_after_failure": True},
                                "tasks": [
                                    {"type": "Hunt Monster Troop", "enabled": enabled,
                                     "params": {"monster_level": level},
                                     "count": count, "max_active": 2,
                                     "next_trigger_delay": 0.0, "one_shot": False, "cron": ""},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    CFG.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def run_sequence(uq):
    """按 QueueTaskBase.run 的真实顺序执行配置相关步骤（run 控制流未改动）。"""
    uq._reload_config()
    uq._factories.clear()
    uq._build_factories()
    return uq._queue_global_params()


# ---- init：消费 v1 ----
write_cfg(count=10, level=1, tick=1.0)
uq_mod.CONFIG_PATH = CFG

uq = object.__new__(uq_mod.UnifiedQueue)          # 跳过 ok Task.__init__ 链
uq.name = "任务调度"
uq._factories = []
uq.logs = []
# ok Task 运行时属性桩
uq.logger = logging.getLogger("verify")
uq.log_info = lambda msg: uq.logs.append(str(msg))
uq.sleep = lambda s: None
uq.exit_is_set = lambda: False
uq.info_set = lambda *a, **k: None
uq._executor = object()
uq._app = object()
uq._paused = False
uq.scene = object()

# 手工执行 __init__ 里的配置逻辑
uq._task_config = uq_mod.load(uq_mod.CONFIG_PATH)
uq._resolve_task_classes()

assert uq._task_config["tasks"][0]["count"] == 10, "init 应读到 v1 count=10"
assert uq._task_config["tasks"][0]["params"]["monster_level"] == 1
init_task_count = uq._task_config["tasks"][0]["count"]

# ---- 盘改 v2 → run 序列 → 断言参数来自 v2 ----
write_cfg(count=3, level=4, tick=2.5)
gp = run_sequence(uq)
f = uq._factories[0]
assert f.count == 3, f"run 序列后工厂应使用 v2 count=3, 实际 {f.count}"
assert f.kwargs["monster_level"] == 4, f"应使用 v2 monster_level=4, 实际 {f.kwargs}"
assert abs(gp["tick_interval"] - 2.5) < 1e-9, f"tick_interval 应为 2.5, 实际 {gp}"
assert abs(f.next_trigger_delay - 0.0) < 1e-9

# ---- 盘改 v3 → 再跑一次 → 断言不是只重载第一次 ----
write_cfg(count=99, level=5, tick=0.5)
gp = run_sequence(uq)
f = uq._factories[0]
assert f.count == 99, f"第二次 run 序列应重读 count=99, 实际 {f.count}"
assert f.kwargs["monster_level"] == 5
assert abs(gp["tick_interval"] - 0.5) < 1e-9

# reload 日志存在（每次 run 重读的痕迹）
reload_logs = [x for x in uq.logs if "reloaded" in x]
assert len(reload_logs) == 2, f"应有 2 条 reloaded 日志, 实际 {len(reload_logs)}"

# ---- 防回归锚点：run() 中 _reload_config 调用先于 _build_factories ----
src = inspect.getsource(QueueTaskBase.run)
i_reload = src.find("self._reload_config()")
i_build = src.find("self._build_factories()")
assert i_reload != -1, "run() 缺少 _reload_config() 调用"
assert i_build != -1, "run() 缺少 _build_factories() 调用"
assert i_reload < i_build, "_reload_config 必须先于 _build_factories"

# ---- 坏 JSON：reload 抛 TaskConfigError（与 __init__ 失败语义一致）----
CFG.write_text("{broken json", encoding="utf-8")
try:
    uq._reload_config()
except TaskConfigError:
    pass
else:
    raise AssertionError("坏 JSON 应抛 TaskConfigError")

# ---- 基类默认钩子空操作 ----
base = QueueTaskBase.__new__(QueueTaskBase)
base._reload_config()  # 不应抛

shutil.rmtree(TMP, ignore_errors=True)
print("ALL ASSERTIONS PASSED")
print("验证点: init=v1 → 盘v2 run序=v2 → 盘v3 run序=v3 → run源码含钩子且先于build → 坏JSON抛TaskConfigError → 基类默认空操作")
