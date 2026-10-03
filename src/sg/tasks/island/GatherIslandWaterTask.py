"""
岛屿采水任务（Gather Island Water）。

生命之泉（核心建筑）产水，最多积累 10 小时，因此本任务每 10 小时执行一次：
  1. 从 city 任务进度列表进入自己的海岛
  2. 缩放到最小（ctrl + 滚轮向下）
  3. 全屏检索并点击产水建筑（island_gather_water_build_1 / _2），一键采水
  4. 通过奖励入口切换到领取界面，领取采水奖励
  5. 出现"获得奖励"提示后点击退出

流程：
  1. 确保前台，清理弹窗
  2. 若不在主界面（城市/世界地图）→ 按 ESC 回到主界面
  3. 点击 global_event_task_need_handle 展开任务列表
  4. 手指向上滑动（每次 1/4 屏），循环全屏搜索任务列表中的海岛入口并点击
  5. ctrl+滚轮缩放到最小
  6. 循环点击两个产水建筑完成采水
  7. 点击 island_button_gather_reward 切换到领取界面
  8. 点击 island_button_reward_get 领取
  9. 等 global_rewward_geted_quit_tip 出现后点击退出
"""

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_EVENT_TASK_NEED_HANDLE,
    ISLAND_AREA_SYMBOL,
    ISLAND_GATHER_WATER_BUILD_1,
    ISLAND_GATHER_WATER_BUILD_2,
    ISLAND_BUTTON_GATHER_REWARD,
    ISLAND_BUTTON_REWARD_GET,
    GLOBAL_MARK_REWARD_GETED_QUIT_TIP,
    GLOBAL_EVENT_TASK_LIST_ISLAND_ENTRY,
)
from src.sg.scene.scene_type import SceneType


class GatherIslandWaterTask(SGBaseTask):
    """
    进入自己的海岛，采集水资源并领取采水奖励。

    =========================================================
    职责边界：
      - 只做业务编排：回主界面 → 进岛 → 缩放 → 采水 → 领奖
      - 不占军队队列（requires_march_queue=False）
      - 生命之泉最多积累 10 小时 → 每 10 小时触发一次
      - 弹窗处理交给 ActivityPopup
      - 恢复细节交给 RecoveryHelper
    =========================================================
    """

    # --------------------------------------------------------
    # 步骤级参数（可按需调整）
    # --------------------------------------------------------

    # 任务列表向下滚动次数上限（每次滚 1/4 屏）
    task_list_scroll_max = 8

    # ctrl+滚轮缩小次数（缩放到最小）
    zoom_out_steps = 6

    # 产水建筑循环点击轮数上限
    gather_rounds_max = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Gather Island Water"
        self.description = (
            "Enter own island, gather water and claim the gather reward."
        )

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
    # 步骤 2：展开任务列表 → 滚动查找海岛入口 → 进入海岛
    # ========================================================

    def _open_task_list(self) -> bool:
        """点击任务列表入口，展开 city 任务进度列表。"""
        if not self._wait_and_click_all_screen(
            GLOBAL_EVENT_TASK_NEED_HANDLE,
            timeout=6.0,
        ):
            self.log_info("未找到任务列表入口")
            return False
        self._sleep(1.0)
        return True

    def _scroll_find_island_entry(self) -> bool:
        """
        在任务列表中滚动查找海岛入口。

        判定"已进入海岛"的依据：
          - 全屏检索到 ISLAND_AREA_SYMBOL（岛屿区域标志），或
          - 检索到 ISLAND_BUTTON_GATHER_WATER（岛屿采水按钮）
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)

        for attempt in range(self.task_list_scroll_max + 1):
            # 先直接检索海岛标志（可能已经进岛 / 无需滚动）
            island_box = self._find(
                ISLAND_AREA_SYMBOL, threshold=0.7, box=full_screen
            )
            if island_box is not None:
                self.log_info("已进入海岛界面（检索到岛屿标志）")
                return True

            # 全屏搜索任务列表中的海岛入口并点击
            entry_box = self._find(
                GLOBAL_EVENT_TASK_LIST_ISLAND_ENTRY, threshold=0.8, box=full_screen
            )
            if entry_box is not None:
                self.log_info("点击任务列表海岛入口")
                self.click(entry_box)
                self._sleep(2.0)

                # 点击后确认是否已进入海岛
                island_box = self._find(
                    ISLAND_AREA_SYMBOL, threshold=0.7, box=full_screen
                )
                if island_box is not None:
                    self.log_info("已进入海岛界面")
                    return True

            # 未进岛 → 向上滑 1/4 屏，继续找
            # 手指向上滑 1/4 屏：列表内容上移，露出下方条目。
            # 滚轮在该列表不生效，必须用滑动。
            self.swipe_relative(0.3, 0.6, 0.3, 0.3, duration=0.3,
                                settle_time=1.0)
            self._sleep(1.5)

        self.log_error("滚动查找海岛入口失败")
        return False

    def _enter_island(self) -> bool:
        return self._open_task_list() and self._scroll_find_island_entry()

    # ========================================================
    # 步骤 3：缩放到最小（ctrl + 滚轮向下）
    # ========================================================

    def _zoom_out_island(self) -> bool:
        self.log_info("按住 ctrl 滚轮向下缩放到最小")
        self.send_key_down("ctrl")
        self._sleep(0.3)
        for _ in range(self.zoom_out_steps):
            self.scroll_relative(0.5, 0.5, -5)
            self._sleep(0.4)
        self.send_key_up("ctrl")
        self._sleep(1.0)
        return True

    # ========================================================
    # 步骤 4：点击产水建筑完成采水
    # ========================================================

    def _gather_water_builds(self) -> bool:
        """
        缩放最小后，全屏检索两个产水建筑并点击采水。

        返回 True 表示至少成功采到一处水。
        """
        full_screen = self.box_of_screen(0, 0, 1, 1)
        builds = (ISLAND_GATHER_WATER_BUILD_1, ISLAND_GATHER_WATER_BUILD_2)

        gathered = 0
        for round_no in range(1, self.gather_rounds_max + 1):
            clicked_any = False

            for build in builds:
                box = self._find(build, threshold=0.7, box=full_screen)
                if box is None:
                    continue

                self.log_info(f"点击产水建筑: {build.name}")
                if self.click(box):
                    gathered += 1
                    clicked_any = True
                    self._sleep(1.5)

            if not clicked_any:
                # 一整轮没有点到任何建筑 → 采水动作完成
                break

        if gathered == 0:
            self.log_error("未找到任何产水建筑")
            return False

        self.log_info(f"采水完成，共点击 {gathered} 处")
        return True

    # ========================================================
    # 步骤 5：领取采水奖励
    # ========================================================

    def _claim_gather_reward(self) -> bool:
        # 切换到领取界面
        if not self._wait_and_click(
            ISLAND_BUTTON_GATHER_REWARD,
            timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1),
        ):
            self.log_info("未找到奖励入口，跳过领奖")
            return False

        self._sleep(1.0)

        # 点击领取按钮
        if not self._wait_and_click(
            ISLAND_BUTTON_REWARD_GET,
            timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1),
        ):
            self.log_info("未找到领取按钮，跳过领奖")
            return False

        self._sleep(1.0)

        # 等待"获得奖励"提示出现后点击退出
        quit_box = self._wait_element(
            GLOBAL_MARK_REWARD_GETED_QUIT_TIP,
            timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1),
            with_recovery=False
        )
        if quit_box is None:
            self.log_info("未出现获得奖励提示，跳过退出点击")
            return True

        self.log_info("点击获得奖励提示退出")
        self.click(quit_box)
        self._sleep(1.0)
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始岛屿采水 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入海岛 ----
        if not self._step(self._enter_island, "进入海岛"):
            self.last_error = "进入海岛失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 缩放到最小 ----
        if not self._step(self._zoom_out_island, "缩放海岛"):
            self.last_error = "缩放海岛失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 采水 ----
        if not self._step(self._gather_water_builds, "采水"):
            self.last_error = "采水失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 领取采水奖励（失败不阻塞任务结果）----
        if not self._step(self._claim_gather_reward, "领取采水奖励",with_recovery=False):
            self.log_info("领取采水奖励失败，不影响采水结果")

        self.log_info("========== 岛屿采水完成 ==========")
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
        self.log_info("========== 岛屿采水（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"岛屿采水失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 岛屿采水完成 ==========")
        return True
