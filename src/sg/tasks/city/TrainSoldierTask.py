"""
士兵训练任务（Train Soldier）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击 GLOBAL_BUTTON_TASKLIST 打开任务列表
  3. 在 global_task_list_search_area 区域内循环检索
     GLOBAL_TASK_LIST_SOLDIER_TRAINING_ICON：
     - 未找到 → 向上滑动，露出下方条目后继续检索
  4. 找到后点击 GLOBAL_TASK_LIST_SOLDIER_TRAIN_ENTRY 跳转兵营
  5. 点击 CITY_BUILDING_SOLDIER_ENTRY 位置弹出交互按钮，
     全屏检索 CITY_BUILDING_SOLDIER_BUTTON_TRAIN；
     检索不到时重新点击入口位置重试一次
  6. 点击 CITY_BUILDING_SOLDIER_BUTTON_TRAIN 跳转训练界面
  7. 依次处理 soldier_tag_infantry / soldier_tag_archer /
     soldier_tag_cavalry 三个标签（逻辑一致）：
     - 灰度匹配（use_gray_scale=True，忽略颜色影响）
     - 匹配到标签 → 点击 → 进入对应兵种操作界面
     - 可训练则检索并点击 SOLDIER_BUTTON_TRAINNOW
     - 找不到训练按钮 = 正在训练或无法训练 → 跳过该兵种
  8. 全部处理完后退回主界面

调度参数：
  - 不占军队队列（requires_march_queue=False，纯 UI 操作）
  - 一次性任务（one_shot=True，count=1）
  - 不可并行：任务列表 / 兵营 / 训练界面同一时刻只能打开一个
"""
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_BUTTON_TASKLIST,
    GLOBAL_TASK_LIST_SOLDIER_TRAINING_ICON,
    GLOBAL_TASK_LIST_SEARCH_AREA,
    GLOBAL_TASK_LIST_SOLDIER_TRAIN_ENTRY,
    CITY_BUILDING_SOLDIER_ENTRY,
    CITY_BUILDING_SOLDIER_BUTTON_TRAIN,
    SOLDIER_TAG_INFANTRY,
    SOLDIER_TAG_ARCHER,
    SOLDIER_TAG_CAVALRY,
    SOLDIER_BUTTON_TRAINNOW,
)
from src.sg.scene.scene_type import SceneType


class TrainSoldierTask(SGBaseTask):
    """
    士兵训练。

    =========================================================
    职责边界：
      - 只做业务编排：开任务列表 → 滚动检索训练入口 → 进兵营
        → 弹交互按钮 → 进训练界面 → 逐兵种尝试立即训练 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 任务列表向上滑动次数上限（每次 1/4 屏，参考黄金炼制）
    task_list_scroll_max = 10

    # 任务列表图标匹配阈值（固定样式）
    list_icon_threshold = 0.8

    # 兵种标签灰度匹配阈值（标签选中态底色会变，忽略颜色影响）
    soldier_tag_threshold = 0.8

    # 兵营交互训练按钮全屏检索等待
    train_button_timeout = 2.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Train Soldier"
        self.description = "Train soldiers via task list entry."

        # ====================================================
        # 队列注入字段（赋默认值，注册侧不下发个性化参数）
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

        # 依次处理的兵种标签（灰度匹配，忽略颜色）
        self.soldier_tags = (
            SOLDIER_TAG_INFANTRY,
            SOLDIER_TAG_ARCHER,
            SOLDIER_TAG_CAVALRY,
        )

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
    # 步骤 2：展开任务列表 → 滚动检索士兵训练图标
    # ========================================================

    def _open_task_list(self) -> bool:
        """点击 GLOBAL_BUTTON_TASKLIST，展开任务列表。"""
        if not self._wait_and_click_all_screen(
            GLOBAL_BUTTON_TASKLIST, timeout=2.0,
        ):
            self.log_info("未找到任务列表按钮")
            return False
        self._sleep(1.0)
        return True

    def _search_box(self):
        """任务列表检索区域：global_task_list_search_area 的 bbox。"""
        return self._safe_box(GLOBAL_TASK_LIST_SEARCH_AREA)

    def _scroll_find_soldier_icon(self) -> bool:
        """
        在 global_task_list_search_area 区域内检索
        GLOBAL_TASK_LIST_SOLDIER_TRAINING_ICON，
        未找到则向上滑动 1/4 屏，直到出现为止。

        滚轮在该列表不生效，必须用滑动（与宠物寻宝一致）。
        """
        search_box = self._search_box()

        for attempt in range(self.task_list_scroll_max + 1):
            icon_box = self._find(
                GLOBAL_TASK_LIST_SOLDIER_TRAINING_ICON,
                threshold=self.list_icon_threshold,
                box=search_box,
            )
            if icon_box is not None:
                self.log_info(
                    f"任务列表检索到士兵训练图标（滑动 {attempt} 次）"
                )
                return True

            # 未找到 → 手指向上滑 1/4 屏，露出下方条目
            self.swipe_relative(0.3, 0.6, 0.3, 0.3, duration=0.3,
                                settle_time=1.0)
            self._sleep(1.5)

        self.log_error("滚动查找士兵训练图标失败")
        return False

    def _click_soldier_train_entry(self) -> bool:
        """
        点击 GLOBAL_TASK_LIST_SOLDIER_TRAIN_ENTRY 跳转兵营。
        """
        if not self._wait_and_click_all_screen(
            GLOBAL_TASK_LIST_SOLDIER_TRAIN_ENTRY, timeout=2.0,
        ):
            self.log_info("未找到士兵训练入口按钮")
            return False
        self._sleep(2.0)
        return True

    def _enter_soldier_page(self) -> bool:
        """开列表 → 滚动检索 → 点入口。"""
        if not self._open_task_list():
            return False
        if not self._scroll_find_soldier_icon():
            return False
        return self._click_soldier_train_entry()

    # ========================================================
    # 步骤 3：兵营 → 弹交互按钮 → 进训练界面
    # ========================================================

    def _enter_training_page(self) -> bool:
        """
        点击 CITY_BUILDING_SOLDIER_ENTRY 弹出交互按钮，
        全屏检索 CITY_BUILDING_SOLDIER_BUTTON_TRAIN 并点击，
        进入训练界面。

        交互按钮检索不到时重新点击入口位置重试一次。
        """
        entry_box = self._safe_box(CITY_BUILDING_SOLDIER_ENTRY)
        if entry_box is None:
            self.log_error("未找到兵营入口 bbox")
            return False

        for attempt in range(2):
            self.click(entry_box)
            self._sleep(1.0)

            train_box = self._wait_element(
                CITY_BUILDING_SOLDIER_BUTTON_TRAIN,
                timeout=self.train_button_timeout,
                box=self.box_of_screen(0, 0, 1, 1),
                with_recovery=False,
            )
            if train_box is not None:
                self.log_info(
                    f"检索到兵营训练按钮（第 {attempt + 1} 次点击入口）"
                )
                self.click(train_box)
                self._sleep(2.0)
                return True

            self.log_info(
                f"未检索到兵营训练按钮（第 {attempt + 1} 次尝试）"
            )

        self.log_error("点击兵营入口后未出现训练按钮")
        return False

    # ========================================================
    # 步骤 4：逐兵种尝试立即训练
    # ========================================================

    def _train_soldier_tag(self, tag) -> bool:
        """
        处理单个兵种标签：灰度匹配 → 点击 → 尝试立即训练。

        - 标签未匹配到：视为当前页无该标签，跳过（返回 True）
        - 训练按钮未检索到：正在训练或无法训练，跳过（返回 True）
        """
        tag_box = self._find(
            tag,
            threshold=self.soldier_tag_threshold,
            box=self.box_of_screen(0, 0, 1, 1),
            use_gray_scale=True,
        )
        if tag_box is None:
            self.log_info(f"未匹配到标签 {tag.name}，跳过")
            return True

        self.log_info(f"匹配到标签 {tag.name}，点击进入")
        self.click(tag_box)
        self._sleep(1.0)

        train_box = self._find(
            SOLDIER_BUTTON_TRAINNOW,
            threshold=0.8,
            box=self.box_of_screen(0, 0, 1, 1),
        )
        if train_box is None:
            self.log_info(
                f"{tag.name}: 未检索到立即训练按钮（正在训练或无法训练），跳过"
            )
            return True

        self.log_info(f"{tag.name}: 点击立即训练")
        self.click(train_box)
        self._sleep(1.5)
        return True

    def _train_all_soldiers(self) -> bool:
        """依次处理三个兵种标签，单个兵种失败不阻断其余兵种。"""
        for tag in self.soldier_tags:
            if not self._train_soldier_tag(tag):
                self.log_error(f"处理标签 {tag.name} 失败")
                return False
        self.log_info("全部兵种处理完成")
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始士兵训练 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 任务列表检索士兵训练入口 ----
        if not self._step(self._enter_soldier_page, "进入兵营"):
            self.last_error = "任务列表检索士兵训练入口失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 兵营 → 训练界面 ----
        if not self._step(self._enter_training_page, "进入训练界面"):
            self.last_error = "进入训练界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 逐兵种尝试立即训练 ----
        if not self._step(self._train_all_soldiers, "逐兵种训练"):
            self.last_error = "逐兵种训练失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 退回主界面（失败不影响训练结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响训练结果")

        self.log_info("========== 士兵训练完成 ==========")
        return (InteractionResult.SUCCESS, 0)

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
        self.log_info("========== 士兵训练（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"士兵训练失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 士兵训练完成 ==========")
        return True
