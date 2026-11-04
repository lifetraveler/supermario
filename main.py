import ok

from src.config import config

if __name__ == '__main__':
    try:
        from src.ui.HtmlExplorerTab import _ensure_webengine_runtime
        _ensure_webengine_runtime()  # must run before ok.OK() creates QApplication
    except ModuleNotFoundError:
        # PySide6 WebEngine (pyside6-addons) is trimmed from release requirements;
        # HtmlExplorerTab is disabled in src/config.py custom_tabs, so skip init.
        pass
    ok = ok.OK(config)
    ok.start()
