"""红测：2.3 成本测算结果没有真正进入 3.1 汇总报告。

现状缺口（实测）：
  · `tech_app/frontend/summary-result.js` 的 `srLiveView()` 把评估项「经济可行性」
    写死成 `status:'待评估'` + `conclusion:'尚未接入可追溯的成本与报价结论。'` ——
    财务在 2.3 完成零件成本 / 组装成本 / 合计 / 确认之后，3.1 依旧显示这句话。
  · 阶段汇总只拼 `2.1 图纸解析` 与 `2.2 组装与整合`（`srIntegrationStage`），没有 2.3。
  · 3.1 保存时由 `srRead()` 把页面表格读成 `evaluation_items` / `stage_results` 落库，
    所以占位句就是**正式报告内容**：后端门禁 `report_workflow.content_issues()`
    （`:230`，status ∈ {待评估, 需补充} 一律拦截）会卡住报告，而用户手工把状态点成
    「可行」时那句话又可能一路进审核。
  · 后端 `services/summary.py` 其实已经装载 2.3 状态（`steps.cost_review`），只是没人用。

本批只做「2.3 → 3.1」这条展示链：聚合层补 2.3 成本口径、3.1 用它生成经济可行性结论与
2.3 阶段行、送审门禁补一条只针对经济可行性的占位句拦截。不改路由、权限、成本算法、
2.3 页面写路径，也不改 2.1 / 2.2 既有的两行结论生成逻辑。

行为断言统一走 node 真实执行 `srLiveView()`（不是静态文本提取）：喂进去的 aggregate
同时带 2.3 的 `steps.cost_review`、2.2 的 `steps.integration.cost` 与聚合层口径
`cost`，断言渲染出来的行内容，因此实现侧无论从哪一处取数都必须产出同样结论。
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
JS_PATH = ROOT / "tech_app" / "frontend" / "summary-result.js"
SUMMARY_PATH = ROOT / "tech_app" / "backend" / "services" / "summary.py"
REPORT_FLOW_PATH = ROOT / "tech_app" / "backend" / "services" / "report_workflow.py"

PLACEHOLDER = "尚未接入可追溯的成本与报价结论"
_UNFINISHED = ("尚未", "暂无", "未接入")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def js_body(source: str, marker: str) -> str:
    """按花括号配平取出一段 JS（函数定义 / 对象字面量）。"""
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


def py_body(source: str, name: str) -> str:
    idx = source.find(f"def {name}(")
    if idx < 0:
        return ""
    rest = source[idx:]
    nxt = rest.find("\ndef ", 1)
    return rest if nxt == -1 else rest[:nxt]


def normalize(text: str) -> str:
    """比较金额时抹平千分位 / 货币符号 / 空白。"""
    return re.sub(r"[,\s¥￥元]", "", str(text or ""))


# --------------------------------------------------------------------------- #
# 造数：确认 / 未确认 / 完全没做三种 2.3 状态
# --------------------------------------------------------------------------- #
TOTAL = 1234.5
IR = {"device_name": "测试整机", "parts": [{"part_id": "P1", "name": "壳体", "quantity": 1}],
      "open_questions": []}
INTEGRATION = {
    "params": {"assembly_name": "测试整机",
               "params": [{"param_code": "clm_size", "name": "尺寸", "value": "13*20",
                           "unit": "mm", "basis": "需求", "source": "AI"}],
               "interfaces": [], "part_refs": []},
    "process": {"steps": [{"step_no": 1, "name": "装配", "equipment": "工装",
                           "duration_min": 10}]},
    "cost": {"quantity": 1, "summary": "整机成本构成",
             "items": [{"category": "material", "name": "材料", "basis": "用量×单价",
                        "quantity": 1, "unit_price": TOTAL, "amount": TOTAL}]},
}
SUMMARY = {"conclusion": "整机可制造", "risks": ["交期偏紧"], "overview": "",
           "highlights": []}
REQUIREMENT = {"requirement_no": "REQ-1", "title": "测试整机"}
REPORT = {"project_id": "p1", "title": "", "status": "draft", "report_no": "RPT-P1",
          "prepared_at": "2026-09-15", "prepared_by": "王工艺", "requirement_no": "REQ-1",
          "evaluation_items": [], "stage_results": [], "source_snapshot": {}}
BREAKDOWN = {"material": 1000.0, "labor": 134.5, "overhead": 100.0, "machining": 0.0,
             "total": TOTAL}


def cost_block(confirmed: bool) -> dict:
    review = {"project_id": "p1", "confirmed": confirmed,
              "confirmed_by": "李财务" if confirmed else None,
              "confirmed_at": "2026-09-15 10:00:00" if confirmed else None,
              "note": "成本已核对" if confirmed else "",
              "actions": [{"kind": "send-to-quote", "label": "回传销售经理继续报价",
                           "detail": "报价卡片进入第 3 步", "at": "2026-09-15 11:00:00",
                           "by": "李财务"}] if confirmed else []}
    return {"project_id": "p1", "ready": confirmed, "missing": [], "review": review,
            "parts_total": {"material": 600.0, "labor": 100.0, "overhead": 50.0,
                            "machining": 0.0, "total": 750.0},
            "assembly": {"id": "assembly", "name": "测试整机", "kind": "assembly",
                         "quantity": 1, "has_cost": True, "breakdown": BREAKDOWN,
                         "unit_cost": TOTAL, "subtotal": TOTAL},
            "final": BREAKDOWN}


def aggregate(confirmed: bool) -> dict:
    return {"project_id": "p1", "device_name": "测试整机", "meta": {}, "ir": IR,
            "steps": {"material": {}, "manufacturing": {}, "cleaning": {}, "assembly": {},
                      "integration": INTEGRATION,
                      "cost_review": cost_block(confirmed)["review"], "production": {}},
            "summary": SUMMARY, "cost": cost_block(confirmed)}


EMPTY_AGGREGATE = {"project_id": "p1", "device_name": "测试整机", "meta": {}, "ir": IR,
                   "steps": {"material": {}, "manufacturing": {}, "cleaning": {},
                             "assembly": {}, "integration": {}, "cost_review": {},
                             "production": {}},
                   "summary": {}, "cost": {"project_id": "p1", "ready": False,
                                           "missing": [], "review": {}, "final": {},
                                           "parts_total": {}, "assembly": {}}}

CASES = [("confirmed", aggregate(True)), ("unconfirmed", aggregate(False)),
         ("empty", EMPTY_AGGREGATE)]

HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    const source = fs.readFileSync(process.argv[1], 'utf8');
    globalThis.esc = value => String(value == null ? '' : value);
    globalThis.api = async () => ({});
    globalThis.statusLabel = value => String(value || '');
    globalThis.location = { search: '', href: '' };
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    globalThis.document = { querySelector: () => null, querySelectorAll: () => [],
      createElement: () => ({ classList: { add() {}, remove() {}, toggle() {} }, style: {},
                              remove() {}, append() {} }),
      body: { append() {} } };
    eval(source + '\\nglobalThis.__srView = (r, a, q) => srLiveView(r, a, q);');
    const cases = JSON.parse(process.argv[2]);
    const report = JSON.parse(process.argv[3]);
    const requirement = JSON.parse(process.argv[4]);
    const out = {};
    for (const [name, aggregate] of cases) {
      const view = globalThis.__srView(report, aggregate, requirement);
      out[name] = { evaluation: view.evaluation, stages: view.stages };
    }
    process.stdout.write(JSON.stringify(out));
    """
).strip()


class SummaryReportIncludesCostReviewRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read(JS_PATH)
        cls.summary = read(SUMMARY_PATH)
        cls.report_flow = read(REPORT_FLOW_PATH)
        cls.views = cls._run_views()

    @classmethod
    def _run_views(cls) -> dict:
        node = shutil.which("node")
        if not node:  # pragma: no cover - 本机无 node 时只能跳过行为验证
            raise unittest.SkipTest("未找到 node，无法执行 3.1 视图的行为断言")
        # srStart(); 之后是页面启动与动作注册，视图装配不依赖它们。
        cut = cls.js.index("\nsrStart();")
        source = cls.js[:cut]
        # 放临时目录，不在仓库工作区留文件（并行改动者与本仓 status 都不该受影响）。
        sandbox = pathlib.Path(tempfile.mkdtemp(prefix="summary-view-harness-"))
        cls.addClassCleanup(shutil.rmtree, sandbox, True)
        script = sandbox / "summary-view.js"
        script.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [node, "-e", HARNESS, str(script), json.dumps(CASES, ensure_ascii=False),
             json.dumps(REPORT, ensure_ascii=False), json.dumps(REQUIREMENT, ensure_ascii=False)],
            capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise AssertionError(f"3.1 视图无法执行：{proc.stderr[-1500:]}")
        return json.loads(proc.stdout)

    # ---------------------------------------------------- 工具
    def _row(self, case: str, item: str) -> dict:
        rows = [row for row in self.views[case]["evaluation"] if row["item"] == item]
        self.assertTrue(rows, f"{case} 缺少评估项「{item}」")
        return rows[0]

    def _stages(self, case: str) -> list:
        return self.views[case]["stages"]

    def _stage(self, case: str, prefix: str) -> dict:
        row = next((row for row in self._stages(case) if row["stage"].startswith(prefix)), None)
        self.assertIsNotNone(row, f"{case} 缺少阶段行「{prefix}」：{[r['stage'] for r in self._stages(case)]}")
        return row

    # ------------------------------------------- R2 经济可行性来自 2.3（行为）
    def test_confirmed_cost_fills_economic_feasibility(self):
        row = self._row("confirmed", "经济可行性")
        self.assertNotEqual(row["status"], "待评估",
                            "2.3 已确认成本后，经济可行性不能再停在「待评估」")
        text = row["conclusion"]
        self.assertIn("1234", normalize(text),
                      f"结论必须带出可追溯的整机成本合计，实际：{text}")
        self.assertTrue("李财务" in text or "确认" in text,
                        f"结论必须带出 2.3 的确认信息，实际：{text}")
        for bad in _UNFINISHED:
            with self.subTest(bad=bad):
                self.assertNotIn(bad, text, f"已确认成本后结论不该出现「{bad}」：{text}")

    def test_confirmed_cost_keeps_placeholder_out(self):
        text = self._row("confirmed", "经济可行性")["conclusion"]
        self.assertNotIn(PLACEHOLDER, text,
                         "占位句「尚未接入可追溯的成本与报价结论」不能再出现在已确认成本的项目里")

    def test_costs_computed_but_unconfirmed_stays_pending(self):
        row = self._row("unconfirmed", "经济可行性")
        self.assertEqual(row["status"], "待评估",
                         "财务还没确认时不能给出「可行」结论")
        self.assertIn("确认", row["conclusion"],
                      f"未确认时必须说清是「尚未确认」而不是「没有成本」，实际：{row['conclusion']}")

    def test_missing_cost_keeps_row_pending(self):
        row = self._row("empty", "经济可行性")
        self.assertEqual(row["status"], "待评估", "没做 2.3 时经济可行性必须仍是待评估")
        self.assertTrue(any(bad in row["conclusion"] for bad in _UNFINISHED)
                        or "成本" in row["conclusion"],
                        f"没做 2.3 时要说清成本尚未完成，实际：{row['conclusion']}")

    # ------------------------------------------- R3 阶段汇总新增 2.3 行（行为）
    def test_summary_has_2_3_stage_after_2_2(self):
        names = [row["stage"] for row in self._stages("confirmed")]
        two_one = next((i for i, name in enumerate(names) if name.startswith("2.1")), None)
        two_two = next((i for i, name in enumerate(names) if name.startswith("2.2")), None)
        two_three = next((i for i, name in enumerate(names) if name.startswith("2.3")), None)
        self.assertIsNotNone(two_one, f"2.1 阶段行被删了：{names}")
        self.assertIsNotNone(two_two, f"2.2 阶段行被删了：{names}")
        self.assertIsNotNone(two_three, f"阶段汇总必须新增 2.3 成本测算行：{names}")
        self.assertLess(two_two, two_three, "2.3 行必须排在 2.2 之后")

    def test_2_3_stage_carries_cost_conclusion_and_passes_gate(self):
        row = self._stage("confirmed", "2.3")
        text = row["conclusion"]
        self.assertIn("1234", normalize(text), f"2.3 行必须带出成本合计，实际：{text}")
        for bad in _UNFINISHED:
            with self.subTest(bad=bad):
                self.assertNotIn(bad, text,
                                 f"后端门禁禁止阶段结论出现「{bad}」，实际：{text}")

    def test_unfinished_cost_never_leaves_unfinished_stage_text(self):
        for case in ("unconfirmed", "empty"):
            for row in self._stages(case):
                for bad in _UNFINISHED:
                    with self.subTest(case=case, stage=row["stage"], bad=bad):
                        self.assertNotIn(bad, row["conclusion"],
                                         "未完成的阶段行不能进汇总表（门禁会拦下整份报告）："
                                         f"{row['stage']} / {row['conclusion']}")

    # ------------------------------------------- 静态结构（补强行为断言）
    def test_placeholder_sentence_is_gone(self):
        self.assertNotIn(PLACEHOLDER, self.js,
                         "3.1 里写死的成本占位句必须删掉，改由 2.3 数据生成")

    def test_view_reads_cost_review_state(self):
        self.assertRegex(self.js, r"cost_review|aggregate\?\.cost|aggregate\.cost",
                         "3.1 视图必须读取 2.3 的成本口径（steps.cost_review 或聚合层 cost）")

    # ------------------------------------------- R1 聚合层口径（静态）
    def test_aggregate_exposes_cost_rollup_at_top_level(self):
        body = py_body(self.summary, "aggregate")
        self.assertTrue(body, "找不到 summary.aggregate()")
        self.assertRegex(body, r'"cost":\s*\S',
                         "aggregate() 必须给出顶层 cost 口径（不得放进 steps，避免改动审核依据摘要）")
        self.assertNotRegex(body, r'"steps":\s*\{[^}]*"cost"',
                            "2.3 成本口径不能塞进 steps（会改变 report_source_payload 摘要）")

    def test_cost_rollup_reuses_existing_2_3_services(self):
        self.assertIn("cost_review", self.summary,
                      "聚合层必须复用既有 services/cost_review 的口径，不得另写成本算法")
        self.assertNotIn("cost_model.breakdown", self.summary,
                         "summary.py 不得自己算成本（那是第二套成本算法）")

    def test_report_source_digest_stays_on_business_steps(self):
        body = py_body(self.report_flow, "report_source_payload")
        self.assertTrue(body, "找不到 report_source_payload()")
        self.assertNotIn('"cost"', body,
                         "审核依据摘要仍只取 device_name / ir / steps / summary")

    # ------------------------------------------- R4 送审门禁补强（静态）
    def test_submit_gate_blocks_placeholder_conclusion(self):
        body = py_body(self.report_flow, "content_issues")
        self.assertTrue(body, "找不到 content_issues()")
        self.assertIn("经济可行性", body,
                      "门禁必须单独看住「经济可行性」这一项（状态被手工改成可行时也拦）")
        self.assertRegex(body, r"尚未接入|未接入|可追溯|占位",
                         "门禁要能识别成本占位句，否则错结论会作为正式报告内容通过送审")
        self.assertIn("待评估", body, "原有的 status ∈ {待评估, 需补充} 规则不得删除")

    # ------------------------------------------- R5 能力不缩水
    def test_existing_routes_and_stages_kept(self):
        for token in ("/summary", "/process-report", "process-report/submit-review"):
            with self.subTest(token=token):
                self.assertIn(token, read(ROOT / "tech_app" / "backend" / "main.py"))
        self.assertIn("srIntegrationStage", self.js, "2.2 阶段行的既有实现不得删除")
        self.assertIn("2.1 图纸解析", self.js, "2.1 阶段行不得删除")


if __name__ == "__main__":
    unittest.main()
