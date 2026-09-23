"""红测：业务的表只能对答案，不能当输入 —— 2.1 的结果与下游门槛里没有客户工作簿与模板表。

Spec：`docs/specs/packaging-business-tables-are-answer-keys-only.md`

用户原话（2026-09-23）：

> 那个业务的表不能作为输入
> 只能是我们用来对答案

现状缺口（HEAD `3902b4a` 工作副本只读实测）：
  · `main.py:7477` 客户工作簿导入端点体内就是 `packaging_parts.save_business_parts(pid, …)` ——
    《酒盒 报价资料.xlsx》当下**能**变成 2.1 的结果文档；
  · `app.js:2580` 空态引导句 `导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本`、
    `app.js:2638` 接口字面量 `…/packaging-business-parts/import`；
  · `packaging_bom.build_bom()` 里 `expand_parts(`（偏移 792）在 `_load_business_parts(project_id)`
    （偏移 1079）**之前**，而 `expand_parts()` 读到模板表为空就 409 `no_part_template`；
  · `packaging_route.build_route()` 的 `no_process_template` 先判死，全程不读零件 / 业务部件文档。

纪律：只读源码 + 导入模块看常量；`unittest.mock` 不参与、不起服务、不发 HTTP、不连 PG / 34、
不写业务数据、不跑真样本。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import inspect
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

REFERENCE_DOC_KEY = "packaging_business_parts_reference"
LEGACY_ROW_HINT = "导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本"
LEGACY_IMPORT_PATH = "/packaging-business-parts/import"
ANSWER_KEY_PHRASE = "只用来对答案"

services = importlib.import_module("tech_app.backend.services")
bom = importlib.import_module("tech_app.backend.services.packaging_bom")
route = importlib.import_module("tech_app.backend.services.packaging_route")
parts = importlib.import_module("tech_app.backend.services.packaging_parts")
resolver = importlib.import_module("tech_app.backend.services.packaging_business_part_resolver")


def read_text(path) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8").replace("\x00", "")


def python_body(source: str, signature: str) -> str:
    """从模块源码里切出某个函数体（到下一个顶格 `def` / `@` 为止）。"""
    at = source.find(signature)
    if at < 0:
        return ""
    rest = source[at:]
    end = len(rest)
    for marker in ("\ndef ", "\n@app.", "\nclass "):
        found = rest.find(marker, len(signature))
        if found >= 0:
            end = min(end, found)
    return rest[:end]


class G1ResultComesFromTheDrawing(unittest.TestCase):
    def test_g1_1_import_endpoint_does_not_write_the_result_document(self):
        body = python_body(read_text(MAIN_PY), "def import_packaging_business_parts(")
        self.assertTrue(body, "main.py 缺少导入端点")
        self.assertNotIn("packaging_parts.save_business_parts(", body,
                         "客户工作簿不许写业务部件结果文档（Spec §2.2）")

    def test_g1_2_reference_document_key_exists(self):
        self.assertEqual(REFERENCE_DOC_KEY, getattr(parts, "BUSINESS_REFERENCE_DOC_KEY", ""),
                         "参照文档要有自己的 doc key（Spec §2.2）")

    def test_g1_3_reference_save_and_load_are_available(self):
        for name in ("business_parts_reference_document", "save_business_parts_reference",
                     "load_business_parts_reference"):
            self.assertTrue(callable(getattr(parts, name, None)),
                            "packaging_parts 缺少 %s()（Spec §2.2）" % name)

    def test_g1_4_import_endpoint_speaks_the_reference_document(self):
        body = python_body(read_text(MAIN_PY), "def import_packaging_business_parts(")
        self.assertTrue(body, "main.py 缺少导入端点")
        self.assertIn("reference", body,
                      "导入端点的产物必须是参照那一份（Spec §2.2）")

    def test_g1_5_reference_key_is_not_the_result_document(self):
        key = getattr(parts, "BUSINESS_REFERENCE_DOC_KEY", "")
        result_key = getattr(parts, "BUSINESS_DOC_KEY", "")
        self.assertTrue(key, "参照文档要有 doc key（Spec §2.2）")
        self.assertNotEqual(result_key, key,
                            "参照文档不许与结果文档共用 key（Spec §2.2）")


class G2AnswerKeysAreNotDownstreamGateways(unittest.TestCase):
    def test_g2_1_bom_reads_the_parts_document_before_the_template_table(self):
        body = python_body(inspect.getsource(bom.build_bom), "def build_bom(")
        self.assertTrue(body, "packaging_bom 缺少 build_bom()")
        at_parts_doc = body.find("_load_business_parts(project_id)")
        at_expand = body.find("expand_parts(")
        self.assertGreaterEqual(at_parts_doc, 0, "BOM 要先读业务部件文档（Spec §2.3）")
        self.assertGreaterEqual(at_expand, 0, "BOM 仍要展开模板（Spec §2.3）")
        self.assertLess(at_parts_doc, at_expand,
                        "零件 / 业务部件文档必须先于模板表被读（Spec §2.3）")

    def test_g2_2_template_gap_is_a_disclosure_not_a_verdict(self):
        self.assertIn("part_templates_unavailable", read_text(bom.__file__),
                      "模板表缺位要变成披露码（Spec §2.3）")

    def test_g2_3_route_reads_the_parts_document_before_the_verdict(self):
        body = python_body(inspect.getsource(route.build_route), "def build_route(")
        self.assertTrue(body, "packaging_route 缺少 build_route()")
        reads = min([index for index in (body.find("load_business_parts"),
                                         body.find("packaging_parts"))
                     if index >= 0] or [-1])
        at_verdict = body.find("no_process_template")
        self.assertGreaterEqual(at_verdict, 0, "工艺模板缺位仍要留痕（Spec §2.3）")
        self.assertGreaterEqual(reads, 0, "工艺要先读零件 / 业务部件文档（Spec §2.3）")
        self.assertLess(reads, at_verdict,
                        "判死前必须先读零件 / 业务部件文档（Spec §2.3）")

    def test_g2_4_process_template_gap_is_a_disclosure_not_a_verdict(self):
        self.assertIn("process_templates_unavailable", read_text(route.__file__),
                      "工艺模板缺位要变成披露码（Spec §2.3）")

    def test_g2_5_legacy_codes_are_kept(self):
        self.assertIn("no_part_template", read_text(bom.__file__),
                      "旧读数 no_part_template 不许消失（Spec §2.3，护栏）")
        self.assertIn("no_process_template", read_text(route.__file__),
                      "旧读数 no_process_template 不许消失（Spec §2.3，护栏）")


class G3FrontendEntryDeclaresItsPurpose(unittest.TestCase):
    def test_g3_1_empty_state_no_longer_invites_the_workbook_as_input(self):
        src = read_text(APP_JS)
        self.assertNotIn(LEGACY_ROW_HINT, src,
                         "空态引导句不许再请人把业务表当输入（Spec §2.4）")

    def test_g3_2_frontend_drops_the_import_literal(self):
        self.assertNotIn(LEGACY_IMPORT_PATH, read_text(APP_JS),
                         "前端不许再打那条写结果文档的接口（Spec §2.4）")

    def test_g3_3_workbook_entry_says_it_is_an_answer_key(self):
        self.assertIn("对答案", read_text(APP_JS),
                      "工作簿入口要自报是对答案参照（Spec §2.4）")

    def test_g3_4_frontend_calls_the_reference_path(self):
        self.assertIn("packaging-business-parts/reference", read_text(APP_JS),
                      "前端要指向参照那一份接口（Spec §2.4）")


class G4Guards(unittest.TestCase):
    def test_g4_1_runtime_refused_sources_are_unchanged(self):
        self.assertEqual(("attachment", "knowledge_base", "drawing_hash", "manual_import"),
                         tuple(resolver.RUNTIME_REFUSED_SOURCES),
                         "解析层的拒绝来源闭集不许动（Spec §2.5）")

    def test_g4_2_authority_sources_are_drawing_or_missing(self):
        self.assertEqual(("dwg", "missing"), tuple(resolver.AUTHORITY_SOURCES),
                         "权威清单来源闭集不许动（Spec §2.5）")

    def test_g4_3_default_seed_path_is_still_empty(self):
        self.assertEqual("", resolver.DEFAULT_SEED_PATH,
                         "运行时默认读不到快照这条不许动（Spec §2.5）")

    def test_g4_4_bom_still_reads_only_the_result_document(self):
        body = python_body(inspect.getsource(bom._load_business_parts),
                           "def _load_business_parts(")
        self.assertIn("load_business_parts(", body, "BOM 仍只读结果文档（Spec §2.5）")
        self.assertNotIn("reference", body, "BOM 不许读参照文档（Spec §2.2）")


class G5Disclosure(unittest.TestCase):
    def test_g5_1_backend_declares_the_reference_path(self):
        self.assertIn("PACKAGING_BUSINESS_PARTS_REFERENCE_PATH", read_text(MAIN_PY),
                      "后端要有参照接口的唯一路径常量（Spec §2.2）")

    def test_g5_2_reference_document_says_why_it_exists(self):
        haystack = read_text(MAIN_PY) + read_text(APP_JS) + read_text(pathlib.Path(parts.__file__))
        self.assertIn(ANSWER_KEY_PHRASE, haystack,
                      "参照文档要说清只用来对答案（Spec §2.2）")

    def test_g5_3_cost_still_has_one_entry_to_the_result_document(self):
        cost = read_text(ROOT / "tech_app" / "backend" / "services" / "packaging_cost.py")
        self.assertIn("load_business_parts(", cost,
                      "成本仍只从结果文档读业务部件（Spec §2.5，护栏）")
        self.assertNotIn("load_business_parts_reference(", cost,
                         "成本不许读参照文档（Spec §2.2）")


if __name__ == "__main__":
    unittest.main()
