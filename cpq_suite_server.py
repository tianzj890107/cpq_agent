# -*- coding: utf-8 -*-
"""
配置报价 CPQ —— 一体化服务（静态前端 + 三个智能体）

把原来的 4 个进程整合成 1 个：
  - serve.py            静态页面        (原 :8010)
  - cpq_agent_server.py 报价助手 Agent  (原 :47292)  ->  /agents/quote/api/*
  - xbom_agent_server.py 配置助手 Agent (原 :47294)  ->  /agents/config/api/*
  - rule_agent_server.py 规则助手 Agent (原 :47296)  ->  /agents/rule/api/*

设计：
  - 单端口（默认 8010，与原 serve.py 一致，EIMOS iframe 地址不变）。
  - 三个 Agent 模块原样 import（不复制逻辑）：本文件只做路由——把
    /agents/<name>/... 前缀剥掉后，直接调用对应模块 Handler 的 do_GET/do_POST，
    SSE 流式、历史、设置、导入数据库等接口全部复用原实现。
  - 各 Agent 仍使用各自的设置/历史文件（cpq_settings.json + cpq_history/、
    xbom_settings.json + xbom_history/、rule_settings.json + rule_history/），互不干扰。
  - 静态部分等价 serve.py（/ -> 报价首页(1).html），并额外**拒绝**下载
    settings/history/database/源码 等敏感文件。
  - 原三个独立服务脚本保留，仍可单独运行（前端 localStorage 可覆盖 Agent 地址）。

启动：
    open-claude/.venv/Scripts/python cpq_suite_server.py            # 默认 8010
    open-claude/.venv/Scripts/python cpq_suite_server.py --port 8020
"""

import argparse
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

AGENTS = {
    "quote": quote_agent,
    "config": config_agent,
    "rule": rule_agent,
}
AGENT_LABELS = {"quote": "报价助手", "config": "配置助手", "rule": "规则助手"}

_AGENT_RE = re.compile(r"^/agents/(quote|config|rule)(/.*)?$")

# ---------------------------------------------------------------- 静态文件
HOME_PAGE = "首页.html"  # 登录/入口页；点「Agent 智能体」进入 报价首页(1).html（三智能体整合页）

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
    "cpq_history", "xbom_history", "rule_history",
    "database", "open-claude", "cpq_data", "__pycache__", ".git",
}
_BLOCKED_EXTS = {".py", ".pyc", ".sqlite", ".db"}
_BLOCKED_FILES = {"cpq_settings.json", "xbom_settings.json", "rule_settings.json"}


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

    def do_OPTIONS(self):
        if self._dispatch_agent("do_OPTIONS"):
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._dispatch_agent("do_GET"):
            self._serve_static()

    def do_HEAD(self):
        if _AGENT_RE.match(self.path):
            self.send_error(405)
        else:
            self._serve_static(head=True)

    def do_POST(self):
        if not self._dispatch_agent("do_POST"):
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
        # 允许被 EIMOS(不同端口)以 iframe 内嵌
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)


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

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[cpq-suite] 首页  : http://{args.host}:{args.port}/   ->  {HOME_PAGE}")
    print(f"[cpq-suite] Agent : http://{args.host}:{args.port}/agents/{{quote|config|rule}}/api/send")
    print("[cpq-suite] EIMOS iframe 仍指向上面的首页地址即可。Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[cpq-suite] 已停止")
    finally:
        for mod in AGENTS.values():
            try:
                mod.bridge.conv.mcp.shutdown()
            except Exception:
                pass
        server.server_close()


if __name__ == "__main__":
    main()
