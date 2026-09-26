"""
联盟捐献任务（联盟科技捐献，奖励联盟币）。

流程：
  1. 确保前台，清理弹窗
  2. 若不在主界面（城市/世界地图）→ 按 ESC 回到主界面
  3. 点击全局标签 GLOBAL_TAG_LEAGUE 进入联盟界面
  4. 点击 LEAGUE_TECH_ENTRY 进入联盟科技页面
  5. 点击 LEAGUE_TECH_UPDATE 打开捐献交互页面
  6. 用 LEAGUE_TECH_UPDATE_REMAIN_TIMES 的 bbox 做 OCR，
     解析出 "剩余/上限" 两组数字，得到剩余捐献次数
  7. 记录 LEAGUE_TECH_UPDATE_DONATE 首次匹配到的 bbox 位置，
     按剩余次数直接在该位置点击（后续点击不再做特征匹配，
     避免前一次捐献弹出的 tip 挡住特征导致匹配失败）
  8. 捐完后重读剩余次数；为 0 → 回主界面结束；
     不为 0 → 继续点击捐献（防御 OCR 偏少的情况）
  9. 完成后返回主界面

调度参数：
  - 不占军队队列（requires_march_queue=False）
  - next_trigger_delay=9000：10 分钟恢复 1 次、上限 25 次，
    捐完后需 150 分钟才能恢复满
  - 不可并行：同一时刻只能有一个捐献界面
"""

import re
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_TAG_LEAGUE,
    LEAGUE_TECH_ENTRY,
    LEAGUE_TECH_UPDATE,
    LEAGUE_TECH_UPDATE_DONATE,
    LEAGUE_TECH_UPDATE_REMAIN_TIMES,
)
from src.sg.scene.scene_type import SceneType


class LeagueTechDonateTask(SGBaseTask):
    """
    联盟科技捐献。

    =========================================================
    职责边界：
      - 只做业务编排：回主界面 → 进联盟 → 进科技 → 捐献 → 回主界面
      - 次数解析（OCR）与点击循环在本类内实现，
        无内部状态、无复用方，不抽独立领域对象
      - 弹窗处理交给 ActivityPopup
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "League Tech Donate"
        self.description = "Donate to league tech for league coins."

        # ====================================================
        # 恢复配置
        # ====================================================
        self.max_recover_attempts = 2

        # 个性化参数容器
        self.extra_config = types.SimpleNamespace()

        # ====================================================
        # 领域对象
        # ====================================================
        self.popup = ActivityPopup(self)
        self.recovery = RecoveryHelper(self, popup=self.popup)

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
    # 步骤 2：进入联盟 → 联盟科技 → 捐献面板
    # ========================================================

    def _open_donate_panel(self) -> bool:
        """
        主界面 → 联盟界面 → 联盟科技页面 → 捐献交互页面。
        """
        # 联盟入口（全局标签）
        if not self._wait_and_click(
            GLOBAL_TAG_LEAGUE, name="联盟标签", timeout=6.0
        ):
            return False
        self._sleep(0.5)

        # 联盟科技入口
        if not self._wait_and_click(
            LEAGUE_TECH_ENTRY, name="联盟科技入口", timeout=6.0
        ):
            return False
        self._sleep(0.5)

        # 捐献图标（打开捐献交互页面）
        if not self._wait_and_click(
            LEAGUE_TECH_UPDATE, name="捐献图标", timeout=6.0
        ):
            return False
        self._sleep(0.5)
        return True

    # ========================================================
    # 步骤 3：OCR 读取剩余捐献次数
    # ========================================================

    def _read_remain_times(self):
        """
        按 resource_id 直接取"剩余次数"元素的 bbox（get_box_by_name，
        不做特征匹配），在 bbox 区域内 OCR，解析 "剩余/上限" 两组数字，
        返回剩余次数。

        返回：
          int  —— 解析成功
          None —— bbox 不存在 / OCR 无数字 / 解析失败
        """
        box = self.get_box_by_name(LEAGUE_TECH_UPDATE_REMAIN_TIMES.resource_id)
        if box is None:
            self.log_error("未找到剩余次数元素")
            return None

        # 只在特征 bbox 附近小范围 OCR，速度和准确率都更好
        results = self.ocr(box=box, log=True)
        if not results:
            self.log_error("剩余次数区域 OCR 无结果")
            return None

        text = "".join(r.name for r in results)
        self.log_info(f"剩余次数 OCR 原文: {text}")

        numbers = re.findall(r"\d+", text)
        if not numbers:
            self.log_error(f"OCR 未解析出数字: {text}")
            return None

        # 第一组是剩余次数，第二组是上限；只有一组时认为就是剩余
        remain = int(numbers[0])
        self.log_info(f"剩余捐献次数: {remain}")
        return remain

    # ========================================================
    # 步骤 4：捐献循环
    # ========================================================
    def _donate_all(self) -> bool:
        """
        按 OCR 得到的剩余次数点击捐献按钮。

        点击方式：捐献按钮按 resource_id 直接取 bbox（get_box_by_name，
        不做特征匹配），剩余几次就点几次。捐献后游戏会弹出 tip，
        特征重匹配可能被 tip 挡住而失败，因此全程不做特征匹配。

        点完后重读剩余次数：
          - 为 0 → 完成
          - 不为 0 → 递归再捐（防御 OCR 偏少导致没捐完）
          - 读不到 → 按已捐次数评估，读不到且一次都没捐过才算失败
        """
        remain = self._read_remain_times()
        if remain is None:
            return False

        if remain <= 0:
            self.log_info("剩余次数为 0，无需捐献")
            return True

        # 按 resource_id 直接取 bbox，不做特征匹配
        donate_box = self.get_box_by_name(
            LEAGUE_TECH_UPDATE_DONATE.resource_id
        )
        if donate_box is None:
            self.log_error("未找到捐献按钮")
            return False

        donated = 0
        while remain>donated:
            # 不做特征重匹配：按首次记录的 bbox 直接点击
            self.click_box(
                donate_box,
                after_sleep=1.0,
            )

            donated += 1
            self.info_set("捐献次数", donated)

            # 每次捐献后重读，次数为 0 即完成
        current = self._read_remain_times()
        if current is None:
            self.log_info("捐献后无法读取剩余次数，按已捐次数结束")
            return True
        if current <= 0:
            self.log_info(f"捐献完成，共捐 {donated} 次")
            return True
        self.log_info(f"还有剩余次数 {current}，递归继续捐献")
        return self._donate_all()


    # ========================================================
    # 步骤 5：退回主界面
    # ========================================================

    def _back_to_main_scene(self) -> bool:
        return self._ensure_main_scene()

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        """
        self.log_info("========== 开始联盟捐献 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 打开捐献面板 ----
        if not self._step(self._open_donate_panel, "打开捐献面板"):
            self.last_error = "打开捐献面板失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 捐献循环 ----
        if not self._step(self._donate_all, "捐献"):
            self.last_error = "捐献失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 退回主界面（失败不影响结果，只记日志）----
        if not self._step(self._back_to_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响捐献结果")

        self.log_info("========== 联盟捐献完成 ==========")
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
        self.log_info("========== 联盟捐献（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"联盟捐献失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 联盟捐献完成 ==========")
        return True
