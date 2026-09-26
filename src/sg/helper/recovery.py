import time


class RecoveryAbort(Exception):
    """
    恢复过程中界面状态已不可信，需要上层中断当前任务。

    当前唯一触发场景：轻/重恢复按 ESC 误触发了「退出游戏」弹窗。
    RecoveryHelper 已做力所能及的善后（补按一次 ESC 关闭弹窗），
    但本次任务的上下文已不可信，交给调用方决定：
      - 重新从头跑这个任务
      - 还是判定失败推进到下一个任务

    RecoveryHelper 不关心调度，只负责发出这个信号。
    """


class RecoveryHelper:
    """
    界面异常恢复助手。

    =========================================================
    为什么需要它？
    =========================================================
    自动化任务执行时经常被"预期之外的界面"打断，例如：
      - 突然弹出的活动弹窗
      - 游戏公告 / 网络提示 / 引导弹窗
      - 停留在某个不认识的界面，导致下一步的特征永远识别不到
    这时如果什么都不做，任务会一直等到超时然后失败。
    RecoveryHelper 的作用就是：卡住时尝试安全动作，把界面拉回来。
    =========================================================
    两个级别的恢复
    =========================================================
    light_recover：轻恢复，用于等待期间。
        只做安全动作（关已知弹窗 + ESC），不做点击确认、点空白。
    full_recover：重恢复，用于步骤失败后的兜底。
        按顺序尝试：关弹窗 → ESC → 点通用确认按钮 → 点空白。

    =========================================================
    与 ActivityPopup 的关系
    =========================================================
    关弹窗这件事本身由 ActivityPopup 负责（element/overall/activity）。
    RecoveryHelper 只是复用它的 try_close_one()。
    构造时传入 popup；不传也能工作，只是没有关弹窗这一步。

    =========================================================
    调用方是谁？
    =========================================================
    1. SGBaseTask._wait_element / _wait_scene
         等待期间每隔 recover_interval 秒自动调一次 light_recover。
         注意：只有 Task 注入了 self.recovery 才生效，
         没注入的简单 Task（如 SGSceneTestTask）不受影响。

    2. HuntMonsterTask._step（或类似步骤包装方法）
         当某个业务步骤重试时，调 full_recover。
         恢复成功就重试当前步骤，不回到任务开头。

    =========================================================
    依赖约定
    =========================================================
    RecoveryHelper 不继承任何框架类，通过 task 引用调用：
        task._find / task.click / task.click_relative /
        task.send_key / task.ocr / task._sleep / task.log_info
    =========================================================
    新增：退出游戏弹窗保护
    =========================================================
    light_recover / full_recover 都会按 ESC。ESC 有概率误触发
    「退出游戏」确认框，一旦出现必须立刻补按一次 ESC 关闭它，
    否则游戏可能被真正关闭，后续所有识别全部错位。

    处理流程：
      1. ESC 之后检测 quit_game_feature 特征
      2. 命中 → 等 quit_game_after_esc_wait 秒
      3. 补按一次 ESC（绕过 esc_cooldown）
      4. 抛 RecoveryAbort，让上层中断并重新调度本任务
    """

    def __init__(self, task, popup=None):
        self.task = task

        # 可选的 ActivityPopup 实例。
        # 如果提供，关弹窗动作会委托给它。
        # 不提供时，_try_close_known_popups 直接返回 False。
        self.popup = popup

        # ====================================================
        # 恢复参数
        # ====================================================

        # 每个恢复动作之后等待的秒数，让界面有时间稳定。
        # 太短：动作发出后界面还没更新，下一步判断会误判。
        # 太长：每次恢复都要多等，影响节奏。
        # 经验值 0.6 ~ 1.0 比较稳。
        self.recover_wait = 0.8

        # ESC 冷却秒数。
        # 连续 ESC 有可能把正常界面也关掉（比如误关了主界面），
        # 所以两次 ESC 之间至少间隔这么久。
        self.esc_cooldown = 2.0

        # 通用确认按钮的 OCR 匹配文本（中英双语）。
        # full_recover 时用 self.task.ocr(match=...) 找这类按钮并点击。
        # 只保留"确认 / 继续"类文本，不含"取消 / 关闭"，
        # 因为取消类按钮点下去通常会把流程带偏。
        self.confirm_texts = [
            "Confirm", "OK", "Yes",
            "确定", "确认", "是", "继续",
        ]

        # full_recover 最后一步：点击屏幕相对空白处。
        # 默认点屏幕上方中间 (0.5, 0.15)，通常是标题栏或空白区，
        # 点下去一般只会关掉当前弹层，不会触发任何业务按钮。
        # 如果游戏该位置有可交互元素，改成其它安全坐标。
        self.blank_click_x = 0.5
        self.blank_click_y = 0.15

        # 已知弹窗关闭元素列表。
        # 由 Task 在初始化时注入，例如：
        #   self.recovery.close_elements = [
        #       BUTTON_CLOSE_1, BUTTON_CLOSE_2, BUTTON_CLOSE_3,
        #   ]
        # 恢复时会按顺序 find_one，命中第一个就点。
        self.close_elements = []

        # ====================================================
        # 退出游戏弹窗保护
        # ====================================================
        # 退出游戏弹窗特征名。不同渠道/皮肤可能不同，做成字段便于覆盖。
        self.quit_game_feature = "global_quit_game"
        self.quit_game_threshold = 0.8

        # 命中弹窗后，补按 ESC 前的等待秒数。
        # 让弹窗动画稳定，避免连续 ESC 被吞掉。
        self.quit_game_after_esc_wait = 1.0

        # ====================================================
        # 内部状态
        # ====================================================

        # 上次按 ESC 的时间戳，用于 esc_cooldown 判断。
        # 只在本对象内部维护，外部不需要访问。
        self._last_esc_time = 0.0

    # ========================================================
    # 轻恢复：等待期间使用
    # ========================================================

    def light_recover(self) -> bool:
        """
        轻恢复：只做安全动作。

        做什么：
          1. 尝试关闭已知弹窗（close_elements）
          2. 尝试按 ESC（带 esc_cooldown 冷却）

        不做什么：
          - 不点通用确认按钮（可能会误点"确定购买"之类）
          - 不点空白区域（可能会触发无关交互）

        调用方：
          SGBaseTask._wait_element / _wait_scene，等待期间每隔
          recover_interval 秒调一次。

        返回：
          True  —— 至少做了一个恢复动作
          False —— 什么都没做（找不到弹窗 + ESC 还在冷却）
        """
        did = False

        # 关弹窗优先级最高：命中就是真弹窗，关了无副作用。
        if self._try_close_known_popups():
            did = True

        # ESC 次之：能关掉大多数浮层，但有冷却限制。
        if self._try_press_esc():
            did = True
            # ESC 后立刻检查是否误触发退出游戏弹窗。
            # 命中会补按 ESC 并抛 RecoveryAbort，不会走到下面 return。
            if self._detect_quit_game_popup():
                self._handle_quit_game_popup()

        return did

    # ========================================================
    # 重恢复：步骤失败时兜底
    # ========================================================

    def full_recover(self) -> bool:
        """
        重恢复：失败兜底，动作范围更大。

        可能抛 RecoveryAbort：ESC 误触发退出游戏弹窗时。
        按顺序尝试（命中一个就返回，不再尝试后面的）：
          1. 关已知弹窗
          2. 按 ESC
          3. OCR 匹配通用确认按钮并点击
          4. 点击屏幕空白区域

        调用方：
          Task 的步骤包装方法（如 HuntMonsterTask._step），
          当某个业务步骤失败、准备重试之前调用。

        返回：
          True  —— 至少做了一个恢复动作（调用方可以重试步骤）
          False —— 四个动作都没做成（调用方不应再重试）
        """
        if self._try_close_known_popups():
            return True

        if self._try_press_esc():
            if self._detect_quit_game_popup():
                self._handle_quit_game_popup()
            return True

        if self._try_click_confirm():
            return True

        if self._try_click_blank():
            return True

        return False

    # ========================================================
    # 具体恢复动作
    # ========================================================

    def _try_close_known_popups(self) -> bool:
        """
        关一个已知弹窗。
        委托给 self.popup.try_close_one()。
        没有注入 popup 时直接返回 False。
        """
        if self.popup is None:
            return False
        return self.popup.try_close_one()

    def _try_press_esc(self) -> bool:
        """
        按 ESC 关闭当前浮层。

        受 esc_cooldown 限制。实际的按键动作委托给 _press_esc_raw，
        后者也被"补按 ESC 关闭退出游戏弹窗"复用。
        返回：
          True  —— 真的按下去了
          False —— 还在冷却中，或按键发送异常
        """
        now = time.time()
        if now - self._last_esc_time < self.esc_cooldown:
            return False
        return self._press_esc_raw()

    def _press_esc_raw(self) -> bool:
        """
        不带冷却地按一次 ESC，并更新 _last_esc_time。

        用于两处：
          - _try_press_esc 通过冷却检查后调用
          - 命中退出游戏弹窗后的补按（需要绕过冷却立即发出）
        """
        try:
            self.task.send_key("esc")
        except Exception as e:
            self.task.log_info(f"恢复: 按 ESC 失败 {e}")
            return False

        self._last_esc_time = time.time()
        self.task.log_info("恢复: 按下 ESC")
        self.task._sleep(self.recover_wait)
        return True

    def _try_click_confirm(self) -> bool:
        """
        用 OCR 查找通用确认按钮并点击。

        匹配文本来自 self.confirm_texts（中英双语）。
        找不到就返回 False，不会强行点击，避免误触。

        返回：
          True  —— 找到并点击了确认按钮
          False —— 未找到，或 OCR 异常
        """
        try:
            results = self.task.ocr(match=self.confirm_texts)
        except Exception as e:
            self.task.log_info(f"恢复: OCR 确认按钮失败 {e}")
            return False

        if not results:
            return False

        box = results[0]
        self.task.log_info(f"恢复: 点击确认按钮 {box.name}")
        self.task.click(box, name=f"恢复-点击确认 {box.name}")
        self.task._sleep(self.recover_wait)
        return True

    def _try_click_blank(self) -> bool:
        """
        点击屏幕相对空白处（blank_click_x, blank_click_y）。

        作为 full_recover 的最后手段：通常只会关闭浮层，
        不会触发任何业务按钮。如果游戏该位置有可交互元素，
        请把坐标改成更安全的位置。

        返回：
          True  —— 点击成功
          False —— 点击异常
        """
        self.task.log_info(
            f"恢复: 点击空白区域 "
            f"({self.blank_click_x}, {self.blank_click_y})"
        )
        try:
            self.task.click_relative(
                self.blank_click_x,
                self.blank_click_y,
                name="恢复-点击空白",
            )
        except Exception as e:
            self.task.log_info(f"恢复: 点击空白失败 {e}")
            return False

        self.task._sleep(self.recover_wait)
        return True

    # ========================================================
    # 退出游戏弹窗：检测 / 补按 ESC / 中断
    # ========================================================

    def _detect_quit_game_popup(self) -> bool:
        """
        检测当前画面是否出现「退出游戏」弹窗。

        检测本身失败（例如 find_one 抛异常）时返回 False，
        不把"检测动作"变成新的故障点。
        """
        try:
            box = self.task.find_one(
                feature_name=self.quit_game_feature,
                threshold=self.quit_game_threshold,
            )
        except Exception as e:
            self.task.log_info(f"恢复: 检测退出游戏弹窗失败 {e}")
            return False
        return box is not None

    def _handle_quit_game_popup(self):
        """
        处理误触发的退出游戏弹窗。

        流程：
          1. 等 quit_game_after_esc_wait 秒（弹窗动画稳定）
          2. 再按一次 ESC（关闭弹窗）
          3. 抛 RecoveryAbort，通知上层中断本任务

        不做"ESC 是否真的关掉弹窗"的二次验证：
        弹窗是否又弹回来属于上层重新调度的自然检测点，
        避免在这里陷入无限重试。
        """
        self.task.log_info(
            f"恢复: 检测到退出游戏弹窗 ({self.quit_game_feature})，"
            f"{self.quit_game_after_esc_wait}s 后补按 ESC 并中断任务"
        )
        self.task._sleep(self.quit_game_after_esc_wait)
        self._press_esc_raw()
        raise RecoveryAbort(
            "恢复过程中误触发退出游戏弹窗，需要重新执行本任务"
        )