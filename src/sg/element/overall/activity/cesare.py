"""
切萨雷征讨领域对象。

职责：
  - 从主界面进入切萨雷界面（便捷入口 / 常规活动滑动查找两条路径）
  - OCR 读取目标实力
  - 侦察 → 点击资源 → 弹出处理窗口
  - 处理窗口分支：挑战（普通野兽式流程）/ 集结（Rally 复用）

不负责：场景判定兜底、体力检查、队列调度、弹窗清理。
"""

import re

from src.sg.scene.elements import (
    GLOBAL_ACTIVITY_FIGHT_QIESALEI_CONVENIENT_ENTRY,
    GLOBAL_ACTIVITY_FIGHT_QIESALEI_CONVENIENT_ENTRY_3,
    GLOBAL_ENTRY_COMMON_ACTIVITY,
    GLOBAL_ENTRY_GREATVALUE_ACTIVITY_SLIDE_AREA,
    GLOBAL_ACTIVITY_FIGHT_QIESALEI_TAG_ENTRY,
    GLOBAL_ACTIVITY_FIGHT_QIESALEI_POWER,
    GLOBAL_ACTIVITY_FIGHT_QIESALEI_BUTTON_RECONNOITRE,
    GLOBAL_ACTIVITY_FIGHT_QIESALEI_RESOURCE,
    GLOBAL_ACTIVITY_FIGHT_QIESALEI_BUTTON_CHALLENGE,
    GLOBAL_ACTIVITY_FIGHT_QIESALEI_BUTTON_RALLY,
    GLOBAL_PLAYER_STAMINA_MASK,
)
from src.sg.scene.scene_type import SceneType


class Cesare:
    """
    切萨雷征讨领域对象。

    对外接口：
      open_qiesalei()      -> bool   从主界面进入切萨雷界面
      read_power()         -> int|None  OCR 目标实力，存 last_power
      reconnoitre()        -> bool   点侦察按钮并等画面跳转
      open_resource()      -> bool   点击资源区域弹出处理窗口
      challenge_or_rally() -> str    处理窗口分支，返回 'challenge'|'rally'|'none'
      pick_rally_entry()   -> bool   找到集结按钮（供 Task 传给 Rally）
    """

    def __init__(self, task):
        self.task = task

        # ---- 参数 ----
        # 全屏匹配便捷入口的超时
        self.entry_timeout = 6.0
        # 常规活动界面滑动查找标签次数上限
        self.tag_scroll_max = 8
        # 每次滑动的距离比例（1/4 屏）
        self.scroll_step = 0.25
        # 滑动后等待动画时间
        self.scroll_settle_time = 1.0

        # ---- 运行时状态 ----
        # OCR 读取到的目标实力，供后续业务使用
        self.last_power = None

    # ========================================================
    # 步骤 1：进入切萨雷界面
    # ========================================================

    def open_qiesalei(self) -> bool:
        """
        主界面 → 切萨雷界面。

        路径 A：主界面存在便捷入口（全屏搜索），直接点击。
        路径 B：常规活动入口 → 超值活动滑动区域先向左滑（每次 1/4 屏）
                直到匹配切萨雷标签；向左找不到再向右滑查找，找到点击进入。
        """
        # ---- 路径 A：便捷入口 ----
        if self.task._wait_and_click_all_screen(
            (GLOBAL_ACTIVITY_FIGHT_QIESALEI_CONVENIENT_ENTRY,GLOBAL_ACTIVITY_FIGHT_QIESALEI_CONVENIENT_ENTRY_3),
            timeout=self.entry_timeout,
        ):
            self.task.log_info("通过便捷入口进入切萨雷")
            self.task._sleep(1.0)
            return True

        self.task.log_info("无便捷入口，走常规活动入口")

        # ---- 路径 B：常规活动 ----
        if not self.task._wait_and_click(
            GLOBAL_ENTRY_COMMON_ACTIVITY,
            timeout=self.entry_timeout,
        ):
            self.task.log_error("未找到常规活动入口")
            return False
        self.task._sleep(1.0)

        # 滑动区域内查找切萨雷标签：先向左滑，找不到再向右滑
        box = self._scroll_find_tag()
        if box is None:
            self.task.log_error("左右滑动后仍未找到切萨雷标签入口")
            return False

        if not self.task.click(box):
            return False
        self.task._sleep(1.0)
        self.task.log_info("已从常规活动进入切萨雷")
        return True

    def _scroll_find_tag(self):
        """
        在滑动区域内查找切萨雷标签入口。

        向左滑动（每次 1/4 屏）直到匹配；向左耗尽后向右滑动查找。
        找到返回标签 Box，耗尽次数返回 None。
        """
        slide_area = self._safe_box(GLOBAL_ENTRY_GREATVALUE_ACTIVITY_SLIDE_AREA)
        if slide_area is None:
            self.task.log_error("未找到滑动区域 bbox")
            return None

        # 向左滑（内容左移，露出右侧标签）
        box = self._find_tag_by_sliding(slide_area, direction="left")
        if box is not None:
            return box

        self.task.log_info("向左滑动未找到，改向右滑动查找")
        return self._find_tag_by_sliding(slide_area, direction="right")

    def _find_tag_by_sliding(self, slide_area, direction: str):
        """在滑动区域内按方向滑动查找切萨雷标签，找到返回 Box。"""
        for attempt in range(self.tag_scroll_max + 1):
            box = self.task._find(
                GLOBAL_ACTIVITY_FIGHT_QIESALEI_TAG_ENTRY,
                box=slide_area,
            )
            if box is not None:
                if attempt > 0:
                    self.task.log_info(
                        f"向{direction}滑动 {attempt} 次后找到切萨雷标签"
                    )
                return box

            self.task.log_info(
                f"未找到切萨雷标签，向{direction}滑动 1/4 屏 ({attempt + 1})"
            )
            # 以滑动区域中心为基准，每次滑动 1/4 屏。
            # swipe_relative 只接受 0-1 相对坐标，不能用 bbox 像素值。
            if direction == "left":
                self.task.swipe_relative(
                    0.625, 0.5, 0.375, 0.5,
                    duration=0.3, settle_time=self.scroll_settle_time,
                )
            else:
                self.task.swipe_relative(
                    0.375, 0.5, 0.625, 0.5,
                    duration=0.3, settle_time=self.scroll_settle_time,
                )

        return None

    # ========================================================
    # 步骤 2：读取目标实力
    # ========================================================

    def read_power(self):
        """
        OCR power 区域读取目标实力，成功写入 last_power 并返回 int。
        未识别返回 None（实力读取本次不阻塞主流程）。
        """
        box = self._safe_box(GLOBAL_ACTIVITY_FIGHT_QIESALEI_POWER)
        if not box:
            self.task.log_info("未找到目标实力区域 bbox")
            return None

        try:
            results = self.task.ocr(box=box)
        except Exception as e:
            self.task.log_info(f"OCR 目标实力异常: {e}")
            results = None

        if not results:
            self.task.log_info("未识别到目标实力")
            return None

        text = results[0].name or ""
        self.task.log_info(f"OCR 目标实力原文: {text}")
        value = self._parse_power_text(text)
        if value is None:
            return None
        self.last_power = value
        self.task.log_info(f"目标实力: {self.last_power}")
        return self.last_power

    # ========================================================
    # 步骤 3：侦察
    # ========================================================

    def reconnoitre(self) -> bool:
        """
        点击侦察按钮。

        返回 False 表示体力不足（弹出体力补充界面），由 Task 结束本次
        任务并按配置等待。返回 True 表示侦察成功、画面已跳转。
        """
        box=self.task.get_box_by_name(GLOBAL_ACTIVITY_FIGHT_QIESALEI_BUTTON_RECONNOITRE.resource_id)
        if box and not self.task.click(
            box
        ):
            return False
        self.task._sleep(1.5)
        return True

    # ========================================================
    # 步骤 4：点击资源弹出处理窗口
    # ========================================================

    def open_resource(self) -> bool:
        """侦察后画面跳转，点击资源区域弹出处理窗口。"""
        if self.task._wait_and_click(GLOBAL_PLAYER_STAMINA_MASK,timeout=2,
        with_recovery=True):
            self.task.log_info(
            "体力不足"
        )
            return False
        
        box=self.task.get_box_by_name(GLOBAL_ACTIVITY_FIGHT_QIESALEI_RESOURCE.resource_id)
        if box and not self.task.click(
            box
        ):
            return False
        self.task._sleep(1.0)
        return True

    # ========================================================
    # 步骤 5：处理窗口分支
    # ========================================================

    def challenge_or_rally(self) -> str:
        """
        处理窗口分支判定（OCR 实力对比）。

        逻辑：
          - 目标实力未知（OCR 失败）→ 默认走挑战
          - 自身实力已知且 >= 目标实力 → 挑战
          - 其余 → 集结

        返回 'challenge' / 'rally' / 'none'。
        'none' 表示窗口内两个按钮都未匹配到，由上层按失败处理。
        """
        playerpower=self.task.player.get_team_power()
        target = self.last_power
        mine = max(playerpower['team_power_sinew'],playerpower['team_power_biggest'])
        if mine is None:
            mine = 150000000
        if target is None:
            mode = "challenge"
        elif mine is not None and mine >= target:
            mode = "challenge"
        else:
            mode = "rally"

        self.task.log_info(
            f"分支判定: 目标实力={target}, 自身实力={mine} → {mode}"
        )
        return mode

    def pick_challenge(self) -> bool:
        """处理窗口内点击挑战按钮。"""
        return self.task._wait_and_click(
            GLOBAL_ACTIVITY_FIGHT_QIESALEI_BUTTON_CHALLENGE,
            timeout=self.entry_timeout,
        )

    def pick_rally_entry(self) -> bool:
        """
        处理窗口内点击集结按钮，进入集结配置页。

        供 Task 的集结分支调用：返回 True 后继续 Rally.execute()。
        """
        return self.task._wait_and_click(
            GLOBAL_ACTIVITY_FIGHT_QIESALEI_BUTTON_RALLY,
            timeout=self.entry_timeout,
        )

    def _parse_power_text(self, text):
        """
        解析 OCR 实力文本为 int。

        用正则提取：
          - 千分位：'1,234,567'
          - 小数：'1.23万' / '12.5'
          - 中文量级：'1.23万' → 12300、'2亿' → 200000000

        无匹配返回 None。
        """
        if text is None:
            return None
        text = str(text).strip()
        if not text:
            return None

        # 匹配 带可选小数/千分位的数字，后接可选中文量级单位
        m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([万亿])?", text)
        if not m:
            return None

        value = float(m.group(1).replace(",", ""))
        unit = m.group(2)
        if unit == "万":
            value *= 10_000
        elif unit == "亿":
            value *= 100_000_000

        return int(value)

    # ========================================================
    # 内部方法
    # ========================================================

    def _safe_box(self, element):
        """
        bbox 定位。框架 get_box_by_name 找不到类别时抛 ValueError，
        这里转为 None，交由上层处理。
        """
        try:
            return self.task.get_box_by_name(element.resource_id)
        except ValueError:
            return None
