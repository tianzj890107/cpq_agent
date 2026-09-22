"""红测：回传报价记录要能回答"这一版是按哪一版成本发的"和"现在成本变了没有"。

Spec：`docs/specs/packaging-handoff-input-drift-disclosure.md`

现状缺口（代码级，都可指到行）：
  · `packaging_handoff.py:451 load_handoff()` 把库里那一行原样吐回去 —— 没有 `stale` /
    `stale_reasons` / `source_versions`，也没有一个地方把"这一版是按哪一版成本发的"说出来；
  · `result_version_of()`（`:128`）是成本结果版本的唯一口径，`send_to_quote()`（`:400`）
    落了 `cost_result_version`，但**读侧从不和当前成本比**：成本重算之后，上一次回传记录
    照旧读起来像"当前有效"；
  · `:458 handoff_versions()` 同样只做排序，一个漂移判定都不带；
  · 当前成本读不到（成本没算过 / 存储异常）与"成本变了"在读回体上分不出来。

纪律：只读源码 + 假仓库 / 假成本；不连 PG / SQLite 生产库、不发 HTTP、不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_handoff as handoff        # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-HANDOFFPIN-001"

OLD_COST = {"built": True, "quote_quantity": 1000, "total_cost": 1000.0}
NEW_COST = {"built": True, "quote_quantity": 1000, "total_cost": 1234.5}
OLD_VERSION = handoff.result_version_of(OLD_COST)
NEW_VERSION = handoff.result_version_of(NEW_COST)


def record(*, cost_result_version=OLD_VERSION, version_no=2, scenario="default"):
    """落库回来的交接记录（`_handoff_row()` 的形状：JSON 列已解析）。"""
    return {
        "handoff_no": "pkghandoff:%s:%s:default:%d" % (PID, REQ_NO, version_no),
        "project_id": PID, "requirement_no": REQ_NO, "scenario_code": scenario,
        "version_no": version_no, "industry": "packaging",
        "engine_version": handoff.ENGINE_VERSION,
        "handoff_version": handoff.HANDOFF_VERSION,
        "handoff_kind": handoff.HANDOFF_KIND,
        "cost_profile": handoff.COST_PROFILE, "pricing_profile": handoff.PRICING_PROFILE,
        "cost_result_version": cost_result_version,
        "package_fingerprint": "fp-v%d" % version_no,
        "package": {"engine_version": handoff.ENGINE_VERSION, "result_version": OLD_VERSION},
        "has_gaps": False, "gap_codes": [], "gap_waiver": None,
        "target_quote_session_id": "quote-sess-1", "target_task_id": "task-1",
        "sent_by": "CF1", "sent_at": "2026-09-22 10:00:00",
        "created_at": "2026-09-22 10:00:00",
    }


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原（属性本不存在则删除）。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []
        self._missing = object()

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name, self._missing)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, old in reversed(self.saved):
            if old is self._missing:
                try:
                    delattr(owner, name)
                except AttributeError:
                    pass
            else:
                setattr(owner, name, old)
        return False


def _load(row, cost=..., cost_error=None, calls=None):
    """在假仓库 / 假成本上跑 `load_handoff()`：只比对返回值，不落任何库。"""
    def loader(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        if cost_error is not None:
            raise cost_error
        return dict(cost or {})

    with _Patch((handoff, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (handoff.da_repo, "load_packaging_handoff", lambda *a, **k: row),
                (handoff.packaging_cost, "load_cost", loader)):
        return handoff.load_handoff(PID, REQ_NO)


def _load_versions(rows, cost=..., cost_error=None, calls=None):
    def loader(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        if cost_error is not None:
            raise cost_error
        return dict(cost)

    with _Patch((handoff, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (handoff.da_repo, "packaging_handoffs", lambda *a, **k: list(rows)),
                (handoff.packaging_cost, "load_cost", loader)):
        return handoff.handoff_versions(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# J 组：记录里的版本 vs 当前成本
# --------------------------------------------------------------------------- #
class JHandoffInputDrift(unittest.TestCase):
    def test_j1_current_cost_moved_forward_is_reported(self):
        result = _load(record(), cost=NEW_COST)
        self.assertIs(True, result.get("stale"),
                      "成本重算之后旧回传必须被标成过期（Spec §2.1）："
                      "现在读接口一个漂移判定都不给")
        self.assertIn("cost_recomputed", result.get("stale_reasons") or [],
                      "必须说清是哪一项变了（cost_recomputed），不许只给一个布尔")
        versions = result.get("source_versions") or {}
        self.assertEqual(OLD_VERSION, versions.get("cost_result_version"),
                         "`source_versions.cost_result_version` 是**发出去那一刻**那一版："
                         "不许拿当前成本现取值兜")
        self.assertEqual("fp-v2", versions.get("package_fingerprint"),
                         "交接包的指纹也必须能读出来（据它判重）")
        self.assertEqual(handoff.HANDOFF_VERSION, versions.get("handoff_version"))

    def test_j2_unchanged_cost_stays_clean(self):
        result = _load(record(), cost=OLD_COST)
        self.assertFalse(bool(result.get("stale")), "成本没变就不是 stale")
        self.assertEqual([], result.get("stale_reasons") or [],
                         "没有漂移时原因清单必须是空的（不许拿键缺失来充数）")

    def test_j3_legacy_record_without_a_version_is_disclosed(self):
        result = _load(record(cost_result_version=None), cost=NEW_COST)
        self.assertIn("provenance_missing", result.get("stale_reasons") or [],
                      "历史记录没有成本版本本身就要披露（Spec §2.1）")
        versions = result.get("source_versions") or {}
        self.assertEqual("", versions.get("cost_result_version"),
                         "没有来源就给空串，**不许**拿当前成本兜一个'看起来对'的版本")

    def test_j4_unreadable_cost_is_unknown_not_a_verdict(self):
        result = _load(record(), cost_error=RuntimeError("cost store down"))
        flag = result.get("cost_unavailable") or {}
        self.assertEqual("cost_unavailable", flag.get("code"),
                         "当前成本读不到必须显式披露（Spec §2.1）")
        self.assertNotIn("cost_recomputed", result.get("stale_reasons") or [],
                         "读不到成本时'比较不了'，不许断言成本变过")
        self.assertEqual(OLD_VERSION, (result.get("source_versions") or {})
                         .get("cost_result_version"), "读不到当前成本也不影响返回存的那份")
        not_built = _load(record(), cost={"built": False, "items": []})
        self.assertEqual("cost_unavailable", (not_built.get("cost_unavailable") or {}).get("code"),
                         "成本还没算过也算'比较不了'（不是'变了'）")

    def test_j5_no_handoff_record_keeps_the_empty_contract(self):
        result = _load(None, cost=OLD_COST)
        self.assertEqual({}, result, "没有回传记录时逐字给 {}（既有口径不变）")

    def test_j6_version_list_carries_the_same_verdict(self):
        rows = [record(version_no=1, cost_result_version=OLD_VERSION),
                record(version_no=2, cost_result_version=NEW_VERSION)]
        calls: list = []
        result = _load_versions(rows, cost=NEW_COST, calls=calls)
        self.assertEqual([2, 1], [row.get("version_no") for row in result],
                         "既有排序（version_no 降序）逐字不变")
        self.assertIs(True, result[1].get("stale"),
                      "第 1 版是按旧成本发的 → 必须报 stale（Spec §2.1）")
        self.assertIn("cost_recomputed", result[1].get("stale_reasons") or [])
        self.assertFalse(bool(result[0].get("stale")), "第 2 版就是当前成本 → 不 stale")
        self.assertEqual(OLD_VERSION, (result[1].get("source_versions") or {})
                         .get("cost_result_version"))
        self.assertEqual(NEW_VERSION, (result[0].get("source_versions") or {})
                         .get("cost_result_version"))
        self.assertLessEqual(len(calls), 1,
                             "当前成本只许读一次（Spec §2.1）：不许每行读一次库")

    def test_j7_existing_keys_are_verbatim(self):
        row = record()
        result = _load(row, cost=NEW_COST)
        for key in ("handoff_no", "version_no", "cost_result_version", "package_fingerprint",
                    "has_gaps", "gap_codes", "package", "sent_by", "sent_at"):
            self.assertEqual(row[key], result.get(key), "既有键 %s 必须逐字不变" % key)


if __name__ == "__main__":
    unittest.main()
