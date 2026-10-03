"""
叛军精锐集结任务（Elite Rebels，使用叛军精锐道具召唤）。

与 HuntScareWolfTask（恐狼）同构：
  - 不搜索巨兽；用背包里的叛军精锐道具触发刷新
  - 找到叛军精锐之后（点击资源）→ 执行集结 → 返回行军等待时间

流程：
  1. 确保前台，清理弹窗
  2. 体力检查（不足则 FAILED）
  3. 若不在主界面（城市/世界地图）→ 按 ESC 回到主界面
  4. 打开背包 → 切到"其他"标签 → 滑动查找并点击叛军精锐道具
     ITEM_ELITE_REBELS_ICON
  5. 点击"使用"按钮 → 游戏自动跳转到世界资源界面
  6. 点击叛军精锐资源（WORLD_RESOURCE_ELITE_REBELS）
  7. 执行集结，返回估算的行军等待时间
"""

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.tasklist.worldtask.troop.rally import Rally

from src.sg.scene.elements import (
    GLOBAL_TAG_BAG,                    # 打开背包
    GLOBAL_TAG_BAG_OTHER,              # 背包"其他"标签
    GLOBAL_TAG_BAG_OTHER_1,
    ITEM_ELITE_REBELS_ICON,            # 叛军精锐道具图标
    ITEM_SCARE_WOLF_CLAW_BUTTON_USE,   # 道具"使用"按钮（通用）
    WORLD_RESOURCE_ELITE_REBELS,       # 世界资源：叛军精锐
)
from src.sg.scene.elements import TEAM_HUNTING
from src.sg.scene.scene_type import SceneType


class HuntEliteRebelsTask(SGBaseTask):
    """
    使用叛军精锐道具召唤叛军精锐并集结。
    =========================================================
    职责边界（与 HuntScareWolfTask 一致）：
      - 只做业务编排：回主界面 → 用道具 → 点击资源 → 集结
      - 循环控制、体力下限、召回策略由队列注入
      - 弹窗处理交给 ActivityPopup
      - 集结、体力读取交给 Rally
    =========================================================
    """

    # 背包向上滑动查找次数上限（每次 1/4 屏）
    bag_scroll_max = 8

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Hunt Elite Rebels"
        self.description = (
            "Use elite-rebels item and rally the elite rebels."
        )

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


    # ========================================================
    # 背包滑动查找
    # ========================================================

    def _scroll_find_item(self, element, item_name: str):
        """
        全屏匹配道具，找不到则向上滑 1/4 屏继续找。

        背包"其他"标签页物品多于一屏时，道具可能不在初始视图内；
        与任务列表一致，滚轮在此界面不生效，必须用滑动。

        返回找到的 Box，全部滑动后仍找不到返回 None。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)
        for attempt in range(self.bag_scroll_max + 1):
            box = self._find(element, threshold=0.8, box=full_screen)
            if box is not None:
                if attempt > 0:
                    self.log_info(f"滑动 {attempt} 次后找到{item_name}")
                return box
            self.log_info(f"未找到{item_name}，向上滑动 1/4 屏 ({attempt + 1})")
            self.swipe_relative(0.3, 0.6, 0.3, 0.3, duration=0.3,
                                settle_time=1.0)
            self._sleep(1.0)
        return None

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
            self.recovery._try_press_esc()
            self._sleep(1.0)

        self.log_error("无法返回主界面")
        return False

    # ========================================================
    # 步骤 2：打开背包 → 其他标签 → 点击叛军精锐道具 → 使用
    # ========================================================

    def _use_elite_rebels_item(self) -> bool:
        """
        打开背包 → 切到"其他"标签 → 滑动查找并点击叛军精锐道具
        → 点击"使用"，并等待游戏跳转到世界资源界面。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        # 打开背包
        if not self._wait_and_click(
            GLOBAL_TAG_BAG, name="背包按钮", timeout=6.0
        ):
            return False
        self._sleep(0.5)

        # 切换到"其他"标签
        if not self._wait_and_click(
            [GLOBAL_TAG_BAG_OTHER, GLOBAL_TAG_BAG_OTHER_1],
            name="背包-其他标签", timeout=6.0,
        ):
            return False
        self._sleep(0.5)

        # 滑动查找并点击叛军精锐道具
        item_box = self._scroll_find_item(
            ITEM_ELITE_REBELS_ICON, "叛军精锐道具"
        )
        if item_box is None:
            self.log_error("滑动查找后仍未找到叛军精锐道具")
            return False
        self.log_info("点击叛军精锐道具")
        if not self.click(item_box):
            return False
        self._sleep(0.5)

        # 点击"使用"
        if not self._wait_and_click(
            ITEM_SCARE_WOLF_CLAW_BUTTON_USE, name="道具-使用按钮",
            timeout=6.0,
        ):
            return False
        self._sleep(1.0)

        # 使用后游戏会跳转到世界资源界面
        if not self._wait_scene(
            SceneType.WORLD_MAP, timeout=10.0, interval=0.5
        ):
            self.log_error("使用道具后未跳转到世界资源界面")
            return False
        return True

    # ========================================================
    # 步骤 3：点击叛军精锐资源
    # ========================================================

    def _click_elite_rebels(self) -> bool:
        """
        点击叛军精锐资源。
        优先特征匹配；匹配不到时，因为上一步（使用道具）会把当前
        资源居中显示，直接按 coco 记录的 bbox 坐标点击。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)
        box = self._find(
            WORLD_RESOURCE_ELITE_REBELS, threshold=0.8, box=full_screen
        )
        if box is not None:
            self.log_info("已点击叛军精锐资源（特征匹配）")
            self.click(box)
            self._sleep(0.8)
            return True

        # 特征匹配失败：按 bbox 坐标直接点击
        # 使用道具后当前资源已被游戏自动居中，
        # coco 文件里记录的 bbox 就是屏幕上的实际位置。
        self.log_info("未匹配到叛军精锐特征，改用 bbox 坐标点击")
        bbox = self.get_box_by_name(WORLD_RESOURCE_ELITE_REBELS.resource_id)
        if not bbox:
            self.log_error("未找到叛军精锐 bbox 坐标")
            return False

        self.click_box(bbox)
        self.log_info("已按 bbox 坐标点击叛军精锐资源")
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
        self.log_info("========== 开始集结叛军精锐 ==========")

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

        # ---- 使用道具（含跳转到世界资源界面）----
        if not self._step(self._use_elite_rebels_item, "使用叛军精锐道具"):
            self.last_error = "使用叛军精锐道具失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 点击叛军精锐 ----
        if not self._step(self._click_elite_rebels, "点击叛军精锐"):
            self.last_error = "点击叛军精锐失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 执行集结（复用 Rally，与恐狼一致）----
        if not self._step(self.rally.execute, "执行集结"):
            self.last_error = "执行集结失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        wait_seconds = self.rally.estimate_wait(self.extra_config)
        self.log_info(
            f"========== 叛军精锐集结完成，"
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
        self.log_info("========== 集结叛军精锐（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"集结叛军精锐失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        if wait_seconds > 0:
            self.log_info(f"等待行军完成 {wait_seconds}s")
            self._sleep(wait_seconds)

        self.log_info("========== 集结叛军精锐完成 ==========")
        return True
