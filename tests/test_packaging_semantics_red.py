"""红测：包装图纸语义、刀线压痕线与需求字段证据 —— DWG 支持第 4 批。

Spec：`docs/specs/packaging-drawing-semantics.md`
夹具：`tests/fixtures/cad_ir/*.json`（15 份**冻结的 CAD IR 文档**，由同目录 build_fixtures.py 生成）
依赖：第 1 批（`file_preflight`）、第 3 批（`cad_ir`）。

现状缺口（实测，不是推断）：
  · `tech_app/backend/services/packaging_semantics/` 不存在；
  · 全仓没有任何「刀线 / 压痕线 / 半切 / 开槽 / 糊口 / 出血 / 拼版」判定代码；
  · 没有 `tech_app/agent_knowledge/rules/packaging_layer_rules.json`，颜色与线型的含义无处配置；
  · 需求侧只有 `field_sources`（5 个值的闭集），**没有** `field_provenance`
    （origin / status / confidence / evidence_refs / conflicts），也没有「未确认不写值」的约束；
  · `packaging_match` 只会读人工填好的需求字段：图纸事实到字段候选这一段完全缺失。

四组不许松动的口径（细则见 Spec §5、§6）：
  1. 未确认不写值 —— `status != "confirmed"` 的字段绝不写进 `requirement.data`；
  2. 单位未确认 → 绝对尺寸永不 confirmed；
  3. 模型只做辅助 —— 不许覆盖 CAD 值、不许写越权字段、不许把预览当尺寸；
  4. 文件名不是证据 —— 「酒盒」「圆盘盒」不得成为盒型或结构参数的依据。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import base64
import copy
import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures" / "cad_ir"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "real_baselines"
PKG = "tech_app.backend.services.packaging_semantics"
PKG_DIR = ROOT / "tech_app" / "backend" / "services" / "packaging_semantics"
PREFLIGHT = "tech_app.backend.services.file_preflight"
REQUIREMENT_SERVICE = "tech_app.backend.services.requirement_service"
DA_REPO = "tech_app.backend.storage.da_repo"
SHIPPED_RULES = ROOT / "tech_app" / "agent_knowledge" / "rules" / "packaging_layer_rules.json"

SEMANTICS_VERSION = "packaging-semantics/1"
ORIGINS = {"confirmed_from_cad", "inferred_from_geometry", "inferred_from_text",
           "inferred_by_model", "user_confirmed", "conflict", "missing"}
STATUSES = {"confirmed", "needs_confirmation", "conflict", "missing"}
ROLES = {"cut", "crease", "half_cut", "v_groove", "glue_flap", "print", "bleed", "frame",
         "hole", "unknown"}
EVIDENCE_LEVELS = {"STRONG", "MODERATE", "WEAK", "CONTRADICTORY", "NONE"}
BOX_TYPES = {"telescope_lid_base", "drawer", "book_style", "folding_carton", "round_tube",
             "irregular", "unknown"}
#: Spec §9 —— 本批新增的 2 条码（HTTP, retryable）。
NEW_ERROR_CODES = {"PACKAGING_SEMANTICS_SOURCE_MISSING": (422, True),
                   "PACKAGING_LAYER_RULES_INVALID": (500, False)}
REQUIRED_KEYS = {"semantics_version", "semantics_id", "semantics_hash", "source", "layers",
                 "roles_summary", "outline", "dimensions", "texts", "box_candidates", "fields",
                 "unresolved", "model_assist", "warnings", "stats", "reviewable"}
STATS_KEYS = {"layer_total", "cut_layer_total", "crease_layer_total", "boundary_candidate_total",
              "hole_total", "conflict_total", "unresolved_total", "box_candidate_total"}
#: Spec §6.1 —— 允许模型输出的顶层键闭集；其余一律算越权。
MODEL_KEYS = {"title_block", "material_candidates", "process_candidates", "box_candidates",
              "layer_interpretations", "open_questions"}

PNG_BYTES = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64)
RASTER_PREVIEW = {"bytes": PNG_BYTES, "media_type": "image/png"}
SVG_PREVIEW = {"bytes": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
               "media_type": "image/svg+xml"}

#: 默认模板的 9 条内置规则（与 Spec §4 的样例同形，测试自带一份，不依赖仓库文件）。
DEFAULT_LAYER_RULES = [
    {"rule_id": "cut_name_v1", "role": "cut", "evidence_level": "STRONG", "confidence": 0.9,
     "match": {"names": ["CUT", "DIE", "刀线", "模切线"], "name_prefix": ["CUT", "DIE"]}},
    {"rule_id": "crease_name_v1", "role": "crease", "evidence_level": "STRONG", "confidence": 0.9,
     "match": {"names": ["CREASE", "FOLD", "压痕", "折痕"]}},
    {"rule_id": "half_cut_name_v1", "role": "half_cut", "evidence_level": "MODERATE",
     "confidence": 0.7, "match": {"names": ["HALFCUT", "HALF_CUT", "半切"]}},
    {"rule_id": "v_groove_name_v1", "role": "v_groove", "evidence_level": "MODERATE",
     "confidence": 0.7, "match": {"names": ["VGROOVE", "V_GROOVE", "V槽"]}},
    {"rule_id": "glue_flap_name_v1", "role": "glue_flap", "evidence_level": "MODERATE",
     "confidence": 0.6, "match": {"names": ["GLUE", "糊口", "粘口"]}},
    {"rule_id": "print_name_v1", "role": "print", "evidence_level": "MODERATE", "confidence": 0.6,
     "match": {"names": ["PRINT", "印刷"]}},
    {"rule_id": "bleed_name_v1", "role": "bleed", "evidence_level": "MODERATE", "confidence": 0.6,
     "match": {"names": ["BLEED", "出血"]}},
    {"rule_id": "frame_name_v1", "role": "frame", "evidence_level": "STRONG", "confidence": 0.8,
     "match": {"names": ["FRAME", "BORDER", "图框", "标题栏"]}},
    {"rule_id": "hole_name_v1", "role": "hole", "evidence_level": "MODERATE", "confidence": 0.6,
     "match": {"names": ["HOLE", "孔位"]}},
]


def rule_set(template="generic", *, layers=None, colors=None, line_types=None):
    return {"rule_set": "packaging_layer_rules_v1", "review_status": "reviewed",
            "default_template": template,
            "templates": {template: {"layers": copy.deepcopy(layers or DEFAULT_LAYER_RULES),
                                     "colors": dict(colors or {}),
                                     "line_types": dict(line_types or {})}}}


def packaging_field_keys():
    from tech_app.backend.services import industry_templates
    keys = set()
    for block in industry_templates.PACKAGING_SPEC:
        for spec_field in block.fields:
            keys.add(spec_field.key)
    return keys


class _StopCall(Exception):
    """哨兵：本批不许发生的调用（模型 / 网络）真的发生了。"""


class SemanticsCase(unittest.TestCase):
    maxDiff = None

    # ---------------------------------------------------------------- 加载
    def module(self):
        try:
            return importlib.import_module(PKG)
        except ModuleNotFoundError as exc:
            name = str(getattr(exc, "name", "") or "")
            if name.endswith("file_preflight"):
                self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现）")
            self.fail("缺少 tech_app/backend/services/packaging_semantics/（本批 Spec §2）：%s" % exc)

    def submodule(self, name):
        self.module()
        try:
            return importlib.import_module("%s.%s" % (PKG, name))
        except ModuleNotFoundError as exc:
            self.fail("缺少 %s.%s（本批 Spec §2）：%s" % (PKG, name, exc))

    def preflight(self):
        try:
            return importlib.import_module(PREFLIGHT)
        except ModuleNotFoundError:
            self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现）")

    # ---------------------------------------------------------------- 夹具
    def fixture(self, name):
        path = FIXTURES / ("%s.json" % name)
        self.assertTrue(path.exists(), "缺少夹具 %s（Spec §11）" % path.name)
        return json.loads(path.read_text(encoding="utf-8"))

    def analyze(self, name, **kwargs):
        package = self.module()
        fn = getattr(package, "analyze", None)
        self.assertTrue(callable(fn), "packaging_semantics 必须导出 analyze()（Spec §2）")
        return fn(self.fixture(name), **kwargs)

    def analyze_ir(self, ir, **kwargs):
        package = self.module()
        return package.analyze(ir, **kwargs)

    # ---------------------------------------------------------------- 断言帮手
    def field(self, semantics, key):
        fields = semantics.get("fields") or {}
        self.assertIn(key, fields, "语义结果里必须有字段 %s（Spec §3/§5）" % key)
        entry = fields[key]
        self.assertIn(entry.get("origin"), ORIGINS, "origin 必须是闭集内的值：%r" % entry.get("origin"))
        self.assertIn(entry.get("status"), STATUSES,
                      "status 必须是闭集内的值：%r" % entry.get("status"))
        return entry

    def layer_role(self, semantics, layer_name):
        for item in semantics.get("layers") or []:
            if item.get("name") == layer_name:
                return item
        self.fail("语义结果里没有图层 %s（Spec §3）" % layer_name)

    def json_safe(self, value):
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            self.fail("语义结果必须 JSON 安全（无 NaN/Infinity/bytes/Path）：%s" % exc)

    def imports_of(self, path):
        """AST 读 import 名：不做子串扫描，避免注释里出现同样字样时误判。"""
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        return found

    def package_files(self):
        self.assertTrue(PKG_DIR.exists(), "缺少 packaging_semantics/（Spec §2）")
        return sorted(PKG_DIR.rglob("*.py"))

    def dict_keys(self, node, found=None):
        found = set() if found is None else found
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(str(key))
                self.dict_keys(value, found)
        elif isinstance(node, list):
            for item in node:
                self.dict_keys(item, found)
        return found

    def evidence_refs(self, node, found=None):
        found = [] if found is None else found
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("evidence_refs") or key == "evidence_ref":
                    if isinstance(value, str):
                        found.append(value)
                    else:
                        found.extend([item for item in (value or []) if isinstance(item, str)])
                else:
                    self.evidence_refs(value, found)
        elif isinstance(node, list):
            for item in node:
                self.evidence_refs(item, found)
        return found

    def warning_codes(self, semantics):
        return {str(item.get("code")) for item in (semantics.get("warnings") or [])
                if isinstance(item, dict)}

    def expect_error(self, code, fn, *args, **kwargs):
        preflight = self.preflight()
        err_type = getattr(preflight, "FileCapabilityError")
        expected = NEW_ERROR_CODES[code]
        try:
            result = fn(*args, **kwargs)
        except err_type as exc:
            self.assertEqual(getattr(exc, "stable_error_code", None), code,
                             "错误码必须是 %s（Spec §9）" % code)
            self.assertEqual(getattr(exc, "http_status", None), expected[0], code)
            self.assertEqual(getattr(exc, "retryable", None), expected[1], code)
            self.assertTrue(getattr(exc, "message", ""), "%s 必须有中文用户文案" % code)
            return exc
        except BaseException as exc:  # noqa: BLE001 - 只做分类
            self.fail("期望 %s，实际抛出 %s: %s" % (code, type(exc).__name__, exc))
        self.fail("期望 %s，实际成功返回：%r" % (code, result))

    # ---------------------------------------------------------------- 模型缝
    def patch_model(self, result=None, calls=None, error=None):
        """把 `claude_client.run` 换成可控假实现；返回调用记录列表。"""
        client = importlib.import_module("tech_app.backend.services.claude_client")
        calls = [] if calls is None else calls

        def fake(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            if error is not None:
                raise error
            return result

        patch = mock.patch.object(client, "run", fake)
        patch.start()
        self.addCleanup(patch.stop)
        return calls

    # ---------------------------------------------------------------- 需求看板（内存）
    def memory_requirement(self, data=None, project_id="proj-semantics"):
        service = importlib.import_module(REQUIREMENT_SERVICE)
        req = {"project_id": project_id, "requirement_no": "REQ-1", "status": "draft",
               "title": "包装需求", "data": copy.deepcopy(data or {}), "created_by": "tester",
               "history": [], "waivers": []}
        state = {"saved": 0, "audit": []}

        def load(pid):
            return copy.deepcopy(req) if pid == project_id else None

        def save(pid, doc, author="system"):
            state["saved"] += 1
            req.clear()
            req.update(copy.deepcopy(doc))

        for target, attr in ((service.store, "load_requirement"), (service.store, "save_requirement")):
            patch = mock.patch.object(target, attr, load if attr == "load_requirement" else save)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(service.store, "audit",
                                  lambda *a, **k: state["audit"].append((a, k)))
        patch.start()
        self.addCleanup(patch.stop)
        return service, req, state, project_id

    def apply(self, semantics, service, project_id, **kwargs):
        package = self.module()
        fn = getattr(package, "apply_to_requirement", None)
        self.assertTrue(callable(fn), "packaging_semantics 必须导出 apply_to_requirement()（Spec §2）")
        return fn(project_id, semantics, **kwargs)

    # ---------------------------------------------------------------- 内存 persistence
    def memory_persistence(self):
        persistence = self.submodule("persistence")
        for name in ("save_semantics", "load_semantics", "list_semantics"):
            self.assertTrue(callable(getattr(persistence, name, None)),
                            "persistence 必须导出 %s()（Spec §2）" % name)
        state = {"saved": []}

        def save(project_id, semantics):
            for index, item in enumerate(state["saved"]):
                if item.get("semantics_id") == semantics.get("semantics_id"):
                    state["saved"][index] = copy.deepcopy(semantics)
                    return copy.deepcopy(semantics)
            state["saved"].insert(0, copy.deepcopy(semantics))
            return copy.deepcopy(semantics)

        def load(project_id, semantics_id=None):
            for item in state["saved"]:
                if semantics_id is None or item.get("semantics_id") == semantics_id:
                    return copy.deepcopy(item)
            return None

        def list_(project_id):
            return [copy.deepcopy(item) for item in state["saved"]]

        for attr, fn in (("save_semantics", save), ("load_semantics", load),
                         ("list_semantics", list_)):
            patch = mock.patch.object(persistence, attr, fn)
            patch.start()
            self.addCleanup(patch.stop)
        return persistence, state


class AStructureAndDeterminism(SemanticsCase):
    def test_a1_required_keys_and_version(self):
        result = self.analyze("cut_crease_layers", rules=rule_set())
        missing = sorted(REQUIRED_KEYS - set(result))
        self.assertEqual(missing, [], "语义文档缺键（Spec §3）：%s" % missing)
        self.assertEqual(result.get("semantics_version"), SEMANTICS_VERSION)
        self.assertTrue(result.get("semantics_id"), "semantics_id 不能为空")
        self.assertTrue(result.get("semantics_hash"), "semantics_hash 不能为空")

    def test_a2_version_constant_is_frozen(self):
        package = self.module()
        self.assertEqual(getattr(package, "SEMANTICS_VERSION", None), SEMANTICS_VERSION)

    def test_a3_capability_reports_rules_and_model(self):
        package = self.module()
        cap = package.capability()
        for key in ("available", "rules_path", "rules_version", "template", "model_assist", "message"):
            self.assertIn(key, cap, "capability() 缺 %s（Spec §2）" % key)
        self.assertTrue(str(cap.get("rules_path")).endswith("packaging_layer_rules.json"),
                        "默认规则路径必须是 agent_knowledge/rules/packaging_layer_rules.json")
        self.assertTrue(cap.get("rules_version"), "rules_version 必须给出版本指纹（Spec §4）")

    def test_a4_analyze_is_idempotent(self):
        first = self.analyze("hole_plate", rules=rule_set())
        second = self.analyze("hole_plate", rules=rule_set())
        self.assertEqual(first.get("semantics_hash"), second.get("semantics_hash"),
                         "同样输入两次调用必须得到同一个 semantics_hash（Spec §2）")
        self.assertEqual(first.get("semantics_id"), second.get("semantics_id"))

    def test_a5_result_is_json_safe(self):
        for name in ("hole_plate", "dim_conflict", "material_texts", "round_outline"):
            self.json_safe(self.analyze(name, rules=rule_set()))

    def test_a6_lists_are_canonically_ordered(self):
        result = self.analyze("cut_crease_layers", rules=rule_set())
        names = [item.get("name") for item in result.get("layers") or []]
        self.assertEqual(names, sorted(names), "layers 必须按 name 排序（Spec §3）")
        keys = [(item.get("field"), item.get("reason")) for item in result.get("unresolved") or []]
        self.assertEqual(keys, sorted(keys), "unresolved 必须按 (field, reason) 排序（Spec §3）")

    def test_a7_analyze_writes_nothing(self):
        persistence = self.memory_persistence()
        calls = self.patch_model(result={})
        writer = mock.Mock(side_effect=_StopCall("analyze 不许落盘"))
        with mock.patch.object(persistence, "save_semantics", writer):
            self.analyze("hole_plate", rules=rule_set())
        self.assertEqual(calls, [], "analyze() 是纯函数：不许调模型（Spec §2）")
        self.assertEqual(writer.call_count, 0, "analyze() 不许落盘（Spec §2）")

    def test_a8_every_evidence_ref_resolves_in_the_input_ir(self):
        for name in ("cut_crease_layers", "hole_plate", "dim_conflict", "material_texts"):
            ir = self.fixture(name)
            result = self.analyze(name, rules=rule_set())
            known = set(ir.get("evidence") or {})
            missing = sorted({ref for ref in self.evidence_refs(result) if ref not in known})
            self.assertEqual(missing, [], "%s 里有无法回查的证据引用（Spec §3）：%s" % (name, missing[:6]))

    def test_a9_source_anchors_are_passed_through(self):
        ir = self.fixture("hole_plate")
        result = self.analyze("hole_plate", rules=rule_set())
        source = result.get("source") or {}
        self.assertEqual(source.get("ir_id"), ir.get("ir_id"))
        self.assertEqual(source.get("ir_hash"), ir.get("ir_hash"), "不许重算或改写 ir_hash")
        self.assertEqual(source.get("ir_version"), ir.get("ir_version"))
        self.assertEqual(source.get("drawing_version"), ir.get("source", {}).get("drawing_version"))

    def test_a10_upstream_warnings_are_not_laundered(self):
        result = self.analyze("warnings_passthrough", rules=rule_set())
        codes = self.warning_codes(result)
        self.assertIn("conversion_degraded", codes, "上游降级必须如实透传（Spec §3）")
        blob = self.json_safe(result)
        for word in ("无损", "lossless", "完全一致"):
            self.assertNotIn(word, blob, "有告警时不许声称 %s" % word)

    def test_a11_stats_match_the_content(self):
        result = self.analyze("cut_crease_layers", rules=rule_set())
        stats = result.get("stats") or {}
        self.assertEqual(set(stats), STATS_KEYS, "stats 键必须与 Spec §3 一致")
        self.assertEqual(stats.get("layer_total"), len(result.get("layers") or []))
        self.assertEqual(stats.get("unresolved_total"), len(result.get("unresolved") or []))
        self.assertEqual(stats.get("box_candidate_total"), len(result.get("box_candidates") or []))


class BRolesAndRules(SemanticsCase):
    def test_b1_roles_come_from_the_rule_config(self):
        result = self.analyze("cut_crease_layers", rules=rule_set())
        expected = {"CUT": "cut", "CREASE": "crease", "PRINT": "print", "FRAME": "frame"}
        for layer_name, role in expected.items():
            entry = self.layer_role(result, layer_name)
            self.assertEqual(entry.get("role"), role, "%s 的角色（Spec §4）" % layer_name)
            self.assertIn(entry.get("evidence_level"), EVIDENCE_LEVELS)
            self.assertTrue(entry.get("matched_rule_id"), "%s 必须记录命中的规则 id" % layer_name)
            self.assertTrue(entry.get("evidence_refs"))

    def test_b2_undeclared_layers_stay_unknown(self):
        result = self.analyze("cut_crease_layers", rules=rule_set())
        entry = self.layer_role(result, "MYSTERY_LAYER")
        self.assertEqual(entry.get("role"), "unknown")
        self.assertLessEqual(float(entry.get("role_confidence") or 0.0), 0.3,
                             "没命中的图层不许给高置信度（Spec §4）")
        self.assertEqual(entry.get("role_source"), "none")
        fields = {item.get("field") for item in result.get("unresolved") or []}
        self.assertTrue(fields, "unknown 图层必须进 unresolved（Spec §4）")

    def test_b3_colors_are_only_explained_by_config(self):
        default = self.analyze("color_only_layers", rules=rule_set())
        for name in ("LAYER1", "LAYER2", "LAYER3"):
            self.assertEqual(self.layer_role(default, name).get("role"), "unknown",
                             "默认模板没有颜色映射时不许把颜色读成刀线（Spec §4）")
        configured = self.analyze("color_only_layers", rules=rule_set(colors={"3": "cut"}))
        entry = self.layer_role(configured, "LAYER1")
        self.assertEqual(entry.get("role"), "cut", "客户模板配了颜色后必须生效")
        self.assertEqual(entry.get("role_source"), "color_rule")
        self.assertEqual(self.layer_role(configured, "LAYER2").get("role"), "unknown",
                         "没配的颜色不许被顺手解释")

    def test_b4_shipped_default_rules_have_no_color_table(self):
        self.assertTrue(SHIPPED_RULES.exists(), "必须交付 %s（Spec §4）" % SHIPPED_RULES.name)
        data = json.loads(SHIPPED_RULES.read_text(encoding="utf-8"))
        self.assertEqual(data.get("rule_set"), "packaging_layer_rules_v1")
        self.assertEqual(data.get("review_status"), "reviewed", "规则快照必须声明已审阅")
        template = (data.get("templates") or {}).get(data.get("default_template")) or {}
        self.assertEqual(dict(template.get("colors") or {}), {},
                         "默认模板不得内置任何「颜色编号 = 角色」的映射（Spec §4）")
        self.assertEqual(dict(template.get("line_types") or {}), {},
                         "默认模板不得内置任何「线型 = 角色」的映射（Spec §4）")
        roles = {rule.get("role") for rule in template.get("layers") or []}
        self.assertTrue(roles, "默认模板必须有图层名规则")
        self.assertNotIn("unknown", roles, "unknown 只能来自「没命中」，不许写进配置（Spec §4）")
        self.assertTrue(roles <= ROLES, "规则里的角色必须是闭集内的值：%s" % sorted(roles - ROLES))

    def test_b5_line_type_is_only_weak_evidence(self):
        weak = self.analyze("dashed_crease_layer",
                            rules=rule_set(line_types={"DASHED": "crease"}))
        entry = self.layer_role(weak, "LAYER_A")
        self.assertEqual(entry.get("role"), "crease")
        self.assertEqual(entry.get("evidence_level"), "WEAK", "线型只能作弱证据（Spec §4）")
        self.assertLessEqual(float(entry.get("role_confidence") or 0.0), 0.5)
        plain = self.analyze("dashed_crease_layer", rules=rule_set())
        self.assertEqual(self.layer_role(plain, "LAYER_A").get("role"), "unknown",
                         "没配线型时不许把虚线当压痕（Spec §4）")


class COutlineAndPanels(SemanticsCase):
    def test_c1_closed_boundary_candidate(self):
        result = self.analyze("hole_plate", rules=rule_set())
        candidates = (result.get("outline") or {}).get("boundary_candidates") or []
        self.assertTrue(candidates, "必须给出闭合外轮廓候选（Spec §3）")
        first = candidates[0]
        self.assertTrue(first.get("is_closed"))
        self.assertEqual(len(first.get("bbox") or []), 4)
        self.assertGreater(float(first.get("area") or 0.0), 0.0)
        self.assertTrue(first.get("evidence_refs"))

    def test_c2_holes_are_listed_with_evidence(self):
        result = self.analyze("hole_plate", rules=rule_set())
        holes = (result.get("outline") or {}).get("holes") or []
        self.assertEqual(len(holes), 2, "两个圆孔都要在（Spec §3）")
        for item in holes:
            self.assertTrue(item.get("evidence_refs"))
            self.assertEqual(item.get("kind"), "circle")

    def test_c3_multi_panel_candidate(self):
        result = self.analyze("multi_panel", rules=rule_set())
        panel = (result.get("outline") or {}).get("panel") or {}
        self.assertTrue(panel.get("multi_up"), "两个相同闭合轮廓 → 多拼候选（Spec §7）")
        self.assertEqual(panel.get("panel_count"), 2)
        self.assertTrue(panel.get("candidates"))

    def test_c4_round_outline_only_yields_a_candidate(self):
        result = self.analyze("round_outline", rules=rule_set())
        types = {item.get("candidate_type") for item in result.get("box_candidates") or []}
        self.assertIn("round_tube", types, "圆形轮廓应给出圆型筒盒候选（Spec §7）")
        self.assertTrue(types <= BOX_TYPES, "候选类型必须是闭集内的值：%s" % sorted(types - BOX_TYPES))
        for item in result.get("box_candidates") or []:
            for key in ("candidate_type", "confidence", "matched_features", "missing_features",
                        "contradictory_features", "evidence_refs"):
                self.assertIn(key, item, "候选必须带 %s（Spec §7）" % key)

    def test_c5_candidate_order_is_input_order_independent(self):
        base = self.analyze("cut_crease_layers", rules=rule_set())
        shuffled = self.fixture("cut_crease_layers")
        geometry = shuffled.get("geometry") or {}
        geometry["closed_outlines"] = list(reversed(geometry.get("closed_outlines") or []))
        shuffled["layers"] = list(reversed(shuffled.get("layers") or []))
        shuffled["entities"] = list(reversed(shuffled.get("entities") or []))
        again = self.analyze_ir(shuffled, rules=rule_set())
        pick = lambda doc: [item.get("outline_id")
                            for item in (doc.get("outline") or {}).get("boundary_candidates") or []]
        self.assertEqual(pick(base), pick(again), "候选顺序必须与输入书写顺序无关（Spec §3）")


class DFieldsAndConflicts(SemanticsCase):
    def test_d1_conflict_keeps_both_evidences(self):
        result = self.analyze("dim_conflict", rules=rule_set())
        conflicts = (result.get("dimensions") or {}).get("conflicts") or []
        self.assertTrue(conflicts, "标注 70 / 几何 72 必须形成冲突（Spec §5.2 第 4 条）")
        item = conflicts[0]
        self.assertAlmostEqual(float(item.get("declared")), 70.0)
        self.assertAlmostEqual(float(item.get("measured")), 72.0)
        self.assertAlmostEqual(float(item.get("delta")), 2.0)
        self.assertGreaterEqual(len(item.get("evidence_refs") or []), 2,
                                "冲突必须保留两条证据（Spec §5.2 第 4 条）")
        self.assertEqual(item.get("status"), "conflict")

    def test_d2_within_tolerance_is_not_a_conflict(self):
        result = self.analyze("dim_conflict_tolerance", rules=rule_set())
        self.assertEqual((result.get("dimensions") or {}).get("conflicts") or [], [],
                         "容差内的偏差不是冲突（Spec §5.3）")
        entry = self.field(result, "inner_length")
        self.assertEqual(entry.get("status"), "confirmed")
        self.assertEqual(entry.get("origin"), "confirmed_from_cad")
        self.assertAlmostEqual(float(entry.get("value")), 70.0,
                               "值取标注值 declared_value（Spec §5.3 分支 1）")
        self.assertNotIn("PACKAGING_UNIT_UNCONFIRMED", self.warning_codes(result))

    def test_d3_conflict_field_is_not_confirmed_and_blocks_dependents(self):
        result = self.analyze("dim_conflict", rules=rule_set())
        entry = self.field(result, "inner_length")
        self.assertEqual(entry.get("status"), "conflict")
        self.assertNotEqual(entry.get("origin"), "confirmed_from_cad")
        blocked = {item.get("field") for item in result.get("unresolved") or []}
        self.assertIn("inner_length", blocked, "冲突字段必须进 unresolved（Spec §5.2 第 4 条）")

    def test_d4_unconfirmed_units_never_confirm_absolute_sizes(self):
        result = self.analyze("unitless_dimensions", rules=rule_set())
        entry = self.field(result, "inner_length")
        self.assertEqual(entry.get("status"), "needs_confirmation",
                         "单位未确认时绝对尺寸不许确认（Spec §5.2 第 2 条）")
        self.assertNotEqual(entry.get("origin"), "confirmed_from_cad")
        self.assertIn("PACKAGING_UNIT_UNCONFIRMED", self.warning_codes(result))

    def test_d5_unconfirmed_values_never_reach_the_board(self):
        service, req, _state, project_id = self.memory_requirement()
        inferred = self.analyze("cut_crease_layers", rules=rule_set())
        self.apply(inferred, service, project_id)
        self.assertNotIn("inner_length", req.get("data") or {},
                         "未确认的推断值不许写进需求看板（Spec §5.2 第 1 条）")
        provenance = (req.get("data") or {}).get("field_provenance") or {}
        self.assertIn("inner_length", provenance, "但必须留下 provenance 留痕")
        self.assertEqual(provenance["inner_length"].get("status"), "needs_confirmation")

        service2, req2, _state2, pid2 = self.memory_requirement()
        confirmed = self.analyze("dim_conflict_tolerance", rules=rule_set())
        self.apply(confirmed, service2, pid2)
        self.assertEqual((req2.get("data") or {}).get("inner_length"), 70.0,
                         "已确认的 CAD 值必须写进看板（Spec §5.4）")

    def test_d6_conflicts_and_missing_are_enumerated(self):
        conflict = self.analyze("dim_conflict", rules=rule_set())
        rows = {item.get("field"): item for item in conflict.get("unresolved") or []}
        self.assertEqual(rows.get("inner_length", {}).get("reason"), "conflict")
        empty = self.analyze("no_material_texts", rules=rule_set())
        missing = {item.get("field") for item in empty.get("unresolved") or []}
        self.assertIn("face_paper", missing, "没有材料信息时必须是 missing（Spec §5.2 第 7 条）")

    def test_d7_user_confirmed_values_are_never_overwritten(self):
        seed = {"inner_length": 123.0, "field_sources": {"inner_length": "manual"},
                "field_provenance": {"inner_length": {
                    "origin": "user_confirmed", "status": "confirmed", "value": 123.0,
                    "confidence": 1.0, "evidence_level": "STRONG", "evidence_refs": [],
                    "conflicts": [], "alternatives": [], "ir_id": "", "ir_hash": ""}}}
        service, req, _state, project_id = self.memory_requirement(seed)
        result = self.apply(self.analyze("dim_conflict_tolerance", rules=rule_set()),
                            service, project_id)
        data = (result or req).get("data") or {}
        self.assertEqual(data.get("inner_length"), 123.0,
                         "绝不覆盖用户已确认值（Spec §5.2 第 5 条）")
        self.assertEqual((data.get("field_sources") or {}).get("inner_length"), "manual",
                         "用户手工来源不许被降级（既有 merge_field_sources 规则）")
        entry = (data.get("field_provenance") or {}).get("inner_length") or {}
        self.assertEqual(entry.get("origin"), "user_confirmed")
        self.assertTrue(entry.get("alternatives"), "新证据必须进 alternatives（Spec §5.2 第 5 条）")
        self.assertIn("PACKAGING_FIELD_USER_CONFIRMED", self.warning_codes(result))

    def test_d8_missing_material_is_never_invented(self):
        result = self.analyze("no_material_texts", rules=rule_set())
        entry = self.field(result, "face_paper")
        self.assertEqual(entry.get("origin"), "missing")
        self.assertEqual(entry.get("status"), "missing")
        self.assertIsNone(entry.get("value"))
        blob = self.json_safe(result)
        for word in ("FR-4", "白卡纸", "350g", "灰板"):
            self.assertNotIn(word, blob, "缺材料时必须保持 missing，不许编造 %s（Spec §5.2 第 7 条）" % word)

    def test_d9_field_provenance_is_page_state_not_a_business_field(self):
        repo = importlib.import_module(DA_REPO)
        self.assertIn("field_provenance", repo._STRUCTURAL_DATA_KEYS,
                      "field_provenance 必须进 _STRUCTURAL_DATA_KEYS（Spec §5.4）")
        self.assertIn("field_sources", repo._STRUCTURAL_DATA_KEYS)

    def test_d10_writes_go_through_requirement_service(self):
        service, req, _state, project_id = self.memory_requirement()
        original = service.save_requirement_draft
        calls = []

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        with mock.patch.object(service, "save_requirement_draft", spy):
            self.apply(self.analyze("dim_conflict_tolerance", rules=rule_set()),
                       service, project_id)
        self.assertEqual(len(calls), 1,
                         "写需求看板必须经 requirement_service.save_requirement_draft（Spec §5.4）")


class EModelAssist(SemanticsCase):
    def test_e1_no_preview_means_no_model_call(self):
        calls = self.patch_model(result={"title_block": {}})
        result = self.analyze("no_preview", rules=rule_set(), use_model=True)
        self.assertEqual(calls, [], "没有栅格预览时模型调用数必须是 0（Spec §6）")
        self.assertFalse((result.get("model_assist") or {}).get("used"))
        self.assertEqual((result.get("model_assist") or {}).get("stable_error_code"),
                         "PACKAGING_PREVIEW_UNAVAILABLE")
        self.assertTrue((result.get("outline") or {}).get("boundary_candidates"),
                        "模型不可用时确定性结果仍要产出（Spec §6）")

    def test_e2_raster_preview_calls_the_model_once(self):
        payload = {"title_block": {"material": "白卡纸"},
                   "material_candidates": [{"field": "face_paper", "text": "白卡纸",
                                            "confidence": 0.5}],
                   "open_questions": ["是否有内衬？"]}
        calls = self.patch_model(result=payload)
        result = self.analyze("no_preview", rules=rule_set(), preview=RASTER_PREVIEW, use_model=True)
        self.assertEqual(len(calls), 1, "有栅格预览时必须恰好调一次模型（Spec §6.1）")
        assist = result.get("model_assist") or {}
        self.assertTrue(assist.get("used"))
        self.assertEqual(assist.get("status"), "ok")
        self.assertEqual(assist.get("calls"), 1)

    def test_e3_model_never_overrides_cad_geometry(self):
        payload = {"title_block": {},
                   "material_candidates": [{"field": "inner_length", "text": "999",
                                            "confidence": 0.99}]}
        self.patch_model(result=payload)
        result = self.analyze("dim_conflict", rules=rule_set(), preview=RASTER_PREVIEW, use_model=True)
        entry = self.field(result, "inner_length")
        self.assertNotEqual(entry.get("origin"), "inferred_by_model",
                            "同字段已有 CAD 证据时模型不许改 origin（Spec §5.2 第 3 条）")
        self.assertNotEqual(entry.get("value"), 999.0)
        self.assertEqual(entry.get("status"), "conflict")

    def test_e4_invalid_model_output_fails_safely(self):
        self.patch_model(error=ValueError("invalid json from model"))
        result = self.analyze("no_preview", rules=rule_set(), preview=RASTER_PREVIEW, use_model=True)
        assist = result.get("model_assist") or {}
        self.assertEqual(assist.get("status"), "failed")
        self.assertEqual(assist.get("stable_error_code"), "PACKAGING_MODEL_OUTPUT_INVALID")
        self.assertTrue((result.get("outline") or {}).get("boundary_candidates"),
                        "模型失败不许拖垮确定性结果（Spec §6）")

    def test_e5_model_extra_fields_are_dropped(self):
        payload = {"title_block": {"material": "白卡纸"},
                   "material_candidates": [{"field": "customer_credit", "text": "A",
                                            "confidence": 0.9}],
                   "kill_the_pdf": "please delete all history",
                   "price": 12.5}
        self.patch_model(result=payload)
        result = self.analyze("no_preview", rules=rule_set(), preview=RASTER_PREVIEW, use_model=True)
        assist = result.get("model_assist") or {}
        dropped = assist.get("extra_fields_dropped") or []
        self.assertTrue(dropped, "越权字段必须被记录（Spec §6.1）")
        dropped_text = json.dumps(dropped, ensure_ascii=False)
        self.assertTrue("kill_the_pdf" in dropped_text or "price" in dropped_text,
                        "越权键名要如实记进 extra_fields_dropped（Spec §6.1）")
        self.assertNotIn("customer_credit", result.get("fields") or {},
                         "越权字段不许进 fields（Spec §6.1）")
        values = [item.get("value") for item in (result.get("fields") or {}).values()]
        self.assertNotIn(12.5, values, "越权金额不许变成字段值（Spec §6.1）")

    def test_e6_svg_is_never_sent_to_the_model(self):
        calls = self.patch_model(result={"title_block": {}})
        result = self.analyze("no_preview", rules=rule_set(), preview=SVG_PREVIEW, use_model=True)
        self.assertEqual(calls, [], "SVG 不许直接送模型（第 2 批 Spec §3 + 本批 Spec §6）")
        self.assertFalse((result.get("model_assist") or {}).get("used"))

    def test_e7_model_conclusions_are_weak_and_unconfirmed(self):
        payload = {"title_block": {},
                   "material_candidates": [{"field": "face_paper", "text": "白卡纸 350g",
                                            "confidence": 0.95}]}
        self.patch_model(result=payload)
        result = self.analyze("no_material_texts", rules=rule_set(),
                              preview=RASTER_PREVIEW, use_model=True)
        entry = self.field(result, "face_paper")
        self.assertEqual(entry.get("origin"), "inferred_by_model")
        self.assertEqual(entry.get("status"), "needs_confirmation")
        self.assertEqual(entry.get("evidence_level"), "WEAK",
                         "模型结论恒为弱证据（Spec §6）")
        self.assertLessEqual(float(entry.get("confidence") or 0.0), 0.6)

    def test_e8_no_base64_or_raw_dxf_in_the_document(self):
        self.patch_model(result={"title_block": {"material": "白卡纸"}})
        result = self.analyze("no_preview", rules=rule_set(), preview=RASTER_PREVIEW, use_model=True)
        blob = self.json_safe(result)
        self.assertNotIn(base64.b64encode(PNG_BYTES).decode(), blob, "不许内嵌预览 Base64（Spec §6）")
        self.assertNotIn("data:image/", blob)
        self.assertNotIn("SECTION", blob, "不许把 DXF 原文塞进结果")

    def test_e9_no_chain_of_thought_anywhere(self):
        payload = {"title_block": {}, "reasoning": "先看刀线再看压痕",
                   "open_questions": ["需要人工确认"]}
        self.patch_model(result=payload)
        result = self.analyze("no_preview", rules=rule_set(), preview=RASTER_PREVIEW, use_model=True)
        keys = self.dict_keys(result)
        for word in ("reasoning", "thinking", "chain_of_thought"):
            self.assertNotIn(word, keys, "产物不许出现模型思维链字段（Spec §6）")
        self.assertNotIn("先看刀线", self.json_safe(result),
                         "思维链内容不许被落进任何字段（Spec §6）")

    def test_e10_model_can_be_switched_off(self):
        calls = self.patch_model(result={"title_block": {}})
        self.analyze("no_preview", rules=rule_set(), preview=RASTER_PREVIEW, use_model=False)
        self.assertEqual(calls, [], "use_model=False 时必须零调用（Spec §6）")
        with mock.patch.dict(os.environ, {"PACKAGING_SEMANTICS_MODEL": "off"}):
            result = self.analyze("no_preview", rules=rule_set(), preview=RASTER_PREVIEW,
                                  use_model=True)
        self.assertEqual(calls, [], "PACKAGING_SEMANTICS_MODEL=off 必须全局锁死（Spec §6）")
        self.assertFalse((result.get("model_assist") or {}).get("used"))


class FCandidatesAndBoundaries(SemanticsCase):
    def test_f1_field_keys_come_from_the_packaging_template(self):
        allowed = packaging_field_keys()
        self.assertTrue(allowed, "必须能读到包装模板字段（industry_templates.PACKAGING_SPEC）")
        for name in ("cut_crease_layers", "material_texts", "dim_conflict", "hole_plate"):
            result = self.analyze(name, rules=rule_set())
            used = set(result.get("fields") or {})
            used |= {item.get("field") for item in result.get("unresolved") or []}
            unknown = sorted(key for key in used if key not in allowed)
            self.assertEqual(unknown, [], "%s 用了模板外的字段键（Spec §7）：%s" % (name, unknown))

    def test_f2_box_type_is_never_confirmed(self):
        for name in ("round_outline", "cut_crease_layers", "ambiguous_layers"):
            result = self.analyze(name, rules=rule_set())
            entry = (result.get("fields") or {}).get("box_type")
            if entry is None:
                continue
            self.assertNotEqual(entry.get("status"), "confirmed",
                                "盒型在本批只能出候选（Spec §7）")
            self.assertNotEqual(entry.get("origin"), "confirmed_from_cad")

    def test_f3_no_box_match_engine_is_touched(self):
        for path in self.package_files():
            offenders = [name for name in self.imports_of(path) if "packaging_match" in name]
            self.assertEqual(offenders, [],
                             "%s 不许依赖盒型匹配引擎（Spec §7）" % path.name)

    def test_f4_filename_is_not_evidence(self):
        first = self.analyze("cut_crease_layers", rules=rule_set())
        renamed = self.fixture("cut_crease_layers")
        renamed["source"]["attachment_name"] = "圆盘盒.dwg"
        renamed["source"]["original_filename"] = "圆盘盒.dwg"
        second = self.analyze_ir(renamed, rules=rule_set())
        self.assertEqual(first.get("fields"), second.get("fields"),
                         "文件名不是证据：改名不得改变字段结论（Spec §5.2 第 6 条）")
        self.assertEqual(first.get("box_candidates"), second.get("box_candidates"))


class GIdempotencyAndVersions(SemanticsCase):
    def test_g1_same_input_same_semantics(self):
        package = self.module()
        first = package.analyze(self.fixture("hole_plate"), rules=rule_set())
        second = package.analyze(self.fixture("hole_plate"), rules=rule_set())
        self.assertEqual(first.get("semantics_id"), second.get("semantics_id"))

    def test_g2_new_ir_version_keeps_the_old_evidence_readable(self):
        package = self.module()
        persistence, state = self.memory_persistence()
        project_id = "proj-versions"
        first_ir = self.fixture("hole_plate")
        first = package.analyze_conversion(project_id, ir=first_ir, rules=rule_set())
        again = package.analyze_conversion(project_id, ir=first_ir, rules=rule_set())
        self.assertEqual(first.get("semantics_id"), again.get("semantics_id"))
        self.assertEqual(len(state["saved"]), 1, "同输入重跑不许新增版本条目（Spec §8）")

        second_ir = copy.deepcopy(first_ir)
        second_ir["ir_id"] = "b" * 16
        second_ir["ir_hash"] = "c" * 64
        second_ir["source"]["drawing_version"] = 2
        second = package.analyze_conversion(project_id, ir=second_ir, rules=rule_set())
        self.assertNotEqual(second.get("semantics_id"), first.get("semantics_id"))
        self.assertEqual(len(state["saved"]), 2)
        old = package.load_semantics(project_id, first.get("semantics_id"))
        self.assertIsNotNone(old, "旧语义版本必须仍可回看（Spec §8）")
        self.assertEqual((old.get("source") or {}).get("ir_hash"), first_ir["ir_hash"])
        for entry in (old.get("fields") or {}).values():
            self.assertEqual(entry.get("ir_hash"), first_ir["ir_hash"],
                             "字段证据必须指向产生它的那一版 IR（Spec §8）")

    def test_g3_migrate_three_branches(self):
        package = self.module()
        migrate = getattr(package, "migrate", None)
        self.assertTrue(callable(migrate), "packaging_semantics 必须导出 migrate()（Spec §8）")
        current = self.analyze("hole_plate", rules=rule_set())
        self.assertEqual(migrate(copy.deepcopy(current)).get("status"), "ok")
        legacy = copy.deepcopy(current)
        legacy.pop("semantics_version", None)
        report = migrate(legacy)
        self.assertEqual(report.get("status"), "needs_rebuild")
        self.assertEqual(report.get("reason"), "missing_semantics_version")
        future = copy.deepcopy(current)
        future["semantics_version"] = "packaging-semantics/99"
        with self.assertRaises(ValueError):
            migrate(future)


class HErrorCodes(SemanticsCase):
    def test_h1_missing_cad_ir_uses_the_new_code(self):
        package = self.module()
        try:
            cad_ir = importlib.import_module("tech_app.backend.services.cad_ir")
        except ModuleNotFoundError:
            self.fail("依赖 DWG 第 3 批（`cad_ir` 未实现，见第 3 批 Spec §2）")
        with mock.patch.object(cad_ir, "load_ir", lambda *a, **k: None):
            self.expect_error("PACKAGING_SEMANTICS_SOURCE_MISSING",
                              package.analyze_conversion, "proj-empty", rules=rule_set())

    def test_h2_missing_rules_file_uses_the_new_code(self):
        with mock.patch.dict(os.environ, {"PACKAGING_LAYER_RULES_PATH": "/nonexistent/rules.json"}):
            self.expect_error("PACKAGING_LAYER_RULES_INVALID", self.analyze, "hole_plate")

    def test_h3_unknown_template_uses_the_new_code(self):
        bad = rule_set()
        bad["default_template"] = "generic"
        bad["templates"] = {}
        self.expect_error("PACKAGING_LAYER_RULES_INVALID", self.analyze, "hole_plate", rules=bad)

    def test_h4_new_codes_join_the_authoritative_closed_set(self):
        preflight = self.preflight()
        table = getattr(preflight, "STABLE_ERROR_CODES", None)
        self.assertIsInstance(table, dict, "必须导出 STABLE_ERROR_CODES（第 1 批 Spec §3）")
        for code, (status, retryable) in NEW_ERROR_CODES.items():
            self.assertIn(code, table, "%s 必须并入第 1 批权威闭集（Spec §9）" % code)
            self.assertEqual(table[code].get("http_status"), status, code)
            self.assertEqual(table[code].get("retryable"), retryable, code)
            self.assertTrue(table[code].get("message"), "%s 必须有中文文案" % code)


class ISafetyAndCopy(SemanticsCase):
    def test_i1_semantics_never_imports_vision_or_step_import(self):
        for path in self.package_files():
            banned = [name for name in self.imports_of(path)
                      if name.endswith("vision") or name.endswith("step_import")
                      or ".vision" in name or ".step_import" in name]
            self.assertEqual(banned, [], "%s 不许依赖 %s（Spec §2）" % (path.name, banned))

    def test_i2_only_model_assist_talks_to_the_model_client(self):
        offenders = []
        for path in self.package_files():
            if path.name == "model_assist.py":
                continue
            for name in self.imports_of(path):
                if name.endswith(("claude_client", "qwen_client", "openai_client")):
                    offenders.append("%s -> %s" % (path.name, name))
        self.assertEqual(offenders, [], "模型访问只允许经 model_assist.py（Spec §6）")

    def test_i3_error_messages_are_clean(self):
        preflight = self.preflight()
        err = self.expect_error("PACKAGING_LAYER_RULES_INVALID",
                                self.analyze, "hole_plate",
                                rules={"rule_set": "x", "default_template": "nope",
                                       "templates": {}})
        message = str(getattr(err, "message", ""))
        self.assertNotIn("Traceback", message)
        self.assertNotIn(str(ROOT), message, "错误文案不许带绝对部署路径（Spec §9）")

    def test_i4_persisted_documents_never_embed_preview_or_dxf(self):
        package = self.module()
        persistence, state = self.memory_persistence()
        self.patch_model(result={"title_block": {"material": "白卡纸"}})
        package.analyze_conversion("proj-audit", ir=self.fixture("no_preview"), rules=rule_set(),
                                   preview=RASTER_PREVIEW, use_model=True)
        blob = json.dumps(state["saved"], ensure_ascii=False, allow_nan=False)
        self.assertNotIn(base64.b64encode(PNG_BYTES).decode(), blob)
        self.assertNotIn("data:image/", blob)

    def test_i5_summarize_stays_small(self):
        package = self.module()
        summarize = getattr(package, "summarize", None)
        self.assertTrue(callable(summarize), "packaging_semantics 必须导出 summarize()（Spec §2）")
        summary = summarize(self.analyze("hole_plate", rules=rule_set()))
        self.assertNotIn("entities", summary, "摘要不许带实体明细（Spec §2）")
        self.assertNotIn("evidence", summary, "摘要不许带整份证据字典")
        blob = self.json_safe(summary)
        self.assertLess(len(blob), 8000, "摘要必须是摘要，不是整份结果")


class JDependenciesAndRealSamples(SemanticsCase):
    def test_j1_cad_ir_is_available(self):
        try:
            importlib.import_module("tech_app.backend.services.cad_ir")
        except ModuleNotFoundError as exc:
            self.fail("依赖 DWG 第 3 批（`cad_ir` 未实现）：%s" % exc)

    def test_j2_real_sample_baseline_requires_human_review(self):
        goldens = sorted(GOLDEN_DIR.glob("*.packaging.golden.json")) if GOLDEN_DIR.exists() else []
        if not goldens:
            self.skipTest("真实基线未人工复核")
        for path in goldens:
            golden = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(golden.get("reviewed_by"), "%s 缺复核人" % path.name)
            self.assertTrue(golden.get("reviewed_at"), "%s 缺复核时间" % path.name)
            for key in ("source_sha256", "must_have", "must_not_have", "unresolved"):
                self.assertIn(key, golden, "%s 缺 %s（Spec §12）" % (path.name, key))


if __name__ == "__main__":
    unittest.main(verbosity=2)

