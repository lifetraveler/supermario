import time

from src.sg.tasks.SGBaseTask import SGBaseTask

from src.sg.scene.elements import (
    BUTTON_CLOSE_1,
    BUTTON_CLOSE_2,
    BUTTON_CLOSE_3,
    BUTTON_GOTO_WORLDMAP,
    BUTTON_SEARCH_RESOURCES,
    GIANT_BEAST,
    BUTTON_RALLY_GIANT_BEAST,
    TEAM_HUNTING,
    BUTTON_HUNT_EXPEDITION,
    BUTTON_RESOURCESEARCH,
    BUTTON_STARTRALLY,
)

from src.sg.scene.scene_type import SceneType


class HuntMonsterTask(SGBaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Hunt Giant Beast"
        self.description = "Search and rally giant beast."

        self.default_timeout = 8.0
        self.default_interval = 0.3
        self.after_click_wait = 0.3

        # ====================================================
        # 循环配置
        # ====================================================
        # 执行次数，<=0 表示不限制次数
        self.max_runs = 1

        # 体力下限，<=0 表示不检查体力
        self.min_stamina = 0

        # 每轮结束后的等待时间
        self.loop_interval = 1.0

        # ====================================================
        # 恢复配置
        # ====================================================
        # 每个业务步骤失败时，最多尝试 N 次"恢复 + 重试"
        self.max_recover_attempts = 2

        # 每次恢复动作后等待的秒数，让界面稳定
        self.recover_wait = 0.8

        # 在等待元素期间，每隔 N 秒尝试一次"轻恢复"
        # <=0 表示关闭等待层的恢复
        self.recover_interval = 3.0

        # 恢复时按 ESC 的冷却，避免连续 ESC 把正常界面也关掉
        self.esc_cooldown = 2.0

        # 通用确认按钮文本（中英双语）
        # 用于识别"确认 / 确定 / 继续"类按钮
        self.confirm_texts = [
            "Confirm", "OK", "Yes",
            "确定", "确认", "是", "继续",
        ]

        # 恢复时点击的空白区域（相对坐标）
        # 默认点屏幕上方中间，通常是空白区
        self.blank_click_x = 0.5
        self.blank_click_y = 0.15

        # 内部状态：上次轻恢复的时间戳
        self._last_light_recover = 0.0

        # 内部状态：上次按 ESC 的时间戳
        self._last_esc_time = 0.0

    # ========================================================
    # 基础操作
    # ========================================================

    def _sleep(self, seconds: float):
        """
        优先使用基类的 sleep。
        如果当前基类没有 self.sleep，则退回 Python 标准库 time.sleep。
        """
        sleep_func = getattr(self, "sleep", None)
        if callable(sleep_func):
            sleep_func(seconds)
        else:
            time.sleep(seconds)

    def _find(self, element, threshold=0.8):
        return self.find_one(
            feature_name=element.resource_id,
            threshold=threshold,
        )

    def _wait_element(
        self,
        element,
        timeout=None,
        interval=None,
        threshold=0.8,
        with_recovery=True,
    ):
        """
        循环等待元素出现。
        点击后页面有动画时，不要只 find 一次，而是等它出现。

        额外能力：
          - 每 recover_interval 秒尝试一次"轻恢复"，防止被弹窗卡住
        """
        if timeout is None:
            timeout = self.default_timeout
        if interval is None:
            interval = self.default_interval

        deadline = time.time() + timeout
        last_light_recover = time.time()

        self.log_info(f"等待元素出现: {element.name}, 超时={timeout}s")

        while time.time() < deadline:
            box = self._find(element, threshold=threshold)
            if box is not None:
                self.log_info(f"元素已出现: {element.name}")
                return box

            self._sleep(interval)

            # 等待层的轻恢复：只做安全动作
            if (
                with_recovery
                and self.recover_interval > 0
                and time.time() - last_light_recover >= self.recover_interval
            ):
                last_light_recover = time.time()
                self.log_info(
                    f"[{element.name}] 等待中，尝试轻恢复"
                )
                self._light_recover()

        self.log_info(f"等待元素超时: {element.name}")
        return None

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
        点击前先等待元素出现，出现后再点击。
        点击后只做很短的等待，后续步骤继续用 _wait_and_click 等下一个元素。
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

    def _wait_scene(self, scene_type, timeout=10.0, interval=0.5) -> bool:
        """
        等待场景切换完成。
        同样带轻恢复：切换期间被弹窗挡住也能自动处理。
        """
        deadline = time.time() + timeout
        last_light_recover = time.time()

        self.log_info(f"等待场景: {scene_type.value}, 超时={timeout}s")

        while time.time() < deadline:
            scene = self.scene_detector.detect()
            if scene.type == scene_type:
                self.log_info(f"场景已到达: {scene_type.value}")
                return True

            self._sleep(interval)

            if (
                self.recover_interval > 0
                and time.time() - last_light_recover >= self.recover_interval
            ):
                last_light_recover = time.time()
                self.log_info(
                    f"[等待场景 {scene_type.value}] 尝试轻恢复"
                )
                self._light_recover()

        scene = self.scene_detector.detect()
        self.log_info(f"等待场景超时，当前场景: {scene.type.value}")
        return False

    # ========================================================
    # 异常恢复
    # ========================================================

    def _light_recover(self) -> bool:
        """
        轻恢复：只做安全动作。
          - 关已知弹窗
          - 按 ESC（带冷却）
        不做点击确认、点空白等可能有副作用的动作。
        返回 True 表示至少做了一个恢复动作。
        """
        did_anything = False

        # 1. 关已知弹窗
        if self._try_close_known_popups():
            did_anything = True

        # 2. 按 ESC（带冷却，避免连续 ESC）
        if self._try_press_esc():
            did_anything = True

        return did_anything

    def recover_from_blocker(self) -> bool:
        """
        重恢复：用于步骤失败后的兜底。
        顺序：
          1. 关已知弹窗
          2. 按 ESC（带冷却）
          3. 点通用确认按钮（OCR 匹配中英确认文本）
          4. 点屏幕空白区域
        返回 True 表示至少做了一个恢复动作。
        """
        if self._try_close_known_popups():
            return True

        if self._try_press_esc():
            return True

        if self._try_click_confirm():
            return True

        if self._try_click_blank():
            return True

        return False

    def _try_close_known_popups(self) -> bool:
        """尝试关闭已知的活动弹窗按钮。"""
        for button in (
            BUTTON_CLOSE_1,
            BUTTON_CLOSE_2,
            BUTTON_CLOSE_3,
        ):
            box = self._find(button)
            if box is None:
                continue

            self.log_info(f"恢复: 关闭弹窗 {button.name}")
            self.click(box, name=f"恢复-关闭弹窗 {button.name}")
            self._sleep(self.recover_wait)
            return True

        return False

    def _try_press_esc(self) -> bool:
        """尝试按 ESC（带冷却）。"""
        now = time.time()
        if now - self._last_esc_time < self.esc_cooldown:
            return False

        try:
            self.send_key("esc")
        except Exception as e:
            self.log_info(f"恢复: 按 ESC 失败 {e}")
            return False

        self._last_esc_time = now
        self.log_info("恢复: 按下 ESC")
        self._sleep(self.recover_wait)
        return True

    def _try_click_confirm(self) -> bool:
        """
        尝试用 OCR 找通用确认按钮并点击。
        找不到返回 False，不会强行点击。
        """
        try:
            results = self.ocr(match=self.confirm_texts)
        except Exception as e:
            self.log_info(f"恢复: OCR 确认按钮失败 {e}")
            return False

        if not results:
            return False

        box = results[0]
        self.log_info(f"恢复: 点击确认按钮 {box.name}")
        self.click(box, name=f"恢复-点击确认 {box.name}")
        self._sleep(self.recover_wait)
        return True

    def _try_click_blank(self) -> bool:
        """
        尝试点击屏幕相对空白处。
        使用相对坐标，默认点屏幕上方中间。
        """
        self.log_info(
            f"恢复: 点击空白区域 "
            f"({self.blank_click_x}, {self.blank_click_y})"
        )
        try:
            self.click_relative(
                self.blank_click_x,
                self.blank_click_y,
                name="恢复-点击空白",
            )
        except Exception as e:
            self.log_info(f"恢复: 点击空白失败 {e}")
            return False

        self._sleep(self.recover_wait)
        return True

    # ========================================================
    # 步骤包装：失败 → 恢复 → 重试
    # ========================================================

    def _run_step_with_recovery(self, step_func, step_name) -> bool:
        """
        执行一个业务步骤，失败时尝试恢复并重试。
        step_func 是无参可调用，返回 True 表示成功。

        关键点：
          - 恢复成功后从当前步骤重试，不会回到任务开头
          - 最多重试 max_recover_attempts 次
          - 恢复动作返回 False 时不再重试
        """
        for attempt in range(self.max_recover_attempts + 1):
            if attempt > 0:
                self.log_info(
                    f"步骤 [{step_name}] 第 {attempt} 次重试前恢复"
                )
                if not self.recover_from_blocker():
                    self.log_info(
                        f"步骤 [{step_name}] 无法恢复，停止重试"
                    )
                    return False
                self._sleep(self.recover_wait)

            if step_func():
                return True

            self.log_info(f"步骤 [{step_name}] 失败")

        return False

    # ========================================================
    # 体力检查钩子
    # ========================================================

    def get_current_stamina(self):
        """
        返回当前体力值；返回 None 表示未实现 / 不检查体力。

        TODO: 用户根据实际 OCR / 特征读取后填写。
        """
        return None

    def _check_stamina_enough(self) -> bool:
        if self.min_stamina <= 0:
            return True

        stamina = self.get_current_stamina()
        if stamina is None:
            self.log_info("体力检测未实现，跳过体力退出条件")
            return True

        self.log_info(f"当前体力: {stamina}，下限: {self.min_stamina}")
        return stamina >= self.min_stamina

    # ========================================================
    # 循环控制
    # ========================================================

    def _check_loop_continue(self, finished_count: int) -> bool:
        if self.max_runs > 0 and finished_count >= self.max_runs:
            self.log_info(f"已达到执行次数上限: {self.max_runs}")
            return False

        if not self._check_stamina_enough():
            self.log_info(f"体力低于下限 {self.min_stamina}，停止循环")
            return False

        return True

    # ========================================================
    # 1. 关闭活动弹窗
    # ========================================================

    def close_popups(self):
        self.log_info("开始检查活动弹窗")

        while True:
            closed = False

            for button in (
                BUTTON_CLOSE_1,
                BUTTON_CLOSE_2,
                BUTTON_CLOSE_3,
            ):
                box = self._find(button)

                if box is None:
                    continue

                self.log_info(f"发现活动弹窗: {button.name}")

                if self.click(
                    box,
                    name=f"关闭弹窗: {button.name}",
                ):
                    closed = True
                    self._sleep(0.5)
                    break

            if not closed:
                break

        self.log_info("活动弹窗检查完成")

    # ========================================================
    # 2. 进入荒野
    # ========================================================

    def enter_wilderness(self) -> bool:
        scene = self.scene_detector.detect()
        self.log_info(f"当前场景: {scene.type.value}")

        if scene.type == SceneType.WORLD_MAP:
            self.log_info("当前已经在荒野")
            return True

        if scene.type == SceneType.CITY:
            self.log_info("当前在主城，准备进入荒野")

            if not self._wait_and_click(
                BUTTON_GOTO_WORLDMAP,
                name="进入荒野",
                timeout=6.0,
            ):
                return False

            return self._wait_scene(
                SceneType.WORLD_MAP,
                timeout=10.0,
                interval=0.5,
            )

        self.log_error(f"无法进入荒野，未知场景: {scene.type.value}")
        return False

    # ========================================================
    # 3. 搜索资源
    # ========================================================

    def search_resources(self) -> bool:
        self.log_info("准备搜索资源")
        return self._wait_and_click(
            BUTTON_SEARCH_RESOURCES,
            name="搜索资源",
            timeout=8.0,
        )

    # ========================================================
    # 4. 选择巨兽
    # ========================================================

    def select_giant_beast(self) -> bool:
        self.log_info("准备选择巨兽")
        return self._wait_and_click(
            GIANT_BEAST,
            name="选择巨兽",
            timeout=8.0,
            threshold=0.5,
        )

    # ========================================================
    # 4.1 搜索确认位置
    # ========================================================

    def select_giant_beast_position(self) -> bool:
        self.log_info("搜索位置")
        return self._wait_and_click(
            BUTTON_RESOURCESEARCH,
            name="搜索位置",
            timeout=8.0,
            threshold=0.5,
        )

    # ========================================================
    # 5. 点击集结巨兽
    # ========================================================

    def rally_giant_beast(self) -> bool:
        self.log_info("准备发起巨兽集结")
        return self._wait_and_click(
            BUTTON_RALLY_GIANT_BEAST,
            name="集结巨兽",
            timeout=8.0,
        )

    # ========================================================
    # 5.1 集结巨兽确定
    # ========================================================

    def button_startrally(self) -> bool:
        self.log_info("集结确定")
        return self._wait_and_click(
            BUTTON_STARTRALLY,
            name="集结确定",
            timeout=8.0,
        )

    # ========================================================
    # 6. 选择队伍
    # ========================================================

    def select_team(self) -> bool:
        self.log_info("准备选择集结队伍")
        return self._wait_and_click(
            TEAM_HUNTING,
            name="选择 野外打怪集结 队伍",
            timeout=8.0,
        )

    # ========================================================
    # 7. 发起出征
    # ========================================================

    def hunt_expedition(self) -> bool:
        self.log_info("准备发起巨兽远征")
        return self._wait_and_click(
            BUTTON_HUNT_EXPEDITION,
            name="发起巨兽远征",
            timeout=8.0,
        )

    # ========================================================
    # 单次执行
    # ========================================================

    def _run_once(self) -> bool:
        """
        一次完整流程。
        每个步骤都套上恢复重试：失败会先尝试恢复并重试当前步骤，
        不会回到开头，也不会因为一次超时就整轮失败。
        """
        self.log_info("========== 开始集结巨兽 ==========")

        self.ensure_in_front()

        # close_popups 本身是循环+可重复调用的，失败不重试也没关系
        self.close_popups()

        if not self._run_step_with_recovery(
            self.enter_wilderness, "进入荒野"
        ):
            self.log_error("进入荒野失败")
            return False

        if not self._run_step_with_recovery(
            self.search_resources, "搜索资源"
        ):
            self.log_error("搜索资源失败")
            return False

        if not self._run_step_with_recovery(
            self.select_giant_beast, "选择巨兽"
        ):
            self.log_error("选择巨兽失败")
            return False

        if not self._run_step_with_recovery(
            self.select_giant_beast_position, "选择巨兽位置"
        ):
            self.log_error("选择巨兽位置失败")
            return False

        if not self._run_step_with_recovery(
            self.rally_giant_beast, "进入集结页面"
        ):
            self.log_error("进入巨兽集结页面失败")
            return False

        if not self._run_step_with_recovery(
            self.button_startrally, "集结确认"
        ):
            self.log_error("集结确认失败")
            return False

        if not self._run_step_with_recovery(
            self.select_team, "选择集结队伍"
        ):
            self.log_error("选择集结队伍失败")
            return False

        if not self._run_step_with_recovery(
            self.hunt_expedition, "发起远征"
        ):
            self.log_error("发起巨兽远征失败")
            return False

        self.log_info("========== 巨兽集结完成 ==========")
        return True

    # ========================================================
    # Main
    # ========================================================

    def run(self):
        self.log_info("========== 开始集结巨兽循环 ==========")

        finished_count = 0

        while True:
            if not self._check_loop_continue(finished_count):
                break

            finished_count += 1
            self.log_info(f"---------- 第 {finished_count} 次执行 ----------")

            success = self._run_once()

            if not success:
                self.log_error(f"第 {finished_count} 次执行失败，停止循环")
                return False

            self.log_info(f"第 {finished_count} 次执行完成")

            if not self._check_loop_continue(finished_count):
                break

            self._sleep(self.loop_interval)

        self.log_info(
            f"========== 集结巨兽循环结束，共执行 {finished_count} 次 =========="
        )
        return True