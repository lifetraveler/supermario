# -*- coding: utf-8 -*-
"""
联盟总动员（League Mobilization）领域对象。

职责：
  - 从主界面进入联盟总动员界面（常规活动入口 → 页签滑动灰度查找）
  - 剩余可接次数 OCR（"可接次数 x/20"）
  - 购买次数（第一次免费 / 消耗钻石提交）
  - 专属任务卡槽：点击 → OCR 描述匹配配置目标 → 接受 / 刷新 / 记录刷新时间
  - 公共任务（阶段 A）：向上滑动，特征检索 + 奖励标志扫描，
    点开卡片 OCR 校验描述 → 接受 / 刷新（受开关控制）/ 关闭
  - 公共任务（阶段 B，仅刷新开启）：回滚到滚动区域上沿后逐行向下清扫，
    未接卡片按 刷新目标 特征决定 刷新 / 保留；滑动幅度按基准点行高

不负责：场景判定兜底、弹窗清理、调度协议、次数用尽后的等待决策。
"""

import re

from src.sg.scene.elements import (
    GLOBAL_ENTRY_COMMON_ACTIVITY,
    LEAGUE_MOB_ENTRY_TAG,
    LEAGUE_MOB_TIMES_OCR,
    LEAGUE_MOB_BUY_TIMES_ENTRY,
    LEAGUE_MOB_BUY_FIRST_FREE,
    LEAGUE_MOB_BUY_DIAMOND_PANEL,
    LEAGUE_MOB_DIAMOND_BUY_SUBMIT,
    LEAGUE_MOB_EXCLUSIVE_SLOT_1,
    LEAGUE_MOB_EXCLUSIVE_SLOT_2,
    LEAGUE_MOB_DESC_AREA,
    LEAGUE_MOB_ACCEPT_BUTTON,
    LEAGUE_MOB_REFRESH_BUTTON,
    LEAGUE_MOB_SCROLL_WINDOW,
    LEAGUE_MOB_PUBLIC_CLOSE,
    LEAGUE_MOB_PUBLIC_REFRESH,
    LEAGUE_MOB_PUBLIC_ACCEPT,
    LEAGUE_MOB_PUBLIC_DESC,
    LEAGUE_MOB_PUBLIC_BASE_POINT,
    LEAGUE_MOB_PUBLIC_OFFSET_AREA,
    LEAGUE_MOB_PUBLIC_REWARD_FLAG,
    LEAGUE_MOB_SCROLL_TOP_EDGE,
)

# 专属卡槽（顺序即处理顺序）
EXCLUSIVE_SLOTS = (LEAGUE_MOB_EXCLUSIVE_SLOT_1, LEAGUE_MOB_EXCLUSIVE_SLOT_2)

# ============================================================
# 目标定义：参数选项 → 描述窗口 OCR 包含匹配的关键词
# ============================================================
# OCR 文案为描述窗口去除换行/空白后的包含匹配关键词。
# 公共任务卡片图标特征（任务目标-训练/黄金/巨兽）在 GOAL_ELEMENTS 单独
# 维护，供阶段 B 不点开卡片即可预判。
MOBILITY_GOALS = {
    "训练12万": {"text": "训练"},
    "黄金150": {"text": "黄金"},
    "巨兽5": {"text": "巨兽"},
    "巨兽10": {"text": "巨兽"},
    "巨兽15": {"text": "巨兽"},
}


class Mobilization:
    """
    联盟总动员领域对象。

    对外接口：
      open_activity()            -> bool  主界面 → 联盟总动员界面
      read_remain_times()        -> int   OCR 可接次数，失败返回 -1
      purchase_times()           -> bool  购买 1 次（免费或钻石）
      run_exclusive_slot(slot, goals) -> (action, refresh_seconds)
      run_public_tasks(goals, can_refresh, scroll_step) -> str
      run_refresh_sweep(refresh_goals, scroll_step)     -> str
      read_refresh_time(text)    -> int|None  刷新倒计时文本 → 秒
    """

    def __init__(self, task):
        self.task = task

        # ---- 参数 ----
        # 页签滑动查找次数上限
        self.tag_scroll_max = 10
        # 页签每次滑动的距离比例（横向）
        self.tag_scroll_step = 0.2
        # 滑动后等待动画时间
        self.scroll_settle_time = 1.0
        # 剩余次数 OCR 失败时的重试次数
        self.times_ocr_retries = 3
        # 公共任务阶段 A 的最大滑动轮数
        self.public_scroll_max = 10
        # 回滚阶段的最大滑动次数
        self.rollback_scroll_max = 15

        # ---- 运行时状态 ----
        # 最近一次公共任务滑动的像素距离（日志/调试用）
        self.last_public_scroll_px = 0

    # ========================================================
    # 步骤 0：进入联盟总动员界面
    # ========================================================

    def open_activity(self) -> bool:
        """
        主界面 → 常规活动 → 页签滑动灰度查找联盟总动员标签 → 点击进入。

        页签从左向右依次排布，从第一屏起向左滑（内容左移）逐屏检索；
        找到点击后等待界面稳定。
        """
        if not self.task._wait_and_click(
            GLOBAL_ENTRY_COMMON_ACTIVITY, timeout=2.0
        ):
            self.task.log_error("未找到常规活动入口")
            return False
        self.task._sleep(1.0)

        for attempt in range(self.tag_scroll_max + 1):
            box = self.task._find(
                LEAGUE_MOB_ENTRY_TAG,
                use_gray_scale=True,
                box=self.task.box_of_screen(0, 0, 1, 1),
            )
            if box is not None:
                if attempt > 0:
                    self.task.log_info(
                        f"滑动 {attempt} 次后找到联盟总动员标签"
                    )
                if not self.task.click(box):
                    return False
                self.task._sleep(1.0)
                self.task.log_info("已进入联盟总动员界面")
                return True

            self.task.log_info(
                f"未找到联盟总动员标签，向左滑动 ({attempt + 1})"
            )
            self.task.swipe_relative(
                0.65, 0.5, 0.65 - self.tag_scroll_step, 0.5,
                duration=0.3, settle_time=self.scroll_settle_time,
            )

        self.task.log_error("滑动检索耗尽仍未找到联盟总动员标签")
        return False

    # ========================================================
    # 步骤 1：剩余可接次数
    # ========================================================

    def read_remain_times(self) -> int:
        """
        OCR "可接次数 x/20" 区域，解析剩余可接次数 x。

        返回值：
          >= 0  剩余可接次数
          -1    OCR 失败 / 解析失败
        """
        for _ in range(self.times_ocr_retries):
            box = self.task._safe_box(LEAGUE_MOB_TIMES_OCR)
            if box is not None:
                text = self.task._ocr_text(box) or ""
                compact = re.sub(r"\s+", "", text)
                self.task.log_info(f"剩余次数 OCR 原文: {text!r}")
                m = re.search(r"(\d+)\s*/\s*\d+", compact)
                if m:
                    times = int(m.group(1))
                    self.task.log_info(f"剩余可接次数: {times}")
                    return times
            self.task._sleep(0.5)
        self.task.log_error("剩余次数 OCR 解析失败")
        return -1

    # ========================================================
    # 步骤 2：购买次数
    # ========================================================

    def purchase_times(self) -> bool:
        """
        购买 1 次可接次数。

        分支：
          - 第一次免费面板：点免费按钮即成功
          - 消耗钻石面板：点提交按钮即成功（购买数与面板默认一致，
            按需求无需增减调节；增减/取消按钮已定义留作扩展）
        """
        if not self.task._wait_and_click(
            LEAGUE_MOB_BUY_TIMES_ENTRY, timeout=2.0
        ):
            self.task.log_error("未找到购买次数入口")
            return False
        self.task._sleep(1.0)

        box = self.task._wait_element(
            (LEAGUE_MOB_BUY_FIRST_FREE, LEAGUE_MOB_BUY_DIAMOND_PANEL),
            timeout=3.0,
        )
        if box is None:
            self.task.log_error("购买面板未弹出")
            return False

        if self.task._find(
            LEAGUE_MOB_BUY_FIRST_FREE,
            box=self.task.box_of_screen(0, 0, 1, 1),
        ):
            if not self.task._wait_and_click(
                LEAGUE_MOB_BUY_FIRST_FREE, timeout=2.0
            ):
                return False
            self.task._sleep(1.0)
            self.task.log_info("已购买次数（第一次免费）")
            return True

        if not self.task._wait_and_click(
            LEAGUE_MOB_DIAMOND_BUY_SUBMIT, timeout=2.0
        ):
            self.task.log_error("钻石购买面板未找到提交按钮")
            return False
        self.task._sleep(1.0)
        self.task.log_info("已购买次数（消耗钻石）")
        return True

    # ========================================================
    # 刷新时间解析
    # ========================================================

    def read_refresh_time(self, text):
        """
        把刷新倒计时 OCR 文本解析成秒。

        兼容：中文单位词（"8小时30分"）、冒号（"01:23:45" / "12:34"）、
        裸数字（按 秒/分:秒/时:分:秒 推断）。失败返回 None。
        """
        if not text:
            return None
        text = str(text).strip()
        if not text:
            return None

        unit_pairs = re.findall(r"(\d+)\s*(小时|时|分|秒)", text)
        if unit_pairs:
            total = 0
            for num, unit in unit_pairs:
                n = int(num)
                if unit in ("小时", "时"):
                    total += n * 3600
                elif unit == "分":
                    total += n * 60
                else:
                    total += n
            return total

        if ":" in text:
            parts = [p for p in text.split(":") if p.strip().isdigit()]
            numbers = [int(p) for p in parts]
            if len(numbers) >= 3:
                h, m, s = numbers[-3], numbers[-2], numbers[-1]
                return h * 3600 + m * 60 + s
            if len(numbers) == 2:
                m, s = numbers
                return m * 60 + s
            if len(numbers) == 1:
                return numbers[0]
            return None

        numbers = [int(n) for n in re.findall(r"\d+", text)]
        if len(numbers) >= 3:
            h, m, s = numbers[-3], numbers[-2], numbers[-1]
            return h * 3600 + m * 60 + s
        if len(numbers) == 2:
            m, s = numbers
            return m * 60 + s
        if len(numbers) == 1:
            return numbers[0]
        return None

    # ========================================================
    # OCR 工具
    # ========================================================

    def _ocr_desc(self, desc_element) -> str:
        """OCR 描述区域（元素 bbox），去换行/空白合并成单行文本。"""
        box = self.task._safe_box(desc_element)
        if box is None:
            self.task.log_info(f"未找到描述区域 bbox: {desc_element.name}")
            return ""
        try:
            results = self.task.ocr(box=box)
        except Exception as e:
            self.task.log_info(f"OCR 描述异常: {e}")
            return ""
        if not results:
            return ""
        text = "".join((r.name or "") for r in results)
        return re.sub(r"\s+", "", text)

    def _match_goal_by_text(self, text, goals):
        """描述文本按配置顺序匹配目标关键词；命中返回目标名，否则 None。"""
        for goal in goals:
            if goal in MOBILITY_GOALS and MOBILITY_GOALS[goal]["text"] in text:
                return goal
        return None

    # ========================================================
    # 专属任务卡槽
    # ========================================================

    def run_exclusive_slot(self, slot_element, goals):
        """
        处理一个专属任务卡槽。

        流程：点击卡槽 → 弹出描述窗口 → OCR 描述匹配配置目标：
          - 命中    → 点接受按钮 → 'accepted'
          - 未命中  → 点刷新按钮 → 确认 → OCR 该卡槽刷新倒计时

        返回 (action, refresh_seconds)：
          action = 'accepted' | 'refreshed' | 'failed'
          refresh_seconds：走刷新分支时为 OCR 倒计时秒数（失败 None）
        """
        if not self.task._wait_and_click(slot_element, timeout=2.0):
            self.task.log_error(f"点击卡槽失败: {slot_element.name}")
            return ("failed", None)
        self.task._sleep(0.5)

        text = self._ocr_desc(LEAGUE_MOB_DESC_AREA)
        self.task.log_info(f"专属任务描述: {text!r}")
        goal = self._match_goal_by_text(text, goals)

        if goal is not None:
            if not self.task._wait_and_click(
                LEAGUE_MOB_ACCEPT_BUTTON, timeout=2.0
            ):
                self.task.log_error("点接受按钮失败")
                return ("failed", None)
            self.task._sleep(0.8)
            self.task.log_info(f"专属任务已接受（目标 {goal}）")
            return ("accepted", None)

        if not self.task._wait_and_click(
            LEAGUE_MOB_REFRESH_BUTTON, timeout=2.0
        ):
            self.task.log_error("点刷新按钮失败")
            return ("failed", None)
        self.task._sleep(1.0)

        # 刷新后卡槽描述区域显示倒计时
        countdown = self._ocr_desc(LEAGUE_MOB_DESC_AREA)
        seconds = self.read_refresh_time(countdown)
        self.task.log_info(
            f"专属任务已刷新，倒计时 OCR: {countdown!r} → {seconds}s"
        )
        return ("refreshed", seconds)

    # ========================================================
    # 公共任务：阶段 A（向上滑动检索）
    # ========================================================

    def run_public_tasks(self, goals, can_refresh, scroll_step):
        """
        公共任务阶段 A：向上滑动检索并处理。

        参数：
          goals        配置的公共任务目标名列表（顺序即优先级）
          can_refresh  是否允许刷新（参数 公共任务刷新）
          scroll_step  每次滑动幅度（相对值 0-1）

        返回状态：
          'accepted'  至少接受了一个任务
          'nomatch'   处理完但没接到（窗口都关了，现场可信）
          'done'      无目标配置 / 无滚动窗口，跳过
          'failed'    中途失败
        """
        window = self.task._safe_box(LEAGUE_MOB_SCROLL_WINDOW)
        if window is None:
            self.task.log_info("未找到任务滚动窗口 bbox，跳过公共任务")
            return "done"
        if not goals:
            self.task.log_info("未配置公共任务目标，跳过")
            return "done"

        accepted_any = False
        for round_index in range(self.public_scroll_max + 1):
            # ---- 特征优先：滚动窗口内直接匹配目标卡片 ----
            box = self._find_goal_cards(window, goals)
            if box is not None:
                if not self.task.click(box):
                    return "failed"
                self.task._sleep(0.5)
                result = self._open_dialog_and_verify(
                    LEAGUE_MOB_PUBLIC_DESC, goals, can_refresh)
                if result == "failed":
                    return "failed"
                if result == "accepted":
                    accepted_any = True
                    break

            # ---- 奖励标志法：找未接任务的卡片 → 点开 OCR 校验 ----
            handled = self._handle_unclaimed_cards(
                window, LEAGUE_MOB_PUBLIC_DESC, goals, can_refresh)
            if handled == "failed":
                return "failed"
            if handled == "accepted":
                accepted_any = True
                break

            # ---- 滑动换屏（内容上移看更下面的任务）----
            if round_index >= self.public_scroll_max:
                self.task.log_info("公共任务滑动轮数耗尽")
                break
            self._scroll_window_px(
                window, window.y + window.height * 0.85,
                window.y + window.height * 0.85 - scroll_step * self.task.height_of_screen(1.0),
            )

        return "accepted" if accepted_any else "nomatch"

    def _find_goal_cards(self, window, goals):
        """在滚动窗口内按目标关键词顺序找卡片（此处仅作为检索入口，
        供特征模板存在时使用；当前目标均为纯 OCR 匹配，恒返回 None）。"""
        return None

    def _handle_unclaimed_cards(self, window, desc_element, goals, can_refresh):
        """
        奖励标志位法处理当前屏的未接任务卡片。

        匹配滚动区域内所有基准点 → 每个基准点构造偏移 bbox
        （x=基准点右缘，y=基准点 y；宽高取偏移区域特征），
        在偏移 bbox 内匹配奖励标志：有标志 = 未接任务 → 点开卡片
        OCR 校验描述 → 接受 / 刷新 / 关闭。
        返回 'none' | 'accepted' | 'failed'。
        """
        bases = self.task.find_feature(
            LEAGUE_MOB_PUBLIC_BASE_POINT.resource_id,
            box=window,
            limit=10,
        ) or []
        if not bases:
            return "none"

        offset_box = self.task._find(LEAGUE_MOB_PUBLIC_OFFSET_AREA)
        if offset_box is None:
            self.task.log_info("未找到偏移区域特征，跳过奖励标志扫描")
            return "none"

        for base in bases:
            # 偏移 bbox：全屏 box 基底上覆写像素值（Box 可变）。
            region = self.task.box_of_screen(0, 0, 1, 1)
            region.x = base.x + base.width
            region.y = base.y
            region.width = offset_box.width
            region.height = offset_box.height
            flag = self.task._find(
                LEAGUE_MOB_PUBLIC_REWARD_FLAG, box=region
            )
            if flag is None:
                continue

            # 点奖励标志打开任务窗口
            if not self.task.click(flag):
                return "failed"
            self.task._sleep(0.5)
            result = self._open_dialog_and_verify(
                desc_element, goals, can_refresh)
            if result == "failed":
                return "failed"
            if result == "accepted":
                return "accepted"
            # 窗口已处理（刷新/关闭），继续扫下一个卡片
        return "none"

    def _open_dialog_and_verify(self, desc_element, goals, can_refresh):
        """
        点开卡片后的描述窗口处理：OCR 校验 → 接受 / 刷新 / 关闭。

        返回 'accepted' | 'closed' | 'refreshed' | 'failed'。
        """
        text = self._ocr_desc(desc_element)
        self.task.log_info(f"公共任务描述: {text!r}")
        goal = self._match_goal_by_text(text, goals)

        if goal is not None:
            if self.task._wait_and_click(
                LEAGUE_MOB_PUBLIC_ACCEPT, timeout=2.0
            ):
                self.task._sleep(0.8)
                self.task.log_info(f"公共任务已接受（目标 {goal}）")
                return "accepted"
            self.task.log_error("公共任务点接受失败")
            return "failed"

        if can_refresh:
            if self.task._wait_and_click(
                LEAGUE_MOB_PUBLIC_REFRESH, timeout=2.0
            ):
                self.task._sleep(0.8)
                self.task.log_info("公共任务不匹配，已刷新")
                return "refreshed"
            self.task.log_error("公共任务点刷新失败")
            return "failed"

        if self.task._wait_and_click(LEAGUE_MOB_PUBLIC_CLOSE, timeout=2.0):
            self.task._sleep(0.5)
            self.task.log_info("公共任务不匹配且不刷新，关闭窗口")
            return "closed"
        self.task.log_error("公共任务关窗失败")
        return "failed"

    # ========================================================
    # 公共任务：阶段 B（回滚 + 逐行向下刷新清扫）
    # ========================================================

    def run_refresh_sweep(self, refresh_goals, scroll_step):
        """
        阶段 B：仅 公共任务刷新 开启时调用。

        流程：回滚到滚动区域上沿 → 逐行向下清扫：
          基准点 → 偏移 bbox → 奖励标志（未接任务）→
          匹配刷新目标关键词特征：
            命中（值得保留）→ 关闭窗口
            未命中 → 点开 → 刷新
          滑动幅度按当前屏基准点行高（compute_row_scroll）。

        返回 'done' | 'failed'。
        """
        window = self.task._safe_box(LEAGUE_MOB_SCROLL_WINDOW)
        if window is None:
            self.task.log_info("未找到任务滚动窗口 bbox，跳过刷新清扫")
            return "done"

        if not self._rollback_to_top(window, scroll_step):
            self.task.log_info("回滚失败，跳过刷新清扫")
            return "done"

        screen_h = self.task.height_of_screen(1.0)
        for _ in range(self.public_scroll_max + 1):
            handled = self._sweep_row_unclaimed(
                window, refresh_goals)
            if handled == "failed":
                return "failed"

            params = self.compute_row_scroll(window)
            if params is None:
                self.task.log_info("无基准点可计算行高，清扫结束")
                return "done"
            from_x, from_y, dist_y = params
            if dist_y <= 0 or dist_y > 0.9:
                self.task.log_info("行高异常，清扫结束")
                return "done"

            to_y = from_y - dist_y
            self.task.swipe_relative(
                from_x, from_y, from_x, max(0.0, to_y),
                duration=0.3, settle_time=self.scroll_settle_time,
            )

        self.task.log_info("刷新清扫轮数耗尽")
        return "done"

    def _rollback_to_top(self, window, scroll_step) -> bool:
        """向下滑动直到出现滚动区域上沿（回滚到列表顶部）。"""
        for _ in range(self.rollback_scroll_max):
            edge = self.task._find(
                LEAGUE_MOB_SCROLL_TOP_EDGE,
                box=self.task.box_of_screen(0, 0, 1, 1),
            )
            if edge is not None:
                self.task.log_info("已回滚到公共任务列表顶部")
                return True
            self._scroll_window_down(window, scroll_step)
        self.task.log_info("回滚未检测到上沿，已达滑动次数上限")
        return False

    def _sweep_row_unclaimed(self, window, refresh_goals):
        """
        清扫当前屏：未接卡片（奖励标志）按刷新目标处理。

        命中刷新目标 → 点开卡片 → 关闭（保留任务）；
        未命中 → 点开卡片 → 刷新。
        返回 'none' | 'failed'。
        """
        bases = self.task.find_feature(
            LEAGUE_MOB_PUBLIC_BASE_POINT.resource_id,
            box=window,
            limit=10,
        ) or []
        if not bases:
            return "none"

        offset_box = self.task._find(LEAGUE_MOB_PUBLIC_OFFSET_AREA)
        if offset_box is None:
            self.task.log_info("未找到偏移区域特征，跳过清扫")
            return "none"

        for base in bases:
            region = self.task.box_of_screen(0, 0, 1, 1)
            region.x = base.x + base.width
            region.y = base.y
            region.width = offset_box.width
            region.height = offset_box.height
            flag = self.task._find(
                LEAGUE_MOB_PUBLIC_REWARD_FLAG, box=region
            )
            if flag is None:
                continue

            # 偏移区域内按卡片特征预判是否值得保留
            keep = self._region_matches_any_goal(region, refresh_goals)
            if not self.task.click(flag):
                return "failed"
            self.task._sleep(0.5)

            if keep:
                if not self.task._wait_and_click(
                    LEAGUE_MOB_PUBLIC_CLOSE, timeout=2.0
                ):
                    self.task.log_error("清扫关窗失败")
                    return "failed"
                self.task._sleep(0.5)
                self.task.log_info("清扫：命中刷新目标，保留任务")
            else:
                if not self.task._wait_and_click(
                    LEAGUE_MOB_PUBLIC_REFRESH, timeout=2.0
                ):
                    self.task.log_error("清扫刷新失败")
                    return "failed"
                self.task._sleep(0.8)
                self.task.log_info("清扫：未命中刷新目标，已刷新")
        return "none"

    def _region_matches_any_goal(self, region, goals):
        """偏移区域内匹配刷新目标（当前目标均为 OCR 文案，恒 False）。"""
        return False

    # ========================================================
    # 滑动工具
    # ========================================================

    def _scroll_window_px(self, window, from_y_px, to_y_px):
        """在滚动窗口内以像素坐标滑动一屏（x 取窗口中线）。"""
        screen_w = self.task.width_of_screen(1.0)
        screen_h = self.task.height_of_screen(1.0)
        if not screen_w or not screen_h:
            return
        from_x_px = window.x + window.width * 0.5
        self.last_public_scroll_px = abs(int(from_y_px - to_y_px))
        self.task.swipe_relative(
            from_x_px / screen_w,
            from_y_px / screen_h,
            from_x_px / screen_w,
            to_y_px / screen_h,
            duration=0.3,
            settle_time=self.scroll_settle_time,
        )

    def _scroll_window_down(self, window, scroll_step):
        """向下滑动（内容下移回到更上面的任务）。"""
        screen_h = self.task.height_of_screen(1.0)
        from_y_px = window.y + window.height * 0.15
        to_y_px = from_y_px + screen_h * scroll_step
        self._scroll_window_px(window, from_y_px, to_y_px)

    def compute_row_scroll(self, window):
        """
        按当前屏基准点行高计算"向下滑一档"的滑动参数。

        起点：所有基准点中最大 y+height+5px、x 取最大 x；
        距离：最大 (y+height+5px) − 最小 y。
        返回 (from_rel_x, from_rel_y, dist_rel_y)；无基准点返回 None。
        """
        bases = self.task.find_feature(
            LEAGUE_MOB_PUBLIC_BASE_POINT.resource_id,
            box=window,
            limit=10,
        ) or []
        if not bases:
            return None

        max_bottom = max(b.y + b.height for b in bases) + 5
        min_y = min(b.y for b in bases)
        max_x = max(b.x for b in bases)
        dist_px = max_bottom - min_y
        if dist_px <= 0:
            return None

        screen_w = self.task.width_of_screen(1.0)
        screen_h = self.task.height_of_screen(1.0)
        if not screen_w or not screen_h:
            return None

        return (
            max_x / screen_w,
            max_bottom / screen_h,
            dist_px / screen_h,
        )
