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

## [2026-09-29] 岛屿采水任务（Gather Island Water）

### 原始需求
海岛采集任务：进入自己的海岛采集水资源（生命之泉产水，最多积累 10 小时 → 每 10 小时触发一次），并在同一界面领取采水奖励。不占军队队列、不可并发（count=1）。跳转别人海岛采集本期不做。

### 变更点
- `elements.py` 新增 6 个元素：`ISLAND_GATHER_WATER_BUILD_1/2`（产水建筑）、`ISLAND_BUTTON_GATHER_REWARD`（奖励入口）、`ISLAND_BUTTON_REWARD_GET`（领取按钮）、`GLOBAL_REWARD_GETED_QUIT_TIP`（获得奖励退出提示，resource_id 按用户口述保留 rewward 双写）；复用已有 `ISLAND_AREA_SYMBOL` / `ISLAND_BUTTON_GATHER_WATER` / `GLOBAL_EVENT_TASK_NEED_HANDLE`。
- 新增 `GatherIslandWaterTask`（继承 `SGBaseTask`）：业务编排 回主界面（ESC 兜底）→ 点 `GLOBAL_EVENT_TASK_NEED_HANDLE` 展开任务列表 → 滚轮下滑（每次 1/4 屏，最多 8 次）+ 全屏检索循环找海岛入口 → ctrl+滚轮缩到最小（`send_key_down/up('ctrl')` + `scroll_relative`）→ 全屏循环点击两个产水建筑采水（至少采到一处即成功）→ 领奖流程（切领取界面 → 点领取 → 等 `global_rewward_geted_quit_tip` 出现点击退出）。全部业务步骤用 `_step` 包裹；领奖失败不阻塞采水结果。
- 新增注册 `reg_gather_island_water.py`：`key="Gather Island Water"`，`default_count=1`，`requires_march_queue=False`，`next_trigger_delay=36000.0`（10 小时），无 `default_max_active`，无 extra_config。

### 修复点
- 任务列表滚动无效 → `GatherIslandWaterTask._scroll_find_island_entry` 改用 `swipe_relative` 手指向上滑 1/4 屏（0.75→0.5，duration=0.3，settle=1.0），滚轮在该列表不生效。

### 涉及文件
- `src/sg/scene/elements.py`（追加元素）
- `src/sg/tasks/island/GatherIslandWaterTask.py`（新增；后由用户从 `world/` 移至 `island/` 目录，注册导入路径同步修正）
- `src/sg/tasks/registrations/reg_gather_island_water.py`（新增）

### 测试
- 独立模式：未跑（需游戏前台与真实画面）。
- 队列模式：未跑（同上）。
- `py_compile` 通过；导入/注册验证通过：`TASK_REGISTRY['Gather Island Water']` 注册成功（count=1 / delay=36000 / 无 max_active），8 个元素 resource_id 全部正确，任务类 `run_interaction` / `_run_once` / `run` / `_step` / `check_completed` 接口齐全。

### 遗留 / 风险
- [TODO] 采集界面缩放暂缓：框架当前不支持缩放操作（ctrl+滚轮组合/捏合手势），`_zoom_out_island` 的 ctrl+scroll_relative 实现暂定；需先整改框架支持缩放手势，再回来实现采集界面的缩放到最小，然后采水步骤才能稳定全屏检索到产水建筑。
- 新增元素的特征图已录入（ok_templates 重录，coco_annotations 同步更新），但实机识别率未验证。
- 进岛判据用 `ISLAND_AREA_SYMBOL`（threshold=0.7 全屏）；若海岛界面实际不显示该标志，需换 `ISLAND_BUTTON_GATHER_WATER` 或 OCR 判据。

---

## [2026-09-29] 联盟宝箱领取（League Reward Box）

### 原始需求
联盟日常奖励宝箱领取：主界面进入联盟页，点击 league_reward_box_entry 进入领取宝箱页面。页面有两个 tag（战利品宝箱 / 盟友赠礼），先点击 tag 头，再在对应 tag 下根据 get_item（绿色领取按钮）判断是否有可领取条目，然后点击 get 一键领取；第二个 tag 相同逻辑。领取后出现"获得奖励"通用界面，按通用退出标志 tip 点击任意位置退出。不占军队队列、每日一次、不可并行。

### 变更点
- `src/sg/scene/elements.py`：新增联盟宝箱 6 个元素（LEAGUE_REWARD_BOX_ENTRY / LEAGUE_REWARD_BOX_TAG / LEAGUE_FRIEND_REWARD_BOX_TAG / LEAGUE_REWARD_BOX_BUTTON_GET / LEAGUE_FRIEND_REWARD_BOX_BUTTON_GET / LEAGUE_REWARD_BOX_BUTTON_GET_ITEM）。
- 新增 `src/sg/tasks/league/LeagueRewardBoxTask.py`：编排 回主界面 → 进联盟 → 进宝箱页 → 战利品 tag（检测可领取 → 一键领取 → 退出 tip）→ 切盟友赠礼 tag（同逻辑）→ ESC 回主界面。全部业务步骤 `_step` 包裹；领取失败不阻塞整体结果。
- 新增 `src/sg/tasks/registrations/reg_league_reward_box.py`：`key="League Reward Box"`，`default_count=1`，`requires_march_queue=False`，`next_trigger_delay=86400`，无 max_active / extra_config / default_kwargs。

### 修复点
- 退出 tip 元素拼写 bug：`GLOBAL_MASK_REWWARD_GETED_QUIT_TIP`（resource_id=`global_mask_...`）与 coco 标注不符。ok 框架对未知特征 find_feature 直接抛 ValueError、get_box_by_name 返回 None，导致岛屿采水任务的"点击退出"步骤从未真正匹配成功。已改名为 `GLOBAL_MARK_REWARD_GETED_QUIT_TIP` 并同步迁移 `GatherIslandWaterTask.py` 的 2 处引用。提交前 coco 由标注工具重导出，tip 类别最终定名 `global_mark_reward_geted_quit_tip`（单 w，id=145），代码已对齐并实测 confidence=1.0 命中。

### 关键实现结论（实测，勿回退）
- tag 切换必须用 `get_box_by_name` 静态 bbox：tag 特征模板只匹配截图时状态（战利品=未选中棕底、盟友=选中白底），在另一态上最高 0.32 分，特征匹配无法完成切换。
- 可领取检测用 `find_one(league_reward_box_button_get_item)`：绿色按钮模板命中=有可领取；"已领取"灰色文本天然不命中。战利品页实测命中 (708,772)，盟友已领取页 0 命中。
- 盟友赠礼一键领取按钮模板是灰色禁用态（永远无法命中绿色启用态），故盟友 tab 由 get_item 检测通过后，用 `[GET, FRIEND_GET]` 多候选等待点击（实测 FRIEND_GET 模板在其截图上 confidence=1.0，禁用态可点出提示或无害）。
- 退出 tip `global_mark_reward_geted_quit_tip`（单 w）实测在其截图 103.png 上 confidence=1.0 命中 (308,1442)。

### 涉及文件
- `src/sg/scene/elements.py`（新增 6 元素 + tip 改名修复）
- `src/sg/tasks/island/GatherIslandWaterTask.py`（引用迁移到新常量名）
- `src/sg/tasks/league/LeagueRewardBoxTask.py`（新增）
- `src/sg/tasks/registrations/reg_league_reward_box.py`(新增)

### 测试
- 独立模式：未跑（需游戏前台与真实画面）。
- 队列模式：未跑（同上）。
- `py_compile` 通过；`import src.sg.tasks.registrations` 后 `TASK_REGISTRY['League Reward Box']` 注册成功（count=1 / delay=86400 / 无 march_queue）；7 个 resource_id 与 coco 标注逐一核对一致；模板匹配实测：可领取检测/一键按钮/退出 tip 在对应截图上均 confidence≈1.0 命中；旧常量名 git grep 无残留。

### 遗留 / 风险
- 盟友赠礼 tab 有可领取条目时的绿色按钮样式按与战利品一致处理（现有截图全部已领取，无样本验证）；若有差异，get_item 检测不到会安全跳过（no-op），日志可见。
- 盟友赠礼一键领取按钮（灰色禁用态模板）语义与战利品绿色启用态不同：若点击禁用按钮无效果，一键领取退化为逐条目点击 get_item 的需求，待实机验证后补充。
- 实机识别率未验证，需独立模式跑一次确认全流程。

---

## [2026-09-30] 队列配置按启用状态收起展开（GenericQueueTask）

### 原始需求
统一任务调度面板把所有注册类型的全部配置（Enabled / Count / Max Active / Next Trigger Delay / extra_config）平铺渲染，未启用的类型也占大量视觉空间。要求：未启用时只显示 Enabled 开关（收起其余配置），勾选后才展开该类型的其余配置。

### 变更点
- `GenericQueueTask._build_config`：利用 ok-script 原生 `config_type[key]["sub_configs"]` 机制（Qt 端 `ConfigCard.__setup_sub_configs` 监听 bool 开关 `checkedChanged`；Web 端 `ok/core/config_schema.py:config_visibility` 同语义），把 `Count` / `Max Active` / `Next Trigger Delay` 与全部 `extra_config` 项挂到 `"{key}: Enabled"` 下：`{True: [子配置列表]}`。未启用 → 规则查 `False` 无命中 → 全部收起；勾选 → 全部展开。Enabled 开关本身始终可见可勾选。
- 注册文件零改动：机制集中在 `_build_config`，未来新增注册类型自动获得收起行为；新增 extra_config 项自动进收起列表。
- 纯展示层联动：配置值始终持久化（`configs/GenericQueueTask.json`），`_build_factories` 读取不受收起影响。

### 修复点
- 无。

### 涉及文件
- `src/sg/tasks/queue/GenericQueueTask.py`（`_build_config` 挂 sub_configs + 文档注释更新）
- `tests/TestGenericQueueConfig.py`（新增回归测试）

### 测试
- 离屏 QT + ok-script 测试环境实测：9 个注册类型全部挂上 sub_configs；未启用类型 Enabled 可见、其余全部隐藏；已启用类型（持久化配置 Hunt Monster Troop=enabled）6 项全展开；动态切换 Gather Troop Enabled 后子配置变可见；`_build_factories` 正常（count=6 / max_active=2 / kwargs 完整）。
- 回归测试 3 用例全过：所有类型声明 sub_configs、禁用类型除开关外全部收起、启用类型全展开。

### 遗留 / 风险
- 已启用类型的配置在 UI 上是展开状态；若要"每个类型折叠成卡片、点开才见配置"，需动 TaskTab 列表层（ExpandSettingCard 卡片级），改动面大，本期不做。
- `GenericQueueTask._build_config` 里 `name_prefix=meta['name_prefix']` 局部变量为既有遗留（未使用），本次未清理。

## [2026-09-30] 队列快照展示任务明细（get_snapshot）

### 原始需求
队列任务界面（`QueueTaskBase._update_info` 铺 `TaskQueue.get_snapshot()`）目前只显示各状态计数与倒计时（`next_finish_in` / `next_trigger_in`），看不出具体是哪些任务在排队。要求：各状态展示具体任务名；等待触发的任务按触发顺序展示，并显示预计执行时刻（绝对时间）；不再展示倒计时。

### 变更点
- `TaskQueue.get_snapshot()` 重写：新增 `scheduled_tasks` / `pending_tasks` / `in_progress_tasks` / `failed_tasks` 四组明细列表；空列表不放进快照（UI 无空行），`_update_info` 的 key 自动展开逻辑零改动兼容。
- `scheduled_tasks` 按 `trigger_time` 升序（= 触发顺序；插入序跨工厂时可能偏离触发序，显式排序），每项 `任务名 预计执行时刻`；时刻今天显示 `HH:MM`，跨天 `MM-DD HH:MM`（新增静态方法 `_fmt_clock`）。
- `in_progress_tasks` 每项 `任务名 预计完成时刻`（`estimated_finish_time` 为 None 时只显示任务名），替代原 `next_finish_in` 的信息量。
- 删除 `next_finish_in` / `next_trigger_in` 两个倒计时字段（唯一消费方就是 UI 展示，无其他调用点）。
- DONE 只计数不出明细列表：任务对象不会被移除，无限 count 下列表会无限增长。
- UI 渲染链路复用 ok-script 现有能力：`TaskTab.update_task_info` → `value_to_string` 对 list 自动 `', '` join 成一行，快照值直接放 list 即可。

### 修复点
- 无。

### 涉及文件
- `src/scheduler/task_queue.py`（`get_snapshot` 重写 + `_fmt_clock` 新增）

### 测试
- 冒烟通过：模拟乱序插入 4 任务（SCHEDULED×3 / PENDING / IN_PROGRESS 混合），`scheduled_tasks` 按触发时刻升序输出（`Hunt Monster #2 21:48` → `Hunt Monster #1 22:47` → `League Reward Box #1 10-01 21:47`），跨天带日期前缀；空状态（pending/failed=0）不出行。
- 实机 UI 渲染未验证：list 经 `value_to_string` join 单行后，长列表在 `task_info_table` 中的显示宽度需真机确认。

### 遗留 / 风险
- PENDING 列表按插入序（FIFO）展示，未考虑 `next_retry_time` 未到的任务实际会被 `_pick_next` 跳过，展示顺序与真实执行顺序在重试场景下可能有偏差。
- RUNNING 任务无明细列表（同时最多 1 个，`current` 字段已覆盖）。

## [2026-09-30] 队列失败补建策略 + 一次性任务 + 定时任务（TaskQueue / TaskFactory）

### 原始需求
占用军队队列的任务（如集结巨兽 count=3、并行 1）在 #1 成功、#2 失败后，剩余次数永远建不出来：补任务只有 SUCCESS 后的 `_create_follower` 和一次性 `_replenish` 两条路径，任务 FAILED 后两条路都不触发；且 `all_done()` 里 `can_submit_more()` 恒 True，run() 主循环死转。要求：失败后可选"继续跑满次数"或"停止"（全局参数）。另新增两类任务：一次性任务（只建一个实例循环执行 Count 次）、定时任务（按 cron 表达式触发，支持 Count 次后停止）。

### 变更点
- **失败补建策略**：`TaskQueue.continue_after_failure`（队列级全局参数，默认 True=继续）。tick 末尾新增 `_check_stalled_factories()` 扫描"工厂名下全部任务已终态（DONE/FAILED）但 count 未用完"的卡死状态：继续 → 补建下一个（trigger=now+next_trigger_delay，失败次数也算进 count）；停止 → 标 `factory.exhausted`，剩余次数作废。修复 `all_done()` 永不满足导致 run() 死循环的问题。
- **一次性任务**：`TaskFactory.one_shot`。整个工厂只建 1 个实例，`task.one_shot_remaining` 记剩余次数；SUCCESS/FAILED 终态后由 `_reschedule_for_next_run()` 扣减重排 PENDING（按 next_trigger_delay 延后），到 0 终态；不走 `_create_follower`。非阻塞自重排方案：每次执行走完整 SUCCESS→IN_PROGRESS→check_completed 链路，UI 刷新/暂停/停止随时可用。
- **定时任务**：`TaskFactory.cron`（croniter 语法）。首次执行也等触发点——`_replenish` 建任务时按 cron 预置 `trigger_time`（SCHEDULED）；任务终态后用 croniter 算下一次触发点再排 SCHEDULED；执行 Count 次后停止；表达式非法在建任务时即标 exhausted + 任务 FAILED（防死循环）。与 next_trigger_delay / one_shot 互斥。
- **配额记账拆分**：`submitted` 恢复"已 create 的任务实例数"原语义（所有工厂一致）；单实例复用型（one_shot/cron）的执行次数收敛由新字段 `finished_count` 承担（每终态 +1）。修正中间版本把 submitted 当"剩余次数"扣减导致 `can_submit_more()` 初始即 False、run() 主循环一次都不进、任务建不出来的问题。
- **count=0 口径**：count=0 常规语义是无限，但一次性/定时任务复用单实例、无法表达无限（`finished_count < 0` 恒 False，任务建不出来）。约定：count=0 按 1 次处理，`can_submit_more()` 用 `finished_count < max(1, count)`，`one_shot_remaining` 预置 `max(1, count)`。
- **时区修复**：croniter 传 float 时间戳按 UTC 解析（差 8 小时），统一走 `_next_cron_time()` 公共方法，本地 datetime 进出再转回纪元秒。
- **配置层**：`register_task_type` 新增 `default_one_shot` / `default_cron`；每类型自动展开 "One Shot"（bool）/ "Cron"（文本，空=关）配置项并挂 Enabled 收起列表；`QueueTaskBase` 新增全局 "Continue After Failure" 开关（默认 True），run() 时注入队列。`add()` 尊重调用方已判定的 FAILED 终态不再覆盖为 PENDING。

### 涉及文件
- `src/scheduler/task_queue.py`（TaskFactory：one_shot/cron/finished_count/exhausted；TaskQueue：`_check_stalled_factories` / `_reschedule_for_next_run` / `_next_cron_time` / `_replenish` 预置 / `add` 终态尊重 / `can_submit_more` 拆分）
- `src/sg/tasks/queue/QueueTaskBase.py`（全局 Continue After Failure 配置 + 注入）
- `src/sg/tasks/queue/GenericQueueTask.py`（register_task_type 新参数 + One Shot/Cron 配置展开）
- `pyproject.toml` / `uv.lock`（新增 croniter 依赖）
- `tests/TestTaskQueueScheduling.py`（新增，14 用例）
- `tests/TestGenericQueueConfig.py`（扩展 One Shot/Cron 配置回归）
- `.agents/skills/automation-framework-arch/SKILL.md`（架构变更记录追加 5 条）

### 测试
- `tests/TestTaskQueueScheduling.py` 14 用例全绿：失败停止/继续两策略（原始 bug 场景 count=3 并行1 #1成#2败）、one_shot 单实例跑满/失败计入次数/count=0 端到端、cron 首次等触发点/时区秒级对齐守护/非法表达式建任务即 exhausted、配额衰减/无限模式/exhausted 语义。
- `tests/TestGenericQueueConfig.py` 4 用例全绿（含 One Shot/Cron 配置声明与收起守护）。
- run() 链路复核：one_shot count=3 → 建 1 实例执行 3 次（submitted=1, finished_count=3）收敛；cron count=2 → 两轮到点执行后收敛；初始 all_done=False 能进 while。
- `tests/TestMain.py` 3 个失败经 stash 对照确认为存量问题（placeholder feature/OCR），与本次无关。

### 遗留 / 风险
- 单实例复用型工厂的 `finished_count` 是新概念：后续若有代码直接读工厂计数做业务判断，需区分 `submitted`（实例数）与 `finished_count`（已执行次数）。
- cron 触发点按墙钟对齐（`*/5` = :00/:05/:10...），不是从启动时刻起算。
- cron 首次执行等触发点：启动后任务立即出现在 SCHEDULED 列表，到下一个分钟/周期边界才跑第一次；想立即验证可用 `* * * * *`。
- 实机未验证：GUI 面板 "Continue After Failure" / "One Shot" / "Cron" 三项配置的展示与持久化需真机确认（离屏测试只覆盖 schema 层）。

---

## [2026-10-01] 六项业务任务（讨伐领取 / 统帅奖励 / 宠物寻宝 / 恐狼滑动找道具 / 叛军精锐 / 集结选队门控）

### 原始需求
待办任务.txt 任务区 1–6：
1. 主界面讨伐：GOBAL_TAG_MARCH 进讨伐界面 → 两层领取按钮（global_tag_march_button_get / _next）→ 退出回主界面。
2. 统帅领取奖励：city_daily_free_leader_entry 进统帅界面 → 匹配 leaderbox 点击 → 弹窗匹配 page_button_geted（已领取）后退出 → 匹配 page_button_get_reward 领取 → "获得奖励"弹窗后结束 → 回主界面。
3. 宠物训练：global_event_task_need_handle 展开任务列表 → 上滑找 global_task_list_pet_search_treasure_entry → 宝藏界面全屏循环匹配 pet_symbol_treasure_hunt_1/_2（timeout=2、不重试）→ 点击后点 pet_button_search_treasure 派遣 → 找不到为止 → 回主界面。
4. 恐狼：背包里找不到狼爪时向上滑动，直到匹配到。
5. 叛军精锐：与恐狼同构，道具换 item_elite_rebels_icon，跳转后点击 world_resource_elite_rebels，接集结流程。
6. 所有集结流程：并发队列=1 时才在 rally apply 调整军队配置时按 team_element（TEAM_HUNTING 省体力队）选队；并发>1 跳过 select_team。

### 变更点
- **元素同步**（按 Skill C 规则，resource_id 原样照抄 coco categories，只追加不删旧）：elements.py 新增 13 个 SceneElement——讨伐 2（GLOBAL_TAG_MARCH_BUTTON_GET / _GET_NEXT）、统帅 5（CITY_DAILY_FREE_LEADER_ENTRY / _TITLE / _BOX / _PAGE_BUTTON_GETED / _PAGE_BUTTON_GET_REWARD）、宠物 4（GLOBAL_TASK_LIST_PET_SEARCH_TREASURE_ENTRY / PET_SYMBOL_TREASURE_HUNT_1 / _2 / PET_BUTTON_SEARCH_TREASURE）、叛军 2（ITEM_ELITE_REBELS_ICON / WORLD_RESOURCE_ELITE_REBELS）。
- **新增 PunishClaimTask**（daily/）：回主界面 → 点行军标签 → 两层领取按钮全屏检索点击 → ESC 回主界面；注册 key="Punish Claim"，count=1，不占队列，delay=86400。
- **新增 LeaderRewardTask**（daily/）：进统帅界面（标题确认）→ 循环领取（≤5 轮：找 box 图标 → 点击 → geted 弹窗=已领过点击退出 / get_reward=领取 → "获得奖励"tip 退出）→ 回主界面；注册 key="Leader Reward"，count=1，不占队列，delay=86400。
- **新增 PetTreasureHuntTask**（daily/）：复用岛屿采水的任务列表滑动模式（swipe_relative 上滑 1/4 屏 ×8）找宠物寻宝入口 → 循环派遣（_wait_element timeout=2 with_recovery=False 匹配 _1/_2 标志 → 点击 → pet_button_search_treasure 派遣）→ 找不到即正常结束 → 回主界面；注册 key="Pet Treasure Hunt"，count=1，不占队列，delay=86400。
- **HuntScareWolfTask**：新增 `bag_scroll_max=8` 常量与 `_scroll_find_item()`（全屏匹配 → 找不到上滑 1/4 屏重试）；`_use_scare_wolf_claw` 找狼爪改走滑动查找。
- **新增 HuntEliteRebelsTask**（world/）：与恐狼同构——道具换 ITEM_ELITE_REBELS_ICON（同样滑动查找）、"使用"按钮沿用 ITEM_SCARE_WOLF_CLAW_BUTTON_USE（道具使用按钮通用）、资源点击先特征匹配 WORLD_RESOURCE_ELITE_REBELS 失败再按 coco bbox 兜底（使用道具后游戏强制居中），之后 rally.execute 集结 + estimate_wait；注册 key="Hunt Elite Rebels Troop"，count=0（无限），占队列，delay=0。
- **集结选队门控**：`TaskFactory.create()` 新增把 `max_active` 注入任务实例（kwargs 显式设置时以 kwargs 为准）；`RallyConfig.select_team()` 开头检查 `task.max_active > 1` → 跳过选队（省体力队伍只有一支，多路集结共用会互相覆盖配置），=1 且 team_element 非 None 才执行选队。HuntMonster / HuntScareWolf / HuntEliteRebels 三个集结任务自动生效，无需改任务代码。

### 修复点
- 无（全部为新任务/新行为，未改动既有失败路径）。

### 涉及文件
- `src/sg/scene/elements.py`（新增 13 元素）
- `src/sg/tasks/daily/PunishClaimTask.py`（新增）
- `src/sg/tasks/daily/LeaderRewardTask.py`（新增）
- `src/sg/tasks/daily/PetTreasureHuntTask.py`（新增）
- `src/sg/tasks/world/HuntScareWolfTask.py`（滑动找道具）
- `src/sg/tasks/world/HuntEliteRebelsTask.py`（新增）
- `src/sg/tasks/registrations/reg_punish_claim.py` / `reg_leader_reward.py` / `reg_pet_treasure.py` / `reg_hunt_elite_rebels.py`（新增）
- `src/scheduler/task_queue.py`（TaskFactory.create 注入 max_active）
- `src/sg/element/overall/tasklist/worldtask/troop/rally_config.py`（select_team 按 max_active 门控）

### 测试
- 全部触及文件 `py_compile` 通过。
- 导入/注册验证：13 个新元素 resource_id 与 coco categories 逐一比对一致；TASK_REGISTRY 13 项，4 个新 key 全部注册成功且参数正确（Punish Claim/Leader Reward/Pet Treasure Hunt count=1 不占队列，Hunt Elite Rebels Troop count=0 占队列）。
- select_team 门控单测（FakeTask）：max_active=2 跳过不点击；=1 且配置队伍执行点击；=1 未配置队伍跳过。
- TaskFactory.create 注入单测：max_active=2 注入实例；kwargs 显式 max_active=5 优先。
- `tests/TestTaskQueueScheduling.py` + `tests/TestGenericQueueConfig.py` 21 用例全绿（框架层无回归）。

### 遗留 / 风险
- 实机未验证：三个新每日任务与叛军精锐的全流程需游戏前台确认；新元素特征图已录入（coco 已含全部 13 项），但识别率未实测。
- 叛军精锐"使用"按钮复用恐狼的 item_scare_wolf_claw_button_use 模板：若游戏为不同道具渲染不同的使用按钮样式，需拆独立元素。
- LeaderRewardTask 的"已领取"分支点击 geted 标志本身作为退出动作（用户原文"匹配leaderboxgeted后点击退出"）；若该标志不可点击需改为 ESC。
- PetTreasureHuntTask 派遣后未处理可能的"派遣成功"弹窗（用户未提及）；实机若发现弹窗遮挡，需在 dispatch 循环里加 popup 处理。
- 恐狼/叛军精锐的 `_scroll_find_item` 为类级复制（两处 ~25 行）；第三处出现时应上移到 SGBaseTask 或公共 helper。

## [2026-10-01] 领取体力（Claim Stamina）

### 原始需求
主界面点击用户头像 global_player_headphoto，进入用户界面，直接点击 global_page_player_stamina_button_add 按钮，进入体力领取界面。如果找到 global_page_player_stamina_button_add_next 领取按钮，则点击领取，弹出领取成功界面后 ESC 返回上一层，接着做下一次领取的时间计算。如果没有找到领取按钮，则识别 global_page_player_stamina_add_waittime 区域的下次领取倒计时，通过当前时间算出下一次领取时间。最后退回主界面。

### 变更点
- **elements.py 新增 4 个元素**（领取体力段）：GLOBAL_PLAYER_HEADPHOTO（主界面头像）、GLOBAL_PAGE_PLAYER_STAMINA_BUTTON_ADD（体力入口加号）、GLOBAL_PAGE_PLAYER_STAMINA_BUTTON_ADD_NEXT（领取按钮）、GLOBAL_PAGE_PLAYER_STAMINA_ADD_WAITTIME（倒计时区域）。
- **新增 ClaimStaminaTask**（daily/）：回主界面 → 点头像进用户界面 → 点加号进体力领取界面 → 分支：找到领取按钮则点击领取 + 领取成功后 ESC 回上一层、下次领取 = 当前时间 + claim_cooldown；未找到则 OCR 倒计时区域、下次领取 = 当前时间 + 倒计时（识别失败兜底 default_wait_seconds=3600，下限 min_wait_seconds=60 防空转）→ 回主界面。
- **动态等待调度**：run_interaction 返回 (SUCCESS, 距下次可领取秒数)，check_completed 以 next_claim_time 为准——队列 IN_PROGRESS 到点后放行 DONE，follower 以 next_trigger_delay=0 在同一时刻触发下一次领取，循环节奏完全由游戏内倒计时驱动。注册 count=0（无限）+ next_trigger_delay=0 + 不占队列。
- **倒计时解析** `_parse_countdown_text`：支持 "01:23:45"、"12:34"、"5"、"1小时23分45秒" 等格式，冒号按段数映射、无冒号按数字组个数推断（3 组=时分秒，2 组=分秒，1 组=秒）。
- **注册文件 reg_claim_stamina.py**：key="Claim Stamina"，extra_config 暴露 Claim Cooldown（attr=claim_cooldown，默认 3600s）。

### 修复点
- 无（全新任务，未改动既有代码路径）。

### 涉及文件
- `src/sg/scene/elements.py`（新增 4 元素）
- `src/sg/tasks/daily/ClaimStaminaTask.py`（新增）
- `src/sg/tasks/registrations/reg_claim_stamina.py`（新增）

### 测试
- py_compile / import 通过；注册经 pkgutil 扫描验证 TASK_REGISTRY 注册成功，extra_config attr 展开 kwargs['claim_cooldown']=3600.0 正确。
- `_parse_countdown_text` 七种输入格式单测全通过。
- 打桩冒烟：成功路径步骤编排 front→main→player→stamina→claim→main、返回 (SUCCESS, 1234)；失败路径（进用户界面失败）返回 (FAILED, 0) 且 last_error 正确；check_completed 到点前 False / 到点后 True 语义验证通过。
- 实机未验证（需游戏前台确认 4 个新元素特征识别与 OCR 倒计时）。

### 遗留 / 风险
- 领取成功界面以固定等待 claim_popup_wait=1.5s 后按 ESC 处理，未做弹窗特征确认；若领取动画超 1.5s 可能 ESC 落空（影响：退层失败，后续 _ensure_main_scene 兜底，不影响领取结果）。
- 倒计时 OCR 依赖 get_box_by_name 命中 GLOBAL_PAGE_PLAYER_STAMINA_ADD_WAITTIME 的 bbox；若该元素未录特征图会走 3600s 兜底，表现为领取间隔变大而非错误领取。
- claim_cooldown 的真实值需实机校准（当前默认 3600s 为估计值）。

## [2026-10-02] 实测调优（消息中心 / 统帅 / 宠物寻宝 / 瞭望塔 + 元素与特征图补录）

### 原始需求
首轮六项任务与领取体力上线后实机联调：修复实机暴露的识别与流程问题（弹窗关闭超时不足、消息标签/奖励角标多变体漏识别、宠物寻宝入口被遮挡、派遣按钮识别、宝藏标志误匹配、瞭望塔误匹配），并补录对应特征图到 coco。

### 变更点
- **ClaimMailRewardTask**：领取按钮/切换标签 timeout 降至 1.5s/2.0s 提高节奏；每条领取成功后 sleep 2s + ESC 关奖励弹窗，随后立即删已读消息（从 `_claim_all_tags` 移入 `_claim_current_tag`，无奖励标签仍删一次）；无可领判断改为同时匹配 `MESSAGE_TAG_REWARD_ICON` / `_ICON_1`；标签列表加入 `MESSAGE_TAG_LEAGUE_1`；`_del_readed_msg` 加上限最多删 3 次。
- **LeaderRewardTask**：统帅入口/标题确认 timeout 6s→2s。
- **PetTreasureHuntTask**：入口查找先匹配新增的 `GLOBAL_TASK_LIST_PET_SEARCH_TREASURE_MASK`（遮挡判定）再定位 ENTRY 点击；宝藏标志加入 `_3` 且 threshold 提到 0.95；派遣界面改为两连点——先 `PET_BUTTON_SEARCH_TREASURE_START`（出征）再 `PET_BUTTON_SEARCH_TREASURE`（确认）；每轮派遣后 ESC 退出结果页。
- **WatchTowerEventTask**：事件元素 `_find` threshold 提到 0.95（降低误匹配）。
- **ActivityPopup.close_all**：click 传 timeout=2，加速弹窗扫描。
- **元素同步**（resource_id 照抄 coco categories，只增不删）：`MESSAGE_TAG_LEAGUE_1` / `MESSAGE_TAG_REWARD_ICON_1` / `MESSAGE_REWARD_CLOSE_TIP`（原 `MESSAGE_REWARD` 更名，resource_id 实测为 message_reward_close_tip）/ `GLOBAL_TASK_LIST_PET_SEARCH_TREASURE_MASK` / `PET_SYMBOL_TREASURE_HUNT_3` / `PET_BUTTON_SEARCH_TREASURE_START`，`CITY_DAILY_FREE_LEADER_TITLE` 引用补齐；coco_annotations.json 同步 387 行（新增 7 个 category 及特征图 36–51.png）。

### 修复点
- `MESSAGE_REWARD` 更名为 `MESSAGE_REWARD_CLOSE_TIP`：原 resource_id 与实机特征不符，按 coco 实测 id 修正。
- 宠物寻宝入口点击前未校验 ENTRY 是否存在即 click 的空指针风险：先 MASK 判定、ENTRY 复查后再点。

### 涉及文件
- `src/sg/scene/elements.py`（新增 6 元素、更名 1 元素）
- `src/sg/tasks/daily/ClaimMailRewardTask.py` / `LeaderRewardTask.py` / `PetTreasureHuntTask.py`
- `src/sg/tasks/world/WatchTowerEventTask.py`
- `src/sg/element/overall/activity/popup.py`
- `ok_templates/coco_annotations.json` + 新增特征图 36–51.png

### 测试
- 全部触及文件 `py_compile` 通过；`import src.sg.tasks.registrations` 后 TASK_REGISTRY 14 项齐全；四个改动任务类导入成功。
- `tests/TestTaskQueueScheduling.py` + `tests/TestGenericQueueConfig.py` 21 用例全绿（框架层无回归）。
- 实机联调通过：待办任务.txt 全部 10 项标记 done；本轮调优即实机验证结论。

### 遗留 / 风险
- `_del_readed_msg` 的 3 次上限与各 timeout（1.5/2.0s）为实机经验值，网络慢时可能提前放弃，表现为少领而非失败。
- 宝藏标志 threshold=0.95 较激进，若游戏换肤/动画帧导致识别率下降，需回退 0.9 并补特征图。
- `src/sg/test.py`（ADB 多用户启动游戏辅助脚本）为手工工具，未纳入任务框架，保持 untracked。

## [2026-10-03] 切萨雷征讨（Cesare Fight）

### 原始需求
游戏内两周一次的常规活动"切萨雷征讨"自动化：
1. city 主界面读当前体力与实力（本次不实现，预留）；
2. 主界面全屏搜索便捷入口 `global_activity_fight_qiesalei_convenient_entry`，找到直接进入切萨雷界面；
3. 未找到 → 点 `global_entry_common_activity` 进常规活动，在 `global_entry_greatvalue_activity_slide_area` 区域先向左滑（每次 1/4 屏）匹配 `global_activity_fight_qiesalei_tag_entry`，向左找不到再向右滑；匹配后点击进入；
4. OCR `global_activity_fight_qiesalei_power` 读目标实力存临时变量（后续业务可用）；
5. 点 `global_activity_fight_qiesalei_button_reconnoitre` 侦察；体力不足会弹体力补充界面（后续实现），本次任务标记结束并按配置等待；
6. 点 `global_activity_fight_qiesalei_resource` 弹出处理窗口，三分支：
   - 挑战 `global_activity_fight_qiesalei_button_challenge`（普通野兽式流程，主路径）；
   - 集结 `global_activity_fight_qiesalei_button_rally`（实力不够，走集结）；
   - 求助 `global_activity_fight_qiesalei_button_need_help`（todo 待实现）。

用户决策：分支用 OCR 实力对比；挑战后确认同野兽击杀；占队列+不可并行（count=1）；体力不足新增等待配置项。

### 变更点
- `elements.py` 新增 10 个切萨雷元素（resource_id 与 coco categories id 183-193 逐一比对一致，只增不删）。
- 新增 `Cesare` 领域对象（`src/sg/element/overall/activity/cesare.py`）：便捷入口/常规活动双路径导航、滑动区左右滑找标签（每次 1/4 屏，向左优先）、目标实力 OCR（兼容千分位）、侦察、资源窗口、挑战/集结分支判定（目标未知→挑战；自身≥目标→挑战；否则集结）、求助力求按钮预留未接。
- `Rally.execute(entry_elements=None)` 参数化集结入口按钮：默认 `(BUTTON_RALLY_GIANT_BEAST_1,)` 行为不变；切萨雷分支传 `()`（弹窗入口已由 Cesare 点击）。
- 新增 `CesareFightTask`（`src/sg/tasks/world/CesareFightTask.py`）：编排 前台→清弹窗→回主界面→进界面→读实力→侦察→资源窗口→分支；挑战分支复用 `Beast.kill()` 野兽击杀确认；集结分支复用 `Rally`；侦察失败按 `stamina_wait_seconds` 返回等待。
- 新增注册 `reg_cesare_fight.py`：key=`Cesare Fight`，count=1，占队列，delay=0，default_kwargs 与 extra_config 三项（auto_recall / min_stamina / stamina_wait_seconds=3600）一一对应且每项带 attr。

### 修复点
- 无（本次为新增任务；Rally 签名变化经 import 回归确认对恐狼/野兽/巨兽调用方向后兼容）。

### 涉及文件
- `src/sg/scene/elements.py`
- `src/sg/element/overall/activity/cesare.py`（新增）
- `src/sg/element/overall/tasklist/worldtask/troop/rally.py`
- `src/sg/tasks/world/CesareFightTask.py`（新增）
- `src/sg/tasks/registrations/reg_cesare_fight.py`（新增，pkgutil 自动挂载）

### 测试
- 全部触及文件 `py_compile` 通过。
- 注册冒烟：TASK_REGISTRY 15 项，`Cesare Fight` 注册成功（count=1 / requires_march_queue=True / delay=0.0），extra_config attrs 与 default_kwargs 完全一致。
- 元素一致性：10 个新 resource_id 全部存在于 coco categories。
- 分支判定 + OCR 数字提取单测（FakeTask）4 用例全过：None→challenge、mine>=target→challenge、mine<target→rally、mine 未知→rally；'1,234,567'→1234567。
- 实机：未验证（待活动开启后联调）。

### 遗留 / 风险
- 体力补充界面处理（todo）：当前侦察按钮点不到/失败按"体力不足"处理并等待 `stamina_wait_seconds`。
- 求助模式（`button_need_help`）todo 未实现。
- 自身实力读取（`my_power`）todo：当前恒为 None，分支退化为"目标未知→挑战 / 目标已知→集结"的保守路径；实力读取实现后注入 task.my_power 即自动启用对比。
- 常规活动界面向左滑的边界行为（滑到头后是否自动反弹）未实机确认；滑动次数上限 8 次可按实机调整。
- `stamina_wait_seconds=3600` 为默认经验值，GUI 可调。

## [2026-10-03] 野外建筑资源采集（Gather Resource Troop）

### 原始需求
新增野外建筑资源采集任务：进入世界地图，OCR 读取军队队列有效/最大数量，按空闲队列数（最大-有效）派出采集；空闲为 0 → FAILED"没有采集队列"。点搜索资源按钮进搜索面板，在滑动区域 OCR 检测"大型"资源（记录类型，后续跳过同类型普通矿），普通资源面包/木材/石头/铁矿按当天日期除以 4 的余数轮转选取，大型资源优先；最多占用 4 个队列，每种一轮：选资源（等级用游戏默认，todo）→ 确认搜索 → OCR 预计采集时间暂存 → 点开始采集；循环直到空闲队列占满或 4 队全部派出。不占集结判定、每日一次、不可并行。

### 变更点
- 同步 coco categories 194-197 到 `elements.py`：`WORLD_ICON_RESOURCE_SLIDE_AREA` / `WORLD_RESOURCE_SEARCH_LARGE_RESOURCE` / `WORLD_GATHER_REOURCE_BUTTON_START` / `WORLD_GATHER_REOURCE_TIME_AREA`。
- 新增 `ResourceGatherSearcher` 领域对象：大型资源 OCR 检测（滑动区域内匹配"大型"+类型关键词，结果当天缓存）；`build_gather_plan(slot_count)` 实现轮转规则——普通资源按 面包→木材→石头→铁矿 循环，无大型矿时起点=日期%4（0→面包），有大型矿时起点=大型资源类型的下一种（大石头→余数0对应铁矿），跳过与大型同类型的普通矿，大型优先占 1 队，最多 4 队；`run_for(key)` 面板打开→选资源（滑动查找，与 BeastSearcher 同构）→确认搜索（等级 todo 跳过）。
- 新增 `GatherResourceTask`（继承 `SGBaseTask`）：前台→清弹窗→进荒野→Troop 读空闲队列（0→FAILED"没有采集队列"）→首轮搜索面板打开后触发大型资源检测+计划生成（plan_provider 回调，因滑动区域是面板内元素）→逐队"搜索→OCR 采集时间（暂存展示）→开始采集"→计划耗尽 SUCCESS。全部业务步骤 `_step` 包裹；保留 `run_interaction`/`_run_once`/`run` 三入口。
- 新增注册 `reg_gather_resource.py`：`key="Gather Resource Troop"`，`count=1`（不可并行，无 max_active），`requires_march_queue=False`（本任务就是去占采集队列），`next_trigger_delay=86400`，无 extra_config。

### 修复点
- 无（新任务，未改动既有逻辑文件）。

### 涉及文件
- `src/sg/scene/elements.py`（追加 4 个元素）
- `src/sg/element/world/resource/gather/gather_searcher.py`（新增）
- `src/sg/element/world/resource/gather/__init__.py`（新增）
- `src/sg/tasks/world/GatherResourceTask.py`（新增）
- `src/sg/tasks/registrations/reg_gather_resource.py`（新增，pkgutil 自动挂载）

### 测试
- 全部触及文件 `ast.parse` 通过；新元素 import 验证通过。
- 注册冒烟：pkgutil 自动加载后 TASK_REGISTRY 16 项，`Gather Resource Troop` 注册成功（count=1 / requires_march_queue=False / delay=86400.0 / 无 kwargs）。
- 轮转规划单测（FakeTask + monkeypatch 日期）：大石头余数0/4队→石头铁矿面包木材、大石头/2队→石头铁矿、大木头→木头石头铁矿面包、无大型矿余数0→面包木材石头铁矿、大铁矿余数3→铁矿面包木材石头，全 PASS；任务方法完整性 + 领域对象无反向 import 验证通过。
- 实机：未验证（需要游戏前台）。

### 遗留 / 风险
- 等级选择 todo 未实现：当前用游戏搜索面板默认已选等级，`select_level` 跳过。
- 大型资源 OCR 依赖滑动区域特征（`wolrd_icon_resource_slide_area`）与"大型/伐木场/面包/石头/铁矿"关键词；阈值和关键词匹配需实机校准。
- 首轮计划生成依赖面板打开后滑动区域可见；若 OCR 失败按无大型矿处理（保守路径）。
- `_wait_and_click` 未传 `name`（基类日志回退 `element.name`），符合 7.2 调用约定。

---

### 修复点（2026-10-03 二轮）
- `Cesare.read_power`：原 `"".join(ch.isdigit())` 逐字拼接丢失小数点，`'1.23万'` 被解析成 12（差 100 倍）。改用正则 `r"(\d[\d,]*(?:\.\d+)?)\s*([万亿])?"` 提取，支持千分位/小数/万/亿量级，`'1.23万'`→12300、`'2亿'`→200000000。9 用例单测全过。
- `Cesare._find_tag_by_sliding`：把 bbox 像素中心 y（约 1370）传给了 `swipe_relative`（只接受 0-1 相对坐标），会滑出屏幕。改为 `0.5` 屏幕中心。

## [2026-10-03] 领主（Player）数据领域对象

### 原始需求
新建 `Player`（领主）领域对象，集中管理体力与部队实力的获取：
1. 流程：主界面头像 → 领主展示界面；按 `global_page_player_stamina` OCR 体力（斜杠分割，前=有效体力，后=最大自动恢复上限）；经 `global_palyer_page_team_entry` → `global_palyer_page_team_org_entry` 进编组界面；点 `team_prepare_hunting_less_sinew` 切省体力编组，OCR `global_player_team_power_area` → `task.team_power_sinew`；切 `global_player_team_power_biggest` 最强编组再 OCR → `task.team_power_biggest`；退回主界面。
2. 数据落盘 `game_temp_data.json`。
3. 三级取值：各组件先用 task 全局字段 → 文件 → 现场跑获取流程。

### 变更点
- `SGBaseTask` 新增 4 个领主全局字段：`player_stamina` / `player_stamina_max` / `team_power_sinew` / `team_power_biggest`（默认 None）。
- `elements.py` 新增 5 个领主元素（id 151/198/199/200/201，全部 coco 已标注；注意 `palyer` 拼写为 coco 原文，照抄）。
- 新增 `Player` 领域对象（`src/sg/element/overall/player.py`）：
  - `collect_all()`：头像→体力→部队编组→省体力实力→最强编组实力→ESC 回主界面；
  - `_parse_stamina_text`：斜杠分割正则，`'1,200/2,000'`→(1200, 2000)、单数字→(150, None)；
  - `_parse_power_text`：与 Cesare 同口径（千分位/小数/万/亿）；
  - JSON 持久化：合并写、损坏容错（非 dict/JSON 错误返回空）；
  - `get_stamina()` / `get_team_power('sinew'|'biggest')` / `ensure_player_data()` 三级取值，文件命中自动回填 task 字段。
- `.gitignore` 追加 `game_temp_data.json`。

### 修复点
- 无。

### 涉及文件
- `src/sg/tasks/SGBaseTask.py`
- `src/sg/scene/elements.py`
- `src/sg/element/overall/player.py`（新增）
- `.gitignore`

### 测试
- `py_compile` 全过。
- 单测（FakeTask）14 用例全过：体力 7 例（斜杠/千分位/单数字/杂字容错）、实力 3 例、JSON 保存/文件回填/task 优先/损坏文件容错。
- 回归：CesareFightTask / HuntScareWolfTask / HuntBeastTask 导入正常，TASK_REGISTRY 16 项（新增 Gather Resource Troop 为同期他人变更）。
- 实机：未验证。

### 遗留 / 风险
- 编组界面点击 `TEAM_HUNTING` 后是否需要滚动/翻页才可见，未实机确认。
- `ensure_player_data` 现场采集为整轮 `collect_all`（进一次界面全拿），不做单项补采——体力过期刷新策略（何时视为失效）待后续业务定。
- OCR 体力原文若带其他装饰字符（如等级图标遮挡），解析可能失败，实机联调时观察。

### 变更点（2026-10-03 三轮）
- 重写 `src/sg/tasks/test/SGTestTask.py`（原为 HuntMonsterTask 批量自检）为 **Player 功能自检**：8 个用例逐项验证领主领域对象。
  - 纯逻辑 6 项：体力解析（7 例）/ 实力解析（7 例）/ JSON 持久化 roundtrip / 三级取值-文件回填 / 三级取值-task 优先 / ensure 数据齐全跳过采集；
  - 实机 2 项：`collect_all` 完整采集（需在主界面）/ 三级取值现场获取（清 task 字段+删 json 后 `get_stamina`）。
  - 每项 PASS/FAIL 逐例打点，末尾汇总 notify；用例前后备份/恢复 task 领主字段，互不污染。
  - 挂载不变：`config.py onetime_tasks` 的 `src.sg.tasks.test.SGTestTask`，UI 点击触发，独立模式 `run()`。
- 验证：py_compile 过；脱框架运行 6 项纯逻辑用例全 PASS；实机 2 项待游戏画面联调。

### 变更点（2026-10-03 四轮）
- `game_temp_data.json` 改为嵌套格式：
  ```json
  {"player": {"stamina": {"remain": 10, "max": 200},
              "team_power": {"team_power_sinew": 161573316, "team_power_biggest": 176913223}}}
  ```
- `Player` 相应改造：`_read_file_stamina` / `_read_file_team_power`（嵌套读取，缺失侧 None）、`_task_fields_to_nested`（task 字段→嵌套片段）、`_write_task_fields`（嵌套→task 回填）、`_merge_nested` 深层合并（部分字段保存不再抹掉另一段）；`get_stamina` / `get_team_power` 三级取值语义不变。
- 五组端到端验证全过：完整保存格式逐字节一致 / 新会话文件回填 / 部分字段保存深层合并不丢段 / 用户手写样例原样可读 / 缺段+非法 JSON 容错。
- 顺带修复：SGTestTask 移除 `runtime_locale` 引用（ok 框架运行时注入属性，独立路径下不存在导致 AttributeError）。

### 修复点（2026-10-03 五轮）
- `SGTestTask._test_json_roundtrip`：断言仍用旧扁平键（`disk.get(Player.KEY_STAMINA)`），嵌套格式下 `get` 返回 None 恒 FAIL。改为嵌套取值断言（`disk["player"]["stamina"]["remain"]` 等 4 项，含 `team_power_biggest` 缺席校验）。脱框架运行 3 个受影响用例（roundtrip/文件回填/task 优先）全 PASS。

### 变更点（2026-10-03 六轮：task 与 json 嵌套层级统一）
- `SGBaseTask`：4 个扁平领主字段（`player_stamina` / `player_stamina_max` / `team_power_sinew` / `team_power_biggest`）合并为单个 `task.player` 嵌套结构，与 `game_temp_data.json` 完全同层级（`player.stamina.remain/max`、`player.team_power.team_power_sinew/biggest`）。
- `Player` 配套改造：
  - 写入端：`_set_task_stamina` / `_set_task_team_power`（自动建段）；
  - 读取端：`_task_player` / `_task_stamina` / `_task_team_power`（缺失安全，类型异常视为空）；
  - 转换层：`_write_task_fields` = 深层合并进 `task.player`；`_task_fields_to_nested` = `task.player` → json 片段（过滤 None 叶子）；
  - `get_stamina` / `get_team_power` / `ensure_player_data` 对外语义不变。
- `SGTestTask` 全部用例改用 `task.player` 嵌套；领域对象实例改名 `player_obj`（`self.player` 已被数据结构占用）；文件类用例改用独立临时文件（`game_temp_data.json.test`，用后删除），不再污染真实数据文件。
- 端到端 6 组验证全过：task 嵌套写入 / task→json 同层级 / json→task 回填 / task 优先 / ensure 跳过 / 6 用例连跑 + 真实文件零污染。

### 变更点（2026-10-03 七轮：get_team_power 接口重整）
- `Player.get_team_power()`：无参，返回整个 `team_power` 节点（dict，三级取值：task → json → 现场采集；完全无数据返回 None）。
- `Player.get_team_power_key(key)`：`'sinew'`/`'biggest'` 查单键，内部复用 `get_team_power()` 后 `.get(file_key)`。
- 底层 `_task_team_power(key=None)` 合并为单定义：无参返回节点 dict、传 key 返回单值（此前手工改动残留双定义，其中一处引用拼错的 `__task_team_power` 会 AttributeError，`get_team_power` 旧体引用未定义变量会 NameError——均已消除）。
- 补回被误删的 `_set_task_stamina` / `_set_task_team_power` 写入端定义。
- 端到端 6 组验证全过：写入端 / 整节点取值 / 单键取值 / 空节点走文件与现场 / 文件部分字段回填 / 回填后单键补查。

### 变更点（2026-10-03 八轮：用户改动整理 + 提交前收口）
- `_step` 通用化上移：`SGBaseTask._step(step_func, step_name, *args, with_recovery=True, **kwargs)` 成为唯一实现——支持业务参数透传（`*args/**kwargs`）与 `with_recovery` 开关。删除 15 个任务类的本地副本（三种历史变体：无参 / `*args` 透传 / `with_recovery` 开关），全部由基类继承。行为差异说明：WatchTowerEventTask 原副本 `with_recovery=False` 时"单次执行不重试"，基类版为"重试但不恢复"——搜索事件类步骤重试无害（目标可能天然不存在），统一采用基类语义。
- `Beast` 领域对象支持修改队伍：新增 `team_element` 注入字段（默认 None）与 `select_team()`（与 RallyConfig 同规则：并发>1 跳过、未配置跳过）；`challenge(smalltili=False)` 开关控制挑战后是否切队，选队失败阻断流程（原实现忽略返回值且未初始化 `team_element`，未注入时 AttributeError）。`click_challenge_button` 改为全屏 `_wait_element` 检索。
- `CesareFightTask`：挑战步骤 `_step(self.beast.challenge, "挑战", smalltili=True)` 走省体力队伍；注入 `beast.team_element = TEAM_HUNTING`。
- `task_queue.py`：one_shot/cron 类型任务成功后 `estimated_finish_time = now`（触发时间由 `_reschedule_for_next_run` 控制）。
- 日志修正：`TaskQueue wait` 打印 `estimated_finish_time` 而非相对 `wait_seconds`。
- 新增 GatherResourceTask（采集资源）+ Gather 领域对象 + reg_gather_resource 注册；PetTreasureHuntTask/LeaderRewardTask/ClaimMailRewardTask 小调整；elements.py +181 行（含 gather/切萨雷/领主）；coco_annotations.json +486 行（新增特征图 10 张）。
- 测试隔离：SGTestTask 文件类用例使用独立临时文件，`game_temp_data.json.test` 用后即删。
- 验证：26 个 py 文件 py_compile 全过；TASK_REGISTRY 16 项全任务 `_step` 归属基类（无副本遮蔽）；`_step` 三种调用形态（无参/kwargs 透传/with_recovery=False）行为单测全过；Beast.select_team 未配置/并发>1 跳过路径验证通过。

## [2026-10-11] 采集出征派遣复用 Rally（troop_start 出发类型封装）

### 原始需求
GatherResourceTask 的 TODO：开始采集后做"队列选择出发派遣"。复用 Rally，给 Rally 加出发类型，区分野怪（集结）与资源采集，对应队列/英雄/兵力做相应处理。把 rally_config 的 apply（调整队伍/英雄/兵力）与 rally 的 _hunt_expedition（点集结出发）封装成一个方法 `troop_start`，入参 team_group_name / team_task_type / team_hero。apply 的选队子方法按传入队伍名 OCR 识别队伍选择区域：有则点击选取，无则不改（本版先按"没有"处理，"有"分支预留）。本版兵力、英雄不改。

### 变更点
- `Rally`（src/sg/element/overall/tasklist/worldtask/troop/rally.py）：
  - 新增 `ExpeditionType` 枚举（HUNT=hunt 野怪/集结，GATHER=gather 资源采集）。
  - 新增 `troop_start(team_group_name=None, team_task_type=None, team_hero=None)`：封装 `config.apply` + `_hunt_expedition`；`_normalize_expedition_type` 做类型归一化（None→HUNT，字符串按值匹配，非法值报错返回 False）。运行时状态 `last_expedition_type`。
  - `execute()` 尾段改为委托 `troop_start(team_task_type=HUNT)`——四个狩猎任务（HuntMonster/HuntScareWolf/HuntEliteRebels/CesareFight）调用点行为不变。
  - `_hunt_expedition` 点击日志去掉写死的"发起巨兽远征"文案，改为中性"点击出征/远征按钮"。
- `RallyConfig`（rally_config.py）：
  - 新增 `team_select_area`（出征页顶部预设标签行相对区域 0.03,0.07→0.82,0.13，无独立特征标注）与 `team_select_click_enabled`（预留开关，本版 False）。
  - `select_team(team_group_name)`：传队伍名走 OCR 选队 `_select_team_by_name`；不传走旧 team_element 特征路径。
  - `_select_team_by_name`：OCR 区域 → 精确命中优先/子串兜底匹配；命中且开关开启→点击选中（预留"有"分支），未命中/开关关闭→沿用系统默认。选不到队不算失败，恒返回 True（点击失败除外）。
  - `apply(team_group_name, team_hero)` / `adjust_heroes(team_hero)` 增参透传；team_hero 本版仅日志说明不生效。
- `GatherResourceTask`（world/GatherResourceTask.py）：点"开始采集"后新增 `_step(self._dispatch_troop, "出征派遣")`；`_dispatch_troop` 调 `rally.troop_start(team_group_name, GATHER)`。注入字段 `team_group_name`（默认 None=系统默认队伍）。
- `configs/task_config.json`：Gather Resource Troop 定义段 params 新增 `Team Group Name`（attr=team_group_name，default=null，widget=null）；description 补充派遣说明。实例段无需改（loader 自动回退 default）。
- `troop/__init__.py` 导出 `ExpeditionType`。

### 修复点
- 新代码缺陷（桩测试暴露）：`_select_team_by_name` 纯子串匹配会把相邻标签碎片误判命中（查 "cj" 命中 "xcj"，且其 x 更靠前被误选）。修复为排序键 `（非精确命中, x）`——精确匹配优先，子串兜底。

### 涉及文件
- `src/sg/element/overall/tasklist/worldtask/troop/rally.py`
- `src/sg/element/overall/tasklist/worldtask/troop/rally_config.py`
- `src/sg/element/overall/tasklist/worldtask/troop/__init__.py`
- `src/sg/tasks/world/GatherResourceTask.py`
- `configs/task_config.json`
- `scripts/test_troop_start_stub.py`（新增，脱框架桩测试）

### 测试
- `py_compile` 4 个改动 py 全过；`task_config.json` JSON 合法。
- 导入链：troop 包（Rally/RallyConfig/ExpeditionType）、GatherResourceTask、TASK_TYPE_MAP 15 类型、5 个 Rally 使用方任务回归导入全过。
- 桩测试 11/11 PASS：类型归一化 3 例（None→HUNT/"gather"→GATHER/非法→False 短路）、成功路径 apply→expedition 顺序、apply 失败短路、OCR 选队 4 例（未命中沿用默认/命中+开关关不点击/命中+开关开点击精确命中/OCR 异常容错）、旧路径 team_element 回归。
- 配置加载校验：gather params 定义 team_group_name，实例条目覆盖链回退 default=null。
- 实机：未验证（需游戏前台）。

### 遗留 / 风险
- "有队伍名则点击选中"分支由 `team_select_click_enabled` 开关预留（本版 False），实机验证 OCR 命中可靠后置 True 启用。
- `team_select_area` 相对区域按 900x1600 截图目测（出征页顶部标签行），其他分辨率未验证。
- 按出发类型差异化处理队列/英雄/兵力（HUNT vs GATHER）为预留设计：本版 GATHER 只记日志+选队，兵力/英雄不动；后续在此分叉。
- 采集确认面板点"开始采集"后是否必然进入出征配置页（而非直接派兵），需实机确认；若部分资源点直接派兵，`_dispatch_troop` 需加"出征页存在性"前置判断。

## [2026-10-12] 城堡黄金炼制（Golden Refine）

### 原始需求
一次性每日任务：进入主页面，点击 tasklist（global_button_tasklist），在弹出的列表中检索 global_task_list_icon_golden；未检索到则向上滑动继续检索，循环进行。若检索到 global_task_list_icon_adventure 说明列表已滑到底，此时仍未找到 golden 则任务已完成。检索到则点击特征（全屏匹配）global_task_list_icon_golden_entry 进入黄金炼制界面，根据 city_daily_task_button_golden_get_times 特征位置 OCR 剩余次数，循环对应次数点击 city_daily_task_button_golden_get，完成后退回主界面。不占队列、每日一次、不可并行。

### 变更点
- 新增 5 个 SceneElement（`src/sg/scene/elements.py`）：`GLOBAL_TASK_LIST_ICON_GOLDEN`（列表检索标志）、`GLOBAL_TASK_LIST_ICON_ADVENTURE`（列表底部标志）、`GLOBAL_TASK_LIST_ICON_GOLDEN_ENTRY`（入口按钮，全屏匹配点击）、`CITY_DAILY_TASK_BUTTON_GOLDEN_GET_TIMES`（剩余次数 OCR 区域）、`CITY_DAILY_TASK_BUTTON_GOLDEN_GET`（领取按钮）。
- 新增 `GoldenRefineTask`（继承 `SGBaseTask`，`src/sg/tasks/city/`）：编排 前台 → 清弹窗 → 回主界面 → 开任务列表 → 滚动检索 → 点入口 → OCR 次数 → 循环领取 → 回主界面。业务步骤全部 `_step` 包裹。
- 列表检索策略：golden 找到 → 点入口；adventure 出现且无 golden → `task_list_bottom_reached=True`，视为任务已完成（成功，不算失败）；滑动上限 10 次（1/4 屏/次）。
- 进入炼制界面以 `CITY_DAILY_TASK_BUTTON_GOLDEN_GET_TIMES` 特征等待确认（点入口后 6s 内出现）；`_in_golden_page()` 特征判定决定是否执行领取。
- OCR 次数解析兼容 `'3/5'`、`'3'`、`'1,200/2,000'`、空白；无结果/不可解析按 0 处理（跳过领取，不算失败）。
- 注册：`task_type_map.py` 加 `"Golden Refine"` 映射；`task_config.json` 加定义段（count=1、max_active=1、requires_march_queue=false、next_trigger_delay=86400、params=[]，无个性化参数）。

### 修复点
- 无（新任务，未改动既有文件逻辑）。

### 涉及文件
- `src/sg/scene/elements.py`（追加 5 个元素）
- `src/sg/tasks/city/GoldenRefineTask.py`（新增）
- `src/sg/tasks/task_type_map.py`（加映射行）
- `configs/task_config.json`（加 task_types 定义段 + machines 实例段条目，实例段为调度器实际消费入口）

### 测试
- 独立模式：未跑（需游戏前台与真实画面；`py_compile` 通过）。
- 队列模式：未跑（同上）。
- 导入/注册验证：通过 —— `TASK_TYPE_MAP['Golden Refine']` 解析到 `GoldenRefineTask`，5 个元素 resource_id 非空，task_config.json JSON 合法。
- 解析逻辑单测：通过 —— 8 组用例（斜杠/纯数字/千分位/空白/非法/空/None/0）全部符合预期。
- 配置加载验证：通过 —— `TaskConfigLoader.load()` 解析出 Golden Refine 实例条目，字段覆盖链正确（requires_march_queue 继承定义段 defaults）。

### 遗留 / 风险
- 实机未验证：golden/adventure 图标特征匹配阈值（0.8）与列表滑动节奏（1.5s settle）需真实画面确认。
- `_ensure_main_scene` 复用 ESC 方案；若 tasklist 打开时 ESC 行为不同（关闭列表 vs 退出游戏弹窗），依赖 RecoveryHelper 兜底，需实机确认。
- adventure 图标若在其他任务列表条目中复用（非底部专属），会出现"提前判定到底"的误判，需实机确认其唯一性。

---

## [2026-10-12] 士兵训练（Train Soldier）

### 原始需求
进入主页面，点开任务列表，在 global_task_list_search_area 区域内循环检索 global_task_list_soldier_training_icon（未检索到向上滑动，直到找到）。找到后点击 global_task_list_soldier_train_entry 跳转兵营，点击 city_building_soldier_entry 位置弹出交互按钮，全屏检索 city_building_soldier_button_train（检索不到时重新点击入口位置重试一次），点击后跳转训练界面。依次处理 soldier_tag_infantry / soldier_tag_archer / soldier_tag_cavalry 三个标签（逻辑一致）：灰度匹配（use_gray_scale=true，忽略颜色影响）检索到标签后点击进入，可训练则点击 soldier_button_trainnow，找不到训练按钮说明正在训练或无法训练（跳过）。全部处理完后回到主界面。

调度参数：不占军队队列、一次性（one_shot）、不可并行。

### 变更点
- `SGBaseTask` 通用能力：`_find` / `_wait_element` / `_wait_and_click` / `_wait_and_click_all_screen` 新增 `use_gray_scale` 参数（默认 False，向后兼容），透传 ok 框架 `find_one` 的灰度匹配。
- `elements.py` 新增 id 215-218 元素：`GLOBAL_TASK_LIST_SOLDIER_TRAINING_ICON` / `GLOBAL_TASK_LIST_SEARCH_AREA` / `CITY_BUILDING_SOLDIER_BUTTON_TRAIN` / `GLOBAL_TASK_LIST_SOLDIER_TRAIN_ENTRY`。
- `elements.py` id 44 修正命名：`CITY_BUILDING_BUTTON_TRAIN` → `CITY_BUILDING_SOLDIER_ENTRY`（与 coco 标注 `city_building_soldier_entry` 对齐；旧名在标注中不存在匹配不到模板，且全库无调用方）。
- 新增 `TrainSoldierTask`（`src/sg/tasks/city/`，继承 SGBaseTask）：任务列表滚动检索 → 点入口进兵营 → 点入口 bbox 弹交互按钮（bbox 兜底重试 1 次）→ 进训练界面 → 三兵种标签灰度匹配逐个尝试立即训练 → ESC 回主界面。全部业务步骤 `_step` 包裹；兵种标签匹配不到 / 训练按钮检索不到均跳过不阻断。
- 注册：`task_type_map.TASK_TYPE_MAP` 加 `"Train Soldier"` 映射；`task_config.json` `task_types[]` 加定义段（count=1，max_active=1，requires_march_queue=false，one_shot=true，params=[]）。实例段未添加——用户在 webui 中按需启用。

### 修复点
- 修复 `task_type_map.py` 编辑过程中误插入的重复 `"Golden Refine"` 行（曾顶掉 `"Leader Reward"` 行），已恢复 17 项映射与 task_config.json 完全一致。

### 涉及文件
- `src/sg/tasks/SGBaseTask.py`（灰度参数透传）
- `src/sg/scene/elements.py`（id 44 改名 + id 215-218 新增）
- `src/sg/tasks/city/TrainSoldierTask.py`（新增）
- `src/sg/tasks/task_type_map.py`（加映射）
- `configs/task_config.json`（加定义段）

### 测试
- 独立模式：未跑（需游戏前台与真实画面；`py_compile` 全部通过）。
- 队列模式：未跑（同上）。
- 导入/注册验证：通过 —— `config_loader.load()` 17 类型解析成功；`TASK_TYPE_MAP` 与 `task_config.json` 双向一致；`Train Soldier` 定义段 one_shot=True / requires_march_queue=False / class=TrainSoldierTask；`UnifiedQueue` 构建通过；灰度参数签名四方法默认值均为 False。

### 遗留 / 风险
- 实机未验证：训练图标 / 入口 / 兵营交互按钮的特征匹配阈值（0.8）、列表滑动节奏（1.5s settle）、兵种标签灰度匹配效果需真实画面确认。
- 标注数据中 `city_building_soldier_entry`（id 44）与 `city_building_button_train` 旧名不同源，若有外部工具仍按旧名检索需同步。
- 第三个兵种标签按现有元素 `SOLDIER_TAG_CAVALRY`（标注名 `soldier_tag_cavalry`）处理；若游戏内另有"步兵骑兵"混合页签需补独立元素。

---

## [2026-10-12] 士兵训练实例挂载 + Skill B 注册流程修订

### 原始需求
新任务开发完以后，配置文件也要跟上：`configs/task_config.json` 的 `machines[].users[].tasks[]` 必须同步加 `{"type": "Train Soldier", "enabled": true}` 实例条目，后续新任务自动处理，固化进开发流程。

### 变更点
- `configs/task_config.json` 实例段追加 Train Soldier 条目（enabled=true，count=1，one_shot=true，next_trigger_delay=0，params={}）——任务启用后即可进调度。
- `.agents/skills/new-task-development/SKILL.md` 注册机制整体修订（原第六章 reg_*.py 模板已随 4be06a9 架构变更过时）：
  - 第六章重写为"JSON 配置驱动注册三动作"：TASK_TYPE_MAP 映射 + task_types[] 定义段 + machines 实例条目（enabled: true），并明确"三个动作缺一不可，新任务开发完必须同步实例条目"。
  - 流程总览（一）第 6/7 步改为注册三动作；检查清单（九）注册项替换为六键 defaults / attr / 实例段挂载 / 双向一致性冒烟；指令模板（十）注册路径与约束同步。
  - §7.6 `_click_box` 引用修正为 `self.click(box)`（基类无 `_click_box`，今日实现时已实测确认）。

### 涉及文件
- `configs/task_config.json`（实例条目）
- `.agents/skills/new-task-development/SKILL.md`（注册流程修订）

### 测试
- 通过 —— 实例条目加载验证：`config_loader.load()` 解析出 `enabled=True, count=1, one_shot=True, params={}`；JSON 合法（17 任务实例）。

### 遗留 / 风险
- 无（流程文档变更，运行时行为不变）。

---

## [2026-10-12] 日常银行存取（Bank Save）

### 原始需求
待办任务.txt 任务区 11：银行自动存取。主界面点击超值活动 global_entry_greatvalue_activity 进入活动选择界面，在区域 global_sss_activity_head 内按灰度搜索特征 global_sss_activity_head（区域与特征同名：get_box_by_name 定位区域 bbox，区域内灰度 _find），未找到向左滑 1/4 屏反复循环匹配，最多 6 次滑动，未找到则结束。找到后点击该特征，按参数 bankdays（1/7/15/30，默认 1）选择存期标签 activity_tag_bank_save_{1,7,15,30}day，点击弹出确定界面后点击 activity_tag_bank_button_save 完成存储，最后退回主界面。

### 决策记录
- 三核心参数：requires_march_queue=false（纯 UI）、next_trigger_delay=86400（每日）、不可并行 count=1 无 max_active。
- 滑动耗尽语义：6 次未找到 → 标记 FAILED（用户确认"照常 FAILED 等下轮"，不做 RETRY/宽容处理）。
- 灰度查找：区域 bbox + 区域内 use_gray_scale=True（用户确认 bbox 区域+区域内灰度方案）。

### 变更点
- 新增 `src/sg/element/overall/activity/bank.py`：Bank 领域对象（open_activity / find_bank_head / pick_save_days / confirm_save），SAVE_DAY_ELEMENTS 存期映射，参数化 scroll_max=6 / scroll_step=0.25。
- 新增 `src/sg/tasks/city/BankSaveTask.py`：BankSaveTask 聚合任务（业务编排，全部业务步骤 _step 包裹，run_interaction 返回 (InteractionResult, wait_seconds)，保留 _run_once / run 双入口）。
- `src/sg/scene/elements.py` 补录 7 个 SceneElement（id 219-225）：GLOBAL_ENTRY_GREATVALUE_ACTIVITY、GLOBAL_SSS_ACTIVITY_HEAD、ACTIVITY_TAG_BANK_SAVE_{1,7,15,30}DAY、ACTIVITY_TAG_BANK_BUTTON_SAVE，全部对应 coco 标注类别（id 100/174-177/192/219）。
- `src/sg/tasks/task_type_map.py` 加 "Bank Save" 映射。
- `configs/task_config.json`：task_types[] 加定义段（params 含 bankdays drop_down [1,7,15,30]），machines 实例段加 enabled=true 条目（params={} 用默认 1 天）。
- 新增 `scripts/test_bank_save_stub.py`：27 项行为桩测试（脱框架）。

### 修复点
- 无（新任务开发，无既有行为回归；task_type_map 编辑过程中挤掉的 9 条 import / "Hunt Beast Troop" 映射行已全部补回并经双向一致性冒烟验证）。

### 涉及文件
- `src/sg/element/overall/activity/bank.py`（新增）
- `src/sg/tasks/city/BankSaveTask.py`（新增）
- `src/sg/scene/elements.py`（元素补录）
- `src/sg/tasks/task_type_map.py`（映射）
- `configs/task_config.json`（定义段 + 实例条目）
- `scripts/test_bank_save_stub.py`（新增）

### 测试
- 独立模式（桩）：`scripts/test_bank_save_stub.py` 27 PASS / 0 FAIL——入口点击、区域 bbox+灰度查找（含区域缺失变体）、滑动 6 次耗尽、存期选择（合法/非法）、确认存储、run_interaction 编排（SUCCESS/FAILED/last_error）、run/_run_once 双入口、步骤顺序断言。
- 注册冒烟：`config_loader.load()` + `parse_document()` 通过，18 类 JSON↔TASK_TYPE_MAP 双向一致，Bank Save 实例 enabled=true、params.bankdays=1。
- 实机队列模式：未测（需真机验证，见遗留）。

### 遗留 / 风险
- 区域与特征同名的灰度查找依赖游戏截图模板质量，实机若出现区域 bbox 偏移需微调 head_threshold=0.8。
- 实机未验证：独立模式 run 与队列调度需真机跑一次确认（尤其存期弹窗特征首次识别）。

---

## [2026-10-13] 自动领取仓库奖励（Claim Warehouse Reward）

### 原始需求
待办任务.txt 任务区 16：进入 world 主界面，点开 tasklist，循环上滑在任务列表检索特征值 city_task_list_warehouse_entry；滑动到底（判断标志 global_task_list_down_edge）仍未检索到则说明不允许领取，返回失败，不需要重试。检索到则点击特征（点击即领取），跳转领取成功界面，OCR city_task_list_warehouse_next_reward_time 区域获取下一次领取时间，返回主界面，任务返回成功以及下一次任务时间。

### 决策记录
- 调度模式：动态等待（用户确认"框架已支持按返回的下一次任务时间自动重建任务"）——count=0 + next_trigger_delay=0，SUCCESS 返回 OCR 出的距下次可领取秒数，follower 精确落在下一次领取时刻（ClaimStamina 同构）。
- 领取步骤：点 entry 即领取（用户确认），coco 已标注的 warehouse_button_get / warehouse_button_geted_tips 不录入不使用。
- 滑到底语义：FAILED 不重试（需求原文），由编排层直接判定绕过 `_step` 恢复重试，避免重复整轮滑动；队列 stalled 扫描按 delay=0 立即补建重扫。

### 变更点
- `src/sg/scene/elements.py` 补录 3 个 SceneElement（coco id 165/168/220，均已有特征图模板）：`CITY_TASK_LIST_WAREHOUSE_ENTRY`（仓库入口）、`CITY_TASK_LIST_WAREHOUSE_NEXT_REWARD_TIME`（下次领取时间 OCR 区域）、`GLOBAL_TASK_LIST_DOWN_EDGE`（列表滑到底标志）。
- 新增 `src/sg/tasks/city/ClaimWarehouseRewardTask`（继承 SGBaseTask）：编排 前台 → 清弹窗 → 回主界面 → 开任务列表 → 滑动检索（上滑 1/4 屏 ×10 上限，先找 entry 再找 down_edge）→ 点 entry 领取（等 next_reward_time 特征确认进入成功界面）→ OCR 下次领取时间（解析失败兜底 3600s，下限 60s）→ 回主界面。OCR 时间写入 `next_reward_time` 驱动 `check_completed()`，与 ClaimStamina 调度链路同构。
- 注册三动作：`task_type_map.py` 加 `"Claim Warehouse Reward"` 映射；`task_config.json` 加定义段（count=0、max_active=1、requires_march_queue=false、next_trigger_delay=0、params=[]）+ 实例段 enabled=true 条目。
- 新增 `scripts/test_warehouse_stub.py` 脱框架桩测试（FakeTask）。

### 修复点
- task_type_map.py 在 stash 恢复过程中被挤掉 3 行（GoldenRefineTask import、Claim Stamina / Claim Mail Reward 映射），git diff 对照 HEAD 后全部补回，双向一致性冒烟确认 19 类型无缺失。

### 涉及文件
- `src/sg/scene/elements.py`（追加 3 元素）
- `src/sg/tasks/city/ClaimWarehouseRewardTask.py`（新增）
- `src/sg/tasks/task_type_map.py`（加映射）
- `configs/task_config.json`（定义段 + 实例条目）
- `scripts/test_warehouse_stub.py`（新增）

### 测试
- 独立模式（桩）：`scripts/test_warehouse_stub.py` 6 组全过——倒计时解析 8 例（时分秒/分秒/纯数字/中文混杂/空/None）、成功路径（SUCCESS, 7200）+ 步骤顺序断言 + check_completed 未到点/到点语义、滑到底 FAILED 不重试（无恢复重试无 claim 调用）、OCR 失败兜底 3600s、下限 60s、领取点击失败 FAILED、_run_once/run 双入口。
- 注册冒烟：py_compile 全过；TASK_TYPE_MAP↔task_config.json 双向一致（19 类型）；config_loader.load() 解析出实例条目 enabled=true、count=0、delay=0；3 个元素 resource_id 与 coco categories 一致。
- 实机：未验证（需游戏前台确认 entry/down_edge 特征匹配与 next_reward_time 区域 OCR）。

### 遗留 / 风险
- 实机未验证：entry 图标阈值（0.8）、down_edge 出现时机（列表中段是否提前可见）、next_reward_time 文本格式（是否含中文"时/分"）需真机确认。
- FAILED（滑到底）+ delay=0 组合下队列 stalled 扫描会立即补建重扫（约 40s/轮），持续到可领取为止；若嫌轮询频繁可将实例条目 next_trigger_delay 调大。

---

## [2026-10-13] 储值中心每日免费奖励（Claim Buy Center Reward）

### 原始需求
从主界面点击 city_daily_free_reward_entry 区域进入储值中心，进入循环。退出条件为 city_daily_free_reward_area 区域检索不到未领取标记 city_daily_free_reward_buy_center_remind。循环内在 city_daily_free_reward_mask_area 区域 OCR 判断当前礼包：OCR 文字包含"随心自选"则点击 city_daily_free_reward_buy_center_box，随后全局检索特征判断是否进入领取成功界面——进入则点击一次成功标（global_mark_reward_geted_quit_tip，用户确认），未进入则不处理。之后在 area 区域检索 city_daily_free_reward_buy_center_remind_left 或 remind，检索到则点击翻到下一个活动 tag 继续循环。其他礼包的检索特征用户后续给出（流程一致），暂无分支时直接翻页跳过（用户确认）。

- 退出条件：remind + remind_left 双侧红点都检索不到才退出（用户确认，避免翻页后左侧红点被漏检）；加 claim_rounds_max=10 轮数上限防死循环。
- 成功界面判定：全局检索 GLOBAL_MARK_REWARD_GETED_QUIT_TIP（与 Leader/League/GatherIsland 既有模式一致，用户确认）。
- 其他礼包扩展点：`gift_handlers` 字典（OCR 关键词 → 分支方法名），后续补特征时加一行注册 + 对应分支方法即可，无需改主循环。
- 礼包特征映射（用户 2026-10-13 确认）：随心自选=buy_center_box，每日特惠=buy_center_box_1，每周特惠=待定（OCR 命中后翻页跳过，gift_handlers 未注册）。BOX_1 语义修正：不是随心自选的翻页位置，而是每日特惠礼包，两分支各自独立。
- 搜索区域修正：ok 框架 find_one 的 box 参数是裁剪搜索区域（FeatureSet.find_one_feature 源码确认）。BOX bbox [717,271] 在 AREA bbox [12,128,886,106] 之外，礼包点击必须全屏检索（box_of_screen(0,0,1,1)），不能用 area_box 限制。

### 变更点
- `src/sg/scene/elements.py` 追加 9 个 SceneElement（coco id 105/113/114/115/116/117/221/222，全部已有特征图与 bbox 标注）：ENTRY（主界面入口）、BUY_CENTER（界面标题，进入确认）、AREA（主体区域，红点检索范围）、MASK_AREA（礼包标题 OCR 区域）、BUY_CENTER_BOX（随心自选礼包）/ BOX_1（每日特惠礼包）、REMIND / REMIND_LEFT（未领取标记右/左）。
- 新增 `src/sg/tasks/daily/ClaimBuyCenterRewardTask`（继承 SGBaseTask）：前台 → 清弹窗 → 回主界面 → 进储值中心（标题确认）→ 循环[双红点检查 → OCR 礼包名（重试 3 次）→ gift_handlers 分发 → 点红点翻页] → 回主界面。
- 注册三动作：`task_type_map.py` 加 `"Claim Buy Center Reward"` 映射；`task_config.json` 加定义段（count=1、max_active=1、requires_march_queue=false、next_trigger_delay=86400、params=[]）+ 实例段 enabled=true 条目。
- 二次修正（同日）：`gift_handlers` 增加"每日特惠"分支（`_claim_daily_special_box`）；两礼包分支统一收敛到公共流程 `_click_gift_and_dismiss`（全屏检索点击礼包图标 → quit_tip 成功界面检测关闭）；`elements.py` BOX_1 desc 修正为"每日特惠礼包"。

### 修复点
- `task_type_map.py` 编辑过程中两次误删既有行（HuntScareWolfTask import、Pet Treasure Hunt / League Reward Box 映射），冒烟双向一致性检查捕获后全部补回（git diff HEAD 对照）。

### 涉及文件
- `src/sg/scene/elements.py`（追加 9 元素）
- `src/sg/tasks/daily/ClaimBuyCenterRewardTask.py`（新增）
- `src/sg/tasks/task_type_map.py`（加映射 + 修复误删）
- `configs/task_config.json`（定义段 + 实例条目）

### 测试
- 注册冒烟：py_compile 全过；TASK_TYPE_MAP↔task_config.json 双向一致（20 类型）；config_loader.load() 解析出实例条目 enabled=true、delay=86400、defaults 六键齐全；9 元素 resource_id 与 coco categories 全部一致；gift_handlers 分支方法存在性检查通过。
- 实机：未验证（MuMu 实例未开机；需真机确认入口/标题/红点特征匹配、mask_area OCR"随心自选"命中率、成功弹窗 quit_tip 特征）。

### 遗留 / 风险
- "随心自选" OCR 命中率未实机验证：mask_area 区域文字如有艺术字/描边，可能需要调整 OCR 阈值或改特征匹配。
- 其他礼包特征待用户补充后扩展 gift_handlers。
- AREA/MASK_AREA 两个区域元素仅作 bbox 定位不参与特征匹配；红点在 area bbox 外的情况（如有悬浮按钮遮挡）需真机确认。

---
## [2026-10-13] 任务配置编辑页——配置方案（保存 / 加载合并切换）

### 原始需求
参数配置页（TaskConfigEditorTab）支持把当前配置保存成"方案"，并通过"加载方案"在多个方案间快速切换。关键约束：切换时用方案 JSON **合并**（不是覆盖）到原始 JSON（所有任务实例停用态），避免配置新增任务后老方案缺少该任务条目、覆盖导致任务丢失。

### 变更点
- 顶栏新增两个按钮：存为方案（快照当前 self.doc 到 configs/task_config_profiles/<名称>.json，同名确认覆盖）、加载方案（下拉框选择 + 删除 + 切换）。
- 合并算法 _merge_doc：base=当前文档深拷贝全部置 enabled=false（原始 JSON），按业务键递归匹配合并——task_types[].type、machines[].name、apps[].package、users[].user_id、tasks[].type、params[].attr（KEYED_LISTS 按列表键名定身份，匿名列表如 widget.options 整体替换防重复）。匹配节点以方案为准；base 独有条目（新增任务/类型/参数）原样保留（保持停用态）；方案独有条目原样追加（忠实还原方案，保证幂等）。
- 切换流程：深拷贝预演合并 + validate_doc 校验（有问题弹确认）→ 通过后在 self.doc 上清空重放合并（原位更新，保持 doc 与任务卡片闭包的 dict 引用不变）→ _normalize_group_order → _sync_all(keep_cards=True) → 标脏不落盘（需手动保存）。
- 卡片复用显示同步：_defaults_rows 每行挂 _ctl_set+_dkey，_param_cell 单元格挂 _ctl_set+_attr（switch/number/drop_down/multi_drop_down/text 五种控件），_param_box 挂差异点 _dot_refresh；复用卡片 _refresh_state 按新数据回填开关/次数/参数值/差异圆点（blockSignals 防写回）。与上移下移（_move_task）同一复用机制，不整页重建。
- 未保存修改时先弹"基于当前未保存数据合并"确认。

### 修复点
- _param_cell 的 drop_down 分支连接了从未定义的 _drop_changed（选择即抛 AttributeError，参数无法修改）→ 补实现：剥"（不在选项中）"提示后写入 task.params。
- 删除死代码 _param_row（被 _param_cell 遮蔽、引用同样不存在的 _multi_toggle/_drop_changed）、被遮蔽的旧版 _defaults_rows（无信号连接）。
- _field_row/_param_box 差异圆点闭包暴露为可刷新钩子（原先创建后无法更新）。

### 涉及文件
- src/ui/TaskConfigEditorTab.py（方案功能 + 卡片复用钩子 + 修复）
- configs/task_config_profiles/（运行时生成，方案存储目录，用户数据不入库——首个方案文件随本提交入库作示例）

### 测试
- 离屏 Qt 全流程冒烟（QT_QPA_PLATFORM=offscreen）：20/20 卡片对象复用；新增类型+任务后切方案→新条目保留且停用、老任务还原方案状态；删除方案内条目后切换→自动追加还原；连续切换两次 JSON 完全一致（幂等）；validate_doc 零报错；方案快照与 doc 一致；下拉对话框选择/删除路径；drop_down 参数渲染路径无异常。
- 未实机验证：对话框在真实高分屏下的显示比例（截图反馈的列表显示不全问题已通过下拉框规避）。

### 遗留 / 风险
- 方案切换只改编辑器内存态，需点"保存"写入 task_config.json 后运行时才消费（InfoBar 已提示"记得保存落盘"）。
- 方案文件是全量快照：类型定义段也随方案走，切方案会连带回滚类型定义（params/default/widget 等）；若将来要求"方案只管实例、定义跟随最新"，需把 task_types 从合并范围剔除。
- machines/apps/users 按 name/package/user_id 匹配：重命名机器/包名后旧方案会匹配失败而整条追加，需手动清理。

---

## [2026-10-20] 踏浪活动任务（Wave Ride）

### 原始需求
主界面点击超值活动入口（global_entry_greatvalue_activity）进入活动选择界面，在 global_sss_activity_head 区域内灰度搜索"踏浪-标签"：未找到向左滑循环匹配最多 6 次，仍未找到向右滑循环匹配最多 6 次，耗尽则失败。找到后点击标签进入踏浪界面：先匹配"踏浪-一键领取"，匹配到则点击、延时 2s 后点"一键派遣"，下次触发延后 8 小时（可配置）；未匹配到则匹配"踏浪-探索"，匹配到再灰度匹配"一键派遣"，命中即点击、同样延后 8 小时，未命中则在"踏浪-剩余时间"区域 OCR 剩余时间作为下次触发时间；都未匹配直接 OCR 剩余时间。结束后返回主界面。

### 变更点
- `src/sg/scene/elements.py` 新增踏浪 5 个 SceneElement（resource_id 与 coco_annotations.json category name 严格一致）：`ACTIVITY_TAG_WAVE_RIDE`（踏浪-标签）、`ACTIVITY_TAG_WAVE_RIDE_CLAIM_ALL`（一键领取）、`ACTIVITY_TAG_WAVE_RIDE_EXPLORE`（探索）、`ACTIVITY_TAG_WAVE_RIDE_DISPATCH`（一键派遣）、`ACTIVITY_TAG_WAVE_RIDE_REMAIN_TIME`（剩余时间 OCR 区域）。特征图 127-130.png 已在 ok_templates 标注，无需补图。
- 新增 `src/sg/element/overall/activity/wave_ride.py`：`WaveRide` 领域对象——双向滑动灰度搜索（复用 BankSaveTask 的 GLOBAL_SSS_ACTIVITY_HEAD 区域滑动骨架，扩展为先左 6 次再右 6 次）、三分支处理（领取→2s→派遣 / 探索→灰度派遣→OCR 兜底 / 直接 OCR）、OCR 剩余时间解析（中文单位词优先：'8小时30分'→30600s；冒号：时:分:秒/分:秒；裸数字→秒）。
- 新增 `src/sg/tasks/city/WaveRideTask.py`（继承 SGBaseTask）：前台 → 清弹窗 → 回主界面 → 进超值活动 → 双向滑动找标签 → 三分支处理 → 回主界面（失败不影响结果）。注入字段 `dispatch_wait_hours`（默认 8）。SUCCESS 返回动态 wait_seconds（8h 或 OCR 秒数）。
- 注册三动作：`task_type_map.py` 加 `"Wave Ride": WaveRideTask`；`task_config.json` 定义段（count=0 无限循环、max_active=1、requires_march_queue=false、**next_trigger_delay=0**——动态间隔全走 SUCCESS 返回的 wait_seconds，配置再叠加会双倍延时；params 含 Dispatch Wait Hours attr=dispatch_wait_hours）；实例条目 enabled=true 挂 user0。

### 修复点
- `_parse_time_text` 初版按数字组个数推断（与 ClaimStaminaTask 同口径），单测抓到 `'8小时30分'` 被误判为分秒组合（510s，实际 30600s）→ 改为中文单位词配对解析优先，冒号/裸数字推断兜底。

### 涉及文件
- src/sg/scene/elements.py（追加踏浪元素常量）
- src/sg/element/overall/activity/wave_ride.py（新增）
- src/sg/tasks/city/WaveRideTask.py（新增）
- src/sg/tasks/task_type_map.py（加映射）
- configs/task_config.json（定义段 + 实例条目）

### 测试
- 冒烟通过：config_loader.load() 全量解析 OK；TASK_TYPE_MAP 与 task_config.json 双向一致（21 types）；元素 import OK；WaveRideTask/WaveRide import OK；实例契约断言（enabled=true、count=0、max_active=1、next_trigger_delay=0、params 注入 dispatch_wait_hours=8）全过。
- `_parse_time_text` 单测 14 用例全过（时:分:秒 / 分:秒 / 中文单位 / 裸数字 / 异常输入）。
- 独立模式 / 队列模式实机未跑（需游戏前台真实画面）。

### 遗留 / 风险
- 实机未验证：踏浪标签灰度匹配阈值（0.8）、双向滑动的边界表现（活动列表是否循环滚动）、一键领取/派遣/探索三特征在不同活动状态下的可见性组合。
- OCR"剩余时间"的文本格式假设（时:分:秒 或 中文单位）基于其他任务先例，真实字体渲染下的识别率需实机确认；识别失败已有 8h 兜底。
- 分支 a（一键领取成功后）若领取后"一键派遣"按钮不可见（如船已派出），任务按 FAILED 结束重试，不会死循环。

---

## [2026-10-22] 多 user 轮转调度（TaskQueue / TaskFactory / UnifiedQueue）

### 原始需求（待办 5.5）
配置启用了多个 userid 时，按 userid 启动对应的应用并执行该 user 配置的任务；执行完成（或超过预估时间，或距该 user 下一个任务超过半小时）后，关闭当前应用、启动下一个配置 userid 的应用并放到前台，再执行其任务。TaskFactory 需要增加 userid/package/machine 区分任务归属。

### 实现
- **loader 多链**：`parse_user_chains(doc)` 返回全部 enabled 的 machine→app→user 链（按配置顺序）；`parse_document(doc, type_groups, chain=…)` 支持按链解析；新增 `parse_user_configs(doc)` 逐链解析并附加 `machine/package/user_id` 标记。`pick_active_user` 退化为首链便捷入口，单链老调用行为不变。顺带修复：`parse_document`/`parse_user_configs` 缺省定义段改走 `load_type_document(DEFAULT_TYPE_PATH)`（原来用函数默认参数绑定，测试重定向 `DEFAULT_TYPE_PATH` 不生效）。
- **TaskFactory 链标记**：新增 `machine/package/user_id` 字段，`create()` 下发到任务实例（`task.machine/package/user_id`）；`UnifiedQueue._build_factories` 从当前生效链传入。
- **QueueTaskBase 协议**：`run()` 改为轮转主循环——每圈调 `_run_queue_cycle()`，返回 `yield`/`done_yield` 继续下一圈，`done`/`stop` 结束；`_rotation_yield_reason(queue_done=False)` 钩子（默认恒 None=单 user 一圈即终）供子类覆写；`_run_queue_cycle` 收尾段按「user_stopped > all_done > 中途让位」优先级返回。
- **UnifiedQueue 轮转**：≥2 个 enabled user 进入轮转。每圈 `_next_rotation_user()` 按游标取链 → `_switch_app_for_user()`（`adb_stop_package` 关当前 → `adb_start_package(package, user_id)` 启动目标 → 更新 `dm.user_id`；目标=当前时跳过重启；非 ADB 环境按"已在位"处理；启动失败跳过该 user，全失败回退第一链）。`_reload_config` 重读后按 `user_id` 保留本圈已切换的链（防止重读冲掉切换状态）。`ROTATION_IDLE_GAP=1800s`。
- **让位判定**（`_rotation_yield_reason`）：`queue_done=True`（队列自然跑完）→ 游标未回绕即 `cycle_done`（切下一个 user），回绕即本轮 pass 结束；圈中判定：超预算（Σ 启用任务 `estimated_seconds`（缺省回退 task_excute_time.json 均值）× max(1,count)，全未知不限时）→ `budget`；有 RUNNING/IN_PROGRESS 不让位（行军中绝不中途放弃）；最近可唤醒任务（SCHEDULED.trigger_time / PENDING.next_retry_time，已过视为马上）距今 >30min → `next_task_gap`；只剩 DONE/FAILED → `no_pending`；初始填充前（tasks 空）不判定。

### 涉及文件
- src/sg/tasks/config_loader/task_config_loader.py（parse_user_chains / parse_user_configs / parse_document chain 参数 / DEFAULT_TYPE_PATH 缺省修复）
- src/sg/tasks/config_loader/__init__.py（导出）
- src/scheduler/task_queue.py（TaskFactory machine/package/user_id + create 注入）
- src/sg/tasks/queue/QueueTaskBase.py（run 轮转主循环 / _rotation_yield_reason / _run_queue_cycle 协议）
- src/sg/tasks/queue/UnifiedQueue.py（轮转核心 + reload 保留当前链 + CONFIG_PATH 定义恢复）
- tests/TestUserRotation.py（29 用例）、scripts/verify_user_rotation.py（e2e 仿真）
- scripts/verify_unified_queue_reload.py（防回归锚点从 run() 迁到 _run_queue_cycle()）

### 测试
- `tests/TestUserRotation.py` 29 用例全绿（多链解析 / 链标记下发 / 游标与跳失败 / 让位判定六场景 / 预算 / 圈返回值协议）。
- `tests/TestTaskQueueScheduling.py` 14 用例全绿（调度层零回归）。
- `scripts/verify_user_rotation.py` e2e：双 user 配置 → 圈1 user0（在位不重启）→ 任务跑完 done_yield → 圈2 切 user10（stop+am start 调用序断言）→ pass 结束。
- `scripts/verify_unified_queue_reload.py` 全过（重读行为不变）。
- 实机未验证：分身启动 / force-stop 时序需真机确认（复用 2026-10-15 已真机验证的 adb_start_package 分身分支）。

---


## [2026-10-27] 英雄招募任务（Hero Enlist）

### 原始需求
主城界面点击 global_tag_hero 进入英雄列表 → 点击 hero_tag_enlist_entry 进入招募界面 → 匹配 hero_add_mask_scene 确认进入招募场景（未匹配到即失败结束）。招募分两种方式（游戏参数下拉，默认免费）：免费招募（灰度匹配 city_hero_button_enlist_1，可能 2 个，逐个点击进成功页，间隔 1s 点 hero_add_back，每 3 次校验一次 mask 特征，命中即停；找不到按钮则读下次免费时间——匹配 hero_add_free_next_time（可能多个），OCR 区域 = hero_time_area 的 x/width + 命中区域的 y/height，解析出时间回传作为下次触发间隔，失败兜底 86400）与批量招募（按配置次数循环 bbox 直接点击"英雄招募普通10次"/"英雄招募传奇10次"，出现批量提醒界面则点"不再提醒"→"确认"，领取动画期每 1s 按 ESC 直到 mask 特征重新命中，完成后退回主堡）。不占军队队列、不可并行、next_trigger_delay=0（动态间隔全走 SUCCESS wait_seconds）。

### 变更点
- `src/sg/scene/elements.py` 新增 11 个 SceneElement：HERO_TAG_ENLIST_ENTRY / HERO_ADD_MASK_SCENE / CITY_HERO_BUTTON_ENLIST_1 / HERO_ADD_BACK / HERO_ADD_FREE_NEXT_TIME / HERO_TIME_AREA / HERO_RECRUIT_NORMAL_TEN / HERO_RECRUIT_LEGEND_TEN / HERO_RECRUIT_BATCH_TIP_SCENE / HERO_RECRUIT_BATCH_NO_REMIND / HERO_RECRUIT_BATCH_CONFIRM。GLOBAL_TAG_HERO 复用已有（id:29）。中文命名元素（英雄招募普通10次 等 5 个）resource_id 与需求原文口述名一致，待 coco 标注后核对。
- 新增 `src/sg/tasks/city/HeroEnlistTask.py`：编排 回主界面 → 进英雄列表 → 进招募界面（mask 校验失败=FAILED）→ 按模式免费/批量 → 回主界面。免费模式 `find_feature(..., use_gray_scale=True)` 拿全部命中框逐个处理；`_back_to_enlist_scene` 间隔 1s 点 back、每 3 次校验 mask；`_read_next_free_time` 多命中从上往下逐个组 OCR 框（Box(area.x, mark.y, w=area.width, h=mark.height)）；批量模式 bbox 直点 + 提醒界面处理 + `send_key("esc")` 每 1s 直发（不走 recovery 版，其 esc_cooldown 冷却满足不了 1s 节奏）。`parse_time_text` 模块级函数（与 WaveRide 同口径：中文单位词配对 > 冒号 > 裸数字）。
- 注册三动作：`task_type_map.py` 加 `"Hero Enlist": HeroEnlistTask`；`configs/task_type.json` 定义段（type="Hero Enlist"、id=hero_enlist、count=0 无限、max_active=1、requires_march_queue=false、next_trigger_delay=0、params 三项：Enlist Mode drop_down [免费,批量] / Batch Normal Times / Batch Legend Times）；`configs/task_config.json` user0 链实例条目 enabled=true params={}。

### 修复点
- 编辑过程中 `task_type_map.py` 的 ClaimBuyCenterRewardTask import 行被误覆盖 → 已恢复；`task_config.json` user10 链的 wave_ride 条目被误删部分行 → 已恢复原状。

### 涉及文件
- src/sg/scene/elements.py（追加 11 元素）
- src/sg/tasks/city/HeroEnlistTask.py（新增）
- src/sg/tasks/task_type_map.py（加映射 + 恢复误删 import）
- configs/task_type.json（定义段）
- configs/task_config.json（实例条目 + 恢复 user10 wave_ride）
- docs/PROJECT_REQUIREMENTS.md（本节）

### 测试
- 冒烟通过：两个 JSON 解析 OK；config_loader.load() 解析 user0 链，hero_enlist 进调度（enabled/count=0/max_active=1/delay=0/params 三项注入全对）；TASK_TYPE_MAP 与 task_type.json 双向一致（22 types）。
- `parse_time_text` 单测 7 用例全过（'8小时30分'/'01:23:45'/'12:34'/'90'/'7小时'/空/无数字）。
- 类级冒烟：11 元素可导入、HeroEnlistTask 接口齐全（run_interaction/_run_once/run/_step/last_error/extra_config）。
- 独立模式 / 队列模式实机未跑（需游戏前台真实画面）。

### 遗留 / 风险
- 实机未验证：11 个新特征的截图未标注录入（ok_templates/coco_annotations.json 尚无对应 category），任务在标注完成前无法实际识别；resource_id 需与后续 coco 导出核对。
- 中文命名元素（英雄招募普通10次 等）的 category name 是否被标注工具保留原样待确认；若 coco 导出为拼音/英文需同步改元素 resource_id。
- 免费按钮灰度阈值 0.8、mask 特征在招募成功页停留期的表现需真机确认。
- OCR 下次免费时间的文本格式假设基于其他任务先例（时:分:秒 / 中文单位），真实渲染识别率需实机确认；识别失败已有 86400s 兜底。

---

## [2026-10-28] 竞技场挑战（Arena Challenge）

### 原始需求
建立新分支（feature/arena-challenge，并把建分支固化进 Skill B 标准流程）。新增竞技场挑战任务：
- 参数：每次挑战次数（默认 0=不限，以 OCR 剩余次数为准）、无免费次数后购买次数（默认 0=不购买）。
- 流程：城市主界面 → 任务列表 → 灰度匹配 global_task_list_city_tag → 上滑检索"训练士兵-弓" → 点击跳转弓兵营 → 点击"弓兵兵营旁边-竞技场"（bbox 直点）→ 点击"竞技场挑战"弹挑战列表。
- 购买次数（参数>0 时）：点"竞技场增加次数"进购买流程 → OCR"竞技场-购买次数-识别" → 与参数对比按差量点增加/减少 → 点"竞技场购买次数消耗钻石"。
- OCR"竞技场挑战剩余次数"得挑战次数，循环：对手列表区域找绿色 RGB(3,208,111)，多个取第一个点击 → 一键上阵 → 战斗 → 等 2s → 匹配"竞技场快速通过1"命中则快速挑战（快速通过2 → 等 2s → 快速通过3）。
- 达到 challenge_limit 或剩余次数用尽 → 退出到主堡界面。

### 变更点
- 新增 16 个 SceneElement（src/sg/scene/elements.py 尾部"竞技场挑战"段）：GLOBAL_TASK_LIST_CITY_TAG / GLOBAL_TASK_LIST_SOLDIER_ARCHER_ENTRY / CITY_BARRACKS_ARCANE_AREA / ARENA_BUTTON_CHALLENGE / ARENA_BUTTON_ADD_TIMES / ARENA_BUY_TIMES_OCR_AREA / ARENA_BUY_TIMES_BUTTON_ADD / ARENA_BUY_TIMES_BUTTON_SUB / ARENA_BUY_TIMES_BUTTON_CONFIRM / ARENA_CHALLENGE_REMAIN_TIMES / ARENA_OPPONENT_LIST_AREA / ARENA_BUTTON_ONE_KEY_DEPLOY / ARENA_BUTTON_FIGHT / ARENA_QUICK_PASS_1 / ARENA_QUICK_PASS_2 / ARENA_QUICK_PASS_3。
- 新增 src/sg/tasks/city/ArenaChallengeTask.py：SGBaseTask 子类，_step 编排，run_interaction 返回 (InteractionResult, wait_seconds)，保留 _run_once/run 双入口。
- 绿色对手检索：ok 框架 ok.util.color.find_color_rectangles，color_range={'r':[0,33],'g':[178,238],'b':[81,141]}（RGB(3,208,111)±30），项目内首次引入颜色矩形检索。
- 滑到底判定：相邻两次滑动后 global_task_list_city_tag 灰度匹配位置不变 → 列表到底，任务视为完成（非失败）。
- 注册三动作：task_type_map.py 加 "Arena Challenge" 映射；configs/task_type.json 加定义段（id=arena_challenge，count=1/max_active=1/requires_march_queue=false/next_trigger_delay=86400，params: challenge_limit=0、buy_times=0）；configs/task_config.json user0/user10 各加 enabled=true 实例条目。
- Skill B（.agents/skills/new-task-development/SKILL.md）：流程总览新增第 1 步"建功能分支"，新增 §11.2 功能分支规范，检查清单补分支项，定义段文件名纠正为 task_type.json（defaults 七键含 max_failures）。

### 修复点
- 无（新任务，无历史缺陷修复）。

### 涉及文件
- src/sg/scene/elements.py（16 元素）
- src/sg/tasks/city/ArenaChallengeTask.py（新任务类）
- src/sg/tasks/task_type_map.py（映射）
- configs/task_type.json（定义段）
- configs/task_config.json（user0 enabled=true / user1 enabled=false 实例条目）
- .agents/skills/new-task-development/SKILL.md（分支规范）
- docs/PROJECT_REQUIREMENTS.md（本节）

### 测试
- 冒烟通过：两个 JSON 解析 OK；config_loader.parse_document 解析出 arena_challenge 实例（enabled=true/count=1/max_active=1/delay=86400/params 两项注入全对）；TASK_TYPE_MAP 与 task_type.json 双向一致（23 types）；实例段全部 id 有定义。
- parse_times_text 单测 7 用例全过（'3/5'/'12'/'1,200'/'5 / 10'/'无'/None/'剩余 3 次'）。
- 类级冒烟：16 元素可导入，ArenaChallengeTask 接口齐全（run_interaction/_run_once/run/check_completed/_step/last_error/extra_config/challenge_limit/buy_times）。
- 实机已跑一次：task_excute_time.json 记录 arena_challenge 样本 1 次（avg≈33.4s），成功走通加载链；更完整的实机回归待后续观察。

### 遗留 / 风险
- 实机仅跑通一次，尚未充分回归：16 个特征模板已在 ok_templates/coco_annotations.json 有标注（id 278-288/299/300-303），但识别率与 bbox 精度需真机确认；"弓兵兵营旁边-竞技场"为 bbox 直点，依赖 COCO 标注坐标准确。
- 绿色 RGB(3,208,111) ±30 容差是对屏幕渲染颜色的假设，实际对手列表若带渐变/暗色滤镜可能漏检，需真机调整 green_color_range。
- 上滑滑到底判定基于 city_tag 位置不变，若任务列表滑动时标签一起移动（吸顶除外）会误判提前结束。
- 购买次数 OCR 失败时直接点消耗钻石确认（不做增减），若当前值远大于目标会多买，需真机确认 OCR 稳定性。

---


## [2026-10-28] 黄金科技研究（Golden Tech Research）

### 原始需求
进入城市主界面，点击任务列表，在「科技研究」分区定位黄金科技研究入口-1（必要时在任务列表搜索区域内循环上滑检索）。检索到入口后，先判断其下方状态特征"黄金科技研究状态-1-状态判断-空闲"是否为「空闲中」：不满足（已有研究在进行）则返回失败，不需重试。满足则点击入口，游戏跳转至战争学院建筑，点击黄金科技-2 进入科技树界面，点击黄金科技研究-3 定位当前可研究节点，按配置的兵种优先级在 皇家科技-4-弓-科技1 / 黄金科技-4-骑-科技1 / 黄金科技-4-步-科技1 三个互为备选的分支节点中选择其一点击。弹出研究确认弹窗后，点击黄金科技研究-5-确认按钮-预估时间框中的「研究」按钮开始研究。研究启动后在黄金科技研究-6-状态时间-互助底栏 OCR 剩余研究时间，并点击「联盟互助」，返回主界面，任务返回成功以及研究完成时间。参数：兵种优先级下拉（弓/骑/步）；子项研究特征名（可直接配置 coco 中的对应特征 name）。

### 变更点
- 新增 10 个 SceneElement 常量（coco id 268-276、304）：TASKLIST_TECH_RESEARCH_SECTION / GOLDEN_TECH_RESEARCH_ENTRY / GOLDEN_TECH_RESEARCH_STATUS_IDLE / GOLDEN_TECH_ACADEMY / GOLDEN_TECH_RESEARCH_NODE / GOLDEN_TECH_ARCHER_NODE_1 / GOLDEN_TECH_CAVALRY_NODE_1 / GOLDEN_TECH_INFANTRY_NODE_1 / GOLDEN_TECH_CONFIRM_RESEARCH_BUTTON / GOLDEN_TECH_STATUS_TIME_HELP。
- 新增 src/sg/tasks/city/GoldenTechResearchTask.py：开任务列表 → 上滑检索黄金科技研究入口 → 空闲状态门槛判断 → 点击入口 → 战争学院 → 科技树可研究节点 → sub_tech_names 子项特征优先、troop_priority（弓/骑/步）默认分支三选一（优先项未出现时回退其余两个分支）→ 确认弹窗点「研究」→ 状态底栏 OCR 剩余时间 + 点击联盟互助 → 回主界面。
- 调度语义对齐 ClaimWarehouseReward：SUCCESS 回传 wait_seconds=剩余研究时间，队列置 IN_PROGRESS，check_completed 到点放行；注册 count=1/max_active=1/requires_march_queue=false/next_trigger_delay=0。
- 入口非「空闲」（研究进行中）或列表滑到底无入口 → FAILED 不重试（需求原文）。
- 注册三动作：task_type_map.py 加 "Golden Tech Research" 映射；task_type.json 加定义段（id=golden_tech_research，params: troop_priority 下拉、sub_tech_names 文本、default_retry_wait=3600）；task_config.json user0/user10 各加 enabled=true 实例条目（params 留空用默认值）。

### 修复点
- 无（新任务，无历史缺陷修复）。

### 涉及文件
- src/sg/scene/elements.py（10 元素）
- src/sg/tasks/city/GoldenTechResearchTask.py（新任务类）
- src/sg/tasks/task_type_map.py（映射）
- configs/task_type.json（定义段）
- configs/task_config.json（user0/user10 enabled=true 实例条目）
- docs/PROJECT_REQUIREMENTS.md（本节）

### 测试
- 冒烟通过：elements.py 编译 OK，248 个常量无重名、resource_id 无重复；TASK_TYPE_MAP import OK（24 types），golden_tech_research → GoldenTechResearchTask；config_loader.load() / load_type_document() 解析通过，实例条目与定义段全部装载；TASK_TYPE_MAP 与 task_type.json 按 id 双向一致（全部实例 id 有定义且可映射）。
- 类级冒烟：任务类 import OK，__init__ 注入 3 个参数字段（troop_priority/sub_tech_names/default_retry_wait），全部引用元素存在；任务类遵循工厂 kwargs→setattr 注入契约（task_queue.py:166-168）。
- 独立模式/队列模式实机验证：未跑（本机无模拟器会话，特征模板已入库，待真机验证）。

### 遗留 / 风险
- 10 个特征模板尚未真机验证识别率；黄金科技研究-5 确认按钮在弹窗内，若特征截图为整框而非按钮本体，点击可能落点偏差，需真机确认。
- sub_tech_names 依赖 get_box_by_name（bbox 直点），子项特征必须是 coco 已标注且有 bbox 的资源。
- 研究进行中返回 FAILED 走 max_failures 统计（当前 max_failures=0 不停止），若日志噪音明显可改为入口状态判断前置 RETRY 语义，需真机观察后决定。
- 状态底栏「联盟互助」直接点特征框中心，若框内互助按钮不在中心需真机调整点击偏移。

---

## [2026-10-28] 联盟商店购买（League Shop Buy）

### 原始需求
进入主界面（城市或世界，不在主界面则先 ESC 退回主界面），点击 global_tag_league 进入联盟界面，点击 league_shop_entry 进入联盟商店界面。先确定本次要处理的页签列表：若系统日期为周一，则处理 [今日, 本周] 两个页签；其余星期仅处理 [今日]。对每个待处理页签执行「买空」循环：在商品列表区域循环灰度检索 联盟商店-3-3折 折扣角标，每匹配到一个即点击其所在商品卡片，弹出「确定购买」弹窗后点击 联盟商店-4-购买确认（数量保持默认 1），弹窗关闭后回到商品列表重新检索，直到该页签下再也匹配不到 3 折角标为止。防御分支：点击角标后若超时未弹出确认弹窗（商品已售完/角标残留），记录其位置不再点击，继续检索其余角标，防止死循环。整个任务从头到尾一次都未匹配到 3 折角标则返回失败，不需要重试。全部买空后返回主界面，任务返回成功；下次触发时间按配置固定间隔（商店顶部「下次刷新」倒计时暂无 coco 特征，不做 OCR）。

### 变更点
- 新任务 LeagueShopBuyTask（src/sg/tasks/league/LeagueShopBuyTask.py）：回主界面 → GLOBAL_TAG_LEAGUE → LEAGUE_SHOP_ENTRY → 按星期选页签（周一 [今日,本周]，其余 [今日]）→ 逐页签买空 → 回主界面。不占军队队列，next_trigger_delay=86400 每日一次，不可并行 count=1/max_active=1，无 params。
- 页签状态机（灰度匹配，模板语义已按 coco 裁剪核实）：LEAGUE_SHOP_TAG_TODAY_SELECTED（今日-选中态 浅底深字）命中 或 LEAGUE_SHOP_TAG_WEEK_UNSELECTED（本周-未选中态 深底白字）命中 → 在今日页；两者都不命中 → 在本周页。页签点击热区 coco 无特征（需求允许），用 get_box_by_name 取页签标注 bbox 直接点击；切换成功用状态特征消失/重现验证。
- 买空循环用 find_feature 多匹配（非 _wait_element 单匹配）：同屏多角标时最高置信度可能恰是已跳过位置，多匹配才能继续选其余角标，避免误判买空。弹窗超时的角标位置记入 _skipped_positions（40px 网格量化去重，上限 32），_find_badge 自动跳过；单页签购买次数上限 60 防异常死循环。
- 注册三动作：task_type_map.py 加 "League Shop Buy" 映射；task_type.json 加定义段（id=league_shop_buy，params=[]）；task_config.json user0 enabled=true、user10 enabled=false 实例条目。
- elements.py 追加联盟商店段 4 个特征常量（254/255/259/260）。

### 修复点
- 无（新任务，无历史缺陷修复）。

### 涉及文件
- src/sg/scene/elements.py（4 元素：LEAGUE_SHOP_BUY_CONFIRM / LEAGUE_SHOP_TAG_TODAY_SELECTED / LEAGUE_SHOP_SALE_BADGE / LEAGUE_SHOP_TAG_WEEK_UNSELECTED）
- src/sg/tasks/league/LeagueShopBuyTask.py（新任务类）
- src/sg/tasks/task_type_map.py（映射）
- configs/task_type.json（定义段）
- configs/task_config.json（user0 enabled=true / user10 enabled=false 实例条目）
- docs/PROJECT_REQUIREMENTS.md（本节）

### 测试
- 注册冒烟通过：config_loader.load() 装载 League Shop Buy 定义段与 user0 enabled 实例条目；TASK_TYPE_MAP 与 task_type.json 双向一致（25 types），league_shop_buy → LeagueShopBuyTask，Arena Challenge / Hero Enlist 映射完整无缺。
- 类级冒烟通过：任务类 import OK（继承 SGBaseTask，14 方法齐全），星期分支逻辑验证（周一=0 → [today,week]，其余 → [today]），4 个特征常量 resource_id 与 coco 一致。
- 独立模式/队列模式实机验证：未跑（本机无模拟器会话，特征模板已入库，待真机验证）。

### 遗留 / 风险
- 4 个特征模板未真机验证识别率；尤其 LEAGUE_SHOP_BUY_CONFIRM 模板含币图标+具体价格「7,800」，不同商品价格不同，若匹配率低需换成纯按钮底色特征（需补充 coco 标注）。
- 3 折角标模板只覆盖 -70% 红色角标；其他折扣档位（如绿色 -40%）不在购买范围，若需求扩大需补特征。
- 页签切换依赖 get_box_by_name 静态标注 bbox（900x1600 标注分辨率），分辨率适配由框架缩放机制承担，真机需确认点击落点。
- 「确定购买」弹窗若被其他系统弹窗（如部队抵达通知条）遮挡，确认按钮匹配可能失败走防御分支跳过该商品，下轮重检会再试，不影响买空正确性。

---


## [2026-10-28] 联盟商店购买 — 最小折扣多选参数

### 原始需求
加一个参数「最小折扣」，多选，可选 3 折、6 折、原价，默认勾选 3 折；选择多个则购买多个折扣档位。折扣对应特征：3折=联盟商店-3-3折、6折=联盟商店-3-6折、原价=联盟商店-3-原价（coco id 259/305/306，后两者为本需求新增标注）。

### 变更点
- elements.py 追加 LEAGUE_SHOP_SALE_BADGE_60（联盟商店-3-6折）/ LEAGUE_SHOP_SALE_BADGE_FULL（联盟商店-3-原价）两个角标常量。
- LeagueShopBuyTask：新增队列注入字段 min_discount（list，默认 ["3折"]）；类常量 DISCOUNT_BADGE_MAP 档位→特征映射；_active_badge_elements() 按参数解析角标列表（去重保序，非法档位忽略）；_find_badge() 改为逐档位多匹配检索同一帧先到先得；日志/失败文案从「3 折」改为「所选折扣」。
- task_type.json 定义段 params[] 增加 Min Discount（attr=min_discount，widget=multi_drop_down options=[3折,6折,原价]，default=["3折"]），更新 description。
- 连带修复 UI 既有 bug：TaskConfigEditorTab._multi_toggle 全仓无定义（multi_drop_down 勾选即 AttributeError），按 _drop_changed 同款契约补实现（勾选加入/取消移除、字符串比较、_mark_dirty）。

### 修复点
- TaskConfigEditorTab._multi_toggle 缺失（既有 bug，multi_drop_down 控件首次实际使用即暴露）。

### 涉及文件
- src/sg/scene/elements.py（+2 元素）
- src/sg/tasks/league/LeagueShopBuyTask.py（min_discount 字段 / DISCOUNT_BADGE_MAP / _active_badge_elements / _find_badge 多档位）
- configs/task_type.json（定义段 params）
- src/ui/TaskConfigEditorTab.py（_multi_toggle 补缺）
- ok_templates/coco_annotations.json（305/306 新特征标注，用户外部新增）

### 测试
- config_loader.load() 通过：min_discount 默认值 ["3折"] 正确装载；TASK_TYPE_MAP 与 task_type.json 双向一致（25 types）。
- 任务类逻辑验证（绕开框架构造）：档位映射 resource_id 与 coco 一致；多选保序去重；空选/非法档位返回空；_pos_key 位置键正确。
- _multi_toggle 逻辑验证（stub _mark_dirty）：勾选/重复勾选/取消、params 写入、dirty 计数全部通过。
- 实机验证：未跑（待真机确认 6 折/原价角标识别率）。

### 遗留 / 风险
- 「原价」角标若与商品卡底色对比度不足，灰度匹配率待真机确认；匹配不到原价角标时任务行为与「全部页签无所选折扣 → FAILED」语义一致（勾选原价且全无原价商品时会判 FAILED，属预期但需用户知晓——勾选越多档位，FAILED 越难触发）。
- _multi_toggle 存 params 为字符串列表（["3折"]），与 drop_down 的字符串值不同型；loader 直接透传无类型校验，UI 回显按 str(x) 比较，闭环成立。

---


## [2026-10-29] 联盟总动员（League Mobilization，OCR 文字匹配版）

### 原始需求
联盟总动员-刷新高效指定-ocr文字匹配版。主界面点 global_entry_common_activity 进常规活动，页签滑动区域循环滑动灰度匹配 联盟总动员-1-任务入口-灰度（顶部奖杯页签）进入。主循环每轮先 OCR 联盟总动员-2-剩余次数-ocr 解析"可接次数 x/20"，x+购买当日次数 y=0 则 FAILED（无多余次数）；y>0 且 x=0 时逐次购买（购买次数入口 → 第一次免费直接点 / 消耗钻石面板提交）。专属任务1/2 依次：点击卡槽 → OCR 任务描述区域（去换行）与 专属任务目标 多选参数（训练12万"通过训练/晋升士兵提升120000点实力"/黄金150/巨兽5/10/15次）包含匹配，命中点 4-接受按钮，未命中点 4-刷新按钮并 OCR 刷新倒计时，两个卡槽取更近时间回传。公共任务：在 任务滚动窗口 向上滑动（幅度=公共任务滚动幅度参数，默认0.3）检索 公共任务目标 多选项；基准点+偏移区域构成卡片 bbox，bbox 内匹配 奖励标志=未接任务，点开 OCR 4-公共任务-描述 校验后 接受/刷新（受 公共任务刷新 开关控制）/关闭；刷新开启时最后向下滑回 滚动区域-上沿，再按基准点行高（最大y+height+5 起点、行高差为距）逐行向下清扫未接卡片：命中 公共任务刷新目标 保留（关闭窗口），未命中刷新。不占军队队列、立即触发、不可并行（count=1）。

### 变更点
- 新增 `src/sg/element/overall/activity/mobilization.py`：Mobilization 领域对象（进界面滑动灰度检索、剩余次数 OCR、购买次数两分支、专属卡槽处理、公共任务阶段 A 检索 + 阶段 B 逐行清扫、倒计时解析、基准点行高滑动计算）。
- 新增 `src/sg/tasks/league/LeagueMobilizationTask.py`：聚合任务（编排 + 次数主循环 + 刷新倒计时取更近回传 wait_seconds）。
- `src/sg/scene/elements.py` 追加 39 个 LEAGUE_MOB_* 元素（与 coco id 247–335 一一对应，双向校验 0 缺失 0 多余）。
- `src/sg/tasks/task_type_map.py` 加 `"League Mobilization": LeagueMobilizationTask`（第 26 种）。
- `configs/task_type.json` 加定义段：defaults（count=1/max_active=1/requires_march_queue=false/next_trigger_delay=0），params 6 项——专属任务目标/公共任务刷新目标/公共任务目标均为 multi_drop_down（选项 训练12万/黄金150/巨兽5/巨兽10/巨兽15，默认勾选训练12万），购买当日次数 number 默认 1，公共任务刷新 switch 默认 false，公共任务滚动幅度 number 默认 0.3。
- `configs/task_config.json` 两个 user 链各追加 `{"id": "league_mobilization", "enabled": true, "params": {}}` 实例条目。

### 修复点
- 初版领域对象偏移 bbox 误用 `box_of_screen` 的相对比例 width/height（该方法按比例而非像素取参），改为全屏 box 基底覆写像素 x/y/width/height（Box 可变）。
- 公共任务滑动参数错误落在 swipe 调用尾部造成语法损坏，重写 `_scroll_window_px` 修复。

### 涉及文件
- src/sg/element/overall/activity/mobilization.py（新增）
- src/sg/tasks/league/LeagueMobilizationTask.py（新增）
- src/sg/scene/elements.py（追加 39 元素）
- src/sg/tasks/task_type_map.py（+1 映射）
- configs/task_type.json（+1 定义段）
- configs/task_config.json（2 个实例条目）
- docs/PROJECT_REQUIREMENTS.md（本节归档）

### 测试
- 独立模式：未跑（需真机游戏前台；run() 入口已按 HuntScareWolf/HeroEnlist 模式实现）。
- 队列模式：未跑（真机确认）。
- 注册冒烟：config_loader.load() 通过；定义段 id ↔ type ↔ TASK_TYPE_MAP 三向一致（26 种）；两条 user 链均解析出 enabled=true 的 league_mobilization 实例且 params 六键齐全；elements.py 39 元素与 coco 全集 0 缺 0 多；4 个改动文件 py_compile 通过。

### 遗留 / 风险
- 目标关键词匹配按参数顺序首个命中（训练12万→"训练"、黄金150→"黄金"、巨兽5/10/15→"巨兽"）；巨兽三档 OCR 文案若不含具体次数数字，无法区分 5/10/15 次——需真机 OCR "击败巨兽N次" 原文确认后细化关键词。
- 公共任务卡片图标特征（任务目标-训练/黄金/巨兽等）已入库但当前匹配全走 OCR 文案（需求即为 OCR 版）；若真机描述区域 OCR 不可靠，可在 MOBILITY_GOALS 补 element 字段切回特征匹配。
- 钻石购买面板按默认数量直接提交（需求：剩余次数>0 时按需增减）；若真机默认数量≠1 需补 增/减 调节逻辑（元素已备好）。
- 阶段 B 清扫的"保留=关闭窗口"分支：关闭窗口是否影响可接次数待真机确认。
- 刷新确认弹窗（联盟总动员-5-界面-刷新确认界面 / 5-刷新确认按钮）已入库未接线：专属刷新分支当前点 4-刷新按钮后直接 OCR 倒计时；若真机刷新后必须二次确认，需在 run_exclusive_slot 刷新分支插入确认步骤。

---


## [2026-10-29] 联盟总动员 — 修订一轮（接取节奏 / 专属预检 / 优先级与门控）

### 原始需求
用户实测反馈修订：
1. 可接次数是"可接任务数"（每次只能接 1 个），不是一轮能接完的总量——去掉轮末重读次数的主循环，一轮流程 = 次数判断 → 专属 → 公共（按门控）→ 结束回传。
2. 专属任务1/2 点击前先 OCR 卡面：strict 解析出刷新倒计时 = 正在刷新，跳过该卡槽后续操作，倒计时记入下次触发候选。
3. 公共任务刷新目标 参数未勾选任何值 → 不做公共任务刷新（含清扫）。
4. 公共任务目标 未配置 → 不做公共任务接受。
5. 优先级：专属优先；两个专属卡槽预检都在刷新且 刷新完成时刻已到明天（now + 倒计时跨过当日 24 点）才放行公共任务接受；公共任务的刷新操作只受 公共任务刷新 开关控制。

### 变更点
- `Mobilization.read_refresh_time(text, strict=False)`：strict 模式只认 中文单位（含"天"）/冒号格式，裸数字不视作倒计时——修复任务描述"训练120000点"里的 120000 被误判成 120000s 倒计时的隐患；非 strict 保留裸数字兜底（刷新后 OCR 用）。
- `Mobilization.check_slot_refreshing(slot_element)`：点击前预检，OCR 卡面 strict 解析，返回倒计时秒数或 None。
- `Mobilization.run_exclusive_slot`：接入预检，新增 action='refreshing'（卡槽刷新中，直接返回倒计时）。
- `LeagueMobilizationTask._run_exclusive_slots`：返回 (state, ok)，state='busy'（两卡槽均刷新中）/ 'free'；倒计时取更近者写入 next_wait_seconds。
- `LeagueMobilizationTask._public_ready`：两卡槽预检均出倒计时且均跨天 → True；任一卡槽可今天内刷完 → False（专属优先）。
- `LeagueMobilizationTask._run_public_tasks`：门控——公共目标空 → 跳过接受；刷新关 → 不刷新；刷新开但刷新目标空 → 不清扫。
- `LeagueMobilizationTask.run_interaction`：删除轮末重读次数循环（用户已先行注释），串入 专属 busy/跨天 → 公共 放行链。

### 修复点
- read_refresh_time 初次编辑残留重复 unit_pairs 块，已清理。

### 涉及文件
- src/sg/element/overall/activity/mobilization.py
- src/sg/tasks/league/LeagueMobilizationTask.py
- docs/PROJECT_REQUIREMENTS.md（本节）

### 测试
- 行为验证（mock 驱动，9 场景全过）：strict 解析（120000/150 不误判、8小时30分/01:23:45/12:34/1天 正确）；跨天判定（23:00+2h 跨天 / 23:00+30m 不跨 / 09:00+16h 跨天）；专属编排（双 refreshing→busy 且取近值 3600、accepted+refreshed→free、失败传播、refreshing+accepted→free 且倒计时记录）；公共门控矩阵（刷新关→不清扫、刷新开+目标勾选→清扫、刷新开+目标空→不清扫、公共目标空→全跳过）。
- 独立/队列模式：待真机。

### 遗留 / 风险
- "已到了明天"判定基于本机时钟 + OCR 倒计时；若游戏刷新时刻与卡面倒计时口径不一致（如领取后有缓冲），边界 ±分钟级误差可接受。
- 专属目标未配置时 state='free' 且公共任务直接放行（无专属可接 = 不占用专属名额）——如需"未配置专属也要求卡槽空闲才接公共"需再确认。

---


## [2026-10-31] 联盟总动员 — 任务目标参数 name:desc 化

### 原始需求
三个目标参数（专属任务目标 / 公共任务目标 / 公共任务刷新目标）的 options 此前只有 "训练12万/黄金150/巨兽5..." 简单描述，没有每个任务的明确判断文案。要求 option 为 name:desc 结构：下拉/勾选展示 name，desc 作为 OCR 判断值（如 训练12万：通过训练/晋升士兵提升120000点实力）。_match_goal_by_text 用配置值解析出的 desc 做包含匹配。

### 变更点
- 方案 A（options 自编码），未加"任务目标维护"独立参数：options 直接写成 name:desc 串——勾选值自包含判断值，运行时零反查、编辑器零改动（multi_drop_down 复选框 label 全文展示 name:desc）。
- task_type.json：三参数 options 换成 5 条 name:desc（训练12万/黄金150/巨兽5/巨兽10/巨兽15 各带完整 OCR 文案），default 同步为新格式。
- task_config.json 两个 user 链的存量值升级（"训练12万" → "训练12万:通过训练/晋升士兵提升120000点实力"）。
- mobilization.py：新增模块级 parse_goal_value / goal_names / goal_desc；_match_goal_by_text 重写——desc 包含匹配（空白归一），返回 (name, 原配置值)；两个调用点日志改用 goal[0]。
- 降级兼容：不含冒号的旧格式值按 LEGACY_KEYWORDS 短词回退（训练12万→"训练"、黄金150→"黄金"、巨兽N→"巨兽"），未知名回退 name 本身。

### 涉及文件
- configs/task_type.json（options/default）
- configs/task_config.json（2 处实例存量值）
- src/sg/element/overall/activity/mobilization.py（解析 + 匹配重写）
- src/sg/tasks/league/LeagueMobilizationTask.py（默认值/注释同步）

### 测试
- 行为验证 12 例全过：name:desc 解析、旧格式回退、desc 空白归一匹配（含换行 OCR 文本）、巨兽 N 次区分（10 命中巨兽10、8 不命中）、优先级顺序、空列表。
- loader 冒烟：load() 通过、26 任务 id↔type↔MAP 一致、两 user 链新值注入正确。
- TaskConfigEditorTab.py 无需改动（multi_drop_down str(opt) 展示即 name:desc 全文）。

### 遗留 / 风险
- 选项编辑仍走定义段的"选项,逗号分隔"输入框（逗号分隔 name:desc 串）；desc 内含逗号会被拆断——当前 5 条 desc 无逗号，后续新增目标时 desc 避免用逗号（或改在 JSON 里直接编辑）。
- desc 判断为包含匹配：若游戏改文案（如"提升120,000点"加千分位），需同步更新 options 的 desc。

---

