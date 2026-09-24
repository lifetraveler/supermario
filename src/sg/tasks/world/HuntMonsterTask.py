from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.tasklist.worldtask.troop.rally import Rally
from src.sg.element.world.resource.monster.monster_searcher import (
    MonsterSearcher,
)

from src.sg.scene.elements import (
    BUTTON_GOTO_WORLDMAP,
    TEAM_HUNTING,
)
from src.sg.scene.scene_type import SceneType


class HuntMonsterTask(SGBaseTask):
    """
    集结巨兽任务。

    =========================================================
    这个类现在负责什么？
    =========================================================
    只做业务编排：
      1. 组合领域对象（Popup / Recovery / Searcher / Rally）
      2. 按顺序调用它们的接口
      3. 步骤失败时的恢复重试

    不再负责：
      - 循环控制（由 GenericQueueTask / TaskQueue 负责）
      - 次数上限、体力下限（由队列配置驱动，Task 只读取）
      - 弹窗的具体处理逻辑（由 ActivityPopup 负责）
      - 体力的读取逻辑（由 Rally 负责）
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Hunt Monster"
        self.description = "Search and rally a monster."

        # ====================================================
        # 队列注入字段（由外部赋值）
        # ====================================================
        # 体力下限。<=0 表示不检查体力。
        # 由 GenericQueueTask / TaskQueue 通过 kwargs 注入。
        self.min_stamina = 0

        # 满员时是否召回。<=默认 False。
        self.auto_recall = False

        # 巨兽级别。None 表示不选择级别。
        self.monster_level = None

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2
        self.recover_interval = 3.0

        # ====================================================
        # 领域对象
        # ====================================================
        # 弹窗处理：负责关已知弹窗
        self.popup = ActivityPopup(self)

        # 恢复助手：复用 popup 的关弹窗能力
        self.recovery = RecoveryHelper(self, popup=self.popup)

        # 目标搜索：负责"搜索资源 → 选巨兽 → 选级别 → 确认位置"
        self.searcher = MonsterSearcher(self)

        # 集结：负责队列准备、集结执行、时间预估、体力检查
        self.rally = Rally(self)
        self.rally.config.team_element = TEAM_HUNTING
        # 队列空闲特征名由用户注入：
        # self.rally.queue.idle_queue_features = [
        #     "march_queue_idle_1", "march_queue_idle_2",
        # ]

    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试
    # ========================================================

    def _step(self, step_func, step_name) -> bool:
        """
        执行一个业务步骤，失败时尝试恢复并重试。
          - 恢复成功后从当前步骤重试，不回到任务开头
          - 最多重试 max_recover_attempts 次
          - 恢复动作返回 False 时不再重试
        """
        for attempt in range(self.max_recover_attempts + 1):
            if attempt > 0:
                self.log_info(
                    f"步骤 [{step_name}] 第 {attempt} 次重试前恢复"
                )
                if not self.recovery.full_recover():
                    self.log_info(
                        f"步骤 [{step_name}] 无法恢复，停止重试"
                    )
                    return False
                self._sleep(self.recovery.recover_wait)

            if step_func():
                return True

            self.log_info(f"步骤 [{step_name}] 失败")

        return False

    # ========================================================
    # 场景相关
    # ========================================================

    def enter_wilderness(self) -> bool:
        """
        确保进入荒野。
        已在荒野直接返回 True；
        在主城则点击"进入荒野"，等场景切换完成。
        """
        scene = self.scene_detector.detect()
        self.log_info(f"当前场景: {scene.type.value}")

        if scene.type == SceneType.WORLD_MAP:
            self.log_info("当前已经在荒野")
            return True

        if scene.type == SceneType.CITY:
            self.log_info("当前在主城，准备进入荒野")
            if not self._wait_and_click(
                BUTTON_GOTO_WORLDMAP, name="进入荒野", timeout=6.0
            ):
                return False
            return self._wait_scene(
                SceneType.WORLD_MAP, timeout=10.0, interval=0.5
            )

        self.log_error(f"无法进入荒野，未知场景: {scene.type.value}")
        return False

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。

        流程：
          1. 前置：确保前台、清弹窗
          2. 体力检查（不足则 FAILED）
          3. 进入荒野
          4. 队列准备（无空闲则 RETRY）
          5. 搜索目标
          6. 执行集结
          7. 返回估算的行军等待时间
        """
        self.log_info("========== 开始集结巨兽 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 体力检查 ----
        if not self.rally.has_enough_stamina(self.min_stamina):
            self.last_error = (
                f"体力低于下限 {self.min_stamina}"
            )
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入荒野 ----
        if not self._step(self.enter_wilderness, "进入荒野"):
            self.last_error = "进入荒野失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # todo ---- 队列准备 ----
        # ready, wait_seconds = self.rally.prepare()
        # if not ready:
        #     if wait_seconds > 0:
        #         self.log_info(f"队列繁忙，{wait_seconds}s 后重试")
        #         return (InteractionResult.RETRY, wait_seconds)
        #     self.last_error = "队列不可用"
        #     self.log_error(self.last_error)
        #     return (InteractionResult.FAILED, 0)

        # ---- 搜索目标 ----
        if not self._step(
            lambda: self.searcher.run(level=self.monster_level),
            "搜索目标",
        ):
            self.last_error = "搜索目标失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 执行集结 ----
        if not self._step(self.rally.execute, "执行集结"):
            self.last_error = "执行集结失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        wait_seconds = self.rally.estimate_wait()
        self.log_info(
            f"========== 巨兽集结完成，"
            f"预计 {wait_seconds:.1f}s 后完成 =========="
        )
        return (InteractionResult.SUCCESS, wait_seconds)

    # ========================================================
    # 兼容旧接口
    # ========================================================

    def _run_once(self):
        """
        兼容旧的 TaskQueue 调用方式。
        返回 (success: bool, wait_seconds: float)。
        """
        result, wait_seconds = self.run_interaction()
        success = (result == InteractionResult.SUCCESS)
        return (success, wait_seconds)

    # ========================================================
    # 独立运行入口
    # ========================================================

    def run(self):
        """
        独立运行：一次完整交互 + 原地等待行军时间。

        注意：
          循环次数、体力下限、满员召回等策略由外部（队列）
          通过 self.min_stamina / self.auto_recall 等字段注入。
          单独运行时这些字段保持默认（不检查）。

          如果通过 GenericQueueTask 队列运行，不会走这里。
        """
        self.log_info(
            "========== 集结巨兽（独立模式） =========="
        )

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(
                f"集结巨兽失败: {self.last_error}"
            )
            return False

        if result == InteractionResult.RETRY:
            self.log_info(
                f"暂不可执行，{wait_seconds:.1f}s 后结束"
            )
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        # SUCCESS
        if wait_seconds > 0:
            self.log_info(f"等待行军完成 {wait_seconds}s")
            self._sleep(wait_seconds)

        self.log_info("========== 集结巨兽完成 ==========")
        return True