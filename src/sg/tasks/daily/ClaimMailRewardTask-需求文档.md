# 消息中心领奖（Claim Mail Reward）

## 需求
每日自动领取消息中心下的所有可领奖励，并清理已读消息。

## 流程
1. 前台 → 清弹窗
2. ESC 回主界面（幂等）
3. 打开消息中心：`global_icon_message_entry`
4. 遍历 联盟 / 系统 / 报告 三个标签（战斗标签无奖励，不遍历）：
   每个标签内先删除已读消息（`message_button_del` → `message_button_del_confirm`）
   → 检测奖励标记 `message_tag_reward_icon`，无标记跳过
   → 有标记则全屏循环点 `message_button_rec`，连续 2 轮找不到或累计 5 次 → 下一标签
5. ESC 关闭消息中心

## 涉及元素
- 入口：`global_icon_message_entry`
- 标签：`message_tag_league` / `message_tag_sys` / `message_tag_report`（战斗标签 `message_tag_war` 无奖励，不遍历）
- 删除已读：`message_button_del` → `message_button_del_confirm`
- 奖励标记：`message_tag_reward_icon`（标签页"有可领奖励"角标）
- 领取：`message_button_rec`（全屏检索循环）
- 领取后标识：`message_reward`

## 验收标准

- [x] 导入链完整：elements / Task 类 / 注册模块均可导入，注册表含 `Claim Mail Reward`。
- [x] 注册参数正确：count=1、不占队列（requires_march_queue=False）、每日触发（86400s）、无 max_active。
- [x] Mock 冒烟三场景：
  - 删除已读 + 领取（每标签各 1 条）→ SUCCESS，等待 86400s；
  - 无已读、无奖励标记 → 标签全跳过，仍 SUCCESS；
  - 不在主界面 → ESC 幂等兜底。
- [ ] 真机冒烟：独立模式 `run()`（游戏内打开消息中心实际领取）。
- [ ] 队列模式：GUI 勾选后 SUCCESS，24h 后自动重建同类任务。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-09-28 | 初版实现：elements.py 增 message_* 常量；新增 ClaimMailRewardTask + reg_claim_mail.py | 新需求：每日领取消息中心奖励 |
| 2026-09-28 | 新增删除已读消息步骤（`_del_readed_msg`）；新增奖励标记检测（`message_tag_reward_icon`）跳过无奖励标签；移除战斗标签遍历；入口改为仅 `global_icon_message_entry`；修复 `_claim_current_tag` 多余参数；整理重复定义与未用导入 | 用户实机反馈迭代 |

## 遗留 / 风险

- 领取循环硬上限 5 次/标签（成功 + 未命中累计），删除循环依赖按钮消失自然终止。
- `_wait_and_click_all_screen` 每次全屏检索，领取密集时耗时略长，可接受。
- 真机验证未做（本机无模拟器环境），元素 bbox / 特征阈值（rec=0.85）可能需实机微调。
