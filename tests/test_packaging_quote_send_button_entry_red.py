"""红测：包装整包回传必须有**前端可点的入口**，且既有按钮出口的正文要带上整包。

Spec：`docs/specs/packaging-quote-send-button-entry.md`

现状缺口（9-22 在本机 HEAD 上逐条核对，不是推断）：
  · `packaging-quote/send` 这个字面量只在后端源码 / tests / docs 里出现，
    `tech_app/frontend/**` 与仓库根 `*.html` **一次都不出现** → 只有 curl / Agent 调得动它。
  · 前端确有"回传"按钮，但打的是别的出口：`#crToQuote`（`cost-review.html:152`）→
    `POST /api/projects/{pid}/cost-review/send-to-quote`；`report-publish-result.js:143` →
    `POST /api/projects/{pid}/process-report/send-to-quote`。
  · 那两条出口的正文来自 `cost_flow.integration_quote_result()`，里面**没有** `packaging_package`；
    整包只有 `packaging_handoff.bridge_result()`（`packaging_handoff.py:270-275`）会装。
  · 面板 `packaging-quote-panel.js:54` 只认 `snapshot.packaging_package`（在
    `报价首页.html:1588` / `确认需求解析结果.html:1000` 都已加载）——面板在，数据来不了。

只点前端按钮的用户因此永远看不到卡片第 2 步的「包装：定价与报价分区」面板。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRONTEND = ROOT / "tech_app" / "frontend"
COST_FLOW = ROOT / "tech_app" / "backend" / "services" / "cost_flow.py"
PACKAGING_HANDOFF = ROOT / "tech_app" / "backend" / "services" / "packaging_handoff.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
COST_REVIEW_JS = FRONTEND / "cost-review.js"
COST_REVIEW_HTML = FRONTEND / "cost-review.html"
PANEL_JS = FRONTEND / "packaging-quote-panel.js"

#: 前端可达性的搜索面（§2.1）：tech_app 页面脚本 + 仓库根的两张报价卡片页。
ROOT_PAGES = ("报价首页.html", "确认需求解析结果.html")
SEND_LITERAL = "packaging-quote/send"


def text_of(path):
    return path.read_text(encoding="utf-8", errors="replace")


def frontend_entry_files():
    """所有可能放这颗按钮的前端实现文件（排除 .venv / vendor）。"""
    files = sorted(FRONTEND.rglob("*.js")) + sorted(FRONTEND.rglob("*.html"))
    files += [ROOT / name for name in ROOT_PAGES if (ROOT / name).exists()]
    return [path for path in files
            if "/.venv/" not in str(path) and "/vendor/" not in str(path)]


def function_body(text, name):
    """按缩进取一个模块级函数的函数体（签名可跨行；取不到返回空串）。"""
    match = re.search(r"^def %s\(" % re.escape(name), text, re.M)
    if not match:
        return ""
    lines = text[match.start():].splitlines(keepends=True)
    index = 0
    while index < len(lines) and not lines[index].rstrip().endswith(":"):
        index += 1
    body = []
    for line in lines[index + 1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        body.append(line)
    return "".join(body)


class AMissingPieces(unittest.TestCase):
    def test_a1_frontend_has_a_packaging_send_button(self):
        """§2.1：包装回传必须有一颗前端可点的按钮（前端源码里出现 `packaging-quote/send`）。"""
        hits = [str(path.relative_to(ROOT)) for path in frontend_entry_files()
                if SEND_LITERAL in text_of(path)]
        self.assertTrue(
            hits,
            "前端一处都没有引用 `%s`（今天命中数 0）—— 包装整包回传只有 HTTP 接口、没有按钮，"
            "只点按钮的用户跑不到这条链上（Spec §2.1 / §1.1）。" % SEND_LITERAL)

    def test_a2_shared_quote_body_carries_the_packaging_package(self):
        """§2.2：既有按钮出口（cost-review / process-report 共用正文）要能带 `packaging_package`。"""
        text = text_of(COST_FLOW)
        self.assertIn("def integration_quote_result", text, "测试前提失效：正文构造函数必须仍在")
        self.assertTrue(
            "packaging_package" in text,
            "`cost_flow.py` 里没有 `packaging_package` —— 从成本复核页点「回传销售经理继续报价」时，"
            "包装整包不会被带过去，卡片第 2 步的包装分区永远空着（Spec §2.2 / §1.3）。")


class BGuards(unittest.TestCase):
    def test_b1_bridge_result_still_wraps_the_ten_section_package(self):
        text = text_of(PACKAGING_HANDOFF)
        body = function_body(text, "bridge_result")
        self.assertTrue(body, "取不到 `bridge_result()` —— 测试前提失效")
        self.assertIn('result["packaging_package"] = package', body,
                      "整包只在 `bridge_result()` 里装上，这一句不许被搬走或改名（Spec §2.4）")
        match = re.search(r"PACKAGE_SECTIONS\s*=\s*\(([^)]*)\)", text)
        self.assertIsNotNone(match, "`PACKAGE_SECTIONS` 必须仍在（Spec §2.4）")
        sections = re.findall(r'"([a-z_]+)"', match.group(1))
        self.assertEqual(
            ["industry", "requirement", "box_type", "params", "bom", "route",
             "cost", "gaps", "formulas", "source"], sections,
            "交接包仍是这 10 段、顺序不变（Spec §2.4）：%s" % sections)

    def test_b2_write_roles_and_gap_waiver_unchanged(self):
        text = text_of(PACKAGING_HANDOFF)
        self.assertRegex(
            text,
            r'HANDOFF_WRITE_ROLES\s*=\s*\{[^}]*"finance_manager"[^}]*"process_manager"'
            r'[^}]*"process_director"[^}]*"admin"[^}]*\}',
            "写权限闭集不许被放宽或收窄（Spec §2.3）")
        body = function_body(text, "_guard_gaps")
        self.assertTrue(body, "取不到 `_guard_gaps()` —— 测试前提失效")
        self.assertIn("allow_gaps", body, "缺口放行开关必须仍在（Spec §2.3）")
        self.assertIn("reason", body, "`allow_gaps=True` 必须仍要求写明原因（Spec §2.3）")

    def test_b3_existing_send_to_quote_button_survives(self):
        self.assertIn("cost-review/send-to-quote", text_of(MAIN_PY),
                      "既有出口不许被换掉（Spec §2.5）")
        html = text_of(COST_REVIEW_HTML)
        self.assertIn('id="crToQuote"', html, "`#crToQuote` 按钮必须仍在（Spec §2.5）")
        self.assertIn("crRunOp('send-to-quote')", text_of(COST_REVIEW_JS),
                      "`#crToQuote` 必须仍打 `send-to-quote`（Spec §2.5）")

    def test_b4_panel_still_keyed_on_snapshot_packaging_package(self):
        text = text_of(PANEL_JS)
        self.assertIn("snapshot.packaging_package", text,
                      "面板仍必须只认快照里的 `packaging_package`，不许自己兜底造一份（Spec §2.6）")
        for page in ROOT_PAGES:
            self.assertIn("packaging-quote-panel.js", text_of(ROOT / page),
                          "%s 必须仍加载面板脚本（Spec §2.6）" % page)


if __name__ == "__main__":
    unittest.main()
