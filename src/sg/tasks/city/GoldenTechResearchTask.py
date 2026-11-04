"""
黄金科技研究任务（Golden Tech Research）。

流程（按用户需求原文）：
  1. 确保前台，清理弹窗，回到主界面
  2. 点击 GLOBAL_BUTTON_TASKLIST 打开任务列表
  3. 在任务列表中向上滑动（每次 1/4 屏），循环检索
     GOLDEN_TECH_RESEARCH_ENTRY（黄金科技研究入口-1）
  4. 检索到入口后，判断其下方状态特征
     GOLDEN_TECH_RESEARCH_STATUS_IDLE（黄金科技研究状态-1-状态判断-空闲）：
     - 匹配到「空闲中」→ 点击入口，跳转战争学院
     - 不满足（已有研究在进行）→ FAILED，不重试
  5. 点击 GOLDEN_TECH_ACADEMY（黄金科技-2）进入科技树界面
  6. 点击 GOLDEN_TECH_RESEARCH_NODE（黄金科技研究-3）定位当前可研究节点
  7. 按配置的兵种优先级在三个分支节点中选择其一点击：
     - 弓 → GOLDEN_TECH_ARCHER_NODE_1（皇家科技-4-弓-科技1）
     - 骑 → GOLDEN_TECH_CAVALRY_NODE_1（黄金科技-4-骑-科技1）
     - 步 → GOLDEN_TECH_INFANTRY_NODE_1（黄金科技-4-步-科技1）
     - sub_tech_names 配置了子项特征名时，按顺序优先匹配子项
  8. 弹出研究确认弹窗后，点击
     GOLDEN_TECH_CONFIRM_RESEARCH_BUTTON（黄金科技研究-5-确认按钮-预估时间框）
     中的「研究」按钮开始研究
  9. 研究启动后，OCR GOLDEN_TECH_STATUS_TIME_HELP（黄金科技研究-6-状态时间-互助）
     底栏剩余研究时间，并点击「联盟互助」
 10. 退回主界面，任务返回成功以及研究完成时间（wait_seconds = 剩余研究时间）

调度语义（与 ClaimWarehouseReward 对齐）：
  - run_interaction() 返回 (SUCCESS, wait_seconds)，wait_seconds =
    剩余研究时间。队列把任务置 IN_PROGRESS，到点后 check_completed()
    放行 DONE，follower 按 next_trigger_delay=0 在同一时刻触发下一次。
  - 注册为 count=1（不可并行）+ next_trigger_delay=0，循环节奏由
    本任务的动态等待驱动。
  - 入口状态非「空闲」（已有研究进行中）→ FAILED 不重试（需求原文）；
    OCR 失败时按 default_retry_wait 兜底重试间隔。
"""
import re
import time
import types

from src.sg.tasks.SGBaseTask import SGBaseTask
from src.scheduler.task_status import InteractionResult

from src.sg.helper.recovery import RecoveryHelper

from src.sg.element.overall.activity.popup import ActivityPopup

from src.sg.scene.elements import (
    GLOBAL_BUTTON_TASKLIST,
    GLOBAL_TASK_LIST_DOWN_EDGE,
    GOLDEN_TECH_RESEARCH_ENTRY,
    GOLDEN_TECH_RESEARCH_STATUS_IDLE,
    GOLDEN_TECH_ACADEMY,
    GOLDEN_TECH_RESEARCH_NODE,
    GOLDEN_TECH_ARCHER_NODE_1,
    GOLDEN_TECH_CAVALRY_NODE_1,
    GOLDEN_TECH_INFANTRY_NODE_1,
    GOLDEN_TECH_CONFIRM_RESEARCH_BUTTON,
    GOLDEN_TECH_STATUS_TIME_HELP,
)

from src.sg.scene.scene_type import SceneType


class GoldenTechResearchTask(SGBaseTask):
    """
    黄金科技研究。

    =========================================================
    职责边界：
      - 只做业务编排：开任务列表 → 滚动检索黄金科技研究入口 →
        空闲判断 → 进战争学院科技树 → 按兵种优先级选分支 →
        确认研究 → OCR 剩余时间 + 联盟互助 → 回主界面
      - 弹窗处理交给 ActivityPopup
      - 恢复交给 RecoveryHelper
    =========================================================
    """

    # 任务列表向上滑动次数上限（每次 1/4 屏，参考仓库奖励/士兵训练）
    task_list_scroll_max = 10

    # 任务列表图标匹配阈值（固定样式）
    list_icon_threshold = 0.8

    # 跳转战争学院 / 科技树的界面等待
    page_switch_timeout = 2.0

    # 研究启动后底栏 OCR 区域出现等待（研究动画完成）
    research_start_timeout = 10.0

    # OCR 剩余研究时间失败的兜底间隔（秒）
    default_retry_wait = 3600.0

    # 动态等待下限：避免 OCR 异常小值导致空转（秒）
    min_wait_seconds = 60.0

    # 兵种优先级 → 默认分支节点（第 4 步三选一）
    TROOP_NODE_MAP = {
        "弓": GOLDEN_TECH_ARCHER_NODE_1,
        "骑": GOLDEN_TECH_CAVALRY_NODE_1,
        "步": GOLDEN_TECH_INFANTRY_NODE_1,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "Golden Tech Research"
        self.description = (
            "Golden tech research: open task list, find golden tech "
            "research entry, check idle status, pick troop branch in "
            "academy tech tree, confirm research, OCR remaining time "
            "and request alliance help."
        )

        # ====================================================
        # 队列注入字段（赋默认值，不写死配置）
        # ====================================================
        # 兵种优先级：弓 / 骑 / 步（下拉三选一）
        self.troop_priority = "弓"
        # 子项研究特征名（coco resource_id），逗号分隔；空 = 只用默认节点
        self.sub_tech_names = ""
        # OCR 剩余时间失败的兜底重试间隔（秒）
        self.default_retry_wait = 3600

        # 个性化参数容器
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
        # 内部状态
        # ====================================================
        # 研究完成时间戳（本地纪元秒），check_completed 用
        self.next_research_time = None
        # 本次选中的分支节点 resource_id（info 展示）
        self.picked_node = None

    # ========================================================
    # 通用助手
    # ========================================================

    def _full_screen(self):
        return self.box_of_screen(0, 0, 1, 1)

    def _parse_countdown_text(self, text: str):
        """
        把倒计时文本解析成秒数。

        兼容格式：
          - "01:23:45" / "1:23:45"   → 时:分:秒
          - "12:34"                  → 分:秒
          - "1小时23分45秒" 等混杂文本 → 按数字组个数推断
        解析失败返回 None。
        """
        if not text:
            return None

        text = text.strip()
        self.log_info(f"剩余研究时间 OCR 原始文本: {text}")

        # 冒号分隔：按段数直接映射
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

        # 无冒号：按数字组个数推断（3 组=时分秒，2 组=分秒，1 组=秒）
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
    # 步骤 1：展开任务列表 → 滚动检索黄金科技研究入口
    # ========================================================

    def _open_task_list(self) -> bool:
        """点击 GLOBAL_BUTTON_TASKLIST，展开任务列表。"""
        if not self._wait_and_click_all_screen(
            GLOBAL_BUTTON_TASKLIST, timeout=2.0,
        ):
            self.log_info("未找到任务列表按钮")
            return False
        self._sleep(1.0)
        return True

    def _scroll_find_research_entry(self) -> bool:
        """
        在任务列表中向上滑动（手指上滑 1/4 屏），
        直到出现 GOLDEN_TECH_RESEARCH_ENTRY。

        返回 True  ：列表中出现入口，可以做空闲判断
        返回 False ：滑动次数耗尽仍未出现。
                     若期间发现 down_edge（列表到底），
                     说明本任务不可用（需求原文：不重试）。
        """
        full = self._full_screen()

        for attempt in range(self.task_list_scroll_max + 1):
            entry_box = self._find(
                GOLDEN_TECH_RESEARCH_ENTRY,
                threshold=self.list_icon_threshold, box=full,
            )
            if entry_box is not None:
                self.log_info(
                    f"任务列表检索到黄金科技研究入口（滑动 {attempt} 次）"
                )
                return True

            # 滑到底标志出现 → 列表已到底，入口不存在
            edge_box = self._find(
                GLOBAL_TASK_LIST_DOWN_EDGE,
                threshold=self.list_icon_threshold, box=full,
            )
            if edge_box is not None:
                self.log_info(
                    "任务列表已到底（发现 down_edge），"
                    "无黄金科技研究入口"
                )
                return False

            # 未找到 → 手指向上滑 1/4 屏，露出下方条目
            self.swipe_relative(0.3, 0.6, 0.3, 0.3, duration=0.3,
                                settle_time=1.0)
            self._sleep(1.5)

        self.log_error("滚动查找黄金科技研究入口失败")
        return False

    def _check_idle_and_click_entry(self) -> bool:
        """
        判断入口状态特征是否为「空闲中」：
        - 满足 → 点击入口，跳转战争学院
        - 不满足（已有研究在进行）→ False，调用方判 FAILED 不重试
        """
        full = self._full_screen()

        idle_box = self._find(
            GOLDEN_TECH_RESEARCH_STATUS_IDLE,
            threshold=self.list_icon_threshold, box=full,
        )
        if idle_box is None:
            self.log_info(
                "黄金科技研究入口非「空闲中」（已有研究进行中），"
                "本次跳过不重试"
            )
            return False

        self.log_info("黄金科技研究入口状态为「空闲中」，点击入口")
        self.click(idle_box)
        self._sleep(2.0)
        return True

    def _enter_research_page(self) -> bool:
        """点击 GOLDEN_TECH_ACADEMY（黄金科技-2），打开科技树界面。"""
        full = self._full_screen()
        academy_box = self._safe_box(GOLDEN_TECH_ACADEMY)
        if academy_box is None:
            # 点击入口后可能未成功跳转，等待元素出现
            academy_box = self._wait_element(
                GOLDEN_TECH_ACADEMY,
                timeout=self.page_switch_timeout,
                threshold=self.list_icon_threshold,
                box=full,
                with_recovery=False,
            )
        if academy_box is None:
            self.log_info("未找到战争学院建筑（黄金科技-2）")
            return False
        self.click(academy_box)
        self._sleep(1.5)
        return True

    def _click_research_node(self) -> bool:
        """点击 GOLDEN_TECH_RESEARCH_NODE，定位当前可研究节点。"""
        full = self._full_screen()
        if not self._wait_and_click(
            GOLDEN_TECH_RESEARCH_NODE,
            timeout=self.page_switch_timeout,
            box=full,
        ):
            self.log_info("未找到当前可研究节点（黄金科技研究-3）")
            return False
        self._sleep(1.0)
        return True

    # ========================================================
    # 步骤 2：按兵种优先级选择分支节点
    # ========================================================

    def _select_troop_branch(self) -> bool:
        """
        按配置的兵种优先级在三个分支节点中选择其一点击。

        - sub_tech_names 配置了子项特征名时，按顺序优先匹配子项
          （任意命中即点击）；
        - 子项未命中 → 回退到 troop_priority 对应的默认第 4 步节点。
        """
        full = self._full_screen()

        # 子项优先：coco resource_id 逗号分隔
        names = [
            n.strip() for n in str(self.sub_tech_names or "").split(",")
            if n.strip()
        ]
        for rid in names:
            box = None
            try:
                box = self.get_box_by_name(rid)
            except ValueError:
                box = None
            if box is not None:
                self.log_info(f"命中子项研究特征 {rid}，点击")
                self.picked_node = rid
                self.click(box)
                self._sleep(1.0)
                return True

        # 默认分支节点：按 troop_priority 三选一
        node = self.TROOP_NODE_MAP.get(
            str(self.troop_priority or "弓").strip()
        )
        if node is None:
            self.log_error(
                f"未知兵种优先级: {self.troop_priority}，"
                f"可选 {'/'.join(self.TROOP_NODE_MAP)}"
            )
            return False

        node_box = self._find(
            node, threshold=self.list_icon_threshold, box=full,
        )
        if node_box is None:
            self.log_info(
                f"分支节点 {node.resource_id} 未在界面出现，"
                "尝试其余两个分支"
            )
            for alt in self.TROOP_NODE_MAP.values():
                if alt is node:
                    continue
                alt_box = self._find(
                    alt, threshold=self.list_icon_threshold, box=full,
                )
                if alt_box is not None:
                    node, node_box = alt, alt_box
                    self.log_info(f"回退到分支 {alt.resource_id}")
                    break
        if node_box is None:
            self.log_info("三个分支节点均未匹配到")
            return False

        self.picked_node = node.resource_id
        self.log_info(f"选择分支节点 {node.resource_id}")
        self.click(node_box)
        self._sleep(1.0)
        return True

    # ========================================================
    # 步骤 3：确认研究弹窗 → 点击「研究」
    # ========================================================

    def _confirm_research(self) -> bool:
        """
        弹出研究确认弹窗后，点击
        GOLDEN_TECH_CONFIRM_RESEARCH_BUTTON 中的「研究」按钮。
        （该框内同时含预估研究时长，可选 OCR，本次不做 OCR。）
        """
        full = self._full_screen()
        if not self._wait_and_click(
            GOLDEN_TECH_CONFIRM_RESEARCH_BUTTON,
            timeout=self.page_switch_timeout,
            box=full,
        ):
            self.log_info("研究确认弹窗未出现或未找到「研究」按钮")
            return False
        self._sleep(2.0)
        return True

    # ========================================================
    # 步骤 4：OCR 剩余研究时间 + 联盟互助
    # ========================================================

    def _read_remain_and_help(self) -> bool:
        """
        研究启动后：
        - 等待 GOLDEN_TECH_STATUS_TIME_HELP 底栏出现（研究动画完成）
        - OCR 该区域得到剩余研究时间
        - 点击该区域内的「联盟互助」
        返回 False 时调用方按 default_retry_wait 兜底。
        """
        full = self._full_screen()
        status_box = self._wait_element(
            GOLDEN_TECH_STATUS_TIME_HELP,
            timeout=self.research_start_timeout,
            interval=1.0,
            box=full,
            with_recovery=False,
        )
        if status_box is None:
            self.log_info("研究启动后未出现状态底栏（互助区域）")
            return False

        # OCR 剩余研究时间
        try:
            results = self.ocr(box=status_box)
        except Exception as e:
            self.log_info(f"OCR 剩余研究时间异常: {e}")
            results = None

        seconds = None
        if results:
            text = "".join(r.name for r in results)
            seconds = self._parse_countdown_text(text)
            if seconds is None:
                self.log_info(f"剩余研究时间解析失败: {text}")
        else:
            self.log_info("状态底栏 OCR 无结果")

        # 点击「联盟互助」（区域即互助按钮本体）
        self.click(status_box)
        self._sleep(1.0)

        if seconds is None:
            return False

        wait = max(self.min_wait_seconds, float(seconds))
        self.next_research_time = time.time() + wait
        self.log_info(f"剩余研究时间 {wait:.0f}s，已完成联盟互助")
        return True

    # ========================================================
    # 组合步骤
    # ========================================================

    def _enter_and_confirm_idle(self) -> bool:
        """开列表 → 滚动检索入口 → 空闲判断 → 点入口。"""
        if not self._open_task_list():
            return False

        if not self._scroll_find_research_entry():
            return False

        return self._check_idle_and_click_entry()

    def _research_in_tech_tree(self) -> bool:
        """进战争学院 → 定位可研究节点 → 选分支 → 确认研究。"""
        if not self._enter_research_page():
            return False
        if not self._click_research_node():
            return False
        if not self._select_troop_branch():
            return False
        return self._confirm_research()

    # ========================================================
    # 队列调用入口
    # ========================================================

    def run_interaction(self):
        """
        队列调用入口。只做交互，不做等待。
        返回 (InteractionResult, wait_seconds)，
        wait_seconds = 剩余研究时间。
        """
        self.log_info("========== 开始黄金科技研究 ==========")

        self.ensure_in_front()

        # 清掉所有已知弹窗
        self.popup.close_all()

        # ---- 回到主界面 ----
        success, _scene = self._ensure_main_scene()
        if not success:
            self.last_error = "回到主界面失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        self.next_research_time = None
        self.picked_node = None

        # ---- 开列表 → 检索入口 → 空闲判断 ----
        # 失败不重试：入口非「空闲」（已有研究进行中）或列表无入口
        # 属正常状态，重扫无意义，这里直接判 FAILED。
        if not self._enter_and_confirm_idle():
            self.last_error = (
                "黄金科技研究入口非空闲或任务列表未检索到入口"
            )
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- 进科技树 → 选分支 → 确认研究 ----
        if not self._step(self._research_in_tech_tree, "科技树研究"):
            self.last_error = "科技树研究流程失败"
            self.log_error(self.last_error)
            return (InteractionResult.FAILED, 0)

        # ---- OCR 剩余时间 + 联盟互助 ----
        if not self._read_remain_and_help():
            self.log_warning(
                "剩余研究时间识别失败，"
                f"使用默认间隔 {self.default_retry_wait:.0f}s"
            )
            wait = max(self.min_wait_seconds, float(self.default_retry_wait))
            self.next_research_time = time.time() + wait
        else:
            wait = max(
                self.min_wait_seconds,
                self.next_research_time - time.time(),
            )

        self.info_set("Picked Node", self.picked_node or "-")
        self.info_set("Next Research In", f"{wait / 60:.0f} min")

        # ---- 退回主界面（失败不影响研究启动结果）----
        if not self._step(self._ensure_main_scene, "退回主界面"):
            self.log_info("退回主界面失败，不影响研究启动结果")

        self.log_info(
            f"========== 黄金科技研究完成，"
            f"{wait:.0f}s 后研究完成 =========="
        )
        return (InteractionResult.SUCCESS, wait)

    def check_completed(self) -> bool:
        """
        IN_PROGRESS 到点后的二次校验：
        到达研究完成时间才算完成，让 follower 精准接续。
        """
        if self.next_research_time is None:
            return True
        return time.time() >= self.next_research_time - 1.0

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
        self.log_info("========== 黄金科技研究（独立模式） ==========")

        result, wait_seconds = self.run_interaction()

        if result == InteractionResult.FAILED:
            self.log_error(f"黄金科技研究失败: {self.last_error}")
            return False

        if result == InteractionResult.RETRY:
            self.log_info(f"暂不可执行，{wait_seconds:.1f}s 后结束")
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            return False

        self.log_info(
            f"========== 黄金科技研究完成，"
            f"{wait_seconds:.0f}s 后研究完成 =========="
        )
        return True
