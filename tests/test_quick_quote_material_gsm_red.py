"""红测：材料克重的单位与纸种口径 —— 逆向快速报价第 10 批。

Spec：`docs/specs/quick-quote-10-material-gsm-attribution.md`
依赖：批 5 客户端 `cpq_quick_quote_file.py`（已实现）、批 7 服务 `unified_parse.py`（已实现）。

现状缺口（`## 251.1` 在 34 上真跑出来的，不是推断）：

```
酒盒.dwg 材料标注：235g白卡底PET光银裱A9 E坑 / 名称：左盖面纸\\n材料：225G铜版底PET光银 …
34 上客户端：inputs={"v_groove": true}，face_paper_gsm / grey_board_gsm 全进 missing，
             warnings=[…, "材料标注里读出了克重，但分不清是面纸还是灰板：请人工确认"]
```

三条独立原因（Spec §0）：单位只认小写 `g`（真图写 `225G`）、纸种词表缺真图用词
（`白卡/铜版/单粉/双灰`）、取值与纸种不配对（`search()` 只取第一个数字 + 任一词命中）。

纪律：
  · A–D 组全离线（只喂 `material_notes`，不联网、不连库、不转图）；
  · E 组是本机真实样本（`裕同包装项目-待开发/`），没有转换器就 skip，不是失败；
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
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = SAMPLES_DIR / "酒盒.dwg"          # 酒盒.dwg
ROUND_BOX = SAMPLES_DIR / "圆盘盒.dwg"        # 圆盘盒.dwg

#: 批 5 既有文案（Spec C4 要求逐字沿用，不许改措辞）。
AMBIGUOUS_WARNING = "材料标注里读出了克重，但分不清是面纸还是灰板：请人工确认"

FACE = "face_paper_gsm"
GREY = "grey_board_gsm"
GSM_KEYS = (FACE, GREY)


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

    def gsm(self, notes, **kwargs):
        """只喂 material_notes，回 {"face": …, "grey": …}（缺失的键不出现）。"""
        fields = {"units": "mm", "material_notes": list(notes)}
        mapped = self.module().to_match_inputs({"kind": "drawing", "fields": fields}, **kwargs)
        inputs = mapped["inputs"]
        return {key: inputs[key] for key in GSM_KEYS if key in inputs}

    def warnings_text(self, notes):
        fields = {"units": "mm", "material_notes": list(notes)}
        mapped = self.module().to_match_inputs({"kind": "drawing", "fields": fields})
        return [str(item) for item in (mapped.get("warnings") or [])]


# --------------------------------------------------------------------------- #
# A 组：单位识别（大写 / gsm / 克 / g-m2 都要认；mm 厚度绝不许当克重）
# --------------------------------------------------------------------------- #
class TestAUnits(Base):
    def test_a1_lowercase_g(self):
        self.assertEqual({FACE: 235.0}, self.gsm(["235g面纸底PET光银裱A9 E坑"]),
                         "小写 g + 既有词表（面纸）：批 5 基线，不许打回")

    def test_a2_uppercase_g(self):
        self.assertEqual({FACE: 225.0}, self.gsm(["名称：左盖面纸材料：225G铜版底PET光银"]),
                         "中文图纸写「225G」：大写 G 必须当克重（Spec C1）")

    def test_a3_uppercase_gsm(self):
        self.assertEqual({GREY: 1200.0}, self.gsm(["灰板1200GSM"]))

    def test_a4_chinese_unit(self):
        self.assertEqual({FACE: 200.0}, self.gsm(["面纸200克铜版纸"]))

    def test_a5_gram_per_square_meter(self):
        self.assertEqual({FACE: 200.0}, self.gsm(["面纸 200 g/m²"]))

    def test_a6_thickness_is_not_grammage(self):
        notes = ["底板：2.5MM灰板", "1.8mm 灰板裱光银纸", "名称：顶托EVA材料：35mm厚EVA",
                 "名称：顶托灰板材料：2mm灰板2mm灰板对裱11层=22mm"]
        self.assertEqual({}, self.gsm(notes), "mm 厚度不是克重（Spec C1）")
        for text in self.warnings_text(notes):
            self.assertNotIn("读出了克重", text, "厚度误判才会报克重歧义")

    def test_a7_space_before_unit(self):
        self.assertEqual({FACE: 300.0}, self.gsm(["面纸 300 G 白卡"]))


# --------------------------------------------------------------------------- #
# B 组：纸种词表（真图用词；有歧义的「粉灰」故意不登记）
# --------------------------------------------------------------------------- #
class TestBVocabulary(Base):
    def test_b1_baika_is_face(self):
        self.assertEqual({FACE: 235.0}, self.gsm(["235g白卡底PET光银"]))

    def test_b2_tongban_is_face(self):
        self.assertEqual({FACE: 200.0}, self.gsm(["200G铜版纸/PP-36M"]))

    def test_b3_danfen_is_face(self):
        self.assertEqual({FACE: 350.0}, self.gsm(["10PC圆盒 内托面卡：350g单粉 420*805mm 排2模"]))

    def test_b4_shuanghui_is_grey(self):
        self.assertEqual({GREY: 1100.0}, self.gsm(["底盒底板1，1100G双灰板"]))

    def test_b5_quanhui_is_grey(self):
        self.assertEqual({GREY: 1000.0}, self.gsm(["围板1000g全灰板"]))

    def test_b6_huika_is_grey(self):
        self.assertEqual({GREY: 900.0}, self.gsm(["底板900G灰卡"]))

    def test_b7_fenhui_stays_ambiguous(self):
        out = self.gsm(["350g粉灰"])
        self.assertEqual({}, out, "「粉灰」两种口径都讲得通：不许猜（Spec C2）")
        self.assertIn(AMBIGUOUS_WARNING, self.warnings_text(["350g粉灰"]))

    def test_b8_dangerous_single_chars_not_in_table(self):
        module = self.module()
        words = {str(word) for word in tuple(module._FACE_WORDS) + tuple(module._GREY_WORDS)}
        for word in ("纸", "卡", "板", "灰", "坑"):
            self.assertNotIn(word, words, "会把任意文字判成纸种（Spec C2）：%r" % (word,))

    def test_b9_real_drawing_words_registered(self):
        module = self.module()
        face = {str(word) for word in module._FACE_WORDS}
        grey = {str(word) for word in module._GREY_WORDS}
        for word in ("白卡", "铜版", "单粉"):
            self.assertIn(word, face, "真图面纸用词必须在面纸桶里（Spec C2）：%s" % word)
        for word in ("双灰", "全灰", "灰卡"):
            self.assertIn(word, grey, "真图灰板用词必须在灰板桶里（Spec C2）：%s" % word)


# --------------------------------------------------------------------------- #
# C 组：逐值配对（不许"第一个数字 + 任一词命中"）
# --------------------------------------------------------------------------- #
class TestCPairing(Base):
    def test_c1_value_then_word(self):
        self.assertEqual({FACE: 225.0}, self.gsm(["面纸225G铜版底PET光银"]))

    def test_c2_word_then_value(self):
        self.assertEqual({GREY: 1200.0}, self.gsm(["灰板 1200gsm"]))

    def test_c3_one_note_two_papers(self):
        self.assertEqual({FACE: 250.0, GREY: 1200.0}, self.gsm(["衬纸250g白卡裱1200g双灰"]),
                         "一条标注两个纸种：250→白卡、1200→双灰（Spec C3）")

    def test_c4_real_round_box_note(self):
        note = "内托底垫板灰板内衬裱卡：衬纸250g白卡裱800g双灰 810*435mm 排2模"
        self.assertEqual({FACE: 250.0, GREY: 800.0}, self.gsm([note]))

    def test_c5_following_word_wins_over_preceding(self):
        self.assertEqual({FACE: 250.0}, self.gsm(["灰板底衬纸250g白卡"]),
                         "紧跟在值之后的纸种词优先（Spec C3.1）")

    def test_c6_first_attributed_value_wins(self):
        self.assertEqual({FACE: 250.0},
                         self.gsm(["面纸 250g 铜版纸", "面纸300G白卡/哑PP"]),
                         "同一个桶里先到先得（沿用批 5 setdefault 口径）")

    def test_c7_preceding_word_covers_later_values(self):
        out = self.gsm(["灰板 1200g 1500g"])
        self.assertEqual({GREY: 1200.0}, out,
                         "两个值都归属灰板，同桶先到先得（Spec C3.2/C3）")
        self.assertNotIn(AMBIGUOUS_WARNING, self.warnings_text(["灰板 1200g 1500g"]),
                         "能归属就不许报歧义")


# --------------------------------------------------------------------------- #
# D 组：不猜 + warning
# --------------------------------------------------------------------------- #
class TestDNoGuess(Base):
    def test_d1_unknown_paper_writes_nothing(self):
        self.assertEqual({}, self.gsm(["材料：300g"]))

    def test_d2_warning_text_verbatim(self):
        self.assertIn(AMBIGUOUS_WARNING, self.warnings_text(["材料：300g"]),
                      "批 5 的文案逐字沿用（Spec C4）")

    def test_d3_warning_once(self):
        notes = ["材料：300g", "材料：400g", "名称：贴牌材料：325G PET光银"]
        self.assertEqual(1, self.warnings_text(notes).count(AMBIGUOUS_WARNING),
                         "同一条文案只记一次，不刷屏")

    def test_d4_attributed_value_still_reported_alongside_warning(self):
        notes = ["名称：左盖面纸材料：225G铜版底PET光银", "材料：300g"]
        self.assertEqual({FACE: 225.0}, self.gsm(notes),
                         "能配对的照写，配不上的丢给 warning")
        self.assertIn(AMBIGUOUS_WARNING, self.warnings_text(notes),
                      "只要丢了值就要说，不许因为别的键读到了就静默（Spec C4）")

    def test_d5_unattributed_value_never_lands_in_any_gsm_key(self):
        module = self.module()
        fields = {"units": "mm", "material_notes": ["材料：300g"]}
        mapped = module.to_match_inputs({"kind": "drawing", "fields": fields})
        for key in GSM_KEYS:
            self.assertNotIn(key, mapped["inputs"])
            self.assertIn(key, mapped["missing"], "读不出就必须如实进 missing")


# --------------------------------------------------------------------------- #
# E 组：真样本金标（本机有转换器才跑）
# --------------------------------------------------------------------------- #
class TestERealSamples(Base):
    def _fields(self, path):
        if not path.exists():
            self.skipTest("真实样本不在本机：%s" % path)
        try:
            from tech_app.backend.services import cad_converter
            from tech_app.backend.services import unified_parse
        except ImportError:                                     # pragma: no cover
            self.skipTest("技术工艺侧 cad_converter / unified_parse 不在本机")
        cap = cad_converter.capability() or {}
        if not cap.get("available"):
            self.skipTest("本机没有可用 DWG 转换器（capability().available=false）")
        import base64
        payload = {"name": path.name, "data": base64.b64encode(path.read_bytes()).decode("ascii")}
        return unified_parse.parse_payload(payload)["fields"]

    def _mapped(self, path):
        return self.module().to_match_inputs({"kind": "drawing", "fields": self._fields(path)})

    def test_e1_wine_box_face_paper_gsm(self):
        mapped = self._mapped(WINE_BOX)
        self.assertAlmostEqual(235.0, float(mapped["inputs"][FACE]), places=6,
                               msg="酒盒.dwg 面纸 = 235g白卡（Spec C5）")

    def test_e2_wine_box_grey_board_not_guessed(self):
        mapped = self._mapped(WINE_BOX)
        self.assertNotIn(GREY, mapped["inputs"],
                         "酒盒.dwg 的灰板只写了 mm 厚度，「350g粉灰」有歧义 → 不许猜（Spec C5）")
        self.assertIn(GREY, mapped["missing"])

    def test_e3_wine_box_does_not_cry_wolf(self):
        mapped = self._mapped(WINE_BOX)
        joined = " ".join(str(item) for item in (mapped.get("warnings") or []))
        self.assertNotIn(AMBIGUOUS_WARNING, joined,
                         "进 material_notes 的克重都配上了纸种：不许无端报歧义（Spec C5）")

    def test_e4_round_box_golden(self):
        mapped = self._mapped(ROUND_BOX)
        inputs = mapped["inputs"]
        self.assertAlmostEqual(300.0, float(inputs[FACE]), places=6,
                               msg="圆盘盒.dwg 面卡 = 300G白卡/哑PP（Spec C5）")
        self.assertAlmostEqual(1200.0, float(inputs[GREY]), places=6,
                               msg="圆盘盒.dwg 灰板 = 1200g双灰（Spec C5）")


# --------------------------------------------------------------------------- #
# F 组：护栏（今天已绿，改完必须还绿）
# --------------------------------------------------------------------------- #
class TestFGuards(Base):
    def test_f1_batch5_baseline_still_green(self):
        self.assertEqual({FACE: 250.0, GREY: 1200.0},
                         self.gsm(["面纸 250g 铜版纸", "灰板 1200gsm"]),
                         "批 5 test_e7 的输入必须逐字仍然成立")

    def test_f2_output_shape_unchanged(self):
        module = self.module()
        fields = {"units": "mm", "material_notes": ["235g白卡"]}
        mapped = module.to_match_inputs({"kind": "drawing", "fields": fields})
        self.assertEqual({"inputs", "missing", "sources", "warnings", "units_factor",
                          "match_input_keys"}, set(mapped),
                         "to_match_inputs 出参形状一字不动（Spec C6）")

    def test_f3_missing_and_inputs_never_overlap(self):
        module = self.module()
        fields = {"units": "mm", "material_notes": ["235g白卡", "材料：300g"]}
        mapped = module.to_match_inputs({"kind": "drawing", "fields": fields})
        for key in mapped["missing"]:
            self.assertNotIn(key, mapped["inputs"])

    def test_f4_sources_mark_parse(self):
        module = self.module()
        fields = {"units": "mm", "material_notes": ["235g面纸"]}
        mapped = module.to_match_inputs({"kind": "drawing", "fields": fields})
        self.assertEqual("parse", mapped["sources"][FACE])

    def test_f5_no_new_keys_beyond_closed_set(self):
        module = self.module()
        fields = {"units": "mm", "material_notes": ["235g白卡", "1200g双灰板"]}
        mapped = module.to_match_inputs({"kind": "drawing", "fields": fields})
        allowed = set(mapped["match_input_keys"])
        for key in mapped["inputs"]:
            self.assertIn(key, allowed, "不许往匹配输入里塞闭集外的键")


if __name__ == "__main__":                                      # pragma: no cover
    unittest.main()
