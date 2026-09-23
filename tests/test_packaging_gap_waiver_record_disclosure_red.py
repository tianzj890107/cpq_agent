"""红测：有人签过的成本缺口放行留痕，读不出来时就悄悄消失 —— 门禁分不出「没签过」和「签了但这份留痕不能用」。

Spec：`docs/specs/packaging-gap-waiver-record-must-not-silently-disappear.md`

现状缺口（本机实测，打桩 `store` / `persistence` / 假依赖模块，一次盘都没写）：
  · `packaging_drawing_flow/gates.py:139 _gap_waiver()` 的返回只有 `dict`（留痕成立）与 `None`（其余全部）；
    五条拒绝理由（`:157` JSON 解析异常、`:161` 不是对象、`:166` by/at/reason 有空、`:170` codes 空、
    `:173` codes 不覆盖）共用一个 `None`；
  · 调用点 `:241-249` 只拿得到"有 / 没有"这一个二值 → 下列六种"留痕其实存在但没被采用"的交接记录，
    与"从来没签过"在返回体、行上的键、`blocking_message()` 的用户文案上**逐字相同**：

        键盘缺席 / `""` / `"{not json"` / `"[1,2]"` / `by|at|reason` 有空 / `codes: []` / `codes: ["other_code"]`
        → 行上键都是 [code, message, source]，都无 waived / waiver / waiver_invalid，
          文案都是「成本仍存在缺口，缺口清零后才能生成正式报价」

  · 同文件 `:97 _READ_SEVERITY` / `:203 _finish()` 已经为"上游读不到"单独披露过
    （`reads` / `reads_unavailable`）—— 留痕这条链漏了同样的处理。

纪律：打桩 + 假依赖模块，纯内存；不连 PG / SQLite、不建项目、不写盘、不起服务、不发 HTTP。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 包 `__init__` 里有一个同名函数 `gates()`，会把子模块属性遮掉 —— 走 importlib 拿真模块。
gates = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.gates")

PID = "testpid00001"
GATE_SRC = ROOT / "tech_app" / "backend" / "services" / "packaging_drawing_flow" / "gates.py"
GAP_CODE = "loss_rate_missing"
WAIVER = {"by": "PE1", "at": "2026-09-20T11:00:00+08:00",
          "reason": "客户已电话同意按首版估算走", "codes": [GAP_CODE]}
ROW_MESSAGE = "成本仍存在缺口，缺口清零后才能生成正式报价"
BLOCKING_MESSAGE = ROW_MESSAGE
REASONS = ("unreadable_json", "not_an_object", "missing_fields", "empty_codes",
           "codes_not_covering")
INVALID_CODE = "cost_gap_waiver_unusable"
CONFIRMED_FIELDS = {"inner_length": 70.0, "inner_width": 40.0, "inner_height": 120.0,
                    "closure_type": "tuck", "face_paper_gsm": 350.0, "v_groove": False,
                    "quote_quantity": 1000.0}
COST = {"built": True, "has_gaps": True, "items": [],
        "gaps": [{"code": GAP_CODE}], "gap_codes": [GAP_CODE]}


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
        for owner, name, old in reversed(self.saved):
            setattr(owner, name, old)
        return False


class _Module:
    def __init__(self, **functions):
        for name, value in functions.items():
            setattr(self, name, value)


def _requirement():
    data = dict(CONFIRMED_FIELDS)
    data["field_sources"] = {key: "manual" for key in CONFIRMED_FIELDS}
    data["field_provenance"] = {key: {"status": "confirmed", "origin": "user_confirmed"}
                                for key in CONFIRMED_FIELDS}
    return {"project_id": PID, "status": "draft", "data": data}


def _engines(*, handoff=None, cost=None):
    return {
        "packaging_match": _Module(load_box_match=lambda *a: {
            "decision": "confirmed", "confirmed_box_type": "folding_carton"}),
        "packaging_bom": _Module(load_bom=lambda *a: {
            "built": True, "generated_at": "2026-09-20T11:00:00+08:00"}),
        "packaging_route": _Module(load_route=lambda *a: {"built": True, "status": "confirmed"}),
        "packaging_cost": _Module(load_cost=lambda *a: dict(cost if cost is not None else COST),
                                  minimum_charge_policy=lambda: {"status": "chosen"}),
        "packaging_handoff": _Module(load_handoff=lambda *a: dict(handoff or {})),
    }


def _stages(*, handoff=None, cost=None):
    engines = _engines(handoff=handoff, cost=cost)

    def resolve(name):
        return engines.get(name)

    with _Patch((gates.store, "load_requirement", lambda project_id: _requirement()),
                (gates.persistence, "load_flow", lambda project_id: {}),
                (gates.anchor_mod, "current_anchor",
                 lambda project_id: {"unit_status": "confirmed"}),
                (gates.anchor_mod, "requirement_snapshot_version",
                 lambda project_id: "reqsnap/1:abc")):
        return gates.build(PID, resolve=resolve)["stages"]


def entry_of(handoff):
    """一个交接记录 → `quote_publish` 段（唯一会看留痕的那一段）。"""
    return _stages(handoff=handoff)["quote_publish"]


def gap_row(entry):
    for row in entry.get("blocking") or []:
        if row.get("code") == "cost_gaps_unresolved":
            return row
    return None


class WaiverRecordDisclosure(unittest.TestCase):
    maxDiff = None

    # ------------------------------------------------------------------ R1
    def test_r1_a_valid_waiver_is_still_recognised_and_has_no_invalid_key(self):
        entry = entry_of(dict(has_gaps=True,
                             gap_waiver_json=json.dumps(WAIVER, ensure_ascii=False)))
        row = gap_row(entry)
        self.assertTrue(row, "缺口未清时 cost_gaps_unresolved 必须还在 blocking（Spec §2.2 冻结面）")
        self.assertEqual(row.get("waived"), True, "合法留痕仍要被认出来（Spec §2.3）")
        self.assertFalse(entry.get("waiver_invalid"),
                         "合法留痕不许再报「留痕不可用」（Spec §2.3）")
        self.assertEqual({key: entry["waiver"][key] for key in ("by", "at", "reason", "codes")},
                         WAIVER, "放行摘要四项与输入逐字一致（Spec §2.2 冻结面）")

    # ------------------------------------------------------------------ R2
    def test_r2_a_corrupt_record_is_disclosed_with_a_reason(self):
        entry = entry_of({"has_gaps": True, "gap_waiver_json": "{not json"})
        flag = entry.get("waiver_invalid") or {}
        self.assertTrue(flag,
                        "留痕存在但读不出来时必须披露（Spec §2.2）：今天它与「从来没签过」"
                        "在返回体、行上的键、用户文案上逐字相同")
        self.assertEqual(flag.get("code"), INVALID_CODE, "披露用稳定码（Spec §2.2）")
        self.assertEqual(flag.get("reason"), "unreadable_json", "读不出来的是 JSON 解析（Spec §2.1）")
        self.assertEqual(flag.get("codes"), [], "读不到码就给空列表，不许编（Spec §2.2）")
        row = gap_row(entry)
        self.assertEqual(row.get("waived"), None, "披露不是放宽：不合法留痕不许当放行（Spec §2.2）")
        self.assertEqual(row.get("waiver_unusable"), "unreadable_json",
                         "行上也要自证「这里本来有一份留痕」（Spec §2.2）")

    # ------------------------------------------------------------------ R3
    def test_r3_each_unusable_shape_gets_its_own_reason(self):
        cases = (
            ("unreadable_json", "{not json", []),
            ("not_an_object", json.dumps([1, 2]), []),
            ("missing_fields", json.dumps({**WAIVER, "at": ""}), WAIVER["codes"]),
            ("empty_codes", json.dumps({**WAIVER, "codes": []}), []),
            ("codes_not_covering", json.dumps({**WAIVER, "codes": ["other_code"]}),
             ["other_code"]),
        )
        for reason, raw, codes in cases:
            with self.subTest(reason=reason):
                entry = entry_of({"has_gaps": True, "gap_waiver_json": raw})
                flag = entry.get("waiver_invalid") or {}
                self.assertEqual(flag.get("reason"), reason,
                                 "拒绝原因必须逐条说得出（Spec §2.1）：%s" % raw)
                self.assertEqual(flag.get("code"), INVALID_CODE)
                self.assertEqual(flag.get("codes"), codes,
                                 "留痕里读到的码原样带出来（Spec §2.2）")
                self.assertEqual((gap_row(entry) or {}).get("waiver_unusable"), reason)

    # ------------------------------------------------------------------ R4
    def test_r4_no_record_at_all_stays_silent(self):
        absent = {"has_gaps": True}
        for label, handoff in (("键缺席", absent), ("None", {"has_gaps": True,
                                                         "gap_waiver_json": None}),
                               ("空串", {"has_gaps": True, "gap_waiver_json": ""}),
                               ("空白串", {"has_gaps": True, "gap_waiver_json": "   "})):
            with self.subTest(shape=label):
                entry = entry_of(handoff)
                self.assertFalse(entry.get("waiver_invalid"),
                                 "没有留痕时不许报「留痕不可用」（Spec §2.3）：%s" % label)
                self.assertFalse(entry.get("waiver"))

    # ------------------------------------------------------------------ R5
    def test_r5_disclosure_is_reachable_from_the_public_entry_and_adds_no_blocker(self):
        entry = entry_of({"has_gaps": True, "gap_waiver_json": json.dumps({"by": "PE1"})})
        self.assertTrue(entry.get("waiver_invalid"),
                        "披露必须能从公开入口 `gates.build()` 读到（Spec §2.2）")
        codes = [row.get("code") for row in entry.get("blocking") or []]
        self.assertNotIn(INVALID_CODE, codes,
                         "留痕不可用**不是**一条新门禁：不许塞进 blocking，"
                         "免得把 go/no-go 与退出码一起改了（Spec §2.2）")
        self.assertEqual(gates.blocking_message(entry), BLOCKING_MESSAGE,
                         "用户文案本批不动（Spec §2.4）：留痕不可用只走返回体与行上的键")
        self.assertEqual(entry.get("status"), "blocked", "结论不变（Spec §2.2 冻结面）")

    # ------------------------------------------------------------------ R6
    def test_r6_source_declares_the_closed_reason_set_and_the_two_keys(self):
        source = GATE_SRC.read_text(encoding="utf-8")
        reasons = getattr(gates, "WAIVER_INVALID_REASONS", None)
        self.assertIsNotNone(reasons,
                             "`gates.py` 必须有闭集常量 `WAIVER_INVALID_REASONS`（Spec §2.1）")
        self.assertEqual(tuple(reasons), REASONS,
                         "五个拒绝原因逐字冻结（Spec §2.1）")
        for key in ("waiver_invalid", "waiver_unusable"):
            self.assertIn(key, source, "`%s` 必须出现在 `gates.py` 里（Spec §2.2）" % key)

    # ------------------------------------------------------------------ R7
    def test_r7_the_four_judgements_are_frozen(self):
        shapes = ("{not json", json.dumps([1, 2]), json.dumps({**WAIVER, "by": ""}),
                  json.dumps({**WAIVER, "codes": []}), json.dumps({**WAIVER,
                                                                   "codes": ["other_code"]}))
        for raw in shapes:
            with self.subTest(raw=raw):
                entry = entry_of({"has_gaps": True, "gap_waiver_json": raw})
                row = gap_row(entry)
                self.assertTrue(row, "缺口未清时 cost_gaps_unresolved 不许少（Spec §2.2）")
                self.assertNotEqual(row.get("waived"), True,
                                    "不合法留痕不许当放行（Spec §2.2 冻结 `## 440` 的 C3）")
                self.assertFalse(entry.get("waiver"))
                self.assertEqual(row.get("message"), ROW_MESSAGE, "行上文案逐字不改（Spec §2.2）")
                self.assertEqual(entry.get("status"), "blocked")

    # ------------------------------------------------------------------ R8
    def test_r8_reason_set_is_total_and_one_to_one(self):
        shapes = ("{not json", json.dumps([1, 2]), json.dumps({**WAIVER, "reason": " "}),
                  json.dumps({**WAIVER, "codes": []}),
                  json.dumps({**WAIVER, "codes": ["other_code"]}))
        seen = []
        for raw in shapes:
            reason = (entry_of({"has_gaps": True, "gap_waiver_json": raw})
                      .get("waiver_invalid") or {}).get("reason")
            self.assertIn(reason, REASONS,
                          "拒绝原因必须落在闭集里（Spec §2.1）：%r" % reason)
            seen.append(reason)
        self.assertEqual(len(set(seen)), len(shapes),
                         "五种形态必须映射到五个**互不相同**的原因（Spec §2.1）：%r" % seen)
        self.assertEqual(set(seen), set(REASONS),
                         "闭集不许空转：五个原因都要真的能被触发（Spec §2.1）")
        silent = entry_of({"has_gaps": True})
        self.assertIsNone((silent.get("waiver_invalid") or {}).get("reason"),
                          "没留痕时一个原因都不报（Spec §2.3）")


if __name__ == "__main__":
    unittest.main()
