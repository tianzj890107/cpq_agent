"""红测：包装下游"做不下去"的原因必须**机器可分**，不能只回一句中文。

Spec：`docs/specs/packaging-downstream-block-code-parity.md`

现状缺口（2026-09-22 在 34 上真跑实测，不是推断）：

  · 一张 34 上的真实项目（`0f080b24c65d`，需求单 `REQ-E2E-JIUHE-001`）在一键解析跑完之后，
    下游四个入口给出的四句话**长得完全不一样，而且没有一句能拿来判分支**：
      - `POST …/requirement/box-match` → 400 `盒型匹配只对包装行业的需求单生效`
        （真实原因其实是"这张需求草稿没写 industry"）；
      - `POST …/requirement/packaging-cost` → 409 `工艺路线尚未确认，人工费无从取工时（Spec §2.13）`；
      - 再跑一次 → 409 `报价数量缺失或 ≤ 0，无法测算包装成本（Spec §2.13）`；
      - `downstream_prepare` → `blocked`，理由在 `blocking[].code = field_missing` 里。
  · `packaging_bom.BomError` / `packaging_route.RouteError` / `packaging_cost.CostError` 都已经是
    `(message, status_code, code)` 三件套，**只有 `packaging_match.BoxMatchError` 没有 `code`**：
    同一个系统里一半有码一半没码，前端就没法统一"缺什么就补什么"。
  · 最刺眼的一处：**需求单根本不存在**时（没有草稿），盒型匹配报的还是
    `盒型匹配只对包装行业的需求单生效` —— 用户按这句话去改行业，永远改不好。

纪律：只读源码 + 假 store；不连 PG、不发 HTTP、不写任何文件。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import (packaging_bom, packaging_cost,  # noqa: E402
                                       packaging_handoff, packaging_match,
                                       packaging_route)

#: 四个包装服务 + 回传，业务错误的同构契约：message / status_code / code。
ERROR_CLASSES = (packaging_match.BoxMatchError, packaging_bom.BomError,
                 packaging_route.RouteError, packaging_cost.CostError,
                 packaging_handoff.HandoffError)


def _patch_store(module, doc):
    real = module.store.load_requirement
    module.store.load_requirement = lambda project_id: doc
    return real


# --------------------------------------------------------------------------- #
# A 组：五个业务错误必须同构（BoxMatchError 现在没有 code）
# --------------------------------------------------------------------------- #
class TestAErrorParity(unittest.TestCase):
    def test_a1_every_packaging_error_carries_a_stable_code(self):
        bad = []
        for cls in ERROR_CLASSES:
            try:
                exc = cls("x", 409, "some_code")
            except TypeError:
                bad.append("%s：构造函数还没有第 3 个位置参数 code" % cls.__name__)
                continue
            if getattr(exc, "code", None) != "some_code":
                bad.append("%s：构造后 .code 不是 'some_code'" % cls.__name__)
            if getattr(exc, "status_code", None) != 409:
                bad.append("%s：status_code 被串了" % cls.__name__)
        self.assertEqual([], bad,
                         "包装业务错误必须同构（Spec §2.1）：\n" + "\n".join(bad))

    def test_a2_code_defaults_to_empty_string(self):
        for cls in ERROR_CLASSES:
            exc = cls("x", 409)
            self.assertTrue(hasattr(exc, "code"), "%s 缺少 code 属性" % cls.__name__)
            self.assertEqual("", str(getattr(exc, "code")),
                             "%s 的 code 缺省必须是空串，不能是 None" % cls.__name__)


# --------------------------------------------------------------------------- #
# B 组：盒型匹配的两个前置缺口必须分得开
# --------------------------------------------------------------------------- #
class TestBBoxMatchPreconditions(unittest.TestCase):
    def test_b1_missing_requirement_draft_is_not_reported_as_industry_mismatch(self):
        real = _patch_store(packaging_match, None)          # 没有需求草稿
        try:
            with self.assertRaises(packaging_match.BoxMatchError) as ctx:
                packaging_match.run_box_match("0f080b24c65d")
        finally:
            packaging_match.store.load_requirement = real
        self.assertEqual("requirement_draft_missing", str(getattr(ctx.exception, "code", "")),
                         "没有需求草稿时的码必须是 requirement_draft_missing，"
                         "现在报的是「只对包装行业生效」，用户按它改行业永远改不好")

    def test_b2_other_industry_gets_its_own_code(self):
        doc = {"project_id": "p", "requirement_no": "REQ-X", "data": {"industry": "battery"}}
        real = _patch_store(packaging_match, doc)
        try:
            with self.assertRaises(packaging_match.BoxMatchError) as ctx:
                packaging_match.run_box_match("0f080b24c65d")
        finally:
            packaging_match.store.load_requirement = real
        self.assertEqual("industry_missing", str(getattr(ctx.exception, "code", "")),
                         "非包装行业要有自己的码 industry_missing（它是可处置的：去需求单选行业）")

    def test_b3_two_preconditions_do_not_share_a_message(self):
        doc_none = None
        doc_battery = {"project_id": "p", "requirement_no": "REQ-X",
                       "data": {"industry": "battery"}}
        messages = []
        for doc in (doc_none, doc_battery):
            real = _patch_store(packaging_match, doc)
            try:
                packaging_match.run_box_match("0f080b24c65d")
                self.fail("这两条都该被拒绝")
            except packaging_match.BoxMatchError as exc:
                messages.append(str(exc))
            finally:
                packaging_match.store.load_requirement = real
        self.assertNotEqual(messages[0], messages[1],
                            "缺草稿与非本行业不能共用同一句文案（Spec §2.2）")


# --------------------------------------------------------------------------- #
# C 组：护栏（现在就应通过：既有码不许被本批改掉）
# --------------------------------------------------------------------------- #
class TestCExistingCodesAreFrozen(unittest.TestCase):
    def test_c1_bom_without_confirmed_box_type_keeps_its_code(self):
        doc = {"project_id": "p", "requirement_no": "REQ-X",
               "data": {"industry": "packaging"}}
        real_store = _patch_store(packaging_bom, doc)
        real_match = packaging_bom.da_repo.load_box_match
        packaging_bom.da_repo.load_box_match = lambda *a, **k: {}
        try:
            with self.assertRaises(packaging_bom.BomError) as ctx:
                packaging_bom.build_bom("p", "REQ-X")
        finally:
            packaging_bom.store.load_requirement = real_store
            packaging_bom.da_repo.load_box_match = real_match
        self.assertEqual("box_type_not_confirmed", str(getattr(ctx.exception, "code", "")))

    def test_c2_handoff_error_keeps_three_part_shape(self):
        exc = packaging_handoff.HandoffError("x", 409, "cost_gaps_unresolved")
        self.assertEqual(409, exc.status_code)
        self.assertEqual("cost_gaps_unresolved", exc.code)
        self.assertEqual("x", str(exc))


if __name__ == "__main__":
    unittest.main()
