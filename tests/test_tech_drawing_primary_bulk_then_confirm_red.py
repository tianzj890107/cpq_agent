"""红测：2.1 主按钮三段式（解析 → 批量工艺推荐 → 确认解析结果）与清单顺序。

用户要求：解析完成后主按钮是「一键生成全部工艺推荐」，**之后**（全部零件都已有工艺推荐）
主按钮才是「确认解析结果」；两者在按钮清单里的位置也照这个顺序排。

现状缺口：`app.js` 里 `confirmDrawingResult` 是 order 15、`role: drawingParsed() ? "primary" : "aux"`，
一解析完就抢走主按钮；`runAllPartProcesses` 是 order 20 且恒为 aux。

红线：解析链路、确认闸门、批量受控串行实现、只读探测、后端单零件路由一个都不能少。
"""
from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "tech_app" / "frontend" / "app.js").read_text(
    encoding="utf-8", errors="replace").replace("\x00", "")
MAIN = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8")


def block_from(text: str, marker: str) -> str:
    """从 marker 起按大括号配平取块；跳过注释与字符串，避免模板字面量里的花括号干扰。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    # marker 可以自带开括号（例如 "async function f(options = {}) {"）。
    tail = idx + len(marker) - 1
    if text[tail:tail + 1] == "{":
        brace = tail
    else:
        brace = text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
        i += 1
    return ""


def order_of(block: str) -> int:
    match = re.search(r"order:\s*(\d+)", block)
    if not match:
        raise AssertionError("动作块里找不到 order")
    return int(match.group(1))


class DrawingPrimaryStateMachineContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parse = block_from(APP, "parseDrawing:")
        cls.bulk = block_from(APP, "runAllPartProcesses:")
        cls.confirm = block_from(APP, "confirmDrawingResult:")
        cls.complete = block_from(APP, "function partsProcessComplete(")
        cls.probe = block_from(APP, "async function probePartsProcessState(")
        cls.render_ir = block_from(APP, "function renderIR(")

    def test_blocks_are_found(self):
        for name, block in (("parseDrawing", self.parse), ("runAllPartProcesses", self.bulk),
                            ("confirmDrawingResult", self.confirm),
                            ("partsProcessComplete", self.complete),
                            ("probePartsProcessState", self.probe)):
            with self.subTest(action=name):
                self.assertTrue(block, f"找不到 {name} 的实现块")

    # ------------------------------------------------ R1 主按钮三段式
    def test_unparsed_stage_keeps_parse_as_the_only_primary(self):
        self.assertRegex(self.parse, r'role:\s*drawingParsed\(\)\s*\?\s*"aux"\s*:\s*"primary"')
        self.assertEqual(order_of(self.parse), 10)

    def test_bulk_process_is_primary_after_parse_until_everything_is_generated(self):
        self.assertEqual(order_of(self.bulk), 15,
                         "「一键生成全部工艺推荐」必须排在「确认解析结果」之前")
        self.assertRegex(
            self.bulk,
            r'role:\s*\(?\s*drawingParsed\(\)\s*&&\s*!partsProcessComplete\(\)\s*\)?\s*'
            r'\?\s*"primary"\s*:\s*"aux"',
            "解析完成且还没全部生成时，主按钮必须是「一键生成全部工艺推荐」",
        )

    def test_confirm_becomes_primary_only_after_everything_is_generated(self):
        self.assertEqual(order_of(self.confirm), 25)
        self.assertRegex(
            self.confirm,
            r'role:\s*partsProcessComplete\(\)\s*\?\s*"primary"\s*:\s*"aux"',
            "只有全部零件都有工艺推荐之后，「确认解析结果」才是主按钮",
        )
        self.assertNotRegex(
            self.confirm,
            r'role:\s*drawingParsed\(\)',
            "「确认解析结果」不得再因为「解析完了」就抢主按钮",
        )
        self.assertIn("visible: drawingParsed()", self.confirm,
                      "确认动作仍只在有解析结果时出现")
        self.assertIn("enabled: true", self.confirm,
                      "没生成完也要可点，点了由既有闸门给真实原因")

    def test_list_order_follows_the_state_machine(self):
        self.assertLess(order_of(self.parse), order_of(self.bulk))
        self.assertLess(order_of(self.bulk), order_of(self.confirm))
        for other in ("modelLookup:", "verify:", "searchComponents:"):
            self.assertGreater(order_of(block_from(APP, other)), order_of(self.confirm),
                               f"{other} 排在确认动作前面，清单顺序不再一致")

    # ------------------------------------------------ R2 全部生成判定
    def test_complete_check_is_synchronous_and_covers_every_part(self):
        self.assertTrue(self.complete)
        self.assertIn("drawingParsed()", self.complete)
        self.assertRegex(self.complete, r"\.every\(")
        self.assertNotIn("fetch(", self.complete, "同步判定里不能发请求")
        self.assertNotIn("await ", self.complete, "getState 只能同步读，不能 await")

    def test_ready_parts_are_cached_per_project_part(self):
        self.assertRegex(APP, r"processReadyParts\s*=\s*new\s+Set\(\)")
        self.assertRegex(APP, r"function\s+markPartProcessReady\s*\(")

    def test_read_only_probe_reuses_existing_check_and_is_deduplicated(self):
        self.assertTrue(self.probe, "缺少 probePartsProcessState()")
        self.assertIn("partHasExistingProcess(", self.probe,
                      "探测必须复用既有只读判定，不得另写第二份")
        self.assertNotIn("fetch(", self.probe, "探测不得自己发请求")
        self.assertNotRegex(self.probe, r'method:\s*"POST"', "探测必须保持只读")
        self.assertRegex(self.probe, r"processProbeKey", "同一份零件表不得重复探测")
        self.assertRegex(self.probe, r"processProbeBusy", "探测需要单飞，避免并发重复打接口")

    def test_probe_refreshes_the_parent_snapshot_without_a_click(self):
        helper = block_from(APP, "function refreshBoardActionState(")
        self.assertTrue(helper, "缺少 refreshBoardActionState()：结论变化要主动重发快照")
        self.assertIn("refreshState(", helper)
        self.assertIn("refreshBoardActionState()", self.probe,
                      "探测结束后必须重发快照，主按钮不能等用户再点一次")
        self.assertIn("refreshBoardActionState()",
                      block_from(APP, "async function runAllPartProcesses(options = {}) {"),
                      "批量跑完 / 逐件成功后同样要重发快照")

    def test_probe_runs_after_ir_render(self):
        self.assertIn("probePartsProcessState()", self.render_ir,
                      "IR 渲染后要按当前零件表探一次，否则重新打开项目时主按钮判错")

    def test_generation_marks_parts_ready(self):
        bulk_run = block_from(APP, "async function runAllPartProcesses(options = {}) {")
        self.assertIn("markPartProcessReady(", bulk_run,
                      "批量里 skip / 成功的零件都要记为「已有工艺推荐」")
        auto_open = block_from(APP, "function autoOpenGeneratedProcess(")
        self.assertIn("markPartProcessReady(", auto_open,
                      "自动展开前读到的「已有工艺」也要记进缓存")

    # ------------------------------------------------ 能力不缩水
    def test_bulk_implementation_and_single_part_route_kept(self):
        bulk_run = block_from(APP, "async function runAllPartProcesses(options = {}) {")
        self.assertIn("partHasExistingProcess(", bulk_run)
        self.assertIn("runOnePartProcess(", bulk_run)
        self.assertIn("/parts/${", APP)
        self.assertIn("/process", APP)
        self.assertNotRegex(MAIN, r'@app\.post\(["\'][^"\']*(?:process-all|process/bulk|bulk-process)')

    def test_confirm_entry_and_parse_entry_kept(self):
        self.assertIn('requestNavigate("process"', self.confirm)
        self.assertIn("parseDrawingInBackground(", self.parse)
        self.assertIn("deferred: true", self.parse)


if __name__ == "__main__":
    unittest.main()
