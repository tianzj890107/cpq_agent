"""红测：角色候选「读不到」的披露，不许在下一层调用点被丢掉。

Spec：`docs/specs/packaging-bom-role-unbound-template-disclosure.md`
血缘：`docs/specs/packaging-silent-degradation-disclosure.md` §2.3–§2.5（本批补的是同一披露的丢失）、
`packaging-part-role-manual-mapping.md` §4.3、`packaging-box-candidate-rank-and-runnability.md`。

现状缺口（读代码 + 离线打桩实测，2026-09-22）：
  · `role_candidates_for()`（`packaging_bom.py:860`）KB 读不到时给
    `part_templates: []` + `templates_unavailable: {code: template_lookup_failed, ...}`；
  · `_load_role_scope()`（`:1100`）只取 `part_templates`，**标记被丢掉**
    （实测 `scope.get("templates_unavailable")` → `None`），并且自己又写了一处
    `except Exception: templates = []`；
  · 于是 `GET …/packaging-bom` 的未映射清单只给"空候选"，与"这个盒型确实没有候选角色"
    同形；同一时刻角色映射面板却会说"模板暂时读不到" —— 两个面板说法不一致。

纪律：
  · 只跑离线单测：全部打桩（假 da_repo / 假 meta 文档），不连 PG、不连 34、不发 HTTP、
    不写业务数据；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_bom as bom  # noqa: E402

PID = "pkgbom00001"
REQ_NO = "REQ-BOM-0001"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

LOOKUP_FAILED = {"code": "template_lookup_failed", "reason": "RuntimeError",
                 "message": "部件模板暂时读不到（知识库读失败：RuntimeError），请稍后重试；"
                            "这不代表该盒型没有部件模板"}

UNBOUND_ROW = {"item_key": "box_part_1", "item_name": "面纸", "bom_category": "box_part",
               "part_code": "DWG-P01", "part_role": "unbound"}


def scope_with(return_value=None, error=None):
    """打桩 `role_candidates_for()` 后调 `_load_role_scope()`（`unavailable={}` 跳过 meta 文档）。"""
    kwargs = {"side_effect": error} if error is not None else {"return_value": return_value}
    with mock.patch.object(bom, "role_candidates_for", **kwargs):
        return bom._load_role_scope(PID, REQ_NO, [dict(UNBOUND_ROW)], "YT-DWG-WINE",
                                    unavailable={})


# --------------------------------------------------------------------------- #
# A. _load_role_scope 必须原样透出披露
# --------------------------------------------------------------------------- #
class ARoleScopeDisclosure(unittest.TestCase):
    def test_a1_flag_is_passed_through_verbatim(self):
        scope = scope_with(return_value={"box_type_code": "YT-DWG-WINE", "requirement_no": REQ_NO,
                                        "part_templates": [], "templates_unavailable": LOOKUP_FAILED})
        self.assertIn("templates_unavailable", scope,
                      "role_candidates_for() 已经算好的披露必须原样透出（Spec §2.1）："
                      "今天只取 part_templates，标记被丢掉了")
        self.assertEqual(LOOKUP_FAILED, scope.get("templates_unavailable"),
                         "必须逐字带出（不许只抄 code、不许重算一份）")

    def test_a2_lookup_raise_is_disclosed_and_rows_stay(self):
        scope = scope_with(error=RuntimeError("kb down"))
        flag = scope.get("templates_unavailable") or {}
        self.assertEqual("template_lookup_failed", flag.get("code"),
                         "它抛异常时也必须留痕（Spec §2.1）")
        self.assertIn("RuntimeError", str(flag.get("reason")),
                      "reason 必须是异常类名（Spec §2.1）")
        self.assertTrue(str(flag.get("message") or "").strip(), "必须有人话（Spec §2.1）")
        self.assertEqual(1, scope.get("unbound_total"),
                         "候选读不到**不许**让未映射行消失（Spec §4）")
        self.assertEqual([], (scope.get("items") or [{}])[0].get("role_candidates"),
                         "候选清单照旧给空（结论不变），但必须有上面的留痕（Spec §4）")

    def test_a3_success_keeps_the_flag_empty(self):
        scope = scope_with(return_value={"box_type_code": "YT-DWG-WINE", "requirement_no": REQ_NO,
                                        "part_templates": [{"part_code": "WINE-P01",
                                                            "component": "面纸"}],
                                        "templates_unavailable": {}})
        self.assertIn("templates_unavailable", scope, "键必须存在（Spec §2.1）")
        self.assertEqual({}, scope.get("templates_unavailable") or {},
                         "读得到时必须是 {}（不许常驻非空，否则等于没披露）")
        self.assertTrue((scope.get("items") or [{}])[0].get("role_candidates"),
                        "读得到时候选必须照旧填进清单行（Spec §4）")


# --------------------------------------------------------------------------- #
# B. load_bom 必须挂到未映射清单旁边
# --------------------------------------------------------------------------- #
def load_bom_patched(case, scope):
    """打桩 `load_bom()` 的七处外部依赖（假 da_repo / 假 meta 文档），离线读一遍。"""
    stubs = [
        mock.patch.object(bom.da_repo, "load_packaging_bom", return_value=[]),
        mock.patch.object(bom.da_repo, "load_box_match", return_value={}),
        mock.patch.object(bom, "_resolve_requirement_no", return_value=REQ_NO),
        mock.patch.object(bom, "_pairing_scope", return_value={"review": [], "unavailable": {}}),
        mock.patch.object(bom, "_parts_binding_scope",
                          return_value={"stale": [], "unavailable": {}, "parts_id": "",
                                        "parts_hash": ""}),
        mock.patch.object(bom, "_bind_error_scope", return_value={}),
        mock.patch.object(bom, "_role_scope", return_value=scope),
    ]
    for stub in stubs:
        stub.start()
        case.addCleanup(stub.stop)
    return bom.load_bom(PID, REQ_NO)


class BLoadBomDisclosure(unittest.TestCase):
    def test_b1_bom_carries_the_flag(self):
        scope = {"engine_version": "x", "candidates_source": "confirmed_box_type",
                 "role_candidates": [], "items": [dict(UNBOUND_ROW)], "mapped_total": 0,
                 "unbound_total": 1, "unavailable": {}, "templates_unavailable": LOOKUP_FAILED}
        body = load_bom_patched(self, scope)
        self.assertIn("role_unbound_templates_unavailable", body,
                      "load_bom() 必须把它挂到未映射清单旁边（Spec §2.2）")
        self.assertEqual(LOOKUP_FAILED, body.get("role_unbound_templates_unavailable"),
                         "必须逐字等于 _role_scope() 那一份（Spec §2.2）")


# --------------------------------------------------------------------------- #
# D. 前端必须解释空下拉框
# --------------------------------------------------------------------------- #
class DFrontend(unittest.TestCase):
    def test_d1_unbound_list_explains_empty_candidates(self):
        text = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("data-role-unbound-templates-unavailable" in text,
                        "未映射清单必须有稳定的披露钩子（Spec §2.3）")
        self.assertTrue("role_unbound_templates_unavailable" in text,
                        "前端必须读这个键才能说同一句话（Spec §2.3）")


# --------------------------------------------------------------------------- #
# E. 护栏（今天必须绿）
# --------------------------------------------------------------------------- #
class EGuards(unittest.TestCase):
    def test_e1_scope_existing_keys_unchanged(self):
        scope = scope_with(return_value={"box_type_code": "YT-DWG-WINE", "requirement_no": REQ_NO,
                                        "part_templates": [], "templates_unavailable": {}})
        for key in ("engine_version", "candidates_source", "role_candidates", "items",
                    "mapped_total", "unbound_total", "unavailable"):
            self.assertIn(key, scope, "既有键 %s 不许消失（Spec §4）" % key)

    def test_e2_role_map_status_shape_unchanged(self):
        status = bom.role_map_status([dict(UNBOUND_ROW)], box_type_code="YT-DWG-WINE",
                                     part_templates=[], role_map={})
        for key in ("unbound_total", "mapped_total", "items", "role_candidates",
                    "engine_version", "candidates_source"):
            self.assertIn(key, status, "role_map_status() 的既有形状不许变（Spec §4）")
        self.assertEqual(1, status["unbound_total"], "未映射行照旧进清单（Spec §4）")

    def test_e3_load_bom_existing_role_keys(self):
        scope = {"engine_version": "x", "candidates_source": "confirmed_box_type",
                 "role_candidates": [], "items": [], "mapped_total": 0,
                 "unbound_total": 0, "unavailable": {}}
        body = load_bom_patched(self, scope)
        for key in ("role_unbound", "role_unbound_total", "role_unbound_unavailable"):
            self.assertIn(key, body, "既有键 %s 不许消失（Spec §4）" % key)


if __name__ == "__main__":
    unittest.main()
