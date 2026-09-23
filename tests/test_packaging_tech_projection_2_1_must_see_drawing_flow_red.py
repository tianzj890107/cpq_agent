"""红测：包装项目的技术侧流程投影必须认得图纸解析链路的产物（Spec §2/§3）。

Spec：`docs/specs/packaging-tech-projection-2-1-must-see-drawing-flow.md`

现状缺口（2026-09-23 在本机核对源码 + 34 实测，不是推断）：
  · `workflow_projection._LOADERS` 只登记 `store.load_ir`（**技术侧** DesignIR），
    没有任何包装图纸解析链路的取数项；
  · `_judge("2.1")` 只看 `facts["ir"]` → 包装 DWG 项目（IR 与零件写在 `cad_ir` /
    `packaging_parts` 两份文档里）永判 `not_started` + `["图纸还没有解析"]`；
  · `_rows_for()` 用"前面任何一个子步骤没完成"给后面每一步追加 `请先完成 X 标题`
    → 2.1 之后 12 步全部 `actionable=false`，`next_action` 永远指回 2.1；
  · 34 实测：项目 `8131f6d29d99` 解析 8/8、零件 263 件，投影仍报
    `2.1 not_started / 图纸还没有解析`，1.2/1.3/3.1… 全带
    `请先完成 2.1 图纸解析`。

只在**包装侧**扩展 2.1 的判据，技术侧口径与其它 12 步一字不动。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROJECTION_PY = ROOT / "tech_app" / "backend" / "services" / "workflow_projection.py"
STAGES_PY = ROOT / "tech_app" / "backend" / "services" / "workflow_stages.py"

PACKAGING_LOADER_NAMES = ("packaging_cad_ir", "packaging_parts")
EXPECTED_SUBS = ["1.1", "2.1", "1.2", "1.3", "3.1", "3.2", "3.3",
                 "4.1", "4.2", "4.3", "5.1", "5.2", "5.3"]
EXPECTED_STATUS_ENUM = (
    "not_started", "in_progress", "generated", "edited", "awaiting_confirmation",
    "confirmed", "blocked", "stale", "in_review", "approved", "published",
)
NO_PARTS_MISSING = "本次没有识别出零件，需人工确认无零件结果后方可继续"
NOT_PARSED_MISSING = "图纸还没有解析"
PRIOR_BLOCK_TEMPLATE = "请先完成 2.1 图纸解析"

# 34 现场那份包装产物的形状（只取判据需要的字段）。
PACK_IR = {"ir_id": "ir:8131f6d29d99", "units": "mm", "entities": 1204}
PACK_PARTS = {"parts": [{"part_code": "DWG-P01"}, {"part_code": "DWG-P02"}],
              "stats": {"part_total": 263}}
TECH_IR = {"parts": [{"id": "part-1"}]}

PROBE_SCRIPT = r'''
import json
import tech_app.backend.services.workflow_projection as wp
import tech_app.backend.services.workflow_stages as st

# 只替换两个有副作用/依赖外部模型的取数：成本汇总（要连库）与技术侧 IR 的模型解析。
# 判据本身（_judge / _rows_for / _next_action）一律走真实代码。
wp._cost_data = lambda pid, facts: None
wp._ir_model = lambda data: (type("IR", (), {"parts": list((data or {}).get("parts") or [])})()
                             if isinstance(data, dict) else None)


def facts(*, packaging_ir=None, packaging_parts=None, tech_ir=None,
          req_status="pending_review", audit=None):
    return {"errors": {}, "meta": {}, "requirement": {"status": req_status},
            "ir": tech_ir, "plan": None, "review": None, "summary": None,
            "report": {}, "audit": list(audit or []),
            "packaging_cad_ir": packaging_ir, "packaging_parts": packaging_parts}


out = {}
out["loaders"] = [name for name, _ in wp._LOADERS]
out["a2"] = wp._judge("2.1", "p-x", facts(packaging_ir=%(pack_ir)s, packaging_parts=%(pack_parts)s))

rows = wp._rows_for("p-x", facts(packaging_ir=%(pack_ir)s, packaging_parts=%(pack_parts)s),
                    "engineer")
out["rows"] = [{"key": r["key"], "status": r["status"], "completed": r["completed"],
                "actionable": r["actionable"], "missing": r["missing_requirements"],
                "blocked_reasons": r["blocked_reasons"]} for r in rows]
out["next_action"] = wp._next_action(rows)

rows_none = wp._rows_for("p-x", facts(), "engineer")
out["rows_none"] = [{"key": r["key"], "completed": r["completed"],
                     "blocked_reasons": r["blocked_reasons"]} for r in rows_none]
out["next_action_none"] = wp._next_action(rows_none)

out["b1"] = wp._judge("2.1", "p-x", facts())
out["b2"] = wp._judge("2.1", "p-x", facts(tech_ir=%(tech_ir)s))
empty_tech = {"parts": []}
out["b3_no_audit"] = wp._judge("2.1", "p-x", facts(tech_ir=empty_tech))
out["b3_audit"] = wp._judge("2.1", "p-x", facts(
    tech_ir=empty_tech, audit=[{"action": "parse_no_parts_confirmed"}]))
out["a5_no_audit"] = wp._judge("2.1", "p-x", facts(
    packaging_ir=%(pack_ir)s, packaging_parts={"parts": [], "stats": {"part_total": 0}}))
out["a5_audit"] = wp._judge("2.1", "p-x", facts(
    packaging_ir=%(pack_ir)s, packaging_parts={"parts": [], "stats": {"part_total": 0}},
    audit=[{"action": "parse_no_parts_confirmed"}]))
out["b4_draft"] = wp._judge("1.1", "p-x", facts(req_status="draft"))
out["b4_pending"] = wp._judge("1.2", "p-x", facts(req_status="pending_confirmation"))
out["subs"] = [row["sub"] for row in st.STAGES]
out["status_enum"] = list(wp.STATUS_ENUM)
print(json.dumps(out, ensure_ascii=False))
'''


def _probe() -> dict:
    script = PROBE_SCRIPT % {"pack_ir": json.dumps(PACK_IR, ensure_ascii=False),
                             "pack_parts": json.dumps(PACK_PARTS, ensure_ascii=False),
                             "tech_ir": json.dumps(TECH_IR, ensure_ascii=False)}
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(script)],
                          cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError("探针跑不起来（投影还没接包装来源时这是红的一部分）：\n"
                             + proc.stdout[-2000:] + proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


class PackagingProjection21Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _probe()

    # ---------------- A 组：包装项目必须认得（今天红） ----------------

    def test_a1_loaders_register_packaging_sources(self):
        names = list(self.out["loaders"])
        for name in PACKAGING_LOADER_NAMES:
            self.assertIn(name, names,
                          "投影的取数表里必须登记包装图纸解析链路的来源（Spec §3.1）："
                          "`cad_ir.load_ir` / `packaging_parts.load_parts`，"
                          "否则 2.1 永远看不到包装项目真的解析过。")

    def test_a2_2_1_completes_with_packaging_products(self):
        verdict = self.out["a2"]
        self.assertEqual(verdict.get("status"), "generated",
                         "包装项目解析完（IR + 263 件零件）时，2.1 的状态必须与技术侧"
                         "有零件时逐字一致（generated）：%r" % (verdict,))
        self.assertTrue(verdict.get("completed"), "2.1 必须判成已完成：%r" % (verdict,))
        self.assertEqual(list(verdict.get("missing") or []), [],
                         "解析产物齐了就不许再说缺东西：%r" % (verdict,))

    def test_a3_downstream_steps_not_blocked_by_2_1(self):
        rows = {row["key"]: row for row in self.out["rows"]}
        self.assertTrue(rows["2.1"]["completed"], "2.1 自己必须先算完成：%r" % (rows["2.1"],))
        for key in ("1.2", "1.3", "3.1", "3.2", "3.3"):
            self.assertNotIn(PRIOR_BLOCK_TEMPLATE, rows[key]["blocked_reasons"],
                             "%s 不许再被 2.1 挡住：%r" % (key, rows[key]))

    def test_a4_next_action_not_back_to_2_1(self):
        action = self.out["next_action"] or {}
        self.assertNotEqual(action.get("key"), "2.1",
                            "看板的下一步不许再指回 2.1 图纸解析：%r" % (action,))

    def test_a5_packaging_zero_parts_branch(self):
        """包装侧同一条"0 零件要人工确认"的分支（Spec §3.2 第 2 条）：今天完全没接。"""
        without = self.out["a5_no_audit"]
        self.assertEqual(without.get("status"), "generated", repr(without))
        self.assertFalse(without.get("completed"), repr(without))
        self.assertEqual(list(without.get("missing") or []), [NO_PARTS_MISSING], repr(without))
        with_audit = self.out["a5_audit"]
        self.assertEqual(with_audit.get("status"), "confirmed", repr(with_audit))
        self.assertTrue(with_audit.get("completed"), repr(with_audit))

    # ---------------- B 组：护栏（今天就是绿的，不许被改红） ----------------

    def test_b1_without_any_products_still_says_not_parsed(self):
        verdict = self.out["b1"]
        self.assertEqual(verdict.get("status"), "not_started", repr(verdict))
        self.assertFalse(verdict.get("completed"), repr(verdict))
        self.assertEqual(list(verdict.get("missing") or []), [NOT_PARSED_MISSING], repr(verdict))

    def test_b2_technical_ir_branch_unchanged(self):
        verdict = self.out["b2"]
        self.assertEqual(verdict.get("status"), "generated", repr(verdict))
        self.assertTrue(verdict.get("completed"), repr(verdict))

    def test_b3_zero_parts_needs_human_confirmation(self):
        """技术侧既有分支（有 IR、零件 0）逐字不变 —— 今天就是绿的。"""
        without = self.out["b3_no_audit"]
        self.assertFalse(without.get("completed"), repr(without))
        self.assertEqual(list(without.get("missing") or []), [NO_PARTS_MISSING], repr(without))
        self.assertEqual(without.get("status"), "generated", repr(without))
        with_audit = self.out["b3_audit"]
        self.assertTrue(with_audit.get("completed"), repr(with_audit))
        self.assertEqual(with_audit.get("status"), "confirmed", repr(with_audit))

    def test_b4_requirement_steps_unchanged(self):
        self.assertEqual(self.out["b4_draft"].get("status"), "in_progress",
                         repr(self.out["b4_draft"]))
        self.assertFalse(self.out["b4_draft"].get("completed"), repr(self.out["b4_draft"]))
        pending = self.out["b4_pending"]
        self.assertEqual(pending.get("status"), "awaiting_confirmation", repr(pending))
        self.assertEqual(list(pending.get("missing") or []), ["需求确认尚未提交"], repr(pending))

    def test_b5_stage_table_and_status_enum_unchanged(self):
        self.assertEqual(list(self.out["subs"]), EXPECTED_SUBS,
                         "13 个子步骤编号表是本 Spec 的冻结面")
        self.assertEqual(tuple(self.out["status_enum"]), EXPECTED_STATUS_ENUM,
                         "统一状态枚举是冻结面")

    def test_b6_prior_block_template_unchanged(self):
        """2.1 真没完成时，**还没完成**的行仍要逐字说「请先完成 2.1 图纸解析」。

        `## 461`（`tech-projection-step-state-must-agree-with-its-reasons.md` §2.1）之后，
        `completed=true` 的行不再挂前置文本 —— 本 fixture 的需求是 `pending_review`，
        `1.2` 因此已 `confirmed / completed=true`（原来它挂了这句，是因为旧实现**不分完成与否**
        一律追加，属"已完成的步骤挂着前置提示"那个自相矛盾）。这里改看同一份 `rows_none` 里真正
        未完成的 `1.3`：断言本身（前置文案逐字不变）没有被放宽，`2.1` 不完成时的提示一个词不改。
        """
        rows = {row["key"]: row for row in self.out["rows_none"]}
        self.assertFalse(rows["2.1"]["completed"], repr(rows["2.1"]))
        self.assertTrue(rows["1.2"]["completed"],
                        "本 fixture 里 1.2 在 pending_review 下已完成：%r" % (rows["1.2"],))
        row = rows["1.3"]
        self.assertFalse(row["completed"], "1.3 在本 fixture 里还没完成：%r" % (row,))
        self.assertIn(PRIOR_BLOCK_TEMPLATE, row["blocked_reasons"],
                      "2.1 真没完成时，前置阻断仍必须逐字说「请先完成 2.1 图纸解析」：%r"
                      % (row,))

    def test_b7_source_of_truth_is_the_projection_module(self):
        source = PROJECTION_PY.read_text(encoding="utf-8")
        self.assertIn("_rows_for", source)
        self.assertIn("请先完成", source, "前置阻断的唯一处仍是本模块，不许挪到前端")
        self.assertTrue(STAGES_PY.exists(), "13 步编号表仍来自 workflow_stages")


if __name__ == "__main__":
    unittest.main()
