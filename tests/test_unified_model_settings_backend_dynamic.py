"""统一模型配置（报价为唯一事实源）的动态验证。

静态契约见 tests/test_unified_model_settings_and_api_keys_red.py；这里在真实后端
模块上验证行为：

  · 技术工艺读的是报价的 cpq_settings.json —— 报价侧写进去，技术工艺下一次调用
    就能解析到，不需要重启、也不需要技术工艺自己再保存一次；
  · 技术工艺保存时写的是同一份文件，不会再落一份 tech_data/llm_settings.json ；
  · 报价侧在另一个进程里改了模型 / 网关 / Key，技术工艺下一次对话会重建 client，
    继续沿用旧 client 的路径必须消失；
  · 任何 GET 快照都只回打码提示，不含明文 Key。

全部使用临时配置文件与假 Key：不联网、不读真实密钥、不碰真实运行数据。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_services():
    """导入共享配置与技术工艺适配层。"""
    if "dotenv" not in sys.modules:
        try:
            import dotenv  # noqa: F401
        except ModuleNotFoundError:  # pragma: no cover - 本地无依赖时的兜底
            stub = types.ModuleType("dotenv")
            stub.load_dotenv = lambda *args, **kwargs: None
            sys.modules["dotenv"] = stub
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cpq-settings-test-"))
    import cpq_shared_settings
    from tech_app.backend.services import llm_settings
    return cpq_shared_settings, llm_settings


cpq_shared_settings, llm_settings = _load_services()


class UnifiedModelSettingsBackendDynamicTest(unittest.TestCase):
    _ENV_KEYS = (
        "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY", "CLAUDE_MODEL", "CPQ_AUTH_BASE_URL",
    )

    def setUp(self):
        self._saved_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        self._tmp = tempfile.TemporaryDirectory(prefix="cpq-settings-")
        settings_path = Path(self._tmp.name) / "cpq_settings.json"
        patcher = mock.patch.object(
            cpq_shared_settings, "SETTINGS_PATH", str(settings_path))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings_path = settings_path

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def _write_quote_settings(self, data):
        """模拟报价侧（另一个进程）保存设置。"""
        self.settings_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_tech_resolves_the_quote_model_without_its_own_save(self):
        self._write_quote_settings({
            "model": "qwen3.5-plus",
            "temperature": 0.2,
            "api_keys": {"qwen": "test-qwen-key"},
        })
        route = llm_settings.resolve(vision=False)
        self.assertEqual(route["model"], "qwen3.5-plus")
        self.assertEqual(route["provider"], "qwen")
        self.assertEqual(route["api_key"], "test-qwen-key")
        self.assertEqual(llm_settings.inference_params()["temperature"], 0.2)
        # 报价改成 Anthropic 后立刻跟随，不需要技术工艺再保存一次。
        self._write_quote_settings({
            "model": "claude-opus-5",
            "api_keys": {"anthropic": "test-anthropic-key"},
        })
        self.assertEqual(llm_settings.resolve(vision=False)["provider"], "anthropic")

    def test_tech_update_writes_the_single_quote_config_file(self):
        self._write_quote_settings({"model": "qwen3.5-plus",
                                    "api_keys": {"qwen": "test-qwen-key"}})
        llm_settings.update({"model": "gpt-5.6-sol"}, can_edit=True)
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["model"], "gpt-5.6-sol")
        # 写的是同一份文件，且不覆盖别的 provider 已有 Key。
        self.assertEqual(saved["api_keys"]["qwen"], "test-qwen-key")
        self.assertFalse((ROOT / "tech_app" / "tech_data" / "llm_settings.json").exists())

    def test_snapshot_never_returns_a_plaintext_key(self):
        self._write_quote_settings({
            "model": "qwen3.5-plus", "api_keys": {"qwen": "test-qwen-key"},
        })
        snap = llm_settings.snapshot(can_edit=True, can_edit_secrets=True)
        self.assertNotIn("test-qwen-key", json.dumps(snap, ensure_ascii=False))
        qwen = [item for item in snap["keys"] if item["provider"] == "qwen"][0]
        self.assertTrue(qwen["configured"])
        self.assertTrue(qwen["hint"])

    def test_live_sessions_rebuild_only_when_the_route_changes(self):
        self._write_quote_settings({
            "model": "qwen3.5-plus", "api_keys": {"qwen": "test-qwen-key"},
        })
        calls = []

        def fake_apply(params, *, rebuild_client=False):
            calls.append(rebuild_client)

        with mock.patch("tech_app.backend.services.oc_agent.apply_settings",
                        side_effect=fake_apply):
            llm_settings.sync_live_agents()
            llm_settings.sync_live_agents()
            # 报价侧换了模型与 Key：下一次同步必须重建 client。
            self._write_quote_settings({
                "model": "deepseek-v4-pro",
                "api_keys": {"deepseek": "test-deepseek-key"},
            })
            llm_settings.sync_live_agents()
        self.assertEqual(calls, [True, False, True])


if __name__ == "__main__":
    unittest.main()
