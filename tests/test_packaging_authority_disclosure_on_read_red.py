"""红测：权威清单的两条披露（跳过的行 / 部件图归属）必须在读回路径上活下来。

Spec：`docs/specs/packaging-authority-disclosure-on-read.md`
依赖口径：`packaging-business-parts-and-cad-plan-view.md`（导入器与业务部件层）、
          `packaging-business-part-panel-evidence.md`（面板依据区与读接口形状）。

现状缺口（真样本实测，不是推断）：

  · 导入器已经算出了两条披露：真样本 `裕同包装项目-待开发/酒盒 报价资料.xlsx` 跳过 4 行
    （含第 32 行客户备注「客人要求每个盒子需装配两包干燥剂，每次送货需1%的备品（免费），
    请核算价格注意」），28 件部件图的归属全是 `thumbnail_source="order"`（按顺序推定）；
  · `packaging_parts.business_parts_document()` 把这两条都丢了：文档里没有 `authority` 块，
    件级 `authority` 少了 `thumbnail_refs` / `thumbnail_source` / `group_hint`；
  · `main._business_parts_body()` 出参里没有 `authority` —— 刷新一次页面，披露就没了
    （只有导入那一次响应里有 `import_skipped` / `import_stats`）。

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


def authority_doc(*, image_total: int = 28, part_total: int = 28, sources=("order",),
                  with_images: bool = True, skipped=None):
    """按导入器的真实形状造一份权威清单产物（不是从工作簿现读：离线、确定）。"""
    parts = []
    for index in range(1, part_total + 1):
        source = sources[(index - 1) % len(sources)]
        ref = "image:零部件排版工艺!%d#%d" % (index, index) if with_images else ""
        parts.append({"sequence_no": index, "name": "件%d" % index,
                      "business_part_code": "JWXR21-P%02d" % index,
                      "thumbnail_ref": ref, "thumbnail_refs": [ref] if ref else [],
                      "thumbnail_source": source if ref else "",
                      "group_hint": "", "source": {"sheet": "零部件排版工艺", "row": index + 3}})
    return {"engine_version": "packaging-part-authority/1", "parts": parts,
            "skipped": list(skipped or []),
            "source": {"file": "酒盒 报价资料.xlsx", "sheet": "零部件排版工艺",
                       "file_hash": "1358f7cd363f1d8b8e7df59a6dea460dd1a420cfc54a6a4a5cee3ba41cbc3947",
                       "code_prefix": "JWXR21"},
            "stats": {"part_total": part_total, "image_total": image_total,
                      "skipped_total": len(skipped or []),
                      "thumbnail_bound_total": part_total if with_images else 0},
            "unavailable": []}


#: 真样本第一行（`零部件排版工艺` 第 4 行）的字段形状：既有 13 键一个不少。
FULL_PART_ROW = {
    "sequence_no": 1, "name": "左盖面纸", "business_part_code": "JWXR21-P01",
    "product_size_text": "307.07x528.89mm", "length_mm": 307.07, "width_mm": 528.89,
    "height_mm": None, "material_text": "225G太阳铜版底PET光银", "layout_text": "787x560mm=1M",
    "process_text": "开料-UV印刷K+4专+19um即涂防刮花哑胶（佳泓）+丝印UV 1处—全印莱+模切+包盒",
    "note": "丝印网版网目300", "thumbnail_ref": "image:零部件排版工艺!1#1",
    "thumbnail_refs": ["image:零部件排版工艺!1#1"], "thumbnail_source": "order",
    "merged_from": {}, "group_hint": "", "quantity": "", "purchase": "",
    "source": {"sheet": "零部件排版工艺", "row": 4},
}

REAL_SKIPPED = [{"row": 3, "reason": "blank_row", "message": "整行空白"},
                {"row": 32, "reason": "not_a_part_row",
                 "message": "有序号但没有名称，或既无尺寸也无材料/排版/工艺/部件图（说明行），"
                            "按非部件行跳过",
                 "sequence_no": 29,
                 "text": "客人要求每个盒子需装配两包干燥剂，每次送货需1%的备品（免费），请核算价格注意"},
                {"row": 33, "reason": "no_sequence", "message": "没有序号，按说明/签名行跳过",
                 "text": "制表：秦建"},
                {"row": 34, "reason": "blank_row", "message": "整行空白"}]


# --------------------------------------------------------------------------- #
# A 组：纯函数 authority_disclosure()
# --------------------------------------------------------------------------- #
class ADisclosurePureFunction(unittest.TestCase):
    def _fn(self):
        from tech_app.backend.services import packaging_parts
        fn = getattr(packaging_parts, "authority_disclosure", None)
        if fn is None:
            self.fail("packaging_parts 没有 authority_disclosure()（Spec §C1）")
        return fn

    def test_a1_empty_inputs_return_empty_object(self):
        fn = self._fn()
        for value in (None, {}, {"parts": []}, {"parts": None}, [], "x"):
            self.assertEqual({}, fn(value), "没有权威行时给 {}，不许抛异常、不许编数（Spec §C1）")

    def test_a2_stats_are_copied_and_defaulted_to_zero(self):
        fn = self._fn()
        got = fn(authority_doc(skipped=REAL_SKIPPED))
        self.assertEqual({"part_total": 28, "image_total": 28, "skipped_total": 4,
                          "thumbnail_bound_total": 28}, got["stats"],
                         "stats 四个键必须存在且逐字来自导入器（Spec §C1）")
        thin = fn({"parts": [{"name": "a"}]})
        self.assertEqual({"part_total": 1, "image_total": 0, "skipped_total": 0,
                          "thumbnail_bound_total": 1 if thin["thumbnail"]["bound_total"] else 0},
                         thin["stats"], "导入器没给 stats 时按件数兜底、其余给 0（Spec §C1）")

    def test_a3_thumbnail_ownership_is_reported_not_assumed(self):
        fn = self._fn()
        order = fn(authority_doc(sources=("order",)))["thumbnail"]
        self.assertEqual(28, order["order_total"])
        self.assertEqual(0, order["anchor_row_total"])
        self.assertEqual("order", order["bound_by"],
                         "28 件全按顺序归属必须报 order，不许说成逐行核对过（Spec §C1）")
        anchor = fn(authority_doc(sources=("anchor_row",)))["thumbnail"]
        self.assertEqual("anchor_row", anchor["bound_by"])
        mixed = fn(authority_doc(sources=("order", "anchor_row")))["thumbnail"]
        self.assertEqual("mixed", mixed["bound_by"])
        self.assertEqual(14, mixed["order_total"])
        self.assertEqual(14, mixed["anchor_row_total"])
        without = fn(authority_doc(with_images=False))["thumbnail"]
        self.assertEqual("", without["bound_by"])
        self.assertEqual(0, without["bound_total"])
        self.assertEqual(28, without["missing_total"])
        self.assertEqual(0, fn(authority_doc())[ "thumbnail"]["missing_total"])

    def test_a4_skipped_rows_are_copied_verbatim(self):
        fn = self._fn()
        got = fn(authority_doc(skipped=REAL_SKIPPED))
        self.assertEqual(4, len(got["skipped"]))
        self.assertEqual([3, 32, 33, 34], [row["row"] for row in got["skipped"]],
                         "跳过行的顺序不许变（Spec §C1）")
        for copy, original in zip(got["skipped"], REAL_SKIPPED):
            for key, value in original.items():
                self.assertEqual(value, copy.get(key),
                                 "跳过行的 %s 必须逐字照抄（Spec §C1）" % key)
            for key in ("row", "reason", "message", "sequence_no", "text"):
                self.assertIn(key, copy, "跳过行的键 %s 必须存在（Spec §C1）" % key)
        self.assertEqual(REAL_SKIPPED[1]["text"], got["skipped"][1]["text"],
                         "客户备注原文必须逐字带出来（Spec §C1）")
        self.assertEqual("", got["skipped"][0]["text"], "没有文字的跳过行给空串")
        thin = fn({"parts": [{"name": "a"}], "skipped": [{"row": 9, "reason": "blank_row"}]})
        self.assertEqual([{"row": 9, "reason": "blank_row", "message": "",
                           "sequence_no": 0, "text": ""}], thin["skipped"],
                         "缺省字段也要补齐，键必须存在（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：业务部件文档带上这两条
# --------------------------------------------------------------------------- #
class BDocumentCarriesDisclosure(unittest.TestCase):
    def _document(self, authority, geometry=None):
        from tech_app.backend.services import packaging_parts
        return packaging_parts.business_parts_document(authority, geometry or {})

    def test_b1_part_authority_keeps_thumbnail_and_group_keys(self):
        authority = authority_doc(skipped=REAL_SKIPPED)
        authority["parts"][0] = dict(FULL_PART_ROW)
        doc = self._document(authority)
        first = doc["business_parts"][0]["authority"]
        for key in ("thumbnail_refs", "thumbnail_source", "group_hint"):
            self.assertIn(key, first, "件级 authority 丢了 %s（Spec §C2）" % key)
        self.assertEqual("order", first["thumbnail_source"])
        self.assertEqual(["image:零部件排版工艺!1#1"], first["thumbnail_refs"])
        for key in ("sequence_no", "product_size_text", "material_text", "process_text",
                    "layout_text", "note", "thumbnail_ref", "merged_from", "quantity",
                    "purchase", "source", "length_mm", "width_mm"):
            self.assertIn(key, first, "既有键 %s 不许回退（Spec §C2）" % key)

    def test_b2_document_level_authority_block_matches_the_pure_function(self):
        from tech_app.backend.services import packaging_parts
        authority = authority_doc(skipped=REAL_SKIPPED)
        doc = self._document(authority)
        self.assertIn("authority", doc, "文档级没有 authority 块 —— 刷新一次披露就没了（Spec §C2）")
        self.assertEqual(packaging_parts.authority_disclosure(authority), doc["authority"])
        self.assertEqual(4, doc["authority"]["stats"]["skipped_total"])

    def test_b3_no_authority_rows_means_empty_block(self):
        doc = self._document({})
        self.assertEqual({}, doc["authority"], "没有业务部件行时给 {}（Spec §C2）")
        self.assertEqual([], doc["business_parts"])

    def test_b4_disclosure_is_part_of_the_version_anchor(self):
        from tech_app.backend.services import packaging_parts
        same_a = self._document(authority_doc(skipped=REAL_SKIPPED))
        same_b = self._document(authority_doc(skipped=REAL_SKIPPED))
        changed = self._document(authority_doc(skipped=REAL_SKIPPED, sources=("anchor_row",)))
        self.assertEqual(same_a["business_parts_hash"], same_b["business_parts_hash"],
                         "同输入必须同版本（幂等，Spec §C2）")
        self.assertNotEqual(same_a["business_parts_hash"], changed["business_parts_hash"],
                            "披露变了就是新版本，下游据此判 stale（Spec §C2）")

    def test_b5_existing_shape_survives(self):
        doc = self._document(authority_doc())
        for key in ("engine_version", "business_parts", "geometry_evidence", "stats",
                    "unavailable", "bindings_shared", "legacy_parts_id", "source"):
            self.assertIn(key, doc, "既有键 %s 不许回退（Spec §C2）" % key)
        self.assertEqual(28, doc["stats"]["business_part_total"])
        self.assertIn("authority_file_hash", doc["source"])


# --------------------------------------------------------------------------- #
# C 组：读接口交出来
# --------------------------------------------------------------------------- #
class CReadPayload(unittest.TestCase):
    def _body(self, doc):
        from tech_app.backend import main
        return main._business_parts_body("probe-project", doc)

    def test_c1_generated_document_passes_the_block_through(self):
        from tech_app.backend.services import packaging_parts
        authority = authority_doc(skipped=REAL_SKIPPED)
        doc = packaging_parts.business_parts_document(authority, {})
        body = self._body(doc)
        self.assertIn("authority", body,
                      "读接口把披露整块吞掉了：刷新一次页面就没了（Spec §C3）")
        self.assertEqual(packaging_parts.authority_disclosure(authority), body["authority"])
        for key in ("built", "engine_version", "business_parts_id", "business_parts_hash",
                    "business_parts", "geometry_evidence", "gap", "summary", "source",
                    "binding_statuses"):
            self.assertIn(key, body, "既有键 %s 不许回退（Spec §C3）" % key)

    def test_c2_missing_document_gives_empty_block(self):
        body = self._body({"business_parts": [], "stats": {}})
        self.assertEqual({}, body.get("authority"), "没有清单时给 {}，不许编披露（Spec §C3）")

    def test_c3_old_document_without_the_block_is_not_dressed_up(self):
        body = self._body({"business_parts": [{"business_part_code": "JWXR21-P01"}], "stats": {}})
        self.assertEqual({}, body.get("authority"),
                         "老文档没有披露就给 {}，不许拿空清单冒充（Spec §C3）")


# --------------------------------------------------------------------------- #
# D 组：前端纯函数 packagingAuthorityDisclosureLines()（node 真跑）
# --------------------------------------------------------------------------- #
def _doc_for_frontend(authority):
    return {"business_parts": [], "authority": authority}


class DDisclosureLines(unittest.TestCase):
    def _lines(self, authority):
        got = value_of("packagingAuthorityDisclosureLines", _doc_for_frontend(authority))
        if not isinstance(got, list):
            self.fail("packagingAuthorityDisclosureLines() 必须返回数组（Spec §C4）")
        return [str(item) for item in got]

    def test_d1_order_based_ownership_is_worded_as_inference(self):
        from tech_app.backend.services import packaging_parts
        lines = self._lines(packaging_parts.authority_disclosure(authority_doc(sources=("order",))))
        joined = "\n".join(lines)
        self.assertIn("按顺序推定", joined, "按顺序归属必须说成推定（Spec §C4）")
        self.assertIn("不是按锚点行逐行核对", joined, "要点明没有逐行核对（Spec §C4）")
        self.assertIn("28", joined, "文案里要带件数（Spec §C4）")

    def test_d2_other_ownership_states(self):
        from tech_app.backend.services import packaging_parts
        anchor = "\n".join(self._lines(packaging_parts.authority_disclosure(
            authority_doc(sources=("anchor_row",)))))
        self.assertIn("按锚点行", anchor)
        mixed = "\n".join(self._lines(packaging_parts.authority_disclosure(
            authority_doc(sources=("order", "anchor_row")))))
        self.assertIn("来源不统一", mixed)
        self.assertIn("人工核对", mixed)
        none = "\n".join(self._lines(packaging_parts.authority_disclosure(
            authority_doc(with_images=False))))
        self.assertIn("没配到部件图", none)

    def test_d3_skipped_summary_names_the_reasons(self):
        from tech_app.backend.services import packaging_parts
        lines = self._lines(packaging_parts.authority_disclosure(authority_doc(skipped=REAL_SKIPPED)))
        joined = "\n".join(lines)
        self.assertIn("被跳过", joined, "跳过行必须在页面上说出来（Spec §C4）")
        self.assertIn("不是业务部件", joined, "要说清跳过的行不是部件（Spec §C4）")
        for reason in ("blank_row", "not_a_part_row", "no_sequence"):
            self.assertIn(reason, joined, "原因码 %s 要能看见（Spec §C4）" % reason)

    def test_d4_customer_note_is_shown_verbatim(self):
        from tech_app.backend.services import packaging_parts
        lines = self._lines(packaging_parts.authority_disclosure(authority_doc(skipped=REAL_SKIPPED)))
        hit = [line for line in lines if "第 32 行" in line]
        self.assertEqual(1, len(hit), "带文字的跳过行要逐条列出来（Spec §C4）")
        self.assertIn(REAL_SKIPPED[1]["text"], hit[0], "客户原文必须逐字出现（Spec §C4）")
        self.assertIn("请人工确认", hit[0], "要有人工确认的出口（Spec §C4）")
        self.assertTrue(any("第 33 行" in line for line in lines), "第 33 行也要列（Spec §C4）")
        self.assertFalse(any("第 3 行" in line for line in lines),
                         "没有文字的跳过行只进汇总，不逐条列（Spec §C4）")

    def test_d5_no_disclosure_means_no_lines(self):
        from tech_app.backend.services import packaging_parts
        for doc in ({}, {"authority": {}}, {"authority": None}):
            self.assertEqual([], value_of("packagingAuthorityDisclosureLines", doc),
                             "拿不到披露时不许编文案（Spec §C4）")
        empty = value_of("packagingAuthorityDisclosureLines",
                         _doc_for_frontend(packaging_parts.authority_disclosure({})))
        self.assertEqual([], empty)

    def test_d6_pure_function_does_not_touch_the_dom_or_the_network(self):
        body = function_body("packagingAuthorityDisclosureLines")
        self.assertTrue(body, "缺少纯函数 packagingAuthorityDisclosureLines()（Spec §C4）")
        for banned in ("document", "sessionStorage", "localStorage", "window.", "fetch("):
            self.assertNotIn(banned, body, "纯函数里不许出现 %s（Spec §C4）" % banned)


# --------------------------------------------------------------------------- #
# E 组：接线（左栏 + 右栏），且不许显示文件名
# --------------------------------------------------------------------------- #
class EWiring(unittest.TestCase):
    def _source(self):
        return APP_JS_PATH.read_text(encoding="utf-8", errors="replace")

    def test_e1_left_column_shows_the_disclosure(self):
        source = self._source()
        start = source.find("function renderPackagingBusinessTree")
        self.assertGreaterEqual(start, 0, "左栏渲染函数不见了")
        block = source[start:start + 3000]
        self.assertIn("packagingAuthorityDisclosureLines", block,
                      "左栏必须用 C4 的文案函数（Spec §C5）")
        # `## 494` §2.3 之后源码里不再出现那个词本身（拼接还原），所以这里钉住**构造式**：
        # 渲染出来的属性值一个字没变，现场 grep `qq-author` 照样找得到这个节点。
        self.assertIn('"data-qq-" + "author" + "ity-skip"', block,
                      "左栏要有一个可定位的披露节点（Spec §C5）")
        self.assertIn("textContent", block, "披露按纯文本渲染，不拼 HTML（Spec §C5）")

    def test_e2_panel_shows_the_two_facts(self):
        source = self._source()
        start = source.find("function openPackagingBusinessPart")
        self.assertGreaterEqual(start, 0)
        block = source[start:start + 3000]
        self.assertIn("部件图", block, "右栏要给「部件图」一行（Spec §C5）")
        self.assertIn("清单告警", block, "右栏要给「清单告警」一行（Spec §C5）")
        self.assertIn("packagingAuthorityDisclosureLines", block,
                      "告警文案必须来自 C4（Spec §C5）")

    def test_e3_file_name_is_not_rendered(self):
        source = self._source()
        self.assertNotIn("authority.file", source,
                         "读回路径没有文件名，页面也不许显示（Spec §C5 / §9）")


if __name__ == "__main__":                                              # pragma: no cover
    unittest.main(verbosity=2)
