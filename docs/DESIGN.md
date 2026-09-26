# Supermario（奔奔王国自动化）项目需求设计文档

| 项 | 内容 |
|---|---|
| 项目 | 奔奔王国游戏自动化（ok-script 框架 + 自研任务调度层） |
| 文档职责 | 记录需求、总体设计、以及每次需求/设计变更 |
| 约定 | 新需求 → 补「2. 需求列表」+ 对应模块设计；任何变更 → 必须在「6. 变更记录」追加一行 |
| 关联文档 | [src/sg/tasks/设计说明.md](../src/sg/tasks/设计说明.md)（队列调度详细设计）、各任务目录下 `*-需求文档.md` |

---

## 1. 项目概述

### 1.1 目标

自动化执行游戏「奔奔王国」中的重复性操作：派兵打巨兽/恐狼、采集、联盟捐献、日常宝箱等。用户在 GUI 上勾选任务类型并配置参数，程序按队列自动执行、自动补任务、自动恢复异常界面。

### 1.2 总体架构

```
┌─────────────────────────────────────────────┐
│ ok-script 框架（.venv 中的 ok 包）            │
│ BaseTask: 截图/特征匹配/OCR/点击/按键         │
└──────────────┬──────────────────────────────┘
               ▲
┌──────────────┴──────────────────────────────┐
│ SGBaseTask（src/sg/tasks/SGBaseTask.py）     │
│ 场景识别、等待工具、恢复协作、调度字段         │
└──────────────┬──────────────────────────────┘
               ▲
┌──────────────┴──────────────────────────────┐
│ 业务任务层（src/sg/tasks/<category>/）        │
│ HuntMonster / HuntScareWolf / Gather /       │
│ LeagueTechDonate / DailyChest ...            │
└──────────────┬──────────────────────────────┘
               │ 注册
┌──────────────┴──────────────────────────────┐
│ GenericQueueTask 注册表 + TaskQueue 调度      │
│ （src/sg/tasks/queue/ + src/scheduler/）     │
└─────────────────────────────────────────────┘
```

### 1.3 关键机制

| 机制 | 说明 | 代码位置 |
|---|---|---|
| 任务注册 | `register_task_type(key, ...)`，pkgutil 自动扫描 registrations 目录 | `GenericQueueTask.py`、`registrations/__init__.py` |
| 队列调度 | TaskFactory 按 `count`/`max_active`/`requires_march_queue`/`next_trigger_delay` 补任务 | `src/scheduler/task_queue.py` |
| 场景识别 | `SceneDetector.detect()` → SceneType（CITY/WORLD_MAP/UNKNOWN…） | `src/sg/scene/scene_detector.py` |
| 元素特征 | `SceneElement.resource_id` 与 `ok_templates/coco_annotations.json` categories 对齐 | `src/sg/scene/elements.py` |
| 恢复 | `RecoveryHelper` 轻恢复/重恢复，ESC 兜底 | `src/sg/helper/recovery.py` |

---

## 2. 需求列表

| 编号 | 需求 | 日期 | 状态 | 详细文档 / 代码 |
|---|---|---|---|---|
| R-001 | 巨兽出击（搜索型，占军队队列，可并行） | 先于本文档 | 已实现 | `tasks/world/HuntMonsterTask.py` |
| R-002 | 恐狼出击（道具触发型，占军队队列，不可并行） | 先于本文档 | 已实现 | `tasks/world/HuntScareWolfTask.py` |
| R-003 | 采集 | 先于本文档 | 已实现 | `tasks/world/GatherTask.py` |
| R-004 | 联盟捐献：捐材料换联盟币；次数 10 分钟恢复 1 次、上限 25 次，捐完 150 分钟循环一次；不占军队队列、不可并行、间隔 9000 秒 | 2026-09-26 | 已实现（待真机验证） | [tasks/league/LeagueTechDonateTask-需求文档.md](../src/sg/tasks/league/LeagueTechDonateTask-需求文档.md) |

> 编号规则：`R-XXX` 递增；历史需求补录时按文档创建日期顺延编号。

---

## 3. 需求 → 设计映射

每个新需求按 Skill B（`.agents/skills/new-task-development/SKILL.md`）流程落地：

```
需求
 ├─ 三核心参数 → requires_march_queue / next_trigger_delay / max_active（或 count=1）
 ├─ 流程拆解   → 步骤列表（步骤名 / 元素 / 交互方式 / 超时 / 失败策略）
 ├─ 领域对象   → 复用（Popup/Recovery/Rally/Searcher）或新写（有状态才抽）
 ├─ 聚合任务   → tasks/<category>/XxxTask.py（继承 SGBaseTask，_step 包裹，run_interaction 协议）
 ├─ 注册       → tasks/registrations/reg_xxx.py（extra_config 每项带 attr）
 └─ 需求文档   → tasks/<category>/XxxTask-需求文档.md（本仓库约定）
```

### 3.1 R-004 联盟捐献设计要点

- **流程**：回主界面（ESC 兜底）→ `GLOBAL_TAG_LEAGUE` → `LEAGUE_TECH_ENTRY` → `LEAGUE_TECH_UPDATE` → `get_box_by_name` 取剩余次数 bbox → OCR 读次数 → 按 bbox 定长点击 `LEAGUE_TECH_UPDATE_DONATE` → 复查次数为 0 → 回主界面。
- **点击方式**：捐献交互界面内全程不做特征匹配，按钮与剩余次数区域均用 `get_box_by_name(resource_id)` 直接取标注 bbox（见变更 C-002、C-004）。
- **OCR**：`LEAGUE_TECH_UPDATE_REMAIN_TIMES` bbox 内 OCR，正则提取数字，第一组为剩余次数。
- **防御**：按 OCR 次数定长点击；点完重读次数，不为 0 递归续捐；读不到且已捐过按成功结束。

---

## 4. 设计约定（全项目必须遵守）

1. **业务与调度隔离**：`scheduler/`、`tasks/queue/` 不 import 任何业务包，不出现业务术语（详见 [src/sg/tasks/设计说明.md](../src/sg/tasks/设计说明.md) 第 2、8 节）。
2. **特征名即事实**：`SceneElement.resource_id` 必须与 `ok_templates/coco_annotations.json` categories 完全一致（小写），新增元素后必须跑对齐检查。
3. **步骤一律 `_step` 包裹**：失败 → 恢复 → 重试；`RETRY` 只用于"暂时不可执行"，真失败用 `FAILED` 且先设 `last_error`。
4. **OCR 结果文本在 Box.name**：`self.ocr(...)` 返回的 Box，识别文字存于 `.name` 属性。
5. **弹层遮挡时不用特征匹配，用 `get_box_by_name`**：会被临时弹层（tip）遮挡的目标，用 `get_box_by_name(resource_id)` 按资源标注 bbox 直接定位/点击，不做图像特征匹配（R-004 的教训，后续同类场景直接沿用）。注意 `get_box_by_name` 与 `_wait_element` 的区别：前者直接查标注 bbox，后者底层仍是特征匹配。
6. **文档同步**：改流程/参数/交互方式 → 同步改对应需求文档 + 本文档变更记录。

---

## 5. 验证约定

| 变更类型 | 必须验证 |
|---|---|
| 新任务 | 冒烟：注册表出现新 key、resource_id 与 coco 对齐、协议方法完整；真机：独立模式 `run()` 跑通后挂队列 |
| 元素改动 | resource_id 与 coco categories 对齐脚本 |
| 调度参数 | TaskQueue 行为（follower 触发时间、max_active 名额） |
| 交互方式 | 真机观察实际界面行为（如弹层遮挡） |

---

## 6. 变更记录

| 编号 | 日期 | 类型 | 变更内容 | 涉及需求/模块 | 原因 |
|---|---|---|---|---|---|
| C-001 | 2026-09-26 | 需求 | 新增联盟捐献任务：捐材料换联盟币；每 10 分钟恢复 1 次、上限 25 次；捐完当前全部次数后 9000 秒再触发；不占军队队列、不可并行（count=1）。实现 `LeagueTechDonateTask` + 注册文件 | R-004 | 新需求 |
| C-002 | 2026-09-26 | 设计 | 捐献按钮点击方式变更：由"每次点击前特征匹配"改为"首次特征匹配记录 bbox，后续按 bbox 位置直接点击，不再重匹配" | R-004 | 每次捐献后游戏弹出 tip 遮挡特征区域，特征重匹配会失败；位置点击不受弹层影响 |
| C-003 | 2026-09-26 | 文档 | 修正 `LEAGUE_TECH_UPDATE_REMAIN_TIMES` 注释 id（53→82），确认其 resource_id 为小写 `league_tech_update_remain_times` 与 coco 标注一致 | R-004 | 特征匹配按 resource_id 查找，与 coco 不一致会导致匹配不到 |
| C-004 | 2026-09-26 | 设计 | 联盟捐献进一步去特征匹配：剩余次数读取由 `_wait_element`（特征匹配）改为 `get_box_by_name(resource_id)` 直接取 bbox；捐献按钮同样改 `get_box_by_name` 取 bbox，按 OCR 次数定长点击，点完重读、不为 0 递归续捐。捐献交互界面内全程无特征匹配 | R-004 | `_wait_element` 底层仍是特征匹配，捐献后弹出的 tip 遮挡特征会失败；`get_box_by_name` 按 resource_id 直接取标注 bbox，不做图像匹配，不受弹层影响（补充说明：该方法可直接按 resource_id 取 box，初版遗漏） |

---

## 7. 待办 / 风险

| 项 | 说明 | 关联 |
|---|---|---|
| 联盟捐献真机验证 | 独立模式跑通全流程，重点观察：OCR 数字解析、tip 弹出时 bbox 点击是否落点正确、次数清零后退出 | R-004 |
| bbox 位置点击风险 | 若捐献过程中界面发生平移/缩放，位置点击会落空（当前观察捐献面板为静态，风险低） | C-002 |
| elements.py 注释 id 重复 | 文件内多处 `# id` 注释重复（历史问题），仅注释不影响运行，暂不清理 | — |
