"""红测：`role_known_ratio` 是唯一一个只能靠比值反推的分子 —— 门禁的「角色已知件数」地板会被舍入悄悄放行。

Spec：`docs/specs/packaging-parts-role-known-numerator-must-not-be-reconstructed.md`

现状缺口（本机实测，不是推断）：
  · `summarize()` 早就给出了 `closed_total` / `material_known_total` / `thickness_known_total` /
    `thickness_unknown_total` / `processable_total` 这些**绝对分子**，唯独 `role_known_ratio`
    只有比值、没有分子；
  · 于是门禁 `tech_app/tools/packaging_parts_gate.py::_sample_metrics()` 只能反推：

        summary["role_known_total"] = int(round(float(summary.get("role_known_ratio") or 0.0) * part_total))

    再由 `sample_verdict()` 拿它比 `THRESHOLDS["圆盘盒.dwg"]["role_known_total"] = 8`；
  · 比值是舍过的（`packaging_parts._round(value) = round(float(value), 3)`，`packaging_parts.py:439`），
    所以反推在分母变大时会偏 ±1：合成 3000 件里 2 件角色已知 → 比值 `0.001` → 反推 **3**
    （多报 1，地板 3 会被放行）；合成 16000 件里 1 件 → 比值 `0.0` → 反推 **0**（少报 1）。
    也就是说 go/no-go 的地板由三位小数的舍入决定，而两份真图今天恰好整除（0 与 9）才没出事。

纪律：合成夹具（纯函数）+ 真实样本（`CPQ_DWG_REAL_SAMPLES=1` 才真转换；样本只读、产物只写
临时目录）+ 源码守卫（ast）；不连 PG / 34、不起服务、不发 HTTP、不写业务数据、不改业务实现。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
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

PARTS_MOD = "tech_app.backend.services.packaging_parts"
PARTS_SRC = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
GATE_MOD = "tech_app.tools.packaging_parts_gate"
GATE_SRC = ROOT / "tech_app" / "tools" / "packaging_parts_gate.py"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "cad_ir"
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"

RATIO_KEY = "role_known_ratio"
TOTAL_KEY = "role_known_total"
#: 四个早就显式给出的分子 + 厚度反面膜件数（Spec §2.3 冻结面：本批只加 `role_known_total`）。
EXISTING_NUMERATORS = ("closed_total", "material_known_total", "thickness_known_total",
                       "thickness_unknown_total", "processable_total")
#: 门禁读数与地板（Spec `packaging-parts-gate-threshold-recalibration.md` §C1）——本批一个字不改。
FROZEN_THRESHOLDS = {"酒盒.dwg": {"closed_total": 7},
                     "圆盘盒.dwg": {"closed_total": 32, "role_known_total": 8}}
FROZEN_GATE_ITEMS_HEAD = (("parts_outline_engine", "auto"),
                          ("parts_outline_real_sample", "auto"),
                          ("parts_panel_wired", "auto"),
                          ("parts_downstream_wired", "auto"),
                          ("parts_3d_wired", "auto"),
                          ("parts_demo_script", "manual"))


def fixture_module():
    """按路径加载夹具构造器（不 import `tests` 包，避免依赖 __init__.py）。"""
    path = FIXTURE_DIR / "build_fixtures.py"
    spec = importlib.util.spec_from_file_location("cpq_cad_ir_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parts_module():
    return importlib.import_module(PARTS_MOD)


def gate_module():
    return importlib.import_module(GATE_MOD)


def synth_doc():
    """合成 CAD IR → 真零件文档（4 件、其中 3 件角色已知 —— 打底用）。"""
    return parts_module().extract(fixture_module().parts_panels(), None)


def doc_with_roles(roles):
    """按给定 role 清单造一份最小零件文档（只为喂 `summarize()`，不假装是图形学夹具）。"""
    rows = [{"part_code": "DWG-P%05d" % (index + 1),
             "component_id": "cmp:%d" % (index + 1),
             "role": role,
             "outline_status": "open"}
            for index, role in enumerate(roles)]
    return {"engine_version": parts_module().ENGINE_VERSION,
            "stats": {"part_total": len(rows)}, "parts": rows, "filtered": []}


def role_known_of(rows):
    """从零件行重算「角色已知件数」（与引擎同一句口径：`_text(role) not in ("", "unknown")`）。"""
    return sum(1 for row in rows if str(row.get("role") or "").strip() not in ("", "unknown"))


def summarize(doc):
    return parts_module().summarize(doc)


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def key_reads(node):
    """一个函数体里**读**过哪些字典键：`x["k"]` 与 `x.get("k")` 两种写法都算。"""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Subscript):
            slice_node = sub.slice
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                out.add(slice_node.value)
        elif isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else "")
            if name in ("get", "setdefault") and sub.args:
                first = sub.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    out.add(first.value)
    return out


def returned_dict(tree, func_name):
    """函数里那个「最大」的 `return {...}`（`summarize()` 的摘要字典）。"""
    func = _find_function(tree, func_name)
    assert func is not None, "找不到 %s()" % func_name
    best = None
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        if best is None or len(node.value.keys) > len(best.keys):
            best = node.value
    assert best is not None, "找不到 %s() 里的 return {...}" % func_name
    return best


def value_of(dict_node, key):
    for dict_key, value in zip(dict_node.keys, dict_node.values):
        if isinstance(dict_key, ast.Constant) and dict_key.value == key:
            return value
    return None


def names_in(node):
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def ratio_subject_names(node):
    """`_ratio(role_known)` 里的 `role_known`；不是这种形状就退回整棵子树的名字集。"""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1:
        return names_in(node.args[0])
    return names_in(node)


class SyntheticNumeratorCase(unittest.TestCase):
    maxDiff = None

    # ------------------------------------------------------------------ R1
    def test_r1_summarize_states_the_role_known_numerator(self):
        summary = summarize(doc_with_roles(["cut", "unknown", ""]))
        self.assertTrue(TOTAL_KEY in summary,
                      "`summarize()` 必须像 `closed_total` 那样把角色已知件的**分子**说出来"
                      "（Spec §2.1）：缺 %r —— 缺了它，门禁只能拿比值 × 分母反推" % TOTAL_KEY)
        self.assertEqual(summary[TOTAL_KEY], 1,
                         "3 件里 1 件角色已知（`unknown` 与空串都不算已知，Spec §2.1）")
        self.assertEqual(summary["part_total"], 3)

    # ------------------------------------------------------------------ R2
    def test_r2_numerator_is_recomputable_and_ratio_is_unchanged(self):
        summary = summarize(doc_with_roles(["cut", "unknown", ""]))
        self.assertTrue(TOTAL_KEY in summary, "缺 %r（Spec §2.1）" % TOTAL_KEY)
        rows = doc_with_roles(["cut", "unknown", ""])["parts"]
        self.assertEqual(summary[TOTAL_KEY], role_known_of(rows),
                         "分子必须等于从零件行重算的值（同一次遍历，Spec §2.1）")
        self.assertLessEqual(0, summary[TOTAL_KEY])
        self.assertLessEqual(summary[TOTAL_KEY], summary["part_total"])
        self.assertEqual(summary[RATIO_KEY], round(summary[TOTAL_KEY] / summary["part_total"], 3),
                         "比值口径不变：`role_known_ratio == round(分子/分母, 3)`（Spec §2.1）")
        self.assertEqual(summary[RATIO_KEY], 0.333,
                         "1/3 的比值取值与精度一个字不改（Spec §2.3 冻结 `_round` 3 位）")

    def test_r2b_zero_parts_keeps_the_old_ratio_and_a_zero_numerator(self):
        summary = summarize(doc_with_roles([]))
        self.assertTrue(TOTAL_KEY in summary, "缺 %r（Spec §2.1）" % TOTAL_KEY)
        self.assertEqual(summary[TOTAL_KEY], 0, "没有零件时分子是 0，不是 null")
        self.assertEqual(summary[RATIO_KEY], 0.0, "分母为 0 时比值仍是 0.0（口径不变）")

    # ------------------------------------------------------------------ R3
    def test_r3_reconstruction_from_the_ratio_would_be_wrong(self):
        cases = ((3000, 2, 0.001, 3), (16000, 1, 0.0, 0))
        for total, known, frozen_ratio, reconstructed in cases:
            with self.subTest(part_total=total, role_known=known):
                roles = ["cut"] * known + ["unknown"] * (total - known)
                summary = summarize(doc_with_roles(roles))
                self.assertTrue(TOTAL_KEY in summary,
                              "缺 %r（Spec §2.1）：门禁就只能反推" % TOTAL_KEY)
                self.assertEqual(summary[TOTAL_KEY], known,
                                 "分子必须是真值 %d（Spec §2.1）" % known)
                self.assertEqual(summary[RATIO_KEY], frozen_ratio,
                                 "比值精度冻结在 3 位：%d 件里 %d 件就是 %r（Spec §2.3）"
                                 % (total, known, frozen_ratio))
                guess = int(round(summary[RATIO_KEY] * total))
                self.assertEqual(guess, reconstructed,
                                 "反推公式本身是确定性的：%d 件里 %d 件会被反推成 %d"
                                 % (total, known, reconstructed))
                self.assertNotEqual(guess, summary[TOTAL_KEY],
                                    "反推会偏（Spec §1）：少了这个分子，门禁的 go/no-go 地板"
                                    "就由三位小数的舍入决定")

    # ------------------------------------------------------------------ R7
    def test_r7_existing_numerators_and_the_ratio_are_untouched(self):
        doc = synth_doc()
        summary = summarize(doc)
        rows = [row for row in (doc.get("parts") or []) if isinstance(row, dict)]
        for key in EXISTING_NUMERATORS:
            self.assertIn(key, summary, "既有分子键 %r 不许消失（Spec §2.3）" % key)
        self.assertEqual(summary["closed_total"],
                         sum(1 for row in rows
                             if str(row.get("outline_status") or "").strip() == "closed"),
                         "`closed_total` 口径不变（Spec §2.3）")
        self.assertEqual(summary["processable_total"],
                         sum(1 for row in rows if parts_module().processability(row).get("ok")),
                         "`processable_total` 口径不变（Spec §2.3）")
        self.assertEqual(summary["thickness_unknown_total"],
                         max(0, summary["part_total"] - summary["thickness_known_total"]),
                         "`thickness_unknown_total` 口径不变（Spec §2.3）")
        self.assertEqual(summary[RATIO_KEY], round(role_known_of(rows) / summary["part_total"], 3),
                         "`role_known_ratio` 取值口径不变（Spec §2.3）")

    # ------------------------------------------------------------------ R8
    def test_r8_engine_source_shares_one_traversal_for_ratio_and_numerator(self):
        source = PARTS_SRC.read_text(encoding="utf-8")
        tree = ast.parse(source)
        summary_dict = returned_dict(tree, "summarize")
        ratio_node = value_of(summary_dict, RATIO_KEY)
        total_node = value_of(summary_dict, TOTAL_KEY)
        self.assertIsNotNone(total_node,
                             "`summarize()` 的返回字典里必须有 %r（Spec §2.1）；"
                             "今天它只能由比值反算，所以引擎里根本没有这个键" % TOTAL_KEY)
        subjects = ratio_subject_names(ratio_node)
        self.assertTrue(subjects, "找不到算比值用的那个计数变量（Spec §2.1）")
        self.assertEqual(subjects, names_in(total_node),
                         "分子必须和比值出自**同一个计数变量**（Spec §2.1/§2.3）："
                         "比值的被乘数是 %s，分子却是 %s" % (sorted(subjects), sorted(names_in(total_node))))
        self.assertNotIn("ratio", ast.unparse(total_node),
                         "分子不许由比值反算（Spec §2.3）：%s" % ast.unparse(total_node))
        counter = sorted(subjects)[0]
        binding = None
        for node in ast.walk(_find_function(tree, "summarize")):
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == counter for target in node.targets):
                binding = node.value
        self.assertIsNotNone(binding, "找不到 `%s = …` 的计数语句（Spec §2.1）" % counter)
        counted = ast.unparse(binding)
        self.assertIn("role", counted, "计数的那个变量必须数的就是 role（Spec §2.1）：%s" % counted)
        self.assertIn("unknown", counted,
                      "「角色已知」的判据仍是 `role` 不是 `\"\"`/`unknown`（Spec §2.1）：%s" % counted)


class GateNumeratorCase(unittest.TestCase):
    maxDiff = None

    # ------------------------------------------------------------------ R5
    def test_r5_gate_reads_the_engine_numerator_instead_of_reconstructing_it(self):
        tree = ast.parse(GATE_SRC.read_text(encoding="utf-8"))
        func = _find_function(tree, "_sample_metrics")
        self.assertIsNotNone(func, "门禁里找不到 `_sample_metrics()`（Spec §2.2）")
        reads = key_reads(func)
        self.assertTrue(TOTAL_KEY in reads,
                      "`_sample_metrics()` 必须直读引擎给的分子 `summary[%r]`（Spec §2.2）；"
                      "今天它一个键都没读到，是拿比值乘分母反推的" % TOTAL_KEY)
        self.assertNotIn(RATIO_KEY, reads,
                         "`_sample_metrics()` 不许再出现 `%s` 这个取数（Spec §2.2）："
                         "比值是舍过的，拿它乘分母反推会让地板被舍入左右" % RATIO_KEY)
        body = ast.unparse(func)
        self.assertNotIn(RATIO_KEY, body,
                         "反推写法（含 `.get(\"%s\")`）必须一并去掉（Spec §2.2）" % RATIO_KEY)

    # ------------------------------------------------------------------ R6
    def test_r6_gate_floors_and_verdict_semantics_are_frozen(self):
        gate = gate_module()
        self.assertEqual(gate.THRESHOLDS, FROZEN_THRESHOLDS,
                         "门禁地板一个字不改（Spec §2.2）")
        head = tuple((item_id, kind) for item_id, kind, _title in gate.GATE_ITEMS[:len(FROZEN_GATE_ITEMS_HEAD)])
        self.assertEqual(head, FROZEN_GATE_ITEMS_HEAD,
                         "六项清单的 id 与顺序冻结，新增项只能追加在末尾（Spec §2.2）")

    def test_r6b_sample_verdict_compares_counts_only(self):
        gate = gate_module()
        passing = {"role_known_total": 8, "closed_total": 32,
                   "processable_total": 1, "solid_total": 1}
        self.assertEqual(gate.sample_verdict(ROUND_BOX, passing), [],
                         "等于地板算过（Spec §2.2）")
        below = dict(passing, role_known_total=7)
        reasons = gate.sample_verdict(ROUND_BOX, below)
        self.assertEqual(len(reasons), 1, "低于地板给 1 条原因（Spec §2.2）：%r" % reasons)
        self.assertIn("role_known_total", reasons[0])
        loud_ratio = dict(below, role_known_ratio=0.99)
        self.assertEqual(gate.sample_verdict(ROUND_BOX, loud_ratio), reasons,
                         "不许「比值或计数」双通道：比值再漂亮也不能把计数地板顶掉"
                         "（Spec `packaging-parts-gate-threshold-recalibration.md` §C1/§C3）")
        frozen = dict(passing)
        gate.sample_verdict(ROUND_BOX, frozen)
        self.assertEqual(frozen, passing, "`sample_verdict()` 是纯函数：不改入参")
        self.assertEqual(gate.sample_verdict(ROUND_BOX, passing), [],
                         "`sample_verdict()` 是纯函数：两次调用同一个答案")


class RealSampleRoleKnownNumerator(unittest.TestCase):
    """R4：两份真图的真值 —— 分子必须能从零件行重算出来，比值口径一个字不改。"""

    workspace = None
    docs = {}

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
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-role-known-"))
        try:
            cad_ir = importlib.import_module("tech_app.backend.services.cad_ir")
            sem = importlib.import_module("tech_app.backend.services.packaging_semantics")
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
                cls.docs[name] = parts_module().extract(ir, sem.analyze(ir))
        except unittest.SkipTest:
            raise
        except Exception as exc:                                     # noqa: BLE001
            raise unittest.SkipTest("真实样本转换/解析失败：%s: %s" % (type(exc).__name__, exc))

    @classmethod
    def tearDownClass(cls):
        if cls.workspace is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def test_r4_real_samples_state_their_numerator(self):
        for name in (WINE_BOX, ROUND_BOX):
            doc = self.docs[name]
            summary = summarize(doc)
            rows = [row for row in (doc.get("parts") or []) if isinstance(row, dict)]
            expected = role_known_of(rows)
            print("%s：part_total=%d 角色已知=%d ratio=%r（重算 %d）"
                  % (name, summary.get("part_total"), expected, summary.get(RATIO_KEY), expected))
            self.assertTrue(TOTAL_KEY in summary,
                          "%s：`summarize()` 必须给出角色已知件的分子（Spec §2.1）——"
                          "今天是靠 `int(round(ratio × part_total))` 反推的" % name)
            self.assertEqual(summary[TOTAL_KEY], expected,
                             "%s：分子必须等于从零件行重算的值（Spec §2.1）" % name)
            self.assertLessEqual(0, summary[TOTAL_KEY])
            self.assertLessEqual(summary[TOTAL_KEY], summary["part_total"])
            self.assertEqual(summary[RATIO_KEY],
                             round(summary[TOTAL_KEY] / summary["part_total"], 3),
                             "%s：比值口径不变 `round(分子/分母, 3)`（Spec §2.1/§2.3）" % name)
            for key in EXISTING_NUMERATORS:
                self.assertIn(key, summary, "%s：既有分子键 %r 不许消失（Spec §2.3）" % (name, key))


if __name__ == "__main__":
    unittest.main()
