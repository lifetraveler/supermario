from src.sg.tasks.queue.QueueTaskBase import QueueTaskBase
from src.scheduler.task_queue import TaskFactory


# =============================================================================
# GenericQueueTask —— 通用队列调度器
# =============================================================================
# 设计目标：
#   提供一个"与业务完全无关"的容器——任何游戏任务都能注册进来，
#   GUI 会自动为每个注册项展开一组配置（启用 / 次数 / 并发 / 额外参数）。
#
# 【与业务无关】
#   本类不知道"部队""宝箱""活动"的存在，它只认识 TASK_REGISTRY。
#   业务术语只在各自的注册文件里出现，本类里一个业务词都不该有。
#
# 使用方式：
#   1. 业务方调用 GenericQueueTask.register_task_type(...) 注册
#   2. 用户勾选想要的类型
#   3. _build_factories() 只把"启用"的类型转成 TaskFactory 交给队列
#
# 注册项结构（TASK_REGISTRY[key]）：
#   {
#       "task_class"        : 具体任务类
#       "name_prefix"       : 任务名前缀（用于日志 / 快照区分）
#       "default_count"     : 默认提交次数，0 = 无限
#       "default_max_active": 默认并发上限
#       "default_kwargs"    : 传给任务构造的固定参数
#       "extra_config"      : 额外配置项（自动展开到 GUI）
#       "description"       : GUI 上显示的中文说明
#   }
# =============================================================================
class GenericQueueTask(QueueTaskBase):

    # 全局注册表，类级别共享。
    # 任何模块只要 import 时调用一次 register_task_type 就会生效。
    TASK_REGISTRY: dict = {}

    # =========================================================================
    # 注册 API
    # =========================================================================

    @classmethod
    def register_task_type(
        cls,
        key: str,
        task_class,
        name_prefix: str = None,
        default_count: int = 0,
        default_max_active: int = 1,
        default_requires_march_queue: bool = False,
        default_next_trigger_delay: float = 0.0,
        default_one_shot: bool = False,               # ← 一次性任务
        default_cron: str = None,                     # ← 定时任务 cron 表达式
        default_kwargs: dict = None,
        extra_config: dict = None,
        description: str = "",
    ):
        """
        注册一个业务任务类型。

        参数：
            key                : 唯一标识，同时作为 GUI 配置前缀。
                                 可以是任何业务名，如 "Hunt Monster Troop"、"Daily Chest"。
            task_class         : 实际执行的任务类（继承自 SGBaseTask）。
            name_prefix        : 任务名前缀，用于日志与快照区分不同工厂。
            default_count      : 默认提交次数，0 = 无限。
            default_max_active : 默认并发上限。
            default_one_shot   : 一次性任务。True 时整个类型只建 1 个
                                 任务实例，实例循环执行 Count 次，
                                 不走 follower 链。
            default_cron       : 定时任务 cron 表达式（croniter 语法）。
                                 非空时 trigger_time 由 cron 计算，
                                 每次到点执行一次，Count 次后停止；
                                 与 next_trigger_delay / one_shot 互斥。
            default_kwargs     : 传给任务的固定参数（不随用户配置改变）。
            extra_config       : 该类型独有的额外配置项，形如：
                {
                    "Monster Level": {
                        "default": 1,
                        "desc"   : "巨兽级别",
                        "type"   : {"type": "drop_down",
                                    "options": ["1","2","3","4","5"]},
                    },
                }
                GUI 会自动把每一项展开成 "{key}: {extra_key}" 的配置。
            description        : GUI 上显示的中文说明。

        为什么要用注册而不是继承？
            因为一个队列要同时容纳多种业务，
            如果每加一种就要新建一个类，会让代码线性膨胀。
            注册表把"类型扩展"从"类扩展"变成了"数据扩展"。
        """
        cls.TASK_REGISTRY[key] = {
            "task_class": task_class,
            "name_prefix": name_prefix or key,
            "default_count": default_count,
            "default_max_active": default_max_active,
            "default_requires_march_queue": default_requires_march_queue,
            "default_next_trigger_delay": default_next_trigger_delay,
            "default_one_shot": default_one_shot,
            "default_cron": default_cron,
            "default_kwargs": dict(default_kwargs or {}),
            "extra_config": extra_config or {},
            "description": description or key,
        }

    # =========================================================================
    # 初始化
    # =========================================================================

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 中性名称：业务子类通常会覆盖它（例如 HuntMonsterTroop）。
        self.name = "Generic Queue"
        self.description = "通用队列调度器，可调度任意游戏任务。"

        # 按注册表把 GUI 配置铺开。
        self._build_config()

    # =========================================================================
    # GUI 配置自动展开
    # =========================================================================

    def _build_config(self):
        """
        遍历注册表，为每个注册项在 GUI 上生成配置项。

        每个注册项固定生成四项：
            "{key}: Enabled"            —— 是否启用
            "{key}: Count"              —— 提交次数
            "{key}: Max Active"         —— 并发上限
            "{key}: Next Trigger Delay" —— 任务间隔时间

        然后遍历 extra_config，为业务特有参数再生成若干项。
        这样业务方只需在注册时声明"我有哪些参数"，
        不用手写 default_config / config_description / config_type。

        收起/展开：
            通过 ok-script 的 sub_configs 机制挂在 Enabled 开关上：
            未启用时 Count / Max Active / Next Trigger Delay 与
            extra_config 全部收起，勾选 Enabled 后才展开。
            配置值始终存在，收起只是展示层行为，不影响 _build_factories。
        """
        for key, meta in self.TASK_REGISTRY.items():
            prefix = key
            # ---- 基础配置 ----
            self.default_config[f"{prefix}: Enabled"] = False
            self.default_config[f"{prefix}: Count"] = meta["default_count"]
            self.default_config[f"{prefix}: Max Active"] = meta["default_max_active"]
            self.default_config[f"{prefix}: Next Trigger Delay"] = meta["default_next_trigger_delay"]
            self.default_config[f"{prefix}: One Shot"] = meta["default_one_shot"]
            self.default_config[f"{prefix}: Cron"] = meta["default_cron"] or ""

            self.config_description[f"{prefix}: Enabled"] = (
                f"启用「{meta['description']}」"
            )
            self.config_description[f"{prefix}: Count"] = "执行次数，0 = 无限"
            self.config_description[f"{prefix}: Max Active"] = "并发上限"
            self.config_description[f"{prefix}: Next Trigger Delay"] = "任务间隔时间"
            self.config_description[f"{prefix}: One Shot"] = (
                "一次性任务：只建一个实例循环执行 Count 次，"
                "不再逐次派生。One shot: run one instance Count times."
            )
            self.config_description[f"{prefix}: Cron"] = (
                "定时任务 cron 表达式（如 0 8 * * *），留空关闭；"
                "启用后按时间触发，Count 次后停止。"
                "Cron schedule (croniter), empty = off."
            )
            # ---- 收起/展开：未启用时把其余配置全部挂在 Enabled 下 ----
            # ok-script 约定：config_type[key]["sub_configs"] 声明
            # "父配置取值 → 显示哪些子配置"。Enabled 是 bool 开关，
            # False → 不显示任何子配置，True → 全部展开。
            # 仅展示层联动，配置值始终持久化，不影响 _build_factories。
            collapsed = [
                f"{prefix}: Count",
                f"{prefix}: Max Active",
                f"{prefix}: Next Trigger Delay",
                f"{prefix}: One Shot",
                f"{prefix}: Cron",
            ]

            # ---- 业务特有配置 ----
            for extra_key, extra in meta["extra_config"].items():
                full = f"{prefix}: {extra_key}"
                self.default_config[full] = extra["default"]
                self.config_description[full] = extra.get("desc", "")
                if "type" in extra:
                    # 例如下拉框、多选、文本框……
                    self.config_type[full] = extra["type"]
                collapsed.append(full)

            self.config_type[f"{prefix}: Enabled"] = {
                "sub_configs": {True: collapsed},
            }

    # =========================================================================
    # 把"启用的类型"转成 TaskFactory
    # =========================================================================

    def _build_factories(self):
        """
        只把用户在 GUI 上勾选（Enabled=True）的类型转成 TaskFactory。
        未启用的类型直接跳过——这样用户可以在一个队列里
        自由组合今天想跑的任务，而不需要写多份配置。
        """
        for key, meta in self.TASK_REGISTRY.items():
            if not self.config.get(f"{key}: Enabled", False):
                continue

            # 用户配置优先，缺省时回退到注册时的默认值。
            count = int(self.config.get(f"{key}: Count", meta["default_count"]))
            max_active = int(self.config.get(
                f"{key}: Max Active", meta["default_max_active"]
            ))
            one_shot = bool(self.config.get(
                f"{key}: One Shot", meta["default_one_shot"]
            ))
            cron = str(self.config.get(f"{key}: Cron", "")).strip() or None

            # 先把固定参数拷一份，再把用户的额外配置合并进去。
            kwargs = dict(meta["default_kwargs"])
            for extra_key, extra in meta["extra_config"].items():
                # extra 里声明了 attr 就用 attr，没声明就退回原 key（保持兼容）
                attr = extra.get("attr", extra_key)
                kwargs[attr] = self.config.get(f"{key}: {extra_key}")

            self.log_info(
                f"[Factory] key={key}, count={count}, "
                f"max_active={max_active}, one_shot={one_shot}, cron={cron}"
            )

            self._factories.append(TaskFactory(
                task_class=meta["task_class"],
                count=count,
                max_active=max_active,
                requires_march_queue=meta.get(
                    "default_requires_march_queue", False
                ),
                next_trigger_delay=meta.get(
                    "default_next_trigger_delay", 0.0
                ),
                one_shot=one_shot,
                cron=cron,
                kwargs=kwargs,
                name_prefix=meta["name_prefix"],
            ))