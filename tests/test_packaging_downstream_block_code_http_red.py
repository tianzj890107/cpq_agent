"""护栏：包装下游"做不下去"的原因必须在**出口**上留住稳定 `code`。

Spec：`docs/specs/packaging-downstream-block-code-parity.md`
契约本体红测：`tests/test_packaging_downstream_block_code_red.py`（7 条，服务层）

那 7 条只管到服务层：业务错误带上 `code` 了。但只要中间任意一段把结构吃掉，
用户看到的仍然是一句没法处置的中文，前端也仍然判不出"缺什么补什么"。本文件
补的是**出口侧两段**：

  · `tech_app/backend/main.py`：五条包装链路（盒型匹配 / BOM / 路线 / 成本 /
    回传）的业务错误必须以同一形状 `{message, code}` 出 HTTP。历史事实是五条
    全都只回 `str(exc)`，Spec §2.2 对这个"既有出口"的描述与源码不符（已记进
    Spec 的偏差段与 changelog）。
  · `tech_app/frontend/workflow.js` + `requirement-confirm.js`：`apiError()` 与
    四个 `*Api()` 兜底必须能从结构化 detail 里取出人话（否则屏幕上只剩
    `请求失败 (409)` / `[object Object]`），盒型匹配面板必须按 `code` 给下一步。

纪律：只读源码 + 假 store + `node` 真跑前端纯函数；不连 PG、不发 HTTP、不写文件。
禁止为了让本文件转绿而修改本文件。
"""
from __future__ import annotations

import ast
import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_match  # noqa: E402

MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
WORKFLOW_JS = ROOT / "tech_app" / "frontend" / "workflow.js"
CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

FLOW_HELPERS = ("_box_match_flow", "_packaging_bom_flow", "_packaging_route_flow",
                "_packaging_cost_flow", "_packaging_handoff_flow")

EXTRACT_JS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const marker = 'function ' + process.argv[2] + '(';
const start = src.indexOf(marker);
if (start < 0) { throw new Error('missing ' + process.argv[2]); }
let depth = 0, seen = false, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
  const ch = src[i];
  if (ch === '{') { depth++; seen = true; }
  else if (ch === '}') { depth--; if (seen && depth === 0) { end = i + 1; break; } }
}
if (end < 0) { throw new Error('unbalanced ' + process.argv[2]); }
const fn = eval('(' + src.slice(start, end) + ')');
const out = JSON.parse(process.argv[3]).map(args => fn.apply(null, args));
process.stdout.write(JSON.stringify(out));
"""


def function_source(path: pathlib.Path, name: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text)
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return ""


def run_js_function(path: pathlib.Path, name: str, cases: list) -> list:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("本机没有 node，无法真跑前端纯函数")
    proc = subprocess.run([node, "-e", EXTRACT_JS, str(path), name, json.dumps(cases)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout)


class _FakeExc(Exception):
    def __init__(self, message, status_code, code=""):
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = str(code)


# --------------------------------------------------------------------------- #
# A 组：五条链路的 HTTP 出口同构
# --------------------------------------------------------------------------- #
class AHttpParity(unittest.TestCase):

    def test_a1_shared_detail_helper_keeps_code_and_message(self):
        body = function_source(MAIN_PY, "_packaging_error_detail")
        self.assertTrue(body, "main.py 必须有 _packaging_error_detail（Spec §2.2）")
        import tech_app.backend.main as main
        detail = main._packaging_error_detail(_FakeExc("缺少需求草稿", 409, "requirement_draft_missing"))
        self.assertIsInstance(detail, dict, "出口必须是结构化 detail，不能退回纯字符串")
        self.assertEqual("缺少需求草稿", detail.get("message"))
        self.assertEqual("requirement_draft_missing", detail.get("code"))
        blank = main._packaging_error_detail(_FakeExc("别的业务拒绝", 400))
        self.assertEqual("", blank.get("code"),
                         "没有码的业务错误 code 必须是空串（不能是 None），否则前端判分支会踩空")

    def test_a2_every_packaging_flow_uses_the_shared_detail(self):
        bad = []
        for name in FLOW_HELPERS:
            body = function_source(MAIN_PY, name)
            if not body:
                bad.append("%s：main.py 里找不到这个出口函数" % name)
                continue
            if "_packaging_error_detail" not in body:
                bad.append("%s：没有把 code 放进 HTTP 返回体" % name)
            if "str(exc))" in body.replace(" ", ""):
                bad.append("%s：仍在用 str(exc) 直接当 detail" % name)
        self.assertEqual([], bad, "五条包装链路的出口必须同构（Spec §2.2）：\n" + "\n".join(bad))

    def test_a3_cross_industry_rejection_also_carries_a_code(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        start = source.index("def assert_industry_scoped_candidates")
        block = source[start:source.index("\ndef ", start + 10)]
        self.assertIn("no_industry_candidate", block,
                      "候选全部跨行业被剔除也是业务拒绝，必须带码（Spec §2.2）")

    def test_a4_routes_still_registered(self):
        import tech_app.backend.main as main
        paths = {route.path for route in main.app.routes}
        for path in ("/api/projects/{pid}/requirement/box-match",
                     "/api/projects/{pid}/requirement/packaging-bom",
                     "/api/projects/{pid}/requirement/packaging-route",
                     "/api/projects/{pid}/requirement/packaging-cost"):
            self.assertTrue(any(item.rstrip("/") == path.rstrip("/") for item in paths),
                            "本批只改出口形状，不许动路由：缺 %s" % path)


# --------------------------------------------------------------------------- #
# B 组：前端必须把结构化 detail 读成人话，并按码给下一步
# --------------------------------------------------------------------------- #
class BFrontend(unittest.TestCase):

    def test_b1_api_error_reads_structured_detail(self):
        cases = [
            [{"detail": {"code": "industry_missing", "message": "这张需求单的行业不是「包装」"}}, 400],
            [{"detail": "盒型匹配只对包装行业的需求单生效"}, 400],
            [{"detail": [{"loc": ["body", "x"], "msg": "字段必填"}]}, 422],
            [{}, 500],
        ]
        out = run_js_function(WORKFLOW_JS, "apiError", cases)
        self.assertEqual("这张需求单的行业不是「包装」", out[0],
                         "结构化 detail 里的人话必须活到界面，不能被换成「请求失败 (400)」")
        self.assertEqual("盒型匹配只对包装行业的需求单生效", out[1], "纯字符串 detail 的口径不许变")
        self.assertIn("字段必填", out[2], "422 的数组 detail 摊开口径不许变")
        self.assertIn("500", out[3], "都不认识时仍回状态码兜底")

    def test_b2_box_match_panel_dispatches_on_code(self):
        source = CONFIRM_JS.read_text(encoding="utf-8", errors="replace")
        for code in ("requirement_draft_missing", "industry_missing"):
            self.assertIn(code, source, "盒型匹配面板必须按码给处置（Spec §2.3）：缺 %s" % code)
        self.assertIn("requirement-create.html", source, "跳转出口必须指向需求单页面")
        for keys in ("error.code", "bmBlockAction", "BM_BLOCK_ACTIONS"):
            self.assertIn(keys, source, "面板要按 code 选出口：缺 %s" % keys)
        self.assertIn("box_type_not_confirmed", source,
                      "未确认盒型是「留在本页继续确认」，也必须在这张表里出现")

    def test_b3_confirm_panel_apis_do_not_swallow_object_detail(self):
        source = CONFIRM_JS.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("detail = payload.detail || payload.message || '';", source,
                         "对象 detail 会被拼成 [object Object]：必须从 detail.message 取人话")
        for name in ("bmApi", "pbApi", "prApi", "pcApi"):
            start = source.index("function %s(" % name) if ("function %s(" % name) in source \
                else source.index("async function %s(" % name)
            block = source[start:start + 1800]
            self.assertIn("detail", block, "%s 必须从响应里取 detail" % name)

    def test_b4_scripts_still_parse(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("本机没有 node，跳过语法护栏")
        for path in (WORKFLOW_JS, CONFIRM_JS):
            proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(0, proc.returncode,
                             "%s 语法不过：%s" % (path.name, (proc.stderr or "")[:400]))


# --------------------------------------------------------------------------- #
# C 组：拒绝不许被放松，既有状态码分布不许被本批改掉
# --------------------------------------------------------------------------- #
class CStatusCodesFrozen(unittest.TestCase):

    def test_c1_decide_box_match_keeps_403_400_409(self):
        real_load = packaging_match.store.load_requirement
        real_record = packaging_match.da_repo.load_box_match
        packaging_match.store.load_requirement = lambda project_id: {
            "project_id": "p", "requirement_no": "REQ-X", "data": {"industry": "packaging"}}
        packaging_match.da_repo.load_box_match = lambda *a, **k: {}
        try:
            with self.assertRaises(packaging_match.BoxMatchError) as role:
                packaging_match.decide_box_match("p", "REQ-X", "confirmed", "BOX-A",
                                                 actor={"role": "viewer"})
            with self.assertRaises(packaging_match.BoxMatchError) as unknown:
                packaging_match.decide_box_match("p", "REQ-X", "bogus", None,
                                                 actor={"role": "process_manager"})
            with self.assertRaises(packaging_match.BoxMatchError) as norecord:
                packaging_match.decide_box_match("p", "REQ-X", "confirmed", "BOX-A",
                                                 actor={"role": "process_manager"})
        finally:
            packaging_match.store.load_requirement = real_load
            packaging_match.da_repo.load_box_match = real_record
        self.assertEqual(403, role.exception.status_code)
        self.assertEqual(400, unknown.exception.status_code)
        self.assertEqual(409, norecord.exception.status_code)
        for exc in (role.exception, unknown.exception, norecord.exception):
            self.assertTrue(str(exc.code), "既有拒绝点也必须带码，但状态码分布不许变")

    def test_c2_two_preconditions_keep_their_own_status_codes(self):
        real_load = packaging_match.store.load_requirement
        try:
            packaging_match.store.load_requirement = lambda project_id: None
            with self.assertRaises(packaging_match.BoxMatchError) as missing:
                packaging_match.run_box_match("p")
            packaging_match.store.load_requirement = lambda project_id: {
                "project_id": "p", "requirement_no": "REQ-X", "data": {"industry": "battery"}}
            with self.assertRaises(packaging_match.BoxMatchError) as industry:
                packaging_match.run_box_match("p")
        finally:
            packaging_match.store.load_requirement = real_load
        self.assertEqual((409, "requirement_draft_missing"),
                         (missing.exception.status_code, missing.exception.code))
        self.assertEqual((400, "industry_missing"),
                         (industry.exception.status_code, industry.exception.code))


if __name__ == "__main__":
    unittest.main()
