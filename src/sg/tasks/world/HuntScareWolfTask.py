"""
恐狼集结任务（使用"狼爪"道具召唤恐狼）。

与 Hunt Monster 的差异：
  - 不搜索巨兽；用背包里的狼爪道具触发恐狼刷新
  - 找到恐狼之后（点击资源）→ 执行集结 → 返回行军等待时间，
    与"找到巨兽以后"的流程一致

流程：
  1. 确保前台，清理弹窗
  2. 体力检查（不足则 FAILED）
  3. 若不在主界面（城市/世界地图）→ 按 ESC 回到主界面
  4. 打开背包 → 切到"其他"标签 → 点击狼爪 ITEM_SCARE_WOLF_CLAW
  5. 点击"使用"按钮 item_scare_wolf_claw_button_use
     → 游戏自动跳转到世界资源界面
  6. 点击恐狼资源（world_resource_scare_wolf / world_resource_scare_wolf_1）
  7. 执行集结，返回估算的行军等待时间
"""

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.tasklist.worldtask.troop.rally import Rally

# ---- 背包相关元素（路径按项目实际结构调整） ----
from src.sg.scene.elements import (
    GOBAL_TAG_BAG,                    # 打开背包
    GOBAL_TAG_BAG_OTHER,          # 背包"其他"标签
    GOBAL_TAG_BAG_OTHER_1,
    ITEM_SCARE_WOLF_CLAW,               # 狼爪道具图标
    ITEM_SCARE_WOLF_CLAW_BUTTON_USE,    # 狼爪"使用"按钮
    WORLD_RESOURCE_SCARE_WOLF,
    WORLD_RESOURCE_SCARE_WOLF_1,
    WORLD_RESOURCE_SCARE_WOLF_0,
)


from src.sg.scene.elements import TEAM_HUNTING
from src.sg.scene.scene_type import SceneType


class HuntScareWolfTask(SGBaseTask):
    """
    使用狼爪召唤恐狼并集结。

    =========================================================
    职责边界（与 Hunt Monster 一致）：
      - 只做业务编排：回主界面 → 用道具 → 点击恐狼 → 集结
      - 循环控制、体力下限、召回策略由队列注入
      - 弹窗处理交给 ActivityPopup
      - 集结、体力读取交给 Rally
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Hunt Scare Wolf"
        self.description = "Use scare-wolf claw and rally the scare wolf."

        # ====================================================
        # 队列注入字段（由外部赋值）
        # ====================================================
        # 体力下限。<=0 表示不检查体力。
        self.min_stamina = 0

        # 满员时是否召回。
        self.auto_recall = False

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

        # 集结复用
        self.rally = Rally(self)
        self.rally.config.team_element = TEAM_HUNTING
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
    # 步骤 1：回到主界面（城市 / 世界地图）
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
            # 说明：具体按键 API 以基类为准，例如 self._press_key(KeyCode.ESC)
            self.recovery._try_press_esc()
            self._sleep(1.0)

        self.log_error("无法返回主界面")
        return False

    # ========================================================
    # 步骤 2：打开背包 → 其他标签 → 点击狼爪 → 使用
    # ========================================================

    def _use_scare_wolf_claw(self) -> bool:
        """
        打开背包 → 切到"其他"标签 → 点击狼爪 → 点击"使用"，
        并等待游戏跳转到世界资源界面。
        """
        # 打开背包
        if not self._wait_and_click(
            GOBAL_TAG_BAG, name="背包按钮", timeout=6.0
        ):
            return False
        self._sleep(0.5)

        # 切换到"其他"标签
        if not self._wait_and_click(
            [GOBAL_TAG_BAG_OTHER,GOBAL_TAG_BAG_OTHER_1] ,name="背包-其他标签", timeout=6.0
        ):
            return False
        self._sleep(0.5)

        # 找到并点击狼爪
        if not self._wait_and_click(
            ITEM_SCARE_WOLF_CLAW, name="狼爪道具", timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1)
        ):
            return False
        self._sleep(0.5)

        # 点击"使用"
        if not self._wait_and_click(
            ITEM_SCARE_WOLF_CLAW_BUTTON_USE, name="狼爪-使用按钮", timeout=6.0
        ):
            return False
        self._sleep(1.0)

        # 使用后游戏会跳转到世界资源界面
        if not self._wait_scene(
            SceneType.WORLD_MAP, timeout=10.0, interval=0.5
        ):
            self.log_error("使用狼爪后未跳转到世界资源界面")
            return False
        return True

    # ========================================================
    # 步骤 3：点击恐狼
    # ========================================================

    def _click_scare_wolf(self) -> bool:
        """
        点击恐狼资源。
        优先按特征值匹配；匹配不到时，因为上一步（使用狼爪）会把当前资源
        居中显示，所以直接按 coco 文件中记录的 bbox 坐标点击。
        """
        # for element in (
        #     WORLD_RESOURCE_SCARE_WOLF_0,
        #     WORLD_RESOURCE_SCARE_WOLF,
        #     WORLD_RESOURCE_SCARE_WOLF_1,
        # ):
        #     if self._wait_and_click(element, name="恐狼", timeout=3.0):
        #         self.log_info("已点击恐狼资源（特征匹配）")
        #         self._sleep(0.8)
        #         return True

        # # 特征匹配失败：按 bbox 坐标直接点击
        # # 说明：使用狼爪后当前资源已被游戏自动居中，
        # # 因此 coco 文件里记录的 bbox 就是屏幕上的实际位置。
        # self.log_info("未匹配到恐狼特征，改用 bbox 坐标点击")
        box = self.get_box_by_name(WORLD_RESOURCE_SCARE_WOLF.resource_id)
        if not box:
            self.log_error("未找到恐狼 bbox 坐标")
            return False

        self.click_box(box)
        self.log_info("已按 bbox 坐标点击恐狼资源")
        self._sleep(0.8)
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始集结恐狼 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 体力检查 ----
        if not self.rally.has_enough_stamina(self.min_stamina):
            self.last_error = f"体力低于下限 {self.min_stamina}"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 使用狼爪（含跳转到世界资源界面）----
        if not self._step(self._use_scare_wolf_claw, "使用狼爪"):
            self.last_error = "使用狼爪失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 点击恐狼 ----
        if not self._step(self._click_scare_wolf, "点击恐狼"):
            self.last_error = "点击恐狼失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 执行集结（复用 Rally，与"找到巨兽"之后一致）----
        if not self._step(self.rally.execute, "执行集结"):
            self.last_error = "执行集结失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        wait_seconds = self.rally.estimate_wait(self.extra_config)
        self.log_info(
            f"========== 恐狼集结完成，"
            f"预计 {wait_seconds:.1f}s 后完成 =========="
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
        self.log_info("========== 集结恐狼（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"集结恐狼失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        if wait_seconds > 0:
            self.log_info(f"等待行军完成 {wait_seconds}s")
            self._sleep(wait_seconds)

        self.log_info("========== 集结恐狼完成 ==========")
        return True