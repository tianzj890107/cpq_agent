"""守卫：文档点名的路径必须真实存在，隔离自检的根目录口径必须写对。

Spec：`docs/specs/doc-path-and-root-consistency.md`
血缘：`docs/specs/spec-status-consistency-repo-wide.md`（同类"文档与事实不一致"的收口）；
`docs/specs/packaging-parts-downstream-acceptance.md` §6.1 / §12（该盯哪个根）。

现状缺口（2026-09-22 实测，不是推断）：
  · `docs/specs/*.md` + `DEPLOYMENT.md` + `README.md` + `AGENTS.md` 反引号点名的一类路径
    793 处、去重 335 个，其中 3 个不存在：2 个是 chat 系列"已被取代"的历史名（属实），
    1 个是真的写错了（`…_by_state_and_nonblocking_…`，该文件叫 `…_and_nonblocking_…`）；
  · `DEPLOYMENT.md` 第 6b 步的隔离断言长期只数 `tech_app/data/*/meta.json`，而运行目录是
    `tech_app/tech_data` → 恒等于 `0 → 0`，什么都没证明（脚本侧由
    `test_deploy_isolation_root_red.py` 守，本批补文档侧）。

纪律：
  · 只读：扫文档 + 读脚本 + `bash -n`（子进程）；不改任何文件、不起服务、不连库；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

SCAN_FILES = ["DEPLOYMENT.md", "README.md", "AGENTS.md"]
PATH_RE = re.compile(
    r"^(tests/[A-Za-z0-9_./-]+\.py"
    r"|scripts/[A-Za-z0-9_./-]+\.(?:sh|py)"
    r"|tech_app/tools/[A-Za-z0-9_./-]+\.py"
    r"|docs/specs/[A-Za-z0-9_./-]+\.md)$"
)
BACKTICK_RE = re.compile(r"`([^`\n]+?)`")
SPLIT_RE = re.compile(r"[\s,;()\[\]]+")

#: 唯一白名单：被"取代"的历史名（旧方案文件已删，Spec 正文逐字记录"取代：…"）。
#: 不许扩充来"修"一条本来能修的错路径 —— §1.1。
SUPERSEDE_WHITELIST = frozenset({
    "docs/specs/chat-white-bubble-and-expandable-run-progress.md",
    "tests/test_chat_white_bubble_and_expandable_run_progress_red.py",
})
#: 去重路径数的下限：只防"规则空转"（扫描器写坏了、路径全没扫到时报警），不做上限。
MIN_UNIQUE_PATHS = 300
#: 与 `docs/specs/packaging-parts-downstream-acceptance.md` §1.2 同口径的窗口。
SUPERSEDE_WINDOW = 300

DEPLOY_MD = ROOT / "DEPLOYMENT.md"
DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"
LAUNCHER = ROOT / "tech_app_launch.py"


def doc_files():
    files = [ROOT / name for name in SCAN_FILES]
    files += sorted((ROOT / "docs" / "specs").glob("*.md"))
    return [p for p in files if p.is_file()]


def referenced_paths(text: str):
    """反引号点名的一类路径（去重前，保留出现顺序）。"""
    out = []
    for m in BACKTICK_RE.finditer(text):
        for tok in SPLIT_RE.split(m.group(1).strip()):
            tok = tok.strip().strip("`'\"")
            if tok and PATH_RE.match(tok):
                out.append(tok)
    return out


def scan():
    """-> (all_refs, missing{token: sorted(files)})"""
    refs = []
    missing = {}
    for path in doc_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for tok in referenced_paths(text):
            refs.append((rel, tok))
            if tok in SUPERSEDE_WHITELIST:
                continue
            if not (ROOT / tok).exists():
                missing.setdefault(tok, set()).add(rel)
    return refs, {k: sorted(v) for k, v in missing.items()}


def step_text(script: str, start_marker: str, end_marker: str) -> str:
    start = script.find(start_marker)
    end = script.find(end_marker, start + 1)
    assert start >= 0 and end > start, f"找不到区间 {start_marker!r}..{end_marker!r}"
    return script[start:end]


def near_supersede(text: str, token: str) -> bool:
    for m in re.finditer(re.escape(token), text):
        lo = max(0, m.start() - SUPERSEDE_WINDOW)
        hi = min(len(text), m.end() + SUPERSEDE_WINDOW)
        if "取代" in text[lo:hi]:
            return True
    return False


class DocPathExists(unittest.TestCase):
    def test_a1_no_referenced_path_is_missing(self):
        refs, missing = scan()
        self.assertTrue(refs, "扫描器空转：一个路径都没扫到")
        self.assertEqual(
            missing, {},
            "文档点名的路径不存在（要么改成对的，要么补上文件）："
            + "; ".join(f"{t} <- {', '.join(v)}" for t, v in sorted(missing.items())),
        )

    def test_a2_scan_is_not_vacuous(self):
        refs, _ = scan()
        unique = {t for _, t in refs}
        self.assertGreaterEqual(
            len(unique), MIN_UNIQUE_PATHS,
            f"只扫到 {len(unique)} 个去重路径（<{MIN_UNIQUE_PATHS}）：扫描器可能写坏了",
        )

    def test_a3_whitelist_only_covers_documented_supersede_names(self):
        _refs, _missing = scan()
        for token in sorted(SUPERSEDE_WHITELIST):
            hit = False
            for path in doc_files():
                text = path.read_text(encoding="utf-8")
                if token in text and near_supersede(text, token):
                    hit = True
                    break
            self.assertTrue(
                hit, f"白名单条目 {token} 附近没有「取代」记录 —— 不许拿它挡真错路径"
            )
            self.assertFalse(
                (ROOT / token).exists(),
                f"{token} 现在真实存在了：白名单该删（假白名单会让守卫空转）",
            )


class DeploymentRootWording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DEPLOY_MD.read_text(encoding="utf-8")

    def _step6b(self):
        return step_text(self.text, "第 6b 步", "两步互补")

    def test_b1_step6b_names_both_roots(self):
        seg = self._step6b()
        self.assertIn("tech_app/tech_data", seg, "§1.2：必须点名运行目录")
        self.assertIn("tech_app/data", seg, "§1.2：必须点名历史/杂项目录")
        self.assertIn("meta.json", seg, "§1.2：断言对象是 meta.json")
        self.assertRegex(
            seg, r"证明不了什么|什么都没证明|等于没证明",
            "§1.2：必须显式写出「只盯后者证明不了什么」这类判断（否则读者以为数它就够了）",
        )

    def test_b2_no_single_root_glob_claim_left(self):
        self.assertNotIn(
            "tech_app/data/*", self.text,
            "§1.2：`tech_app/data/*` 这种只数历史目录的 glob 命令不许再出现",
        )

    def test_b3_sample_project_id_hint_points_at_live_root(self):
        idx = self.text.find("CPQ_PARTS_PROJECT_ID=<项目id>")
        self.assertGreater(idx, 0, "第 6 步的样本项目 id 提示不见了，Spec 要重核")
        lead = self.text[max(0, idx - 400):idx]
        self.assertIn("tech_app/tech_data", lead, "§1.2：项目 id 命令该指运行目录")


class DeployScriptRootContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = DEPLOY_SH.read_text(encoding="utf-8")
        cls.step = step_text(cls.script, 'step "6b.', 'step "7. 结论"')

    def test_c1_resolution_chain_matches_launcher_default(self):
        self.assertIn(
            'LIVE_DATA_DIR="${DATA_DIR:-${CPQ_DATA_DIR:-$REPO/tech_app/tech_data}}"',
            self.step, "§1.3：解析链必须 DATA_DIR → CPQ_DATA_DIR → <repo>/tech_app/tech_data",
        )
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('"tech_data"', launcher, "启动器缺省根不再是 tech_data：Spec §1.3 要重核")

    def test_c2_both_roots_are_counted_and_compared(self):
        self.assertIn('META_BEFORE="$(count_projects "$LIVE_DATA_DIR")"', self.step)
        self.assertIn('META_INCIDENTAL_BEFORE="$(count_projects "$REPO/tech_app/data")"', self.step)
        self.assertIn("[ \"$META_BEFORE\" = \"$META_AFTER\" ]", self.step)
        self.assertIn("[ \"$META_INCIDENTAL_BEFORE\" = \"$META_INCIDENTAL_AFTER\" ]", self.step)

    def test_c3_zero_project_warning_is_explicit(self):
        self.assertIn("证明不了什么", self.step, "§1.3：0 个项目时要显式说明断言没意义")

    def test_c4_script_parses(self):
        bash = subprocess.run(
            ["bash", "-n", str(DEPLOY_SH)], capture_output=True, text=True
        )
        self.assertEqual(bash.returncode, 0, f"bash -n 失败：{bash.stderr}")


if __name__ == "__main__":
    unittest.main()
