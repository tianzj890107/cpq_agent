"""红测：业务部件面板的「依据」区要显示权威出处与绑定分量证据，并清掉上一件的残留。

Spec：`docs/specs/packaging-business-part-panel-evidence.md`
依赖口径：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（面板口径）、
          `docs/specs/packaging-business-part-plan-click-and-bound-outline.md`（点选分流与轮廓）

现状缺口（源码 / 形状实测，不是推断）：

  · `openPackagingBusinessPart()` 从不写 `#packagingPartEvidence` —— 上一件几何零件的实体证据
    （或"正在读取零件详情…"）会一直留在右栏，与当前选中的业务部件张冠李戴；
  · `main._business_parts_body()` 出参里没有 `source`（实测只有 binding_statuses / built /
    business_parts / business_parts_hash / business_parts_id / engine_version / gap /
    geometry_evidence / summary），文档里的 `{ir_id, ir_hash, authority_file_hash, authority_sheet}`
    没有出口，页面无从显示"这份清单来自哪张表、哪一版"。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_JS_PATH = ROOT / "tech_app" / "frontend" / "app.js"
APP_JS = APP_JS_PATH.read_text(encoding="utf-8")

EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extract(name) {
  const at = src.indexOf("function " + name + "(");
  if (at < 0) return null;
  let i = src.indexOf("{", at);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(at, j + 1); }
  }
  return null;
}
const names = process.argv[3].split(",");
const deps = ["esc"];
const bodies = {};
for (const name of names.concat(deps)) {
  const fn = extract(name);
  if (fn) bodies[name] = fn;
}
const target = process.argv[4];
if (!bodies[target]) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
if (process.argv[5] === "body") { console.log(JSON.stringify({ missing: false, body: bodies[target] })); process.exit(0); }
for (const name of deps) { if (bodies[name]) eval(bodies[name]); }
eval(bodies[target]);
const cases = JSON.parse(process.argv[5]);
const out = [];
for (const args of cases) {
  const call = target + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def run_cases(name: str, cases, timeout: int = 60):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS_PATH), name, name, json.dumps(cases)],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS_PATH), name, name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def value_of(name: str, *args):
    payload = run_cases(name, [list(args)])
    if payload.get("missing"):
        raise AssertionError("app.js 里没有 %s" % name)
    result = payload["results"][0]
    if not result.get("ok"):
        raise AssertionError("%s(%r) 抛错：%s" % (name, args, result.get("error")))
    return result["value"]


BUSINESS_ROW = {
    "business_part_code": "JWXR21-P01", "name": "左盖面纸",
    "authority": {"product_size_text": "307.07x528.89mm", "material_text": "225G太阳铜版底PET光银",
                  "source": {"sheet": "零部件排版工艺", "row": 4}},
    "geometry_binding": {"status": "ambiguous", "component_ids": ["cmp:139", "cmp:47"]},
}
DOC_SOURCE = {"authority_file_hash": "1358f7cd363f1d8b8e7df59a6dea460dd1a420cfc54a6a4a5cee3ba41cbc3947",
              "authority_sheet": "零部件排版工艺", "ir_id": "", "ir_hash": ""}
COMPONENTS = [
    {"component_id": "cmp:139", "layers": ["CUT", "CREASE"], "role": "cut", "entity_ids": ["e1", "e2", "e3"]},
    {"component_id": "cmp:47", "layers": ["CUT"], "role": "cut", "entity_ids": ["e4"]},
    {"component_id": "cmp:other", "layers": ["FRAME"], "role": "frame", "entity_ids": []},
]


# --------------------------------------------------------------------------- #
# A 组：读接口带权威出处
# --------------------------------------------------------------------------- #
class AReadPayloadCarriesSource(unittest.TestCase):
    def test_a1_read_payload_passes_the_document_source_through(self):
        try:
            from tech_app.backend import main
        except Exception as exc:                                            # noqa: BLE001
            self.fail("导不进 tech_app.backend.main：%s" % exc)
        doc = {"business_parts": [{"business_part_code": "JWXR21-P01"}],
               "source": dict(DOC_SOURCE), "stats": {}, "geometry_evidence": {}}
        body = main._business_parts_body("probe-project", doc)
        self.assertIn("source", body,
                      "读接口把权威出处整块吞掉了：页面无从显示'这份清单来自哪张表、哪一版'（Spec §C1）")
        self.assertEqual(DOC_SOURCE, body["source"], "出处必须逐字透传")

    def test_a2_missing_source_stays_empty_not_invented(self):
        from tech_app.backend import main
        body = main._business_parts_body("probe-project", {"business_parts": [], "stats": {}})
        self.assertEqual({}, body.get("source"), "老文档没有出处就给 {}，不许编文件名")

    def test_a3_existing_keys_survive(self):
        from tech_app.backend import main
        body = main._business_parts_body("probe-project",
                                         {"business_parts": [{"business_part_code": "X"}], "stats": {}})
        for key in ("built", "engine_version", "business_parts_id", "business_parts_hash",
                    "business_parts", "geometry_evidence", "gap", "summary", "binding_statuses"):
            self.assertIn(key, body, "读接口既有键 %s 不许消失" % key)


# --------------------------------------------------------------------------- #
# B 组：依据行（纯函数，node 真跑）
# --------------------------------------------------------------------------- #
class BEvidenceRows(unittest.TestCase):
    def test_b1_authority_line_names_sheet_row_and_fingerprint(self):
        rows = value_of("packagingBusinessPartEvidenceRows", BUSINESS_ROW, COMPONENTS, DOC_SOURCE)
        self.assertTrue(rows, "依据行不能为空")
        first = rows[0]
        self.assertEqual("authority", first["kind"], "第一条必须是权威出处（Spec §C2）")
        self.assertIn("零部件排版工艺", first["ref"])
        self.assertIn("4", first["ref"])
        self.assertIn("1358f7cd", first["ref"], "出处要带文件指纹前 12 位（导入器不存文件名）")

    def test_b2_authority_line_is_honest_when_nothing_is_recorded(self):
        rows = value_of("packagingBusinessPartEvidenceRows",
                        {"business_part_code": "X", "authority": {}}, [], {})
        first = rows[0]
        self.assertEqual("authority", first["kind"])
        self.assertEqual("", first["ref"])
        self.assertIn("未记录", first["note"], "拼不出出处就如实说未记录，不许编")

    def test_b3_one_line_per_bound_component(self):
        rows = value_of("packagingBusinessPartEvidenceRows", BUSINESS_ROW, COMPONENTS, DOC_SOURCE)
        bound = [row for row in rows if row["kind"] == "component"]
        self.assertEqual(["cmp:139", "cmp:47"], [row["ref"] for row in bound],
                         "每件绑定分量一行，按绑定顺序")
        self.assertIn("cut", bound[0]["note"])
        self.assertIn("CUT", bound[0]["layer"])
        self.assertIn("3", bound[0]["note"], "图元数要写出来")

    def test_b4_unbound_part_says_so(self):
        rows = value_of("packagingBusinessPartEvidenceRows",
                        {"business_part_code": "X", "authority": {}, "geometry_binding": {}},
                        COMPONENTS, {})
        kinds = [row["kind"] for row in rows]
        self.assertIn("binding", kinds, "没有绑定时要有一行说明（Spec §C2）")
        note = [row["note"] for row in rows if row["kind"] == "binding"][0]
        self.assertIn("尚未在 CAD 图中定位", note)

    def test_b5_no_literal_undefined_in_notes(self):
        rows = value_of("packagingBusinessPartEvidenceRows",
                        {"business_part_code": "X", "authority": {},
                         "geometry_binding": {"component_ids": ["cmp:missing"]}}, [], {})
        for row in rows:
            self.assertNotIn("undefined", row["note"])
            self.assertNotIn("null", row["note"])


# --------------------------------------------------------------------------- #
# C 组：接线（函数体断言）
# --------------------------------------------------------------------------- #
class CWiring(unittest.TestCase):
    def test_c1_business_panel_rewrites_the_evidence_block(self):
        body = function_body("openPackagingBusinessPart")
        self.assertTrue(body, "openPackagingBusinessPart() 找不到（签名变了？）")
        self.assertIn("packagingPartEvidence", body,
                      "面板必须重写依据区，不许留上一件的内容（Spec §C2）")
        self.assertIn("packagingBusinessPartEvidenceRows", body, "依据行必须走纯函数")

    def test_c2_geometry_panel_shares_the_row_renderer(self):
        body = function_body("renderPackagingPartPanel")
        self.assertIn("packagingPartEvidenceRowsHtml", body,
                      "两种面板共用一套行渲染（Spec §C3）")
        self.assertIn("packagingPartEvidence", body)

    def test_c3_plan_arrival_refreshes_the_selected_business_part(self):
        body = function_body("renderPackagingCadPlan")
        self.assertIn("currentPackagingBusinessPartCode", body,
                      "证据后到要用新证据重画当前业务部件（Spec §C4）")
        self.assertIn("openPackagingBusinessPart", body, "刷新就是重画这一件面板")


# --------------------------------------------------------------------------- #
# D 组：护栏（现状即绿）
# --------------------------------------------------------------------------- #
class DGuards(unittest.TestCase):
    def test_d1_geometry_part_channel_is_not_rerouted(self):
        body = function_body("selectPackagingPart")
        self.assertIn("packaging-parts/", body, "几何零件通道仍读单件详情接口")
        self.assertNotIn("packagingBusinessPartEvidenceRows", body)

    def test_d2_new_functions_are_node_executable(self):
        body = function_body("packagingBusinessPartEvidenceRows")
        self.assertTrue(body, "缺少纯函数 packagingBusinessPartEvidenceRows()")
        for token in ("document", "sessionStorage", "localStorage", "window.", "fetch("):
            self.assertNotIn(token, body, "纯函数不许引用 %s" % token)

    def test_d3_row_renderer_keeps_the_shared_markup(self):
        body = function_body("packagingPartEvidenceRowsHtml")
        self.assertTrue(body, "缺少共用行渲染 packagingPartEvidenceRowsHtml()")
        self.assertIn("packaging-part-evidence-row", body, "块样式必须与既有面板一致")
        self.assertIn("这一件没有可回查的实体证据。", body, "空态文案不许改")

    def test_d4_app_js_still_parses(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("未安装 node，跳过语法检查")
        completed = subprocess.run([node, "--check", str(APP_JS_PATH)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(0, completed.returncode, "app.js 语法错误：\n%s" % completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
