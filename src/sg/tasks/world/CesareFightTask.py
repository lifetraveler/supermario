"""
切萨雷征讨任务（两周一次的常规活动）。

流程：
  1. 确保前台，清理弹窗
  2. 回到主界面（城市 / 世界地图）
  3. 进入切萨雷界面（便捷入口 / 常规活动滑动查找）
  4. OCR 读取目标实力（失败不阻塞）
  5. 点击侦察按钮（体力不足 → 本次结束，按配置等待）
  6. 点击资源区域，弹出处理窗口
  7. 分支：
     - 挑战：走普通野兽式击杀确认（world_event_beast_kill）
     - 集结：点集结按钮 → Rally 复用（行军队列）

领域对象：Cesare（导航/实力/侦察/分支）、Rally（集结复用）。
"""

import types

from src.sg.element.overall.player import Player
from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.activity.cesare import Cesare
from src.sg.element.overall.tasklist.worldtask.troop.rally import Rally

from src.sg.element.world.resource.beast.beast import Beast


from src.sg.scene.elements import (TEAM_HUNTING,GLOBAL_ACTIVITY_FIGHT_QIESALEI_BUTTON_CHALLENGE)
from src.sg.scene.scene_type import SceneType


class CesareFightTask(SGBaseTask):
    """
    切萨雷征讨。

    =========================================================
    职责边界：
      - 只做业务编排：进界面 → 侦察 → 分支（挑战/集结）
      - 循环控制、体力下限、等待策略由队列注入
      - 弹窗处理交给 ActivityPopup
      - 导航与分支交给 Cesare
      - 集结流程复用 Rally
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Cesare Fight"
        self.description = "Fight the Cesare event (challenge or rally)."

        # ====================================================
        # 队列注入字段（由外部赋值）
        # ====================================================
        # 满员时是否召回。
        self.auto_recall = False

        # 体力不足（侦察弹出补充界面）时本次任务结束的等待秒数。
        self.stamina_wait_seconds = 3600

        # ====================================================
        # 个性化参数容器
        # ====================================================
        self.extra_config = types.SimpleNamespace()

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)
        self.cesare = Cesare(self)

        # 集结复用（切萨雷弹窗的集结按钮由 Cesare 点击后，
        # execute 不再需要弹窗入口元素）
        self.rally = Rally(self)
        self.rally.config.team_element = TEAM_HUNTING

        # 挑战分支复用野兽击杀确认
        self.beast = Beast(self)
        self.beast.team_element = TEAM_HUNTING
        
        self.player = Player(self)

        # 自身实力（todo：待实力读取实现后注入）
        self.my_power = None

    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试
    # ========================================================


    # ========================================================
    # 场景相关
    # ========================================================

    def _ensure_main_scene(self) -> bool:
        """
        确保处于主界面。主界面定义为 CITY 或 WORLD_MAP。
        否则连按 ESC 直到回到主界面。
        """
        for _ in range(5):
            scene = self.scene_detector.detect()
            self.log_info(f"当前场景: {scene.type.value}")

            if scene.type in (SceneType.CITY, SceneType.WORLD_MAP):
                self.log_info("已在主界面")
                return True

            self.log_info("不在主界面，按 ESC 返回")
            self.recovery._try_press_esc()
            self._sleep(1.0)

        self.log_error("无法返回主界面")
        return False

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始切萨雷征讨 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 读取目标实力（失败不阻塞） ----
        self.cesare.read_power()

        # ---- 进入切萨雷界面 ----
        if not self._step(self.cesare.open_qiesalei, "进入切萨雷界面"):
            self.last_error = "进入切萨雷界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # 如果直接进入了资源界面的话，需要跳过一些步骤来处理
        if not self._wait_element(GLOBAL_ACTIVITY_FIGHT_QIESALEI_BUTTON_CHALLENGE,timeout=1,with_recovery=False):
            # ---- 侦察（False = 体力不足弹出补充界面，本次结束） ----
            if not self._step(self.cesare.reconnoitre, "侦察"):
                self.last_error = "侦察失败（可能体力不足）"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, self.stamina_wait_seconds)

            # ---- 点击资源弹出处理窗口 ----
            if not self._step(self.cesare.open_resource, "打开处理窗口"):
                self.last_error = "打开处理窗口失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)

        # ---- 分支：挑战 / 集结 ----
        mode = self.cesare.challenge_or_rally()

        if mode == "challenge":
            if not self._step(self.beast.challenge, "挑战",smalltili=True):
                self.last_error = "挑战失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)
            wait_seconds = self.beast.estimate_wait()
        elif mode == "rally":
            if not self._step(self._do_rally, "集结"):
                self.last_error = "集结失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)
            wait_seconds = self.rally.estimate_wait(self.extra_config)
        else:
            self.last_error = "处理窗口未匹配到挑战/集结按钮"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        self.log_info(
            f"========== 切萨雷征讨完成（{mode}），"
            f"预计 {wait_seconds:.1f}s 后完成 =========="
        )
        return (InteractionResult.SUCCESS, wait_seconds)

    # ========================================================
    # 分支实现
    # ========================================================

    def _do_challenge(self) -> bool:
        """
        挑战分支：点处理窗口挑战按钮 → 野兽式击杀确认。
        """
        if not self.cesare.pick_challenge():
            self.log_error("未找到挑战按钮")
            return False
        self._sleep(1.0)
        return self.beast.kill()

    def _do_rally(self) -> bool:
        """
        集结分支：点处理窗口集结按钮进入集结配置页 → Rally 流程。
        Rally.execute() 传空入口元素（弹窗入口已由 Cesare 处理）。
        """
        if not self.cesare.pick_rally_entry():
            self.log_error("未找到集结按钮")
            return False
        self._sleep(1.0)
        # 切萨雷弹窗的集结入口已点过，直接走确认集结 → 配置 → 出征
        return self.rally.execute(entry_elements=())

    # ========================================================
    # 兼容旧接口
    # ========================================================

    def check_completed(self) -> bool:
        return True

    def _run_once(self):
        result, wait_seconds = self.run_interaction()
        success = (result == InteractionResult.SUCCESS)
        return (success, wait_seconds)

    # ========================================================
    # 独立运行入口
    # ========================================================

    def run(self):
        self.log_info("========== 切萨雷征讨（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"切萨雷征讨失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        if wait_seconds > 0:
            self.log_info(f"等待完成 {wait_seconds:.1f}s")
            self._sleep(wait_seconds)

        self.log_info("========== 切萨雷征讨完成 ==========")
        return True
