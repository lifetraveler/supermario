import time

from src.scheduler.task_status import InteractionResult, TaskStatus
from src.sg.scene.scene_detector import SceneDetector
from src.tasks.MyBaseTask import MyBaseTask


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

    def _find(self, element, threshold=0.8):
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
        )

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
          element        要等的 SceneElement
          timeout        超时秒数，默认 self.default_timeout
          interval       轮询间隔，默认 self.default_interval
          threshold      匹配阈值
          with_recovery  是否在等待期间启用轻恢复

        额外能力：
          如果 Task 注入了 self.recovery 且设置了 self.recover_interval，
          则等待期间每隔 recover_interval 秒调用一次
          recovery.light_recover()。
          这样可以自动清掉"等待期间突然弹出的未知弹窗"，避免一直空等。

        返回：
          找到的 Box（成功）或 None（超时）
        """
        if timeout is None:
            timeout = self.default_timeout
        if interval is None:
            interval = self.default_interval

        deadline = time.time() + timeout
        last_light_recover = time.time()

        self.log_info(
            f"等待元素出现: {element.name}, 超时={timeout}s"
        )

        while time.time() < deadline:
            box = self._find(element, threshold=threshold)
            if box is not None:
                self.log_info(f"元素已出现: {element.name}")
                return box

            self._sleep(interval)

            # 等待层轻恢复：只有 Task 注入了 recovery 才启用。
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
                    f"[{element.name}] 等待中，尝试轻恢复"
                )
                recovery.light_recover()

        self.log_info(f"等待元素超时: {element.name}")
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
        """
        box = self._wait_element(
            element,
            timeout=timeout,
            interval=interval,
            threshold=threshold,
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

        返回：
          True  —— 已到达目标场景
          False —— 超时仍未到达
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
                recovery.light_recover()

        scene = self.scene_detector.detect()
        self.log_info(
            f"等待场景超时，当前场景: {scene.type.value}"
        )
        return False