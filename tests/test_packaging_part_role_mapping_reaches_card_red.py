"""红测：人工映射的业务角色必须看得见（现在 BOM 侧映射完了，卡片上永远是 unknown）。

Spec：`docs/specs/packaging-part-role-mapping-must-reach-the-card.md`

现状缺口（9-22 在本机核对，不是推断）：
  · 零件行的角色由 `_layer_roles()`（图层名 → 角色）在 `extract()` 时给出；真实客户图
    `layers = ["0","DESIGN"]` 无语义 → 全 `unknown`（34 实测 `role_known_ratio = 0.0`、
    `stats.by_role = {"unknown": 64}`）。
  · 人工映射只写 BOM 侧：`…/packaging-bom/role-map` → `packaging_bom.apply_role_mapping()`
    → `save_role_mapping()`：事实源写 BOM 行的 `size_source_json.dwg_binding`，留痕写 meta 文档
    `ROLE_MAP_DOC_KEY = "packaging_bom_role_map"` 的 `by_requirement[需求单][行键]`。
    **没有任何一步写零件文档那一行。**
  · 读回侧只有 `packaging_parts._manual_fill_overlay()` 挂 overlay，而它的键集合逐字只有
    `DOC_KEY_MATERIAL` / `DOC_KEY_THICKNESS` / `DOC_KEY_OUTLINE` —— **角色这本账不在里面**。
  · 卡片 `card_row()` 的 `"role"` 与 `summarize()` 的 `role_known_ratio` 都只读零件行
    → 人工映射对它们不可见。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_parts"
BOM = "tech_app.backend.services.packaging_bom"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
BOM_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_bom.py"

PROJECT = "p-role-card"
REQUIREMENT = "REQ-ROLE-CARD-1"
MAPPED_PART = "DWG-P09"
MAPPED_ROLE = "面纸"
OTHER_PART = "DWG-P10"
GHOST_PART = "DWG-NOPE"
MANUAL_MAPPING_KIND = "manual_mapping"

#: 映射记录的 `part_code` 在零件文档里找不到时的"不该被改"的那一行（Spec §2.5）。
CARD_LABELS = ["零件号", "名称", "角色", "材料", "厚度(mm)", "展开长(mm)", "展开宽(mm)",
               "轮廓状态", "可算", "不可算原因"]

PROBE_SCRIPT = r'''
import json
import tech_app.backend.services.packaging_parts as pp
import tech_app.backend.services.packaging_bom as bom
from tech_app.backend.storage.meta_backend import get_backend

def row(code):
    return {"part_code": code, "name": "图纸零件 " + code,
            "outline_status": "closed", "outline_reason": "", "role": "unknown",
            "material": {"spec": "灰板", "grade": "", "material_code": ""},
            "material_source": {"kind": "note", "text": "图纸注记"},
            "thickness_mm": 1.2, "thickness_source": {"kind": "note", "text": "1.2mm"},
            "unfolded_length_mm": 440.0, "unfolded_width_mm": 480.0,
            "attribution": {"kind": "", "material_unresolved": [], "thickness_unresolved": []}}

pp.save_parts("%(project)s", {"parts": [row("%(mapped)s"), row("%(other)s")],
                              "stats": {"part_total": 2}})

# 「人工映射已落盘」：形状与 packaging_bom.save_role_mapping() 写进 by_requirement 的记录逐字一致。
def record(item_key, part_code, role):
    return {"item_key": item_key, "part_code": part_code, "role": role,
            "previous_role": "unbound", "superseded_role": "",
            "mapped_by": "PE1", "mapped_at": "2026-09-22 10:00:00", "note": "",
            "binding_method": "manual_mapping", "history": []}

get_backend().put_doc("%(project)s", bom.ROLE_MAP_DOC_KEY, {
    "by_requirement": {"%(requirement)s": {
        "item-1": record("item-1", "%(mapped)s", "%(role)s"),
        "item-2": record("item-2", "%(ghost)s", "不能用")}}})

record_doc = pp.load_parts("%(project)s") or {}
def picked(code):
    return next(r for r in (record_doc.get("parts") or []) if r.get("part_code") == code)

mapped = picked("%(mapped)s")
other = picked("%(other)s")
summary = pp.summarize(record_doc)
print(json.dumps({
    "role": mapped.get("role"),
    "role_source": mapped.get("role_source"),
    "other_role": other.get("role"),
    "other_role_source": other.get("role_source"),
    "ratio": summary.get("role_known_ratio"),
    "card_role": pp.card_row(mapped).get("role"),
    "doc_keys": sorted((pp.load_parts("%(project)s") or {}).keys()),
}, ensure_ascii=False, default=str))
'''


def module():
    return importlib.import_module(PKG)


def probe():
    """在独立 DATA_DIR 里真跑一遍「映射记录落盘 → 读零件行 / 摘要 / 卡片行」。"""
    script = PROBE_SCRIPT % {"project": PROJECT, "requirement": REQUIREMENT,
                             "mapped": MAPPED_PART, "other": OTHER_PART,
                             "ghost": GHOST_PART, "role": MAPPED_ROLE}
    with tempfile.TemporaryDirectory(prefix="cpq-role-card-") as tmp:
        env = dict(os.environ, DATA_DIR=tmp, STORAGE_BACKEND="json")
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                   text=True, timeout=300, cwd=str(ROOT), env=env)
    if completed.returncode != 0:
        raise AssertionError("探针跑不起来：%s"
                             % (completed.stderr or completed.stdout)[-800:])
    return json.loads((completed.stdout or "{}").strip().splitlines()[-1])


def text_of(path):
    return path.read_text(encoding="utf-8", errors="replace")


def function_body(text, name):
    match = re.search(r"^def %s\(" % re.escape(name), text, re.M)
    if not match:
        return ""
    lines = text[match.start():].splitlines(keepends=True)
    index = 0
    while index < len(lines) and not lines[index].rstrip().endswith(":"):
        index += 1
    body = []
    for line in lines[index + 1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        body.append(line)
    return "".join(body)


class ARoleReadback(unittest.TestCase):
    """A 组：映射落盘之后，零件行 / 摘要 / 卡片行都要看得到（今天全红）。"""

    def test_a1_mapped_role_lands_on_the_part_row(self):
        state = probe()
        self.assertEqual(MAPPED_ROLE, state["role"],
                         "人工映射记录已经落盘，但 `load_parts()` 那一行的角色仍是 %r —— "
                         "`_manual_fill_overlay()` 只合材料/料厚/轮廓三本账，角色这本账不在里面"
                         "（Spec §2.1 第 1 条）" % state["role"])

    def test_a2_role_known_ratio_moves_with_the_mapping(self):
        state = probe()
        self.assertEqual(1.0, state["ratio"],
                         "`summarize()[\"role_known_ratio\"]` 仍是 %r —— 这一件已经被人工映射过了，"
                         "必须算进「已知」（Spec §2.1 第 2 条）" % state["ratio"])

    def test_a3_card_row_shows_the_mapped_role(self):
        state = probe()
        self.assertEqual(MAPPED_ROLE, state["card_role"],
                         "卡片 `card_row()[\"role\"]` 仍是 %r —— 报价卡片第 6 步的「角色」列"
                         "永远只能显示 unknown（Spec §2.1 第 2 条）" % state["card_role"])

    def test_a4_part_row_carries_a_manual_mapping_trace(self):
        source = probe()["role_source"]
        self.assertIsInstance(source, dict,
                              "零件行上没有 `role_source` 留痕（%r）—— 必须能分辨「人工映射」与"
                              "「图纸图层推定」两种来源（Spec §2.1 第 3 条）" % (source,))
        self.assertEqual(MANUAL_MAPPING_KIND, str(source.get("kind") or ""),
                         "`role_source.kind` 必须是 %r（Spec §2.1 第 3 条）" % MANUAL_MAPPING_KIND)
        self.assertTrue(str(source.get("bound_by") or ""),
                        "`role_source` 必须带 `bound_by`（谁映射的）（Spec §2.1 第 3 条）")


class BGuards(unittest.TestCase):
    """B 组：今天就是绿的，不许被改红。"""

    def test_b1_autobind_rejection_and_layer_roles_unchanged(self):
        bom_text = text_of(BOM_PY)
        self.assertIn("def reject_unknown_role_autobind", bom_text,
                      "`reject_unknown_role_autobind()` 是 §4.4 的红线，不许撤（Spec §2.4）")
        parts_text = text_of(PARTS_PY)
        self.assertIn("def _layer_roles", parts_text, "`_layer_roles()` 必须仍在（Spec §2.4）")
        body = function_body(parts_text, "extract")
        self.assertTrue(body, "取不到 `extract()` —— 测试前提失效")
        self.assertNotIn("role_source", body,
                         "`extract()` 的自动判定不许自己写 `role_source` —— 只有人工映射才留这个痕"
                         "（Spec §2.4）")

    def test_b2_bom_side_symbols_survive(self):
        text = text_of(BOM_PY)
        for token in ("ROLE_MAP_DOC_KEY", "def apply_role_mapping", "def save_role_mapping",
                      "def apply_saved_role_map", "def role_map_status"):
            self.assertIn(token, text, "BOM 侧 %s 不许被动（Spec §2.5 第 6 条）" % token)

    def test_b3_unmatched_part_code_changes_nothing(self):
        state = probe()
        self.assertEqual("unknown", state["other_role"],
                         "映射记录里的 `part_code` 配不上零件文档里的件时，那一行不许被改"
                         "（现在是 %r）（Spec §2.5 第 5 条）" % state["other_role"])
        self.assertIsNone(state["other_role_source"],
                          "配对不上的件不许凭空多出 `role_source`（Spec §2.5 第 5 条）")

    def test_b4_card_columns_and_processability_unchanged(self):
        mod = module()
        self.assertEqual(CARD_LABELS, [col["label"] for col in mod.card_columns()],
                         "卡片列定义是冻结面（Spec §2.5 第 6 条）")
        body = function_body(text_of(PARTS_PY), "card_row")
        self.assertIn("processability", body,
                      "`card_row()` 的可算性仍必须只取自 `processability()`（Spec §2.5 第 6 条）")


if __name__ == "__main__":
    unittest.main()
