"""红测：去掉「以财务经理身份登录…这里是只读」的只读横幅（外层 + iframe 两处）。

用户要求：这段归属说明「全都不要」。现状 `cpq-sso.js` 的 `showReadonlyBar()` 在
`!state.canWrite` 时往 body 插 `.cpq-sso-bar`，外层工作台与嵌入阶段页各插一条 → 两处同时出现。

红线：写请求拦截（403 预判 + toast）、登录墙、能力分流、后端权限判定一个都不能少。
"""
from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
SSO = (FRONTEND / "cpq-sso.js").read_text(encoding="utf-8", errors="replace")


class ReadonlyBarIsGone(unittest.TestCase):
    def test_bar_renderer_and_styles_are_removed(self):
        for token in ("showReadonlyBar", "cpq-sso-bar"):
            with self.subTest(token=token):
                self.assertNotIn(token, SSO, f"只读横幅已下线，不该再出现 {token}")

    def test_ownership_sentence_is_gone_everywhere(self):
        for token in ("这里是只读", "你负责 <b>2.3 成本测算</b>", "2.3 成本测算，其余步骤只读",
                      "身份登录，"):
            with self.subTest(token=token):
                self.assertNotIn(token, SSO, f"这句归属说明要整体去掉：{token}")
        for path in sorted(FRONTEND.glob("*")):
            if path.suffix not in (".js", ".html", ".css"):
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            with self.subTest(file=path.name):
                self.assertNotIn("这里是只读", body,
                                 f"{path.name} 里又出现了只读横幅文案")

    def test_no_second_bar_injection_path(self):
        self.assertNotIn(".cpq-sso-bar{", SSO, "横幅样式要一起删干净")
        self.assertNotIn("showReadonlyBar", SSO)


class WriteGateStays(unittest.TestCase):
    def test_frontend_gate_and_403_response_stay(self):
        self.assertIn("state.canWrite || (state.canCost && isCostUrl(url))", SSO,
                      "能力分流不能删；前端只是提前告知，判定仍在后端")
        self.assertIn("status: 403", SSO, "写请求预判仍要返回结构化 403")
        self.assertIn("toast(detail)", SSO, "被拦时仍要把原因告诉用户")

    def test_login_wall_and_helpers_stay(self):
        for token in ("showLoginWall", "openCpqLogin", "COST_URL_PATTERNS", "isCostUrl",
                      "cpq-sso-mask", "cpq-sso-ready", "window.CpqSso"):
            with self.subTest(token=token):
                self.assertIn(token, SSO, f"既有能力被删除：{token}")

    def test_blocked_write_toast_still_names_the_owner(self):
        start = SSO.find("var detail = state.canCost")
        self.assertGreater(start, 0, "找不到写请求被拦时的说明文本")
        body = SSO[start:start + 400]
        self.assertIn("归工艺经理", body, "被拦时仍要说清这一步归谁")
        self.assertNotIn("2.3 成本测算", body, "别再复述那段 2.3 归属说明")


if __name__ == "__main__":
    unittest.main()
