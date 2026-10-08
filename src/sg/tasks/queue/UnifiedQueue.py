# -*- coding: utf-8 -*-
"""
UnifiedQueue —— 统一队列入口（JSON 配置驱动）。

配置来源：configs/task_config.json（task_types 定义段 + machines 实例段）。
  - webui 直接读写该 JSON 即可控制参数；
  - 多设备 / 多应用 / 多分身：加载器本期只消费第一条 enabled 的
    machine → app → user 链（schema 已预留，见 config_loader）。

老 GUI 配置（UnifiedQueue.json 的 "{key}: {Field}" 扁平键）已废弃；
TaskQueue / 队列调度逻辑不变，只是工厂参数的来源换成了 JSON。
"""
from pathlib import Path

from qfluentwidgets import FluentIcon

from src.sg.tasks.config_loader import TaskConfigError, load
from src.sg.tasks.queue.QueueTaskBase import QueueTaskBase
from src.sg.tasks.task_type_map import TASK_TYPE_MAP

CONFIG_PATH = Path("configs") / "task_config.json"


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

    # =========================================================================
    # type → 任务类解析
    # =========================================================================

    def _resolve_task_classes(self):
        """
        校验 task_config.json 的每个 type 都在 TASK_TYPE_MAP 里，
        并把解析出的类写进条目。未知 type 是配置错误：启动即抛。
        """
        for entry in self._task_config["tasks"]:
            ttype = entry["type"]
            task_class = TASK_TYPE_MAP.get(ttype)
            if task_class is None:
                raise TaskConfigError(
                    f"type {ttype!r} 不在 task_type_map.TASK_TYPE_MAP，"
                    f"请在代码侧映射中注册该任务类"
                )
            entry["task_class"] = task_class

    # =========================================================================
    # QueueTaskBase 钩子实现：全局参数来自 JSON
    # =========================================================================

    def _reload_config(self):
        """
        每次 run() 从磁盘重读 task_config.json。

        __init__ 里的 load 只保证"进程启动时配置可用"（启动即炸出结构错误）；
        真正生效的是这次重读——webui / 手改 JSON 后无需重启程序，
        重新启动任务即可拿到最新参数。整次运行使用同一次快照，
        运行中途改文件不影响进行中的调度。
        """
        self._task_config = load(CONFIG_PATH)
        self._resolve_task_classes()
        self.log_info(
            f"[TaskConfig] reloaded trail={self._task_config['trail']} "
            f"types={len(self._task_config['task_types'])} "
            f"tasks={len(self._task_config['tasks'])}"
        )

    def _queue_global_params(self):
        q = self._task_config["queue"]
        return {
            "tick_interval": float(q["tick_interval"]),
            "continue_after_failure": bool(q["continue_after_failure"]),
        }

    # =========================================================================
    # 工厂构建：enabled 的实例条目 → TaskFactory
    # =========================================================================

    def _build_factories(self):
        """
        只把 JSON 里 enabled=True 的任务条目转成 TaskFactory。
        覆盖链已在加载器完成：entry 缺省字段回退 defaults / params.default。
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
            ))


from src.scheduler.task_queue import TaskFactory  # noqa: E402  # TaskFactory 在父类模块定义；底部 import 避免循环引用
