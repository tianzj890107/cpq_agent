"""红测：成本明细行「用了几个默认值、哪几个」要说得出、存得下、读得回（Spec
`packaging-cost-assumption-disclosure.md`）。

现状缺口（代码级，可指到行）：
  · `compute_line()` 逐行产出 `assumptions`（`_merge_variables()` `:1089` 给
    `"%s=0903=%s"` / `"%s=默认=%s"`），但 `_line_to_item()`（`:1813`）只透传
    `("formula_source", "rule_snapshot_version")`，`_item()`（`:1792`）键表里没有
    `assumptions` → 明细行这一份**丢在内存里**；
  · `wip_packaging_cost_item` 没有 `assumptions_json` 这一列
    （`da_repo._PACKAGING_COST_ITEM_COLUMNS:917` / `da_schema.sql` / `da_db._ADDED_COLUMNS`
    三处都没有）→ 就算带了也存不下；
  · 真引擎真库实测：算完 34 行里 13 行带非空 `assumptions`（合计 69 条，其中 1 条 `=0903=`），
    落库后没有任何一行带这一份；整单顶层 `assumptions` 只收运输那一条 → `[]`；
  · `requirement-confirm.js` 里 `assumptions` **0 处引用** → 页面上看不见"这个金额里有几个
    是默认值顶上去的"。

纪律：真引擎真库（临时 SQLite / 临时 meta）用于 A 组；B 组用假仓库行 + 打桩；C 组用
`node -e` 抽 `requirement-confirm.js` 的具名纯函数真跑；D 组为冻结面守卫。
不连 PG / 34、不发 HTTP、不写生产数据。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import inspect
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_cost as cost           # noqa: E402
from tech_app.backend.storage import da_db, da_repo                    # noqa: E402
from tests.test_packaging_cost_engine_red import CostCase              # noqa: E402

PID = "testpid00001"
REQ_NO = "REQ-COSTASSUME-001"

CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

SUMMARY_TEMPLATE = "这一单有 %d 条默认值，其中 %d 条来自 0903 常量。"
SUMMARY_EMPTY = "这一单没有用默认值顶上去的参数。"
SUMMARY_UNKNOWN = ("后端没给这本账（assumptions_total / assumptions_0903_total）："
                   "这一单用了几个默认值是未知，不是 0。")

#: `compute_line("die_cutting", {"imposition_count": None})` 的逐字清单（Spec §D2 守卫）。
DIE_CUT_ASSUMPTIONS = [
    "setup_minutes=0903=120.0", "capacity_per_hour=0903=6500.0",
    "equipment_rate=0903=197.52", "labor_rate=0903=190.06",
    "imposition_count=默认=1.0", "proof_base=默认=0.0", "front_colors=默认=0.0",
    "back_colors=默认=0.0", "overhead_seconds=默认=0.0", "overhead_rate=默认=0.0",
]

STORED_ITEMS = [
    {"seq": 2, "part_code": None, "part_name": "", "cost_category": "material",
     "assumptions_json": json.dumps(["imposition_count=默认=1.0", "proof_base=默认=0.0"],
                                    ensure_ascii=False)},
    {"seq": 1, "part_code": "P1", "part_name": "面纸", "cost_category": "print",
     "assumptions_json": json.dumps(["labor_rate=0903=190.06"], ensure_ascii=False)},
]


def _stored_estimate_row(assumptions="__absent__"):
    row = {"estimate_id": 7, "project_id": PID, "requirement_no": REQ_NO,
           "scenario_code": "default", "industry": "packaging",
           "engine_version": cost.ENGINE_VERSION, "cost_profile": cost.COST_PROFILE,
           "currency": "CNY", "quote_quantity": 1000, "tax_rate": 0.13,
           "loss_base_scope": cost.DEFAULT_LOSS_BASE_SCOPE,
           "material_total": 100.0, "process_total": 50.0, "labor_total": 20.0,
           "tooling_total": 0.0, "packaging_total": 5.0, "freight_total": 3.0,
           "other_total": 2.0, "subtotal": 180.0, "loss_amount": 1.8,
           "total_cost": 1234.5, "has_gaps": 0, "gaps_json": "[]", "computed_at":
           "2026-09-22 10:00:00", "computed_by": "PE1",
           "computed_by_role": "process_engineer"}
    if assumptions != "__absent__":
        row["assumptions_json"] = assumptions
    return row


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


def _load_cost(row, items=()):
    """读一条成本单：仓库行与明细行都打桩，其余走真实现。"""
    listing = [dict(item) for item in items]
    with _Patch((cost, "_resolve_requirement_no", lambda *a, **k: REQ_NO),
                (cost.da_repo, "load_packaging_cost", lambda *a, **k: row),
                (cost.da_repo, "load_packaging_cost_items", lambda *a, **k: listing),
                (cost, "_upstream_route_version", lambda *a, **k: ""),
                (cost.da_repo, "load_packaging_bom", lambda *a, **k: [])):
        return cost.load_cost(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# A 组：真引擎真库往返（算完那一份 == 读回来那一份）
# --------------------------------------------------------------------------- #
class ARoundTrip(CostCase):
    def assumptions_of(self, document):
        return [item.get("assumptions") for item in document["items"]]

    def test_a1_lines_carry_assumptions_and_read_back_verbatim(self):
        self.prepare()
        built = self.build_cost()
        carried = [row for row in built["items"] if row.get("assumptions")]
        self.assertTrue(carried,
                        "夹具前提：明细行必须带着 `compute_line()` 给的 assumptions"
                        "（Spec §3：34 行里 13 行非空）——现在一行都没有")
        loaded = self.load_cost()
        self.assertEqual(self.assumptions_of(built), self.assumptions_of(loaded),
                         "算完那一份默认值清单读回来必须逐字相同（Spec §C2）："
                         "现在重开成本单，这一行用了哪几个默认值就查不到了")

    def test_a2_item_table_persists_the_assumptions(self):
        self.prepare()
        built = self.build_cost()
        rows = self.db_rows("SELECT * FROM wip_packaging_cost_item ORDER BY seq")
        self.assertTrue(rows, "夹具前提：明细行已落库")
        self.assertIn("assumptions_json", rows[0],
                      "明细表必须有 `assumptions_json` 这一列（Spec §C1）")
        stored = [json.loads(row["assumptions_json"] or "[]") for row in rows]
        self.assertEqual(self.assumptions_of(built), stored,
                         "存的就是算出来的那一份，顺序与文案都不许改（Spec §C1）")

    def test_a3_top_level_counts_come_from_the_replayed_lines(self):
        self.prepare()
        built = self.build_cost()
        expected = sum(len(row.get("assumptions") or []) for row in built["items"])
        expected_0903 = sum(1 for row in built["items"]
                            for text in (row.get("assumptions") or [])
                            if "=0903=" in text)
        self.assertGreater(expected, 0, "夹具前提：这一单用了默认值（Spec §3：69 条）")
        loaded = self.load_cost()
        self.assertEqual(expected, loaded.get("assumptions_total"),
                         "顶层 `assumptions_total` 必须是各明细行合计（Spec §C3）")
        self.assertEqual(expected_0903, loaded.get("assumptions_0903_total"),
                         "顶层 `assumptions_0903_total` 数的是含 `=0903=` 的条数（Spec §C3）")

    def test_a4_read_side_does_not_recompute(self):
        self.prepare()
        built = self.build_cost()
        self.assertTrue([row for row in built["items"] if row.get("assumptions")],
                        "夹具前提：算完那一趟明细行带着 assumptions（Spec §3）")

        def boom(*args, **kwargs):
            raise AssertionError("读侧不许现算 assumptions（Spec §C2）")

        with _Patch((cost, "compute_line", boom), (cost, "_merge_variables", boom)):
            loaded = self.load_cost()
        self.assertEqual(self.assumptions_of(built), self.assumptions_of(loaded),
                         "读回来的就是算时那一份，与当前费率 / 规则快照无关（Spec §C2）")


# --------------------------------------------------------------------------- #
# B 组：读侧重放与老成本单（假仓库行）
# --------------------------------------------------------------------------- #
class BReplay(unittest.TestCase):
    def test_b1_stored_assumptions_are_replayed(self):
        out = _load_cost(_stored_estimate_row(), STORED_ITEMS)
        self.assertEqual(["imposition_count=默认=1.0", "proof_base=默认=0.0"],
                         out["items"][0].get("assumptions"),
                         "逐字回放落库那一份（Spec §C2）")
        self.assertEqual(["labor_rate=0903=190.06"], out["items"][1].get("assumptions"),
                         "逐字回放落库那一份（Spec §C2）")

    def test_b2_legacy_item_reports_empty_not_none(self):
        legacy = [{"seq": 1, "part_code": "P1", "part_name": "面纸",
                   "cost_category": "print"}]
        out = _load_cost(_stored_estimate_row(), legacy)
        item = out["items"][0]
        self.assertIn("assumptions", item,
                      "老明细行也要有 `assumptions` 键（Spec §C2）：取不到就是空清单")
        self.assertIsInstance(item["assumptions"], list, "类型是列表（Spec §C2）")
        self.assertEqual([], item["assumptions"], "老明细行没记过 → 空清单，不许编（Spec §C2）")
        self.assertEqual(0, out.get("assumptions_total"), "取不到就是 0（Spec §C3）")
        self.assertEqual(0, out.get("assumptions_0903_total"), "取不到就是 0（Spec §C3）")

    def test_b3_unreadable_column_is_not_fatal(self):
        for bad in (None, "{}", "not json", "7"):
            items = [{"seq": 1, "part_code": "P1", "part_name": "面纸",
                      "cost_category": "print", "assumptions_json": bad}]
            out = _load_cost(_stored_estimate_row(), items)
            item = out["items"][0]
            self.assertIn("assumptions", item,
                          "解不出也要有这一键（Spec §C2）：%r" % bad)
            self.assertEqual([], item["assumptions"],
                             "解不出 / 不是列表 → 空清单，不许抛（Spec §C2）：%r" % bad)

    def test_b4_counts_none_and_unknown_are_not_zero_pretending(self):
        items = [{"seq": 1, "part_code": "P1", "part_name": "面纸",
                  "cost_category": "print",
                  "assumptions_json": json.dumps(["a=默认=1.0", "b=0903=2.0"])},
                 {"seq": 2, "part_code": None, "part_name": "",
                  "cost_category": "material",
                  "assumptions_json": json.dumps(["c=默认=3.0"])}]
        out = _load_cost(_stored_estimate_row(), items)
        self.assertEqual(3, out.get("assumptions_total"), "只从回放后的明细行算（Spec §C3）")
        self.assertEqual(1, out.get("assumptions_0903_total"), "数含 `=0903=` 的条数（Spec §C3）")

    def test_b5_top_level_assumptions_semantics_unchanged(self):
        out = _load_cost(_stored_estimate_row(json.dumps(["loading_rate=不可用，只走最低运费分支"],
                                                         ensure_ascii=False)), STORED_ITEMS)
        self.assertEqual(["loading_rate=不可用，只走最低运费分支"], out["assumptions"],
                         "顶层 `assumptions` 照旧逐字回放 estimate 行那一份（Spec §C3）")

    def test_b6_not_built_still_has_the_two_keys(self):
        out = _load_cost(None, ())
        self.assertIs(False, out.get("built"), "夹具前提：还没算过")
        self.assertEqual(0, out.get("assumptions_total"),
                         "还没算过也给 0，不许 null / 抛错（Spec §C3）")
        self.assertEqual(0, out.get("assumptions_0903_total"),
                         "还没算过也给 0，不许 null / 抛错（Spec §C3）")


# --------------------------------------------------------------------------- #
# C 组：前端成本面板这本账（node 真跑具名纯函数）
# --------------------------------------------------------------------------- #
EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extract(name) {
  const at = src.indexOf("function " + name + "(");
  if (at < 0) return null;
  let i = src.indexOf("{", at);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(at, j + 1); }
  }
  return null;
}
const name = process.argv[3];
const mode = process.argv[4];
const extras = JSON.parse(process.argv[5] || "[]");
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
if (mode === "body") { console.log(JSON.stringify({ missing: false, body: fn })); process.exit(0); }
const cases = JSON.parse(mode);
eval(extras.map(extract).filter(Boolean).concat([fn]).join("\n"));
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""

BLOCK_DEPS = ["pcEsc", "pcAssumptions"]


def run_cases(name: str, cases, also=()):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(CONFIRM_JS), name,
                           json.dumps(list(cases)), json.dumps(list(also))],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("requirement-confirm.js 缺少纯函数 %s()（Spec §C1/C4）" % name)
    for index, item in enumerate(payload["results"]):
        if not item.get("ok"):
            raise AssertionError("%s() 第 %d 个入参抛异常：%s（Spec §C4）"
                                 % (name, index, item.get("error")))
    return [item["value"] for item in payload["results"]]


def value(name: str, args, also=()):
    return run_cases(name, [args], also=also)[0]


ITEM_A = {"seq": 1, "part_code": "P1", "part_name": "面纸", "cost_category": "print",
          "assumptions": ["labor_rate=0903=190.06", "proof_base=默认=0.0"]}
ITEM_B = {"seq": 2, "part_code": None, "part_name": "", "cost_category": "material",
          "assumptions": ["imposition_count=默认=1.0"]}


class CPanel(unittest.TestCase):
    def test_c1_counts_are_taken_from_the_payload(self):
        out = value("pcAssumptions", [{"assumptions_total": 3, "assumptions_0903_total": 1},
                                      [ITEM_A, ITEM_B]], also=["pcEsc"])
        self.assertEqual(3, out["total"], "计数逐字取后端（Spec §C4）")
        self.assertEqual(1, out["source0903"], "计数逐字取后端（Spec §C4）")
        self.assertEqual(SUMMARY_TEMPLATE % (3, 1), out["summary"], "摘要句逐字（Spec §C4）")
        self.assertEqual(3, len(out["rows"]), "逐条展开明细行里的假设（Spec §C4）")
        self.assertEqual(["面纸", "面纸", "项目级"], [row["part"] for row in out["rows"]],
                         "部件名空 → 项目级（Spec §C4）")
        self.assertEqual(["labor_rate=0903=190.06", "proof_base=默认=0.0",
                          "imposition_count=默认=1.0"],
                         [row["text"] for row in out["rows"]],
                         "假设原文逐字、按 seq 升序（Spec §C4）")

    def test_c2_missing_counts_are_unknown_not_zero(self):
        out = value("pcAssumptions", [{}, [ITEM_A]], also=["pcEsc"])
        self.assertIsNone(out["total"], "取不到计数 → null，不许当 0（Spec §C4）")
        self.assertEqual(SUMMARY_UNKNOWN, out["summary"], "「未知」不是「0」（Spec §C4）")

    def test_c3_empty_book_says_so_in_chinese(self):
        out = value("pcAssumptions", [{"assumptions_total": 0, "assumptions_0903_total": 0},
                                      []], also=["pcEsc"])
        self.assertEqual([], out["rows"], "没有明细行就没有条目（Spec §C4）")
        self.assertEqual(SUMMARY_EMPTY, out["summary"], "空账一句中文（Spec §C4）")
        block = value("pcAssumptionsBlock",
                      [{"assumptions_total": 0, "assumptions_0903_total": 0}, []],
                      also=BLOCK_DEPS)
        self.assertIn('data-pc-assumptions="0"', block, "块带总数钩子（Spec §C4）")
        self.assertIn(SUMMARY_EMPTY, block, "空态也要说出那句中文（Spec §C4）")
        self.assertNotIn("<table", block, "空态不画空表（Spec §C4）")

    def test_c4_block_lists_every_assumption(self):
        block = value("pcAssumptionsBlock",
                      [{"assumptions_total": 3, "assumptions_0903_total": 1},
                       [ITEM_A, ITEM_B]], also=BLOCK_DEPS)
        self.assertIn('data-pc-assumptions="3"', block, "块带总数钩子（Spec §C4）")
        self.assertIn(SUMMARY_TEMPLATE % (3, 1), block, "摘要句进块（Spec §C4）")
        for index, text in enumerate(("labor_rate=0903=190.06", "proof_base=默认=0.0",
                                      "imposition_count=默认=1.0")):
            self.assertIn('data-pc-assumption="%d"' % index, block,
                          "逐条带序号钩子（Spec §C4）")
            self.assertIn(text, block, "假设原文逐字进块（Spec §C4）：%s" % text)


# --------------------------------------------------------------------------- #
# D 组：冻结面守卫
# --------------------------------------------------------------------------- #
class DFreeze(CostCase):
    def test_d1_schema_migration_and_write_side_agree(self):
        self.assertIn(("wip_packaging_cost_item", "assumptions_json", "TEXT"),
                      da_db._ADDED_COLUMNS,
                      "老库要靠 ALTER TABLE 补这一列（Spec §C1）")
        schema = (ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql").read_text(
            encoding="utf-8")
        at = schema.index("CREATE TABLE IF NOT EXISTS wip_packaging_cost_item")
        block = schema[at:schema.index(");", at)]
        self.assertIn("assumptions_json", block, "新库的建表语句也要有这一列（Spec §C1）")
        self.assertIn("assumptions_json", da_repo._PACKAGING_COST_ITEM_COLUMNS,
                      "明细行列清单要带上它（Spec §C1）")
        self.assertIn("assumptions_json", inspect.getsource(da_repo.save_packaging_cost),
                      "写侧要走同一处显式键（Spec §C1）")

    def test_d2_assumption_text_is_verbatim(self):
        line = self.cost_mod().compute_line("die_cutting", {"imposition_count": None})
        self.assertEqual(DIE_CUT_ASSUMPTIONS, line["assumptions"],
                         "`<name>=0903=<值>` / `<name>=默认=<值>` 的文案与顺序逐字不变（Spec §C5）")

    def test_d3_catalog_and_key_tables_are_frozen(self):
        self.assertEqual({"imposition_count": 1.0, "proof_base": 0.0, "front_colors": 0.0,
                          "back_colors": 0.0, "overhead_seconds": 0.0, "overhead_rate": 0.0},
                         dict(cost._IMPLICIT_DEFAULTS),
                         "隐式默认值一个数不改（Spec §C5）")
        self.assertEqual(0, cost.FORMULA_CATALOG["PKG-C-DIE-CUT"]["minimum_charge"],
                         "最低收费口径不动（Spec §C5）")
        self.assertIn("computed_by_role", da_repo._PACKAGING_COST_COLUMNS,
                      "estimate 行的列清单逐字不变（Spec §C5）")
        self.assertNotIn("assumptions_json", da_repo._PACKAGING_COST_COLUMNS,
                         "estimate 行的 `assumptions_json` 是既有显式键，不进列清单（Spec §C5）")

    def test_d4_panel_wires_the_block_and_still_checks(self):
        source = CONFIRM_JS.read_text(encoding="utf-8")
        self.assertIn("${pcAssumptionsBlock(cost, items)}", source,
                      "成本面板要挂上这本账（Spec §C4）")
        self.assertIn("${pcContentBindingBlock(cost)}", source,
                      "既有包材绑定那一块一个字不动（Spec §C4）")
        self.assertLess(source.index("${pcContentBindingBlock(cost)}"),
                        source.index("${pcAssumptionsBlock(cost, items)}"),
                        "新块挂在既有块之后（Spec §C4）")
        proc = subprocess.run(["node", "--check", str(CONFIRM_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, "内联脚本仍要过语法检查：%s" % proc.stderr[:400])


if __name__ == "__main__":
    unittest.main()
