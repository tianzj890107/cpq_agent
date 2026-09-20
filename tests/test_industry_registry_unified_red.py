"""红测：全局行业口径统一（唯一行业注册表 + 新增包装行业）—— 包装第 1 批。

用户口径（原话要点）：「现在三个行业是半导体/电池/电器；加入包装后应统一成为四个行业」，
且行业不能只加在下拉框里，必须「报价和技术工艺共同使用同一个行业注册表」。

Spec：docs/specs/global-industry-registry-and-packaging.md

现状缺口（实测，不是推断）：
  · industry_templates.INDUSTRIES = ('semiconductor','battery','appliance')，没有 packaging；
  · da_repo._normalized_industry('packaging') -> 'semiconductor'（静默改行业）；
  · da_schema.sql 的 CHECK 不接受 'packaging'；
  · cpq_wf._ddl_pg('cpq_wf') 里 cpq_wf_card 完全没有 industry 列，报价侧无行业概念；
  · 报价首页.html / cpq-industry.js / tech-task.js / requirement-create.js 各自硬编码三项；
  · tests/ 下现在没有任何一个测试引用 industry。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import inspect
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUOTE_HOME = ROOT / "\u62a5\u4ef7\u9996\u9875.html"
SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"
REGISTRY_PY = ROOT / "cpq_industries.py"
TECH_TASK_JS = ROOT / "tech_app" / "frontend" / "tech-task.js"
CPQ_INDUSTRY_JS = ROOT / "tech_app" / "frontend" / "cpq-industry.js"
REQ_CREATE_JS = ROOT / "tech_app" / "frontend" / "requirement-create.js"

SELECTABLE = ("semiconductor", "battery", "appliance", "packaging")
LEGACY = ("flexible",)
PROFILE_FIELDS = ("key", "label", "enabled", "quote_template", "process_template",
                  "knowledge_scope", "cost_profile", "pricing_profile")

CHILD_PROBE = r'''
import json, sys
sys.path.insert(0, __ROOT__)
import cpq_industries as reg
reg.INDUSTRY_KEYS = tuple(list(reg.INDUSTRY_KEYS) + ["probe_x"])
reg.INDUSTRIES = dict(reg.INDUSTRIES)
reg.INDUSTRIES["probe_x"] = dict(reg.INDUSTRIES["packaging"], key="probe_x", label="Probe")
out = {}
try:
    import tech_app.backend.services.industry_templates as it
    out["industry_templates"] = list(it.INDUSTRIES)
except Exception as exc:
    out["industry_templates_error"] = "%s: %s" % (type(exc).__name__, exc)
try:
    import tech_app.backend.storage.da_repo as repo
    out["da_repo"] = list(repo.INDUSTRY_KEYS)
except Exception as exc:
    out["da_repo_error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out))
'''


def load_registry():
    """注册表模块；不存在时返回 None（用例给明确断言，不抛 ImportError）。"""
    try:
        return importlib.import_module("cpq_industries")
    except Exception:
        return None


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def tech_templates():
    return importlib.import_module("tech_app.backend.services.industry_templates")


def da_repo():
    return importlib.import_module("tech_app.backend.storage.da_repo")


def option_values(html: str, select_id: str = "techIndustry"):
    m = re.search(r'<select[^>]*id="%s"[^>]*>(.*?)</select>' % re.escape(select_id),
                  html, re.S)
    if not m:
        return None
    return re.findall(r'<option value="([^"]+)"', m.group(1))


def js_string_array(src: str, name: str):
    """抓 `const NAME = ['a','b']` / `= [['a','x'],...]` 里的行业键。"""
    m = re.search(re.escape(name) + r"\s*=\s*\[(.*?)\]", src, re.S)
    if not m:
        return None
    body = m.group(1)
    pairs = re.findall(r"\[\s*'([a-z_]+)'\s*,", body)
    if pairs:
        return pairs
    flat = re.findall(r"'([a-z_]+)'", body)
    return flat or None


def run_probe() -> dict:
    code = CHILD_PROBE.replace("__ROOT__", repr(str(ROOT)))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError("子进程探针失败：%s" % (proc.stderr or "")[-800:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def src_requirement_ddl() -> str:
    sql = read(SCHEMA_SQL)
    m = re.search(r"CREATE TABLE IF NOT EXISTS\s+src_requirement\s*\(.*?\n\)\s*;", sql, re.S)
    if not m:
        raise AssertionError("da_schema.sql 里找不到 src_requirement 建表语句")
    return m.group(0)


def insert_requirement_industry(value):
    """在临时 sqlite 里真跑 src_requirement 的 DDL，插一行，看 CHECK 收不收。"""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(src_requirement_ddl())
        conn.execute(
            "INSERT INTO src_requirement (requirement_no, project_id, industry)"
            " VALUES ('REQ-1', 'P-1', ?)", (value,))
        conn.commit()
        return True
    finally:
        conn.close()


class Harness(unittest.TestCase):
    def setUp(self):
        self.reg = load_registry()

    def registry(self):
        self.assertIsNotNone(
            self.reg,
            "缺少唯一行业注册表 cpq_industries.py（Spec 4.1）")
        return self.reg


class ARegistry(Harness):
    def test_a1_registry_lists_the_four_selectable_industries(self):
        reg = self.registry()
        self.assertEqual(SELECTABLE, tuple(reg.INDUSTRY_KEYS),
                         "注册表的可选行业应为 %r（顺序即展示顺序）" % (SELECTABLE,))
        self.assertEqual(SELECTABLE, tuple(reg.industry_keys()))
        self.assertEqual(SELECTABLE, tuple(item["key"] for item in reg.all_industries()),
                         "all_industries() 必须按 INDUSTRY_KEYS 顺序返回四个行业")

    def test_a2_each_industry_carries_the_full_profile(self):
        reg = self.registry()
        for key, item in reg.INDUSTRIES.items():
            with self.subTest(industry=key):
                for field in PROFILE_FIELDS:
                    self.assertIn(field, item, "%s 缺少字段 %r" % (key, field))
                self.assertTrue(str(item["label"]).strip(), "%s 没有中文标签" % key)

    def test_a3_packaging_reserves_cost_and_pricing_profiles(self):
        reg = self.registry()
        self.assertTrue(reg.is_supported("packaging"), "包装必须是可选行业")
        pkg = reg.profile_of("packaging")
        self.assertEqual("packaging", pkg["key"])
        self.assertEqual("包装", pkg["label"])
        self.assertTrue(pkg["enabled"])
        self.assertEqual("packaging_v1", pkg["cost_profile"],
                         "包装必须预留 cost_profile=packaging_v1（第 7 批实现）")
        self.assertEqual("packaging_margin_v1", pkg["pricing_profile"],
                         "包装必须预留 pricing_profile=packaging_margin_v1（第 8 批实现）")

    def test_a4_normalize_folds_unknown_and_blank_to_the_default(self):
        reg = self.registry()
        for raw in (None, "", "   ", "unknown"):
            with self.subTest(raw=raw):
                got = reg.normalize(raw)
                self.assertIn(got, SELECTABLE,
                              "%r 归一化后必须是四个可选行业之一，实际 %r" % (raw, got))
        self.assertEqual("battery", reg.normalize("Battery"))
        self.assertEqual("packaging", reg.normalize(" PACKAGING "))
        self.assertEqual(reg.DEFAULT_INDUSTRY, reg.normalize("不存在"))
        self.assertEqual("semiconductor", reg.DEFAULT_INDUSTRY,
                         "默认行业必须保持半导体（既有三行业口径不变）")

    def test_a5_legacy_flexible_is_readable_but_not_selectable(self):
        reg = self.registry()
        self.assertTrue(reg.is_legacy("flexible"), "flexible 必须被认成历史键")
        self.assertFalse(reg.is_supported("flexible"),
                         "flexible 已下线，不得出现在可选项里")
        self.assertTrue(reg.is_known("flexible"), "历史键必须仍然可读")
        self.assertEqual("flexible", reg.normalize("flexible"),
                         "历史草稿的 flexible 必须原样保留，不能静默改成半导体")


class BConsumersDeriveFromRegistry(Harness):
    def test_b1_tech_templates_industry_tuple_matches_the_registry(self):
        reg = self.registry()
        it = tech_templates()
        self.assertEqual(tuple(reg.industry_keys()), tuple(it.INDUSTRIES),
                         "industry_templates.INDUSTRIES 必须与注册表一致（四个行业）")
        self.assertEqual(reg.DEFAULT_INDUSTRY, it.DEFAULT_INDUSTRY)
        self.assertEqual("包装", it.INDUSTRY_LABELS.get("packaging"),
                         "industry_templates 必须给出包装的中文标签")

    def test_b2_tech_templates_builds_packaging_without_crashing(self):
        it = tech_templates()
        blocks = it.blocks("packaging")
        self.assertTrue(len(blocks) >= 1,
                        "packaging 至少要有一个占位块，否则 section_checks 会 IndexError")
        keys = it.field_keys("packaging")
        self.assertTrue(keys, "packaging 的字段键不能为空（第 2 批补齐完整字段）")
        self.assertEqual("packaging", it.normalize("packaging"),
                         "industry_templates.normalize 不能把 packaging 落回半导体")
        self.assertEqual("包装", it.label("packaging"))
        it.section_checks("packaging")

    def test_b3_consumers_follow_the_registry_single_source(self):
        self.registry()
        out = run_probe()
        self.assertNotIn("industry_templates_error", out,
                         "industry_templates 导入失败：%s" % out.get("industry_templates_error"))
        self.assertIn("probe_x", out.get("industry_templates") or [],
                      "industry_templates 没有从注册表派生（探针行业没被看到）——"
                      "说明它还在自己硬编码行业清单")
        self.assertNotIn("da_repo_error", out,
                         "da_repo 导入失败：%s" % out.get("da_repo_error"))
        self.assertIn("probe_x", out.get("da_repo") or [],
                      "da_repo.INDUSTRY_KEYS 没有从注册表派生（探针行业没被看到）")

    def test_b4_requirement_extract_accepts_packaging(self):
        """真跑抽取入口：人工选的包装行业必须被认成受支持行业，不能被当成非法值丢掉。"""
        extract = importlib.import_module("tech_app.backend.services.requirement_extract")
        captured = {}
        original = extract.llm_client.complete_to_model

        def fake(system_prompt, user_text, model, **kwargs):
            captured["system"] = system_prompt
            return extract.RequirementDocumentExtraction()

        extract.llm_client.complete_to_model = fake
        try:
            prepared = extract.PreparedDocuments(
                text="纸盒：长200 宽150 高80，面纸128g，需要烫金",
                processed_files=[], skipped_files=[])
            extract.extract_requirement_fields(prepared, "packaging")
            self.assertIn("用户已人工指定行业模板为 packaging",
                          captured.get("system") or "",
                          "AI 抽取不认 packaging：人工在首页选的包装被当成非法值丢掉")
            extract.extract_requirement_fields(prepared, "battery")
            self.assertIn("用户已人工指定行业模板为 battery",
                          captured.get("system") or "",
                          "三行业既有行为被破坏：电池的人工指定丢了")
        finally:
            extract.llm_client.complete_to_model = original


class CStorageAcceptsPackaging(Harness):
    def test_c1_da_repo_normalizes_packaging(self):
        self.registry()
        repo = da_repo()
        self.assertEqual("packaging", repo._normalized_industry("packaging"),
                         "da_repo 把 packaging 静默改成了别的行业")
        self.assertEqual("packaging", repo._normalized_industry("PACKAGING "),
                         "大小写/空白必须归一化")

    def test_c2_da_repo_industry_keys_match_the_registry(self):
        reg = self.registry()
        repo = da_repo()
        expected = set(reg.industry_keys()) | set(LEGACY)
        self.assertEqual(expected, set(repo.INDUSTRY_KEYS),
                         "da_repo.INDUSTRY_KEYS 必须等于注册表可选行业 + 历史键")

    def test_c3_requirement_check_constraint_accepts_packaging(self):
        try:
            ok = insert_requirement_industry("packaging")
        except Exception as exc:
            self.fail("src_requirement 的 CHECK 不接受 packaging（%s）——"
                      "行业约束必须把 packaging 列进白名单" % exc)
        self.assertTrue(ok, "src_requirement 的 CHECK 必须接受 packaging")
        for bad in ("packaging_v2", "unknown"):
            with self.subTest(bad=bad):
                with self.assertRaises(Exception,
                                       msg="非法行业 %r 必须被 CHECK 拒绝" % bad):
                    insert_requirement_industry(bad)

    def test_c4_legacy_values_still_normalize(self):
        self.registry()
        repo = da_repo()
        self.assertEqual("flexible", repo._normalized_industry("flexible"),
                         "历史 flexible 草稿必须还能读")
        for raw in (None, "", "  ", "weird_industry"):
            with self.subTest(raw=raw):
                self.assertEqual("semiconductor", repo._normalized_industry(raw),
                                 "%r 必须落默认行业且不抛错" % (raw,))


class DQuoteCardCarriesIndustry(Harness):
    def test_d1_card_migration_adds_an_industry_column(self):
        cpq_wf = importlib.import_module("cpq_wf")
        self.assertTrue(callable(getattr(cpq_wf, "_ddl_pg", None)),
                        "cpq_wf._ddl_pg 不存在，无法验证卡片迁移")
        statements = cpq_wf._ddl_pg("cpq_wf")
        card_ddl = [s for s in statements if "cpq_wf_card" in s and "industry" in s]
        self.assertTrue(card_ddl, "cpq_wf_card 的建表/迁移语句里没有 industry 列")
        self.assertTrue(
            any(re.search(r"ADD COLUMN IF NOT EXISTS\s+industry", s, re.I)
                for s in card_ddl),
            "老库升级必须有一条幂等的 ALTER TABLE ... cpq_wf_card"
            " ADD COLUMN IF NOT EXISTS industry（照 business_case_id 的写法）")

    def _fake(self):
        harness = importlib.import_module(
            "tests.test_quote_task_coexistence_and_atomic_claim_red")
        return harness.FakeDB(), harness._FakePatch

    def test_d2_sync_card_persists_the_industry(self):
        cpq_wf = importlib.import_module("cpq_wf")
        sig = inspect.signature(cpq_wf.sync_card)
        self.assertIn("industry", sig.parameters,
                      "cpq_wf.sync_card 必须接受 industry 参数（报价会话携带行业）")
        db, patch = self._fake()
        with patch(db):
            created = cpq_wf.sync_card("sess-p1-0001", {"user_id": 1},
                                       title="包装报价", industry="packaging")
            self.assertEqual("packaging", created.get("industry"),
                             "建卡时没有把 industry 写进 cpq_wf_card")
            again = cpq_wf.get_card("sess-p1-0001")
            self.assertEqual("packaging", (again or {}).get("industry"),
                             "get_card 没有把 industry 读回来")

    def test_d3_unknown_industry_falls_back_to_the_default(self):
        cpq_wf = importlib.import_module("cpq_wf")
        sig = inspect.signature(cpq_wf.sync_card)
        self.assertIn("industry", sig.parameters,
                      "cpq_wf.sync_card 必须接受 industry 参数（报价会话携带行业）")
        db, patch = self._fake()
        with patch(db):
            created = cpq_wf.sync_card("sess-p1-0002", {"user_id": 1},
                                       title="非法行业", industry="not_a_industry")
            self.assertEqual("semiconductor", created.get("industry"),
                             "未知行业必须落默认行业，不能原样写进卡片")

    def test_d4_legacy_card_without_industry_still_reads(self):
        cpq_wf = importlib.import_module("cpq_wf")
        db, patch = self._fake()
        with patch(db):
            created = cpq_wf.sync_card("sess-p1-0003", {"user_id": 1}, title="老卡片")
            self.assertIn("industry", created,
                          "卡片读回里必须有 industry 字段（老卡片可以为空）")
            self.assertEqual("", created.get("industry") or "",
                             "老卡片读回必须是空字符串，调用方按默认行业处理")


class EFrontendIndustrySelection(Harness):
    def test_e1_quote_home_shows_the_industry_select_in_quote_mode(self):
        html = read(QUOTE_HOME)
        m = re.search(r'<div class="upload-section" id="techIndustryBox"[^>]*>', html)
        self.assertIsNotNone(m, "报价首页找不到 #techIndustryBox")
        self.assertNotIn("display:none", m.group(0).replace(" ", ""),
                         "行业下拉不能默认隐藏：报价模式也要能选行业")
        mm = re.search(r"function applyUploadMode\(mode\)\s*\{(.*?)\n    \}", html, re.S)
        self.assertIsNotNone(mm, "报价首页找不到 applyUploadMode")
        self.assertNotIn("techIndustryBox", mm.group(1),
                         "applyUploadMode 仍按模式隐藏行业下拉（tech 专属），"
                         "报价模式选不到行业")

    def test_e2_quote_home_options_match_the_registry(self):
        reg = self.registry()
        html = read(QUOTE_HOME)
        values = option_values(html)
        self.assertIsNotNone(values, "报价首页找不到 #techIndustry 的 option")
        self.assertEqual(list(reg.industry_keys()), list(dict.fromkeys(values)),
                         "报价首页的行业选项必须与注册表一致（含 packaging）")
        self.assertNotIn("const TECH_INDUSTRIES = ['semiconductor', 'battery', 'appliance']",
                         html,
                         "报价首页仍然硬编码旧的行业三元组")

    def test_e3_tech_pages_options_match_the_registry(self):
        reg = self.registry()
        expected = set(reg.industry_keys())
        for path, name in ((CPQ_INDUSTRY_JS, "OK"), (TECH_TASK_JS, "TT_INDUSTRIES")):
            src = read(path)
            with self.subTest(file=path.name):
                keys = js_string_array(src, name)
                self.assertIsNotNone(keys, "%s 找不到 %s" % (path.name, name))
                self.assertEqual(expected, set(keys),
                                 "%s 的行业集合必须与注册表一致" % path.name)
        req = read(REQ_CREATE_JS)
        self.assertIn('value="packaging"', req,
                      "requirement-create.js 的行业下拉缺少包装选项")
        self.assertIn("包装", req, "requirement-create.js 缺少包装标签")


if __name__ == "__main__":
    unittest.main()
