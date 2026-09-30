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
