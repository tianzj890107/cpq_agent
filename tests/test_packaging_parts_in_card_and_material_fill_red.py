"""红测：卡片上看得见拆出来的零件 + 缺材料的件补得进去。

Spec：`docs/specs/packaging-parts-in-card-and-material-fill.md`

现状缺口（34 真跑，2026-09-22，报价会话 `c0239386c1c4` / 技术项目 `a42e5e60a720` /
卡片 `3991598492811269436`，不是推断）：

  · 卡片第 6 步快照里确实有零件表（`packaging_parts.数据` 64 行），但报价卡片页渲染不出来：
    `GET /agents/quote/api/meta?industry=packaging` 的 `forms` 只有 17 节、`step` 只到 5，
    **没有第 6 步任何一节**；`确认需求解析结果.html` 的 `wfRestoreStepData()` 对 `FORMS`
    不认识的 section 直接 `return`（无声丢弃），并且该页 `grep "api/projects"` = 0 命中 ——
    卡片页自己也没读过技术项目接口；
  · 64 件里 51 件卡在 `PACKAGING_PART_MATERIAL_UNKNOWN`（`material_gap_mix.no_material_note=39`），
    而**材料**没有任何人工补录入口：`main.py` 只有 `.../packaging-parts/{part_code}/thickness`
    一条写路由，`packaging_parts.py` 只有 `set_manual_thickness` / `save_part_thickness` /
    `load_part_thickness`，没有 `set_manual_material` 一类；`summarize()` 有
    `thickness_manual_total` 却没有 `material_manual_total`；
  · `processability()` 只缺材料时的文案是
    `这一件缺材料/厚度：material（请在需求里补全后重跑解析）` —— 对只缺材料的件，这句话把
    用户指向"整体重跑八步"这条唯一出路（而那条路本身还有顺序陷阱）。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import copy
import importlib
import json
import os
import pathlib
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
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
QUOTE_HTML = ROOT / "确认需求解析结果.html"
SUITE_PY = ROOT / "cpq_agent_server.py"

MANUAL_KIND = "manual"
MATERIAL_PATH_TOKEN = "PACKAGING_PART_MATERIAL_PATH"
MATERIAL_INVALID_CODE = "PACKAGING_PART_MATERIAL_INVALID"
MATERIAL_AUDIT = "workflow:packaging_part_material_bound"

#: 卡片第 6 步零件表必须有的列（Spec §2.1 第 2 条）。
REQUIRED_PART_COLUMNS = ("零件号", "名称", "角色", "材料", "厚度(mm)",
                         "展开长(mm)", "展开宽(mm)", "轮廓状态", "可算", "不可算原因")

#: A4 护栏：既有固定表单目录（`/agents/quote/api/meta` 实测 17 节）一个都不许少。
EXISTING_FORM_IDS = ("s1_basic", "s1_dest", "s1_products", "s1_techparams", "s1_payment",
                     "s1_logistics", "s2_products", "s2_techparams", "s2_custom_spec",
                     "s2_packaging", "s2_packaging_cost", "s3_products", "s3_markup",
                     "s4_products", "s4_markup", "s5_basic", "s5_detail")


def module():
    return importlib.import_module(PKG)


def closed_row(part_code="DWG-P01", **overrides):
    """这一层要的是"已闭合、但缺材料/缺料厚"的行（下游判据的直接输入）。"""
    row = {"part_code": part_code, "name": "图纸零件 " + part_code,
           "outline_status": "closed", "outline_reason": "",
           "material": None, "material_source": None,
           "thickness_mm": None, "thickness_source": None,
           "unfolded_length_mm": 440.123, "unfolded_width_mm": 482.92,
           "attribution": {"kind": "", "material_unresolved": [], "thickness_unresolved": []}}
    row.update(overrides)
    return row


def html_text():
    return QUOTE_HTML.read_text(encoding="utf-8", errors="replace")


def restore_body():
    """`wfRestoreStepData()` 的函数体（找不到时返回空串，断言会给出明确失败原因）。"""
    text = html_text()
    marker = "async function wfRestoreStepData"
    start = text.find(marker)
    if start < 0:
        return ""
    end = text.find("\n    async function ", start + len(marker))
    return text[start:end] if end > 0 else text[start:start + 8000]


# --------------------------------------------------------------------------- #
# A 组：卡片第 6 步真的渲染得出零件表
# --------------------------------------------------------------------------- #
class ACardPartVisibility(unittest.TestCase):
    def test_a1_unknown_table_section_is_not_silently_dropped(self):
        """快照里 `kind == 'table'` 的未知节必须渲染，不许 `FORMS.find` 取不到就 return。"""
        body = restore_body()
        self.assertTrue(body, "取不到 wfRestoreStepData() —— 测试前提失效（Spec §2.1）")
        anchor = body.find("FORMS.find")
        self.assertGreaterEqual(anchor, 0, "wfRestoreStepData 必须仍然按 FORMS 找已知节")
        window = body[anchor:anchor + 800]
        generic = (("'table'" in window) or ('"table"' in window)) and ("renderTableSection" in window)
        catalog = "packaging_parts" in SUITE_PY.read_text(encoding="utf-8", errors="replace")
        self.assertTrue(generic or catalog,
                        "卡片页必须能渲染快照里 kind=table 的未知节（通用渲染），"
                        "或把 packaging_parts 登记进固定表单目录 —— 现在两条都没有（Spec §2.1 第 1 条）")

    def test_a2_card_page_resolves_project_and_reads_parts(self):
        text = html_text()
        for token in ("/api/projects", "source_session_id", "packaging-parts"):
            self.assertIn(token, text,
                          "卡片页必须按会话反查技术项目并读零件端点，缺 %s（Spec §2.1 第 4 条）" % token)

    def test_a3_part_table_declares_the_required_columns(self):
        sources = [html_text()]
        for path in (PARTS_PY, MAIN_PY, SUITE_PY):
            sources.append(path.read_text(encoding="utf-8", errors="replace"))
        blob = "\n".join(sources)
        missing = [col for col in REQUIRED_PART_COLUMNS if col not in blob]
        self.assertEqual([], missing,
                         "卡片零件表缺少这些列：%s（Spec §2.1 第 2 条）" % "、".join(missing))

    def test_a4_existing_form_catalog_is_intact(self):
        """护栏：不许为了让第 6 步显示零件而改坏既有 17 节固定表单。"""
        text = SUITE_PY.read_text(encoding="utf-8", errors="replace")
        missing = [sid for sid in EXISTING_FORM_IDS if sid not in text]
        self.assertEqual([], missing, "既有固定表单节被改掉：%s" % "、".join(missing))


# --------------------------------------------------------------------------- #
# B 组：材料的人工补录（料厚那套的镜像）
# --------------------------------------------------------------------------- #
class BMaterialFill(unittest.TestCase):
    def test_b1_pure_function_copies_and_rejects_blank(self):
        setter = getattr(module(), "set_manual_material", None)
        self.assertTrue(callable(setter),
                        "packaging_parts 必须提供 set_manual_material()（Spec §2.2 第 1 条）")
        row = closed_row()
        before = copy.deepcopy(row)
        updated = setter(row, "灰板", bound_by="PE1", reason="图纸未标材料")
        self.assertEqual("灰板", module()._material_spec(updated.get("material")))
        source = updated.get("material_source") or {}
        self.assertEqual(MANUAL_KIND, source.get("kind"))
        self.assertEqual("PE1", source.get("bound_by"))
        self.assertEqual(before, row, "不许原地改入参（Spec §2.2 第 1 条）")
        with self.assertRaises(ValueError):
            setter(row, "   ", bound_by="PE1")

    def test_b2_persistence_helpers_exist_and_are_idempotent(self):
        mod = module()
        for name in ("save_part_material", "load_part_material"):
            self.assertTrue(callable(getattr(mod, name, None)),
                            "packaging_parts 必须提供 %s()（Spec §2.2 第 2 条）" % name)
        self.assertTrue(getattr(mod, "DOC_KEY_MATERIAL", ""),
                        "人工材料必须走独立的文档通道常量 DOC_KEY_MATERIAL（Spec §2.2 第 2 条）")
        script = (
            "import json, %s as pp;"
            "a = pp.save_part_material('p-red', 'DWG-P01', '灰板', bound_by='PE1', reason='r');"
            "b = pp.save_part_material('p-red', 'DWG-P01', '灰板', bound_by='PE1', reason='r');"
            "print(json.dumps({'same': a.get('record_hash') == b.get('record_hash'),"
            " 'readback': (pp.load_part_material('p-red', 'DWG-P01') or {}).get('spec')}))"
            % PKG
        )
        with tempfile.TemporaryDirectory(prefix="cpq-mat-fill-") as tmp:
            env = dict(os.environ, DATA_DIR=tmp)
            completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                       text=True, timeout=300, cwd=str(ROOT), env=env)
        self.assertEqual(0, completed.returncode,
                         "save_part_material/load_part_material 跑不起来：%s"
                         % (completed.stderr or completed.stdout)[-600:])
        payload = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
        self.assertTrue(payload.get("same"), "同一 (件, 材料, 人, 理由) 必须幂等（Spec §2.2 第 2 条）")
        self.assertEqual("灰板", payload.get("readback"), "人工材料必须读得回来（Spec §2.2 第 2 条）")

    def test_b3_routes_and_write_guard_are_wired(self):
        text = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for token in (MATERIAL_PATH_TOKEN, "BOX_MATCH_DECIDE_ROLES", MATERIAL_INVALID_CODE,
                      MATERIAL_AUDIT):
            self.assertIn(token, text, "main.py 缺少 %s（Spec §2.2 第 3 条）" % token)
        self.assertIn("/material", text, "必须注册 .../packaging-parts/{part_code}/material（Spec §2.2 第 3 条）")

    def test_b4_filled_material_unblocks_this_part_only(self):
        mod = module()
        setter = getattr(mod, "set_manual_material", None)
        self.assertTrue(callable(setter), "先要有 set_manual_material()（Spec §2.2 第 1 条）")
        row = closed_row()
        first = mod.processability(row)
        self.assertEqual("PACKAGING_PART_MATERIAL_UNKNOWN", first.get("code"))
        self.assertIn("material", first.get("missing_variables") or [])
        with_material = setter(row, "灰板", bound_by="PE1")
        second = mod.processability(with_material)
        self.assertNotIn("material", second.get("missing_variables") or [],
                         "补过材料之后不许再报缺 material（Spec §2.2 第 4 条）")
        self.assertEqual(["thickness_mm"], second.get("missing_variables"),
                         "只补材料时应当只报缺 thickness_mm（Spec §2.2 第 4 条）")
        third = mod.processability(mod.set_manual_thickness(with_material, 2.0, bound_by="PE1"))
        self.assertTrue(third.get("ok"), "材料 + 料厚都补齐后这一件必须可算（Spec §2.2 第 4 条）")

    def test_b5_summary_counts_manual_material_separately(self):
        mod = module()
        manual = closed_row("DWG-P01", material={"spec": "灰板"},
                            material_source={"kind": "manual", "text": "人工补", "bound_by": "PE1"})
        evidenced = closed_row("DWG-P02", material={"spec": "灰板"},
                               material_source={"kind": "part_note", "text": "2mm灰板"})
        summary = mod.summarize({"parts": [manual, evidenced]})
        self.assertIn("material_manual_total", summary,
                      "summarize() 必须新增 material_manual_total（Spec §2.2 第 5 条）")
        self.assertEqual(1, summary.get("material_manual_total"),
                         "人工补的材料要单独数（Spec §2.2 第 5 条）")
        self.assertEqual(2, summary.get("material_known_total"),
                         "人工补的也算已知材料（Spec §2.2 第 5 条）")
        self.assertAlmostEqual(0.5, summary.get("material_evidence_ratio") or 0.0, places=6,
                               msg="人工值不许抬高 material_evidence_ratio（Spec §2.2 第 5 条）")


# --------------------------------------------------------------------------- #
# C 组：卡住的时候要说清"怎么补"
# --------------------------------------------------------------------------- #
class CBlockMessage(unittest.TestCase):
    def test_c1_message_points_at_the_in_page_fix(self):
        message = module().processability(closed_row()).get("message") or ""
        self.assertIn("补材料", message,
                      "缺材料的件必须指向件级「补材料」，不许只写重跑解析（Spec §2.3 第 1 条）")
        only_thickness = module().processability(closed_row(material={"spec": "灰板"})).get("message") or ""
        self.assertIn("补料厚", only_thickness,
                      "只缺料厚的件必须指向「补料厚」（Spec §2.3 第 1 条）")
        self.assertNotIn("重跑解析", message,
                         "不许把唯一出路写成「回需求补全后重跑解析」（Spec §2.3 第 1 条）")

    def test_c2_frontend_material_fix_entry_exists(self):
        text = APP_JS.read_text(encoding="utf-8", errors="replace")
        self.assertIn("part-thickness-fix", text, "测试前提：既有补料厚控件必须还在")
        self.assertIn("part-material-fix", text,
                      "零件行材料为空时必须有同范式的补材料控件（Spec §2.3 第 2 条）")
        self.assertIn("/material", text, "补材料控件必须调 .../material（Spec §2.3 第 2 条）")


# --------------------------------------------------------------------------- #
# D 组：护栏
# --------------------------------------------------------------------------- #
class DGuards(unittest.TestCase):
    def test_d1_not_closed_is_still_refused(self):
        result = module().processability(closed_row(outline_status="open", outline_reason="odd_endpoints"))
        self.assertFalse(result.get("ok"))
        self.assertEqual("PACKAGING_PART_NOT_CLOSED", result.get("code"),
                         "未闭合件仍然 409，不许拿包围盒硬排工艺（Spec §3）")

    def test_d2_unknown_role_is_never_autobound(self):
        text = PARTS_PY.read_text(encoding="utf-8", errors="replace")
        self.assertIn("reject_unknown_role_autobind", text,
                      "不借「补材料」自动贴业务角色（Spec §3）")


if __name__ == "__main__":
    unittest.main()
