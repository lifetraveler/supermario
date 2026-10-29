# 变更记录：TaskQueue 调度循环优化（固定轮询 → 事件驱动）

日期：2026-10-30　分支：`feature/scheduler-event-driven`（基于 master a878c5f）
方案：DeepSeek 建议（local://paste-1.md）落地版，计划文件 `.agents/plans/scheduler-event-driven.md`。

## 改动一览

### 批次 1：`src/scheduler/task_queue.py`（1153 → 1338 行）

| 项 | 内容 |
|---|---|
| wake_callback | 队列状态变化时通知主循环提前 tick；回调异常吞掉记 debug，绝不拖垮调度 |
| version | 对外可见状态变化的单调计数（UI 刷新门控）；`_bump()` = version+1 + 唤醒通知，是所有"观感变化"的统一出口 |
| `_set_status(task, st)` | 状态赋值统一入口：赋值 + IN_PROGRESS 计数同步 + `_bump`；队列内 11 处 `task.status` 直赋值全部迁移 |
| `_count_in_progress` | O(N) 全量扫 → dict O(1) 读（`_in_progress_count`，key=factory_key；remove_task/clear 同步减） |
| `next_wakeup_time()` | 最早需要 tick 的时间戳：SCHEDULED=trigger_time；PENDING=max(next_retry_time, now)；IN_PROGRESS=estimated_finish_time（None 跳过）；paused/DONE/FAILED/RUNNING 跳过；无候选返回 None |
| `_check_stalled_factories(now)` | ① 签名删 user_id 参数（**bug 修复**：旧实现按"本 tick pick 到的任务的 user_id"过滤，未 pick 到任务时 user_id=None → user_id=0 的工厂全被跳过，卡死永难解除；补建归属本就依赖 `factory.user_id`（create 时盖章），与 tick 参数无关）② 开头建 by_factory 索引，O(F×N) → O(N+F) |

### 批次 2：`src/sg/tasks/queue/QueueTaskBase.py`（325 → 436 行）

| 项 | 内容 |
|---|---|
| 唤醒基础设施 | `threading.Condition` + `_wake_flag`（notify-before-wait 竞态保护）自管等待。不用框架 `TaskExecutor.sleep`：它被 notify 后会重新武装剩余时长，无法提前取消 |
| wake 回路 | `queue.wake_callback = self._notify_wake`；`exit_event.bind_condition(self._wake_cond)`——ok `ExitEvent.set()` 会 notify_all 绑定的 Condition → 用户停止 100ms 内响应（无需轮询兜底） |
| 主循环 | `while not all_done(): tick → _update_info → wait = max(min_sleep, min(next_wakeup_time-now, max_idle_sleep)) → _wait_wake(wait)`；删除固定 `sleep(tick_interval)`；paused 分支同样走 `_wait_wake`（unpause 不经本 Condition，最多 30s 兜底醒） |
| UI 节流 | `_update_info` 按 `queue.version` 门控（version 缺失=老队列对象，回退全量刷新）；`run()` finally 重置 `_last_info_version` 强制刷终态 |
| 配置 | 新增 `"Max Idle Sleep"`(30.0) / `"Min Sleep"`(0.05)；`"Tick Interval"` 键保留但不再驱动节奏（JSON/GUI 兼容留置，描述标注已废弃） |
| 测试工件 | `tests/TestUserRotation.py` 的 `__new__` 构造补 `_wake_cond` 等最小属性集 + mock queue 补 `next_wakeup_time` 桩（行为不变） |

## 测试

- 新增 `tests/TestTaskQueueWakeup.py`（22 tests）：next_wakeup_time 全分支 / wake_callback 触发计数 / 增量计数 vs 暴力比对（固定种子 100×200）/ stalled user_id 修复 / version 门控。
- 新增 `tests/TestQueueTaskBaseCycle.py`（14 tests）：唤醒基元 / 等待时长计算 / 全流程收敛 / exit_event.set() <100ms 退出 / version 门控。
- 基准 `tests/BenchTaskQueue.py`（python tests/BenchTaskQueue.py 可直跑）。

结果：86 tests OK（TestTaskQueueScheduling 21 + TestUserRotation 29 + 新增 36）。
`tests/TestMain.py` 3 个 OCR 错误为改动前已存在（stash 验证，依赖真机画面），与本次无关。

## 微基准（us/tick，越小越好）

```
[1] _activate_scheduled + _pick_next（march 饱和）
N          旧 O(N²)      新 O(N)       加速
100        734.3         31.3          23.5x
1000       73484.5       287.5         255.6x
10000      8058025.8     3240.0        2487.0x

[2] _check_stalled_factories（F=100）
N          旧 O(F×N)     新 O(N+F)     加速
10000      43457.6       868.9         50.0x

[3] _count_in_progress 单次
N          旧            新 O(1)       加速
10000      798.2         0.1           7982x
```

## 行为影响（唯一变化点）

1. tick 节奏：固定 1s 轮询 → 事件驱动（到点/事件唤醒，空闲最长 30s 一醒）。任务触发精度不变（trigger_time 硬约束未动）。
2. 用户停止响应：最长 1s → <100ms。
3. `_check_stalled_factories` 失效场景修复：多 user 队列中 march 名额占满、tick 未 pick 到任务期间，FAILED 工厂的补建现在能发生。
4. `"Tick Interval"` 配置不再影响节奏（保留兼容）；`"Max Idle Sleep"`/`"Min Sleep"` 可调兜底。

架构变更记录已同步追加至 `.agents/skills/automation-framework-arch/SKILL.md` 第八节。
