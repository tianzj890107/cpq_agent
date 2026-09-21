"""红测：统一解析服务的字段形状 → 报价侧匹配输入 —— 逆向快速报价第 9 批。

Spec：`docs/specs/quick-quote-9-parse-field-alignment.md`
依赖：批 5 客户端 `cpq_quick_quote_file.py`、批 7 服务 `unified_parse.py`（两者都已实现）。

现状缺口（`## 250` 在 34 上真跑出来的，不是推断）：

```
parse_file kind=drawing layers=8 dims=316 texts=127 v_groove=True     ← 解析是对的
to_match_inputs -> {"inner_width": 14362.15, "inner_height": 6151.80, "v_groove": true}
```

`14362×6152` 是**整张图纸的幅面**（服务按 Spec 批 7 §2.4 标了 `source="document_extents"`），
被批 5 客户端当成了盒子的内宽 / 内高；而 `annotated_dimensions` 是服务按 Spec 回的**裸实测值**
（没有轴名），客户端一个都用不上且不说原因。两条口径各自都对，接起来才出问题。

纪律：
  · 全部离线：不连库、不联网、不真转图纸；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE = "cpq_quick_quote_file"
SIZE_KEYS = ("inner_length", "inner_width", "inner_height")

#: 批 7 服务在真样本上的真实形状（`## 250` 的 34 复验输出，数值逐字照抄）。
SERVICE_FIELDS = {
    "units": "mm",
    "layers": ["0", "CUTTER", "DESIGN"],
    "annotated_dimensions": [14362.14786672, 6151.798535235, 396.5, 47.0],
    "text_annotations": ["235g白卡底PET光银裱A9 E坑", "V slot(V槽）"],
    "material_notes": ["235g白卡底PET光银裱A9 E坑"],
    "v_groove": True,
    "outline_size": {"width": 14362.14786672, "height": 6151.798535235,
                     "source": "document_extents"},
}

#: 只带图纸幅面、没有任何可信尺寸的最小形状。
SHEET_ONLY_FIELDS = {
    "units": "mm",
    "outline_size": {"width": 14362.14786672, "height": 6151.798535235,
                     "source": "document_extents"},
}


def load():
    try:
        return importlib.import_module(MODULE)
    except Exception:                                          # noqa: BLE001
        return None


class Base(unittest.TestCase):
    maxDiff = None

    def module(self):
        module = load()
        if module is None:
            self.fail("%s 不存在（批 5）" % MODULE)
        return module

    def mapped(self, fields, **kwargs):
        return self.module().to_match_inputs({"kind": "drawing", "fields": dict(fields)}, **kwargs)

    def warnings_of(self, mapped):
        return " ".join(str(item) for item in (mapped.get("warnings") or []))


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANaming(Base):
    def test_a1_sheet_size_sources_is_declared(self):
        module = self.module()
        self.assertTrue(hasattr(module, "SHEET_SIZE_SOURCES"),
                        "必须有 SHEET_SIZE_SOURCES（Spec 批 9 §1.1）")
        self.assertIn("document_extents", tuple(module.SHEET_SIZE_SOURCES))

    def test_a2_dimensions_helper_takes_warnings(self):
        module = self.module()
        helper = getattr(module, "_dimensions", None)
        self.assertTrue(callable(helper), "_dimensions 必须可调用")
        warnings = []
        out = helper({"outline_size": {"width": 10.0, "height": 5.0,
                                       "source": "document_extents"}}, 1.0, warnings)
        self.assertEqual({}, out, "图纸幅面不得产出任何内尺寸（Spec §1.2 第 1 条）")
        self.assertTrue(warnings, "拒绝幅面时必须记一条 warning")

    def test_a3_to_match_inputs_shape_unchanged(self):
        out = self.mapped(SHEET_ONLY_FIELDS)
        for key in ("inputs", "missing", "sources", "warnings", "units_factor",
                    "match_input_keys"):
            self.assertIn(key, out, "出参形状不许改（Spec §1.3）")


# --------------------------------------------------------------------------- #
# B 组：图纸幅面必须被拒
# --------------------------------------------------------------------------- #
class TestBSheetSizeIsRejected(Base):
    def test_b1_no_inner_size_from_extents(self):
        out = self.mapped(SHEET_ONLY_FIELDS)
        for key in SIZE_KEYS:
            self.assertNotIn(key, out["inputs"],
                             "%s 不得来自图纸幅面（Spec §1.2 第 1 条）" % key)

    def test_b2_missing_records_the_gap(self):
        out = self.mapped(SHEET_ONLY_FIELDS)
        for key in SIZE_KEYS:
            self.assertIn(key, out["missing"],
                          "%s 没拿到就必须如实进 missing（Spec §1.3）" % key)
            self.assertNotIn(key, out["sources"])

    def test_b3_warning_explains_document_extents(self):
        out = self.mapped(SHEET_ONLY_FIELDS)
        joined = self.warnings_of(out)
        self.assertIn("图纸范围", joined,
                      "要讲清这是整张图的幅面、不是内尺寸（Spec §1.2 第 1 条）")

    def test_b4_sheet_numbers_never_leak_into_inputs(self):
        out = self.mapped(dict(SHEET_ONLY_FIELDS,
                               layers=["0"],
                               text_annotations=["灰板 1200g"]))
        leaked = [key for key, value in out["inputs"].items()
                  if isinstance(value, (int, float)) and not isinstance(value, bool)
                  and abs(float(value) - 14362.14786672) < 1e-6]
        self.assertEqual([], leaked, "幅面数值不得换个键名塞进 inputs")


# --------------------------------------------------------------------------- #
# C 组：不要误伤
# --------------------------------------------------------------------------- #
class TestCNoRegresssionOnOtherSources(Base):
    def test_c1_outline_without_source_still_maps(self):
        out = self.mapped({"units": "mm",
                           "outline_size": {"length": 210.0, "width": 160.0, "height": 85.0}})
        self.assertAlmostEqual(210.0, float(out["inputs"]["inner_length"]), places=6)
        self.assertAlmostEqual(160.0, float(out["inputs"]["inner_width"]), places=6)
        self.assertAlmostEqual(85.0, float(out["inputs"]["inner_height"]), places=6)

    def test_c2_other_sources_still_map(self):
        out = self.mapped({"units": "mm",
                           "outline_size": {"length": 210.0, "width": 160.0, "height": 85.0,
                                            "source": "product_outline"}})
        self.assertAlmostEqual(210.0, float(out["inputs"]["inner_length"]), places=6)

    def test_c3_units_still_converted(self):
        out = self.mapped({"units": "cm",
                           "outline_size": {"length": 21.0, "width": 16.0, "height": 8.5}})
        self.assertAlmostEqual(210.0, float(out["inputs"]["inner_length"]), places=6)

    def test_c4_annotated_axis_still_wins_over_outline(self):
        out = self.mapped({"units": "mm",
                           "annotated_dimensions": [{"axis": "inner_length", "value": 200.0}],
                           "outline_size": {"length": 999.0, "width": 160.0}})
        self.assertAlmostEqual(200.0, float(out["inputs"]["inner_length"]), places=6,
                               msg="标注内尺寸优先于外形尺寸（批 5 §2.4 第 2 条不回退）")


# --------------------------------------------------------------------------- #
# D 组：裸数字标注不许猜轴
# --------------------------------------------------------------------------- #
class TestDBareMeasuredValues(Base):
    def test_d1_bare_numbers_are_not_used(self):
        out = self.mapped({"units": "mm", "annotated_dimensions": [219.6435, 90.0]})
        for key in SIZE_KEYS:
            self.assertNotIn(key, out["inputs"], "裸数字没有轴名，不许当内尺寸（Spec §1.2 第 2 条）")

    def test_d2_warning_explains_missing_axis(self):
        out = self.mapped({"units": "mm", "annotated_dimensions": [219.6435, 90.0]})
        joined = self.warnings_of(out)
        self.assertIn("轴", joined, "要说清服务只回实测值、没有轴名（Spec §1.2 第 2 条）")

    def test_d3_axis_tagged_dicts_still_work(self):
        out = self.mapped({"units": "mm",
                           "annotated_dimensions": [{"axis": "inner_length", "value": 200.0},
                                                    {"axis": "inner_width", "value": 150.0},
                                                    90.0],
                           "outline_size": {"length": 999.0, "width": 999.0, "height": 80.0}})
        self.assertAlmostEqual(200.0, float(out["inputs"]["inner_length"]), places=6)
        self.assertAlmostEqual(150.0, float(out["inputs"]["inner_width"]), places=6)
        self.assertNotIn("inner_length", out["missing"])


# --------------------------------------------------------------------------- #
# E 组：批 7 服务的真实形状
# --------------------------------------------------------------------------- #
class TestEServiceShape(Base):
    def test_e1_no_size_from_service_extents(self):
        out = self.mapped(SERVICE_FIELDS)
        for key in SIZE_KEYS:
            self.assertNotIn(key, out["inputs"], "服务回的是图纸幅面 + 裸实测值（Spec §0）")
            self.assertIn(key, out["missing"])

    def test_e2_other_fields_still_map(self):
        out = self.mapped(SERVICE_FIELDS)
        self.assertIs(True, out["inputs"]["v_groove"])
        self.assertEqual("parse", out["sources"]["v_groove"])
        # 材料克重：真图那两条标注是「白卡 / 铜版 + 大写 G」，客户端今天只认「灰板 / 面纸 + 小写 g」，
        # 所以这里不断言真图的克重（那是客户端关键词口径的另一个缺口，不在本批范围）；
        # 只证明**克重这条通路没被本批改动打坏**。
        out2 = self.mapped(dict(SERVICE_FIELDS, material_notes=["面纸 250g 铜版纸"]))
        self.assertAlmostEqual(250.0, float(out2["inputs"]["face_paper_gsm"]), places=6)

    def test_e3_fallback_still_fills_the_size_gap(self):
        out = self.mapped(SERVICE_FIELDS, fallback={"inner_length": 200, "inner_width": 150,
                                                   "inner_height": 80})
        self.assertAlmostEqual(200.0, float(out["inputs"]["inner_length"]), places=6)
        self.assertEqual("fallback", out["sources"]["inner_length"])


# --------------------------------------------------------------------------- #
# F 组：批 5 护栏（逐条不回退）
# --------------------------------------------------------------------------- #
class TestFBatch5Guardrails(Base):
    def test_f1_missing_units_still_warns(self):
        out = self.mapped({"units": "", "outline_size": {"length": 210.0, "width": 160.0,
                                                         "height": 85.0}})
        self.assertAlmostEqual(210.0, float(out["inputs"]["inner_length"]), places=6)
        self.assertTrue(out["warnings"], "单位缺失要记 warning（批 5 e10）")

    def test_f2_unparsed_keys_stay_missing(self):
        out = self.mapped(SERVICE_FIELDS)
        for key in ("quantity", "box_type", "inner_length"):
            self.assertIn(key, out["missing"])
            self.assertNotIn(key, out["inputs"])

    def test_f3_conflict_warning_still_emitted(self):
        out = self.mapped({"units": "mm",
                           "outline_size": {"length": 210.0, "width": 160.0, "height": 85.0}},
                          fallback={"inner_length": 999})
        joined = self.warnings_of(out)
        self.assertIn("inner_length", joined, "解析值与 fallback 冲突仍要记 warning（批 5 §2.4 第 4 条）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
