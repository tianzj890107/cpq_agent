"""红测：DWG 支持第 6 批 —— 2D/3D 分流、真实样本 E2E 与部署验收门禁。

Spec：`docs/specs/dwg-final-acceptance.md`

现状缺口（实测，不是推断）：
  · 全仓没有 `dwg_dispatch*`：没有任何地方回答"这份图到底有没有三维实体"，
    `/api/projects/3d`（`main.py:1425`）只看项目里有没有三维产物，不看图纸性质；
  · `cad_converter.capability()` 只有一行 `three_d_conversion: False` 声明
    （`service.py:297`），**没有** `three_d` / `acceptance` 两段，`support_claim` 恒为
    `conversion_available`、`dwg_supported` 恒为 `False`（`service.py:299-301`）；
  · `tech_app/backend/services/dwg_acceptance.py` 不存在：没有任何"支持 DWG"的可验证记录；
  · `tech_app/tools/dwg_deploy_gate.py` / `dwg_acceptance_report.py` / `dwg_sample_e2e.py`
    都不存在：上线门禁还是人工口头确认；
  · `tests/fixtures/dwg_acceptance/` 不存在：没有金标，也没有审批人；
  · `.gitlab-ci.yml` 的 `python_contract` 一把跑 `unittest discover`，真实转换器冒烟
    （`tech_app/tools/dwg_conversion_smoke.py`）没有独立 job，CI 分不清"适配器测试"和"真实冒烟"。

因此本批默认判定是 No-Go，并且这个结论本身要被锁住：缺 L4 证据时全仓不许出现"支持 DWG"的
说法（`G48`）。

分组（Spec §11，53 条）：
  A 分流状态机（12）/ B 能力输出（6）/ C 验收声明（7）/ D 金标审批（5）/ E 门禁脚本（9）/
  F 上限与回滚数据（5）/ G 回滚与诚实（4）/ H 适配器契约（2）/ I 服务集成（3）。

纪律：
  · 全部离线、确定性、无网络；不写金标、不写真实 `tech_data`、不改服务器配置；
  · 转换器一律 fake/注入，不依赖本机是否装了 LibreDWG；唯一读真环境的是 `B14`/`C20`
    的"现读"断言，且对两种现场都成立；
  · 两份真实 DWG 与 `裕同包装项目-待开发/` 只读、不入库，文件名不作为证据。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DISPATCH = "tech_app.backend.services.dwg_dispatch"
DISPATCH_FILE = ROOT / "tech_app" / "backend" / "services" / "dwg_dispatch.py"
DISPATCH_DIR = ROOT / "tech_app" / "backend" / "services" / "dwg_dispatch"
ACCEPTANCE = "tech_app.backend.services.dwg_acceptance"
ACCEPTANCE_FILE = ROOT / "tech_app" / "backend" / "services" / "dwg_acceptance.py"
CAD_CONVERTER = "tech_app.backend.services.cad_converter"
MAIN = ROOT / "tech_app" / "backend" / "main.py"
STORE = "tech_app.backend.storage.store"
CI = ROOT / ".gitlab-ci.yml"
GATE_TOOL = ROOT / "tech_app" / "tools" / "dwg_deploy_gate.py"
REPORT_TOOL = ROOT / "tech_app" / "tools" / "dwg_acceptance_report.py"
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "dwg_acceptance"
RECORD_FILE = ROOT / "tech_app" / "agent_knowledge" / "dwg_acceptance.json"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"

# --------------------------------------------------------------------------- #
# 冻结常量（Spec §1/§2/§3/§6/§8）
# --------------------------------------------------------------------------- #
DISPATCH_VERSION = "dwg-dispatch/1"
DRAWING_KINDS = ("packaging_2d", "2d_only", "3d_present_unsupported", "3d_convertible",
                 "mixed", "unknown")
DRAWING3D_STATUS = ("2d_parsed", "3d_absent", "3d_converter_unavailable",
                    "3d_conversion_failed", "3d_converted_and_parsed", "3d_unknown")
THREE_D_ENTITY_TYPES = ("3DFACE", "3DSOLID", "BODY", "REGION", "SURFACE", "MESH",
                        "POLYFACE", "EXTRUDED", "EXTRUDEDSURFACE", "LOFTED", "REVOLVED")
THREE_D_ARTIFACT_ROLES = ("step", "stp", "sat", "iges", "igs")
STATUS_MESSAGES = {
    "2d_parsed": "二维图纸已解析，可继续提取结构与需求字段",
    "3d_absent": "图纸里没有三维实体",
    "3d_converter_unavailable": "图纸含三维实体，但当前转换器不支持导出三维；已按二维图纸解析",
    "3d_conversion_failed": "三维导出失败，已按二维图纸解析",
    "3d_converted_and_parsed": "三维实体已导出并解析",
    "3d_unknown": "无法判断是否含三维实体，已按二维图纸解析",
}
THREE_D_STATUS_VALUES = ("supported", "unsupported", "unavailable", "unknown")
THREE_D_STATUS_KEYS = {"declared", "supported", "available", "status", "reason"}
ACCEPTANCE_KEYS = {"present", "valid", "reason", "approved_by", "approved_at",
                   "golden_version"}
ACCEPTANCE_REASONS = ("missing_record", "unreadable", "record_version_unsupported",
                      "converter_mismatch", "binary_mismatch", "samples_missing",
                      "sample_hash_mismatch", "unapproved", "e2e_report_missing",
                      "e2e_report_hash_mismatch")
SUPPORT_CLAIMS = ("orchestration_only", "conversion_available", "supported")
ACCEPTANCE_RECORD_VERSION = "dwg-acceptance/1"
THREE_D_CONVERT_STATUSES = ("not_present", "converted", "unsupported", "unavailable",
                            "failed", "requires_real_converter", "unknown")
TWO_D_ENTITY_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE",
                      "SPLINE", "TEXT", "MTEXT", "DIMENSION", "HATCH", "INSERT")
EVIDENCE_SOURCES = ("file_preflight", "manifest", "cad_ir", "packaging_semantics")
PIPELINES = ("2d", "3d", "none")
DISPATCH_DEPS = ("cad_converter", "cad_ir", "packaging_semantics", "step_import")
GATE_VERSION = "dwg-deploy-gate/1"
GATE_STATUSES = ("ok", "fail", "manual_unacknowledged", "acknowledged", "skip")
GATE_ITEMS = (
    ("converter_license", "manual"),
    ("converter_version_pinned", "auto"),
    ("health_reports_capability", "auto"),
    ("tmp_dir_permissions", "auto"),
    ("disk_quota_and_cleanup", "auto"),
    ("conversion_timeout", "auto"),
    ("concurrency_limit", "auto"),
    ("malicious_cad_isolation", "auto"),
    ("model_failure_isolation", "auto"),
    ("db_migration_rollback", "auto"),
    ("legacy_projects_open", "auto"),
    ("non_packaging_no_regression", "auto"),
    ("step_flow_no_regression", "auto"),
    ("no_secrets_in_logs_or_fixtures", "auto"),
    ("no_dev_machine_dependency", "auto"),
    ("ci_separates_adapter_and_real_smoke", "auto"),
    ("real_samples_e2e_passed", "manual"),
    ("converter_chain_configured", "auto"),
)
LIMIT_KEYS = (
    "CAD_CONVERTER_TIMEOUT_SECONDS", "CAD_CONVERTER_MAX_OUTPUT_BYTES",
    "CAD_CONVERTER_MAX_OUTPUT_FILES", "CAD_CONVERTER_MAX_CONCURRENCY",
    "DWG_DISPATCH_MAX_CONCURRENCY_PER_PROJECT", "CAD_IR_PARSE_TIMEOUT_SECONDS",
    "CAD_IR_MAX_ENTITIES", "CAD_ARTIFACT_RETENTION_DAYS", "CAD_ARTIFACT_CLEANUP_ENABLED",
    "DWG_DISPATCH_ENABLED",
)
LIMIT_DEFAULTS = {
    "CAD_CONVERTER_TIMEOUT_SECONDS": 120,
    "CAD_CONVERTER_MAX_OUTPUT_BYTES": 256 * 1024 * 1024,
    "CAD_CONVERTER_MAX_OUTPUT_FILES": 20,
    "CAD_CONVERTER_MAX_CONCURRENCY": 2,
    "DWG_DISPATCH_MAX_CONCURRENCY_PER_PROJECT": 1,
    "CAD_IR_PARSE_TIMEOUT_SECONDS": 120,
    "CAD_IR_MAX_ENTITIES": 500000,
    "CAD_ARTIFACT_RETENTION_DAYS": 30,
    "CAD_ARTIFACT_CLEANUP_ENABLED": False,
    "DWG_DISPATCH_ENABLED": False,
}
RETENTION_PLAN_KEYS = {"policy_version", "days", "keep", "expire"}
HEALTH_KEYS = ("available", "provider", "converter_version", "three_d", "acceptance",
               "support_claim", "dwg_supported", "env")
ROUTING_KEYS = ("project_id", "dispatch_version", "status", "classification", "limits")
#: Spec §1.4 —— 分流层顶层 import 白名单（与第 5 批一致）。
TOP_LEVEL_WHITELIST = {
    "__future__", "collections", "copy", "datetime", "hashlib", "importlib", "json", "os",
    "pathlib", "re", "time", "typing", "unicodedata", "uuid",
}
#: Spec §1.4 —— 顶层禁止出现的模块（惰性 import 只能发生在函数体内）。
FORBIDDEN_TOP_LEVEL = ("ezdxf", "vision", "step_import", "requests", "subprocess",
                       "urllib", "socket", "qwen_client", "llm_client")
#: Spec §1.3 规则 4 / 禁止事项 —— 分流层不许碰预览图与模型。
PREVIEW_TOKENS = ("vision", "qwen", "llm_client", "PIL", "Image.open", "pixel", "base64",
                  "data:image", "render_preview", "ocr")
#: 全仓"能力声明"禁用短语（Spec §10.3 / 红测 `G48`）。
CLAIM_PHRASES = ("支持 DWG", "DWG 已支持", "已完成 DWG 支持")
#: Spec §6 —— 报告/输出禁用词。
PROHIBITED_TEXT = ("Traceback", "API_KEY", "api_key", "data:image", "base64",
                   "thinking", "BEGIN SECTION", "stderr:")
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(pathlib.Path(path).read_bytes())


# --------------------------------------------------------------------------- #
# 基类
# --------------------------------------------------------------------------- #
class FinalAcceptanceCase(unittest.TestCase):
    """公共装载器：模块缺失一律 fail（不许 ERROR、不许静默 skip）。"""

    maxDiff = None

    def package(self):
        try:
            return importlib.import_module(DISPATCH)
        except Exception as exc:                       # noqa: BLE001 - 红测要原文
            self.fail("缺少 %s（Spec §1）：%s: %s" % (DISPATCH, type(exc).__name__, exc))

    def acceptance_module(self):
        try:
            return importlib.import_module(ACCEPTANCE)
        except Exception as exc:                       # noqa: BLE001
            self.fail("缺少 %s（Spec §3.1）：%s: %s" % (ACCEPTANCE, type(exc).__name__, exc))

    def converter(self):
        return importlib.import_module(CAD_CONVERTER)

    def dispatch_sources(self):
        files = sorted(DISPATCH_DIR.rglob("*.py")) if DISPATCH_DIR.is_dir() else []
        if not files and DISPATCH_FILE.exists():
            files = [DISPATCH_FILE]
        if not files:
            self.fail("缺少 %s 或 %s/（Spec §1.1）" % (DISPATCH_FILE, DISPATCH_DIR))
        return {path: path.read_text(encoding="utf-8", errors="replace") for path in files}

    def accept_sources(self):
        if not ACCEPTANCE_FILE.exists():
            self.fail("缺少 %s（Spec §3.1）" % ACCEPTANCE_FILE)
        return {ACCEPTANCE_FILE: ACCEPTANCE_FILE.read_text(encoding="utf-8",
                                                           errors="replace")}

    def tool_source(self, path, hint):
        if not path.exists():
            self.fail("缺少 %s（%s）" % (path, hint))
        return path.read_text(encoding="utf-8", errors="replace")

    def use_env(self, **values):
        """临时改环境变量（None 表示删除）；每个用例自动还原。"""
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in LIMIT_KEYS + ("DWG_ACCEPTANCE_RECORD", "DWG_ACCEPTANCE_SAMPLES_DIR",
                                 "DWG_CONVERTER_PROVIDER", "CAD_CONVERTER",
                                 "CAD_CONVERTER_ALLOW_SIMULATED", "APP_ENV",
                                 "DWG_CONVERTER_BINARY", "DWG_CONVERTER_VERSION",
                                 "CAD_IR_MAX_ENTITIES", "CAD_CONVERTER_TIMEOUT_SECONDS"):
            os.environ.pop(key, None)
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)

    # -------------------------------------------------------------- 临时目录
    def tmpdir(self, prefix="dwg-b6-"):
        path = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def memory_store(self):
        """真 `store` + 临时目录上的 Json/Local 后端（不碰真实运行数据）。"""
        store_mod = importlib.import_module(STORE)
        from tech_app.backend.storage.blob_backend import LocalBlobBackend
        from tech_app.backend.storage.meta_backend import JsonMetaBackend
        root = self.tmpdir("dwg-b6-store-")
        meta, blob = JsonMetaBackend(root), LocalBlobBackend(root)
        for attr, value in (("_meta", lambda: meta), ("_blob", lambda: blob)):
            patch = mock.patch.object(store_mod, attr, value)
            patch.start()
            self.addCleanup(patch.stop)
        return store_mod, meta, blob, root

    def project(self, store_mod, name=WINE_BOX):
        return store_mod.create_project(name, b"\x00" * 64, owner="tester",
                                        owner_display_name="测试员")

    # -------------------------------------------------------------- 输入工厂
    def artifact(self, root, *, role="step", name="converted.step", payload=b"STEP-BYTES",
                 sha256=None):
        path = pathlib.Path(root) / name
        path.write_bytes(payload)
        return {"role": role, "path": str(path), "sha256": sha256 or sha256_bytes(payload),
                "bytes": len(payload)}

    def manifest_doc(self, *, conversion_id="conv-0001", status="success_with_warnings",
                     output_files=(), three_d=None, name="oda", version="27.1",
                     converter_role="primary", fallback_used=False, primary_failure_code=""):
        doc = {
            "manifest_version": "dwg-conversion-manifest/1",
            "conversion_id": conversion_id,
            "status": status,
            "converter_name": name,
            "converter_version": version,
            "converter_role": converter_role,
            "fallback_used": fallback_used,
            "primary_failure_code": primary_failure_code,
            "output_files": [dict(item) for item in output_files],
            "warnings": [],
            "error_code": "",
        }
        if three_d is not None:
            doc["three_d"] = dict(three_d)
        return doc

    def ir_doc(self, *, ir_id="ir-0001", entity_types=("LINE", "LWPOLYLINE"), layers=(),
               z=None, entity_count=None):
        entities = []
        for index, kind in enumerate(entity_types):
            row = {"id": "e-%04d" % (index + 1), "type": kind, "layer": "0"}
            if z is not None:
                row["z"] = z
            entities.append(row)
        return {
            "ir_version": "cad-ir/1",
            "ir_id": ir_id,
            "entities": entities,
            "entity_count": len(entities) if entity_count is None else entity_count,
            "layers": [dict(item) for item in layers],
        }

    def semantics_doc(self, *, semantics_version="packaging-semantics/1", layers=()):
        return {
            "semantics_version": semantics_version,
            "layers": [dict(item) for item in layers],
            "fields": [],
        }

    def converter_dep(self, *, available=True, three_d_conversion=False,
                      version="0.14", manifest=None):
        """假 `cad_converter` 依赖：只暴露分流层要用的两个函数。"""
        cap = {
            "available": available,
            "simulated": False,
            "converter_version": version,
            "three_d_conversion": three_d_conversion,
            "support_claim": "conversion_available" if available else "orchestration_only",
            "dwg_supported": False,
        }
        dep = mock.Mock()
        dep.capability.return_value = dict(cap)
        dep.latest_manifest.return_value = dict(manifest) if manifest else None
        return dep

    def step_import_dep(self, *, available):
        dep = mock.Mock()
        dep.AVAILABLE = bool(available)
        return dep

    def deps(self, *, manifest=None, ir=None, semantics=None, available=True,
             three_d_conversion=False, step_available=False):
        cad_ir = mock.Mock()
        cad_ir.load_ir.return_value = dict(ir) if ir is not None else None
        sem = mock.Mock()
        sem.latest.return_value = dict(semantics) if semantics is not None else None
        return {
            "cad_converter": self.converter_dep(available=available,
                                                three_d_conversion=three_d_conversion,
                                                manifest=manifest),
            "cad_ir": cad_ir,
            "packaging_semantics": sem,
            "step_import": self.step_import_dep(available=step_available),
        }

    def classify(self, module, project_id="p1", *, manifest=None, ir=None, semantics=None,
                 **kwargs):
        return module.classify(project_id, manifest=manifest, ir=ir, semantics=semantics,
                               deps=self.deps(manifest=manifest, ir=ir, semantics=semantics,
                                              **kwargs))

    def route(self, module, project_id="p1", classification=None, *, manifest=None, ir=None,
              semantics=None, **kwargs):
        return module.route(project_id, classification=classification,
                            deps=self.deps(manifest=manifest, ir=ir, semantics=semantics,
                                           **kwargs))

    def top_level_imports(self, path):
        """模块级（含顶层 try/if）import 名；函数体内的惰性 import 不算。"""
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        found = set()

        def walk(nodes):
            for node in nodes:
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    found.add("." * int(node.level or 0) + (node.module or ""))
                elif isinstance(node, (ast.Try, ast.If, ast.With)):
                    walk(getattr(node, "body", []))
                    walk(getattr(node, "orelse", []))
                    walk(getattr(node, "finalbody", []))
                    for handler in getattr(node, "handlers", []):
                        walk(getattr(handler, "body", []))

        walk(tree.body)
        return found


# --------------------------------------------------------------------------- #
# A. 2D/3D 分流状态机（Spec §1）
# --------------------------------------------------------------------------- #
class ADispatchStateMachine(FinalAcceptanceCase):
    def test_a1_frozen_closed_sets_are_exact(self):
        mod = self.package()
        self.assertEqual(mod.DISPATCH_VERSION, DISPATCH_VERSION,
                         "DISPATCH_VERSION 必须是 %s（Spec §1.1）" % DISPATCH_VERSION)
        self.assertEqual(tuple(mod.DRAWING_KINDS), DRAWING_KINDS,
                         "DRAWING_KINDS 闭集必须与 Spec §1.1 逐字一致")
        self.assertEqual(tuple(mod.DRAWING3D_STATUS), DRAWING3D_STATUS,
                         "DRAWING3D_STATUS 六态闭集必须与 Spec §1.1 逐字一致")
        self.assertEqual(tuple(mod.THREE_D_ENTITY_TYPES), THREE_D_ENTITY_TYPES,
                         "THREE_D_ENTITY_TYPES 闭集必须与 Spec §1.1 逐字一致")
        self.assertEqual(tuple(mod.THREE_D_ARTIFACT_ROLES), THREE_D_ARTIFACT_ROLES,
                         "THREE_D_ARTIFACT_ROLES 闭集必须与 Spec §1.1 逐字一致")

    def test_a2_status_messages_are_chinese_and_complete(self):
        mod = self.package()
        table = getattr(mod, "STATUS_MESSAGES", None)
        self.assertIsInstance(table, dict, "STATUS_MESSAGES 必须是 dict（Spec §1.1）")
        self.assertEqual(set(table), set(DRAWING3D_STATUS),
                         "STATUS_MESSAGES 的键集必须正好是六态（Spec §1.1）")
        for code, message in table.items():
            text = str(message or "")
            self.assertGreaterEqual(len(text), 8, "%s 的文案太短，业务用户读不懂" % code)
            self.assertTrue(re.search(r"[\u4e00-\u9fff]", text),
                            "%s 的文案必须是中文（Spec §1.5）" % code)
            for token in ("Traceback", "Error", "Exception", "None", ".py", "stderr",
                          "/", "\\", "{", "}"):
                self.assertNotIn(token, text,
                                 "%s 的文案不许暴露 %r（Spec §1.5）" % (code, token))

    def test_a3_z_coordinates_are_not_three_d_evidence(self):
        mod = self.package()
        manifest = self.manifest_doc()
        flat = self.ir_doc(entity_types=("LINE", "LWPOLYLINE", "ARC"), z=12.5)
        result = self.classify(mod, "p1", manifest=manifest, ir=flat,
                               three_d_conversion=True)
        self.assertEqual(result["drawing_kind"], "2d_only",
                         "实体带非零 Z 但类型全是二维时仍是 2d_only（Spec §1.3 规则 2）")
        self.assertTrue(result["signals"].get("z_only_ignored"),
                        "signals.z_only_ignored 必须为真（Spec §1.2）")
        zero = self.ir_doc(entity_types=("LINE", "LWPOLYLINE"), z=0.0)
        plain = self.classify(mod, "p1", manifest=manifest, ir=zero, three_d_conversion=True)
        self.assertFalse(plain["signals"].get("z_only_ignored"),
                         "全零 Z 不该被记成 z_only_ignored（Spec §1.2）")
        route = self.route(mod, "p1", result, manifest=manifest, ir=flat,
                           three_d_conversion=True)
        self.assertEqual(route["pipeline"], "2d",
                         "Z 坐标永远不能把图纸推上三维路径（Spec §1.3 规则 2）")

    def test_a4_filename_and_project_id_are_not_evidence(self):
        mod = self.package()
        signature = inspect.signature(mod.classify)
        for bad in ("filename", "file_name", "name", "path", "file", "basename",
                    "source_name"):
            self.assertNotIn(bad, signature.parameters,
                             "classify() 不许接受 %r：文件名不是证据（Spec §1.3 规则 3）"
                             % bad)
        first = self.classify(mod, "三维盒.dwg", manifest=None, ir=None, semantics=None)
        second = self.classify(mod, "p1", manifest=None, ir=None, semantics=None)
        self.assertEqual(first["drawing_kind"], "unknown",
                         "没有证据时不许因为文件名里带'三维'就猜三维（Spec §1.3 规则 3）")
        self.assertEqual(first["drawing_kind"], second["drawing_kind"],
                         "同一批证据必须给出同一结论，与项目/文件名无关（Spec §1.3 规则 3）")

    def test_a5_three_d_entities_without_capability_are_reported_unsupported(self):
        mod = self.package()
        manifest = self.manifest_doc()
        ir = self.ir_doc(entity_types=("3DSOLID",))
        result = self.classify(mod, "p1", manifest=manifest, ir=ir,
                               three_d_conversion=False)
        self.assertEqual(result["drawing_kind"], "3d_present_unsupported",
                         "有三维实体但转换器不支持时必须如实报 3d_present_unsupported"
                         "（Spec §1.3 规则 5③）")
        self.assertTrue(result["signals"].get("has_3d_entities"),
                        "signals.has_3d_entities 必须为真（Spec §1.2）")
        route = self.route(mod, "p1", result, manifest=manifest, ir=ir,
                           three_d_conversion=False)
        self.assertEqual(route["pipeline"], "2d")
        self.assertEqual(route["status"], "3d_converter_unavailable",
                         "三维不可转换时按二维解析并把话说清楚（Spec §1.5.1）")

    def test_a6_verified_three_d_artifact_opens_the_three_d_path(self):
        mod = self.package()
        root = self.tmpdir()
        artifact = self.artifact(root, role="step")
        manifest = self.manifest_doc(output_files=[artifact])
        ir = self.ir_doc(entity_types=("3DSOLID",))
        result = self.classify(mod, "p1", manifest=manifest, ir=ir,
                               three_d_conversion=True)
        self.assertEqual(result["drawing_kind"], "3d_convertible",
                         "有三维实体 + 产物 sha256 校验通过 → 3d_convertible"
                         "（Spec §1.3 规则 5②）")
        route = self.route(mod, "p1", result, manifest=manifest, ir=ir,
                           three_d_conversion=True, step_available=True)
        self.assertEqual(route["pipeline"], "3d", "证据齐备时才允许进三维解析（Spec §1.4）")
        self.assertEqual(route["status"], "3d_converted_and_parsed")
        blocked = self.route(mod, "p1", result, manifest=manifest, ir=ir,
                             three_d_conversion=True, step_available=False)
        self.assertEqual(blocked["pipeline"], "2d",
                         "三维解析器不可用时必须回落二维，不许硬进（Spec §1.4）")
        self.assertEqual(blocked["status"], "3d_conversion_failed")
        self.assertTrue(blocked["blocked_by"], "回落必须写清楚缺哪一条（Spec §1.4）")
        self.assertNotEqual(blocked["status"], "3d_converted_and_parsed",
                            "转换失败后不许静默声称三维成功（Spec §1.5.1）")

    def test_a7_packaging_layers_make_it_a_packaging_drawing(self):
        mod = self.package()
        manifest = self.manifest_doc()
        ir = self.ir_doc()
        cut = self.semantics_doc(layers=[{"name": "CUT", "role": "cut"},
                                         {"name": "CREASE", "role": "crease"}])
        result = self.classify(mod, "p1", manifest=manifest, ir=ir, semantics=cut)
        self.assertEqual(result["drawing_kind"], "packaging_2d",
                         "命中刀线/压痕线图层 → packaging_2d（Spec §1.3 规则 7）")
        self.assertIn("CUT", list(result["signals"].get("packaging_layers") or []))
        vague = self.semantics_doc(layers=[{"name": "图层1", "role": "unknown"}])
        soft = self.classify(mod, "p1", manifest=manifest, ir=ir, semantics=vague)
        self.assertEqual(soft["drawing_kind"], "2d_only",
                         "不规范图层名只能留在 2d_only，不许当成包装展开图"
                         "（Spec §1.3 规则 7）")

    def test_a8_preview_images_are_never_evidence(self):
        mod = self.package()
        for path, text in self.dispatch_sources().items():
            for token in PREVIEW_TOKENS:
                self.assertNotIn(token, text,
                                 "%s 里出现 %r：分流层不许开图、不许像素测量、"
                                 "不许把预览发给模型（Spec §1.3 规则 4）" % (path.name, token))
        root = self.tmpdir()
        preview = self.artifact(root, role="png", name="preview.png", payload=b"\x89PNG")
        manifest = self.manifest_doc(output_files=[preview])
        ir = self.ir_doc(entity_types=("3DSOLID",))
        result = self.classify(mod, "p1", manifest=manifest, ir=ir,
                               three_d_conversion=False)
        self.assertEqual(result["drawing_kind"], "3d_present_unsupported",
                         "预览产物不算三维中间格式证据（Spec §1.1 role 闭集）")

    def test_a9_evidence_is_sorted_and_traceable(self):
        mod = self.package()
        root = self.tmpdir()
        artifact = self.artifact(root, role="step")
        manifest = self.manifest_doc(conversion_id="conv-0042", output_files=[artifact])
        ir = self.ir_doc(ir_id="ir-0007", layers=[{"name": "CUT", "role": "cut"}])
        semantics = self.semantics_doc(semantics_version="packaging-semantics/1",
                                       layers=[{"name": "CUT", "role": "cut"}])
        result = self.classify(mod, "p1", manifest=manifest, ir=ir, semantics=semantics,
                               three_d_conversion=True)
        items = list(result.get("evidence") or [])
        self.assertTrue(items, "classify() 必须给出 evidence（Spec §1.2）")
        keys = []
        for item in items:
            self.assertTrue({"source", "key", "ref"} <= set(item),
                            "evidence 项必须带 source/key/ref（Spec §1.2）: %r" % (item,))
            self.assertIn(item["source"], EVIDENCE_SOURCES,
                          "evidence.source 越界（Spec §1.2）: %r" % (item["source"],))
            self.assertTrue(str(item["ref"] or "").strip(), "evidence.ref 不许为空")
            keys.append((item["source"], item["key"], item["ref"]))
            kind, _, value = str(item["ref"]).partition(":")
            if kind == "ir":
                self.assertEqual(value, "ir-0007", "ref 必须指回真实 IR（Spec §1.6）")
            elif kind == "manifest":
                self.assertEqual(value, "conv-0042", "ref 必须指回真实 manifest（Spec §1.6）")
            elif kind == "semantics":
                self.assertEqual(value, "packaging-semantics/1",
                                 "ref 必须指回真实语义版本（Spec §1.6）")
            else:
                self.fail("evidence.ref 形状越界（Spec §1.6）: %r" % (item["ref"],))
        self.assertEqual(keys, sorted(keys), "evidence 必须按 (source,key,ref) 排序（Spec §1.2）")
        again = self.classify(mod, "p1", manifest=manifest, ir=ir, semantics=semantics,
                              three_d_conversion=True)
        self.assertEqual(json.dumps(result, sort_keys=True, ensure_ascii=False),
                         json.dumps(again, sort_keys=True, ensure_ascii=False),
                         "同输入必须同结论、同排序（Spec §1.2）")

    def test_a10_unknown_is_handled_conservatively(self):
        mod = self.package()
        result = mod.classify("p1", manifest=None, ir=None, semantics=None,
                              deps=self.deps(available=True))
        self.assertEqual(result["drawing_kind"], "unknown",
                         "证据不足 → unknown（Spec §1.3 规则 5①）")
        route = mod.route("p1", classification=result, deps=self.deps(available=True))
        self.assertIn(route["pipeline"], ("2d", "none"),
                      "unknown 必须按二维保守处理（Spec §1.3 规则 9）")
        self.assertEqual(route["status"], "3d_unknown")

    def test_a11_signature_and_imports_cannot_carry_raw_dwg(self):
        mod = self.package()
        for name in ("classify", "route", "dispatch_document"):
            params = inspect.signature(getattr(mod, name)).parameters
            for bad in ("content", "data", "raw", "blob", "payload", "stream",
                        "dwg_bytes", "file_bytes", "body"):
                self.assertNotIn(bad, params,
                                 "%s() 不许接受原始字节参数 %r（Spec §1.4" % (name, bad))
            for param in params:
                self.assertNotIn("bytes", param.lower(),
                                 "%s() 的参数 %r 看起来能带原始字节（Spec §1.4）"
                                 % (name, param))
        for path, text in self.dispatch_sources().items():
            imports = self.top_level_imports(path)
            for module in sorted(imports):
                if module.startswith("."):
                    continue
                top = module.split(".")[0]
                self.assertNotIn(top, FORBIDDEN_TOP_LEVEL,
                                 "%s 顶层 import 了 %r：三维解析必须惰性经 deps 拿"
                                 "（Spec §1.4）" % (path.name, module))
                self.assertTrue(top in TOP_LEVEL_WHITELIST or top == "tech_app",
                                "%s 顶层 import 了白名单外的 %r（Spec §1.4）"
                                % (path.name, module))

    def test_a12_six_states_follow_the_frozen_derivation_table(self):
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        self.use_env(DWG_DISPATCH_ENABLED="true")
        out = self.tmpdir()
        step = self.artifact(out, role="step")
        broken = self.artifact(out, role="step", name="broken.step",
                               sha256="0" * 64)
        cut = self.semantics_doc(layers=[{"name": "CUT", "role": "cut"}])
        scenarios = (
            ("2d_parsed", dict(manifest=self.manifest_doc(), ir=self.ir_doc(),
                               semantics=cut), {}),
            ("3d_absent", dict(manifest=self.manifest_doc(),
                               ir=self.ir_doc(entity_types=("LINE", "ARC")),
                               semantics=None), {}),
            ("3d_converter_unavailable",
             dict(manifest=self.manifest_doc(), ir=self.ir_doc(entity_types=("3DSOLID",)),
                  semantics=None), {"three_d_conversion": False}),
            ("3d_conversion_failed",
             dict(manifest=self.manifest_doc(output_files=[broken]),
                  ir=self.ir_doc(entity_types=("3DSOLID",)), semantics=None),
             {"three_d_conversion": True, "step_available": False}),
            ("3d_converted_and_parsed",
             dict(manifest=self.manifest_doc(output_files=[step]),
                  ir=self.ir_doc(entity_types=("3DSOLID",)), semantics=None),
             {"three_d_conversion": True, "step_available": True}),
            ("3d_unknown", dict(manifest=None, ir=None, semantics=None), {}),
        )
        for expected, inputs, kwargs in scenarios:
            result = self.classify(mod, pid, **inputs, **kwargs)
            route = self.route(mod, pid, result, **inputs, **kwargs)
            self.assertEqual(route["status"], expected,
                             "六态推导表（Spec §1.5.1）不符：%r" % (result["drawing_kind"],))
            self.assertIn(route["status"], DRAWING3D_STATUS)
            if route["status"] == "3d_converted_and_parsed":
                self.assertEqual(route["pipeline"], "3d",
                                 "3d_converted_and_parsed 只允许出现在三维路径（Spec §1.5.1）")
            else:
                self.assertNotEqual(route["pipeline"], "3d",
                                    "非'已导出并解析'的结论不许走三维路径（Spec §1.5.1）")


# --------------------------------------------------------------------------- #
# B. 能力输出（Spec §2）
# --------------------------------------------------------------------------- #
class BCapabilityOutput(FinalAcceptanceCase):
    def _health_source(self):
        text = MAIN.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"@app\.get\(\"/api/health\"\)\s*\ndef health\(\):(.*?)(?=\n@app\.|\ndef )",
                          text, re.S)
        if not match:
            self.fail("main.py 里找不到 /api/health 的 health()（Spec §2.2）")
        return match.group(1)

    def test_b13_health_segment_reports_three_d_and_acceptance(self):
        block = self._health_source()
        self.assertRegex(block, r"\"cad_converter\"\s*:\s*cad_converter\.capability\(\)",
                         "/api/health 的 cad_converter 段必须直接来自 capability()（Spec §2.2）")
        cap = self.converter().capability()
        for key in HEALTH_KEYS:
            self.assertIn(key, cap, "/api/health 的 cad_converter 段缺少 %s（Spec §2.2）" % key)

    def test_b14_three_d_segment_has_frozen_closed_set(self):
        self.use_env(DWG_CONVERTER_PROVIDER="none")
        cap = self.converter().capability()
        three_d = cap.get("three_d")
        self.assertIsInstance(three_d, dict, "capability() 必须新增 three_d 段（Spec §2.1）")
        self.assertEqual(set(three_d), THREE_D_STATUS_KEYS,
                         "three_d 的键集必须正好是 %r（Spec §2.1）" % (sorted(THREE_D_STATUS_KEYS),))
        self.assertIn(three_d["status"], THREE_D_STATUS_VALUES,
                      "three_d.status 闭集越界：%r（Spec §2.1）" % (three_d["status"],))
        self.assertFalse(three_d["available"], "没有转换器时 three_d.available 必须为假")
        self.assertEqual(three_d["status"], "unavailable")
        self.assertTrue(str(three_d["reason"] or "").strip(), "three_d.reason 必须说明原因")

    def test_b15_acceptance_segment_uses_stable_reason_codes(self):
        cap = self.converter().capability()
        acceptance = cap.get("acceptance")
        self.assertIsInstance(acceptance, dict, "capability() 必须新增 acceptance 段（Spec §2.1）")
        self.assertEqual(set(acceptance), ACCEPTANCE_KEYS,
                         "acceptance 的键集必须正好是 %r（Spec §2.1）"
                         % (sorted(ACCEPTANCE_KEYS),))
        reason = str(acceptance["reason"] or "")
        if acceptance["valid"]:
            self.assertEqual(reason, "", "有效记录的 reason 必须是空串（Spec §3.1）")
        else:
            self.assertIn(reason, ACCEPTANCE_REASONS,
                          "acceptance.reason 必须是闭集内的稳定码（Spec §2.1）：%r" % (reason,))

    def test_b16_drawing_routing_route_is_read_only(self):
        text = MAIN.read_text(encoding="utf-8", errors="replace")
        blocks = []
        for match in re.finditer(r"@app\.(get|post|put|delete)\(\"([^\"]*)\"", text):
            start = match.start()
            nxt = text.find("\n@app.", match.end())
            blocks.append((match.group(1), match.group(2),
                           text[start:nxt if nxt > 0 else len(text)]))
        hit = [item for item in blocks if item[1].endswith("/drawing-routing")]
        self.assertTrue(hit, "main.py 里没有 /api/projects/{id}/drawing-routing（Spec §2.3）")
        method, route, block = hit[0]
        self.assertEqual(method, "get", "分流查询必须是只读 GET（Spec §2.3）")
        self.assertIn("can_read", block, "分流查询必须走 project_access.can_read（Spec §2.3）")
        for token in ("save_", "put_doc", "json.dump", ".write_text", "invalidate_"):
            self.assertNotIn(token, block,
                             "只读路由里不许出现写操作 %r（Spec §2.3）" % token)
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        before = {path: sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}
        mod.status_for(pid)
        mod.status_for(pid)
        after = {path: sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}
        self.assertEqual(before, after, "status_for() 必须是纯读取，不许改任何存储文件（Spec §2.3）")

    def test_b17_legacy_project_reports_unknown_without_raising(self):
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        self.use_env(DWG_DISPATCH_ENABLED="true")
        status = mod.status_for(pid)
        self.assertEqual(status["code"], "3d_unknown",
                         "没有分流文档的老项目必须报 3d_unknown（Spec §1.5）")
        self.assertIn(status["pipeline"], ("2d", "none"))
        status2 = mod.status_for("不存在的项目")
        self.assertEqual(status2["code"], "3d_unknown", "未知项目也不许抛异常（Spec §1.5）")

    def test_b18_two_status_tables_are_not_derived_from_each_other(self):
        mod = self.package()
        manifest = self.manifest_doc()
        ir = self.ir_doc(entity_types=("LINE", "ARC"))
        machine_supports = self.route(mod, "p1", self.classify(
            mod, "p1", manifest=manifest, ir=ir, three_d_conversion=True),
            manifest=manifest, ir=ir, three_d_conversion=True)
        self.assertEqual(machine_supports["status"], "3d_absent",
                         "机器支持三维不等于这份图有三维（Spec §2.1）")
        solid = self.ir_doc(entity_types=("3DSOLID",))
        codes = set()
        for flag in (True, False):
            classification = self.classify(mod, "p1", manifest=self.manifest_doc(), ir=solid,
                                           three_d_conversion=flag)
            codes.add(self.route(mod, "p1", classification, manifest=self.manifest_doc(),
                                 ir=solid, three_d_conversion=flag)["status"])
        self.assertEqual(codes, {"3d_converter_unavailable"},
                         "同一份图的六态只由图纸证据决定，不许跟着机器能力变（Spec §2.1）")


# --------------------------------------------------------------------------- #
# C. 验收声明（Spec §3）
# --------------------------------------------------------------------------- #
class CAcceptanceClaim(FinalAcceptanceCase):
    def live(self, *, name="oda", version="27.1", binary="b" * 64, samples=None):
        return {"converter_name": name, "converter_version": version,
                "binary_sha256": binary,
                "samples": dict(samples if samples is not None
                                else {WINE_BOX: "a" * 64, ROUND_BOX: "c" * 64})}

    def record_env(self, *, approved_by="zhangzhen",
                   approved_at="2026-09-20T13:00:00+08:00", version="27.1",
                   report_payload=b"# E2E report\n"):
        root = self.tmpdir("dwg-b6-acc-")
        samples_dir = root / "samples"
        samples_dir.mkdir()
        hashes = {}
        for name, payload in ((WINE_BOX, b"WINE-DWG"), (ROUND_BOX, b"ROUND-DWG")):
            path = samples_dir / name
            path.write_bytes(payload)
            hashes[name] = sha256_bytes(payload)
        report = root / "e2e-report.md"
        report.write_bytes(report_payload)
        live = self.live(samples=hashes)
        record = {
            "record_version": ACCEPTANCE_RECORD_VERSION,
            "converter": {"name": "oda", "version": version, "binary_sha256": "b" * 64},
            "samples": [{"filename": name, "sha256": digest, "dxf_sha256": "d" * 64,
                         "preview_sha256": "e" * 64, "layer_count": 12, "entity_count": 340,
                         "non_blank": True} for name, digest in sorted(hashes.items())],
            "e2e": {"report_path": str(report), "report_sha256": sha256_file(report),
                    "finished_at": "2026-09-20T12:00:00+08:00"},
            "golden_version": "2026-09-20.1",
            "approved_by": approved_by, "approved_at": approved_at,
        }
        return root, samples_dir, live, record

    def state(self, record, *, live, samples_dir):
        return self.acceptance_module().validate(record, live=live, samples_dir=samples_dir)

    def test_c19_support_claim_table_has_exactly_four_rows(self):
        mod = self.acceptance_module()
        real = {"available": True, "simulated": False}
        fake = {"available": True, "simulated": True}
        rows = (
            ({"adapter": {"available": False, "simulated": False},
              "record_state": {"present": False, "valid": False, "reason": "missing_record"}},
             "orchestration_only", False),
            ({"adapter": real,
              "record_state": {"present": False, "valid": False, "reason": "missing_record"}},
             "conversion_available", False),
            ({"adapter": fake,
              "record_state": {"present": True, "valid": True, "reason": ""}},
             "orchestration_only", False),
            ({"adapter": real,
              "record_state": {"present": True, "valid": True, "reason": ""}},
             "supported", True),
        )
        for kwargs, claim, supported in rows:
            result = mod.support_claim(**kwargs)
            self.assertEqual(result["support_claim"], claim,
                             "声明推导表（Spec §3.2）不符：%r" % (kwargs,))
            self.assertEqual(bool(result["dwg_supported"]), supported,
                             "dwg_supported 必须与推导表一致（Spec §3.2）：%r" % (kwargs,))
            self.assertIn(result["support_claim"], SUPPORT_CLAIMS)

    def test_c20_an_installed_converter_is_not_a_support_claim(self):
        mod = self.acceptance_module()
        result = mod.support_claim(adapter={"available": True, "simulated": False},
                                  record_state={"present": True, "valid": False,
                                                "reason": "unapproved"})
        self.assertNotEqual(result["support_claim"], "supported",
                            "装了转换器 + 记录未审批 → 不许声称支持（Spec §3.2）")
        self.assertFalse(result["dwg_supported"])
        cap = self.converter().capability()
        self.assertNotEqual(cap["support_claim"], "supported",
                            "本机没有有效验收记录，support_claim 不许是 supported（Spec §3.2）")
        self.assertFalse(cap["dwg_supported"])
        self.assertIn(cap["support_claim"], SUPPORT_CLAIMS)
        self.assertNotIn("\"real\"", json.dumps(cap, ensure_ascii=False))
        self.assertNotEqual(cap["support_claim"], "real",
                            "第 2 批禁令延续：support_claim 不许出现 'real'（Spec §3.2）")

    def test_c21_missing_unreadable_and_unsupported_records_are_named(self):
        mod = self.acceptance_module()
        root, samples_dir, live, record = self.record_env()
        self.assertEqual(self.state({}, live=live, samples_dir=samples_dir)["reason"],
                         "missing_record", "记录不存在必须是 missing_record（Spec §3.1）")
        corrupt = root / "corrupt.json"
        corrupt.write_text("{ not json", encoding="utf-8")
        broken = mod.validate(None, live=live, samples_dir=samples_dir, record_path=corrupt)
        self.assertEqual(broken["reason"], "unreadable",
                         "损坏的记录必须是 unreadable（Spec §3.1）")
        self.assertFalse(broken["valid"])
        old = dict(record)
        old["record_version"] = "dwg-acceptance/9"
        self.assertEqual(self.state(old, live=live, samples_dir=samples_dir)["reason"],
                         "record_version_unsupported", "版本不受支持必须点名（Spec §3.1）")

    def test_c22_converter_and_binary_mismatch_are_named(self):
        mod = self.acceptance_module()
        root, samples_dir, live, record = self.record_env()
        self.assertTrue(self.state(record, live=live, samples_dir=samples_dir)["valid"],
                        "构造的有效记录必须能通过校验（Spec §3.2）")
        other = json.loads(json.dumps(record))
        other["converter"]["version"] = "0.13"
        self.assertEqual(self.state(other, live=live, samples_dir=samples_dir)["reason"],
                         "converter_mismatch", "转换器版本不一致必须点名（Spec §3.2）")
        renamed = json.loads(json.dumps(record))
        renamed["converter"]["name"] = "libredwg"
        self.assertEqual(self.state(renamed, live=live, samples_dir=samples_dir)["reason"],
                         "converter_mismatch", "转换器改名必须点名（Spec §3.2）")
        swapped = json.loads(json.dumps(record))
        swapped["converter"]["binary_sha256"] = "9" * 64
        self.assertEqual(self.state(swapped, live=live, samples_dir=samples_dir)["reason"],
                         "binary_mismatch", "二进制指纹不一致必须点名（Spec §3.2）")

    def test_c23_sample_files_and_hashes_are_named(self):
        root, samples_dir, live, record = self.record_env()
        empty = root / "empty-samples"
        empty.mkdir()
        self.assertEqual(self.state(record, live=live, samples_dir=empty)["reason"],
                         "samples_missing", "样本文件不在必须点名（Spec §3.2）")
        short = json.loads(json.dumps(record))
        short["samples"] = short["samples"][:1]
        self.assertEqual(self.state(short, live=live, samples_dir=samples_dir)["reason"],
                         "samples_missing", "样本少于两份必须点名（Spec §3.2）")
        moved = json.loads(json.dumps(record))
        moved["samples"][0]["sha256"] = "f" * 64
        self.assertEqual(self.state(moved, live=live, samples_dir=samples_dir)["reason"],
                         "sample_hash_mismatch", "样本被换过必须点名（Spec §3.2）")

    def test_c24_unapproved_and_absent_e2e_report_are_named(self):
        root, samples_dir, live, record = self.record_env()
        unapproved = json.loads(json.dumps(record))
        unapproved["approved_by"] = ""
        self.assertEqual(self.state(unapproved, live=live, samples_dir=samples_dir)["reason"],
                         "unapproved", "没有审批人必须点名（Spec §3.2）")
        undated = json.loads(json.dumps(record))
        undated["approved_at"] = "上周"
        self.assertEqual(self.state(undated, live=live, samples_dir=samples_dir)["reason"],
                         "unapproved", "审批时间不可解析必须点名（Spec §3.2）")
        gone = json.loads(json.dumps(record))
        gone["e2e"]["report_path"] = str(root / "nope.md")
        self.assertEqual(self.state(gone, live=live, samples_dir=samples_dir)["reason"],
                         "e2e_report_missing", "E2E 报告缺失必须点名（Spec §3.2）")
        stale = json.loads(json.dumps(record))
        stale["e2e"]["report_sha256"] = "7" * 64
        self.assertEqual(self.state(stale, live=live, samples_dir=samples_dir)["reason"],
                         "e2e_report_hash_mismatch", "E2E 报告被改过必须点名（Spec §3.2）")

    def test_c25_record_is_reread_on_every_call(self):
        mod = self.acceptance_module()
        root, samples_dir, live, record = self.record_env()
        path = root / "record.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        first = mod.acceptance(live=live, record_path=path)
        self.assertTrue(first["valid"], "有效记录必须当场可读（Spec §3.1）")
        self.assertEqual(first["golden_version"], "2026-09-20.1")
        record["approved_by"] = ""
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        second = mod.acceptance(live=live, record_path=path)
        self.assertFalse(second["valid"],
                         "记录必须每次现读：改文件后立刻生效，不许 import 时固化（Spec §3.1）")
        self.assertEqual(second["reason"], "unapproved")
        path.unlink()
        third = mod.acceptance(live=live, record_path=path)
        self.assertEqual(third["reason"], "missing_record")
        self.assertEqual(mod.record_path().name, "dwg_acceptance.json",
                         "默认记录路径必须是 tech_app/agent_knowledge/dwg_acceptance.json"
                         "（Spec §3.1）")


def run_tool(args, *, env=None, timeout=240):
    """跑仓内工具脚本（子进程，cwd=仓库根）；工具缺失时由调用方给出稳定失败信息。"""
    merged = dict(os.environ)
    for key in ("DWG_ACCEPTANCE_RECORD", "DWG_DISPATCH_ENABLED", "CPQ_DWG_REAL_SAMPLES"):
        merged.pop(key, None)
    if env:
        merged.update({k: str(v) for k, v in env.items()})
    return subprocess.run([sys.executable] + [str(item) for item in args],
                          capture_output=True, text=True, timeout=timeout,
                          env=merged, cwd=str(ROOT))


# --------------------------------------------------------------------------- #
# D. 金标验收（Spec §4）
# --------------------------------------------------------------------------- #
class DGoldenBaseline(FinalAcceptanceCase):
    SAMPLE_KEYS = {"golden_version", "sample_filename", "source_sha256",
                   "detected_dwg_version", "converter", "artifacts", "stats", "unit_status",
                   "cut_layers", "crease_layers", "box_candidates", "key_dimensions",
                   "required_unresolved", "forbidden_fields", "three_d_status",
                   "reviewed_by", "reviewed_at", "approval"}

    def golden_versions(self):
        if not GOLDEN_DIR.is_dir():
            self.fail("缺少 tests/fixtures/dwg_acceptance/<golden_version>/（Spec §4.1）："
                      "没有金标就没有「真实转换能力已验收」这句话的依据")
        versions = [item for item in sorted(GOLDEN_DIR.iterdir()) if item.is_dir()]
        self.assertTrue(versions, "金标目录下必须至少有一个 <golden_version>/（Spec §4.1）")
        return versions

    def test_d26_golden_baseline_files_are_complete(self):
        for version in self.golden_versions():
            manifest = version / "manifest.json"
            self.assertTrue(manifest.is_file(), "缺少 %s（Spec §4.1）" % manifest)
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertIn("golden_version", meta)
            self.assertIn(WINE_BOX, meta.get("samples", {}), "金标必须覆盖两份真实样本")
            self.assertIn(ROUND_BOX, meta.get("samples", {}), "金标必须覆盖两份真实样本")
            for name in (WINE_BOX, ROUND_BOX):
                path = version / (name.replace(".dwg", "") + ".json")
                self.assertTrue(path.is_file(), "缺少样本金标 %s（Spec §4.1）" % path)
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(self.SAMPLE_KEYS <= set(doc),
                                "%s 缺少字段 %r（Spec §4.1）"
                                % (path.name, sorted(self.SAMPLE_KEYS - set(doc))))
                converter = doc.get("converter") or {}
                self.assertEqual(converter.get("role"), "primary",
                                 "%s 的金标只许记主转换器产物（Spec §4.1）" % path.name)
                self.assertFalse(converter.get("fallback_used"),
                                 "%s 的金标不许把回退产物当主转换器证据（Spec §4.1）" % path.name)
                self.assertEqual(len(str(doc["source_sha256"])), 64,
                                 "%s 的 source_sha256 必须是 sha256（Spec §4.1）" % path.name)
                self.assertTrue(doc["forbidden_fields"],
                                "%s 的 forbidden_fields 必须非空（Spec §4.1）" % path.name)
                self.assertIn(doc["three_d_status"], DRAWING3D_STATUS + ("",),
                              "%s 的 three_d_status 越界（Spec §4.1）" % path.name)

    def test_d27_unapproved_golden_cannot_claim_support(self):
        for version in self.golden_versions():
            for name in (WINE_BOX, ROUND_BOX):
                path = version / (name.replace(".dwg", "") + ".json")
                doc = json.loads(path.read_text(encoding="utf-8"))
                approval = doc.get("approval") or {}
                self.assertTrue(str(approval.get("approved_by") or "").strip(),
                                "%s 没有审批人：未经人工审批的金标不许当作验收证据"
                                "（Spec §4.2）" % path.name)
                self.assertTrue(str(approval.get("approved_at") or "").strip(),
                                "%s 没有审批时间（Spec §4.2）" % path.name)
        baseline = self.tmpdir("dwg-b6-baseline-")
        version = baseline / "2026-09-20.1"
        version.mkdir()
        (version / "manifest.json").write_text(json.dumps(
            {"golden_version": "2026-09-20.1", "samples": {WINE_BOX: {}, ROUND_BOX: {}}}),
            encoding="utf-8")
        for name in (WINE_BOX, ROUND_BOX):
            (version / (name.replace(".dwg", "") + ".json")).write_text(json.dumps(
                {"golden_version": "2026-09-20.1", "sample_filename": name,
                 "forbidden_fields": ["material"], "approval": {"approved_by": "",
                                                               "approved_at": ""}}),
                encoding="utf-8")
        self.tool_source(REPORT_TOOL, "Spec §4.2 金标工具")
        result = run_tool([REPORT_TOOL, "--verify", "--baseline-dir", version])
        self.assertNotEqual(result.returncode, 0,
                            "未审批的金标必须让校验非零退出（Spec §4.2）：%s"
                            % (result.stdout or "")[-400:])

    def test_d28_write_baseline_requires_an_approver(self):
        self.tool_source(REPORT_TOOL, "Spec §4.2 金标工具")
        target = self.tmpdir("dwg-b6-write-")
        result = run_tool([REPORT_TOOL, "--write-baseline", "--baseline-dir", target])
        self.assertNotEqual(result.returncode, 0,
                            "缺 --approved-by 时 --write-baseline 必须非零退出（Spec §4.2）")
        self.assertEqual(sorted(target.rglob("*")), [],
                         "缺 --approved-by 时不许写任何文件（Spec §4.2）")
        self.assertIn("--approved-by", result.stdout + result.stderr,
                      "拒绝写入时必须说清楚缺什么（Spec §4.2）")

    def test_d29_the_tool_never_rewrites_baselines_by_default(self):
        source = self.tool_source(REPORT_TOOL, "Spec §4.2 金标工具")
        self.assertNotIn("--update-snapshot", source,
                         "不许提供自动刷 snapshot 的开关（Spec §4.2）")
        for version in self.golden_versions():
            before = {path: sha256_file(path) for path in sorted(version.rglob("*"))
                      if path.is_file()}
            self.assertFalse(before == {}, "金标目录不能是空的（Spec §4.1）")
            run_tool([REPORT_TOOL, "--verify", "--baseline-dir", version])
            after = {path: sha256_file(path) for path in sorted(version.rglob("*"))
                     if path.is_file()}
            self.assertEqual(before, after,
                             "--verify 默认只读比较，不许改动金标（Spec §4.2）")

    def test_d30_no_test_writes_into_the_golden_directory(self):
        write_calls = {"write_text", "write_bytes", "dump", "dump_all", "rmtree", "unlink",
                       "remove", "rename", "replace", "copy", "copyfile", "copytree",
                       "mkdir", "touch", "makedirs"}

        def call_name(node):
            func = node.func
            if isinstance(func, ast.Attribute):
                return func.attr
            if isinstance(func, ast.Name):
                return func.id
            return ""

        offenders = []
        for path in sorted((ROOT / "tests").rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            # 只对提到金标目录的文件做 AST 扫描：候选文件极少，避免对大文件做
            # O(节点数 × 文件长度) 的 get_source_segment。
            if not any(token in text for token in ("GOLDEN_DIR", "dwg_acceptance",
                                                   "acceptance")):
                continue
            for node in ast.walk(ast.parse(text)):
                if not isinstance(node, ast.Call):
                    continue
                lines = text.splitlines()
                start = max(int(node.lineno) - 1, 0)
                end = min(int(getattr(node, "end_lineno", node.lineno) or node.lineno),
                          len(lines))
                segment = "\n".join(lines[start:end])
                if "GOLDEN_DIR" not in segment and "dwg_acceptance" not in segment:
                    continue
                if call_name(node) in write_calls:
                    offenders.append("%s: %s" % (path.name, segment.splitlines()[0][:90]))
        self.assertEqual(offenders, [],
                         "测试代码不许写金标目录（Spec §4.2）：%r" % offenders)


# --------------------------------------------------------------------------- #
# E. 部署门禁脚本（Spec §6/§7）
# --------------------------------------------------------------------------- #
class EDeployGate(FinalAcceptanceCase):
    def gate_json(self, env="ci", extra=(), env_vars=None):
        self.tool_source(GATE_TOOL, "Spec §6 部署门禁脚本")
        result = run_tool([GATE_TOOL, "--env", env, "--json"] + list(extra),
                          env=env_vars)
        text = (result.stdout or "").strip()
        self.assertTrue(text, "门禁脚本必须输出 JSON（Spec §6）：rc=%s stderr=%s"
                        % (result.returncode, (result.stderr or "")[-300:]))
        try:
            payload = json.loads(text[text.find("{"):])
        except ValueError as exc:
            self.fail("门禁脚本 --json 输出不是 JSON（Spec §6）：%s" % exc)
        return payload, result

    def block_of(self, name, text=None):
        text = text if text is not None else CI.read_text(encoding="utf-8")
        lines = text.splitlines()
        out, inside = [], False
        for line in lines:
            if not line.strip() or line.startswith("#"):
                continue
            if line[0] not in " \t":
                inside = line.split(":")[0].strip() == name
                if inside:
                    out.append(line)
                continue
            if inside:
                out.append(line)
        self.assertTrue(out, ".gitlab-ci.yml 里没有 %s job（Spec §5）" % name)
        return "\n".join(out)

    def test_e31_the_gate_tool_runs_and_declares_its_version(self):
        source = self.tool_source(GATE_TOOL, "Spec §6 部署门禁脚本")
        self.assertIn(GATE_VERSION, source,
                      "门禁脚本必须声明 GATE_VERSION = %r（Spec §6）" % GATE_VERSION)
        result = run_tool([GATE_TOOL, "--help"])
        self.assertEqual(result.returncode, 0, "门禁脚本 --help 必须可跑（Spec §6）")
        for flag in ("--env", "--json", "--report", "--ack"):
            self.assertIn(flag, result.stdout, "--help 必须列出 %s（Spec §6）" % flag)

    def test_e32_gate_output_shape_is_frozen(self):
        payload, _ = self.gate_json()
        self.assertEqual(payload.get("gate_version"), GATE_VERSION)
        self.assertEqual(payload.get("env"), "ci")
        self.assertIn(payload.get("verdict"), ("go", "no_go"))
        items = payload.get("items")
        self.assertIsInstance(items, list, "items 必须是列表（Spec §6）")
        self.assertEqual(len(items), len(GATE_ITEMS), "门禁必须逐项检查（Spec §7）")
        for item in items:
            self.assertTrue({"id", "kind", "status", "evidence", "message"} <= set(item),
                            "门禁项缺字段（Spec §6）：%r" % (item,))
            self.assertIn(item["status"], GATE_STATUSES,
                          "status 越界（Spec §6）：%r" % (item["status"],))
        summary = payload.get("summary")
        self.assertTrue({"ok", "fail", "manual", "acknowledged", "skip"} <= set(summary),
                        "summary 缺键（Spec §6）：%r" % (summary,))
        self.assertEqual(sum(summary[key] for key in
                             ("ok", "fail", "manual", "acknowledged", "skip")), len(items),
                         "summary 必须能对上 items（Spec §6）")

    def test_e33_ci_separates_adapter_tests_from_real_smoke(self):
        real = self.block_of("dwg_real_samples")
        self.assertIn("when: manual", real, "真实样本 job 必须 when: manual（Spec §5）")
        self.assertIn("allow_failure: false", real,
                      "真实样本 job 不许 allow_failure（Spec §5）")
        self.assertIn("dwg_sample_e2e.py", real,
                      "真实样本 job 只许调 tech_app/tools/dwg_sample_e2e.py（Spec §5）")
        contract = self.block_of("python_contract")
        self.assertNotIn("CPQ_DWG_REAL_SAMPLES", contract,
                         "python_contract 不许跑真实样本冒烟（Spec §5）")
        self.assertNotIn("dwg_sample_e2e", contract,
                         "python_contract 不许调真实样本脚本（Spec §5）")

    def test_e34_gate_items_match_the_frozen_manifest(self):
        payload, _ = self.gate_json()
        actual = tuple((item["id"], item["kind"]) for item in payload["items"])
        self.assertEqual(actual, GATE_ITEMS,
                         "门禁清单必须与 Spec §7 的 18 项逐项一致（id/kind 都冻结）")

    def test_e35_exit_code_follows_the_verdict(self):
        payload, result = self.gate_json()
        expected_zero = payload["verdict"] == "go"
        self.assertEqual(result.returncode == 0, expected_zero,
                         "退出码必须与 verdict 一致（Spec §6）：verdict=%r rc=%r"
                         % (payload["verdict"], result.returncode))
        _, broken = self.gate_json(extra=["--ack", "not_a_real_item=someone"])
        self.assertEqual(broken.returncode, 2,
                         "未知 --ack id 必须是用法错误退出码 2（Spec §6）")

    def test_e36_skip_is_allowed_in_ci_but_fails_in_production(self):
        env = {"DWG_CONVERTER_PROVIDER": "none", "DWG_CONVERTER_BINARY": ""}
        ci_payload, _ = self.gate_json(env="ci", env_vars=env)
        prod_payload, _ = self.gate_json(env="production", env_vars=env)
        pick = lambda payload, item_id: next(  # noqa: E731
            item for item in payload["items"] if item["id"] == item_id)
        ci_item = pick(ci_payload, "converter_version_pinned")
        prod_item = pick(prod_payload, "converter_version_pinned")
        self.assertEqual(ci_item["status"], "skip",
                         "CI 没有转换器时允许 skip，但必须写明原因（Spec §7/§6）")
        self.assertIn("转换器", str(ci_item.get("message") or ""))
        self.assertEqual(prod_item["status"], "fail",
                         "生产环境没有转换器必须判失败，不许 skip（Spec §6）")
        self.assertEqual(prod_payload["summary"]["skip"], 0,
                         "production 下 skip 一律算 fail（Spec §6）")

    def test_e37_manual_items_need_an_explicit_ack(self):
        payload, _ = self.gate_json()
        manual = [item for item in payload["items"] if item["kind"] == "manual"]
        self.assertTrue(manual, "必须存在人工门禁项（Spec §7）")
        for item in manual:
            self.assertEqual(item["status"], "manual_unacknowledged",
                             "未确认的人工项必须显示 manual_unacknowledged（Spec §6）")
        acked, _ = self.gate_json(extra=["--ack", "real_samples_e2e_passed=zhangzhen",
                                         "--ack", "converter_license=zhangzhen"])
        picked = next(item for item in acked["items"]
                      if item["id"] == "real_samples_e2e_passed")
        self.assertEqual(picked["status"], "acknowledged",
                         "--ack 必须把人工项标成 acknowledged（Spec §6）")
        self.assertGreaterEqual(acked["summary"]["acknowledged"], 2)
        self.assertEqual(acked["verdict"], "no_go",
                         "人工项确认不能盖住自动项失败（Spec §6）")

    def test_e38_the_gate_never_reads_dotenv_or_the_network(self):
        source = self.tool_source(GATE_TOOL, "Spec §6 部署门禁脚本")
        for token in ("dotenv", "load_dotenv", "\".env\"", "'.env'", "requests", "urllib",
                      "httpx", "aiohttp", "socket", "http.client"):
            self.assertNotIn(token, source,
                             "门禁脚本不许读 .env、不许联网（Spec §6）：%r" % token)
        sentinel = "CPQ_RED_SENTINEL_SECRET_VALUE"
        payload, result = self.gate_json(
            env_vars={"CPQ_SECRET_SENTINEL": sentinel,
                      "DWG_ACCEPTANCE_RECORD": "/definitely/not/here.json",
                      "HTTPS_PROXY": "http://127.0.0.1:9"})
        blob = json.dumps(payload, ensure_ascii=False) + (result.stdout or "")
        self.assertNotIn(sentinel, blob, "门禁输出不许带出环境里的密钥（Spec §6）")
        self.assertTrue(str(payload["verdict"]) in ("go", "no_go"))

    def test_e39_gate_report_is_markdown_and_clean(self):
        self.tool_source(GATE_TOOL, "Spec §6 部署门禁脚本")
        target = self.tmpdir("dwg-b6-gate-") / "report.md"
        result = run_tool([GATE_TOOL, "--env", "ci", "--report", target])
        self.assertTrue(target.is_file(),
                        "--report 必须写出 Markdown 报告（Spec §6/§10.1）：%s"
                        % ((result.stderr or "")[-300:],))
        text = target.read_text(encoding="utf-8")
        for anchor in ("DWG 支持最终验收报告", "判定", "能力声明"):
            self.assertIn(anchor, text, "报告缺少 %r（Spec §10.1）" % anchor)
        for token in PROHIBITED_TEXT:
            self.assertNotIn(token, text, "报告里不许出现 %r（Spec §6）" % token)
        self.assertEqual(result.returncode == 0, "NO-GO" not in text.upper(),
                         "报告判定必须与退出码一致（Spec §6）")


# --------------------------------------------------------------------------- #
# F. 上限、保留策略与重启恢复（Spec §8）
# --------------------------------------------------------------------------- #
class FLimitsAndRecovery(FinalAcceptanceCase):
    ITEMS = ({"id": "old", "created_at": "2026-08-20T23:59:59+00:00"},
             {"id": "edge", "created_at": "2026-08-21T00:00:00+00:00"},
             {"id": "fresh", "created_at": "2026-09-19T00:00:00+00:00"})
    NOW = "2026-09-20T00:00:00+00:00"

    def test_f40_limits_are_json_safe_and_reflect_the_live_config(self):
        self.use_env()
        mod = self.package()
        limits = mod.limits()
        self.assertEqual(set(limits), set(LIMIT_KEYS),
                         "limits() 的键闭集必须正好是 Spec §8 的十个配置名：%r"
                         % (sorted(set(limits) ^ set(LIMIT_KEYS)),))
        json.dumps(limits, allow_nan=False)
        for key, expected in LIMIT_DEFAULTS.items():
            self.assertEqual(limits[key], expected,
                             "%s 的默认值必须是 %r（Spec §8）" % (key, expected))
        self.use_env(CAD_CONVERTER_TIMEOUT_SECONDS="7", DWG_DISPATCH_ENABLED="true",
                     CAD_ARTIFACT_CLEANUP_ENABLED="true")
        live = mod.limits()
        self.assertEqual(live["CAD_CONVERTER_TIMEOUT_SECONDS"], 7,
                         "limits() 必须返回当前生效值（Spec §8）")
        self.assertIs(live["DWG_DISPATCH_ENABLED"], True, "开关类键必须返回布尔")
        self.assertIs(live["CAD_ARTIFACT_CLEANUP_ENABLED"], True)

    def test_f41_retention_plan_never_touches_the_disk(self):
        self.use_env()
        mod = self.package()

        def boom(*args, **kwargs):
            raise AssertionError("retention_plan() 不许碰磁盘（Spec §8）")

        with mock.patch("builtins.open", boom), \
                mock.patch("pathlib.Path.unlink", boom), \
                mock.patch("pathlib.Path.write_text", boom), \
                mock.patch("pathlib.Path.mkdir", boom), \
                mock.patch("shutil.rmtree", boom), \
                mock.patch("os.remove", boom):
            plan = mod.retention_plan(self.NOW, self.ITEMS)
        self.assertEqual(set(plan), RETENTION_PLAN_KEYS,
                         "retention_plan() 的键必须是 %r（Spec §8）"
                         % (sorted(RETENTION_PLAN_KEYS),))
        self.assertEqual(plan["policy_version"], "artifact-retention/1")
        self.assertEqual(plan["days"], 30, "days 必须回填当前生效值（Spec §8）")
        self.assertEqual(plan["keep"], [])
        json.dumps(plan, allow_nan=False)

    def test_f42_default_cleanup_never_expires_historical_artifacts(self):
        self.use_env()
        mod = self.package()
        plan = mod.retention_plan(self.NOW, self.ITEMS)
        self.assertEqual(plan["expire"], [],
                         "CAD_ARTIFACT_CLEANUP_ENABLED 非 true 时 expire 必须为空"
                         "（Spec §8：默认不动任何历史数据）")
        explicit = mod.retention_plan(self.NOW, self.ITEMS, days=1)
        self.assertEqual(explicit["expire"], [],
                         "显式 days 也不能绕过清理开关（Spec §8）")

    def test_f43_retention_boundaries_and_keep_list(self):
        self.use_env(CAD_ARTIFACT_CLEANUP_ENABLED="true", CAD_ARTIFACT_RETENTION_DAYS="30")
        mod = self.package()
        plan = mod.retention_plan(self.NOW, self.ITEMS)
        self.assertEqual([item["id"] for item in plan["expire"]], ["old"],
                         "只有严格早于 now-days 的产物才进 expire（Spec §8）")
        short = mod.retention_plan(self.NOW, self.ITEMS, days=1)
        self.assertEqual([item["id"] for item in short["expire"]], ["old", "edge"],
                         "days 覆盖必须生效，边界项仍然保留（Spec §8）")
        kept = mod.retention_plan(self.NOW, self.ITEMS, keep=("old",))
        self.assertEqual(kept["expire"], [], "keep 里的 id 永不进 expire（Spec §8）")
        self.assertIn("old", kept["keep"])

    def test_f44_recover_marks_interrupted_and_deletes_nothing(self):
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        payload = {"drawing_kind": "packaging_2d", "pipeline": "2d", "evidence": [1, 2]}
        meta.put_doc(pid, "dwg_dispatch", {"dispatch_version": DISPATCH_VERSION,
                                          "project_id": pid, "status": "running",
                                          "payload": payload})
        meta.put_doc(pid, "ir", {"ir_version": "cad-ir/1", "ir_id": "ir-0001"})
        before = {path: sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}
        mod.recover(pid)
        doc = meta.get_doc(pid, "dwg_dispatch")
        self.assertEqual(doc["status"], "interrupted",
                         "服务重启后 running 的分流判定必须标 interrupted（Spec §8）")
        self.assertEqual(doc.get("payload"), payload,
                         "recover() 只改状态，不许丢内容（Spec §8）")
        self.assertTrue(meta.get_doc(pid, "ir"),
                        "recover() 不许删其它文档位（Spec §8）")
        after = {path: sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}
        self.assertTrue(set(before) <= set(after),
                        "recover() 不许删任何既有文件（Spec §8）")
        mod.recover("不存在的项目")


# --------------------------------------------------------------------------- #
# G. 回滚与诚实（Spec §9/§10）
# --------------------------------------------------------------------------- #
class GRollbackAndHonesty(FinalAcceptanceCase):
    def chain(self, mod, pid, **kwargs):
        inputs = dict(manifest=self.manifest_doc(), ir=self.ir_doc(),
                      semantics=self.semantics_doc(layers=[{"name": "CUT", "role": "cut"}]))
        inputs.update(kwargs)
        classification = self.classify(mod, pid, **inputs)
        return inputs, classification, self.route(mod, pid, classification, **inputs)

    def test_g45_disabled_switch_returns_the_frozen_closed_values(self):
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        self.use_env(DWG_DISPATCH_ENABLED="false")
        inputs, classification, route = self.chain(mod, pid)
        self.assertEqual(route["pipeline"], "none",
                         "关开关后 route() 必须给 pipeline='none'（Spec §9）")
        self.assertEqual(route["blocked_by"], "DWG_DISPATCH_DISABLED")
        status = mod.status_for(pid)
        self.assertEqual(status["code"], "3d_unknown")
        self.assertEqual(status["pipeline"], "none")
        doc = mod.dispatch_document(pid, deps=self.deps(**inputs))
        self.assertEqual(doc.get("pipeline"), "none",
                         "关闭态 dispatch_document() 返回同形状的 none（Spec §9）")
        self.assertFalse(meta.get_doc(pid, "dwg_dispatch"),
                         "关闭态不许写分流文档（Spec §9）")

    def test_g46_rollback_keeps_history_and_stays_answerable(self):
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        self.use_env(DWG_DISPATCH_ENABLED="true")
        inputs, classification, route = self.chain(mod, pid)
        mod.dispatch_document(pid, deps=self.deps(**inputs))
        stored = meta.get_doc(pid, "dwg_dispatch")
        self.assertTrue(stored, "开启状态下必须先落库，才能验证回滚（Spec §9）")
        self.use_env(DWG_DISPATCH_ENABLED="false")
        cap = self.converter().capability()
        self.assertIn(cap["support_claim"], SUPPORT_CLAIMS,
                      "回滚必须让 support_claim 落到闭集内（Spec §9）")
        self.assertIsInstance(cap["dwg_supported"], bool)
        _, _, rolled_back = self.chain(mod, pid)
        self.assertEqual(rolled_back["pipeline"], "none")
        status = mod.status_for(pid)
        self.assertIn(status["code"], DRAWING3D_STATUS)
        self.assertFalse(status["fresh"],
                         "关闭分流后旧结论不许当当前结论（Spec §9）")
        self.assertEqual(meta.get_doc(pid, "dwg_dispatch"), stored,
                         "回滚不等于删除：既有分流文档必须原样保留（Spec §9）")

    def test_g47_migrate_refuses_to_guess_and_summarize_is_stable(self):
        mod = self.package()
        with self.assertRaises(ValueError):
            mod.migrate({"dispatch_version": "dwg-dispatch/999"})
        rebuilt = mod.migrate({})
        self.assertEqual(rebuilt.get("status"), "needs_rebuild",
                         "缺版本必须标记 needs_rebuild，不许硬读（Spec §9）")
        current = {"dispatch_version": DISPATCH_VERSION, "project_id": "p1",
                   "drawing_kind": "packaging_2d", "pipeline": "2d", "status": "2d_parsed"}
        self.assertEqual(mod.migrate(current), current, "当前版本原样返回（Spec §9）")
        summary = mod.summarize(current)
        self.assertTrue({"dispatch_version", "drawing_kind", "pipeline", "status"}
                        <= set(summary), "summarize() 缺键（Spec §1.1）")
        json.dumps(summary, allow_nan=False)

    def test_g48_claim_wording_is_locked_while_l4_is_missing(self):
        cap = self.converter().capability()
        self.assertFalse(cap["dwg_supported"], "缺 L4 证据时 dwg_supported 必须为假")
        self.assertNotEqual(cap["support_claim"], "supported")
        texts = [str(cap.get("message") or "")]
        self.tool_source(GATE_TOOL, "Spec §6 部署门禁脚本")
        report_path = self.tmpdir("dwg-b6-claim-") / "gate.md"
        run_tool([GATE_TOOL, "--env", "ci", "--report", report_path])
        if report_path.is_file():
            texts.append(report_path.read_text(encoding="utf-8"))
        self.tool_source(REPORT_TOOL, "Spec §4.2 金标工具")
        empty_baseline = self.tmpdir("dwg-b6-claim-base-")
        result = run_tool([REPORT_TOOL, "--verify", "--baseline-dir", empty_baseline,
                           "--json"])
        texts.append((result.stdout or "") + (result.stderr or ""))
        for text in texts:
            for phrase in CLAIM_PHRASES:
                self.assertNotIn(phrase, text,
                                 "缺 L4 证据时不许出现 %r（Spec §10.3）" % phrase)


# --------------------------------------------------------------------------- #
# H. 适配器契约（Spec §2.1/§5 L2）
# --------------------------------------------------------------------------- #
class HAdapterContract(FinalAcceptanceCase):
    def request(self, root):
        from tech_app.backend.services.cad_converter.adapters.base import ConversionRequest
        source = pathlib.Path(root) / "source.dwg"
        source.write_bytes(b"\x00" * 32)
        return ConversionRequest(project_id="p1", attachment_name="酒盒.dwg",
                                 drawing_version=1, source_path=source,
                                 source_sha256="a" * 64, source_format="dwg",
                                 detected_dwg_version="AC1027", output_dir=pathlib.Path(root),
                                 timeout_seconds=5.0, conversion_id="conv-0001")

    def test_h49_fake_and_real_adapters_share_the_three_d_contract(self):
        converter = self.converter()
        self.use_env(DWG_CONVERTER_PROVIDER="none")
        off = converter.capability()
        self.use_env(CAD_CONVERTER="fake", CAD_CONVERTER_ALLOW_SIMULATED="true",
                     APP_ENV="ci")
        fake = converter.capability()
        for name, cap in (("真实/缺省", off), ("fake", fake)):
            three_d = cap.get("three_d")
            self.assertIsInstance(three_d, dict,
                                  "%s 适配器都必须给出 three_d 段（Spec §2.1）" % name)
            self.assertEqual(set(three_d), THREE_D_STATUS_KEYS)
            self.assertIn(three_d["status"], THREE_D_STATUS_VALUES)
            self.assertEqual(bool(three_d["supported"]), bool(cap["three_d_conversion"]),
                             "%s：three_d.supported 必须与适配器声明一致（Spec §2.1）" % name)
            self.assertIn("acceptance", cap,
                          "%s 适配器都必须给出 acceptance 段（Spec §2.1）" % name)
        self.assertEqual(fake["support_claim"], "orchestration_only",
                         "fake 适配器永远只算编排能力（Spec §3.2）")

    def test_h50_three_d_probe_never_fakes_an_artifact(self):
        from tech_app.backend.services.cad_converter.adapters.fake import FakeAdapter
        mod = self.package()
        root = self.tmpdir("dwg-b6-3d-")
        request = self.request(root)
        present = FakeAdapter(three_d="present").convert_3d_if_supported(request)
        absent = FakeAdapter(three_d="not_present").convert_3d_if_supported(request)
        for evidence in (present, absent):
            self.assertIn(evidence["status"], THREE_D_CONVERT_STATUSES,
                          "三维探测回执的 status 越界（Spec §5 L2）：%r" % (evidence,))
            if evidence["status"] != "converted":
                self.assertIsNone(evidence["artifact_path"],
                                  "没有真转换成功就不许给产物路径（Spec §5 L2）")
        self.assertEqual(absent["status"], "not_present")
        manifest = self.manifest_doc(output_files=[])
        ir = self.ir_doc(entity_types=("3DSOLID",))
        classification = self.classify(mod, "p1", manifest=manifest, ir=ir,
                                       three_d_conversion=True)
        self.assertNotEqual(classification["drawing_kind"], "3d_convertible",
                            "没有真实产物的三维声明不许被判成 3d_convertible（Spec §3.2/§4）")


# --------------------------------------------------------------------------- #
# I. 服务集成（Spec §5 L3/§8）
# --------------------------------------------------------------------------- #
class IServiceIntegration(FinalAcceptanceCase):
    def test_i51_dispatch_persists_a_document_and_reports_it(self):
        self.use_env(DWG_DISPATCH_ENABLED="true")
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        inputs = dict(manifest=self.manifest_doc(output_files=[
            self.artifact(self.tmpdir("dwg-b6-art-"), role="step")]),
            ir=self.ir_doc(entity_types=("3DSOLID",)), semantics=None,
            three_d_conversion=True, step_available=True)
        doc = mod.dispatch_document(pid, deps=self.deps(**inputs))
        self.assertEqual(doc["dispatch_version"], DISPATCH_VERSION)
        self.assertTrue(meta.get_doc(pid, "dwg_dispatch"),
                        "分流结论必须落库到 dwg_dispatch 文档位（Spec §1.1）")
        self.assertIn("dwg_dispatch", tuple(store_mod.PARSE_STAGE_DOCS),
                      "dwg_dispatch 必须进 store.PARSE_STAGE_DOCS（Spec §1.1）")
        status = mod.status_for(pid)
        self.assertEqual(status["code"], "3d_converted_and_parsed",
                         "落库后 status_for() 必须复现同一结论（Spec §1.5）")
        self.assertTrue(status["fresh"], "刚落库的结论必须是 fresh（Spec §1.5）")

    def test_i52_recovery_keeps_the_project_answerable(self):
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        meta.put_doc(pid, "dwg_dispatch", {"dispatch_version": DISPATCH_VERSION,
                                          "project_id": pid, "status": "running",
                                          "pipeline": "3d", "drawing_kind": "3d_convertible"})
        mod.recover(pid)
        self.use_env(DWG_DISPATCH_ENABLED="true")
        status = mod.status_for(pid)
        self.assertIn(status["code"], DRAWING3D_STATUS)
        self.assertFalse(status["fresh"],
                         "被中断的旧结论不许当当前结论（Spec §8）")
        inputs = dict(manifest=self.manifest_doc(), ir=self.ir_doc(),
                      semantics=self.semantics_doc(layers=[{"name": "CREASE",
                                                            "role": "crease"}]))
        classification = self.classify(mod, pid, **inputs)
        route = self.route(mod, pid, classification, **inputs)
        self.assertEqual(route["status"], "2d_parsed",
                         "重启后重算必须能给出正常结论（Spec §8）")

    def test_i53_repeated_dispatch_is_idempotent(self):
        self.use_env(DWG_DISPATCH_ENABLED="true")
        mod = self.package()
        store_mod, meta, blob, root = self.memory_store()
        pid = self.project(store_mod)
        inputs = dict(manifest=self.manifest_doc(), ir=self.ir_doc(),
                      semantics=self.semantics_doc(layers=[{"name": "CUT", "role": "cut"}]))
        deps = self.deps(**inputs)
        first = mod.dispatch_document(pid, deps=deps)
        before = {path for path in root.rglob("*") if path.is_file()}
        second = mod.dispatch_document(pid, deps=deps)
        after = {path for path in root.rglob("*") if path.is_file()}
        self.assertEqual(first.get("content_hash"), second.get("content_hash"),
                         "同一输入重复分流必须给出同一 content_hash（Spec §8）")
        self.assertEqual(first.get("drawing_kind"), second.get("drawing_kind"))
        self.assertEqual(before, after,
                         "重复分流不许生成新文件/新文档位（Spec §8）")
        self.assertEqual(json.dumps(second, sort_keys=True, ensure_ascii=False),
                         json.dumps(mod.dispatch_document(pid, deps=deps), sort_keys=True,
                                    ensure_ascii=False),
                         "幂等：第三次结果仍与第二次一致（Spec §8）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
