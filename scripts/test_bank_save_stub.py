# -*- coding: utf-8 -*-
"""
Bank 领域对象 + BankSaveTask 编排 行为桩测试（脱框架）。

覆盖：
  1. open_activity：入口点击成功 / 失败
  2. find_bank_head：首次区域内灰度命中 → 点击 head → 等到存期标签
  3. find_bank_head：滑动 N 次后命中 → last_scroll_count 记录
  4. find_bank_head：滑动 6 次耗尽仍未命中 → False（含区域缺失变体）
  5. find_bank_head：head 命中但存期标签未弹出 → False
  6. pick_save_days：bankdays=7 → 点 7day 标签；非法值 → False
  7. confirm_save：确认按钮点击
  8. run_interaction 编排：全成功 → SUCCESS
  9. run_interaction 编排：滑动耗尽 / 入口失败 → FAILED + last_error
 10. run：FAILED → False；SUCCESS → True；_run_once 兼容入口
"""
import sys
import types

sys.path.insert(0, ".")

from src.sg.element.overall.activity.bank import Bank
from src.sg.tasks.city.BankSaveTask import BankSaveTask
from src.scheduler.task_status import InteractionResult
from ok.feature.Box import Box

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), name)


AREA_BOX = Box(100, 200, 300, 100, name="global_sss_activity_head")
HEAD_BOX = Box(150, 250, 200, 60, name="global_sss_activity_head")


class FakeCore:
    """桩核心字段：Bank 与 BankSaveTask 编排共用的最小 task 接口。"""

    # 屏幕尺寸属性（与 TaskStub 的覆盖一致，供百分比换算读取）
    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    def __init__(self):
        self.logs = []
        self.find_results = []        # _find 按调用顺序返回
        self.safe_boxes = [AREA_BOX]  # _safe_box 按调用顺序返回
        self.wait_results = []        # _wait_element 按调用顺序返回
        self.wait_click_results = []  # _wait_and_click 按调用顺序返回
        self.clicks = []
        self.click_return = True
        self.swipes = []
        self.last_error = None
        self.find_calls = []
        self.step_calls = []
        self.scene_values = []

        # BankSaveTask 编排字段
        self.name = None
        self.description = None
        self.bankdays = 1
        self.max_recover_attempts = 0
        self.extra_config = types.SimpleNamespace()
        self.popup = types.SimpleNamespace(close_all=lambda: None)
        self.recovery = types.SimpleNamespace(
            _try_press_esc=lambda: None,
            full_recover=lambda: True,
            recover_wait=0,
        )
        self.bank = Bank(self)
        # 屏幕尺寸（百分比换算基准）：900x1600，与游戏截图一致
        self._width = 900
        self._height = 1600

    # ---- 日志 / 睡眠 ----
    def log_info(self, msg):
        self.logs.append(str(msg))

    def log_error(self, msg):
        self.logs.append(str(msg))

    def _sleep(self, s):
        pass

    # ---- 查找 / 点击 / 滑动 ----
    def _find(self, element, threshold=0.8, box=None, use_gray_scale=False):
        self.find_calls.append(
            (element.resource_id, threshold, box, use_gray_scale))
        if self.find_results:
            return self.find_results.pop(0)
        return None

    def _safe_box(self, element):
        if self.safe_boxes:
            return self.safe_boxes.pop(0)
        return None

    def _wait_element(self, element, timeout=None, threshold=0.8,
                      with_recovery=True, box=None, use_gray_scale=False):
        if self.wait_results:
            return self.wait_results.pop(0)
        return None

    def _wait_and_click(self, element, timeout=None, with_recovery=True,
                        box=None, name=None):
        ok = self.wait_click_results.pop(0) if self.wait_click_results \
            else False
        if ok:
            self.clicks.append(
                element.name if not isinstance(element, list)
                else element[0].name)
        return ok

    def click(self, box, name=None):
        if not self.click_return:
            return False
        self.clicks.append(getattr(box, "name", str(box)))
        return True

    def swipe_relative(self, fx, fy, tx, ty, duration=0.3, settle_time=1.0):
        self.swipes.append(("rel", fx, fy, tx, ty))

    # ---- 编排辅助 ----
    def _step(self, step_func, step_name, *args, **kwargs):
        self.step_calls.append(step_name)
        for attempt in range(self.max_recover_attempts + 1):
            if step_func():
                return True
            self.log_info(f"步骤 [{step_name}] 失败")
        return False

    def ensure_in_front(self):
        pass

    def _ensure_main_scene(self):
        scene_val = self.scene_values.pop(0) if self.scene_values else "city"
        return scene_val in ("city", "world_map")



class TaskStub(BankSaveTask, FakeCore):
    """BankSaveTask 编排方法 + FakeCore 桩字段的组合。"""

    def __init__(self):
        FakeCore.__init__(self)
        # 框架等待层默认值（真实 _wait_element / _wait_and_click 依赖）
        self.default_timeout = 0.1
        self.default_interval = 0.05
        # 屏幕尺寸（width/height property 的存储字段）
        self._width = 900
        self._height = 1600

    # 屏蔽框架设备依赖（真实 ensure_in_front 走 adb 设备管理）
    def ensure_in_front(self):
        pass

    def is_adb(self):
        return False

    # 屏幕尺寸属性（框架版走 executor.method，桩用固定值）
    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    # 覆盖框架依赖 executor 的查找方法（_safe_box / _find）
    def _safe_box(self, element):
        return FakeCore._safe_box(self, element)

    def _find(self, element, threshold=0.8, box=None, use_gray_scale=False):
        return FakeCore._find(
            self, element, threshold=threshold, box=box,
            use_gray_scale=use_gray_scale)

    # 覆盖框架 click（Box 分支走 executor.interaction）
    def click(self, box, name=None):
        return FakeCore.click(self, box, name=name)

    # 覆盖框架 swipe_relative / swipe（底层走 executor 帧宽高）
    def swipe_relative(self, from_x, from_y, to_x, to_y,
                       duration=0.3, settle_time=1.0):
        FakeCore.swipe_relative(
            self, from_x, from_y, to_x, to_y,
            duration=duration, settle_time=settle_time)

    def swipe(self, from_x, from_y, to_x, to_y, duration=0.5,
              after_sleep=0.1, settle_time=0):
        FakeCore.swipe(
            self, from_x, from_y, to_x, to_y,
            duration=duration, after_sleep=after_sleep,
            settle_time=settle_time)

    # 覆盖框架 _sleep（底层走 executor.sleep）
    def _sleep(self, seconds):
        pass

    # 完全覆盖 SGBaseTask._wait_and_click（框架版拿 _wait_element 的 Box 点击）
    def _wait_and_click(self, element, timeout=None, with_recovery=True,
                        box=None, name=None):
        return FakeCore._wait_and_click(
            self, element, timeout=timeout, with_recovery=with_recovery,
            box=box, name=name)

    # 完全覆盖 SGBaseTask._wait_element
    def _wait_element(self, element, timeout=None, interval=None,
                      threshold=0.8, with_recovery=True, box=None,
                      use_gray_scale=False):
        return FakeCore._wait_element(
            self, element, timeout=timeout, threshold=threshold,
            with_recovery=with_recovery, box=box,
            use_gray_scale=use_gray_scale)

    def _ensure_main_scene(self):
        scene_val = self.scene_values.pop(0) if self.scene_values else "city"
        return scene_val in ("city", "world_map")

    # 屏蔽 SGBaseTask/Task 框架日志（依赖 self.logger）
    def log_info(self, msg):
        self.logs.append(str(msg))

    def log_error(self, msg):
        self.logs.append(str(msg))

    def log_warning(self, msg):
        self.logs.append(str(msg))

    # 覆盖 SGBaseTask._step（MRO 中 SGBaseTask 优先于 FakeCore），
    # 委托 FakeCore 记录 step_calls
    def _step(self, step_func, step_name, *args, **kwargs):
        return FakeCore._step(
            self, step_func, step_name, *args, **kwargs)


def make_bank():
    task = FakeCore()
    return task, task.bank


def make_task():
    t = TaskStub.__new__(TaskStub)
    TaskStub.__init__(t)
    return t


# ============================================================
# 1. open_activity
# ============================================================
task, bank = make_bank()
task.wait_click_results = [True]
check("1 入口点击成功 → True", bank.open_activity() is True)
task, bank = make_bank()
task.wait_click_results = [False]
check("1 入口未找到 → False", bank.open_activity() is False)

# ============================================================
# 2. find_bank_head 首次区域内灰度命中
# ============================================================
task, bank = make_bank()
task.find_results = [HEAD_BOX]
task.wait_results = [True]
ok = bank.find_bank_head()
check("2 首次命中 → True", ok)
check("2 灰度匹配开启", task.find_calls
      and task.find_calls[0][3] is True)
check("2 区域 bbox 传入 _find", task.find_calls
      and task.find_calls[0][2] is AREA_BOX)
check("2 点击 head", "global_sss_activity_head" in task.clicks)
check("2 last_scroll_count=0", bank.last_scroll_count == 0)

# ============================================================
# 3. 滑动 2 次后命中
# ============================================================
task, bank = make_bank()
# 每轮 find 1 个 + swipe 1 个：2 轮滑动 + 第 3 轮 find 命中 = 5 个
task.safe_boxes = [AREA_BOX] * 5
task.find_results = [None, None, HEAD_BOX]
task.wait_results = [True]
ok = bank.find_bank_head()
check("3 滑动 2 次后命中 → True", ok and bank.last_scroll_count == 2)
check("3 滑动 2 次均走 swipe_relative",
      len(task.swipes) == 2
      and all(s[0] == "rel" for s in task.swipes))

# AREA_BOX = Box(100, 200, 300, 100)，屏 900x1600：
# 中心像素 (250, 250) → 相对 (0.2778, 0.15625)；跨度 300*0.25=75px → 0.08333
cx, cy = AREA_BOX.center()
rel_cx, rel_cy = cx / 900, cy / 1600
rel_span = AREA_BOX.width * 0.25 / 900
expect = ("rel", rel_cx + rel_span / 2, rel_cy,
          rel_cx - rel_span / 2, rel_cy)
check("3 滑动线 = 区域中心 y% + 跨度 = 区域宽 1/4 %屏",
      task.swipes[0] == expect and task.swipes[1] == expect)

# 3b. 区域 bbox 缺失 → 退回屏幕中心相对坐标；bbox 恢复后区域内 find 才命中
task, bank = make_bank()
# 调用序：每轮 find+swipe 各耗 1 个 safe_box。
# 前 3 轮 6 次全 None(rel 回退)；轮4 find 第7次 None(rel)、swipe 第8次 AREA_BOX(rel)；
# 轮5 find 第9次 AREA_BOX → find_results[0]=None → swipe 第10次 rel；
# 轮6 find 第11次 AREA_BOX → find_results[1]=HEAD_BOX 命中
task.safe_boxes = [None] * 7 + [AREA_BOX] * 5
task.find_results = [None, HEAD_BOX]
task.wait_results = [True]
ok = bank.find_bank_head()
check("3b 区域缺失回退屏幕中心后按区域滑动命中",
      ok and bank.last_scroll_count == 5
      and task.swipes[:3] == [("rel", 0.625, 0.5, 0.375, 0.5)] * 3
      and task.swipes[3] == expect and task.swipes[4] == expect)

# ============================================================
# 4. 滑动耗尽
# ============================================================
task, bank = make_bank()
# 6 轮滑动（find+swipe 各 1）+ 第 7 轮 find = 13 个
task.safe_boxes = [AREA_BOX] * 13
task.find_results = [None] * 7
ok = bank.find_bank_head()
check("4 滑动 6 次耗尽 → False", ok is False)
check("4 实际滑动 6 次", len(task.swipes) == 6)

# 4b. 区域 bbox 一直缺失也走滑动循环
task, bank = make_bank()
task.safe_boxes = [None] * 13
task.find_results = [None] * 7
ok = bank.find_bank_head()
check("4b 区域缺失同滑动耗尽 → False", ok is False and len(task.swipes) == 6)

# ============================================================
# 5. head 命中但存期标签未弹出
# ============================================================
task, bank = make_bank()
task.find_results = [HEAD_BOX]
task.wait_results = [False]
ok = bank.find_bank_head()
check("5 存期界面未弹出 → False", ok is False)

# ============================================================
# 6. pick_save_days
# ============================================================
task, bank = make_bank()
task.wait_click_results = [True]
bank.bankdays = 7
check("6 bankdays=7 → True", bank.pick_save_days() is True)
check("6 点击 7day 标签", "activity_tag_bank_save_7day" in task.clicks)
task, bank = make_bank()
bank.bankdays = 99
check("6 非法 bankdays → False", bank.pick_save_days() is False)

# ============================================================
# 7. confirm_save
# ============================================================
task, bank = make_bank()
task.wait_click_results = [True]
check("7 确认存储 → True", bank.confirm_save() is True)
check("7 点击确认按钮", "activity_tag_bank_button_save" in task.clicks)

# ============================================================
# 8. run_interaction 全成功
# ============================================================
task = make_task()
task.scene_values = ["city", "city"]  # 进入时 + 退出时
task.wait_click_results = [True, True, True]  # 入口/存期/确认
task.safe_boxes = [AREA_BOX]
task.find_results = [HEAD_BOX]
task.wait_results = [True]
result, wait_s = task.run_interaction()
check("8 全成功 → SUCCESS", result == InteractionResult.SUCCESS and wait_s == 0)
check("8 步骤顺序", task.step_calls == [
    "回到主界面", "进入超值活动", "查找银行入口",
    "选择存期", "确认存储", "退回主界面"])

# ============================================================
# 9. run_interaction 滑动耗尽 / 入口失败
# ============================================================
task = make_task()
task.scene_values = ["city"]
task.wait_click_results = [True]  # 入口
task.safe_boxes = [None] * 7
result, _ = task.run_interaction()
check("9 滑动耗尽 → FAILED", result == InteractionResult.FAILED)
check("9 last_error=滑动查找银行入口失败",
      task.last_error == "滑动查找银行入口失败")

task = make_task()
task.scene_values = ["city"]
task.wait_click_results = [False]
result, _ = task.run_interaction()
check("9b 入口失败 → FAILED", result == InteractionResult.FAILED)
check("9b last_error=进入超值活动失败",
      task.last_error == "进入超值活动失败")

# ============================================================
# 10. run / _run_once 入口
# ============================================================
task = make_task()
task.scene_values = ["city", "city"]
task.wait_click_results = [True, True, True]
task.safe_boxes = [AREA_BOX]
task.find_results = [HEAD_BOX]
task.wait_results = [True]
check("10 run SUCCESS → True", task.run() is True)

task = make_task()
task.scene_values = ["city"]
task.wait_click_results = [False]
check("10 run FAILED → False", task.run() is False)

task = make_task()
task.scene_values = ["city", "city"]
task.wait_click_results = [True, True, True]
task.safe_boxes = [AREA_BOX]
task.find_results = [HEAD_BOX]
task.wait_results = [True]
ok, ws = task._run_once()
check("10 _run_once → (True, 0)", ok is True and ws == 0)

print(f"\n==== {len(PASS)} PASS / {len(FAIL)} FAIL ====")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
