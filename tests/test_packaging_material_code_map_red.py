"""红测：材料原文 → 材料码要有**唯一映射表**与"该补哪一条"的可执行缺口
（Spec `packaging-business-material-code-map.md`）。

现状缺口（代码级，可指到行）：
  · `packaging_bom._resolve_material_code()`（`:377-386`）只有"取第一个空白分词、在
    `kb_material.name` 里唯一包含"这一条规则 —— 客户原文（`350G玖龙粉灰` / `EVA` / `磁铁`）
    大多整串无空白或分词撞多个候选，解析不到就只剩一句 `material_unresolved`；
  · `tech_app/agent_knowledge/rules/packaging_material_code_map.json` **不存在**：
    业务/采购就算想给映射也没有可维护的落点（`packaging_material_map` 模块也不存在）；
  · `gaps.material_unresolved`（`:1330`）只给 `item_key` 清单：不分"没有候选 / 多个候选"、
    没有 `action`；`_business_material_scope()`（`:554-565`）不说"其中几行是映射给的"；
  · 没有一处披露"映射表读不到"（读不到会与"业务还没给映射"长得一模一样）。

纪律：纯函数 + 临时文件（`tempfile`）+ 合成材料清单；不连 PG / 34、不写业务数据、不发 HTTP。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_bom as bom                  # noqa: E402

SHIPPED_MAP = (ROOT / "tech_app" / "agent_knowledge" / "rules"
               / "packaging_material_code_map.json")
RULE_SET = "packaging_material_code_map_v1"
ERROR_CODE = "PACKAGING_MATERIAL_CODE_MAP_INVALID"

#: 合成材料清单（形状逐字同 `kb_repo.list_materials()`）：`350G玖龙粉灰` 有多条同名候选 →
#: 既有分词规则必然给不出唯一解，正好用来验证"映射优先"。
MATERIALS = [
    {"material_code": "MAT-PAPER-350", "name": "350G玖龙粉灰 面纸"},
    {"material_code": "MAT-PAPER-350B", "name": "350G玖龙粉灰 底纸"},
    {"material_code": "MAT-EVA", "name": "EVA 内托"},
]
MATERIAL_CODES = {row["material_code"] for row in MATERIALS}

UNRESOLVED_REASONS = ("map_key_missing", "map_entry_not_applied", "map_unknown")
RESOLVED_REASONS = ("map_hit", "legacy_hit")
REASON_CLOSURE = UNRESOLVED_REASONS + RESOLVED_REASONS


def map_module():
    try:
        from tech_app.backend.services import packaging_material_map as module
    except Exception as exc:                       # noqa: BLE001 — 模块不存在即判失败
        raise AssertionError("缺少 packaging_material_map 模块（Spec §C1）：%s" % exc)
    return module


def write_map(directory, entries, *, rule_set=RULE_SET, raw=None):
    path = pathlib.Path(directory) / "map.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    path.write_text(json.dumps({"rule_set": rule_set, "review_status": "draft",
                               "note": "测试用", "entries": entries}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def entry(text, code, note=""):
    return {"text": text, "material_code": code, "note": note}


def business_doc(texts):
    return {"business_parts": [{"business_part_code": "PART-P%02d" % index,
                                "name": "件%d" % index,
                                "authority": {"material_text": text}}
                               for index, text in enumerate(texts, start=1)]}


class _Patch:
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


# --------------------------------------------------------------------------- #
# A 组：映射表本身（唯一的落点 + 形状 + 空表）
# --------------------------------------------------------------------------- #
class AMapFile(unittest.TestCase):
    def test_a1_shipped_map_exists_with_the_agreed_shape(self):
        self.assertTrue(SHIPPED_MAP.exists(),
                        "缺少唯一映射表 %s（Spec §C1）" % SHIPPED_MAP.name)
        data = json.loads(SHIPPED_MAP.read_text(encoding="utf-8"))
        self.assertEqual(RULE_SET, data.get("rule_set"), "rule_set 逐字固定（Spec §C1）")
        self.assertIsInstance(data.get("entries"), list, "entries 必须是数组（Spec §C1）")
        self.assertEqual([], data.get("entries"),
                         "本批不许替业务填映射：entries 保持空表（Spec §C1/§4）")

    def test_a2_module_constants_are_named(self):
        module = map_module()
        self.assertEqual("packaging-material-map/1", module.ENGINE_VERSION)
        self.assertEqual("packaging_material_code_map.json", module.MATERIAL_MAP_FILENAME)
        self.assertEqual(RULE_SET, module.RULE_SET)
        self.assertEqual(ERROR_CODE, module.MATERIAL_MAP_ERROR_CODE)
        self.assertEqual(("hit", "missing", "ambiguous"), tuple(module.LOOKUP_STATUSES))

    def test_a3_default_path_points_at_the_shipped_file(self):
        module = map_module()
        path, source = module.map_path()
        self.assertEqual("default", source, "没有 env 覆盖时来源是 default（Spec §C1）")
        self.assertEqual(SHIPPED_MAP.resolve(), pathlib.Path(path).resolve(),
                         "默认路径必须指向仓库内置的那一份（Spec §C1）")

    def test_a4_env_override_is_honoured(self):
        module = map_module()
        directory = tempfile.mkdtemp(prefix="cpq-material-map-")
        path = write_map(directory, [])
        with _Patch((module.os.environ, "get", None)) if False else _Patch(
                (os, "environ", dict(os.environ, **{module.ENV_MATERIAL_MAP_PATH: str(path)}))):
            resolved, source = module.map_path()
        self.assertEqual("override", source, "env 覆盖时来源是 override（Spec §C1）")
        self.assertEqual(path.resolve(), pathlib.Path(resolved).resolve())


# --------------------------------------------------------------------------- #
# B 组：读取（缺失 / 非法 / 结构不对一律抛，不许静默回落）
# --------------------------------------------------------------------------- #
class BReadMap(unittest.TestCase):
    def test_b1_valid_file_gives_entries_source_and_fingerprint(self):
        module = map_module()
        directory = tempfile.mkdtemp(prefix="cpq-material-map-")
        path = write_map(directory, [entry("350G玖龙粉灰", "MAT-PAPER-350")])
        entries, source, fingerprint = module.read_material_map(path)
        self.assertEqual(1, len(entries), "合法映射要读得出来（Spec §C1）")
        self.assertEqual("MAT-PAPER-350", entries[0].get("material_code"))
        self.assertEqual("override", source, "显式给路径时来源是 override（Spec §C1）")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest()[:12], fingerprint,
                         "fingerprint = 文件字节 sha256 前 12 位（Spec §C1）")

    def test_b2_missing_file_raises_with_a_stable_code(self):
        module = map_module()
        missing = pathlib.Path(tempfile.mkdtemp(prefix="cpq-material-map-")) / "nope.json"
        with self.assertRaises(module.MaterialMapError) as ctx:
            module.read_material_map(missing)
        self.assertEqual(ERROR_CODE, getattr(ctx.exception, "code", ""),
                         "读不到要带稳定码（Spec §C1）")
        self.assertEqual(ERROR_CODE, getattr(ctx.exception, "stable_error_code", ""))

    def test_b3_bad_shapes_raise(self):
        module = map_module()
        directory = tempfile.mkdtemp(prefix="cpq-material-map-")
        cases = [
            ("不是 JSON", "{not json"),
            ("不是对象", "[1, 2]"),
            ("rule_set 不对", json.dumps({"rule_set": "other", "entries": []})),
            ("entries 不是数组", json.dumps({"rule_set": RULE_SET, "entries": {"a": 1}})),
            ("条目缺 text", json.dumps({"rule_set": RULE_SET,
                                        "entries": [{"material_code": "MAT-1"}]})),
            ("条目缺码", json.dumps({"rule_set": RULE_SET, "entries": [{"text": "x"}]})),
            ("条目不是对象", json.dumps({"rule_set": RULE_SET, "entries": ["x"]})),
        ]
        for label, raw in cases:
            path = write_map(directory, [], raw=raw)
            with self.assertRaises(module.MaterialMapError, msg="必须拒绝：%s（Spec §C1）" % label):
                module.read_material_map(path)

    def test_b4_normalize_ignores_whitespace_and_ascii_case(self):
        module = map_module()
        self.assertEqual(module.normalize_material_text("350G 玖龙粉灰"),
                         module.normalize_material_text("350g玖龙粉灰"),
                         "空白与 ASCII 大小写都不算区别（Spec §C1）")
        self.assertEqual(module.normalize_material_text("EVA\u3000内托"),
                         module.normalize_material_text("eva内托"),
                         "全角空格也是空白（Spec §C1）")
        self.assertEqual("", module.normalize_material_text(None))

    def test_b5_lookup_three_states(self):
        module = map_module()
        entries = [entry("350G玖龙粉灰", "MAT-PAPER-350"),
                   entry("EVA", "MAT-EVA"), entry("EVA", "MAT-EVA-B")]
        self.assertEqual(("MAT-PAPER-350", "hit"),
                         module.lookup_material_code("350g 玖龙粉灰", entries))
        self.assertEqual(("", "missing"), module.lookup_material_code("磁铁", entries))
        code, status = module.lookup_material_code("EVA", entries)
        self.assertEqual("ambiguous", status, "同键两码必须 ambiguous，不许猜（Spec §C1）")
        self.assertEqual("", code)


# --------------------------------------------------------------------------- #
# C 组：解析优先级（映射优先、既有规则兜底、码要真在清单里）
# --------------------------------------------------------------------------- #
class CResolvePriority(unittest.TestCase):
    def test_c1_map_hit_wins_over_the_legacy_rule(self):
        entries = [entry("350G玖龙粉灰", "MAT-PAPER-350")]
        code = bom.resolve_material_code("350G 玖龙粉灰", MATERIALS, map_entries=entries)
        self.assertEqual("MAT-PAPER-350", code, "映射命中优先于既有分词规则（Spec §C2）")

    def test_c2_map_code_must_exist_in_the_material_index(self):
        entries = [entry("350G玖龙粉灰", "MAT-NOT-IN-KB")]
        code = bom.resolve_material_code("350G玖龙粉灰", MATERIALS, map_entries=entries)
        self.assertIsNone(code, "映射给了清单里没有的码 → 不算解析出来（Spec §C2/§4）")

    def test_c3_ambiguous_map_entry_gives_no_code(self):
        entries = [entry("350G玖龙粉灰", "MAT-PAPER-350"),
                   entry("350g玖龙粉灰", "MAT-PAPER-350B")]
        self.assertIsNone(bom.resolve_material_code("350G玖龙粉灰", MATERIALS,
                                                    map_entries=entries),
                          "同键两码不许猜（Spec §C2/§4）")

    def test_c4_legacy_rule_still_works_when_the_map_misses(self):
        code = bom.resolve_material_code("EVA 内托", MATERIALS, map_entries=[])
        self.assertEqual("MAT-EVA", code, "映射没给时必须逐字回到既有分词规则（Spec §C2）")
        self.assertEqual("MAT-EVA", bom.resolve_material_code("EVA 内托", MATERIALS),
                         "不传映射表时行为与今天逐字相同（Spec §C4）")

    def test_c5_no_candidates_still_gives_none(self):
        self.assertIsNone(bom.resolve_material_code("磁铁", MATERIALS, map_entries=[]),
                          "既有规则解不出就还是 None（Spec §C4）")

    def test_c6_empty_map_matches_today_row_by_row(self):
        texts = ["350G玖龙粉灰", "EVA 内托", "磁铁", "", "  "]
        for text in texts:
            with_map = bom.resolve_material_code(text, MATERIALS, map_entries=[])
            without = bom.resolve_material_code(text, MATERIALS)
            self.assertEqual(without, with_map,
                             "空映射表 = 今天的行为（Spec §C4）：%r" % text)


# --------------------------------------------------------------------------- #
# D 组：账与缺口（哪几行是映射给的 + 每行怎么补）
# --------------------------------------------------------------------------- #
class DScopeAndGaps(unittest.TestCase):
    def _rows(self):
        return bom.business_material_rows(
            business_doc(["350G玖龙粉灰", "EVA 内托", "磁铁", "未收录原文"]),
            materials=MATERIALS,
            map_entries=[entry("350G玖龙粉灰", "MAT-PAPER-350")])

    def test_d1_map_row_is_resolved_and_traceable(self):
        rows = self._rows()
        by_key = {row["item_key"]: row for row in rows}
        self.assertEqual("MAT-PAPER-350", by_key["350G玖龙粉灰"]["material_code"],
                         "映射命中要落成材料码（Spec §C2）")
        self.assertEqual("MAT-EVA", by_key["EVA 内托"]["material_code"],
                         "既有规则兜底不变（Spec §C2）")
        # 既有口径就是"解不出来给 None（材料清单为空时给空串）"，两种都算留空。
        self.assertFalse(by_key["未收录原文"]["material_code"],
                         "解不出来仍然留空、不许编（Spec §C2/§4）")

    def test_d2_scope_counts_reasons(self):
        scope = bom._business_material_scope(self._rows(),
                                            map_entries=[entry("350G玖龙粉灰", "MAT-PAPER-350")])
        for key in ("row_total", "resolved_total", "unresolved_total", "keys",
                    "reason_counts", "map_hit_total", "map_unavailable"):
            self.assertIn(key, scope, "账上必须有这个键（Spec §C3）：%s" % key)
        self.assertEqual(4, scope["row_total"])
        self.assertEqual(2, scope["resolved_total"])
        self.assertEqual(2, scope["unresolved_total"])
        self.assertEqual(1, scope["map_hit_total"], "映射给了几条要单独说（Spec §C3）")
        self.assertEqual(1, scope["reason_counts"].get("legacy_hit", 0),
                         "既有规则解的那条要分家（Spec §C3）")
        self.assertEqual(2, scope["reason_counts"].get("map_key_missing", 0),
                         "映射里没有的原文算一档（Spec §C3：磁铁 + 未收录原文）")
        self.assertNotIn("map_entry_not_applied", scope["reason_counts"],
                         "这一版的映射都生效了，就不许报「写了没生效」（Spec §C3）")
        self.assertEqual({}, scope["map_unavailable"], "读得到映射表时这里是空对象（Spec §C3）")

    def test_d2b_map_entry_with_an_unknown_code_lands_on_not_applied(self):
        rows = bom.business_material_rows(business_doc(["350G玖龙粉灰"]), materials=MATERIALS,
                                          map_entries=[entry("350G玖龙粉灰", "MAT-NOT-IN-KB")])
        self.assertFalse(rows[0]["material_code"], "清单里没有的码不许落到行上（Spec §C2）")
        scope = bom._business_material_scope(
            rows, map_entries=[entry("350G玖龙粉灰", "MAT-NOT-IN-KB")])
        self.assertEqual(1, scope["reason_counts"].get("map_entry_not_applied", 0),
                         "映射里写了却没生效要单独算一档（Spec §C3）")
        self.assertEqual(0, scope["map_hit_total"])

    def test_d3_classify_reason_closure(self):
        with_map = [entry("350G玖龙粉灰", "MAT-PAPER-350")]
        cases = [
            ({"material": "350G 玖龙粉灰", "material_code": "MAT-PAPER-350"}, "map_hit"),
            ({"material": "EVA 内托", "material_code": "MAT-EVA"}, "legacy_hit"),
            ({"material": "磁铁", "material_code": ""}, "map_key_missing"),
            ({"material": "350G玖龙粉灰", "material_code": ""}, "map_entry_not_applied"),
        ]
        for row, expected in cases:
            got = bom.classify_material_resolution(row, map_entries=with_map)
            self.assertEqual(expected, got, "读路径四档（Spec §C3）：%r" % (row,))
            self.assertIn(got, REASON_CLOSURE)
        self.assertEqual("map_unknown",
                         bom.classify_material_resolution(
                             {"material": "磁铁", "material_code": ""},
                             map_entries=[], map_available=False),
                         "映射表读不到时不许猜档（Spec §C3）")

    def test_d4_gaps_detail_gives_an_action_per_row(self):
        items = self._rows()
        detail = bom.material_unresolved_detail(
            items, map_entries=[entry("350G玖龙粉灰", "MAT-PAPER-350")])
        keys = [row["item_key"] for row in detail]
        self.assertEqual(sorted(row["item_key"] for row in items
                                if not row["material_code"]), sorted(keys),
                         "逐条缺口与 material_unresolved 同一批行（Spec §C3）")
        for row in detail:
            self.assertIn(row["reason"], UNRESOLVED_REASONS)
            self.assertTrue(str(row.get("action") or "").strip(),
                            "每条缺口都要给可执行动作（Spec §C3）")
            self.assertIn("packaging_material_code_map.json", row["action"],
                          "动作要点名改哪个文件（Spec §C3）")

    def test_d5_map_unavailable_is_disclosed(self):
        scope = bom._business_material_scope(self._rows(), map_entries=[],
                                            map_available=False)
        self.assertTrue(scope["map_unavailable"], "读不到映射表必须如实披露（Spec §C3）")
        self.assertEqual(ERROR_CODE, scope["map_unavailable"].get("code"))
        self.assertTrue(str(scope["map_unavailable"].get("message") or "").strip())
        self.assertEqual(0, scope["map_hit_total"])
        self.assertEqual(scope["row_total"],
                         scope["reason_counts"].get("map_unknown", 0),
                         "读不到时每一行都只能是 map_unknown（Spec §C3）")

    def test_d6_read_map_helper_never_touches_the_network_or_db(self):
        source = (ROOT / "tech_app" / "backend" / "services"
                  / "packaging_material_map.py").read_text(encoding="utf-8")
        for banned in ("import requests", "import httpx", "da_repo", "from ..storage",
                       "urllib.request", "import socket"):
            self.assertNotIn(banned, source, "映射模块不许联网 / 连库（Spec §4）：%s" % banned)


# --------------------------------------------------------------------------- #
# E 组：护栏（现状即绿）—— 既有规则与既有账一个字不动
# --------------------------------------------------------------------------- #
class EGuardrails(unittest.TestCase):
    def test_e1_legacy_resolver_source_is_unchanged(self):
        import inspect
        source = inspect.getsource(bom._resolve_material_code)
        self.assertIn("text.split()[0]", source,
                      "既有分词规则仍是原样那一句（Spec §C4）")
        self.assertNotIn("map_entries", source, "既有规则里不许塞映射逻辑（Spec §C4）")

    def test_e2_scope_keys_are_additive(self):
        scope = bom._business_material_scope([])
        self.assertEqual({"row_total": 0, "resolved_total": 0, "unresolved_total": 0, "keys": []},
                         {key: scope[key] for key in
                          ("row_total", "resolved_total", "unresolved_total", "keys")},
                         "既有四键的形状不许变（Spec §C4）")

    def test_e3_no_hardcoded_material_codes_in_the_resolver(self):
        import inspect
        source = inspect.getsource(bom.resolve_material_code)
        self.assertNotIn("MAT-", source, "映射不许硬编码在代码里（Spec §4）")

    def test_e4_shipped_map_has_no_business_entries_yet(self):
        data = json.loads(SHIPPED_MAP.read_text(encoding="utf-8"))
        self.assertEqual([], data["entries"],
                         "本批不替业务填映射（Spec §1/§4）")
        self.assertEqual("draft", data.get("review_status"),
                         "映射还没签字，仍是 draft（Spec §C1）")


if __name__ == "__main__":
    unittest.main()
