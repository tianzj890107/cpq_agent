"""红测：DWG 图纸 → 零件（展开件）提取与下游回填。

Spec：`docs/specs/packaging-dwg-parts-extraction.md`
夹具：`tests/fixtures/cad_ir/parts_panels.json`（同构于真实 `酒盒.dwg` 的 CAD IR 统计，
      由 `tests/fixtures/cad_ir/build_fixtures.py` 生成）
真实样本：`裕同包装项目-待开发/酒盒.dwg`（不在仓库里；不在本机时该组 skip）

**现状缺口（34 实测，不是推断）**：

  · `tech_app/backend/services/packaging_parts.py` 不存在 —— 全仓没有任何"从 CAD IR 连通分量
    提零件"的代码；
  · 34 上 `酒盒.dwg` 跑完 2.1 之后 `ir.parts = 0`、2.1 左栏零件树写「完成解析后显示零件清单」；
  · 图纸解析链路 `STEP_IDS` 只有 7 步，没有"零件提取"这一步；
  · BOM 的零件行全部来自盒型模板（`source: kb_packaging_part_template`），
    知识库里 `YT-DWG-WINE-700ML` 的 11 行 `size_expr` 全为空 → 展开尺寸无处可来
    → 行 `needs_input`、成本 `part_size_missing`、`material_total = 0.0`；
  · 真图 `closed_outline_total = 2 / open_outline_total = 5598 / components = 402`：
    「零件 = 闭合轮廓」在真图上不成立，必须按连通分量聚合。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import (da_db, kb_repo, meta_backend, store)  # noqa: E402
from tech_app.backend.services import packaging_bom, packaging_semantics  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "cad_ir"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
FLOW_MODEL_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "model.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
PKG = "tech_app.backend.services.packaging_parts"

ENGINE_VERSION = "packaging-parts/1"
PART_CODE_FORMAT = "DWG-P%02d"
REASON_CODES = ("edge_over_max", "area_over_max", "area_under_min", "no_curve_entity",
                "no_components", "all_filtered", "no_unit")
DEFAULT_OPTIONS = {"min_area_mm2": 2000, "max_edge_mm": 1200, "max_area_mm2": 1000000,
                   "max_parts": 64}
ROLE_PRIORITY = ("cut", "half_cut", "crease", "v_groove", "glue_flap", "print", "bleed",
                 "frame", "hole", "unknown")

#: 夹具里过筛的 4 件（面积降序 = 零件号顺序），数值由 `parts_panels()` 的坐标算出。
EXPECTED_PARTS = [
    {"part_code": "DWG-P01", "component_id": "cmp:B", "length": 231.2, "width": 196.0,
     "area": 231.2 * 196.0, "role": "cut"},
    {"part_code": "DWG-P02", "component_id": "cmp:L", "length": 231.2, "width": 100.8,
     "area": 231.2 * 100.8, "role": "cut"},
    {"part_code": "DWG-P03", "component_id": "cmp:W", "length": 231.2, "width": 45.0,
     "area": 231.2 * 45.0, "role": "cut"},
    {"part_code": "DWG-P04", "component_id": "cmp:I", "length": 60.0, "width": 50.0,
     "area": 60.0 * 50.0, "role": "unknown"},
]

EXPECTED_FILTERED = {"cmp:F": {"edge_over_max", "area_over_max"},
                     "cmp:T": {"area_under_min"},
                     "cmp:X": {"no_curve_entity"}}

# --------------------------------------------------------------------------- #
# 知识库夹具：**照 34 生产库里 2026-09-21 的行**（只搬测试要用的列）
# --------------------------------------------------------------------------- #
BOX_RB01001 = {"box_type_code": "YT-RB-01001-A", "name": "天地盖盒（全盖）", "family": "01天地盖",
               "size_l_min": 80.0, "size_l_max": 400.0, "size_w_min": 80.0, "size_w_max": 300.0,
               "size_h_min": 25.0, "size_h_max": 120.0, "fit_clearance": 1.8,
               "grey_board_thickness": "2.0（1.5/2.5可选）", "face_paper_gsm": "157-250",
               "closure_type": "天地盖", "part_count": 10, "v_groove": "是",
               "business_status": "标准", "source_type": "demo", "industry": "packaging",
               "status": "active"}

PART_TEMPLATES_RB01001 = [
    ("RB01001-P01", "盖面", "上盖", "灰板 2.0mm", 1, "L+4t+2c  ×  W+4t+2c",
     "L+4t+2c", "W+4t+2c", ""),
    ("RB01001-P02", "盖壁（长边）", "上盖", "灰板 2.0mm", 2, "L+4t+2c  ×  H盖",
     "L+4t+2c", "H盖", ""),
    ("RB01001-P03", "盖壁（短边）", "上盖", "灰板 2.0mm", 2, "W+4t+2c  ×  H盖",
     "W+4t+2c", "H盖", ""),
    ("RB01001-P04", "底面", "下底", "灰板 2.0mm", 1, "L  ×  W", "L", "W", ""),
    ("RB01001-P05", "底壁（长边）", "下底", "灰板 2.0mm", 2, "L+2t  ×  H", "L+2t", "H", ""),
    ("RB01001-P06", "底壁（短边）", "下底", "灰板 2.0mm", 2, "W  ×  H", "W", "H", ""),
    ("RB01001-P07", "盖面纸", "面纸", "特种纸 200g", 1, "盖展开尺寸 + 包边 + 出血3mm",
     "盖展开尺寸 + 包边 + 出血3mm", "", ""),
    ("RB01001-P08", "盒身面纸", "面纸", "特种纸 200g", 1, "盒身展开尺寸 + 包边 + 出血3mm",
     "盒身展开尺寸 + 包边 + 出血3mm", "", ""),
    ("RB01001-P09", "内托", "内托", "EVA 植绒 5mm", 1, "（L-4）×（W-4）× 8",
     "（L-4）", "（W-4）", "8"),
    ("RB01001-P10", "丝带拉手", "配件", "涤纶丝带 10mm", 1, "长度 = W/3 + 40",
     "长度 = W/3 + 40", "", ""),
]

BOX_WINE = {"box_type_code": "YT-DWG-WINE-700ML", "name": "700ML双开门酒盒（DWG实样）",
            "family": "书型盒/双开门礼盒", "size_l_min": 219.0, "size_l_max": 220.5,
            "size_w_min": 86.0, "size_w_max": 90.0, "size_h_min": 86.0, "size_h_max": 90.0,
            "fit_clearance": None, "grey_board_thickness": "1.8/2.0/2.5；顶托对裱22",
            "face_paper_gsm": "225/235/325/350", "closure_type": "双开门/对开",
            "part_count": 18, "v_groove": "是", "business_status": "权威实样",
            "source_type": "dwg_confirmed", "industry": "packaging", "status": "active"}

#: DWG 实样盒型的 11 行：名字 / 材料 / 数量都有，**尺寸表达式全空**（生产库现状）。
PART_TEMPLATES_WINE = [
    ("WINE-P01", "左盖面纸", "面纸", "225G铜版底PET光银", 1),
    ("WINE-P02", "右盖面纸", "面纸", "225G铜版底PET光银", 1),
    ("WINE-P03", "左右盒背灰板", "盒背", "2.5mm灰板", 1),
    ("WINE-P04", "外盒里层灰板", "盒体", "1.8mm灰板裱光银纸", 1),
    ("WINE-P05", "外盒外层衬板", "衬板", "350g粉灰", 1),
    ("WINE-P06", "内盒1", "内盒", "2mm灰板+225G铜版底PET光银", 1),
    ("WINE-P07", "内盒2", "内盒", "2mm灰板+225G铜版底PET光银", 1),
    ("WINE-P08", "内盒3", "内盒", "2mm灰板+225G铜版底PET光银", 1),
    ("WINE-P09", "贴牌", "标牌", "325G PET光银+3层300G白卡+225G PET光银", 1),
    ("WINE-P10", "酒盒顶托", "内托", "35mm EVA+325G PET光银+225G铜版底PET光银", 1),
    ("WINE-P11", "酒盒底托", "内托", "2mm灰板对裱11层=22mm+325G PET光银", 1),
]

REQ = {"industry": "packaging", "quote_quantity": 5000, "inner_length": 219.6,
       "inner_width": 89.2, "inner_height": 86.4, "fit_clearance": "", "box_type":
       "YT-DWG-WINE-700ML", "closure_type": "天地盖", "v_groove": "yes", "face_paper_gsm": "225"}


def _kb_tables():
    templates = []
    for seq, (code, name, component, material, quantity, size_expr, length_expr, width_expr,
              height_expr) in enumerate(PART_TEMPLATES_RB01001, start=1):
        templates.append({"part_code": code, "box_type_code": BOX_RB01001["box_type_code"],
                          "seq": seq, "name": name, "component": component,
                          "material": material, "quantity": quantity, "size_expr": size_expr,
                          "size_length_expr": length_expr, "size_width_expr": width_expr,
                          "size_height_expr": height_expr, "is_optional": 0,
                          "industry": "packaging", "source_type": "demo"})
    for seq, (code, name, component, material, quantity) in enumerate(PART_TEMPLATES_WINE,
                                                                    start=1):
        templates.append({"part_code": code, "box_type_code": BOX_WINE["box_type_code"],
                          "seq": seq, "name": name, "component": component,
                          "material": material, "quantity": quantity, "size_expr": None,
                          "size_length_expr": None, "size_width_expr": None,
                          "size_height_expr": None, "is_optional": 0,
                          "industry": "packaging", "source_type": "dwg_confirmed"})
    return {"kb_packaging_part_template": templates,
            "kb_packaging_box_type": [copy.deepcopy(BOX_RB01001), copy.deepcopy(BOX_WINE)]}


# --------------------------------------------------------------------------- #
# 夹具、模块加载
# --------------------------------------------------------------------------- #
def load_module():
    try:
        return importlib.import_module(PKG)
    except ModuleNotFoundError:
        return None


def fixture_ir(name: str = "parts_panels") -> dict:
    return json.loads((FIXTURES / ("%s.json" % name)).read_text(encoding="utf-8"))


class PartsCase(unittest.TestCase):
    """共用：知识库快照可替换 + 独立临时 SQLite / meta 目录。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "parts.sqlite3"
        self._patch_da = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch_da.start()
        da_db.init_db(self.db_file)
        self._backend = meta_backend._backend
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        meta_backend._backend = meta_backend.JsonMetaBackend(self.data_dir)
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = _kb_tables()

    def tearDown(self):
        meta_backend._backend = self._backend
        self._patch_da.stop()
        da_db.close_conn()
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    # ---- 模块 -------------------------------------------------------------- #
    def module(self):
        module = load_module()
        if module is None:
            self.fail("缺少 tech_app/backend/services/packaging_parts.py（Spec §4）")
        return module

    def extract(self, ir=None, options=None):
        return self.module().extract(ir or fixture_ir(), options=options)

    # ---- 断言助手 ---------------------------------------------------------- #
    def assertContract(self, module):
        for name in ("ENGINE_VERSION", "DOC_KEY", "DEFAULT_OPTIONS", "CURVE_TYPES",
                     "REASON_CODES", "PART_CODE_FORMAT"):
            self.assertTrue(hasattr(module, name), "packaging_parts 缺少契约常量 %s" % name)
        for name in ("extract", "save_parts", "load_parts", "bind_rows", "summarize"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "packaging_parts 缺少契约函数 %s()" % name)
        self.assertEqual(module.ENGINE_VERSION, ENGINE_VERSION)
        self.assertEqual(module.PART_CODE_FORMAT, PART_CODE_FORMAT)
        self.assertEqual(dict(module.DEFAULT_OPTIONS), DEFAULT_OPTIONS)
        self.assertEqual(set(module.REASON_CODES), set(REASON_CODES))

    def parts_of(self, doc):
        return {row["part_code"]: row for row in doc.get("parts") or []}


# --------------------------------------------------------------------------- #
# A 组：提取（夹具）
# --------------------------------------------------------------------------- #
class AExtract(PartsCase):
    def test_a1_module_and_contract_exist(self):
        self.assertContract(self.module())

    def test_a2_four_panels_in_area_order(self):
        doc = self.extract()
        codes = [row["part_code"] for row in doc["parts"]]
        self.assertEqual(codes, [item["part_code"] for item in EXPECTED_PARTS],
                         "零件号必须按 (面积降序, component_id 升序) 稳定编号")
        self.assertEqual([row["component_id"] for row in doc["parts"]],
                         [item["component_id"] for item in EXPECTED_PARTS])

    def test_a3_unfolded_size_comes_from_bbox(self):
        parts = self.parts_of(self.extract())
        for expected in EXPECTED_PARTS:
            row = parts[expected["part_code"]]
            self.assertAlmostEqual(row["unfolded_length_mm"], expected["length"], places=6,
                                   msg="%s 展开长" % expected["part_code"])
            self.assertAlmostEqual(row["unfolded_width_mm"], expected["width"], places=6,
                                   msg="%s 展开宽" % expected["part_code"])
            self.assertAlmostEqual(row["area_mm2"], expected["area"], places=3)
            self.assertEqual(row["size_source"], "dwg_outline")

    def test_a4_role_is_the_highest_of_the_component(self):
        parts = self.parts_of(self.extract())
        for expected in EXPECTED_PARTS:
            self.assertEqual(parts[expected["part_code"]]["role"], expected["role"],
                             "%s 的件内角色取优先级最高者（cut > crease > …）"
                             % expected["part_code"])
        self.assertEqual(parts["DWG-P01"]["layers"], ["CREASE", "CUT"],
                         "layers 去重升序，且必须列出件内出现过的全部图层")

    def test_a5_evidence_is_resolvable(self):
        ir = fixture_ir()
        evidence = ir.get("evidence") or {}
        for row in self.extract(ir)["parts"]:
            self.assertTrue(row["evidence_refs"], "%s 必须带证据引用" % row["part_code"])
            for ref in row["evidence_refs"]:
                self.assertIn(ref, evidence, "证据引用必须能在 ir.evidence 里回查：%s" % ref)
            self.assertTrue(row["entity_ids"], "%s 必须带实体 id" % row["part_code"])

    def test_a6_unit_unconfirmed_means_no_numbers(self):
        ir = fixture_ir()
        ir["units"] = {"drawing_units": "unitless", "unit_status": "needs_confirmation",
                       "unit_confidence": 0.4, "scale_to_mm": None, "candidates": []}
        doc = self.extract(ir)
        codes = [row["code"] for row in doc["unavailable"]]
        self.assertIn("no_unit", codes, "单位未确认时必须给 no_unit（Spec C4）")
        for row in doc["parts"]:
            self.assertIsNone(row["unfolded_length_mm"],
                              "单位未确认不许写绝对尺寸（第 4 批口径不变）")

    def test_a7_summarize_is_a_summary(self):
        module = self.module()
        summary = module.summarize(self.extract())
        self.assertNotIn("entity_ids", json.dumps(summary, ensure_ascii=False),
                         "摘要不许带实体明细")
        self.assertLess(len(json.dumps(summary, ensure_ascii=False)), 8000)


# --------------------------------------------------------------------------- #
# B 组：过滤 / 上限 / 确定性
# --------------------------------------------------------------------------- #
class BFilterAndDeterminism(PartsCase):
    def test_b1_frame_tiny_and_text_groups_are_filtered(self):
        doc = self.extract()
        filtered = {row["component_id"]: set(row["reasons"]) for row in doc["filtered"]}
        self.assertEqual(set(filtered), set(EXPECTED_FILTERED))
        for component_id, reasons in EXPECTED_FILTERED.items():
            self.assertTrue(reasons.issubset(filtered[component_id]),
                            "%s 的筛除原因缺：%s" % (component_id,
                                                sorted(reasons - filtered[component_id])))

    def test_b2_stats(self):
        stats = self.extract()["stats"]
        self.assertEqual(stats["part_total"], 4)
        self.assertEqual(stats["filtered_total"], 3)
        self.assertEqual(stats["truncated"], 0)
        self.assertEqual(set(stats["by_role"]), {row["role"] for row in self.extract()["parts"]})

    def test_b3_max_parts_truncates_and_says_so(self):
        doc = self.extract(options={"max_parts": 2})
        self.assertEqual([row["part_code"] for row in doc["parts"]], ["DWG-P01", "DWG-P02"])
        self.assertEqual(doc["stats"]["truncated"], 2)

    def test_b4_input_order_does_not_change_output(self):
        ir = fixture_ir()
        shuffled = copy.deepcopy(ir)
        shuffled["geometry"]["components"] = list(reversed(shuffled["geometry"]["components"]))
        for component in shuffled["geometry"]["components"]:
            component["entity_ids"] = list(reversed(component["entity_ids"]))
        first = json.dumps(self.extract(ir), ensure_ascii=False, sort_keys=True)
        second = json.dumps(self.extract(shuffled), ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second, "同一份 IR 换书写顺序必须逐字相同（Spec C3）")

    def test_b5_repeated_component_is_marked_not_dropped(self):
        ir = fixture_ir()
        duplicate = None
        for component in ir["geometry"]["components"]:
            if component["component_id"] == "cmp:L":
                duplicate = copy.deepcopy(component)
                duplicate["component_id"] = "cmp:L2"
                duplicate["entity_ids"] = ["ent:model:L9"]  # 同尺寸、同实体数
        self.assertIsNotNone(duplicate)
        ir["geometry"]["components"].append(duplicate)
        doc = self.extract(ir)
        marked = [row for row in doc["parts"] if row.get("repeat_of")]
        self.assertEqual(len(marked), 1, "重复件必须保留并标 repeat_of（Spec C3）")
        self.assertEqual(marked[0]["repeat_of"], "DWG-P02")


# --------------------------------------------------------------------------- #
# C 组：提不出来时必须说清为什么
# --------------------------------------------------------------------------- #
class CUnavailable(PartsCase):
    def test_c1_no_components(self):
        ir = fixture_ir()
        ir["geometry"]["components"] = []
        doc = self.extract(ir)
        self.assertEqual(doc["parts"], [])
        self.assertIn("no_components", [row["code"] for row in doc["unavailable"]])

    def test_c2_all_filtered(self):
        doc = self.extract(options={"min_area_mm2": 10 ** 9})
        self.assertEqual(doc["parts"], [])
        self.assertIn("all_filtered", [row["code"] for row in doc["unavailable"]])
        self.assertEqual(doc["stats"]["filtered_total"], 7)


# --------------------------------------------------------------------------- #
# D 组：落库 / 读回 / 链路 / 前端
# --------------------------------------------------------------------------- #
class DStorageAndWiring(PartsCase):
    def test_d1_save_and_load_are_versioned(self):
        module = self.module()
        doc = self.extract()
        saved = module.save_parts("parts00001", doc)
        self.assertTrue(saved.get("parts_id"), "落库必须给 parts_id")
        again = module.save_parts("parts00001", doc)
        self.assertEqual(again.get("parts_id"), saved.get("parts_id"),
                         "同一份文档重复落库不许换 id（幂等）")
        loaded = module.load_parts("parts00001")
        self.assertEqual(len(loaded["parts"]), len(doc["parts"]))
        self.assertIsNotNone(module.load_parts("parts00001", saved["parts_id"]))
        self.assertIsNone(module.load_parts("parts00001", "parts:nope"))

    def test_d2_http_routes_exist(self):
        text = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for needle in ("/requirement/packaging-parts",
                       "packaging-parts/extract"):
            self.assertIn(needle, text, "main.py 缺包装零件接口（Spec C5）")
        self.assertIn("BOX_MATCH_DECIDE_ROLES", text,
                      "零件提取的写权限必须直接引用 packaging_match 的角色常量")

    def test_d3_flow_has_the_parts_step(self):
        flow = importlib.import_module("tech_app.backend.services.packaging_drawing_flow")
        model = importlib.import_module(
            "tech_app.backend.services.packaging_drawing_flow.model")
        self.assertIn("parts_extract", model.STEP_IDS, "STEP_IDS 必须新增零件提取（Spec C6）")
        self.assertEqual(model.STEP_TITLES.get("parts_extract"), "零件提取")
        steps = list(model.STEP_IDS)
        self.assertLess(steps.index("packaging_semantics"), steps.index("parts_extract"))
        self.assertLess(steps.index("parts_extract"), steps.index("field_write"))
        source = pathlib.Path(FLOW_MODEL_PY).read_text(encoding="utf-8")
        self.assertIn("parts_extract", source)
        self.assertTrue(hasattr(flow, "gates"))

    def test_d4_step_detail_reports_parts(self):
        source = (ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow"
                  / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("packaging_parts", source, "链路必须接 packaging_parts（Spec C6）")
        for key in ("parts_id", "parts_hash", "parts_total", "filtered_total", "truncated",
                    "unavailable"):
            self.assertIn(key, source, "零件提取这一步的 detail 必须带 %s（Spec C6）" % key)

    def test_d5_front_end_renders_the_tree(self):
        text = APP_JS.read_text(encoding="utf-8", errors="replace")
        self.assertIn("packaging-parts", text, "2.1 零件树必须读零件文档（Spec §5）")
        self.assertNotIn("完成解析后显示零件清单", text,
                         "零件树不许再写「完成解析后显示零件清单」（图纸链路现在必须出零件）")


# --------------------------------------------------------------------------- #
# E 组：回填 BOM 行（C7）
# --------------------------------------------------------------------------- #
class EBomBinding(PartsCase):
    def expanded(self, box_code, inputs):
        return packaging_bom.expand_parts(box_code, inputs)

    def test_e1_baseline_needs_input_hangs_on_the_missing_variables(self):
        expanded = self.expanded("YT-RB-01001-A", REQ)
        status = {row["part_code"]: row["status"] for row in expanded["parts"]}
        self.assertEqual(sorted(k for k, v in status.items() if v == "needs_input"),
                         ["RB01001-P02", "RB01001-P03", "RB01001-P07", "RB01001-P08"],
                         "基线：这 4 行现在因为缺 H盖 / 盖展开尺寸 / 包边 而 needs_input")

    def test_e2_parts_fill_the_rows(self):
        module = self.module()
        items = packaging_bom._assemble(self.expanded("YT-RB-01001-A", REQ),
                                        packaging_bom._load_box_type("YT-RB-01001-A"),
                                        REQ, "REQ-X")
        result = module.bind_rows(items, self.extract())
        bound = {row["item_key"]: row for row in result["items"]}
        for code in ("RB01001-P02", "RB01001-P03", "RB01001-P07", "RB01001-P08"):
            row = bound[code]
            self.assertEqual(row["status"], "computed", "%s 绑定后必须能算" % code)
            self.assertTrue(row["length_mm"], "%s 必须有展开长" % code)
            self.assertTrue(row["width_mm"], "%s 必须有展开宽" % code)
            self.assertEqual(row["source"], "dwg_parts", "%s 必须标出来源是图纸零件" % code)
            source = row["size_source"] if isinstance(row["size_source"], dict) else \
                json.loads(row["size_source_json"])
            binding = source.get("dwg_binding") or {}
            self.assertEqual(binding.get("rule_id"), "dwg_parts_row_pairing_v1")
            self.assertTrue(binding.get("component_id"))
            self.assertTrue(binding.get("part_code"))
        self.assertEqual(result["bound"], 4)
        self.assertEqual(result["unbound"], [])

    def test_e3_locked_rows_are_never_touched(self):
        module = self.module()
        items = packaging_bom._assemble(self.expanded("YT-RB-01001-A", REQ),
                                        packaging_bom._load_box_type("YT-RB-01001-A"),
                                        REQ, "REQ-X")
        for row in items:
            if row["item_key"] == "RB01001-P07":
                row["locked"] = 1
                row["length_mm"] = 999.0
                row["width_mm"] = 999.0
        result = module.bind_rows(items, self.extract())
        locked = [row for row in result["items"] if row["item_key"] == "RB01001-P07"][0]
        self.assertEqual(locked["length_mm"], 999.0, "锁定行不许被绑定覆盖（Spec C7.1）")
        self.assertEqual(result["skipped_locked"], 1)

    def test_e4_rows_without_a_part_stay_needs_input_with_a_code(self):
        module = self.module()
        items = packaging_bom._assemble(self.expanded("YT-RB-01001-A", REQ),
                                        packaging_bom._load_box_type("YT-RB-01001-A"),
                                        REQ, "REQ-X")
        tiny = {"parts": [], "stats": {}, "unavailable": [{"code": "all_filtered",
                                                           "message": "全被筛"}]}
        result = module.bind_rows(items, tiny)
        self.assertEqual(result["bound"], 0)
        self.assertEqual(len(result["unbound"]), 4, "绑不上必须如实报 4 条")
        for code in result["unbound"]:
            self.assertTrue(code.startswith("part_size_unbound:"),
                            "缺口码必须是 part_size_unbound:<part_code>，实际 %s" % code)

    def test_e5_computable_rows_are_untouched(self):
        module = self.module()
        before = packaging_bom._assemble(self.expanded("YT-RB-01001-A", REQ),
                                         packaging_bom._load_box_type("YT-RB-01001-A"),
                                         REQ, "REQ-X")
        after = module.bind_rows(before, self.extract())["items"]
        keep = ("RB01001-P01", "RB01001-P04", "RB01001-P05", "RB01001-P06",
                "RB01001-P09", "RB01001-P10")
        old = {row["item_key"]: row for row in before}
        new = {row["item_key"]: row for row in after}
        for code in keep:
            for key in ("length_mm", "width_mm", "height_mm", "status", "source"):
                self.assertEqual(new[code][key], old[code][key],
                                 "%s.%s 表达式本来就算得出来，一个字都不许改（Spec C7.6）"
                                 % (code, key))

    def test_e6_dwg_box_rows_without_any_expression_are_bound_too(self):
        module = self.module()
        wine = dict(REQ)
        items = packaging_bom._assemble(self.expanded("YT-DWG-WINE-700ML", wine),
                                        packaging_bom._load_box_type("YT-DWG-WINE-700ML"),
                                        wine, "REQ-X")
        unbound_sizes = [row for row in items
                         if row["bom_category"] == "box_part" and not row.get("length_mm")]
        self.assertEqual(len(unbound_sizes), 11, "基线：DWG 实样盒型 11 行都没有展开尺寸")
        result = module.bind_rows(items, self.extract())
        bound = [row for row in result["items"]
                 if row["bom_category"] == "box_part" and row.get("length_mm")]
        self.assertEqual(len(bound), 11, "图纸零件必须把这 11 行填上展开尺寸（Spec C7）")


# --------------------------------------------------------------------------- #
# F 组：展开尺寸 → 成本能算（第 7 批口径）
# --------------------------------------------------------------------------- #
class FCostUsesTheSize(PartsCase):
    def test_f1_material_line_needs_a_size_then_it_computes(self):
        from tech_app.backend.services import packaging_cost
        variables = {"cut_length": 231.2, "cut_width": 100.8 + 5, "gsm": 225.0,
                     "ton_price": 6300.0, "imposition_count": 1.0, "proof_base": 0.0,
                     "quote_quantity": 5000.0, "tax_factor": 1.13,
                     "machine_length": 231.2, "machine_width": 100.8}
        line = packaging_cost.compute_line("material", variables)
        self.assertGreater(line.get("amount") or 0, 0,
                           "有了展开尺寸 + 克重 + 吨价，材料行就该出金额（Spec §1）")

    def test_f2_cost_still_reports_part_size_missing_without_a_size(self):
        text = (ROOT / "tech_app" / "backend" / "services" / "packaging_cost.py").read_text(
            encoding="utf-8")
        self.assertIn("part_size_missing", text,
                      "第 7 批的缺口码不变：没尺寸就不出金额（本批只负责让尺寸有值）")


# --------------------------------------------------------------------------- #
# G 组：不回归
# --------------------------------------------------------------------------- #
class GNoRegression(PartsCase):
    def test_g1_semantics_stats_are_unchanged(self):
        stats = packaging_semantics.analyze(fixture_ir())["stats"]
        self.assertEqual(stats["cut_layer_total"], 1)
        self.assertEqual(stats["crease_layer_total"], 1)
        self.assertEqual(stats["layer_total"], 5)

    def test_g2_bom_still_works_without_a_parts_document(self):
        expanded = packaging_bom.expand_parts("YT-RB-01001-A", REQ)
        self.assertEqual(expanded["needs_input_count"], 4,
                         "没有零件文档时口径逐字不变（不能偷偷把 needs_input 变 0）")

    def test_g3_parts_module_is_a_pure_function(self):
        module = self.module()
        before = json.dumps(kb_repo._snapshot(), ensure_ascii=False, sort_keys=True)
        with mock.patch.object(store, "audit") as spy:
            module.extract(fixture_ir())
            self.assertFalse(spy.called, "extract() 是纯函数：不许写审计、不许落盘")
        after = json.dumps(kb_repo._snapshot(), ensure_ascii=False, sort_keys=True)
        self.assertEqual(before, after)


# --------------------------------------------------------------------------- #
# H 组：真实样本（本机有样本 + libredwg 才跑）
# --------------------------------------------------------------------------- #
class HRealSample(PartsCase):
    def real_ir(self):
        sample = SAMPLES_DIR / "酒盒.dwg"
        if not sample.exists():
            self.skipTest("真实样本不在本机：%s" % sample)
        tool = shutil.which("dwg2dxf")
        if not tool:
            self.skipTest("本机没有 libredwg 的 dwg2dxf")
        digest = hashlib.sha256(sample.read_bytes()).hexdigest()[:16]
        cache = pathlib.Path(tempfile.mkdtemp()) / ("real_%s.dxf" % digest)
        subprocess.run([tool, "-y", "-o", str(cache), str(sample)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from tech_app.backend.services import cad_ir
        return cad_ir.parse_dxf(
            cache.read_bytes(), filename=cache.name,
            source={"kind": "dxf_2d", "attachment_name": "酒盒.dwg",
                    "converter_name": "libredwg", "converter_version": "0.14",
                    "converter_role": "primary", "fallback_used": False,
                    "drawing_version": 1, "conversion_status": "success_with_warnings"})

    def test_h1_real_dwg_produces_parts(self):
        ir = self.real_ir()
        doc = self.extract(ir)
        self.assertGreaterEqual(doc["stats"]["part_total"], 2,
                                "真实酒盒.dwg 必须出零件（Spec C4）")
        area = 0.0
        for row in doc["parts"]:
            self.assertGreater(row["unfolded_length_mm"] or 0, 0)
            self.assertGreater(row["unfolded_width_mm"] or 0, 0)
            self.assertLessEqual(row["unfolded_length_mm"], 1200.0)
            area = max(area, row["area_mm2"])
        self.assertGreater(area, 2000.0)

    def test_h2_real_dwg_filters_the_sheet_frame(self):
        doc = self.extract(self.real_ir())
        reasons = {reason for row in doc["filtered"] for reason in row["reasons"]}
        self.assertIn("edge_over_max", reasons,
                      "整版图框（真实样本 4451×3117mm）必须被筛掉，不能当成零件")


if __name__ == "__main__":
    unittest.main(verbosity=2)
