"""红测：图纸零件的下游结论必须读得回来（落库 + 两个依据路由 + 进度文案一致）。

Spec：`docs/specs/packaging-parts-downstream-readback.md`
依赖：第 3 层（单件工艺与成本）、第 2 层（面板数据源）。

现状缺口（34 线上实测，不是推断）：

  · 项目 `f1417060ae9d`：每件跑完 `POST …/packaging-parts/{code}/process` → 任务 succeeded、
    `result.plan.steps` **4 道工序**，紧接着读 `GET …/packaging-parts/{code}/process`
    → `{"plan": null, "validation": null, "coverage": null}`（刷新即丢）；
  · `GET …/packaging-parts/{code}/process-lookup` / `…/cost-lookup`
    → **404 Not Found**（前端 `inline-analysis.js:116` 固定要读这两个路径，「依据」面板永远空）；
  · 同一次任务里进度上报「共 0 道工序：沿用库内 0 道、缺失需新建 0 道」——报的是**库内**计数，
    而产出是 4 道工序，用户看到"0 道工序"会以为没有结果。

不许放宽：`processability()` 的三道门槛、任务状态机与 `dedup_key` 口径、结论不写技术 IR。

纪律：全部离线（不连 PG、不调模型、不起服务）；落库用内存后端打桩，不写任何业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PARTS = importlib.import_module("tech_app.backend.services.packaging_parts")

MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"

PROJECT_ID = "f1417060ae9d"
SAVE_LOAD = ("save_part_process", "load_part_process", "save_part_cost", "load_part_cost")
DOC_KEYS = ("DOC_KEY_PROCESS", "DOC_KEY_COST")
LOOKUP_PATHS = ("process-lookup", "cost-lookup")

PLAN = {"part_id": "DWG-P07", "part_name": "图纸零件 P07", "material": "灰板 2mm",
        "steps": [{"step_no": 10, "name": "板料准备与开料"}, {"step_no": 20, "name": "主体成形"},
                  {"step_no": 30, "name": "去毛刺"}, {"step_no": 40, "name": "最终检验"}],
        "overall_note": "AI 未返回结构化工序明细，系统已按零件分类与几何特征补齐通用工序骨架"}


class MemoryBackend:
    """最小内存后端：只实现 get_doc / put_doc，够 `packaging_parts` 的版本化范式。"""

    def __init__(self):
        self.docs = {}
        self.writes = 0

    def get_doc(self, project_id, key):
        return self.docs.get((project_id, key))

    def put_doc(self, project_id, key, doc):
        self.writes += 1
        self.docs[(project_id, key)] = doc
        return doc


class ReadbackCase(unittest.TestCase):
    maxDiff = None

    def exports(self):
        missing = [name for name in SAVE_LOAD if not callable(getattr(PARTS, name, None))]
        if missing:
            self.fail("packaging_parts 必须导出 %s（Spec §2.1 命名契约）；缺 %s"
                      % ("/".join(SAVE_LOAD), "、".join(missing)))

    def memory(self):
        backend = MemoryBackend()
        patch = mock.patch.object(PARTS, "get_backend", lambda: backend)
        patch.start()
        self.addCleanup(patch.stop)
        return backend

    def text(self, path):
        return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# A 组：落库 / 读回（Spec §2.1）
# --------------------------------------------------------------------------- #
class APersistAndRead(ReadbackCase):

    def test_a1_names_are_exported(self):
        self.exports()

    def test_a2_doc_keys_are_declared(self):
        for name in DOC_KEYS:
            value = getattr(PARTS, name, None)
            self.assertTrue(isinstance(value, str) and value,
                            "packaging_parts 必须有常量 %s（Spec §2.1 命名契约）" % name)
        self.assertNotEqual(getattr(PARTS, "DOC_KEY_PROCESS", ""),
                            getattr(PARTS, "DOC_KEY_COST", ""),
                            "工艺与成本必须是两份文档，不许挤在同一个 key 里")

    def test_a3_process_round_trip(self):
        self.exports()
        self.memory()
        payload = {"part_code": "DWG-P07", "parts_id": "parts:x", "engine_version": "packaging-part-process/1",
                   "plan": PLAN, "validation": {"step_count": 4}, "coverage": {"library_steps": 0},
                   "source": {"task_id": "t1", "actor": "PE1"}}
        PARTS.save_part_process(PROJECT_ID, payload)
        got = PARTS.load_part_process(PROJECT_ID, "DWG-P07")
        self.assertEqual((got or {}).get("plan"), PLAN,
                         "存进去的工艺结论必须原样读回来（这正是 34 上 plan 恒为 null 的那一步）")
        self.assertEqual((got or {}).get("part_code"), "DWG-P07")

    def test_a4_cost_round_trip(self):
        self.exports()
        self.memory()
        analysis = {"part_id": "DWG-P07", "unit_cost": None, "items": []}
        PARTS.save_part_cost(PROJECT_ID, {"part_code": "DWG-P07", "parts_id": "parts:x",
                                          "analysis": analysis, "summary": {"quantity": 1}})
        got = PARTS.load_part_cost(PROJECT_ID, "DWG-P07")
        self.assertEqual((got or {}).get("analysis"), analysis)

    def test_a5_repeat_save_is_idempotent(self):
        self.exports()
        backend = self.memory()
        payload = {"part_code": "DWG-P07", "parts_id": "parts:x", "plan": PLAN}
        PARTS.save_part_process(PROJECT_ID, payload)
        first = backend.writes
        PARTS.save_part_process(PROJECT_ID, dict(payload))
        self.assertEqual(backend.writes, first,
                         "同一份结论重复落库不许新增版本（Spec §2.1）")

    def test_a6_missing_part_reads_empty_not_error(self):
        self.exports()
        self.memory()
        self.assertEqual(PARTS.load_part_process(PROJECT_ID, "DWG-P99") or {}, {},
                         "没跑过的零件读回空文档，不许抛异常、不许 404（Spec §2.1）")

    def test_a7_part_code_mismatch_is_not_reused(self):
        self.exports()
        self.memory()
        PARTS.save_part_process(PROJECT_ID, {"part_code": "DWG-P07", "plan": PLAN})
        got = PARTS.load_part_process(PROJECT_ID, "DWG-P08")
        self.assertNotEqual((got or {}).get("plan"), PLAN,
                            "换了零件必须读不到别人的结论（Spec §2.1）")


# --------------------------------------------------------------------------- #
# B 组：路由读回（Spec §2.1 / §2.2）
# --------------------------------------------------------------------------- #
class BRoutes(ReadbackCase):

    def route_body(self, name):
        import ast
        tree = ast.parse(self.text(MAIN_PY))
        lines = self.text(MAIN_PY).splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return "\n".join(lines[node.lineno - 1: node.end_lineno])
        return ""

    def test_b1_get_process_reads_the_record(self):
        body = self.route_body("get_packaging_part_process")
        self.assertTrue(body, "main.py 必须有 get_packaging_part_process")
        self.assertIn("load_part_process", body,
                      "GET 单件工艺必须读落库文档，不许恒回 null（Spec §2.1）")

    def test_b2_get_cost_reads_the_record(self):
        body = self.route_body("get_packaging_part_cost")
        self.assertTrue(body, "main.py 必须有 get_packaging_part_cost")
        self.assertIn("load_part_cost", body,
                      "GET 单件成本必须读落库文档，不许恒回 null（Spec §2.1）")

    def test_b3_lookup_routes_are_registered(self):
        source = self.text(MAIN_PY)
        for suffix in LOOKUP_PATHS:
            self.assertIn("packaging-parts/{part_code}/" + suffix, source,
                          "必须注册 GET …/packaging-parts/{part_code}/%s（Spec §2.2）" % suffix)

    def test_b4_lookup_reads_parts_not_the_tech_ir(self):
        for name in ("get_packaging_part_process_lookup", "get_packaging_part_cost_lookup"):
            body = self.route_body(name)
            self.assertTrue(body, "main.py 必须有 %s（Spec §2.2 命名契约）" % name)
            self.assertNotIn("store.load_ir", body,
                             "%s 不许读技术 IR（图纸零件不在 IR 里，Spec §2.2）" % name)
            self.assertIn("packaging_parts", body, "%s 必须经零件文档取数据" % name)

    def test_b5_tasks_write_a_version_on_success(self):
        source = self.text(MAIN_PY)
        self.assertIn("save_part_process", source,
                      "工艺任务成功时必须落一版结论（Spec §2.1）")
        self.assertIn("save_part_cost", source,
                      "成本任务成功时必须落一版结论（Spec §2.1）")


# --------------------------------------------------------------------------- #
# C 组：进度文案（Spec §2.3）
# --------------------------------------------------------------------------- #
class CProgressText(ReadbackCase):

    def test_c1_progress_reports_the_produced_step_count(self):
        source = self.text(MAIN_PY)
        self.assertIn("len(plan_dict", source.replace("len(plan.steps", "len(plan_dict"),
                      "进度里的工序数必须是这次产出的工序数（Spec §2.3）")
        self.assertNotIn('f"  ↳ 共 {coverage', source,
                         "不许把库内计数当作工序数上报（Spec §2.3）")

    def test_c2_overall_note_is_visible(self):
        source = self.text(MAIN_PY)
        self.assertIn("overall_note", source,
                      "系统补通用骨架这类事实必须出现在任务进度/结果里（Spec §2.3）")


# --------------------------------------------------------------------------- #
# D 组：回归护栏（Spec §2.4）
# --------------------------------------------------------------------------- #
class DGuards(ReadbackCase):

    def test_d1_processability_thresholds_unchanged(self):
        source = self.text(PARTS_PY)
        for code in ("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                     "PACKAGING_PART_NOT_FOUND"):
            self.assertIn(code, source, "三道门槛不许被本批放宽（Spec §2.4）")

    def test_d2_conclusions_still_do_not_touch_the_tech_ir(self):
        source = self.text(PARTS_PY)
        self.assertNotIn("save_ir", source, "结论仍然不许写技术 IR（Spec §2.4）")

    def test_d3_existing_api_untouched(self):
        for name in ("extract", "save_parts", "load_parts", "bind_rows", "summarize"):
            self.assertTrue(callable(getattr(PARTS, name, None)),
                            "既有 API %s 不许改名/删除（Spec §2.4）" % name)


if __name__ == "__main__":
    unittest.main()
