"""红测：从报价开始的包装 DWG 终验（E2E 链路 / Go-No-Go / 报告与回滚）。

Spec：`docs/specs/quote-first-final-acceptance.md`（新五批 · 第 5 批 · 终验）。

现状缺口（本地只读实测）：

  · 全仓 `grep -rn "ROLE_CHAIN|go_no_go|GO_BLOCKERS"` → **0**：从报价开始的五角色交接
    （销售 → 工艺 → 财务 → 工艺 → 销售）散在现场记录里，仓库没有任何一处声明
    "这一步的账号、前置门禁、必须留下的证据"。
  · `dwg_deploy_gate.GATE_ITEMS` 已 19 项，但**全是转换器侧**的：没有任何一条覆盖
    "项目是否真的从报价开始""知识库是否权威数据""包装闭环是否真的走通"
    "历史会话是否恢复""stale 结果是否被当成有效报价"。
  · `dwg_acceptance.SUPPORT_CLAIMS` 已禁止 "real" 这类模糊声明，金标目录
    `tests/fixtures/dwg_acceptance/2026-09-21.1/` 也在位 —— 缺的是把"能力声明"
    挂到这条业务验收链路上，以及 Go/No-Go 的纯函数判定。
  · `DEPLOYMENT.md` 已有第 1 批的转换器小节与第 2 批的知识库小节，**没有**终验小节。

纪律：本批是终验的**判定口径**，不授权部署 —— 红测只读源码、常量与文档，
不连服务器、不调模型、不写业务数据、不执行任何部署命令。

禁止为了让红测转绿而修改本文件，也禁止自动更新金标来让红测转绿。
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC = ROOT / "docs" / "specs" / "quote-first-final-acceptance.md"
MODULE_PY = ROOT / "tech_app" / "tools" / "quote_first_acceptance.py"
DEPLOYMENT_MD = ROOT / "DEPLOYMENT.md"
GOLDEN_ROOT = ROOT / "tests" / "fixtures" / "dwg_acceptance"

#: 五角色交接链路（首尾都是销售经理）。
ROLE_CHAIN = ("sales_mgr", "process_mgr", "finance_mgr", "process_mgr", "sales_mgr")

#: §2 的 12 步 key（顺序即执行顺序）。
STEP_KEYS = ("quote_create", "requirement_fill", "box_match", "tech_handoff", "dwg_parse",
             "params", "bom", "route", "cost", "report", "back_to_quote", "history_recover")

#: §3 的门禁 id（10 项，语义齐全）。
GATE_IDS = ("entry_from_quote", "business_case_linked", "kb_authoritative",
            "dwg_parsed_natively", "packaging_closure_complete", "cost_traceable",
            "stale_not_published", "history_recovery", "role_chain_handoff",
            "golden_approved")
GATE_KINDS = ("auto", "manual")

#: §5 的报告必含字段与金标人工业务小节。
REPORT_FIELDS_REQUIRED = ("golden_version", "converter", "samples", "steps", "gate_verdict",
                          "blockers", "claim", "approved_by", "approved_at", "rollback_plan")
GOLDEN_SECTIONS_REQUIRED = ("unit_status", "cut_layer", "crease_layer", "box_type_candidates",
                            "key_dimensions", "pending_confirmations",
                            "forbidden_hallucinations", "downstream_snapshot",
                            "quote_draft_allowed", "three_d_status")

#: 关键证据键的抽样（每步证据不许是空壳）。
EVIDENCE_SPOT = {"quote_create": "business_case_id", "tech_handoff": "source_task_id",
                 "dwg_parse": "converter_version", "back_to_quote": "handoff_id",
                 "history_recover": "chat_turns"}

CLAIM_GO = "包装行业 DWG 支持完成"
CLAIM_ORCHESTRATION = "DWG 编排能力完成，真实转换能力未验收"


def setUpModule():
    if not SPEC.exists():
        raise AssertionError(f"缺少本批 Spec：{SPEC.relative_to(ROOT)}")


def must(condition, message: str):
    if not condition:
        raise AssertionError(message)


def read(path) -> str:
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def acceptance():
    """`tech_app/tools/quote_first_acceptance.py`（本批的唯一入口）。"""
    if not MODULE_PY.exists():
        raise AssertionError(
            "缺少 tech_app/tools/quote_first_acceptance.py（Spec §2）—— 终验链路、门禁清单与 "
            "Go/No-Go 判定必须有一处唯一口径，现在散在现场记录里")
    if str(ROOT / "tech_app" / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tech_app" / "tools"))
    try:
        return importlib.import_module("quote_first_acceptance")
    except Exception as exc:                                # pragma: no cover - 缺口路径
        raise AssertionError(f"quote_first_acceptance 不能导入：{exc}") from exc


def steps():
    module = acceptance()
    rows = getattr(module, "STEPS", None)
    must(isinstance(rows, (list, tuple)) and rows, "STEPS 必须是非空的 12 步清单（Spec §2）")
    return list(rows)


def go_no_go(evidence):
    module = acceptance()
    fn = getattr(module, "go_no_go", None)
    must(callable(fn), "必须以纯函数 go_no_go(evidence) 给出 Go/No-Go 判定（Spec §4）")
    return fn(evidence)


def all_pass() -> dict:
    return {
        "samples": ("酒盒.dwg", "圆盘盒.dwg"),
        "steps": tuple(item.get("key") for item in steps()),
        "entry_origin": "quote",
        "industry": "packaging",
        "kb_source_types": ("workbook", "dwg_confirmed"),
        "dwg_converted_native": True,
        "packaging_closure": True,
        "real_converter": True,
        "history_recovered": True,
        "stale_published": False,
        "permission_ok": True,
    }


# ===========================================================================
# A. 验收链路
# ===========================================================================
class AcceptanceStepsTest(unittest.TestCase):
    def test_a1_module_and_version(self):
        module = acceptance()
        version = str(getattr(module, "MODULE_VERSION", "") or "")
        self.assertTrue(version.strip(), "必须声明 MODULE_VERSION（验收口径的版本）")
        self.assertTrue(list(getattr(module, "ROLE_CHAIN", ()) or ()), "必须声明 ROLE_CHAIN")

    def test_a2_twelve_steps_in_order(self):
        rows = steps()
        self.assertEqual(12, len(rows), f"从报价开始的验收链路必须是 12 步，实测 {len(rows)} 步")
        self.assertEqual(list(range(1, 13)), [int(row.get("no") or 0) for row in rows],
                         "序号必须从 1 连续到 12（顺序即执行顺序）")
        self.assertEqual(list(STEP_KEYS), [str(row.get("key") or "") for row in rows],
                         "12 步的 key 与顺序必须与 Spec §2 的表一致")

    def test_a3_step_keys_are_unique_and_titled(self):
        rows = steps()
        keys = [str(row.get("key") or "") for row in rows]
        self.assertEqual(len(keys), len(set(keys)), f"步骤 key 必须唯一：{keys}")
        for row in rows:
            self.assertTrue(str(row.get("title") or "").strip(),
                            f"步骤 {row.get('key')!r} 缺 title")

    def test_a4_roles_stay_on_the_handoff_chain(self):
        module = acceptance()
        self.assertEqual(list(ROLE_CHAIN), list(getattr(module, "ROLE_CHAIN", ()) or ()),
                         "五角色交接链路必须是 销售 → 工艺 → 财务 → 工艺 → 销售")
        for row in steps():
            self.assertIn(str(row.get("role") or ""), ROLE_CHAIN,
                          f"步骤 {row.get('key')!r} 的角色 {row.get('role')!r} 不在交接链路上")

    def test_a5_every_step_has_a_gate_and_evidence(self):
        for row in steps():
            key = str(row.get("key") or "")
            self.assertTrue(str(row.get("gate") or "").strip(),
                            f"步骤 {key!r} 没有前置门禁 —— 没有门禁的步骤不算验收步骤")
            evidence = list(row.get("evidence") or [])
            self.assertTrue(evidence, f"步骤 {key!r} 没有必须留下的证据")
            self.assertTrue(all(str(item).strip() for item in evidence),
                            f"步骤 {key!r} 的证据键里有空值")
        by_key = {str(row.get("key") or ""): list(row.get("evidence") or []) for row in steps()}
        for key, needle in EVIDENCE_SPOT.items():
            self.assertIn(needle, by_key.get(key, []),
                          f"步骤 {key!r} 的证据必须含 {needle}")
        fields = list(getattr(acceptance(), "REPORT_FIELDS", ()) or ())
        for name in ("steps", "samples"):
            self.assertIn(name, fields,
                          f"报告字段必须含 {name}（每步证据随它留档，Spec §2.4）")


# ===========================================================================
# B. 门禁清单（与第 1 批互补）
# ===========================================================================
class GateItemsTest(unittest.TestCase):
    def test_b1_at_least_ten_unique_items(self):
        gates = list(getattr(acceptance(), "GATE_ITEMS", ()) or ())
        self.assertGreaterEqual(len(gates), 10, "终验门禁至少 10 项（Spec §3）")
        ids = [str(item[0]) for item in gates]
        self.assertEqual(len(ids), len(set(ids)), f"门禁 id 必须唯一：{ids}")

    def test_b2_kinds_come_from_the_closed_set(self):
        for item in getattr(acceptance(), "GATE_ITEMS", ()) or ():
            self.assertEqual(3, len(item), f"门禁项必须是 (id, kind, title)：{item!r}")
            self.assertIn(str(item[1]), GATE_KINDS, f"门禁 {item[0]!r} 的 kind 非法：{item[1]!r}")
            self.assertTrue(str(item[2]).strip(), f"门禁 {item[0]!r} 缺标题")

    def test_b3_the_ten_semantics_are_all_covered(self):
        ids = {str(item[0]) for item in getattr(acceptance(), "GATE_ITEMS", ()) or ()}
        missing = [gate_id for gate_id in GATE_IDS if gate_id not in ids]
        self.assertEqual([], missing, f"终验门禁缺这几条语义：{missing}")

    def test_b4_does_not_duplicate_the_converter_gate(self):
        deploy = importlib.import_module("tech_app.tools.dwg_deploy_gate")
        existing = {str(item[0]) for item in deploy.GATE_ITEMS}
        mine = {str(item[0]) for item in getattr(acceptance(), "GATE_ITEMS", ()) or ()}
        overlap = sorted(existing & mine)
        self.assertEqual([], overlap,
                         f"这些 id 与第 1 批的转换器门禁重复：{overlap}；"
                         "终验只补业务链路那几条，不复制转换器门禁")


# ===========================================================================
# C. Go / No-Go
# ===========================================================================
class GoNoGoTest(unittest.TestCase):
    def test_c1_all_pass_is_go(self):
        out = go_no_go(all_pass())
        self.assertEqual("go", str(out.get("verdict") or ""))
        self.assertEqual([], list(out.get("blockers") or []))
        self.assertEqual(CLAIM_GO, str(out.get("claim") or ""))

    def test_c2_fake_converter_can_only_claim_orchestration(self):
        evidence = all_pass()
        evidence["real_converter"] = False
        out = go_no_go(evidence)
        self.assertEqual("no_go", str(out.get("verdict") or ""),
                         "真实转换器未验收时不许给 go")
        self.assertIn("real_converter_unverified", list(out.get("blockers") or []))
        self.assertEqual(CLAIM_ORCHESTRATION, str(out.get("claim") or ""),
                         "只完成 fake converter 测试时，声明只能是"
                         "「DWG 编排能力完成，真实转换能力未验收」")

    def test_c3_missing_steps_or_wrong_entry_are_blockers(self):
        evidence = all_pass()
        evidence["steps"] = list(STEP_KEYS)[:9]
        self.assertIn("steps_incomplete", list(go_no_go(evidence).get("blockers") or []))
        evidence = all_pass()
        evidence["entry_origin"] = "internal_test"
        self.assertIn("entry_not_from_quote", list(go_no_go(evidence).get("blockers") or []),
                      "项目不是从报价开始时必须阻断 —— 这正是现场卡死的根因")
        evidence = all_pass()
        evidence["kb_source_types"] = ("demo",)
        self.assertIn("kb_not_authoritative", list(go_no_go(evidence).get("blockers") or []),
                      "知识库还只有演示数据时不许给 go")
        evidence = all_pass()
        evidence["stale_published"] = True
        self.assertIn("stale_results_published", list(go_no_go(evidence).get("blockers") or []),
                      "stale 结果被当成有效报价发布时必须阻断")

    def test_c4_blockers_stay_in_the_closed_set_and_are_pure(self):
        module = acceptance()
        closed = tuple(getattr(module, "GO_BLOCKERS", ()) or ())
        self.assertTrue(closed, "必须声明 GO_BLOCKERS 闭集")
        evidence = all_pass()
        evidence["real_converter"] = False
        evidence["packaging_closure"] = False
        evidence["history_recovered"] = False
        first = module.go_no_go(evidence)
        second = module.go_no_go(evidence)
        self.assertEqual(first, second, "同输入必须同输出（纯函数）")
        self.assertIsNot(first, second, "每次调用必须返回新的 dict")
        stray = [item for item in first["blockers"] if item not in closed]
        self.assertEqual([], stray, f"blockers 出现了闭集外的值：{stray}")
        order = {name: index for index, name in enumerate(closed)}
        self.assertEqual(sorted(first["blockers"], key=lambda x: order[x]), list(first["blockers"]),
                         "blockers 必须按 GO_BLOCKERS 的声明顺序稳定输出")
        self.assertEqual(False, evidence["packaging_closure"], "不许改入参")
        source = read(MODULE_PY)
        self.assertEqual([], re.findall(r"^\s*(?:from|import)\s+\S*\b(?:psycopg|sqlite3|requests)\b",
                                        source, re.M),
                         "判定必须是纯函数：不读库、不联网")


# ===========================================================================
# D. 报告模板与金标
# ===========================================================================
class ReportAndGoldenTest(unittest.TestCase):
    def test_d1_report_fields(self):
        fields = list(getattr(acceptance(), "REPORT_FIELDS", ()) or ())
        missing = [name for name in REPORT_FIELDS_REQUIRED if name not in fields]
        self.assertEqual([], missing, f"验收报告缺这些字段：{missing}")

    def test_d2_golden_business_sections(self):
        sections = list(getattr(acceptance(), "GOLDEN_BUSINESS_SECTIONS", ()) or ())
        missing = [name for name in GOLDEN_SECTIONS_REQUIRED if name not in sections]
        self.assertEqual([], missing,
                         f"金标必须由人工填写的业务小节缺：{missing}（Spec §5.2）")

    def test_d3_reuses_the_existing_approval_contract(self):
        source = read(MODULE_PY)
        for key in ("approved_by", "approved_at"):
            self.assertIn(key, source,
                          f"金标审批必须沿用 dwg_acceptance 的 {key} 口径，不另立一套")
        accept = importlib.import_module("tech_app.backend.services.dwg_acceptance")
        self.assertIn("unapproved", tuple(accept.ACCEPTANCE_REASONS),
                      "dwg_acceptance 的未审批原因码不许消失")

    def test_d4_golden_directory_is_in_place(self):
        manifests = sorted(GOLDEN_ROOT.glob("*/manifest.json")) if GOLDEN_ROOT.exists() else []
        must(manifests, f"金标目录不在位：{GOLDEN_ROOT.relative_to(ROOT)}/<golden_version>/manifest.json")
        import json
        data = json.loads(manifests[-1].read_text(encoding="utf-8"))
        for key in ("golden_version", "converter", "samples"):
            self.assertIn(key, data, f"金标 manifest 缺 {key}")
        self.assertTrue(data["samples"], "金标 manifest 必须带两份样本")
        for name, row in data["samples"].items():
            self.assertTrue(str(row.get("source_sha256") or ""), f"{name} 缺 source_sha256")
        self.assertEqual(
            [], [name for name, row in data["samples"].items()
                 if not (GOLDEN_ROOT / data["golden_version"] / str(row.get("golden_file") or ""))
                 .exists()],
            "金标 manifest 指向的样本 JSON 必须真实存在")


# ===========================================================================
# E. 文档与既有机制护栏
# ===========================================================================
class DocAndGuardTest(unittest.TestCase):
    def test_e1_deployment_doc_has_the_final_acceptance_section(self):
        text = read(DEPLOYMENT_MD)
        must(text, "DEPLOYMENT.md 不存在")
        hit = re.search(r"^#{2,3}[^\n]*从报价开始[^\n]*$", text, re.M)
        self.assertIsNotNone(hit, "DEPLOYMENT.md 必须有「从报价开始的终验」小节（Spec §6）")
        start = hit.start()
        section = text[start:start + 6000]
        for key in STEP_KEYS[:4]:
            self.assertIn(key, section, f"终验小节必须写明验收链路（缺步骤 key {key}）")
        self.assertIn("go_no_go", section, "终验小节必须写明 Go/No-Go 判定入口")
        self.assertIn(CLAIM_ORCHESTRATION, section,
                      "终验小节必须写明「只完成 fake converter 时」的声明原文")
        self.assertIn("回滚", section, "终验小节必须写明失败回滚策略")

    def test_e2_support_claims_stay_conservative(self):
        accept = importlib.import_module("tech_app.backend.services.dwg_acceptance")
        claims = tuple(accept.SUPPORT_CLAIMS)
        self.assertNotIn("real", claims, "含糊的 real 声明必须继续被禁止")
        self.assertIn("orchestration_only", claims)
        self.assertIn("supported", claims)

    def test_e3_converter_gate_is_not_reduced(self):
        deploy = importlib.import_module("tech_app.tools.dwg_deploy_gate")
        ids = [str(item[0]) for item in deploy.GATE_ITEMS]
        self.assertGreaterEqual(len(ids), 19, "第 1 批的转换器门禁不许可减项")
        self.assertIn("converter_rollout_documented", ids,
                      "第 1 批的「部署文档已写明转换器配置」门禁必须还在")


if __name__ == "__main__":
    unittest.main()
