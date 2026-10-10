# -*- coding: utf-8 -*-
"""
TaskConfigEditorTab —— configs/task_config.json 的原生 GUI 编辑页（qfluentwidgets）。

功能对标 tools/task_config_editor.html（网页版）：
  - 解析 configs/task_config.json，结构契约与 task_config_loader.py 对齐
  - 三个页签：任务实例 / 类型定义 / JSON 源码
    （设备链为低频操作，不单设页签；machines 通过 JSON 源码页编辑）
  - 校验器镜像 loader 规则（type 重复 / 引用未定义类型 / params 键 ⊆ 定义 /
    widget 白名单 / enabled 链存在），保存前提示
  - 保存：Ctrl+S 或按钮直接写回 configs/task_config.json（写前留 .bak 备份）
"""
import copy
import json
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
    DEFAULT_CONFIG_PATH, PARAM_WIDGETS,
)

DEFAULT_KEYS = (
    "count", "max_active", "requires_march_queue",
    "next_trigger_delay", "one_shot", "cron",
)
DKEY_HELP = {
    "count": "任务总执行次数，0 表示不限",
    "max_active": "同时运行的最大实例数",
    "requires_march_queue": "执行时是否占用军队出征队列",
    "next_trigger_delay": "执行完成后到下次触发的间隔秒数",
    "one_shot": "启用后只执行一次然后停用",
    "cron": "Cron 表达式定时触发，留空表示不使用",
}


def _new_type_defaults():
    return {"count": 1, "max_active": 1, "requires_march_queue": False,
            "next_trigger_delay": 86400, "one_shot": False, "cron": ""}


def _fmt_val(v):
    if isinstance(v, bool):
        return "开" if v else "关"
    if isinstance(v, list):
        return "/".join(map(str, v)) if v else "-"
    if v is None:
        return "-"
    return str(v)


def validate_doc(doc):
    """镜像 loader 的结构校验，返回错误字符串列表（空列表 = 通过）。"""
    errs = []
    if not isinstance(doc, dict):
        return ["文档顶层必须是对象"]
    tt = doc.get("task_types")
    if not isinstance(tt, list) or not tt:
        errs.append("task_types 缺失或为空")
    seen = set()
    for i, t in enumerate(tt or []):
        w = f"task_types[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{w}: 条目必须是对象")
            continue
        if not t.get("type"):
            errs.append(f"{w}: 缺 type")
        elif t["type"] in seen:
            errs.append(f'{w}: type 重复 "{t["type"]}"')
        else:
            seen.add(t["type"])
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
    machines = doc.get("machines")
    if not isinstance(machines, list) or not machines:
        errs.append("machines 缺失或为空")
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
                    if t.get("type") not in seen:
                        errs.append(
                            f'{mw} users[{ui_}] tasks[{ti}]: 引用未定义 type "{t.get("type")}"')
                    attrs = set()
                    for x in (tt or []):
                        if isinstance(x, dict) and x.get("type") == t.get("type"):
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

        self.doc = None            # 当前编辑 dict（直接改它，保存时序列化）
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
        self.btn_reload = PushButton(FluentIcon.SYNC, "还原")
        self.btn_reload.clicked.connect(self._confirm_reload)
        self.btn_save = PrimaryPushButton(FluentIcon.SAVE, "保存")
        self.btn_save.clicked.connect(self._save)
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
        self.search = LineEdit()
        self.search.setPlaceholderText("搜索任务…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(170)
        self.search.textChanged.connect(self._on_filter)
        bar.addWidget(self.search)
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
        path = Path(DEFAULT_CONFIG_PATH)
        if not path.exists():
            self.doc = None
            self._refresh_status()
            return
        try:
            text = path.read_text(encoding="utf-8")
            doc = json.loads(text)
        except Exception as e:
            self.logger.error(f"读取 {path} 失败: {e}")
            self.lbl_status.setText(f"读取失败: {e}")
            return
        self.doc = doc
        self._normalize_group_order()
        self.dirty = False
        self._src_dirty = False
        self._refresh_source()
        self._sync_all()
        if show_info:
            InfoBar.success("已加载", str(path), parent=self.window(), duration=2000)

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
        for t in (self.doc or {}).get("task_types") or []:
            if isinstance(t, dict) and t.get("type") == type_:
                return t
        return None

    def _mark_dirty(self):
        self.dirty = True
        self._refresh_status()

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
        errs = validate_doc(self.doc)
        dirty = " ● 未保存" if self.dirty else ""
        self.lbl_status.setText(
            f"configs/task_config.json{dirty} ｜ 校验: {len(errs)} 个问题")
        self.lbl_status.setToolTip("\n".join(errs[:10]))
        self.btn_save.setEnabled(True)
        self.btn_reload.setEnabled(True)

    def _sync_all(self):
        """文档级变更后的同步：任务页立即刷（当前页），其余页标脏等切换时再建。"""
        self._clamp_sel()
        self._refresh_status()
        self._refresh_chain_combos()
        self._stale_pages = {"types", "machines", "source"}
        self._refresh_tasks_page()
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

    def _group_header(self, text, objname):
        h = self._sec_header(text)
        h.setObjectName(objname)
        return h

    def _card_for(self, tasks, i, cards):
        task = tasks[i]
        tt = self._type_def(task.get("type"))
        if not tt:
            bad = CardWidget()
            bad.setObjectName("_UnknownTypeCard")
            bl = QHBoxLayout(bad)
            bl.addWidget(BodyLabel(
                f'未知类型: {task.get("type")}（定义缺失，运行会报错）'))
            return bad
        key = task.get("type")
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
            tt.get("name_prefix"), task.get("type"), tt.get("description")])).lower()
        card.setVisible(not self.filter_text or self.filter_text.lower() in hay)
        return card


    def _task_card(self, task, tt, index):
        key = (self.sel["m"], self.sel["a"], self.sel["u"], task.get("type"))
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # 头部：标题/描述/统计 + 启用开关 + 展开按钮
        head = QHBoxLayout()
        info = QVBoxLayout()
        info.addWidget(StrongBodyLabel(tt.get("name_prefix") or task.get("type") or "?"))
        if tt.get("description"):
            desc = BodyLabel(tt["description"])
            desc.setWordWrap(True)
            info.addWidget(desc)
        stats = BodyLabel(self._task_stats(task, tt))
        stats.setWordWrap(True)
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
        # 缓存复用时的刷新钩子
        card._refresh_state = lambda t=task, d=tt, s=stats, w=sw: (
            s.setText(self._task_stats(t, d)), w.blockSignals(True),
            w.setChecked(bool(t.get("enabled"))), w.blockSignals(False))
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
        """卡片头部统计行。"""
        bits = []
        c = task.get("count", 0)
        bits.append("次数不限" if c == 0 else f"{c} 次")
        bits.append(f'并发 {task.get("max_active", 1)}')
        bits.append("单次" if task.get("one_shot")
                    else f'间隔 {task.get("next_trigger_delay", 0)}s')
        if task.get("cron"):
            bits.append("cron")
        if task.get("requires_march_queue"):
            bits.append("占队列")
        ps = []
        tparams = task.get("params") or {}
        for pd in tt.get("params") or []:
            attr = pd.get("attr")
            v = tparams.get(attr, pd.get("default"))
            ps.append(f'{pd.get("name")}: {_fmt_val(v)}')
        return " · ".join(bits) + ("　｜　" + " · ".join(ps) if ps else "")

    def _defaults_rows(self, obj, default_obj):
        """六个调度键的编辑行。obj 是任务实例或类型 defaults dict。"""
        rows = []

        def num_row(key):
            e = LineEdit()
            e.setText(str(obj.get(key, 0)))
            return self._field_row(
                {"count": "执行次数", "max_active": "最大并发",
                 "next_trigger_delay": "触发间隔(秒)"}[key],
                DKEY_HELP[key], e, fixed_width=140,
                diff_ref=(lambda o=obj, k=key: o.get(k, 0),
                          default_obj.get(key, 0)))

        def bool_row(key, label):
            sw = SwitchButton(label)
            sw.setChecked(bool(obj.get(key)))
            return self._field_row(
                label, DKEY_HELP[key], sw,
                diff_ref=(lambda o=obj, k=key: o.get(k, False),
                          default_obj.get(key, False)))

        rows.append(num_row("count"))
        rows.append(num_row("max_active"))
        rows.append(bool_row("requires_march_queue", "占用出征队列"))
        rows.append(num_row("next_trigger_delay"))
        rows.append(bool_row("one_shot", "单次执行"))

        e = LineEdit()
        e.setPlaceholderText("留空 = 不使用")
        rows.append(self._field_row(
            "Cron 表达式", DKEY_HELP["cron"], e, fixed_width=220,
            diff_ref=(lambda o=obj: o.get("cron") or "",
                      default_obj.get("cron") or "")))
        return rows

    def _param_cell(self, task, pd):
        """参数渲染为独立圆角单元格：名称行(含差异圆点) + 控件 + 描述。"""
        attr = pd.get("attr")
        default = pd.get("default")
        tparams = task.get("params") or {}
        value = tparams.get(attr, default)
        wtype = (pd.get("widget") or {}).get("type")

        def commit(v):
            task.setdefault("params", {})[attr] = v
            self._mark_dirty()

        if wtype == "switch" or (wtype is None and isinstance(default, bool)):
            ctl = SwitchButton()
            ctl.setChecked(bool(value))
            ctl.checkedChanged.connect(lambda on: commit(bool(on)))
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
        elif wtype in ("drop_down", "multi_drop_down"):
            opts = list((pd.get("widget") or {}).get("options") or [])
            if wtype == "multi_drop_down":
                wrap = QWidget()
                wl = QHBoxLayout(wrap)
                wl.setContentsMargins(0, 0, 0, 0)
                cur = [str(x) for x in (value or [])]
                for opt in opts:
                    ckb = CheckBox(str(opt))
                    ckb.setChecked(str(opt) in cur)
                    ckb.stateChanged.connect(
                        lambda _s, o=opt, c=ckb: self._multi_toggle(task, attr, o, c.isChecked()))
                    wl.addWidget(ckb)
                wl.addStretch(1)
                ctl = wrap
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
                    lambda txt: self._drop_changed(task, attr, txt, opts))
            else:
                ctl = LineEdit()
                ctl.setText("" if value is None else str(value))
                ctl.textChanged.connect(lambda v: commit(v))
        else:
            ctl = LineEdit()
            ctl.setText("" if value is None else str(value))
            ctl.textChanged.connect(lambda v: commit(v))

        desc = pd.get("desc") or ""
        box = self._param_box(pd.get("name") or attr or "?", ctl, desc,
                              (lambda: (task.get("params") or {}).get(attr), default))
        if desc:
            dlab = BodyLabel(desc)
            dlab.setWordWrap(True)
            dlab.setStyleSheet("color:gray;font-size:12px;")
            box.layout().addWidget(dlab)
        return box

    def _defaults_rows(self, obj, default_obj):
        """六个调度键的编辑行。obj 是任务实例或类型 defaults dict。"""
        rows = []

        def num_row(key):
            e = LineEdit()
            e.setText(str(obj.get(key, 0)))
            e.setFixedWidth(140)
            e.textChanged.connect(lambda v, o=obj, k=key: self._set_num(o, k, v))
            return self._field_row(
                {"count": "执行次数", "max_active": "最大并发",
                 "next_trigger_delay": "触发间隔(秒)"}[key],
                DKEY_HELP[key], e,
                (lambda o=obj, k=key: o.get(k, 0), default_obj.get(key, 0)))

        def bool_row(key, label):
            sw = SwitchButton(label)
            sw.setChecked(bool(obj.get(key)))
            sw.checkedChanged.connect(lambda on, o=obj, k=key: self._set_bool(o, k, on))
            return self._field_row(
                label, DKEY_HELP[key], sw,
                (lambda o=obj, k=key: o.get(k, False), default_obj.get(key, False)))

        rows.append(num_row("count"))
        rows.append(num_row("max_active"))
        rows.append(bool_row("requires_march_queue", "占用出征队列"))
        rows.append(num_row("next_trigger_delay"))
        rows.append(bool_row("one_shot", "单次执行"))

        e = LineEdit()
        e.setText(obj.get("cron") or "")
        e.setPlaceholderText("留空 = 不使用")
        e.setFixedWidth(220)
        e.textChanged.connect(lambda v, o=obj: self._set_str(o, "cron", v))
        rows.append(self._field_row(
            "Cron 表达式", DKEY_HELP["cron"], e,
            (lambda o=obj: o.get("cron") or "", default_obj.get("cron") or "")))
        return rows

    def _param_row(self, task, pd):
        attr = pd.get("attr")
        default = pd.get("default")
        tparams = task.get("params") or {}
        value = tparams.get(attr, default)
        wtype = (pd.get("widget") or {}).get("type")

        def commit(v):
            task.setdefault("params", {})[attr] = v
            self._mark_dirty()

        if wtype == "switch" or (wtype is None and isinstance(default, bool)):
            ctl = SwitchButton()
            ctl.setChecked(bool(value))
            ctl.checkedChanged.connect(lambda on: commit(bool(on)))
        elif wtype == "number" or (
                wtype is None and isinstance(default, (int, float)) and not isinstance(default, bool)):
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
        elif wtype in ("drop_down", "multi_drop_down"):
            opts = list((pd.get("widget") or {}).get("options") or [])
            if wtype == "multi_drop_down":
                wrap = QWidget()
                wl = QHBoxLayout(wrap)
                wl.setContentsMargins(0, 0, 0, 0)
                cur = [str(x) for x in (value or [])]
                for opt in opts:
                    ckb = CheckBox(str(opt))
                    ckb.setChecked(str(opt) in cur)
                    ckb.stateChanged.connect(lambda _s, o=opt, c=ckb: self._multi_toggle(task, attr, o, c.isChecked()))
                    wl.addWidget(ckb)
                wl.addStretch(1)
                ctl = wrap
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
                    lambda txt: self._drop_changed(task, attr, txt, opts))
            else:
                ctl = LineEdit()
                ctl.setText("" if value is None else str(value))
                ctl.textChanged.connect(lambda v: commit(v))
        else:
            ctl = LineEdit()
            ctl.setText("" if value is None else str(value))
            ctl.textChanged.connect(lambda v: commit(v))

        return self._field_row(
            pd.get("name") or attr or "?", pd.get("desc") or "", ctl,
            (lambda: (task.get("params") or {}).get(attr), default))

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
        q["continue_after_failure"] = bool(self.caf_switch.isChecked())
        self._mark_dirty()

    def _reset_task(self, task, tt):
        d = tt.get("defaults") or {}
        for k in DEFAULT_KEYS:
            task[k] = copy.deepcopy(d.get(k))
        task["params"] = {pd.get("attr"): copy.deepcopy(pd.get("default"))
                          for pd in tt.get("params") or [] if pd.get("attr")}
        self._mark_dirty()
        self._refresh_tasks_page(keep_cards=True)

    def _remove_task(self, task):
        chain = self._chain()
        if not chain:
            return
        arr = chain[2].get("tasks") or []
        if task in arr:
            arr.remove(task)
        self._card_cache.pop(task.get("type"), None)
        self._mark_dirty()
        self._refresh_tasks_page()

    def _add_task_dialog(self):
        chain = self._chain()
        if not chain or not self.doc:
            return
        have = {t.get("type") for t in chain[2].get("tasks") or []}
        missing = [t for t in self.doc.get("task_types") or []
                   if isinstance(t, dict) and t.get("type") not in have]
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
        d = tt.get("defaults") or _new_type_defaults()
        user.setdefault("tasks", []).append({
            "type": type_, "enabled": False,
            "params": {pd.get("attr"): copy.deepcopy(pd.get("default"))
                       for pd in tt.get("params") or [] if pd.get("attr")},
            "count": d.get("count", 1), "max_active": d.get("max_active", 1),
            "requires_march_queue": d.get("requires_march_queue", False),
            "next_trigger_delay": d.get("next_trigger_delay", 0),
            "one_shot": d.get("one_shot", False), "cron": d.get("cron") or "",
        })
        self.expanded.add((self.sel["m"], self.sel["a"], self.sel["u"], type_))
        self._mark_dirty()
        self._refresh_tasks_page()

    # ==================================================== 类型定义页渲染
    def _refresh_types_page(self):
        with self._suspend_signals():
            self._clear_layout(self.type_list_lay)
            if not self.doc:
                self.type_list_lay.addWidget(BodyLabel("未加载配置"))
                return
            types = self.doc.get("task_types") or []
            if not types:
                self.type_list_lay.addWidget(BodyLabel("暂无类型定义"))
                return
            for tt in types:
                self.type_list_lay.addWidget(self._type_card(tt))
            self.type_list_lay.addStretch(1)

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
        e_type.editingFinished.connect(lambda t=tt, e=e_type: self._rename_type(t, e.text()))
        bl.addWidget(self._field_row("type", "类型标识（重命名会同步实例引用）", e_type))

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
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    for t in u.get("tasks") or []:
                        params = t.get("params")
                        if (t.get("type") == tt.get("type")
                                and isinstance(params, dict) and old in params):
                            params[new_attr] = params.pop(old)
        self._mark_dirty()
        self._card_cache = {}
        self._refresh_tasks_page()

    def _add_param(self, tt):
        n = len(tt.get("params") or []) + 1
        tt.setdefault("params", []).append(
            {"name": "新参数", "attr": f"new_attr_{n}", "default": None,
             "widget": None, "desc": ""})
        self._mark_dirty()
        self._refresh_types_page()

    def _delete_param(self, tt, p):
        params = tt.get("params") or []
        if p in params:
            params.remove(p)
        attr = p.get("attr")
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    for t in u.get("tasks") or []:
                        params = t.get("params")
                        if t.get("type") == tt.get("type") and isinstance(params, dict):
                            params.pop(attr, None)
        self._mark_dirty()
        self._refresh_types_page()
        self._card_cache = {}
        self._refresh_tasks_page()

    def _rename_type(self, tt, new_type):
        new_type = (new_type or "").strip()
        old = tt.get("type")
        if not new_type or new_type == old:
            return
        if any(x.get("type") == new_type
               for x in self.doc.get("task_types") or [] if x is not tt):
            InfoBar.error("type 已存在", new_type, parent=self.window(), duration=2500)
            return
        n = 0
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    for t in u.get("tasks") or []:
                        if t.get("type") == old:
                            t["type"] = new_type
                            n += 1
        self.expanded_types.discard(("T", old))
        tt["type"] = new_type
        self.expanded_types.add(("T", new_type))
        self._mark_dirty()
        self._refresh_types_page()
        self._card_cache = {}
        self._refresh_tasks_page()
        InfoBar.success("已重命名", f"{new_type}（同步 {n} 处实例）",
                        parent=self.window(), duration=2500)

    def _delete_type(self, tt):
        n = 0
        for m in self.doc.get("machines") or []:
            for a in m.get("apps") or []:
                for u in a.get("users") or []:
                    before = len(u.get("tasks") or [])
                    u["tasks"] = [t for t in u.get("tasks") or []
                                  if t.get("type") != tt.get("type")]
                    n += before - len(u.get("tasks") or [])
        types = self.doc.get("task_types") or []
        if tt in types:
            types.remove(tt)
        self.expanded_types.discard(("T", tt.get("type")))
        self._mark_dirty()
        self._refresh_types_page()
        self._card_cache = {}
        self._refresh_tasks_page()
        InfoBar.success("已删除类型", f'{tt.get("type")}（清理 {n} 处实例）',
                        parent=self.window(), duration=2500)

    def _add_type_dialog(self):
        if not self.doc:
            return
        dlg = _InputTextDialog("添加类型", "type（唯一标识）", self.window())
        if not dlg.exec():
            return
        type_ = dlg.value
        if not type_:
            return
        if any(t.get("type") == type_ for t in self.doc.get("task_types") or []):
            InfoBar.error("type 已存在", type_, parent=self.window(), duration=2500)
            return
        self.doc.setdefault("task_types", []).append({
            "type": type_, "name_prefix": type_, "description": "",
            "defaults": _new_type_defaults(), "params": []})
        self.expanded_types.add(("T", type_))
        self._mark_dirty()
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
        self.src_edit.setPlainText(self._dump() if self.doc else "")
        self.src_edit.blockSignals(False)
        self._src_dirty = False
        self.lbl_src_hint.setText("与当前数据一致")

    def _on_source_changed(self):
        if self.src_edit.signalsBlocked():
            return
        self._src_dirty = True
        self.lbl_src_hint.setText("有未应用的修改，切走会丢弃")

    def _apply_source(self):
        try:
            doc = json.loads(self.src_edit.toPlainText())
        except Exception as e:
            InfoBar.error("JSON 解析失败", str(e)[:160], parent=self.window(), duration=4000)
            return
        errs = validate_doc(doc)
        if errs and not self._confirm_ignore_errors(errs, "应用"):
            return
        self.doc = doc
        self._normalize_group_order()
        self._src_dirty = False
        self._sync_all()
        self._mark_dirty()
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

    def _switch_page(self, key):
        if key != "source" and self._src_dirty:
            dlg = Dialog("源码有未应用的修改", "放弃并切换？", self.window())
            if not dlg.exec():
                self.pivot.setCurrentItem("source")
                return
            self._refresh_source()
        self.page_tasks.setVisible(key == "tasks")
        self.page_types.setVisible(key == "types")
        self.page_machines.setVisible(key == "machines")
        self.page_source.setVisible(key == "source")

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
        if not self.doc:
            return
        errs = validate_doc(self.doc)
        if errs and not self._confirm_ignore_errors(errs, "保存"):
            return
        path = Path(DEFAULT_CONFIG_PATH)
        try:
            if path.exists():
                shutil.copy2(path, str(path) + self.BACKUP_SUFFIX)
            path.write_text(self._dump(), encoding="utf-8")
        except Exception as e:
            self.logger.error(f"保存失败: {e}")
            InfoBar.error("保存失败", str(e)[:160], parent=self.window(), duration=4000)
            return
        self.dirty = False
        self._refresh_status()
        if self._src_dirty:
            self._refresh_source()
        InfoBar.success("已保存", str(path), parent=self.window(), duration=2500)

    def _toggle(self, body, key, btn):
        open_ = not body.isVisible()
        body.setVisible(open_)
        if open_:
            self.expanded.add(key)
        else:
            self.expanded.discard(key)
        btn.setIcon(FluentIcon.CARE_UP_SOLID if open_ else FluentIcon.CARE_DOWN_SOLID)

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
