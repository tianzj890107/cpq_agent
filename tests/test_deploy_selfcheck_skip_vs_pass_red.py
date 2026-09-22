"""红测：部署自检的「跳过」不许被算成「通过」（三态判决 + 令牌失效必须重取）。

Spec：`docs/specs/deploy-selfcheck-skip-vs-pass.md`

现状缺口（34 实测，部署 `925c241` 的第 6b 步）：
  · `权威实样路线自检：读不到知识库（… HTTP 403：内部令牌校验失败），跳过`
    紧接着却是 `{"isolated_downstream_selfcheck": "ok", "problems": []}` 与"隔离端到端自检通过"；
  · 即"有一项没跑"被算成了"全部通过"，退出码仍是 0。
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

DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"
SPEC_BLOCK = "6b."
VERDICTS = ("ok", "failed", "incomplete")


def deploy_block():
    text = DEPLOY_SH.read_text(encoding="utf-8")
    start = text.index(SPEC_BLOCK)
    rest = text[start + len(SPEC_BLOCK):]
    stops = [rest.index(marker) for marker in ("step \"7.", "step \"7b", "\n# ----")
             if marker in rest]
    return rest[:min(stops)] if stops else rest


class VerdictIsThreeState(unittest.TestCase):
    """A 组：自检结论必须三态，且有逐项清单。"""

    def setUp(self):
        self.block = deploy_block()

    def test_a1_incomplete_verdict_exists(self):
        self.assertIn("incomplete", self.block,
                      "第 6b 步必须有 incomplete 判决（有项没跑成时用，Spec §2.1）")

    def test_a2_per_check_status_list_exists(self):
        self.assertIn("checks", self.block, "必须输出逐项三态清单 checks（Spec §2.3）")
        for status in ("pass", "failed", "skipped"):
            self.assertIn(status, self.block,
                          "逐项状态闭集缺 %s（Spec §2.4）" % status)

    def test_a3_skipped_list_exists_at_top_level(self):
        self.assertIn("skipped", self.block, "顶层必须有 skipped 清单（Spec §2.4）")

    def test_a4_verdict_closure_is_explicit(self):
        found = [name for name in VERDICTS if name in self.block]
        self.assertEqual(sorted(found), sorted(VERDICTS),
                         "三态闭集不完整：%r（Spec §2.1）" % (found,))
        match = re.search(r"isolated_downstream_selfcheck", self.block)
        self.assertIsNotNone(match, "第 6b 步必须输出 isolated_downstream_selfcheck")


class TokenMustBeVerifiedThenRetried(unittest.TestCase):
    """B 组：取到令牌 != 能用；403 必须重取并把原因打出来。"""

    def setUp(self):
        self.block = deploy_block()

    def test_b1_token_is_verified_before_use(self):
        self.assertIn("快照", self.block, "取到令牌后必须先打一次知识库快照验证（Spec §3.5）")

    def test_b2_token_is_refetched_on_failure(self):
        has_retry = ("重试" in self.block) or ("重取" in self.block) \
            or bool(re.search(r"for\s+\w+\s+in\s+range\(\s*2\s*\)", self.block))
        self.assertTrue(has_retry, "令牌不可用时必须重新取一次（Spec §3.6）")

    def test_b3_skip_reason_carries_http_status_and_body(self):
        self.assertIn("internal_token_rejected", self.block,
                      "跳过原因必须结构化：internal_token_rejected（Spec §2.3）")
        self.assertRegex(self.block, r"HTTP\s*%?s?\s*\{?",
                         "跳过原因里必须带 HTTP 状态码（Spec §3.6）")


class SkipNeverLooksLikePass(unittest.TestCase):
    """C 组：有 skip 就不许打印"通过"、不许退出 0。"""

    def setUp(self):
        self.block = deploy_block()

    def test_c1_pass_banner_only_in_ok_branch(self):
        banner = self.block.index("自检通过")
        window = self.block[max(0, banner - 400):banner]
        self.assertTrue(("ok" in window) or ("verdict" in window),
                        "「自检通过」必须只出现在 verdict == ok 的分支里（Spec §2.2）")

    def test_c2_skip_does_not_exit_zero(self):
        self.assertRegex(self.block, r"(incomplete|skipped)[^\n]*fail",
                         "有 skip 时必须走非零退出（Spec §2.2）")


if __name__ == "__main__":
    unittest.main()
