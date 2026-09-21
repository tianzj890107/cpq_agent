"""红测：DXF 确定性解析与统一 CAD IR —— DWG 支持第 3 批。

Spec：`docs/specs/dxf-cad-ir.md`（前置：第 1 批能力契约、第 2 批转换适配器）。
夹具：`tests/fixtures/dxf/`（19 个小 DXF，可直接打开人工核对；`broken.dxf` 是故意损坏的）。
真实样本：`裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg`（只读）。

首次运行时**必须失败**（实测）：
  · `tech_app/backend/services/cad_ir/` 不存在 → 没有解析器、没有 CAD IR、没有证据引用；
  · 全仓没有任何 DXF 解析（`grep -rn ezdxf --include=*.py .` 只命中第 2 批红测）；
  · 本机没有 DWG 转换器（第 2 批 Spec §1.1）→ 真实样本的转换产物不存在，
    `E` 组必须 `skipTest` 并写明"真实转换产物未就绪"，**不许**拿夹具冒充真实样本基线。

分组与依赖：
  A 算法正确性（夹具）· B 单位 · C 失败与限制 · D 与第 2 批接线（依赖第 2 批）
  E 真实样本（无产物即 skip）· F 夹具自检（现在应为绿）· G 与既有 IR 隔离 · H 版本 · I 幂等

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
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

FIXTURES = ROOT / "tests" / "fixtures" / "dxf"
BASELINES = ROOT / "tests" / "fixtures" / "real_baselines"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = SAMPLES_DIR / "酒盒.dwg"
ROUND_BOX = SAMPLES_DIR / "圆盘盒.dwg"
FRONTEND = ROOT / "tech_app" / "frontend"

CAD_IR_PKG = "tech_app.backend.services.cad_ir"
CAD_IR_PERSISTENCE = CAD_IR_PKG + ".persistence"
CAD_CONVERTER = "tech_app.backend.services.cad_converter"
PREFLIGHT = "tech_app.backend.services.file_preflight"

#: Spec §6.1：本批新增 2 码；HTTP/retryable 必须与第 1 批权威闭集一致
NEW_ERROR_CODES = {
    "CAD_IR_SOURCE_MISSING": (422, True),
    "CAD_IR_ENTITY_LIMIT_EXCEEDED": (413, False),
}
REUSED_ERROR_CODES = {
    "FILE_CORRUPTED": (422, True),
    "DWG_PARSE_FAILED": (502, True),
    "DWG_CONVERTER_NOT_INSTALLED": (415, True),
}
UNIT_STATUSES = {"confirmed", "needs_confirmation", "unknown"}
DRAWING_UNITS = {"mm", "inch", "ft", "cm", "m", "unitless", "unknown"}

REQUIRED_IR_KEYS = {"ir_version", "ir_id", "ir_hash", "source", "parser", "units", "document",
                    "layers", "entities", "geometry", "texts", "dimensions", "unsupported",
                    "warnings", "stats", "evidence"}
REQUIRED_STATS_KEYS = {"entity_total", "layer_total", "closed_outline_total", "open_outline_total",
                       "arc_total", "circle_total", "ellipse_total", "spline_total",
                       "dimension_total", "text_total", "block_ref_total", "unsupported_total"}


class _StopCall(Exception):
    """哨兵：本批不许发生的调用（模型 / 网络）真的发生了。"""


class CadIrCase(unittest.TestCase):
    # ---------------------------------------------------------------- 加载
    def cad_ir(self):
        try:
            return importlib.import_module(CAD_IR_PKG)
        except ModuleNotFoundError as exc:
            name = str(getattr(exc, "name", "") or "")
            if name.endswith("file_preflight"):
                self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现）")
            if name.endswith("cad_converter") or "cad_converter" in name:
                self.fail("依赖 DWG 第 2 批（`cad_converter` 未实现，见第 2 批 Spec §2）")
            self.fail("缺少 tech_app/backend/services/cad_ir/（本批 Spec §2）")

    def module(self, name):
        self.cad_ir()
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            self.fail("缺少 %s（本批 Spec §2）：%s" % (name, exc))

    def preflight(self):
        try:
            return importlib.import_module(PREFLIGHT)
        except ModuleNotFoundError:
            self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现）")

    def converter(self):
        try:
            return importlib.import_module(CAD_CONVERTER)
        except ModuleNotFoundError:
            self.fail("依赖 DWG 第 2 批（`cad_converter` 未实现，见第 2 批 Spec §2）")

    # ---------------------------------------------------------------- 夹具
    def fixture_bytes(self, name):
        path = FIXTURES / name
        self.assertTrue(path.exists(), "缺少夹具 %s（Spec §9）" % name)
        return path.read_bytes()

    def parse(self, name, **kwargs):
        package = self.cad_ir()
        fn = getattr(package, "parse_dxf", None)
        self.assertTrue(callable(fn), "cad_ir 必须导出 parse_dxf()（Spec §2.1）")
        return fn(self.fixture_bytes(name), filename=name, **kwargs)

    def use_env(self, **overrides):
        patcher = mock.patch.dict(os.environ, {k: str(v) for k, v in overrides.items()})
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------------------------------------------------------------- 断言帮手
    def json_safe(self, value):
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            self.fail("CAD IR 必须 JSON 安全（无 NaN/Infinity/bytes/Path）：%s" % exc)

    def entities_of(self, ir, kind=None):
        items = list(ir.get("entities") or [])
        if kind is not None:
            items = [item for item in items if item.get("type") == kind]
        return items

    def one_entity(self, ir, handle):
        for item in ir.get("entities") or []:
            if str(item.get("handle")) == str(handle):
                return item
        self.fail("IR 里找不到 handle=%s 的实体（Spec §3.1）" % handle)

    def evidence_ok(self, ir):
        evidence = ir.get("evidence") or {}
        missing = []
        for group, key in (("entities", "evidence_ref"), ("texts", "evidence_ref"),
                           ("dimensions", "evidence_ref")):
            for item in ir.get(group) or []:
                ref = item.get(key)
                if not ref or ref not in evidence:
                    missing.append("%s:%s" % (group, item.get("entity_id") or item.get("handle")))
        for group in ("closed_outlines", "open_outlines", "holes"):
            for item in (ir.get("geometry") or {}).get(group) or []:
                ref = item.get("evidence_ref")
                if not ref or ref not in evidence:
                    missing.append("geometry.%s:%s" % (group, item.get("outline_id")
                                                       or item.get("entity_id")))
        self.assertEqual(missing, [], "这些节点缺少可解析的 evidence_ref（Spec §7）：%s" % missing[:6])

    def expect_error(self, code, fn, *args, **kwargs):
        preflight = self.preflight()
        err_type = getattr(preflight, "FileCapabilityError")
        table = getattr(preflight, "STABLE_ERROR_CODES", {})
        expected = NEW_ERROR_CODES.get(code) or REUSED_ERROR_CODES.get(code)
        if expected is None:
            entry = table.get(code) or {}
            expected = (entry.get("http_status"), entry.get("retryable"))
        try:
            result = fn(*args, **kwargs)
        except err_type as exc:
            self.assertEqual(getattr(exc, "stable_error_code", None), code,
                             "错误码必须是 %s（Spec §6.1）" % code)
            self.assertEqual(getattr(exc, "http_status", None), expected[0], code)
            self.assertEqual(getattr(exc, "retryable", None), expected[1], code)
            self.assertTrue(getattr(exc, "message", ""), "%s 必须有中文用户文案" % code)
            return exc
        except BaseException as exc:  # noqa: BLE001 - 只做分类
            self.fail("期望 %s，实际抛出 %s: %s" % (code, type(exc).__name__, exc))
        self.fail("期望 %s，实际成功返回：%r" % (code, result))

    # ---------------------------------------------------------------- 持久化（内存版）
    def memory_persistence(self):
        persistence = self.module(CAD_IR_PERSISTENCE)
        root = pathlib.Path(tempfile.mkdtemp(prefix="cad-ir-test-"))
        self.addCleanup(shutil.rmtree, root, True)
        state = {"root": root, "saved": [], "seq": 0}

        def save_ir(project_id, ir):
            for index, item in enumerate(state["saved"]):
                if item.get("ir_id") == ir.get("ir_id"):
                    state["saved"][index] = dict(ir)
                    return dict(ir)
            state["seq"] += 1
            (root / str(project_id)).mkdir(parents=True, exist_ok=True)
            (root / str(project_id) / ("%s.json" % ir.get("ir_id"))).write_text(
                json.dumps(ir, ensure_ascii=False), encoding="utf-8")
            state["saved"].insert(0, dict(ir))
            return dict(ir)

        def load_ir(project_id, ir_id=None):
            for item in state["saved"]:
                if ir_id is None or item.get("ir_id") == ir_id:
                    return dict(item)
            return None

        def list_irs(project_id):
            return [dict(item) for item in state["saved"]]

        for name, fn in (("save_ir", save_ir), ("load_ir", load_ir), ("list_irs", list_irs)):
            self.assertTrue(hasattr(persistence, name),
                            "cad_ir.persistence 必须提供 %s()（Spec §6.4）" % name)
            patcher = mock.patch.object(persistence, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        return state

    def fake_conversion(self, *, available=True, dxf_bytes=None, conversion_id="conv0001",
                        filename="drawing.dxf", manifest_missing=False, tmp=None,
                        status="ok", quality=None, warning_count=0, error_count=0,
                        converter_name="fake", converter_version="0.0.0",
                        converter_role="primary", fallback_used=False,
                        primary_failure_code=""):
        """模拟第 2 批的产物与 manifest（第 2 批未实现时本组按"依赖第 2 批"失败）。"""
        converter = self.converter()
        dxf_bytes = dxf_bytes if dxf_bytes is not None else self.fixture_bytes("rect_10x5.dxf")
        artifact_dir = tmp or pathlib.Path(tempfile.mkdtemp(prefix="cad-ir-conv-"))
        self.addCleanup(shutil.rmtree, artifact_dir, True)
        (artifact_dir / filename).write_bytes(dxf_bytes)

        def artifact_dir_fn(project_id, conversion_id_arg):
            return artifact_dir

        manifest = {
            "conversion_id": conversion_id, "status": status, "is_simulated": False,
            "project_id": "cad-ir-project", "attachment_name": "酒盒.dwg",
            "drawing_version": 2, "original_filename": "酒盒.dwg",
            "source_sha256": "a" * 64, "source_format": "dwg",
            "detected_dwg_version": "AC1027",
            "converter_name": converter_name, "converter_version": converter_version,
            "converter_role": converter_role, "fallback_used": bool(fallback_used),
            "primary_failure_code": primary_failure_code,
            "output_files": [{"role": "dxf", "filename": filename,
                              "sha256": hashlib.sha256(dxf_bytes).hexdigest(),
                              "bytes": len(dxf_bytes)}],
            "output_sha256": {filename: hashlib.sha256(dxf_bytes).hexdigest()},
            "warnings": [], "error_code": None,
            "warning_count": int(warning_count), "error_count": int(error_count),
        }
        if quality is not None:
            manifest["quality"] = dict(quality)
        patches = [
            mock.patch.object(converter, "capability", lambda **kw: {
                "available": available, "simulated": False, "adapter_name": "fake",
                "converter_version": "0.0.0", "dwg_conversion": available,
                "preview_render": available, "three_d_conversion": False,
                "stable_error_code": "" if available else "DWG_CONVERTER_NOT_INSTALLED",
                "message": "", "dwg_supported": False, "support_claim": "conversion_available"}),
            mock.patch.object(converter, "latest_manifest",
                              lambda project_id, **kw: None if manifest_missing else dict(manifest)),
        ]
        if hasattr(converter, "persistence"):
            patches.append(mock.patch.object(converter.persistence, "artifact_dir", artifact_dir_fn))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return {"manifest": manifest, "dir": artifact_dir, "dxf": dxf_bytes}

    def parse_conversion(self, project_id="cad-ir-project", **kwargs):
        package = self.cad_ir()
        fn = getattr(package, "parse_conversion", None)
        self.assertTrue(callable(fn), "cad_ir 必须导出 parse_conversion()（Spec §2.1）")
        return fn(project_id, **kwargs)

    def sample(self, path):
        if not path.exists():
            self.skipTest("客户样本不在本机（不入库）：%s" % path.name)
        return path.read_bytes()


# --------------------------------------------------------------------------- #
# A. 算法正确性（小夹具验算法）
# --------------------------------------------------------------------------- #
class AGeometryBasics(CadIrCase):
    def test_a1_closed_rectangle_area_length_and_bbox(self):
        ir = self.parse("rect_10x5.dxf")
        self.assertEqual(ir.get("ir_version"), self.cad_ir().CAD_IR_VERSION)
        self.assertEqual((ir.get("source") or {}).get("kind"), "dxf_2d")
        self.assertEqual((ir.get("parser") or {}).get("name"), "ezdxf")
        self.assertEqual(ir["stats"]["closed_outline_total"], 1)
        self.assertEqual(ir["stats"]["open_outline_total"], 0)
        outline = ir["geometry"]["closed_outlines"][0]
        self.assertAlmostEqual(outline["area"], 50.0, places=6)
        self.assertAlmostEqual(outline["length"], 30.0, places=6)
        self.assertEqual([round(v, 6) for v in outline["bbox"]], [0.0, 0.0, 10.0, 5.0])
        self.assertEqual(self.one_entity(ir, "10")["entity_id"], "ent:model:10")

    def test_a2_circles_are_holes_not_outlines(self):
        ir = self.parse("hole_plate.dxf")
        self.assertEqual(ir["stats"]["closed_outline_total"], 1, "闭合轮廓只数闭合折线")
        holes = ir["geometry"]["holes"]
        self.assertEqual(sorted(round(hole["diameter"], 3) for hole in holes), [3.0, 6.0])
        self.assertEqual([round(v, 6) for v in ir["geometry"]["closed_outlines"][0]["bbox"]],
                         [0.0, 0.0, 40.0, 20.0])
        for hole in holes:
            self.assertEqual(hole.get("kind"), "circle")
            self.assertTrue(hole.get("center"))

    def test_a3_open_polyline_has_length_but_no_area(self):
        ir = self.parse("open_polyline.dxf")
        self.assertEqual(ir["stats"]["open_outline_total"], 2, "开放折线与 LINE 都算开放回路")
        self.assertEqual(ir["stats"]["closed_outline_total"], 0)
        polyline = [o for o in ir["geometry"]["open_outlines"] if o.get("entity_id") == "ent:model:30"]
        self.assertTrue(polyline, "开放折线必须在 open_outlines 里")
        self.assertAlmostEqual(polyline[0]["length"], 20.0, places=6)
        self.assertIsNone(polyline[0]["area"], "开口折线不得给面积（Spec §3.2）")

    def test_a4_arc_circle_ellipse_spline_are_not_dropped(self):
        ir = self.parse("curves_arc_ellipse_spline.dxf")
        stats = ir["stats"]
        for key in ("arc_total", "circle_total", "ellipse_total", "spline_total"):
            self.assertEqual(stats[key], 1, "%s 不得被丢弃" % key)
        self.assertEqual(ir["stats"]["unsupported_total"], 0)
        arc = self.one_entity(ir, "40")
        self.assertAlmostEqual(arc["length"], math.pi * 10 / 2, places=5)
        circle = self.one_entity(ir, "41")
        self.assertAlmostEqual(circle["length"], 2 * math.pi * 2, places=5)
        ellipse = self.one_entity(ir, "42")
        self.assertEqual([round(v, 6) for v in ellipse["bbox"]], [30.0, 40.0, 70.0, 60.0])
        spline = self.one_entity(ir, "43")
        self.assertGreater(spline["length"], 0.0, "样条长度必须真实计算，不许跳过")

    def test_a5_layers_keep_metadata_and_claim_no_role(self):
        ir = self.parse("layers_cut_crease.dxf")
        layers = {layer["name"]: layer for layer in ir["layers"]}
        for name in ("CUT", "CREASE", "PRINT", "FRAME"):
            self.assertIn(name, layers, "图层 %s 必须原样保留" % name)
        self.assertEqual(layers["CUT"]["entity_count"], 1)
        self.assertEqual(layers["CREASE"]["entity_count"], 2)
        self.assertEqual(layers["PRINT"]["entity_count"], 1)
        self.assertEqual(layers["FRAME"]["entity_count"], 1)
        self.assertEqual(layers["CREASE"]["color"], 3)
        self.assertEqual(layers["CREASE"]["line_type"], "DASHED")
        for name, layer in layers.items():
            self.assertIsNone(layer.get("inferred_role"),
                              "第 3 批不许判断刀线/压痕角色（%s，Spec §3.3）" % name)
        mtext = [text for text in ir["texts"] if text.get("type") == "MTEXT"]
        self.assertTrue(mtext, "MTEXT 必须进 texts")
        self.assertEqual(mtext[0]["normalized_text"], "材质：白卡纸 350g",
                         "必须解码 \\U+XXXX 转义（Spec §3）")

    def test_a6_dimension_text_and_measured_geometry_are_kept_apart(self):
        ir = self.parse("dims_override.dxf")
        self.assertEqual(ir["stats"]["dimension_total"], 1)
        dimension = ir["dimensions"][0]
        self.assertAlmostEqual(dimension["declared_value"], 70.0, places=6)
        self.assertAlmostEqual(dimension["measured_value"], 72.0, places=6)
        self.assertAlmostEqual(dimension["delta"], 2.0, places=6)
        self.assertEqual(dimension["raw_text"], "70")
        rect = self.one_entity(ir, "33")
        self.assertAlmostEqual(rect["length"], 2 * (72 + 20), places=6,
                               msg="几何实测必须来自几何，不许被标注文字改写")
        text = [item for item in ir["texts"] if item.get("normalized_text") == "单位：mm"]
        self.assertTrue(text, "「单位：mm」必须能归一化读出")

    def test_a7_rotated_block_reference_applies_the_transform(self):
        ir = self.parse("block_rotated.dxf")
        resolved = self.entities_of(ir, "LWPOLYLINE")
        self.assertEqual(len(resolved), 1)
        self.assertEqual([round(v, 6) for v in resolved[0]["bbox"]], [95.0, 0.0, 100.0, 10.0],
                         "必须用变换后的绝对坐标，不是块定义里的原始坐标（Spec §5）")
        self.assertTrue(resolved[0].get("block_path"), "块内实体必须记录 block_path")

    def test_a8_nested_blocks_and_repeated_references(self):
        ir = self.parse("block_nested.dxf")
        resolved = self.entities_of(ir, "LWPOLYLINE")
        self.assertEqual(len(resolved), 4, "2 个 OUTER 引用 × 2 个 INNER 引用 = 4 个内层矩形")
        union = [min(item["bbox"][0] for item in resolved), min(item["bbox"][1] for item in resolved),
                 max(item["bbox"][2] for item in resolved), max(item["bbox"][3] for item in resolved)]
        self.assertEqual([round(v, 6) for v in union], [-2.0, 0.0, 64.0, 74.0])
        for item in resolved:
            self.assertEqual(len(item["block_path"]), 2, "嵌套必须完整记录两级 block_path")

    def test_a9_mirrored_blocks_do_not_change_area(self):
        for name in ("block_mirror.dxf", "block_mirror_flip.dxf"):
            ir = self.parse(name)
            resolved = self.entities_of(ir, "LWPOLYLINE")
            self.assertEqual(len(resolved), 2, name)
            areas = [round(item["area"], 6) for item in resolved]
            for area in areas:
                self.assertAlmostEqual(area, 51.0, places=6, msg="镜像不改变面积：%s" % name)
                self.assertGreater(area, 0.0, "镜像后的面积必须是正值：%s" % name)
            first, second = resolved[0]["bbox"], resolved[1]["bbox"]
            self.assertNotEqual([round(v, 6) for v in first], [round(v, 6) for v in second],
                                "镜像引用必须与未镜像引用落在不同位置：%s" % name)
            for bbox in (first, second):
                self.assertAlmostEqual(bbox[2] - bbox[0], 10.0, places=6)
                self.assertAlmostEqual(bbox[3] - bbox[1], 10.0, places=6)

    def test_a10_block_cycle_is_bounded_and_warned(self):
        ir = self.parse("block_cycle.dxf")
        self.assertLessEqual(len(ir["entities"]), 50, "循环引用必须被截断，不能无限展开")
        warnings = " ".join(str(item.get("code")) for item in ir["warnings"])
        self.assertIn("block_cycle", warnings, "循环引用必须留下 block_cycle 警告（Spec §5）")

    def test_a11_unsupported_entities_warn_without_losing_the_drawing(self):
        ir = self.parse("unsupported_entities.dxf")
        kinds = {item.get("type") for item in ir["unsupported"]}
        self.assertIn("REGION", kinds, "不支持的实体必须登记在 unsupported（Spec §3）")
        self.assertGreaterEqual(ir["stats"]["unsupported_total"], 1)
        self.assertEqual(ir["stats"]["closed_outline_total"], 1, "其余实体照常解析")
        blob = " ".join(str(item.get("code")) for item in ir["warnings"])
        self.assertIn("unsupported", blob)

    def test_a12_same_input_gives_the_same_hash_and_ids(self):
        first = self.parse("layers_cut_crease.dxf")
        second = self.parse("layers_cut_crease.dxf")
        self.assertEqual(first["ir_hash"], second["ir_hash"])
        self.assertEqual([item["entity_id"] for item in first["entities"]],
                         [item["entity_id"] for item in second["entities"]])
        self.assertEqual(first["ir_hash"], self.cad_ir().ir_hash(first))

    def test_a13_entity_order_does_not_change_ids_or_hash(self):
        first = self.parse("order_a.dxf")
        second = self.parse("order_b.dxf")
        self.assertEqual({item["entity_id"] for item in first["entities"]},
                         {item["entity_id"] for item in second["entities"]})
        self.assertEqual(first["ir_hash"], second["ir_hash"],
                         "实体书写顺序不同不得改变规范哈希（Spec §3.4）")

    def test_a14_r12_polyline_is_supported(self):
        ir = self.parse("r12_polyline.dxf")
        self.assertEqual(ir["stats"]["closed_outline_total"], 1)
        outline = ir["geometry"]["closed_outlines"][0]
        self.assertAlmostEqual(outline["area"], 50.0, places=6)
        self.assertAlmostEqual(outline["length"], 10 + 10 + math.hypot(10, 10), places=5)

    def test_a15_all_entities_are_counted(self):
        ir = self.parse("many_entities.dxf")
        self.assertEqual(len(self.entities_of(ir, "LINE")), 200)
        self.assertEqual(ir["stats"]["entity_total"], 200)

    def test_a16_predecoded_text_is_never_decoded_twice(self):
        ir = self.parse("text_predecoded.dxf")
        texts = [item["normalized_text"] for item in ir["texts"]]
        self.assertIn("材质：白卡纸 350g", texts,
                      "已是明文的中文必须原样读出（LibreDWG 转出的 DXF 就是明文，Spec §3.6）")
        self.assertIn("角  度", texts, "TEXT 明文同样不许被二次解码或改写")
        self.assertNotIn("\\U+", " ".join(texts), "明文里不许出现伪转义")

    def test_a17_dimension_without_text_falls_back_to_the_measurement(self):
        ir = self.parse("dim_empty_text.dxf")
        self.assertEqual(ir["stats"]["dimension_total"], 1)
        dimension = ir["dimensions"][0]
        self.assertEqual(dimension["raw_text"], "")
        self.assertAlmostEqual(dimension["measured_value"], 72.0, places=6)
        self.assertAlmostEqual(dimension["declared_value"], 72.0, places=6,
                               msg="没有标注文字时 declared 必须回落到实测值（Spec §3.7）")
        self.assertAlmostEqual(dimension["delta"], 0.0, places=6)
        self.json_safe(ir)

    def test_a18_hatch_keeps_its_boundary_but_is_not_a_cut_outline(self):
        ir = self.parse("hatch_boundary.dxf")
        hatches = self.entities_of(ir, "HATCH")
        self.assertEqual(len(hatches), 1, "HATCH 必须进 entities（Spec §3.5）")
        self.assertGreaterEqual(hatches[0]["attributes"].get("boundary_paths", 0), 1,
                                "HATCH 必须登记边界信息")
        self.assertEqual(ir["stats"]["closed_outline_total"], 1,
                         "只有那条闭合折线算轮廓，HATCH 填充不是刀线")
        self.assertEqual(ir["stats"]["unsupported_total"], 0,
                         "HATCH 属本批支持类型，不许塞进 unsupported")


# --------------------------------------------------------------------------- #
# B. 单位不许猜
# --------------------------------------------------------------------------- #
class BUnits(CadIrCase):
    def test_b1_mm_is_confirmed(self):
        ir = self.parse("rect_10x5.dxf")
        units = ir["units"]
        self.assertEqual(units["drawing_units"], "mm")
        self.assertEqual(units["unit_status"], "confirmed")
        self.assertEqual(units["scale_to_mm"], 1.0)
        outline = ir["geometry"]["closed_outlines"][0]
        self.assertAlmostEqual(outline["length_mm"], 30.0, places=6)
        self.assertAlmostEqual(outline["area_mm2"], 50.0, places=6)

    def test_b2_inch_is_confirmed_and_scaled(self):
        ir = self.parse("units_inch.dxf")
        units = ir["units"]
        self.assertEqual(units["drawing_units"], "inch")
        self.assertEqual(units["unit_status"], "confirmed")
        self.assertAlmostEqual(units["scale_to_mm"], 25.4, places=6)
        outline = ir["geometry"]["closed_outlines"][0]
        self.assertAlmostEqual(outline["length"], 40.0, places=6)
        self.assertAlmostEqual(outline["length_mm"], 1016.0, places=4)
        self.assertAlmostEqual(outline["area_mm2"], 100 * 25.4 ** 2, places=3)

    def test_b3_unitless_dxf_never_claims_mm(self):
        ir = self.parse("units_unitless.dxf")
        units = ir["units"]
        self.assertIn(units["unit_status"], UNIT_STATUSES)
        self.assertEqual(units["unit_status"], "needs_confirmation")
        self.assertNotEqual(units["drawing_units"], "mm", "单位未声明时不许直接假定毫米（Spec §4）")
        self.assertIsNone(units["scale_to_mm"])
        self.assertLessEqual(units["unit_confidence"], 0.5)
        self.assertTrue(units.get("candidates"), "必须给出单位候选供人工确认")
        self.assertTrue(any(item.get("unit") == "mm" for item in units["candidates"]))
        outline = ir["geometry"]["closed_outlines"][0]
        self.assertIsNone(outline["length_mm"], "单位未确认时不得给出毫米尺寸")
        self.assertIsNone(outline["area_mm2"])
        self.assertIn("unit_unconfirmed", " ".join(str(w.get("code")) for w in ir["warnings"]))

    def test_b4_capability_reports_the_parser(self):
        cap = self.cad_ir().capability()
        self.assertTrue(cap.get("available"), "本机有 ezdxf，解析能力必须为真")
        self.assertEqual(cap.get("parser"), "ezdxf")
        self.assertTrue(cap.get("parser_version"))
        self.assertGreater(cap.get("max_entities"), 0)
        self.assertGreater(cap.get("max_block_depth"), 0)


# --------------------------------------------------------------------------- #
# C. 失败与限制
# --------------------------------------------------------------------------- #
class CFailuresAndLimits(CadIrCase):
    def test_c1_broken_dxf_fails_safely(self):
        package = self.cad_ir()
        self.expect_error("FILE_CORRUPTED", package.parse_dxf,
                          self.fixture_bytes("broken.dxf"), filename="broken.dxf")

    def test_c2_entity_limit_is_enforced_loudly(self):
        self.use_env(CAD_IR_MAX_ENTITIES="50")
        package = self.cad_ir()
        self.expect_error("CAD_IR_ENTITY_LIMIT_EXCEEDED", package.parse_dxf,
                          self.fixture_bytes("many_entities.dxf"), filename="many_entities.dxf")

    def test_c3_ir_is_json_safe(self):
        for name in ("rect_10x5.dxf", "curves_arc_ellipse_spline.dxf", "dims_override.dxf"):
            ir = self.parse(name)
            blob = self.json_safe(ir)
            for banned in ("NaN", "Infinity", "-Infinity"):
                self.assertNotIn(banned, blob, "%s 的 IR 不得含 %s" % (name, banned))

    def test_c4_every_key_node_carries_resolvable_evidence(self):
        for name in ("hole_plate.dxf", "layers_cut_crease.dxf", "dims_override.dxf"):
            self.evidence_ok(self.parse(name))

    def test_c5_evidence_ids_are_handle_based_not_index_based(self):
        ir = self.parse("order_a.dxf")
        handles = {str(item["handle"]) for item in ir["entities"]}
        for item in ir["entities"]:
            self.assertIn(str(item["handle"]), item["evidence_ref"],
                          "证据引用必须带稳定句柄，不许用数组下标（Spec §3.1）：%s"
                          % item["evidence_ref"])
        self.assertEqual(handles, {"A0", "A1", "A2"})


# --------------------------------------------------------------------------- #
# D. 与第 2 批接线（依赖第 2 批；第 2 批未实现时按"依赖第 2 批"失败）
# --------------------------------------------------------------------------- #
class DConversionWiring(CadIrCase):
    def test_d1_ir_carries_conversion_provenance(self):
        state = self.memory_persistence()
        conversion = self.fake_conversion()
        store = importlib.import_module("tech_app.backend.storage.store")
        audits = []
        with mock.patch.object(store, "audit", lambda pid, action, detail=None: audits.append(
                {"project_id": pid, "action": action, "detail": detail})):
            ir = self.parse_conversion()
        source = ir["source"]
        self.assertEqual(source["conversion_id"], conversion["manifest"]["conversion_id"])
        self.assertEqual(source["dxf_sha256"], hashlib.sha256(conversion["dxf"]).hexdigest())
        self.assertEqual(source["converter_name"], "fake")
        self.assertEqual(source["detected_dwg_version"], "AC1027")
        self.assertEqual(source["project_id"], "cad-ir-project")
        self.assertEqual(source["attachment_name"], "酒盒.dwg")
        self.assertTrue(state["saved"], "解析成功必须经 persistence 落盘（Spec §6.4）")
        self.assertTrue([item for item in audits if "cad_ir" in str(item["action"])],
                        "必须写 cad_ir 审计：%r" % [item["action"] for item in audits])

    def test_d2_missing_source_has_its_own_codes(self):
        self.memory_persistence()
        self.fake_conversion(available=False)
        self.expect_error("DWG_CONVERTER_NOT_INSTALLED", lambda: self.parse_conversion("p"))

    def test_d3_missing_manifest_is_a_retryable_source_gap(self):
        self.memory_persistence()
        self.fake_conversion(available=True, manifest_missing=True)
        self.expect_error("CAD_IR_SOURCE_MISSING", lambda: self.parse_conversion("p"))

    def test_d4_parse_failure_never_marks_the_step_done(self):
        state = self.memory_persistence()
        self.fake_conversion(dxf_bytes=self.fixture_bytes("broken.dxf"))
        store = importlib.import_module("tech_app.backend.storage.store")
        audits = []
        with mock.patch.object(store, "audit", lambda pid, action, detail=None: audits.append(action)):
            self.expect_error("FILE_CORRUPTED", lambda: self.parse_conversion("p"))
        self.assertEqual(state["saved"], [], "解析失败不得写 IR（Spec §6.2）")
        self.assertEqual([a for a in audits if "cad_ir_parsed" in str(a)], [])

    def test_d5_parsing_never_calls_models_or_the_network(self):
        self.memory_persistence()
        self.fake_conversion()
        targets = []
        for name, attr in (("vision", "parse_drawing"), ("claude_client", "run")):
            try:
                module = importlib.import_module("tech_app.backend.services.%s" % name)
            except Exception:  # pragma: no cover - 依赖环境
                continue
            if hasattr(module, attr):
                patcher = mock.patch.object(module, attr, mock.Mock(side_effect=_StopCall()))
                patcher.start()
                self.addCleanup(patcher.stop)
                targets.append("%s.%s" % (name, attr))
        try:
            import urllib.request
            patcher = mock.patch.object(urllib.request, "urlopen",
                                        mock.Mock(side_effect=_StopCall()))
            patcher.start()
            self.addCleanup(patcher.stop)
            targets.append("urllib.request.urlopen")
        except Exception:  # pragma: no cover
            pass
        try:
            self.parse("rect_10x5.dxf")
            self.parse_conversion("p")
        except _StopCall:
            self.fail("CAD IR 解析必须完全离线，实测调用了：%s" % targets)


# --------------------------------------------------------------------------- #
# E. 真实样本（转换产物或人工基线不存在时如实 skip）
# --------------------------------------------------------------------------- #
class ERealSamples(CadIrCase):
    REAL_INVARIANTS = ("entity_total", "layer_total", "closed_outline_total", "dimension_total")

    def test_e1_two_real_samples_parse_when_a_real_conversion_exists(self):
        converter = self.converter()
        capability = converter.capability()
        if not capability.get("available") or capability.get("simulated"):
            self.skipTest("真实转换产物未就绪：本机没有真实 DWG 转换器（第 2 批 Spec §1.1）。"
                          "夹具只能验算法，真实样本必须等第 2 批 B 层通过后再验收")
        adapter = converter.get_adapter()
        package = self.cad_ir()
        for sample in (WINE_BOX, ROUND_BOX):
            content = self.sample(sample)
            manifest = converter.convert_drawing("cad-ir-realsample", sample.name, content,
                                                 adapter=adapter)
            # 干净与「有警告」都是可用产物：具体哪个转换器给哪个状态由第 2 批修复 Spec §5 判
            # （ODA 主链路实测 stderr 为空 → ok；LibreDWG 回退 → success_with_warnings）。
            self.assertIn(manifest["status"], {"ok", "success_with_warnings"}, sample.name)
            dxf_item = [item for item in manifest["output_files"] if item["role"] == "dxf"][0]
            dxf_path = converter.persistence.artifact_dir(
                "cad-ir-realsample", manifest["conversion_id"]) / dxf_item["filename"]
            ir = package.parse_dxf(dxf_path.read_bytes(), filename=sample.name)
            for key in self.REAL_INVARIANTS:
                self.assertIn(key, ir["stats"], "%s 缺少 %s" % (sample.name, key))
            self.assertGreater(ir["stats"]["entity_total"], 0, "%s 实体数为 0" % sample.name)
            self.assertGreater(ir["stats"]["layer_total"], 0, "%s 图层数为 0" % sample.name)
            self.assertIn(ir["units"]["unit_status"], UNIT_STATUSES)
            self.json_safe(ir)
            self.evidence_ok(ir)

    def test_e3_ir_cross_checks_the_conversion_manifest(self):
        """第 2 批的质量计数必须与第 3 批解析出的统计一致（第 3 批 Spec §3.9）。"""
        self.memory_persistence()
        quality = {"verified": True, "entity_count": 1, "layer_count": 6,
                   "text_count": 0, "dimension_count": 0, "block_ref_count": 0}
        self.fake_conversion(status="success_with_warnings", quality=quality,
                            warning_count=252, error_count=3)
        ir = self.parse_conversion("cad-ir-project")
        source = ir["source"]
        self.assertEqual(source.get("conversion_status"), "success_with_warnings")
        self.assertEqual(source.get("warning_count"), 252, "告警计数必须透传（Spec §3.9）")
        self.assertEqual(source.get("error_count"), 3, "错误计数必须透传（Spec §3.9）")
        self.assertTrue((source.get("quality") or {}).get("verified"))
        crosscheck = source.get("crosscheck") or {}
        self.assertTrue(crosscheck.get("match"),
                        "IR 侧顶层计数必须与 manifest 的 quality 一致：%r" % crosscheck)
        self.assertEqual({k: v for k, v in (crosscheck.get("deltas") or {}).items() if v}, {},
                         "一致时逐项差值必须全为 0：%r" % crosscheck.get("deltas"))
        codes = " ".join(str(item.get("code")) for item in ir["warnings"])
        self.assertIn("conversion_degraded", codes,
                      "有损转换必须在 IR 里留下 conversion_degraded 警告")

    def test_e4_manifest_mismatch_is_never_silent(self):
        self.memory_persistence()
        self.fake_conversion(quality={"verified": True, "entity_count": 99, "layer_count": 6},
                            warning_count=0, error_count=0)
        ir = self.parse_conversion("cad-ir-project")
        crosscheck = (ir["source"] or {}).get("crosscheck") or {}
        self.assertFalse(crosscheck.get("match"), "计数不一致必须 match=false：%r" % crosscheck)
        self.assertEqual((crosscheck.get("deltas") or {}).get("entity_count"), -98,
                         "差值口径是 IR 侧 − manifest 侧（Spec §3.9）：%r" % crosscheck.get("deltas"))
        codes = " ".join(str(item.get("code")) for item in ir["warnings"])
        self.assertIn("ir_manifest_mismatch", codes, "不一致不许静默：%r" % codes)

    def test_e5_fallback_conversion_is_marked_in_the_ir(self):
        self.memory_persistence()
        self.fake_conversion(status="success_with_warnings",
                            quality={"verified": True, "entity_count": 1, "layer_count": 6},
                            warning_count=3, error_count=0,
                            converter_name="libredwg", converter_version="0.14",
                            converter_role="fallback", fallback_used=True,
                            primary_failure_code="DWG_CONVERSION_FAILED")
        ir = self.parse_conversion("cad-ir-project")
        source = ir["source"]
        self.assertEqual(source.get("converter_name"), "libredwg")
        self.assertEqual(source.get("converter_role"), "fallback",
                         "产出方身份必须透传（Spec §3.9）：%r" % source)
        self.assertTrue(source.get("fallback_used"), source)
        codes = " ".join(str(item.get("code")) for item in ir["warnings"])
        self.assertIn("conversion_fallback_used", codes,
                      "回退产物必须在 IR 里留痕（Spec §3.9）：%r" % ir["warnings"])

    def test_e2_golden_baseline_must_be_human_reviewed(self):
        missing = [name for name in ("酒盒.golden.json", "圆盘盒.golden.json")
                   if not (BASELINES / name).exists()]
        if missing:
            self.skipTest("真实基线未人工复核（缺少 %s）；不许自动生成 snapshot" % ", ".join(missing))
        for name in ("酒盒.golden.json", "圆盘盒.golden.json"):
            golden = json.loads((BASELINES / name).read_text(encoding="utf-8"))
            for key in ("source_sha256", "reviewed_by", "reviewed_at"):
                self.assertIn(key, golden, "%s 必须由人工填写 %s（Spec §10）" % (name, key))


# --------------------------------------------------------------------------- #
# F. 夹具自检（这两条现在就是绿的：独立证明夹具本身可用）
# --------------------------------------------------------------------------- #
class FFixtureSanity(CadIrCase):
    def test_f1_every_fixture_is_readable_except_the_broken_one(self):
        try:
            import ezdxf
        except ImportError:  # pragma: no cover
            self.skipTest("缺少 ezdxf")
        fixtures = sorted(FIXTURES.glob("*.dxf"))
        self.assertGreaterEqual(len(fixtures), 18, "夹具数量不对：%d" % len(fixtures))
        for path in fixtures:
            if path.name == "broken.dxf":
                continue
            doc = ezdxf.readfile(str(path))  # 读不动就是夹具坏了
            self.assertGreaterEqual(len(list(doc.modelspace())) + len(list(doc.blocks)), 1, path.name)

    def test_f2_fixture_check_mode_still_passes(self):
        script = FIXTURES / "build_fixtures.py"
        self.assertTrue(script.exists(), "夹具生成脚本必须入库（Spec §9）")
        result = subprocess.run([sys.executable, str(script), "--check"],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("READ FAILED", result.stdout)


# --------------------------------------------------------------------------- #
# G. 与既有 IR / 3D 链路隔离
# --------------------------------------------------------------------------- #
class GBoundaries(CadIrCase):
    def package_sources(self):
        directory = ROOT / "tech_app" / "backend" / "services" / "cad_ir"
        if not directory.is_dir():
            self.fail("缺少 tech_app/backend/services/cad_ir/（本批 Spec §2）")
        return {path: path.read_text(encoding="utf-8", errors="replace")
                for path in sorted(directory.rglob("*.py"))}

    def package_code(self):
        """只有代码本体：注释和字符串字面量先剔掉。

        Spec §11 禁的是「引用/调用」模型与 3D 链路，不是禁止在文档里提到它们；
        所以在**代码**上判定，避免"注释里写了 vision 就算违规"的假失败。
        """
        import io
        import tokenize
        code = {}
        for path, text in self.package_sources().items():
            pieces = []
            try:
                for token in tokenize.generate_tokens(io.StringIO(text).readline):
                    if token.type in (tokenize.COMMENT, tokenize.STRING):
                        continue
                    pieces.append(token.string)
            except tokenize.TokenError:
                self.fail("%s 无法分词，源码可能不完整" % path.name)
            code[path] = " ".join(pieces)
        return code

    def test_g1_cad_ir_never_touches_models_or_step_pipeline(self):
        offenders = []
        for path, text in self.package_code().items():
            for banned in ("vision", "qwen_client", "claude_client", "step_import",
                           "models.ir", "DesignIR", "services.geometry", "drawing2d"):
                if banned in text:
                    offenders.append("%s:%s" % (path.name, banned))
        self.assertEqual(offenders, [], "CAD IR 必须与模型/DesignIR/3D 链路隔离（Spec §11）：%s"
                         % offenders)

    def test_g2_cad_ir_uses_its_own_document_slot(self):
        store = importlib.import_module("tech_app.backend.storage.store")
        self.assertIn("cad_ir", getattr(store, "PARSE_STAGE_DOCS", ()),
                      "「本次任务从头开始」必须能清掉 CAD IR（Spec §6.4）")
        offenders = [path.name for path, text in self.package_code().items()
                     if "store.save_ir(" in text or "store.load_ir(" in text]
        self.assertEqual(offenders, [],
                         "CAD IR 不得占用 DesignIR 的 save_ir/load_ir（Spec §11）：%s" % offenders)


# --------------------------------------------------------------------------- #
# H. 版本与摘要
# --------------------------------------------------------------------------- #
class HVersioning(CadIrCase):
    def test_h1_ir_carries_the_contract_version(self):
        package = self.cad_ir()
        self.assertIsInstance(package.CAD_IR_VERSION, str)
        self.assertTrue(package.CAD_IR_VERSION)
        ir = self.parse("rect_10x5.dxf")
        self.assertEqual(ir["ir_version"], package.CAD_IR_VERSION)
        self.assertTrue(ir.get("ir_id"))
        self.assertEqual(len(str(ir["ir_hash"])), 64)

    def test_h2_migrate_refuses_to_guess(self):
        package = self.cad_ir()
        outdated = {"ir_version": None, "layers": []}
        outdated.pop("ir_version")
        self.assertEqual(package.migrate(outdated).get("status"), "needs_rebuild")
        current = self.parse("rect_10x5.dxf")
        self.assertEqual(package.migrate(current).get("status"), "ok")
        with self.assertRaises(ValueError):
            package.migrate({"ir_version": "cad-ir/999", "layers": []})

    def test_h3_summarize_is_small_and_safe(self):
        package = self.cad_ir()
        ir = self.parse("layers_cut_crease.dxf")
        summary = package.summarize(ir)
        blob = self.json_safe(summary)
        self.assertLess(len(blob), 4000, "摘要必须很小，不许把实体明细塞进 Agent 上下文（Spec §7）")
        self.assertEqual(summary.get("ir_id"), ir["ir_id"])
        self.assertIn("stats", summary)
        self.assertNotIn("entities", summary)


# --------------------------------------------------------------------------- #
# I. 幂等与回看
# --------------------------------------------------------------------------- #
class IIdempotency(CadIrCase):
    def test_i1_reparsing_the_same_dxf_is_idempotent(self):
        state = self.memory_persistence()
        self.fake_conversion()
        first = self.parse_conversion("cad-ir-project")
        second = self.parse_conversion("cad-ir-project")
        self.assertEqual(first["ir_id"], second["ir_id"])
        self.assertEqual(first["ir_hash"], second["ir_hash"])
        self.assertEqual(len(self.cad_ir().list_irs("cad-ir-project")), 1,
                         "同一份 DXF 重复解析不得产生重复版本（Spec §6.2）")
        self.assertEqual(len(state["saved"]), 1)

    def test_i2_ir_can_be_looked_up_and_unknown_ids_return_none(self):
        self.memory_persistence()
        self.fake_conversion()
        ir = self.parse_conversion("cad-ir-project")
        loaded = self.cad_ir().load_ir("cad-ir-project", ir["ir_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["ir_hash"], ir["ir_hash"])
        self.assertIsNone(self.cad_ir().load_ir("cad-ir-project", "no-such-ir"))



if __name__ == "__main__":
    unittest.main(verbosity=2)
