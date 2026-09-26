---
name: automation-framework-arch
version: 1.0
description: |
  自动化框架的稳定架构设计记录。用于：
  - 修改队列调度 / 参数传递 / 注册机制前读取历史设计意图
  - 新增任务时理解框架提供的契约
  - 防止 AI 在架构未变更的情况下臆造新机制
  本 Skill 不指导具体业务步骤编写，业务开发看 Skill B。
trigger_keywords:
  - 架构
  - 分层
  - TaskQueue
  - 状态机
  - extra_config
  - 配置注入
  - 注册机制
  - 恢复机制
  - 调度
---

# Skill A：自动化框架架构基础（稳定层）

## 一、分层架构

```
┌─────────────────────────────────────────┐
│ Caller / GUI / 配置                       │
└────────────────────┬────────────────────┘
                     ▼
┌─────────────────────────────────────────┐
│ TaskQueue / GenericQueueTask             │
│ 循环、次数、并发、trigger_time、配置注入   │
└────────────────────┬────────────────────┘
                     ▼
┌─────────────────────────────────────────┐
│ XxxTask（SGBaseTask 子类）                │
│ 业务编排：组合领域对象、_step、run_interaction │
└────────────────────┬────────────────────┘
                     ▼
┌─────────────────────────────────────────┐
│ 领域对象                                  │
│ Rally / Searcher / Popup / Recovery / ... │
└────────────────────┬────────────────────┘
                     ▼
┌─────────────────────────────────────────┐
│ Element / SceneElement / SceneType       │
└─────────────────────────────────────────┘
```

**设计意图**：

| 层 | 只做 | 绝不涉及 |
|---|---|---|
| 队列层 | 状态流转、次数、并发、trigger 时间 | 任何业务名词 |
| 任务层 | 编排步骤、组合领域对象、_step 重试 | 具体识别 / 点击 / 循环 |
| 领域层 | 识别、点击、解析、状态维护 | 认识其他领域对象 |
| 元素层 | UI 特征、场景定义 | 任何流程逻辑 |

---

## 二、队列调度模型

### 2.1 状态机

```
SCHEDULED --(trigger_time ≤ now + 有名额)--> PENDING
PENDING   --(被 pick，屏幕空闲)--> RUNNING
RUNNING   --(SUCCESS)--> IN_PROGRESS --(时间到)--> DONE
          --(RETRY)--> PENDING
          --(FAILED)--> FAILED
IN_PROGRESS 到点后 check_completed()，不通过 → PENDING（延后 5s）
```

### 2.2 关键设计

- **trigger_time 是硬约束**，不是排序因素。未到绝不执行。
- **FIFO**：已到时间的任务按插入顺序执行，插入顺序天然等于触发顺序。
- **两类任务**：
  - `requires_march_queue=True`（巨兽 / 采集）：初始建 `min(max_active, count)`，pick 时查 `IN_PROGRESS < max_active`。
  - `requires_march_queue=False`（宝箱 / 宠物）：初始建 1 个，不查上限。
- **初始填充用 `factory.initial_filled`**，绝不用 `submitted`（会被 `_create_follower` 递增）。
- **后续补建**：SUCCESS 后立即建下一个，`trigger_time = 完成时间 + next_trigger_delay`。

### 2.3 InteractionResult 语义

| 值 | 含义 | 队列行为 |
|---|---|---|
| `SUCCESS` | 交互完成 | 按 `wait_seconds` 进入 IN_PROGRESS |
| `RETRY` | 暂不可执行（队列繁忙等） | 按 `wait_seconds` 后回到 PENDING |
| `FAILED` | 真失败（体力不足、无法恢复） | 不再重试 |

---

## 三、Task 编排契约

Task 只做三件事：

1. 在 `__init__` 组合领域对象。
2. 在 `run_interaction` 按顺序调用步骤。
3. 用 `_step(func, name)` 处理失败恢复。

**契约**：

- `run_interaction() -> (InteractionResult, wait_seconds)`
- `check_completed() -> bool`，默认 True
- 保留 `_run_once()` 兼容旧队列
- 保留 `run()` 独立运行入口
- 业务步骤必须全部用 `_step` 包裹，不允许裸调用

---

## 四、个性化参数传递

### 4.1 两个 extra_config

| 名称 | 层级 | 作用 |
|---|---|---|
| 注册文件里的 `extra_config` | GUI 声明 | 用户可见配置项，每项必须带 `attr` |
| `task.extra_config` | 运行时 | 个性化参数容器，`types.SimpleNamespace()` |

### 4.2 数据流

```
kwargs
  → factory.create(executor, app, scene, **kwargs)
  → setattr(task, k, v)              # 注入 Task 本体，防 __slots__
  → setattr(task.extra_config, k, v) # 注入个性化容器
  → 领域方法 getattr(extra_config, 'name', self.xxx_default)
```

### 4.3 五条硬性规则

1. `extra_config` 必须初始化为 `types.SimpleNamespace()`，禁止 `{}` / `None`。
2. 读取一律 `getattr(obj, name, default)`，禁止 `obj.name`。
3. `setattr(task, k, v)` 必须 try-except `AttributeError`（防 `__slots__`）。
4. `setattr(task.extra_config, k, v)` 不加 try-except（失败即架构错误）。
5. 领域任务禁止反向 import 聚合任务类（防循环依赖）。

---

## 五、配置注入

### 5.1 注册到实例的路径

```
register_task_type(extra_config={
    "Monster Level": {
        "attr": "monster_level",  # ← 必须！实际属性名
        "default": 1,
        ...
    }
})
  → GUI 显示 "Monster Level"
  → 用户改值
  → 通过 kwargs 传入 factory.create
  → setattr(task, "monster_level", value)
```

### 5.2 关键约束

- 字典的 key 是 GUI 显示名，`attr` 才是实际注入的属性名。**缺 `attr` 会注入到错误属性上**。
- `default_kwargs` 的 key 必须与 Task `__init__` 注入字段一一对应。
- `default_kwargs` 默认值应与 Task 内默认值一致。

---

## 六、恢复机制

### 6.1 两个级别

| 级别 | 方法 | 动作 | 触发点 |
|---|---|---|---|
| 轻恢复 | `light_recover()` | 关已知弹窗 + ESC（带冷却） | `_wait_element` 等待期间 |
| 重恢复 | `full_recover()` | 关弹窗 → ESC → OCR → 点空白 | `_step` 步骤失败后 |

### 6.2 松耦合设计

- Task 没注入 `self.recovery`，等待行为与原来一致。
- Task 注入了，`_wait_element` 自动轻恢复（通过 `getattr(self, "recovery", None)`）。
- 恢复过程若界面不可信，抛 `TaskRestartRequested` 给队列决定重跑或跳过。

---

## 七、注册机制

### 7.1 设计意图

- `TaskQueue` 不认识任何业务名词。
- 业务方只负责 `register_task_type` 注册。
- 队列按 `TASK_REGISTRY` 调度。

### 7.2 注册文件路径

`src/sg/tasks/registrations/{任务名}.py`

### 7.3 必须项

- `key`（唯一）、`task_class`、`name_prefix`
- `default_count` / `default_requires_march_queue` / `default_next_trigger_delay`
- `extra_config` 每项必须有 `attr`
- 在 `registrations/__init__.py` import 使其生效

---

## 八、架构变更记录（追加式）

> 未来若修改以上任何一条设计，必须在此追加：

| 日期 | 变更 | 兼容性 |
|---|---|---|
| — | 初版：SimpleNamespace + getattr + factory 注入 | — |
| — | `_wait_element` / `_wait_and_click` 支持单/多元素，共享 timeout 并行轮询 | 单元素调用完全兼容 |

**变更前必问**：

1. 这是所有任务都会用到的通用能力吗？是才改基类。
2. 单元素老调用是否 100% 兼容？
3. 是否引入新的默认行为？必须向后兼容。
4. 业务特例是否被误抽上来？特例留在任务文件。
