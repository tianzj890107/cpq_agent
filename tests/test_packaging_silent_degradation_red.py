"""红测：包装链路上的"静默降级"必须留痕（零件回填 / 人工角色映射 / 角色候选 / 配对复核）。

Spec：`docs/specs/packaging-silent-degradation-disclosure.md`

现状缺口（代码级，五处形状完全一样 —— **失败与"空"在返回体上长得一模一样**）：
  · `packaging_bom.py:1039 _bind_parts()`：`except Exception: return items, [], []`，
    与"这个项目根本没有零件文档"逐字相同 → 零件在、BOM 没尺寸、材料费 0，没有任何地方说得出
    "回填这一步挂了"；
  · `packaging_bom.py:756 _role_doc()` 读失败 `return {"by_requirement": {}}`，
    而 `apply_saved_role_map()` 是 `build_bom()` 每次都调的 → 文档通道抖一下，
    "重算不许把人工映射算没了"（Spec §2.8）就在最需要它的那一刻失效；
    `:832 save_role_mapping()` 的写失败 `return` —— 接口回 200，留痕没落盘；
  · `packaging_bom.py:836 role_candidates_for()` 的 KB 读失败给 `templates = []`，
    与"这个盒型确实没有部件模板"同一个值 → 空下拉让人去改盒型；
  · `packaging_match.py:419 _part_template_count()` 读失败折成 `0`，于是候选被标
    `part_template_available=False`（"选了它走不下去"），`_template_warnings()` 还会给出
    "该盒型在部件模板表里没有模板"这句**断言的错话** —— 用户据此主动避开一个可用的盒型。

纪律：只读源码 + 假后端 / 假零件模块；不连 PG、不发 HTTP、不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_bom as bom            # noqa: E402
from tech_app.backend.services import packaging_match                 # noqa: E402
from tech_app.backend.services import packaging_parts as parts        # noqa: E402
from tech_app.backend.storage import meta_backend                     # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-SILENT-001"

BINDABLE_ROW = {
    "item_key": "WINE-P01", "bom_category": "box_part", "item_name": "左盖面纸",
    "status": "needs_input", "locked": 0, "length_mm": None, "width_mm": None,
    "missing_variables": ["inner_length"],
}
PARTS_DOC = {"parts": [{"part_code": "DWG-P01", "component_id": "cmp:50",
                        "unfolded_length_mm": 440.123, "unfolded_width_mm": 482.92}],
             "stats": {"part_total": 1}}
MAPPED_ROW = {
    "item_key": "WINE-P01", "bom_category": "box_part", "part_code": "WINE-P01",
    "size_source_json": {"dwg_binding": {"part_code": "DWG-P01", "role_value": "面纸",
                                         "binding_method": "manual_mapping", "bound_by": "PE1"}},
}


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


class _FakeBackend:
    def __init__(self, doc=None, get_error=None, put_error=None):
        self.doc = doc or {}
        self.get_error = get_error
        self.put_error = put_error

    def get_doc(self, project_id, key):
        if self.get_error:
            raise self.get_error
        return self.doc

    def put_doc(self, project_id, key, doc):
        if self.put_error:
            raise self.put_error
        self.doc = doc
        return {"ok": True}


# --------------------------------------------------------------------------- #
# A 组：零件文档在、回填挂了 —— 必须留痕，不许静默退回"没有零件"
# --------------------------------------------------------------------------- #
class APartBindingFailure(unittest.TestCase):
    def _bind(self, load_parts=None, bind_rows=None):
        load = load_parts or (lambda project_id: PARTS_DOC)
        bind = bind_rows or (lambda items, doc, **kw: {"items": items})
        with _Patch((parts, "load_parts", load), (parts, "bind_rows", bind)):
            return bom._bind_parts(PID, [dict(BINDABLE_ROW)], REQ_NO)

    def test_a1_binding_crash_leaves_a_stable_code(self):
        def boom(items, doc, **kwargs):
            raise RuntimeError("bind exploded")

        result = self._bind(bind_rows=boom)
        self.assertGreaterEqual(
            len(result), 4,
            "`_bind_parts()` 必须把回填失败**报出来**（Spec §2.1）："
            "现在返回 3 元组，与『根本没有零件文档』逐字相同，现场查不出真因")
        error = result[3] or {}
        self.assertEqual("part_binding_failed", error.get("code"),
                         "失败留痕要带稳定码 part_binding_failed")
        self.assertTrue(str(error.get("reason") or error.get("message") or "").strip(),
                        "留痕里要有人能照着查的原因（异常类名或首行文案）")

    def test_a2_load_bom_exposes_the_binding_error_key(self):
        with _Patch((bom.da_repo, "load_packaging_bom", lambda *a, **k: []),
                    (bom.da_repo, "load_box_match", lambda *a, **k: {}),
                    (bom, "_load_pairing_review", lambda *a, **k: []),
                    (bom, "_role_scope", lambda *a, **k: {"items": [], "unbound_total": 0})):
            doc = bom.load_bom(PID, REQ_NO)
        self.assertIn("binding_error", doc,
                      "读接口必须能读到回填失败（Spec §2.1）：与 pairing_review / role_unbound "
                      "同一口径 —— 键必须存在，没有失败时给 {}")

    def test_a3_no_parts_document_is_not_a_failure(self):
        result = self._bind(load_parts=lambda project_id: {})
        self.assertEqual([dict(BINDABLE_ROW)], [dict(row) for row in result[0]],
                         "没有零件文档时行为逐字不变（不让 BOM 整体失败）")
        error = result[3] if len(result) > 3 else {}
        self.assertEqual({}, error, "『没有零件文档』不是失败，不许给 binding_error 留痕")


# --------------------------------------------------------------------------- #
# B 组：人工角色映射的读/写失败必须显式（不许静默丢映射 / 静默没保存）
# --------------------------------------------------------------------------- #
class BRoleMapFailure(unittest.TestCase):
    def test_b1_read_failure_raises_instead_of_looking_empty(self):
        backend = _FakeBackend(get_error=RuntimeError("doc store down"))
        with _Patch((meta_backend, "get_backend", lambda: backend)):
            with self.assertRaises(bom.BomError) as ctx:
                bom.load_role_map(PID, REQ_NO)
        self.assertEqual("role_map_unavailable", str(getattr(ctx.exception, "code", "")),
                         "读不到映射必须显式报错（Spec §2.2）：静默给 {} 会让下一次重算把 "
                         "人工确认过的角色清掉，而且没有留痕")

    def test_b2_write_failure_raises_instead_of_pretending_success(self):
        backend = _FakeBackend(put_error=RuntimeError("doc store down"))
        with _Patch((meta_backend, "get_backend", lambda: backend),
                    (bom.da_db, "execute", lambda *a, **k: None)):
            with self.assertRaises(bom.BomError) as ctx:
                bom.save_role_mapping(PID, REQ_NO, dict(MAPPED_ROW))
        self.assertEqual("role_map_save_failed", str(getattr(ctx.exception, "code", "")),
                         "留痕写失败必须抛给调用方（Spec §2.2）：现在静默 return，接口回 200，"
                         "『我映射了、它没了』无从追起")

    def test_b3_happy_path_is_unchanged(self):
        backend = _FakeBackend()
        with _Patch((meta_backend, "get_backend", lambda: backend),
                    (bom.da_db, "execute", lambda *a, **k: None)):
            bom.save_role_mapping(PID, REQ_NO, dict(MAPPED_ROW))
            saved = bom.load_role_map(PID, REQ_NO)
        self.assertEqual("面纸", (saved.get("WINE-P01") or {}).get("role"),
                         "正常路径必须逐字不变：写得进、读得出")


# --------------------------------------------------------------------------- #
# C 组：角色候选读不到 KB —— 不许显示成"这个盒型没有部件模板"
# --------------------------------------------------------------------------- #
class CRoleCandidatesLookup(unittest.TestCase):
    def _scope(self, templates=None, error=None):
        def loader(box_code):
            if error:
                raise error
            return templates or []

        with _Patch((bom.da_repo, "load_box_match",
                     lambda *a, **k: {"confirmed_box_type": "YT-DWG-WINE-700ML"}),
                    (bom.kb_repo, "packaging_part_templates", loader)):
            return bom.role_candidates_for(PID, REQ_NO)

    def test_c1_lookup_failure_is_disclosed(self):
        scope = self._scope(error=RuntimeError("kb down"))
        flag = scope.get("templates_unavailable") or {}
        self.assertEqual("template_lookup_failed", flag.get("code"),
                         "KB 读不到必须显式披露（Spec §2.3）：现在给 `part_templates: []`，"
                         "与『这个盒型确实没有部件模板』同一个值，用户会去改盒型")

    def test_c2_lookup_success_keeps_the_flag_empty(self):
        scope = self._scope(templates=[{"part_code": "WINE-P01", "component": "面纸"}])
        self.assertEqual({}, scope.get("templates_unavailable") or {},
                         "读成功时标记必须是 {}（不许常驻非空，否则等于没披露）")


# --------------------------------------------------------------------------- #
# C3 / C4：盒型匹配侧的模板可用性 —— 读不到 ≠ 没有
# --------------------------------------------------------------------------- #
class CTemplateAvailability(unittest.TestCase):
    def _with_kb_failure(self, fn):
        def loader(box_type_code):
            raise RuntimeError("kb down")

        with _Patch((packaging_match.kb_repo, "packaging_part_templates", loader)):
            return fn()

    def test_c3_candidate_availability_is_unknown_not_false_when_lookup_fails(self):
        def call():
            return packaging_match._candidate(
                {"box_type_code": "YT-DWG-WINE-700ML"}, {}, [], [])

        cand = self._with_kb_failure(call)
        self.assertIsNone(cand.get("part_template_available"),
                          "读不到模板时 `part_template_available` 必须是 None（未知，Spec §2.4）："
                          "现在是 False，候选被显示成『选了它往下走 BOM 会 409』，"
                          "用户会主动避开一个完全可用的盒型")
        flag = cand.get("part_template_unavailable") or {}
        self.assertEqual("template_lookup_failed", flag.get("code"),
                         "未知状态要带显式原因码 part_template_unavailable")

    def test_c4_no_template_warning_is_not_faked_from_a_failed_lookup(self):
        warns = self._with_kb_failure(
            lambda: packaging_match._template_warnings("YT-DWG-WINE-700ML"))
        codes = [row.get("code") for row in (warns or [])]
        self.assertNotIn("box_type_without_part_template", codes,
                         "读不到知识库时不许给出『该盒型在部件模板表里没有模板』这句断言"
                         "（Spec §2.4）—— 那是把『我查不到』说成了『它没有』")
        self.assertIn("box_type_template_lookup_failed", codes,
                      "读不到要给自己的码，让界面能说『暂时查不到，请稍后重试』")


# --------------------------------------------------------------------------- #
# D 组：配对复核文档读不到 —— 不许静默变成"没有不一致项"
# --------------------------------------------------------------------------- #
class DPairingReviewLookup(unittest.TestCase):
    """`_pairing_doc()`（`packaging_bom.py:1011`）读失败 → `{"by_requirement": {}}`，
    于是 `load_bom()["pairing_review"]` 给 `[]` —— 与"这次配对没有任何不一致项"逐字相同。
    配对复核是 `bind_rows()` 唯一一处把"配对后材料明显不同类"喊出来的地方，读不到按"没有"处理，
    等于让一次可疑配对在报告里凭空消失（Spec §1.5）。"""

    def _load(self, backend):
        with _Patch((meta_backend, "get_backend", lambda: backend),
                    (bom.da_repo, "load_packaging_bom", lambda *a, **k: []),
                    (bom.da_repo, "load_box_match", lambda *a, **k: {}),
                    (bom, "_role_scope", lambda *a, **k: {"items": [], "unbound_total": 0})):
            return bom.load_bom(PID, REQ_NO)

    def test_d1_read_failure_is_disclosed_instead_of_looking_clean(self):
        doc = self._load(_FakeBackend(get_error=RuntimeError("doc store down")))
        flag = doc.get("pairing_review_unavailable") or {}
        self.assertEqual("pairing_review_unavailable", flag.get("code"),
                         "配对复核读不到必须显式披露（Spec §1.5）：现在给 `pairing_review: []`，"
                         "与『这次配对没有不一致项』同一个值，可疑配对被静默当成没事")

    def test_d2_happy_path_keeps_the_flag_empty(self):
        backend = _FakeBackend(
            doc={"by_requirement": {REQ_NO: [{"code": "material_class_mismatch"}]}})
        doc = self._load(backend)
        self.assertEqual([{"code": "material_class_mismatch"}], doc.get("pairing_review"),
                         "读得到时清单逐字带出（既有口径不变）")
        self.assertEqual({}, doc.get("pairing_review_unavailable") or {},
                         "读成功时标记必须是 {}（不许常驻非空，否则等于没披露）")

    def test_d3_conclusions_are_not_changed_by_the_read_failure(self):
        healthy = self._load(_FakeBackend())
        broken = self._load(_FakeBackend(get_error=RuntimeError("doc store down")))
        for key in ("built", "items", "gaps", "stats"):
            self.assertEqual(healthy.get(key), broken.get(key),
                             "本批只要求留痕：`%s` 不许因为读不到披露文档而变化（Spec §3）" % key)


if __name__ == "__main__":
    unittest.main()
