"""批次 5A 红测：技术工艺「五阶段 + 13 子步骤」全局口径。

背景（用户反馈 + 本次代码走查）：
  · 顶部流程条已经是 5 个大流程（1 工艺评估需求 / 2 图纸解析 / 3 组装与整合 /
    4 成本测算 / 5 工艺评估报告）；
  · 同一个 `tech-workbench.js` 里 `STAGES[].no` 还是老九阶段号（process=2.2、
    cost=2.3、summary/report-review/report-publish=3.1/3.2/3.3），
    `phase / phaseTitle` 还是更老的三阶段标题；
  · 页面标题、页签行、会话结论、Agent 提示词与报错都还在用旧编号与「九阶段」称呼。

本文件把「5 阶段 × 13 子步骤」写成一张规范表，逐面校验前端唯一口径表、后端口径表、
用户可见文案、Agent 提示词与白名单报错。实现前应当失败；失败必须落在口径缺口上，
而不是导入 / 语法 / 环境错误。

不联网、不起服务、不读真实业务数据；后端只在子进程里 import 新模块取口径表。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
BACKEND = ROOT / "tech_app" / "backend"
VENV_PY = ROOT / "open-claude" / ".venv" / "bin" / "python"

WORKBENCH_JS = FRONTEND / "tech-workbench.js"
BOARD_RUNTIME_JS = FRONTEND / "tech-board-runtime.js"
NAV_JS = FRONTEND / "workflow-navigation.js"
OC_AGENT_PY = BACKEND / "services" / "oc_agent.py"
STAGE_TABLE_PY = BACKEND / "services" / "workflow_stages.py"

# 规范口径：stage id → (阶段号, 阶段标题, 子步骤号, 子步骤标题, 页签 view)
CANONICAL = (
    ("requirement-create", "1", "工艺评估需求", "1.1", "创建需求", ""),
    ("requirement-confirm", "1", "工艺评估需求", "1.2", "确认需求", ""),
    ("requirement-review", "1", "工艺评估需求", "1.3", "审核需求", ""),
    ("drawing", "2", "图纸解析", "2.1", "图纸解析", ""),
    ("process", "3", "组装与整合", "3.1", "整合图纸", "drawings"),
    ("cost", "4", "成本测算", "4.1", "零件成本", "parts"),
    ("summary", "5", "工艺评估报告", "5.1", "汇总结果", ""),
    ("report-review", "5", "工艺评估报告", "5.2", "结果审核", ""),
    ("report-publish", "5", "工艺评估报告", "5.3", "发布并回传报价", ""),
)
# 跨子步骤的 stage：进入时的默认子步骤 + 全部子步骤
MULTI_SUB = {
    "process": (("3.1", "整合图纸", "drawings"),
                ("3.2", "参数推荐", "params"),
                ("3.3", "组装工艺", "process")),
    "cost": (("4.1", "零件成本", "parts"),
             ("4.2", "组装成本", "assembly"),
             ("4.3", "汇总", "total")),
}
PHASE_TITLES = ("工艺评估需求", "图纸解析", "组装与整合", "成本测算", "工艺评估报告")
STAGE_IDS = tuple(row[0] for row in CANONICAL)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def js_block(text: str, marker: str) -> str:
    """从 `marker` 起做花括号配对，返回整段对象/数组字面量。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    start = text.find("{", idx)
    alt = text.find("[", idx)
    if start < 0 or (0 <= alt < start):
        start = alt
    if start < 0:
        return ""
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    for i in range(start, len(text)):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def js_entries(block: str) -> list:
    """把 `[{...}, {...}]` 拆成每个 `{...}` 片段。"""
    entries, depth, start = [], 0, None
    for i, ch in enumerate(block):
        if ch == "{":
            depth += 1
            if depth == 1:
                start = i
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                entries.append(block[start:i + 1])
    return entries


def js_value(entry: str, key: str) -> str:
    m = re.search(rf"\b{key}\s*:\s*(['\"])(.*?)\1", entry)
    return m.group(2) if m else ""


def stage_table_entries(js: str) -> dict:
    block = js_block(js, "const STAGES =")
    out = {}
    for entry in js_entries(block):
        sid = js_value(entry, "id")
        if sid:
            out[sid] = entry
    return out


def backend_table() -> dict:
    """在子进程里 import 后端口径表，返回 {rows, phases}；模块缺失时返回空。"""
    script = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "try:\n"
        "    from tech_app.backend.services import workflow_stages as wf\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'error': f'{type(exc).__name__}: {exc}'}))\n"
        "    raise SystemExit(0)\n"
        "phases = getattr(wf, 'PHASES', None)\n"
        "stages = getattr(wf, 'STAGES', None)\n"
        "def norm(item):\n"
        "    if isinstance(item, dict):\n"
        "        return item\n"
        "    return {k: getattr(item, k, None) for k in\n"
        "            ('no', 'title', 'sub', 'sub_title', 'stage_id', 'view', 'subs')}\n"
        "print(json.dumps({'phases': [norm(p) for p in (phases or [])],\n"
        "                  'stages': [norm(s) for s in (stages or [])]}, ensure_ascii=False))\n"
    ) % (str(ROOT),)
    python = VENV_PY if VENV_PY.exists() else Path(sys.executable)
    proc = subprocess.run([str(python), "-c", script], capture_output=True, text=True,
                          cwd=str(ROOT))
    text = (proc.stdout or "").strip().splitlines()
    if not text:
        return {}
    try:
        return json.loads(text[-1])
    except json.JSONDecodeError:
        return {}


class NamingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workbench = read(WORKBENCH_JS)
        cls.entries = stage_table_entries(cls.workbench)
        cls.backend = backend_table()


class FrontendStageTableTest(NamingCase):
    def test_stage_table_covers_all_nine_stages(self):
        self.assertEqual(set(STAGE_IDS), set(self.entries),
                         f"前端口径表必须覆盖 9 个 stage：{sorted(self.entries)}")

    def test_stage_table_uses_five_phase_numbering(self):
        """每个 stage 的阶段号 / 阶段标题 / 子步骤号 / 子步骤标题都必须换成新口径。"""
        wrong = []
        for sid, phase, phase_title, sub, sub_title, _view in CANONICAL:
            entry = self.entries.get(sid, "")
            got = {key: js_value(entry, key)
                   for key in ("phase", "phaseTitle", "no", "subTitle")}
            want = {"phase": phase, "phaseTitle": phase_title,
                    "no": sub, "subTitle": sub_title}
            if got != want:
                wrong.append(f"{sid}: 期望 {want}，实际 {got}")
        self.assertFalse(wrong, "阶段/子步骤编号与规范口径不一致：\n" + "\n".join(wrong))

    def test_top_flow_titles_are_the_five_phases(self):
        block = js_block(self.workbench, "const MAJOR_STEPS =")
        titles = [js_value(entry, "title") for entry in js_entries(block)]
        self.assertEqual(list(PHASE_TITLES), titles,
                         f"顶部流程条必须是这 5 个阶段：{titles}")

    def test_context_substeps_carry_canonical_numbers(self):
        """阶段 1、5 的子步骤按钮必须带自己的子步骤号。"""
        block = js_block(self.workbench, "const CONTEXT_SUBSTEPS =")
        for phase, subs in (("1", (("1.1", "创建"), ("1.2", "确认"), ("1.3", "审核"))),
                            ("5", (("5.1", "汇总结果"), ("5.2", "结果审核"),
                                   ("5.3", "发布并回传报价")))):
            chunk = block[block.find(f"{phase}:")::] if f"{phase}:" in block else ""
            for sub, label in subs:
                self.assertIn(f"'{sub}'", chunk,
                              f"阶段 {phase} 的子步骤按钮必须带编号 {sub}：{block[:200]}")
                self.assertIn(label, chunk, f"阶段 {phase} 必须保留 {label} 按钮")

    def test_child_tab_proxy_carries_canonical_numbers(self):
        """阶段 3、4 的页签必须带 3.x / 4.x 编号。"""
        block = js_block(self.workbench, "const CHILD_TAB_PROXY =")
        for stage, subs in MULTI_SUB.items():
            chunk = block[block.find(f"'{stage}':")::] if f"'{stage}':" in block else ""
            self.assertTrue(chunk, f"CHILD_TAB_PROXY 缺少 {stage}")
            for sub, label, _view in subs:
                self.assertIn(f"'{sub}'", chunk,
                              f"{stage} 的页签必须带编号 {sub}")
                self.assertIn(label, chunk, f"{stage} 的页签必须保留 {label}")

    def test_page_context_follows_the_naming_rule(self):
        """1:1 的 stage 用子步骤号，跨子步骤的 stage 用阶段号；九个取值互不相同。"""
        block = js_block(self.workbench, "const STAGE_AGENT_CONTEXT =")
        seen = {}
        for sid, phase, phase_title, sub, sub_title, _view in CANONICAL:
            idx = block.find(f"'{sid}':")
            self.assertGreater(idx, 0, f"STAGE_AGENT_CONTEXT 缺少 {sid}")
            chunk = block[idx:idx + 200]
            got = js_value(chunk, "pageContext")
            want = (f"{phase} {phase_title}" if sid in MULTI_SUB
                    else f"{sub} {sub_title}")
            self.assertEqual(want, got, f"{sid} 的 page_context 必须是「{want}」")
            self.assertNotIn(got, seen, f"{sid} 与 {seen.get(got)} 的 page_context 重复")
            seen[got] = sid


class BackendStageTableTest(NamingCase):
    def test_backend_exposes_one_stage_table(self):
        self.assertTrue(STAGE_TABLE_PY.exists(),
                        "后端必须有一份口径表：tech_app/backend/services/workflow_stages.py")
        self.assertIn("stages", self.backend, "后端口径表必须导出 STAGES")
        self.assertIn("phases", self.backend, "后端口径表必须导出 PHASES")

    def test_backend_phases_match_canonical(self):
        phases = self.backend.get("phases") or []
        titles = [str(row.get("title") or "") for row in phases]
        self.assertEqual(list(PHASE_TITLES), titles,
                         f"后端口径表的 5 个阶段标题必须是 {PHASE_TITLES}：{titles}")

    def test_backend_stages_cover_all_subs(self):
        rows = self.backend.get("stages") or []
        got = {(str(r.get("stage_id") or ""), str(r.get("sub") or ""),
                str(r.get("sub_title") or ""), str(r.get("view") or "")) for r in rows}
        want = set()
        for sid, _phase, _title, sub, sub_title, view in CANONICAL:
            if sid in MULTI_SUB:
                for sub_no, sub_label, sub_view in MULTI_SUB[sid]:
                    want.add((sid, sub_no, sub_label, sub_view))
            else:
                want.add((sid, sub, sub_title, view))
        missing = sorted(want - got)
        self.assertFalse(missing, f"后端口径表缺少这些子步骤：{missing}（实际 {sorted(got)}）")

    def test_frontend_and_backend_agree(self):
        rows = {(str(r.get("stage_id") or ""), str(r.get("sub") or ""))
                for r in (self.backend.get("stages") or [])}
        want = set()
        for sid, _phase, _title, sub, _sub_title, _view in CANONICAL:
            subs = MULTI_SUB.get(sid)
            if subs:
                want.update((sid, s[0]) for s in subs)
            else:
                want.add((sid, sub))
        self.assertTrue(want.issubset(rows),
                        f"前后端口径表必须逐行一致，后端缺少：{sorted(want - rows)}")


class AgentWordingTest(NamingCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.oc_agent = read(OC_AGENT_PY)
        cls.runtime = read(BOARD_RUNTIME_JS)

    def test_prompt_lists_the_new_table_and_drops_nine_stage_wording(self):
        self.assertNotIn("九阶段", self.oc_agent,
                         "Agent 提示词与报错不得再写「九阶段」")
        for token in ("3.1 整合图纸", "3.2 参数推荐", "3.3 组装工艺",
                      "4.1 零件成本", "4.2 组装成本", "4.3 汇总",
                      "5.1 汇总结果", "5.2 结果审核", "5.3 发布并回传报价"):
            self.assertIn(token, self.oc_agent,
                          f"Agent 提示词必须按新口径列出 {token}")

    def test_stage_whitelist_errors_use_the_new_wording(self):
        for token in ("九阶段", "九个内部阶段"):
            self.assertNotIn(token, self.runtime,
                             "看板运行时的白名单报错不得再写「九阶段」")

    def test_legacy_step_wording_is_gone(self):
        """「第 N 大步」一律改称「第 N 阶段」——前后端所有出现处一起换。"""
        legacy = ("第 5 大步", "第 3 大步", "第 4 大步", "第 2 大步")
        surfaces = (
            OC_AGENT_PY, WORKBENCH_JS, NAV_JS,
            FRONTEND / "cost-review.js", FRONTEND / "cpq-tech-inbox.js",
            FRONTEND / "tech-stage-restore.js", FRONTEND / "tech-session-timeline.js",
            ROOT / "cpq_wf.py", ROOT / "cpq_tech_bridge.py",
            BACKEND / "main.py", BACKEND / "services" / "cost_flow.py",
            BACKEND / "services" / "cpq_bridge.py",
        )
        for path in surfaces:
            text = read(path)
            for token in legacy:
                self.assertNotIn(token, text, f"{path.name} 不得再写「{token}」")


class UserVisibleCopyTest(NamingCase):
    def surfaces(self):
        return {name: read(FRONTEND / name) for name in (
            "assembly-integration.html", "cost-review.html", "index.html",
            "summary-result.js", "report-publish-result.js", "workflow.js",
            "assembly-integration.js", "cost-review.js")}

    def test_assembly_page_shows_phase_three_substeps(self):
        text = self.surfaces()["assembly-integration.html"]
        self.assertNotIn("2.2 组装与整合", text, "组装页不得再显示旧编号 2.2")
        self.assertNotIn("2.3 成本测算", text, "组装页不得再显示旧编号 2.3")
        for token in ("3.1 整合图纸", "3.2 参数推荐", "3.3 组装工艺"):
            self.assertIn(token, text, f"组装页页签行必须显示 {token}")

    def test_cost_page_shows_phase_four_substeps(self):
        text = self.surfaces()["cost-review.html"]
        self.assertNotIn("2.3 成本测算", text, "成本页不得再显示旧编号 2.3")
        for token in ("4.1 零件成本", "4.2 组装成本", "4.3 汇总"):
            self.assertIn(token, text, f"成本页页签行必须显示 {token}")

    def test_drawing_page_keeps_its_single_substep(self):
        text = self.surfaces()["index.html"]
        self.assertIn("2.1 图纸解析", text, "图纸页仍是 2.1 图纸解析")

    def test_report_pages_render_five_phases(self):
        for name in ("summary-result.js", "report-publish-result.js", "workflow.js"):
            text = self.surfaces()[name]
            self.assertNotIn("2.2 组装与整合", text, f"{name} 不得再渲染旧编号 2.2")
            self.assertNotIn("2.3 成本测算", text, f"{name} 不得再渲染旧编号 2.3")
            for token in ("组装与整合", "成本测算", "工艺评估报告"):
                self.assertIn(token, text, f"{name} 的流程条必须包含 {token}")

    def test_summary_and_publish_substeps_are_five_x(self):
        for name in ("summary-result.js", "report-publish-result.js"):
            text = self.surfaces()[name]
            for token in ("5.1 汇总结果", "5.2 结果审核", "5.3 发布并回传报价"):
                self.assertIn(token, text, f"{name} 必须把报告三步写成 {token}")

    def test_integration_js_uses_phase_wording(self):
        text = self.surfaces()["assembly-integration.js"]
        self.assertNotIn("2.2 组装与整合", text, "组装页脚本不得再写旧编号 2.2")
        self.assertNotIn("2.3 成本测算", text, "组装页脚本不得再写旧编号 2.3")
        for token in ("3.1 整合图纸", "3.2 参数推荐", "3.3 组装工艺",
                      "4 成本测算"):
            self.assertIn(token, text, f"组装页脚本必须按新口径提到 {token}")

    def test_summary_stage_rows_use_phase_wording(self):
        text = self.surfaces()["summary-result.js"]
        for token in ("3 组装与整合", "4 成本测算"):
            self.assertIn(token, text, f"汇总阶段行必须写成「{token}」")
        self.assertNotIn("'2.2 组装与整合'", text, "汇总阶段行不得再写 2.2")
        self.assertNotIn("'2.3 成本测算'", text, "汇总阶段行不得再写 2.3")


class NavigationAndGuardrailTest(NamingCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.nav = read(NAV_JS)

    def test_navigation_map_has_five_phases_with_new_numbers(self):
        block = js_block(self.nav, "const stages =")
        self.assertTrue(block, "workflow-navigation.js 仍要有 stages 表")
        for phase, subs in (("1", ("1.1", "1.2", "1.3")), ("2", ("2.1",)),
                            ("3", ("3.1", "3.2", "3.3")), ("4", ("4.1", "4.2", "4.3")),
                            ("5", ("5.1", "5.2", "5.3"))):
            row = re.search(rf"\b{phase}\s*:\s*\[([^\]]*)\]", block)
            self.assertIsNotNone(row, f"stages 表必须登记阶段 {phase}")
            for sub in subs:
                self.assertIn(sub, row.group(1), f"阶段 {phase} 必须登记子步骤 {sub}")
        for stale in ("2.2 组装与整合", "2.3 成本测算"):
            self.assertNotIn(stale, block, f"stages 表不得再出现 {stale}")

    def test_stage_ids_and_pages_are_unchanged(self):
        """护栏：口径只改编号与标题，stage id、页面文件与 URL 参数一个都不动。"""
        for sid in STAGE_IDS:
            self.assertIn(f"'{sid}'", self.workbench, f"stage id {sid} 不得改动")
        pages = ("requirement-create.html", "requirement-confirm.html",
                 "requirement-review.html", "index.html",
                 "assembly-integration.html", "cost-review.html", "summary.html",
                 "report-review.html", "report-publish.html")
        for page in pages:
            self.assertTrue((FRONTEND / page).exists(), f"页面 {page} 不得改名或删除")
            self.assertIn(page, self.workbench, f"阶段表必须继续指向 {page}")

    def test_upstream_step_tables_are_untouched(self):
        """护栏：上游六子步骤与报价 6 步属于别的口径，本批不得顺手改。"""
        config_py = read(BACKEND / "config.py")
        self.assertIn("TECH_SUBSTEPS", config_py, "TECH_SUBSTEPS 必须保留")
        cpq_wf = read(ROOT / "cpq_wf.py")
        for name in ("确认需求配置", "工艺确认", "定价-利润加成", "报价-其他加价项",
                     "报价方案", "输出报价单"):
            self.assertIn(name, cpq_wf, f"报价 6 步里的「{name}」不得改动")


if __name__ == "__main__":
    unittest.main()
