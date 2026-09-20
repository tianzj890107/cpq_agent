"""长结构化输出截断恢复的红测基线（实现前应失败）。

背景：1.1「创建需求」等长 JSON 任务在输出达到 provider 上限（文本默认 12000）时被截断，
现有代码只做"整份重新生成"，且修复分支的预算提升对文本路径是 no-op，因此必然再次截断；
OpenAI 路径则直接放弃。本文件锁定"可续写 + 可提升预算 + 失败可诊断"的统一契约。

静态契约用例任何解释器都能跑；行为用例需要 pydantic/httpx/openai，条件不满足时跳过
（本仓库用 open-claude/.venv/bin/python 运行全部用例）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "tech_app" / "backend" / "services"
QWEN_PATH = SERVICES / "qwen_client.py"
OPENAI_PATH = SERVICES / "openai_client.py"
REQUIREMENT_PATH = SERVICES / "requirement_extract.py"
LLM_OUTPUT_PATH = SERVICES / "llm_output.py"
TECH_AGENT_PATH = SERVICES / "oc_agent.py"
AGENT_SERVER_PATHS = [
    ROOT / "cpq_agent_server.py",
    ROOT / "xbom_agent_server.py",
    ROOT / "rule_agent_server.py",
]


def _load_backend():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "dotenv" not in sys.modules:
        try:
            import dotenv  # noqa: F401
        except ModuleNotFoundError:  # pragma: no cover - 本地无依赖时的兜底
            stub = types.ModuleType("dotenv")
            stub.load_dotenv = lambda *args, **kwargs: None
            sys.modules["dotenv"] = stub
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cpq-truncation-red-"))
    from tech_app.backend.services import qwen_client, requirement_extract
    return qwen_client, requirement_extract


try:
    qwen_client, requirement_extract = _load_backend()
    _DEPS_OK = True
except Exception:                                            # pragma: no cover - 依赖缺失
    qwen_client = requirement_extract = None
    _DEPS_OK = False


def _load_llm_output():
    """按文件路径加载共享模块，避免依赖服务包的 import 链。"""
    if not LLM_OUTPUT_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("cpq_llm_output_probe", LLM_OUTPUT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedTruncationContract(unittest.TestCase):
    """R1 / R6：截断语义与诊断必须收敛到唯一共享模块。"""

    def test_shared_truncation_module_exists_with_required_api(self):
        module = _load_llm_output()
        self.assertIsNotNone(
            module,
            f"缺少共享截断模块 {LLM_OUTPUT_PATH.relative_to(ROOT)}；"
            "截断/续写语义不得在多个 provider 里各写一套。",
        )
        for name in ("OutputTruncated", "TRUNCATION_REASONS", "is_truncated",
                     "raised_budget", "truncation_note"):
            self.assertTrue(hasattr(module, name), f"llm_output 缺少 {name}")

        self.assertTrue(issubclass(module.OutputTruncated, RuntimeError))
        for reason in ("length", "max_tokens", "max_output_tokens"):
            self.assertIn(reason, module.TRUNCATION_REASONS)
            self.assertTrue(module.is_truncated(reason))
        for reason in (None, "stop", "end_turn", "tool_use"):
            self.assertFalse(module.is_truncated(reason))

        error = module.OutputTruncated("截断", finish_reason="length", limit=32768, partial='{"a": 1')
        self.assertEqual(error.finish_reason, "length")
        self.assertEqual(error.limit, 32768)
        self.assertEqual(error.partial, '{"a": 1')

    def test_raised_budget_is_strictly_larger_and_capped(self):
        module = _load_llm_output()
        self.assertIsNotNone(module, "缺少共享截断模块 llm_output.py")
        raised = module.raised_budget(12000, 32768)
        self.assertIsNotNone(raised, "12000 < 32768 时必须能提升预算")
        self.assertGreater(raised, 12000)
        self.assertLessEqual(raised, 32768)
        self.assertIsNone(module.raised_budget(32768, 32768),
                          "已到 provider 上限时不得假装还能提升（只能续写或报错）")

    def test_truncation_note_carries_budget_and_reason(self):
        module = _load_llm_output()
        self.assertIsNotNone(module, "缺少共享截断模块 llm_output.py")
        note = module.truncation_note(
            finish_reason="length", initial_budget=12000, final_budget=24000,
            attempts=2, limit=32768,
        )
        for token in ("length", "12000", "24000", "32768"):
            self.assertIn(token, note, f"截断诊断记录缺少 {token}")


class ProviderSourceContract(unittest.TestCase):
    """R2 / R3 / R4 / R5：各调用路径必须接上统一策略。"""

    @classmethod
    def setUpClass(cls):
        cls.qwen = QWEN_PATH.read_text(encoding="utf-8")
        cls.openai = OPENAI_PATH.read_text(encoding="utf-8")
        cls.requirement = REQUIREMENT_PATH.read_text(encoding="utf-8")

    def test_qwen_text_retry_raises_budget_via_shared_helper(self):
        self.assertNotIn(
            "24000 if vision else 12000", self.qwen,
            "文本路径的修复预算仍是 no-op（12000 -> max(12000, 12000)），"
            "必须改用 llm_output.raised_budget",
        )
        self.assertRegex(self.qwen, r"llm_output|raised_budget")

    def test_qwen_truncation_retry_uses_continuation(self):
        self.assertRegex(
            self.qwen,
            r"partial|已输出|已生成|继续输出|继续补全|续写|剩余(部分|内容)",
            "截断后的重试必须是续写：把已产出的片段交回模型，而不是整份重新生成",
        )

    def test_openai_truncation_is_retried_not_abandoned(self):
        self.assertNotIn("系统未自动重试，避免再次发送图纸产生额外费用", self.openai)
        self.assertRegex(self.openai, r"llm_output|OutputTruncated|raised_budget|is_truncated")

    def test_requirement_extract_uses_shared_output_budget(self):
        self.assertNotRegex(
            self.requirement, r"max_tokens\s*=\s*12000",
            "1.1 需求解析不得再写死 12000，否则长文档必然在末尾被截断",
        )
        self.assertRegex(
            self.requirement, r"llm_output|output_budget|raised_budget",
            "1.1 的输出预算必须来自共享策略",
        )

    def test_tech_agent_surfaces_max_tokens_stop_reason(self):
        source = TECH_AGENT_PATH.read_text(encoding="utf-8")
        self.assertRegex(
            source, r"""stop_reason\s*==\s*["']max_tokens["']""",
            "技术工艺 Agent 必须区分 stop_reason==max_tokens，不得静默当 end_turn",
        )

    def test_three_agent_servers_surface_max_tokens_stop_reason(self):
        for path in AGENT_SERVER_PATHS:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertRegex(
                    source, r"""stop_reason\s*==\s*["']max_tokens["']""",
                    f"{path.name} 未处理 stop_reason==max_tokens",
                )


class _FakeChoice:
    def __init__(self, content, finish_reason):
        self.message = types.SimpleNamespace(content=content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content, finish_reason):
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)


class _FakeCompletions:
    def __init__(self, script, calls):
        self._script = script
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        index = min(len(self._calls) - 1, len(self._script) - 1)
        content, finish_reason = self._script[index]
        return _FakeResponse(content, finish_reason)


class _FakeClient:
    def __init__(self, script, calls):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(script, calls))


@unittest.skipUnless(_DEPS_OK, "需要 pydantic/httpx/openai（用 open-claude/.venv/bin/python 运行）")
class QwenTruncationBehavior(unittest.TestCase):
    """R2：行为层验证"提升预算 + 续写 + 拼接校验"。"""

    def _run(self, script, *, max_tokens=12000):
        from pydantic import BaseModel

        class Doc(BaseModel):
            title: str

        calls: list[dict] = []
        fake = _FakeClient(script, calls)
        with mock.patch.object(qwen_client, "get_client", lambda vision=False: fake), \
                mock.patch.object(qwen_client, "_tuning", lambda: {}):
            try:
                result = qwen_client.run(
                    "系统提示", [qwen_client.text_block("请输出")], Doc, max_tokens=max_tokens,
                )
                return result, calls, None
            except Exception as exc:                          # noqa: BLE001 - 断言里区分类型
                return None, calls, exc

    def test_truncation_retry_sends_continuation_with_larger_budget(self):
        result, calls, error = self._run([
            ('{"title": "half', "length"),
            (' one"}', "stop"),
        ])
        self.assertIsNone(error, f"截断后应能续写成功，实际抛错：{error}")
        self.assertGreaterEqual(len(calls), 2, "截断后必须发起续写请求")
        self.assertGreater(
            calls[1]["max_tokens"], calls[0]["max_tokens"],
            "续写请求的预算必须严格大于首轮，否则等于重复同一次截断",
        )
        second_payload = json.dumps(calls[1]["messages"], ensure_ascii=False)
        self.assertIn("half", second_payload, "续写请求必须携带上一轮已产出的片段")

    def test_continuation_fragments_are_stitched_and_validated(self):
        result, _calls, error = self._run([
            ('{"title": "half', "length"),
            (' one"}', "stop"),
        ])
        self.assertIsNone(error, f"续写片段必须本地拼接后通过校验，实际抛错：{error}")
        self.assertEqual(result.title, "half one")

    def test_hard_limit_raises_output_truncated_with_diagnostics(self):
        _result, calls, error = self._run([('{"title": "never closes', "length")])
        self.assertIsNotNone(error, "一直截断时必须失败，不能返回半截 JSON")
        module = _load_llm_output()
        self.assertIsNotNone(module, "缺少共享截断模块 llm_output.py")
        self.assertIsInstance(
            error, module.OutputTruncated,
            f"一直截断应抛 OutputTruncated（调用方才能识别并提示重试），实际是 {type(error).__name__}: {error}",
        )
        self.assertEqual(error.finish_reason, "length")
        message = str(error)
        self.assertIn("length", message)
        self.assertTrue(any(str(value) in message for value in (error.limit, 12000, 32768)),
                        f"错误信息必须带上限值，实际：{message}")
        self.assertLessEqual(len(calls), 6, "续写次数必须有上限，不能无限重试")


if __name__ == "__main__":
    unittest.main()
