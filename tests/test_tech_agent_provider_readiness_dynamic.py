"""技术工艺 Agent provider readiness 的动态验证。

静态契约见 test_tech_agent_provider_readiness_red.py；这里在真实模块上验证行为：

  · 默认 Qwen 路由只认 Qwen Key，不依赖 Anthropic Key；
  · 缺 Key 的错误指向当前 provider，且不泄露 Key 内容；
  · 切换 provider 会按新路由重建 client，不复用旧 client；
  · 端到端：选 Qwen / CPQ 本地网关时，请求真的发往配置的兼容端点。

测试全部使用 mock 路由与假 Key，不联网、不读真实密钥、不写真实数据目录。
端到端用例需要一个能加载 open-claude 字节码的解释器（open-claude/.venv）和
openai SDK，条件不满足时自动跳过。
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
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cpq-agent-test-"))
    # 与 tech_app_launch.py 一致：open-claude 在仓库根目录，不在 tech_app 下。
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


QWEN = _route(
    "qwen", "阿里云百炼", "qwen3.5-plus",
    "https://dashscope.aliyuncs.com/compatible-mode/v1", False, "test-qwen-key",
)
QWEN_NO_KEY = dict(QWEN, api_key="")
ANTHROPIC_NO_KEY = _route(
    "anthropic", "Anthropic", "claude-opus-5", "https://api.anthropic.com", True, "",
)


class AgentProviderReadinessDynamicTest(unittest.TestCase):
    _ENV_KEYS = (
        "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY", "CLAUDE_MODEL", "QWEN_BASE_URL", "ANTHROPIC_BASE_URL",
    )

    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in self._ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_qwen_route_with_key_is_available_without_anthropic_key(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with mock.patch.object(llm_settings, "resolve", return_value=QWEN), \
                mock.patch.object(oc_agent, "_import_open_claude", return_value={}):
            ok, message = oc_agent.available()
        self.assertTrue(ok, message)
        self.assertEqual(message, "")
        self.assertEqual(os.environ.get("CLAUDE_MODEL"), "qwen3.5-plus")
        self.assertEqual(os.environ.get("QWEN_BASE_URL"), QWEN["base_url"])
        self.assertEqual(os.environ.get("DASHSCOPE_API_KEY"), "test-qwen-key")

    def test_qwen_route_without_key_reports_qwen_not_anthropic(self):
        with mock.patch.object(llm_settings, "resolve", return_value=QWEN_NO_KEY):
            ok, message = oc_agent.available()
        self.assertFalse(ok)
        self.assertIn("阿里云百炼", message)
        self.assertNotIn("ANTHROPIC", message.upper())

    def test_anthropic_route_without_key_reports_anthropic(self):
        with mock.patch.object(llm_settings, "resolve", return_value=ANTHROPIC_NO_KEY):
            ok, message = oc_agent.available()
        self.assertFalse(ok)
        self.assertIn("Anthropic", message)
        self.assertNotIn("qwen", message.lower())

    def test_switch_provider_rebuilds_client_on_new_route(self):
        class FakeRepl:
            def __init__(self):
                self.calls = 0

            def create_client(self):
                self.calls += 1
                return f"client-{self.calls}"

        repl = FakeRepl()
        agent = types.SimpleNamespace(
            conv=types.SimpleNamespace(model="claude-old", client="client-0"),
            _oc={"repl": repl},
        )
        with mock.patch.object(llm_settings, "resolve", return_value=QWEN):
            oc_agent.ProjectAgent._rebuild_client(agent)
        self.assertEqual(agent.conv.client, "client-1")
        self.assertEqual(agent.conv.model, "qwen3.5-plus")
        with mock.patch.object(llm_settings, "resolve", return_value=ANTHROPIC_NO_KEY):
            oc_agent.ProjectAgent._rebuild_client(agent)
        self.assertEqual(agent.conv.client, "client-2")
        self.assertEqual(agent.conv.model, "claude-opus-5")

    def test_environ_base_url_override_is_not_clobbered(self):
        """运维用 QWEN_BASE_URL 指定的业务空间域名不能被默认网关覆盖。"""
        os.environ["QWEN_BASE_URL"] = "https://workspace.example.com/compatible-mode/v1"
        oc_agent.sync_route_environment(QWEN)
        self.assertEqual(
            os.environ["QWEN_BASE_URL"],
            "https://workspace.example.com/compatible-mode/v1",
        )


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
class AgentProviderRouteE2EDynamicTest(unittest.TestCase):
    """把请求真的发到本地假网关，确认选 Qwen / 本地网关时不会走 Anthropic。"""

    _ENV_KEYS = (
        "OPEN_CLAUDE_DIR", "CLAUDE_MODEL", "QWEN_BASE_URL", "QWEN_API_KEY",
        "DASHSCOPE_API_KEY", "TECH_LOCAL_API_KEY", "CPQ_LOCAL_BASE_URL",
    )

    def setUp(self):
        from open_claude import config as oc_config
        self.oc_config = oc_config
        os.environ["OPEN_CLAUDE_DIR"] = str(OPEN_CLAUDE_DIR)
        self._saved = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.seen = {}
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

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
                    "choices": [{"index": 0, "delta": {"content": "pong"}, "finish_reason": None}],
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

    def test_qwen_route_sends_request_to_configured_endpoint_with_qwen_key(self):
        from open_claude import openai_compat
        port = self._start_fake_gateway()
        os.environ["QWEN_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
        os.environ["QWEN_API_KEY"] = "test-qwen-key"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        route = dict(QWEN, base_url=f"http://127.0.0.1:{port}/v1")
        with mock.patch.object(llm_settings, "resolve", return_value=route):
            oc_agent.sync_route_environment(route)
        events = list(openai_compat.stream(
            "qwen", "qwen3.5-plus", [{"role": "user", "content": "ping"}],
            "sys", None, 64, 0.0,
        ))
        self.assertEqual(self.seen.get("path"), "/v1/chat/completions")
        self.assertEqual(self.seen.get("model"), "qwen3.5-plus")
        self.assertEqual(self.seen.get("auth"), "Bearer test-qwen-key")
        self.assertIn("text_delta", [event.get("type") for event in events])

    def test_cpq_local_gateway_is_registered_at_runtime(self):
        self.oc_config.PROVIDERS.setdefault("cpq_local", {
            "label": "本地网关（CPQ 共用）",
            "env": ["TECH_LOCAL_API_KEY"],
            "base_url": None,
        })
        # 真实 CPQ 部署里 llm_settings.PROVIDERS 已含 cpq_local（由 TECH_LOCAL_* 注入触发）。
        local_spec = {
            "label": "本地网关（CPQ 共用）",
            "base_url": "http://127.0.0.1:9999/v1",
            "env": ("TECH_LOCAL_API_KEY",),
            "native": False,
        }
        route = _route(
            "cpq_local", "本地网关（CPQ 共用）", "cpq-local-7b",
            "http://127.0.0.1:9999/v1", False, "local-key",
        )
        with mock.patch.dict(llm_settings.PROVIDERS, {"cpq_local": local_spec}):
            oc_agent.sync_route_environment(route)
            self.assertEqual(self.oc_config.get_model_provider("cpq-local-7b"), "cpq_local")
            self.assertEqual(
                self.oc_config.get_provider_base_url("cpq_local"), "http://127.0.0.1:9999/v1",
            )
            self.assertEqual(self.oc_config.get_api_key_for("cpq_local"), "local-key")


if __name__ == "__main__":
    unittest.main()
