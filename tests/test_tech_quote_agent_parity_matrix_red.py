"""第 21 步红测：技术工艺左侧会话与报价 Agent 的交互能力对照。

覆盖：
1. 新增可机读对照表 docs/specs/tech-agent-recovery-21-quote-parity.json，正好覆盖十四项能力；
2. 每行都有 quote / tech 锚点，锚点必须解析到真实代码；
3. status=live 的行 tech 锚点必须已存在；status=gated 的行必须写 gated_by（16–19）
   且该批次的 Spec 与 Red 测试文件真实存在；
4. 报价参照文件存在；技术工艺侧十四项一项都不缺（缺的必须显式 gated 并标批次）。

不联网、不起服务、不读真实业务数据。
"""
from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "specs" / "tech-agent-recovery-21-quote-parity.json"
QUOTE_PAGE = ROOT / "确认需求解析结果.html"

ROWS = (
    "quick_buttons", "attachments", "tool_trace", "structured_fill",
    "current_step", "prev_next", "transfer", "history", "settings",
    "error_prompt", "task_progress", "result_entry", "session_restore",
    "model_display",
)
# gated_by 批次号 →(Spec, Red 测试)
GATING_ARTIFACTS = {
    16: ("docs/specs/tech-agent-recovery-16-left-toolbar-parity.md",
         "tests/test_tech_left_toolbar_parity_red.py"),
    17: ("docs/specs/tech-agent-recovery-17-tech-ui-protocol.md",
         "tests/test_tech_ui_protocol_red.py"),
    18: ("docs/specs/tech-agent-recovery-18-unified-config-completion.md",
         "tests/test_tech_unified_config_completion_red.py"),
    19: ("docs/specs/tech-agent-recovery-19-nine-stage-page-context.md",
         "tests/test_tech_stage_context_nine_stages_red.py"),
}


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _resolve(anchor):
    """把 '路径#token' 解析成 (文件存在, token 命中)。"""
    if not anchor or "#" not in str(anchor):
        return (False, False)
    rel, token = str(anchor).split("#", 1)
    target = ROOT / rel
    if not target.exists():
        return (False, False)
    return (True, token in _read(target))


class TechQuoteAgentParityMatrixRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = {}
        if MANIFEST.exists():
            try:
                cls.data = json.loads(_read(MANIFEST))
            except json.JSONDecodeError:
                cls.data = {}
        cls.rows = {r.get("id"): r for r in (cls.data.get("rows") or [])
                    if isinstance(r, dict)}

    # ---------------------------------------------------------------- 覆盖
    def test_matrix_declares_fourteen_rows(self):
        self.assertTrue(MANIFEST.exists(),
                        f"缺少对照表 {MANIFEST.relative_to(ROOT)}")
        self.assertEqual(self.data.get("step"), 21, "对照表 step 必须是 21")
        self.assertEqual(set(self.rows), set(ROWS),
                         f"十四项能力必须齐全，实际：{sorted(self.rows)}")
        self.assertEqual(len(self.rows), 14, "十四项不得重复")

    def test_every_row_has_quote_and_tech_anchor(self):
        for row_id in ROWS:
            row = self.rows.get(row_id)
            self.assertIsNotNone(row, f"缺少对照行 {row_id}")
            self.assertTrue(row.get("quote"), f"{row_id} 缺少 quote 锚点")
            self.assertTrue(row.get("tech"), f"{row_id} 缺少 tech 锚点")

    # ---------------------------------------------------------------- 锚点
    def test_quote_anchors_resolve(self):
        for row_id, row in self.rows.items():
            exists, hit = _resolve(row.get("quote"))
            self.assertTrue(exists, f"{row_id} quote 锚点文件不存在：{row.get('quote')}")
            self.assertTrue(hit, f"{row_id} quote 锚点未命中代码：{row.get('quote')}")

    def test_live_rows_resolve_in_tech_code(self):
        for row_id, row in self.rows.items():
            if row.get("status") != "live":
                continue
            exists, hit = _resolve(row.get("tech"))
            self.assertTrue(exists, f"{row_id} tech 锚点文件不存在：{row.get('tech')}")
            self.assertTrue(hit, f"{row_id} tech 锚点未命中代码：{row.get('tech')}")

    def test_gated_rows_reference_existing_batch(self):
        for row_id, row in self.rows.items():
            status = row.get("status")
            self.assertIn(status, ("live", "gated"),
                          f"{row_id} status 只能是 live / gated：{status}")
            if status != "gated":
                continue
            step = row.get("gated_by")
            self.assertIn(step, GATING_ARTIFACTS,
                          f"{row_id} gated_by 必须是 16–19 之一：{step}")
            spec, red = GATING_ARTIFACTS[step]
            self.assertTrue((ROOT / spec).exists(),
                            f"{row_id} 依赖第 {step} 步 Spec 不存在：{spec}")
            self.assertTrue((ROOT / red).exists(),
                            f"{row_id} 依赖第 {step} 步 Red 不存在：{red}")

    # ---------------------------------------------------------------- 守护
    def test_quote_reference_page_exists(self):
        self.assertTrue(QUOTE_PAGE.exists(), "报价参照页面被删除")

    def test_tech_side_has_the_anchor_hosts(self):
        html = _read(ROOT / "tech_app" / "frontend" / "tech-workbench.html")
        for token in ("techChatPane", "ocResultActions", "ocTaskProgressHost",
                      "techHistory", "techSettings", "techModelInfo"):
            self.assertIn(token, html, f"技术工艺侧缺少能力宿主：{token}")


if __name__ == "__main__":
    unittest.main()
