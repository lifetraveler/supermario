import ok

from src.config import config

if __name__ == '__main__':
    from src.ui.HtmlExplorerTab import _ensure_webengine_runtime
    _ensure_webengine_runtime()  # must run before ok.OK() creates QApplication
    ok = ok.OK(config)
    ok.start()
