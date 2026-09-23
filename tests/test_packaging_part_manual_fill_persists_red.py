"""红测：人工补材料 / 补料厚必须**落回零件行**（现在写进了没人读的侧档）。

Spec：`docs/specs/packaging-part-manual-fill-must-land-on-the-part-row.md`

现状缺口（9-22 在本机 HEAD `14afe4b` 上逐条核对，不是推断）：
  · 补录确实写库了：`save_part_material()` → `DOC_KEY_MATERIAL = "packaging_part_material"`
    （`packaging_parts.py:43`），`save_part_thickness()` → `DOC_KEY_THICKNESS`
    （`packaging_parts.py:39`）；两条写路由的 docstring 都写"写零件行的副本"。
  · 但那份"副本"是 `set_manual_material()` / `set_manual_thickness()` 返回的 deepcopy，
    只出现在 HTTP 响应体里 —— 两条路由都没有随后 `save_parts()`，也没有任何 overlay
    把侧档合回零件行；`packaging_part_thickness` / `packaging_part_material` 这两个字面量
    全仓只出现在 `packaging_parts.py` 的常量定义处，`load_part_*()` 的调用点**只有**它们
    自己的 GET 路由（`main.py:7815` / `7874`）。
  · 而下游全部从**零件行**取数：`_packaging_part_row()`（`main.py:7944`）→ `load_parts()`；
    单件工艺路由（`main.py:7969`）→ `processability(row)` → `ok=False` 就 409；
    `summarize()`（`packaging_parts.py:1691`）的 `material_manual_total` 由行现算
    （判据 `packaging_parts.py:1776`）；卡片 `card_row()`（`packaging_parts.py:2228`）同理。
  · 前端 `app.js:1496-1499` 拿 POST 回显打内存补丁，于是本标签页看着"补好了"，
    **刷新就没**、卡片第 6 步也看不见、再点下游**照样 409**。

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
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

#: A1/A3 里那两件「只差一格」的件：P01 只缺材料，P03 只缺料厚（都已闭合）。
PROJECT = "p-manual-persist"
MATERIAL_PART = "DWG-P01"
THICKNESS_PART = "DWG-P03"
FILLED_MATERIAL = "白卡纸"
FILLED_THICKNESS = 0.75

PROBE_SCRIPT = r'''
import json
import tech_app.backend.services.packaging_parts as pp

def row(code, material=None, thickness=None):
    return {"part_code": code, "name": "图纸零件 " + code,
            "outline_status": "closed", "outline_reason": "",
            "material": material, "material_source": None,
            "thickness_mm": thickness,
            "thickness_source": ({"kind": "note", "text": "1.2mm"} if thickness else None),
            "unfolded_length_mm": 440.0, "unfolded_width_mm": 480.0,
            "attribution": {"kind": "", "material_unresolved": [], "thickness_unresolved": []}}

doc = {"parts": [row("%(material_part)s", thickness=1.2),
                 row("%(thickness_part)s", material={"spec": "灰板", "grade": "",
                                                     "material_code": ""}),
                 row("DWG-P09", material={"spec": "灰板", "grade": "",
                                          "material_code": ""}, thickness=1.2)],
       "stats": {"part_total": 3}}
pp.save_parts("%(project)s", doc)

before = pp.summarize(pp.load_parts("%(project)s"))
pp.save_part_material("%(project)s", "%(material_part)s", "%(filled_material)s",
                      bound_by="PE1", reason="图纸未标材料")
pp.save_part_thickness("%(project)s", "%(thickness_part)s", %(filled_thickness)s,
                       bound_by="PE1", reason="图纸未标料厚")

record = pp.load_parts("%(project)s") or {}
after = pp.summarize(record)

def picked(code):
    return next(r for r in (record.get("parts") or []) if r.get("part_code") == code)

def part_state(code):
    item = picked(code)
    verdict = pp.processability(item)
    source_key = "material_source" if code == "%(material_part)s" else "thickness_source"
    return {"value": (pp._material_spec(item.get("material"))
                      if code == "%(material_part)s" else item.get("thickness_mm")),
            "kind": pp._material_source_kind(item, source_key),
            "ok": bool(verdict.get("ok")), "code": verdict.get("code")}

untouched = picked("DWG-P09")
print(json.dumps({
    "material": part_state("%(material_part)s"),
    "thickness": part_state("%(thickness_part)s"),
    "untouched_ok": bool(pp.processability(untouched).get("ok")),
    "manual_total_before": before.get("material_manual_total"),
    "manual_total_after": after.get("material_manual_total"),
    "thickness_manual_after": after.get("thickness_manual_total"),
    "known_material_before": before.get("material_known_total"),
    "known_material_after": after.get("material_known_total"),
    "unknown_mix_before": (before.get("unprocessable_reason_mix") or {}
                           ).get("PACKAGING_PART_MATERIAL_UNKNOWN"),
    "unknown_mix_after": (after.get("unprocessable_reason_mix") or {}
                          ).get("PACKAGING_PART_MATERIAL_UNKNOWN"),
}, ensure_ascii=False))
'''


def module():
    return importlib.import_module(PKG)


def probe():
    """在独立 DATA_DIR 里真跑一遍「落库 → 补材料 / 补料厚 → 重读」并回真实数字。"""
    script = PROBE_SCRIPT % {"project": PROJECT, "material_part": MATERIAL_PART,
                             "thickness_part": THICKNESS_PART,
                             "filled_material": FILLED_MATERIAL,
                             "filled_thickness": FILLED_THICKNESS}
    with tempfile.TemporaryDirectory(prefix="cpq-fill-persist-") as tmp:
        env = dict(os.environ, DATA_DIR=tmp, STORAGE_BACKEND="json")
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                   text=True, timeout=300, cwd=str(ROOT), env=env)
    if completed.returncode != 0:
        raise AssertionError("探针跑不起来：%s"
                             % (completed.stderr or completed.stdout)[-800:])
    return json.loads((completed.stdout or "{}").strip().splitlines()[-1])


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


class AWhenFilled(unittest.TestCase):
    """A 组：补录之后，**零件行**必须已经是补过的样子（今天全红）。"""

    def test_a1_filled_material_lands_on_the_row(self):
        state = probe()["material"]
        self.assertEqual(
            FILLED_MATERIAL, state["value"],
            "补完材料后 `load_parts()` 的那一行材料仍是 %r —— 补录只写进了侧档 "
            "`packaging_part_material`，没有任何读路径把它合回零件行（Spec §2.1 第 1 条）"
            % state["value"])
        self.assertEqual("manual", state["kind"], "行上必须带 `material_source.kind = manual`（Spec §2.1 第 1 条）")
        self.assertTrue(state["ok"],
                        "补完材料后这一件仍不可算（%s）—— 同一件的「工艺推荐」会继续 409（Spec §2.1 第 2 条）"
                        % state["code"])

    def test_a2_summary_accounts_move_with_the_fill(self):
        metrics = probe()
        self.assertEqual(1, metrics["manual_total_after"],
                         "`summarize().material_manual_total` 仍是 %r —— 账按行现算，行没变账就不会变"
                         "（Spec §2.1 第 3 条）" % metrics["manual_total_after"])
        self.assertEqual((metrics["known_material_before"] or 0) + 1,
                         metrics["known_material_after"],
                         "`material_known_total` 没跟着 +1（%r → %r）（Spec §2.1 第 3 条）"
                         % (metrics["known_material_before"], metrics["known_material_after"]))
        # 探针里两件（只缺材料的 P01、只缺料厚的 P03）落在**同一个**原因桶
        # `PACKAGING_PART_MATERIAL_UNKNOWN`（`processability()` 对缺料厚也用这个码），
        # 所以补完两件之后这一桶必须**空掉**，而不是"减一"。
        # （2026-09-22 由实现轮记为测试侧偏差，此处按事实修正断言；Spec §2.1 第 3 条不变。）
        self.assertEqual(2, metrics["unknown_mix_before"],
                         "测试前提失效：补录前应当是 2 件落在缺材料/料厚这一桶")
        self.assertFalse(
            metrics["unknown_mix_after"],
            "补完材料与料厚之后，`unprocessable_reason_mix` 里不该还剩缺材料/料厚的件"
            "（现在是 %r）（Spec §2.1 第 3 条）" % (metrics["unknown_mix_after"],))

    def test_a3_filled_thickness_lands_on_the_row(self):
        metrics = probe()
        state = metrics["thickness"]
        self.assertEqual(FILLED_THICKNESS, state["value"],
                         "补完料厚后那一行的 `thickness_mm` 仍是 %r —— 料厚那套是同一个缺陷"
                         "（Spec §2.1 第 1 条）" % state["value"])
        self.assertEqual("manual", state["kind"], "行上必须带 `thickness_source.kind = manual`（Spec §2.1 第 1 条）")
        self.assertTrue(state["ok"], "补完料厚后这一件仍不可算（%s）（Spec §2.1 第 2 条）" % state["code"])
        self.assertEqual(1, metrics["thickness_manual_after"],
                         "`summarize().thickness_manual_total` 仍是 %r（Spec §2.1 第 3 条）"
                         % metrics["thickness_manual_after"])


class BGuards(unittest.TestCase):
    """B 组：今天就是绿的，不许被改红。"""

    def test_b1_side_documents_still_read_back_and_are_idempotent(self):
        mod = module()
        script = (
            "import json, %s as pp;"
            "a = pp.save_part_material('p-side', 'DWG-P01', '灰板', bound_by='PE1', reason='r');"
            "b = pp.save_part_material('p-side', 'DWG-P01', '灰板', bound_by='PE1', reason='r');"
            "import json as j;"
            "print(j.dumps({'same': a.get('record_hash') == b.get('record_hash'),"
            " 'readback': (pp.load_part_material('p-side', 'DWG-P01') or {}).get('spec'),"
            " 'thickness': (pp.save_part_thickness('p-side', 'DWG-P02', 1.5, bound_by='PE1')"
            " or {}).get('thickness_mm')}))" % PKG
        )
        with tempfile.TemporaryDirectory(prefix="cpq-fill-side-") as tmp:
            env = dict(os.environ, DATA_DIR=tmp, STORAGE_BACKEND="json")
            completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                       text=True, timeout=300, cwd=str(ROOT), env=env)
        self.assertEqual(0, completed.returncode,
                         "侧档读写跑不起来：%s" % (completed.stderr or completed.stdout)[-600:])
        payload = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
        self.assertTrue(payload.get("same"), "侧档幂等口径不许变（Spec §2.4 第 5 条）")
        self.assertEqual("灰板", payload.get("readback"), "侧档必须仍读得回（Spec §2.4 第 5 条）")
        self.assertEqual(1.5, payload.get("thickness"), "料厚侧档必须仍写得进（Spec §2.4 第 5 条）")

    def test_b2_pure_setters_still_copy_and_reject_bad_input(self):
        mod = module()
        row = {"part_code": "DWG-P01", "material": None, "thickness_mm": None,
               "attribution": {"kind": "", "material_unresolved": [],
                               "thickness_unresolved": []}}
        import copy
        before = copy.deepcopy(row)
        material = mod.set_manual_material(row, "灰板", bound_by="PE1")
        thickness = mod.set_manual_thickness(row, 1.2, bound_by="PE1")
        self.assertEqual(before, row, "两个 setter 都必须仍是纯函数（Spec §2.4 第 4 条）")
        self.assertEqual("灰板", mod._material_spec(material.get("material")))
        self.assertEqual(1.2, thickness.get("thickness_mm"))
        for bad in ("   ", None, ""):
            with self.assertRaises(ValueError):
                mod.set_manual_material(row, bad, bound_by="PE1")
        for bad in (0, -1, None, float("nan")):
            with self.assertRaises(ValueError):
                mod.set_manual_thickness(row, bad, bound_by="PE1")

    def test_b3_processability_and_card_columns_unchanged(self):
        mod = module()
        text = PARTS_PY.read_text(encoding="utf-8", errors="replace")
        columns = mod.card_columns()
        self.assertEqual(
            ["零件号", "名称", "角色", "材料", "厚度(mm)", "展开长(mm)", "展开宽(mm)",
             "轮廓状态", "可算", "不可算原因"], [col["label"] for col in columns],
            "卡片列定义是冻结面，本层不许动（Spec §2.4 第 7 条）")
        body = function_body(text, "card_row")
        self.assertTrue(body, "取不到 `card_row()` —— 测试前提失效")
        self.assertIn("processability", body,
                      "`card_row()` 的可算性必须仍**只**取自 `processability()`（Spec §2.4 第 7 条）")
        self.assertEqual("PACKAGING_PART_NOT_CLOSED",
                         mod.processability({"part_code": "DWG-P01", "outline_status": "open"})["code"],
                         "未闭合件的判据码不许变（Spec §2.4 第 7 条）")

    def test_b4_routes_guards_and_response_shape_unchanged(self):
        text = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for token in ("PACKAGING_PART_MATERIAL_PATH", "PACKAGING_PART_THICKNESS_PATH",
                      "workflow:packaging_part_material_bound",
                      "workflow:packaging_part_thickness_bound"):
            self.assertIn(token, text, "main.py 缺少 %s（Spec §2.4 第 7 条）" % token)
        for name in ("set_requirement_packaging_part_material",
                     "set_requirement_packaging_part_thickness"):
            body = function_body(text, name)
            self.assertTrue(body, "取不到 %s() —— 测试前提失效" % name)
            self.assertIn("BOX_MATCH_DECIDE_ROLES", body, "写权限不许放宽/收窄（Spec §2.4 第 7 条）")
            self.assertIn('"part": updated', body, "响应里仍要回 `part`（Spec §2.4 第 4 条）")


if __name__ == "__main__":
    unittest.main()
