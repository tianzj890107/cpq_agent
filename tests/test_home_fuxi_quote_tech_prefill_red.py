"""Homepage contracts; Node executes the real mode switch with isolated DOM stubs."""
import pathlib
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = (ROOT / "报价首页.html").read_text(encoding="utf-8")


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.labels = []
        self.collect = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "category-tag" in attrs.get("class", "").split():
            hidden = "hidden" in attrs or re.search(
                r"display\s*:\s*none", attrs.get("style", "")
            )
            self.collect = not hidden

    def handle_data(self, data):
        if self.collect and data.strip():
            self.labels.append(data.strip())

    def handle_endtag(self, tag):
        if tag in ("span", "button"):
            self.collect = False


class HomeFuxiQuoteTech(unittest.TestCase):
    def test_user_supplied_prefills(self):
        mode_start = HOME.index("const MODES = ") + len("const MODES = ")
        mode_end = HOME.index("\n    };", mode_start) + len("\n    }")
        node = shutil.which("node") or '/Users/sunjiongjun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
        script = "const assert = require('node:assert/strict'); const modes = " + HOME[mode_start:mode_end] + r""";
assert.equal(modes.tech.prefill, '茅台酒盒\n尺寸：50mm*40mm*30mm；盖面纸，铜版纸，180克重， 亮膜；底面纸 铜版纸，哑膜，180克重；盖板材 灰板， 厚度2.5mm，V槽，闭合方式：天地盖 ，包装品类：酒盒、盒型：高档酒盒；预计首批定1000个');
assert.equal(modes.analytics.prefill, '为什么2月份管理单元2800采购订单金额过高，按照已审批口径，有什么改进措施和行动建议，分析数据周期1~4月');
assert.equal(modes.quote.prefill, '', '报价正文尚未提供');
"""
        result = subprocess.run([node, '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_company_logo_alongside_fuxi(self):
        brand = re.search(r'<div class="brand-lockup">([\s\S]*?)</div>', HOME)
        self.assertIsNotNone(brand, '两枚 Logo 应组合展示')
        self.assertIn('assets/brand/fuxi-logo.png', brand.group(1))
        self.assertIn('assets/brand/company-logo.png', brand.group(1))
        self.assertTrue((ROOT / 'assets/brand/company-logo.png').is_file())

    def test_primary_tabs_quote_tech_and_analytics(self):
        parser = Tags()
        parser.feed(HOME)
        self.assertEqual(parser.labels, ["报价智能体", "技术工艺智能体", "经营分析智能体"])

    def test_fuxi_brand_and_headline_present(self):
        for text in ("FUXI", "伏羲相伴", "智慧相随"):
            with self.subTest(text=text):
                self.assertTrue(text in HOME, "首页缺少品牌文案：" + text)

    def test_real_mode_switch_fills_editable_value(self):
        node = shutil.which("node")
        if not node:
            bundled = pathlib.Path("/Users/sunjiongjun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node")
            node = str(bundled) if bundled.exists() else None
        self.assertIsNotNone(node, "需要 Node 执行实际 setMode 行为，不能静默跳过")
        match = re.search(r"function setMode\(mode\)\s*\{[\s\S]*?(?=\n    function syncAssistantUrl)", HOME)
        self.assertIsNotNone(match, "保留首页统一模式切换入口")
        mode_start = HOME.index("const MODES = ") + len("const MODES = ")
        mode_end = HOME.index("\n    };", mode_start) + len("\n    }")
        listener_start = HOME.index("    if (textarea && counter) {")
        listener_end = HOME.index("\n    // ===== 助手模式", listener_start)
        script = """
const assert = require('node:assert/strict');
const MODES = MODE_LITERAL;
MODES.quote.prefill = '报价测试正文：客户甲，储能项目。';
MODES.tech.prefill = '工艺测试正文：法兰盘，批量 500。';
let currentMode = null;
let inputListener;
const textarea = {value: '', placeholder: '', dataset: {}, focus(){},
  addEventListener(name, handler){if(name === 'input') inputListener = handler;},
  dispatchEvent(){inputListener.call(this);}, style: {}, scrollHeight: 120};
const document = {querySelectorAll(){return [];}, querySelector(){return null;}};
const greetingTitle = null, introEl = null, newQuoteBtn = null, searchInput = null;
const counter = {textContent: '', style: {}};
function applyUploadMode(){} function loadCards(){} function syncAssistantUrl(){}
function wireListTabs(){} function tagToMode(){} function techEntries(){return [];}
INPUT_LISTENER
MODE_SWITCH
setMode('quote');
assert.equal(textarea.value, MODES.quote.prefill, '首次进入应填入报价正文');
assert.equal(counter.textContent, MODES.quote.prefill.length + '/500');
assert.equal(textarea.style.height, '120px');
textarea.value = '用户改过的报价';
setMode('quote');
assert.equal(textarea.value, '用户改过的报价', '同 tab 不覆盖编辑');
setMode('tech');
assert.equal(textarea.value, MODES.tech.prefill, '切换技术工艺应替换正文');
setMode('quote');
assert.equal(textarea.value, MODES.quote.prefill, '切回报价应填入报价正文');
MODES.tech.prefill = '长正文'.repeat(250);
textarea.scrollHeight = 800;
setMode('tech');
assert.equal(textarea.value, MODES.tech.prefill, '长正文不得截断');
assert.equal(counter.textContent, '750/500');
assert.equal(textarea.style.height, '280px');
assert.equal(textarea.style.overflowY, 'auto');
""".replace("MODE_LITERAL", HOME[mode_start:mode_end]).replace("MODE_SWITCH", match.group()).replace("INPUT_LISTENER", HOME[listener_start:listener_end])
        result = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
