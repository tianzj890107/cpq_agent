# -*- coding: utf-8 -*-
"""
配置报价 CPQ —— 一体化服务（静态前端 + 三个智能体）

把原来的 4 个进程整合成 1 个：
  - serve.py            静态页面        (原 :8010)
  - cpq_agent_server.py 报价助手 Agent  (原 :47292)  ->  /agents/quote/api/*
  - xbom_agent_server.py 配置助手 Agent (原 :47294)  ->  /agents/config/api/*
  - rule_agent_server.py 规则助手 Agent (原 :47296)  ->  /agents/rule/api/*

设计：
  - 单端口（默认 8010，与原 serve.py 一致）。根路径 / 直接返回首页。
  - 三个 Agent 模块原样 import（不复制逻辑）：本文件只做路由——把
    /agents/<name>/... 前缀剥掉后，直接调用对应模块 Handler 的 do_GET/do_POST，
    SSE 流式、历史、设置、导入数据库等接口全部复用原实现。
  - 各 Agent 仍使用各自的设置/历史文件（cpq_settings.json + cpq_history/、
    xbom_settings.json + xbom_history/、rule_settings.json + rule_history/），互不干扰。
  - 静态部分等价 serve.py（/ -> 报价首页.html），并额外**拒绝**下载
    settings/history/database/源码 等敏感文件。
  - 原三个独立服务脚本保留，仍可单独运行（前端 localStorage 可覆盖 Agent 地址）。

启动：
    open-claude/.venv/Scripts/python cpq_suite_server.py            # 默认 8010
    open-claude/.venv/Scripts/python cpq_suite_server.py --port 8020
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 与各 agent main() 一致：兜底只读文件系统（须在构建 Bridge 前设置）
os.environ["OC_READONLY_FS"] = "1"

# 三个 Agent 模块（import 即完成各自的工具注入/execute_tool 补丁，三者链式共存：
# 各自只接管自己的工具名；sql_query 三份实现等价、都是同库只读，谁接都一样）
import cpq_agent_server as quote_agent    # noqa: E402
import xbom_agent_server as config_agent  # noqa: E402
import rule_agent_server as rule_agent    # noqa: E402
import cpq_auth                            # noqa: E402  登录与角色系统（/auth/*）
import cpq_wf                              # noqa: E402  报价工作流：卡片/步骤/任务（/wf/*）
import cpq_image_server                    # noqa: E402  产品图片维护服务（独立端口，见下）

AGENTS = {
    "quote": quote_agent,
    "config": config_agent,
    "rule": rule_agent,
}
AGENT_LABELS = {"quote": "报价助手", "config": "配置助手", "rule": "规则助手"}

_AGENT_RE = re.compile(r"^/agents/(quote|config|rule)(/.*)?$")

# ---------------------------------------------------------------- 技术工艺 App（反向代理）
# 第四个助手「技术工艺」= 照搬 process_drawing 的 FastAPI 全链路（tech_app/），由本服务
# 作为子进程在 127.0.0.1:TECH_PORT 拉起 uvicorn，并把 /api/* /apps/* 及其前端页面反向代理
# 过去（本服务 root 不使用 /api，互不冲突）。大模型走 tech_app_launch.py 复用的 CPQ 网关。
import http.client  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

TECH_HOST = "127.0.0.1"
TECH_PORT = int(os.getenv("TECH_APP_PORT", "8012"))
# 产品图片维护服务：独立端口（默认 8011），与本服务同进程、后台线程启动
IMAGE_PORT = int(os.getenv("CPQ_IMAGE_PORT", "8011"))
_tech_proc = None
# 反向代理到 tech_app 的路径前缀（root 的 /api、/apps 归 tech_app；本服务 root 只用 /agents 和静态文件）
_TECH_PREFIXES = ("/api/", "/apps/", "/api", "/apps")

# ---------------------------------------------------------------- 静态文件
HOME_PAGE = "报价首页.html"  # 入口直达三智能体整合页（原登录页 首页.html 已弃用，2026-07-16）

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

# 不允许通过 HTTP 下载的内容（settings 里有 API Key 明文；history/库/源码也不该暴露）
_BLOCKED_DIRS = {
    "cpq_history", "xbom_history", "rule_history", "tech_history",
    "database", "open-claude", "cpq_data", "tech_app", "__pycache__", ".git",
}
_BLOCKED_EXTS = {".py", ".pyc", ".sqlite", ".db"}
_BLOCKED_FILES = {"cpq_settings.json", "xbom_settings.json", "rule_settings.json", "tech_settings.json"}


def _resolve_static(path: str):
    """请求路径 -> 本目录内真实文件；目录穿越/敏感文件返回 None。"""
    rel = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0]).lstrip("/")
    if rel in ("", "/"):
        rel = HOME_PAGE
    full = os.path.normpath(os.path.join(SCRIPT_DIR, rel))
    if not full.startswith(SCRIPT_DIR):
        return None
    parts = os.path.relpath(full, SCRIPT_DIR).replace("\\", "/").split("/")
    if parts and parts[0] in _BLOCKED_DIRS:
        return None
    name = os.path.basename(full)
    if name in _BLOCKED_FILES or os.path.splitext(name)[1].lower() in _BLOCKED_EXTS:
        return None
    return full


# ---------------------------------------------------------------- HTTP 路由
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _dispatch_agent(self, method_name: str) -> bool:
        """匹配 /agents/<name>/... 前缀；命中则剥掉前缀，把本次请求整体交给对应
        Agent 模块的 Handler 处理（临时切换 __class__，让 _send_json/_handle_send
        及模块全局 bridge 等全部解析到该模块），处理完恢复。"""
        m = _AGENT_RE.match(self.path)
        if not m:
            return False
        mod = AGENTS[m.group(1)]
        self.path = m.group(2) or "/"
        self.__class__ = mod.Handler
        try:
            getattr(self, method_name)()
        finally:
            self.__class__ = Handler
        return True

    # ------------------------------------------------------------ 技术工艺 App 反向代理
    def _is_tech_path(self) -> bool:
        p = self.path.split("?", 1)[0]
        return p.startswith(_TECH_PREFIXES)

    def _proxy_tech(self):
        """把当前请求整体转发到 tech_app（127.0.0.1:TECH_PORT），响应原样回传。
        用于 /api/* /apps/*（所有方法）以及 root 上非本服务静态文件的前端页面（GET）。
        tech_app 未就绪/断开时回 502，且吞掉写回客户端时的断链异常，避免污染日志。"""
        # 无论成败都要读掉请求体，否则 keep-alive 连接错位
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        # 透传除 Host/连接管理外的请求头
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "connection", "keep-alive",
                                        "proxy-connection", "transfer-encoding")}
        headers["Host"] = f"{TECH_HOST}:{TECH_PORT}"
        conn = None
        try:
            conn = http.client.HTTPConnection(TECH_HOST, TECH_PORT, timeout=600)
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
            hdrs = [(k, v) for k, v in resp.getheaders()
                    if k.lower() not in ("connection", "keep-alive", "transfer-encoding",
                                         "content-length", "proxy-connection")]
        except Exception:
            # tech_app 未启动（本机缺 fastapi/uvicorn 或还在启动中）或中途断开：干净回 502
            self._safe_send(502, "技术工艺服务暂不可用：tech_app 未就绪。"
                            "本机调试需在运行 cpq_suite_server 的同一 Python 里安装 "
                            "fastapi uvicorn[standard] python-multipart sqlalchemy python-dotenv pydantic。"
                            .encode("utf-8"))
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        try:
            self.send_response(status)
            for k, v in hdrs:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端已断开（常见于页面跳转），忽略

    def _safe_send(self, code, body):
        try:
            self._send_raw(code, body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ------------------------------------------------------------ 登录与角色系统 /auth/*
    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except Exception:
            return {}

    def _token(self) -> str:
        """令牌来自 Authorization: Bearer <token>（前端存 localStorage，
        用 header 而非 Cookie，不受 SameSite 限制，跨源/内嵌场景同样可用）。"""
        auth = self.headers.get("Authorization", "") or ""
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    def _client_ip(self) -> str:
        fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return (fwd or self.client_address[0] or "")[:45]

    def _dispatch_auth(self) -> bool:
        """/auth/* 登录与角色接口。命中返回 True。"""
        # 注意：根路径 "/" 去掉尾斜杠后是空串，绝不能兜底成 "/auth"，否则首页会被当成认证接口而 404
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if not path.startswith("/auth"):
            return False
        m = self.command
        # 角色字典是代码里的静态常量、当前登录态查询也能安全降级，二者都**不依赖数据库**，
        # 必须放在下面的 503 守卫之前——否则库一断，注册表单的角色下拉会是空的、根本没法选。
        if path == "/auth/roles" and m == "GET":
            self._send_json(200, {"roles": [{"role_code": k, "role_name": v}
                                            for k, v in cpq_auth.ROLES.items()],
                                  "ready": cpq_auth._backend is not None})
            return True
        if path == "/auth/me" and m == "GET":
            self._send_json(200, {"user": cpq_auth.whoami(self._token()),
                                  "storage": cpq_auth.backend_info()})
            return True
        if cpq_auth._backend is None:      # 需要落库的接口：后端没起来就明确回 503，不装作能用
            self._send_json(503, {"ok": False, "error":
                                  "登录系统未就绪：未能连接服务器 Postgres，请联系管理员检查网络与数据库配置。"})
            return True
        try:
            if path == "/auth/register" and m == "POST":
                d = self._read_json()
                user = cpq_auth.register(d.get("username", ""), d.get("password", ""),
                                         d.get("display_name", ""), d.get("role_code", ""),
                                         d.get("email", ""))
                # 注册成功直接发放会话，免去再登录一次
                out = cpq_auth.login(d.get("username", ""), d.get("password", ""), self._client_ip())
                self._send_json(200, {"ok": True, "user": user, "token": out["token"]})
            elif path == "/auth/login" and m == "POST":
                d = self._read_json()
                out = cpq_auth.login(d.get("username", ""), d.get("password", ""), self._client_ip())
                self._send_json(200, {"ok": True, "user": out["user"], "token": out["token"]})
            elif path == "/auth/logout" and m == "POST":
                cpq_auth.logout(self._token())
                self._send_json(200, {"ok": True})
            elif path == "/auth/users" and m == "GET":
                # 供后续「按角色搜索并派发任务」使用；需登录
                if not cpq_auth.whoami(self._token()):
                    self._send_json(401, {"ok": False, "error": "未登录"})
                else:
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    self._send_json(200, {"users": cpq_auth.list_users((q.get("role") or [""])[0])})
            else:
                self._send_json(404, {"ok": False, "error": "未知接口"})
        except cpq_auth.AuthError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            print(f"[cpq-suite] /auth 出错: {e}", file=sys.stderr)
            self._send_json(500, {"ok": False, "error": "服务异常，请稍后重试"})
        return True

    # ------------------------------------------------------------ 报价工作流 /wf/*
    def _dispatch_wf(self) -> bool:
        """/wf/* 卡片 / 步骤状态 / 任务流转（仅报价助手）。命中返回 True。"""
        parsed = urllib.parse.urlparse(self.path)
        # 同上：根路径不能兜底成 "/wf"
        path = parsed.path.rstrip("/") or "/"
        if not path.startswith("/wf"):
            return False
        q = urllib.parse.parse_qs(parsed.query)
        arg = lambda k: (q.get(k) or [""])[0]  # noqa: E731
        if cpq_auth._backend is None:      # 同上：工作流与登录共用同一后端
            self._send_json(503, {"ok": False, "error":
                                  "工作流未就绪：未能连接服务器 Postgres。"})
            return True
        m = self.command
        try:
            user = cpq_auth.whoami(self._token())
            if path == "/wf/steps" and m == "GET":
                self._send_json(200, {"steps": cpq_wf.step_perms(),
                                      "role_code": (user or {}).get("role_code", "")})
                return True
            # 以下接口都要求登录
            if not user:
                self._send_json(401, {"ok": False, "error": "请先登录"})
                return True
            if path == "/wf/cards" and m == "GET":
                self._send_json(200, {"cards": cpq_wf.my_cards(user)})
            elif path == "/wf/tasks" and m == "GET":
                self._send_json(200, {"tasks": cpq_wf.inbox(user)})
            elif path == "/wf/card" and m == "GET":
                self._send_json(200, cpq_wf.card_detail(arg("session_id"), user))
            elif path == "/wf/card/sync" and m == "POST":
                d = self._read_json()
                card = cpq_wf.sync_card(d.get("session_id", ""), user, d.get("title", ""),
                                        d.get("customer", ""), d.get("project_name", ""),
                                        d.get("current_step"))
                self._send_json(200, {"ok": True, "card": card})
            elif path == "/wf/card/step-data" and m == "GET":
                # 某一步确认时的表单快照：接手人用它恢复前面步骤的表格
                self._send_json(200, {"ok": True,
                                      "data": cpq_wf.step_snapshot(arg("session_id"), arg("step_no"))})
            elif path == "/wf/card/step-start" and m == "POST":
                d = self._read_json()
                card = cpq_wf.start_step(d.get("session_id", ""), d.get("step_no"), user)
                self._send_json(200, {"ok": True, "card": card})
            elif path == "/wf/card/step-done" and m == "POST":
                d = self._read_json()
                out = cpq_wf.complete_step(d.get("session_id", ""), d.get("step_no"), user,
                                           d.get("snapshot", ""))
                self._send_json(200, {"ok": True, **out})
            elif path == "/wf/task/send" and m == "POST":
                d = self._read_json()
                out = cpq_wf.send_task(d.get("session_id", ""), user, d.get("target_type", ""),
                                       d.get("target_role_code", ""), d.get("target_user_id", ""),
                                       d.get("note", ""))
                self._send_json(200, {"ok": True, **out})
            elif path == "/wf/messages" and m == "GET":
                self._send_json(200, cpq_wf.messages(user, int(arg("limit") or 50)))
            elif path == "/wf/messages/read" and m == "POST":
                d = self._read_json()
                self._send_json(200, {"ok": True, "updated": cpq_wf.mark_read(user, d.get("ids"))})
            elif path == "/wf/task/claim" and m == "POST":
                d = self._read_json()
                out = cpq_wf.claim_task(d.get("task_id", ""), user)
                self._send_json(200, {"ok": True, **out})
            else:
                self._send_json(404, {"ok": False, "error": "未知接口"})
        except cpq_wf.WfError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            print(f"[cpq-suite] /wf 出错: {e}", file=sys.stderr)
            self._send_json(500, {"ok": False, "error": "服务异常，请稍后重试"})
        return True

    def do_OPTIONS(self):
        if self._dispatch_agent("do_OPTIONS"):
            return
        if self._is_tech_path():
            self._proxy_tech()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch_product_image(self) -> bool:
        """/product-image/<成品编码> —— 同源取产品图片（复用 8011 的 product_images/ 目录）。
        报价第 1 步的推荐清单在对话框里点「图片」就走这里，避免跨端口/CORS。"""
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/product-image/"):
            return False
        code = urllib.parse.unquote(parsed.path[len("/product-image/"):])
        fn = cpq_image_server._find_image(code)
        if not fn:
            self._safe_send(404, "该产品尚未上传图片".encode("utf-8"))
            return True
        full = os.path.join(cpq_image_server.IMAGE_DIR, fn)
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._safe_send(404, b"not found")
            return True
        import mimetypes
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        self._send_raw(200, data, ctype, length=len(data))
        return True

    def do_GET(self):
        if self._dispatch_agent("do_GET"):
            return
        if self._dispatch_auth():
            return
        if self._dispatch_product_image():
            return
        if self._dispatch_wf():
            return
        if self._is_tech_path():
            self._proxy_tech()
            return
        # 本服务自己的静态文件优先；命不中则交给 tech_app 前端（home.html/*.js/*.css 等）
        full = _resolve_static(self.path)
        if full and os.path.isfile(full):
            self._serve_static()
        else:
            self._proxy_tech()

    def do_HEAD(self):
        if _AGENT_RE.match(self.path):
            self.send_error(405)
            return
        if self._is_tech_path():
            self._proxy_tech()
            return
        full = _resolve_static(self.path)
        if full and os.path.isfile(full):
            self._serve_static(head=True)
        else:
            self._proxy_tech()

    def do_POST(self):
        if self._dispatch_agent("do_POST"):
            return
        if self._dispatch_auth():
            return
        if self._dispatch_wf():
            return
        if self._is_tech_path():
            self._proxy_tech()
            return
        self.send_error(404)

    def do_PUT(self):
        if self._is_tech_path():
            self._proxy_tech()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self._is_tech_path():
            self._proxy_tech()
        else:
            self.send_error(404)

    def do_PATCH(self):
        if self._is_tech_path():
            self._proxy_tech()
        else:
            self.send_error(404)

    def _serve_static(self, head: bool = False):
        full = _resolve_static(self.path)
        if not full or not os.path.isfile(full):
            self._send_raw(404, "404 Not Found".encode("utf-8"))
            return
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            self._send_raw(500, "500 Internal Server Error".encode("utf-8"))
            return
        ctype = MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        self._send_raw(200, b"" if head else body, ctype, length=len(body))

    def _send_raw(self, code, body, ctype="text/plain; charset=utf-8", length=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length if length is not None else len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        # 允许跨源读取/内嵌
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)


# ---------------------------------------------------------------- HTTP 服务器
class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """吞掉客户端断链造成的噪音 traceback，其余错误照常打印。

    浏览器关闭 keep-alive 空闲连接（切页/刷新/关标签页）时，服务端正阻塞在
    handle_one_request 的 readline 上，会抛 ConnectionAbortedError(WinError 10053) /
    ConnectionResetError。这类异常发生在“等待下一个请求”阶段，没有请求失败、
    也没有响应丢失，但 socketserver 默认会打整段 traceback，把真正的错误淹没掉。
    """

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            BrokenPipeError, TimeoutError)):
            return  # 正常的客户端断开，静默忽略
        super().handle_error(request, client_address)


# ---------------------------------------------------------------- 技术工艺 App 子进程
def _start_tech_app():
    """拉起 tech_app（FastAPI/uvicorn）子进程；缺依赖/缺目录则跳过（前端访问时代理回 502）。"""
    global _tech_proc
    launcher = os.path.join(SCRIPT_DIR, "tech_app_launch.py")
    if not os.path.isdir(os.path.join(SCRIPT_DIR, "tech_app")) or not os.path.isfile(launcher):
        print("[cpq-suite] 未找到 tech_app/，技术工艺 App 未启动。")
        return
    try:
        _tech_proc = subprocess.Popen(
            [sys.executable, launcher, "--host", TECH_HOST, "--port", str(TECH_PORT)],
            cwd=SCRIPT_DIR,
        )
    except Exception as e:
        print(f"[cpq-suite] 技术工艺 App 启动失败：{e}", file=sys.stderr)
        return

    def _probe():
        for _ in range(60):  # 最多等 ~30s
            time.sleep(0.5)
            try:
                c = http.client.HTTPConnection(TECH_HOST, TECH_PORT, timeout=2)
                c.request("GET", "/api/health")
                if c.getresponse().status < 500:
                    print(f"[cpq-suite] 技术工艺 App 就绪  http://{TECH_HOST}:{TECH_PORT}  "
                          f"(经 /home.html /api/* 反向代理)")
                    return
            except Exception:
                continue
        print("[cpq-suite] 警告: 技术工艺 App 30s 内未就绪（可能在装依赖或缺 fastapi）。", file=sys.stderr)

    threading.Thread(target=_probe, daemon=True).start()


def _stop_tech_app():
    if _tech_proc is not None:
        try:
            _tech_proc.terminate()
            _tech_proc.wait(timeout=5)
        except Exception:
            try:
                _tech_proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------- 初始化
def _init_agent(name: str, mod):
    """复刻各 agent 模块 main() 里 Bridge 之前的初始化，并构建 bridge。"""
    saved = mod.load_settings()
    mod.apply_saved_provider_keys()
    if saved.get("model"):
        os.environ["CLAUDE_MODEL"] = mod._sanitize_model(mod.resolve_model(saved["model"]))
    else:
        eff = mod._pick_model_by_available_key(mod.get_model())
        if eff != mod.get_model():
            os.environ["CLAUDE_MODEL"] = mod._sanitize_model(eff)
            print(f"[cpq-suite] {AGENT_LABELS[name]}: 默认模型无可用 Key，已切换到 {eff}")
    mod.bridge = mod.Bridge(mod.SCRIPT_DIR)
    if (mod.bridge.conv.model != mod.NO_MODEL_ID
            and not mod.get_api_key_for(mod.get_model_provider(mod.bridge.conv.model))):
        print(
            f"[cpq-suite] 警告: {AGENT_LABELS[name]} 当前模型 {mod.bridge.conv.model} "
            f"未配置 API Key，可在该页面「设置」里填写。",
            file=sys.stderr,
        )
    print(f"[cpq-suite] {AGENT_LABELS[name]:6s} /agents/{name}/api/*  model={mod.bridge.conv.model}")


def main():
    parser = argparse.ArgumentParser(description="配置报价 CPQ 一体化服务（前端 + 三个智能体）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()

    print(f"[cpq-suite] 工作目录 {SCRIPT_DIR}")
    for name, mod in AGENTS.items():
        _init_agent(name, mod)

    # 跨助手设置同步：任一助手 /api/settings 保存后，广播到另外两个模块
    # （三个模块共用 cpq_settings.json，权威文件只由发起方写一次，其余只应用不回写）
    def _peer(m):
        return lambda data: m.pool_apply_settings(data, persist=False)

    for name, mod in AGENTS.items():
        mod.SETTINGS_PEERS = [_peer(m) for n, m in AGENTS.items() if n != name]

    # 登录与角色系统：账号/角色/卡片/步骤/任务/消息**只写线上 Postgres 的 cpq_wf schema**，
    # 没有任何本地存储回落；连不上就让 /auth/* /wf/* 回 503，绝不静默写本地。
    try:
        print(f"[cpq-suite] 登录系统 /auth/*  存储={cpq_auth.init()}")
        print(f"[cpq-suite] 工作流 /wf/*   {cpq_wf.init()}")
    except cpq_auth.BackendUnavailable as e:
        print(f"[cpq-suite] 错误: {e}", file=sys.stderr)
        print("[cpq-suite] 登录与工作流接口将不可用（/auth/* /wf/* 返回 503），"
              "其余功能正常。请检查网络/VPN 与 CPQ_PG_* 配置。", file=sys.stderr)
    except Exception as e:
        print(f"[cpq-suite] 警告: 登录系统初始化失败：{e}", file=sys.stderr)

    _start_tech_app()  # 技术工艺 App（tech_app/ FastAPI 全链路）子进程 + 反向代理

    # 产品图片维护（独立端口 8011，同进程后台线程；失败只告警不影响主服务）
    if cpq_image_server.start_in_thread(args.host, IMAGE_PORT):
        print(f"[cpq-suite] 产品图片: http://{args.host}:{IMAGE_PORT}/   "
              f"图片目录 {os.path.basename(cpq_image_server.IMAGE_DIR)}/")

    server = QuietThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[cpq-suite] 首页  : http://{args.host}:{args.port}/   ->  {HOME_PAGE}")
    print(f"[cpq-suite] Agent : http://{args.host}:{args.port}/agents/{{quote|config|rule}}/api/send")
    print(f"[cpq-suite] 技术工艺: http://{args.host}:{args.port}/home.html  (代理 tech_app :{TECH_PORT})")
    print("[cpq-suite] Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[cpq-suite] 已停止")
    finally:
        _stop_tech_app()
        for mod in AGENTS.values():
            try:
                mod.bridge.conv.mcp.shutdown()
            except Exception:
                pass
        server.server_close()


if __name__ == "__main__":
    main()
