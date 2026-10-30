# -*- coding: utf-8 -*-
"""
TaskConfigEditorTab —— 任务配置的原生 GUI 编辑页（qfluentwidgets）。

双配置文件（结构契约与 task_config_loader.py 对齐）：
  configs/task_type.json   定义段（[{package, task_types[]}]，跟软件绑定）
  configs/task_config.json 实例段（version + machines 多设备/应用/分身链）

功能对标 tools/task_config_editor.html（网页版）：
  - 三个页签：任务实例 / 类型定义 / JSON 源码
    （设备链为低频操作，不单设页签；machines 通过 JSON 源码页编辑）
  - 类型定义页编辑当前链 app 的 package 对应分组；实例页渲染的
    任务描述 / 参数描述都来自 task_type.json 定义段
  - 校验器镜像 loader 规则（type/id 重复 / 引用未定义类型 / params 键
    ⊆ 定义 / widget 白名单 / enabled 链存在），保存前提示
  - 保存：Ctrl+S 或按钮写回两份 JSON（写前各留 .bak 备份）
  - 方案：仅保存当前链启用任务的 {id, params}（app 级语义）；
    应用方案按目标用户已有任务范围交集覆盖
"""
import copy
import json
import re
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CardWidget, CheckBox, ComboBox, Dialog, FluentIcon,
    InfoBar, LineEdit, PlainTextEdit, Pivot, PrimaryPushButton, PushButton,
    ScrollArea, StrongBodyLabel, SubtitleLabel, SwitchButton,
    TransparentPushButton, TransparentToolButton, isDarkTheme,
)

from ok import Config
from ok.ui.qt.widget.CustomTab import CustomTab

from src.sg.tasks.config_loader.task_config_loader import (
    DEFAULT_CONFIG_PATH, DEFAULT_TYPE_PATH, PARAM_WIDGETS,
)

DEFAULT_KEYS = (
    "count", "max_active", "requires_march_queue",
    "next_trigger_delay", "one_shot", "cron", "max_failures",
)
DKEY_HELP = {
    "count": "任务总执行次数，0 表示不限",
    "max_active": "同时运行的最大实例数",
    "requires_march_queue": "执行时是否占用军队出征队列",
    "next_trigger_delay": "执行完成后到下次触发的间隔秒数",
    "cron": "Cron 表达式定时触发，留空表示不使用",
    "max_failures": "最大失败次数：该类型失败达到此次数后不再补建（0 = 回退队列级）",
    "one_shot": "只运行一次"
}

def _fmt_val(v):
    if isinstance(v, bool):
        return "开" if v else "关"
    if isinstance(v, list):
        return "/".join(map(str, v)) if v else "-"
    if v is None:
        return "-"
    return str(v)

def _new_type_defaults():
    return {"count": 1, "max_active": 1, "requires_march_queue": False,
            "next_trigger_delay": 86400, "one_shot": False, "cron": ""}

def validate_doc(doc, type_groups):
    """镜像 loader 的结构校验（实例段 doc + 定义段分组 type_groups），
    返回错误字符串列表（空列表 = 通过）。

    type_groups: {package: task_types list}（load_type_document 返回结构）。
    每个 app 的 tasks 只允许引用其 package 分组里定义的类型。
    """
    errs = []
    if not isinstance(doc, dict):
        return ["实例配置顶层必须是对象"]
    if not isinstance(type_groups, dict) or not type_groups:
        return ["task_type.json 缺失或为空"]
    # 分组内校验（type/id 重复、defaults、params）+ 跨组 type/id 唯一性
    group_seen = {}      # (pkg, "type"/"id", 值) -> pkg
    for pkg, tt in type_groups.items():
        if not isinstance(tt, list):
            errs.append(f"task_type.json[{pkg}]: task_types 必须是数组")
            continue
        seen = set()
        id_to_type = {}
        for i, t in enumerate(tt):
            w = f"task_type.json[{pkg}].task_types[{i}]"
            if not isinstance(t, dict):
                errs.append(f"{w}: 条目必须是对象")
                continue
            ttype = t.get("type")
            if not ttype:
                errs.append(f"{w}: 缺 type")
            elif ttype in seen:
                errs.append(f'{w}: type 重复 "{ttype}"')
            else:
                seen.add(ttype)
            tid = t.get("id")
            if not tid or not isinstance(tid, str):
                errs.append(f"{w}: 缺 id 或 id 非字符串")
            elif tid in id_to_type:
                errs.append(f'{w}: id 重复 "{tid}"')
            else:
                id_to_type[tid] = ttype
            est = t.get("estimated_seconds", 0)
            if isinstance(est, bool) or not isinstance(est, (int, float)) or est < 0:
                errs.append(f"{w}.estimated_seconds 必须是非负数: {est!r}")
            d = t.get("defaults")
            if not isinstance(d, dict):
                errs.append(f"{w}.defaults 缺失")
            else:
                for k in DEFAULT_KEYS:
                    if k not in d:
                        errs.append(f"{w}.defaults 缺字段 {k}")
                if "cron" in d and not isinstance(d.get("cron"), str):
                    errs.append(f"{w}.defaults.cron 必须是字符串")
            attrs = set()
            for pi, p in enumerate(t.get("params") or []):
                if not isinstance(p, dict):
                    errs.append(f"{w}.params[{pi}]: 必须是对象")
                    continue
                if not p.get("name") or not p.get("attr"):
                    errs.append(f"{w}.params[{pi}]: 缺 name/attr")
                attr = p.get("attr")
                if attr:
                    if attr in attrs:
                        errs.append(f'{w}.params attr 重复 "{attr}"')
                    attrs.add(attr)
                wd = p.get("widget")
                if wd is not None:
                    if not isinstance(wd, dict) or wd.get("type") not in PARAM_WIDGETS:
                        errs.append(f"{w}.params[{pi}].widget 非法: {wd!r}")
            for key, val in (("type", ttype), ("id", tid)):
                if val:
                    gkey = (key, val)
                    if gkey in group_seen:
                        errs.append(
                            f'task_type.json: {key} "{val}" 在多个 package 组重复: '
                            f'{group_seen[gkey]} 与 {pkg}')
                    else:
                        group_seen[gkey] = pkg
    # 实例段校验
    machines = doc.get("machines")
    if not isinstance(machines, list) or not machines:
        errs.append("machines 缺失或为空")
    rm = doc.get("rotation_mode")
    if rm is not None and rm not in ("task_flow", "user_rotation"):
        errs.append(f'rotation_mode 必须是 task_flow / user_rotation: "{rm}"')
    has_chain = False
    for mi, m in enumerate(machines or []):
        if not isinstance(m, dict):
            errs.append(f"machines[{mi}]: 必须是对象")
            continue
        mw = f'machines[{mi}]({m.get("name") or "未命名"})'
        for ai, a in enumerate(m.get("apps") or []):
            if not isinstance(a, dict):
                errs.append(f"{mw}.apps[{ai}]: 必须是对象")
                continue
            pkg = a.get("package")
            defs = type_groups.get(pkg)
            if not isinstance(defs, list) or not defs:
                errs.append(f"{mw}.apps[{ai}]: task_type.json 缺少 package 分组 "
                            f'"{pkg}"')
            id_to_type = {}
            for t in defs or []:
                if isinstance(t, dict) and t.get("id"):
                    id_to_type[t["id"]] = t.get("type")
            local_types = {t.get("type") for t in defs or [] if isinstance(t, dict)}
            for ui_, u in enumerate(a.get("users") or []):
                if not isinstance(u, dict):
                    errs.append(f"{mw}.apps[{ai}].users[{ui_}]: 必须是对象")
                    continue
                if m.get("enabled") and a.get("enabled") and u.get("enabled"):
                    has_chain = True
                for ti, t in enumerate(u.get("tasks") or []):
                    if not isinstance(t, dict):
                        errs.append(f"{mw} users[{ui_}] tasks[{ti}]: 必须是对象")
                        continue
                    tid = t.get("id")
                    ttype = t.get("type")
                    if tid:
                        if tid not in id_to_type:
                            errs.append(
                                f'{mw} users[{ui_}] tasks[{ti}]: 引用未定义 id "{tid}"')
                        else:
                            resolved = id_to_type[tid]
                            if ttype and ttype != resolved:
                                errs.append(
                                    f'{mw} users[{ui_}] tasks[{ti}]: '
                                    f'id "{tid}" 与 type "{ttype}" 不一致')
                            ttype = resolved
                    elif ttype not in local_types:
                        errs.append(
                            f'{mw} users[{ui_}] tasks[{ti}]: 引用未定义 type "{ttype}"')
                    attrs = set()
                    for x in defs or []:
                        if isinstance(x, dict) and x.get("type") == ttype:
                            attrs = {p.get("attr") for p in (x.get("params") or [])
                                     if isinstance(p, dict)}
                            break
                    for k in (t.get("params") or {}):
                        if k not in attrs:
                            errs.append(f'{mw} users[{ui_}] tasks[{ti}]: 未知参数 "{k}"')
    if not has_chain:
        errs.append("没有任何 enabled 的 machine/app/user 链")
    return errs


class TaskConfigEditorTab(CustomTab):
    """qfluentwidgets 原生实现的任务配置编辑页。"""

    BACKUP_SUFFIX = ".bak"

    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.SETTING
        self.config = Config(self.__class__.__name__, {})
        self.logger.info(f"TaskConfigEditorTab init {self.__class__.__name__}")

        self.doc = None            # 实例段（version + machines）
        self.type_groups = {}      # 定义段 {package: task_types list}
        self.type_order = []       # task_type.json 的分组顺序（package 列表）
        self.types_dirty = False   # 定义段是否未保存
        self.cur_type_pkg = None   # 类型定义页当前编辑的 package 分组
        self.dirty = False
        self.sel = {"m": 0, "a": 0, "u": 0}
        self.expanded = set()      # 任务卡: (m, a, u, type)
        self.expanded_types = set()  # ("T", type)
        self.filter_text = ""
        self._suspend = 0          # >0 时静默 UI 信号（批量刷新）
        self._card_cache = {}      # type -> 卡片 widget（同链内复用，挪位置不重建）
        self._card_chain = None
        self._stale_pages = set()  # 懒加载: 尚未构建内容的页
        self._build_ui()
        self._reload_from_disk(show_info=False)

    @property
    def name(self):
        return "Task Config"

    # ================================================================ UI
    def _build_ui(self):
        self.view_lay = self.vBoxLayout  # Tab 基类的主布局
        # 顶部：标题 + 状态 + 保存/还原
        top = QWidget(self.view)
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        left.addWidget(SubtitleLabel("任务配置维护"))
        self.lbl_status = BodyLabel("未加载配置")
        self.lbl_status.setToolTip("")
        left.addWidget(self.lbl_status)
        top_lay.addLayout(left, 1)
        self.btn_save_profile = PushButton(FluentIcon.SAVE_AS, "存为方案")
        self.btn_save_profile.clicked.connect(self._save_profile_dialog)
        self.btn_load_profile = PushButton(FluentIcon.FOLDER, "加载方案")
        self.btn_load_profile.clicked.connect(self._load_profile_dialog)
        self.btn_reload = PushButton(FluentIcon.SYNC, "还原")
        self.btn_reload.clicked.connect(self._confirm_reload)
        self.btn_save = PrimaryPushButton(FluentIcon.SAVE, "保存")
        self.btn_save.clicked.connect(self._save)
        top_lay.addWidget(self.btn_save_profile)
        top_lay.addWidget(self.btn_load_profile)
        top_lay.addWidget(self.btn_reload)
        top_lay.addWidget(self.btn_save)

        self.pivot = Pivot(self.view)
        self.pivot.addItem(routeKey="tasks", text="任务实例",
                           onClick=self._on_pivot_tasks)
        self.pivot.addItem(routeKey="types", text="类型定义",
                           onClick=self._on_pivot_types)
        self.pivot.addItem(routeKey="machines", text="设备链",
                           onClick=self._on_pivot_machines)
        self.pivot.addItem(routeKey="source", text="JSON 源码",
                           onClick=self._on_pivot_source)
        self.pivot.setCurrentItem("tasks")

        self.view_lay.addWidget(top)
        self.view_lay.addWidget(self.pivot)
        self.page_tasks = self._build_tasks_page()
        self.page_types = self._build_types_page()
        self.page_machines = self._build_machines_page()
        self.page_source = self._build_source_page()
        for p in (self.page_tasks, self.page_types, self.page_machines,
                  self.page_source):
            p.setVisible(False)
            self.view_lay.addWidget(p)
        self.page_tasks.setVisible(True)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save)

    # ---- 任务实例页 ----
    def _build_tasks_page(self):
        page = QWidget(self.view)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        self.c_m, self.c_a, self.c_u = ComboBox(), ComboBox(), ComboBox()
        for i, cb in enumerate((self.c_m, self.c_a, self.c_u)):
            cb.setMinimumWidth(140)
            cb.currentIndexChanged.connect(lambda _i, k=i: self._on_chain_changed(k))
            bar.addWidget(cb)
        self.lbl_chain_hint = BodyLabel("")
        bar.addWidget(self.lbl_chain_hint)
        bar.addStretch(1)
        self.btn_batch_enable = PushButton(FluentIcon.CHECKBOX, "批量启用")
        self.btn_batch_enable.clicked.connect(self._batch_enable_dialog)
        bar.addWidget(self.btn_batch_enable)
        self.btn_reset_user = PushButton(FluentIcon.SYNC, "重置任务参数")
        self.btn_reset_user.setToolTip(
            "把当前包、当前用户下全部任务重置：停用 + 参数回到定义段默认值")
        self.btn_reset_user.clicked.connect(self._reset_user_tasks)
        bar.addWidget(self.btn_reset_user)
        self.search = LineEdit()
        self.search.setPlaceholderText("搜索任务…")
        bar.addWidget(self.search)
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(170)
        self.search.textChanged.connect(self._on_filter)
        self.btn_add_task = PrimaryPushButton(FluentIcon.ADD, "添加任务")
        self.btn_add_task.clicked.connect(self._add_task_dialog)
        bar.addWidget(self.btn_add_task)
        lay.addLayout(bar)

        # 队列设置（user 级）
        queue_card = CardWidget(self.view)
        ql = QVBoxLayout(queue_card)
        ql.setContentsMargins(14, 10, 14, 10)
        ql.setSpacing(4)
        self.lbl_queue_title = StrongBodyLabel("队列设置")
        ql.addWidget(self.lbl_queue_title)
        self.tick_edit = LineEdit()
        self.tick_edit.setFixedWidth(120)
        self.tick_edit.textChanged.connect(self._queue_changed)
        ql.addWidget(self._field_row("tick 间隔(秒)", "调度器轮询间隔", self.tick_edit))
        self.caf_switch = SwitchButton("失败后继续")
        self.caf_switch.checkedChanged.connect(self._queue_changed)
        ql.addWidget(self._field_row("失败后继续", "任务失败后是否继续调度后续任务", self.caf_switch))
        self.mf_edit = LineEdit()
        self.mf_edit.setFixedWidth(120)
        self.mf_edit.textChanged.connect(self._queue_changed)
        ql.addWidget(self._field_row(
            "最大失败次数", "同一任务类型失败达到此次数后不再补建（0 = 不限）",
            self.mf_edit))
        lay.addWidget(queue_card)

        self.lbl_count = BodyLabel("")
        self.lbl_count._is_count_label = True
        lay.addWidget(self.lbl_count)

        self.task_scroll = ScrollArea(self.view)
        self.task_scroll.setWidgetResizable(True)
        self.task_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.task_scroll.enableTransparentBackground()
        self.task_list = QWidget()
        self.task_list_lay = QVBoxLayout(self.task_list)
        self.task_list_lay.setContentsMargins(0, 0, 0, 0)
        self.task_list_lay.setSpacing(8)
        self.task_scroll.setWidget(self.task_list)
        lay.addWidget(self.task_scroll, 1)
        return page

    # ---- 类型定义页 ----
    def _build_types_page(self):
        page = QWidget(self.view)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)
        bar = QHBoxLayout()
        bar.addWidget(BodyLabel("类型定义是实例的模板：defaults 兜底覆盖链，params 决定实例页控件"))
        bar.addStretch(1)
        self.btn_add_type = PrimaryPushButton(FluentIcon.ADD, "添加类型")
        self.btn_add_type.clicked.connect(self._add_type_dialog)
        bar.addWidget(self.btn_add_type)
        lay.addLayout(bar)

        self.type_scroll = ScrollArea(self.view)
        self.type_scroll.setWidgetResizable(True)
        self.type_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.type_scroll.enableTransparentBackground()
        self.type_list = QWidget()
        self.type_list_lay = QVBoxLayout(self.type_list)
        self.type_list_lay.setContentsMargins(0, 0, 0, 0)
        self.type_list_lay.setSpacing(8)
        self.type_scroll.setWidget(self.type_list)
        lay.addWidget(self.type_scroll, 1)
        return page

    # ---- 设备链页 ----
    def _build_machines_page(self):
        page = QWidget(self.view)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)
        bar = QHBoxLayout()
        bar.addWidget(BodyLabel("运行时消费第一条全部启用的 machine → app → user 链"))
        bar.addStretch(1)
        # 5.7 多用户任务轮转模式（顶层 rotation_mode）
        bar.addWidget(BodyLabel("多用户任务轮转模式:"))
        self.rotation_mode_combo = ComboBox()
        self.rotation_mode_combo.addItems(
            ["任务间流转（默认）", "完全轮转"])
        self.rotation_mode_combo.currentIndexChanged.connect(
            self._on_rotation_mode_changed)
        bar.addWidget(self.rotation_mode_combo)
        bar.addSpacing(12)
        self.btn_add_machine = PrimaryPushButton(FluentIcon.ADD, "添加机器")
        self.btn_add_machine.clicked.connect(self._add_machine_dialog)
        bar.addWidget(self.btn_add_machine)
        lay.addLayout(bar)
        self.machine_scroll = ScrollArea(self.view)
        self.machine_scroll.setWidgetResizable(True)
        self.machine_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.machine_scroll.enableTransparentBackground()
        self.machine_list = QWidget()
        self.machine_list_lay = QVBoxLayout(self.machine_list)
        self.machine_list_lay.setContentsMargins(0, 0, 0, 0)
        self.machine_list_lay.setSpacing(8)
        self.machine_scroll.setWidget(self.machine_list)
        lay.addWidget(self.machine_scroll, 1)
        return page

    # ---- JSON 源码页 ----
    def _build_source_page(self):
        page = QWidget(self.view)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)
        bar = QHBoxLayout()
        self.lbl_src_hint = BodyLabel("与当前数据一致")
        bar.addWidget(self.lbl_src_hint)
        bar.addStretch(1)
        self.btn_src_apply = PrimaryPushButton(FluentIcon.ACCEPT, "校验并应用")
        self.btn_src_apply.clicked.connect(self._apply_source)
        self.btn_src_refresh = PushButton(FluentIcon.SYNC, "从当前数据刷新")
        self.btn_src_refresh.clicked.connect(self._refresh_source)
        self.btn_src_copy = PushButton(FluentIcon.COPY, "复制全部")
        self.btn_src_copy.clicked.connect(self._copy_source)
        bar.addWidget(self.btn_src_apply)
        bar.addWidget(self.btn_src_refresh)
        bar.addWidget(self.btn_src_copy)
        lay.addLayout(bar)
        self.src_edit = PlainTextEdit()
        self.src_edit.setMinimumHeight(320)
        self.src_edit.textChanged.connect(self._on_source_changed)
        lay.addWidget(self.src_edit, 1)
        return page

    # ============================================================ 小部件
    def _field_row(self, label, help_text, widget, diff_ref=None,
                   fixed_width=None):
        """标签(右对齐, 150px) + 控件 + 差异圆点 的水平行，返回容器 widget。

        返回 widget 而非 layout，便于直接放入 QGridLayout。
        """
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = BodyLabel(label)
        lab.setToolTip(help_text or "")
        lab.setFixedWidth(150)
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(lab)
        if fixed_width:
            widget.setFixedWidth(fixed_width)
        lay.addWidget(widget)
        lay.addStretch(1)
        if diff_ref is not None:
            get, default = diff_ref
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet("border-radius:4px;background:#f59e0b;")
            dot.setToolTip("与类型默认值不同")
            try:
                dot.setVisible(get() != default)
            except Exception:
                dot.setVisible(False)
            lay.addWidget(dot)
            lay.addSpacing(8)
            host._diff_dot = dot
            host._diff_get = get
            host._diff_default = default
        return host

    def _sec_header(self, text, extra=""):
        """区块标题: 强调字 + 灰色补充说明, 左侧 4px 强调条。"""
        wrap = QFrame()
        wrap.setObjectName("_SecHeader")
        hl = QHBoxLayout(wrap)
        hl.setContentsMargins(0, 2, 0, 2)
        bar = QFrame()
        bar.setFixedSize(3, 14)
        bar.setStyleSheet(
            "border-radius:1px;background:#009faa;")
        hl.addWidget(bar)
        hl.addSpacing(4)
        hl.addWidget(StrongBodyLabel(text))
        if extra:
            ex = BodyLabel(extra)
            ex.setStyleSheet("color:gray;")
            hl.addWidget(ex)
        hl.addStretch(1)
        return wrap

    def _param_box(self, title, widget, help_text, diff_ref=None):
        """单个参数的圆角单元格: 标题行(名称+差异点) + 控件 + 描述。"""
        box = QFrame()
        box.setObjectName("_ParamBox")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.setSpacing(4)
        title_lay = QHBoxLayout()
        t = StrongBodyLabel(title)
        t.setToolTip(help_text or "")
        title_lay.addWidget(t)
        title_lay.addStretch(1)
        if diff_ref is not None:
            get, default = diff_ref
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet("border-radius:4px;background:#f59e0b;")
            dot.setToolTip("与类型默认值不同")
            try:
                dot.setVisible(get() != default)
            except Exception:
                dot.setVisible(False)
            title_lay.addWidget(dot)
            box._dot_refresh = lambda: dot.setVisible(get() != default)
        bl.addLayout(title_lay)
        bl.addWidget(widget)
        return box

    @staticmethod
    def _style_param_boxes(parent):
        """在共同容器上设置一次按 objectName 匹配的 QSS，
        自动作用于现有与之后创建的 _ParamBox，避免逐卡 findChildren+setStyleSheet。"""
        dark = isDarkTheme()
        bg = "#2b2b2b" if dark else "#f5f7fa"
        border = "#3a3a3a" if dark else "#e0e3e8"
        root = parent
        while root.parentWidget() is not None:
            root = root.parentWidget()
        if getattr(root, "_parambox_styled", False):
            return
        root._parambox_styled = True
        root.setStyleSheet(root.styleSheet() +
            f"QFrame#_ParamBox{{background:{bg};border:1px solid {border};"
            f"border-radius:6px;}}")

    @staticmethod
    def _clear_layout(lay):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            else:
                sub = it.layout()
                if sub is not None:
                    while sub.count():
                        sit = sub.takeAt(0)
                        sw = sit.widget()
                        if sw is not None:
                            sw.deleteLater()
                    sub.deleteLater()

    # ============================================================ 数据
    def _reload_from_disk(self, show_info=True):
        cfg_path = Path(DEFAULT_CONFIG_PATH)
        type_path = Path(DEFAULT_TYPE_PATH)
        self.type_groups = {}
        self.type_order = []
        self.doc = None
        ok = True
        if type_path.exists():
            try:
                groups = json.loads(type_path.read_text(encoding="utf-8"))
                for g in groups or []:
                    pkg = g.get("package")
                    self.type_groups[pkg] = g.get("task_types") or []
                    self.type_order.append(pkg)
            except Exception as e:
                self.logger.error(f"读取 {type_path} 失败: {e}")
                self.lbl_status.setText(f"读取定义失败: {e}")
                ok = False
        else:
            self.lbl_status.setText(f"定义文件不存在: {type_path}")
            ok = False
        if cfg_path.exists():
            try:
                self.doc = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception as e:
                self.logger.error(f"读取 {cfg_path} 失败: {e}")
                self.lbl_status.setText(f"读取失败: {e}")
                ok = False
        else:
            self.lbl_status.setText(f"实例文件不存在: {cfg_path}")
            ok = False
        if self.doc:
            self._normalize_group_order()
        self.dirty = False
        self.types_dirty = False
        self._src_dirty = False
        self._refresh_source()
        self._sync_all()
        if show_info and ok:
            InfoBar.success("已加载", f"{cfg_path.name} + {type_path.name}",
                            parent=self.window(), duration=2000)

    def _normalize_group_order(self):
        """把每个 user 的 tasks 稳定重排为「已启用块在前，未启用块在后」。

        只在加载/应用文档时执行一次（组内保持原相对顺序）；
        之后的启用/停用由 _task_enabled 增量归位。
        """
        if not self.doc:
            return
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    tasks = u.get("tasks") or []
                    tasks.sort(key=lambda t: 0 if t.get("enabled") else 1)

    def _dump(self):
        return json.dumps(self.doc, ensure_ascii=False, indent=2) + "\n"

    def _dump_types(self):
        """task_type.json 序列化：保持文件里的分组顺序。"""
        return json.dumps(
            [{"package": pkg, "task_types": self.type_groups.get(pkg) or []}
             for pkg in self.type_order],
            ensure_ascii=False, indent=2) + "\n"

    def _cur_pkg(self):
        """当前选中链 app 的 package；无链返回 None。"""
        chain = self._chain()
        return chain[1].get("package") if chain else None

    def _pkg_types(self, pkg):
        """package → 定义段列表；未分组返回 []。"""
        return self.type_groups.get(pkg) or []

    def _chain(self):
        """当前选中链 (m, a, u)；越界返回 None。"""
        if not self.doc:
            return None
        ms = self.doc.get("machines") or []
        m = ms[self.sel["m"]] if self.sel["m"] < len(ms) else None
        if not isinstance(m, dict):
            return None
        apps = m.get("apps") or []
        a = apps[self.sel["a"]] if self.sel["a"] < len(apps) else None
        if not isinstance(a, dict):
            return None
        users = a.get("users") or []
        u = users[self.sel["u"]] if self.sel["u"] < len(users) else None
        if not isinstance(u, dict):
            return None
        return m, a, u

    def _clamp_sel(self):
        d = self.doc
        if not d:
            self.sel = {"m": 0, "a": 0, "u": 0}
            return
        ms = d.get("machines") or []
        self.sel["m"] = max(0, min(self.sel["m"], len(ms) - 1)) if ms else 0
        apps = (ms[self.sel["m"]].get("apps") if ms else None) or []
        self.sel["a"] = max(0, min(self.sel["a"], len(apps) - 1)) if apps else 0
        users = (apps[self.sel["a"]].get("users") if apps else None) or []
        self.sel["u"] = max(0, min(self.sel["u"], len(users) - 1)) if users else 0

    def _type_def(self, type_):
        return self._type_def_in(self._cur_pkg(), type_)

    def _type_def_in(self, pkg, type_):
        for t in self._pkg_types(pkg):
            if isinstance(t, dict) and t.get("type") == type_:
                return t
        return None

    def _task_def(self, task):
        """实例条目 → 定义段：优先按 id 反查；旧条目退回 type 查找。

        只在当前链 app 的 package 分组里查（定义段按软件作用域）。
        """
        pkg = self._cur_pkg()
        tid = task.get("id")
        if tid:
            for t in self._pkg_types(pkg):
                if isinstance(t, dict) and t.get("id") == tid:
                    return t
        return self._type_def_in(pkg, task.get("type"))

    def _mark_dirty(self):
        self.dirty = True
        self._refresh_status()

    # ============================================================ 轮转模式（5.7）
    def _on_rotation_mode_changed(self, index):
        if self._suspended or not self.doc:
            return
        self.doc["rotation_mode"] = (
            "user_rotation" if index == 1 else "task_flow")
        self._mark_dirty()

    def _sync_rotation_mode_ui(self):
        """把 doc.rotation_mode 同步到下拉（不触发信号）。"""
        with self._suspend_signals():
            mode = (self.doc or {}).get("rotation_mode", "task_flow")
            self.rotation_mode_combo.setCurrentIndex(
                1 if mode == "user_rotation" else 0)

    def _suspend_signals(self):
        """Suspend signal-driven edits during full-list rebuild."""
        class _Ctx:
            def __enter__(self_inner):
                self._suspend += 1
                return self_inner

            def __exit__(self_inner, *exc):
                self._suspend -= 1
        return _Ctx()

    @property
    def _suspended(self):
        return self._suspend > 0

    # ============================================================ 状态栏
    def _refresh_status(self):
        if not self.doc:
            self.lbl_status.setText("未加载配置")
            self.btn_save.setEnabled(False)
            self.btn_reload.setEnabled(False)
            return
        errs = validate_doc(self.doc, self.type_groups)
        flag = ""
        if self.dirty and self.types_dirty:
            flag = " ● 实例+定义 未保存"
        elif self.dirty:
            flag = " ● 未保存"
        elif self.types_dirty:
            flag = " ● 定义未保存"
        self.lbl_status.setText(
            f"task_config + task_type{flag} ｜ 校验: {len(errs)} 个问题")
        self.lbl_status.setToolTip("\n".join(errs[:10]))
        self.btn_save.setEnabled(True)
        self.btn_reload.setEnabled(True)

    def _sync_all(self, keep_cards=False):
        """文档级变更后的同步：任务页立即刷（当前页），其余页标脏等切换时再建。

        keep_cards=True 时复用任务卡片（同链数据原位更新场景）。
        """
        self._clamp_sel()
        self._refresh_status()
        self._refresh_chain_combos()
        self._stale_pages = {"types", "machines", "source"}
        self._refresh_tasks_page(keep_cards=keep_cards)
        self._stale_pages.discard("tasks")

    # ==================================================== 任务实例页渲染
    def _refresh_chain_combos(self):
        combos = (self.c_m, self.c_a, self.c_u)
        for cb in combos:
            cb.blockSignals(True)
            cb.clear()
        d = self.doc
        if d:
            ms = d.get("machines") or []
            for i, m in enumerate(ms):
                self.c_m.addItem(
                    f'{(m or {}).get("name") or f"machine{i}"}'
                    + ("" if (m or {}).get("enabled") else "（未启用）"))
            m = ms[self.sel["m"]] if self.sel["m"] < len(ms) else {}
            apps = (m or {}).get("apps") or []
            for i, a in enumerate(apps):
                self.c_a.addItem(
                    f'{(a or {}).get("package") or "(未设包名)"}'
                    + ("" if (a or {}).get("enabled") else "（未启用）"))
            a = apps[self.sel["a"]] if self.sel["a"] < len(apps) else {}
            users = (a or {}).get("users") or []
            for i, u in enumerate(users):
                self.c_u.addItem(
                    f'user {(u or {}).get("user_id", i)}'
                    + ("" if (u or {}).get("enabled") else "（未启用）"))
            self.c_m.setCurrentIndex(self.sel["m"])
            self.c_a.setCurrentIndex(self.sel["a"])
            self.c_u.setCurrentIndex(self.sel["u"])
            m_en = bool((m or {}).get("enabled"))
            a_en = bool((apps[self.sel["a"]] or {}).get("enabled")) if self.sel["a"] < len(apps) else False
            u_en = bool((users[self.sel["u"]] or {}).get("enabled")) if self.sel["u"] < len(users) else False
            ok = m_en and a_en and u_en
            self.lbl_chain_hint.setText(
                "生效链" if ok else "当前链未全部启用，运行时不生效")
        for cb in combos:
            cb.blockSignals(False)

    def _on_chain_changed(self, which):
        if self._suspended:
            return
        cb = (self.c_m, self.c_a, self.c_u)[which]
        key = ("m", "a", "u")[which]
        idx = max(0, cb.currentIndex())
        if self.sel.get(key) == idx:
            return
        self.sel[key] = idx
        self._clamp_sel()
        self._refresh_chain_combos()
        self._refresh_tasks_page()

    def _on_filter(self, text):
        self.filter_text = text or ""
        self._refresh_tasks_page()

    def _refresh_tasks_page(self, keep_cards=False):
        """重建任务列表。keep_cards=True 时复用已构建的卡片（链未变时挪位置用）。"""
        with self._suspend_signals():
            chain = self._chain()
            if not chain:
                self._clear_layout(self.task_list_lay)
                self._card_cache = {}
                empty = BodyLabel("未找到设备 / 应用 / 用户链，请到「JSON 源码」页编辑 machines")
                empty.setAlignment(Qt.AlignCenter)
                self.task_list_lay.addWidget(empty)
                self.btn_add_task.setEnabled(False)
                self.lbl_queue_title.setText("队列设置")
                return
            m, a, u = chain
            self.btn_add_task.setEnabled(True)

            q = u.get("queue") or {}
            self.lbl_queue_title.setText(
                f'队列设置（user {u.get("user_id", "?")}） · '
                f'tick {q.get("tick_interval", 1)}s · 失败后'
                f'{"继续" if q.get("continue_after_failure") is not False else "停止"}')
            self.tick_edit.setText(str(q.get("tick_interval", 1)))
            self.mf_edit.setText(str(q.get("max_failures", 0)))
            self.caf_switch.setChecked(q.get("continue_after_failure") is not False)

            chain_id = (self.sel["m"], self.sel["a"], self.sel["u"])
            if not keep_cards or self._card_chain != chain_id:
                self._clear_layout(self.task_list_lay)
                self._card_cache = {}
                self._card_chain = chain_id

            tasks = u.get("tasks") or []
            n_en = sum(1 for t in tasks if t.get("enabled"))
            self.lbl_count.setText(f"{len(tasks)} 个任务 · {n_en} 已启用")
            self.lbl_count.setVisible(True)

            # take 出当前布局全部 item 分类复用（卡片不销毁，只挪位置）
            widgets = []
            spacer = None
            while self.task_list_lay.count():
                it = self.task_list_lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    widgets.append(w)
                elif it.spacerItem() is not None:
                    spacer = it
            lbl_count = next((w for w in widgets if getattr(w, "_is_count_label", False)), None)
            hdr_en = next((w for w in widgets if w.objectName() == "_GroupHeaderEn"), None)
            hdr_dis = next((w for w in widgets if w.objectName() == "_GroupHeaderDis"), None)
            cards = {w._task_type: w for w in widgets if hasattr(w, "_task_type")}
            unknown = [w for w in widgets if w.objectName() == "_UnknownTypeCard"]

            self._card_cache = cards
            # 重建布局
            if lbl_count is not None:
                self.task_list_lay.addWidget(lbl_count)
            if en_idx := [i for i, t in enumerate(tasks) if t.get("enabled")]:
                if hdr_en is None:
                    hdr_en = self._group_header("已启用", "_GroupHeaderEn")
                self.task_list_lay.addWidget(hdr_en)
                for i in en_idx:
                    self.task_list_lay.addWidget(self._card_for(tasks, i, cards))
            if dis_idx := [i for i, t in enumerate(tasks) if not t.get("enabled")]:
                if hdr_dis is None:
                    hdr_dis = self._group_header("未启用", "_GroupHeaderDis")
                self.task_list_lay.addWidget(hdr_dis)
                for i in dis_idx:
                    self.task_list_lay.addWidget(self._card_for(tasks, i, cards))
            for w in unknown:
                self.task_list_lay.addWidget(w)
            if spacer is not None:
                self.task_list_lay.addItem(spacer)
            else:
                self.task_list_lay.addStretch(1)
            # 挪卡/重建后强制重算（不动卡片几何，宽度仍由布局铺满）
            self.task_list_lay.activate()

    def _group_header(self, text, objname):
        h = self._sec_header(text)
        h.setObjectName(objname)
        return h

    def _card_for(self, tasks, i, cards):
        task = tasks[i]
        tt = self._task_def(task)
        if not tt:
            bad = CardWidget()
            bad.setObjectName("_UnknownTypeCard")
            bl = QHBoxLayout(bad)
            bl.addWidget(BodyLabel(
                f'未知任务: id={task.get("id")!r} type={task.get("type")!r}'
                f'（定义缺失，运行会报错）'))
            return bad
        key = task.get("id") or task.get("type")
        card = cards.get(key)
        if card is None:
            card = self._task_card(task, tt, i)
            card._task_type = key
            cards[key] = card  # 同步进缓存（cards 即 self._card_cache）
        else:
            refresh = getattr(card, "_refresh_state", None)
            if refresh:
                refresh()
        # 搜索过滤: 用 setVisible 而不是跳过, 保持缓存完整
        hay = " ".join(filter(None, [
            tt.get("name_prefix"), task.get("id"), task.get("type"),
            tt.get("description")])).lower()
        card.setVisible(not self.filter_text or self.filter_text.lower() in hay)
        return card


    def _task_card(self, task, tt, index):
        key = (self.sel["m"], self.sel["a"], self.sel["u"],
               task.get("id") or task.get("type"))
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # 头部：标题/描述/统计 + 启用开关 + 展开按钮
        head = QHBoxLayout()
        info = QVBoxLayout()
        info.addWidget(StrongBodyLabel(tt.get("name_prefix") or tt.get("type") or "?"))
        if tt.get("description"):
            desc = BodyLabel(tt["description"])
            desc.setWordWrap(False)
            info.addWidget(desc)
        stats = BodyLabel(self._task_stats(task, tt))
        stats.setWordWrap(False)
        info.addWidget(stats)
        head.addLayout(info, 1)
        sw = SwitchButton("启用")
        sw.setChecked(bool(task.get("enabled")))
        sw.checkedChanged.connect(lambda on, t=task: self._task_enabled(t, on))
        head.addWidget(sw)
        body = self._task_body(task, tt, index)
        open_ = key in self.expanded
        btn = TransparentToolButton(FluentIcon.CARE_UP_SOLID if open_ else FluentIcon.CARE_DOWN_SOLID)
        btn.clicked.connect(lambda: self._toggle(body, key, btn))
        head.addWidget(btn)
        lay.addLayout(head)

        body.setVisible(open_)
        lay.addWidget(body)
        # 缓存复用时的刷新钩子：统计行 + 启用开关 + 展开区值控件
        # (dkey, set_fn) / (attr, set_fn) 对，刷新时按 task 当前值回填显示
        setters = [(w._dkey, w._ctl_set) for w in body.findChildren(QWidget)
                   if hasattr(w, "_ctl_set") and hasattr(w, "_dkey")]
        setters += [(w._attr, w._ctl_set) for w in body.findChildren(QFrame)
                    if hasattr(w, "_ctl_set") and hasattr(w, "_attr")]
        dots = [w._dot_refresh for w in body.findChildren(QFrame)
                if hasattr(w, "_dot_refresh")]

        def _refresh_state(t=task, d=tt, s=stats, w=sw, setters=setters, dots=dots):
            s.setText(self._task_stats(t, d))
            w.blockSignals(True)
            w.setChecked(bool(t.get("enabled")))
            w.blockSignals(False)
            params = t.get("params") or {}
            for key, fn in setters:
                if key == "cron":
                    fn(t.get("cron") or "")
                elif key is not None:
                    fn(params.get(key, t.get(key, 0)))
            for fn in dots:
                fn()
        card._refresh_state = _refresh_state
        return card

    def _task_body(self, task, tt, index):
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(4, 2, 4, 0)
        bl.setSpacing(8)

        bl.addWidget(QFrameHLine())
        bl.addWidget(self._sec_header("调度设置"))
        defaults = tt.get("defaults") or {}
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        rows = self._defaults_rows(task, defaults)
        for i, row in enumerate(rows):
            grid.addWidget(row, i // 2, i % 2)
        grid.setColumnStretch(2, 1)
        bl.addLayout(grid)

        params = tt.get("params") or []
        if params:
            bl.addWidget(self._sec_header("参数", f"共 {len(params)} 项"))
            pgrid = QGridLayout()
            pgrid.setContentsMargins(0, 0, 0, 0)
            pgrid.setHorizontalSpacing(8)
            pgrid.setVerticalSpacing(8)
            for i, pd in enumerate(params):
                pgrid.addWidget(self._param_cell(task, pd), i // 2, i % 2)
            pgrid.setColumnStretch(2, 1)
            bl.addLayout(pgrid)
            self._style_param_boxes(body)

        foot = QHBoxLayout()
        foot.addStretch(1)
        b_up = TransparentPushButton(FluentIcon.UP, "上移")
        b_up.clicked.connect(lambda _=False, i=index, d=-1: self._move_task(i, d))
        b_down = TransparentPushButton(FluentIcon.DOWN, "下移")
        b_down.clicked.connect(lambda _=False, i=index, d=1: self._move_task(i, d))
        b_reset = TransparentPushButton(FluentIcon.SYNC, "恢复默认")
        b_reset.clicked.connect(lambda _=False, t=task, d=tt: self._reset_task(t, d))
        b_del = TransparentPushButton(FluentIcon.DELETE, "删除")
        b_del.clicked.connect(lambda _=False, t=task: self._remove_task(t))
        for b in (b_up, b_down, b_reset, b_del):
            foot.addWidget(b)
        bl.addLayout(foot)
        return body

    def _task_stats(self, task, tt):
        """卡片头部统计行（瘦身条目缺失字段回退定义段默认）。"""
        d = tt.get("defaults") or {}
        eff = lambda k, dv: task.get(k, d.get(k, dv))
        bits = []
        c = eff("count", 0)
        bits.append("次数不限" if c == 0 else f"{c} 次")
        bits.append(f'并发 {eff("max_active", 1)}')
        bits.append("单次" if eff("one_shot", False)
                    else f'间隔 {eff("next_trigger_delay", 0)}s')
        if eff("max_failures", 0):
            bits.append(f'失败上限 {eff("max_failures", 0)}')
        if eff("cron", ""):
            bits.append("cron")
        if eff("requires_march_queue", False):
            bits.append("占队列")
        ps = []
        tparams = task.get("params") or {}
        for pd in tt.get("params") or []:
            attr = pd.get("attr")
            v = tparams.get(attr, pd.get("default"))
            ps.append(f'{pd.get("name")}: {_fmt_val(v)}')
        return " · ".join(bits) + ("　｜　" + " · ".join(ps) if ps else "")


    # ---- 目标注册表（goal_registry textarea → key/value）----

    @staticmethod
    def _parse_goal_registry(text):
        """
        任务目标维护 textarea 文本 → {key: value}（保持出现顺序）。

        每行一条「key: value」（全角/半角冒号均可）；无冒号的行取整行
        为 key、value 置空。空行与 # 开头注释行忽略。
        """
        reg = {}
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, val = line.partition(":")
            elif "：" in line:
                key, _, val = line.partition("：")
            else:
                key, val = line, ""
            key = key.strip()
            if key:
                reg[key] = val.strip()
        return reg

    def _effective_options(self, task, pd):
        """
        drop_down / multi_drop_down 的实际选项列表。

        三个目标参数（exclusive_goals / public_goals / public_refresh_goals）
        的 options 动态取本任务 goal_registry 参数值的 key 集合：
        维护 textarea 即改选项；registry 为空时回退定义段静态 options。
        其余参数用定义段 options。
        """
        attr = pd.get("attr")
        static = list((pd.get("widget") or {}).get("options") or [])
        if attr not in ("exclusive_goals", "public_goals", "public_refresh_goals"):
            return static
        reg_text = (task.get("params") or {}).get("goal_registry")
        if reg_text is None:
            reg_text = self._goal_registry_default(task)
        if not reg_text:
            return static
        keys = list(self._parse_goal_registry(reg_text).keys())
        return keys or static

    def _goal_registry_default(self, task):
        """取本任务定义段里 goal_registry 参数的 default（textarea 兜底）。"""
        tt = self._task_def(task) or {}
        for pd in tt.get("params") or []:
            if pd.get("attr") == "goal_registry":
                return pd.get("default") or ""
        return ""

    def _param_cell(self, task, pd):
        """参数渲染为独立圆角单元格：名称行(含差异圆点) + 控件 + 描述。

        box 挂 _ctl_set（外部值刷新函数），供卡片复用时同步显示。
        """
        attr = pd.get("attr")
        default = pd.get("default")
        tparams = task.get("params") or {}
        value = tparams.get(attr, default)
        wtype = (pd.get("widget") or {}).get("type")

        def commit(v):
            task.setdefault("params", {})[attr] = v
            self._mark_dirty()

        set_fn = None
        if wtype == "switch" or (wtype is None and isinstance(default, bool)):
            ctl = SwitchButton()
            ctl.setChecked(bool(value))
            ctl.checkedChanged.connect(lambda on: commit(bool(on)))
            set_fn = lambda v, s=ctl: (s.blockSignals(True),
                                       s.setChecked(bool(v)), s.blockSignals(False))
        elif wtype == "number" or (
                wtype is None and isinstance(default, (int, float))
                and not isinstance(default, bool)):
            ctl = LineEdit()
            ctl.setText("" if value is None else str(value))
            ctl.setFixedWidth(140)

            def num_changed(text, d=default):
                try:
                    v = int(text)
                except ValueError:
                    try:
                        v = float(text)
                    except ValueError:
                        return
                commit(v)
            ctl.textChanged.connect(num_changed)
            set_fn = lambda v, e=ctl: e.setText("" if v is None else str(v))
        elif wtype == "textarea":
            ctl = PlainTextEdit()
            ctl.setPlainText("" if value is None else str(value))
            ctl.setMinimumHeight(110)
            ctl.textChanged.connect(lambda t=ctl: commit(t.toPlainText()))
            set_fn = lambda v, e=ctl: (
                e.blockSignals(True), e.setPlainText("" if v is None else str(v)),
                e.blockSignals(False))
        elif wtype in ("drop_down", "multi_drop_down"):
            opts = self._effective_options(task, pd)
            if wtype == "multi_drop_down":
                wrap = QWidget()
                wl = QHBoxLayout(wrap)
                wl.setContentsMargins(0, 0, 0, 0)
                cur = [str(x) for x in (value or [])]
                boxes = []
                for opt in opts:
                    ckb = CheckBox(str(opt))
                    ckb.setChecked(str(opt) in cur)
                    ckb.stateChanged.connect(
                        lambda _s, o=opt, c=ckb: self._multi_toggle(task, attr, o, c.isChecked()))
                    wl.addWidget(ckb)
                    boxes.append((ckb, str(opt)))
                wl.addStretch(1)
                ctl = wrap

                def set_fn(v, boxes=boxes):
                    cur = [str(x) for x in (v or [])]
                    for ckb, opt in boxes:
                        ckb.blockSignals(True)
                        ckb.setChecked(opt in cur)
                        ckb.blockSignals(False)
            elif opts:
                ctl = ComboBox()
                items = [str(o) for o in opts]
                ctl.addItems(items)
                sval = str(value) if value is not None else ""
                if sval and sval not in items:
                    ctl.addItem(sval + "（不在选项中）")
                ctl.setCurrentText(sval if sval in items
                                   else (sval + "（不在选项中）" if sval else items[0]))
                ctl.currentTextChanged.connect(
                    lambda txt, t=task, a=attr, os_=opts:
                    self._drop_changed(t, a, txt, os_))

                def set_fn(v, c=ctl, items=items):
                    sval = str(v) if v is not None else ""
                    if sval and sval not in items:
                        c.blockSignals(True)
                        c.addItem(sval + "（不在选项中）")
                        c.blockSignals(False)
                    c.setCurrentText(sval if sval in items
                                     else (sval + "（不在选项中）" if sval else items[0]))
            else:
                ctl = LineEdit()
                ctl.setText("" if value is None else str(value))
                ctl.textChanged.connect(lambda v: commit(v))
                set_fn = lambda v, e=ctl: e.setText("" if v is None else str(v))
        else:
            ctl = LineEdit()
            ctl.setText("" if value is None else str(value))
            ctl.textChanged.connect(lambda v: commit(v))
            set_fn = lambda v, e=ctl: e.setText("" if v is None else str(v))

        desc = pd.get("desc") or ""
        get = lambda: (task.get("params") or {}).get(attr)
        box = self._param_box(pd.get("name") or attr or "?", ctl, desc,
                              (get, default))
        if set_fn is not None:
            box._ctl_set = set_fn
        box._attr = attr
        if desc:
            dlab = BodyLabel(desc)
            dlab.setWordWrap(True)
            dlab.setStyleSheet("color:gray;font-size:12px;")
            box.layout().addWidget(dlab)
        return box

    def _drop_changed(self, task, attr, text, opts):
        """drop_down 参数选择提交：剥掉"（不在选项中）"提示后写入。"""
        val = text.replace("（不在选项中）", "").strip()
        task.setdefault("params", {})[attr] = val
        self._mark_dirty()

    def _multi_toggle(self, task, attr, opt, checked):
        """multi_drop_down 参数勾选提交：勾选加入、取消移除，保持 options 顺序。"""
        params = task.setdefault("params", {})
        cur = list(params.get(attr) or [])
        s = str(opt)
        if checked:
            if s not in cur:
                cur.append(s)
        else:
            cur = [x for x in cur if x != s]
        params[attr] = cur
        self._mark_dirty()

    def _defaults_rows(self, obj, default_obj):
        """六个调度键的编辑行。obj 是任务实例或类型 defaults dict。

        每行 host 挂 _ctl_set（外部刷新函数）+ _dkey（键名），
        供卡片复用时同步显示（_refresh_card_state）。
        """
        rows = []

        def num_row(key):
            e = LineEdit()
            e.setText(str(obj.get(key, 0)))
            e.setFixedWidth(140)
            e.textChanged.connect(lambda v, o=obj, k=key: self._set_num(o, k, v))
            host = self._field_row(
                {"count": "执行次数", "max_active": "最大并发",
                 "max_failures": "最大失败次数","next_trigger_delay": "执行完成后到下次触发的间隔秒数"}[key],
                DKEY_HELP[key], e,
                (lambda o=obj, k=key: o.get(k, 0), default_obj.get(key, 0)))
            host._ctl_set = lambda v, e=e: e.setText(str(v))
            host._dkey = key
            return host

        def bool_row(key, label):
            sw = SwitchButton(label)
            sw.setChecked(bool(obj.get(key)))
            sw.checkedChanged.connect(lambda on, o=obj, k=key: self._set_bool(o, k, on))
            host = self._field_row(
                label, DKEY_HELP[key], sw,
                (lambda o=obj, k=key: o.get(k, False), default_obj.get(key, False)))
            host._ctl_set = lambda v, s=sw: (s.blockSignals(True),
                                             s.setChecked(bool(v)), s.blockSignals(False))
            host._dkey = key
            return host

        rows.append(num_row("count"))
        rows.append(num_row("max_active"))
        rows.append(num_row("next_trigger_delay"))
        rows.append(num_row("max_failures"))
        rows.append(bool_row("one_shot", "单次执行"))

        e = LineEdit()
        e.setText(obj.get("cron") or "")
        e.setPlaceholderText("留空 = 不使用")
        e.setFixedWidth(220)
        e.textChanged.connect(lambda v, o=obj: self._set_str(o, "cron", v))
        host = self._field_row(
            "Cron 表达式", DKEY_HELP["cron"], e,
            (lambda o=obj: o.get("cron") or "", default_obj.get("cron") or ""))
        host._ctl_set = lambda v, e=e: e.setText(v or "")
        host._dkey = "cron"
        rows.append(host)
        return rows

    # ------------------------------------------------ 任务实例增删改
    def _task_enabled(self, task, on):
        """启用/停用: 数据归位 + 原地挪卡片（不重建其他卡片）。"""
        task["enabled"] = bool(on)
        chain = self._chain()
        if chain:
            arr = chain[2].setdefault("tasks", [])
            if task in arr:
                arr.remove(task)
                if on:
                    # 已启用块末尾: 第一个未启用任务之前
                    pos = next((i for i, t in enumerate(arr) if not t.get("enabled")),
                               len(arr))
                else:
                    # 未启用块末尾: 数组尾
                    pos = len(arr)
                arr.insert(pos, task)
        self._mark_dirty()
        self._refresh_tasks_page(keep_cards=True)

    def _move_task(self, index, direction):
        """组内移动（数据数组顺序即显示顺序）。"""
        chain = self._chain()
        if not chain:
            return
        arr = chain[2].setdefault("tasks", [])
        j = index + direction
        if not (0 <= j < len(arr)):
            return
        # 不允许跨组: 目标位置的 enabled 状态必须与当前一致
        if bool(arr[index].get("enabled")) != bool(arr[j].get("enabled")):
            return
        arr[index], arr[j] = arr[j], arr[index]
        self._mark_dirty()
        self._refresh_tasks_page(keep_cards=True)

    def _queue_changed(self, *_):
        if self._suspended:
            return
        chain = self._chain()
        if not chain:
            return
        q = chain[2].setdefault("queue", {})
        try:
            q["tick_interval"] = float(self.tick_edit.text() or 1)
        except ValueError:
            pass
        try:
            q["max_failures"] = int(float(self.mf_edit.text() or 0))
        except ValueError:
            pass
        q["continue_after_failure"] = bool(self.caf_switch.isChecked())
        self._mark_dirty()

    def _reset_task(self, task, tt):
        # 瘦身条目：删掉覆盖字段即回退定义段默认
        for k in DEFAULT_KEYS:
            task.pop(k, None)
        task["params"] = {}
        self._mark_dirty()
        self._refresh_tasks_page(keep_cards=True)

    def _remove_task(self, task):
        chain = self._chain()
        if not chain:
            return
        arr = chain[2].get("tasks") or []
        if task in arr:
            arr.remove(task)
        self._card_cache.pop(task.get("id") or task.get("type"), None)
        self._mark_dirty()
        self._refresh_tasks_page()

    def _add_task_dialog(self):
        chain = self._chain()
        if not chain or not self.doc:
            return
        have = {t.get("id") or t.get("type") for t in chain[2].get("tasks") or []}
        missing = [t for t in self._pkg_types(self._cur_pkg())
                   if isinstance(t, dict) and (t.get("id") or t.get("type")) not in have]
        if not missing:
            InfoBar.warning("添加任务", "所有已定义类型均已添加",
                            parent=self.window(), duration=2500)
            return
        dlg = _PickTypeDialog(missing, self.window())
        if dlg.exec() and dlg.picked_type:
            self._insert_task(chain[2], dlg.picked_type)

    def _insert_task(self, user, type_):
        tt = self._type_def(type_)
        if not tt:
            return
        user.setdefault("tasks", []).append({
            "id": tt.get("id"), "enabled": False, "params": {}})
        self.expanded.add((self.sel["m"], self.sel["a"], self.sel["u"], tt.get("id")))
        self._mark_dirty()
        self._refresh_tasks_page()

    def _batch_enable_dialog(self):
        """批量多选启用/停用当前用户任务（新建用户快速勾选场景）。"""
        chain = self._chain()
        if not chain:
            return
        tasks = [t for t in chain[2].get("tasks") or [] if isinstance(t, dict)]
        if not tasks:
            InfoBar.warning("批量启用", "当前用户还没有任务，先「添加任务」",
                            parent=self.window(), duration=2500)
            return
        items = []
        for t in tasks:
            tt = self._task_def(t)
            label = (tt.get("name_prefix") if tt else None) or t.get("id") or t.get("type")
            items.append((t, bool(t.get("enabled")), label))
        dlg = _BatchEnableDialog(items, self.window())
        if not dlg.exec():
            return
        for t, on in dlg.results:
            t["enabled"] = bool(on)
        self._normalize_group_order()
        self._mark_dirty()
        self._refresh_tasks_page()

    def _reset_user_tasks(self):
        """当前包、当前用户全部任务重置：停用 + 删覆盖字段 + 参数清空
        （回退定义段默认值）。"""
        chain = self._chain()
        if not chain:
            return
        tasks = [t for t in chain[2].get("tasks") or [] if isinstance(t, dict)]
        if not tasks:
            InfoBar.warning("重置任务参数", "当前用户没有任务",
                            parent=self.window(), duration=2500)
            return
        dlg = Dialog(
            "重置任务参数",
            f"当前包 {self._cur_pkg() or '?'} / 用户 {chain[2].get('user_id', '?')} 的 "
            f"{len(tasks)} 个任务将全部停用，参数回到默认值。确定？",
            self.window())
        if not dlg.exec():
            return
        for t in tasks:
            t["enabled"] = False
            for k in DEFAULT_KEYS:
                t.pop(k, None)
            t["params"] = {}
        self.expanded.clear()
        self._normalize_group_order()
        self._mark_dirty()
        self._refresh_tasks_page()
        InfoBar.success("已重置", f"{len(tasks)} 个任务已停用并恢复默认参数",
                        parent=self.window(), duration=2500)

    # ==================================================== 类型定义页渲染
    def _refresh_types_page(self):
        with self._suspend_signals():
            self._clear_layout(self.type_list_lay)
            if not self.doc and not self.type_groups:
                self.type_list_lay.addWidget(BodyLabel("未加载配置"))
                return
            # 顶部：package 分组选择（类型定义跟软件绑定，按分组编辑）
            bar = QHBoxLayout()
            bar.addWidget(BodyLabel("软件分组:"))
            self.type_pkg_combo = ComboBox()
            pkgs = list(self.type_order)
            for pkg in pkgs:
                self.type_pkg_combo.addItem(
                    f'{pkg}（{len(self._pkg_types(pkg))} 个类型）')
            if not pkgs:
                self.type_pkg_combo.addItem("（task_type.json 无分组）")
            if self.cur_type_pkg in pkgs:
                self.type_pkg_combo.setCurrentIndex(pkgs.index(self.cur_type_pkg))
            elif pkgs:
                self.cur_type_pkg = pkgs[0]
            self.type_pkg_combo.currentIndexChanged.connect(
                self._on_type_pkg_changed)
            bar.addWidget(self.type_pkg_combo, 1)
            wrap = QWidget()
            wrap.setLayout(bar)
            self.type_list_lay.addWidget(wrap)
            types = self._pkg_types(self.cur_type_pkg)
            if not types:
                self.type_list_lay.addWidget(BodyLabel("该软件暂无类型定义"))
                return
            for tt in types:
                self.type_list_lay.addWidget(self._type_card(tt))
            self.type_list_lay.addStretch(1)

    def _on_type_pkg_changed(self, idx):
        if self._suspended:
            return
        pkgs = list(self.type_order)
        if 0 <= idx < len(pkgs):
            self.cur_type_pkg = pkgs[idx]
            self._refresh_types_page()

    def _type_card(self, tt):
        key = ("T", tt.get("type"))
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        d = tt.get("defaults") or {}
        info = QVBoxLayout()
        info.addWidget(StrongBodyLabel(tt.get("type") or "?"))
        if tt.get("name_prefix"):
            info.addWidget(BodyLabel(tt["name_prefix"]))
        stats = BodyLabel(
            f'{len(tt.get("params") or [])} 个参数 ｜ 默认 '
            f'{"不限" if d.get("count") == 0 else d.get("count")} 次 · 并发 {d.get("max_active")}'
            + (" · 单次" if d.get("one_shot") else ""))
        info.addWidget(stats)
        head = QHBoxLayout()
        head.addLayout(info, 1)
        body = self._type_body(tt)
        open_ = key in self.expanded_types
        btn = TransparentToolButton(FluentIcon.CARE_UP_SOLID if open_ else FluentIcon.CARE_DOWN_SOLID)
        btn.clicked.connect(lambda: self._toggle(body, key, btn))
        head.addWidget(btn)
        lay.addLayout(head)

        body.setVisible(open_)
        lay.addWidget(body)
        return card

    def _type_body(self, tt):
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(4, 0, 0, 0)
        bl.setSpacing(4)
        bl.addWidget(QFrameHLine())
        bl.addWidget(StrongBodyLabel("基本信息"))

        e_type = LineEdit()
        e_type.setText(tt.get("type") or "")
        e_type.setFixedWidth(220)
        e_id = LineEdit()
        e_id.setText(tt.get("id") or "")
        e_id.setFixedWidth(220)
        e_id.editingFinished.connect(lambda t=tt, e=e_id: self._rename_id(t, e.text()))
        bl.addWidget(self._field_row(
            "id", "唯一标记：实例条目按它引用本定义，耗时统计也按它聚合", e_id))

        e_est = LineEdit()
        e_est.setText(str(tt.get("estimated_seconds", 0)))
        e_est.setFixedWidth(220)
        e_est.editingFinished.connect(lambda t=tt, e=e_est: self._set_estimated(t, e.text()))
        bl.addWidget(self._field_row(
            "预估耗时(秒)", "单次执行的预估处理时间，0 = 未知（预留字段）", e_est))

        e_np = LineEdit()
        e_np.setText(tt.get("name_prefix") or "")
        e_np.setFixedWidth(220)
        e_np.textChanged.connect(lambda v, t=tt: self._set_str(t, "name_prefix", v))
        bl.addWidget(self._field_row("名称前缀", "", e_np))

        e_desc = LineEdit()
        e_desc.setText(tt.get("description") or "")
        e_desc.textChanged.connect(lambda v, t=tt: self._set_str(t, "description", v))
        bl.addWidget(self._field_row("描述", "", e_desc))

        bl.addWidget(StrongBodyLabel("默认调度"))
        defaults = tt.setdefault("defaults", _new_type_defaults())
        for row in self._defaults_rows(defaults, {}):
            bl.addWidget(row)

        bl.addWidget(StrongBodyLabel("参数定义"))
        for p in tt.get("params") or []:
            bl.addLayout(self._param_def_row(tt, p))
        b_add = TransparentPushButton(FluentIcon.ADD, "添加参数")
        b_add.clicked.connect(lambda _=False, t=tt: self._add_param(t))
        bl.addWidget(b_add)

        foot = QHBoxLayout()
        foot.addStretch(1)
        b_del = TransparentPushButton(FluentIcon.DELETE, "删除类型")
        b_del.clicked.connect(lambda _=False, t=tt: self._delete_type(t))
        foot.addWidget(b_del)
        bl.addLayout(foot)
        return body

    def _param_def_row(self, tt, p):
        lay = QHBoxLayout()
        lay.setSpacing(6)

        e_name = LineEdit()
        e_name.setText(p.get("name") or "")
        e_name.setPlaceholderText("参数名")
        e_name.setFixedWidth(110)
        e_name.textChanged.connect(lambda v, x=p: self._set_str(x, "name", v))

        e_attr = LineEdit()
        e_attr.setText(p.get("attr") or "")
        e_attr.setPlaceholderText("attr")
        e_attr.setFixedWidth(110)
        e_attr.editingFinished.connect(
            lambda t=tt, x=p, e=e_attr: self._rename_param_attr(t, x, e.text()))

        d = p.get("default")
        if isinstance(d, bool):
            e_def = SwitchButton()
            e_def.setChecked(bool(d))
            e_def.checkedChanged.connect(lambda on, x=p: self._set_bool(x, "default", on))
        else:
            e_def = LineEdit()
            e_def.setText("" if d is None else str(d))
            e_def.setFixedWidth(90)
            e_def.textChanged.connect(lambda v, x=p: self._param_default_changed(x, v))

        e_wid = ComboBox()
        e_wid.addItems(["(自动)", "switch", "number", "text", "drop_down", "multi_drop_down"])
        cur = (p.get("widget") or {}).get("type") if p.get("widget") else "(自动)"
        e_wid.setCurrentText(cur if cur else "(自动)")
        e_wid.currentTextChanged.connect(lambda v, x=p: self._widget_type_changed(x, v))

        e_opts = LineEdit()
        e_opts.setText(",".join(map(str, (p.get("widget") or {}).get("options") or [])))
        e_opts.setPlaceholderText("选项,逗号分隔")
        e_opts.textChanged.connect(lambda v, x=p: self._widget_opts_changed(x, v))

        e_desc = LineEdit()
        e_desc.setText(p.get("desc") or "")
        e_desc.setPlaceholderText("说明")
        e_desc.textChanged.connect(lambda v, x=p: self._set_str(x, "desc", v))

        b_del = TransparentToolButton(FluentIcon.DELETE)
        b_del.clicked.connect(lambda _=False, t=tt, x=p: self._delete_param(t, x))

        for w in (e_name, e_attr, e_def, e_wid, e_opts, e_desc, b_del):
            lay.addWidget(w)
        return lay

    # ==================================================== 设备链页渲染
    def _refresh_machines_page(self):
        with self._suspend_signals():
            self._sync_rotation_mode_ui()
            self._clear_layout(self.machine_list_lay)
            if not self.doc:
                self.machine_list_lay.addWidget(BodyLabel("未加载配置"))
                return
            machines = self.doc.get("machines") or []
            if not machines:
                self.machine_list_lay.addWidget(BodyLabel("暂无机器，点击右上角「添加机器」"))
                return
            for mi, m in enumerate(machines):
                self.machine_list_lay.addWidget(self._machine_card(m, mi))
            self.machine_list_lay.addStretch(1)

    def _machine_card(self, m, mi):
        fe = self._first_enabled_idx()
        is_active = fe and fe[0] == mi
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        info = QVBoxLayout()
        info.addWidget(StrongBodyLabel(m.get("name") or f"machine{mi}"))
        info.addWidget(BodyLabel(f'adb_serial: {m.get("adb_serial") or "-"}'))
        head.addLayout(info, 1)
        if is_active:
            head.addWidget(BodyLabel("生效链"))
        sw = SwitchButton("启用")
        sw.setChecked(bool(m.get("enabled")))
        sw.checkedChanged.connect(lambda on, o=m: self._set_bool(o, "enabled", on))
        head.addWidget(sw)
        lay.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        e_name = LineEdit()
        e_name.setText(m.get("name") or "")
        e_name.setFixedWidth(180)
        e_name.textChanged.connect(lambda v, o=m: self._set_str(o, "name", v))
        grid.addWidget(self._field_row("名称", "", e_name), 0, 0)
        e_serial = LineEdit()
        e_serial.setText(m.get("adb_serial") or "")
        e_serial.setPlaceholderText("如 127.0.0.1:7555，留空用全局")
        e_serial.setFixedWidth(220)
        e_serial.textChanged.connect(lambda v, o=m: self._set_str(o, "adb_serial", v))
        grid.addWidget(self._field_row("ADB 序列", "", e_serial), 0, 1)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)

        lay.addWidget(self._sec_header("应用"))
        for ai, a in enumerate(m.get("apps") or []):
            lay.addWidget(self._app_block(m, a, ai))
        b_add_app = TransparentPushButton(FluentIcon.ADD, "添加应用")
        b_add_app.clicked.connect(lambda _=False, o=m: self._add_app_dialog(o))
        lay.addWidget(b_add_app)

        foot = QHBoxLayout()
        foot.addStretch(1)
        b_del = TransparentPushButton(FluentIcon.DELETE, "删除机器")
        b_del.clicked.connect(lambda _=False, i=mi: self._remove_machine(i))
        foot.addWidget(b_del)
        lay.addLayout(foot)
        return card

    def _app_block(self, m, a, ai):
        fe = self._first_enabled_idx()
        is_active = fe and fe[0] == self.doc["machines"].index(m) and fe[1] == ai
        box = CardWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel(a.get("package") or "(未设置包名)"))
        head.addStretch(1)
        if is_active:
            head.addWidget(BodyLabel("生效链"))
        sw = SwitchButton("启用")
        sw.setChecked(bool(a.get("enabled")))
        sw.checkedChanged.connect(lambda on, o=a: self._set_bool(o, "enabled", on))
        head.addWidget(sw)
        b_del = TransparentToolButton(FluentIcon.DELETE)
        b_del.setToolTip("删除应用")
        b_del.clicked.connect(lambda _=False, m=m, i=ai: self._remove_app(m, i))
        head.addWidget(b_del)
        lay.addLayout(head)

        e_pkg = LineEdit()
        e_pkg.setText(a.get("package") or "")
        e_pkg.setFixedWidth(220)
        e_pkg.textChanged.connect(lambda v, o=a: self._set_str(o, "package", v))
        lay.addWidget(self._field_row("package", "", e_pkg))

        for ui_, u in enumerate(a.get("users") or []):
            lay.addWidget(self._user_block(a, u, ui_))
        b_add_user = TransparentPushButton(FluentIcon.ADD, "添加用户")
        b_add_user.clicked.connect(lambda _=False, o=a: self._add_user_dialog(o))
        lay.addWidget(b_add_user)
        return box

    def _user_block(self, a, u, ui_):
        fe = self._first_enabled_idx()
        box = CardWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        q = u.get("queue") or {}
        n_tasks = len(u.get("tasks") or [])
        n_en = sum(1 for t in u.get("tasks") or [] if t.get("enabled"))
        head = QHBoxLayout()
        info = QVBoxLayout()
        info.addWidget(StrongBodyLabel(f'user {u.get("user_id", ui_)}'))
        info.addWidget(BodyLabel(f"{n_tasks} 任务 · {n_en} 启用 · tick {q.get('tick_interval', 1)}s"))
        head.addLayout(info, 1)
        if fe and fe[2] == ui_:
            head.addWidget(BodyLabel("生效链"))
        sw = SwitchButton("启用")
        sw.setChecked(bool(u.get("enabled")))
        sw.checkedChanged.connect(lambda on, o=u: self._set_bool(o, "enabled", on))
        head.addWidget(sw)
        b_edit = TransparentPushButton(FluentIcon.EDIT, "编辑任务")
        b_edit.clicked.connect(lambda _=False, i=ui_: self._goto_user_tasks(i))
        head.addWidget(b_edit)
        b_del = TransparentToolButton(FluentIcon.DELETE)
        b_del.setToolTip("删除用户")
        b_del.clicked.connect(lambda _=False, a=a, i=ui_: self._remove_user(a, i))
        head.addWidget(b_del)
        lay.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        e_uid = LineEdit()
        e_uid.setText(str(u.get("user_id", 0)))
        e_uid.setFixedWidth(100)
        e_uid.textChanged.connect(
            lambda v, o=u: self._set_num(o, "user_id", v))
        grid.addWidget(self._field_row("user_id", "Android 分身 ID（预留）", e_uid), 0, 0)
        e_tick = LineEdit()
        e_tick.setText(str(q.get("tick_interval", 1)))
        e_tick.setFixedWidth(100)
        e_tick.textChanged.connect(lambda v, o=u: self._set_tick(o, v))
        grid.addWidget(self._field_row("tick 间隔(秒)", "调度器轮询间隔", e_tick), 0, 1)
        caf = SwitchButton("失败后继续")
        caf.setChecked(q.get("continue_after_failure") is not False)
        caf.checkedChanged.connect(lambda on, o=u: self._set_caf(o, on))
        grid.addWidget(self._field_row("失败后继续", "任务失败后是否继续调度", caf), 1, 0)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        return box

    def _first_enabled_idx(self):
        """第一条全 enabled 链的 (mi, ai, ui)，无则 None。"""
        if not self.doc:
            return None
        for mi, m in enumerate(self.doc.get("machines") or []):
            if not m.get("enabled"):
                continue
            for ai, a in enumerate(m.get("apps") or []):
                if not a.get("enabled"):
                    continue
                for ui_, u in enumerate(a.get("users") or []):
                    if u.get("enabled"):
                        return (mi, ai, ui_)
        return None

    def _set_tick(self, u, text):
        try:
            u.setdefault("queue", {})["tick_interval"] = float(text or 1)
            self._mark_dirty()
        except ValueError:
            pass

    def _set_caf(self, u, on):
        u.setdefault("queue", {})["continue_after_failure"] = bool(on)
        self._mark_dirty()

    def _goto_user_tasks(self, ui_):
        self.sel["u"] = ui_
        self._clamp_sel()
        self._refresh_chain_combos()
        self._refresh_tasks_page()
        self._switch_page("tasks")

    def _add_machine_dialog(self):
        if not self.doc:
            return
        dlg = _InputTextDialog("添加机器", "机器名称", self.window())
        if not dlg.exec():
            return
        name = dlg.value or f'machine{len(self.doc.get("machines") or []) + 1}'
        self.doc.setdefault("machines", []).append(
            {"name": name, "enabled": False, "adb_serial": "", "apps": []})
        self._mark_dirty()
        self._refresh_machines_page()

    def _remove_machine(self, mi):
        machines = self.doc.get("machines") or []
        if 0 <= mi < len(machines):
            del machines[mi]
        self._clamp_sel()
        self._mark_dirty()
        self._refresh_machines_page()
        self._refresh_tasks_page()

    def _add_app_dialog(self, m):
        dlg = _InputTextDialog("添加应用", "包名（如 com.got.china）", self.window())
        if not dlg.exec():
            return
        m.setdefault("apps", []).append(
            {"package": dlg.value or "com.got.china", "enabled": False, "users": []})
        self._mark_dirty()
        self._refresh_machines_page()

    def _remove_app(self, m, ai):
        apps = m.get("apps") or []
        if 0 <= ai < len(apps):
            del apps[ai]
        self._clamp_sel()
        self._mark_dirty()
        self._refresh_machines_page()
        self._refresh_tasks_page()

    def _add_user_dialog(self, a):
        nxt = max((x.get("user_id", 0) for x in a.get("users") or []), default=0) + 1
        a.setdefault("users", []).append({
            "user_id": nxt, "enabled": False,
            "queue": {"tick_interval": 1, "continue_after_failure": True},
            "tasks": []})
        self._mark_dirty()
        self._refresh_machines_page()

    def _remove_user(self, a, ui_):
        users = a.get("users") or []
        if 0 <= ui_ < len(users):
            del users[ui_]
        self._clamp_sel()
        self._mark_dirty()
        self._refresh_machines_page()
        self._refresh_tasks_page()


    def _param_default_changed(self, p, text):
        d = p.get("default")
        if isinstance(d, bool):
            return
        for cast in (int, float):
            try:
                p["default"] = cast(text)
                self._mark_dirty()
                return
            except (ValueError, TypeError):
                continue
        p["default"] = text
        self._mark_dirty()

    def _widget_type_changed(self, p, v):
        p["widget"] = None if v == "(自动)" else {
            "type": v, "options": (p.get("widget") or {}).get("options") or []}
        self._mark_dirty()

    def _widget_opts_changed(self, p, text):
        if p.get("widget"):
            p["widget"]["options"] = [s.strip() for s in text.split(",") if s.strip()]
            self._mark_dirty()

    def _rename_param_attr(self, tt, p, new_attr):
        new_attr = (new_attr or "").strip()
        old = p.get("attr")
        if not new_attr or new_attr == old:
            return
        if any(x.get("attr") == new_attr for x in tt.get("params") or [] if x is not p):
            InfoBar.error("attr 重复", new_attr, parent=self.window(), duration=2500)
            return
        p["attr"] = new_attr
        tid = tt.get("id")
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    for t in u.get("tasks") or []:
                        tparams = t.get("params")
                        if ((t.get("id") == tid or t.get("type") == tt.get("type"))
                                and isinstance(tparams, dict) and old in tparams):
                            tparams[new_attr] = tparams.pop(old)
        self._mark_types_dirty()
        self._mark_dirty()
        self._card_cache = {}
        self._refresh_tasks_page()

    def _add_param(self, tt):
        n = len(tt.get("params") or []) + 1
        tt.setdefault("params", []).append(
            {"name": "新参数", "attr": f"new_attr_{n}", "default": None,
             "widget": None, "desc": ""})
        self._mark_types_dirty()
        self._refresh_types_page()

    def _mark_types_dirty(self):
        self.types_dirty = True
        self._refresh_status()

    def _delete_param(self, tt, p):
        params = tt.get("params") or []
        if p in params:
            params.remove(p)
        attr = p.get("attr")
        tid = tt.get("id")
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    for t in u.get("tasks") or []:
                        tparams = t.get("params")
                        if ((t.get("id") == tid or t.get("type") == tt.get("type"))
                                and isinstance(tparams, dict)):
                            tparams.pop(attr, None)
        self._mark_types_dirty()
        self._mark_dirty()
        self._refresh_types_page()

    def _rename_type(self, tt, new_type):
        new_type = (new_type or "").strip()
        old = tt.get("type")
        if not new_type or new_type == old:
            return
        if any(x.get("type") == new_type
               for x in self._pkg_types(self.cur_type_pkg) if x is not tt):
            InfoBar.error("type 已存在", new_type, parent=self.window(), duration=2500)
            return
        # id 化后实例只按 id 引用定义段，type 重命名不再触碰实例条目
        tt["type"] = new_type
        self._mark_types_dirty()
        self._refresh_types_page()
        InfoBar.success("已重命名", new_type, parent=self.window(), duration=2500)

    def _rename_id(self, tt, new_id):
        new_id = (new_id or "").strip()
        old = tt.get("id")
        if not new_id or new_id == old:
            return
        if any(x.get("id") == new_id
               for x in self._pkg_types(self.cur_type_pkg) if x is not tt):
            InfoBar.error("id 已存在", new_id, parent=self.window(), duration=2500)
            return
        tid_old = old
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    for t in u.get("tasks") or []:
                        if t.get("id") == tid_old:
                            t["id"] = new_id
        tt["id"] = new_id
        self._mark_types_dirty()
        self._mark_dirty()
        self._refresh_types_page()
        self._card_cache = {}
        self._refresh_tasks_page()
        InfoBar.success("已重命名 id", new_id, parent=self.window(), duration=2500)

    def _set_estimated(self, tt, text):
        try:
            v = float(text)
        except ValueError:
            return
        if v < 0:
            return
        tt["estimated_seconds"] = v
        self._mark_types_dirty()

    def _delete_type(self, tt):
        n = 0
        tid = tt.get("id")
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    before = len(u.get("tasks") or [])
                    u["tasks"] = [t for t in u.get("tasks") or []
                                  if (tid and t.get("id") == tid)
                                  or (not tid and t.get("type") == tt.get("type"))]
                    n += before - len(u.get("tasks") or [])
        types = self._pkg_types(self.cur_type_pkg)
        if tt in types:
            types.remove(tt)
        self.expanded_types.discard(("T", tt.get("type")))
        self._mark_types_dirty()
        self._mark_dirty()
        self._refresh_types_page()
        self._card_cache = {}
        self._refresh_tasks_page()
        InfoBar.success("已删除类型", f'{tt.get("type")}（清理 {n} 处实例）',
                        parent=self.window(), duration=2500)

    def _add_type_dialog(self):
        if not self.cur_type_pkg:
            InfoBar.warning("添加类型", "没有可用的软件分组", parent=self.window(),
                            duration=2500)
            return
        dlg = _InputTextDialog("添加类型", "type（唯一标识）", self.window())
        if not dlg.exec():
            return
        type_ = dlg.value
        if not type_:
            return
        if any(t.get("type") == type_ for t in self._pkg_types(self.cur_type_pkg)):
            InfoBar.error("type 已存在", type_, parent=self.window(), duration=2500)
            return
        base_id = re.sub(r"[^a-zA-Z0-9]+", "_", type_).strip("_").lower() or "task"
        nid = base_id
        k = 1
        existing = {t.get("id") for t in self._pkg_types(self.cur_type_pkg)}
        while nid in existing:
            k += 1
            nid = f"{base_id}_{k}"
        self.type_groups.setdefault(self.cur_type_pkg, []).append({
            "type": type_, "id": nid, "name_prefix": type_, "description": "",
            "estimated_seconds": 0,
            "defaults": _new_type_defaults(), "params": []})
        self.expanded_types.add(("T", type_))
        self._mark_types_dirty()
        self._refresh_types_page()

    def _switch_page(self, key):
        if key != "source" and self._src_dirty:
            dlg = Dialog("源码有未应用的修改", "放弃并切换？", self.window())
            if not dlg.exec():
                self.pivot.setCurrentItem("source")
                return
            self._refresh_source()
        # 懒构建: 首次进入或标脏时才填充列表内容
        if key in self._stale_pages and key != "source":
            if key == "types":
                self._refresh_types_page()
            elif key == "machines":
                self._refresh_machines_page()
            elif key == "tasks":
                self._refresh_tasks_page()
            self._stale_pages.discard(key)
        self.page_tasks.setVisible(key == "tasks")
        self.page_types.setVisible(key == "types")
        self.page_machines.setVisible(key == "machines")
        self.page_source.setVisible(key == "source")

    def _refresh_source(self):
        self.src_edit.blockSignals(True)
        self.src_edit.setPlainText(
            (self._dump() if self.doc else "") + "\n" + "-" * 60 +
            "\n// ↑ configs/task_config.json 实例段 ↓ configs/task_type.json 定义段\n\n"
            + (self._dump_types() if self.type_groups else ""))
        self.src_edit.blockSignals(False)
        self._src_dirty = False
        self.lbl_src_hint.setText("与当前数据一致（两份文件拼接）")

    def _on_source_changed(self):
        if self.src_edit.signalsBlocked():
            return
        self._src_dirty = True
        self.lbl_src_hint.setText("有未应用的修改，切走会丢弃")

    def _apply_source(self):
        """源码页内容按分隔线切成两段：上=实例段，下=定义段。"""
        SEP = "\n" + "-" * 60 + "\n"
        raw = self.src_edit.toPlainText()
        if SEP not in raw:
            InfoBar.error("缺少分隔线", "实例段与定义段之间需要一行 '----' 分隔",
                          parent=self.window(), duration=4000)
            return
        cfg_raw, type_raw = raw.split(SEP, 1)
        try:
            doc = json.loads(cfg_raw)
            groups = json.loads(type_raw)
        except Exception as e:
            InfoBar.error("JSON 解析失败", str(e)[:160], parent=self.window(), duration=4000)
            return
        try:
            type_groups, type_order = {}, []
            for g in groups or []:
                type_groups[g.get("package")] = g.get("task_types") or []
                type_order.append(g.get("package"))
        except Exception as e:
            InfoBar.error("定义段结构非法", str(e)[:160], parent=self.window(), duration=4000)
            return
        errs = validate_doc(doc, type_groups)
        if errs and not self._confirm_ignore_errors(errs, "应用"):
            return
        self.doc = doc
        self.type_groups = type_groups
        self.type_order = type_order
        self._normalize_group_order()
        self._src_dirty = False
        self._sync_all()
        self._mark_dirty()
        self._mark_types_dirty()
        InfoBar.success("已应用", "源码修改已生效", parent=self.window(), duration=2000)

    def _copy_source(self):
        QApplication.clipboard().setText(self.src_edit.toPlainText())
        InfoBar.success("已复制", "JSON 源码已复制到剪贴板", parent=self.window(), duration=2000)

    def _on_pivot_tasks(self):
        self._switch_page("tasks")

    def _on_pivot_types(self):
        self._switch_page("types")

    def _on_pivot_machines(self):
        self._switch_page("machines")

    def _on_pivot_source(self):
        self._switch_page("source")


    def _profile_dir(self):
        return Path("configs") / "task_config_profiles"

    def _profiles(self, pkg=None):
        """方案名列表；给 pkg 时只列同 package 的方案。"""
        d = self._profile_dir()
        if not d.exists():
            return []
        names = []
        for p in sorted(d.glob("*.json")):
            if pkg is not None:
                try:
                    meta = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if meta.get("package") != pkg:
                    continue
            names.append(p.stem)
        return names

    @staticmethod
    def _profile_version(p):
        """读取方案文件返回 (version, package, tasks)；不合法返回 (None, None, [])。"""
        try:
            meta = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            return None, None, []
        if not isinstance(meta, dict):
            return None, None, []
        if meta.get("version") == 2:
            return 2, meta.get("package"), meta.get("tasks") or []
        # v1 旧全量格式：抽取生效链启用条目，尽量兼容读取
        tasks = []
        pkg = None
        for m in meta.get("machines") or []:
            for a in m.get("apps") or []:
                pkg = a.get("package")
                for u in a.get("users") or []:
                    for t in u.get("tasks") or []:
                        if isinstance(t, dict) and t.get("enabled"):
                            keep = {"id": t.get("id") or t.get("type"),
                                    "params": t.get("params") or {}}
                            tasks.append(keep)
        return 1, pkg, tasks

    def _enabled_task_snapshot(self):
        """当前链启用任务 → [{id, params}]（方案即 app 级配置，与 user 无关）。"""
        chain = self._chain()
        if not chain:
            return []
        out = []
        for t in chain[2].get("tasks") or []:
            if isinstance(t, dict) and t.get("enabled"):
                out.append({"id": t.get("id") or t.get("type"),
                            "params": copy.deepcopy(t.get("params") or {})})
        return out

    def _save_profile(self, name):
        pkg = self._cur_pkg()
        if not pkg:
            InfoBar.warning("无法保存", "当前链没有 package", parent=self.window(),
                            duration=2500)
            return False
        payload = {
            "version": 2,
            "package": pkg,
            "tasks": self._enabled_task_snapshot(),
        }
        d = self._profile_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.json"
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except Exception as e:
            self.logger.error(f"保存方案失败: {e}")
            InfoBar.error("保存方案失败", str(e)[:160], parent=self.window(), duration=4000)
            return False
        return True

    def _apply_profile(self, name):
        """把方案应用到当前链：只在目标用户已有任务范围内覆盖。

        - 方案条目 ∩ 目标范围：写入 params（覆盖值）并 enabled=True；
        - 目标范围内不在方案里的任务：enabled=False（方案语义=启用集）；
        - 方案里有、目标范围没有的任务：忽略（不新增任务实例）。
        """
        chain = self._chain()
        if not chain or not self.doc:
            return
        path = self._profile_dir() / f"{name}.json"
        ver, pkg, entries = self._profile_version(path)
        if ver is None:
            InfoBar.error("读取方案失败", str(path), parent=self.window(), duration=4000)
            return
        cur_pkg = self._cur_pkg()
        if pkg and cur_pkg and pkg != cur_pkg:
            InfoBar.warning(
                "package 不匹配",
                f'方案属于 {pkg}，当前应用是 {cur_pkg}，已取消',
                parent=self.window(), duration=4000)
            return
        by_id = {}
        for e in entries:
            if isinstance(e, dict) and e.get("id"):
                by_id[e["id"]] = e.get("params") or {}
        # 预演校验（参数键必须 ⊆ 当前分组定义）
        pkg_types = self._pkg_types(cur_pkg)
        def_attrs = {}
        for tt in pkg_types:
            def_attrs[tt.get("id") or tt.get("type")] = {
                p.get("attr") for p in (tt.get("params") or []) if isinstance(p, dict)}
        unknown = []
        for tid, params in by_id.items():
            attrs = def_attrs.get(tid)
            if attrs is None:
                unknown.append(tid)
                continue
            unknown += [f"{tid}.{k}" for k in params if k not in attrs]
        if unknown:
            dlg = Dialog("方案含未定义任务/参数", "\n".join(unknown[:8]) +
                         "\n仍要应用（忽略这些）吗？", self.window())
            if not dlg.exec():
                return
        # 原位重放：保持 task dict 引用不变（卡片闭包仍指向同一对象）
        user = chain[2]
        applied = 0
        for t in user.get("tasks") or []:
            if not isinstance(t, dict):
                continue
            key = t.get("id") or t.get("type")
            if key in by_id:
                t["enabled"] = True
                t["params"] = copy.deepcopy(by_id[key])
                applied += 1
            else:
                t["enabled"] = False
        self._normalize_group_order()
        self._sync_all(keep_cards=True)
        self._mark_dirty()
        InfoBar.success("方案已切换", f"{name}（覆盖 {applied} 项，记得保存落盘）",
                        parent=self.window(), duration=2500)

    # ============================================================ 保存
    def _save_profile_dialog(self):
        if not self.doc:
            InfoBar.warning("无法保存", "当前未加载配置", parent=self.window(), duration=2500)
            return
        pkg = self._cur_pkg()
        names = self._profiles(pkg)
        dlg = _SaveProfileDialog(names, self.window())
        if not dlg.exec() or not (dlg.name or "").strip():
            return
        name = dlg.name.strip()
        if (self._profile_dir() / f"{name}.json").exists() and name not in names:
            c = Dialog("方案已存在", f"覆盖方案「{name}」？", self.window())
            if not c.exec():
                return
        if self._save_profile(name):
            InfoBar.success(
                "方案已保存",
                f"{name}（{len(self._enabled_task_snapshot())} 个启用任务）",
                parent=self.window(), duration=2500)

    def _load_profile_dialog(self):
        names = self._profiles(self._cur_pkg())
        if not names:
            InfoBar.warning("没有方案", "当前软件（package）下还没有方案，先用「存为方案」保存",
                            parent=self.window(), duration=2500)
            return
        if self.dirty:
            c = Dialog("当前有未保存修改", "切换方案将基于当前数据覆盖，继续？",
                       self.window())
            if not c.exec():
                return
        dlg = _ProfilePickDialog(names, self.window())
        if not dlg.exec():
            return
        name = dlg.picked_name
        if name:
            self._apply_profile(name)

    # ============================================================ 保存
    def _confirm_ignore_errors(self, errs, verb):
        dlg = Dialog(f"存在 {len(errs)} 个校验问题，仍要{verb}吗？",
                     "\n".join(errs[:5]), self.window())
        return dlg.exec()

    def _confirm_reload(self):
        if self.dirty:
            dlg = Dialog("放弃当前未保存的修改？", "将从磁盘重新加载 task_config.json",
                         self.window())
            if not dlg.exec():
                return
        self._reload_from_disk()

    def _save(self):
        if not self.doc and not self.type_groups:
            return
        errs = validate_doc(self.doc, self.type_groups)
        if errs and not self._confirm_ignore_errors(errs, "保存"):
            return
        path = Path(DEFAULT_CONFIG_PATH)
        tpath = Path(DEFAULT_TYPE_PATH)
        try:
            if path.exists():
                shutil.copy2(path, str(path) + self.BACKUP_SUFFIX)
            path.write_text(self._dump(), encoding="utf-8")
            if self.types_dirty:
                if tpath.exists():
                    shutil.copy2(tpath, str(tpath) + self.BACKUP_SUFFIX)
                tpath.write_text(self._dump_types(), encoding="utf-8")
        except Exception as e:
            self.logger.error(f"保存失败: {e}")
            InfoBar.error("保存失败", str(e)[:160], parent=self.window(), duration=4000)
            return
        self.dirty = False
        self.types_dirty = False
        self._refresh_status()
        if self._src_dirty:
            self._refresh_source()
        InfoBar.success("已保存", "task_config.json + task_type.json",
                        parent=self.window(), duration=2500)

    def _toggle(self, body, key, btn):
        open_ = not body.isVisible()
        body.setVisible(open_)
        if open_:
            self.expanded.add(key)
        else:
            self.expanded.discard(key)
        btn.setIcon(FluentIcon.CARE_UP_SOLID if open_ else FluentIcon.CARE_DOWN_SOLID)
        # 显隐切换后触发布局重算（updateGeometry 只失效缓存，不锁死尺寸）
        card = body.parentWidget()
        if card is not None:
            card.layout().activate()
            card.updateGeometry()

    def _set_num(self, obj, key, text):
        for cast in (int, float):
            try:
                obj[key] = cast(text)
                self._mark_dirty()
                return
            except (ValueError, TypeError):
                continue

    def _set_bool(self, obj, key, on):
        obj[key] = bool(on)
        self._mark_dirty()

    def _set_str(self, obj, key, text):
        if obj.get(key) == text:
            return
        obj[key] = text
        self._mark_dirty()


class _BatchEnableDialog(QDialog):
    """批量多选启用/停用任务实例。results: [(task, bool), …]"""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量启用任务")
        self.results = []
        lay = QVBoxLayout(self)
        lay.addWidget(SubtitleLabel("勾选要启用的任务（当前用户）"))
        self.listw = QListWidget(self)
        self.listw.setSelectionMode(QAbstractItemView.NoSelection)
        self._boxes = []
        for task, enabled, label in items:
            item = QListWidgetItem()
            self.listw.addItem(item)
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 2, 8, 2)
            ckb = CheckBox(str(label or "?"))
            ckb.setChecked(enabled)
            rl.addWidget(ckb)
            rl.addStretch(1)
            item.setSizeHint(row.sizeHint())
            self.listw.setItemWidget(item, row)
            self._boxes.append((task, ckb))
        lay.addWidget(self.listw)
        bar = QHBoxLayout()
        bar.addStretch(1)
        b_all = PushButton("全选")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none = PushButton("全不选")
        b_none.clicked.connect(lambda: self._set_all(False))
        cancel = PushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = PrimaryPushButton("应用")
        ok.clicked.connect(self._on_ok)
        for w in (b_all, b_none, cancel, ok):
            bar.addWidget(w)
        lay.addLayout(bar)
        self.resize(420, 520)

    def _set_all(self, on):
        for _, ckb in self._boxes:
            ckb.setChecked(on)

    def _on_ok(self):
        self.results = [(task, ckb.isChecked()) for task, ckb in self._boxes]
        self.accept()


class _SaveProfileDialog(QDialog):
    """存为方案：名称输入 + 同 package 已有方案下拉（选择即填名，保存覆盖）。"""

    def __init__(self, existing_names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("存为方案")
        self.name = None
        lay = QVBoxLayout(self)
        lay.addWidget(BodyLabel("方案名称（保存当前应用启用任务的 id 与参数）"))
        self.edit = LineEdit()
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.addWidget(BodyLabel("已有方案:"))
        self.combo = ComboBox(self)
        self.combo.addItem("（选择已有方案覆盖）")
        for n in existing_names:
            self.combo.addItem(n)
        self.combo.currentIndexChanged.connect(self._on_pick)
        row.addWidget(self.combo, 1)
        lay.addLayout(row)
        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = PushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = PrimaryPushButton("保存")
        ok.clicked.connect(self._on_ok)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        lay.addLayout(bar)
        self.resize(420, 150)

    def _on_pick(self, idx):
        if idx > 0:
            self.edit.setText(self.combo.itemText(idx))

    def _on_ok(self):
        self.name = self.edit.text().strip()
        self.accept()



class QFrameHLine(QFrame):
    """1px 水平分隔线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)


class _PickTypeDialog(QDialog):
    """从缺失类型里选一个加为任务实例。"""

    def __init__(self, type_defs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加任务实例")
        self.picked_type = None
        lay = QVBoxLayout(self)
        lay.addWidget(SubtitleLabel("选择要添加的任务类型"))
        self.listw = QListWidget(self)
        self.listw.setSelectionMode(QAbstractItemView.SingleSelection)
        for t in type_defs:
            item = QListWidgetItem(
                f'{t.get("name_prefix") or t.get("type")}  ·  {t.get("description") or ""}')
            item.setData(Qt.UserRole, t.get("type"))
            self.listw.addItem(item)
        self.listw.setCurrentRow(0)
        lay.addWidget(self.listw)
        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = PushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = PrimaryPushButton("添加")
        ok.clicked.connect(self._on_ok)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        lay.addLayout(bar)
        self.resize(420, 440)

    def _on_ok(self):
        item = self.listw.currentItem()
        if item is not None:
            self.picked_type = item.data(Qt.UserRole)
        self.accept()


class _InputTextDialog(QDialog):
    def __init__(self, title, label, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.value = None
        lay = QVBoxLayout(self)
        lay.addWidget(BodyLabel(label))
        self.edit = LineEdit()
        lay.addWidget(self.edit)
        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = PushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = PrimaryPushButton("确定")
        ok.clicked.connect(self._on_ok)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        lay.addLayout(bar)

    def _on_ok(self):
        self.value = self.edit.text().strip()
        self.accept()


class _ProfilePickDialog(QDialog):
    """下拉选择已保存方案并切换；支持删除方案。"""

    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("加载方案")
        self.picked_name = None
        lay = QVBoxLayout(self)
        hint = BodyLabel("选择方案切换（合并：配置中新增的任务会保留）")
        lay.addWidget(hint)
        row = QHBoxLayout()
        self.combo = ComboBox(self)
        self.combo.addItems(names)
        if names:
            self.combo.setCurrentIndex(0)
        row.addWidget(self.combo, 1)
        self._del_btn = TransparentToolButton(FluentIcon.DELETE)
        self._del_btn.setToolTip("删除选中的方案")
        self._del_btn.clicked.connect(self._on_del)
        self._del_btn.setEnabled(bool(names))
        row.addWidget(self._del_btn)
        lay.addLayout(row)
        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = PushButton("取消")
        cancel.clicked.connect(self.reject)
        switch = PrimaryPushButton("切换")
        switch.clicked.connect(self._on_ok)
        bar.addWidget(cancel)
        bar.addWidget(switch)
        lay.addLayout(bar)
        self.resize(380, 120)

    def _on_ok(self):
        self.picked_name = self.combo.currentText() or None
        self.accept()

    def _on_del(self):
        name = self.combo.currentText()
        if not name:
            return
        confirm = Dialog("删除方案", f"删除方案「{name}」？", self)
        if not confirm.exec():
            return
        path = Path("configs") / "task_config_profiles" / f"{name}.json"
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        idx = self.combo.currentIndex()
        self.combo.removeItem(idx)
        self._del_btn.setEnabled(self.combo.count() > 0)
        if self.combo.count() == 0:
            self.reject()
