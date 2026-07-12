# -*- coding: utf-8 -*-
"""
配置报价 CPQ —— 报价首页静态服务

把本文件夹(配置报价CPQ)下的「报价首页.html」及配套静态资源通过 HTTP 暴露出来,
供 EIMOS 产品平台「配置报价管理(CPQ) → 报价管理」菜单以 iframe 内嵌。

设计:
  - 根路径 "/" 直接返回「报价首页.html」,这样 EIMOS iframe 只需指向
    http://127.0.0.1:8010/ ,不必在 URL 里出现中文文件名。
  - 其余路径按文件名在本目录内取静态文件(确认需求解析结果.html 等)。
  - 统一 UTF-8、禁用缓存,便于改完页面刷新即见最新效果。

启动:
    python serve.py               # 默认端口 8010
    python serve.py 8020          # 指定端口
    set CPQ_PORT=8020 && python serve.py   # 或用环境变量

依赖:仅标准库,任意 Python 3 均可运行(无需虚拟环境)。
"""
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Windows 控制台/重定向默认 cp1252,打印中文会崩溃 —— 统一切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME_PAGE = "首页.html"  # 登录/入口页；点「Agent 智能体」进入 报价首页(1).html

MIME = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _resolve(path: str):
    """把请求路径映射到本目录内的真实文件,阻止目录穿越。"""
    rel = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0]).lstrip("/")
    if rel in ("", "/"):
        rel = HOME_PAGE
    full = os.path.normpath(os.path.join(BASE_DIR, rel))
    if not full.startswith(BASE_DIR):
        return None
    return full


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        # 允许被 EIMOS(不同端口)以 iframe 内嵌
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        full = _resolve(self.path)
        if not full or not os.path.isfile(full):
            self._send(404, "404 Not Found".encode("utf-8"))
            return
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            self._send(500, "500 Internal Server Error".encode("utf-8"))
            return
        self._send(200, body, ctype)

    do_HEAD = do_GET

    def log_message(self, fmt, *args):  # 精简日志
        sys.stderr.write("[cpq] %s\n" % (fmt % args))


def main():
    port = int(os.environ.get("CPQ_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8010))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("配置报价 CPQ · 报价首页 服务已启动")
    print("  目录 : %s" % BASE_DIR)
    print("  首页 : http://127.0.0.1:%d/   ->  %s" % (port, HOME_PAGE))
    print("  在 EIMOS「配置报价管理(CPQ) → 报价管理」中以 iframe 内嵌该地址。")
    print("  Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        httpd.server_close()


if __name__ == "__main__":
    main()
