"""
野兽挑战任务（搜索野兽图标并发起挑战）。

与 Hunt Monster 的差异：
  - 搜索面板里选择"野兽"图标（WORLD_ICON_RESOURCE_BEAST），非巨兽
  - 无级别选择
  - 确认位置后游戏把目标居中展示，并弹出处理弹窗；
    点击弹窗的挑战按钮（固定位置，与瞭望塔事件
    WORLD_EVENT_OBJECT_HANDLEAREA 一致），后续流程与
    WatchTowerEventTask 的 _handle_beast_event 一致
  - 不发起集结，不估算行军等待

流程：
  1. 确保前台，清理弹窗
  2. 体力检查（不足则 FAILED）
  3. 进入荒野（主城 → 跳转世界地图）
  4. 搜索目标（搜索资源 → 选野兽图标 → 确认位置，
     游戏自动居中目标并弹出处理弹窗）
  5. 挑战：点弹窗挑战按钮 → 击杀
"""

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.tasklist.worldtask.troop.rally import Rally
from src.sg.element.overall.tasklist.worldtask.troop.troop import Troop
from src.sg.element.world.resource.beast.beast_searcher import (
    BeastSearcher,
)
from src.sg.element.world.resource.beast.beast import (
    Beast,
)

from src.sg.scene.elements import (
    BUTTON_GOTO_WORLDMAP,
)
from src.sg.scene.scene_type import SceneType


class HuntBeastTask(SGBaseTask):
    """
    挑战野兽任务。

    =========================================================
    职责边界：
      - 只做业务编排：进荒野 → 搜索野兽 → 挑战
      - 体力下限由队列注入
      - 弹窗处理交给 ActivityPopup
      - 搜索与滑动查找交给 BeastSearcher
      - 挑战弹窗与击杀流程交给 Beast
      - 体力读取交给 Rally（挑战消耗体力，不发起集结）
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Hunt Beast"
        self.description = "Search the beast icon and challenge it."

        # ====================================================
        # 队列注入字段（由外部赋值）
        # ====================================================
        # 体力下限。<=0 表示不检查体力。
        self.min_stamina = 0

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2
        # self.recover_interval = 3.0

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

        # 目标搜索：负责"搜索资源 → 选野兽图标 → 确认位置"
        self.searcher = BeastSearcher(self)

        # 挑战：负责"点弹窗挑战按钮 → 击杀"
        self.beast = Beast(self)

        # 体力读取（挑战消耗体力；本任务不发起集结）
        self.rally = Rally(self)

        # 军队队列：OCR 读取 有效/最大 数量
        self.troop = Troop(self)


    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试
    # ========================================================

    def _step(self, step_func, step_name) -> bool:
        for attempt in range(self.max_recover_attempts + 1):
            if attempt > 0:
                self.log_info(f"步骤 [{step_name}] 第 {attempt} 次重试前恢复")
                if not self.recovery.full_recover():
                    self.log_info(f"步骤 [{step_name}] 无法恢复，停止重试")
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
          4. 搜索目标（确认位置后游戏居中目标并弹出处理弹窗）
          5. 挑战：读单程行军时间 → 点弹窗挑战按钮 → 击杀
          6. 成功返回来回行军耗时，作为下一个任务的预计触发时间
        """
        self.log_info("========== 开始挑战野兽 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 体力检查 ----
        if not self.rally.has_enough_stamina(self.min_stamina):
            self.last_error = f"体力低于下限 {self.min_stamina}"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入荒野 ----
        if not self._step(self.enter_wilderness, "进入荒野"):
            self.last_error = "进入荒野失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)
        
        # ---- 军队队列检查（无空闲队列则等待后重试） ----
        if not self.troop.has_free_slot():
            self.last_error = "无空闲军队队列"
            self.log_info(self.last_error)
            return (InteractionResult.RETRY, 30.0)

        # ---- 搜索目标（游戏自动居中并弹出处理弹窗） ----
        if not self._step(self.searcher.run, "搜索目标"):
            self.last_error = "搜索目标失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 挑战（弹窗挑战按钮 → 击杀） ----
        if not self._step(self.beast.challenge, "执行挑战"):
            self.last_error = "执行挑战失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        wait_seconds = self.beast.estimate_wait()
        self.log_info(
            f"========== 野兽挑战完成，"
            f"预计 {wait_seconds:.1f}s 后可执行下一个任务 =========="
        )
        return (InteractionResult.SUCCESS, wait_seconds)

    # ========================================================
    # 兼容旧接口
    # ========================================================

    def _run_once(self):
        result, wait_seconds = self.run_interaction()
        success = (result == InteractionResult.SUCCESS)
        return (success, wait_seconds)

    # ========================================================
    # 独立运行入口
    # ========================================================

    def run(self):
        self.log_info("========== 挑战野兽（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"挑战野兽失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 挑战野兽完成 ==========")
        return True
