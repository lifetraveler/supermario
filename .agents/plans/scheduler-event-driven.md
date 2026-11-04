# 开发计划：TaskQueue 调度循环优化（固定轮询 → 事件驱动 + 消除 O(N²)）

分支：`feature/scheduler-event-driven`（自 master，master=a878c5f）
基线文件：`src/scheduler/task_queue.py`（1173 行）、`src/sg/tasks/queue/QueueTaskBase.py`（325 行）
现有回归：`tests/TestTaskQueueScheduling.py`（411 行，unittest + mock，FakeTask 直测纯逻辑）、`tests/TestUserRotation.py`
方案来源：local://paste-1.md（DeepSeek 建议），本文件为落地版，已按源码事实修正。

## 硬性约束（每批完成后自查）

1. 状态机不变：SCHEDULED→PENDING→RUNNING→IN_PROGRESS→DONE/FAILED，触发条件不变。
2. trigger_time 硬约束；next_retry_time 未到不 pick。
3. 失败策略：continue_after_failure / max_failures / factory.exhausted / `_on_task_failed` 三入口不变。
4. one_shot / cron 语义不变（finished_count 收敛、_next_cron_time 本地 datetime、非法表达式标 exhausted）。
5. 多 user 轮转：_rotation_yield_reason、before_interaction、_after_queue_done / _on_queue_stopped 时机不变。
6. get_snapshot() 字段名与结构不变。
7. TaskFactory 字段与 create() 下发属性不变。
8. 公开接口签名不变：tick()/add()/add_factory()/all_done()/get_snapshot() 只加不改；不引第三方库。
9. 既有 43 个测试全绿（注意：make_queue 测试工件需补新增构造参数的默认值时，默认值必须保持零行为变化）。

## 框架事实（已核实，写代码前读一遍）

- ok 框架 `ExitEvent`（`.venv/Lib/site-packages/ok/util/handler.py:11-50`）是 `threading.Event` 子类，
  `set()` 时会 `notify_all()` 所有 `bind_condition()` 注册的 `threading.Condition`。
  → 把自建 Condition 注册到 `exit_event.bind_condition(cond)` 即可获得停止即时唤醒，
  **不需要** 0.2s 分片轮询兜底。
- `TaskExecutor.sleep`（`ok/task/TaskExecutor.py:299-347`）被外部 notify 后会重新武装剩余时长，
  框架自带 `_wake_executor` 无法提前取消 sleep → 必须自建 Condition 自管等待。
- `SGBaseTask.exit_is_set()` 即 `self.executor.exit_event.is_set()`。
- 队列单线程驱动：tick 只从 QueueTaskBase 主循环线程调用；wake_callback 可能从任意线程触发
  （唤醒点都在 tick 线程内，但保险起见 Condition 用法保持 notify-all + 谓词检查）。
- `_check_stalled_factories(now, user_id)` 的 user_id 过滤是真 bug（未 pick 到任务时 user_id=None，
  user_id=0 的工厂全被跳过）；factory.create 已把 factory.user_id 盖章到实例（task_queue.py:187），
  方案 A = 移除 user_id 过滤是安全的。
- `UnifiedQueue._next_wake_time(t)`（UnifiedQueue.py:395-411）已有 per-task 唤醒时间推导，
  TaskQueue.next_wakeup_time() 的语义应与它一致（RUNNING/IN_PROGRESS 有 estimated_finish_time
  用之，PENDING 用 next_retry_time，DONE/FAILED 忽略）。

## B1：TaskQueue 核心改造（独立 session，只动 src/scheduler/task_queue.py + 新测试文件）

### B1.1 wake_callback + version

- `__init__` 加 `self.wake_callback = None`、`self.version = 0`。
- 私有 `_notify_wake()`：`cb = self.wake_callback; cb() if cb else None`，异常吞掉记 debug 日志
  （callback 崩溃不能拖垮调度）。
- version 递增点（`self.version += 1`）：add / add_factory / remove_task / clear /
  _do_interaction 任何状态落点（PENDING/IN_PROGRESS/FAILED/RUNNING）/
  _handle_restart_requested 两分支 / _reschedule_for_next_run 状态改写 /
  _check_stalled_factories 补建或 exhausted / _check_in_progress DONE 或转 PENDING /
  _activate_scheduled 有激活 / _replenish 有建任务 / pause_task / resume_task。
  简化实现：抽 `_bump()` 同时做 version+1 与 notify。
- 唤醒点（调 `_notify_wake`，与 version 同点即可，直接用 `_bump()`）：
  重点必须覆盖：add()、add_factory()、remove_task()、start()、stop()、resume()、
  resume_task()、_do_interaction 的 PENDING/IN_PROGRESS 落点、
  _handle_restart_requested、_reschedule_for_next_run、_check_stalled_factories 补建后。
- start/stop/pause/resume 本身不改状态机，只加 _bump()。

### B1.2 next_wakeup_time()

```python
def next_wakeup_time(self):
    """下一次需要 tick 的最早时间戳；None = 无明确时间点（调用方用兜底间隔）。"""
```
- 遍历 self.tasks（P1 后仍是 O(N)，P2 分桶不做）：
  - 跳过 task.paused、DONE、FAILED。
  - RUNNING：当前交互结束前不该被唤醒打断 → 跳过（tick 由交互返回后自然推进；
    run_interaction 返回路径的 _bump 已保证唤醒）。
  - IN_PROGRESS：estimated_finish_time 非 None → 候选；None → 跳过。
  - SCHEDULED：trigger_time 候选。
  - PENDING：max(next_retry_time, now) 候选（next_retry_time 已过 = 立即可跑 → now）。
- 返回最小候选；无候选 None。
- 注意与 _rotation_yield_reason 的 `_next_wake_time`（UnifiedQueue）语义一致：
  IN_PROGRESS 无 finish 时间的，此处跳过（ Unlimited 情况交给兜底间隔）。

### B1.3 _count_in_progress O(1)

- `__init__` 加 `self._in_progress_count = {}`（key=factory_key → int；不用 defaultdict，
  避免 getattr 意外建键）。
- 抽 `_set_status(task, new_status)` 统一入口：内部
  `old = task.status; task.status = new_status; self._sync_in_progress(task, old, new); self._bump()`。
- `_sync_in_progress`：old==IN_PROGRESS 且 new!=IN_PROGRESS → 计数-1；
  new==IN_PROGRESS 且 old!=IN_PROGRESS → 计数+1。key 用 getattr(task,'factory_key',None)。
- 迁移所有直接赋值 `task.status = ...` 的位置（task_queue.py 内共 11 处：
  add 初始化、_check_in_progress 2、_activate_scheduled 1、_do_interaction 5、
  _handle_restart_requested 2、_reschedule_for_next_run 2——以 grep 为准，全部换 _set_status）。
  add() 里的初始化在 append 前调用 _set_status 时 task 尚未入列，计数按 key 直接加减即可
  （无 key 的任务计数器跳过，_count_in_progress 对无 key 返回 0 语义不变）。
- `_count_in_progress(key)` 改为 `return self._in_progress_count.get(key, 0)`。
- remove_task / clear：移除对象若 status==IN_PROGRESS → 计数-1。

### B1.4 _check_stalled_factories 修复 + O(N+F)

- 签名改为 `_check_stalled_factories(self, now)`（tick 内调用点同步，删掉 user_id 实参与形参）。
- 开头建 `by_factory = {}`：`by_factory.setdefault(getattr(t,'factory_key',None), []).append(t)`。
- 删除 `if factory.user_id != user_id: continue` 两行（方案 A），
  在 docstring 写明：多 user 补建依赖 factory.user_id（create 时盖章），与 tick 参数无关。
- 循环内 `own = by_factory.get(factory.key, [])`。

### B1.5 微基准 + 单元测试

- 新文件 `tests/TestTaskQueueWakeup.py`：
  - next_wakeup_time：SCHEDULED/PENDING(未来/已到)/IN_PROGRESS(有/无 finish)/paused/DONE 全分支。
  - wake_callback：add/add_factory/_do_interaction 各触发一次断言（用列表 append 计数）。
  - _count_in_progress：随机状态流转后与暴力 sum 比对（100 任务 × 200 次流转，固定种子）。
  - stalled 修复：user_id=0 工厂全 FAILED + continue_after_failure=True + 未 pick 到任务的 tick → 必须补建。
  - version：状态无变化的 tick 不递增；有变化递增。
- 新文件 `tests/BenchTaskQueue.py`（可直接 python 运行，不进 CI）：
  N=100/1000/10000 任务下：_pick_next+_activate_scheduled 每 tick 耗时、
  _count_in_progress 新旧实现对比、_check_stalled_factories F=100 时 O(F×N) vs O(N+F)、
  get_snapshot 耗时。输出表格文本。
- 全部测试不依赖游戏画面、不 import ok 框架（TaskQueue 本身 import 了 ok.Logger——测试直接
  import src.scheduler.task_queue 即可，TestTaskQueueScheduling 已证明可行）。

### B1 验收

- `python -m pytest tests/TestTaskQueueScheduling.py tests/TestTaskQueueWakeup.py tests/TestUserRotation.py -q` 全绿。
- Bench 输出贴回，1000 任务 _pick_next 路径耗时下降一个量级（O(N²)→O(N)）。

## B2：QueueTaskBase 主循环改造（独立 session，只动 src/sg/tasks/queue/QueueTaskBase.py + 新测试文件）

### B2.1 配置项

- default_config 追加：`"Max Idle Sleep": 30.0`、`"Min Sleep": 0.05`；
  config_description 同步中英说明（风格对齐现有条目）。
- __init__ 缓存 `self._max_idle_sleep` / `self._min_sleep`（run/cycle 开头从 config 读，
  保持与 "Tick Interval" 相同的读取方式）。

### B2.2 唤醒基础设施

- `__init__`：`self._wake_cond = threading.Condition()`、`self._wake_flag = False`。
- `_wait_wake(timeout)`：
  ```python
  with self._wake_cond:
      if self._wake_flag:
          self._wake_flag = False
          return
      self._wake_cond.wait(timeout)
      self._wake_flag = False   # wait 返回（含超时）都清旗标
  ```
- `_notify_wake()`：`with self._wake_cond: self._wake_flag = True; self._wake_cond.notify_all()`。
- `_run_queue_cycle` 构造 TaskQueue 后：`self.queue.wake_callback = self._notify_wake`。
- 停止即时唤醒：构造 TaskQueue 后
  `exit_ev = getattr(self.executor, "exit_event", None)`；
  `hasattr(exit_ev, "bind_condition") and exit_ev.bind_condition(self._wake_cond)`。
  ExitEvent.set() 会 notify_all 该 Condition → _wait_wake 立刻返回 → 主循环查 exit_is_set() 退出。
  用 getattr+hasattr 防御（测试环境 executor 是 mock）。

### B2.3 主循环改写（_run_queue_cycle 第 3 段）

```python
while not self.queue.all_done():
    if self.exit_is_set():
        self.log_info("用户请求停止")
        break
    if self.paused:
        self._wait_wake(self._max_idle_sleep)
        continue
    if self._rotation_yield_reason() is not None:
        self._update_info()
        break
    self.queue.tick()
    self._update_info()
    next_ts = self.queue.next_wakeup_time()
    if next_ts is None:
        wait = self._max_idle_sleep
    else:
        wait = next_ts - time.time()
    wait = max(self._min_sleep, min(wait, self._max_idle_sleep))
    self._wait_wake(wait)
```
- 删除 `tick_interval` 局部变量与 `self.sleep(tick_interval)`（Tick Interval 键保留在
  default_config 不删，JSON/GUI 不动，只是不再驱动节奏——写注释说明）。
- paused 分支同样用 _wait_wake（resume 的 queue.resume() 已带 _bump → 立即醒）。
  注意 QueueTaskBase.paused 是 ok 框架属性（task.py:1337 @property _paused），
  用户 unpause 走 executor.start() → _wake_executor，不会触发本类 _wake_cond；
  paused 兜底唤醒 = Max Idle Sleep（30s），可接受，注释说明。
- SGBaseTask.sleep 可被 sys.exit 打断（exit_event set 时 TaskExecutor.sleep 内 sys.exit(0)），
  _wait_wake 不用 sys.exit 路径——主循环下一轮 exit_is_set() 检查即可，行为等价且更干净。

### B2.4 UI 刷新节流（version 门控）

- __init__ 加 `self._last_info_version = -1`。
- _update_info 开头：
  ```python
  if self.queue is None:
      return
  version = getattr(self.queue, "version", None)
  if version is not None and version == self._last_info_version:
      return
  if version is not None:
      self._last_info_version = version
  # ...原 get_snapshot 循环...
  ```
  （version 缺失=老队列对象，行为回退全量刷新，防御性兼容。）
  run() finally 里的 _update_info() 调用保持不变（队列已终态，version 不变会跳过——
  无妨，finally 前最后一次 tick 已刷过；若担心：finally 里先 _notify 无必要，保持简单）。
  注意：finally 的 _update_info 若被 version 门控跳过，最终态显示可能滞后一次——
  解决：finally 里改为 `self._last_info_version = -1; self._update_info()` 强制刷。
- get_snapshot 内 scheduled_items 截断（P1-3 第 3 点）**不做**——字段结构风险大收益小，
  _fmt_clock 开销在 version 门控后已大幅摊薄。若集成时发现确有需要再议。

### B2.5 测试

- 新文件 `tests/TestQueueTaskBaseCycle.py`：不依赖游戏画面。
  - 构造最小 QueueTaskBase 子类（_build_factories 塞 FakeTask 工厂；executor/app/scene 用 mock，
    参考 TestTaskQueueScheduling 的 FakeTask/make_queue 模式）。
  - next_wakeup_time 驱动的等待时长：mock queue.next_wakeup_time 返回 now+0.3，
    断言 _wait_wake 收到 [0.3, 0.3] 量级的值（patch time.time 与 Condition.wait 记参）。
  - Max Idle Sleep 兜底：next_wakeup_time 返回 None → wait==Max Idle Sleep。
  - 唤醒打断：_wait_wake 挂起中调 _notify_wake → 立即返回。
  - UI 节流：同 version 连续两次 _update_info，info_set 调用次数只增一次；
    version 变化后再刷。
  - 退出：exit_event（真 threading.Event，不用 mock）set 后主循环 100ms 内退出
    （bind_condition 生效证明）。
  - 回归：跑一次全流程 FakeTask 队列，断言全部 DONE、返回值 "done"。

### B2 验收

- `python -m pytest tests/TestQueueTaskBaseCycle.py tests/TestTaskQueueScheduling.py -q` 全绿。

## 集成（主 session 做）

1. 两 session 产出合并后：全量 `python -m pytest tests/ -q`。
2. 冒烟：写临时脚本跑 UnifiedQueue 纯逻辑（mock executor）确认 21 工厂构建 + tick 推进 +
   DONE 收敛路径不回归（不连真机）。
3. CHANGELOG：改动文件顶部或 `.agents/plans/` 下追加简短记录；
   skill 架构变更表（.agents/skills/automation-framework-arch/SKILL.md 追加式变更记录）加一行：
   事件驱动调度改造（wake_callback/version/next_wakeup_time/_set_status 计数器/stalled user_id bug 修复），
   兼容性：调度语义不变，tick 节奏从固定 1s 变事件驱动，Tick Interval 键保留但不再驱动节奏。
4. 分两批提交：
   - `perf(scheduler): TaskQueue 事件唤醒 + O(1) IN_PROGRESS 计数 + stalled 扫描修复`
   - `perf(queue): QueueTaskBase 动态休眠 + Event 唤醒 + UI 节流`

## 会话协议（两个并行 session 共同遵守）

- B1 只改 src/scheduler/task_queue.py 与新增 tests/TestTaskQueueWakeup.py、tests/BenchTaskQueue.py。
- B2 只改 src/sg/tasks/queue/QueueTaskBase.py 与新增 tests/TestQueueTaskBaseCycle.py。
- B2 依赖 B1 的接口名（next_wakeup_time / version / wake_callback），但**不依赖实现**：
  B2 按 B1.1/B1.2 的签名直接写，集成时自然对齐。
- 不跑 git 提交；不跑全仓库测试套件（主 session 集成时统一跑）。
- 产出报告：改动点列表 + 测试输出原文。
