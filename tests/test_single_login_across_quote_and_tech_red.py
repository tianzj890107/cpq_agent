"""报价与技术工艺合并为一套登录与鉴权：Spec / Red。

契约见 docs/specs/single-login-across-quote-and-tech.md。今天的事实是反的：

  · /agents/* 完全不验票。cpq_suite_server.py:137 的 _dispatch_agent() 命中前缀后
    直接把请求交给 Agent 模块，而它在 do_GET(:507)/do_POST(:539) 里是第一个分支。
    实测：不带 Authorization、甚至伪造令牌，/agents/quote/api/settings 与
    /agents/quote/api/send 都回 200 —— 未登录就能改全局模型/API Key、驱动 Agent。
  · 四张报价页面里指向 Agent 基址的 46 处调用全是裸 fetch，不带票；
    报价首页另有 6 处同源技术工艺 /api/* 调用同样是裸 fetch（SSO 下必然 401）。
  · CPQ 角色只有 销售/工艺/财务 三个，没有"工艺技术总监"；技术工艺 3.2 审核要
    REVIEW_ROLES、3.3 发布要 DIRECTOR_ROLES，都指向 process_director，于是只能靠
    auth.enable_cpq_single_manager() 把工艺经理升成全权，职责分离不成立。
  · /api/me 的 sso 块只有 can_write / can_cost，没有 can_review / can_publish；
    cpq-sso.js 的写拦截只看 can_write —— 工艺技术总监点「审核」「发布」会被前端伪 403
    挡住（和当初财务经理点「测算」是同一个坑）。
  · 技术工艺把设置广播给报价侧（llm_settings._post_quote_settings）不带任何凭据，
    一旦 /agents/* 开始验票，这条路会静默失效（现在 except 里就是 pass）。

验证方式：
  A. 子进程真起一体化服务（端口 0，假 Agent 模块），用真 HTTP 打 /agents/*，
     并记录"Agent 是否真的被调用"（验票必须在调用之前）。
  B. 子进程真起技术工艺 App（TestClient + 假 cpq_sso.resolve），读 /api/me 的能力位，
     并按 CPQ_MANAGER_FULL_TECH 真跑一次启动期角色授予。
  C. 源码契约：前端带票、能力位放行、内部令牌、私有化独立模式保留。
"""
from __future__ import annotations

import functools
import importlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

SUITE_PY = ROOT / "cpq_suite_server.py"
CPQ_AUTH_PY = ROOT / "cpq_auth.py"
CPQ_AUTH_JS = ROOT / "cpq_auth.js"
CPQ_SSO_JS = ROOT / "tech_app" / "frontend" / "cpq-sso.js"
CPQ_SSO_PY = ROOT / "tech_app" / "backend" / "services" / "cpq_sso.py"
TECH_AUTH_PY = ROOT / "tech_app" / "backend" / "services" / "auth.py"
TECH_MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
LLM_SETTINGS_PY = ROOT / "tech_app" / "backend" / "services" / "llm_settings.py"

PAGES = (
    "报价首页.html",
    "确认需求解析结果.html",
    "XBOM智能体-配置BOM生成.html",
    "规则助手-规则配置.html",
)

# 前端指向 Agent 基址的写法（4 个页面合计 46 处，全都必须带票）
AGENT_BASE_MARKERS = ("AGENT_URL", "src.base", "modeBase(", "peerBase(")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def fetch_calls(src: str) -> list:
    """取出源码里每一处 fetch(...) 的完整调用表达式（括号配平）。"""
    found = []
    for match in re.finditer(r"\bfetch\s*\(", src):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(src)):
            char = src[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    found.append(src[match.start():index + 1])
                    break
    return found


def balanced_block(src: str, start: int) -> str:
    """从 start 处的 { 起，返回括号配平的整块。"""
    depth = 0
    for index in range(start, len(src)):
        char = src[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[start:index + 1]
    return ""


def js_body(src: str, needle: str, min_len: int = 40) -> str:
    """取 JS 里某个函数/箭头函数的函数体。

    默认参数 `opts = {}` 也会是 `{`，所以太短的块直接跳过，取第一段真正的函数体。
    """
    at = src.find(needle)
    if at < 0:
        return ""
    pos = at
    while True:
        brace = src.find("{", pos)
        if brace < 0:
            return ""
        block = balanced_block(src, brace)
        if len(block) >= min_len:
            return block
        pos = brace + 1


def py_body(src: str, name: str) -> str:
    """取某个顶层函数/方法的函数体（到下一个顶层 def/class 为止）。"""
    idx = src.find("def %s(" % name)
    if idx < 0:
        return ""
    rest = src[idx:]
    end = len(rest)
    for marker in ("\ndef ", "\nasync def ", "\n@app.", "\nclass "):
        at = rest.find(marker, 1)
        if at != -1:
            end = min(end, at)
    return rest[:end]


def child_python() -> str:
    """能同时 import fastapi/httpx 与 cpq_suite_server 的解释器（本机为 open-claude/.venv）。"""
    candidates = [str(ROOT / "open-claude" / ".venv" / "bin" / "python"),
                  sys.executable, shutil.which("python3"), shutil.which("python")]
    probe_code = ("import sys; sys.path.insert(0, %r); "
                  "import fastapi, httpx, cpq_suite_server" % str(ROOT))
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", probe_code], cwd=str(ROOT),
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


# --------------------------------------------------------------------------- #
# A. 一体化服务：/agents/* 的验票（真 HTTP，端口 0，假 Agent 模块）
# --------------------------------------------------------------------------- #
SUITE_CHILD = r'''
import json
import os
import sys
import tempfile
import threading
import types
import urllib.error
import urllib.request
import http.server

root = sys.argv[1]
sys.path.insert(0, root)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cpq-single-login-"))

import cpq_suite_server as suite

CALLS = []          # Agent 处理函数真正被调用的记录（验票必须发生在它之前）

mod = types.ModuleType("fake_quote_agent")


class FakeHandler(suite.Handler):
    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def do_PUT(self):
        self._respond()

    def do_DELETE(self):
        self._respond()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _respond(self):
        CALLS.append("%s %s" % (self.command, self.path))
        body = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


mod.Handler = FakeHandler
suite.AGENTS["quote"] = mod

USERS = {"good-token": {"username": "mgr", "role_code": "process_mgr",
                        "role_name": "工艺经理", "status": "active"}}
suite.cpq_auth.whoami = lambda token: USERS.get(token or "")
suite.cpq_auth._backend = "pg"

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), suite.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

out = {}


def call(method, path, token=None, query=False, raw_token=False):
    url = "http://127.0.0.1:%d%s" % (port, path)
    if query and token:
        url += ("&" if "?" in path else "?") + "token=" + token
    request = urllib.request.Request(url, method=method)
    if token and not query and not raw_token:
        request.add_header("Authorization", "Bearer " + token)
    if raw_token:
        request.add_header("Authorization", token)
    if method == "POST":
        request.data = b"{}"
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return {"status": response.status,
                    "text": response.read().decode("utf-8", "replace")[:600]}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "text": exc.read().decode("utf-8", "replace")[:600]}
    except Exception as exc:      # noqa: BLE001
        return {"status": -1, "text": "%s: %s" % (type(exc).__name__, exc)}


out["no_token_get"] = call("GET", "/agents/quote/api/settings")
out["no_token_post"] = call("POST", "/agents/quote/api/send")
out["bad_token"] = call("GET", "/agents/quote/api/sessions", token="nope-not-a-token")
out["non_bearer_scheme"] = call("GET", "/agents/quote/api/sessions",
                                token="good-token", raw_token=True)
out["calls_after_denied"] = list(CALLS)

out["valid_token"] = call("GET", "/agents/quote/api/sessions", token="good-token")
out["query_token"] = call("GET", "/agents/quote/api/sessions", token="good-token", query=True)
out["calls_after_valid"] = list(CALLS)

out["static_asset"] = call("GET", "/cpq_auth.js")
out["options_preflight"] = call("OPTIONS", "/agents/quote/api/settings")

# 登录后端不可用：不能把在线的人静默踢出去
suite.cpq_auth._backend = None
out["backend_down"] = call("GET", "/agents/quote/api/sessions", token="good-token")
suite.cpq_auth._backend = "pg"

server.shutdown()
print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=None)
def suite_probe() -> dict:
    python = child_python()
    if not python:
        raise unittest.SkipTest("没有能 import fastapi/httpx/cpq_suite_server 的解释器")
    script_dir = tempfile.mkdtemp(prefix="cpq-single-login-script-")
    try:
        script = pathlib.Path(script_dir) / "suite_child.py"
        script.write_text(SUITE_CHILD, encoding="utf-8")
        completed = subprocess.run([python, str(script), str(ROOT)], cwd=str(ROOT),
                                   capture_output=True, text=True, timeout=300)
        if completed.returncode != 0:
            raise AssertionError("一体化服务走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2000:],
                                    completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(script_dir, ignore_errors=True)


class SuiteAgentGateRedTest(unittest.TestCase):
    """C2 / C3 / C4：/agents/* 必须先验票再进 Agent。"""

    @classmethod
    def setUpClass(cls):
        cls.data = suite_probe()

    def case(self, name) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, "%s 缺失：%s" % (name, self.data))
        return value

    def test_get_without_token_is_rejected(self):
        got = self.case("no_token_get")
        self.assertEqual(got.get("status"), 401,
                         "无票的 GET /agents/quote/api/settings 必须 401，实际 %s %s"
                         % (got.get("status"), got.get("text")))

    def test_post_without_token_is_rejected(self):
        got = self.case("no_token_post")
        self.assertEqual(got.get("status"), 401,
                         "无票的 POST /agents/quote/api/send 必须 401，实际 %s %s"
                         % (got.get("status"), got.get("text")))

    def test_rejected_body_asks_to_login(self):
        text = self.case("no_token_get").get("text") or ""
        self.assertIn("登录", text, "401 文案要能看懂（请先登录），实际：%s" % text)

    def test_bogus_token_is_rejected(self):
        got = self.case("bad_token")
        self.assertEqual(got.get("status"), 401,
                         "伪造令牌不得放行，实际 %s %s" % (got.get("status"), got.get("text")))

    def test_non_bearer_scheme_is_rejected(self):
        got = self.case("non_bearer_scheme")
        self.assertEqual(got.get("status"), 401,
                         "Authorization 不带 Bearer 前缀不得当成有效票，实际 %s" % (got.get("status"),))

    def test_denied_requests_never_reach_the_agent(self):
        calls = self.data.get("calls_after_denied") or []
        self.assertEqual(calls, [],
                         "被拒绝的请求不得调用 Agent（会写历史/落盘/调模型），实际调用：%s" % calls)

    def test_valid_token_reaches_the_agent(self):
        got = self.case("valid_token")
        self.assertEqual(got.get("status"), 200,
                         "有效票必须正常进入 Agent，实际 %s %s" % (got.get("status"), got.get("text")))
        self.assertEqual(self.data.get("calls_after_valid"),
                         ["GET /api/sessions", "GET /api/sessions?token=good-token"],
                         "有效票的两次请求都应到达 Agent")

    def test_query_token_is_accepted(self):
        got = self.case("query_token")
        self.assertEqual(got.get("status"), 200,
                         "?token= 透传（发不出请求头的场景）必须同样可过，实际 %s %s"
                         % (got.get("status"), got.get("text")))

    def test_static_asset_not_gated(self):
        got = self.case("static_asset")
        self.assertEqual(got.get("status"), 200,
                         "前端资源不得被本批挡住，实际 %s" % (got.get("status"),))

    def test_options_preflight_not_gated(self):
        got = self.case("options_preflight")
        self.assertIn(got.get("status"), (200, 204),
                      "OPTIONS 预检不带票也必须能过，实际 %s" % (got.get("status"),))

    def test_auth_backend_down_is_503_not_401(self):
        got = self.case("backend_down")
        self.assertEqual(got.get("status"), 503,
                         "验不了票（登录库不可用）必须回 503，回 401 等于把在线的人静默踢出去；"
                         "实际 %s %s" % (got.get("status"), got.get("text")))


# --------------------------------------------------------------------------- #
# B. 技术工艺：能力位 + 启动期角色授予
# --------------------------------------------------------------------------- #
TECH_CHILD = r'''
import json
import os
import sys
import tempfile

root, flag = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="cpq-single-login-tech-")
os.environ["CPQ_SSO"] = "true"
os.environ["CPQ_MANAGER_FULL_TECH"] = flag
os.environ["AUTH_ENABLED"] = "false"

out = {"flag": flag}
try:
    from fastapi.testclient import TestClient
    from tech_app.backend import main as tech_main
    from tech_app.backend.services import auth as tech_auth
    from tech_app.backend.services import cpq_sso

    tech_main._startup_housekeeping()
    out["roles_after_startup"] = {
        "write": sorted(tech_auth.WRITE_ROLES),
        "review": sorted(tech_auth.REVIEW_ROLES),
        "director": sorted(tech_auth.DIRECTOR_ROLES),
    }
    client = TestClient(tech_main.app)

    def me(role):
        cpq_sso.resolve = lambda token, _role=role: {
            "username": "u-" + _role, "role": _role, "display_name": _role, "source": "cpq"}
        response = client.get("/api/me", headers={"Authorization": "Bearer x"})
        return (response.json() or {}).get("sso") or {}

    out["me_director"] = me("process_director")
    out["me_manager"] = me("process_manager")
    out["me_viewer"] = me("viewer")
    # 本地登录在 SSO 模式下必须仍然明确拒绝（保留的两套模式互斥）
    out["local_login"] = client.post("/api/login",
                                     json={"username": "a", "password": "b"}).status_code
except Exception as exc:      # noqa: BLE001
    out["error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out, ensure_ascii=False, default=str))
'''


@functools.lru_cache(maxsize=None)
def tech_probe(flag: str) -> dict:
    python = child_python()
    if not python:
        raise unittest.SkipTest("没有能 import fastapi/httpx 的解释器")
    script_dir = tempfile.mkdtemp(prefix="cpq-single-login-tech-script-")
    try:
        script = pathlib.Path(script_dir) / "tech_child.py"
        script.write_text(TECH_CHILD, encoding="utf-8")
        completed = subprocess.run([python, str(script), str(ROOT), flag], cwd=str(ROOT),
                                   capture_output=True, text=True, timeout=300)
        if completed.returncode != 0:
            raise AssertionError("技术工艺走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2000:],
                                    completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(script_dir, ignore_errors=True)


class CapabilityBitsRedTest(unittest.TestCase):
    """C8 / C9：工艺技术总监要有审核/发布能力位，且开关关掉后工艺经理不再全权。"""

    @classmethod
    def setUpClass(cls):
        cls.off = tech_probe("false")
        cls.on = tech_probe("true")
        for data in (cls.off, cls.on):
            if data.get("error"):
                raise AssertionError("技术工艺走查失败：%s" % data["error"])

    def test_director_has_review_capability(self):
        sso = self.off.get("me_director") or {}
        self.assertTrue(sso.get("can_review"),
                        "工艺技术总监必须有 can_review（3.2 审核），实际 %s" % sso)

    def test_director_has_publish_capability(self):
        sso = self.off.get("me_director") or {}
        self.assertTrue(sso.get("can_publish"),
                        "工艺技术总监必须有 can_publish（3.3 发布），实际 %s" % sso)

    def test_director_is_not_a_tech_writer(self):
        sso = self.off.get("me_director") or {}
        self.assertFalse(sso.get("can_write"), "总监不该拿到 2.1/2.2 的写权限：%s" % sso)

    def test_manager_loses_review_and_publish_when_full_tech_off(self):
        sso = self.off.get("me_manager") or {}
        self.assertFalse(sso.get("can_review"), "关掉全权后工艺经理不得再审核：%s" % sso)
        self.assertFalse(sso.get("can_publish"), "关掉全权后工艺经理不得再发布：%s" % sso)

    def test_manager_keeps_write_when_full_tech_off(self):
        sso = self.off.get("me_manager") or {}
        self.assertTrue(sso.get("can_write"), "工艺经理仍负责 2.1/2.2 的写：%s" % sso)

    def test_full_tech_on_keeps_the_manager_promoted(self):
        roles = self.on.get("roles_after_startup") or {}
        self.assertIn("process_manager", roles.get("review") or [],
                      "CPQ_MANAGER_FULL_TECH=true（默认）必须保持今天的行为")
        self.assertIn("process_manager", roles.get("director") or [])

    def test_full_tech_on_manager_can_review_and_publish(self):
        sso = self.on.get("me_manager") or {}
        self.assertTrue(sso.get("can_review"), "全权模式下工艺经理仍可审核：%s" % sso)
        self.assertTrue(sso.get("can_publish"), "全权模式下工艺经理仍可发布：%s" % sso)

    def test_required_role_name_is_truthful_when_full_tech_off(self):
        self.assertEqual((self.on.get("me_director") or {}).get("required_role_name"),
                         "工艺经理", "全权模式下提示仍应是工艺经理")
        name = (self.off.get("me_director") or {}).get("required_role_name")
        self.assertTrue(name, "登录提示不能是空字符串")
        self.assertNotEqual(name, "工艺经理",
                            "关掉全权后还提示“只能工艺经理使用”，总监会以为自己进不来")

    def test_local_login_still_rejected_in_sso_mode(self):
        code = self.off.get("local_login")
        self.assertIn(code, (409, 401, 403),
                      "SSO 模式下本地登录必须明确拒绝，不能“看着能用”；实际 %s" % code)


# --------------------------------------------------------------------------- #
# C. 前端与源码契约
# --------------------------------------------------------------------------- #
class AgentFetchTokenRedTest(unittest.TestCase):
    """C5：四个页面的 Agent 调用必须统一带票。"""

    @classmethod
    def setUpClass(cls):
        cls.pages = {name: read(ROOT / name) for name in PAGES}
        cls.auth_js = read(CPQ_AUTH_JS)

    def test_cpq_auth_js_exposes_auth_fetch_helper(self):
        self.assertIn("cpqAuthFetch", self.auth_js,
                      "cpq_auth.js 必须提供统一的带票请求助手")
        body = js_body(self.auth_js, "cpqAuthFetch")
        self.assertTrue(body, "找不到 cpqAuthFetch 的实现体")
        self.assertIn("Authorization", body, "助手必须自己附加 Authorization 头")
        self.assertIn("Bearer", body)
        self.assertIn("cpq_auth_token", self.auth_js, "票的来源仍是 localStorage.cpq_auth_token")
        self.assertIn("401", body, "401 时必须走未登录路径，不能把错误体当业务数据渲染")
        self.assertIn("open", body, "401 时必须拉起 CPQ 登录框")

    def test_no_bare_agent_fetch_left(self):
        for name, src in self.pages.items():
            with self.subTest(page=name):
                offenders = [call for call in fetch_calls(src)
                             if any(marker in call for marker in AGENT_BASE_MARKERS)]
                self.assertEqual(offenders, [],
                                 "%s 还有 %d 处不带票的 Agent 调用，例如：%s"
                                 % (name, len(offenders), offenders[:1]))

    def test_quote_home_tech_api_calls_carry_token(self):
        src = self.pages["报价首页.html"]
        offenders = [call for call in fetch_calls(src)
                     if call.startswith("fetch('/api/") or call.startswith('fetch("/api/')]
        self.assertEqual(offenders, [],
                         "报价首页调技术工艺同源 /api/* 也必须带票（SSO 下今天必然 401），"
                         "仍有 %d 处：%s" % (len(offenders), offenders[:2]))

    def test_every_page_refreshes_after_login(self):
        for name, src in self.pages.items():
            with self.subTest(page=name):
                self.assertIn("cpq-auth-change", src,
                              "%s 登录成功后必须重新拉取受保护数据，不能停在空列表" % name)


class FrontendCapabilityGateRedTest(unittest.TestCase):
    """C9：cpq-sso.js 的写拦截要按能力放行审核/发布路径。"""

    @classmethod
    def setUpClass(cls):
        cls.src = read(CPQ_SSO_JS)

    def test_reads_review_and_publish_capabilities(self):
        self.assertIn("can_review", self.src, "写拦截必须认识 can_review")
        self.assertIn("can_publish", self.src, "写拦截必须认识 can_publish")
        self.assertIn("sso.can_review", self.src, "能力位来自 /api/me 的 sso 块")
        self.assertIn("sso.can_publish", self.src)

    def test_allows_review_action_paths(self):
        self.assertIn("/approve", self.src.replace("\\/", "/"),
                      "3.2 的审签通过/驳回必须对 can_review 放行")
        self.assertIn("reject", self.src)

    def test_allows_report_and_requirement_review_paths(self):
        flattened = self.src.replace("\\/", "/")
        for needle in ("process-report/review", "process-report/publish",
                       "requirement/review", "process-report/distribution"):
            self.assertIn(needle, flattened,
                          "can_publish 必须放行 %s，否则总监点下去只有前端伪 403" % needle)


class InternalNotifyTokenRedTest(unittest.TestCase):
    """C4：服务间同步设置不能裸奔，也不能静默失败。"""

    @classmethod
    def setUpClass(cls):
        cls.suite = read(SUITE_PY)
        cls.llm = read(LLM_SETTINGS_PY)

    def test_suite_injects_internal_token_into_child(self):
        self.assertIn("CPQ_INTERNAL_TOKEN", self.suite,
                      "一体化服务必须生成/透出内部令牌给技术工艺子进程")

    def test_suite_accepts_internal_token_header(self):
        flattened = self.suite.replace("_", "-")
        self.assertIn("X-Internal-Token", flattened,
                      "验票处必须认识 X-Internal-Token（服务间调用）")

    def test_llm_settings_sends_internal_token(self):
        body = py_body(self.llm, "_post_quote_settings")
        self.assertTrue(body, "找不到 _post_quote_settings")
        self.assertIn("X-Internal-Token", body,
                      "技术工艺改设置后的广播必须带内部令牌，否则本批之后会静默失效")

    def test_notify_failure_is_not_silent(self):
        body = py_body(self.llm, "_post_quote_settings")
        tail = body[body.find("except"):] if "except" in body else ""
        self.assertTrue("print(" in tail or "warn" in tail.lower(),
                        "广播失败必须留下可诊断的告警，不能 except: pass 假装成功")


class StandaloneModePreservedRedTest(unittest.TestCase):
    """C6：私有化独立模式必须保留（本类全部是保护性约束，不许实现时删掉）。"""

    @classmethod
    def setUpClass(cls):
        cls.main = read(TECH_MAIN_PY)
        cls.sso_py = read(CPQ_SSO_PY)

    def test_local_login_endpoints_still_exist(self):
        self.assertIn('@app.post("/api/login")', self.main)
        self.assertIn('@app.post("/api/register")', self.main)
        self.assertIn('@app.get("/api/users")', self.main)

    def test_local_login_rejection_is_gated_by_sso_switch(self):
        body = py_body(self.main, "_reject_local_login")
        self.assertIn("CPQ_SSO_ENABLED", body,
                      "只有 SSO 模式才拒绝本地登录；独立模式必须照旧可用")

    def test_unknown_role_still_falls_back_to_viewer(self):
        self.assertIn('FALLBACK_ROLE = "viewer"', self.sso_py)

    def test_cpq_roles_are_defined_in_code_not_schema(self):
        # 新增角色只扩充字典取值，不引入数据库取值约束（不改 schema）
        auth_py = read(CPQ_AUTH_PY)
        self.assertIn("ROLES = {", auth_py)
        self.assertNotIn("CHECK (role_code", auth_py)


class RoleParityRedTest(unittest.TestCase):
    """C7：CPQ 侧补齐"工艺技术总监"，并映射到技术工艺的 process_director。"""

    @classmethod
    def setUpClass(cls):
        cls.cpq_auth = _import_cpq_auth()
        cls.cpq_sso = _import_cpq_sso()

    def test_cpq_roles_include_tech_director(self):
        roles = getattr(self.cpq_auth, "ROLES", {})
        self.assertIn("tech_director", roles,
                      "CPQ 侧必须有工艺技术总监角色，否则 3.2/3.3 只能由工艺经理兼任")
        self.assertEqual(roles.get("tech_director"), "工艺技术总监")

    def test_role_map_maps_director_to_process_director(self):
        mapping = getattr(self.cpq_sso, "ROLE_MAP", {})
        self.assertEqual(mapping.get("tech_director"), "process_director",
                         "CPQ 的工艺技术总监必须映射到技术工艺的 process_director")

    def test_role_label_covers_director(self):
        labels = getattr(self.cpq_sso, "TECH_ROLE_LABEL", {})
        self.assertEqual(labels.get("process_director"), "工艺技术总监",
                         "403 文案要说人话，不能把 process_director 直接甩给用户")

    def test_existing_mappings_unchanged(self):
        mapping = getattr(self.cpq_sso, "ROLE_MAP", {})
        self.assertEqual(mapping.get("process_mgr"), "process_manager")
        self.assertEqual(mapping.get("sales_mgr"), "viewer")
        self.assertEqual(mapping.get("finance_mgr"), "finance_manager")
        self.assertEqual(getattr(self.cpq_sso, "FALLBACK_ROLE", ""), "viewer")


def _stub_dotenv() -> None:
    if "dotenv" in sys.modules:
        return
    try:
        importlib.import_module("dotenv")
    except ModuleNotFoundError:
        stub = types.ModuleType("dotenv")
        stub.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = stub


def _import_cpq_auth():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("cpq_auth")


def _import_cpq_sso():
    _stub_dotenv()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cpq-single-login-sso-"))
    return importlib.import_module("tech_app.backend.services.cpq_sso")


if __name__ == "__main__":
    unittest.main()
