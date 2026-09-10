"""Agent 本地网关必须绕过 HTTP(S) 代理 —— 修复前 Red。

已实测复现（本机 open-claude/.venv，Python 3.10）：

    env -u NO_PROXY -u no_proxy HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \\
      open-claude/.venv/bin/python -m unittest \\
      tests.test_tech_agent_loopback_gateway_proxy_bypass_red

结果：指向 127.0.0.1 的本地假网关没有收到任何请求，`openai_compat.stream()` 只返回
一条 `error` 事件。

根因（已用反汇编排除“客户端/endpoint 被缓存”的假设，并用对照实验确认）：
  · `open_claude.openai_compat._client(provider)` 每次调用都新建
    `OpenAI(api_key=..., base_url=get_provider_base_url(provider))`；
  · `get_provider_base_url()` 每次调用都重新读取 `<PROVIDER>_BASE_URL` 环境变量。
    两者都没有缓存，所以 `sync_route_environment()` 之后的新地址本来就是生效的。
  · 真正的问题是 OpenAI SDK 底层的 httpx 默认 `trust_env=True`，会读取进程里的
    `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。部署环境若设置了企业代理而未排除
    本机，指向 127.0.0.1 的 CPQ 本地网关请求会被送进代理，本地网关收不到请求，
    表现为 SDK 抛 `Error code: 502`。

对照实验（同一个假网关，只切 `NO_PROXY`）：
  · 敌意代理 + `NO_PROXY` 未排除 127.0.0.1 → 网关收不到请求（红）；
  · 敌意代理 + `NO_PROXY` 含 127.0.0.1   → 网关收到 `/v1/chat/completions`（绿）。

影响：受管服务器上只要存在未排除本机的代理环境变量，`cpq_local` 本地网关路由就会
不可用；这也是 `tests.test_tech_agent_provider_readiness_dynamic` 在部分机器上
7 项中 1 项失败的原因。

本文件是修复前基线，三组契约：
  1. 静态：`oc_agent` 的受控集成层必须把 loopback 网关排除在代理之外；
  2. 动态（不联网）：`sync_route_environment()` 必须把 loopback 主机并入
     `NO_PROXY` / `no_proxy`，覆盖 127.0.0.1 / localhost / ::1，且不覆盖已有条目、
     不误伤公网域名；
  3. 动态（本地假网关）：在“敌意代理环境”下，发往本地网关的真实请求仍必须命中
     该网关。

允许的修复范围：`tech_app/backend/services/oc_agent.py`（受控集成层）。不要修改
`open-claude` 包内文件（编译产物，禁止反编译或覆盖），也不要关闭进程级代理。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
OPEN_CLAUDE_DIR = ROOT / "open-claude"
OC_AGENT_SOURCE = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"

# 敌意代理环境：代理端口无监听，且不提供 NO_PROXY 例外。
# 修复后 loopback 网关必须绕过它；修复前请求会被送到这里并失败。
HOSTILE_PROXY = {
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "ALL_PROXY": "http://127.0.0.1:9",
    "http_proxy": "http://127.0.0.1:9",
    "https_proxy": "http://127.0.0.1:9",
    "all_proxy": "http://127.0.0.1:9",
}
ENV_KEYS = (
    tuple(HOSTILE_PROXY)
    + ("NO_PROXY", "no_proxy", "QWEN_BASE_URL", "QWEN_API_KEY", "DASHSCOPE_API_KEY",
       "CLAUDE_MODEL", "ANTHROPIC_API_KEY", "OPEN_CLAUDE_DIR")
)


def _load_services():
    """导入后端服务；缺少可选依赖时用最薄的桩补齐，保证测试本身可运行。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "dotenv" not in sys.modules:
        try:
            import dotenv  # noqa: F401
        except ModuleNotFoundError:  # pragma: no cover - 本地无依赖时的兜底
            stub = types.ModuleType("dotenv")
            stub.load_dotenv = lambda *args, **kwargs: None
            sys.modules["dotenv"] = stub
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cpq-agent-proxy-test-"))
    os.environ.setdefault("OPEN_CLAUDE_DIR", str(OPEN_CLAUDE_DIR))
    from tech_app.backend.services import llm_settings, oc_agent
    return llm_settings, oc_agent


llm_settings, oc_agent = _load_services()


def _route(provider, label, model, base_url, native, key):
    return {
        "model": model,
        "provider": provider,
        "provider_label": label,
        "base_url": base_url,
        "native": native,
        "api_key": key,
    }


def _loopback_route(port):
    return _route(
        "qwen", "阿里云百炼", "qwen3.5-plus",
        f"http://127.0.0.1:{port}/v1", False, "test-qwen-key",
    )


class _EnvGuard(unittest.TestCase):
    """保存/恢复代理与路由相关环境变量，避免测试污染运行环境。"""

    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _hostile_proxy(self):
        """模拟“有企业代理、但没有排除本机”的部署环境。"""
        for key in HOSTILE_PROXY:
            os.environ[key] = HOSTILE_PROXY[key]
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)

    @staticmethod
    def _no_proxy_text():
        return ",".join(filter(None, (os.environ.get("NO_PROXY", ""),
                                      os.environ.get("no_proxy", ""))))


class AgentLocalGatewayProxyBypassContractTest(_EnvGuard):
    """静态契约 + 不联网的动态契约：集成层必须把 loopback 网关排除在代理之外。"""

    def test_oc_agent_excludes_loopback_hosts_from_proxy_env(self):
        self.assertTrue(OC_AGENT_SOURCE.is_file(), "缺少 tech_app/backend/services/oc_agent.py")
        source = OC_AGENT_SOURCE.read_text(encoding="utf-8")
        self.assertRegex(source, r"NO_PROXY",
                         "oc_agent 必须把 loopback 网关写入 NO_PROXY")
        self.assertRegex(source, r"no_proxy",
                         "NO_PROXY 与 no_proxy 两种写法都要覆盖，避免小写环境漏判")
        self.assertRegex(source, r"urlsplit|urlparse|hostname",
                         "必须从 base_url 解析主机名，而不是硬编码字符串比较")
        self.assertRegex(source, r"is_loopback|127\.0\.0\.1|localhost|::1",
                         "必须识别 127.0.0.1 / localhost / ::1 这类本机地址")

    def test_loopback_route_env_sync_adds_no_proxy_entry(self):
        """动态（不联网）：sync_route_environment 后 NO_PROXY 必须含 loopback 主机。"""
        self._hostile_proxy()
        oc_agent.sync_route_environment(_loopback_route(8012))
        self.assertIn("127.0.0.1", self._no_proxy_text(),
                      "sync_route_environment 没有把 loopback 网关排除在代理之外")

    def test_all_loopback_host_forms_are_excluded(self):
        """127.0.0.1 / localhost / ::1 三种写法都要被识别，不能只认其中一种。"""
        for host, base_url, expected in (
            ("localhost", "http://localhost:8013/v1", "localhost"),
            ("::1", "http://[::1]:8014/v1", "::1"),
            ("127.0.0.1", "http://127.0.0.1:8015/v1", "127.0.0.1"),
        ):
            with self.subTest(host=host):
                self._hostile_proxy()
                oc_agent.sync_route_environment(_route(
                    "qwen", "阿里云百炼", "qwen3.5-plus", base_url, False, "test-qwen-key",
                ))
                self.assertIn(expected, self._no_proxy_text(),
                              f"{host} 形式的 loopback 网关没有绕过代理")

    def test_available_excludes_proxy_for_loopback_gateway(self):
        """readiness 路径（/agent/meta 走 available）也必须先排除代理再探测。"""
        self._hostile_proxy()
        os.environ["ANTHROPIC_API_KEY"] = ""
        route = _loopback_route(8016)
        with mock.patch.object(llm_settings, "resolve", return_value=route), \
                mock.patch.object(oc_agent, "_import_open_claude", return_value={}):
            ok, message = oc_agent.available()
        self.assertTrue(ok, message)
        self.assertIn("127.0.0.1", self._no_proxy_text(),
                      "available() 返回可用，却没有把 loopback 网关排除在代理之外")

    def test_existing_no_proxy_entries_are_preserved(self):
        """既有 NO_PROXY 条目不能被覆盖（运维可能已排除内网域名）。"""
        self._hostile_proxy()
        os.environ["NO_PROXY"] = "internal.example.com,10.0.0.0/8"
        oc_agent.sync_route_environment(_loopback_route(8017))
        text = self._no_proxy_text()
        self.assertIn("internal.example.com", text)
        self.assertIn("10.0.0.0/8", text)
        self.assertIn("127.0.0.1", text)

    def test_public_gateway_is_not_forced_through_loopback_bypass(self):
        """公网网关（如百炼域名）不能被误判为 loopback：NO_PROXY 不得写入它的主机。"""
        self._hostile_proxy()
        oc_agent.sync_route_environment(_route(
            "qwen", "阿里云百炼", "qwen3.5-plus",
            "https://dashscope.aliyuncs.com/compatible-mode/v1", False, "test-qwen-key",
        ))
        text = self._no_proxy_text()
        self.assertNotIn("dashscope.aliyuncs.com", text)
        self.assertNotIn("*", text)

    def test_sync_route_environment_survives_unparsable_base_url(self):
        """异常 base_url 不能让同步函数抛错（会话创建路径必须在任何输入下安全）。"""
        self._hostile_proxy()
        broken = _route("qwen", "阿里云百炼", "qwen3.5-plus", "not a url", False, "k")
        try:
            oc_agent.sync_route_environment(broken)
        except Exception as exc:  # pragma: no cover - 修复引入解析逻辑后可能抛错
            self.fail(f"sync_route_environment 不能抛异常：{exc!r}")


def _open_claude_e2e_available() -> bool:
    if not (OPEN_CLAUDE_DIR / "open_claude" / "config.pyc").exists():
        return False
    if str(OPEN_CLAUDE_DIR) not in sys.path:
        sys.path.insert(0, str(OPEN_CLAUDE_DIR))
    try:
        import openai  # noqa: F401
        from open_claude import config as oc_config
        from open_claude import openai_compat  # noqa: F401
    except Exception:
        return False
    return "qwen" in getattr(oc_config, "PROVIDERS", {})


OPEN_CLAUDE_E2E = _open_claude_e2e_available()


@unittest.skipUnless(
    OPEN_CLAUDE_E2E,
    "需要匹配 open-claude 字节码的解释器（open-claude/.venv）和 openai SDK",
)
class AgentLoopbackGatewayProxyBypassE2ETest(_EnvGuard):
    """敌意代理环境下，发往本地网关的真实请求必须命中本地网关。"""

    def setUp(self):
        super().setUp()
        os.environ["OPEN_CLAUDE_DIR"] = str(OPEN_CLAUDE_DIR)
        self.seen = {}
        self.server = None
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def _start_fake_gateway(self) -> int:
        seen = self.seen

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                seen["path"] = self.path
                seen["model"] = body.get("model")
                seen["auth"] = self.headers.get("Authorization", "")
                chunk = {
                    "id": "c1", "object": "chat.completion.chunk", "created": 0,
                    "model": body.get("model"),
                    "choices": [{"index": 0, "delta": {"content": "pong"},
                                 "finish_reason": None}],
                }
                payload = (
                    f"data: {json.dumps(chunk)}\n\n"
                    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                    "data: [DONE]\n\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.server = server
        return server.server_address[1]

    def test_local_gateway_receives_request_despite_hostile_proxy_env(self):
        from open_claude import openai_compat
        port = self._start_fake_gateway()
        self._hostile_proxy()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        route = _loopback_route(port)
        # 显式写入网关地址：部署环境可能已用 QWEN_BASE_URL 指了业务空间域名，
        # sync_route_environment 用 setdefault 不会覆盖它，这里必须由测试自己锚定。
        os.environ["QWEN_BASE_URL"] = route["base_url"]
        oc_agent.sync_route_environment(route)
        error = None
        events = []
        try:
            events = list(openai_compat.stream(
                "qwen", "qwen3.5-plus", [{"role": "user", "content": "ping"}],
                "sys", None, 64, 0.0,
            ))
        except Exception as exc:  # 修复前：请求被送到代理，SDK 抛 502 / 连接错误
            error = exc
        self.assertEqual(
            self.seen.get("path"), "/v1/chat/completions",
            "本地网关没有收到请求：loopback 网关被 HTTP(S) 代理拦截。"
            f"NO_PROXY={self._no_proxy_text()!r}，SDK 错误：{error!r}",
        )
        self.assertEqual(self.seen.get("model"), "qwen3.5-plus")
        self.assertIn("text_delta", [event.get("type") for event in events])


if __name__ == "__main__":
    unittest.main()
