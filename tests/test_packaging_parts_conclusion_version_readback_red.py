"""红测：单件工艺/成本结论的读侧必须认零件文档版本。

Spec：`docs/specs/packaging-parts-conclusion-version-readback.md`

现状缺口（代码级，三处，都可指到行）：
  · 写侧已经记了版本（`main.py:8123` 的工艺结论文档带 `parts_id`，成本那一路同口径），
    但两个 GET 路由（`main.py:8273 get_packaging_part_process()` /
    `main.py:8294 get_packaging_part_cost()`）的返回体里**没有 `parts_id`、也不比对当前零件文档**
    —— 重解析换了 `parts_id` 之后，右栏照旧把上一版零件算出的结论显示成当前结果；
  · `packaging_parts.load_part_process()`（`:2329`）/ `load_part_cost()`（`:2339`）只按 `part_code`
    取"最近一版"，**没有按 `parts_id` 读的入口** —— 文档其实按 `(part_code, parts_id)` 分段存着
    （`_save_part_doc()` `:2298-2318`），存得下、读不出；
  · "没跑过" / "当前版" / "上一版零件算的"三种情形在返回体上长得完全一样。

纪律：只读源码 + 假后端 / 假文档；不连 PG / SQLite 生产库、不发 HTTP、不写任何文件、不落库。
（`packaging_parts` 是**模块级**导入 `get_backend`，所以补的是它自己的模块属性。）
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import main                                        # noqa: E402
from tech_app.backend.services import packaging_parts as parts           # noqa: E402

PID = "testpid00001"
PART_CODE = "DWG-P01"
OLD_PARTS_ID = "parts:oldoldoldoldold"
NEW_PARTS_ID = "parts:newnewnewnewnew"

CURRENT_DOC = {"parts": [{"part_code": PART_CODE}], "parts_id": NEW_PARTS_ID,
               "parts_hash": "hash-new"}
NEW_PLAN = {"steps": [{"seq": 1, "name": "模切"}]}
OLD_PLAN = {"steps": [{"seq": 1, "name": "上一版零件的工序"}]}
PROCESS_RECORD = {"part_code": PART_CODE, "parts_id": OLD_PARTS_ID, "plan": OLD_PLAN,
                  "validation": {"ok": True}, "coverage": {"summary": {}},
                  "source": {"computed_at": "2026-09-22 10:00:00"}}
COST_RECORD = {"part_code": PART_CODE, "parts_id": OLD_PARTS_ID,
               "analysis": {"unit_cost": 1.23}, "summary": "上一版零件的成本",
               "source": {"computed_at": "2026-09-22 10:00:00"}}


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, value in reversed(self.saved):
            setattr(owner, name, value)
        return False


def _route(name, record, current=CURRENT_DOC):
    """调两个 GET 读路由；`current=None` 表示当前零件文档读不到。"""
    loader = "load_part_process" if name == "get_packaging_part_process" else "load_part_cost"
    with _Patch((main, "_workflow_project", lambda *a, **k: None),
                (main, "_packaging_part_row", lambda *a, **k: {"row": {"part_code": PART_CODE}}),
                (parts, loader, lambda *a, **k: dict(record) if record else {}),
                (parts, "load_parts", lambda *a, **k: dict(current) if current else None)):
        return getattr(main, name)(PID, PART_CODE, {"username": "PE1"})


# --------------------------------------------------------------------------- #
# K 组：读侧必须回显版本并披露"这是上一版零件算的"
# --------------------------------------------------------------------------- #
class KConclusionVersionReadback(unittest.TestCase):
    def test_k1_reparsed_parts_make_the_conclusion_stale(self):
        for name, key in (("get_packaging_part_process", "plan"),
                          ("get_packaging_part_cost", "analysis")):
            body = _route(name, PROCESS_RECORD if key == "plan" else COST_RECORD)
            self.assertEqual(OLD_PARTS_ID, body.get("parts_id"),
                             "%s：读回体必须带出结论那一版的 `parts_id`（Spec §2.2）" % name)
            self.assertIs(True, body.get("stale"),
                          "%s：结论是上一版零件算的、当前零件文档已换版，必须标出来（Spec §2.2）"
                          "—— 现在右栏照旧把它显示成当前结果" % name)
            self.assertEqual("parts_reparsed", body.get("stale_reason"),
                             "%s：换版要给自己稳定的原因码 parts_reparsed" % name)
            self.assertTrue(body.get(key), "%s：既有结论字段逐字不变" % name)

    def test_k2_missing_parts_document_is_unknown_not_stale(self):
        body = _route("get_packaging_part_process", PROCESS_RECORD, current=None)
        self.assertEqual("parts_unknown", body.get("stale_reason"),
                         "当前零件文档读不到时'比较不了'，不许断言过期（Spec §2.2）")
        self.assertIsNot(True, body.get("stale"), "`parts_unknown` 时 `stale` 不许为 true")

    def test_k3_empty_state_also_carries_the_three_keys(self):
        body = _route("get_packaging_part_process", {})
        self.assertEqual("", body.get("parts_id"), "空态也要给 `parts_id`（键必须存在）")
        self.assertIs(False, bool(body.get("stale")), "空态不是过期")
        self.assertEqual("", body.get("stale_reason") or "", "空态不许给原因码")
        self.assertIsNone(body.get("plan"), "既有空态形状逐字不变")


# --------------------------------------------------------------------------- #
# K 组（护栏）：同版本与不传版本的既有行为
# --------------------------------------------------------------------------- #
class KExistingContractUnchanged(unittest.TestCase):
    def test_k4_same_version_is_not_stale(self):
        record = dict(PROCESS_RECORD)
        record["parts_id"] = NEW_PARTS_ID
        body = _route("get_packaging_part_process", record)
        self.assertIsNot(True, body.get("stale"),
                         "结论与当前零件文档同版时不许标过期")
        self.assertEqual(OLD_PLAN, body.get("plan"), "既有键逐字不变")
        self.assertEqual({"ok": True}, body.get("validation"), "既有键逐字不变")
        self.assertEqual(PART_CODE, body.get("part_code"), "既有键逐字不变")

    def test_k5_can_read_a_specific_version(self):
        items = [{"part_code": PART_CODE, "parts_id": NEW_PARTS_ID, "plan": NEW_PLAN},
                 {"part_code": PART_CODE, "parts_id": OLD_PARTS_ID, "plan": OLD_PLAN}]

        class _Backend:
            def get_doc(self, project_id, key):
                return {"items": items}

            def put_doc(self, project_id, key, doc):        # pragma: no cover - 只读用例
                raise AssertionError("本批读接口不许写库")

        with _Patch((parts, "get_backend", lambda: _Backend())):
            got = parts.load_part_process(PID, PART_CODE, OLD_PARTS_ID)
        self.assertEqual(OLD_PLAN, (got or {}).get("plan"),
                         "必须能按 `parts_id` 精确读回那一版结论（Spec §2.1）："
                         "文档里本来就按 (part_code, parts_id) 分段存着，现在读不出来")

    def test_k6_default_read_still_returns_the_latest(self):
        items = [{"part_code": PART_CODE, "parts_id": NEW_PARTS_ID, "plan": NEW_PLAN},
                 {"part_code": PART_CODE, "parts_id": OLD_PARTS_ID, "plan": OLD_PLAN}]

        class _Backend:
            def get_doc(self, project_id, key):
                return {"items": items}

            def put_doc(self, project_id, key, doc):        # pragma: no cover
                raise AssertionError("本批读接口不许写库")

        with _Patch((parts, "get_backend", lambda: _Backend())):
            got = parts.load_part_process(PID, PART_CODE)
        self.assertEqual(NEW_PLAN, (got or {}).get("plan"),
                         "不传 parts_id 时逐字保持今天的行为：同一 part_code 的最近一版")


if __name__ == "__main__":
    unittest.main()
