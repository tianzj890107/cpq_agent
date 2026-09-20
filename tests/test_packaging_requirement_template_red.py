"""红测：包装需求模板与会话/看板联动 —— 包装第 2 批。

Spec：docs/specs/packaging-requirement-template.md
依赖：第 1 批（唯一行业注册表，四行业含 packaging）已完成。

现状缺口（实测，不是推断）：
  · industry_templates.PACKAGING_SPEC 只有 3.1 的 6 个占位字段（product_name 等），
    没有 3.1–3.6，`required_keys('packaging')` 只剩 {'product_name'}；
  · FILE_BLOCK_SECTION['packaging'] == '3.2'（应为 3.7）；
  · requirement-create.js 没有 RC_PACKAGING_SPECS，rcReplaceProductSpec() 里 packaging
    落到 rcManagedFlexibleSpec()（AI 生成字段历史路径），且不会把图纸块改名到 3.7；
  · requirement_service 没有 merge_field_sources()，没有任何 field_sources 结构；
  · da_repo._STRUCTURAL_DATA_KEYS 不含 'field_sources'；
  · 前端没有来源徽章（用户输入/附件/AI 抽取/AI 推荐/人工修改）。
  · packaging 空需求单的 gaps.keys 里只有 product_name，没有 inner_length 等包装必填项。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQ_CREATE_JS = ROOT / "tech_app" / "frontend" / "requirement-create.js"
NODE = shutil.which("node")

from tech_app.backend.models.workflow import RequirementDoc  # noqa: E402
from tech_app.backend.services import industry_templates as it  # noqa: E402
from tech_app.backend.services import requirement_service as rs  # noqa: E402
from tech_app.backend.storage import da_repo  # noqa: E402

# ---- 4.6 精确字段清单（Spec 附录，红测与前端对齐的唯一口径） ----------------- #
PACKAGING_BLOCKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "3.1": ("产品与订单", (
        "packaging_product_name", "packaging_category", "quote_quantity", "moq",
        "sample_quantity", "mass_quantity", "first_trial", "delivery_due",
        "destination", "currency", "tax_rate")),
    "3.2": ("成品尺寸与盒型", (
        "inner_length", "inner_width", "inner_height", "box_type", "box_family",
        "fit_clearance", "closure_type", "v_groove", "magnetic", "collapsible",
        "open_close_life")),
    "3.3": ("材料", (
        "grey_board", "grey_board_thickness", "face_paper", "face_paper_gsm",
        "lining_paper", "insert_type", "glue", "magnet", "ribbon", "accessories",
        "eco_requirement")),
    "3.4": ("印刷与表面工艺", (
        "print_colors", "spot_colors", "lamination", "hot_stamping", "uv_coating",
        "emboss_deboss", "silk_screen", "die_cutting", "mounting", "special_process",
        "process_area")),
    "3.5": ("包装与物流", (
        "units_per_carton", "carton_size", "flat_card", "poly_bag", "corner_guard",
        "pallet", "units_per_pallet", "shipping_mode", "min_freight", "loading_rate")),
    "3.6": ("商务与价格", (
        "need_cost_estimate", "loss_rate", "proofing_base", "tooling_cost",
        "tooling_amortize_qty", "target_gross_margin", "tech_premium",
        "market_adjustment", "other_markup", "discount")),
}
PACKAGING_KEYS = tuple(key for _, (_, keys) in PACKAGING_BLOCKS.items() for key in keys)
PACKAGING_REQUIRED = {
    "packaging_product_name", "packaging_category", "quote_quantity",
    "inner_length", "inner_width", "inner_height", "box_type", "closure_type",
    "v_groove", "face_paper_gsm",
}
SOURCES = ("user_text", "attachment", "ai_extract", "ai_recommend", "manual")
SOURCE_LABELS = {"user_text": "用户输入", "attachment": "附件", "ai_extract": "AI 抽取",
                 "ai_recommend": "AI 推荐", "manual": "人工修改"}
CJK = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------------------------------------------------- #
# 最小 JS 提取 / 执行脚手架（在 Node 里跑真实源码片段，不做纯正则断言）
# --------------------------------------------------------------------------- #
def _js_source() -> str:
    return REQ_CREATE_JS.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _balanced_end(src: str, open_index: int) -> int:
    """从 src[open_index] == '{' 起找匹配的 '}'，跳过字符串与注释。"""
    depth = 0
    i, n, quote = open_index, len(src), None
    while i < n:
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i)
            i = n if j < 0 else j + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _js_object_literal(marker: str) -> str | None:
    """提取 `marker{...}` 里的对象字面量（marker 以 '=' 结尾时也要能命中）。"""
    src = _js_source()
    for cue in (marker + "={", marker + " = {", marker + "{"):
        idx = src.find(cue)
        if idx < 0:
            continue
        open_index = idx + len(cue) - 1
        end = _balanced_end(src, open_index)
        if end > 0:
            return src[open_index:end + 1]
    return None


def _js_function(signature: str) -> str | None:
    """提取 `signature`（以 '{' 结尾）到匹配 '}' 的函数源码。"""
    src = _js_source()
    idx = src.find(signature)
    if idx < 0:
        return None
    open_index = idx + len(signature) - 1
    end = _balanced_end(src, open_index)
    if end < 0:
        return None
    return src[idx:end + 1]


class _NodeHarness(unittest.TestCase):
    def run_node(self, script: str):
        self.assertIsNotNone(NODE, "本机缺少 node，无法执行前端行为红测")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "harness.mjs"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run([NODE, str(path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"node harness 失败：{proc.stderr[-3000:]}")
        last = [line for line in proc.stdout.strip().splitlines() if line.strip()][-1]
        return json.loads(last)


# --------------------------------------------------------------------------- #
# A. 后端模板（唯一事实源）
# --------------------------------------------------------------------------- #
class ABackendPackagingTemplateTest(unittest.TestCase):
    def test_a1_six_blocks_3_1_to_3_6(self):
        blocks = it.blocks("packaging")
        self.assertEqual([b.section for b in blocks], list(PACKAGING_BLOCKS),
                         "包装模板必须是 3.1–3.6 六段，而不是占位的单段")
        for block in blocks:
            title, _ = PACKAGING_BLOCKS[block.section]
            self.assertEqual(block.title, title, f"{block.section} 标题不一致")

    def test_a2_exact_field_key_set(self):
        self.assertEqual(list(it.field_keys("packaging")), list(PACKAGING_KEYS),
                         "field_keys('packaging') 必须恰好等于 Spec 4.6 的 64 个键（含顺序）")

    def test_a3_exact_required_key_set(self):
        self.assertEqual(it.required_keys("packaging"), PACKAGING_REQUIRED,
                         "required_keys('packaging') 必须恰好等于 Spec 的 10 个必填键")

    def test_a4_required_is_subset_of_fields(self):
        self.assertLessEqual(PACKAGING_REQUIRED, set(it.field_keys("packaging")))

    def test_a5_file_block_section_is_3_7(self):
        self.assertEqual(it.FILE_BLOCK_SECTION.get("packaging"), "3.7",
                         "包装的「图纸与技术资料」块必须排在 3.7")

    def test_a6_label_is_packaging(self):
        self.assertEqual(it.label("packaging"), "包装")

    def test_a7_section_checks_cover_blocks_and_required(self):
        checks = it.section_checks("packaging")
        self.assertTrue(checks, "section_checks('packaging') 不能为空")
        texts = " ".join(str(part) for row in checks for part in row)
        for key in PACKAGING_REQUIRED:
            self.assertIn(key, texts, f"section_checks 必须覆盖必填项 {key}")
        for section, (title, _) in PACKAGING_BLOCKS.items():
            self.assertIn(section, texts, f"section_checks 必须包含 {section}")

    def test_a8_no_semiconductor_keys_leaked(self):
        leaked = {"wafer_size", "chuck_type", "ceramic_material"} & set(it.field_keys("packaging"))
        self.assertFalse(leaked, f"包装模板混入了半导体字段：{sorted(leaked)}")

    def test_a9_labels_are_chinese(self):
        labels = it.labels("packaging")
        self.assertEqual(set(labels), set(PACKAGING_KEYS))
        for key, label in labels.items():
            self.assertTrue(CJK.search(label or ""), f"{key} 缺少中文标签")


# --------------------------------------------------------------------------- #
# B. 前端模板与渲染合同
# --------------------------------------------------------------------------- #
class BFrontendPackagingTemplateTest(_NodeHarness):
    def _keys_by_section(self) -> dict:
        literal = _js_object_literal("const RC_PACKAGING_SPECS")
        self.assertIsNotNone(
            literal, "requirement-create.js 缺少 `const RC_PACKAGING_SPECS={...}` 声明")
        script = (
            f"const RC_PACKAGING_SPECS = {literal};\n"
            "const out = {};\n"
            "for (const [section, block] of Object.entries(RC_PACKAGING_SPECS)) {\n"
            "  out[section] = block.fields.map((f) => f[1]);\n"
            "}\n"
            "console.log(JSON.stringify(out));\n"
        )
        return self.run_node(script)

    def test_b1_packaging_specs_exist(self):
        self.assertTrue(self._keys_by_section(), "RC_PACKAGING_SPECS 必须存在且非空")

    def test_b2_frontend_sections_match_spec(self):
        out = self._keys_by_section()
        self.assertEqual(list(out.keys()), list(PACKAGING_BLOCKS))
        for section, block in out.items():
            self.assertEqual(block, list(PACKAGING_BLOCKS[section][1]),
                             f"前端 {section} 字段与后端不一致（禁止漂移）")

    def test_b3_frontend_keys_equal_backend_exactly(self):
        out = self._keys_by_section()
        flat = [key for keys in out.values() for key in keys]
        self.assertEqual(flat, list(it.field_keys("packaging")))

    def _render_packaging_section(self) -> dict:
        """在最小 DOM harness 里执行真实的 rcReplaceProductSpec()（industry=packaging）。"""
        fn = _js_function("rcReplaceProductSpec=function(){")
        if fn is None:
            fn = _js_function("rcReplaceProductSpec=function (){")
        self.assertIsNotNone(fn, "找不到生效的 rcReplaceProductSpec 赋值实现")
        literal = _js_object_literal("const RC_PACKAGING_SPECS")
        self.assertIsNotNone(literal, "缺少 RC_PACKAGING_SPECS 声明")
        script = (
            "let rcReplaceProductSpec;\n"
            f"const RC_PACKAGING_SPECS = {literal};\n"
            "const RC_SEMI_SPECS = {}, RC_BATTERY_SPECS = {}, RC_APPLIANCE_SPECS = {};\n"
            "const calls = { template: null, flexible: 0 };\n"
            "function rcStaticSpec(t) { calls.template = t; return 'STATIC'; }\n"
            "function rcManagedFlexibleSpec() { calls.flexible += 1; return 'FLEX'; }\n"
            "function rcIndustry() { return 'packaging'; }\n"
            "function rcIndustryBanner() { return 'BANNER'; }\n"
            "function rcBind() {} function rcBindIndustrySpec() {} function rcBindTemplateManager() {}\n"
            "const fileBlockHtml = '<div class=\"spec-block\"><h4 class=\"form-subtitle\">3.4 图纸与技术资料</h4></div>';\n"
            "const fileBlockNode = { outerHTML: fileBlockHtml,\n"
            "  querySelector() { return { textContent: '3.4 图纸与技术资料' }; } };\n"
            "let html = '';\n"
            "const sectionNode = { set innerHTML(v) { html = v; }, get innerHTML() { return html; },\n"
            "  querySelectorAll() { return [fileBlockNode]; }, insertAdjacentHTML() {} };\n"
            "const document = { querySelector(sel) { return sel === '#sectionC' ? sectionNode : null; },\n"
            "  querySelectorAll() { return []; } };\n"
            f"{fn};\n"
            "rcReplaceProductSpec();\n"
            "console.log(JSON.stringify({ flexible: calls.flexible, static: calls.template === RC_PACKAGING_SPECS,\n"
            "  html: html }));\n"
        )
        return self.run_node(script)

    def test_b4_replace_product_spec_uses_static_packaging_spec(self):
        out = self._render_packaging_section()
        self.assertEqual(out["flexible"], 0,
                         "packaging 不得落到 rcManagedFlexibleSpec()（AI 生成字段历史路径）")
        self.assertTrue(out["static"], "packaging 必须走 rcStaticSpec(RC_PACKAGING_SPECS)")

    def test_b5_file_block_renamed_to_3_7(self):
        out = self._render_packaging_section()
        self.assertIn("3.7 图纸与技术资料", out["html"],
                      "packaging 渲染后图纸与技术资料块必须编号为 3.7")
        self.assertNotIn("3.4 图纸与技术资料", out["html"],
                         "packaging 不得保留 3.4 的图纸块编号")

    def test_b6_banner_mentions_packaging_semantics(self):
        src = _js_source()
        self.assertIn("packaging", src)
        banner_fn = _js_function("function rcIndustryBanner(){")
        self.assertIsNotNone(banner_fn, "找不到 rcIndustryBanner")
        self.assertTrue(CJK.search(banner_fn), "行业横幅必须是可读中文")
        self.assertTrue(any(cue in banner_fn for cue in ("盒型", "包装字段", "包装规格", "包装（盒型")),
                        "行业横幅必须为 packaging 单独给出说明（如「已加载包装（盒型/材料/印刷/物流）规格字段」），"
                        "不能沿用 flexible 的历史草稿文案")

    def test_b7_no_second_field_renderer_added(self):
        src = _js_source()
        for required in ("function rcField(", "function rcSpecField(", "function rcStaticSpec("):
            self.assertIn(required, src, f"既有渲染链被破坏：缺少 {required}")


# --------------------------------------------------------------------------- #
# C. 字段来源
# --------------------------------------------------------------------------- #
class CFieldSourcesTest(unittest.TestCase):
    def test_c1_merge_helper_exists(self):
        self.assertTrue(callable(getattr(rs, "merge_field_sources", None)),
                        "requirement_service 必须提供 merge_field_sources(existing, incoming)")

    def _merge(self, existing, incoming):
        fn = getattr(rs, "merge_field_sources", None)
        if not callable(fn):
            self.fail("merge_field_sources 尚未实现")
        return fn(existing, incoming)

    def test_c2_manual_is_never_overwritten(self):
        merged = self._merge({"inner_height": "manual"}, {"inner_height": "ai_extract"})
        self.assertEqual(merged["inner_height"], "manual")

    def test_c3_user_text_not_downgraded_by_ai(self):
        merged = self._merge({"box_type": "user_text"}, {"box_type": "ai_extract"})
        self.assertEqual(merged["box_type"], "user_text")
        merged = self._merge({"box_type": "attachment"}, {"box_type": "ai_recommend"})
        self.assertEqual(merged["box_type"], "attachment")

    def test_c4_new_keys_written_with_incoming_source(self):
        merged = self._merge({}, {"v_groove": "ai_extract", "inner_length": "user_text"})
        self.assertEqual(merged.get("v_groove"), "ai_extract")
        self.assertEqual(merged.get("inner_length"), "user_text")

    def test_c5_unknown_sources_dropped(self):
        merged = self._merge({}, {"box_type": "made_up_source", "inner_width": "llm_guess"})
        self.assertNotIn("box_type", merged)
        self.assertNotIn("inner_width", merged)

    def test_c6_manual_may_replace_ai(self):
        merged = self._merge({"inner_height": "ai_extract"}, {"inner_height": "manual"})
        self.assertEqual(merged["inner_height"], "manual")

    def test_c7_returns_plain_dict_of_known_sources(self):
        merged = self._merge({"a": "manual"}, {"b": "attachment"})
        self.assertIsInstance(merged, dict)
        for value in merged.values():
            self.assertIn(value, SOURCES)

    def test_c8_structural_data_key_registered(self):
        self.assertIn("field_sources", da_repo._STRUCTURAL_DATA_KEYS,
                      "field_sources 必须列为结构性键，否则会被拆成字段行写库")


# --------------------------------------------------------------------------- #
# D. 完整性门禁（真跑 requirement_precheck）
# --------------------------------------------------------------------------- #
def _packaging_doc(data: dict) -> RequirementDoc:
    return RequirementDoc(requirement_no="", project_id="red-probe-packaging",
                          title="包装需求", data={"industry": "packaging", **data})


def _precheck(data: dict) -> dict:
    return rs.requirement_precheck("red-probe-packaging", _packaging_doc(data))


class DCompletenessGateTest(unittest.TestCase):
    def test_d1_empty_packaging_reports_packaging_gaps(self):
        result = _precheck({})
        self.assertFalse(result["ok"])
        missing = set(result["gaps"]["keys"])
        for key in ("inner_length", "inner_width", "inner_height", "box_type",
                    "closure_type", "v_groove", "face_paper_gsm", "quote_quantity"):
            self.assertIn(key, missing, f"空包装需求必须报缺 {key}")

    def test_d2_gaps_use_human_labels_not_raw_keys(self):
        result = _precheck({})
        pairs = dict(zip(result["gaps"]["keys"], result["gaps"]["labels"]))
        for key in ("inner_length", "inner_width", "inner_height", "face_paper_gsm"):
            self.assertIn(key, pairs)
            label = pairs[key]
            self.assertTrue(label and label != key, f"{key} 的缺口标签必须是中文而不是原始 key")
            self.assertTrue(CJK.search(label), f"{key} 的缺口标签缺少中文")

    def test_d3_not_falling_back_to_semiconductor(self):
        missing = set(_precheck({})["gaps"]["keys"])
        self.assertFalse({"wafer_size", "chuck_type"} & missing,
                         "包装需求单不得按半导体模板报缺口")

    def test_d4_section_items_need_info_until_filled(self):
        result = _precheck({})
        sections = {row["item"]: row for row in result["items"]}
        row = sections.get("3.2 成品尺寸与盒型")
        self.assertIsNotNone(row, "确认页必须有「3.2 成品尺寸与盒型」检查项")
        self.assertEqual(row["status"], "need_info")
        self.assertIn("inner_height", row.get("missing") or [])

    def test_d5_filling_all_required_makes_sections_ok(self):
        filled = {key: "已填" for key in PACKAGING_REQUIRED}
        result = _precheck(filled)
        sections = {row["item"]: row for row in result["items"]}
        for section, (title, _) in PACKAGING_BLOCKS.items():
            key = f"{section} {title}"
            self.assertIn(key, sections, f"确认页缺少检查项 {key}")
            self.assertEqual(sections[key]["status"], "ok",
                             f"{key} 在必填项齐全后应为 ok")
        self.assertFalse(set(result["gaps"]["keys"]) & PACKAGING_REQUIRED,
                         "必填项齐全后不应再报包装字段缺口")

    def test_d6_partial_fill_still_reports_missing_height(self):
        filled = {key: "已填" for key in PACKAGING_REQUIRED if key != "inner_height"}
        sections = {row["item"]: row for row in _precheck(filled)["items"]}
        self.assertIn("3.2 成品尺寸与盒型", sections,
                      "确认页必须有「3.2 成品尺寸与盒型」检查项")
        row = sections["3.2 成品尺寸与盒型"]
        self.assertEqual(row["status"], "need_info")
        self.assertIn("inner_height", row.get("missing") or [])
        self.assertFalse(_precheck(filled)["ok"], "缺必填项时不能判定需求完整")


# --------------------------------------------------------------------------- #
# E. 非回归
# --------------------------------------------------------------------------- #
class ENonRegressionTest(unittest.TestCase):
    def test_e1_existing_industry_templates_unchanged(self):
        self.assertIn("wafer_size", it.field_keys("semiconductor"))
        self.assertIn("battery_model", it.field_keys("battery"))
        self.assertIn("appliance_category", it.field_keys("appliance"))
        for industry in ("semiconductor", "battery", "appliance"):
            self.assertNotIn("inner_length", it.field_keys(industry),
                             f"{industry} 不得混入包装字段")

    def test_e2_flexible_still_normalizes_to_default(self):
        self.assertEqual(it.normalize("flexible"), it.DEFAULT_INDUSTRY)
        self.assertEqual(it.normalize("packaging"), "packaging")

    def test_e3_precheck_contract_keys_preserved(self):
        result = _precheck({})
        self.assertEqual(set(result), {"items", "ok", "generated_note", "engine", "gaps"})
        self.assertEqual(result["engine"], "deterministic_rules")
        self.assertTrue(all("status" in row and "item" in row for row in result["items"]))

    def test_e4_source_labels_cover_closed_enum(self):
        literal = _js_object_literal("const RC_FIELD_SOURCE_LABELS")
        self.assertIsNotNone(literal, "requirement-create.js 必须声明 RC_FIELD_SOURCE_LABELS")
        script = f"const L = {literal}; console.log(JSON.stringify(L));\n"
        labels = self._run(script)
        self.assertEqual(set(labels), set(SOURCES))
        for source, label in SOURCE_LABELS.items():
            self.assertEqual(labels[source], label)

    def _run(self, script):
        self.assertIsNotNone(NODE)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "h.mjs"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run([NODE, str(path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_e5_ai_badge_renders_manual_source(self):
        fn = _js_function("function rcAiBadge(name){")
        self.assertIsNotNone(fn, "找不到 rcAiBadge")
        script = (
            "function esc(s){return String(s);}\n"
            "function rcHistoryIteration(){return null;}\n"
            "function rcAiFilledSet(){return new Set();}\n"
            "function rcAiRecommendedSet(){return new Set();}\n"
            "function rcAiConfidence(){return null;}\n"
            "function rcData(){return {field_sources:{inner_height:'manual'}};}\n"
            f"{fn}\n"
            "console.log(JSON.stringify({manual: rcAiBadge('inner_height'),\n"
            "  plain: rcAiBadge('inner_length')}));\n"
        )
        out = self._run(script)
        self.assertIn("人工修改", out["manual"],
                      "field_sources 标为 manual 的字段必须渲染「人工修改」来源徽章")
        self.assertEqual(out["plain"], "",
                         "无来源标记的字段不得凭空新增徽章（避免回归既有行为）")


if __name__ == "__main__":
    unittest.main()
