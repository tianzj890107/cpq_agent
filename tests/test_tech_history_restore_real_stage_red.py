"""红测：历史记录 / 首页卡片恢复项目时落到真实当前阶段。

现状缺口（实测）：
  · `tech_app/frontend/tech-workbench.js:1144` 的 `techStageFromProject()` 只判断
    「需求状态 → 是否有报告 → 是否有 IR」，只要有 IR、还没出报告就一律返回 `process`；
    2.2 参数/工艺是否确认、是否已发送财务、2.3 是否已有成本、成本是否确认、是否已退回
    工艺经理复核，一个都没看。
  · `报价首页.html` 的 `techStageFromFlow()` 是同一份判定的第二份现役拷贝（首页卡片
    点开时用它拼 stage），缺陷与后果一致。
  · 于是：做到 2.3 甚至已测完成本的项目，从历史记录 / 首页点开都会退回 2.2；
    财务经理重开自己的项目也进 2.2 而不是 2.3。

本批把判定收敛成唯一一份纯函数 `window.TechStageRestore.fromSignals(flow, projectData,
signals)`（`tech_app/frontend/tech-stage-restore.js`），两个入口改为取真实数据后委托它。
行为断言用 node 真实执行这个纯函数（不是静态文本提取），覆盖 2.1 → 2.2 → 2.3 → 3.x 全矩阵。

（`tech_app/frontend/home.js` 里还有第三份拷贝，但 `home.html` 已不再加载 `home.js`，
属停用代码，本批不动。）
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONT = ROOT / "tech_app" / "frontend"
SHARED = FRONT / "tech-stage-restore.js"
WORKBENCH = FRONT / "tech-workbench.js"
CPQ_HOME = ROOT / "报价首页.html"

STAGES = ('requirement-create', 'requirement-confirm', 'requirement-review', 'drawing',
          'process', 'cost', 'summary', 'report-review', 'report-publish')
IR = {"parts": [{"part_id": "P1", "name": "壳体"}]}
NO_IR = {}
PLAN_2_2_COST_ONLY = {"cost": {"quantity": 1, "items": [{"category": "material",
                                                          "amount": 120.0}]}}
PLAN_CONFIRMED = {"params_confirmed": True, "process_confirmed": True, "cost": {"items": [{}]}}
PLAN_SENT_TO_FINANCE = {"params_confirmed": True, "process_confirmed": True,
                        "finance_handoff": {"task_id": "T1", "sent_at": "2026-09-15 10:00:00"}}
COST_CONFIRMED = {"confirmed": True, "confirmed_by": "李财务",
                  "confirmed_at": "2026-09-15 11:00:00", "actions": []}
COST_RETURNED = {"confirmed": True, "confirmed_by": "李财务",
                 "actions": [{"kind": "return-to-process", "label": "退回工艺经理复核"}]}
COST_QUOTED = {"confirmed": True, "actions": [{"kind": "send-to-quote",
                                               "label": "回传销售经理继续报价"}]}
COST_NOTE_ONLY = {"note": "先按 1 台核算", "received_at": "2026-09-15 09:00:00"}
COST_DETAIL_PART_COSTED = {"ready": False, "parts": [{"id": "P1", "has_cost": True,
                                                      "unit_cost": 88.0}]}
COST_DETAIL_READY = {"ready": True, "parts": [{"id": "P1", "has_cost": True}],
                     "final": {"total": 88.0}}


def signals(integration=None, cost_review=None, cost_detail=None) -> dict:
    return {"integration": integration or {}, "cost_review": cost_review or {},
            "cost_detail": cost_detail or {}}


def flow(status="approved", report=None) -> dict:
    out = {"requirement": {"status": status}}
    if report is not None:
        out["report"] = report
    return out


# (用例名, flow, projectData, signals, 期望 stage)
CASES = [
    ("requirement_draft", flow("draft"), {"ir": IR}, signals(), "requirement-create"),
    ("requirement_rejected", flow("rejected"), {"ir": IR}, signals(), "requirement-create"),
    ("requirement_pending_confirm", flow("pending_confirmation"), {"ir": IR}, signals(),
     "requirement-confirm"),
    ("requirement_pending_review", flow("pending_review"), {"ir": IR}, signals(),
     "requirement-review"),
    ("report_draft", flow("approved", {"status": "draft"}), {"ir": IR},
     signals(COST_CONFIRMED), "summary"),
    ("report_in_review", flow("approved", {"status": "in_review"}), {"ir": IR}, signals(),
     "report-review"),
    ("report_approved", flow("approved", {"status": "approved"}), {"ir": IR}, signals(),
     "report-publish"),
    ("report_published", flow("approved", {"status": "published"}), {"ir": IR}, signals(),
     "report-publish"),
    ("legacy_without_requirement", flow(""), NO_IR, signals(), "requirement-create"),
    ("legacy_without_requirement_but_parsed", flow(""), {"ir": IR}, signals(), "process"),
    ("no_ir_at_all", flow("approved"), NO_IR, signals(), "drawing"),
    ("only_ir", flow("approved"), {"ir": IR}, signals(), "process"),
    ("params_confirmed_but_not_sent", flow("approved"), {"ir": IR},
     signals(PLAN_CONFIRMED), "process"),
    ("process_confirmed_but_not_sent", flow("approved"), {"ir": IR},
     signals({"params_confirmed": True, "process_confirmed": True}), "process"),
    ("cost_sent_to_finance", flow("approved"), {"ir": IR},
     signals(PLAN_SENT_TO_FINANCE), "cost"),
    ("only_2_2_machine_cost", flow("approved"), {"ir": IR},
     signals(PLAN_2_2_COST_ONLY), "process"),
    ("cost_confirmed", flow("approved"), {"ir": IR}, signals(cost_review=COST_CONFIRMED),
     "cost"),
    ("cost_quoted_but_not_returned", flow("approved"), {"ir": IR},
     signals(cost_review=COST_QUOTED), "cost"),
    ("cost_note_saved", flow("approved"), {"ir": IR}, signals(cost_review=COST_NOTE_ONLY),
     "cost"),
    ("part_cost_measured", flow("approved"), {"ir": IR},
     signals(cost_detail=COST_DETAIL_PART_COSTED), "cost"),
    ("cost_ready", flow("approved"), {"ir": IR}, signals(cost_detail=COST_DETAIL_READY),
     "cost"),
    ("returned_to_process_manager", flow("approved"), {"ir": IR},
     signals(cost_review=COST_RETURNED), "summary"),
    ("cost_stage_without_ir", flow("approved"), NO_IR,
     signals(PLAN_SENT_TO_FINANCE), "cost"),
]

HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    globalThis.window = globalThis.window || {};
    eval(fs.readFileSync(process.argv[1], 'utf8'));
    const api = globalThis.window.TechStageRestore || globalThis.TechStageRestore;
    if (!api || typeof api.fromSignals !== 'function') {
      console.error('NO_API: window.TechStageRestore.fromSignals 缺失');
      process.exit(3);
    }
    const cases = JSON.parse(process.argv[2]);
    const out = {};
    for (const row of cases) {
      out[row[0]] = api.fromSignals(row[1], row[2], row[3]);
    }
    process.stdout.write(JSON.stringify(out));
    """
).strip()


def js_body(source: str, marker: str) -> str:
    idx = source.find(marker)
    if idx < 0:
        return ""
    brace = source.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(source):
        char = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if char == "/" and nxt == "/":
            j = source.find("\n", i)
            i = len(source) if j < 0 else j
            continue
        if char in "\"'`":
            quote = char
            i += 1
            while i < len(source):
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace:i + 1]
        i += 1
    return ""


class HistoryRestoreRealStageRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workbench = WORKBENCH.read_text(encoding="utf-8", errors="replace")
        cls.cpq_home = CPQ_HOME.read_text(encoding="utf-8", errors="replace")
        cls.shared_src = SHARED.read_text(encoding="utf-8", errors="replace") if SHARED.exists() else ""
        cls.matrix = cls._run_matrix()

    @classmethod
    def _run_matrix(cls) -> dict:
        if not SHARED.exists():
            # 共享模块不存在时不让整个类崩掉：让矩阵与结构断言各自报出缺口。
            return {}
        node = shutil.which("node")
        if not node:  # pragma: no cover
            raise unittest.SkipTest("未找到 node，无法执行阶段判定的行为断言")
        sandbox = pathlib.Path(tempfile.mkdtemp(prefix="tech-stage-restore-"))
        cls.addClassCleanup(shutil.rmtree, sandbox, True)
        work = sandbox / "case.js"
        work.write_text("", encoding="utf-8")
        proc = subprocess.run([node, "-e", HARNESS, str(SHARED),
                               json.dumps([[n, f, p, s] for n, f, p, s, _ in CASES],
                                          ensure_ascii=False)],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise AssertionError(f"共享判定模块无法执行：{proc.stderr[-1200:]}")
        return json.loads(proc.stdout)

    # ------------------------------------------- R2 判定矩阵（行为）
    def test_stage_matrix_matches_real_progress(self):
        for name, _flow, _project, _signals, expected in CASES:
            with self.subTest(case=name):
                self.assertEqual(self.matrix.get(name), expected,
                                 f"{name} 应恢复到 {expected}，实际 {self.matrix.get(name)}")

    def test_returned_to_process_manager_lands_on_summary(self):
        self.assertEqual(self.matrix.get("returned_to_process_manager"), "summary",
                         "财务把成本退回工艺经理复核后，重开项目应落到 3.1 汇总，而不是 2.2")

    def test_cost_stage_is_not_rolled_back_to_process(self):
        for name in ("cost_sent_to_finance", "cost_confirmed", "part_cost_measured",
                     "cost_ready", "cost_stage_without_ir"):
            with self.subTest(case=name):
                self.assertEqual(self.matrix.get(name), "cost",
                                 f"{name} 已经做到 2.3，不能退回 2.2 组装与整合")

    def test_2_2_machine_cost_is_not_mistaken_for_2_3(self):
        self.assertEqual(self.matrix.get("only_2_2_machine_cost"), "process",
                         "2.2 自己算的整机成本不算 2.3 已开始：工艺没确认、也没发财务")

    def test_shared_module_reachable_from_node(self):
        self.assertTrue(self.matrix,
                        "共享判定模块缺失或无法执行：必须先有 tech-stage-restore.js")

    def test_every_result_is_a_whitelisted_stage(self):
        for name, value in self.matrix.items():
            with self.subTest(case=name):
                self.assertIn(value, STAGES, f"{name} 返回了白名单外的阶段：{value}")

    # ------------------------------------------- R1 唯一判定实现（结构）
    def test_shared_module_is_pure_and_exports_from_signals(self):
        self.assertTrue(self.shared_src, "缺少 tech-stage-restore.js")
        self.assertIn("fromSignals", self.shared_src,
                      "共享模块必须导出 fromSignals(flow, projectData, signals)")
        for forbidden in ("document.", "fetch(", "localStorage", "XMLHttpRequest"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.shared_src,
                                 "共享判定必须是纯函数：不碰 DOM、不发请求、不读存储")

    def test_workbench_delegates_instead_of_keeping_its_own_branch(self):
        body = js_body(self.workbench, "function techStageFromProject(")
        self.assertTrue(body, "找不到 techStageFromProject()")
        self.assertIn("TechStageRestore",
                      body, "工作台必须委托共享判定，不得再留一份自己的判定")
        self.assertNotIn("'process' : 'drawing'", body,
                         "自建「hasIr ? process : drawing」分支必须删除")

    def test_cpq_home_delegates_instead_of_keeping_its_own_branch(self):
        body = js_body(self.cpq_home, "function techStageFromFlow(")
        self.assertTrue(body, "找不到 报价首页 的 techStageFromFlow()")
        self.assertIn("TechStageRestore", body, "首页必须委托同一份共享判定")
        self.assertNotIn("'process' : 'drawing'", body,
                         "首页自建的「hasIr ? process : drawing」分支必须删除")

    def test_both_pages_load_the_shared_module(self):
        self.assertIn("tech-stage-restore.js", self.workbench,
                      "tech-workbench.html 必须引入共享模块（放在 tech-workbench.js 之前）")
        self.assertIn("tech-stage-restore.js", self.cpq_home,
                      "报价首页.html 必须引入共享模块")

    # ------------------------------------------- R3 取数真实（结构）
    def test_workbench_restore_fetches_real_stage_data(self):
        body = js_body(self.workbench, "async function techHistoryRestore(")
        self.assertTrue(body, "找不到 techHistoryRestore()")
        for url in ("/workflow", "/summary"):
            with self.subTest(url=url):
                self.assertIn(url, body,
                              f"恢复历史必须真的取 {url}，不能再只凭 IR 猜阶段")
        self.assertRegex(body, r"techStageFromProject\([^)]*,[^)]*,",
                         "必须把真实数据作为第三份信号交给判定")

    def test_cpq_home_open_project_fetches_real_stage_data(self):
        body = js_body(self.cpq_home, "async function openTechProject(")
        self.assertTrue(body, "找不到 报价首页 的 openTechProject()")
        for url in ("/workflow", "/summary"):
            with self.subTest(url=url):
                self.assertIn(url, body, f"首页打开项目必须真的取 {url}")

    # ------------------------------------------- R4 不缩水（守卫）
    def test_task_kind_landings_unchanged(self):
        for token in ("tech_new_product", "tech_cost", "tech_cost_return"):
            with self.subTest(token=token):
                self.assertIn(token, self.cpq_home, f"任务类型 {token} 的既有落点不得删除")
        self.assertRegex(self.cpq_home, r"k === 'tech_cost'[\s\S]{0,300}stage=cost",
                         "财务经理领成本任务仍要直接落 2.3")

    def test_stage_whitelist_untouched(self):
        bridge = (FRONT / "tech-board-bridge.js").read_text(encoding="utf-8", errors="replace")
        runtime = (FRONT / "tech-board-runtime.js").read_text(encoding="utf-8", errors="replace")
        for name in STAGES:
            with self.subTest(stage=name):
                self.assertIn(f"'{name}'", bridge, f"桥的 stage 白名单被改动：{name}")
                self.assertIn(f"'{name}'", runtime, f"运行时的 stage 白名单被改动：{name}")

    def test_apply_stage_entry_kept(self):
        self.assertIn("applyStage(stage,", self.workbench,
                      "工作台仍须用 applyStage() 落阶段，不能改成直接拼 URL")
        self.assertIn("techWorkbenchUrl(stage", self.cpq_home,
                      "首页仍须用 techWorkbenchUrl() 拼目标地址")


if __name__ == "__main__":
    unittest.main()
