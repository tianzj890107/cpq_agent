"""红测：视觉能力闸门与模型选择必须跟「这一次实际会用的模型」走。

用户反馈：在「我的模型与密钥」里改了自己的模型，图纸解析仍然报
「当前生效模型 deepseek-v4-flash（来源：平台默认）不支持图像解析…」—— 看起来像自己的
设置没生效，实际是这一关压根没看账号级设置。

根因（已实测，非推断）：账号级覆盖只进了 `resolve()` 一条路。

  · 账号感知：llm_settings.py:315 resolve(vision=…) → _model_and_source(账号)；
    claude_client.py:167、llm_client.py:103 都用它；
  · 只读平台默认：llm_settings.py:286 selected_model() → current_model_id()（全局），
    被 qwen_client.py:291 的 _model_candidates() 与 llm_settings.py:293 的
    ensure_vision_capable() 当成"实际会用的模型"。

于是同一次调用里，闸门判的是平台默认、真正发出去的是账号模型；报错文案永远写
「来源：平台默认」。同源影响：model_lookup.py:55（选路）、ai_governance.py:51（留痕）。

Spec：docs/specs/effective-model-for-vision-and-task-process-detail.md（契约 A1–A6）
"""
from __future__ import annotations

import functools
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICES = ROOT / "tech_app" / "backend" / "services"
LLM_SETTINGS_PY = SERVICES / "llm_settings.py"
QWEN_PY = SERVICES / "qwen_client.py"
MODEL_LOOKUP_PY = SERVICES / "model_lookup.py"
GOVERNANCE_PY = SERVICES / "ai_governance.py"
SPEC = ROOT / "docs" / "specs" / "effective-model-for-vision-and-task-process-detail.md"

ACCOUNT_MODEL = "qwen3.5-plus"
ACCOUNT_TEXT_ONLY_MODEL = "deepseek-v4-flash"
PLATFORM_DEFAULT = "deepseek-v4-flash"
PLATFORM_VISION_DEFAULT = "qwen3.5-plus"
USER = "wugefei"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def py_body(src: str, name: str) -> str:
    """取某个顶层函数的函数体（到下一个顶层 def/class/装饰器为止）。"""
    idx = src.find("def %s(" % name)
    if idx < 0:
        return ""
    rest = src[idx:]
    end = len(rest)
    for marker in ("\ndef ", "\nasync def ", "\n@app.", "\nclass ", "\n# ---"):
        at = rest.find(marker, 1)
        if at != -1:
            end = min(end, at)
    return rest[:end]


def backend_python() -> str:
    candidates = [sys.executable, str(ROOT / "open-claude/.venv/bin/python"),
                  shutil.which("python3"), shutil.which("python")]
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", "import fastapi, pydantic"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


CHILD = r'''
import json
import os
import pathlib
import sys
import types

data_dir, root = sys.argv[1], sys.argv[2]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv

import cpq_shared_settings

GLOBAL = {"model": "deepseek-v4-flash", "temperature": 0.2, "max_tokens": 4096,
          "thinking": False,
          "api_keys": {"qwen": "GLOBAL-QWEN-KEY", "deepseek": "GLOBAL-DS-KEY"}}
cpq_shared_settings.load = lambda legacy=(): dict(GLOBAL)
cpq_shared_settings.save = lambda data: None

ACCOUNT = {"wugefei": {"model": "qwen3.5-plus", "api_keys": {"qwen": "MY-QWEN-KEY"}}}

client = types.ModuleType("tech_app.backend.services.cpq_auth_client")


class CpqAuthUnavailable(RuntimeError):
    pass


client.CpqAuthUnavailable = CpqAuthUnavailable


def _entry(name):
    return ACCOUNT.get(str(name)) or {}


client.user_overrides = lambda name: {
    "model": str(_entry(name).get("model") or ""),
    "api_keys": dict(_entry(name).get("api_keys") or {})}
client.summary = lambda name: {
    "model": str(_entry(name).get("model") or ""),
    "has_model": bool(_entry(name).get("model")), "keys": {}}
client.set_model = lambda *args, **kwargs: {}
client.set_key = lambda *args, **kwargs: {}
client.invalidate = lambda name="": None
client.set_request_token = lambda token="": None
client.update_profile = lambda *args, **kwargs: {}
client.change_password = lambda *args, **kwargs: {}
sys.modules["tech_app.backend.services.cpq_auth_client"] = client

from pydantic import BaseModel

from tech_app.backend.services import acting_user, llm_settings, llm_client
from tech_app.backend.services import qwen_client, model_lookup, ai_governance

out = {"data_dir": data_dir}


class ProbeOut(BaseModel):
    ok: bool = True


def call(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - 探针要把异常原样带回父进程
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def call_ok(fn):
    """只回 ok / error 摘要，避免把整份加载了模型的返回体带回去。"""
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": value}


# 真跑一次调用时抓「实际发出去的 model= 参数」。
CALLED = {}


class _Completions:
    def create(self, **kwargs):
        CALLED["model"] = kwargs.get("model")
        msg = types.SimpleNamespace(content='{"ok": true}')
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])


class _Chat:
    completions = _Completions()


class _FakeClient:
    chat = _Chat()


qwen_client.get_client = lambda vision=False: _FakeClient()

TEXT_BLOCKS = [{"type": "text", "text": "请解析这份材料"}]
IMAGE_BLOCKS = [{"type": "image", "filename": "source.png", "data": "aGVsbG8="},
                {"type": "text", "text": "解析这张图纸"}]

acting_user.set_acting_user("wugefei")

# ---- 场景一：账号选了支持图像的模型，平台默认不支持 ----
out["effective"] = call(lambda: llm_settings.effective())
out["resolve_vision_model"] = call(lambda: llm_settings.resolve(vision=True)["model"])
out["selected_model_vision"] = call(lambda: llm_settings.selected_model(vision=True))
out["selected_model_text"] = call(lambda: llm_settings.selected_model(vision=False))
out["candidates_vision"] = call(lambda: list(qwen_client._model_candidates(True)))
out["candidates_text"] = call(lambda: list(qwen_client._model_candidates(False)))
out["vision_call"] = call_ok(lambda: llm_client.run("系统提示", list(IMAGE_BLOCKS), ProbeOut).ok)
out["vision_call_model"] = CALLED.get("model")
CALLED.clear()
out["text_call"] = call_ok(lambda: llm_client.run("系统提示", list(TEXT_BLOCKS), ProbeOut).ok)
out["text_call_model"] = CALLED.get("model")
CALLED.clear()

# ---- 场景二：账号选了不支持图像的模型，平台默认支持 ----
ACCOUNT["wugefei"] = {"model": "deepseek-v4-flash", "api_keys": {"qwen": "MY-QWEN-KEY"}}
GLOBAL["model"] = "qwen3.5-plus"
out["text_only_account_effective"] = call(lambda: llm_settings.effective())
out["text_only_account_resolve"] = call(lambda: llm_settings.resolve(vision=True)["model"])
out["text_only_account_candidates"] = call(lambda: list(qwen_client._model_candidates(True)))
out["text_only_account_vision_call"] = call_ok(
    lambda: llm_client.run("系统提示", list(IMAGE_BLOCKS), ProbeOut).ok)
out["text_only_account_call_model"] = CALLED.get("model")
CALLED.clear()

# ---- 场景三：没有账号模型时，平台默认的口径一个字不变 ----
ACCOUNT["wugefei"] = {}
GLOBAL["model"] = "deepseek-v4-flash"
out["no_account_resolve"] = call(lambda: llm_settings.resolve(vision=True)["model"])
out["no_account_selected"] = call(lambda: llm_settings.selected_model(vision=True))
out["no_account_candidates"] = call(lambda: list(qwen_client._model_candidates(True)))

# ---- 场景四：型号核验的选路 / 留痕的模型名 ----
ACCOUNT["wugefei"] = {"model": "qwen3.5-plus", "api_keys": {"qwen": "MY-QWEN-KEY"}}
GLOBAL["model"] = "deepseek-v4-flash"
ROUTE = {"branch": ""}

qwen_client.complete_to_model_with_web_search = (
    lambda *args, **kwargs: (
        ROUTE.__setitem__("branch", "qwen-native"),
        (ProbeOut(), {"sources": [], "search_count": 0, "model": "qwen3.5-plus"}),
    )[1])

_original_run = llm_client.run


def _fake_hosted_run(*args, **kwargs):
    ROUTE["branch"] = "hosted-tool"
    return ProbeOut()


llm_client.run = _fake_hosted_run
out["websearch"] = call_ok(
    lambda: bool(model_lookup._lookup_with_search({"device_name": "探针"}, max_tokens=512)))
out["websearch_branch"] = ROUTE["branch"]
llm_client.run = _original_run

llm_client.last_used_model = lambda: None
out["audit_model"] = call(lambda: ai_governance.metadata("parse", {}).get("model"))

print(json.dumps(out, ensure_ascii=False))
'''


@functools.lru_cache(maxsize=None)
def probe() -> dict:
    python = backend_python()
    if not python:
        raise unittest.SkipTest("没有可 import fastapi/pydantic 的解释器，跳过生效模型走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-effective-model-")
    script_dir = tempfile.mkdtemp(prefix="cpq-effective-model-script-")
    try:
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run([python, str(script), data_dir, str(ROOT)],
                                   capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "生效模型走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout[-2000:], completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


class EffectiveModelBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = probe()

    def value(self, key: str):
        return self.out.get(key)

    def error_text(self, key: str) -> str:
        value = self.out.get(key)
        self.assertIsInstance(value, dict, "%s 没有回错误对象：%r" % (key, value))
        self.assertIn("error", value, "%s 必须明确失败，实际：%r" % (key, value))
        return str(value["error"])


class VisionGateFollowsEffectiveModelTest(EffectiveModelBase):
    def test_01_spec_pins_the_contract(self):
        spec = read(SPEC)
        self.assertTrue(spec, "缺少 docs/specs/effective-model-for-vision-and-task-process-detail.md")
        for token in ("selected_model", "resolve(", "_model_candidates", "ensure_vision_capable",
                      "detail", "oc-process-detail", "process_log"):
            self.assertIn(token, spec, "spec 未钉住 %s" % token)

    def test_02_effective_model_is_the_account_model(self):
        self.assertEqual(ACCOUNT_MODEL, self.value("effective")["model"],
                         "账号级覆盖本身没生效：%s" % self.value("effective"))
        self.assertEqual("account", self.value("effective")["source"])

    def test_03_resolve_vision_uses_the_account_model(self):
        self.assertEqual(ACCOUNT_MODEL, self.value("resolve_vision_model"),
                         "resolve(vision=True) 没走账号模型：%s" % self.value("resolve_vision_model"))

    def test_04_selected_model_matches_resolve_for_both_capabilities(self):
        self.assertEqual(ACCOUNT_MODEL, self.value("selected_model_vision"),
                         "selected_model(vision=True) 与 resolve() 不是同一个模型：%s"
                         % self.value("selected_model_vision"))
        self.assertEqual(ACCOUNT_MODEL, self.value("selected_model_text"),
                         "selected_model(vision=False) 没走账号模型：%s"
                         % self.value("selected_model_text"))

    def test_05_candidate_list_equals_the_effective_model(self):
        self.assertEqual([ACCOUNT_MODEL], self.value("candidates_vision"),
                         "候选模型仍取自平台默认：%s" % self.value("candidates_vision"))
        self.assertEqual([ACCOUNT_MODEL], self.value("candidates_text"),
                         "文本候选模型仍取自平台默认：%s" % self.value("candidates_text"))

    def test_06_actual_call_sends_the_effective_model(self):
        self.assertEqual({"ok": True}, self.value("vision_call"),
                         "带图调用没跑起来：%s" % self.value("vision_call"))
        self.assertEqual(ACCOUNT_MODEL, self.value("vision_call_model"),
                         "实际发出去的是平台默认：%s" % self.value("vision_call_model"))
        self.assertEqual({"ok": True}, self.value("text_call"),
                         "文本调用没跑起来：%s" % self.value("text_call"))
        self.assertEqual(ACCOUNT_MODEL, self.value("text_call_model"),
                         "文本调用实际发出去的是平台默认：%s" % self.value("text_call_model"))

    def test_07_gate_names_the_account_model_when_it_is_text_only(self):
        text = self.error_text("text_only_account_resolve")
        self.assertIn(ACCOUNT_TEXT_ONLY_MODEL, text,
                      "报错点名的不是账号模型：%s" % text)
        self.assertIn("账号", text,
                      "账号模型不支持图像时，来源必须写清「账号 … 的个人设置」：%s" % text)
        self.assertEqual(ACCOUNT_TEXT_ONLY_MODEL, self.value("text_only_account_effective")["model"])

    def test_08_candidate_list_rejects_the_text_only_account_model(self):
        text = self.error_text("text_only_account_candidates")
        self.assertIn(ACCOUNT_TEXT_ONLY_MODEL, text,
                      "候选模型清单没有按生效模型判定：%s" % text)
        self.assertNotEqual({"ok": True}, self.value("text_only_account_vision_call"),
                            "账号模型不支持图像时必须明确失败，不能拿平台默认偷偷跑：%s"
                            % self.value("text_only_account_vision_call"))

    def test_09_no_account_model_keeps_the_platform_default_wording(self):
        for key in ("no_account_resolve", "no_account_selected", "no_account_candidates"):
            text = self.error_text(key)
            self.assertIn(PLATFORM_DEFAULT, text, "%s 应点名平台默认：%s" % (key, text))
            self.assertIn("平台默认", text, "%s 的来源标签不再是「平台默认」：%s" % (key, text))

    def test_10_type_lookup_route_follows_the_effective_model(self):
        self.assertEqual({"ok": True}, self.value("websearch"),
                         "型号核验没跑起来：%s" % self.value("websearch"))
        self.assertEqual("qwen-native", self.value("websearch_branch"),
                         "型号核验的选路仍按平台默认（deepseek）判 provider：%s"
                         % self.value("websearch_branch"))

    def test_11_audit_records_the_effective_model(self):
        self.assertEqual(ACCOUNT_MODEL, self.value("audit_model"),
                         "留痕记的是平台默认，不是实际生效的模型：%s" % self.value("audit_model"))


class EffectiveModelSourceContractTest(unittest.TestCase):
    """源码契约：调用期不允许再拿「平台默认」当模型。"""

    @classmethod
    def setUpClass(cls):
        cls.settings = read(LLM_SETTINGS_PY)
        cls.qwen = read(QWEN_PY)
        cls.lookup = read(MODEL_LOOKUP_PY)
        cls.governance = read(GOVERNANCE_PY)

    def test_20_platform_default_is_only_read_inside_llm_settings(self):
        offenders = []
        for path in sorted(SERVICES.glob("*.py")):
            if path.name == "llm_settings.py":
                continue
            if "current_model_id(" in read(path):
                offenders.append(path.name)
        self.assertEqual([], offenders,
                         "调用期仍在直接读平台默认模型（绕开账号覆盖）：%s" % offenders)

    def test_21_selected_model_resolves_through_the_account(self):
        body = py_body(self.settings, "selected_model")
        self.assertTrue(body, "找不到 selected_model()")
        self.assertTrue(re.search(r"_model_and_source\(|resolve\(", body),
                        "selected_model() 仍直接取平台默认：%s" % body[:300])

    def test_22_model_lookup_does_not_pick_the_provider_from_the_raw_default(self):
        body = py_body(self.lookup, "_lookup_with_search")
        self.assertTrue(body, "找不到 _lookup_with_search()")
        self.assertNotIn("selected_model(", body,
                         "型号核验的选路仍在用平台默认模型判 provider：%s" % body[:300])

    def test_23_governance_fallback_is_account_aware(self):
        body = py_body(self.governance, "_model")
        self.assertTrue(body, "找不到 ai_governance._model()")
        self.assertNotIn("selected_model(", body,
                         "留痕兜底仍在用平台默认模型：%s" % body[:300])


if __name__ == "__main__":
    unittest.main(verbosity=2)
