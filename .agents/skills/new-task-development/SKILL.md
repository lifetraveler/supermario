---
name: new-task-development
version: 1.0
description: |
  在已定型的自动化框架上开发新任务。用于：
  - 接入新的业务场景 / 活动任务
  - 定义新的领域对象
  - 编写聚合任务的调用步骤
  - 为新任务注册配置
  前置：框架架构稳定，见 Skill A。本 Skill 只指导业务开发。
trigger_keywords:
  - 新任务
  - 新增任务
  - 开发任务
  - 领域对象
  - 聚合任务
  - 任务注册
  - extra_config
  - 步骤编排
---

# Skill B：新任务开发（日常使用）

## 一、开发流程总览

```
1. 明确业务场景 → 决定任务的三个核心参数
2. 拆解流程 → 列出步骤序列
3. 识别可复用领域对象 → 决定哪些新写、哪些复用
4. 编写领域对象（Searcher / 交互领域）
5. 编写聚合任务类（组合 + 编排）
6. 编写注册文件
7. 挂载到 registrations/__init__.py
8. 测试：独立模式 → 队列模式
```

---

## 二、开工前必答的三个问题

| 问题 | 影响 |
|---|---|
| **是否占军队队列？** | `requires_march_queue` = True/False |
| **多久跑一次？** | `next_trigger_delay`（0 立即 / 86400 每日） |
| **能否并发？** | 能 → 设 `default_max_active`；不能 → 删掉该字段 + `count=1` |

### 默认值速查

| 任务类型 | requires_march_queue | next_trigger_delay |
|---|---|---|
| 巨兽 / 采集类 | `True` | `0` |
| 每日宝箱 / 宠物 | `False` | `86400` |
| 一次性任务 | `False` | `0`（count=1） |

---

## 三、流程拆解：从业务到步骤

### 3.1 步骤设计卡片

每个步骤必须能回答：

| 字段 | 说明 | 示例 |
|---|---|---|
| **步骤名** | 中文短语，用于日志 | "进入荒野" |
| **目标** | 识别什么 / 跳转到哪 | 检测到世界地图场景 |
| **资源 id** | `SceneElement` 的 `resource_id` | `BUTTON_GOTO_WORLDMAP` |
| **交互方式** | 点 / 滑 / OCR / 等待 / 按键 | `_wait_and_click` |
| **超时 / 阈值** | 步骤级超时 | `timeout=6.0` |
| **失败后** | 继续 / 重试 / 中止 | 用 `_step` 包，返回 False → FAILED |

### 3.2 常见步骤模板

```python
# 1. 进入某场景
def enter_target_scene(self) -> bool:
    scene = self.scene_detector.detect()
    self.log_info(f"当前场景: {scene.type.value}")
    if scene.type == SceneType.WORLD_MAP:
        return True
    if scene.type == SceneType.CITY:
        if not self._wait_and_click(BUTTON_GOTO_WORLDMAP,
                                    name="进入荒野", timeout=6.0):
            return False
        return self._wait_scene(SceneType.WORLD_MAP, timeout=10.0)
    return False

# 2. 打开某面板
def open_panel(self) -> bool:
    if not self._wait_and_click(BUTTON_PANEL, name="面板入口", timeout=6.0):
        return False
    self._sleep(0.5)
    return True

# 3. 面板内切换标签
def switch_tab(self) -> bool:
    return self._wait_and_click(PANEL_TAB_OTHER, name="其他标签", timeout=6.0)

# 4. 选中目标（多候选）
def select_target(self) -> bool:
    return self._wait_and_click(
        [TARGET_A, TARGET_B], name="目标", timeout=6.0,
    )

# 5. 使用道具
def use_item(self) -> bool:
    if not self._wait_and_click(ITEM, name="道具", timeout=6.0):
        return False
    self._sleep(0.5)
    return self._wait_and_click(ITEM_BUTTON_USE, name="使用按钮", timeout=6.0)

# 6. 特征失败 → bbox 兜底（仅当游戏强制居中）
def click_by_feature_or_bbox(self) -> bool:
    if self._wait_and_click([ELEM_A, ELEM_B], name="目标", timeout=6.0):
        self._sleep(0.8)
        return True
    self.log_info("特征未匹配，改用 bbox 坐标点击")
    box = self.get_box_by_name(ELEM_A.resource_id)
    if not box:
        self.log_error("未找到 bbox")
        return False
    self._click_box(box)
    self._sleep(0.8)
    return True
```

---

## 四、领域对象的建立

### 4.1 什么时候写新领域对象

| 信号 | 是否抽领域对象 |
|---|---|
| 有内部状态（队列快照、上次耗时） | ✅ 抽 |
| 与多个任务复用 | ✅ 抽 |
| 逻辑超过 5 步 | ✅ 抽 |
| 纯操作 3 行能写完 | ❌ 留在 Task |

### 4.2 领域对象模板

```python
class XxxDomain:
    """
    职责：一句话描述。
    不负责：一句话描述。
    """
    def __init__(self, task):
        self.task = task

        # 参数
        self.timeout = 6.0

        # 用户注入字段
        self.some_element = None

        # 内部状态
        self.last_result = None

    def some_action(self) -> bool:
        return self.task._wait_and_click(
            self.some_element, name="...", timeout=self.timeout,
        )
```

**约束**：

- 不继承框架类。
- 只通过 `task._find / click / ocr / _wait_* / log_* / _sleep` 做事。
- 不 import 其他领域对象。
- 有状态才独立成对象。

### 4.3 搜索类任务：继承 BaseResourceSearcher

```python
class XxxSearcher(BaseResourceSearcher):
    def __init__(self, task):
        super().__init__(task)
        # 滑动查找配置（可选）
        self.scroll_max_attempts = 8
        self.scroll_from_x = 0.5
        self.scroll_to_x = 0.7
        self.scroll_settle_time = 1.0
        self.right_edge_elements = [WORLD_ICON_RESOURCE_STONE]

    def get_resource_element(self):
        return GIANT_BEAST

    def get_level_elements(self):
        return self.level_elements

    def select_resource(self) -> bool:
        """覆写父类：找不到时滑动继续找"""
        ...
```

---

## 五、聚合任务类编写

### 5.1 骨架

```python
import types
from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult
from src.sg.helper.recovery import RecoveryHelper
from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.tasklist.worldtask.troop.rally import Rally
from src.sg.scene.elements import TEAM_HUNTING
from src.sg.scene.scene_type import SceneType


class XxxTask(SGBaseTask):
    """
    一句话职责。

    负责：业务编排。
    不负责：循环次数、体力下限、弹窗细节、恢复细节。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Xxx"
        self.description = "..."

        # 队列注入字段（赋默认值，不写死配置）
        self.min_stamina = 0
        self.auto_recall = False
        self.xxx_level = None

        # 个性化参数容器
        self.extra_config = types.SimpleNamespace()

        # 恢复配置
        self.max_recover_attempts = 2
        self.recover_interval = 3.0

        # 领域对象统一在 __init__ 组合
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)
        self.rally = Rally(self)
        self.rally.config.team_element = TEAM_HUNTING
        self.searcher = XxxSearcher(self)

    def _step(self, step_func, step_name) -> bool:
        # 逐字复制标准实现，勿各写各的
        for attempt in range(self.max_recover_attempts + 1):
            if attempt > 0:
                self.log_info(f"步骤 [{step_name}] 第 {attempt} 次重试前恢复")
                if not self.recovery.full_recover():
                    self.log_info(f"步骤 [{step_name}] 无法恢复，停止重试")
                    return False
                self._sleep(self.recovery.recover_wait)
            if step_func():
                return True
            self.log_info(f"步骤 [{step_name}] 失败")
        return False

    # 业务步骤
    def enter_target_scene(self) -> bool: ...
    def select_target(self) -> bool: ...
    def execute(self) -> bool: ...

    def run_interaction(self):
        self.log_info("========== 开始 XXX ==========")
        self.ensure_in_front()
        self.popup.close_all()

        if not self.rally.has_enough_stamina(self.min_stamina):
            self.last_error = f"体力低于下限 {self.min_stamina}"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        ready, wait_seconds = self.rally.prepare()
        if not ready:
            if wait_seconds > 0:
                return (InteractionResult.RETRY, wait_seconds)
            self.last_error = "队列不可用"
            return (InteractionResult.FAILED, 0)

        if not self._step(self.enter_target_scene, "进入场景"):
            self.last_error = "进入场景失败"
            return (InteractionResult.FAILED, 0)

        if not self._step(self.select_target, "选择目标"):
            self.last_error = "选择目标失败"
            return (InteractionResult.FAILED, 0)

        if not self._step(self.execute, "执行"):
            self.last_error = "执行失败"
            return (InteractionResult.FAILED, 0)

        wait_seconds = self.rally.estimate_wait()
        self.log_info(f"========== XXX 完成，预计 {wait_seconds:.1f}s ==========")
        return (InteractionResult.SUCCESS, wait_seconds)

    def check_completed(self) -> bool:
        return True

    def _run_once(self):
        result, wait_seconds = self.run_interaction()
        return (result == InteractionResult.SUCCESS, wait_seconds)

    def run(self):
        result, wait_seconds = self.run_interaction()
        if result == InteractionResult.FAILED:
            self.log_error(f"失败: {self.last_error}")
            return False
        if result == InteractionResult.RETRY:
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False
        if wait_seconds > 0:
            self._sleep(wait_seconds)
        return True
```

### 5.2 个性化参数的下传

新任务需要个性化参数时：

```python
# 1. __init__ 里初始化容器
self.extra_config = types.SimpleNamespace()

# 2. factory 自动注入（无需改 factory）
#    for k, v in kwargs.items():
#        setattr(task, k, v)
#        setattr(task.extra_config, k, v)

# 3. 领域方法读取
def run_interaction(self, extra_config=None):
    ec = extra_config or getattr(self.task, "extra_config", None)
    rally_wait = getattr(ec, "rally_config_wait", self.rally_default_wait)
    auto_recall = getattr(ec, "auto_recall", False)
```

### 5.3 调用步骤编排要点

- **顺序**：前台 → 清弹窗 → 前置检查 → 场景/面板 → 选择 → 执行 → 估算等待。
- **每个业务步骤用 `_step` 包**，返回 False 立刻 `FAILED`。
- **`RETRY` 只用于"暂时不可执行"**（队列繁忙、CD 中），不用于真失败。
- **`FAILED` 前设置 `self.last_error`**，方便日志定位。

---

## 六、注册文件编写

### 6.1 模板

```python
from src.sg.tasks.queue.GenericQueueTask import GenericQueueTask
from src.sg.tasks.world.XxxTask import XxxTask

GenericQueueTask.register_task_type(
    key="Xxx Troop",                    # 唯一标识
    task_class=XxxTask,
    name_prefix="Xxx",                  # 队列显示名前缀
    default_count=6,
    default_max_active=2,               # 不可并行时删掉这一行
    default_requires_march_queue=True,
    default_next_trigger_delay=0.0,
    default_kwargs={                    # 不在 GUI 暴露的固定参数
        "auto_recall": False,
        "min_stamina": 0,
    },
    description="Xxx。...",
    extra_config={                      # GUI 可见配置项
        "Monster Level": {
            "attr": "monster_level",    # ← 必须！
            "default": 1,
            "desc": "级别。",
            "type": {
                "type": "drop_down",
                "options": ["1", "2", "3", "4", "5"],
            },
        },
        "Auto Recall": {
            "attr": "auto_recall",      # ← 必须！
            "default": False,
            "desc": "满员时是否召回。",
        },
        "Min Stamina": {
            "attr": "min_stamina",      # ← 必须！
            "default": 0,
            "desc": "体力下限，0 表示不检查。",
        },
    },
)
```

### 6.2 决策表

| 情况 | 调整 |
|---|---|
| 不能并行 | 删 `default_max_active`，`default_count=1` |
| 有级别 / 子类型 | `extra_config` 加 `type: drop_down` |
| 无级别 | **不加** `monster_level` 相关字段 |
| 不占队列 | `default_requires_march_queue=False` |
| 每日一次 | `default_next_trigger_delay=86400` |

### 6.3 GUI 支持的类型

```python
"foo": True                              # bool → switch
"foo": 5                                 # int → spin box
"foo": 1.5                               # float → double spin
"foo": "text"                            # str → line edit
"foo": ["a", "b"]                        # list → editable list

{"type": "drop_down", "options": [...]}
{"type": "multi_selection", "options": [...]}
{"type": "text_edit"}
```

### 6.4 挂载

```python
# src/sg/tasks/registrations/__init__.py
from src.sg.tasks.registrations import xxx_task
```

---

## 七、API 速查（Task 基类方法）

### 7.1 元素识别

| 方法 | 用途 | 返回 |
|---|---|---|
| `self.find_one(feature_name, threshold)` | 找单个特征（字符串） | Box / None |
| `self.find_feature(...)` | 找多个特征 | list |
| `self._find(element, threshold=0.8, box=None)` | 按 SceneElement 查找 | Box / None |
| `self._wait_element(element, timeout, interval, threshold, with_recovery, box)` | 等待单/多元素 | Box / None |
| `self.get_box_by_name(resource_id)` | 按名字取 bbox | Box / None |

### 7.2 点击 / 滑动 / 按键

| 方法 | 用途 |
|---|---|
| `self.click(box, name)` | 点 Box |
| `self.click_relative(x, y, name)` | 相对坐标点击 |
| `self.click_box(box, ...)` / `self._click_box(box)` | 点 Box |
| `self._wait_and_click(element, name, timeout, ...)` | 等待并点击单/多元素 |
| `self.swipe_relative(from_x, from_y, to_x, to_y, duration, settle_time)` | 相对滑动 |
| `self.send_key(key)` | 发送按键 |

### 7.3 OCR

| 方法 | 用途 |
|---|---|
| `self.ocr(x, y, to_x, to_y, box, match, threshold, ...)` | 识别文字 |
| `self.wait_ocr(...)` | 等待 OCR 结果 |
| `self.wait_click_ocr(...)` | 等待并点击 OCR 结果 |

### 7.4 场景 / 等待

| 方法 | 用途 |
|---|---|
| `self.scene_detector.detect()` | 检测当前场景，返回 Scene |
| `self._wait_scene(scene_type, timeout, interval)` | 等待场景切换 |
| `self.wait_until(condition, time_out, ...)` | 等待条件成立 |

### 7.5 通用 / 日志 / 状态

| 方法 | 用途 |
|---|---|
| `self.ensure_in_front()` | 确保游戏前台 |
| `self._sleep(seconds)` | 可被 exit_event 中断的睡眠 |
| `self.sleep(seconds)` | 同 `_sleep`，长睡眠用 `_sleep` |
| `self.log_info / log_warning / log_error` | 日志 |
| `self.info_set(key, value)` / `info_incr(key, inc)` | UI 信息 |
| `self.last_error` | 失败原因（返回 FAILED 前设置） |

### 7.6 什么时候用哪个

| 场景 | 用 |
|---|---|
| 点按钮，元素出现时间不确定 | `_wait_and_click` |
| 点按钮，多个候选（如两个恐狼特征） | `_wait_and_click([A, B], ...)` |
| 元素已确定存在，直接点 | `click(box, name)` |
| 等页面整体切换 | `_wait_scene` |
| 等文字出现 | `wait_ocr` |
| 长等待（行军几分钟） | `_sleep` |
| 特征匹配失败，游戏强制居中 | `get_box_by_name` + `_click_box`（业务特例） |

---

## 八、参考实例

### 8.1 HuntMonsterTask（搜索型）

- **特点**：使用 `MonsterSearcher` 做"搜索资源 → 选巨兽 → 选级别 → 确认"。
- **流程**：前台 → 清弹窗 → 体力检查 → 进荒野 → 队列准备 → 搜索 → 集结 → 估算等待。
- **注入字段**：`min_stamina` / `auto_recall` / `monster_level`。
- **注册**：`default_max_active=2`（可并行），`extra_config` 含 drop_down 级别。

### 8.2 HuntScareWolfTask（道具触发型）

- **与巨兽的差异**：
  - 不用 `MonsterSearcher`，改用道具触发恐狼刷新。
  - 新增 `_ensure_main_scene()`：按 ESC 兜底回主界面（幂等）。
  - `_use_scare_wolf_claw()`：背包 → 其他标签 → 点狼爪 → 使用 → 等世界资源界面。
  - `_click_scare_wolf()`：多候选特征匹配；失败则按 bbox 直接点。
  - `Rally` 完全复用。
- **注册**：不可并行，删 `default_max_active`，`count=1`，无级别配置。

```python
def _ensure_main_scene(self) -> bool:
    for _ in range(5):
        scene = self.scene_detector.detect()
        self.log_info(f"当前场景: {scene.type.value}")
        if scene.type in (SceneType.CITY, SceneType.WORLD_MAP):
            return True
        self.log_info("不在主界面，按 ESC 返回")
        self._press_esc()
        self._sleep(1.0)
    self.log_error("无法返回主界面")
    return False

def _use_scare_wolf_claw(self) -> bool:
    if not self._wait_and_click(BUTTON_BACKPACK, name="背包按钮", timeout=6.0):
        return False
    self._sleep(0.5)
    if not self._wait_and_click(BUTTON_BACKPACK_TAB_OTHER,
                                name="背包-其他标签", timeout=6.0):
        return False
    self._sleep(0.5)
    if not self._wait_and_click(ITEM_SCARE_WOLF_CLAW,
                                name="狼爪道具", timeout=6.0):
        return False
    self._sleep(0.5)
    if not self._wait_and_click(ITEM_SCARE_WOLF_CLAW_BUTTON_USE,
                                name="狼爪-使用按钮", timeout=6.0):
        return False
    self._sleep(1.0)
    if not self._wait_scene(SceneType.WORLD_MAP, timeout=10.0, interval=0.5):
        self.log_error("使用狼爪后未跳转到世界资源界面")
        return False
    return True

def _click_scare_wolf(self) -> bool:
    if self._wait_and_click(
        [WORLD_RESOURCE_SCARE_WOLF, WORLD_RESOURCE_SCARE_WOLF_1],
        name="恐狼", timeout=6.0,
    ):
        self._sleep(0.8)
        return True
    self.log_info("未匹配到恐狼特征，改用 bbox 坐标直接点击")
    box = self.get_box_by_name(WORLD_RESOURCE_SCARE_WOLF.resource_id)
    if not box:
        self.log_error("未找到恐狼 bbox 坐标")
        return False
    self._click_box(box)
    self._sleep(0.8)
    return True
```

---

## 九、新任务开发检查清单

- [ ] 明确 `requires_march_queue` / `next_trigger_delay` / 是否可并行
- [ ] 拆解流程为步骤列表，每步有"资源 id + 交互方式"
- [ ] 判断哪些领域对象可复用，哪些新写
- [ ] 领域对象只持 `task` 引用，通过 `task._find / click / ocr` 做事
- [ ] 领域对象不互相依赖，不反向 import 聚合任务
- [ ] Task 类 `__init__` 组合领域对象，调 `super()`
- [ ] `self.name` / `self.description` 已设置
- [ ] 队列注入字段在 `__init__` 赋默认值
- [ ] `self.extra_config = types.SimpleNamespace()` 已初始化
- [ ] `_step` 逐字复制标准实现
- [ ] 业务步骤全部用 `_step` 包裹
- [ ] `run_interaction` 返回 `(InteractionResult, wait_seconds)`
- [ ] 前置检查失败 `FAILED`，可恢复情况 `RETRY`
- [ ] `_run_once` / `run` 两个入口已保留
- [ ] 多元素等待用列表传入，不串行两次调用
- [ ] bbox 兜底逻辑放本文件，不动基类
- [ ] 注册文件 `extra_config` 每项有 `attr`
- [ ] `default_kwargs` 与 Task 注入字段一一对应
- [ ] 不可并行时删 `default_max_active`，`count=1`
- [ ] `registrations/__init__.py` 已 import
- [ ] 先测独立模式 `run()`，再测队列模式

---

## 十、给 AI 的指令模板

```text
参考 Skill B：新任务开发。

需求：<一句话描述新任务>

新任务类路径：src/sg/tasks/<category>/<XxxTask>.py
注册文件路径：src/sg/tasks/registrations/<xxx>.py

已定：
- 是否占队列：<是/否>
- 触发延迟：<0 / 86400 / ...>
- 是否可并行：<是 → max_active=N / 否 → count=1 无 max_active>
- 队列注入字段：<列出>
- 可复用领域对象：<Popup / Recovery / Rally / Searcher / ...>
- 需要新写的领域对象：<列出及其职责>

流程步骤（每步：目标 / 资源 id / 交互方式 / 超时）：
1. <步骤 1>
2. <步骤 2>
3. ...

约束：
1. Task 只做业务编排，继承 SGBaseTask
2. 业务步骤全部用 _step 包裹
3. run_interaction 返回 (InteractionResult, wait_seconds)
4. 保留 _run_once / run 两个入口
5. 多元素等待用列表传入 _wait_and_click
6. 特例逻辑（bbox 兜底等）放本文件
7. 注册文件 extra_config 每项必须带 attr
8. default_kwargs 与 Task 注入字段一致
9. 领域对象不反向 import 聚合任务类
```
