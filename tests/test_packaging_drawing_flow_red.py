"""红测：DWG 图纸解析会话、右侧需求看板与包装业务流程贯通 —— DWG 支持第 5 批。

Spec：`docs/specs/dwg-semantics-agent-flow.md`

现状缺口（实测，不是推断）：
  · `tech_app/backend/services/` 下没有 `packaging_drawing_flow*`：全仓没有任何
    「文件预检 → DWG 转换 → CAD IR → 包装语义 → 字段写入 → 待确认 → 下游准备」的领域步骤链，
    会话里因此看不出"现在轮到哪一步"；
  · `/agent/event`（`main.py:2498`）只做「入队 + 幂等」，没有领域步骤语义；
  · 门禁基本只有"project exists"：`grep -rn "minimum_charge_policy" tech_app/backend/`
    只命中 `packaging_cost.py`（读取与标注），**没有任何一处用它拦下游**；
  · `grep -rn "source_versions\\|requirement_snapshot_version" tech_app/backend/` **零命中**
    —— 盒型 / BOM / 路线 / 成本 / 报价之间没有版本六元组；
  · 图纸维度的 stale 不存在：`packaging_match.load_box_match`（`:465-501`）与
    `packaging_route._stale_reasons`（`:398`）都只比需求字段。

依赖隔离（本批最重要的一条纪律）：
  第 3 批（`cad_ir`）与第 4 批（`packaging_semantics`）由**并行会话**实现中，落地状态会变。
  除 `I` 组外，本文件一律通过 `run_flow(..., deps={...})` 注入 fake 依赖，因此红全部落在
  **第 5 批自己的缺口**（服务不存在 / 门禁不存在 / 版本继承不存在 / stale 不传播），
  不会因为依赖没落地而误红。

每组对应的用户要求见 Spec §10.1。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import copy
import importlib
import importlib.util
import json
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FLOW = "tech_app.backend.services.packaging_drawing_flow"
FLOW_DIR = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow"
FLOW_FILE = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow.py"
MAIN = ROOT / "tech_app" / "backend" / "main.py"
STORE = "tech_app.backend.storage.store"
REQUIREMENT_SERVICE = "tech_app.backend.services.requirement_service"
PREFLIGHT = "tech_app.backend.services.file_preflight"
PACKAGING_COST = "tech_app.backend.services.packaging_cost"
PACKAGING_HANDOFF = "tech_app.backend.services.packaging_handoff"

FLOW_VERSION = "packaging-drawing-flow/1"
STEP_IDS = ("file_preflight", "dwg_convert", "cad_ir_parse", "packaging_semantics",
            "field_write", "pending_confirm", "downstream_prepare")
STEP_TITLES = {"file_preflight": "文件预检", "dwg_convert": "DWG 转换",
               "cad_ir_parse": "CAD 矢量解析", "packaging_semantics": "包装语义识别",
               "field_write": "字段写入", "pending_confirm": "待确认生成",
               "downstream_prepare": "后续任务准备"}
STEP_STATUSES = {"pending", "running", "completed", "failed", "blocked",
                 "unavailable", "skipped"}
FIELD_BOARD_STATES = {"written", "pending", "conflict", "missing", "preserved", "skipped"}
GATE_STAGES = ("box_match", "bom", "route", "cost", "quote_draft", "quote_publish")
BLOCKING_CODES = {"field_missing", "field_unconfirmed", "field_conflict", "unit_unconfirmed",
                  "box_match_not_confirmed", "bom_not_built", "route_not_confirmed",
                  "cost_not_built", "cost_gaps_unresolved", "minimum_charge_policy_unresolved"}
#: Spec §9 —— 本批新增的 2 条码（HTTP, retryable）。
NEW_ERROR_CODES = {"PACKAGING_GATE_BLOCKED": (409, True),
                   "PACKAGING_FLOW_DEPENDENCY_MISSING": (500, False)}
#: Spec §2.2 —— 依赖缝的闭集（九个）。
DEPENDENCIES = ("file_preflight", "cad_converter", "cad_ir", "packaging_semantics",
                "requirement_service", "packaging_match", "packaging_bom",
                "packaging_route", "packaging_cost")
#: 事件 kind 闭集（Spec §4.1）：只许用既有三种，不许新造 flow_step / flow_field。
EVENT_KINDS = {"user", "task", "session-note"}
#: Spec §4.4 —— 事件文本与摘要里不许出现的字样。
PROHIBITED_TEXT = ("/tmp/", "Traceback", "data:image", "base64", "thinking",
                   "BEGIN SECTION", "ezdxf", "stderr:", "API_KEY")
#: Spec §5.1 —— 门禁字段矩阵（与引擎常量对齐）。
GATE_FIELDS = {
    "box_match": ("inner_length", "inner_width", "inner_height", "closure_type"),
    "bom": ("inner_length", "inner_width", "inner_height", "face_paper_gsm"),
    "route": ("closure_type", "v_groove", "face_paper_gsm"),
    "cost": ("quote_quantity",),
    "quote_draft": (),
    "quote_publish": ("inner_length", "inner_width", "inner_height", "closure_type"),
}
FULLY_CONFIRMED = {"inner_length": 70.0, "inner_width": 40.0, "inner_height": 120.0,
                   "closure_type": "tuck", "face_paper_gsm": 350.0, "v_groove": False,
                   "quote_quantity": 1000.0}

SEMANTICS_VERSION = "packaging-semantics/1"
CAD_IR_VERSION = "cad-ir/1"
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"


def dwg_bytes(version: str = "AC1027", size: int = 4096) -> bytes:
    """最小可识别 DWG：头部 AC10xx + 0x80 处哨兵（第 1 批只按内容识别，本批够用）。"""
    data = bytearray(b"\x00" * size)
    data[0:6] = version.encode("ascii")
    data[0x80:0x80 + 11] = b"AcFssFcAJMB"
    return bytes(data)


def cad_ir_document(*, unit_status: str = "confirmed", ir_hash: str = "b" * 64,
                    drawing_version: int = 1) -> dict:
    """冻结形状的最小 CAD IR（本批只读 units / 摘要与证据）。"""
    return {
        "ir_version": CAD_IR_VERSION,
        "ir_id": "ir-0001",
        "ir_hash": ir_hash,
        "source": {"project_id": "", "drawing_version": drawing_version,
                   "source_sha256": "a" * 64, "conversion_id": "conv-0001"},
        "units": {"drawing_units": "mm" if unit_status == "confirmed" else "",
                  "unit_status": unit_status, "scale_to_mm": 1.0,
                  "unit_candidates": []},
        "layers": [{"name": "CUT", "color": 1, "line_type": "CONTINUOUS",
                    "visible": True, "frozen": False, "entity_count": 4,
                    "evidence_refs": ["ev:L:CUT"]}],
        "entities": [{"entity_id": "ent:model:10", "type": "LINE", "layer": "CUT",
                      "length": 70.0, "bbox": [0.0, 0.0, 70.0, 0.0],
                      "evidence_refs": ["ev:E:10"]}],
        "geometry": {"closed_outlines": [], "open_outlines": [], "holes": [],
                     "components": []},
        "dimensions": [{"dimension_id": "dim:1", "declared_value": 70.0,
                        "measured_value": 70.0, "delta": 0.0, "tolerance": 0.5,
                        "unit": "mm", "layer": "DIM",
                        "evidence_refs": ["ev:D:34"]}],
        "texts": [{"text_id": "txt:1", "raw_text": "白卡纸 350g",
                   "normalized_text": "白卡纸350g", "layer": "TEXT",
                   "evidence_refs": ["ev:T:1"]}],
        "evidence": {
            "ev:L:CUT": {"kind": "layer", "handle": "", "layer": "CUT", "spatial": None},
            "ev:E:10": {"kind": "entity", "handle": "10", "layer": "CUT",
                        "spatial": [0.0, 0.0]},
            "ev:D:34": {"kind": "dimension", "handle": "34", "layer": "DIM",
                        "spatial": [0.0, 0.0]},
            "ev:T:1": {"kind": "entity", "handle": "1", "layer": "TEXT",
                       "spatial": [0.0, 0.0]},
        },
        "warnings": [], "unsupported": [],
        "stats": {"layer_total": 1, "entity_total": 1, "dimension_total": 1,
                  "text_total": 1},
    }


def semantics_document(*, fields: dict | None = None, unit_status: str = "confirmed",
                       semantics_hash: str = "c" * 64,
                       drawing_version: int = 1) -> dict:
    """冻结形状的最小包装语义文档（第 4 批契约）。"""
    default_fields = {
        "inner_length": {"origin": "confirmed_from_cad", "status": "confirmed",
                         "value": 70.0, "confidence": 1.0, "evidence_level": "STRONG",
                         "evidence_refs": ["ev:D:34"], "conflicts": [],
                         "alternatives": [], "unit": "mm"},
        "inner_width": {"origin": "inferred_from_geometry", "status": "needs_confirmation",
                        "value": 40.0, "confidence": 0.8, "evidence_level": "MODERATE",
                        "evidence_refs": ["ev:E:10"], "conflicts": [],
                        "alternatives": [], "unit": "mm"},
        "inner_height": {"origin": "missing", "status": "missing", "value": None,
                         "confidence": 0.0, "evidence_level": "NONE",
                         "evidence_refs": [], "conflicts": [], "alternatives": [],
                         "unit": ""},
        "face_paper": {"origin": "missing", "status": "missing", "value": None,
                       "confidence": 0.0, "evidence_level": "NONE",
                       "evidence_refs": [], "conflicts": [], "alternatives": [],
                       "unit": ""},
    }
    return {
        "semantics_version": SEMANTICS_VERSION,
        "semantics_id": "sem-0001",
        "semantics_hash": semantics_hash,
        "source": {"project_id": "", "ir_id": "ir-0001", "ir_hash": "b" * 64,
                   "ir_version": CAD_IR_VERSION, "drawing_version": drawing_version,
                   "conversion_status": "success_with_warnings",
                   "warning_count": 252, "error_count": 3, "template": "generic",
                   "rules_version": "rules1234567", "rules_path_kind": "default",
                   "unit_status": unit_status},
        "layers": [], "roles_summary": {},
        "outline": {"boundary_candidates": [], "bleed_candidates": [], "windows": [],
                    "holes": [], "panel": {"panel_count": 1, "multi_up": False,
                                           "candidates": [], "evidence_refs": []}},
        "dimensions": {"conflicts": [], "measured_total": 0, "declared_total": 0},
        "texts": {"material_candidates": [], "process_candidates": [],
                  "title_block": {}, "unit_hints": []},
        "box_candidates": [], "fields": copy.deepcopy(default_fields if fields is None else fields),
        "unresolved": [{"field": "inner_height", "reason": "no_rule_matched",
                        "status": "missing"}],
        "model_assist": {"used": False, "status": "unavailable", "calls": 0, "model": "",
                         "stable_error_code": "", "preview_kind": "none",
                         "evidence_level": "NONE", "extra_fields_dropped": []},
        "warnings": [], "stats": {"layer_total": 1, "cut_layer_total": 1,
                                  "crease_layer_total": 0, "boundary_candidate_total": 0,
                                  "hole_total": 0, "conflict_total": 0,
                                  "unresolved_total": 1, "box_candidate_total": 0},
        "reviewable": True,
    }


def conversion_manifest(*, status: str = "success_with_warnings", conversion_id: str = "conv-0001",
                        drawing_version: int = 1, error_code: str = "",
                        warning_count: int = 252, error_count: int = 3) -> dict:
    """冻结形状的最小转换 manifest（第 2 批契约）。"""
    return {
        "conversion_id": conversion_id,
        "project_id": "",
        "source_sha256": "a" * 64,
        "source_format": "dwg",
        "detected_dwg_version": "AC1027",
        "converter_name": "libredwg",
        "converter_version": "0.14",
        "conversion_options": {"provider": "libredwg"},
        "output_files": [{"role": "dxf", "name": "converted.dxf", "size": 1024,
                          "sha256": "d" * 64}],
        "output_sha256": {"dxf": "d" * 64},
        "warnings": [], "warning_count": warning_count, "error_count": error_count,
        "started_at": "2026-09-20T00:00:00+08:00", "finished_at": "2026-09-20T00:00:01+08:00",
        "status": status, "error_code": error_code, "drawing_version": drawing_version,
        "is_simulated": False,
        "quality": {"verified": True, "entity_count": 6711, "layer_count": 8,
                    "checks": ["exit_code", "non_empty", "structure", "entities", "layers"],
                    "problem": ""},
    }


class _StopCall(Exception):
    """哨兵：本批不许发生的调用（模型 / 网络）真的发生了。"""


class FakeConverter:
    """第 2 批 `cad_converter` 的替身（同名同参，Spec §2.2.1）。"""

    name = "fake-converter"

    def __init__(self, *, manifest: dict | None = None, error: Exception | None = None,
                 available: bool = True):
        self.manifest = manifest if manifest is not None else conversion_manifest()
        self.error = error
        self.available = available
        self.calls: list[dict] = []

    def capability(self) -> dict:
        return {"available": self.available, "provider": "fake", "binary": "fake",
                "converter_version": "0.14", "dwg_supported": False,
                "support_claim": "conversion_available", "warnings": []}

    def convert_drawing(self, project_id, filename, content, **kwargs):
        self.calls.append({"project_id": project_id, "filename": filename,
                           "size": len(content), **kwargs})
        if self.error is not None:
            raise self.error
        manifest = copy.deepcopy(self.manifest)
        manifest["project_id"] = project_id
        manifest["drawing_version"] = int(kwargs.get("drawing_version") or 1)
        self.manifest = manifest
        return manifest

    def latest_manifest(self, project_id, **kwargs):
        return copy.deepcopy(self.manifest) if self.available else None


class FakeCadIr:
    """第 3 批 `cad_ir` 的替身（同名同参，Spec §2.2.1）。"""

    name = "fake-cad-ir"

    def __init__(self, *, ir: dict | None = None, error: Exception | None = None,
                 available: bool = True):
        self.ir = ir if ir is not None else cad_ir_document()
        self.error = error
        self.available = available
        self.calls: list[dict] = []

    def parse_conversion(self, project_id, **kwargs):
        self.calls.append({"project_id": project_id, **kwargs})
        if self.error is not None:
            raise self.error
        self.ir["source"]["project_id"] = project_id
        self.ir["source"]["drawing_version"] = int(kwargs.get("drawing_version") or 1)
        return copy.deepcopy(self.ir)

    def load_ir(self, project_id, ir_id=None):
        return copy.deepcopy(self.ir) if self.available else None

    def summarize(self, ir):
        units = (ir or {}).get("units") or {}
        stats = (ir or {}).get("stats") or {}
        return {"ir_id": (ir or {}).get("ir_id", ""), "ir_hash": (ir or {}).get("ir_hash", ""),
                "ir_version": (ir or {}).get("ir_version", ""),
                "layer_total": stats.get("layer_total", 0),
                "entity_total": stats.get("entity_total", 0),
                "unit_status": units.get("unit_status", "")}


class FakeSemantics:
    """第 4 批 `packaging_semantics` 的替身（同名同参，Spec §2.2.1）。

    `apply_to_requirement` 在这里按第 4 批的**最小可见契约**实现：只写 `status=="confirmed"`
    的字段、维护 `field_provenance` / `field_sources`、不覆盖 `manual` 来源。
    第 4 批真实实现落地后，`I3` 会用真实模块跑一遍同断言，防止两边契约漂移。
    """

    name = "fake-semantics"

    def __init__(self, *, semantics: dict | None = None, error: Exception | None = None,
                 available: bool = True):
        self.semantics = semantics if semantics is not None else semantics_document()
        self.error = error
        self.available = available
        self.analyze_calls: list[dict] = []
        self.apply_calls: list[dict] = []

    def analyze_conversion(self, project_id, ir=None, **kwargs):
        self.analyze_calls.append({"project_id": project_id, "ir_id": (ir or {}).get("ir_id"),
                                   **kwargs})
        if self.error is not None:
            raise self.error
        doc = copy.deepcopy(self.semantics)
        doc["source"]["project_id"] = project_id
        doc["source"]["ir_hash"] = (ir or {}).get("ir_hash", "")
        doc["source"]["unit_status"] = ((ir or {}).get("units") or {}).get("unit_status", "")
        self.semantics = doc
        return copy.deepcopy(doc)

    def apply_to_requirement(self, project_id, semantics, *, accept=(), author="system"):
        from tech_app.backend.storage import store as store_mod
        self.apply_calls.append({"project_id": project_id,
                                 "fields": sorted((semantics.get("fields") or {}).keys()),
                                 "accept": list(accept or ()), "author": author})
        doc = store_mod.load_requirement(project_id) or {}
        data = dict(doc.get("data") or {})
        sources = dict(data.get("field_sources") or {})
        provenance = dict(data.get("field_provenance") or {})
        written: list[str] = []
        preserved: list[str] = []
        for key, entry in (semantics.get("fields") or {}).items():
            if (entry or {}).get("status") != "confirmed":
                continue
            if sources.get(key) == "manual" or (provenance.get(key) or {}).get(
                    "origin") == "user_confirmed":
                preserved.append(key)
                continue
            data[key] = (entry or {}).get("value")
            provenance[key] = {"origin": (entry or {}).get("origin", ""),
                               "status": (entry or {}).get("status", ""),
                               "value": (entry or {}).get("value"),
                               "confidence": (entry or {}).get("confidence", 0.0),
                               "evidence_level": (entry or {}).get("evidence_level", ""),
                               "evidence_refs": list((entry or {}).get("evidence_refs") or []),
                               "conflicts": list((entry or {}).get("conflicts") or []),
                               "alternatives": list((entry or {}).get("alternatives") or []),
                               "ir_id": (semantics.get("source") or {}).get("ir_id", ""),
                               "ir_hash": (semantics.get("source") or {}).get("ir_hash", "")}
            sources[key] = "attachment"
            written.append(key)
        data["field_sources"] = sources
        data["field_provenance"] = provenance
        doc["data"] = data
        store_mod.save_requirement(project_id, doc, author=author)
        return {"written": written, "preserved": preserved, "accept": list(accept or ())}

    def summarize(self, semantics):
        return {"semantics_id": (semantics or {}).get("semantics_id", ""),
                "semantics_hash": (semantics or {}).get("semantics_hash", ""),
                "semantics_version": (semantics or {}).get("semantics_version", ""),
                "stats": (semantics or {}).get("stats", {}),
                "unresolved_total": len((semantics or {}).get("unresolved") or [])}


class FlowCase(unittest.TestCase):
    maxDiff = None

    # ------------------------------------------------------------------ 加载
    def module(self):
        try:
            return importlib.import_module(FLOW)
        except ModuleNotFoundError as exc:
            name = str(getattr(exc, "name", "") or "")
            if name.endswith("file_preflight"):
                self.fail("依赖 DWG 第 1 批（`file_preflight` 未实现）")
            self.fail("缺少 tech_app/backend/services/packaging_drawing_flow/（本批 Spec §2）：%s" % exc)

    def submodule(self, name):
        self.module()
        try:
            return importlib.import_module("%s.%s" % (FLOW, name))
        except ModuleNotFoundError as exc:
            self.fail("缺少 %s.%s（本批 Spec §2）：%s" % (FLOW, name, exc))

    def package_files(self):
        if not FLOW_DIR.exists():
            self.fail("缺少 packaging_drawing_flow/（本批 Spec §2）")
        files = sorted(FLOW_DIR.rglob("*.py"))
        if not files and FLOW_FILE.exists():
            files = [FLOW_FILE]
        self.assertTrue(files, "packaging_drawing_flow/ 里没有 Python 文件（Spec §2）")
        return files

    def flow_source(self):
        return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                         for path in self.package_files())

    # ------------------------------------------------------------------ 存储
    def memory_store(self):
        """真 `store` + 临时目录上的 Json/Local 后端（不碰真实运行数据）。"""
        store_mod = importlib.import_module(STORE)
        from tech_app.backend.storage.blob_backend import LocalBlobBackend
        from tech_app.backend.storage.meta_backend import JsonMetaBackend
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="flow-red-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        meta, blob = JsonMetaBackend(tmp), LocalBlobBackend(tmp)
        for attr, value in (("_meta", lambda: meta), ("_blob", lambda: blob)):
            patch = mock.patch.object(store_mod, attr, value)
            patch.start()
            self.addCleanup(patch.stop)
        return store_mod, meta, blob, tmp

    def project(self, *, filename=WINE_BOX, content=None, requirement_data=None,
                requirement_status="draft", store_mod=None):
        store_mod = store_mod or self.memory_store()[0]
        pid = store_mod.create_project(filename, dwg_bytes() if content is None else content,
                                       owner="tester", owner_display_name="测试员")
        data = {"industry": "packaging"}
        data.update(requirement_data or {})
        store_mod.save_requirement(pid, {
            "project_id": pid, "requirement_no": "REQ-1", "title": "包装需求",
            "status": requirement_status, "data": data, "created_by": "tester",
            "history": [], "waivers": [],
        })
        return pid

    def confirm_fields(self, store_mod, pid, fields):
        """把一批字段标成人工确认（模拟需求确认页的动作，不改实现）。"""
        doc = store_mod.load_requirement(pid) or {}
        data = dict(doc.get("data") or {})
        sources = dict(data.get("field_sources") or {})
        provenance = dict(data.get("field_provenance") or {})
        for key, value in fields.items():
            data[key] = value
            sources[key] = "manual"
            provenance[key] = {"origin": "user_confirmed", "status": "confirmed",
                               "value": value, "confidence": 1.0,
                               "evidence_level": "STRONG", "evidence_refs": ["ev:U:1"],
                               "conflicts": [], "alternatives": [], "ir_id": "", "ir_hash": ""}
        data["field_sources"] = sources
        data["field_provenance"] = provenance
        doc["data"] = data
        store_mod.save_requirement(pid, doc)
        return doc

    def requirement_data(self, store_mod, pid):
        return ((store_mod.load_requirement(pid) or {}).get("data") or {})

    # ------------------------------------------------------------------ 依赖缝
    def deps(self, *, converter=None, cad_ir=None, semantics=None, unit_status="confirmed",
             ir=None, sem=None, engines=None):
        preflight = importlib.import_module(PREFLIGHT)
        requirement_service = importlib.import_module(REQUIREMENT_SERVICE)
        engines = engines if engines is not None else FakeEngines()
        return {
            "file_preflight": preflight,
            "requirement_service": requirement_service,
            "cad_converter": converter if converter is not None else FakeConverter(),
            "cad_ir": cad_ir if cad_ir is not None else FakeCadIr(
                ir=ir if ir is not None else cad_ir_document(unit_status=unit_status)),
            "packaging_semantics": semantics if semantics is not None else FakeSemantics(
                semantics=sem if sem is not None else semantics_document(unit_status=unit_status)),
            "packaging_match": engines,
            "packaging_bom": engines,
            "packaging_route": engines,
            "packaging_cost": engines,
        }

    def start(self, pid, *, prompt="解析酒盒.dwg并提取包装结构和需求字段", **deps):
        module = self.module()
        return module.run_flow(pid, prompt=prompt, actor="tester", deps=self.deps(**deps))

    # ------------------------------------------------------------------ 读回
    def events(self, store_mod, pid):
        return store_mod.load_session_events(pid)

    def task_cards(self, store_mod, pid):
        return [row for row in self.events(store_mod, pid)
                if str(row.get("kind")) == "task"]

    def notes(self, store_mod, pid):
        return [row for row in self.events(store_mod, pid)
                if str(row.get("kind")) == "session-note"]

    def user_entries(self, store_mod, pid):
        return [row for row in self.events(store_mod, pid)
                if str(row.get("kind")) == "user"]

    def card_of(self, store_mod, pid, step_id):
        for row in self.task_cards(store_mod, pid):
            task = row.get("task") or {}
            if str(task.get("id") or "").endswith(":" + step_id):
                return row
        return None

    def step_of(self, state, step_id):
        for row in (state or {}).get("steps") or []:
            if str(row.get("step_id")) == step_id:
                return row
        self.fail("flow_state 里没有步骤 %s（Spec §3）" % step_id)

    # ------------------------------------------------------------------ 断言帮手
    def assert_json_safe(self, value, why="结果"):
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            self.fail("%s 必须 JSON 安全（无 NaN/Infinity/bytes/Path）：%s" % (why, exc))

    def assert_no_prohibited_text(self, value, why="事件文本"):
        blob = json.dumps(value, ensure_ascii=False)
        for word in PROHIBITED_TEXT:
            self.assertNotIn(word, blob, "%s 不许出现 %r（Spec §4.4）" % (why, word))
        for match in re.findall(r"[A-Za-z]:\\\\|/Users/|/home/", blob):
            self.fail("%s 不许出现绝对路径（Spec §4.4）：%r" % (why, match))

    def expect_error(self, code, fn, *args, **kwargs):
        module = self.module()
        err_type = getattr(module, "DrawingFlowError", None)
        self.assertTrue(err_type is not None, "packaging_drawing_flow 必须导出 DrawingFlowError（Spec §5.3）")
        expected = NEW_ERROR_CODES[code]
        try:
            result = fn(*args, **kwargs)
        except err_type as exc:
            self.assertEqual(getattr(exc, "stable_error_code", None), code,
                             "错误码必须是 %s（Spec §9）" % code)
            self.assertEqual(getattr(exc, "http_status", None), expected[0], code)
            self.assertEqual(getattr(exc, "retryable", None), expected[1], code)
            message = str(getattr(exc, "message", "") or str(exc))
            self.assertTrue(message, "%s 必须有中文用户文案" % code)
            for word in ("Traceback", "File \"", "/Users/", "/home/"):
                self.assertNotIn(word, message, "%s 的用户文案不许含堆栈或绝对路径" % code)
            return exc
        except BaseException as exc:  # noqa: BLE001 - 只做分类
            self.fail("期望 %s，实际抛出 %s: %s" % (code, type(exc).__name__, exc))
        self.fail("期望 %s，实际成功返回：%r" % (code, result))

    def imports_of(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                # 相对 import 把层级补回去：`from .model import X` → `.model`。
                found.add("." * int(node.level or 0) + (node.module or ""))
        return found

    def main_route_source(self, path_suffix):
        """按装饰器路径截出某个路由函数的源码（用于读写权限分开断言）。"""
        text = MAIN.read_text(encoding="utf-8", errors="replace")
        marker = '@app.'
        blocks = []
        for match in re.finditer(r"@app\.(get|post|put|delete)\(\"([^\"]*)\"", text):
            start = match.start()
            nxt = text.find("\n@app.", match.end())
            blocks.append((match.group(2), text[start:nxt if nxt > 0 else len(text)]))
        for route, block in blocks:
            if route.endswith(path_suffix):
                return route, block
        self.fail("main.py 里没有以 %r 结尾的路由（Spec §8）" % path_suffix)


class AFlowChainOrder(FlowCase):
    """A 组：步骤链闭集、顺序、状态机、依赖缝、安全（Spec §2/§3，要求 1/2/3）。"""

    def test_a1_user_bubble_precedes_the_first_step_and_is_not_duplicated(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        state = self.start(pid)
        rows = self.events(store_mod, pid)
        users = [row for row in rows if str(row.get("kind")) == "user"]
        self.assertEqual(len(users), 1, "同一次解析只许一条用户气泡（Spec §4.1/§7）")
        self.assertTrue(str(users[0].get("text") or "").strip(), "用户气泡必须有正文")
        cards = self.task_cards(store_mod, pid)
        self.assertTrue(cards, "必须产生执行卡（Spec §4.1）")
        self.assertLess(int(users[0].get("seq") or 0), int(cards[0].get("seq") or 0),
                        "用户气泡必须排在第一步执行卡之前（要求 1）")
        self.module().run_flow(pid, prompt="解析酒盒.dwg并提取包装结构和需求字段",
                               actor="tester", deps=self.deps(), run_id=state["run_id"])
        self.assertEqual(len(self.user_entries(store_mod, pid)), 1,
                         "同一 run 重跑不得再出一条气泡（Spec §7）")

    def test_a2_every_step_is_one_tool_card_with_the_frozen_id(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        state = self.start(pid)
        cards = self.task_cards(store_mod, pid)
        self.assertEqual(len(cards), len(STEP_IDS),
                         "七步各一张卡，不多不少（要求 2）：%r" % [c.get("key") for c in cards])
        for row in cards:
            task = row.get("task") or {}
            self.assertTrue(str(task.get("id") or "").startswith("flow:%s:" % state["run_id"]),
                            "task.id 必须是 flow:<run_id>:<step_id>（Spec §4.1）：%r" % task.get("id"))
            self.assertTrue(str(task.get("label") or "").strip(), "任务卡必须有中文名")
            self.assertIn(str(task.get("status") or ""), STEP_STATUSES, "卡片状态在闭集内")
        kinds = {str(row.get("kind")) for row in self.events(store_mod, pid)}
        self.assertTrue(kinds <= EVENT_KINDS,
                        "只许用既有 kind（Spec §4.1）：%r" % sorted(kinds - EVENT_KINDS))

    def test_a3_step_order_is_frozen_and_seq_is_strictly_ascending(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        self.start(pid)
        cards = self.task_cards(store_mod, pid)
        order = [str((row.get("task") or {}).get("id") or "").rsplit(":", 1)[-1] for row in cards]
        self.assertEqual(order, list(STEP_IDS),
                         "执行顺序必须与 STEP_IDS 一致（要求 3）：%r" % order)
        seqs = [int(row.get("seq") or 0) for row in cards]
        self.assertEqual(seqs, sorted(set(seqs)), "seq 必须严格递增（Spec §3.2）")

    def test_a4_step_closed_sets_match_the_spec(self):
        module = self.module()
        self.assertEqual(tuple(getattr(module, "STEP_IDS", ())), STEP_IDS, "StepB：步骤闭集")
        self.assertEqual(dict(getattr(module, "STEP_TITLES", {})), STEP_TITLES, "StepB：中文名")
        self.assertEqual(set(getattr(module, "STEP_STATUSES", ())), STEP_STATUSES, "StepB：状态闭集")
        self.assertEqual(set(getattr(module, "FIELD_BOARD_STATES", ())), FIELD_BOARD_STATES,
                         "StepB：看板状态闭集")
        self.assertEqual(tuple(getattr(module, "GATE_STAGES", ())), GATE_STAGES, "StepB：门禁段闭集")
        self.assertEqual(str(getattr(module, "FLOW_VERSION", "")), FLOW_VERSION, "StepB：版本号")

    def test_a5_steps_plan_is_the_execution_order(self):
        module = self.module()
        plan = module.steps()
        self.assertEqual([row.get("step_id") for row in plan], list(STEP_IDS), "steps() 就是执行顺序")
        for index, row in enumerate(plan):
            self.assertEqual(int(row.get("index", -1)), index, "index 从 0 递增")
            self.assertIn(str(row.get("title") or ""), STEP_TITLES.values(), "标题在闭集内")

    def test_a6_capability_lists_the_dependency_seams(self):
        module = self.module()
        cap = module.capability()
        self.assertIn("dependencies", cap, "capability() 必须报依赖状态（Spec §2.1）")
        self.assertEqual(set(cap["dependencies"]), set(DEPENDENCIES),
                         "依赖名就是 Spec §2.2 的九个（多一个少一个都算越界）")

    def test_a7_flow_state_is_json_safe_and_has_no_raw_drawing_content(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        self.start(pid)
        state = self.module().flow_state(pid)
        self.assert_json_safe(state, "flow_state")
        self.assert_no_prohibited_text(state, "flow_state")
        for row in self.events(store_mod, pid):
            self.assert_no_prohibited_text(row, "会话条目")

    def test_a8_every_step_detail_is_safe_and_evidence_bearing(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        state = self.start(pid)
        for row in state["steps"]:
            detail = row.get("detail") or {}
            self.assert_json_safe(detail, "步骤 %s 的 detail" % row.get("step_id"))
        step = self.step_of(state, "field_write")
        self.assertIn("fields", step.get("detail") or {},
                      "字段写入步骤必须回带逐字段结论（Spec §4.3）")

    def test_a9_missing_dependency_is_unavailable_not_completed(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        deps = self.deps()
        deps["cad_ir"] = None
        state = module.run_flow(pid, prompt="解析酒盒.dwg并提取包装结构和需求字段",
                                actor="tester", deps=deps)
        step = self.step_of(state, "cad_ir_parse")
        self.assertEqual(step.get("status"), "unavailable",
                         "依赖未就绪必须是 unavailable，不许假装完成（Spec §3.1）")
        self.assertEqual(str((step.get("detail") or {}).get("dependency") or ""), "cad_ir")
        self.assertEqual(str(step.get("error_code") or ""), "PACKAGING_FLOW_DEPENDENCY_MISSING")
        for later in ("packaging_semantics", "field_write", "pending_confirm", "downstream_prepare"):
            self.assertEqual(self.step_of(state, later).get("status"), "pending",
                             "失败/不可用之后的步骤必须保持 pending（Spec §3.2）")
        self.assertNotEqual(state.get("status"), "completed", "整条 run 不许标成完成")
        self.assertNotIn("parsed", (store_mod.load_meta(pid) or {}).get("stages") or {},
                         "解析失败不许写 stages.parsed")

    def test_a10_the_flow_package_never_reimplements_conversion_or_parsing(self):
        allowed = {"__future__", "collections", "copy", "datetime", "hashlib", "importlib",
                   "json", "os", "pathlib", "re", "time", "typing", "unicodedata", "uuid"}
        for path in self.package_files():
            for name in self.imports_of(path):
                if name.startswith("."):
                    continue  # 包内相对 import：同包文件，天然允许
                head = name.split(".")[0]
                self.assertNotIn(head, {"vision", "step_import", "requests", "ezdxf",
                                        "subprocess", "httpx"},
                                 "%s 不许 import %s（Spec §2.3）" % (path.name, name))
                if name.startswith("tech_app"):
                    continue
                self.assertTrue(head in allowed,
                                "%s 引入了未登记的外部 import %s" % (path.name, name))
        source = self.flow_source()
        for word in ("ezdxf", "struct.unpack", "subprocess", "UploadFile", "requests."):
            self.assertNotIn(word, source, "流层不许出现 %r（Spec §2.2.1/§2.3）" % word)

    def test_a11_the_whole_chain_calls_no_model(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        client = importlib.import_module("tech_app.backend.services.claude_client")
        calls: list = []

        def stop(*args, **kwargs):
            calls.append((args, kwargs))
            raise _StopCall("本批链路不许调模型")

        with mock.patch.object(client, "run", stop):
            self.start(pid)
        self.assertEqual(calls, [], "编排链路必须完全离线（Spec §3.2 第 6 条）")

    def test_a12_summarize_is_a_small_safe_summary(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        state = self.start(pid)
        summary = self.module().summarize(state)
        self.assert_json_safe(summary, "summarize")
        text = json.dumps(summary, ensure_ascii=False)
        self.assertLess(len(text), 4000, "摘要必须是小摘要，不许塞实体明细（Spec §2.1）")
        for word in ("entities", "evidence_refs\": [\"ev:E", "BEGIN SECTION", "base64"):
            self.assertNotIn(word, text, "摘要里不许出现 %r" % word)


class BFieldBoardSync(FlowCase):
    """B 组：字段写入与右侧看板的逐步同步、来源与证据、未确认不落值（Spec §4.3，要求 4/5/6/26）。"""

    def run_until(self, pid, module, deps, upto, *, prompt="解析酒盒.dwg并提取包装结构和需求字段"):
        state = module.start(pid, prompt=prompt, actor="tester")
        run_id = state["run_id"]
        for step_id in STEP_IDS:
            if step_id == upto:
                break
            module.run_step(pid, step_id, actor="tester", run_id=run_id, deps=deps)
        return run_id

    def test_b4_board_is_written_during_the_field_step_not_at_the_end(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module, deps = self.module(), self.deps()
        run_id = self.run_until(pid, module, deps, "field_write")
        self.assertNotIn("inner_length", self.requirement_data(store_mod, pid),
                         "字段写入步骤之前，看板不许出现来自图纸的字段（不许提前写）")
        step = module.run_step(pid, "field_write", actor="tester", run_id=run_id, deps=deps)
        self.assertIn(step.get("status"), {"completed", "blocked"},
                      "字段写入步骤应正常结束：%r" % step.get("status"))
        self.assertEqual(self.requirement_data(store_mod, pid).get("inner_length"), 70.0,
                         "确认过的字段必须在 field_write 这一步就写进看板（要求 4）")
        notes = [row for row in self.notes(store_mod, pid) if row.get("field")]
        keys = [str((row.get("field") or {}).get("key")) for row in notes]
        self.assertIn("inner_length", keys, "每个字段必须有一条自己的过程行（Spec §4.3）")
        card = self.card_of(store_mod, pid, "field_write")
        self.assertTrue(card, "字段写入必须有执行卡")
        self.assertGreater(min(int(row.get("seq") or 0) for row in notes),
                           int(card.get("seq") or 0),
                           "字段过程行必须排在字段写入卡之后")
        self.assertIsNone(self.card_of(store_mod, pid, "downstream_prepare"),
                          "还没跑下游准备，就不该有那张卡（不许提前建）")

    def test_b5_every_field_row_carries_source_status_and_evidence(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        state = self.start(pid)
        notes = [row for row in self.notes(store_mod, pid) if row.get("field")]
        self.assertTrue(notes, "字段写入必须逐字段播过程行（Spec §4.3）")
        for row in notes:
            field = row["field"]
            self.assertIn(field.get("board"), FIELD_BOARD_STATES, "board 在闭集内")
            self.assertTrue(str(field.get("origin") or ""), "必须回带来源（要求 5）")
            self.assertIn(str(field.get("status") or ""),
                          {"confirmed", "needs_confirmation", "conflict", "missing"})
            self.assertIsInstance(field.get("evidence_refs"), list, "必须回带证据引用")
        written = [row for row in notes if row["field"].get("board") == "written"]
        self.assertTrue(written, "本夹具里 inner_length 应当被写入")
        self.assertTrue(written[0]["field"]["evidence_refs"], "已写入的字段必须带证据（要求 5）")
        state_fields = (state.get("fields") or {})
        self.assertEqual(state_fields.get("inner_length", {}).get("board"), "written")
        self.assertEqual(state_fields.get("inner_width", {}).get("board"), "pending")

    def test_b6_model_candidates_stay_pending_and_never_reach_the_board(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        sem = semantics_document()
        sem["fields"]["closure_type"] = {
            "origin": "inferred_by_model", "status": "needs_confirmation",
            "value": "tuck", "confidence": 0.6, "evidence_level": "WEAK",
            "evidence_refs": [], "conflicts": [], "alternatives": [], "unit": ""}
        state = self.start(pid, sem=sem)
        self.assertNotIn("closure_type", self.requirement_data(store_mod, pid),
                         "模型推断不许自动确认、不许写进 requirement.data（要求 6）")
        self.assertEqual((state.get("fields") or {}).get("closure_type", {}).get("board"),
                         "pending", "模型候选必须是待确认")
        self.assertIn("closure_type",
                      (state.get("pending") or {}).get("needs_confirmation") or [],
                      "模型候选必须进待确认清单")

    def test_b7_user_confirmed_values_are_preserved_not_overwritten(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        self.confirm_fields(store_mod, pid, {"inner_length": 88.8})
        state = self.start(pid)
        self.assertEqual(self.requirement_data(store_mod, pid).get("inner_length"), 88.8,
                         "不许覆盖用户已确认的值（Spec §4.3）")
        self.assertEqual((state.get("fields") or {}).get("inner_length", {}).get("board"),
                         "preserved", "已有用户确认值时必须标 preserved")

    def test_b8_missing_fields_create_pending_items_without_inventing_values(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        state = self.start(pid)
        self.assertNotIn("face_paper", self.requirement_data(store_mod, pid),
                         "图纸里没有的材料信息必须保持缺失（不许编造）")
        self.assertEqual((state.get("fields") or {}).get("face_paper", {}).get("board"),
                         "missing", "缺失字段必须标 missing 并生成待补项")

    def test_b26_unconfirmed_values_never_reach_cost_inputs(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        state = self.start(pid)
        data = self.requirement_data(store_mod, pid)
        for key, entry in (state.get("fields") or {}).items():
            if entry.get("board") in ("written", "preserved"):
                continue
            self.assertNotIn(key, data, "未确认字段 %s 不许写进看板（也就进不了成本输入）" % key)
        gaps = {str(item.get("field")) for item in module.inheritance(pid)["unresolved_gaps"]}
        self.assertTrue({"inner_width", "inner_height"} <= gaps,
                        "未确认/缺失字段必须进 unresolved_gaps（要求 26）：%r" % gaps)

    def test_b27_unit_unconfirmed_keeps_absolute_sizes_blocked(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.start(pid, unit_status="needs_confirmation")
        self.assertNotEqual(module.current_anchor(pid).get("unit_status"), "confirmed",
                            "IR 单位未确认时锚点不许写 confirmed")
        gate = module.gates(pid, stage="box_match")
        codes = {str(item.get("code")) for item in gate.get("blocking") or []}
        codes |= {str(item.get("code")) for item in gate.get("warnings") or []}
        self.assertIn("unit_unconfirmed", codes, "单位未确认时必须给出 unit_unconfirmed（Spec §5.2）")
        self.assertEqual(gate.get("status"), "blocked", "单位未确认时盒型匹配必须被拦")


class FakeEngines:
    """四个下游引擎的替身：只提供门禁与版本继承需要的那几个读接口（Spec §2.2.1）。"""

    def __init__(self, *, box=None, bom=None, route=None, cost=None, policy=None,
                 route_versions=None):
        self.box = box if box is not None else {"decision": "none", "confirmed_box_type": "",
                                                "engine_version": "packaging_match_v1"}
        self.bom = bom if bom is not None else {"built": False, "items": [],
                                                "engine_version": "packaging_bom_v1"}
        self.route = route if route is not None else {"built": False, "status": "draft",
                                                      "steps": [],
                                                      "engine_version": "packaging_route_v1"}
        self.cost = cost if cost is not None else {"built": False, "has_gaps": False, "gaps": [],
                                                   "total_cost": 0.0, "engine_version": "packaging_cost_v1"}
        self.policy = policy if policy is not None else {
            "status": "pending", "chosen": "", "policy": "unresolved",
            "fallback": "sheet_labor_rate", "decided_by": ""}
        self._route_versions = route_versions if route_versions is not None else []

    def load_box_match(self, project_id, requirement_no="", **kwargs):
        return copy.deepcopy(self.box)

    def load_bom(self, project_id, requirement_no="", **kwargs):
        return copy.deepcopy(self.bom)

    def load_route(self, project_id, requirement_no="", **kwargs):
        return copy.deepcopy(self.route)

    def route_versions(self, project_id, requirement_no="", **kwargs):
        return copy.deepcopy(self._route_versions)

    def load_cost(self, project_id, requirement_no="", **kwargs):
        return copy.deepcopy(self.cost)

    def result_version_of(self, cost):
        return "pkgcost-v1:%s:%.6f" % (str(cost.get("quote_quantity") or ""),
                                       float(cost.get("total_cost") or 0.0))

    def minimum_charge_policy(self):
        return copy.deepcopy(self.policy)


class CGates(FlowCase):
    """C 组：门禁矩阵、冲突阻断、确认解锁、版本链条、正式报价闸门（Spec §5/§6，要求 7–13）。"""

    def engines(self, **kwargs):
        return FakeEngines(**kwargs)

    def patch_deps(self, module, engines, **overrides):
        resolver = self.resolver(engines, **overrides)
        patch = mock.patch.object(module, "_dependency", resolver)
        patch.start()
        self.addCleanup(patch.stop)
        return resolver

    def resolver(self, engines, **overrides):
        base = self.deps(engines=engines)
        base.update(overrides)

        def resolve(name):
            return base.get(name)
        return resolve

    def confirmed_sem(self, **extra):
        fields = copy.deepcopy(semantics_document()["fields"])
        for key, value in FULLY_CONFIRMED.items():
            fields[key] = {"origin": "confirmed_from_cad", "status": "confirmed",
                           "value": value, "confidence": 1.0, "evidence_level": "STRONG",
                           "evidence_refs": ["ev:D:34"], "conflicts": [],
                           "alternatives": [], "unit": "mm"}
        for key, entry in (extra or {}).items():
            fields[key] = entry
        sem = semantics_document()
        sem["fields"] = fields
        return sem

    def test_c7_conflicting_field_blocks_dependent_steps(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        sem = self.confirmed_sem(inner_length={
            "origin": "conflict", "status": "conflict", "value": None, "confidence": 0.0,
            "evidence_level": "CONTRADICTORY", "evidence_refs": ["ev:D:34", "ev:E:10"],
            "conflicts": [{"field": "inner_length", "declared": 70.0, "measured": 72.0,
                           "delta": 2.0, "tolerance": 0.5, "unit": "mm",
                           "status": "conflict", "evidence_refs": ["ev:D:34", "ev:E:10"]}],
            "alternatives": [], "unit": "mm"})
        state = self.start(pid, sem=sem, engines=self.engines())
        self.assertEqual((state.get("fields") or {}).get("inner_length", {}).get("board"),
                         "conflict", "冲突字段必须标 conflict（要求 7）")
        gate = self.module().gates(pid, stage="box_match")
        self.assertEqual(gate.get("status"), "blocked", "冲突字段必须拦住依赖步骤（要求 7）")
        codes = {str(item.get("code")) for item in gate.get("blocking") or []}
        self.assertIn("field_conflict", codes, "阻塞原因必须点名冲突：%r" % codes)
        self.assertNotIn("inner_length", self.requirement_data(store_mod, pid),
                         "冲突字段不许被静默选一个值写进看板")
        payload = json.dumps(state, ensure_ascii=False)
        self.assertIn("ev:D:34", payload)
        self.assertIn("ev:E:10", payload)

    def test_c8_user_confirmation_opens_the_blocked_stages(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.start(pid, sem=self.confirmed_sem(), engines=self.engines())
        before = module.gates(pid, stage="box_match")
        self.assertEqual(before.get("status"), "blocked",
                         "字段还没人工确认时不许开放盒型匹配（要求 8）")
        codes = {str(item.get("code")) for item in before.get("blocking") or []}
        self.assertTrue(codes & {"field_missing", "field_unconfirmed"}, codes)
        self.confirm_fields(store_mod, pid, FULLY_CONFIRMED)
        after = module.gates(pid, stage="box_match")
        self.assertEqual(after.get("status"), "open",
                         "人工确认后同一段必须解锁（要求 8）：%r" % after.get("blocking"))

    def test_c9_box_match_result_reaches_bom(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.confirm_fields(store_mod, pid, FULLY_CONFIRMED)
        engines = self.engines(box={"decision": "confirmed", "confirmed_box_type": "folding_carton",
                                    "confirmed_by": "zhang", "confirmed_at": "2026-09-20T10:00:00+08:00",
                                    "engine_version": "packaging_match_v1"})
        self.patch_deps(module, engines)
        chain = module.inheritance(pid, stage="bom")["stage_chain"]
        box = [row for row in chain if row.get("stage") == "box_match"]
        self.assertTrue(box, "BOM 的版本链里必须有盒型确认结果（要求 9）：%r" % chain)
        self.assertEqual(box[0].get("value"), "folding_carton")
        self.assertEqual(box[0].get("confirmed_by"), "zhang")
        self.assertIn("source_versions", (ROOT / "tech_app/backend/services/packaging_bom.py")
                      .read_text(encoding="utf-8"), "BOM 结果必须追加 source_versions（Spec §6.1）")

    def test_c10_bom_version_reaches_route(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.confirm_fields(store_mod, pid, FULLY_CONFIRMED)
        engines = self.engines(bom={"built": True, "generated_at": "2026-09-20T11:00:00+08:00",
                                    "items": [], "engine_version": "packaging_bom_v1"})
        self.patch_deps(module, engines)
        chain = module.inheritance(pid, stage="route")["stage_chain"]
        bom = [row for row in chain if row.get("stage") == "bom"]
        self.assertTrue(bom, "工艺路线的版本链里必须有 BOM 版本（要求 10）：%r" % chain)
        self.assertEqual(bom[0].get("value"), "2026-09-20T11:00:00+08:00")
        self.assertIn("source_versions", (ROOT / "tech_app/backend/services/packaging_route.py")
                      .read_text(encoding="utf-8"), "路线结果必须追加 source_versions")

    def test_c11_route_version_reaches_cost(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.confirm_fields(store_mod, pid, FULLY_CONFIRMED)
        engines = self.engines(route={"built": True, "status": "confirmed", "steps": [],
                                      "engine_version": "packaging_route_v1"},
                               route_versions=[{"version": 3, "status": "confirmed"}])
        self.patch_deps(module, engines)
        chain = module.inheritance(pid, stage="cost")["stage_chain"]
        route = [row for row in chain if row.get("stage") == "route"]
        self.assertTrue(route, "成本的版本链里必须有工艺路线版本（要求 11）：%r" % chain)
        self.assertEqual(str(route[0].get("value")), "3")
        self.assertIn("source_versions", (ROOT / "tech_app/backend/services/packaging_cost.py")
                      .read_text(encoding="utf-8"), "成本结果必须追加 source_versions")

    def test_c12_cost_gaps_reach_the_quote_draft(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.confirm_fields(store_mod, pid, FULLY_CONFIRMED)
        engines = self.engines(cost={"built": True, "has_gaps": True,
                                     "gaps": [{"code": "missing_rate", "message": "缺少费率"}],
                                     "total_cost": 12.5, "engine_version": "packaging_cost_v1"})
        self.patch_deps(module, engines)
        self.assertTrue(module.inheritance(pid)["unresolved_gaps"],
                        "成本缺口必须出现在 unresolved_gaps（要求 12）")
        self.assertEqual(module.gates(pid, stage="quote_draft").get("status"), "open",
                         "有缺口也要允许存在报价草稿（Spec §5.1）")
        self.assertIn("source_versions", (ROOT / "tech_app/backend/services/packaging_handoff.py")
                      .read_text(encoding="utf-8"), "交接包必须追加 source_versions")

    def test_c13_unresolved_gaps_and_pending_policy_block_formal_publish(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.confirm_fields(store_mod, pid, FULLY_CONFIRMED)
        engines = self.engines(cost={"built": True, "has_gaps": True,
                                     "gaps": [{"code": "missing_rate"}], "total_cost": 1.0},
                               policy={"status": "pending", "policy": "unresolved",
                                       "fallback": "sheet_labor_rate"})
        self.patch_deps(module, engines)
        blocked = module.gates(pid, stage="quote_publish")
        self.assertEqual(blocked.get("status"), "blocked", "未裁决 + 缺口时不许放开正式报价（要求 13）")
        codes = {str(item.get("code")) for item in blocked.get("blocking") or []}
        self.assertIn("minimum_charge_policy_unresolved", codes, codes)
        module2 = self.module()
        self.expect_error("PACKAGING_GATE_BLOCKED", module2.require_gate, pid, "quote_publish")
        engines.box = {"decision": "confirmed", "confirmed_box_type": "folding_carton"}
        engines.cost = {"built": True, "has_gaps": False, "gaps": [], "total_cost": 1.0}
        engines.policy = {"status": "chosen", "policy": "sheet_industry_standard",
                          "chosen": "sheet_industry_standard", "decided_by": "owner"}
        opened = module.gates(pid, stage="quote_publish")
        self.assertEqual(opened.get("status"), "open",
                         "口径裁决且无缺口后必须开放：%r" % opened.get("blocking"))

    def test_c14_gate_matrix_covers_every_stage_with_the_frozen_fields(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.patch_deps(module, self.engines())
        gates = module.gates(pid)
        self.assertEqual(set(gates.get("stages") or {}), set(GATE_STAGES),
                         "门禁必须覆盖六个下游段（Spec §5.1）")
        for stage, fields in GATE_FIELDS.items():
            entry = gates["stages"][stage]
            self.assertEqual(tuple(entry.get("requires") or ()), fields,
                             "%s 的必需字段必须与 Spec §5.1 一致" % stage)
            self.assertIn(entry.get("status"), {"open", "blocked"})
            for item in entry.get("blocking") or []:
                self.assertIn(str(item.get("code")), BLOCKING_CODES, "阻塞码必须在闭集内")
                self.assertTrue(str(item.get("message") or ""), "阻塞必须给中文原因")
        self.assertEqual(module.gates(pid, stage="bom").get("stage"), "bom",
                         "单段查询只回一段")

    def test_c15_require_gate_raises_one_named_error_and_rejects_unknown_stages(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.patch_deps(module, self.engines())
        with self.assertRaises(ValueError):
            module.require_gate(pid, "not_a_stage")
        error = self.expect_error("PACKAGING_GATE_BLOCKED", module.require_gate, pid, "box_match")
        message = str(getattr(error, "message", "") or error)
        self.assertLess(len(message), 240, "错误文案必须短，不重复播报（要求 18）")
        self.assertNotIn("\n", message, "错误文案只给一处结论，不做多段播报")


class DVersionAnchorAndStale(FlowCase):
    """D 组：版本六元组、锚点确定性、stale 传播且不覆盖正文（Spec §6，要求 14）。"""

    STALE_REASONS = {"drawing_version_changed", "source_sha256_changed", "ir_hash_changed",
                     "semantics_hash_changed", "requirement_snapshot_changed",
                     "converter_version_changed"}
    REQUIRED_KEYS = ("source_drawing_version", "source_ir_version",
                     "requirement_snapshot_version", "confirmed_by", "confirmed_at",
                     "unresolved_gaps")

    def test_d14_new_drawing_version_marks_downstream_stale_without_overwriting(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        meta.put_doc(pid, "pricing", {"approval": {"status": "approved"}, "total": 999.0})
        meta.put_doc(pid, "report", {"status": "published", "report_no": "R-1"})
        first = self.start(pid)
        self.assertEqual(module.stale_view(pid)["stale"], False, "第一次跑不该有 stale")
        store_mod.replace_source(pid, WINE_BOX, dwg_bytes("AC1032"))
        second = self.start(pid)
        self.assertNotEqual(first["run_id"], second["run_id"], "新图纸版本必须是新 run（Spec §6.2）")
        stale = module.stale_view(pid)
        self.assertTrue(stale["stale"], "新图纸版本必须把下游标成 stale（要求 14）")
        self.assertTrue(set(stale.get("reasons") or []) <= self.STALE_REASONS,
                        "reasons 必须在闭集内：%r" % stale.get("reasons"))
        self.assertTrue(any(stale["downstream"].get(stage) for stage in
                            ("box_match", "bom", "route", "cost", "quote_draft")),
                        "至少有一段的 stale 被标上：%r" % stale.get("downstream"))
        # 注意：`store.replace_source()` 自己就会调 `store.invalidate_confirmations()`
        # 把既有审批打回 draft（既有行为，且**不删正文**）。本批只要求流程不覆盖正文、
        # 不往旧报价单里塞新键；下游 stale 只做标注（Spec §6.3）。
        pricing = meta.get_doc(pid, "pricing") or {}
        self.assertEqual(sorted(pricing), ["approval", "total", "updated_at"],
                         "流程不许往旧报价单里塞新键：%r" % sorted(pricing))
        self.assertEqual(pricing.get("total"), 999.0, "旧报价单正文不许被流程改写（Spec §6.3）")
        self.assertEqual((pricing.get("approval") or {}).get("status"), "draft",
                         "打回 draft 只能来自既有 replace_source 的撤销，不是流程新写的")
        self.assertEqual(meta.get_doc(pid, "report"),
                         {"status": "published", "report_no": "R-1"},
                         "已发布报告不许被静默改写")
        cards = self.task_cards(store_mod, pid)
        old = [row for row in cards
               if str((row.get("task") or {}).get("id") or "").startswith("flow:%s:" % first["run_id"])]
        self.assertEqual(len(old), len(STEP_IDS), "旧 run 的卡片必须原样保留（可回看）")

    def test_d15_requirement_snapshot_version_is_deterministic_and_field_sensitive(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        before = module.requirement_snapshot_version(pid)
        self.assertTrue(str(before).startswith("reqsnap/1:"), "版本串格式固定（Spec §6.1）")
        self.assertEqual(before, module.requirement_snapshot_version(pid), "同输入必须同版本")
        doc = dict(store_mod.load_requirement(pid) or {})
        doc["updated_at"] = "2026-09-20T23:59:59+08:00"
        doc["history"] = [{"action": "noop"}]
        store_mod.save_requirement(pid, doc)
        self.assertEqual(before, module.requirement_snapshot_version(pid),
                         "时间戳 / history 不许影响需求快照版本（否则 stale 判定失效）")
        self.confirm_fields(store_mod, pid, {"inner_length": 70.0})
        self.assertNotEqual(before, module.requirement_snapshot_version(pid),
                            "字段确认必须改变需求快照版本")

    def test_d16_run_id_is_content_addressed(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        kwargs = {"prompt": "解析酒盒.dwg", "source_sha256": "a" * 64, "drawing_version": 1,
                  "requirement_snapshot_version": module.requirement_snapshot_version(pid)}
        first = module.run_id_for(pid, **kwargs)
        self.assertEqual(first, module.run_id_for(pid, **kwargs), "同输入必须同 run_id")
        changed = dict(kwargs, drawing_version=2)
        self.assertNotEqual(first, module.run_id_for(pid, **changed), "图纸版本变化必须换 run")
        changed2 = dict(kwargs, requirement_snapshot_version="reqsnap/1:deadbeefdeadbeef")
        self.assertNotEqual(first, module.run_id_for(pid, **changed2), "需求快照变化必须换 run")

    def test_d17_anchor_records_every_version_dimension(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.start(pid, ir=cad_ir_document(ir_hash="e" * 64))
        anchor = module.current_anchor(pid)
        for key in ("drawing_version", "source_sha256", "ir_id", "ir_hash", "ir_version",
                    "semantics_id", "semantics_hash", "semantics_version", "unit_status",
                    "requirement_snapshot_version"):
            self.assertIn(key, anchor, "锚点必须记 %s（Spec §6.2）" % key)
            self.assertNotEqual(anchor.get(key), None, "锚点 %s 不许是 None" % key)
        self.assertEqual(anchor["ir_hash"], "e" * 64, "锚点必须用真实 IR 指纹")
        self.assert_json_safe(anchor, "anchor")

    def test_d18_stale_can_be_marked_and_cleared_per_stage(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.start(pid)
        marked = module.mark_downstream_stale(pid, ["ir_hash_changed"], actor="tester")
        self.assertTrue(marked.get("stale"))
        self.assertIn("ir_hash_changed", module.stale_view(pid)["reasons"])
        module.clear_downstream_stale(pid, "box_match", actor="tester")
        self.assertFalse(module.stale_view(pid)["downstream"]["box_match"],
                         "重新生成过的段必须能逐段解除 stale")
        self.assertTrue(module.stale_view(pid)["downstream"]["cost"],
                        "没重新生成的段必须仍然 stale")

    def test_d19_stale_reasons_reject_unknown_codes(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        with self.assertRaises(ValueError):
            module.mark_downstream_stale(pid, ["something_else"])

    def test_d20_inheritance_six_tuple_and_migrate_branches(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.start(pid)
        payload = module.inheritance(pid)
        for key in self.REQUIRED_KEYS:
            self.assertIn(key, payload, "版本六元组必须含 %s（Spec §6.1）" % key)
        self.assert_json_safe(payload, "inheritance")
        self.assertEqual(module.migrate({"flow_version": FLOW_VERSION}), {"status": "ok"})
        self.assertEqual(module.migrate({}).get("status"), "needs_rebuild")
        with self.assertRaises(ValueError):
            module.migrate({"flow_version": "packaging-drawing-flow/99"})


class ERetryAndIdempotency(FlowCase):
    """E 组：重跑幂等、单步重试、不重复上传（Spec §7，要求 15/23/24）。"""

    def test_e15_rerun_does_not_duplicate_fields_messages_or_tasks(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        first = self.start(pid)
        cards_before = len(self.task_cards(store_mod, pid))
        notes_before = len(self.notes(store_mod, pid))
        users_before = len(self.user_entries(store_mod, pid))
        data_before = copy.deepcopy(self.requirement_data(store_mod, pid))
        second = self.start(pid)
        self.assertEqual(first["run_id"], second["run_id"], "同附件同需求快照必须复用 run（要求 15）")
        self.assertEqual(len(self.task_cards(store_mod, pid)), cards_before,
                         "重跑不许新增执行卡（要求 15）")
        self.assertEqual(len(self.notes(store_mod, pid)), notes_before,
                         "重跑不许新增字段过程行（要求 15）")
        self.assertEqual(len(self.user_entries(store_mod, pid)), users_before,
                         "重跑不许新增用户气泡（要求 15）")
        self.assertEqual(self.requirement_data(store_mod, pid), data_before,
                         "重跑不许重复写需求字段（要求 15）")

    def test_e23_failed_step_can_be_retried_in_place(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        converter = FakeConverter(error=RuntimeError("转换器炸了"))
        deps = self.deps(converter=converter)
        state = module.run_flow(pid, prompt="解析酒盒.dwg", actor="tester", deps=deps)
        self.assertEqual(self.step_of(state, "dwg_convert").get("status"), "failed",
                         "转换失败必须如实标 failed")
        card = self.card_of(store_mod, pid, "dwg_convert")
        self.assertTrue(card, "失败的步骤也要有卡")
        keys_before = [str((row.get("task") or {}).get("id") or "")
                       for row in self.task_cards(store_mod, pid)]
        converter.error = None
        deps = self.deps(converter=converter)
        retried = module.run_step(pid, "dwg_convert", actor="tester",
                                  run_id=state["run_id"], deps=deps,
                                  retry_of=str(card.get("key") or ""))
        self.assertEqual(retried.get("status"), "completed", "重试成功后必须继续推进")
        self.assertEqual(int(retried.get("attempt") or 0), 2, "重试必须记 attempt=2（Spec §7）")
        # 重试会**继续跑完剩余 pending 步**（Spec §7），所以卡片总数会补齐到七张；
        # 要求 23 真正禁止的是"同一步骤出现第二张卡"：key 不许重复，且必须是同一张。
        keys_after = [str((row.get("task") or {}).get("id") or "")
                      for row in self.task_cards(store_mod, pid)]
        self.assertEqual(len(keys_after), len(set(keys_after)),
                         "同一步骤不许出现第二张卡（要求 23）：%r" % keys_after)
        self.assertEqual(keys_after[:len(keys_before)], keys_before,
                         "重试前已有的卡必须原样保留、按同一 key 就地更新（要求 23）")
        self.assertEqual(keys_after.count(str(card.get("key"))), 1,
                         "重试后失败的那一步仍只许有一张卡")
        after = module.flow_state(pid)
        self.assertNotEqual(self.step_of(after, "cad_ir_parse").get("status"), "pending",
                            "重试成功后后续步骤必须继续跑（要求 23）")

    def test_e24_retry_never_reuploads_the_attachment(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        before_meta = copy.deepcopy(store_mod.load_meta(pid))
        before_bytes = blob.get_bytes("%s/source.dwg" % pid)
        deps = self.deps(converter=FakeConverter(error=RuntimeError("转换器炸了")))
        state = module.run_flow(pid, prompt="解析酒盒.dwg", actor="tester", deps=deps)
        module.run_step(pid, "dwg_convert", actor="tester", run_id=state["run_id"],
                        deps=self.deps(converter=FakeConverter(error=RuntimeError("又炸了"))),
                        retry_of="flow:%s:dwg_convert" % state["run_id"])
        after_meta = store_mod.load_meta(pid)
        self.assertEqual(after_meta.get("attachments"), before_meta.get("attachments"),
                         "重试不许改动附件列表（要求 24）")
        self.assertEqual(after_meta.get("source_path"), before_meta.get("source_path"))
        self.assertEqual(int(after_meta.get("input_revision") or 1),
                         int(before_meta.get("input_revision") or 1),
                         "重试不许推进输入版本")
        self.assertEqual(blob.get_bytes("%s/source.dwg" % pid), before_bytes,
                         "重试不许重写原始附件字节（要求 24）")
        source = self.flow_source()
        for word in ("UploadFile", "multipart", "requests.post", "put_bytes", "save_upload"):
            self.assertNotIn(word, source, "流层不许自己上传/写附件（要求 24）：%r" % word)

    def test_e25_unknown_step_id_is_rejected(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        state = self.start(pid)
        with self.assertRaises(ValueError):
            module.run_step(pid, "not_a_step", actor="tester", run_id=state["run_id"],
                            deps=self.deps())

    def test_e26_concurrent_reruns_keep_one_card_per_step(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        import threading
        errors: list = []

        def worker():
            try:
                module.run_flow(pid, prompt="解析酒盒.dwg并提取包装结构和需求字段",
                                actor="tester", deps=self.deps())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [], "并发重跑不许抛异常：%r" % errors)
        cards = self.task_cards(store_mod, pid)
        ids = [str((row.get("task") or {}).get("id") or "") for row in cards]
        self.assertEqual(len(ids), len(set(ids)), "并发下同一 run 的步骤卡必须唯一：%r" % ids)
        self.assertEqual(len(ids), len(STEP_IDS), "并发下仍是七张卡")
        self.assertEqual(len(self.user_entries(store_mod, pid)), 1, "并发下气泡只许一条")


class FRestoreAndHistory(FlowCase):
    """F 组：刷新/重进项目后的会话、字段证据与处理状态恢复（Spec §12，要求 16/17/29/30）。"""

    def test_f16_flow_cards_replay_in_the_same_order_after_reload(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.start(pid)
        rows = self.events(store_mod, pid)
        replayed = module.flow_state(pid)
        self.assertEqual([str(row.get("step_id")) for row in replayed["steps"]], list(STEP_IDS),
                         "回放顺序必须与执行顺序一致（要求 16）")
        seqs = [int(row.get("seq") or 0) for row in replayed["steps"]]
        self.assertEqual(seqs, sorted(seqs), "回放里的 seq 必须递增（要求 16）")
        card_ids = [str((row.get("task") or {}).get("id")) for row in rows
                    if str(row.get("kind")) == "task"]
        self.assertEqual(card_ids, ["flow:%s:%s" % (replayed["run_id"], step)
                                    for step in STEP_IDS],
                         "刷新后仍能按同一批 key 回放（要求 16）")

    def test_f17_history_restores_field_evidence(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        self.start(pid)
        state = self.module().flow_state(pid)
        written = [key for key, entry in (state.get("fields") or {}).items()
                   if entry.get("board") == "written"]
        self.assertTrue(written, "本夹具至少应有一个字段被写入")
        for key in written:
            entry = state["fields"][key]
            self.assertTrue(entry.get("evidence_refs"), "恢复后字段仍须带证据（要求 17）")
            self.assertTrue(str(entry.get("origin") or ""), "恢复后字段仍须带来源（要求 17）")
            self.assertTrue(str(entry.get("ir_hash") or ""), "字段证据必须锚定 IR 版本（要求 17）")
        notes = [row for row in self.notes(store_mod, pid) if row.get("field")]
        self.assertTrue(notes, "字段过程行必须落库，重进项目仍能看到（要求 17）")

    def test_f29_both_sample_projects_reopen_from_history(self):
        store_mod, meta, blob, tmp = self.memory_store()
        module = self.module()
        projects = {}
        for name in (WINE_BOX, ROUND_BOX):
            pid = self.project(store_mod=store_mod, filename=name)
            self.start(pid, prompt="解析%s并提取包装结构和需求字段" % name)
            projects[name] = pid
        for name, pid in projects.items():
            self.assertTrue(store_mod.load_meta(pid), "%s 的项目仍在历史清单里" % name)
            state = module.flow_state(pid)
            self.assertTrue(state.get("run_id"), "%s 重开后必须能恢复 run" % name)
            self.assertEqual(len(state["steps"]), len(STEP_IDS), "%s 七步状态齐全" % name)

    def test_f30_full_restore_covers_session_fields_and_status(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.start(pid)
        state = module.flow_state(pid)
        self.assertTrue(self.events(store_mod, pid), "会话条目必须落库（要求 30）")
        self.assertTrue(state.get("fields"), "字段处理状态必须落库（要求 30）")
        self.assertTrue(state.get("status"), "整条 run 的状态必须落库（要求 30）")
        self.assertEqual(state.get("anchor", {}).get("requirement_snapshot_version"),
                         module.requirement_snapshot_version(pid),
                         "恢复后的锚点必须与当前需求快照一致（要求 30）")
        self.assert_json_safe(state, "flow_state")


class GPermissionsAndNotices(FlowCase):
    """G 组：读/写权限分开、角色不阻断合法流转、错误只播一处（Spec §8，要求 18/27/28）。"""

    def test_g18_a_blocked_flow_keeps_exactly_one_notice(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        state = self.start(pid)
        failed = [row for row in self.task_cards(store_mod, pid)
                  if str((row.get("task") or {}).get("status")) == "failed"]
        self.assertEqual(failed, [],
                         "门禁拦住下游不是失败：不许因此产生失败卡（要求 18）")
        self.assertEqual(self.step_of(state, "downstream_prepare").get("status"), "completed",
                         "后续任务准备步骤本身必须完成（Spec §3.2 第 4 条）")
        self.assertNotEqual(state.get("status"), "failed", "门禁 blocked 不等于 failed")
        error = self.expect_error("PACKAGING_GATE_BLOCKED", module.require_gate, pid, "cost")
        message = str(getattr(error, "message", "") or error)
        self.assertNotIn("\n", message, "权限/门禁提示只给一处，不做多段播报（要求 18）")
        self.assertLess(len(message), 240, "提示必须短（要求 18）")

    def test_g27_reads_are_actor_free_and_roles_do_not_block_the_handoff(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        self.start(pid)
        # 纯读接口不带 actor、不做权限判定：销售 / 财务只读可见性由项目层负责（Spec §8）。
        self.assertTrue(module.flow_state(pid).get("run_id"))
        self.assertIn("stages", module.gates(pid))
        self.assertIn("stale", module.stale_view(pid))
        for path in self.package_files():
            names = self.imports_of(path)
            self.assertNotIn("tech_app.backend.services.auth", names,
                             "%s 不许自己判权限（Spec §8）" % path.name)
        auth = importlib.import_module("tech_app.backend.services.auth")
        self.assertIn("finance_manager", set(auth.SESSION_WRITE_ROLES),
                      "财务经理必须能写会话（否则合法流转被拦，要求 27）")
        self.assertIn("engineer", set(auth.SESSION_WRITE_ROLES))
        self.assertNotIn("finance_manager", set(auth.WRITE_ROLES),
                         "Agent 对话仍走 WRITE_ROLES，不放宽（Spec §8）")

    def test_g28_read_and_write_routes_are_gated_separately(self):
        read_route, read_block = self.main_route_source("/drawing-flow")
        write_route, write_block = self.main_route_source("/drawing-flow/run")
        self.assertNotEqual(read_route, write_route, "读与写必须是两条独立路由（要求 28）")
        self.assertRegex(read_block, r"@app\.(get)\(",
                         "读路由必须是 GET：%r" % read_route)
        self.assertIn("current_user", read_block, "读路由只要求登录（Spec §8）")
        self.assertNotIn("SESSION_WRITE_ROLES", read_block, "读路由不许要求写权限")
        self.assertIn("post", write_block, "写路由必须是 POST：%r" % write_route)
        self.assertIn("SESSION_WRITE_ROLES", write_block, "写路由用会话写权限（Spec §8）")
        self.assertIn("_require(", write_block, "写路由必须走既有 _require 门禁")


class HReusedFrontendContracts(FlowCase):
    """H 组：不新增第二套前端契约（Spec §0/§4.1，要求 19/20/21/22/25）。"""

    def test_h19_no_new_colour_contract_is_introduced(self):
        source = self.flow_source()
        for word in ("rgb(", "orange", "warning-color", "style=", "class="):
            self.assertNotIn(word, source.lower(),
                             "流层不许带任何视觉/配色契约（Spec §0）：%r" % word)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", source),
                          "流层不许写死十六进制颜色（Spec §0）")
        route_source = self.main_route_source("/drawing-flow/run")[1]
        self.assertNotIn("style", route_source.lower(), "路由不许回带样式字段")

    def test_h20_events_reuse_the_existing_task_and_note_shape(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        self.start(pid)
        allowed = {"kind", "source", "stage", "text", "task", "ui", "key", "ts", "seq", "field"}
        for row in self.events(store_mod, pid):
            self.assertTrue(set(row) <= allowed,
                            "会话条目不许新增渲染字段（要求 20）：%r" % sorted(set(row) - allowed))
            self.assertIn(str(row.get("kind")), EVENT_KINDS)
        cards = self.task_cards(store_mod, pid)
        task_keys = {"id", "label", "status", "steps", "error"}
        for row in cards:
            self.assertTrue(set(row["task"]) <= task_keys,
                            "任务卡形状必须沿用既有 key（要求 20）：%r"
                            % sorted(set(row["task"]) - task_keys))

    def test_h21_and_h22_the_flow_ships_no_layout_or_composer_state(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        state = self.start(pid)
        blob_text = json.dumps({"state": state, "gates": self.module().gates(pid)}, ensure_ascii=False)
        for word in ("model_settings", "composer", "input_height", "align_bottom",
                     "style", "css"):
            self.assertNotIn(word, blob_text.lower(),
                             "流程状态不许携带输入框/布局契约（要求 21/22）：%r" % word)

    def test_h25_no_chain_of_thought_is_ever_exposed(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        state = self.start(pid)
        for row in self.events(store_mod, pid):
            self.assert_no_prohibited_text(row, "会话条目")
        self.assert_no_prohibited_text(state, "flow_state")
        for word in ("思维链", "chain_of_thought", "reasoning"):
            self.assertNotIn(word, json.dumps(state, ensure_ascii=False))


class IDependencyStatus(FlowCase):
    """I 组：依赖现状如实登记（第 3/4 批并行落地中，本组不把依赖当成第 5 批的验收条件）。"""

    def spec_of(self, name):
        try:
            return importlib.util.find_spec("tech_app.backend.services.%s" % name)
        except (ImportError, ValueError):
            return None

    def test_i1_capability_reports_missing_dependencies_truthfully(self):
        module = self.module()
        deps = module.capability()["dependencies"]
        for name in ("cad_ir", "packaging_semantics", "cad_converter", "file_preflight"):
            self.assertEqual(bool(deps.get(name)), self.spec_of(name) is not None,
                             "capability() 对 %s 的登记必须与实际一致，不许粉饰" % name)

    def test_i2_a_missing_dependency_degrades_the_chain_but_keeps_going(self):
        store_mod, meta, blob, tmp = self.memory_store()
        pid = self.project(store_mod=store_mod)
        module = self.module()
        if self.spec_of("cad_ir") is not None:
            self.skipTest("第 3 批（cad_ir）已落地，本条只在依赖缺失时有意义")
        deps = self.deps()
        deps["cad_ir"] = None
        state = module.run_flow(pid, prompt="解析酒盒.dwg", actor="tester", deps=deps)
        self.assertEqual(self.step_of(state, "cad_ir_parse").get("status"), "unavailable")
        self.assertEqual(self.step_of(state, "file_preflight").get("status"), "completed",
                         "依赖缺失不许把已经能跑完的步骤也算失败")

    def test_i3_real_dependency_keeps_the_frozen_signature(self):
        module = self.module()
        missing = [name for name in ("cad_ir", "packaging_semantics")
                   if self.spec_of(name) is None]
        if missing:
            self.fail("依赖未落地（%s）；第 5 批红测用 fake 隔离，本条只做签名核对。"
                      "本条的失败不是第 5 批的缺口。" % "、".join(missing))
        for name, functions in (("cad_ir", ("parse_conversion", "load_ir", "summarize")),
                                ("packaging_semantics", ("analyze_conversion",
                                                         "apply_to_requirement", "summarize"))):
            package = importlib.import_module("tech_app.backend.services.%s" % name)
            for function in functions:
                self.assertTrue(callable(getattr(package, function, None)),
                                "%s 必须导出 %s()（Spec §2.2.1 冻结的调用面）" % (name, function))


if __name__ == "__main__":
    unittest.main(verbosity=2)
