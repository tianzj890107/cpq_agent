"""Guard packaging advice copy from treating non-runnable candidates as below-threshold scores."""
import pathlib
import json
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = ROOT / "确认需求解析结果.html"


def render_suggestion(match):
    source = HTML.read_text(encoding="utf-8")
    helper_start = source.index("    function packagingMatchNotice(m) {")
    helper_end = source.index("    function renderCandidates(ui, opts) {", helper_start)
    suggestion_start = source.index("    function showTechNewSuggestion(m) {")
    suggestion_end = source.index("\n    /* ---------------- 工艺回传的", suggestion_start)
    function_source = source[helper_start:helper_end] + source[suggestion_start:suggestion_end]
    script = r"""
const nodes = {};
const document = {
  getElementById: (id) => id === 'wfTechNewBubble' ? (nodes[id] || null) : (nodes[id] || (nodes[id] = {})),
  createElement: () => ({ setAttribute() {} }),
};
const chatEl = { appendChild(node) { nodes[node.id] = node; } };
const scrollChat = () => {};
const escapeHtml = (value) => String(value);
const wfOpenSend = () => {};
""" + function_source + "\nshowTechNewSuggestion(" + json.dumps(match, ensure_ascii=False) + ");\n"
    script += "console.log(nodes.wfTechNewBubble.innerHTML);"
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return proc.stdout


class PackagingMatchAdviceCopyTests(unittest.TestCase):
    def test_size_out_of_range_does_not_claim_score_below_70(self):
        rendered = render_suggestion({
            "industry": "packaging", "below": True, "threshold": None,
            "needs_new_tooling": True, "new_tooling_reason": "size_out_of_range",
            "best": {"total": 79.4}, "advice": "盒型库里没有可确认的现有盒型（size_out_of_range），需要新制评估。",
        })
        self.assertNotIn("低于 70 分", rendered)
        self.assertIn("尺寸", rendered)
        self.assertIn("79.4", rendered)

    def test_no_box_type_does_not_claim_size_out_of_range(self):
        rendered = render_suggestion({
            "industry": "packaging", "below": True, "threshold": None,
            "needs_new_tooling": True, "new_tooling_reason": "no_box_type",
            "best": {}, "advice": "盒型库里没有可确认的现有盒型（no_box_type），需要新制评估。",
        })
        self.assertNotIn("尺寸超出", rendered)
        self.assertIn("暂无可用盒型", rendered)

    def test_other_industries_keep_their_actual_threshold_wording(self):
        rendered = render_suggestion({
            "industry": "battery", "below": True, "threshold": 70,
            "best": {"total": 64.5}, "advice": "无高度匹配标品。",
        })
        self.assertIn("64.5 分，低于 70 分", rendered)


if __name__ == "__main__":
    unittest.main()
