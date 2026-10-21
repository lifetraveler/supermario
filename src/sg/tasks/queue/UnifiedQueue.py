# -*- coding: utf-8 -*-
"""
UnifiedQueue —— 统一队列入口（JSON 配置驱动）。

配置来源：configs/task_config.json（machines 实例段）+
         configs/task_type.json（按 package 分组的定义段）。
  - webui 直接读写该 JSON 即可控制参数；
  - 多设备 / 多应用 / 多分身：解析全部 enabled 的 machine → app → user 链。
    单链（绝大多数场景）行为与老版完全一致；≥2 个 enabled user 时
    进入轮转模式（5.5）：按配置顺序逐 user「切应用 → 跑队列」，
    当前 user 无事可做（跑完 / 超预估 / 距下一任务 > 30 分钟）就让位。

老 GUI 配置（UnifiedQueue.json 的 "{key}: {Field}" 扁平键）已废弃；
TaskQueue / 队列调度逻辑不变，只是工厂参数的来源换成了 JSON。
"""
import time
from pathlib import Path

from qfluentwidgets import FluentIcon

from src.scheduler.task_status import TaskStatus
from src.sg.helper.execution_time_stats import ExecutionTimeStats
from src.sg.tasks.config_loader import (
    TaskConfigError, load, load_document, parse_user_configs,
)
from src.sg.tasks.queue.QueueTaskBase import QueueTaskBase
from src.sg.tasks.task_type_map import TASK_TYPE_MAP


class UnifiedQueue(QueueTaskBase):
    """
    统一队列入口。

    _build_factories 把 task_config.json 里 enabled 的任务条目转成
    TaskFactory；queue 全局参数（tick_interval / continue_after_failure）
    同样来自 JSON。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # UI 上显示的名字（不出现 Queue，用中性词）
        self.name = "任务调度"
        self.description = "统一调度 task_config.json 中启用的任务类型"
        self.icon = FluentIcon.SYNC
        self.group_name="奔奔王国"
        self.group_icon=FluentIcon.CAFE

        # ------------------------------------------------------------------
        # 启动即解析配置：错误（缺文件 / 结构非法 / 引用未定义类型 /
        # type 不在代码侧映射）在这里炸出来，而不是等到 run() 中段。
        # 解析结果存 self._task_config，供 _build_factories /
        # _queue_global_params 消费。
        # ------------------------------------------------------------------
        self._task_config = load(CONFIG_PATH)
        self._resolve_task_classes()
        self.log_info(
            f"[TaskConfig] {CONFIG_PATH} trail={self._task_config['trail']} "
            f"types={len(self._task_config['task_types'])} "
            f"tasks={len(self._task_config['tasks'])}"
        )

        # ------------------------------------------------------------------
        # 多 user 轮转（5.5）：每次 run() 从磁盘解析全部 enabled 链。
        # ≥2 个链 → 轮转模式（按配置顺序逐 user：切应用 → 跑队列）；
        # 1 个链  → 行为与老版完全一致（单 user 单圈）。
        # _rotation_index 在 run() 开头重置。
        # ------------------------------------------------------------------
        self._rotation_users = []
        self._rotation_index = 0
        self._rotation_started_at = 0.0
        self._rotation_deadline = None

    def run(self):
        """每次激活从第一条 enabled 链开始轮转（游标重置）。"""
        self._rotation_index = 0
        return super().run()

    # =========================================================================
    # QueueTaskBase 钩子实现：全局参数来自 JSON
    # =========================================================================

    def _reload_config(self):
        """
        每圈 _run_queue_cycle() 从磁盘重读 task_config.json + task_type.json。

        __init__ 里的 load 只保证"进程启动时配置可用"（启动即炸出结构错误）；
        真正生效的是这次重读——webui / 手改 JSON 后无需重启程序，
        重新启动任务即可拿到最新参数。整圈运行使用同一次快照，
        运行中途改文件不影响进行中的调度。

        多 user 轮转：这里同时解析全部 enabled 链（parse_user_configs）。
        单链场景 _rotation_users 只有 1 项，行为与老版一致。
        """
        doc = load_document(CONFIG_PATH)
        self._rotation_users = parse_user_configs(doc)
        self._task_config = self._rotation_users[0]
        for cfg in self._rotation_users:
            self._resolve_task_classes(cfg)
        self.log_info(
            f"[TaskConfig] reloaded trail={self._task_config['trail']} "
            f"types={len(self._task_config['task_types'])} "
            f"tasks={len(self._task_config['tasks'])} "
            f"users={len(self._rotation_users)}"
        )

    def _resolve_task_classes(self, cfg=None):
        """
        校验 cfg（缺省=当前生效链）的每个 type 都在 TASK_TYPE_MAP 里，
        并把解析出的类写进条目。未知 type 是配置错误：启动即抛。
        """
        if cfg is None:
            cfg = self._task_config
        for entry in cfg["tasks"]:
            ttype = entry["type"]
            task_class = TASK_TYPE_MAP.get(ttype)
            if task_class is None:
                raise TaskConfigError(
                    f"type {ttype!r} 不在 task_type_map.TASK_TYPE_MAP，"
                    f"请在代码侧映射中注册该任务类"
                )
            entry["task_class"] = task_class

    def _queue_global_params(self):
        q = self._task_config["queue"]
        return {
            "tick_interval": float(q["tick_interval"]),
            "continue_after_failure": bool(q["continue_after_failure"]),
        }

    def _on_queue_stopped(self):
        """队列停止后把耗时样本落盘（所有停止路径统一收尾）。"""
        self._persist_execution_times()

    # =========================================================================
    # 队列收尾：按配置切到指定分身用户
    # =========================================================================

    def _after_queue_done(self):
        """
        所有任务自然完成后，按 task_config.json 生效链的 after_done_user_id：
          - 0（默认）：不动，保持当前应用；
          - 非零且 != 当前 user_id：关闭当前应用，再以该用户启动应用。

        设备能力来自 DeviceManager.adb_stop_package / adb_start_package
        （后者带 user_id 时走 am start --user 分身分支）。
        非模拟器 / ADB 环境（capture 非 adb）静默跳过。
        """
        app = self._task_config.get("app") or {}
        package = app.get("package")
        target_uid = app.get("after_done_user_id") or 0
        if not target_uid or not package:
            return
        current_uid = getattr(self.executor.device_manager, "user_id", 0) or 0
        if target_uid == current_uid:
            self.log_info(f"[AfterDone] 目标用户 {target_uid} 即当前用户，跳过切换")
            return
        dm = self.executor.device_manager
        self.log_info(
            f"[AfterDone] 队列完成，切换应用用户: user{current_uid} -> user{target_uid}")
        try:
            dm.adb_stop_package(package, user_id=current_uid)
        except Exception as e:
            self.log_info(f"[AfterDone] 关闭应用失败（继续启动）: {e}")
        try:
            ok = dm.adb_start_package(package, user_id=target_uid)
        except Exception as e:
            ok = False
            self.log_info(f"[AfterDone] 启动用户 {target_uid} 应用异常: {e}")
        if ok is False:
            self.log_info(f"[AfterDone] 用户 {target_uid} 应用启动失败，请检查分身是否安装")
        else:
            # 切完用户后更新 DeviceManager 视角，后续截图/交互跟随新用户
            dm.user_id = target_uid

    # =========================================================================
    # 多 user 轮转调度（5.5）
    # =========================================================================
    # 语义（用户确认）：当前 user「无事可做」时切到下一个 user。
    # 无事可做 = ①队列自然跑完（本圈 done）；②本圈耗时超过预算
    # （Σ 启用任务 est（缺省回退耗时统计均值）× max(1,count)，
    # 全部未知则无预算上限）；③没有 IN_PROGRESS/RUNNING 的任务、
    # 且距本 user 最近的可唤醒任务（SCHEDULED 的 trigger_time /
    # PENDING 的 next_retry_time）超过 30 分钟。IN_PROGRESS（军队
    # 已派出）绝不中途放弃。
    # =========================================================================

    # 距下一个任务超过该秒数即让位（半小时）
    ROTATION_IDLE_GAP = 30 * 60

    def _run_queue_cycle(self):
        """
        覆写：轮转模式下每圈先推进轮转游标并切换应用，再跑队列。
        单链（_rotation_users <= 1）与基类行为完全一致。

        切换失败（启动应用异常 / 分身未安装）→ 跳过该 user 换下一个，
        全部失败 → 正常跑第一链的任务（应用可能已在前台，交给
        DeviceManager 的 ensure_in_front 兜底）。
        """
        if len(self._rotation_users) <= 1:
            return super()._run_queue_cycle()

        self._mark_rotation_started()
        user_cfg = self._next_rotation_user()
        if user_cfg is None:
            # 所有 user 切换失败：回到第一条链硬着头皮跑（打日志告警）
            user_cfg = self._rotation_users[0]
            self._task_config = user_cfg
            self.log_info(
                f"[Rotation] 所有 user 切换失败，回退第一条链 "
                f"{user_cfg['trail']}")
        else:
            self._task_config = user_cfg
        self._rotation_index = self._rotation_users.index(user_cfg)
        self.log_info(
            f"[Rotation] 圈 {self._rotation_index + 1}/"
            f"{len(self._rotation_users)}: {user_cfg['trail']} "
            f"(package={user_cfg['package']}, "
            f"user_id={user_cfg['user_id']}, "
            f"enabled_tasks={sum(1 for t in user_cfg['tasks'] if t['enabled'])})"
        )
        return super()._run_queue_cycle()

    def _next_rotation_user(self):
        """
        轮转游标推进：按配置顺序取下一个 user，跳过切换失败的。
        返回 None 表示全部失败。
        """
        users = self._rotation_users
        for _ in range(len(users)):
            self._rotation_index += 1
            if self._rotation_index >= len(users):
                self._rotation_index = 0
            cfg = users[self._rotation_index]
            if self._switch_app_for_user(cfg):
                return cfg
        return None

    def _switch_app_for_user(self, user_cfg):
        """
        把应用切到该 user：force-stop 当前 → am start --user。

        目标 user == DeviceManager 当前 user 时视为已在位（首次圈
        DeviceManager.user_id 即配置注入值，无需重启应用）。
        非 ADB 环境（无 adb shell 能力）跳过返回 True——桌面窗口
        场景无分身概念，用户自己保证前台是目标应用。
        """
        package = user_cfg.get("package")
        target_uid = user_cfg.get("user_id") or 0
        dm = getattr(self.executor, "device_manager", None)
        if dm is None or not hasattr(dm, "adb_start_package"):
            return True  # 非 ADB 环境：无从切换，按"已在位"处理
        current_uid = getattr(dm, "user_id", 0) or 0
        if target_uid == current_uid:
            self.log_info(f"[Rotation] user{target_uid} 即当前用户，无需切换")
            return True
        try:
            dm.adb_stop_package(package, user_id=current_uid)
        except Exception as e:
            self.log_info(f"[Rotation] 关闭 user{current_uid} 应用失败（继续）: {e}")
        try:
            ok = dm.adb_start_package(package, user_id=target_uid)
        except Exception as e:
            ok = False
            self.log_info(f"[Rotation] 启动 user{target_uid} 应用异常: {e}")
        if ok is False:
            self.log_info(
                f"[Rotation] user{target_uid} 应用启动失败，跳过该 user")
            return False
        dm.user_id = target_uid
        self.log_info(f"[Rotation] 应用已切到 user{target_uid} ({package})")
        return True

    def _rotation_yield_reason(self):
        """
        当前 user 是否无事可做（仅多 user 轮转模式判定；单链恒 None）。

        判定按序：
          1. 本圈已超预算（_cycle_deadline 已到）→ "budget"
          2. 队列里有 RUNNING/IN_PROGRESS → 在忙，不让位
          3. 最近的可唤醒任务（SCHEDULED.trigger_time /
             PENDING.next_retry_time）距今 > ROTATION_IDLE_GAP
             → "next_task_gap"
          4. 其余（有任务马上要跑）→ None 继续跑
        """
        if len(self._rotation_users) <= 1 or self.queue is None:
            return None
        now = time.time()
        deadline = self._rotation_budget_deadline()
        if deadline is not None and now >= deadline:
            return "budget"
        running = [
            t for t in self.queue.tasks
            if t.status in (TaskStatus.RUNNING, TaskStatus.IN_PROGRESS)
        ]
        if running:
            return None
        next_wake = min(
            self._next_wake_time(t) for t in self.queue.tasks
        ) if self.queue.tasks else None
        if next_wake is None:
            return "no_pending"
        if next_wake == float("inf"):
            # 只剩 DONE/FAILED：队列实际已无事可做
            return "no_pending"
        if next_wake - now > self.ROTATION_IDLE_GAP:
            return f"next_task_gap({(next_wake - now) / 60:.0f}min)"
        return None

    def _next_wake_time(self, task):
        """
        任务最早可能重新参与调度的时间戳。
        RUNNING/IN_PROGRESS 视为马上（0）；SCHEDULED 看 trigger_time；
        PENDING 的 next_retry_time 已过 = 马上可跑（0），未过 = 到点
        （one_shot 的远期自重排落在这里，绝不能当成"马上"）；
        DONE / FAILED 不再唤醒（inf）。
        """
        now = time.time()
        if task.status in (TaskStatus.RUNNING, TaskStatus.IN_PROGRESS):
            return 0.0
        if task.status == TaskStatus.SCHEDULED:
            return task.trigger_time
        if task.status == TaskStatus.PENDING:
            retry = task.next_retry_time or 0.0
            return retry if retry > now else 0.0
        return float("inf")

    def _rotation_budget_deadline(self):
        """
        本圈预算截止时间（圈起点 + 预估总耗时），无预算返回 None。
        预估 = Σ 启用任务的 est（定义段 estimated_seconds，缺 0 回退
        task_excute_time.json 实测均值）× max(1, count)；总和为 0
        （全部未知）时不设上限。
        """
        if getattr(self, "_rotation_deadline", None) is not None:
            return self._rotation_deadline
        budget = 0.0
        stats = None
        for entry in self._task_config["tasks"]:
            if not entry["enabled"]:
                continue
            est = float(entry.get("estimated_seconds") or 0)
            if est <= 0 and stats is None:
                stats = ExecutionTimeStats()
            if est <= 0:
                rec = stats.get(entry["id"])
                est = float(rec["avg"]) if rec else 0.0
            budget += est * max(1, int(entry.get("count") or 0))
        if budget <= 0:
            self._rotation_deadline = None
            self.log_info("[Rotation] 任务均未配置预估时间，本圈不限时")
        else:
            self._rotation_deadline = self._rotation_started_at + budget
            self.log_info(
                f"[Rotation] 本圈预算 {budget / 60:.0f} 分钟")
        return self._rotation_deadline

    def _mark_rotation_started(self):
        """圈起点打点 + 预算缓存清空（每圈 _run_queue_cycle 开头调用）。"""
        self._rotation_started_at = time.time()
        self._rotation_deadline = None

    # =========================================================================
    # 工厂构建：enabled 的实例条目 → TaskFactory
    # =========================================================================

    def _build_factories(self):
        """
        只把 JSON 里 enabled=True 的任务条目转成 TaskFactory。
        覆盖链已在加载器完成：entry 缺省字段回退 defaults / params.default。
        type_id（定义段 id）随工厂下发：TaskQueue 耗时统计按它聚合，
        一次性/定时任务的预估时间展示同样按它索引。
        链标记（machine/package/user_id）一并下发（5.5 轮转区分各 user）。
        """
        for entry in self._task_config["tasks"]:
            if not entry["enabled"]:
                continue

            cron = entry["cron"] or None
            self._factories.append(TaskFactory(
                task_class=entry["task_class"],
                count=int(entry["count"]),
                max_active=int(entry["max_active"]),
                requires_march_queue=bool(entry["requires_march_queue"]),
                next_trigger_delay=float(entry["next_trigger_delay"]),
                one_shot=bool(entry["one_shot"]),
                cron=cron,
                kwargs=dict(entry["params"]),
                name_prefix=entry["name_prefix"],
                type_id=entry["id"],
                estimated_seconds=float(entry["estimated_seconds"]),
                machine=self._task_config.get("machine"),
                package=self._task_config.get("package"),
                user_id=self._task_config.get("user_id", 0),
            ))

    # =========================================================================
    # 收尾：执行耗时样本落盘（5.4）
    # =========================================================================

    def _persist_execution_times(self):
        """
        把本次 run 聚合的单次交互耗时样本合并进
        configs/task_excute_time.json（滚动均值，按定义段 id 聚合）。
        统计失败只记日志，不影响调度收尾。
        """
        samples = getattr(self.queue, "execution_samples", None)
        if not samples:
            return
        try:
            stats = ExecutionTimeStats()
            merged = stats.merge(samples)
            if merged and stats.save():
                summary = " | ".join(
                    f"{tid}: {avg:.1f}s(n={n})" for tid, (avg, n) in merged.items()
                )
                self.log_info(f"[ExecTime] 已写入 task_excute_time.json: {summary}")
        except Exception as e:
            self.log_info(f"[ExecTime] 耗时统计写入失败（忽略）: {e}")


from src.scheduler.task_queue import TaskFactory  # noqa: E402  # TaskFactory 在父类模块定义；底部 import 避免循环引用
