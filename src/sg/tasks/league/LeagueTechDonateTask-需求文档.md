# 联盟捐献任务 —— 需求文档

| 项 | 内容 |
|---|---|
| 任务名 | League Tech Donate（联盟科技捐献） |
| 任务类 | `src/sg/tasks/league/LeagueTechDonateTask.py` |
| 注册文件 | `src/sg/tasks/registrations/reg_league_donate.py` |
| 状态 | 已实现（待真机验证） |
| 提出日期 | 2026-09-26 |

---

## 1. 需求描述

联盟捐献任务：捐献材料会奖励联盟币，联盟币可以用来买各种道具。

捐献次数**每 10 分钟恢复一次，上限 25 次**。全部捐完后需要 150 分钟（25 × 6 分钟……即 25 × 10 分钟 = 150 分钟）才能恢复满，因此本任务的最短循环周期为 150 分钟。

## 2. 核心参数

| 参数 | 值 | 依据 |
|---|---|---|
| 占军队队列 | 否 | 纯界面操作，不派兵 |
| 触发间隔 `next_trigger_delay` | 9000 秒（150 分钟） | 10 分钟/次 × 25 次上限 |
| 可否并行 | 否，`count=1` 且无 `max_active` | 同一时刻只能打开一个捐献界面 |

## 3. 操作流程（用户原始描述）

1. 先返回到主界面。
2. 特征匹配联盟 tag（`GLOBAL_TAG_LEAGUE`），点击进入联盟界面。
3. 联盟界面内根据特征联盟科技入口（`LEAGUE_TECH_ENTRY`）匹配到对应位置，点击进入联盟科技页面。
4. 联盟科技页面，根据特征捐献图标（`LEAGUE_TECH_UPDATE`）匹配到要捐献的科技位置，点击弹出捐献交互页面。
5. 捐献交互界面，根据特征剩余次数（`LEAGUE_TECH_UPDATE_REMAIN_TIMES`）获取 bbox，然后在 bbox 区域做一次 OCR，把结果拿到后进行字符串拆分，得到两组数字：第一组是剩余次数，第二组是次数上限。
6. 捐献交互界面根据特征捐献按钮（`LEAGUE_TECH_UPDATE_DONATE`）匹配到按钮进行点击，点击次数为第 5 步 OCR 获取到的剩余次数。
7. 捐献完成后再按第 5 步方式重读剩余次数：次数为 0 则退回主界面；不为 0 则再次调用第 6 步。

## 4. 实现约定（开发中确认）

### 4.1 bbox 直接定位（2026-09-26 两次变更，最终形态）

捐献交互界面内**全程不做特征匹配**，统一用 `get_box_by_name(resource_id)` 直接取 bbox：

- 捐献按钮 `LEAGUE_TECH_UPDATE_DONATE`：`get_box_by_name` 取 bbox，剩余几次就按该 bbox 点击几次（`click_box`）；
- 剩余次数区域 `LEAGUE_TECH_UPDATE_REMAIN_TIMES`：`get_box_by_name` 取 bbox，在 bbox 内 OCR 读剩余次数；
- 点完后重读剩余次数：为 0 → 完成；不为 0 → 递归 `_donate_all()` 再捐。

原因：每次捐献后游戏会弹出 tip，遮挡特征区域，`_wait_element` / `_wait_and_click` 这类特征匹配方式会失败。`get_box_by_name` 按资源的 resource_id 直接取标注 bbox，不做图像匹配，不受弹层影响。

### 4.2 元素资源 id

特征匹配按 `resource_id` 查找，必须与 `ok_templates/coco_annotations.json` 的 categories 一致（小写）。`LEAGUE_TECH_UPDATE_REMAIN_TIMES` 的 `resource_id` 为 `league_tech_update_remain_times`。

| 元素 | resource_id | 用途 |
|---|---|---|
| `GLOBAL_TAG_LEAGUE` | `global_tag_league` | 联盟入口（全局标签） |
| `LEAGUE_TECH_ENTRY` | `league_tech_entry` | 联盟科技入口 |
| `LEAGUE_TECH_UPDATE` | `league_tech_update` | 捐献图标（打开捐献面板） |
| `LEAGUE_TECH_UPDATE_DONATE` | `league_tech_update_donate` | 捐献按钮 |
| `LEAGUE_TECH_UPDATE_REMAIN_TIMES` | `league_tech_update_remain_times` | 剩余次数区域（OCR bbox） |

### 4.3 OCR 解析

- bbox 由 `get_box_by_name(LEAGUE_TECH_UPDATE_REMAIN_TIMES.resource_id)` 直接获取，不做特征匹配；
- `re.findall(r"\d+", text)` 提取数字；
- 第一组为剩余次数，第二组为上限；只有一组时认为就是剩余次数。

### 4.4 防御逻辑

- 每次点击后重读剩余次数：为 0 → 完成；不为 0 → 继续点（防御 OCR 偏少导致没捐完）；
- 读不到剩余次数：已捐过则按成功结束，一次都没捐过才算失败。

## 5. 验收标准

- [ ] 独立模式 `run()` 全流程跑通：进联盟 → 捐献 → 次数清零 → 回主界面。
- [ ] OCR 能正确解析 "剩余/上限" 两组数字。
- [ ] 捐献过程中弹出 tip 时，bbox 位置点击不受影响。
- [ ] 队列模式：任务 SUCCESS 后 9000 秒建立同类后续任务。
- [ ] 次数为 0 时任务直接成功返回，不做无谓点击。

## 6. 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-09-26 | 初版实现 | 新需求 |
| 2026-09-26 | 捐献按钮改为首次特征匹配记录 bbox、后续按位置直接点击，不再特征重匹配 | 捐献后弹出的 tip 遮挡特征，重匹配会失败 |
| 2026-09-26 | 剩余次数读取也改为 `get_box_by_name(resource_id)` 直接取 bbox（原 `_wait_element` 特征匹配弃用）；捐献循环改为按 OCR 次数定长点击 + 递归续捐 | `_wait_element` 底层仍是特征匹配，会被捐献 tip 遮挡而失败；`get_box_by_name` 按 resource_id 直接取标注 bbox，不受弹层影响 |
