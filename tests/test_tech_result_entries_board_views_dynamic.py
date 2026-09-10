"""第 5 步动态验证：看板视图导航协议与“左侧视图名 ⊆ 看板已注册视图”。

分成两半：

1. 在 Node 里用最小宿主加载真实的 `tech-board-runtime.js`，真发 `navigate-view`
   命令，验证已注册视图会被切到、未知视图返回结构化失败、非同源/非父窗口消息被拒；
2. 把父壳三个来源（结果按钮成对登记、dispatchDrawingCapability 字面量、＋ 菜单的
   data-tech-capability）合起来，核对是否都被 2.1 看板注册。

不联网、不起服务、不读真实业务数据。
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

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const posted = [];
const handlers = {};

global.window = global;
global.parent = { postMessage: (message) => posted.push(message) };
global.location = { origin: 'http://localhost', search: '' };
global.document = { readyState: 'complete' };
global.addEventListener = (type, handler) => { handlers[type] = handler; };

vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'));

const runtime = global.TechBoardRuntime;
const views = {};
['parts', 'questions', 'report', 'evidence', 'review', 'files'].forEach((name) => {
  views[name] = { label: name, run: () => ({ shown: name }) };
});
runtime.registerViews(views);

function send(name, payload, requestId, overrides) {
  const options = overrides || {};
  handlers['message']({
    origin: options.origin || 'http://localhost',
    source: options.source || global.parent,
    data: {
      namespace: 'cpq:tech-board', version: 1, type: 'command',
      requestId: requestId, projectId: '', stage: 'drawing',
      name: name, payload: payload || {},
    },
  });
}
const find = (id) => posted.filter((m) => m && m.type === 'result' && m.requestId === id).pop() || null;

send('navigate-view', { view: 'parts' }, 'req-ok');
send('navigate-view', { view: 'no-such-view' }, 'req-unknown');
const before = posted.length;
send('navigate-view', { view: 'parts' }, 'req-foreign-origin', { origin: 'http://evil.example' });
send('navigate-view', { view: 'parts' }, 'req-foreign-source', { source: { postMessage: () => {} } });
const foreignReplies = posted.length - before;

setTimeout(() => {
  const ok = find('req-ok');
  const unknown = find('req-unknown');
  process.stdout.write(JSON.stringify({
    ok_result: ok && ok.payload ? ok.payload : null,
    unknown_result: unknown && unknown.payload ? unknown.payload : null,
    active: runtime.snapshot().view.active,
    foreign_replies: foreignReplies,
  }));
}, 80);
"""


class TechResultEntriesBoardViewsDynamicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = (FRONTEND / "agent-chat.js").read_text(
            encoding="utf-8", errors="replace").replace("\x00", "")
        cls.html = (FRONTEND / "tech-workbench.html").read_text(encoding="utf-8")
        cls.board = (FRONTEND / "app.js").read_text(encoding="utf-8", errors="replace")

    def _registered_views(self):
        match = re.search(r"registerViews\(\s*\{([\s\S]*?)\n\s*\}\)", self.board)
        if not match:
            return set()
        return set(re.findall(r"(?:^|[\s{,])['\"]?([A-Za-z][\w-]*)['\"]?\s*:", match.group(1)))

    def _left_view_targets(self):
        targets = set(re.findall(
            r'["\']oc[A-Za-z]*Action["\']\s*,\s*["\']([a-z-]+)["\']', self.chat))
        targets |= set(re.findall(r'dispatchDrawingCapability\(\s*["\']([a-z-]+)["\']', self.chat))
        targets |= set(re.findall(r'data-tech-capability="([a-z0-9-]+)"', self.html))
        return {name for name in targets if name}

    @unittest.skipUnless(shutil.which("node"), "需要 node 才能跑运行时动态验证")
    def test_runtime_switches_registered_view_and_rejects_unknown(self):
        self.assertTrue(RUNTIME.is_file(), "缺少 tech-board-runtime.js")
        with tempfile.TemporaryDirectory(prefix="cpq-view-nav-") as tmp:
            harness = Path(tmp) / "harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            proc = subprocess.run(
                [shutil.which("node"), str(harness), str(RUNTIME)],
                capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, f"运行时宿主失败：{proc.stderr}")
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok_result"] and result["ok_result"]["ok"],
                        f"navigate-view 未成功：{result['ok_result']}")
        self.assertEqual(result["active"], "parts", "视图未切到 parts")
        unknown = result["unknown_result"] or {}
        self.assertFalse(unknown.get("ok"), "未知视图必须返回失败")
        self.assertEqual((unknown.get("error") or {}).get("code"), "unknown-action")
        self.assertEqual(result["foreign_replies"], 0, "非同源/非父窗口消息必须被拒绝")

    def test_left_view_targets_are_registered_by_drawing_board(self):
        targets = self._left_view_targets()
        self.assertTrue(targets, "未识别出父壳的看板视图入口")
        registered = self._registered_views()
        missing = sorted(targets - registered)
        self.assertFalse(missing, f"父壳入口对应的视图未在看板注册：{missing}")


if __name__ == "__main__":
    unittest.main()
