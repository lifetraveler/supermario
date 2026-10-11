# -*- coding: utf-8 -*-
"""
troop_start / RallyConfig OCR 选队 行为桩测试（脱框架）。

覆盖：
  1. troop_start 默认类型归一化（None → HUNT）
  2. troop_start 字符串类型归一化（"gather" → GATHER；非法值 → False）
  3. troop_start 成功路径：apply + _hunt_expedition 顺序执行
  4. troop_start apply 失败短路（不点出征）
  5. OCR 选队-未命中 → 沿用默认，返回 True（本版主路径）
  6. OCR 选队-命中但开关关闭（预留分支）→ 不点击，返回 True
  7. OCR 选队-命中且开关开启 → 点击选中
  8. OCR 异常 → 沿用默认，返回 True
  9. 兼容回归：execute() 旧路径（team_element 特征选队）不受影响
"""
import sys
import types

sys.path.insert(0, ".")

from src.sg.element.overall.tasklist.worldtask.troop.rally import (
    ExpeditionType,
    Rally,
)
from src.sg.element.overall.tasklist.worldtask.troop.rally_config import (
    RallyConfig,
)
from ok.feature.Box import Box

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), name)


class FakeTask:
    """最小桩：只实现 Rally/RallyConfig 用到的 task 接口。"""

    def __init__(self):
        self.max_active = 1
        self.logs = []
        self.ocr_results = []
        self.ocr_error = None
        self.clicks = []
        self.click_return = True
        self.box_of_screen_calls = []
        self.apply_calls = []
        self.expedition_calls = 0
        self.expedition_return = True

        self.config = RallyConfig(self)

    def log_info(self, msg):
        self.logs.append(str(msg))

    def log_error(self, msg):
        self.logs.append(str(msg))

    def _sleep(self, s):
        pass

    def box_of_screen(self, x, y, to_x, to_y, *a, **k):
        self.box_of_screen_calls.append((x, y, to_x, to_y))
        return Box(x, y, to_x=to_x, to_y=to_y, name="fake_area")

    def ocr(self, box=None, **k):
        if self.ocr_error:
            raise self.ocr_error
        return self.ocr_results

    def click(self, box, name=None):
        self.clicks.append(getattr(box, "name", str(box)))
        return self.click_return

    # Rally.troop_start 内部依赖的桩
    def _apply_spy(self, team_group_name=None, team_hero=None):
        self.apply_calls.append((team_group_name, team_hero))
        return self.apply_return

    def _hunt_expedition_spy(self):
        self.expedition_calls += 1
        return self.expedition_return


def make_rally():
    task = FakeTask()
    rally = Rally.__new__(Rally)
    rally.task = task
    rally.config = task.config
    rally.timer = types.SimpleNamespace(read_march_time=lambda b: None)
    rally.last_expedition_type = None
    # 用桩替换真实 apply / _hunt_expedition，隔离点击细节
    rally.config.apply = task._apply_spy
    rally._hunt_expedition = task._hunt_expedition_spy
    task.apply_return = True
    return task, rally


# 1. 默认类型归一化
task, rally = make_rally()
ok = rally.troop_start(team_group_name="cj")
check("1 默认类型=None → HUNT",
      ok and rally.last_expedition_type == ExpeditionType.HUNT)
check("1 apply 收到 team_group_name='cj'",
      task.apply_calls == [("cj", None)])

# 2. 字符串类型归一化
task, rally = make_rally()
rally.troop_start(team_task_type="gather")
check("2 'gather' → GATHER",
      rally.last_expedition_type == ExpeditionType.GATHER)
task, rally = make_rally()
ok = rally.troop_start(team_task_type="nonsense")
check("2 非法类型 → False 且不 apply",
      not ok and task.apply_calls == [] and task.expedition_calls == 0)

# 3. 成功路径顺序：apply → expedition
task, rally = make_rally()
rally.troop_start(team_group_name="cj", team_task_type=ExpeditionType.GATHER)
check("3 apply 与 expedition 各一次",
      len(task.apply_calls) == 1 and task.expedition_calls == 1)

# 4. apply 失败短路
task, rally = make_rally()
task.apply_return = False
ok = rally.troop_start()
check("4 apply 失败 → False 且不点出征",
      not ok and task.expedition_calls == 0)

# 5. OCR 未命中 → 沿用默认，返回 True
task = FakeTask()
cfg = task.config
cfg.team_element = None
task.ocr_results = [Box(100, 150, 60, 60, name="dy"), Box(400, 150, 60, 60, name="xk1")]
ok = cfg._select_team_by_name("cj")
check("5 未命中 → True（沿用默认）且不点击",
      ok and task.clicks == [])

# 6. 命中但预留开关关闭 → 不点击，返回 True
task = FakeTask()
cfg = task.config
task.ocr_results = [Box(600, 150, 60, 60, name="cj")]
ok = cfg._select_team_by_name("cj")
check("6 命中+开关关 → True 不点击",
      ok and task.clicks == [])

# 7. 命中且开关开启 → 点击选中最左命中
task = FakeTask()
cfg = task.config
cfg.team_select_click_enabled = True
task.ocr_results = [
    Box(300, 150, 60, 60, name="xcj"),
    Box(600, 150, 60, 60, name="cj"),
]
ok = cfg._select_team_by_name("cj")
check("7 命中+开关开 → 点击 'cj'",
      ok and task.clicks == ["cj"])

# 8. OCR 异常 → True 沿用默认
task = FakeTask()
cfg = task.config
task.ocr_error = RuntimeError("no frame")
ok = cfg._select_team_by_name("cj")
check("8 OCR 异常 → True 沿用默认", ok)

# 9. 旧路径回归：team_element 特征选队不受影响
task = FakeTask()
cfg = task.config
cfg.team_element = types.SimpleNamespace(name="team_hunting")
called = {}
task._wait_and_click = lambda element, name=None: called.setdefault("e", element.name) and True or True
ok = cfg.select_team()
check("9 旧路径 team_element 走 _wait_and_click",
      ok and called.get("e") == "team_hunting")

print(f"\n==== {len(PASS)} PASS / {len(FAIL)} FAIL ====")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
