"""红测：部署版本身份（health 暴露 build commit + 部署脚本落 stamp + 与 HEAD 对账）。

Spec：`docs/specs/deploy-build-identity.md`
依赖：`tech_app/backend/main.py` 的 `/api/health`、`scripts/deploy_34_bare.sh`、`DEPLOYMENT.md`。

现状缺口（2026-09-21 实测，不是推断）：
  · `/api/health` 只回 `status` / 模型 / `cad_converter` / `auth_enabled` / `sso_enabled`，
    **没有任何版本字段**；
  · `tech_app/backend/services/` 下没有 `build_identity.py`，仓库里没有 `cpq_build.json` 之类的
    stamp 机制；
  · `scripts/deploy_34_bare.sh` 只在结论里打印一行 `7312eca → 5ec90a6` 文本，重启后无从查询；
  · 结果：验收期间判断「34 跑的是哪个 commit」只能靠功能差异反推，或再部署一次对齐 HEAD。

纪律：
  · 不启动服务、不连 PG、不发 HTTP；
  · stamp 一律临时目录 fixture（`tempfile`），只有「回退 git」一组用真实仓库根（只读）；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"
DEPLOY_MD = ROOT / "DEPLOYMENT.md"

EXPECTED_BUILD_KEYS = ("commit", "branch", "ref", "deployed_at", "source")


def load_build_identity():
    try:
        from tech_app.backend.services import build_identity     # noqa: PLC0415
    except ImportError:                                          # pragma: no cover
        return None
    return build_identity


class Base(unittest.TestCase):
    def module(self):
        module = load_build_identity()
        if module is None:
            self.fail("tech_app/backend/services/build_identity.py 不存在（Spec §2.1）")
        return module

    def build_info(self, **kw):
        module = self.module()
        fn = getattr(module, "build_info", None)
        if not callable(fn):
            self.fail("build_identity.build_info() 缺失（Spec §2.1）")
        return fn(**kw)

    def write_stamp(self, payload, directory=None):
        directory = pathlib.Path(directory or tempfile.mkdtemp(prefix="cpq-build-"))
        path = directory / "cpq_build.json"
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def git_head(self, repo=ROOT):
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        return proc.stdout.strip()

    def deploy_source(self):
        self.assertTrue(DEPLOY_SH.exists(), "scripts/deploy_34_bare.sh 不存在")
        return DEPLOY_SH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANaming(Base):
    def test_a1_constants(self):
        module = self.module()
        self.assertEqual("CPQ_BUILD_STAMP", getattr(module, "STAMP_ENV", None))
        self.assertEqual("cpq_build.json", getattr(module, "STAMP_FILENAME", None))
        self.assertEqual(EXPECTED_BUILD_KEYS, tuple(getattr(module, "BUILD_KEYS", ())))
        self.assertEqual("unknown", getattr(module, "UNKNOWN", None))

    def test_a2_stamp_path_env_wins_and_default_sits_outside_repo(self):
        module = self.module()
        fn = getattr(module, "stamp_path", None)
        if not callable(fn):
            self.fail("build_identity.stamp_path() 缺失（Spec §2.1）")
        custom = "/tmp/cpq-custom-build.json"
        self.assertEqual(pathlib.Path(custom),
                         pathlib.Path(fn(env={module.STAMP_ENV: custom}, repo_root=ROOT)))
        default = pathlib.Path(fn(env={}, repo_root=ROOT))
        self.assertEqual(module.STAMP_FILENAME, default.name)
        self.assertNotEqual(ROOT, default.parent,
                            "缺省 stamp 必须落在部署目录之外，别脏了工作区（Spec §2.1）")


# --------------------------------------------------------------------------- #
# B 组：build_info 行为
# --------------------------------------------------------------------------- #
class TestBBuildInfo(Base):
    def test_b1_reads_stamp(self):
        path = self.write_stamp({"commit": "abc1234", "branch": "ytbz", "ref": "ytbz",
                                 "deployed_at": "2026-09-21T23:00:00+08:00"})
        out = self.build_info(env={self.module().STAMP_ENV: str(path)}, repo_root=ROOT)
        self.assertEqual("abc1234", out.get("commit"))
        self.assertEqual("ytbz", out.get("branch"))
        self.assertEqual("ytbz", out.get("ref"))
        self.assertEqual("2026-09-21T23:00:00+08:00", out.get("deployed_at"))
        self.assertEqual("stamp", out.get("source"))

    def test_b2_missing_stamp_falls_back_to_git(self):
        out = self.build_info(env={self.module().STAMP_ENV: "/nonexistent/cpq_build.json"},
                              repo_root=ROOT)
        self.assertEqual("git", out.get("source"),
                         "stamp 不在时必须回退 git rev-parse（Spec §2.1 第 2 条）")
        self.assertEqual(self.git_head(), out.get("commit"))

    def test_b3_broken_json_does_not_raise(self):
        path = self.write_stamp("{not json at all")
        out = self.build_info(env={self.module().STAMP_ENV: str(path)}, repo_root=ROOT)
        self.assertEqual("git", out.get("source"), "坏 stamp 必须静默回退，不许抛")

    def test_b4_stamp_without_commit_falls_back(self):
        for payload in ({"branch": "ytbz"}, {"commit": ""}, {"commit": "   "}, ["not", "object"]):
            path = self.write_stamp(payload)
            out = self.build_info(env={self.module().STAMP_ENV: str(path)}, repo_root=ROOT)
            self.assertEqual("git", out.get("source"),
                             "stamp 缺 commit / commit 为空必须回退：%r" % (payload,))

    def test_b5_env_path_wins_over_default_location(self):
        directory = pathlib.Path(tempfile.mkdtemp(prefix="cpq-build-default-"))
        (directory / self.module().STAMP_FILENAME).write_text(
            json.dumps({"commit": "default000", "branch": "b", "ref": "r",
                        "deployed_at": "t"}), encoding="utf-8")
        # 缺省位置在 repo_root 的父目录：用 repo_root=<dir>/sub 指向它
        sub = directory / "sub"
        sub.mkdir()
        ignored = self.build_info(env={}, repo_root=sub)
        self.assertEqual("default000", ignored.get("commit"))

        explicit = self.write_stamp({"commit": "explicit1", "branch": "b", "ref": "r",
                                     "deployed_at": "t"}, directory=tempfile.mkdtemp(
                                         prefix="cpq-build-explicit-"))
        wins = self.build_info(env={self.module().STAMP_ENV: str(explicit)}, repo_root=sub)
        self.assertEqual("explicit1", wins.get("commit"), "env 指到哪儿就读哪儿")

    def test_b6_all_unavailable_is_unknown_and_never_raises(self):
        empty = pathlib.Path(tempfile.mkdtemp(prefix="cpq-no-git-"))
        out = self.build_info(env={self.module().STAMP_ENV: str(empty / "missing.json")},
                              repo_root=empty)
        self.assertEqual(self.module().UNKNOWN, out.get("source"),
                         "stamp 与 git 都拿不到必须回 source=unknown（Spec §2.1 第 3 条）")
        self.assertEqual("", out.get("commit"))

    def test_b7_key_set_is_stable_and_all_strings(self):
        path = self.write_stamp({"commit": "abc1234", "extra": "x"})
        out = self.build_info(env={self.module().STAMP_ENV: str(path)}, repo_root=ROOT)
        self.assertEqual(EXPECTED_BUILD_KEYS, tuple(out.keys()),
                         "键集必须恒等于 BUILD_KEYS（顺序也固定）")
        for key, value in out.items():
            self.assertIsInstance(value, str, "%s 必须是字符串" % key)
        self.assertNotIn("extra", out, "stamp 里的额外键不许漏进出参")


# --------------------------------------------------------------------------- #
# C 组：/api/health 暴露 build 段
# --------------------------------------------------------------------------- #
class TestCHealth(Base):
    def test_c1_health_returns_build_section(self):
        self.assertTrue(MAIN_PY.exists(), "tech_app/backend/main.py 不存在")
        src = MAIN_PY.read_text(encoding="utf-8")
        self.assertIn('"build"', src, "/api/health 必须回 build 段（Spec §2.2）")
        self.assertTrue("build_identity" in src or "build_info" in src,
                        "health 必须调用 build_identity.build_info()，不许自己拼 commit")

    def test_c2_health_keeps_existing_keys(self):
        src = MAIN_PY.read_text(encoding="utf-8")
        start = src.index('@app.get("/api/health")')
        window = src[start:start + 3000]
        for key in ('"status": "ok"', '"cad_converter"', '"auth_enabled"'):
            self.assertIn(key, window, "既有 health 字段不许动：%s" % key)
        self.assertIn("build", window)


# --------------------------------------------------------------------------- #
# D 组：部署脚本落 stamp 并自检
# --------------------------------------------------------------------------- #
class TestDDeployScript(Base):
    def test_d1_script_writes_stamp(self):
        src = self.deploy_source()
        self.assertIn("CPQ_BUILD_STAMP", src, "脚本必须支持/注入 CPQ_BUILD_STAMP（Spec §2.3）")
        self.assertIn("cpq_build.json", src, "脚本必须写缺省 stamp 文件名")
        for key in ("commit", "branch", "deployed_at"):
            self.assertIn(key, src, "stamp 必须含 %s" % key)

    def test_d2_script_compares_stamp_commit_with_head(self):
        src = self.deploy_source()
        for token in ("BUILD_COMMIT", "HEAD_COMMIT"):
            self.assertIn(token, src, "脚本必须按 Spec §2.3 用变量 %s 保存并比对" % token)
        compare_at = max(src.find("BUILD_COMMIT"), src.find("HEAD_COMMIT"))
        tail = src[compare_at:]
        self.assertIn("fail", tail, "stamp 的 commit 与 HEAD 不一致必须非零退出")

    def test_d3_conclusion_prints_build_commit(self):
        src = self.deploy_source()
        start = src.find("7. 结论")
        self.assertNotEqual(-1, start, "脚本缺结论段")
        window = src[start:]
        self.assertTrue("commit" in window.lower(),
                        "结论行必须打印本次部署的 build commit（Spec §2.3）")


# --------------------------------------------------------------------------- #
# E 组：部署文档
# --------------------------------------------------------------------------- #
class TestEDeployment(Base):
    def test_e1_documented(self):
        self.assertTrue(DEPLOY_MD.exists(), "DEPLOYMENT.md 不存在")
        text = DEPLOY_MD.read_text(encoding="utf-8")
        for token in ("CPQ_BUILD_STAMP", "cpq_build.json"):
            self.assertIn(token, text, "DEPLOYMENT.md 必须登记 %s（Spec §2.4）" % token)
        self.assertIn("build", text, "文档必须写清 build 段怎么读")
        self.assertIn("rev-parse", text, "文档必须给出与 HEAD 对账的命令")


if __name__ == "__main__":                                         # pragma: no cover
    unittest.main()
