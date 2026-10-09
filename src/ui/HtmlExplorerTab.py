import json
import logging
import os
import re
import sys
from ctypes import byref, c_int
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action, FluentIcon, LineEdit, RoundMenu, TransparentToolButton, isDarkTheme,
)

from ok import Config
from ok.ui.qt.widget.CustomTab import CustomTab

_BRIDGE_NAME = "pybridge"

logger = logging.getLogger(__name__)


def _ensure_webengine_runtime():
    """Idempotent process-level init required by QtWebEngine.

    MUST be called before QApplication is created. Doing it lazily on first
    HtmlExplorerTab construction is too late (ok.OK() builds QApplication in
    its constructor), so entrypoints call this before creating ok.OK().
    """
    from PySide6.QtWebEngineQuick import QtWebEngineQuick
    QtWebEngineQuick.initialize()


class _Bridge(QObject):
    """QWebChannel bridge between page JS and the tab.

    JS side:
        window.pybridge.call_py(JSON.stringify({name: 'ping', args: [1]}))
        window.pybridge.event.connect(function(payload) { ... })
    """

    event = Signal(str)  # JSON payload pushed Python -> JS

    def __init__(self, tab: "HtmlExplorerTab", parent=None):
        super().__init__(parent)
        self.tab = tab

    @Slot(str)
    def call_py(self, payload: str) -> None:
        """Single JS entry: dispatch {name, args} to HtmlExplorerTab.on_message."""
        try:
            req = json.loads(payload)
            name = str(req.get("name", ""))
            args = req.get("args", [])
            if not isinstance(args, list):
                args = [args]
        except (ValueError, TypeError):
            self.tab.logger.error(f'pybridge invalid payload: {payload!r}')
            return
        try:
            self.tab.on_message(name, args)
        except Exception as e:
            self.tab.logger.error(f'pybridge call_py {name!r} failed: {e}')


class _Page(QWebEnginePage):
    """QWebEnginePage: logs JS console output into the ok logger."""

    def __init__(self, profile, tab: "HtmlExplorerTab", parent=None):
        super().__init__(profile, parent)
        self.tab = tab

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        tag = {0: 'info', 1: 'warning'}.get(int(level), 'error')
        if tag == 'error':
            self.tab.logger.error(f'[js] {message} ({sourceID}:{lineNumber})')
        else:
            self.tab.logger.debug(f'[js:{tag}] {message} ({sourceID}:{lineNumber})')

    def createWindow(self, _type):
        # window.open / target=_blank: delegate to the tab, which owns the
        # profile and the page factory.
        return self.tab._create_popup()


class HtmlExplorerTab(CustomTab):
    """Fluent-styled browser tab embedded in the ok MainWindow.

    The QWebEngineView is created LAZILY on first show, not in __init__.
    Its native child window breaks DWM composition while the Mica backdrop
    is active (black nav/title bands on every tab from startup). Deferring
    creation until the user opens this tab keeps the rest of the GUI
    pristine; at that moment we tear the backdrop down first, then create
    the native child on a normal opaque composition path.

    Features:
      - address bar with history dropdown + reload/stop; accepts bare
        Windows paths (F:\\dir\\file.html) and local files
      - popups (window.open / target=_blank) open in a native dialog that
        can be closed normally (title bar X button / Esc)
      - persistent default URL (ok.Config -> configs/HtmlExplorerTab.json)
      - two-way JS bridge (QWebChannel) on every page incl. popups:
          JS -> Python: window.pybridge.call_py(JSON.stringify({name, args}))
          Python -> JS: window.pybridge.event (JSON string signal)
    """

    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.GLOBE
        self.config = Config(self.__class__.__name__, {
            'default_url': 'https://example.com',
        })
        self.web_view = None
        self.profile = None
        self.page = None
        self.bridge = None
        self.channel = None
        self._popup_dialogs = []  # keeps QDialog references until closed
        self._pending_url = str(self.config.get('default_url') or 'https://example.com')

        # ---- top toolbar -------------------------------------------------
        self.url_edit = LineEdit()
        self.url_edit.setPlaceholderText('https:// or F:\\path\\file.html')
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.returnPressed.connect(self._on_url_entered)

        self.btn_back = TransparentToolButton(FluentIcon.CARE_LEFT_SOLID)
        self.btn_back.setToolTip('Back')
        self.btn_back.clicked.connect(self.web_back)

        self.btn_forward = TransparentToolButton(FluentIcon.CARE_RIGHT_SOLID)
        self.btn_forward.setToolTip('Forward')
        self.btn_forward.clicked.connect(self.web_forward)

        self.btn_reload = TransparentToolButton(FluentIcon.SYNC)
        self.btn_reload.setToolTip('Reload')
        self.btn_reload.clicked.connect(self.web_reload)

        self.btn_history = TransparentToolButton(FluentIcon.HOME)
        self.btn_history.setToolTip('Saved pages')
        self.btn_history.clicked.connect(self._show_history_menu)

        # ---- layout (web view added lazily in _ensure_web_view) -----------
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.setContentsMargins(0, 0, 0, 0)
        for w in (self.btn_back, self.btn_forward, self.btn_reload, self.url_edit,
                  self.btn_history):
            bar.addWidget(w)

        container = QWidget(self.view)
        container.setLayout(bar)

        self.vBoxLayout.addWidget(container)

    # ------------------------------------------------------------------ name
    @property
    def name(self):
        return "Html Explorer"

    # ------------------------------------------------------------- lifecycle
    def showEvent(self, event):
        super().showEvent(event)
        # Tear the Mica backdrop down BEFORE the first native child window
        # exists; then create the WebEngine view on the plain composition path.
        self._disable_mica_backdrop()
        self._ensure_web_view()

    def _disable_mica_backdrop(self):
        window = self.window()
        if window is None or not hasattr(window, 'isMicaEffectEnabled'):
            return
        if not window.isMicaEffectEnabled():
            return
        window.setMicaEffectEnabled(False)
        window.setBackgroundColor(
            QColor(32, 32, 32) if isDarkTheme() else QColor(240, 244, 249))
        self._reset_dwm_backdrop(window)
        self.logger.info('HtmlExplorerTab disabled Mica effect for WebEngine compatibility')

    @staticmethod
    def _reset_dwm_backdrop(window) -> None:
        """Undo everything qframelesswindow.setMicaEffect did to the HWND.

        removeBackgroundEffect alone only disables the accent policy. It leaves:
          - the glass sheet extended into the client area
            (MARGINS 16777215, 16777215, 0, 0 -> the black left/top bands
            around native child windows), and
          - the Mica backdrop attribute active: undocumented attr 1029
            (DWMWA_MICA_EFFECT) on builds < 22523, DWMSBT attr 38 above.
        Reset all three; unknown attribute ids just fail with an HRESULT.
        """
        try:
            from qframelesswindow.windows.c_structures import MARGINS

            window_effect = window.windowEffect
            hWnd = int(window.winId())

            # 1) collapse the DWM glass sheet
            window_effect.DwmExtendFrameIntoClientArea(hWnd, byref(MARGINS(0, 0, 0, 0)))

            # 2) undocumented DWMWA_MICA_EFFECT (1029), used on builds < 22523
            window_effect.DwmSetWindowAttribute(hWnd, 1029, byref(c_int(0)), 4)

            # 3) DWMSBT: 1 = DWMSBT_NONE (builds >= 22523; harmless before)
            window_effect.DwmSetWindowAttribute(hWnd, 38, byref(c_int(1)), 4)

            # 4) accent policy fully disabled (belt and braces)
            window_effect.removeBackgroundEffect(hWnd)
        except Exception as e:
            logger.warning(f'reset dwm backdrop failed: {e}')

    def _ensure_web_view(self):
        """Create profile/page/view/channel on first use."""
        if self.web_view is not None:
            return

        self.profile = QWebEngineProfile(self)

        self.page = _Page(self.profile, self)
        self.web_view = QWebEngineView(self.view)
        self.web_view.setPage(self.page)
        self.web_view.urlChanged.connect(self._on_url_changed)
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.setZoomFactor(1.0)

        self.bridge = self._setup_page(self.page)

        self.vBoxLayout.addWidget(self.web_view, 1)

        if self._pending_url:
            self.web_view.load(QUrl(self._pending_url))

    def _setup_page(self, page: QWebEnginePage) -> _Bridge:
        """Apply settings + QWebChannel bridge + injection scripts to a page.

        Used for the main page and every popup page alike.
        """
        settings: QWebEngineSettings = page.settings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.ShowScrollBars, True)

        bridge = _Bridge(self, page)
        channel = QWebChannel(page)
        channel.registerObject(_BRIDGE_NAME, bridge)
        page.setWebChannel(channel)

        # Inject bridge bootstrap into every frame at document creation.
        script = QWebEngineScript()
        script.setSourceUrl(QUrl('qrc:///qtwebchannel/qwebchannel.js'))
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        page.scripts().insert(script)

        bootstrap = QWebEngineScript()
        bootstrap.setName('pybridge-bootstrap')
        bootstrap.setInjectionPoint(QWebEngineScript.DocumentCreation)
        bootstrap.setWorldId(QWebEngineScript.MainWorld)
        bootstrap.setRunsOnSubFrames(True)
        bootstrap.setSourceCode(f"""
            new QWebChannel(qt.webChannelTransport, function(channel) {{
                window.pybridge = channel.objects.{_BRIDGE_NAME};
            }});
        """)
        page.scripts().insert(bootstrap)
        return bridge

    # ------------------------------------------------------------- popups
    def _create_popup(self) -> QWebEnginePage:
        """window.open()/target=_blank: open a native, closable dialog.

        The previous implementation parented the popup QWebEngineView to the
        tab content, which rendered it as an overlay INSIDE the tab with no
        title bar and no way to close it. A QDialog gives a native frame
        with a working close button (and Esc).
        """
        dlg = QDialog(self.window() or self)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.resize(900, 700)
        dlg.setWindowTitle('Popup')

        view = QWebEngineView(dlg)
        page = _Page(self.profile, self, view)
        view.setPage(page)
        self._setup_page(page)
        page.loadFinished.connect(lambda ok: self.logger.debug(
            f'popup load {"ok" if ok else "failed"}: {page.url().toString()}'))

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(view)
        view.titleChanged.connect(dlg.setWindowTitle)
        dlg.show()

        self._popup_dialogs.append(dlg)
        dlg.destroyed.connect(
            lambda *_: self._popup_dialogs.remove(dlg) if dlg in self._popup_dialogs else None)
        return page

    # ------------------------------------------------------------- navigation
    def navigate(self, url: str) -> None:
        """Navigate to url.

        Accepts: full URLs (https://, file://), bare Windows paths
        (F:\\dir\\file.html, F:/dir/file.html) and existing local paths.
        Anything else gets the https:// prefix.
        """
        url = (url or '').strip().strip('"').strip("'")
        if not url:
            return
        if '://' not in url:
            if re.match(r'^[A-Za-z]:[\\/]', url) or os.path.exists(url):
                self._load(QUrl.fromLocalFile(os.path.abspath(url)))
                return
            url = 'https://' + url
        self._load(QUrl(url))

    def _load(self, qurl: QUrl) -> None:
        self._pending_url = qurl.toString()
        text = qurl.toLocalFile() if qurl.scheme() == 'file' else qurl.toString()
        self.url_edit.setText(text)
        if self.web_view is not None:
            self.web_view.load(qurl)

    def web_back(self):
        if self.web_view is not None:
            self.web_view.back()

    def web_forward(self):
        if self.web_view is not None:
            self.web_view.forward()

    def web_reload(self):
        if self.web_view is not None:
            self.web_view.reload()

    # ------------------------------------------------------------- slots
    def _on_url_entered(self):
        self.navigate(self.url_edit.text())

    def _on_url_changed(self, url: QUrl):
        text = url.toLocalFile() if url.scheme() == 'file' else url.toString()
        self.url_edit.setText(text)
        if url.scheme() in ('http', 'https', 'file'):
            self._record_history(url.toString())

    def _on_load_finished(self, ok: bool):
        if not ok:
            self.logger.error(f'HtmlExplorerTab load failed: {self.web_view.url().toString()}')

    def _record_history(self, url: str) -> None:
        """Keep most-recent-first unique URL list, capped."""
        entries = [e for e in self._history_entries() if e.get('url') != url]
        entries.insert(0, {'url': url, 'title': ''})
        self.config['history'] = entries[:20]

    def _show_history_menu(self):
        menu = RoundMenu(parent=self)
        for entry in self._history_entries():
            url = entry.get('url', '')
            title = entry.get('title') or url
            act = Action(FluentIcon.GLOBE, title[:60], triggered=lambda _=False, u=url: self.navigate(u))
            menu.addAction(act)
        menu.exec(self.btn_history.mapToGlobal(self.btn_history.rect().bottomLeft()))

    def _history_entries(self) -> list[dict]:
        try:
            raw = self.config.get('history', [])
            return raw if isinstance(raw, list) else []
        except Exception:
            return []

    # ------------------------------------------------------- python -> JS
    def push_event(self, name: str, args: list | None = None) -> None:
        """Python -> page JS: emit bridge.event with JSON payload."""
        if self.bridge is None:
            return
        payload = json.dumps({'name': name, 'args': args or []})
        self.bridge.event.emit(payload)

    def run_js(self, code: str, callback: Callable[[Any], None] | None = None) -> None:
        """Evaluate JS in the main frame; optional callback receives the result."""
        if self.page is None:
            return
        self.page.runJavaScript(code, QWebEngineScript.MainWorld, callback)

    # ---------------------------------------------------------- JS -> python
    def on_message(self, name: str, args: list) -> None:
        """Override/handle JS->Python messages here (dispatch point)."""
        self.logger.info(f'pybridge message {name!r} args={args!r}')
        if name == 'ping':
            self.push_event('pong', [args[0] if args else None])

    # ------------------------------------------------------------- lifecycle
    def deleteLater(self):
        # WebEngine needs explicit teardown of profile/channel.
        if self.page is not None:
            try:
                self.page.setWebChannel(None)
            except Exception:
                pass
        if self.profile is not None:
            try:
                self.profile.deleteLater()
            except Exception:
                pass
        super().deleteLater()
