"""
宠物寻宝派遣任务（Pet Treasure Hunt）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗
  2. city 主界面根据 GLOBAL_EVENT_TASK_NEED_HANDLE 区域点击，
     弹出城市的 tasklist
  3. 参考 海岛采水 GatherIslandWaterTask：向上滑动，
     直到找到 GLOBAL_TASK_LIST_PET_SEARCH_TREASURE_ENTRY 入口，点击后
     跳转到宠物寻找宝藏界面
  4. 全屏循环匹配 PET_SYMBOL_TREASURE_HUNT_1 / _2 / _3：
     - 每找到一个点击，跳转到派遣界面
     - 直接点击 PET_BUTTON_SEARCH_TREASURE 派遣，本次结束
     - 回到宝藏界面继续找下一个，直到找不到
  5. 最终返回主界面

匹配参数（按需求原文）：
  - timeout=2，不重试（with_recovery=False）
  - 找不到所有标志 → 派遣结束（不是失败）

调度参数：
  - 不占军队队列（requires_march_queue=False，宠物派遣不占行军）
  - 每日一次（next_trigger_delay=86400）
  - 不可并行：任务列表 / 宝藏界面同一时刻只能打开一个
"""

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_EVENT_TASK_NEED_HANDLE,
    GLOBAL_TASK_LIST_PET_SEARCH_TREASURE_ENTRY,
    PET_SYMBOL_TREASURE_HUNT_1,
    PET_SYMBOL_TREASURE_HUNT_2,
    PET_SYMBOL_TREASURE_HUNT_3,
    PET_BUTTON_SEARCH_TREASURE,
    PET_BUTTON_SEARCH_TREASURE_START,
    PET_SYMBOL_TREASURE_REWARD_ENTRY,
    GLOBAL_TASK_LIST_PET_SEARCH_TREASURE_MASK,
    PET_SYMBOL_TREASURE_FRIEND_TAG_SEND,
    PET_SYMBOL_TREASURE_FRIEND_TAG_SEND_BUTTON_GET,
    PET_SYMBOL_TREASURE_MY_TAG_SEND,
    PET_REWARD_GETED_TIPS,
)
from src.sg.scene.scene_type import SceneType


class PetTreasureHuntTask(SGBaseTask):
    """
    宠物寻宝派遣。

    =========================================================
    职责边界：
      - 只做业务编排：展开任务列表 → 进宝藏界面 → 循环派遣 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 任务列表向上滑动次数上限（每次 1/4 屏，参考岛屿采水）
    task_list_scroll_max = 8

    # 宝藏标志全屏匹配阈值（标志样式固定，略提阈值减少误点）
    symbol_threshold = 0.8

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Pet Treasure Hunt"
        self.description = "Dispatch pets to search treasures."

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试（标准实现，勿改）
    # ========================================================


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
    # 步骤 2：展开任务列表 → 滚动查找宠物寻宝入口
    # ========================================================

    def _open_task_list(self) -> bool:
        """点击 GLOBAL_EVENT_TASK_NEED_HANDLE，展开 city 任务列表。"""
        if not self._wait_and_click_all_screen(
            GLOBAL_EVENT_TASK_NEED_HANDLE, timeout=6.0,
        ):
            self.log_info("未找到任务列表入口")
            return False
        self._sleep(1.0)
        return True

    def _scroll_find_pet_entry(self) -> bool:
        """
        在任务列表中向上滑动（手指上滑 1/4 屏），
        直到找到宠物寻宝入口并点击，进入宝藏界面。

        滚动方式与 GatherIslandWaterTask._scroll_find_island_entry 一致：
        滚轮在该列表不生效，必须用滑动。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        for attempt in range(self.task_list_scroll_max + 1):
            entry_box = self._find(
                GLOBAL_TASK_LIST_PET_SEARCH_TREASURE_MASK,
                threshold=0.8, box=full_screen
            )
            if entry_box is not None:
                entry_box = self._find(
                GLOBAL_TASK_LIST_PET_SEARCH_TREASURE_ENTRY,
                threshold=0.8, box=full_screen
            )
                self.log_info("点击任务列表宠物寻宝入口")
                self.click(entry_box)
                self._sleep(2.0)
                return True

            # 未找到 → 手指向上滑 1/4 屏，露出下方条目
            self.swipe_relative(0.3, 0.6, 0.3, 0.3, duration=0.3,
                                settle_time=1.0)
            self._sleep(1.5)

        self.log_error("滚动查找宠物寻宝入口失败")
        return False

    def _enter_treasure_page(self) -> bool:
        return self._open_task_list() and self._scroll_find_pet_entry()

    # ========================================================
    # 步骤 3：循环派遣宝藏
    # ========================================================

    def _find_dispatchable_symbol(self):
        """
        全屏匹配一个可派遣的宝藏标志。
        按需求：timeout=2、不重试（with_recovery=False）。
        找到返回 box，找不到返回 None。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)
        for element in (PET_SYMBOL_TREASURE_HUNT_1,
                        PET_SYMBOL_TREASURE_HUNT_2,
                        PET_SYMBOL_TREASURE_HUNT_3):
            box = self._wait_element(
                element,
                timeout=2.0,
                threshold=0.95,
                box=full_screen,
                with_recovery=False,
            )
            if box is not None:
                return box
        return None

    def _dispatch_one(self, symbol_box) -> bool:
        """
        点击宝藏标志 → 跳转派遣界面 → 点击派遣按钮。
        """
        self.log_info("点击可派遣宝藏标志")
        if not self.click(symbol_box):
            return False
        self._sleep(1.5)
        
         # 派遣界面：直接点击派遣按钮
        if not self._wait_and_click(
            PET_BUTTON_SEARCH_TREASURE_START, name="宠物派遣",
            timeout=6.0, box=self.box_of_screen(0, 0, 1, 1),
        ):
            return False
        self._sleep(1.5)
        # 派遣界面：直接点击派遣按钮
        if not self._wait_and_click(
            PET_BUTTON_SEARCH_TREASURE, name="开始寻宝",
            timeout=6.0, box=self.box_of_screen(0, 0, 1, 1),
        ):
            return False
        self._sleep(1.5)
        return True

    def _get_mates_reward(self) -> bool:
        """
        
        找到 → 派遣一次；找不到 → 结束（正常完成）。
        """
        if not self._wait_and_click(PET_SYMBOL_TREASURE_REWARD_ENTRY,timeout=2,with_recovery=False,threshold=0.95):
            self.log_info("没有找到奖励")
            return False
        self._sleep(1)
        # 领取队友的，直接点击
        box = self.get_box_by_name(PET_SYMBOL_TREASURE_FRIEND_TAG_SEND.resource_id)
        if box is None:
            self.log_error("未找到盟友赠礼 tag 的 bbox")
            return False
        self.log_info("点击盟友赠礼 tag 切换")
        self.click_box(box)
        self._sleep(1)
        self._wait_and_click(PET_SYMBOL_TREASURE_FRIEND_TAG_SEND_BUTTON_GET,timeout=2,with_recovery=False)
        self._sleep(2)
        self._wait_and_click(PET_REWARD_GETED_TIPS,timeout=2,with_recovery=False)        
        return True

            
            
    def _dispatch_all(self) -> bool:
        """
        循环派遣：每轮全屏匹配宝藏标志，
        找到 → 派遣一次；找不到 → 结束（正常完成）。

        返回 True 表示流程走完（无论派出几个）。
        """
        dispatched = 0
        while True:
            symbol_box = self._find_dispatchable_symbol()
            if symbol_box is None:
                self.log_info(
                    f"无可派遣宝藏标志，派遣结束，共派遣 {dispatched} 次"
                )
                return True

            if not self._step(
                lambda b=symbol_box: self._dispatch_one(b),
                f"派遣宝藏#{dispatched + 1}",
            ):
                self.log_error("派遣宝藏失败")
                return False
            self.recovery._try_press_esc()            
            dispatched += 1

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始宠物寻宝派遣 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入宝藏界面 ----
        if not self._step(self._enter_treasure_page, "进入宝藏界面"):
            self.last_error = "进入宝藏界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 循环派遣 ----
        if not self._step(self._dispatch_all, "循环派遣宝藏"):
            self.last_error = "派遣宝藏失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)
        
        # ---- 领取联盟宝藏 ----
        if not self._step(self._get_mates_reward, "循环派遣宝藏"):
            self.last_error = "派遣宝藏失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)
        
        # ---- 退回主界面（失败不影响派遣结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响派遣结果")

        self.log_info("========== 宠物寻宝派遣完成 ==========")
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
        self.log_info("========== 宠物寻宝派遣（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"宠物寻宝派遣失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 宠物寻宝派遣完成 ==========")
        return True
