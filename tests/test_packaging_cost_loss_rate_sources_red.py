"""红测：损耗率取数必须接库里已有的两处权威源（材料行 / 因子表作用域）。

Spec：docs/specs/packaging-cost-loss-rate-authoritative-sources.md

现状缺口（代码事实 + 数据事实，逐条可复现）：
  · `packaging_cost.default_loss_rate()` 只按文字认「灰板」与「纸」，其余一律 None；
  · 调用点 `loss_rate_for()` 只把因子表传进去，**材料行没传** —— 而材料行在同一循环里已经取到；
  · `kb_material.standard_loss_rate` 是 NOT NULL DEFAULT 0 的真列，包装 5 条材料都有值
    （EVA 片材 0.10、特种纸 0.09…），引擎一处都没读；
  · `kb_cost_factor.applicable_scope` 也是真列，`kb_repo.effective_factor()` 早有"专用作用域压过
    通用兜底"的口径，成本引擎没走那条路。

后果：材料行写着 0.10 的材料照样报 `loss_rate_missing`，那一行还照出金额 → 被判"静默按 0"
→ 整份成本 provisional。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import inspect
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_cost as cost_mod  # noqa: E402
from tech_app.backend.storage import da_seed_packaging as seed  # noqa: E402


def _material_rows():
    return {row["material"]["material_code"]: dict(row["material"]) for row in seed.MATERIALS}


def _factor(code, value, *, scope="包材", effective_from="2026-01-01", factor_type="scrap"):
    return {"factor_code": code, "factor_type": factor_type, "applicable_scope": scope,
            "value": value, "effective_from": effective_from, "effective_to": None}


class AAuthoritativeSources(unittest.TestCase):
    """A 组：材料行与因子表里已有的值必须被用到。"""

    def call(self, text, **kwargs):
        try:
            return cost_mod.default_loss_rate(text, **kwargs)
        except TypeError as exc:                                # 关键字参数还没加出来
            self.fail("default_loss_rate 必须接受 Spec §2.1 的关键字参数（material= 等）：%s" % exc)

    def test_a1_material_standard_loss_rate_is_used(self):
        rows = _material_rows()
        eva = rows["MAT-PKG-EVA"]
        self.assertEqual(0.10, eva.get("standard_loss_rate"), "夹具前提：EVA 材料行有标准损耗率")
        self.assertEqual(0.1, self.call("EVA 片材", material=eva, rows=list(seed.COST_FACTORS)),
                         "材料行写着标准损耗率就必须用（现在只认灰板/纸两种文字 → None）")

    def test_a2_every_seeded_material_resolves_to_its_own_value(self):
        bad = []
        for code, material in _material_rows().items():
            want = material.get("standard_loss_rate")
            if not want:
                continue
            got = self.call(material.get("name") or "", material=material,
                            rows=list(seed.COST_FACTORS))
            if got != want:
                bad.append("%s（%s）应取材料行的 %s，实测 %r" % (code, material.get("name"), want, got))
        self.assertEqual([], bad, "每一条有标准损耗率的材料都必须取到自己的值：\n" + "\n".join(bad))

    def test_a3_material_scope_beats_text_fallback(self):
        special = _material_rows()["MAT-PKG-SPECIAL"]          # 特种纸 0.09，文字兜底会给 0.06
        self.assertEqual(0.09, self.call("特种纸", material=special, rows=list(seed.COST_FACTORS)),
                         "材料行 0.09 必须压过「纸」的文字兜底 0.06")

    def test_a4_zero_means_not_registered(self):
        material = {"material_code": "MAT-PKG-X", "name": "装帧布", "category": "包材",
                    "standard_loss_rate": 0}
        rows = [_factor("F-PKG-LOSS-SCOPED", 0.07)]
        self.assertEqual(0.07, self.call("装帧布", material=material, rows=rows),
                         "standard_loss_rate=0 是「未登记」（DDL 是 NOT NULL DEFAULT 0），"
                         "必须继续往下找，不许当成损耗 0 返回")

    def test_a5_scoped_factor_beats_scope_less_fallback(self):
        material = {"material_code": "MAT-PKG-Y", "name": "装帧布", "category": "包材"}
        rows = [_factor("F-GENERIC", 0.99, scope=None, effective_from="2026-09-01"),
                _factor("F-SCOPED", 0.07, scope="包材", effective_from="2026-01-01")]
        self.assertEqual(0.07, self.call("装帧布", material=material, rows=rows),
                         "同作用域（材料行 category=包材）必须压过无作用域的兜底，"
                         "口径与 kb_repo.effective_factor 一致")

    def test_a6_without_material_no_factor_is_picked(self):
        rows = [_factor("F-GENERIC", 0.99, scope=None), _factor("F-SCOPED", 0.07)]
        self.assertIsNone(self.call("装帧布", rows=rows),
                          "没有材料行就没有作用域 → 只能 None + 缺口，不许随便捡一条因子")

    def test_a7_text_fallback_is_not_removed(self):
        rows = list(seed.COST_FACTORS)
        self.assertEqual(0.08, self.call("灰板 2.0mm", rows=rows), "既有灰板文字兜底不许删")
        self.assertEqual(0.06, self.call("铜版纸面纸", rows=rows), "既有纸类文字兜底不许删")

    def test_a8_source_guards(self):
        src = pathlib.Path(cost_mod.__file__).read_text(encoding="utf-8")
        self.assertIn("standard_loss_rate", src,
                      "引擎必须读 kb_material.standard_loss_rate（现在源码里没有这个字面）")
        body = inspect.getsource(cost_mod.default_loss_rate)
        self.assertNotIn("or 0.0", body, "取不到损耗率不许按 0 兜底")
        self.assertNotIn("return 0.0", body, "取不到损耗率不许按 0 兜底")


if __name__ == "__main__":
    unittest.main()
