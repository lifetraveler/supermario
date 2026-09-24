# =============================================================================
# queue/ —— 队列模式适配层
# =============================================================================
# 本包只包含「与业务无关」的队列调度基础设施。
#
# 术语约定：
#   - Queue  ：开发概念，指队列这种数据结构 / 调度模式。
#              只应出现在本包、scheduler 包、以及 SGBaseTask 内部实现里。
#   - Task   ：单个可执行单元，由 TaskFactory 按需创建。
#   - 业务名词：部队 / 日常 / 活动 / 采集…… 一律不得出现在本包。
#
# 依赖方向（单向）：
#   queue/  ──依赖──▶  sg.tasks.SGBaseTask
#                       sg.scheduler.task_queue
#   queue/  ──禁止──▶  任何 world / daily / activity 等业务包
# =============================================================================

from .QueueTaskBase import QueueTaskBase
from .GenericQueueTask import GenericQueueTask
# from .world import GatherTroop          # noqa: F401
# from .daily import DailyChest           # noqa: F401
# from .daily import ClaimReward          # noqa: F401
# from .activity import ActivityRunner    # noqa: F401

__all__ = ["QueueTaskBase", "GenericQueueTask"]