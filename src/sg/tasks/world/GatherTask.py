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


class GatherTask(SGBaseTask):

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

    def _wait_element(self, element, timeout=None, interval=None, threshold=0.8):
        """
        循环等待元素出现。
        点击后页面有动画时，不要只 find 一次，而是等它出现。
        """
        if timeout is None:
            timeout = self.default_timeout
        if interval is None:
            interval = self.default_interval

        deadline = time.time() + timeout

        self.log_info(f"等待元素出现: {element.name}, 超时={timeout}s")

        while time.time() < deadline:
            box = self._find(element, threshold=threshold)
            if box is not None:
                self.log_info(f"元素已出现: {element.name}")
                return box

            self._sleep(interval)

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
        """
        deadline = time.time() + timeout

        self.log_info(f"等待场景: {scene_type.value}, 超时={timeout}s")

        while time.time() < deadline:
            scene = self.scene_detector.detect()
            if scene.type == scene_type:
                self.log_info(f"场景已到达: {scene_type.value}")
                return True

            self._sleep(interval)

        scene = self.scene_detector.detect()
        self.log_info(f"等待场景超时，当前场景: {scene.type.value}")
        return False

    # ========================================================
    # 体力检查钩子
    # ========================================================

    def get_current_stamina(self):
        """
        返回当前体力值；返回 None 表示未实现 / 不检查体力。

        当前项目还没有确认体力读取 API，所以这里先返回 None。
        等你确认体力怎么读之后，只改这个方法即可。

        例如以后可能是：
        - OCR 读取体力文本；
        - 或通过某个 feature 判断体力是否足够。
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
        # 次数条件
        if self.max_runs > 0 and finished_count >= self.max_runs:
            self.log_info(f"已达到执行次数上限: {self.max_runs}")
            return False

        # 体力条件
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
        self.log_info("========== 开始集结巨兽 ==========")

        self.ensure_in_front()

        self.close_popups()

        if not self.enter_wilderness():
            self.log_error("进入荒野失败")
            return False

        if not self.search_resources():
            self.log_error("搜索资源失败")
            return False

        if not self.select_giant_beast():
            self.log_error("选择巨兽失败")
            return False

        if not self.select_giant_beast_position():
            self.log_error("选择巨兽位置失败")
            return False

        if not self.rally_giant_beast():
            self.log_error("进入巨兽集结页面失败")
            return False

        if not self.button_startrally():
            self.log_error("集结确认失败")
            return False

        if not self.select_team():
            self.log_error("选择集结队伍失败")
            return False

        if not self.hunt_expedition():
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

            # 每轮结束再检查一次，达到次数或体力不足就退出
            if not self._check_loop_continue(finished_count):
                break

            self._sleep(self.loop_interval)

        self.log_info(f"========== 集结巨兽循环结束，共执行 {finished_count} 次 ==========")
        return True