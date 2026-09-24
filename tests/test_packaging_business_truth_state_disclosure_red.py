# -*- coding: utf-8 -*-
"""业务部件的「识别 / 推断 / 待确认」三档必须走到读接口与页面（红测）。

Spec：`docs/specs/packaging-business-truth-state-disclosure.md`
编号（A/B/C/D/E/F）与该 Spec §2 各条契约逐条对应。

现状缺口（实测，不是推断；HEAD `8f8a910` 工作副本）：

  · `packaging_business_part_resolver` 早就把每件业务部件分成
    `observed` / `inferred` / `pending_confirmation` 三档（`## 466`），
    `resolve_business_parts()["detail"]` 也给了 `truth_state_counts`；
  · 但 `packaging_parts.business_parts_document()` 的行形状是固定五键、**不带** `truth_state`，
    `summarize_business_parts()` 也没有三档计数；
  · 流程 `detail` 的复制清单里没有三档；读接口 `_business_parts_body()` 的 `summary` 同样没有；
  · `tech_app/frontend/app.js` 全文 0 处 `truth_state`。

业务后果：页面上「从图纸推导（待人工确认）」是一句笼统提示 —— 客户看不出**哪几件是图上有的、
哪几件是结构规则补的**（真样本 `内卡` / `磁铁` 就是补的那两件）。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据；
  · 前端两条纯函数用 `node` **实际执行**（不是文本 grep）；`node --check` 守住语法；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRONTEND = ROOT / "tech_app" / "frontend"
APP_JS = FRONTEND / "app.js"
STEPS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "steps.py"
SAMPLE_ROOT = ROOT / "tech_app/data/cad-ir-realsample/conversions"

WINE = ("47c39dc1ab6738fc48c8",
        "0991c8b0a9646d1fea6571d2ae6155923351c05ca2aeeb6ed544ef19df93f3e0")

TRUTH_STATES = ("observed", "inferred", "pending_confirmation")
#: Spec §2.6 的文案表（逐字）。
#: `observed` 的标签按 `## 495` 去掉（用户要求"图上识别"这句直接不要）——
#: 见 docs/specs/packaging-2-1-part-row-size-and-material-lines.md §2.3/§2.4。
TRUTH_LABELS = {"observed": "", "inferred": "规则纠名（推断）",
                "pending_confirmation": "结构规则补件（待确认）"}

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
const fn = extract(process.argv[3]);
if (!fn) { console.log(JSON.stringify({missing: true})); process.exit(0); }
const cases = JSON.parse(process.argv[4]);
const out = {missing: false, results: []};
eval(fn);
for (const c of cases) { try { out.results.push(eval(process.argv[3] + "(" + JSON.stringify(c) + ")")); }
  catch (e) { out.results.push("ERR:" + e.message); } }
console.log(JSON.stringify(out));
"""


def run_frontend_cases(function: str, cases):
    """把 app.js 里的纯函数抽出来交给 node 真跑。"""
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), function,
                           json.dumps(cases)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def parts_module():
    return importlib.import_module("tech_app.backend.services.packaging_parts")


def resolver_module():
    return importlib.import_module("tech_app.backend.services.packaging_business_part_resolver")


def main_module():
    return importlib.import_module("tech_app.backend.main")


def authority(rows, *, derived: bool = True):
    doc = {"source": {"file_hash": "red", "sheet": "red"}, "parts": list(rows)}
    if derived:
        doc["derived_from_drawing"] = True
    return doc


def derived_rows():
    return [
        {"business_part_code": "PART-01", "name": "左盖面纸", "truth_state": "observed"},
        {"business_part_code": "PART-02", "name": "底托灰板", "truth_state": "inferred"},
        {"business_part_code": "PART-03", "name": "磁铁", "truth_state": "pending_confirmation"},
        {"business_part_code": "PART-04", "name": "没有档位"},
    ]


def build_doc(rows=None, *, derived: bool = True):
    return parts_module().business_parts_document(authority(rows or derived_rows(), derived=derived),
                                                 {"parts": []})


def stats_of(doc):
    return doc.get("stats") if isinstance(doc.get("stats"), dict) else {}


# --------------------------------------------------------------------------- #
# A 组：行形状必须带 truth_state（Spec §2.1）
# --------------------------------------------------------------------------- #
class RowsCarryTruthStateRed(unittest.TestCase):
    def test_a1_derived_rows_carry_the_state_verbatim(self):
        rows = build_doc()["business_parts"]
        got = [row.get("truth_state") for row in rows[:3]]
        self.assertEqual(["observed", "inferred", "pending_confirmation"], got,
                         "图纸来源的行必须逐字带着三档（Spec §2.1）：%r" % got)

    def test_a2_missing_state_defaults_to_observed(self):
        rows = build_doc()["business_parts"]
        self.assertEqual("observed", rows[3].get("truth_state"),
                         "图纸来源但没给档位 → `observed`（解析器自己就是这么兜的，Spec §2.1）")

    def test_a3_workbook_rows_do_not_claim_observed(self):
        doc = build_doc([{"business_part_code": "P1", "name": "工作簿件"}], derived=False)
        row = doc["business_parts"][0]
        self.assertEqual("", row.get("truth_state"),
                         "不是从图纸推导的清单不许伪称「图上识别」（Spec §2.1）：%r"
                         % (row.get("truth_state"),))

    def test_a4_existing_row_keys_are_untouched(self):
        rows = build_doc()["business_parts"]
        self.assertTrue({"business_part_code", "name", "authority", "geometry_binding", "thumbnail"}
                        <= set(rows[0]),
                        "既有五键必须逐字保留（Spec §2.1）：%r" % sorted(rows[0]))

    def test_a5_unknown_state_falls_back_without_raising(self):
        doc = build_doc([{"business_part_code": "P1", "name": "x", "truth_state": "wat"}])
        self.assertEqual("observed", doc["business_parts"][0].get("truth_state"),
                         "闭集外的值走「没给」那条路，不许抛异常（Spec §2.1）")


# --------------------------------------------------------------------------- #
# B 组：三档计数（Spec §2.2）
# --------------------------------------------------------------------------- #
class TruthStateCountersRed(unittest.TestCase):
    def test_b1_document_stats_have_the_counters(self):
        stats = stats_of(build_doc())
        for key in ("truth_state_counts", "observed_total", "inferred_total",
                    "pending_confirmation_total"):
            self.assertIn(key, stats, "文档 stats 缺 %s（Spec §2.2）" % key)
        self.assertEqual(sorted(TRUTH_STATES), sorted(stats["truth_state_counts"]),
                         "`truth_state_counts` 三档键必须都在（Spec §2.2）")

    def test_b2_summary_has_the_counters(self):
        doc = build_doc()
        stats = parts_module().summarize_business_parts(doc)["stats"]
        self.assertEqual(2, stats.get("observed_total"),
                         "摘要里的三档分子：第三件没给档位 ⇒ 按 `observed` 兜（Spec §2.1/§2.2）")
        self.assertEqual(1, stats.get("inferred_total"), "摘要里的三档分子（Spec §2.2）")
        self.assertEqual(1, stats.get("pending_confirmation_total"), "摘要里的三档分子（Spec §2.2）")

    def test_b3_counts_match_the_rows(self):
        stats = stats_of(build_doc())
        self.assertEqual({"observed": 2, "inferred": 1, "pending_confirmation": 1},
                         stats["truth_state_counts"],
                         "计数必须与行一致：给档位的三件 + 没给档位按 `observed` 兜的那一件（Spec §2.1/§2.2）")
        self.assertEqual(4, stats.get("business_part_total"), "总件数不许被三档口径改掉（Spec §2.2）")

    def test_b4_empty_document_counts_are_zero(self):
        stats = parts_module().summarize_business_parts({})["stats"]
        self.assertEqual(0, stats.get("observed_total"), "空文档给 0，不许 null / 抛错（Spec §2.2）")
        self.assertEqual({"observed": 0, "inferred": 0, "pending_confirmation": 0},
                         stats.get("truth_state_counts"), "空文档三档全 0（Spec §2.2）")

    def test_b5_workbook_document_counts_nothing_but_the_total(self):
        """工作簿来源的清单：闭集外的值 / 空串一律 **不** 变 `observed`，只进总件数（Spec §2.1/§2.2）。"""
        doc = build_doc([{"business_part_code": "P1", "name": "x", "truth_state": "wat"},
                         {"business_part_code": "P2", "name": "y", "truth_state": ""}],
                        derived=False)
        stats = stats_of(doc)
        self.assertEqual({"observed": 0, "inferred": 0, "pending_confirmation": 0},
                         stats["truth_state_counts"], "工作簿来源的行不进任何一档（Spec §2.2）")
        self.assertEqual(2, stats.get("business_part_total"), "它们仍进总件数（Spec §2.2）")
        self.assertEqual(["", ""], [row.get("truth_state") for row in doc["business_parts"]],
                         "工作簿来源的行是空串，不是 `observed`（Spec §2.1）")

    def test_b6_existing_five_counts_are_untouched(self):
        stats = stats_of(build_doc())
        for key in ("business_part_total", "bound_total", "partial_total", "unbound_total",
                    "ambiguous_total"):
            self.assertIn(key, stats, "既有五笔账不许被本批挪走（Spec §2.2）：%s" % key)


# --------------------------------------------------------------------------- #
# C 组：判定只有一处（Spec §2.3）
# --------------------------------------------------------------------------- #
class SingleCaliberRed(unittest.TestCase):
    def test_c1_closed_set_matches_the_resolver(self):
        module = parts_module()
        declared = getattr(module, "BUSINESS_TRUTH_STATES", None)
        self.assertIsNotNone(declared, "`packaging_parts` 侧必须有闭集常量（Spec §2.3）")
        self.assertEqual(tuple(resolver_module().TRUTH_STATES), tuple(declared),
                         "三档闭集必须与解析器逐字相同（Spec §2.3）")

    def test_c2_resolver_is_still_the_source_of_the_states(self):
        conversion_id, drawing_sha = WINE
        path = SAMPLE_ROOT / conversion_id / "converted.dxf"
        if not path.exists():
            self.skipTest("缺少真样本 DXF（%s）" % conversion_id)
        cad_ir = importlib.import_module("tech_app.backend.services.cad_ir")
        ir = cad_ir.parse_dxf(path.read_bytes(), filename="酒盒.dxf",
                             source={"source_sha256": drawing_sha,
                                     "original_filename": "酒盒.dwg"})
        resolver = resolver_module()
        out = resolver.resolve_business_parts("red-test", ir, None)
        rows = [row for row in (out.get("authority_rows") or []) if isinstance(row, dict)]
        self.assertTrue(rows, "真样本必须解析出业务部件（Spec §2.3）")
        for row in rows:
            self.assertIn(str(row.get("truth_state") or "observed"), TRUTH_STATES,
                          "解析器的三档判定不许被本批改动（Spec §2.3）：%r" % row.get("truth_state"))
        counts = (out.get("detail") or {}).get("truth_state_counts") or {}
        self.assertEqual(rows_total(rows), sum(int(counts.get(k) or 0) for k in TRUTH_STATES),
                         "解析器给的三档计数必须覆盖每一行（Spec §2.3）")


def code_to_label(value) -> str:
    """读接口的行没有这一键时给 `None` —— 归一成 `""`（页面就是这么处理的，Spec §2.5）。"""
    return "" if value is None else str(value)


def rows_total(rows) -> int:
    return len([row for row in rows if str(row.get("truth_state") or "observed") in TRUTH_STATES])


# --------------------------------------------------------------------------- #
# D 组：流程 detail 也要说得出三档（Spec §2.4）
# --------------------------------------------------------------------------- #
class FlowDetailRed(unittest.TestCase):
    def test_d1_copy_list_carries_the_counters(self):
        source = STEPS_PY.read_text(encoding="utf-8")
        at = source.find("def _resolve_business_parts(")
        self.assertGreater(at, 0, "`_resolve_business_parts()` 不见了（Spec §2.4）")
        window = source[at:at + 6000]
        for key in ("truth_state_counts", "observed_total", "inferred_total",
                    "pending_confirmation_total"):
            self.assertIn('"%s"' % key, window,
                          "流程 detail 的复制清单缺 %s（Spec §2.4）" % key)


# --------------------------------------------------------------------------- #
# E 组：读接口如实带出去（Spec §2.5）
# --------------------------------------------------------------------------- #
class ReadBodyRed(unittest.TestCase):
    def test_e1_summary_carries_the_counters(self):
        doc = build_doc()
        body = main_module()._business_parts_body("red-test", doc)
        stats = (body.get("summary") or {}).get("stats") or {}
        self.assertEqual(2, stats.get("observed_total"), "读接口的 summary 必须带三档（Spec §2.5）")
        self.assertEqual({"observed": 2, "inferred": 1, "pending_confirmation": 1},
                         stats.get("truth_state_counts"), "读接口的三档计数（Spec §2.5）")

    def test_e2_rows_carry_the_state(self):
        body = main_module()._business_parts_body("red-test", build_doc())
        rows = [row for row in (body.get("business_parts") or []) if isinstance(row, dict)]
        self.assertEqual("pending_confirmation", rows[2].get("truth_state"),
                         "读接口的行必须带着档位（Spec §2.5）")

    def test_e3_legacy_document_is_zero_not_error(self):
        legacy = {"business_parts": [{"business_part_code": "P1", "name": "老件",
                                      "geometry_binding": {"status": "unbound"}}],
                  "engine_version": "packaging-business-parts/1"}
        body = main_module()._business_parts_body("red-test", legacy)
        stats = (body.get("summary") or {}).get("stats") or {}
        self.assertEqual({"observed": 0, "inferred": 0, "pending_confirmation": 0},
                         stats.get("truth_state_counts"), "老文档给零计数（Spec §2.5）")
        self.assertEqual("", code_to_label(body["business_parts"][0].get("truth_state")),
                         "老文档的行缺这一键（或空串）时按「不显示」处理，不许抛异常（Spec §2.5）")

    def test_e4_existing_source_disclosure_keys_are_untouched(self):
        body = main_module()._business_parts_body("red-test", build_doc())
        for key in ("derived_from_drawing", "gold_standard_used", "refused_sources",
                    "binding_statuses", "gap", "source"):
            self.assertIn(key, body, "读接口既有键不许被本批挪走（Spec §2.5）：%s" % key)


# --------------------------------------------------------------------------- #
# F 组：页面照 payload 显示（Spec §2.6）
# --------------------------------------------------------------------------- #
class FrontendRed(unittest.TestCase):
    def test_f1_pure_functions_exist_and_are_free_of_dom(self):
        source = APP_JS.read_text(encoding="utf-8")
        for name in ("packagingBusinessPartTruthLabel", "packagingBusinessTruthLine"):
            self.assertIn("function %s(" % name, source,
                          "缺少纯函数 %s()：页面文案必须可被 node 直接执行（Spec §2.6）" % name)
            body = source.split("function %s(" % name, 1)[1].split("\nfunction ", 1)[0]
            for forbidden in ("document", "window", "localStorage", "sessionStorage", "$(", "fetch("):
                self.assertFalse(forbidden in body,
                                 "%s() 不得引用 %s（Spec §2.6）" % (name, forbidden))

    def test_f2_label_texts_are_verbatim(self):
        cases = ["observed", "inferred", "pending_confirmation", "wat", "", None]
        out = run_frontend_cases("packagingBusinessPartTruthLabel", cases)
        self.assertFalse(out.get("missing"), "packagingBusinessPartTruthLabel() 不存在")
        self.assertEqual([TRUTH_LABELS["observed"], TRUTH_LABELS["inferred"],
                          TRUTH_LABELS["pending_confirmation"], "", "", ""],
                         out["results"], "三档文案必须逐字取自 Spec §2.6")

    def test_f3_truth_line_reads_the_counters(self):
        cases = [
            {"summary": {"stats": {"truth_state_counts": {"observed": 20, "inferred": 5,
                                                          "pending_confirmation": 2}}}},
            {"summary": {"stats": {"observed_total": 1, "inferred_total": 2,
                                   "pending_confirmation_total": 3}}},
            {"summary": {"stats": {"truth_state_counts": {"observed": 0, "inferred": 0,
                                                          "pending_confirmation": 0}}}},
            {},
        ]
        out = run_frontend_cases("packagingBusinessTruthLine", cases)
        self.assertFalse(out.get("missing"), "packagingBusinessTruthLine() 不存在")
        self.assertEqual(["识别 20 · 推断 5 · 待确认 2", "识别 1 · 推断 2 · 待确认 3", "", ""],
                         out["results"], "三档合计为 0 时不许显示这行（Spec §2.6）")

    def test_f4_render_slots_exist_and_syntax_is_valid(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("data-qq-truth-line", source, "业务部件树上缺三档那一行（Spec §2.6）")
        self.assertIn("data-qq-truth-state", source, "零件行上缺三档标签位（Spec §2.6）")
        proc = subprocess.run(["node", "--check", str(APP_JS)], capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(0, proc.returncode, "app.js 语法检查没过：%s" % (proc.stderr or "")[:400])


if __name__ == "__main__":
    unittest.main()
