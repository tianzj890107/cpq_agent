"""Red tests for docs/specs/e2e-quick-quote-executable-path.md."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "cpq_agent_server.py").read_text(encoding="utf-8")
PANEL = (ROOT / "tech_app/frontend/quick-quote-panel.js").read_text(encoding="utf-8")


class QuickQuoteTransportRed(unittest.TestCase):
    def test_all_json_responses_are_normalized(self):
        self.assertTrue("def _json_safe_response" in SERVER,
                      "PG datetime/Decimal/UUID 必须先统一转成 JSON-safe 值")

    def test_send_json_invokes_normalizer(self):
        start = SERVER.find("def _send_json")
        self.assertGreaterEqual(start, 0)
        body = SERVER[start:start + 1400]
        self.assertTrue("_json_safe_response" in body,
                      "_send_json 未走统一序列化，datetime 会直接断连接")


class QuickQuoteHttpClosureRed(unittest.TestCase):
    REQUIRED_PATHS = (
        "/api/quick-quote/sessions",
        "/match", "/baseline", "/workspace", "/price", "/confirm",
        "/transfer-to-precise",
    )

    def test_required_commands_are_routed(self):
        for path in self.REQUIRED_PATHS:
            self.assertTrue(path in SERVER, f"快速报价 HTTP 闭环缺少 {path}")

    def test_write_commands_are_idempotent(self):
        self.assertTrue("quick_quote_idempotency" in SERVER,
                      "快速报价写请求必须有幂等键与结果复用")


class QuickQuoteUiClosureRed(unittest.TestCase):
    def test_case_row_is_selectable(self):
        self.assertTrue("selectQuickQuoteBaseline" in PANEL,
                      "案例列表只能看不能选")

    def test_workspace_can_edit_and_reprice(self):
        for token in ("saveQuickQuoteWorkspace", "repriceQuickQuote",
                      "confirmQuickQuote"):
            self.assertTrue(token in PANEL, f"快速报价 UI 缺少 {token}")

    def test_confirm_creates_visible_quote_card(self):
        self.assertTrue("quick_quote_session_id" in PANEL,
                      "确认后没有可恢复的快速报价 session/card 身份")

    def test_dwg_reuses_drawing_flow(self):
        self.assertTrue("transferDwgToDrawingFlow" in PANEL,
                      "DWG 必须复用 drawing-flow，而不是复制或忽略解析能力")

    def test_precise_fallback_exists(self):
        self.assertTrue("transferQuickQuoteToPrecise" in PANEL,
                      "无合格案例时必须可无损转精准报价")


if __name__ == "__main__":
    unittest.main()
