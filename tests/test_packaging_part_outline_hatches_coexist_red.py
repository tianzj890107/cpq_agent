"""红测：未闭合件的两条出路（人工签字 / 单件重算）必须**并存**，不许后写的顶掉先写的。

Spec：`docs/specs/packaging-part-outline-hatches-must-coexist.md`

现状缺口（2026-09-23 在本机核对，不是推断；离线纯函数 + 侧档读写，未起服务、未发 HTTP）：
  · 两条出路写进**同一个** doc key：`save_part_outline()`（`kind="manual_bbox"`）与
    `save_part_outline_recompute()`（`kind="recomputed_outline"`）都走
    `_save_part_doc(project_id, DOC_KEY_OUTLINE, …)`（`packaging_parts.py`，`DOC_KEY_OUTLINE
    = "packaging_part_outline"`）。
  · 读回时 `_manual_fill_overlay()` 用 `setdefault(code, {}).setdefault(key, item)` 遍历
    `_part_doc_items()`（新→旧）→ **每件每 key 只取最近一版**（last-writer-wins），
    再按 `kind` 二选一分派给 `set_manual_outline()` 或 `set_recomputed_outline()`
    → 行上**永远不可能同时**有 `outline_confirmation` 与 `outline_recompute`。
  · 而放行判据是 `outline_status != "closed" and not outline_confirmation(payload)`（`processability()`）
    → 签字记录被顶掉 = 这一件**重新 409**。
  · 本机实测：签字 → 重算（仍 `open`）：`ok` `True → False`、`outline_confirmation` `True → False`；
    签字 → 重算（闭合）：`ok` 仍 `True` 但签字留痕也丢；重算 → 签字：`outline_recompute` 丢。

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
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"

PROJECT = "p-outline-hatches"
RETRY_PART = "DWG-P01"       # A1 / A2：签字 → 重算（仍 open）
CLOSING_PART = "DWG-P02"     # A3：签字 → 重算（这次闭合）
REVERSE_PART = "DWG-P03"     # A4：重算（仍 open） → 签字
UNTOUCHED_PART = "DWG-P09"   # B1：什么都没点
SIGNER = "PE1"
SIGN_REASON = "图纸断口是真的，按包围盒估算放行"
CLOSED_OUTLINE = {"points": [[0, 0], [10, 0], [10, 10], [0, 10]],
                  "bbox": [0, 0, 10, 10], "area_mm2": 100.0}
OPEN_REASON = "odd_endpoints"
CARD_LABELS = ["零件号", "名称", "角色", "材料", "厚度(mm)", "展开长(mm)", "展开宽(mm)",
               "轮廓状态", "可算", "不可算原因"]

PROBE_SCRIPT = r'''
import json
import tech_app.backend.services.packaging_parts as pp

def open_row(code):
    """只卡在轮廓上的件：材料与料厚都给足，唯一的前置是 `outline_status`。"""
    return {"part_code": code, "name": "图纸零件 " + code,
            "outline_status": "open", "outline_reason": "%(reason)s",
            "material": {"spec": "灰板", "grade": "", "material_code": ""},
            "material_source": {"kind": "note", "text": "图纸注记"},
            "thickness_mm": 1.2, "thickness_source": {"kind": "note", "text": "1.2mm"},
            "unfolded_length_mm": 440.0, "unfolded_width_mm": 480.0,
            "attribution": {"kind": "", "material_unresolved": [],
                            "thickness_unresolved": []}}

pp.save_parts("%(project)s", {"parts": [open_row("%(retry)s"), open_row("%(closing)s"),
                                         open_row("%(reverse)s"), open_row("%(untouched)s")],
                              "stats": {"part_total": 4}})

def recompute(part_code, status, extra=None):
    record = {"part_code": part_code, "kind": pp.RECOMPUTED_OUTLINE_KIND,
              "rule_id": pp.RECOMPUTE_RULE_ID, "component_id": "c-" + part_code,
              "scale": 4.0, "outline_status": status,
              "outline_reason": "" if status == "closed" else "%(reason)s",
              "outline_diagnosis": {"odd_degree_vertices": 2}}
    record.update(extra or {})
    pp.save_part_outline_recompute("%(project)s", record)

def sign(part_code):
    pp.save_part_outline("%(project)s", part_code, bound_by="%(signer)s",
                         reason="%(sign_reason)s", confirmed_at="2026-09-23 09:00:00")

def state(part_code):
    row = next(r for r in ((pp.load_parts("%(project)s") or {}).get("parts") or [])
               if r.get("part_code") == part_code)
    verdict = pp.processability(row)
    confirm = pp.outline_confirmation(row)
    return {"ok": bool(verdict.get("ok")), "code": _text(verdict.get("code")),
            "status": _text(row.get("outline_status")),
            "confirmation": bool(row.get("outline_confirmation")),
            "recompute": bool(row.get("outline_recompute")),
            "signed_by": _text(confirm.get("bound_by")),
            "signed_reason": _text(confirm.get("reason")),
            "missing": list(verdict.get("missing_variables") or [])}

def _text(value):
    return "" if value is None else str(value)

# A1 / A2：先签字放行，再点一次「重算轮廓」（这次仍然算不出闭合环）
sign("%(retry)s")
after_sign = state("%(retry)s")
recompute("%(retry)s", "open")
after_recompute = state("%(retry)s")

# A3：先签字，再重算 —— 这次真的闭合了
sign("%(closing)s")
recompute("%(closing)s", "closed", {"outline": %(closed_outline)s,
                                    "size_source": "closed_outline",
                                    "unfolded_length_mm": 10.0,
                                    "unfolded_width_mm": 10.0, "area_mm2": 100.0})
closing = state("%(closing)s")

# A4：反方向 —— 先重算（仍 open），再签字
recompute("%(reverse)s", "open")
sign("%(reverse)s")
reverse = state("%(reverse)s")

print(json.dumps({"after_sign": after_sign, "after_recompute": after_recompute,
                  "closing": closing, "reverse": reverse,
                  "untouched": state("%(untouched)s")},
                 ensure_ascii=False))
'''


def module():
    return importlib.import_module(PKG)


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


def probe():
    """在独立 DATA_DIR 里真跑一遍「签字 / 重算」两种顺序，回真实状态（离线）。"""
    script = PROBE_SCRIPT % {"project": PROJECT, "retry": RETRY_PART,
                             "closing": CLOSING_PART, "reverse": REVERSE_PART,
                             "untouched": UNTOUCHED_PART, "reason": OPEN_REASON,
                             "signer": SIGNER, "sign_reason": SIGN_REASON,
                             "closed_outline": json.dumps(CLOSED_OUTLINE)}
    with tempfile.TemporaryDirectory(prefix="cpq-outline-hatch-") as tmp:
        env = dict(os.environ, DATA_DIR=tmp, STORAGE_BACKEND="json")
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                   text=True, timeout=300, cwd=str(ROOT), env=env)
    if completed.returncode != 0:
        raise AssertionError("探针跑不起来：%s"
                             % (completed.stderr or completed.stdout)[-800:])
    return json.loads((completed.stdout or "{}").strip().splitlines()[-1])


class AHatchesCoexist(unittest.TestCase):
    """A 组：两条出路并存（今天全红）。"""

    def test_a1_signature_survives_a_later_recompute(self):
        state = probe()
        self.assertTrue(state["after_sign"]["ok"],
                        "测试前提失效：签字之后这一件本来就该放行（%r）" % state["after_sign"])
        self.assertTrue(
            state["after_recompute"]["ok"],
            "签字放行之后，一次「重算轮廓」（这次仍算不出闭合）把这一件顶回了不可算"
            "（%r）—— 人签的字被静默抹掉，用户什么提示都看不到（Spec §2.1 第 1/2 条）"
            % state["after_recompute"])

    def test_a2_signature_trace_is_not_dropped(self):
        after = probe()["after_recompute"]
        self.assertTrue(after["confirmation"],
                        "重算之后行上的 `outline_confirmation` 没了 —— 签字留痕不许被另一条出路清掉"
                        "（Spec §2.1 第 2 条）")
        self.assertEqual(SIGNER, after["signed_by"],
                         "签字人必须逐字保留（Spec §2.1 第 2 条）")
        self.assertEqual(SIGN_REASON, after["signed_reason"],
                         "签字理由必须逐字保留（Spec §2.1 第 2 条）")

    def test_a3_both_traces_visible_when_recompute_closes_it(self):
        closing = probe()["closing"]
        self.assertEqual("closed", closing["status"],
                         "重算真的算出了闭合环时，几何结论必须按重算更新（Spec §2.1 第 3 条）")
        self.assertTrue(closing["ok"], "闭合之后这一件必须可算（Spec §2.1 第 3 条）")
        self.assertTrue(closing["confirmation"],
                        "重算闭合之后，之前那份人工签字留痕也丢了 —— 两份留痕必须同时可见"
                        "（Spec §2.1 第 4 条）")
        self.assertTrue(closing["recompute"], "重算结论本身必须留在行上（Spec §2.1 第 4 条）")

    def test_a4_reverse_order_keeps_the_recompute_trace(self):
        reverse = probe()["reverse"]
        self.assertTrue(reverse["confirmation"], "反方向：签字本身必须在行上（Spec §2.1 第 1 条）")
        self.assertTrue(reverse["ok"], "反方向：签字之后这一件必须可算（Spec §2.1 第 2 条）")
        self.assertTrue(reverse["recompute"],
                        "先重算（仍 open）再签字时，重算那份留痕被签字顶掉了 —— "
                        "两条出路必须并存，不是后写的赢（Spec §2.1 第 1/4 条）")


class BGuards(unittest.TestCase):
    """B 组：今天就是绿的，不许被改红。"""

    def test_b1_untouched_open_part_still_rejected(self):
        untouched = probe()["untouched"]
        self.assertFalse(untouched["ok"], "没有任何留痕的未闭合件不许放行（Spec §2.5 第 1 条）")
        self.assertEqual("PACKAGING_PART_NOT_CLOSED", untouched["code"],
                         "稳定码不许变（Spec §2.5 第 1 条）")
        self.assertEqual(["outline"], untouched["missing"],
                         "`missing_variables` 必须仍是 [\"outline\"]（Spec §2.5 第 1 条）")
        self.assertFalse(untouched["confirmation"], "没签字就不该有签字留痕（Spec §2.5 第 1 条）")
        self.assertFalse(untouched["recompute"], "没重算就不该有重算留痕（Spec §2.5 第 1 条）")

    def test_b2_recompute_stays_a_pure_function(self):
        mod = module()
        row = {"part_code": "DWG-P01", "component_id": "c-1", "outline_status": "open"}
        before = json.dumps(row, sort_keys=True)
        with self.assertRaises(ValueError):
            mod.recompute_outline(row, None)
        with self.assertRaises(ValueError):
            mod.recompute_outline(row, {"geometry": {"components": []}, "entities": []})
        self.assertEqual(before, json.dumps(row, sort_keys=True),
                         "`recompute_outline()` 必须仍是纯函数（Spec §2.5 第 6 条）")

    def test_b3_nobody_writes_outline_status_closed_manually(self):
        pattern = re.compile(r'outline_status"?\]?\s*=\s*"closed"')
        self.assertIsNone(pattern.search(text_of(PARTS_PY)),
                          "`closed` 只能由几何判定给出，不许人工写（Spec §2.5 第 5 条）")

    def test_b4_frozen_surface_unchanged(self):
        mod = module()
        self.assertEqual(CARD_LABELS, [col["label"] for col in mod.card_columns()],
                         "卡片列定义是冻结面（Spec §2.5 第 6 条）")
        text = text_of(PARTS_PY)
        for token in ("part_outline_manual_bbox_v1", "part_outline_recompute_v1"):
            self.assertIn(token, text, "两条 rule id 不许改（Spec §2.5 第 6 条）：%s" % token)
        self.assertEqual(
            ["no_curve_entity", "unit_unconfirmed", "loop_budget_exhausted",
             "odd_endpoints", "loop_too_small"], list(mod.OUTLINE_OPEN_REASONS),
            "未闭合原因闭集是冻结面（Spec §2.5 第 6 条）")
        self.assertIn("processability", function_body(text, "card_row"),
                      "`card_row()` 的可算性仍必须只取自 `processability()`（Spec §2.5 第 6 条）")


if __name__ == "__main__":
    unittest.main()
