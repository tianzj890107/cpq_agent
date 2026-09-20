"""空特征零件回退到「plate / box / cylinder 空模板」（第二批，范围已按用户口径收窄）。

背景（用户口径 + 线上实证）：
  · 线上项目 7393f6a00ccc 的 P-005 输出线缆组件（型号 XT30）features=[]；
  · 参数编辑器只遍历已存在的 features（app.js 遍历 (part.features || [])），空特征零件
    在界面上只剩「名称 / 数量 / 材料 / 保存 / 重新生成」，没有任何基体类型与尺寸输入框；
  · 后端 part_edit 只允许改**已有**特征的**已有**数值字段，features=[] 时任何
    feature_index 都是「特征序号 1 不存在」；Agent 的 UpdatePartParameters 复用同一份
    实现，因此也修不了、也不会问用户。
  · 用户口径：不回退到什么都能填的自由表单，也不引入分类字段 —— 就是固定三种基体模板，
    字段没填就是空的，人工或 Agent 补。

契约见 docs/specs/tech-empty-feature-base-geometry-fallback.md：
  C1 前端 features 为空时渲染固定基体模板（BASE_FEATURE_TYPES 三选一 + data-base-dim）；
  C2 保存把模板写进 features[0]，留空保持空；
  C3 后端受控能力 initialize_base_feature / replace_base_feature（UI 与 Agent 共用一份）；
  C4 留空不再当错误（None / 空串跳过），非数值与 <=0 仍拒绝；
  C5 单件重生成只影响该零件；
  C6 Agent 同一份服务 + 缺参数必须问用户、不得编造尺寸；
  C7 与第 1 批的状态码对齐（不新增词表）；
  C8 不做分类字段、不做「不需要 CAD」。

验证方式：
  · 后端用带 fastapi/pydantic 的解释器（本机为 open-claude/.venv/bin/python）在子进程里
    真跑 part_edit / oc_agent._update_part 与 TestClient（临时 DATA_DIR、假项目、不联网）；
  · 单件重生成场景用**真实 CadQuery**（缺 CadQuery 时该组用例跳过）；
  · 前端做源码契约断言（固定模板、封闭类型、保存写回、静态资源版本号）。
"""
from __future__ import annotations

import functools
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


APP = read(FRONTEND / "app.js")
INDEX_HTML = read(FRONTEND / "index.html")
PART_EDIT = read(ROOT / "tech_app/backend/services/part_edit.py")
OC_AGENT = read(ROOT / "tech_app/backend/services/oc_agent.py")
MAIN = read(ROOT / "tech_app/backend/main.py")
IR_MODEL = read(ROOT / "tech_app/backend/models/ir.py")

ALLOWED_BASE_TYPES = {"plate", "box", "cylinder"}
FORBIDDEN_FIELD_NAMES = ("cad_requirement", "make_or_buy", "geometry_representation")


def block_from(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
        i += 1
    return ""


def def_block(text: str, marker: str) -> str:
    """Python 函数/方法体：从 marker 截到下一个顶层 def/class，避开 docstring 里的花括号。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    match = re.search(r"\n(?=(?:def|class|@)\s)", text[idx + len(marker):])
    end = idx + len(marker) + match.start() if match else len(text)
    return text[idx:end]


# --------------------------------------------------------------------------- #
# 后端走查：一个子进程跑完全部场景
# --------------------------------------------------------------------------- #
CHILD = r'''
import json
import os
import sys
import time
import types

data_dir, root = sys.argv[1], sys.argv[2]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

out = {}
try:
    import cadquery  # noqa: F401
    out["cadquery"] = True
except Exception:
    out["cadquery"] = False

from tech_app.backend.models.ir import DesignIR, Part
from tech_app.backend.services import part_edit
from tech_app.backend.storage import store

BOX_INPUT = {"length": 60, "width": 40, "height": 20}


def blank_part(part_id="P-005", name="输出线缆组件"):
    return Part(part_id=part_id, name=name, model_no="XT30", quantity=1, features=[])


def call(name, part, **kwargs):
    """调用受控能力并如实记录结果（不存在也如实记录，而不是抛出去）。"""
    fn = getattr(part_edit, name, None)
    if fn is None:
        return {"missing": name}
    try:
        changes, geometry_changed = fn(part, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc),
                "features": [f.model_dump() for f in part.features]}
    return {"ok": True, "changes": changes, "geometry_changed": geometry_changed,
            "features": [f.model_dump() for f in part.features]}


def case_init_service():
    result = {}
    part = blank_part()
    result["box"] = call("initialize_base_feature", part, feature_type="box",
                         dimensions=dict(BOX_INPUT))
    result["already_has_features"] = call(
        "initialize_base_feature", part, feature_type="plate",
        dimensions={"length": 10, "width": 10, "thickness": 2})

    variants = {
        "unknown_type": {"feature_type": "hole", "dimensions": {"diameter": 4, "height": 5}},
        "empty_type": {"feature_type": "", "dimensions": {}},
        "extra_field": {"feature_type": "box",
                        "dimensions": {"length": 1, "width": 1, "height": 1, "diameter": 3}},
        "missing_dim": {"feature_type": "box", "dimensions": {"length": 1, "width": 1}},
        "zero_dim": {"feature_type": "box", "dimensions": {"length": 1, "width": 1, "height": 0}},
        "negative_dim": {"feature_type": "box",
                         "dimensions": {"length": 1, "width": 1, "height": -3}},
        "text_dim": {"feature_type": "box",
                     "dimensions": {"length": 1, "width": 1, "height": "abc"}},
        "cylinder": {"feature_type": "cylinder", "dimensions": {"diameter": 8, "height": 12}},
        "plate": {"feature_type": "plate",
                  "dimensions": {"length": 90, "width": 40, "thickness": 1.6}},
        "upper_case": {"feature_type": " Box ", "dimensions": dict(BOX_INPUT)},
    }
    for name, kwargs in variants.items():
        result[name] = call("initialize_base_feature", blank_part(), **kwargs)

    bad_base = Part(part_id="P-002", name="下壳", quantity=1,
                    features=[{"type": "hole", "diameter": 4},
                              {"type": "fillet", "radius": 1.0}])
    result["replace_ok"] = call("replace_base_feature", bad_base, feature_type="box",
                                dimensions={"length": 108, "width": 56, "height": 13.25})
    valid_base = Part(part_id="P-001", name="上盖", quantity=1,
                      features=[{"type": "box", "length": 108, "width": 56, "height": 13.25}])
    result["replace_on_valid_base"] = call(
        "replace_base_feature", valid_base, feature_type="plate",
        dimensions={"length": 10, "width": 10, "thickness": 2})
    result["replace_unknown_type"] = call(
        "replace_base_feature", bad_base, feature_type="hole",
        dimensions={"diameter": 4, "height": 5})
    return result


def case_blank_skip():
    """留空不再当错误：None / 空串跳过；非数值与 <=0 仍然拒绝。"""
    def run(value):
        part = Part(part_id="P-001", name="上盖", quantity=1,
                    features=[{"type": "box", "length": 10, "width": 5, "height": 3}])
        try:
            changes, geometry_changed = part_edit.apply_edit(
                part, feature_updates=[{"feature_index": 0, "field": "height", "value": value}])
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error_type": type(exc).__name__, "error": str(exc),
                    "height": part.features[0].height}
        return {"ok": True, "changes": changes, "geometry_changed": geometry_changed,
                "height": part.features[0].height}

    return {"none": run(None), "empty": run(""), "blank_space": run("   "),
            "text": run("abc"), "zero": run(0), "negative": run(-2), "valid": run(7.5)}


def case_agent_tool():
    from tech_app.backend.services import oc_agent

    pid = store.create_project(source_filename="电池图纸案例1.png", source_bytes=b"png",
                               note="base feature probe", owner="tester")
    store.save_ir(pid, DesignIR(device_name="电池箱", design_intent="空特征补录探针",
                                parts=[{"part_id": "P-005", "name": "输出线缆组件",
                                        "model_no": "XT30", "quantity": 1, "features": []}]
                                ).model_dump(), stage="parsed")
    result = {"project": pid}
    result["missing_dim"] = oc_agent._update_part(pid, {
        "part_id": "P-005", "base_feature": {"type": "box", "length": 60, "width": 40},
        "reason": "用户只给了长宽"})
    result["ir_after_missing_dim"] = store.load_ir(pid)
    result["bad_type"] = oc_agent._update_part(pid, {
        "part_id": "P-005", "base_feature": {"type": "hole", "diameter": 4},
        "reason": "型号核验推测的孔"})
    result["free_field"] = oc_agent._update_part(pid, {
        "part_id": "P-005", "base_feature": {"type": "box", "length": 60, "width": 40,
                                             "height": 20, "wall_thickness": 1.5},
        "reason": "顺手加个字段"})
    result["ir_after_rejects"] = store.load_ir(pid)
    result["ok"] = oc_agent._update_part(pid, {
        "part_id": "P-005",
        "base_feature": {"type": "box", "length": 60, "width": 40, "height": 20},
        "reason": "用户口述：简化成长方体"})
    after = store.load_ir(pid) or {}
    result["ir_after_ok"] = after
    result["audit"] = [row.get("action") for row in store.list_audit(pid)][-6:]
    result["versions"] = [row.get("stage") for row in store.list_versions(pid)][-6:]
    return result


def case_regenerate_single():
    pid = store.create_project(source_filename="电池图纸案例1.png", source_bytes=b"png",
                               note="single regenerate probe", owner="tester")
    store.save_ir(pid, DesignIR(device_name="电池箱", design_intent="单件重生成探针", parts=[
        {"part_id": "P-001", "name": "上盖", "quantity": 1,
         "features": [{"type": "box", "length": 108, "width": 56, "height": 13.25}]},
        {"part_id": "P-005", "name": "输出线缆组件", "model_no": "XT30", "quantity": 1,
         "features": []},
    ]).model_dump(), stage="parsed")

    from starlette.testclient import TestClient
    import tech_app.backend.main as main
    client = TestClient(main.app, raise_server_exceptions=False)

    # 前端「选 box、填完三个尺寸、点保存/重新生成」等价于：把模板写进 features[0] 并回存 IR。
    ir = store.load_ir(pid)
    for part in ir["parts"]:
        if part["part_id"] == "P-005":
            part["features"] = [{"type": "box", "length": 60, "width": 40, "height": 20}]
    saved = client.put("/api/projects/%s/ir" % pid, json=ir)

    seed_3d = {"parts": [{"part_id": "P-001", "name": "上盖", "ok": True,
                          "volume_mm3": 12345.6, "mass_g": 1.0, "bbox": [108, 56, 13.25],
                          "warnings": [], "error": None,
                          "stl_url": "/api/projects/%s/geometry/P-001.stl" % pid,
                          "step_url": "/api/projects/%s/geometry/P-001.step" % pid}],
               "source_ir_hash": ""}
    seed_2d = {"parts": [{"part_id": "P-001", "name": "上盖", "ok": True,
                          "views": {"front": "P-001.svg"}, "dxf": "P-001.dxf",
                          "warnings": [], "error": None}], "source_ir_hash": ""}
    store.save_geometry_result(pid, seed_3d)
    store.save_drawings_result(pid, seed_2d)

    response = client.post("/api/projects/%s/parts/P-005/regenerate" % pid)
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:400]}
    return {"project": pid, "save_ir_http": saved.status_code, "http": response.status_code,
            "body": body,
            "store_3d": store.load_geometry_result(pid),
            "store_2d": store.load_drawings_result(pid)}


CASES = {"init_service": case_init_service, "blank_skip": case_blank_skip,
         "agent_tool": case_agent_tool, "regenerate": case_regenerate_single}

for name, fn in CASES.items():
    try:
        out[name] = fn()
    except Exception as exc:  # noqa: BLE001 — 单个场景失败不影响其它场景
        out[name] = {"probe_error": "%s: %s" % (type(exc).__name__, exc)}

print(json.dumps(out, ensure_ascii=False, default=str))
'''


def backend_python() -> str:
    candidates = [sys.executable, str(ROOT / "open-claude/.venv/bin/python"),
                  shutil.which("python3"), shutil.which("python")]
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", "import fastapi, pydantic"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


@functools.lru_cache(maxsize=None)
def probe() -> dict:
    python = backend_python()
    if not python:
        raise unittest.SkipTest("没有可 import fastapi/pydantic 的解释器，跳过基体补录走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-base-feature-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-base-feature-script-")
    try:
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run(
            [python, str(script), data_dir, str(ROOT)],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "基体补录走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout[-2000:], completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


class BaseFeatureBackendRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = probe()

    def case(self, name: str) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, f"走查场景 {name} 缺失：{value!r}")
        self.assertNotIn("probe_error", value, f"走查场景 {name} 抛错：{value.get('probe_error')}")
        return value

    def assertRejected(self, outcome: dict, where: str):
        self.assertNotIn("missing", outcome,
                         f"{where}：受控能力不存在（part_edit 缺少该函数）")
        self.assertFalse(outcome.get("ok"), f"{where} 必须被拒绝：{outcome}")

    def assertAccepted(self, outcome: dict, where: str):
        self.assertNotIn("missing", outcome,
                         f"{where}：受控能力不存在（part_edit 缺少该函数）")
        self.assertTrue(outcome.get("ok"), f"{where} 必须被接受：{outcome}")

    # -------------------------------------------------- C3 初始化
    def test_initialize_box_from_empty_features(self):
        case = self.case("init_service")
        outcome = case["box"]
        self.assertAccepted(outcome, "features 为空时建立 box")
        self.assertTrue(outcome.get("geometry_changed"), "补基体必须标记需要重生几何")
        features = outcome["features"]
        self.assertEqual(len(features), 1, "只补一个基体特征，不得顺手加别的特征")
        self.assertEqual(features[0]["type"], "box")
        for field, value in (("length", 60), ("width", 40), ("height", 20)):
            with self.subTest(field=field):
                self.assertEqual(features[0].get(field), value)
        changed = " ".join(str(entry.get("field")) for entry in outcome["changes"])
        self.assertIn("features[0]", changed, f"变更清单必须如实记录改了什么：{outcome['changes']}")

    def test_initialize_rejected_when_features_already_exist(self):
        case = self.case("init_service")
        self.assertRejected(case["already_has_features"],
                            "已经有特征（含合法基体）时不得再“初始化”")

    def test_only_three_base_types_are_accepted(self):
        case = self.case("init_service")
        for name in ("unknown_type", "empty_type"):
            with self.subTest(case=name):
                outcome = case[name]
                self.assertRejected(outcome, f"非法基体类型 {name}")
                message = str(outcome.get("error") or "")
                for allowed in ALLOWED_BASE_TYPES:
                    self.assertIn(allowed, message,
                                  f"拒绝时要告诉用户允许哪些类型：{message}")

    def test_extra_fields_outside_template_are_rejected(self):
        case = self.case("init_service")
        self.assertRejected(case["extra_field"], "基体模板之外的额外字段（自由加字段）")

    def test_dims_must_be_complete_and_positive(self):
        case = self.case("init_service")
        for name in ("missing_dim", "zero_dim", "negative_dim", "text_dim"):
            with self.subTest(case=name):
                self.assertRejected(case[name], f"尺寸非法（{name}）")

    def test_cylinder_and_plate_templates(self):
        case = self.case("init_service")
        cylinder = case["cylinder"]
        self.assertAccepted(cylinder, "cylinder 只需 diameter + height")
        self.assertEqual({k: cylinder["features"][0][k] for k in ("diameter", "height")},
                         {"diameter": 8, "height": 12})
        plate = case["plate"]
        self.assertAccepted(plate, "plate 只需 length + width + thickness")
        self.assertEqual({k: plate["features"][0][k] for k in ("length", "width", "thickness")},
                         {"length": 90, "width": 40, "thickness": 1.6})
        self.assertAccepted(case["upper_case"], "类型大小写与空格应归一，而不是报错")

    # -------------------------------------------------- C3 替换
    def test_replace_invalid_base_feature_keeps_the_rest(self):
        case = self.case("init_service")
        outcome = case["replace_ok"]
        self.assertAccepted(outcome, "首个特征类型非法时可以替换成合法基体")
        features = outcome["features"]
        self.assertEqual(features[0]["type"], "box", "替换的是首个特征")
        self.assertEqual(features[0]["height"], 13.25, "新基体尺寸必须写入")
        self.assertEqual([f["type"] for f in features[1:]], ["fillet"],
                         "features[1:] 必须原样保留，不得顺手删掉")

    def test_replace_rejected_when_base_is_already_valid(self):
        case = self.case("init_service")
        self.assertRejected(case["replace_on_valid_base"], "首个特征已是合法基体时不得替换")
        self.assertRejected(case["replace_unknown_type"], "替换也只允许三种基体类型")

    # -------------------------------------------------- C4 留空语义
    def test_blank_dimension_is_skipped_not_an_error(self):
        case = self.case("blank_skip")
        for name in ("none", "empty", "blank_space"):
            with self.subTest(case=name):
                outcome = case[name]
                self.assertTrue(outcome.get("ok"),
                                f"留空必须是「待补」而不是保存错误：{outcome}")
                self.assertEqual(outcome.get("changes"), [], "留空不产生变更")
                self.assertEqual(outcome.get("height"), 3, "留空不得清掉已有数值")

    def test_invalid_dimension_values_are_still_rejected(self):
        case = self.case("blank_skip")
        for name in ("text", "zero", "negative"):
            with self.subTest(case=name):
                self.assertFalse(case[name].get("ok"), f"{name} 仍然必须被拒绝")
        self.assertTrue(case["valid"].get("ok"), "正常数值必须照旧可改")
        self.assertEqual(case["valid"].get("height"), 7.5)

    # -------------------------------------------------- C6 Agent 同一份服务
    def test_agent_tool_initializes_base_feature_and_persists(self):
        case = self.case("agent_tool")
        outcome = case["ok"]
        self.assertFalse(outcome.get("error"), f"Agent 补基体必须成功：{outcome}")
        self.assertTrue(outcome.get("applied"), f"必须如实回报已应用：{outcome}")
        features = ((case["ir_after_ok"] or {}).get("parts") or [{}])[0].get("features") or []
        self.assertEqual(len(features), 1, "IR 里必须只剩这一个基体特征")
        self.assertEqual(features[0].get("type"), "box")
        self.assertEqual(features[0].get("height"), 20)
        self.assertIn("agent_part_edit", case["audit"], f"必须写审计：{case['audit']}")
        self.assertIn("agent_edited", case["versions"], f"必须留版本快照：{case['versions']}")

    def test_agent_tool_refuses_to_invent_dimensions(self):
        case = self.case("agent_tool")
        outcome = case["missing_dim"]
        self.assertTrue(outcome.get("error"), f"缺尺寸时必须返回 error：{outcome}")
        self.assertFalse(outcome.get("applied", False), "缺尺寸不得应用")
        parts = (case["ir_after_missing_dim"] or {}).get("parts") or [{}]
        self.assertEqual(parts[0].get("features"), [],
                         "被拒绝的调用不得改动 IR：Agent 不许自己编尺寸")

    def test_agent_tool_refuses_illegal_type_and_free_fields(self):
        case = self.case("agent_tool")
        for name in ("bad_type", "free_field"):
            with self.subTest(case=name):
                outcome = case[name]
                self.assertTrue(outcome.get("error"), f"{name} 必须返回 error：{outcome}")
                self.assertFalse(outcome.get("applied", False), f"{name} 不得应用")
        parts = (case["ir_after_rejects"] or {}).get("parts") or [{}]
        self.assertEqual(parts[0].get("features"), [],
                         "被拒绝的调用必须保持 IR 原样（尤其不许出现模板外字段）")

    def test_agent_tool_schema_closes_the_base_feature(self):
        tool = block_from(OC_AGENT, '"name": "UpdatePartParameters"')
        self.assertTrue(tool, "找不到 UpdatePartParameters 的工具定义")
        self.assertIn("base_feature", tool, "工具 schema 必须给出补基体的封闭入参")
        self.assertRegex(tool, r'"plate"[\s\S]{0,80}"box"[\s\S]{0,80}"cylinder"',
                         "类型必须用 enum 固定成三选一，不能自由填")
        for forbidden in ("hole", "fillet", "chamfer"):
            with self.subTest(forbidden=forbidden):
                self.assertNotRegex(tool, r'enum"\s*:\s*\[[^\]]*"%s"' % forbidden,
                                    f"{forbidden} 不得成为可选的基体类型")
        self.assertTrue(
            ("不得编造" in tool or "不要编造" in tool or "不能编造" in tool),
            "工具描述必须写清「不得编造尺寸」，否则模型会自己猜一个值")
        self.assertTrue(("询问" in tool or "问清楚" in tool or "先问" in tool),
                        "工具描述必须要求缺参数时先问用户，而不是调用工具")

    def test_agent_tool_reuses_part_edit_service(self):
        update = def_block(OC_AGENT, "def _update_part(")
        self.assertTrue(update, "找不到 oc_agent._update_part()")
        self.assertIn("part_edit", update, "Agent 必须复用同一份 part_edit，不许另写一套校验")
        self.assertIn("base_feature", update, "Agent 路径要把 base_feature 透给同一份服务")

    # -------------------------------------------------- C5 单件重生成
    def test_base_feature_edit_then_single_part_regenerate(self):
        if not self.data.get("cadquery"):
            self.skipTest("本机缺少 CadQuery，跳过真实单件重生成走查")
        case = self.case("regenerate")
        self.assertEqual(case["save_ir_http"], 200, "补完模板后回存 IR 必须成功")
        self.assertEqual(case["http"], 200, f"单件重生成必须成功：{case['body']}")
        geometry = case["store_3d"] or {}
        parts = {p.get("part_id"): p for p in geometry.get("parts") or []}
        self.assertTrue((parts.get("P-005") or {}).get("ok"),
                        f"补完尺寸的 P-005 必须能生成成功：{parts.get('P-005')}")
        drawings = case["store_2d"] or {}
        dparts = {p.get("part_id"): p for p in drawings.get("parts") or []}
        self.assertTrue((dparts.get("P-005") or {}).get("ok"),
                        f"同一零件补齐后 2D 也要出图：{dparts.get('P-005')}")

    def test_single_part_regenerate_leaves_other_parts_untouched(self):
        if not self.data.get("cadquery"):
            self.skipTest("本机缺少 CadQuery，跳过真实单件重生成走查")
        case = self.case("regenerate")
        for key, label in (("store_3d", "3D"), ("store_2d", "2D")):
            with self.subTest(doc=label):
                doc = case[key] or {}
                parts = {p.get("part_id"): p for p in doc.get("parts") or []}
                entry = parts.get("P-001")
                self.assertIsNotNone(entry, f"{label} 里 P-001 的既有结果不得被删掉")
                if label == "3D":
                    self.assertEqual(entry.get("volume_mm3"), 12345.6,
                                     "只重生成 P-005，不得改动 P-001 的既有结果")
                    self.assertTrue(entry.get("stl_url"), "P-001 的既有下载链接不得丢")
                else:
                    self.assertEqual(entry.get("views"), {"front": "P-001.svg"},
                                     "只重生成 P-005，不得改动 P-001 的 2D 结果")

    def test_imported_step_projects_still_refuse_ir_edits(self):
        self.assertIn("def blocks_feature_edit(", PART_EDIT,
                      "导入的精确 3D 项目仍必须禁止文本改参")
        update = def_block(OC_AGENT, "def _update_part(")
        self.assertIn("blocks_feature_edit", update, "Agent 路径必须先过这道门")

    def test_workbench_entry_also_shares_the_same_capability(self):
        """老入口（workbench-chat）也要能提出同样的受控补基体，避免两条会话入口规则漂移。"""
        model = def_block(MAIN, "class WorkbenchPartEdit(")
        self.assertTrue(model, "找不到 WorkbenchPartEdit 请求模型")
        self.assertIn("base_feature", model,
                      "老入口的请求模型必须能带上补基体，否则两个入口能力会漂移")
        apply_body = def_block(MAIN, "def _apply_workbench_chat_edit(")
        self.assertIn("part_edit.apply_edit", apply_body,
                      "老入口必须继续复用同一份 part_edit")
        self.assertIn("base_feature", apply_body, "老入口要把 base_feature 透给同一份服务")

    def test_no_part_classification_is_introduced(self):
        for name in FORBIDDEN_FIELD_NAMES:
            with self.subTest(field=name):
                self.assertNotIn(name, IR_MODEL,
                                 "本批明确不做零件分类/是否需要 CAD 字段（物理表要能长期稳定）")


class BaseFeatureFrontendRedTest(unittest.TestCase):
    """前端：空特征时给固定的三选一模板，不给自由加字段的自由度。"""

    @classmethod
    def setUpClass(cls):
        cls.app = APP

    def base_types_const(self) -> set:
        match = re.search(r"BASE_FEATURE_TYPES\s*=\s*\[([^\]]*)\]", self.app)
        self.assertIsNotNone(match, "必须用 BASE_FEATURE_TYPES 常量锁死可选基体类型")
        found = set(re.findall(r"['\"](\w+)['\"]", match.group(1)))
        return found

    def test_base_feature_type_whitelist_is_closed(self):
        self.assertEqual(self.base_types_const(), ALLOWED_BASE_TYPES,
                         "基体类型白名单必须恰好是 plate / box / cylinder")

    def test_editor_renders_the_template_from_the_whitelist(self):
        for token in ("parameter-base-feature", "data-base-type", "data-base-dim"):
            with self.subTest(token=token):
                self.assertIn(token, self.app,
                              f"空特征零件的参数编辑器必须渲染基体模板（缺 {token}）")
        anchor = self.app.find("data-base-type")
        window = self.app[max(0, anchor - 1500): anchor + 1500]
        self.assertIn("BASE_FEATURE_TYPES", window,
                      "类型下拉必须由 BASE_FEATURE_TYPES 白名单生成，避免以后被人手加类型")

    def test_editor_branch_for_empty_features(self):
        self.assertRegex(
            self.app,
            r"(?:!\s*part\.features\.length|\(part\.features \|\| \[\]\)\.length ===? 0"
            r"|part\.features\s*\|\|\s*\[\s*\]\s*\)\s*\.length\s*===?\s*0)",
            "必须有「features 为空」的分支，否则空特征零件仍然只剩名称/数量/材料")

    def test_save_writes_the_template_into_first_feature(self):
        body = block_from(self.app, "async function savePartEdits(")
        self.assertTrue(body, "找不到 savePartEdits()")
        for token in ("data-base-type", "data-base-dim"):
            with self.subTest(token=token):
                self.assertIn(token, body, f"保存时必须读取模板（{token}）")
        self.assertRegex(body, r"features\[0\]\s*=",
                         "保存必须把模板写进 features[0]，而不是只改已有的特征")
        self.assertRegex(body, r"===?\s*\"\"[\s\S]{0,80}?null",
                         "留空的尺寸必须保持空（null），不得填 0 或默认值")

    def test_app_js_asset_version_is_bumped(self):
        self.assertIn("app.js?v=", INDEX_HTML, "index.html 必须继续带版本号引用 app.js")
        self.assertNotIn("app.js?v=20260914-split1", INDEX_HTML,
                         "本批改了 app.js，index.html 的 ?v= 必须同步 bump，否则浏览器还在跑旧代码")

    def test_no_free_form_feature_adding(self):
        for token in ("添加特征", "新增特征", "自定义字段"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.app,
                                 "本批不提供自由新增特征/字段的入口（物理表要能长期稳定）")
        self.assertNotRegex(self.app, r'data-base-type[\s\S]{0,600}?value="(hole|hole_pattern|fillet|chamfer)"',
                            "基体类型下拉里不得混入其它特征类型")


if __name__ == "__main__":
    unittest.main()
