"""
野兽挑战领域对象。

从 WatchTowerEventTask 的野兽事件处理抽象而来：
  - 处理弹窗的挑战按钮是固定位置，与瞭望塔事件使用的
    WORLD_EVENT_OBJECT_HANDLEAREA 一致（本次不抽公共元素，直接复用）
  - 击杀前从 beast_time_way 区域 OCR 单程行军时间，
    来回耗时 = 2 × 单程；与巨兽集结的 rally 时间计算一样，
    用于队列建立下一个任务的预计触发时间

职责：点击挑战按钮 + 读取行军时间 + 执行击杀流程。
不负责：搜索目标、场景切换、队列调度。
"""

import re

from src.sg.scene.elements import (
    WORLD_EVENT_OBJECT_HANDLEAREA,
    WORLD_EVENT_BEAST_KILL,
    BEAST_TIME_WAY,
)


class Beast:
    """
    野兽挑战。

    职责：在处理弹窗中点击挑战按钮（固定位置），并执行击杀流程。
    不负责：搜索目标、场景切换、队列调度。
    """

    def __init__(self, task):
        self.task = task

        # 搜索确认后游戏会居中目标并弹出处理弹窗，
        # 点挑战按钮前先等弹窗渲染完成。
        self.challenge_settle_seconds = 1.0

        # 击杀点击后的稳定等待（与瞭望塔野兽事件一致）
        self.kill_settle_seconds = 1.0

        # OCR 不到单程时间时的兜底（秒）
        self.default_one_way_seconds = 300

        # 挑战进入选队界面时要选的队伍（SceneElement）。
        # 由任务注入（如 TEAM_HUNTING）；None = 选队步骤跳过。
        self.team_element = None

    # ========================================================
    # 完整流程
    # ========================================================

    def challenge(self, smalltili=False) -> bool:
        """
        完整挑战流程：
          1. 等处理弹窗渲染完成
          2. 点击弹窗的挑战按钮（固定位置，与瞭望塔事件
             WORLD_EVENT_OBJECT_HANDLEAREA 一致，暂不抽公共元素）
          3. smalltili=True 时进入选队界面并切换队伍
             （队伍由 task 注入的 beast.team_element 决定）
          4. 读取单程行军时间（kill 前，弹窗仍在）
          5. 点击挑战击杀按钮（world_event_beast_kill）

        参数名 smalltili（小体力）沿用调用方命名：省体力队伍挑战开关。
        """
        self.task._sleep(self.challenge_settle_seconds)

        if not self.click_challenge_button():
            return False

        # 点击了挑战，进入选取队伍界面，按开关决定是否切队
        if smalltili and not self.select_team():
            self.task.log_error("挑战选队失败")
            return False

        # 单程时间要在弹窗还在时读
        self.read_one_way_time()

        return self.kill()

    def select_team(self) -> bool:
        """
        选队步骤（与 RallyConfig.select_team 同规则）：
          - 并发 > 1 跳过（省体力队伍只有一支，多路共用会互相覆盖）
          - 未注入 team_element 跳过
        """
        max_active = getattr(self.task, "max_active", 1) or 1
        if max_active > 1:
            self.task.log_info(
                f"Beast.select_team: 并发={max_active} > 1，跳过选择队伍"
            )
            return True
        if self.team_element is None:
            self.task.log_info("Beast.select_team: 未配置队伍，跳过")
            return True
        return self.task._wait_and_click(
            self.team_element,
            name=f"选择集结队伍: {self.team_element.name}",
        )

    # ========================================================
    # 步骤 1：挑战按钮（固定位置）
    # ========================================================

    def click_challenge_button(self) -> bool:
        """点击处理弹窗中的挑战按钮（固定位置 bbox）。"""
        box = self.task._wait_element(WORLD_EVENT_OBJECT_HANDLEAREA,box=self.task.box_of_screen(0, 0, 1, 1))
        if not box:
            self.task.log_error("未找到挑战按钮（处理弹窗固定位置）bbox")
            return False

        self.task.log_info("点击挑战按钮")
        if not self.task.click(box):
            return False
        return True

    # ========================================================
    # 步骤 2：击杀流程（与瞭望塔 _handle_beast_event 一致）
    # ========================================================

    def kill(self) -> bool:
        """bbox 点击挑战击杀按钮完成流程。"""
        box = self._safe_box(WORLD_EVENT_BEAST_KILL)
        if not box:
            self.task.log_error("未找到挑战击杀按钮 bbox")
            return False

        self.task.log_info("点击挑战击杀按钮")
        if not self.task.click(box):
            return False

        self.task._sleep(self.kill_settle_seconds)
        return True

    # ========================================================
    # 内部方法
    # ========================================================

    def _safe_box(self, element):
        """
        bbox 定位。
        框架 get_box_by_name 找不到类别时抛 ValueError，
        这里转为 None，交由上层 _step 的恢复重试机制接管。
        """
        try:
            return self.task.get_box_by_name(element.resource_id)
        except ValueError:
            return None

    # ========================================================
    # 行军时间（kill 前读，弹窗还在）
    # ========================================================

    def read_one_way_time(self):
        """
        OCR beast_time_way 区域读取单程行军时间。

        解析支持 '90'、'1:30'、'1:30:00' 等格式，
        与 RallyTimer.parse_time_text 一致。

        成功 → 记录 last_round_trip_seconds = 2 × 单程，返回单程秒数；
        失败 → 使用 default_one_way_seconds 兜底，返回 None。
        """
        box = self._safe_box(BEAST_TIME_WAY)
        if not box:
            self.task.log_error("未找到 beast_time_way bbox，使用兜底时间")
            self.last_round_trip_seconds = float(
                self.default_one_way_seconds * 2
            )
            return None

        try:
            results = self.task.ocr(box=box)
        except Exception as e:
            self.task.log_info(f"OCR 行军时间异常: {e}")
            results = None

        seconds = None
        if results:
            text = results[0].name or ""
            self.task.log_info(f"OCR 单程行军时间原文: {text}")
            seconds = self._parse_time_text(text)

        if seconds is None or seconds <= 0:
            self.task.log_info(
                f"未识别到单程行军时间，使用兜底值: "
                f"{self.default_one_way_seconds}s"
            )
            seconds = float(self.default_one_way_seconds)

        self.last_round_trip_seconds = float(seconds) * 2
        self.task.log_info(
            f"单程 {seconds:.0f}s，来回 {self.last_round_trip_seconds:.0f}s"
        )
        return seconds

    def estimate_wait(self) -> float:
        """
        返回预计等待时间（来回行军耗时）。
        供 run_interaction 返回给队列，作为下一个任务的预计触发时间。
        """
        if self.last_round_trip_seconds <= 0:
            return float(self.default_one_way_seconds * 2)
        return float(self.last_round_trip_seconds)

    def _parse_time_text(self, text):
        """
        把 OCR 文本解析成秒。
        支持: '90', '1:30', '1:30:00'（与巨兽集结的 RallyTimer 相同口径）。
        """
        if text is None:
            return None
        text = str(text).strip()
        if not text:
            return None

        if text.isdigit():
            return int(text)

        if ":" in text or "：" in text:
            normalized = text.replace("：", ":")
            parts = normalized.split(":")
            try:
                nums = [int(p) for p in parts]
            except ValueError:
                return None
            if len(nums) == 2:
                return nums[0] * 60 + nums[1]
            if len(nums) == 3:
                return nums[0] * 3600 + nums[1] * 60 + nums[2]
            return None

        # 兜底：提取文本中的所有数字按 时/分/秒 合并
        total = 0
        hour = re.search(r"(\d+)\s*[时小]", text)
        minute = re.search(r"(\d+)\s*分", text)
        second = re.search(r"(\d+)\s*秒", text)
        if hour:
            total += int(hour.group(1)) * 3600
        if minute:
            total += int(minute.group(1)) * 60
        if second:
            total += int(second.group(1))
        return total if total > 0 else None
