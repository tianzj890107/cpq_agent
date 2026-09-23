# -*- coding: utf-8 -*-
"""件名里的 MTEXT 格式码必须被剥掉（红测）。

对应 Spec：`docs/specs/packaging-part-name-mtext-codes.md`
编号（A/B/C/D/E）与该 Spec §2 各条契约逐条对应。

本批现场结论（只读实测，HEAD `0312614` 工作副本）：
  · `圆盘盒.dwg` 66 个业务部件里 **39 个件名带 MTEXT 格式码**（`\\C1;地盒内圈衬纸2`、
    `\\fSimSun|b1|i0|c134|p2; BC坑`、`\\fArial|b0|i0|c0|p34;\\W1;402X50.5MM…`）—— 见 Spec §1；
  · 根因是 `_strip_mtext_codes()` 只剥**带花括号**的 `{\\C0;…}`；
  · 其中一条是镜像重复件（`ent:model:8C665` / `8D60F`，件名与材料逐字相同）被格式码拆成两件，
    归并后圆盘盒 66 → 65 行（Spec §5，本批 supersede 那份 Spec 的 39 这一格）。

纪律：
  · 只读：读仓库内的真样本 DXF / 调解析器，不连库、不起服务、不写业务数据；
  · 禁止为了让红测转绿而放宽断言或改期望值。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "tech_app/data/cad-ir-realsample/conversions"

#: 真样本：conversion_id 与 DWG 的 sha-256（取自转换 manifest，与其它红测同一份口径）。
SAMPLES = {
    "酒盒": ("47c39dc1ab6738fc48c8",
             "0991c8b0a9646d1fea6571d2ae6155923351c05ca2aeeb6ed544ef19df93f3e0"),
    "圆盘盒": ("a6140fbc4e9b8d2e9bee",
               "4c70ce7b3774c2a1803a942341a606c92758228cf982c3bf1016642be44a531b"),
}

#: MTEXT 控制序列（Spec §2.1）：`\C1;` / `\c2367469;` / `\fSimSun|b0|i0|c134|p2;` / `\W1;` / `\H1.5x;`。
CONTROL = re.compile(r"\\[A-Za-z][^;\\]*;")

#: 改前带码、改后必须干净的件名（Spec §1 的逐字对照表）。
DIRTY_TO_CLEAN = {
    "圆盘盒": (
        ("{\\fSimSun|b0|i0|c134|p2;\\C1;地盒内圈衬纸2}", "地盒内圈衬纸2"),
        ("\\fSimSun|b1|i0|c134|p2; BC坑", "BC坑"),
        ("\\C1;10PC圆盒 内托面卡：350g单粉 420*805mm 排2模 2024-04-06", "10PC圆盒 内托面卡"),
    ),
}

#: 镜像重复件（Spec §5）：两条原文只是 MTEXT run 切法不同，件名与材料逐字相同。
MIRROR_PAIR = (
    "\\C240;\\c2367469;1\\C1;0PC圆盒 盖/地内外圈围边衬纸1/2：250g双铜 860*460mm 各排2模 2024-04-06",
    "\\C240;\\c2367469;\\C1;10PC圆盒 盖/地内外圈围边衬纸1/2：250g双铜 860*460mm 各排2模 2024-04-06",
)

#: 改后的镜部件名（两条原文归一成这一个）。
MIRROR_NAME = "10PC圆盒 盖/地内外圈围边衬纸1/2"


def _resolver():
    from tech_app.backend.services import packaging_business_part_resolver as module
    return module


def _require_sample(test: unittest.TestCase, label: str):
    conversion_id, _ = SAMPLES[label]
    if not (SAMPLE_ROOT / conversion_id / "converted.dxf").exists():
        test.skipTest("缺少真样本 DXF（%s）" % conversion_id)


_IR_CACHE: dict = {}
_OUT_CACHE: dict = {}


def _real_ir(label: str):
    if label in _IR_CACHE:
        return _IR_CACHE[label]
    from tech_app.backend.services import cad_ir
    conversion_id, drawing_sha = SAMPLES[label]
    path = SAMPLE_ROOT / conversion_id / "converted.dxf"
    ir = cad_ir.parse_dxf(path.read_bytes(), filename="%s.dxf" % label,
                          source={"source_sha256": drawing_sha,
                                  "original_filename": "%s.dwg" % label})
    _IR_CACHE[label] = ir
    return ir


def _rows(label: str) -> list:
    if label not in _OUT_CACHE:
        out = _resolver().resolve_business_parts("red-test", _real_ir(label), None)
        _OUT_CACHE[label] = [row for row in (out.get("authority_rows") or [])
                             if isinstance(row, dict)]
    return _OUT_CACHE[label]


def _names(label: str) -> list:
    return [str(row.get("name") or "") for row in _rows(label)]


def _texts(label: str) -> list:
    return [str(row.get("raw_text") or "") for row in (_real_ir(label).get("texts") or [])]


# --------------------------------------------------------------------------- #
# A 组：圆盘盒真样本 —— 件名出图纸就是干净的（Spec §2.1/§2.4）
# --------------------------------------------------------------------------- #
class RealSamplePartNamesAreCleanRed(unittest.TestCase):
    def test_a1_no_part_name_contains_a_backslash(self):
        _require_sample(self, "圆盘盒")
        dirty = [name for name in _names("圆盘盒") if "\\" in name]
        self.assertEqual([], dirty,
                         "圆盘盒：件名里还留着 MTEXT 格式码（Spec §2.1）：%r" % dirty[:3])

    def test_a2_no_part_name_matches_a_control_sequence(self):
        _require_sample(self, "圆盘盒")
        dirty = [name for name in _names("圆盘盒") if CONTROL.search(name)]
        self.assertEqual([], dirty,
                         "圆盘盒：件名里还有 \\C…; / \\f…; / \\W…; 这类控制序列（Spec §2.1）：%r"
                         % dirty[:3])

    def test_a3_dirty_forms_become_clean_names(self):
        _require_sample(self, "圆盘盒")
        names = _names("圆盘盒")
        for raw, clean in DIRTY_TO_CLEAN["圆盘盒"]:
            name, _, _ = _resolver().split_label_parts(raw)
            self.assertEqual(clean, name,
                             "同一份原文的件名必须逐字是 %r（Spec §1）：%r → %r" % (clean, raw, name))
            self.assertIn(clean, names,
                          "%r 必须在圆盘盒的件名清单里（Spec §1）" % clean)

    def test_a4_row_count_merges_the_mirror_duplicate(self):
        _require_sample(self, "圆盘盒")
        self.assertEqual(65, len(_rows("圆盘盒")),
                         "圆盘盒行数：格式码造的假件归并回镜像后是 65 行（Spec §5；改前 66）")

    def test_a5_the_mirror_pair_is_one_part(self):
        _require_sample(self, "圆盘盒")
        resolver = _resolver()
        names = []
        for raw in MIRROR_PAIR:
            name, material, _ = resolver.split_label_parts(raw)
            self.assertEqual(MIRROR_NAME, name,
                             "镜像还原文的件名必须逐字相同（Spec §5）：%r → %r" % (raw, name))
            self.assertIn("250g双铜 860*460mm 各排2模", material,
                          "镜像还原文的材料必须逐字相同（Spec §5）")
            names.append(name)
        self.assertEqual(1, len(set(names)), "两条原文必须是同一件（Spec §5）")
        self.assertEqual(1, _names("圆盘盒").count(MIRROR_NAME),
                         "%r 在件名清单里只许出现一次（Spec §5）" % MIRROR_NAME)

    def test_a6_kept_anchors_carry_no_codes(self):
        _require_sample(self, "圆盘盒")
        anchors = _resolver().extract_text_anchors(_real_ir("圆盘盒"))
        kept = [item for item in anchors if not item.get("excluded")]
        dirty = [item.get("name") for item in kept if CONTROL.search(item.get("name") or "")]
        self.assertEqual([], dirty,
                         "圆盘盒：kept 锚点的件名里还有格式码（改前 84 条，Spec §1）：%r" % dirty[:3])


# --------------------------------------------------------------------------- #
# B 组：酒盒不许被本批改动（Spec §2.5）
# --------------------------------------------------------------------------- #
class WineBoxMustNotRegress(unittest.TestCase):
    def test_b1_row_count_is_unchanged(self):
        _require_sample(self, "酒盒")
        self.assertEqual(28, len(_rows("酒盒")), "酒盒行数不许变（Spec §2.5）")

    def test_b2_derived_count_is_unchanged(self):
        _require_sample(self, "酒盒")
        derived = [row for row in _rows("酒盒") if str(row.get("status") or "") == "derived"]
        self.assertEqual(26, len(derived), "酒盒 derived 件数不许变（Spec §2.5）")

    def test_b3_names_have_no_codes(self):
        _require_sample(self, "酒盒")
        self.assertEqual([], [name for name in _names("酒盒") if CONTROL.search(name)],
                         "酒盒件名本来就是干净的，不许被本批弄脏（Spec §2.5）")

    def test_b4_line_break_form_still_splits(self):
        _require_sample(self, "酒盒")
        raws = [raw for raw in _texts("酒盒") if raw.startswith("名称：")]
        self.assertTrue(raws, "酒盒真样本里应当有「名称：…\\P材料：…」这种写法（Spec §2.2）")
        self.assertTrue(any("\\P" in raw for raw in raws),
                        "酒盒真样本的折行符就是 \\P（Spec §2.2）")


# --------------------------------------------------------------------------- #
# C 组：`\P` 折行与全角 `；`（Spec §2.2/§2.3）
# --------------------------------------------------------------------------- #
class ParagraphBreakIsNotAFormatCodeRed(unittest.TestCase):
    """`\P` 是折行不是格式码 —— 半角/全角分号都不许让它后面的正文被吞掉。"""

    def test_c1_half_width_semicolon_after_line_break(self):
        name, material, _ = _resolver().split_label_parts(
            "内托支撑折板\\P250G白卡纸;860*500=40M")
        self.assertEqual("内托支撑折板", name,
                         "`\\P…;` 不许把折行后的正文并进件名（Spec §2.2）")
        self.assertIn("250G白卡纸", material, "折行后的材料必须留着（Spec §2.2）")

    def test_c2_full_width_semicolon_after_line_break(self):
        name, material, _ = _resolver().split_label_parts(
            "内托支撑折板\\P250G白卡纸；860*500=40M")
        self.assertEqual("内托支撑折板", name,
                         "全角 `；` 也不许让 `\\P…；` 被当成格式码（Spec §2.3）")
        self.assertIn("250G白卡纸", material, "折行后的材料必须留着（Spec §2.3）")

    def test_c3_real_sample_style_label_still_splits(self):
        name, material, process = _resolver().split_label_parts(
            "内托支撑围条灰板\\P650G灰板\\P正面图，啤面")
        self.assertEqual("内托支撑围条灰板", name, "件名只取第一段（Spec §2.2）")
        self.assertEqual("650G灰板", material, "第二段是材料（Spec §2.2）")
        self.assertEqual("啤面", process, "视图标题既不进材料也不进工艺（Spec §2.2）")

    def test_c4_colon_form_still_splits(self):
        name, material, _ = _resolver().split_label_parts(
            "名称：左盖面纸\\P材料：225G铜版底PET光银")
        self.assertEqual("左盖面纸", name, "老口径不许回退（Spec §2.2）")
        self.assertEqual("225G铜版底PET光银", material, "老口径不许回退（Spec §2.2）")


# --------------------------------------------------------------------------- #
# D 组：带花括号的老口径不许回退（Spec §2.1）
# --------------------------------------------------------------------------- #
class BracedFormMustNotRegress(unittest.TestCase):
    def test_d1_braced_font_and_color(self):
        name, _, _ = _resolver().split_label_parts("{\\fSimSun|b0|i0|c134|p2;\\C1;地盒内圈衬纸2}")
        self.assertEqual("地盒内圈衬纸2", name, "花括号 + 字体码 + 颜色码全剥（Spec §2.1）")

    def test_d2_unterminated_braced_color(self):
        name, _, _ = _resolver().split_label_parts("{\\C0;旧款大货色位不够")
        self.assertEqual("旧款大货色位不够", name, "未闭合的花括号形式也要剥（Spec §2.1）")

    def test_d3_braced_font_with_cjk_name(self):
        name, _, _ = _resolver().split_label_parts("{\\fKaiTi|b0|i0|c134|p49;纸张纹路}")
        self.assertEqual("纸张纹路", name, "花括号 + 楷体码（真样本 圆盘盒 有 28 条）（Spec §2.1）")


# --------------------------------------------------------------------------- #
# E 组：口径护栏（Spec §2.1/§2.4/§2.5）
# --------------------------------------------------------------------------- #
class CaliberGuards(unittest.TestCase):
    def test_e1_control_regex_declines_paragraph_breaks(self):
        module = _resolver()
        pattern = getattr(module, "_MTEXT_CODES", None)
        self.assertIsNotNone(pattern, "必须把控制序列正则提成模块级 `_MTEXT_CODES`（Spec §2.2）")
        for raw in ("\\P650G灰板", "\\PBC坑 K=K", "\\P正面图", "\\p正面图"):
            self.assertIsNone(pattern.match(raw),
                              "`\\P` / `\\p` 是折行，不是格式码（Spec §2.2）：%r" % raw)
        for raw in ("\\C1;地盒内圈衬纸2", "\\fSimSun|b0|i0|c134|p2; BC坑", "\\W1;113X49.5MM"):
            self.assertIsNotNone(pattern.match(raw),
                                 "真格式码必须仍然被匹配（Spec §2.1）：%r" % raw)

    def test_e2_real_sample_line_breaks_are_preserved(self):
        _require_sample(self, "圆盘盒")
        with_break = [raw for raw in _texts("圆盘盒") if "\\P" in raw]
        self.assertTrue(with_break, "圆盘盒真样本里应当有 `\\P` 折行（Spec §2.2）")
        for raw in with_break:
            kept = _resolver()._strip_mtext_codes(raw)
            self.assertIn("\\P", kept, "`\\P` 折行不许被剥掉（Spec §2.2）：%r" % raw)

    def test_e3_material_and_process_text_have_no_codes(self):
        _require_sample(self, "圆盘盒")
        for field in ("material_text", "process_text"):
            dirty = [str(row.get(field) or "") for row in _rows("圆盘盒")
                     if CONTROL.search(str(row.get(field) or ""))]
            self.assertEqual([], dirty, "%s 不许带格式码（Spec §2.5）：%r" % (field, dirty[:3]))

    def test_e4_caliber_matches_the_ir_layer(self):
        _require_sample(self, "圆盘盒")
        strip = _resolver()._strip_mtext_codes
        dirty = [raw for raw in _texts("圆盘盒") if CONTROL.search(strip(raw))]
        self.assertEqual([], dirty,
                         "剥完不许再留控制序列 —— IR 层的 normalized_text 早就是这条口径（Spec §2.4）：%r"
                         % dirty[:3])

    def test_e5_status_and_matcher_untouched(self):
        module = _resolver()
        self.assertEqual(("dwg", "missing"), tuple(module.AUTHORITY_SOURCES),
                         "来源闭集不许被本批改动（Spec §2.5）")
        self.assertTrue(module.VIEW_MARKERS and module.PROCESS_MARKERS,
                        "视图/工艺标记表不许被本批清空（Spec §2.5）")


if __name__ == "__main__":
    unittest.main()
