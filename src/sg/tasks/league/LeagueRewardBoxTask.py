"""
联盟宝箱领取任务（战利品宝箱 + 盟友赠礼两个 tag）。

流程：
  1. 确保前台，清理弹窗
  2. 若不在主界面（城市/世界地图）→ 按 ESC 回到主界面
  3. 点击 GLOBAL_TAG_LEAGUE 进入联盟界面
  4. 点击 LEAGUE_REWARD_BOX_ENTRY 进入联盟宝箱页面
  5. 处理当前默认选中的"战利品宝箱" tag：
     - find_one(LEAGUE_REWARD_BOX_BUTTON_GET_ITEM) 检测是否有
       绿色"领取"条目按钮（"已领取"文本不会命中）
     - 有 → 点击 LEAGUE_REWARD_BOX_BUTTON_GET 一键领取，
       等待"获得奖励"tip 出现后点击任意位置退出
     - 无 → 跳过领取
  6. 点击盟友赠礼 tag 切换（get_box_by_name 静态 bbox，
     因为 tag 特征模板只匹配未选中态，选中后特征会失配）
  7. 盟友赠礼 tag 同 5 的检测 + 领取 + 退出逻辑
  8. 按 ESC 逐层退回主界面

调度参数：
  - 不占军队队列（requires_march_queue=False）
  - next_trigger_delay=86400：每日领取一次
  - 不可并行：宝箱页面同一时刻只能打开一个

关键实现约束（实测结论，勿改回特征匹配）：
  - tag 切换必须用 get_box_by_name 静态 bbox：tag 特征模板
    截取自未选中/选中各一态，在另一态上最高仅 0.32 分，
    特征匹配无法完成切换。
  - 可领取检测用 find_one(get_item)：绿色按钮模板来自
    可领取态；"已领取"为灰色文本，天然不命中。
  - 一键领取按钮只在绿色启用态才匹配（盟友赠礼的模板是
    灰色禁用态，永远命中不了绿色态，故盟友 tab 用
    get_item 检测决定是否点一键按钮的静态 bbox）。
"""

import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_TAG_LEAGUE,
    LEAGUE_REWARD_BOX_ENTRY,
    LEAGUE_REWARD_BOX_TAG,
    LEAGUE_FRIEND_REWARD_BOX_TAG,
    LEAGUE_REWARD_BOX_BUTTON_GET,
    LEAGUE_FRIEND_REWARD_BOX_BUTTON_GET,
    LEAGUE_REWARD_BOX_BUTTON_GET_ITEM,
    GLOBAL_MARK_REWARD_GETED_QUIT_TIP,
)
from src.sg.scene.scene_type import SceneType


class LeagueRewardBoxTask(SGBaseTask):
    """
    联盟宝箱领取。

    =========================================================
    职责边界：
      - 只做业务编排：回主界面 → 进联盟 → 进宝箱页 →
        两个 tag 逐个"检测可领取 → 一键领取 → 退出 tip" → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 无内部状态、无复用方，不抽独立领域对象
    =========================================================
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "League Reward Box"
        self.description = "Claim league reward boxes (loot & friend gifts)."

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
    # 步骤 2：进入联盟 → 宝箱页面
    # ========================================================

    def _open_reward_box_page(self) -> bool:
        """
        主界面 → 联盟界面 → 联盟宝箱页面。
        """
        # 联盟入口（全局标签）
        if not self._wait_and_click(GLOBAL_TAG_LEAGUE, timeout=6.0):
            return False
        self._sleep(0.5)

        # 宝箱入口横幅
        if not self._wait_and_click(
            LEAGUE_REWARD_BOX_ENTRY,
            timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1),
        ):
            return False
        self._sleep(1.0)
        return True

    # ========================================================
    # 步骤 3：当前 tag 下检测可领取并一键领取
    # ========================================================

    def _has_claimable_item(self) -> bool:
        """
        检测当前 tag 下是否存在绿色"领取"条目按钮。

        find_one(league_reward_box_button_get_item)：
          - 模板为绿色可领取按钮，命中即有可领取条目
          - "已领取"为灰色文本，天然不命中
        """
        box = self.find_one(
            feature_name=LEAGUE_REWARD_BOX_BUTTON_GET_ITEM.resource_id,
            box=self.box_of_screen(0, 0, 1, 1),
        )
        return box is not None

    def _claim_current_tag(self) -> bool:
        """
        当前 tag 的领取逻辑：
          无可领取条目 → 直接成功（跳过）
          有 → 点一键领取 → 等"获得奖励"tip → 点击退出
        """
        if not self._has_claimable_item():
            self.log_info("当前 tag 无可领取条目，跳过")
            return True

        self.log_info("检测到可领取条目，点击一键领取")
        if not self._wait_and_click(
            [LEAGUE_REWARD_BOX_BUTTON_GET, LEAGUE_FRIEND_REWARD_BOX_BUTTON_GET],
            timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1),
        ):
            self.log_info("一键领取按钮未匹配，视为无可领取")
            return True

        self._sleep(1.5)

        # 等待"获得奖励"提示出现后点击任意位置退出
        quit_box = self._wait_element(
            GLOBAL_MARK_REWARD_GETED_QUIT_TIP,
            timeout=6.0,
            box=self.box_of_screen(0, 0, 1, 1),
            with_recovery=False,
        )
        if quit_box is None:
            self.log_info("未出现获得奖励提示，跳过退出点击")
            return True

        self.log_info("点击获得奖励提示退出")
        self.click(quit_box)
        self._sleep(1.0)
        return True

    # ========================================================
    # 步骤 4：切换到盟友赠礼 tag
    # ========================================================

    def _switch_to_friend_tag(self) -> bool:
        """
        切换到"盟友赠礼" tag。

        必须用 get_box_by_name 静态 bbox 直接点击：
          tag 特征模板只匹配截图时的状态（战利品 tag 模板=未选中棕底，
          盟友 tag 模板=选中白底），在另一态上最高 0.32 分，
          _wait_and_click 特征匹配无法完成切换。
        """
        box = self.get_box_by_name(LEAGUE_FRIEND_REWARD_BOX_TAG.resource_id)
        if box is None:
            self.log_error("未找到盟友赠礼 tag 的 bbox")
            return False

        self.log_info("点击盟友赠礼 tag 切换")
        self.click_box(box)
        self._sleep(1.0)
        return True

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
        self.log_info("========== 开始联盟宝箱领取 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        if not self._step(self._ensure_main_scene, "回到主界面"):
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 打开宝箱页面 ----
        if not self._step(self._open_reward_box_page, "打开宝箱页面"):
            self.last_error = "打开宝箱页面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 战利品宝箱（默认选中 tag）----
        if not self._step(self._claim_current_tag, "领取战利品宝箱"):
            # 领取失败不阻塞：可能只是特征波动，继续处理盟友赠礼
            self.log_info("战利品宝箱领取失败，继续盟友赠礼")

        # ---- 切换到盟友赠礼 tag ----
        if not self._step(self._switch_to_friend_tag, "切换盟友赠礼"):
            self.log_info("切换盟友赠礼失败，跳过该 tag 领取")
        else:
            # ---- 盟友赠礼领取 ----
            if not self._step(self._claim_current_tag, "领取盟友赠礼"):
                self.log_info("盟友赠礼领取失败，不影响整体结果")

        # ---- 退回主界面（失败不影响结果，只记日志）----
        if not self._step(self._back_to_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响领取结果")

        self.log_info("========== 联盟宝箱领取完成 ==========")
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
        self.log_info("========== 联盟宝箱领取（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"联盟宝箱领取失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 联盟宝箱领取完成 ==========")
        return True
