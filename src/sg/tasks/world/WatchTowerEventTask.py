"""
瞭望塔事件任务。

游戏规则：
  - 瞭望塔位于世界主界面（荒野），事件每天 0/8/16 点刷新。
  - 处理事件消耗体力：体力 >= 12 才继续。
  - 事件类型：
      * CONQ      → 讨伐：点讨伐按钮 → 等待约 5s → 讨伐成功标志出现 → 完成
      * ZHANGPENG → 营救：点击即可完成
      * BEAST     → 挑战：点击杀按钮完成；占用军队队列，可配置占用上限

流程：
  1. 确保前台，清理弹窗，回到主界面（城市 → 跳荒野）
  2. OCR world_event_stamina 区域读体力，< 12 结束
  3. 按 bbox 点击瞭望塔标记（WILDERNESS_TARGET）进入瞭望塔界面
  4. 按固定优先级全屏搜索事件（ZHANGPENG_3 → ... → CONQ_1），
     特征匹配命中后点击，弹出前往查看对话框
  5. 全屏检索 world_event_button_forward 并点击，
     画面自动跳转到具体事件对象
  6. 按 world_event_object_handlearea 的 bbox 确认事件对象页，
     再按第 4 步命中的事件类型分派处理（见各 handler）
  7. 处理完成后回到第 2 步循环，直到无事件可处理或体力耗尽

独立运行（run）：跑满一轮（无事件或体力耗尽）。
队列运行（run_interaction）：每轮 run_interaction 处理一个事件，
    返回 SUCCESS 后由 TaskQueue 建立后续任务。
"""

import time

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    BUTTON_GOTO_WORLDMAP,
    WILDERNESS,
    WORLD_EVENT_STAMINA,
    WORLD_EVENT_BUTTON_FORWARD,
    WORLD_EVENT_OBJECT_HANDLEAREA,
    WORLD_EVENT_FIGHT,
    WORLD_EVENT_FIGHT_SUCCESS,
    WORLD_EVENT_BEAST_KILL,
    WORLD_EVENT_ZHANGPENG_1,
    WORLD_EVENT_ZHANGPENG_3,
    WORLD_EVENT_BEAST_1,
    WORLD_EVENT_BEAST_2,
    WORLD_EVENT_BEAST_3,
    WORLD_EVENT_CONQ_1,
    WORLD_EVENT_CONQ_2,
    WORLD_EVENT_CONQ_3,
    WORLD_EVENT_FIRED,
)
from src.sg.scene.scene_type import SceneType

# 搜索优先级：帐篷 > 野兽 > 征服（用户指定顺序）
_EVENT_SEARCH_ORDER = (
    WORLD_EVENT_ZHANGPENG_3,
    WORLD_EVENT_ZHANGPENG_1,
    WORLD_EVENT_BEAST_3,
    WORLD_EVENT_BEAST_2,
    WORLD_EVENT_BEAST_1,
    WORLD_EVENT_CONQ_3,
    WORLD_EVENT_CONQ_2,
    WORLD_EVENT_CONQ_1,
)

# 事件类型分派：resource_id 含关键字 → 处理方法名
_EVENT_HANDLERS = {
    "conq": "_handle_conq_event",
    "zhangpeng": "_handle_zhangpeng_event",
    "beast": "_handle_beast_event",
}

# 事件处理消耗的最低体力
_MIN_STAMINA_REQUIRED = 12

# 讨伐点击后的固定等待（游戏规则：约 5 秒出结果）
_FIGHT_SETTLE_SECONDS = 5.0

_FIGHT_SUCCESS_TIMEOUT = 15.0


class WatchTowerEventTask(SGBaseTask):
    """
    瞭望塔事件处理。

    =========================================================
    职责边界：
      - 只做业务编排：回主界面 → 读体力 → 进瞭望塔 →
        搜事件 → 前往 → 按类型处理 → 循环
      - 弹窗处理交给 ActivityPopup
      - 步骤失败恢复交给 RecoveryHelper
      - 挑战事件的军队队列占用上限由队列注入 beast_queue_limit
    不负责：
      - 任务次数与调度（由 GenericQueueTask / TaskQueue 负责）
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Watch Tower Event"
        self.description = (
            "Handle watch tower events "
            "(rescue tents, conquer monsters, challenge beasts)."
        )

        # ====================================================
        # 队列注入字段（由外部赋值）
        # ====================================================
        # 挑战（BEAST）类事件最多可占用的军队队列数。
        # 0 表示不挑战，直接跳过野兽类事件。
        self.beast_queue_limit = 1

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2
        self.recover_interval = 3.0

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

        # ====================================================
        # 内部状态
        # ====================================================
        # 本轮命中的事件元素（WORLD_EVENT_* SceneElement）
        self._hit_event = None

        # 体力耗尽标志：体力低于下限属正常终止，区别于识别失败
        self._stamina_exhausted = False

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
    # 安全 bbox 定位：ok 框架 get_box_by_name 找不到会抛 ValueError
    # ========================================================

    def _safe_box(self, element) -> "Box | None":
        """
        按 SceneElement 的 resource_id 取 bbox。
        框架 get_box_by_name 找不到类别时抛 ValueError，
        这里转为 None，让 _step 的恢复重试机制接管。
        """
        try:
            return self.get_box_by_name(element.resource_id)
        except ValueError:
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

    def _enter_world_map(self) -> bool:
        """
        确保在世界主界面（荒野）。
        已在世界地图直接返回；在城市则点击跳转标签。
        """
        scene = self.scene_detector.detect()
        self.log_info(f"当前场景: {scene.type.value}")

        if scene.type == SceneType.WORLD_MAP:
            self.log_info("当前已经在世界主界面")
            return True

        if scene.type == SceneType.CITY:
            if not self._wait_and_click(
                BUTTON_GOTO_WORLDMAP, timeout=6.0
            ):
                return False
            return self._wait_scene(
                SceneType.WORLD_MAP, timeout=10.0, interval=0.5
            )

        self.log_error(f"无法进入世界主界面，未知场景: {scene.type.value}")
        return False

    # ========================================================
    # 步骤 2：体力检查
    # ========================================================

    def _read_stamina(self):
        """
        OCR world_event_stamina 区域读取当前体力。
        识别失败返回 None。
        """

        box = self.get_box_by_name(WORLD_EVENT_STAMINA.resource_id)
        try:
            if box:
                results = self.ocr(box=box)
        except Exception as e:
            self.log_info(f"OCR 体力异常: {e}")
            return None

        if not results:
            self.log_info("未识别到体力")
            return None

        text = results[0].name
        self.log_info(f"OCR 体力值原始文本为: {text}")

        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            self.log_info("体力文本中未解析出数字")
            return None
        return int(digits)

    def _has_enough_stamina(self) -> bool:
        stamina = self._read_stamina()
        if stamina is None:
            self.log_error("体力读取失败，无法判断能否处理事件")
            return False

        if stamina < _MIN_STAMINA_REQUIRED:
            self.log_info(
                f"当前体力 {stamina} 低于 {_MIN_STAMINA_REQUIRED}，"
                f"结束瞭望塔事件处理"
            )
            self._stamina_exhausted = True
            return False

        self.log_info(
            f"当前体力 {stamina} >= {_MIN_STAMINA_REQUIRED}，可以继续"
        )
        return True

    # ========================================================
    # 步骤 3：进入瞭望塔界面
    # ========================================================

    def _enter_watch_tower(self) -> bool:
        """
        按 bbox 直接点击世界主界面的瞭望塔标记，进入瞭望塔界面。
        进入成功的标志：全屏能找到任一事件特征（或前进按钮出现前的
        对话框）。这里以"找到至少一个事件特征"为准。
        """
        box = self._safe_box(WILDERNESS)
        if not box:
            self.log_error("未找到瞭望塔标记 bbox")
            return False

        self.log_info("点击瞭望塔标记")
        if not self.click(box):
            return False
        self._sleep(1.0)
        return True

        # # 等待瞭望塔界面出现：全屏检索任一事件特征 box=self.box_of_screen(0, 0, 1, 1) 是全屏参数
        # found = self._wait_element(
        #     list(_EVENT_SEARCH_ORDER), with_recovery=False,timeout=30,box=self.box_of_screen(0, 0, 1, 1)
        # )
        # if found is not None:
        #     self.log_info("已进入瞭望塔界面（发现事件特征）")
        #     # _wait_element 返回的是 box，不携带元素信息；
        #     # 交给下一步 _search_event 重新精确匹配。
        #     return True

        # self.log_info("瞭望塔界面未发现任何事件特征")
        # return False

    # ========================================================
    # 步骤 4：按优先级搜索事件并点击
    # ========================================================

    def _search_event(self) -> bool:
        """
        按固定优先级全屏搜索事件特征，命中即点击，弹出前往查看对话框。

        命中的元素记录到 self._hit_event，供第 6 步分派。
        全部未命中返回 False（表示没有可处理的事件）。
        """
        self._hit_event = None
        
        # 这里先写一个处理任务已经完成的，根据 world_event_fired
        while True:
            if not self._wait_and_click(WORLD_EVENT_FIRED,box=self.box_of_screen(0, 0, 1, 1),with_recovery=False,timeout=2):
                break
            else:
                self._wait_and_click(WORLD_EVENT_FIRED,box=self.box_of_screen(0, 0, 1, 1),with_recovery=False,timeout=2)
                self._sleep(2.0)
        
        for element in _EVENT_SEARCH_ORDER:
            box = self._find(element,box=self.box_of_screen(0, 0, 1, 1))
            if box is None:
                continue

            self.log_info(f"发现事件: {element.name}")
            if not self.click(box):
                return False
            self._hit_event = element
            self._sleep(0.8)
            return True

        self.log_info("瞭望塔内没有可处理的事件")
        return False

    # ========================================================
    # 步骤 5：点击"前往查看"进入事件对象页
    # ========================================================

    def _go_to_event_object(self) -> bool:
        """
        对话框弹出后，全屏检索 world_event_button_forward 并点击。
        点击后画面自动跳转到具体事件对象。
        """
        if not self._wait_and_click(
            WORLD_EVENT_BUTTON_FORWARD,
            box=self.box_of_screen(0, 0, 1, 1),
        ):
            return False
        # 跳转到事件对象需要时间
        self._sleep(1.5)
        return True

    # ========================================================
    # 步骤 6：按事件类型分派处理
    # ========================================================

    def _dispatch_event(self) -> bool:
        """
        根据 _search_event 命中的事件类型分派到对应处理流程。
        world_event_object_handlearea 用于确认已到达事件对象页。
        """
        if self._hit_event is None:
            self.log_error("未记录命中事件，无法分派")
            return False

        rid = self._hit_event.resource_id

        # 确认已到达事件对象页：handlearea 的 bbox 可定位
        handle_box = self._safe_box(WORLD_EVENT_OBJECT_HANDLEAREA)
        if not handle_box:
            self.log_error("未找到事件处理区域 bbox，可能未跳转成功")
            return False
        if not self.click(handle_box):
            return False
        for key, handler_name in _EVENT_HANDLERS.items():
            if key in rid:
                handler = getattr(self, handler_name)
                self.log_info(f"分派事件处理: {handler_name}")
                return handler()

        self.log_error(f"未知事件类型: {rid}")
        return False

    # --------------------------------------------------------
    # 5.1 讨伐流程（CONQ）
    # --------------------------------------------------------

    def _handle_conq_event(self) -> bool:
        """
        讨伐流程：
          1. bbox 直接点击 wolrd_event_fight
          2. 等待约 5 秒
          3. 等待 world_event_fight_success 特征出现 → 成功
        """
        box = self._safe_box(WORLD_EVENT_FIGHT)
        if not box:
            self.log_error("未找到讨伐按钮 bbox")
            return False

        self.log_info("点击讨伐按钮")
        if not self.click(box):
            return False

        self.log_info(f"等待讨伐结果 {_FIGHT_SETTLE_SECONDS:.0f}s")
        self._sleep(_FIGHT_SETTLE_SECONDS)

        success_box = self._wait_element(
            WORLD_EVENT_FIGHT_SUCCESS,
            timeout=_FIGHT_SUCCESS_TIMEOUT,
        )
        if success_box is None:
            self.log_error("讨伐结果未出现成功标志")
            return False

        self.log_info("讨伐成功")
        return True

    # --------------------------------------------------------
    # 5.2 营救流程（ZHANGPENG）
    # --------------------------------------------------------

    def _handle_zhangpeng_event(self) -> bool:
        """
        营救流程：点击即可完成。
        处理区域即 handlearea 的 bbox，直接点击。
        """
        box = self._safe_box(WORLD_EVENT_OBJECT_HANDLEAREA)
        if not box:
            self.log_error("未找到营救点击区域 bbox")
            return False

        self.log_info("点击营救区域")
        if not self.click(box):
            return False
        self._sleep(1.0)
        return True

    # --------------------------------------------------------
    # 5.3 挑战流程（BEAST）
    # --------------------------------------------------------

    def _handle_beast_event(self) -> bool:
        """
        挑战流程：
          1. 检查军队队列占用是否达到上限（beast_queue_limit）
             - 超限 → 跳过本事件（返回特殊信号，交由编排层决定
               是否等队列）
          2. bbox 直接点击 world_event_beast_kill 完成流程
        返回：
          True  —— 挑战已发起
          False —— 失败或跳过
        """
        box = self._safe_box(WORLD_EVENT_BEAST_KILL)
        if not box:
            self.log_error("未找到挑战击杀按钮 bbox")
            return False

        self.log_info("点击挑战击杀按钮")
        if not self.click(box):
            return False
        self._sleep(1.0)
        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。处理一个瞭望塔事件。
        返回 (InteractionResult, wait_seconds)。

        - 无事件 / 体力不足 / 步骤失败 → FAILED（队列停止补同类任务）
        - 挑战类事件因军队队列占满而暂不能发起 → RETRY（等待队列）
        """
        self.log_info("========== 开始处理瞭望塔事件 ==========")
        self._stamina_exhausted = False

        self.ensure_in_front()
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进入世界主界面 ----
        if not self._step(self._enter_world_map, "进入世界主界面"):
            self.last_error = "进入世界主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)
 

        # ---- 进入瞭望塔 ----
        if not self._step(self._enter_watch_tower, "进入瞭望塔"):
            self.last_error = "进入瞭望塔失败（无事件或标记未找到）"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 体力检查 ----
        if not self._step(self._has_enough_stamina, "体力检查"):
            self.last_error = "体力不足或读取失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)
        
        # ---- 搜索事件 ----
        if not self._step(self._search_event, "搜索事件"):
            self.last_error = "瞭望塔内无可处理事件"
            self.log_info(self.last_error)            
            return (InteractionResult.SUCCESS, 0)
        else:
            # ---- 前往事件对象 ----
            if not self._step(self._go_to_event_object, "前往事件对象"):
                self.last_error = "前往事件对象失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)

            # ---- 分派处理 ----
            if not self._step(self._dispatch_event, "处理事件"):
                self.last_error = "事件处理失败"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)

            self.log_info("========== 瞭望塔事件处理完成 ==========")
            return self.run_interaction()

        

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
        """
        独立运行：循环处理瞭望塔事件，直到：
          - 瞭望塔内没有可处理的事件
          - 体力低于 12
          - 某个步骤重试后仍然失败
        """
        self.log_info("========== 瞭望塔事件（独立模式） ==========")

        processed = 0


        while True:
            result, wait_seconds = self.run_interaction()

            if result == InteractionResult.FAILED:
                if self.last_error == "瞭望塔内无可处理事件":
                    self.log_info(
                        f"事件处理完毕，共处理 {processed} 个事件"
                    )
                    return True
                if self._stamina_exhausted:
                    self.log_info(
                        f"体力耗尽，共处理 {processed} 个事件"
                    )
                    return True
                self.log_error(f"瞭望塔事件失败: {self.last_error}")
                return False

            if result == InteractionResult.RETRY:
                self.log_info(
                    f"暂不可执行，{wait_seconds:.1f}s 后重试"
                )
                if wait_seconds > 0:
                    self._sleep(wait_seconds)
                continue

            processed += 1
            self.log_info(f"已处理 {processed} 个瞭望塔事件")

            # 回到主界面再进入下一轮（run_interaction 自带回主界面）
            if wait_seconds > 0:
                self._sleep(wait_seconds)
