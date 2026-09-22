"""红测：包材缺口必须分「本单用到的项」与「没绑上本单的项」，后者只披露不阻断。

Spec：docs/specs/packaging-cost-gaps-scoped-to-order-contents.md

现状缺口（本机用真实种子复现，不依赖线上）：
  · `compute_project()` 把 `kb_repo.packaging_cost_contents()` **整表**喂进 `compute_packaging`，
    再把每一行的 gap 无条件收进 `gaps`（`packaging_cost.py:2060-2063`）；
  · 隔卡 / 胶袋 等行在源工作簿里尺寸用量本来就是空格（种子注释逐行写着），于是每一单都拿到同一批
    `content_formula_error:PKG-P-*`，而它们的 severity 是 `blocking`；
  · 后果：34 那次 11 条阻断缺口里 7 条来自本单 BOM 根本没绑上的包材项 →
    `verdict=provisional` → `gates.quote_publish=blocked / cost_gaps_unresolved`。

夹具用真实种子（`da_seed_packaging.COST_CONTENTS` + 内置 `PKG-P-*` 公式），不连库、不复制常量。

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

CARTON = "PKG-CT-CARTON"      # 彩盒：算得出来
PAD = "PKG-CT-PAD"            # 平卡：算得出来
DIVIDER = "PKG-CT-DIVIDER"    # 隔卡：源工作簿缺尺寸/用量 → content_formula_error:PKG-P-DIVIDER
BAG = "PKG-CT-BAG"            # 胶袋：同上 → content_formula_error:PKG-P-BAG
SAMPLE = (CARTON, PAD, DIVIDER, BAG)


def _rows(*codes):
    wanted = codes or SAMPLE
    return [dict(row) for row in seed.COST_CONTENTS if row["content_code"] in wanted]


def _by_code(result):
    return {line.get("content_code"): line for line in result["lines"]}


def _gap_codes(rows):
    return sorted((row.get("gap") or {}).get("code") for row in (rows or []))


class AContentScope(unittest.TestCase):
    """A 组：行级绑定事实 + 缺口分家。"""

    def pack(self, rows, **kwargs):
        try:
            return cost_mod.compute_packaging(rows, **kwargs)
        except TypeError as exc:                                # bound_content_codes 还没加出来
            self.fail("compute_packaging 必须接受 Spec §2.1 的 bound_content_codes 关键字参数：%s" % exc)

    def test_a1_line_carries_binding_status(self):
        out = self.pack(_rows(), bound_content_codes={CARTON})
        seen = {line.get("content_code"): (line.get("binding") or {}).get("status")
                for line in out["lines"]}
        self.assertEqual("bound", seen.get(CARTON), "给了绑定集合时命中项必须标 bound：%r" % seen)
        self.assertEqual("unbound", seen.get(DIVIDER),
                         "没绑上本单的包材项必须标 unbound：%r" % seen)
        self.assertEqual("unbound", seen.get(BAG), "同上：%r" % seen)

    def test_a2_unbound_formula_error_is_disclosed_not_blocking(self):
        out = self.pack(_rows(), bound_content_codes={CARTON, PAD})
        self.assertIn("unbound_gaps", out, "返回体必须新增 unbound_gaps（披露用）")
        self.assertIn("bound_gaps", out, "返回体必须新增 bound_gaps（结论用）")
        self.assertEqual(["content_formula_error:PKG-P-BAG", "content_formula_error:PKG-P-DIVIDER"],
                         _gap_codes(out["unbound_gaps"]),
                         "没绑上本单的 content_formula_error 只能进 unbound_gaps")
        self.assertEqual([], _gap_codes(out["bound_gaps"]),
                         "本单没用到的项不许再往阻断缺口里加：%r" % _gap_codes(out["bound_gaps"]))

    def test_a3_bound_formula_error_still_blocks(self):
        out = self.pack(_rows(), bound_content_codes={DIVIDER})
        self.assertEqual(["content_formula_error:PKG-P-DIVIDER"], _gap_codes(out["bound_gaps"]),
                         "本单**用得到**的项算不出来，照旧必须是阻断缺口")
        self.assertEqual(["content_formula_error:PKG-P-BAG"], _gap_codes(out["unbound_gaps"]),
                         "没绑上的那条仍然只披露")

    def test_a4_without_binding_set_behaviour_is_unchanged(self):
        out = self.pack(_rows())
        self.assertEqual([], _gap_codes(out.get("unbound_gaps")),
                         "没给绑定集合时不许凭空判 unbound")
        self.assertEqual(sorted(["content_formula_error:PKG-P-BAG",
                                 "content_formula_error:PKG-P-DIVIDER"]),
                         _gap_codes(out.get("bound_gaps")),
                         "没给绑定集合时必须与今天逐字相同（全部照报）")
        statuses = {(line.get("binding") or {}).get("status") for line in out["lines"]}
        self.assertEqual({"unknown"}, statuses, "没给绑定集合时状态是 unknown：%r" % statuses)

    def test_a5_other_gap_codes_are_never_exempted(self):
        rows = _rows()
        for row in rows:
            if row["content_code"] == BAG:
                row["units_per_pack"] = 0                      # → invalid_units_per_pack
        out = self.pack(rows, bound_content_codes={CARTON})
        self.assertIn("invalid_units_per_pack", _gap_codes(out["bound_gaps"]),
                      "本批只收 content_formula_error 这一类；别的码就算没绑上本单也不许豁免")
        self.assertNotIn("invalid_units_per_pack", _gap_codes(out["unbound_gaps"]))

    def test_a6_line_level_gap_is_not_removed(self):
        out = self.pack(_rows(), bound_content_codes={CARTON})
        lines = _by_code(out)
        self.assertEqual("content_formula_error:PKG-P-DIVIDER",
                         (lines[DIVIDER].get("gap") or {}).get("code"),
                         "分家只是不给结论，行上的缺口必须留着（页面靠它显示「这一项算不出来」）")

    def test_a7_readiness_reports_unbound_total(self):
        gate = cost_mod.packaging_cost_readiness_gate(
            {"built": True, "gaps": [],
             "gaps_unbound_to_order": [{"code": "content_formula_error:PKG-P-BAG",
                                        "content_code": BAG, "binding_status": "unbound"}]})
        self.assertEqual(1, gate.get("unbound_total"),
                         "就绪门必须把「没绑上本单」单独报数")
        self.assertEqual(0, gate["blocking_total"], "没绑上的项不许进 blocking_total")
        self.assertTrue(gate["formal_ready"], "它也不许单独把成本打成 provisional")

    def test_a8_call_site_must_pass_the_binding_set(self):
        body = inspect.getsource(cost_mod.compute_project)
        self.assertIn("bound_content_codes", body,
                      "compute_project 必须把本单绑定的包材项传进 compute_packaging")
        self.assertNotIn("compute_packaging(kb_repo.packaging_cost_contents())", body,
                         "不许再裸调整表（这就是那 7 条假阻断的来路）")


if __name__ == "__main__":
    unittest.main()
