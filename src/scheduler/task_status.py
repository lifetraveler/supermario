from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class InteractionResult(Enum):
    SUCCESS = "success"
    RETRY = "retry"
    FAILED = "failed"