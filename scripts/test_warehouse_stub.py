# -*- coding: utf-8 -*-
"""ClaimWarehouseRewardTask 脱框架桩测试（FakeTask，不起游戏/ok 框架）。"""
import sys
import time
import types

sys.path.insert(0, '.')

from src.scheduler.task_status import InteractionResult
from src.sg.scene.elements import (
    CITY_TASK_LIST_WAREHOUSE_ENTRY,
    CITY_TASK_LIST_WAREHOUSE_NEXT_REWARD_TIME,
    GLOBAL_TASK_LIST_DOWN_EDGE,
    GLOBAL_BUTTON_TASKLIST,
)


class FakeBox:
    def __init__(self, name="box"):
        self.name = name


class FakeTask:
    """桩：模拟 SGBaseTask 的交互面，记录调用序列。"""

    def __init__(self, scenario):
        self.scenario = scenario          # 步骤行为剧本
        self.calls = []                   # 方法调用序列
        self.last_error = None
        self.max_recover_attempts = 2
        self.extra_config = types.SimpleNamespace()
        self.next_reward_time = None
        self.info = {}

        from src.sg.tasks.city.ClaimWarehouseRewardTask import ClaimWarehouseRewardTask
        import src.sg.tasks.city.ClaimWarehouseRewardTask as mod

        self.task = ClaimWarehouseRewardTask.__new__(ClaimWarehouseRewardTask)
        self.task.name = "Claim Warehouse Reward"
        self.task.description = "stub"
        self.task.extra_config = self.extra_config
        self.task.max_recover_attempts = 2
        self.task.next_reward_time = None
        self.task.last_error = None

        # 绑定桩方法
        self.task._ensure_main_scene = self._ensure_main_scene
        self.task._open_task_list = self._open_task_list
        self.task._scroll_find_warehouse_entry = self._scroll_find_warehouse_entry
        self.task._claim_via_entry = self._claim_via_entry
        self.task._enter_and_claim = self._enter_and_claim
        self.task._read_next_wait_seconds = self._read_next_wait_seconds
        self.task._parse_countdown_text = (
            lambda text, _t=self.task: ClaimWarehouseRewardTask._parse_countdown_text(_t, text)
        )
        self.task.log_info = lambda msg: self.calls.append(("log", msg))
        self.task.log_warning = lambda msg: self.calls.append(("warn", msg))
        self.task.log_error = lambda msg: self.calls.append(("err", msg))
        self._parse_countdown_text = (
            lambda text: self.task._parse_countdown_text(text)
        )
        self.task.log_warning = lambda msg: self.calls.append(("warn", msg))
        self.task.log_error = lambda msg: self.calls.append(("err", msg))
        self.task.info_set = lambda k, v: self.info.update({k: v})
        self.task.ensure_in_front = lambda: None
        self.task._step = lambda fn, name, *a, **k: fn()
        self.task.popup = types.SimpleNamespace(close_all=lambda: None)
        self.task.recovery = types.SimpleNamespace(_try_press_esc=lambda: None)

    # ---- 桩方法（按剧本走） ----
    def _ensure_main_scene(self):
        self.calls.append("main_scene")
        return True

    def _open_task_list(self):
        self.calls.append("open_list")
        return True

    def _scroll_find_warehouse_entry(self):
        self.calls.append("scroll_find")
        return self.scenario["scroll_found"]

    def _claim_via_entry(self):
        self.calls.append("claim_entry")
        return self.scenario["claim_ok"]

    def _enter_and_claim(self):
        if not self._open_task_list():
            return False
        if not self._scroll_find_warehouse_entry():
            return False
        return self._claim_via_entry()

    def _read_next_wait_seconds(self):
        self.calls.append("ocr_time")
        return self.scenario.get("wait_seconds")


def make():
    from src.sg.tasks.city.ClaimWarehouseRewardTask import ClaimWarehouseRewardTask
    return ClaimWarehouseRewardTask


T = make()

# ============ 1. 倒计时解析 ============
cases = [
    ("01:23:45", 3600 + 23 * 60 + 45),
    ("12:34", 12 * 60 + 34),
    ("5", 5),
    ("1小时23分45秒", 3600 + 23 * 60 + 45),
    ("1:30", 90),
    ("", None),
    (None, None),
    ("abc", None),
]
ft = FakeTask({})
for text, want in cases:
    got = ft._parse_countdown_text(text)
    assert got == want, f"parse({text!r}) = {got}, want {want}"
print(f"1. 倒计时解析 {len(cases)} 例全过")

# ============ 2. 成功路径：OCR 时间参与调度 ============
ft = FakeTask({"scroll_found": True, "claim_ok": True, "wait_seconds": 7200})
result, wait = ft.task.run_interaction()
assert result is InteractionResult.SUCCESS, f"result={result}"
assert wait == 7200, f"wait={wait}"
assert ft.task.next_reward_time is not None
assert ft.task.check_completed() is False  # 未到点
ft.task.next_reward_time = time.time() - 5
assert ft.task.check_completed() is True   # 到点放行
seq = [c for c in ft.calls if isinstance(c, str)]
assert seq == ["main_scene", "open_list", "scroll_find", "claim_entry", "ocr_time", "main_scene"], seq
print("2. 成功路径: (SUCCESS, 7200), 步骤顺序正确, check_completed 语义正确")

# ============ 3. 滑到底未见入口 → FAILED，不重试 ============
ft = FakeTask({"scroll_found": False})
result, wait = ft.task.run_interaction()
assert result is InteractionResult.FAILED
assert wait == 0
assert ft.task.last_error is not None
seq = [c for c in ft.calls if isinstance(c, str)]
assert seq == ["main_scene", "open_list", "scroll_find"], seq  # 无恢复重试、无 claim
print("3. 滑到底路径: FAILED 不重试, last_error 已设置")

# ============ 4. OCR 失败 → 兜底 3600s, 下限 60s ============
ft = FakeTask({"scroll_found": True, "claim_ok": True, "wait_seconds": None})
result, wait = ft.task.run_interaction()
assert wait == 3600.0, f"wait={wait}"

ft = FakeTask({"scroll_found": True, "claim_ok": True, "wait_seconds": 5})
result, wait = ft.task.run_interaction()
assert wait == 60.0, f"wait={wait}"
print("4. 兜底 3600s / 下限 60s 语义正确")

# ============ 5. 检索到入口但点击领取失败 → FAILED ============
ft = FakeTask({"scroll_found": True, "claim_ok": False})
result, wait = ft.task.run_interaction()
assert result is InteractionResult.FAILED
print("5. 领取点击失败路径: FAILED")

# ============ 6. _run_once / run 双入口 ============
ft = FakeTask({"scroll_found": True, "claim_ok": True, "wait_seconds": 60})
ok, w = ft.task._run_once()
assert ok is True and w == 60
ft2 = FakeTask({"scroll_found": False})
ok2 = ft2.task.run()
assert ok2 is False
print("6. _run_once / run 双入口语义正确")

print("\nALL 6 STUB TESTS PASS")
