"""看板动作静态 role 在运行时快照里的解析（1.2 / 1.3 / 3.2 主按钮被静默降级）。

背景（本文件建立时的实测结论）：
  · tech-board-runtime.js 的 entryState() 只读 getState() 返回的 role，条目外层静态
    声明的 role 从不进入 raw，于是「外层写 role: 'primary'、getState 只返回
    visible/enabled/busy」的条目全被降级成 'aux'；
  · 全仓库九个阶段页面里，条目外层静态 primary 只有三处：1.2 confirmRequirement、
    1.3 submitRequirementReview、3.2 approveProcessReport，三颗按钮因此在父壳左侧
    操作栏里都渲染成白底描边的次要按钮（父壳只渲染 role === 'primary' 的那一颗）；
  · 旧测试只断言源码文本里出现 role: 'primary'，没有按运行时的真实快照规则执行
    getState()，所以 136 项全绿仍然漏检。

验证方式：在 Node 的 vm 里加载真实的 tech-board-runtime.js，把每个场景注册进一个
全新上下文，然后读运行时真正会发给父壳的快照（snapshot() / publish 出去的信封
payload.actions / auditPrimary()）。不解析源码文本推断行为。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
RUNTIME = FRONTEND / "tech-board-runtime.js"
PAGE_FILES = {
    "requirement-confirm": "requirement-confirm-page.js",
    "requirement-review": "requirement-review-page.js",
    "report-review": "report-review-result.js",
}
STAGE_PAGES = {
    "requirement-create": "requirement-create.js",
    "requirement-confirm": "requirement-confirm-page.js",
    "requirement-review": "requirement-review-page.js",
    "drawing": "app.js",
    "process": "assembly-integration.js",
    "cost": "cost-review.js",
    "summary": "summary-result.js",
    "report-review": "report-review-result.js",
    "report-publish": "report-publish-result.js",
}
# 三处「外层静态 primary、getState 不返回 role」的真实条目（名称 → 页面文件）。
STATIC_PRIMARY_ENTRIES = {
    "confirmRequirement": "requirement-confirm-page.js",
    "submitRequirementReview": "requirement-review-page.js",
    "approveProcessReport": "report-review-result.js",
}

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const runtimeCode = fs.readFileSync(process.argv[2], 'utf8');
const ORIGIN = 'http://localhost';

// 每个场景都用一个全新上下文：注册表互不影响，读到的就是这一帧真实快照。
function boot(search) {
  const handlers = [];
  const env = {
    location: { origin: ORIGIN, search: search || '', pathname: '/board.html' },
    document: { readyState: 'complete' },
    setTimeout: setTimeout, clearTimeout: clearTimeout, clearInterval: clearInterval,
    URLSearchParams: URLSearchParams, console: console,
  };
  env.window = env;
  env.addEventListener = (type, fn) => { if (type === 'message') handlers.push(fn); };
  const posts = [];
  env.parent = { postMessage: (msg) => posts.push(JSON.parse(JSON.stringify(msg))) };
  vm.runInContext(runtimeCode, vm.createContext(env), { filename: 'tech-board-runtime.js' });
  return { runtime: env.TechBoardRuntime, posts: posts };
}

function pick(state) {
  return { label: state.label, role: state.role, order: state.order, hint: state.hint,
           visible: state.visible, enabled: state.enabled, busy: state.busy };
}

// 父壳真正收到的 action-state 信封里的 role（不是运行时内部返回值的副本）。
function publishedRole(posts, name) {
  for (let i = posts.length - 1; i >= 0; i -= 1) {
    const msg = posts[i];
    if (!msg || msg.type !== 'state' || msg.name !== 'action-state') continue;
    const payload = msg.payload || {};
    const state = (payload.actions || {})[name];
    if (state) return state.role;
  }
  return null;
}

const out = {};

// 场景 1：1.2/1.3/3.2 的真实写法 —— 外层静态 primary，getState 不返回 role。
{
  const { runtime, posts } = boot('?project=P1&stage=requirement-confirm');
  runtime.registerActions({
    confirmRequirement: {
      label: '✓ 通过确认',
      role: 'primary',
      order: 10,
      hint: '确认需求单并进入审核',
      run: () => ({ ok: true }),
      getState: () => ({ visible: true, enabled: true, busy: false }),
    },
  });
  out.static_primary = pick(runtime.snapshot().actions.confirmRequirement);
  out.static_primary_published_role = publishedRole(posts, 'confirmRequirement');
  out.static_primary_audit = runtime.auditPrimary();
  out.static_primary_diagnostics = runtime.primaryDiagnostics();
}

// 场景 2：完全没有 getState 的静态条目。
{
  const { runtime } = boot('?project=P1&stage=drawing');
  runtime.registerActions({
    confirmParseResult: { label: '确认解析结果', role: 'primary', run: () => ({ ok: true }) },
  });
  out.no_getstate = pick(runtime.snapshot().actions.confirmParseResult);
}

// 场景 3：getState 返回 role（动态优先，2.2/2.3/3.1/3.3 依赖它）。
{
  const { runtime } = boot('?project=P1&stage=process');
  runtime.registerActions({
    generateIntegrationProcess: {
      label: '一键生成组装工艺', role: 'primary', order: 40,
      run: () => ({ ok: true }),
      getState: () => ({ visible: true, enabled: true, busy: false, role: 'aux' }),
    },
  });
  out.dynamic_override = pick(runtime.snapshot().actions.generateIntegrationProcess);
}

// 场景 4：外层静态 aux + getState 不返回 role → 不得被升成主按钮。
{
  const { runtime } = boot('?project=P1&stage=process');
  runtime.registerActions({
    saveIntegrationParams: {
      label: '保存参数', role: 'aux', order: 50,
      run: () => ({ ok: true }),
      getState: () => ({ visible: true, enabled: true, busy: false }),
    },
  });
  out.static_aux = pick(runtime.snapshot().actions.saveIntegrationParams);
}

// 场景 5：getState 抛错 → 保留静态元数据基线，不抛异常。
{
  const { runtime } = boot('?project=P1&stage=cost');
  runtime.registerActions({
    confirmCostReview: {
      label: '确认成本', role: 'primary', order: 20, hint: '确认成本',
      run: () => ({ ok: true }),
      getState: () => { throw new Error('boom'); },
    },
  });
  out.throwing_getstate = pick(runtime.snapshot().actions.confirmCostReview);
}

// 场景 6：updateActionState 覆盖值优先级最高。
{
  const { runtime } = boot('?project=P1&stage=summary');
  runtime.registerActions({
    submitProcessReportReview: {
      label: '提交审核', role: 'primary', order: 10,
      run: () => ({ ok: true }),
      getState: () => ({ visible: true, enabled: true, busy: false }),
    },
  });
  runtime.updateActionState('submitProcessReportReview', { role: 'aux' });
  out.override = pick(runtime.snapshot().actions.submitProcessReportReview);
}

// 场景 7：order / hint 的静态回退与动态优先。
{
  const { runtime } = boot('?project=P1&stage=report-review');
  runtime.registerActions({
    approveProcessReport: {
      label: '审核通过并进入下一步', role: 'primary', order: 10, hint: '静态提示',
      run: () => ({ ok: true }),
      getState: () => ({ visible: true, enabled: true, busy: false }),
    },
    autoApprove: {
      label: '自动通过', role: 'primary', order: 99, hint: '静态提示',
      run: () => ({ ok: true }),
      getState: () => ({ visible: false, enabled: true, busy: false, order: 7, hint: '动态提示' }),
    },
  });
  out.meta_fallback = pick(runtime.snapshot().actions.approveProcessReport);
  out.meta_dynamic = pick(runtime.snapshot().actions.autoApprove);
}

console.log(JSON.stringify(out));
"""


def read(path: str) -> str:
    return (ROOT / path).read_bytes().replace(b"\x00", b"").decode("utf-8")


def balanced_block(source: str, open_index: int) -> str:
    """从 source[open_index]（'{' 或 '('）起取括号配平的一段，忽略字符串里的括号。"""
    pairs = {"{": "}", "(": ")", "[": "]"}
    open_ch = source[open_index]
    close_ch = pairs[open_ch]
    depth = 0
    quote = ""
    escaped = False
    index = open_index
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        else:
            if char in "\"'`":
                quote = char
            elif char == open_ch:
                depth += 1
            elif char == close_ch:
                depth -= 1
                if depth == 0:
                    return source[open_index:index + 1]
        index += 1
    raise AssertionError("括号不配平：起始位置 %d" % open_index)


def entry_block(source: str, action: str) -> str:
    match = re.search(r"\b%s\s*:\s*\{" % re.escape(action), source)
    if not match:
        raise AssertionError("未找到动作条目：%s" % action)
    return balanced_block(source, source.index("{", match.start()))


def run_harness() -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("未安装 node，无法执行看板运行时快照测试")
    workdir = tempfile.mkdtemp(prefix="tech-board-role-")
    try:
        script = Path(workdir) / "harness.js"
        script.write_text(HARNESS, encoding="utf-8")
        completed = subprocess.run(
            [node, str(script), str(RUNTIME)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        )
        if completed.returncode != 0:
            raise AssertionError(
                "运行时快照 harness 执行失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout, completed.stderr))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class TechBoardStaticActionRoleSnapshot(unittest.TestCase):
    """运行时快照必须继承条目外层的静态 role / order / hint。"""

    @classmethod
    def setUpClass(cls):
        cls.data = run_harness()

    # ---------------------------------------------------------------- 行为（真实运行时）
    def test_static_primary_role_survives_getstate_without_role(self):
        state = self.data["static_primary"]
        self.assertEqual("primary", state["role"], "外层静态 role: 'primary' 被 getState() 覆盖成 aux")
        self.assertEqual("✓ 通过确认", state["label"])
        self.assertIs(True, state["visible"])
        self.assertIs(True, state["enabled"])
        self.assertIs(False, state["busy"])

    def test_published_action_state_carries_static_primary_role(self):
        self.assertEqual(
            "primary", self.data["static_primary_published_role"],
            "父壳收到的 action-state 信封里该动作仍是 aux，主槽位不会渲染成蓝色实心按钮")

    def test_audit_sees_exactly_one_primary_and_no_diagnostic(self):
        audit = self.data["static_primary_audit"]
        self.assertEqual(1, audit["primary_count"], audit)
        self.assertEqual(["confirmRequirement"], audit["primary_actions"], audit)
        self.assertIs(True, audit["ok"], audit)
        self.assertIsNone(self.data["static_primary_diagnostics"],
                          "静态 primary 生效后不应再报「可见主按钮数不是 1」")

    def test_static_primary_without_getstate_still_primary(self):
        self.assertEqual("primary", self.data["no_getstate"]["role"])

    def test_getstate_role_still_overrides_static_role(self):
        self.assertEqual("aux", self.data["dynamic_override"]["role"],
                         "getState() 动态返回的 role 必须覆盖静态声明（2.2/2.3/3.1/3.3 依赖它）")

    def test_update_action_state_override_still_wins(self):
        self.assertEqual("aux", self.data["override"]["role"])

    def test_static_aux_entry_is_not_promoted(self):
        self.assertEqual("aux", self.data["static_aux"]["role"])

    def test_throwing_getstate_keeps_static_metadata(self):
        state = self.data["throwing_getstate"]
        self.assertEqual("primary", state["role"])
        self.assertEqual(20, state["order"])
        self.assertEqual("确认成本", state["hint"])
        self.assertIs(True, state["visible"])

    def test_order_and_hint_fall_back_to_static_then_yield_to_getstate(self):
        fallback = self.data["meta_fallback"]
        self.assertEqual(10, fallback["order"], fallback)
        self.assertEqual("静态提示", fallback["hint"], fallback)
        dynamic = self.data["meta_dynamic"]
        self.assertEqual(7, dynamic["order"], dynamic)
        self.assertEqual("动态提示", dynamic["hint"], dynamic)


class TechBoardStaticActionRoleSingleSource(unittest.TestCase):
    """真实页面必须以静态 role 为唯一事实来源，运行时契约与动作表不得缩水。"""

    def test_three_reported_entries_declare_static_primary_role(self):
        for action, filename in STATIC_PRIMARY_ENTRIES.items():
            with self.subTest(action=action, file=filename):
                block = entry_block(read("tech_app/frontend/%s" % filename), action)
                self.assertRegex(block, r"\brole:\s*'primary'",
                                 "条目外层的静态 role: 'primary' 被删除或挪走了")

    def test_three_reported_entries_do_not_duplicate_role_inside_getstate(self):
        for action, filename in STATIC_PRIMARY_ENTRIES.items():
            with self.subTest(action=action, file=filename):
                block = entry_block(read("tech_app/frontend/%s" % filename), action)
                getstate_at = block.find("getState")
                self.assertGreater(getstate_at, -1, "%s 缺少 getState()" % action)
                getstate_part = block[getstate_at:]
                self.assertIsNone(
                    re.search(r"\brole\s*:", getstate_part),
                    "getState() 里又写了一遍 role：同一颗按钮出现两处事实来源")

    def test_runtime_keeps_public_api_and_protocol_constants(self):
        source = read("tech_app/frontend/tech-board-runtime.js")
        for name in ("registerActions", "registerViews", "updateActionState", "refreshState",
                     "setContext", "snapshot", "auditPrimary", "primaryDiagnostics",
                     "publish", "emitState", "publishStatus", "setView", "nextRequestId"):
            with self.subTest(export=name):
                self.assertRegex(source, r"\b%s\s*:" % re.escape(name))
        self.assertIn("var NAMESPACE = 'cpq:tech-board';", source)
        self.assertIn("var VERSION = 1;", source)

    def test_nine_stage_pages_keep_registering_their_actions(self):
        self.assertEqual(9, len(STAGE_PAGES))
        for stage, filename in STAGE_PAGES.items():
            with self.subTest(stage=stage):
                self.assertIn("registerActions", read("tech_app/frontend/%s" % filename))


if __name__ == "__main__":
    unittest.main()
