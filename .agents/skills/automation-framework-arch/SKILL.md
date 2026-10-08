---
name: automation-framework-arch
version: 1.0
description: |
  自动化框架的架构设计与开发。用于：
  - 修改队列调度 / 参数传递 / 注册机制前读取历史设计意图
  - 新增任务时理解框架提供的契约
  - 防止 AI 在架构未变更的情况下臆造新机制
  本 Skill 不指导具体业务步骤编写，业务开发看 Skill B。
trigger_keywords:
  - 架构任务
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

```
┌─────────────────────────────────────────┐
│ Caller / GUI / 配置                       │
└────────────────────┬────────────────────┘
                     ▼
┌─────────────────────────────────────────┐
│ TaskQueue / TaskFactory                   │
│ 循环、次数、并发、trigger_time            │
└────────────────────┬────────────────────┘
                     ▼
┌─────────────────────────────────────────┐
│ UnifiedQueue（QueueTaskBase 子类）        │
│ 消费 task_config.json → TaskFactory       │
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

### 4.1 定义与实例

| 名称 | 位置 | 作用 |
|---|---|---|
| `task_types[].params[]` | task_config.json 定义段 | 用户可见配置项声明（name/attr/default/widget/desc） |
| `machines[].apps[].users[].tasks[]` | task_config.json 实例段 | 每设备/应用/分身实际执行的覆盖值 |
| `task.extra_config` | 运行时 | 个性化参数容器，`types.SimpleNamespace()` |

### 4.2 数据流

```
configs/task_config.json
  task_types[].params[]: {"name": "Monster Level", "attr": "monster_level",
                          "default": 1, "widget": {...}, "desc": "..."}
  machines[].apps[].users[].tasks[]: {"type": "...", "enabled": true,
                                      "params": {"monster_level": 1}, ...}
  → 加载器覆盖链：tasks 条目字段 → task_types[].defaults → params[].default
  → UnifiedQueue._build_factories() 生成 TaskFactory(kwargs=params)
  → factory.create: setattr(task, k, v) + setattr(task.extra_config, k, v)
  → 领域方法 getattr(extra_config, 'name', self.xxx_default)
```

### 4.3 五条硬性规则

1. `extra_config` 必须初始化为 `types.SimpleNamespace()`，禁止 `{}` / `None`。
2. 读取一律 `getattr(obj, name, default)`，禁止 `obj.name`。
3. `setattr(task, k, v)` 必须 try-except `AttributeError`（防 `__slots__`）。
4. `setattr(task.extra_config, k, v)` 不加 try-except（失败即架构错误）。
5. 领域任务禁止反向 import 聚合任务类（防循环依赖）。

### 4.4 关键约束

- `params[].name` 是显示名，`attr` 才是实际注入的属性名。**缺 `attr` 会注入到错误属性上**。
- params 的 key 必须与 Task `__init__` 注入字段一一对应，默认值应与 Task 内默认值一致。
- widget 显式声明渲染方式：`switch / number / text / drop_down{options} / multi_drop_down{options}`。
- 实例条目只写用户改过的值，缺省字段运行时回退定义段。

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

## 七、注册机制（JSON 配置驱动）

### 7.1 设计意图

- `TaskQueue` 不认识任何业务名词。
- 业务定义全部在 `configs/task_config.json`（webui 直接读写控制参数）。
- 代码侧只保留 `task_type_map.TASK_TYPE_MAP`（type 短 id → 任务类）；
  JSON 里禁止写 import 路径 / 类名。

### 7.2 配置结构（单文件双段）

```
configs/task_config.json
├─ version: 1
├─ task_types[]   定义段（等价于旧 registrations/reg_*.py）
│    {type, name_prefix, description,
│     defaults{count,max_active,requires_march_queue,
│               next_trigger_delay,one_shot,cron},
│     params[{name,attr,default,widget,desc}]}
└─ machines[]    实例段（多设备/多应用/多分身 schema）
     {name, enabled, adb_serial,      ← adb -s 预留，未消费
      apps[{package, enabled,
            users[{user_id, enabled,  ← adb --user 预留，未消费
                   queue{tick_interval,continue_after_failure},
                   tasks[{type,enabled,params,...覆盖字段}]}]}]}
```

### 7.3 新增一种任务的三个动作

1. 写任务类（SGBaseTask 子类）。
2. `task_config.json` 的 `task_types[]` 加定义段。
3. `task_type_map.TASK_TYPE_MAP` 加一行映射。

是否启用、跑几次由 `machines` 段的 `tasks[]` 条目决定。

### 7.4 加载与校验

- 加载器 `src/sg/tasks/config_loader/`：结构校验在启动即抛
  `TaskConfigError`（缺文件 / type 重复 / 引用未定义类型 / widget 非法）。
- 本期只消费第一条 enabled 的 machine→app→user 链；
  多设备 `adb -s` / 分身 `--user` 派发是后续功能，schema 已预留。

---

## 八、架构变更记录（追加式）

> 未来若修改以上任何一条设计，必须在此追加：

| 日期 | 变更 | 兼容性 |
|---|---|---|
| — | 初版：SimpleNamespace + getattr + factory 注入 | — |
| — | `_wait_element` / `_wait_and_click` 支持单/多元素，共享 timeout 并行轮询 | 单元素调用完全兼容 |
| 2026-09-30 | 失败补建策略：TaskQueue 新增 `_check_stalled_factories`（tick 末尾扫描"全部终态但 count 未用完"的卡死工厂）+ 队列级 `continue_after_failure`（默认 True=补建跑满次数；False=标 `factory.exhausted` 停止）。修复旧实现中任务 FAILED 后既无 follower 又无 replenish 导致剩余次数丢失、`all_done()` 永不满足、run() 死循环的问题。`TaskFactory.can_submit_more()` 尊重 exhausted | 默认行为=继续跑满，向后兼容；旧"失败即卡死"场景变为按策略收敛 |
| 2026-09-30 | 一次性任务：`TaskFactory.one_shot`，整个工厂只建 1 个实例，`one_shot_remaining` 记剩余次数，终态（SUCCESS/FAILED）后由 `_reschedule_for_next_run` 扣减并重排 PENDING，到 0 终态；不走 `_create_follower`。注册参数 `default_one_shot`，GUI 配置 "{key}: One Shot" | 新增开关默认 False，不启用时调度行为与原来完全一致 |
| 2026-09-30 | 定时任务：`TaskFactory.cron`（croniter 表达式），trigger_time 由 cron 计算，终态后排下一次触发（SCHEDULED），执行 Count 次后停止；非法表达式标 exhausted 防死循环。注册参数 `default_cron`，GUI 配置 "{key}: Cron"。与 next_trigger_delay / one_shot 互斥 | 新增配置默认空=关闭；常规任务行为不变 |
| 2026-09-30 | 配额记账拆分：`submitted` 恢复"已 create 的任务实例数"原语义（所有工厂一致）；单实例复用型（one_shot/cron）的执行次数收敛改由新字段 `finished_count` 承担（每终态 +1，`finished_count >= count` 即收敛）。修正初版实现把 submitted 当"剩余次数"扣减导致 `can_submit_more()` 初始即 False、run() 主循环一次都不进、任务建不出来的问题 | submitted 语义与旧版完全一致；finished_count 为新增字段，常规任务恒为 0 不参与判断 |
| 2026-09-30 | cron 首次执行也等触发点：`_replenish` 建任务时按 cron 预置 `trigger_time`（SCHEDULED），不再"启动即跑一次"；表达式非法在建任务时就标 exhausted + 任务 FAILED。抽取 `_next_cron_time()` 公共方法统一时区处理（croniter 传 float 会按 UTC 解析差时区，必须用本地 datetime 进出）；`add()` 尊重调用方已判定的 FAILED 终态不再覆盖 | cron 行为从"立即跑一次+周期"变为"严格按 cron 到点才跑"；one_shot 与常规任务行为不变 |
| 2026-10-01 | 工厂简览日志：QueueTaskBase 新增 `_log_factories_summary()`，run() 构建工厂后输出单行 `[Factories] N 个: name_prefix(x count,active max_active[,one_shot][,cron=…][,march]) …`，作为启动第一条汇总。原来散在 `_build_factories` 里的逐条 `[Factory]` 日志删除，逐条详情由 `TaskQueue.add_factory` 保留 | 纯日志新增；调度逻辑不变 |
| 2026-10-01 | 配置折叠开关：每类任务新增 "{key}: Expanded"（**默认 True=展开**），sub_configs 两级链控 Enabled→Expanded→详情（Count/Max Active/Next Trigger Delay/One Shot/Cron/extra_config）。Enabled=False 全收起；Enabled=True 时由 Expanded 决定详情显隐。旧配置文件里的 Expanded: false（旧默认值迁移产物）已在 configs/UnifiedQueue.json 手工翻为 true | 新增 bool 默认 True（展开）；Config.verify_config 自动迁移旧配置文件缺失键；_build_factories 不读 Expanded，调度行为不变 |
| 2026-10-04 | ADB 手势集成：`ADBInteraction` 新增手势方法（scroll_page/fling/scroll_horizontal/pinch_in/pinch_out/zoom/two_finger_gesture/drag/long_click/double_click/swipe_points），原 scroll(direction) 更名 **scroll_page** 避让 `BaseInteraction.scroll(x, y, count)` 滚轮契约（否则 ADB 设备上 scroll_relative 会把参数误读成 direction/percent/duration）；新增 `width/height` property 透写 `device_width/device_height`（DeviceManager do_start 重连时写 `.width/.height`，不透写则缓存陈旧）。`BaseInteraction` 补手势存根：不支持的后端安全降级（drag 默认委托 swipe）。`ExecutorOperation` 按 click 模式集成同一组手势：统一 `_resolve_point`（Box/0~1 相对/像素/元组）+ `_gesture` 分发（调试框 + reset_scene + after_sleep + 不支持时告警跳过返回 False） | 新增方法默认不改变现有调用；`scroll_relative`/`scroll` 滚轮行为 100% 兼容；老 `swipe`/`click` 路径未动 |
| 2026-10-04 | u2 3.7.0 兼容修复（真机联调发现）：① `_gesture` 打包规则——点序列手势（swipe_points/two_finger_gesture）的坐标必须打包成**单个元组参数**或**每点一个元组**下发，禁止拍平成标量位置参数（会顶掉后面的 duration 形参报 got multiple values）；② `ADBInteraction.pinch_in/out` 改走 `u2()` 根 UiObject（u2 3.x 把 pinch 从 Device 挪到 UiObject，但 server 端 pinchIn/pinchOut RPC 仍在，真机验证可用）；③ `two_finger_gesture` 的 `gesture` RPC 已被 u2 server 移除（-32601），改为从四点向量推导 pinch_in/pinch_out（跨度收拢=捏合/撑开=张开，percent 按跨度比折算），非缩放双指轨迹不再支持 | swipe/drag/long_click/double_click/swipe_points 调用面不变；two_finger_gesture 语义收窄为缩放类双指；真机 127.0.0.1:16416 全部 11 项手势验证通过 |
| 2026-10-08 | 注册机制 JSON 化：业务定义从 `registrations/reg_*.py`（15 个文件 + `register_task_type`/`TASK_REGISTRY`）外置为 `configs/task_config.json` 单文件双段（`task_types` 定义段 + `machines` 实例段，多设备/多应用/多分身 schema 预留 `adb_serial`/`user_id`，本期只消费第一条 enabled 链）。新增 `task_type_map.TASK_TYPE_MAP`（type→类，代码侧唯一映射）与 `config_loader/`（解析 + 覆盖链 task→defaults→params.default + 启动即抛 `TaskConfigError`）；`UnifiedQueue` 直接继承 `QueueTaskBase` 并覆写 `_queue_global_params()` 钩子（基类默认仍读 GUI 配置），`GenericQueueTask` 注册表删除；用户在旧面板的调值（Hunt Monster count=10 等）由迁移脚本写入实例段，`Expanded` 展示态丢弃 | 调度器（TaskQueue/TaskFactory）零改动；JSON 驱动的工厂经冒烟验证 10/10 任务 DONE；老 GUI 配置 UnifiedQueue.json 废弃删除；webui 只需读写 task_config.json |

**变更前必问**：

1. 这是所有任务都会用到的通用能力吗？是才改基类。
2. 单元素老调用是否 100% 兼容？
3. 是否引入新的默认行为？必须向后兼容。
4. 业务特例是否被误抽上来？特例留在任务文件。
