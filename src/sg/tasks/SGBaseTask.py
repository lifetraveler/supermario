import time
import types # 导入这个模块

from src.scheduler.task_status import InteractionResult, TaskStatus
from src.sg.scene.scene_detector import SceneDetector
from src.sg.helper.recovery import RecoveryAbort  # 按实际模块路径调整
from src.tasks.MyBaseTask import MyBaseTask

class TaskRestartRequested(Exception):
    """
    任务需要从头重新执行。

    =========================================================
    与普通失败的区别
    =========================================================
    - 普通失败（RETRY / FAILED）：
        本次交互无法完成，按重试策略等待后再试，或直接判定失败。
        失败的语义是"这次没做成"，但现场本身仍然可信。

    - 本异常：
        当前任务的执行环境已经被破坏（例如恢复过程中按 ESC
        误触发了「退出游戏」弹窗），恢复层已经做了力所能及的
        善后（补按 ESC 关闭弹窗），但本次任务的上下文已不可信，
        应丢弃本次执行、重新从头跑这个任务。

    =========================================================
    触发链
    =========================================================
    RecoveryHelper 在 light_recover / full_recover 中按 ESC 后
    检测到退出游戏弹窗 → 补按一次 ESC → 抛 RecoveryAbort。

    SGBaseTask 的等待工具（_wait_element / _wait_scene）捕获
    RecoveryAbort，转换成 TaskRestartRequested 抛给上层。

    这样 RecoveryHelper 不需要认识调度协议，TaskQueue 也不需要
    认识 RecoveryAbort，两边通过本异常解耦。

    =========================================================
    TaskQueue 侧需要处理
    =========================================================
    调用 run_interaction() 时 try/except TaskRestartRequested：
      - 该任务支持重跑 → 状态重置为 PENDING 重新入队；
      - 该任务不支持重跑 → 标记 FAILED，直接推进到下一个任务。
    无论哪种情况都不应 stop 整个队列。
    """


class SGBaseTask(MyBaseTask):
    """
    奔奔王国自动化任务基类。

    =========================================================
    这个类提供什么？
    =========================================================
    1. SceneDetector：识别当前场景。
    2. 队列领域字段：task_id / status / estimated_finish_time 等，
       由 TaskQueue 调度时使用。
    3. 协议方法：run_interaction() / check_completed()，
       子类实现后者，由 TaskQueue 调用。
    4. 通用等待工具：
       _sleep / _find / _wait_element / _wait_and_click / _wait_scene。
       所有领域对象（Rally / MonsterSearcher 等）通过 task 引用复用。

    =========================================================
    等待工具的三个共性
    =========================================================
    - 轮询 + 超时：以 default_interval 为间隔，反复查找，直到
      default_timeout 超时。不用固定 sleep，因为页面跳转耗时不定。
    - 可选轻恢复：如果 Task 注入了 self.recovery，
      则等待期间每隔 recover_interval 秒自动调用
      recovery.light_recover()，防止被未知弹窗卡住。
    - 松耦合：没注入 recovery 的 Task（如 SGSceneTestTask）
      行为与原来完全一致，不受影响。

    =========================================================
    等待层与恢复层的异常契约
    =========================================================
    RecoveryHelper 在恢复过程中如果遇到"界面已经不可信"的状态
    （当前唯一场景：ESC 误触发退出游戏弹窗），会抛 RecoveryAbort。
    等待工具捕获它并转成 TaskRestartRequested，交由 TaskQueue
    决定重跑还是跳过本任务。

    这样做的好处：
      - RecoveryHelper 不知道调度协议（不需要 import 调度相关类）
      - TaskQueue 不需要知道恢复层的细节（只认 TaskRestartRequested）
      - 等待工具是两者之间唯一的翻译层

    =========================================================
    子类可覆写的参数
    =========================================================
    - default_timeout / default_interval / after_click_wait
    - recover_interval（如果要启用等待层恢复）
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "SG Base Task"
        self.description = "Base task for SG game automation."
        self.scene_detector = SceneDetector(self)

        # ====================================================
        # 队列领域字段（由 TaskQueue 调度时使用）
        # ====================================================
        self.task_id = None                    # 队列内唯一 id
        self.status = TaskStatus.PENDING       # 任务状态
        self.estimated_finish_time = None      # 预计完成时间戳
        self.next_retry_time = 0.0             # 下次可重试时间戳
        self.finished_at = None                # 实际完成时间戳
        self.last_error = None                 # 最后一次失败原因


        # ====================================================
        # 通用等待参数
        # ====================================================

        # 等待单个元素出现的默认超时（秒）。
        # 点击后页面跳转一般在 1~3 秒内完成，8 秒足够宽松，
        # 同时不会让失败场景等太久。
        self.default_timeout = 8.0

        # 轮询间隔（秒）。
        # 每次 find_one 都要截图 + 特征匹配，间隔太短会浪费 CPU。
        # 0.3 秒是准确性和开销之间的折中。
        self.default_interval = 0.3

        # 点击之后统一等待的秒数。
        # 避免动作刚发出去，帧还没更新，下一步 find_one 在旧帧上误判。
        # 只做很短的等待，真正的"等待完成"交给后续的 _wait_* 处理。
        self.after_click_wait = 0.3

        # 计划开始时间戳。>0 且 now >= 该值时，SCHEDULED 才可激活。
        # 由 TaskQueue 在任务进 IN_PROGRESS 时自动赋值。
        self.scheduled_start_time = 0.0

        # ====================================================
        # 触发调度字段
        # ====================================================
        # 最早可开始交互的时间戳（硬约束）：
        #   - now < trigger_time  → 绝对不能执行
        #   - now >= trigger_time → 可以执行（参与排队）
        # 0 表示立即可以执行。
        # 由 TaskQueue 在建立后续任务时写入。
        self.trigger_time = 0.0

        # 下一个同类任务的触发延迟（秒）。
        # 本任务 SUCCESS 后，TaskQueue 会建立下一个同类任务，
        # 其 trigger_time = 本任务的完成时间 + next_trigger_delay。
        #   - 0      → 立即触发（巨兽占军队队列用）
        #   - 86400  → 24h 后触发（宝箱 / 宠物等每日任务用）
        # 任务类可在 __init__ 里定义默认值，
        # 也可由 TaskFactory 的 next_trigger_delay 覆盖。
        self.next_trigger_delay = 0.0
        
        # 核心修复：初始化为 SimpleNamespace，而不是 None 或 {}
        self.extra_config = types.SimpleNamespace()
    # ========================================================
    # 协议方法：由 TaskQueue 调用
    # ========================================================

    def run_interaction(self):
        """
        只做交互，不做等待。

        返回 (InteractionResult, wait_seconds)：
          - SUCCESS: 交互完成，wait_seconds = 预计等待秒数
          - RETRY:   暂时不能做，wait_seconds = 重试等待秒数
          - FAILED:  真失败

        可能抛 TaskRestartRequested：
          当等待层遇到"恢复过程中误触发退出游戏弹窗"这类
          现场不可信的情况，会向上抛出本异常，由 TaskQueue
          决定重跑还是跳过当前任务。

        由 TaskQueue 调度时调用，子类必须重写。
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 必须实现 run_interaction()"
        )

    def check_completed(self) -> bool:
        """
        预计时间到了之后，是否真正完成。

        默认直接返回 True（相信时间估算）。
        需要更精确时，子类可以重写，用特征识别判断
        "行军是否真的结束"之类的状态。

        由 TaskQueue 在 estimated_finish_time 到期后调用。
        """
        return True

    # ========================================================
    # 通用工具：sleep
    # ========================================================

    def _sleep(self, seconds: float):
        """
        等待指定秒数。

        优先使用基类的 self.sleep（可以被 exit_event 中断，
        用户在 GUI 里点停止时能立即退出）。
        如果基类没有 self.sleep，则退回 Python 标准库 time.sleep。
        """
        if seconds <= 0:
            return

        sleep_func = getattr(self, "sleep", None)
        if callable(sleep_func):
            sleep_func(seconds)
        else:
            time.sleep(seconds)

    # ========================================================
    # 通用工具：查找
    # ========================================================

    def _find(self, element, threshold=0.8,box=None):
        """
        按 SceneElement 的 resource_id 查找一个 box。

        与 find_one 的区别：
          - find_one 接受 feature_name 字符串
          - _find 接受 SceneElement 对象，直接取 resource_id，
            调用方不用每次都写 element.resource_id
        """
        return self.find_one(
            feature_name=element.resource_id,
            threshold=threshold,
            box=box,
        )

    # ========================================================
    # 等待层共用：恢复 + 异常转换
    # ========================================================

    def _light_recover_or_restart(self, context: str):
        """
        调用一次轻恢复，并把恢复层的"界面不可信"信号转成调度协议。

        参数 context 只用于日志，说明这次恢复是在等什么
        （例如 "等待元素: BUTTON_XXX" 或 "等待场景: WORLD_MAP"）。

        调用约定：
          - 只有在 Task 注入了 self.recovery 时才应调用本方法；
            调用方需自行完成 recovery / recover_interval 的空判断。
          - RecoveryAbort 会被转成 TaskRestartRequested 抛出，
            一路传到 TaskQueue。

        为什么把转换逻辑抽出来：
          _wait_element 和 _wait_scene 的处理完全一样，
          抽出来避免两处重复写 try/except。
        """
        recovery = getattr(self, "recovery", None)
        if recovery is None:
            # 松耦合保护：没注入 recovery 的 Task 调用到这里是空操作。
            # 理论上调用方已经判过，这里再兜一层，防止漏判。
            return

        try:
            recovery.light_recover()
        except RecoveryAbort as e:
            # RecoveryHelper 已经补按 ESC 关闭了退出游戏弹窗，
            # 但本次任务的上下文已不可信，交给 TaskQueue 重新调度。
            self.log_info(
                f"[{context}] 恢复触发中断: {e}，"
                f"抛出 TaskRestartRequested 交由 TaskQueue 处理"
            )
            raise TaskRestartRequested(str(e)) from e

    # ========================================================
    # 等待元素
    # ========================================================
    def _wait_element(
        self,
        element,
        timeout=None,
        interval=None,
        threshold=0.8,
        with_recovery=True,
        box=None,
    ):
        """
        循环等待某个 SceneElement 出现。

        适用场景：
          - 点击某按钮后，下一个界面的元素还没渲染出来
          - 页面有动画，元素出现时间不确定
        不适用：
          - 等"场景整体切换"用 _wait_scene
          - 等"某个文本"用 task.wait_ocr 等框架方法

        参数：
          element        要等的 SceneElement；
                         也支持传入 list/tuple，任一命中即返回。
                         - 多个候选共享同一个 timeout 总时限
                         - 每一轮轮询依次尝试所有候选，
                           谁先出现先返回谁（并行轮询）
          timeout        超时秒数，默认 self.default_timeout
          interval       轮询间隔，默认 self.default_interval
          threshold      匹配阈值
          with_recovery  是否在等待期间启用轻恢复
          box            可选的预置 Box（透传给 _find）

        额外能力：
          如果 Task 注入了 self.recovery 且设置了 self.recover_interval，
          则等待期间每隔 recover_interval 秒调用一次
          recovery.light_recover()。
          如果轻恢复过程中 ESC 误触发了退出游戏弹窗，
          RecoveryHelper 会补按一次 ESC 并抛 RecoveryAbort，
          本方法捕获后转成 TaskRestartRequested 向上抛，
          由 TaskQueue 决定重跑还是跳过当前任务。

        返回：
          找到的 Box（成功）或 None（超时 / 无候选）

        可能抛：
          TaskRestartRequested —— 恢复过程中界面已不可信
        """
        # ---- 1. 归一化：单个 or 多个候选 ----
        # 保持对旧调用的完全兼容：单元素会被包成 [element]
        if isinstance(element, (list, tuple)):
            elements = list(element)
        else:
            elements = [element]

        # 空候选没有意义，直接失败，避免空转整个 timeout
        if not elements:
            self.log_info("未提供任何候选元素，等待直接失败")
            return None

        # ---- 2. 解析超时/间隔默认值 ----
        if timeout is None:
            timeout = self.default_timeout
        if interval is None:
            interval = self.default_interval

        deadline = time.time() + timeout
        last_light_recover = time.time()

        # 日志用的候选名列表（对非规范元素做 getattr 兜底）
        names = [getattr(e, "name", str(e)) for e in elements]

        self.log_info(
            f"等待元素出现: {names}, 超时={timeout}s"
        )

        # ---- 3. 轮询：每轮依次尝试所有候选 ----
        while time.time() < deadline:
            for elem in elements:
                box_found = self._find(
                    elem, threshold=threshold, box=box
                )
                if box_found is not None:
                    self.log_info(f"元素已出现: {elem.name}")
                    return box_found

            self._sleep(interval)

            # ---- 等待层轻恢复：只有 Task 注入了 recovery 才启用 ----
            # 用 getattr 松耦合，没注入的简单 Task 完全不受影响。
            recovery = getattr(self, "recovery", None)
            recover_interval = getattr(self, "recover_interval", 0)
            if (
                with_recovery
                and recovery is not None
                and recover_interval > 0
                and time.time() - last_light_recover >= recover_interval
            ):
                last_light_recover = time.time()
                self.log_info(
                    f"[{names}] 等待中，尝试轻恢复"
                )
                self._light_recover_or_restart(
                    context=f"等待元素 {names}"
                )

        self.log_info(f"等待元素超时: {names}")
        return None
      
    # ========================================================
    # 等待并点击
    # ========================================================

    def _wait_and_click(
        self,
        element,
        name=None,
        timeout=None,
        interval=None,
        threshold=0.8,
        after_click_wait=None,
        with_recovery=True,
        box=None,
    ) -> bool:
        """
        _wait_element 的语法糖 + 点击。

        流程：
          1. 调用 _wait_element 等元素出现
          2. 出现后调用 self.click 点击
          3. 点击后短暂等待 after_click_wait，让下一步的帧更新

        参数里 name 是点击动作的日志名，默认用 element.name。
        after_click_wait 默认用 self.after_click_wait。

        返回：
          True  —— 找到并点击成功
          False —— 未找到，或 click 返回 False

        可能抛：
          TaskRestartRequested —— 从 _wait_element 透传上来。
          本方法不捕获，交给 TaskQueue 处理。
        """
        box = self._wait_element(
            element,
            timeout=timeout,
            interval=interval,
            threshold=threshold,
            with_recovery=with_recovery,
            box=box
        )

        if box is None:
            self.log_info(f"未找到元素，无法点击: {element.name}")
            return False

        click_name = name or element.name
        self.log_info(f"点击元素: {click_name}")

        success = self.click(
            box,
            name=click_name,
        )

        if success:
            wait_seconds = (
                self.after_click_wait
                if after_click_wait is None
                else after_click_wait
            )
            if wait_seconds > 0:
                self._sleep(wait_seconds)

        return success

    # ========================================================
    # 等待场景
    # ========================================================

    def _wait_scene(self, scene_type, timeout=10.0, interval=0.5) -> bool:
        """
        等待场景切换完成。

        与 _wait_element 的区别：
          - _wait_element：等某个 UI 元素出现，用于页面内的跳转
          - _wait_scene  ：等 scene_detector 判定当前场景变为目标类型，
                           用于"点击进入某个大场景"这类切换

        典型用法：
          点击 BUTTON_GOTO_WORLDMAP 之后，调用本方法等场景真正
          切到 SceneType.WORLD_MAP，确认切换完成再走下一步。
          直接判断场景比去猜某个 UI 元素更可靠。

        参数：
          scene_type  目标 SceneType
          timeout     超时秒数（场景切换可能涉及加载，一般 10s 足够）
          interval    轮询间隔（场景切换较慢，0.5s 即可，减少无谓开销）

        额外能力：
          同 _wait_element，如果 Task 注入了 self.recovery，
          等待期间每隔 recover_interval 秒自动轻恢复。
          轻恢复过程中 ESC 误触发退出游戏弹窗时，同样会抛
          TaskRestartRequested 交由 TaskQueue 处理。

        返回：
          True  —— 已到达目标场景
          False —— 超时仍未到达

        可能抛：
          TaskRestartRequested —— 恢复过程中界面已不可信
        """
        deadline = time.time() + timeout
        last_light_recover = time.time()

        self.log_info(
            f"等待场景: {scene_type.value}, 超时={timeout}s"
        )

        while time.time() < deadline:
            scene = self.scene_detector.detect()
            if scene.type == scene_type:
                self.log_info(f"场景已到达: {scene_type.value}")
                return True

            self._sleep(interval)

            recovery = getattr(self, "recovery", None)
            recover_interval = getattr(self, "recover_interval", 0)
            if (
                recovery is not None
                and recover_interval > 0
                and time.time() - last_light_recover >= recover_interval
            ):
                last_light_recover = time.time()
                self.log_info(
                    f"[等待场景 {scene_type.value}] 尝试轻恢复"
                )
                self._light_recover_or_restart(
                    context=f"等待场景 {scene_type.value}"
                )

        scene = self.scene_detector.detect()
        self.log_info(
            f"等待场景超时，当前场景: {scene.type.value}"
        )
        return False