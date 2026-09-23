"""红测：图层名里的 `_U+XXXX` / `\\U+XXXX` 转义必须还原后再匹配角色规则。

Spec：`docs/specs/packaging-layer-name-unicode-escape.md`

现状缺口（真实跑出来的，不是推断）：
  · LibreDWG（本机唯一可用转换器）把中文图层名写成 `_U+56FE_U+5C42 1`（= `图层 1`），
    而 `cad_ir` 的 `normalize_text()` 只服务**文字实体**（且只认 `\\U+`），图层名从不经过它 ——
    `roles.resolve_layer()` 拿到的 `name` 就是转义串，规则表里写的是 `全穿刀` / `压线 Crease`，
    于是**被转义的规则层会判成 `unknown`**：刀线与压痕线分不出，盒型候选/成品轮廓跟着退化；
  · 本机实测：`酒盒.dwg` 8 层里同时有 `图层 2`（明文）与 `_U+56FE_U+5C42 1`（转义）；
    `圆盘盒.dwg` 32 层里也有 `_U+56FE_U+5C42 1`；
  · 同一条真实跑还暴露判据问题（Spec §2.4）：`酒盒.dwg` 根本没有 `Make2D$可见线$普通线`
    这一层（那是圆盘盒的层），既有 D 组对酒盒断言它会 `self.fail()` → 真样本 D 组恒红。

纪律：纯函数 + 真实样本（`CPQ_DWG_REAL_SAMPLES=1` 才跑真实转换，样本只读、产物只写临时目录）；
不连 PG / 34、不起服务、不发 HTTP、不写业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_semantics"
ROLES_MOD = PKG + ".roles"
RULES_MOD = PKG + ".rules"
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"

ESCAPED_TU1 = "_U+56FE_U+5C42 1"          # 图层 1
ESCAPED_CUT = "_U+5168_U+7A7F_U+5200"     # 全穿刀
ESCAPED_CREASE = "_U+538B_U+7EBF Crease"  # 压线 Crease
ESCAPED_FRAME = "_U+56FE_U+6846_U+5C42"   # 图框层
RULE_LAYERS = {"全穿刀": "cut", "压线 Crease": "crease", "图框层": "frame", "排图层": "frame"}


def roles_module():
    return importlib.import_module(ROLES_MOD)


def rules_module():
    return importlib.import_module(RULES_MOD)


def normalize(text):
    module = roles_module()
    fn = getattr(module, "normalize_layer_name", None)
    if not callable(fn):
        raise AssertionError("缺少纯函数 %s.normalize_layer_name()（Spec §2.1）" % ROLES_MOD)
    return fn(text)


def template():
    rules = rules_module()
    resolved = rules.resolve_rule_set()
    _, conf = rules.resolve_template(resolved["rule_set"], None)
    return conf


def role_of(layer_name, known=None):
    module = roles_module()
    row = module.resolve_layer({"name": layer_name, "color": 7, "line_type": "CONTINUOUS",
                                "entity_count": 3},
                               template(), known if known is not None else set())
    return row


# --------------------------------------------------------------------------- #
# W1–W3：纯函数
# --------------------------------------------------------------------------- #
class WNormalizeLayerName(unittest.TestCase):
    def test_w1_libredwg_underscore_escape_is_decoded(self):
        self.assertEqual(normalize(ESCAPED_TU1), "图层 1",
                         "LibreDWG 的 `_U+XXXX` 转义必须还原成 `图层 1`（Spec §2.1）")

    def test_w2_backslash_form_and_case_insensitive(self):
        self.assertEqual(normalize("_u+56fe_u+5c42 1"), "图层 1",
                         "转义标记大小写不敏感")
        self.assertEqual(normalize("\\U+56FE\\U+5C42"), "图层",
                         "AutoCAD 的 `\\U+XXXX` 写法同样要还原")
        self.assertEqual(normalize("Make2D$_U+53EF_U+89C1_U+7EBF$普通线"),
                         "Make2D$可见线$普通线", "只还原转义段，其余字符原样")

    def test_w3_broken_or_plain_names_are_safe(self):
        for value, expected in ((None, ""), ("", ""), ("_U+ZZZZ", "_U+ZZZZ"),
                                ("_U+56F", "_U+56F"), ("全穿刀", "全穿刀"),
                                ("CUTTER", "CUTTER"), ("图层 2", "图层 2")):
            self.assertEqual(normalize(value), expected,
                             "残缺转义不许抛异常、普通名必须逐字不变：%r" % (value,))


# --------------------------------------------------------------------------- #
# W4–W5：匹配与披露
# --------------------------------------------------------------------------- #
class WRoleMatchingAndDisclosure(unittest.TestCase):
    def test_w4_escaped_rule_layers_still_get_their_role(self):
        for layer, expected in ((ESCAPED_CUT, "cut"), (ESCAPED_CREASE, "crease"),
                                (ESCAPED_FRAME, "frame")):
            self.assertEqual(role_of(layer)["role"], expected,
                             "被转义的规则层必须仍判 %s（Spec §2.2）：%s" % (expected, layer))

    def test_w5_name_is_kept_and_normalized_name_is_disclosed(self):
        known = {"ev:L:%s" % ESCAPED_TU1}
        row = role_of(ESCAPED_TU1, known=known)
        self.assertEqual(row.get("name"), ESCAPED_TU1,
                         "`name` 必须仍是原名逐字（界面/证据要对回图上的层名）")
        self.assertIn("name_normalized", row, "每行必须有 name_normalized 键（Spec §2.2）")
        self.assertEqual(row.get("name_normalized"), "图层 1")
        self.assertIn("ev:L:%s" % ESCAPED_TU1, row.get("evidence_refs") or [],
                      "证据引用仍按原名 `ev:L:<原名>`（不许换成还原名）")


# --------------------------------------------------------------------------- #
# W6–W7：真实样本（默认不跑；缺转换器/样本时按缺失原因 skip）
# --------------------------------------------------------------------------- #
class WRealSamples(unittest.TestCase):
    workspace = None
    layers = {}
    semantics = {}

    @classmethod
    def setUpClass(cls):
        if os.environ.get("CPQ_DWG_REAL_SAMPLES") != "1":
            raise unittest.SkipTest("未设置 CPQ_DWG_REAL_SAMPLES=1：真实样本组默认不跑（Spec §5）")
        try:
            converter = importlib.import_module("tech_app.backend.services.cad_converter")
            capability = converter.capability()
        except Exception as exc:                                     # noqa: BLE001
            raise unittest.SkipTest("转换器适配层不可用：%s: %s" % (type(exc).__name__, exc))
        if not capability.get("available") or capability.get("simulated"):
            raise unittest.SkipTest("没有可用的真实转换器：%r" % capability.get("message"))
        missing = [name for name in (WINE_BOX, ROUND_BOX) if not (SAMPLES_DIR / name).is_file()]
        if missing:
            raise unittest.SkipTest("样本不在本机：缺少 %s" % "、".join(missing))
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-layer-escape-"))
        try:
            cad_ir = importlib.import_module("tech_app.backend.services.cad_ir")
            semantics_mod = importlib.import_module(PKG)
            for name in (WINE_BOX, ROUND_BOX):
                out = cls.workspace / name.replace(".dwg", "")
                completed = subprocess.run(
                    [sys.executable, str(SAMPLE_TOOL), "--sample", str(SAMPLES_DIR / name),
                     "--out", str(out), "--json"],
                    capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
                text = (completed.stdout or "").strip()
                payload = json.loads(text[text.find("{"):]) if text else {}
                dxf = pathlib.Path(str(payload.get("dxf_path") or ""))
                if not dxf.is_file():
                    raise unittest.SkipTest("%s 转换未产出 DXF（rc=%s）"
                                            % (name, completed.returncode))
                ir = cad_ir.parse_dxf(dxf.read_bytes(), filename=dxf.name,
                                      source={"kind": "dwg_2d", "attachment_name": name})
                cls.layers[name] = [str(row.get("name") or "") for row in (ir.get("layers") or [])]
                cls.semantics[name] = semantics_mod.analyze(ir)
        except unittest.SkipTest:
            raise
        except Exception as exc:                                     # noqa: BLE001
            raise unittest.SkipTest("真实样本转换/解析失败：%s: %s" % (type(exc).__name__, exc))

    @classmethod
    def tearDownClass(cls):
        if cls.workspace is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def rows(self, name):
        return [row for row in ((self.semantics.get(name) or {}).get("layers") or [])
                if isinstance(row, dict)]

    def test_w6_escaped_layers_are_disclosed_normalized(self):
        for name in (WINE_BOX, ROUND_BOX):
            raw = self.layers.get(name) or []
            self.assertIn(ESCAPED_TU1, raw,
                          "%s 的真实产物里应当有 `%s`（Spec §1 实测）" % (name, ESCAPED_TU1))
            for row in self.rows(name):
                self.assertIn("name_normalized", row,
                              "%s 的图层行必须有 name_normalized 键" % name)
                self.assertNotIn("_U+", str(row.get("name_normalized") or ""),
                                 "%s 的 %r 还原后不该还剩转义" % (name, row.get("name")))
                self.assertNotIn("\\U+", str(row.get("name_normalized") or ""))
        roles = {row.get("name"): row.get("role") for row in self.rows(ROUND_BOX)}
        for layer, expected in RULE_LAYERS.items():
            self.assertEqual(roles.get(layer), expected,
                             "圆盘盒的 `%s` 角色不许被本批改动影响" % layer)

    def test_w7_wine_box_has_no_make2d_layer(self):
        wine = " ".join(self.layers.get(WINE_BOX) or [])
        self.assertNotIn("Make2D", wine,
                         "酒盒.dwg 真实产物里没有 Make2D 层（Spec §2.4 的事实钉）")
        round_ = " ".join(self.layers.get(ROUND_BOX) or [])
        self.assertIn("Make2D$可见线$普通线", round_,
                      "`Make2D$可见线$普通线` 是圆盘盒的层，不是酒盒的")


# --------------------------------------------------------------------------- #
# W8：既有判据不许被本批改动（护栏）
# --------------------------------------------------------------------------- #
class WExistingRolesUnchanged(unittest.TestCase):
    def test_w8_plain_layer_roles_stay_the_same(self):
        self.assertEqual(role_of("CUTTER")["role"], "cut", "`CUTTER` 仍靠 name_prefix 命中")
        self.assertEqual(role_of("全穿刀")["role"], "cut")
        self.assertEqual(role_of("压线 Crease")["role"], "crease")
        for layer in ("DESIGN", "Defpoints", "Make2D$可见线$普通线", "轮廓线"):
            self.assertNotIn(role_of(layer)["role"], ("cut", "crease"),
                             "%s 不得被误判成刀线/压线（Spec §1.2）" % layer)


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main(verbosity=2)
