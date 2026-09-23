"""红测：重新进入 2.1 必须读回**已经落库的那一版**零件（进入即读回 + 不许催人重新解析）。

Spec：`docs/specs/packaging-parts-entry-readback.md`

现状缺口（都可指到行）：
  · `tech_app/frontend/app.js` 里 `refreshPackagingParts()`（读零件文档 + `renderTree`）只有两个
    调用点 —— 批量挤出之后（`:2160`）与"一键解析"跑完之后（`:3293`）；
  · `openProject()`（`:3806`）进入项目时只调 `loadDrawingFlowPanel()`（`:3853`）拉链路状态，
    **从不读零件文档**；深链 / 刷新走的 `afterAuth() → openProject().then(replayDrawingTimeline)`
    （`:540`）也不读；
  · 于是 `currentPackagingParts` 一直是 `null` → `renderTree()` 走空态（`:4198-4203`）→
    `packagingPartsEmptyText({}, …)` 给「零件文档还没生成，请先跑一键解析图纸。」（`:2204`）。

服务端**没有**丢数据（Spec §2.1 本机隔离实测）：同一份 酒盒.dwg 八步 8/8、`parts_extract` completed、
`parts_id=parts:9b0069978377e477`、263 件；**换一个进程**再读仍是同一版 263 件。

纪律：`node` + `vm` 抽具名函数体执行（前端：把 `openProject()` 与**真的**加载链
`refreshPackagingParts → fetchPackagingParts → packagingPartsItems/QueryString` 一起放进桩上下文，
断言的"请求"是加载链自己发出来的那条，不是桩自己记的一笔）+ 临时 `DATA_DIR` 的纯存储读取（后端）；
不起服务、不发 HTTP、不连 PG、不写业务数据。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

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

from tech_app.backend.services import packaging_parts            # noqa: E402
from tech_app.backend.storage import meta_backend                 # noqa: E402

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
PID = "978876df2bbb"
PARTS_URL = "/requirement/packaging-parts"
LEGACY_SENTENCE = "零件文档还没生成，请先跑一键解析图纸。"
EVIDENCE_PARTS_ID = "parts:9b0069978377e477"
EVIDENCE_TOTAL = 263

NODE_JS = r"""
const fs = require("fs");
const vm = require("vm");
// 桩环境里"链路状态那一路抛了"不该把探针自己弄死：只记录，不退出。
process.on("unhandledRejection", function () {});
const appPath = process.env.CPQ_APP_JS;
const mode = process.env.CPQ_MODE;
const src = fs.readFileSync(appPath, "utf8");

function extract(name) {
  let at = src.indexOf("function " + name + "(");
  if (at < 0) return null;
  if (src.slice(Math.max(0, at - 6), at) === "async ") at -= 6;
  let i = src.indexOf("{", at);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(at, j + 1); }
  }
  return null;
}

if (mode === "emptytxt") {
  const fn = extract("packagingPartsEmptyText");
  if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
  const cases = JSON.parse(fs.readFileSync(process.env.CPQ_CASES, "utf8"));
  eval(fn);
  const out = cases.map(function (c) {
    return String(packagingPartsEmptyText(c.doc, c.preconditions, c.flow));
  });
  console.log(JSON.stringify({ missing: false, out: out }));
  process.exit(0);
}

const fns = ["refreshPackagingParts", "fetchPackagingParts", "packagingPartsItems",
             "packagingPartsQueryString", "fetchPackagingBusinessParts"]
             .map(function (name) { return extract(name); });
if (fns.some(function (body) { return body === null; })) {
  console.log(JSON.stringify({ missing: true }));
  process.exit(0);
}
const fn = extract("openProject");
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
const flowFails = process.env.CPQ_VARIANT === "flow-fails";
const marks = [];
const hits = [];
const state = { error: "" };
let ctx = null;
const box = function () {
  return { style: {}, innerHTML: "", textContent: "", disabled: false, title: "",
           remove: function () {}, appendChild: function () {},
           querySelector: function () { return null; },
           querySelectorAll: function () { return []; },
           classList: { toggle: function () {}, add: function () {}, remove: function () {} },
           dataset: {} };
};
const sandbox = new Proxy({
  console: console, Promise: Promise, JSON: JSON, Object: Object, Array: Array,
  Number: Number, String: String, Math: Math, RegExp: RegExp, Date: Date, Set: Set,
  Map: Map, Error: Error, setTimeout: setTimeout, clearTimeout: clearTimeout,
  setImmediate: setImmediate, URLSearchParams: URLSearchParams, API: "",
  fetch: async function (url) {
    // 记下"谁在什么身份下发的这条请求"——加载链自己发的才算数。
    hits.push({ url: String(url), marks: marks.slice(),
                project: ctx ? String(ctx.currentProject || "") : "" });
    return { ok: true, status: 200,
             json: async function () { return { meta: { source_filename: "酒盒.dwg", note: "" },
                                                items: [], parts: [], total: 0,
                                                built: false, stats: {} }; } };
  },
  renderDrawingEntry: function () { marks.push("entry-decision"); return "drawing_flow"; },
  loadDrawingFlowPanel: async function () {
    marks.push("flow-panel");
    if (flowFails) throw new Error("flow-state-unavailable");
    return null;
  },
  renderTree: function () { marks.push("render-tree"); },
  loadPackagingRoleMap: async function () { return null; },
  refreshPackagingBomRoleUnboundNote: async function () { return null; },
  $: function () { return box(); },
  document: { querySelector: function () { return box(); },
              createElement: function () { return box(); } },
  localStorage: { setItem: function () {}, removeItem: function () {},
                  getItem: function () { return null; } },
  status: function () {}, renderIR: function () {}, syncActionSheet: function () {},
  loadModelLookup: function () {}, loadVerification: function () {},
  updateChatContext: function () {}, renderChat: function () {}, loadVersions: function () {},
  setWorkflow: function () {}, mediaUrl: function (url) { return url; },
  hidePackagingPartPanel: function () {},
}, { has: function () { return true; },
     get: function (target, key) { return key in target ? target[key] : undefined; } });

ctx = vm.createContext(sandbox);
vm.runInContext("var currentProject='';var currentIR=null;var currentGeometry=null;"
                + "var currentDrawings=null;var artifact_status=null;var currentSelectedId=null;"
                + "var diffPick=[];var currentDrawingEntry='';var currentDrawingIsImg=false;"
                + "var currentPackagingParts=null;var currentPackagingBusinessParts=null;"
                + "var packagingPartsPage={offset:0,limit:64,kind:'',role:'',outline_status:'',"
                + "min_area_mm2:''};var packagingPartsShown=[];", ctx);
fns.forEach(function (body) { vm.runInContext(body, ctx); });
vm.runInContext(fn, ctx);
Promise.resolve()
  .then(function () { return vm.runInContext("openProject('" + process.env.CPQ_PID + "')", ctx); })
  .then(function () {}, function (error) {
    state.error = String((error && error.message) || error);
  })
  .then(function () { return new Promise(function (done) { setImmediate(done); }); })
  .then(function () {
    console.log(JSON.stringify({ missing: false, hits: hits, marks: marks, state: state }));
  });
"""


def _run_node(mode: str, variant: str = "", cases_path: str = "") -> dict:
    env = dict(os.environ)
    env.update({"CPQ_APP_JS": str(APP_JS), "CPQ_MODE": mode, "CPQ_VARIANT": variant,
                "CPQ_PID": PID, "CPQ_CASES": cases_path})
    proc = subprocess.run(["node", "-e", NODE_JS], env=env,
                          text=True, capture_output=True, timeout=120)
    if proc.returncode:
        raise AssertionError("node 跑不动：%s" % (proc.stderr or proc.stdout))
    return json.loads(proc.stdout.strip().splitlines()[-1])


def probe_entry(flow_fails: bool = False) -> dict:
    return _run_node("entry", "flow-fails" if flow_fails else "ok")


def call_empty_text(cases: list) -> list:
    handle = pathlib.Path(tempfile.mkdtemp()) / "cases.json"
    handle.write_text(json.dumps(cases), encoding="utf-8")
    payload = _run_node("emptytxt", cases_path=str(handle))
    if payload.get("missing"):
        raise AssertionError("找不到 packagingPartsEmptyText()")
    return payload["out"]


def requested(out: dict) -> list:
    return [hit["url"] for hit in out["hits"]]


def parts_reads(out: dict) -> list:
    """只认零件文档那一条（业务部件清单是另一条 URL，不算）。"""
    return [hit for hit in out["hits"]
            if PARTS_URL in hit["url"] and "business-parts" not in hit["url"]]


def flow_with_evidence(parts_id: str = EVIDENCE_PARTS_ID, total: int = EVIDENCE_TOTAL,
                       status: str = "completed") -> dict:
    return {"run_id": "run-1", "status": "completed",
            "steps": [{"step_id": "packaging_semantics", "status": "completed", "detail": {}},
                      {"step_id": "parts_extract", "status": status,
                       "detail": {"parts_id": parts_id, "parts_hash": "hash",
                                  "parts_total": total}}]}


# --------------------------------------------------------------------------- #
# A 组：进入即读回
# --------------------------------------------------------------------------- #
class AEntryReadsBackTheDocument(unittest.TestCase):
    def test_a1_entering_the_board_reads_the_parts_document(self):
        """进入 2.1 必须真的发出一次零件文档读取（今天：一条都没有）。"""
        out = probe_entry()
        self.assertFalse(out.get("missing"), "找不到 openProject() 或它的加载链")
        self.assertTrue(parts_reads(out),
                        "进入项目后一次都没读零件文档（请求只有 %r，Spec §4 A1）—— "
                        "零件明明已经在库里，页面却只会说'还没生成，请先跑一键解析图纸'"
                        % (requested(out),))

    def test_a2_flow_state_failure_does_not_skip_the_parts_read(self):
        """链路状态那一路没读到（抛异常），零件读回仍必须发生（两条路互不牵连）。"""
        out = probe_entry(flow_fails=True)
        self.assertTrue(parts_reads(out),
                        "链路状态读不到就把零件读回一起跳过了（请求 %r，错误 %r，Spec §4 A2）"
                        % (requested(out), out["state"]["error"]))

    def test_a3_loader_runs_after_the_entry_decision_with_a_project_identity(self):
        """发起加载时入口已判定、`currentProject` 已是本项目（否则读的是别人/上一次的项目）。"""
        out = probe_entry()
        hits = parts_reads(out)
        self.assertTrue(hits,
                        "进入路径没有读零件文档（请求 %r，Spec §4 A1/A3）" % (requested(out),))
        hit = hits[0]
        self.assertIn("entry-decision", hit["marks"],
                      "零件读取发生在入口判定之前：`currentDrawingEntry` 还没就位，"
                      "renderTree() 会把零件面板收掉（Spec §4 A1）")
        self.assertEqual(PID, hit["project"],
                         "读零件时 currentProject 不是本项目（是 %r，Spec §4 A3）"
                         % (hit["project"],))

    def test_a4_entry_does_not_build_its_own_parts_url(self):
        """护栏：进入路径复用唯一加载器，不许自己拼第二份零件 URL。"""
        src = APP_JS.read_text(encoding="utf-8")
        start = src.index("async function openProject(")
        end = src.index("\nfunction avgConfidence(", start)
        body = src[start:end]
        self.assertNotIn(PARTS_URL, body,
                         "openProject() 里出现了第二份零件 URL（Spec §4 A5：只许调 "
                         "refreshPackagingParts()）")

    def test_a5_refresh_still_redraws_the_left_column(self):
        """护栏：唯一加载器仍然负责重画左栏（进入路径复用它就等于会渲染）。"""
        src = APP_JS.read_text(encoding="utf-8")
        start = src.index("async function refreshPackagingParts(")
        end = src.index("\nfunction packagingRoleUnboundReadProblemText(", start)
        self.assertIn("renderTree(", src[start:end],
                      "refreshPackagingParts() 不再重画左栏（Spec §3 第 4 条）")


# --------------------------------------------------------------------------- #
# B 组：链路已经产出过零件时不许催人重新解析
# --------------------------------------------------------------------------- #
class BEmptyTextUsesTheFlowEvidence(unittest.TestCase):
    def test_b1_extracted_parts_never_ask_for_another_parse(self):
        """链路里已经有这一版零件 → 空态必须给证据，不许再说'还没生成，请先跑一键解析'。"""
        text = call_empty_text([{"doc": {}, "preconditions": [], "flow": flow_with_evidence()}])[0]
        self.assertNotIn("零件文档还没生成", text, "给了错的下一步（Spec §4 B3）：%r" % (text,))
        self.assertNotIn("请先跑一键解析图纸", text, "给了错的下一步（Spec §4 B3）：%r" % (text,))
        self.assertIn(EVIDENCE_PARTS_ID, text, "没说清已经有哪一版零件：%r" % (text,))
        self.assertIn(str(EVIDENCE_TOTAL), text, "没给件数证据：%r" % (text,))
        self.assertIn("不用重新解析", text, "没告诉用户不必重跑：%r" % (text,))

    def test_b2_without_evidence_keeps_todays_sentence(self):
        """护栏：没有链路证据时逐字保持今天那句。"""
        text = call_empty_text([{"doc": {}, "preconditions": [], "flow": {}}])[0]
        self.assertEqual(LEGACY_SENTENCE, text)

    def test_b3_blocked_step_is_not_evidence(self):
        """护栏：`parts_extract` 是 blocked（缺前置条件）时不算"已经产出过零件"。"""
        flow = flow_with_evidence(parts_id="", total=0, status="blocked")
        text = call_empty_text([{"doc": {}, "preconditions": [], "flow": flow}])[0]
        self.assertEqual(LEGACY_SENTENCE, text)

    def test_b4_read_problem_still_wins(self):
        """护栏：读失败自己的那句话优先级不变（既有 Spec，一个字不改）。"""
        doc = {"read_problem": {"code": "parts_unavailable", "status": 500, "message": ""}}
        text = call_empty_text([{"doc": doc, "preconditions": [], "flow": flow_with_evidence()}])[0]
        self.assertEqual("暂时读不到零件文档（HTTP 500），请稍后重试；这不代表这份图纸没有零件", text)

    def test_b5_parts_present_keeps_the_column_silent(self):
        """护栏：有零件就一定不给空态文案（含给了链路证据时）。"""
        doc = {"parts": [{"part_code": "DWG-P01"}], "built": True, "total": 1}
        text = call_empty_text([{"doc": doc, "preconditions": [], "flow": flow_with_evidence()}])[0]
        self.assertEqual("", text)


# --------------------------------------------------------------------------- #
# C 组：服务端护栏（数据本来就没丢）
# --------------------------------------------------------------------------- #
class CServerKeepsTheDocument(unittest.TestCase):
    def setUp(self):
        self._backend = meta_backend._backend
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        meta_backend._backend = meta_backend.JsonMetaBackend(self.data_dir)
        self.addCleanup(self._restore)

    def _restore(self):
        meta_backend._backend = self._backend

    @staticmethod
    def _doc(total: int) -> dict:
        return {"built": True, "source": {"kind": "cad_ir"},
                "parts": [{"part_code": "DWG-P%02d" % index, "length_mm": 100.0 + index,
                           "width_mm": 50.0} for index in range(1, total + 1)],
                "filtered": [], "unavailable": [], "stats": {"part_total": total}}

    def test_c1_a_fresh_backend_reads_the_same_version(self):
        pid = "probeentry01"
        saved = packaging_parts.save_parts(pid, self._doc(3))
        meta_backend._backend = meta_backend.JsonMetaBackend(self.data_dir)   # 新实例 = 换进程
        again = packaging_parts.load_parts(pid) or {}
        self.assertEqual(saved.get("parts_id"), again.get("parts_id"),
                         "换一个后端实例就读不到同一版零件了（Spec §4 C1）")
        self.assertEqual(3, len(again.get("parts") or []))

    def test_c2_no_parts_id_returns_the_latest_version(self):
        pid = "probeentry02"
        first = packaging_parts.save_parts(pid, self._doc(2))
        second = packaging_parts.save_parts(pid, self._doc(4))
        self.assertNotEqual(first.get("parts_id"), second.get("parts_id"))
        latest = packaging_parts.load_parts(pid) or {}
        self.assertEqual(second.get("parts_id"), latest.get("parts_id"),
                         "不带 parts_id 时没回最新一版（Spec §4 C2）")
        self.assertEqual(4, len(latest.get("parts") or []))
        again = packaging_parts.save_parts(pid, self._doc(4))
        self.assertEqual(second.get("parts_id"), again.get("parts_id"), "同一份内容必须幂等")
        self.assertEqual(2, len(packaging_parts.list_parts(pid)), "同一 parts_id 必须覆盖，不新增记录")


if __name__ == "__main__":
    unittest.main()
