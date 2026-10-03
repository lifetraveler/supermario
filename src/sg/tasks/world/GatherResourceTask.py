"""
野外资源采集任务（Gather Resource）。

与 Hunt Beast / Hunt Monster 的差异：
  - 不占集结判定（requires_march_queue=False）：
    本任务的目的就是去占用军队采集队列。
  - 一次任务实例内派出多个采集队列（<= 空闲队列数，最多 4 个）。
  - 资源选择：大型资源优先（滑动区域内 OCR 检测"大型"），
    普通资源按 面包 → 木材 → 石头 → 铁矿 循环，
    起始偏移 = 当天日期 % 4，跳过与大型资源同类型的普通矿。
  - 每轮：重新打开搜索面板 → 选资源 → 确认搜索 →
    OCR 预计采集时间 → 点"开始采集"。
  - 等级选择 TODO 待实现：使用游戏默认已选等级。
  - 无空闲军队队列 → FAILED，原因"没有采集队列"。

流程：
  1. 确保前台，清理弹窗
  2. 进入荒野（主城 → 跳转世界地图）
  3. OCR 读取军队队列（troop_march_team_num），空闲 = 最大 - 有效；
     空闲 <= 0 → FAILED（没有采集队列）
  4. 检测大型资源（滑动区域 OCR，当天缓存一次）
  5. 计算采集计划（大型优先 + 日期余数循环普通资源，最多 4 个）
  6. 循环计划：每轮重开搜索面板 → 选资源 → 确认 → OCR 时间 → 开始采集
  7. 全部派出（或计划耗尽）→ SUCCESS
"""

import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.element.overall.tasklist.worldtask.troop.troop import Troop
from src.sg.element.world.resource.gather.gather_searcher import (
    ResourceGatherSearcher,
    RESOURCE_NAMES,
)

from src.sg.scene.elements import (
    BUTTON_GOTO_WORLDMAP,
    WORLD_GATHER_REOURCE_TIME_AREA,
    WORLD_GATHER_REOURCE_BUTTON_START,
)
from src.sg.scene.scene_type import SceneType


class GatherResourceTask(SGBaseTask):
    """
    野外资源采集任务。

    =========================================================
    职责边界：
      - 只做业务编排：进荒野 → 读队列 → 规划 → 逐个搜索 → 开始采集
      - 队列数量读取交给 Troop（空闲 = 最大 - 有效）
      - 大型资源检测 / 采集计划 / 搜索选资源交给 ResourceGatherSearcher
      - 弹窗处理交给 ActivityPopup
      - 等级选择 TODO 待实现（游戏默认已选等级）
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Gather Resource"
        self.description = (
            "Gather field resources with free march queues. "
            "野外建筑资源采集。"
        )

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # 每轮开始采集后的稳定等待（秒），等采集动画/面板关闭。
        self.after_start_wait = 1.0

        # ====================================================
        # 个性化参数容器（factory 自动注入）
        # ====================================================
        self.extra_config = types.SimpleNamespace()

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

        # 军队队列：OCR 读取 有效/最大 数量
        self.troop = Troop(self)

        # 资源搜索：大型资源检测 + 采集计划 + 搜索面板选资源
        self.searcher = ResourceGatherSearcher(self)

        # 运行时状态：最后一轮 OCR 的预计采集时间（秒），仅日志/展示。
        self.last_gather_seconds = None

        # 运行时状态：本轮空闲队列数快照（计划生成回调使用）。
        self.free_slots_snapshot = 0

        # 运行时状态：最近一次计划取出的资源 key（日志显示用）。
        self._last_plan_key = None

    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试
    # ========================================================


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
                BUTTON_GOTO_WORLDMAP, timeout=6.0
            ):
                return False
            return self._wait_scene(
                SceneType.WORLD_MAP, timeout=10.0, interval=0.5
            )

        self.log_error(f"无法进入荒野，未知场景: {scene.type.value}")
        return False

    # ========================================================
    # 队列读取
    # ========================================================

    def read_free_slots(self) -> int:
        """
        读取空闲军队队列数 = 最大 - 有效。
        读取失败返回 0（调用方按"没有采集队列"处理）。
        """
        valid_count, max_count = self.troop.read_counts()
        if valid_count is None or max_count is None:
            self.log_info("队列数量读取失败")
            return 0
        return self.troop.free_slot_count()

    # ========================================================
    # 单个资源的采集循环
    # ========================================================

    def _search_and_gather(self, key) -> bool:
        """
        执行一轮采集：
          搜索面板 → 选资源（首轮同时检测大型资源并生成计划）
          → 确认搜索 → OCR 采集时间 → 开始采集。

        key 为 None 表示首轮（仅打开面板触发检测与计划生成，
        实际选用的资源由 searcher.plan[0] 决定）。

        游戏确认搜索后会居中目标并弹出采集确认面板，
        时间区域 OCR 只做暂存展示，不参与判定。
        """
        # ---- 搜索（重开面板，面板内状态可能已变） ----
        if key is None:
            if not self._step(
                lambda: self.searcher.run_for(self._first_plan_key()),
                "搜索资源",
            ):
                self.last_error = "搜索资源失败（首轮）"
                self.log_error(self.last_error)
                return False
        else:
            if not self._step(
                lambda: self.searcher.run_for(key), "搜索资源"
            ):
                self.last_error = f"搜索资源失败: {RESOURCE_NAMES[key]}"
                self.log_error(self.last_error)
                return False

        # ---- OCR 预计采集时间（暂存展示） ----
        self._read_gather_time()

        # ---- 开始采集 ----
        if not self._step(self._click_start_gather, "开始采集"):
            self.last_error = "点击开始采集失败"
            self.log_error(self.last_error)
            return False

        name = RESOURCE_NAMES.get(
            key, RESOURCE_NAMES.get(self._last_planned_key(), "?")
        )
        self.log_info(f"采集已开始: {name}")
        return True

    def _first_plan_key(self):
        """首轮调用：计划生成后取出并返回第一个资源；计划为空返回 None。"""
        if self.searcher.plan:
            key = self.searcher.plan.pop(0)
            self._last_plan_key = key
            return key
        return None

    def _last_planned_key(self):
        """最近一次计划取出的资源 key，供日志显示。"""
        return getattr(self, "_last_plan_key", None)

    def _read_gather_time(self):
        """
        OCR wolrd_gather_reource_time_area 读取预计采集时间，暂存展示。
        读取失败不影响流程（时间仅用于日志 / info 展示）。
        """
        box = self._find(
            WORLD_GATHER_REOURCE_TIME_AREA,
            box=self.box_of_screen(0, 0, 1, 1),
        )
        if box is None:
            self.log_info("未找到预计采集时间区域，跳过时间 OCR")
            self.last_gather_seconds = None
            return

        try:
            results = self.ocr(box=box)
        except Exception as e:
            self.log_info(f"OCR 预计采集时间异常: {e}")
            results = None

        if not results:
            self.log_info("预计采集时间 OCR 无结果")
            self.last_gather_seconds = None
            return

        text = " ".join((b.name or "") for b in results)
        self.log_info(f"OCR 预计采集时间原文: {text}")
        seconds = self._parse_time_text(text)
        self.last_gather_seconds = seconds
        if seconds is not None:
            self.info_set("预计采集时间(秒)", seconds)
            self.log_info(f"预计采集时间: {seconds}s")

    def _parse_time_text(self, text):
        """
        把 OCR 文本解析成秒。
        支持: '90', '1:30', '1:30:00'（与 Beast._parse_time_text 同口径）。
        """
        import re

        if text is None:
            return None
        text = str(text).strip()
        if not text:
            return None

        if text.isdigit():
            return int(text)

        if ":" in text or "：" in text:
            normalized = text.replace("：", ":")
            parts = normalized.split(":")
            try:
                nums = [int(p) for p in parts if p.strip()]
            except ValueError:
                nums = []
            if len(nums) == 2:
                return nums[0] * 60 + nums[1]
            if len(nums) == 3:
                return nums[0] * 3600 + nums[1] * 60 + nums[2]
            return None

        # 兜底：提取文本中的 时/分/秒 数字合并
        total = 0
        hour = re.search(r"(\d+)\s*[时小]", text)
        minute = re.search(r"(\d+)\s*分", text)
        second = re.search(r"(\d+)\s*秒", text)
        if hour:
            total += int(hour.group(1)) * 3600
        if minute:
            total += int(minute.group(1)) * 60
        if second:
            total += int(second.group(1))
        return total if total > 0 else None

    def _click_start_gather(self) -> bool:
        """点击 wolrd_gather_reource_button_start 开始采集。"""
        if not self._wait_and_click(
            WORLD_GATHER_REOURCE_BUTTON_START, timeout=8.0
        ):
            return False
        self._sleep(self.after_start_wait)
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def _make_plan(self, large_key):
        """
        计划生成回调（注入 searcher.plan_provider）。
        在搜索面板打开、大型资源检测完成后由 searcher 调用一次。
        """
        free_slots = self.free_slots_snapshot or 0
        self.searcher.large_resource_type = large_key
        return self.searcher.build_gather_plan(free_slots)

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。

        流程：
          1. 前置：确保前台、清弹窗
          2. 进入荒野
          3. 读空闲队列数；<=0 → FAILED（没有采集队列）
          4. 逐个资源搜索：首轮面板打开后检测大型资源并生成计划，
             后续轮次直接按计划取下一个资源
          5. 每轮：搜索 → OCR 采集时间 → 开始采集
          6. 计划完成 → SUCCESS
        """
        self.log_info("========== 开始野外资源采集 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 进入荒野 ----
        if not self._step(self.enter_wilderness, "进入荒野"):
            self.last_error = "进入荒野失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 空闲队列检查 ----
        free_slots = self.read_free_slots()
        if free_slots <= 0:
            self.last_error = "没有采集队列（空闲队列数为 0 或读取失败）"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)
        self.log_info(f"空闲采集队列: {free_slots}")
        self.free_slots_snapshot = free_slots

        # 计划在首轮搜索面板打开后生成（大型资源检测需要面板内滑动区域）。
        self.searcher.plan_provider = self._make_plan

        # ---- 循环派出采集队列 ----
        # 首轮（计划未生成）：key=None，打开面板后检测大型资源并生成计划；
        # 计划生成后由 _first_plan_key() 取出第一个资源并执行搜索。
        # 后续轮次：计划逐个弹出，计划耗尽 → 任务完成。
        started = 0
        while True:
            if self.searcher.plan_provided:
                if not self.searcher.plan:
                    break  # 计划耗尽 → 任务完成
                key = self.searcher.plan.pop(0)
                self._last_plan_key = key
                self.log_info(
                    f"采集目标: {RESOURCE_NAMES[key]} "
                    f"(第 {started + 1} 队)"
                )
            else:
                key = None  # 首轮触发面板打开 + 检测 + 计划生成

            if not self._search_and_gather(key):
                return (InteractionResult.FAILED, 0)
            started += 1
            self.info_set("已派出采集队列", started)

        if started == 0:
            # 首轮计划生成即为空（理论不可达，兜底保护）。
            self.last_error = "采集计划为空"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        self.log_info(
            f"========== 野外资源采集完成，共派出 {started} 个采集队列 "
            f"=========="
        )
        # 采集开始后军队队列被占用，本任务自身不需要等待。
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
        self.log_info("========== 野外资源采集（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"野外资源采集失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 野外资源采集完成 ==========")
        return True
