# 项目需求归档

只追加，不覆盖历史。

## [2026-09-28] 野兽出击部队（Hunt Beast Troop）

### 原始需求
新增搜索型出击任务：在荒野搜索面板中选择"野兽"图标并发起集结。占军队队列、立即触发、可并行（max_active=2）、无级别选择。

### 变更点
- 新增 `BeastSearcher`（继承 `BaseResourceSearcher`）：搜索资源 → 选野兽图标（`WORLD_ICON_RESOURCE_BEAST`）→ 确认位置；带资源栏滑动查找（石头/铁矿锚点 + 惯性滑动），与 `MonsterSearcher` 同构；无级别（`get_level_elements()` 返回 `{}`）。
- 新增 `HuntBeastTask`（继承 `SGBaseTask`）：业务编排 前台 → 清弹窗 → 体力检查 → 进荒野 → 搜索目标 → 集结 → 估算等待。全部业务步骤用 `_step` 包裹；`estimate_wait(self.extra_config)` 按最新惯例传参。
- 新增注册 `reg_hunt_beast.py`：`key="Hunt Beast Troop"`，`default_count=6`，`default_max_active=2`，`requires_march_queue=True`，`next_trigger_delay=0.0`；extra_config 含 Auto Recall / Min Stamina / Rally Config Wait（均带 attr，与 default_kwargs 一一对应）。
- `registrations/__init__.py` 用 pkgutil 自动扫描，无需手动挂载。

### 修复点
- 无（新任务，未改动既有文件）。

### 涉及文件
- `src/sg/element/world/resource/beast/beast_searcher.py`（新增）
- `src/sg/element/world/resource/beast/__init__.py`（新增）
- `src/sg/tasks/world/HuntBeastTask.py`（新增）
- `src/sg/tasks/registrations/reg_hunt_beast.py`（新增）

### 测试
- 独立模式：未跑（需游戏前台与真实画面；`py_compile` 全部通过）。
- 队列模式：未跑（同上）。
- 导入/注册验证：通过 —— `TASK_REGISTRY['Hunt Beast Troop']` 注册成功，extra_config 每项带 attr 且与 default_kwargs 一致，`BeastSearcher.get_resource_element().resource_id == 'wolrd_icon_resource_beast'`，pkgutil 挂载验证 `MOUNT_OK`。

### 遗留 / 风险
- 实机未验证：野兽图标的特征匹配阈值（沿用 searcher 默认 0.5）与资源栏滑动查找需在真实画面上确认。
- `Rally.prepare()` 未接入 `run_interaction`，与 `HuntMonsterTask` 现状一致（其队列准备段被注释为 todo）；若队列需要准备检查，三任务需一并补。

---

## [2026-09-28] 野兽挑战流程改造（Hunt Beast Troop 第二轮）

### 原始需求
搜索确认野兽位置后，游戏在世界地图把目标居中展示并弹出处理弹窗；点击弹窗的挑战按钮（固定位置，与瞭望塔事件 `WORLD_EVENT_OBJECT_HANDLEAREA` 一致，本次不抽公共元素），后续流程与 `WatchTowerEventTask._handle_beast_event` 一致。将该处理抽象到 beast 领域对象，只改 `HuntBeastTask`，`WatchTowerEventTask` 不动。

### 变更点
- 新增 `src/sg/element/world/resource/beast/beast.py`：`BeastChallenge` 领域对象——`challenge()` = 等弹窗渲染 → 点挑战按钮（bbox，`WORLD_EVENT_OBJECT_HANDLEAREA`）→ 击杀（bbox，`WORLD_EVENT_BEAST_KILL`，与瞭望塔一致）；`_safe_box` 捕获 `ValueError` 交给 `_step` 恢复重试。
- `beast/__init__.py` 导出 `BeastChallenge`。
- `HuntBeastTask` 改为挑战流程：移除 `rally.execute` / `estimate_wait` / `auto_recall` / `TEAM_HUNTING`；保留 `Rally` 仅做体力检查（挑战消耗体力）；搜索确认后接 `challenge.challenge()`；成功返回 `(SUCCESS, 0)`（无行军等待）。
- 注册文件同步精简：删 `Auto Recall` / `Rally Config Wait`，`default_kwargs` 只剩 `min_stamina`；`requires_march_queue=True` 保留（挑战发起进攻占队列）。

### 修复点
- 修复上一轮编辑事故中 `HuntBeastTask.py` 的残留拼接内容（截断至新文件末尾）。

### 涉及文件
- `src/sg/element/world/resource/beast/beast.py`（新增）
- `src/sg/element/world/resource/beast/__init__.py`（修改导出）
- `src/sg/tasks/world/HuntBeastTask.py`（重写为挑战流程）
- `src/sg/tasks/registrations/reg_hunt_beast.py`（精简配置）

### 测试
- `py_compile` 通过；导入验证通过：注册表 attr/kwargs 一致、`BeastChallenge` 使用瞭望塔同款元素、任务类无 `TEAM_HUNTING`/`auto_recall`/`estimate_wait` 残留。
- 实机未验证：居中弹窗的固定 bbox 在不同分辨率下的表现、击杀后无成功标志等待，需真机确认。

### 遗留 / 风险
- 挑战按钮复用 `WORLD_EVENT_OBJECT_HANDLEAREA` 的 bbox：若两者语义将来分叉（野兽弹窗与瞭望塔弹窗布局不同），需拆独立 SceneElement。
- 击杀成功无显式校验（瞭望塔原逻辑亦无 `WORLD_EVENT_BEAST_KILL` 后续标志），失败只能靠下一轮体力/队列状态间接暴露。

---

## [2026-09-28] 军队队列管理 Troop（第三轮）

### 原始需求
在 `src/sg/element/overall/tasklist/worldtask/troop/` 下实现 `troop.py` 队列管理：根据 `troop_march_team_num` 所在位置直接 OCR，返回军队队列的有效数量（第一个数字）与最大数量（第二个数字）。

### 变更点
- 新增 `Troop` 领域对象：`read_counts()` 返回 `(valid_count, max_count)`，OCR 结果按 x 排序后 `re.findall(r"\d+")` 解析，兼容 "3/5" 单文本框与 "3" "5" 分离文本框两种渲染；bbox 缺失 / OCR 异常 / 数字不足两个 → `(None, None)`；`free_slot_count()` / `has_free_slot()` 派生查询（读取失败视为无空闲）。
- `troop/__init__.py` 导出 `Troop`。
- `HuntBeastTask` 接入：`__init__` 组合 `self.troop = Troop(self)`；`run_interaction` 在进荒野后、搜索前检查 `has_free_slot()`，无空闲 → `RETRY 30s`（替换用户留下的 `march_team_num` 占位行）。

### 修复点
- 无。

### 涉及文件
- `src/sg/element/overall/tasklist/worldtask/troop/troop.py`（新增）
- `src/sg/element/overall/tasklist/worldtask/troop/__init__.py`（修改导出）
- `src/sg/tasks/world/HuntBeastTask.py`（接入队列检查）

### 测试
- `py_compile` 通过；单测通过：模拟 OCR 验证 (3,5) 分离框、(5,5) 单框 "5/5"、bbox 缺失 ValueError 三种路径；`has_free_slot` 空/满/失败语义正确。
- 实机未验证：`troop_march_team_num` 区域实际 OCR 识别率需真机确认。

### 遗留 / 风险
- `MarchQueue`（逐槽位特征扫描）与 `Troop`（整体 OCR 计数）并存，机制不同；后续统一时以用户决策为准。
- `HuntBeastTask` 的 RETRY 等待 30s 为固定值，未按队列剩余时间动态估算。

---

## [2026-09-28] 野兽挑战行军时间预估（第四轮）

### 原始需求
Beast challenge 在 kill 前根据界面 `beast_time_way` 区域 OCR 单程行军时间，计算来回耗时；与巨兽集结的 rally 时间计算作用一致——作为 `run_interaction` 返回的 `wait_seconds`，影响 TaskQueue 建立下一个任务的预计触发时间。

### 变更点
- `elements.py` 新增 `BEAST_TIME_WAY`（resource_id=`beast_time_way`，id:9 来自 coco 标注）。
- `Beast` 领域对象：`challenge()` 流程在点挑战按钮之后、kill 之前插入 `read_one_way_time()`（弹窗仍在时可读）；`last_round_trip_seconds = 2 × 单程`；`estimate_wait()` 返回来回耗时，OCR 失败兜底 `default_one_way_seconds=300`（来回 600s）；`_parse_time_text` 支持 `'90'`/`'1:30'`/`'1:30:00'`，与 `RallyTimer` 同口径。
- `HuntBeastTask.run_interaction` 成功路径返回 `(SUCCESS, self.beast.estimate_wait())`，替代原 `(SUCCESS, 0)`。

### 修复点
- 无。

### 涉及文件
- `src/sg/scene/elements.py`（新增元素）
- `src/sg/element/world/resource/beast/beast.py`（时间读取与预估）
- `src/sg/tasks/world/HuntBeastTask.py`（成功返回 wait_seconds）

### 测试
- `py_compile` 通过；单测通过：模拟 OCR `'1:30'` → 来回 180s；OCR 空 → 兜底 600s；`_parse_time_text` 三格式口径正确。
- 实机未验证：`beast_time_way` 区域实际显示格式（是否含中文"分/秒"）与 OCR 识别率需真机确认。

### 遗留 / 风险
- `_parse_time_text` 与 `RallyTimer.parse_time_text` 是平行实现（领域对象不互相 import 的约束所致）；将来统一时间解析时可上移到公共 helper。
- 兜底 300s 是拍估值，真机跑一轮后建议按实际分布调整。

---
