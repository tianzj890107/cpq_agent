"""红测：任务文件在卡片内预览 + 修掉「请先在配置报价 CPQ 中登录」。

用户口径：

> 现在在任务文件的卡片里点击一个文件就会跳转到一个网页，应该是直接在这个卡片里就可以预览这个文件，
> 而且关键是这个网页还 {"detail":"请先在配置报价 CPQ 中登录"}

现状（实测，非推断）：

· 卡片/工作区视图：`app.js` 的 `renderBoardFiles()`（`:2801`）里 `link.href = file.url;
  link.target = "_blank";`（`:2833-2840`）—— 裸链接、强行新开标签页；
· 2.1 悬浮小窗：`agent-chat.js` 的 `fileRow()`（`:1293-1305`）同款 `link.href = file.url;
  link.target = "_blank"`；
· 清单里的 url 全是同源 `/api/...` 相对路径（`main.py:1109-1170`），受 app 级鉴权保护：
  `_cpq_sso_guard()`（`main.py:372-393`）取票顺序 `Authorization: Bearer` → `?token=`（`:350-358`），
  都没有就 `raise HTTPException(401, "请先在配置报价 CPQ 中登录")`（`:387`）——
  顶层导航既不带请求头也没有 token，所以必然 401，浏览器把 JSON 错误体当网页显示；
· 现成能力：`app.js:21-30` 的 `window.fetch` 包装会给同源 `/api/` 自动加 `Authorization`。

Spec：docs/specs/tech-file-preview-in-card-and-auth.md
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
INDEX = FRONTEND / "index.html"
WORKBENCH = FRONTEND / "tech-workbench.html"
APP_JS = FRONTEND / "app.js"
CHAT_JS = FRONTEND / "agent-chat.js"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
SPEC = ROOT / "docs" / "specs" / "tech-file-preview-in-card-and-auth.md"

# 改前实测的缓存号：本批动了 app.js / agent-chat.js，这三处必须换号。
OLD_VERSIONS = {
    "index.html|app.js": "20260917-partchrome1",
    "index.html|agent-chat.js": "20260917-modelrow1",
    "tech-workbench.html|agent-chat.js": "20260917-modelrow1",
}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def js_function(text: str, name: str) -> str:
    """按大括号配对取出 `function name(...) {...}` 的完整块（含函数名）。"""
    marker = "function %s(" % name
    start = text.find(marker)
    if start < 0:
        return ""
    brace = text.find("{", start)
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i)
            i = len(text) if j < 0 else j + 2
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return ""


def asset_version(html: str, asset: str) -> str:
    match = re.search(re.escape(asset) + r"\?v=([^\"'&]+)", html)
    return match.group(1) if match else ""


def child_python() -> str:
    candidates = [str(ROOT / "open-claude" / ".venv" / "bin" / "python"),
                  sys.executable, shutil.which("python3"), shutil.which("python")]
    probe = ("import sys; sys.path.insert(0, %r); import tech_app.backend.main"
             % str(ROOT))
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        done = subprocess.run([candidate, "-c", probe], cwd=str(ROOT),
                              capture_output=True, text=True)
        if done.returncode == 0:
            return candidate
    return sys.executable


def run_child(script: str, *args: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="cpq-file-preview-") as tmp:
        path = pathlib.Path(tmp) / "probe.py"
        path.write_text(script, encoding="utf-8")
        done = subprocess.run([child_python(), str(path), *args],
                              capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            raise AssertionError("子进程探针失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (done.returncode, done.stdout[-2500:], done.stderr[-2500:]))
        return json.loads(done.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# 子进程：真起技术工艺 App，验「文件 url 是同源 /api」与「无票必 401」
# --------------------------------------------------------------------------- #
CHILD = r'''
import asyncio
import json
import os
import sys
import types

data_dir, root = sys.argv[1], sys.argv[2]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

import cpq_shared_settings

cpq_shared_settings.load = lambda legacy=(): {
    "model": "qwen3.5-plus", "temperature": 0.2, "max_tokens": 4096, "thinking": False,
    "api_keys": {"qwen": "GLOBAL-QWEN-KEY"}}

from starlette.testclient import TestClient
from fastapi import HTTPException

import tech_app.backend.main as main
from tech_app.backend.storage import store

PID = store.create_project(source_filename="file-preview.png", source_bytes=b"png",
                           note="file preview probe", owner="tester")
client = TestClient(main.app, raise_server_exceptions=False)
manifest = client.get("/api/projects/%s/files" % PID).json()


class _FakeURL:
    def __init__(self, path):
        self.path = path


class _FakeHeaders:
    def __init__(self, data):
        self._data = {str(k).lower(): v for k, v in (data or {}).items()}

    def get(self, key, default=""):
        return self._data.get(str(key).lower(), default)


class _FakeQuery:
    def __init__(self, data):
        self._data = dict(data or {})

    def get(self, key, default=""):
        return self._data.get(key, default)


class _FakeRequest:
    def __init__(self, path, headers=None, query=None):
        self.url = _FakeURL(path)
        self.headers = _FakeHeaders(headers)
        self.query_params = _FakeQuery(query)
        self.state = types.SimpleNamespace()


async def probe():
    main.cpq_sso.resolve = lambda token: None
    try:
        await main._cpq_sso_guard(_FakeRequest("/api/projects/%s/source" % PID))
        no_ticket = "NO-ERROR"
    except HTTPException as exc:
        no_ticket = "%s|%s" % (exc.status_code, exc.detail)
    main.cpq_sso.resolve = lambda token: {"username": "probe", "role": "engineer",
                                         "display_name": "探针", "is_system": False}
    request = _FakeRequest("/api/projects/%s/source" % PID,
                           headers={"authorization": "Bearer probe-ticket"})
    try:
        await main._cpq_sso_guard(request)
        passed = str(getattr(request.state, "user", {}).get("username", ""))
    except Exception as exc:                                  # noqa: BLE001 - 探针要看到原因
        passed = "ERR:%s" % exc
    by_query = main._sso_token(_FakeRequest("/api/projects/%s/source" % PID,
                                            query={"token": "q-ticket"}))
    by_header = main._sso_token(_FakeRequest("/api/projects/%s/source" % PID,
                                             headers={"authorization": "Bearer h-ticket"}))
    return no_ticket, passed, by_query, by_header


no_ticket, passed, by_query, by_header = asyncio.run(probe())
urls = [f.get("url") for g in (manifest.get("groups") or []) for f in (g.get("files") or [])]
print(json.dumps({"status": client.get("/api/health").status_code,
                  "total": manifest.get("total"),
                  "urls": urls,
                  "no_ticket": no_ticket,
                  "passed": passed,
                  "by_query": by_query,
                  "by_header": by_header}, ensure_ascii=False))
'''


class AuthRootCauseGuardTest(unittest.TestCase):
    """根因护栏：文件 url 就是同源 /api，且无票必然 401 —— 不许靠放宽鉴权来「修好」。"""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="cpq-file-preview-work-")
        try:
            cls.out = run_child(CHILD, str(cls.work), str(ROOT))
        finally:
            shutil.rmtree(cls.work, ignore_errors=True)

    def test_manifest_is_served(self):
        self.assertEqual(200, self.out["status"])
        self.assertGreaterEqual(self.out["total"], 1, "探针项目应当至少有需求原图：%s" % self.out)

    def test_file_urls_are_same_origin_api_paths(self):
        self.assertTrue(self.out["urls"], "清单里一个文件都没有：%s" % self.out)
        for url in self.out["urls"]:
            self.assertTrue(str(url).startswith("/api/projects/"),
                            "文件 url 不是同源 /api 路径，前端带不了票：%s" % url)
            self.assertNotIn("http", str(url), "文件 url 不应该是绝对地址：%s" % url)

    def test_no_ticket_means_401_with_that_message(self):
        self.assertEqual("401|请先在配置报价 CPQ 中登录", self.out["no_ticket"],
                         "无票时的行为变了 —— 这正是用户看到那句 JSON 的来源")

    def test_ticket_lets_the_request_through(self):
        self.assertEqual("probe", self.out["passed"],
                         "带票的请求应当放行并挂上 request.state.user")
        self.assertEqual("q-ticket", self.out["by_query"], "?token= 透传约定被改掉了")
        self.assertEqual("h-ticket", self.out["by_header"], "Authorization 取票被改掉了")


# --------------------------------------------------------------------------- #
# 契约钉死
# --------------------------------------------------------------------------- #
class SpecPinnedTest(unittest.TestCase):
    def test_spec_pins_every_contract(self):
        text = read(SPEC)
        for anchor in ("CadFilePreview", "createObjectURL", "revokeObjectURL",
                       "TEXT_PREVIEW_LIMIT", "登录状态已失效", "该类型暂不支持预览",
                       "不在卡片内预览", "_blank", "Authorization"):
            self.assertIn(anchor, text, "Spec 缺少契约锚点：%s" % anchor)


# --------------------------------------------------------------------------- #
# 契约 A：卡片内预览
# --------------------------------------------------------------------------- #
class PreviewEntryRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = read(APP_JS)
        cls.rows = js_function(cls.app, "renderBoardFiles")

    def test_file_rows_are_no_longer_bare_links(self):
        self.assertTrue(self.rows, "找不到 renderBoardFiles()")
        self.assertFalse("_blank" in self.rows, "文件名还在新开标签页跳转")
        self.assertFalse("href = file.url" in self.app, "裸链接必须全部改掉")
        self.assertFalse("window.open(file.url" in self.app, "不许再用新窗口打开文件")

    def test_rows_call_the_shared_preview(self):
        self.assertIn("CadFilePreview", self.rows,
                      "点击文件应当交给统一预览入口，而不是自己跳转")

    def test_preview_api_is_exposed(self):
        found = re.search(r"window\.CadFilePreview\s*=\s*\{[^}]*\bopen\b[^}]*\bclose\b", self.app, re.S)
        self.assertIsNotNone(found, "缺少 window.CadFilePreview = { open, close }")

    def test_preview_uses_authenticated_fetch_and_releases_urls(self):
        for token in ("createObjectURL", "revokeObjectURL", "TEXT_PREVIEW_LIMIT"):
            self.assertTrue(token in self.app, "预览缺少能力：%s" % token)
        self.assertRegex(self.app, r"""createElement\((["'])iframe\1\)""",
                         "预览缺少 pdf 的 iframe 分支")

    def test_preview_branches_cover_every_kind(self):
        for token in ("该类型暂不支持预览", "不在卡片内预览", "登录状态已失效"):
            self.assertTrue(token in self.app, "预览缺少分支文案：%s" % token)

    def test_preview_does_not_put_the_token_in_the_url(self):
        rows = self.rows
        self.assertNotIn("mediaUrl(", rows,
                         "预览不该再把 token 拼进 URL（走 Authorization 请求头）")


# --------------------------------------------------------------------------- #
# 契约 B/C：两处入口共用一份预览
# --------------------------------------------------------------------------- #
class DockEntryRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = read(CHAT_JS)
        cls.row = js_function(cls.chat, "fileRow")

    def test_dock_rows_are_no_longer_bare_links(self):
        self.assertTrue(self.row, "找不到 fileRow()")
        self.assertFalse("_blank" in self.row, "2.1 悬浮小窗的文件还在新开标签页")
        self.assertFalse("href = file.url" in self.chat, "裸链接必须全部改掉")

    def test_dock_rows_call_the_shared_preview(self):
        self.assertIn("CadFilePreview", self.row,
                      "2.1 悬浮小窗也要用同一份预览，不复制第二套")

    def test_preview_logic_is_not_duplicated(self):
        for token in ("createObjectURL", "TEXT_PREVIEW_LIMIT", "该类型暂不支持预览"):
            self.assertFalse(token in self.chat,
                             "预览实现只能在 app.js 里有一份：agent-chat.js 不该出现 %s" % token)


class CacheBustRedTest(unittest.TestCase):
    def test_assets_are_cachebusted(self):
        pages = {"index.html": read(INDEX), "tech-workbench.html": read(WORKBENCH)}
        for key, old in OLD_VERSIONS.items():
            page, asset = key.split("|")
            self.assertNotEqual(old, asset_version(pages[page], asset),
                                "%s 里 %s 的 ?v= 没有提升，用户仍会命中旧缓存" % (page, asset))


if __name__ == "__main__":
    unittest.main(verbosity=2)
