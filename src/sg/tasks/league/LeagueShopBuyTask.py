"""
联盟商店购买任务（League Shop Buy）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面（城市 / 世界地图，否则 ESC 退回）
  2. 点击 GLOBAL_TAG_LEAGUE 进入联盟界面
  3. 点击 LEAGUE_SHOP_ENTRY 进入联盟商店界面
  4. 确定本次要处理的页签列表：
     - 系统日期为周一 → [今日, 本周]
     - 其余星期       → [今日]
  5. 对每个页签执行「买空」循环：
     - 全屏灰度检索 LEAGUE_SHOP_SALE_BADGE（联盟商店-3-3折 角标）
     - 匹配到 → 点击其所在商品卡片 → 弹出「确定购买」弹窗 →
       点击 LEAGUE_SHOP_BUY_CONFIRM（联盟商店-4-购买确认，数量默认 1）
     - 弹窗关闭后回到商品列表，重新检索同一区域
     - 直到该页签下再也匹配不到 3 折角标为止
       （每次购买剩余数递减、售完后角标消失，循环天然买空）
  6. 防御分支：点击角标后若超时未弹出确认弹窗（商品已售完 / 角标残留），
     记录该角标位置不再点击，继续检索其余角标，防止死循环
  7. 当前页签买空后，若还有下一个页签（仅周一），点页签区域切换后重复买空
  8. 全部买空后返回主界面，任务成功

失败语义（需求原文）：
  - 从头到尾一次都没匹配到 3 折角标（所有页签均无）→ FAILED，不重试
  - 商店顶部「下次刷新」倒计时无 coco 特征，不做 OCR，
    下次触发时间按配置固定间隔（next_trigger_delay=86400 每日一次）

页签判断（模板语义已核实，灰度匹配）：
  - LEAGUE_SHOP_TAG_TODAY_SELECTED（今日-选中态，浅底深字）命中 → 在今日页
  - LEAGUE_SHOP_TAG_WEEK_UNSELECTED（本周-未选中态，深底白字）命中 → 在今日页
  - 两者都不命中 → 在本周页
  - 页签点击热区 coco 无特征，用 get_box_by_name 取「今日」选中态标注 bbox
    直接点击（切到本周）；「本周」未选中态 bbox 直接点击（切回今日）

调度语义：
  - 不占军队队列（requires_march_queue=False）
  - next_trigger_delay=86400：每日一次
  - 不可并行：同一时刻只能有一个商店界面（count=1，无 max_active）
"""

import datetime
import time
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup
from src.sg.scene.elements import (
    GLOBAL_TAG_LEAGUE,
    LEAGUE_SHOP_ENTRY,
    LEAGUE_SHOP_BUY_CONFIRM,
    LEAGUE_SHOP_TAG_TODAY_SELECTED,
    LEAGUE_SHOP_TAG_WEEK_UNSELECTED,
    LEAGUE_SHOP_SALE_BADGE,
    LEAGUE_SHOP_SALE_BADGE_60,
    LEAGUE_SHOP_SALE_BADGE_FULL,
)

from src.sg.scene.scene_type import SceneType


class LeagueShopBuyTask(SGBaseTask):
    """
    联盟商店购买。

    =========================================================
    职责边界：
      - 只做业务编排：回主界面 → 进联盟 → 进商店 →
        按星期选页签 → 逐页签买空 → 回主界面
      - 买空循环（角标检索 / 点击 / 确认 / 位置去重）在本类内实现，
        无内部状态复用方，不抽独立领域对象
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 全屏灰度匹配 3 折角标的单轮检索超时（秒）。
    # 角标要么在当前帧，要么已售完消失：短超时即「本屏无货」判定。
    badge_search_timeout = 2.0

    # 确认购买弹窗出现等待（秒）
    confirm_popup_timeout = 3.0

    # 点击角标后弹窗渲染缓冲（秒）
    popup_settle = 1.0

    # 购买确认点击后回到商品列表的缓冲（秒）
    buy_settle = 1.5

    # 页签切换后的界面缓冲（秒）
    tab_switch_settle = 1.0

    # 折扣档位 → 角标特征（min_discount 参数选项，多选全买）
    DISCOUNT_BADGE_MAP = {
        "3折": LEAGUE_SHOP_SALE_BADGE,
        "6折": LEAGUE_SHOP_SALE_BADGE_60,
        "原价": LEAGUE_SHOP_SALE_BADGE_FULL,
    }

    # 同一位置角标的防御记忆上限（防止异常情况下列表无限增长）
    skipped_positions_max = 32

    # 单页签买空循环的购买次数上限（防御异常死循环）
    buy_rounds_max = 60

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "League Shop Buy"
        self.description = (
            "League shop buy: open league shop, pick tabs by weekday "
            "(Mon: today+week, else today), buy out every 30%-off item "
            "via the confirm popup, then return to main scene."
        )

        # ====================================================
        # 个性化参数容器
        # ====================================================
        self.extra_config = types.SimpleNamespace()

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
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 最小折扣多选：勾选的档位全部购买；默认仅 3 折
        self.min_discount = ["3折"]

        # ====================================================
        # 内部状态
        # ====================================================
        # 本次任务是否至少匹配到过一个所选档位角标
        # （需求：所有页签都无所选折扣 → FAILED 不重试）
        self._saw_any_badge = False

        # 弹窗超时角标的位置去重集合（本次任务内有效）
        self._skipped_positions = set()

    # ========================================================
    # 通用助手
    # ========================================================

    def _full_screen(self):
        return self.box_of_screen(0, 0, 1, 1)

    # ========================================================
    # 步骤 1：进入联盟商店
    # ========================================================

    def _open_league_shop(self) -> bool:
        """
        主界面 → 联盟界面 → 联盟商店界面。
        """
        # 联盟入口（全局标签）
        if not self._wait_and_click(GLOBAL_TAG_LEAGUE, timeout=2.0):
            return False
        self._sleep(0.5)

        # 联盟商店入口
        if not self._wait_and_click(LEAGUE_SHOP_ENTRY, timeout=2.0):
            return False
        self._sleep(0.5)
        return True

    # ========================================================
    # 步骤 2：页签状态判断与切换
    # ========================================================

    def _on_today_tab(self) -> bool:
        """
        灰度匹配判断当前是否停留在「今日」页签。

        模板语义（已按 coco 模板裁剪核实）：
          - 今日-选中态（浅底深字）命中 → 在今日页
          - 本周-未选中态（深底白字）命中 → 今日被选中（在今日页）
          - 两者都不命中 → 在本周页
        """
        full = self._full_screen()
        today_selected = self._find(
            LEAGUE_SHOP_TAG_TODAY_SELECTED,
            threshold=0.8, box=full, use_gray_scale=True,
        )
        if today_selected is not None:
            return True

        week_unselected = self._find(
            LEAGUE_SHOP_TAG_WEEK_UNSELECTED,
            threshold=0.8, box=full, use_gray_scale=True,
        )
        return week_unselected is not None

    def _switch_to_week_tab(self) -> bool:
        """
        切换到「本周」页签。

        coco 无页签点击热区特征（需求原文允许）：用「今日」选中态的
        标注 bbox 直接点击（页签点击热区，非状态匹配）。
        切换成功的验证：今日选中态特征消失。
        """
        today_tag_box = self._safe_box(LEAGUE_SHOP_TAG_TODAY_SELECTED)
        if today_tag_box is None:
            self.log_error("未找到「今日」页签 bbox，无法切换到本周")
            return False

        self.click(today_tag_box)
        self._sleep(self.tab_switch_settle)

        # 切换成功验证：今日选中态消失
        if self._find(
            LEAGUE_SHOP_TAG_TODAY_SELECTED,
            threshold=0.8, box=self._full_screen(), use_gray_scale=True,
        ) is None:
            self.log_info("已切换到本周页签")
            return True

        self.log_warning("点击今日页签后选中态未消失，切换失败")
        return False

    def _switch_to_today_tab(self) -> bool:
        """
        从「本周」切回「今日」页签。

        同样用「本周」未选中态的标注 bbox 直接点击；切换成功验证：
        本周未选中态特征重新出现（今日被选中时本周回到未选中态）。
        """
        week_tag_box = self._safe_box(LEAGUE_SHOP_TAG_WEEK_UNSELECTED)
        if week_tag_box is None:
            self.log_error("未找到「本周」页签 bbox，无法切回今日")
            return False

        self.click(week_tag_box)
        self._sleep(self.tab_switch_settle)

        if self._find(
            LEAGUE_SHOP_TAG_WEEK_UNSELECTED,
            threshold=0.8, box=self._full_screen(), use_gray_scale=True,
        ) is not None:
            self.log_info("已切换回今日页签")
            return True

        self.log_warning("点击本周页签后未选中态未出现，切换失败")
        return False

    # ========================================================
    # 步骤 3：单页签买空循环
    # ========================================================

    def _active_badge_elements(self) -> list:
        """按 min_discount 参数解析本次要检索的角标元素列表（去重保序）。"""
        elements = []
        for discount in (self.min_discount or []):
            elem = self.DISCOUNT_BADGE_MAP.get(discount)
            if elem is not None and elem not in elements:
                elements.append(elem)
        return elements

    def _find_badge(self):
        """
        全屏灰度检索所选折扣档位的角标，返回第一个未被跳过的匹配。

        多档位（min_discount 勾选多个）时逐个特征检索同一帧，
        先到先得；每个特征用 find_feature（返回全部匹配）而非
        _wait_element（只回置信度最高的单个）：同屏多角标时，
        最高置信度的可能恰是已跳过位置，必须能继续选中其余角标，
        否则会误判买空。

        轮询上限 badge_search_timeout：角标要么在当前帧，要么已售完消失。
        """
        elements = self._active_badge_elements()
        if not elements:
            self.log_error("min_discount 未勾选任何有效折扣档位")
            return None

        full = self._full_screen()
        deadline = time.time() + self.badge_search_timeout
        while True:
            for elem in elements:
                badges = self.find_feature(
                    elem.resource_id,
                    threshold=0.8,
                    box=full,
                    # use_gray_scale=True, #这里不需要灰度，因为买完以后，灰度还可以匹配到
                )
                for badge in badges:
                    if self._pos_key(badge) not in self._skipped_positions:
                        self.log_info(f"匹配到 {elem.name} 角标")
                        return badge
            if time.time() >= deadline:
                return None
            self._sleep(0.5)

    def _pos_key(self, box) -> tuple:
        """角标位置去重键：以中心点近似格子坐标（40px 网格量化）。"""
        cx, cy = box.center()
        return (int(cx // 40), int(cy // 40))

    def _buyout_current_tab(self) -> bool:
        """
        在当前页签执行「买空」循环。

        每轮：
          1. 全屏灰度检索所选折扣档位角标（timeout=2.0，不重试恢复）
          2. 匹配到 → 取第一个未被跳过的角标 → 点击其位置
          3. 等「确定购买」弹窗 → 点购买确认 → 回商品列表
          4. 弹窗超时 → 该位置记入跳过集合（售完 / 角标残留），不再点击
          5. 匹配不到角标 → 本页签买空结束

        返回：
          True  —— 循环正常结束（买空 / 全部角标被跳过 / 达次数上限）
          False —— 购买确认点击失败等真失败
        """
        skipped_positions = self._skipped_positions
        buy_rounds = 0

        while buy_rounds < self.buy_rounds_max:
            badge = self._find_badge()

            if badge is None:
                self.log_info("当前页签未再匹配到所选折扣角标，买空结束")
                return True

            self._saw_any_badge = True

            key = self._pos_key(badge)
            buy_rounds += 1
            self.log_info(f"第 {buy_rounds} 次购买：点击折扣角标位置 {key}")
            self.click(badge)
            self._sleep(self.popup_settle)

            # 等「确定购买」弹窗并点击购买确认
            confirm_box = self._wait_element(
                LEAGUE_SHOP_BUY_CONFIRM,
                timeout=self.confirm_popup_timeout,
                interval=0.5,
                threshold=0.8,
                with_recovery=False,
                box=self._full_screen(),
                use_gray_scale=True,
            )
            if confirm_box is None:
                # 防御分支：角标点击后未弹出确认弹窗
                # （商品已售完 / 角标残留）→ 记录位置不再点击，
                # _find_badge 会自动跳过该位置继续检索其余角标
                self.log_warning(
                    f"点击角标后未弹出确认购买弹窗，位置 {key} 记入跳过集合"
                )
                skipped_positions.add(key)
                if len(skipped_positions) > self.skipped_positions_max:
                    self.log_error("跳过位置数异常，终止本页签买空")
                    return True
                continue

            if not self.click(confirm_box):
                self.log_error("购买确认按钮点击失败")
                return False
            self.info_set("Buy Count", buy_rounds)
            self._sleep(self.buy_settle)

        self.log_warning(f"达到单页签购买次数上限 {self.buy_rounds_max}，终止")
        return True

    # ========================================================
    # 步骤 4：全部页签处理
    # ========================================================

    def _buyout_all_tabs(self) -> bool:
        """
        按星期确定页签列表并逐页签买空。

        - 周一 → [今日, 本周]；其余 → [今日]
        - 需求：整个任务从头到尾一次都没匹配到所选折扣 → FAILED 不重试
        """
        weekday = datetime.date.today().weekday()  # Monday=0
        tabs = ["today", "week"] if weekday == 0 else ["today"]
        self.log_info(
            f"今天星期{weekday + 1}，处理页签: "
            f"{['今日'] + (['本周'] if weekday == 0 else [])}"
        )

        for index, tab in enumerate(tabs):
            if tab == "week":
                # 从今日切到本周
                if not self._switch_to_week_tab():
                    self.log_error("切换到本周页签失败")
                    return False

            self.log_info(f"开始处理页签 [{tab}]")
            if not self._buyout_current_tab():
                return False

            _ = index

        return True

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)。
        下次触发时间按配置固定间隔（next_trigger_delay=86400），
        本任务不回传动态等待。
        """
        self.log_info("========== 开始联盟商店购买 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        success, _scene = self._ensure_main_scene()
        if not success:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        self._saw_any_badge = False

        # ---- 进联盟 → 商店 ----
        if not self._step(self._open_league_shop, "进入联盟商店"):
            self.last_error = "进入联盟商店失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 确认已在今日页签 ----
        # 商店打开后默认停留在今日页签；若不在（异常残留状态），
        # 用页签 bbox 拉回今日，保证页签列表从今日开始处理。
        if not self._on_today_tab():
            self.log_info("当前不在今日页签，尝试切回今日")
            if not self._switch_to_today_tab():
                self.last_error = "无法切回今日页签"
                self.log_error(self.last_error)
                return (InteractionResult.FAILED, 0)

        # ---- 逐页签买空 ----
        if not self._step(self._buyout_all_tabs, "联盟商店买空"):
            self.last_error = "联盟商店买空流程失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 需求：从头到尾一次都没匹配到所选折扣 → FAILED 不重试 ----
        if not self._saw_any_badge:
            self.last_error = "两个页签均未匹配到所选折扣角标，无可购商品"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        self.info_set("Buy Count", self.info_get("Buy Count", 0))

        # ---- 退回主界面（失败不影响购买结果，只记日志）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响购买结果")

        self.log_info("========== 联盟商店购买完成 ==========")
        return (InteractionResult.SUCCESS, 0)

    # ========================================================
    # 兼容旧接口
    # ========================================================

    def check_completed(self) -> bool:
        return True

    def _run_once(self):
        result, wait_seconds = self.run_interaction()
        success = (result == InteractionResult.SUCCESS)
        return (success, wait_seconds)

    # ========================================================
    # 独立运行入口
    # ========================================================

    def run(self):
        self.log_info("========== 联盟商店购买（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"联盟商店购买失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info("========== 联盟商店购买完成 ==========")
        return True
