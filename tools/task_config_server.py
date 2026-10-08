# -*- coding: utf-8 -*-
"""
task_config_server —— 静态托管 + task_config.json 保存接口。

用法（项目根目录）：
    python tools/task_config_server.py            # 默认 127.0.0.1:8642
    python tools/task_config_server.py 9000       # 指定端口

替代 `python -m http.server`：
  - 同样的静态托管（tools/ 与 configs/ 之外仅有限白名单）
  - 额外提供 POST /api/task_config：把请求体 JSON 原子写回
    configs/task_config.json，供 task_config_editor.html 保存，
    免去浏览器"另存为"弹窗。
  - 原子写：先写 .tmp 再 os.replace；写前备份到 task_config.json.bak
"""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 项目根目录
CONFIG = ROOT / "configs" / "task_config.json"
HOST, DEFAULT_PORT = "127.0.0.1", 8642

# 静态服务白名单之外的前缀（防目录穿越之外的多余暴露）
STATIC_PREFIXES = ("/tools/", "/configs/", "/tests/", "/scripts/", "/docs/", "/src/", "/.agents/")


class Handler(SimpleHTTPRequestHandler):
    # 以项目根为文档根（编辑器从 /tools/... 加载，并 fetch /configs/task_config.json）
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # ---- 保存接口 ----
    def do_POST(self):
        if self.path.rstrip("/") != "/api/task_config":
            self._json_response(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            doc = json.loads(raw.decode("utf-8"))          # 必须是合法 JSON
        except Exception as e:
            self._json_response(400, {"ok": False, "error": f"bad json: {e}"})
            return
        # 最小结构校验：防止 UI 状态异常时把垃圾写进真实配置
        if not (isinstance(doc, dict) and isinstance(doc.get("task_types"), list)
                and isinstance(doc.get("machines"), list)):
            self._json_response(422, {"ok": False,
                "error": "结构不符：需要包含 task_types[] 与 machines[] 的对象"})
            return
        text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"

        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        try:
            if CONFIG.exists():                            # 写前备份（单份滚动）
                bak = CONFIG.with_suffix(".json.bak")
                bak.write_bytes(CONFIG.read_bytes())
            tmp = CONFIG.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, CONFIG)                        # 原子落盘
        except OSError as e:
            self._json_response(500, {"ok": False, "error": f"write failed: {e}"})
            return
        self._json_response(200, {"ok": True, "path": str(CONFIG.relative_to(ROOT))})

    # ---- 静态文件白名单 ----
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean != "/" and not clean.startswith(STATIC_PREFIXES):
            # 白名单外返回一个必不存在的路径 → 404
            path = "/__denied__" + clean
        return super().translate_path(path)

    def _json_response(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):                     # 静音逐请求日志
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    srv = ThreadingHTTPServer((HOST, port), Handler)
    print(f"task_config server: http://{HOST}:{port}/tools/task_config_editor.html")
    print(f"  save api : POST http://{HOST}:{port}/api/task_config")
    print(f"  target   : {CONFIG}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
