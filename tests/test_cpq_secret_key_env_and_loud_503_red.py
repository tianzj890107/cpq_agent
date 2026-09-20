"""账号级密钥的加密密钥：配置文件要真被读到 + 缺密钥必须是可执行的 503：Spec / Red。

契约见 docs/specs/cpq-secret-key-env-and-loud-503.md。今天的事实是：

  · cpq_suite_server.py 从不调用 load_dotenv()（只有 tech_app/backend/config.py:9 调），
    所以 8010 只能靠手工 export —— 换台机器/换个人就复发；
  · 没配 CPQ_USER_SECRET_KEY 时 cpq_user_secrets._key() 抛 SecretKeyMissing
    （cpq_user_secrets.py:56-60），它不是 cpq_auth.AuthError，于是落进兜底分支，
    回 500「服务异常，请稍后重试」（cpq_suite_server.py:516-519）；
  · 技术工艺把它包成 CpqAuthUnavailable，界面上显示成「登录服务暂不可用」——
    看着像登录服务故障，实际是缺一个环境变量。

验证方式：
  · 子进程真 import cpq_suite_server（临时 DATA_DIR），验证 CPQ_ENV_FILE 指向的文件真被读进环境；
  · 子进程真起一体化服务的 Handler，用打桩的 cpq_auth 打真 HTTP 请求，断言
    PUT /auth/my/llm 与 PUT /auth/internal/user-llm 都回 503 + 可执行文案；
  · 静态契约：cpq_suite_server / tech_app main / DEPLOYMENT.md 的源码与文档断言。
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "specs" / "cpq-secret-key-env-and-loud-503.md"
SUITE_PY = ROOT / "cpq_suite_server.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
DEPLOYMENT_MD = ROOT / "DEPLOYMENT.md"

SECRET_VAR = "CPQ_USER_SECRET_KEY"
ENV_FILE_VAR = "CPQ_ENV_FILE"
GENERIC_500_TEXT = "服务异常，请稍后重试"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def def_block(text: str, marker: str) -> str:
    """Python：从 marker 截到下一个顶层 def/class（花括号无关）。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    match = re.search(r"\n(?=(?:def|class|@|async def)\s)", text[idx + len(marker):])
    end = idx + len(marker) + match.start() if match else len(text)
    return text[idx:end]


def child_python() -> str:
    """能 import 一体化服务的解释器（本机为 open-claude/.venv）。"""
    candidates = [str(ROOT / "open-claude" / ".venv" / "bin" / "python"),
                  sys.executable, shutil.which("python3"), shutil.which("python")]
    probe_code = ("import sys; sys.path.insert(0, %r); import cpq_suite_server" % str(ROOT))
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", probe_code], cwd=str(ROOT),
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return sys.executable


def run_child(script: str, *args: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="cpq-secret-probe-") as tmp:
        path = pathlib.Path(tmp) / "probe.py"
        path.write_text(script, encoding="utf-8")
        completed = subprocess.run([child_python(), str(path), *args],
                                   capture_output=True, text=True, timeout=240)
        if completed.returncode != 0:
            raise AssertionError("子进程探针失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                                 % (completed.returncode, completed.stdout[-2500:],
                                    completed.stderr[-2500:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])


def new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


# --------------------------------------------------------------------------- #
# 子进程 A：配置文件到底有没有被读进来
# --------------------------------------------------------------------------- #
CHILD_ENV = r'''
import base64
import json
import os
import sys
import tempfile

root, mode, env_file, key_in_file, key_exported = sys.argv[1:6]
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="cpq-env-probe-")
os.environ.pop("CPQ_USER_SECRET_KEY", None)
os.environ["CPQ_ENV_FILE"] = env_file
if key_exported:
    os.environ["CPQ_USER_SECRET_KEY"] = key_exported
sys.path.insert(0, root)

import cpq_suite_server as suite          # noqa: E402  导入期就该把配置文件读进来
import cpq_user_secrets                   # noqa: E402

out = {"mode": mode}
out["env_visible"] = bool(os.environ.get("CPQ_USER_SECRET_KEY"))
out["json_key"] = True
try:
    sealed = cpq_user_secrets.seal("probe")
    out["seal_ok"] = True
    out["roundtrip"] = cpq_user_secrets.open(sealed) == "probe"
    out["uses_expected"] = (cpq_user_secrets._key() == base64.b64decode(key_exported or key_in_file))
except BaseException as exc:               # noqa: BLE001
    out["seal_ok"] = False
    out["seal_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
print(json.dumps(out, ensure_ascii=False))
'''


# --------------------------------------------------------------------------- #
# 子进程 B：真打 /auth 的写接口，看缺密钥时回什么
# --------------------------------------------------------------------------- #
CHILD_AUTH = r'''
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

root = sys.argv[1]
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="cpq-auth-probe-")
sys.path.insert(0, root)

import cpq_suite_server as suite           # noqa: E402
import cpq_user_secrets                    # noqa: E402

INTERNAL = "internal-token-probe-1"
os.environ["CPQ_INTERNAL_TOKEN"] = INTERNAL
suite.CPQ_INTERNAL_TOKEN = INTERNAL
# 需要落库的接口先过 _backend 守卫；whoami 打桩，绝不真连 PG。
suite.cpq_auth._backend = "pg"
suite.cpq_auth.whoami = lambda token: {"user_id": "1", "username": "zhangsan",
                                       "role_code": "viewer"}
suite._auth_user_by_login = lambda name: {"user_id": "1", "username": name or "zhangsan"}
suite.cpq_auth.get_user_llm = lambda uid: {"model": "", "api_keys": {}}

STATE = {"mode": "secret"}
MISSING_TEXT = (cpq_user_secrets.ENV_VAR + " 未配置或不是 32 字节的 base64/hex 材料："
                "账号级模型与密钥必须加密落库，拒绝以明文写入。")


def fake_set_user_llm(user_id, model=None, provider=None, key=None):
    if STATE["mode"] == "secret":
        raise cpq_user_secrets.SecretKeyMissing(MISSING_TEXT)
    if STATE["mode"] == "boom":
        raise RuntimeError("boom")
    return {"model": "", "api_keys": {}}


suite.cpq_auth.set_user_llm = fake_set_user_llm

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), suite.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def call(method, path, body=None, internal=False):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                     data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if internal:
        request.add_header("X-Internal-Token", INTERNAL)
    else:
        request.add_header("Authorization", "Bearer user-token")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except BaseException as exc:            # noqa: BLE001
        return {"status": -1, "text": "%s: %s" % (type(exc).__name__, exc), "json": {}}
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        payload = {}
    return {"status": status, "text": raw[:600], "json": payload}


WRITE = {"api_key": "sk-probe-123", "api_key_provider": "anthropic"}
out = {}
out["my_llm_secret"] = call("PUT", "/auth/my/llm", WRITE)
out["internal_secret"] = call("PUT", "/auth/internal/user-llm",
                              {"username": "zhangsan", **WRITE}, internal=True)
STATE["mode"] = "boom"
out["my_llm_boom"] = call("PUT", "/auth/my/llm", WRITE)
STATE["mode"] = "ok"
out["my_llm_ok"] = call("PUT", "/auth/my/llm", WRITE)
out["get_internal"] = call("GET", "/auth/internal/user-llm?username=zhangsan", internal=True)
out["get_my"] = call("GET", "/auth/my/llm")
print(json.dumps(out, ensure_ascii=False))
'''


# --------------------------------------------------------------------------- #
# A. 源码与文档契约
# --------------------------------------------------------------------------- #
class SecretKeyEnvContractTest(unittest.TestCase):
    def test_01_spec_pins_the_contract(self):
        text = read(SPEC)
        self.assertTrue(text, "缺少 docs/specs/cpq-secret-key-env-and-loud-503.md")
        for token in (ENV_FILE_VAR, SECRET_VAR, "load_dotenv", "503", "DEPLOYMENT.md"):
            self.assertIn(token, text, "spec 未钉住 %s" % token)

    def test_02_suite_server_loads_env_file_before_reading_env(self):
        text = read(SUITE_PY)
        index = text.find("load_dotenv(")
        self.assertNotEqual(-1, index,
                            "cpq_suite_server.py 没有 load_dotenv()：配置文件这条路在 8010 上不存在")
        self.assertIn(ENV_FILE_VAR, text,
                      "配置文件的路径要支持 %s（把密钥放在仓库外）" % ENV_FILE_VAR)
        for later in ("import cpq_auth", "import cpq_wf", "import cpq_agent_server"):
            at = text.find(later)
            self.assertNotEqual(-1, at, "找不到 %s" % later)
            self.assertLess(index, at,
                            "load_dotenv() 必须排在 `%s` 之前：这些模块在导入期就读环境变量" % later)

    def test_03_secret_key_missing_maps_to_503(self):
        text = read(SUITE_PY)
        self.assertTrue(re.search(r"except\s+cpq_user_secrets\.SecretKeyMissing", text),
                        "缺少 SecretKeyMissing 的专用分支，缺密钥会被压成通用 500")
        block = def_block(text, "except cpq_user_secrets.SecretKeyMissing")
        self.assertIn("503", block, "缺密钥必须回 503：%s" % block[:300])
        self.assertIn(SECRET_VAR, block, "503 文案必须点名 %s：%s" % (SECRET_VAR, block[:300]))
        self.assertIn("未配置", block, "503 文案要说清是「未配置」：%s" % block[:300])
        self.assertIn(GENERIC_500_TEXT, text,
                      "通用 500 分支不得被顺手改掉（其它异常仍是 500）")

    def test_04_deployment_lists_the_two_env_vars(self):
        text = read(DEPLOYMENT_MD)
        for token in (SECRET_VAR, "CPQ_INTERNAL_TOKEN"):
            self.assertIn(token, text, "DEPLOYMENT.md 部署前置检查缺 %s" % token)
        self.assertIn("不可更换", text,
                      "DEPLOYMENT.md 必须写清加密密钥启用后不可更换（换了连读都会失败）")

    def test_05_tech_app_message_stops_claiming_login_outage(self):
        body = def_block(read(MAIN_PY), "async def cpq_auth_unavailable_handler(")
        self.assertTrue(body, "找不到 tech_app 的 CpqAuthUnavailable 处理函数")
        self.assertIn("账号级模型与密钥", body,
                      "文案要指向账号级模型与密钥这件事：%s" % body[:300])
        self.assertNotIn("登录服务暂不可用", body,
                         "缺密钥不是「登录服务故障」，不要再这么写：%s" % body[:300])


# --------------------------------------------------------------------------- #
# B. 配置文件真被读进来
# --------------------------------------------------------------------------- #
class SecretKeyEnvFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = pathlib.Path(tempfile.mkdtemp(prefix="cpq-secret-env-"))
        cls.key_in_file = new_key()
        cls.key_exported = new_key()
        cls.env_file = cls.work / "cpq_env.sh"
        # 与线上 cpq_env.sh 同一种写法：shell 风格 + export 前缀。
        cls.env_file.write_text("export %s=%s\n" % (SECRET_VAR, cls.key_in_file),
                                encoding="utf-8")
        cls.file_only = run_child(CHILD_ENV, str(ROOT), "file-only", str(cls.env_file),
                                  cls.key_in_file, "")
        cls.exported_wins = run_child(CHILD_ENV, str(ROOT), "exported-wins", str(cls.env_file),
                                      cls.key_in_file, cls.key_exported)

    def test_10_env_file_is_actually_loaded(self):
        self.assertTrue(self.file_only.get("env_visible"),
                        "%s 指向的文件没有被读进环境：%s" % (ENV_FILE_VAR, self.file_only))
        self.assertTrue(self.file_only.get("seal_ok"),
                        "配置文件里的加密密钥没生效：%s" % self.file_only)
        self.assertTrue(self.file_only.get("roundtrip"),
                        "seal/open 往返失败：%s" % self.file_only)
        self.assertTrue(self.file_only.get("uses_expected"),
                        "用的不是配置文件里那把密钥：%s" % self.file_only)

    def test_11_exported_value_wins_over_file(self):
        self.assertTrue(self.exported_wins.get("uses_expected"),
                        "已经 export 的变量优先于文件内容（load_dotenv 不得覆盖）：%s"
                        % self.exported_wins)


# --------------------------------------------------------------------------- #
# C. /auth 写接口：缺密钥 = 可执行的 503
# --------------------------------------------------------------------------- #
class SecretKeyLoud503Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_child(CHILD_AUTH, str(ROOT))

    def _loud(self, case: str, got: dict):
        self.assertEqual(503, got.get("status"),
                         "缺加密密钥必须回 503（现在是 %s）：%s" % (got.get("status"), got))
        error = str((got.get("json") or {}).get("error") or "")
        self.assertIn(SECRET_VAR, error,
                      "%s 的报错必须点名 %s：%s" % (case, SECRET_VAR, got))
        self.assertIn("未配置", error, "%s 的报错要说清是「未配置」：%s" % (case, got))
        self.assertNotIn(GENERIC_500_TEXT, error,
                         "%s 不能再回「%s」：%s" % (case, GENERIC_500_TEXT, got))

    def test_20_my_llm_put_is_503_with_actionable_text(self):
        self._loud("PUT /auth/my/llm", self.out.get("my_llm_secret") or {})

    def test_21_internal_put_is_503_with_actionable_text(self):
        self._loud("PUT /auth/internal/user-llm", self.out.get("internal_secret") or {})

    def test_22_other_errors_stay_500(self):
        got = self.out.get("my_llm_boom") or {}
        self.assertEqual(500, got.get("status"),
                         "其它异常仍必须是 500，不能顺手都改成 503：%s" % got)
        self.assertIn(GENERIC_500_TEXT, str((got.get("json") or {}).get("error") or ""),
                      "通用 500 文案不得变：%s" % got)

    def test_23_read_paths_unaffected(self):
        for case in ("get_my", "get_internal"):
            got = self.out.get(case) or {}
            self.assertEqual(200, got.get("status"),
                             "读取不 seal，缺密钥也必须照常可用（%s）：%s" % (case, got))

    def test_24_normal_write_still_200(self):
        got = self.out.get("my_llm_ok") or {}
        self.assertEqual(200, got.get("status"),
                         "密钥齐备时写入必须正常（不得因为本批改动而回归）：%s" % got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
