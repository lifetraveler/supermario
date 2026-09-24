from enum import Enum


class TaskStatus(Enum):
    # 已进队列，等 trigger_time 到才激活
    SCHEDULED = "scheduled"

    # trigger_time 已到，等待被 pick
    PENDING = "pending"

    # 正在交互（同一时刻只会有一个）
    RUNNING = "running"

    # 交互完成，等游戏内完成
    IN_PROGRESS = "in_progress"

    DONE = "done"
    FAILED = "failed"

    @property
    def is_active(self) -> bool:
        """是否算"活跃"（占用并行名额）。SCHEDULED 不算活跃。"""
        return self in (
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.IN_PROGRESS,
        )


class InteractionResult(Enum):
    SUCCESS = "success"
    RETRY = "retry"
    FAILED = "failed"