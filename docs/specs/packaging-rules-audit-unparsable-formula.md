# Spec：规则对账把"这条公式我读不懂"判成"你抄错了"（`source_cell_mismatch` + 退出码 2），而且报告里说不出是哪一边读不懂

状态：Spec + 红测（已实现）（原状：本批只改"逐字校验读不懂时的出口"：`verbatim_compare()` 已经算出
`reason = "unparsable"`，工具却把它折进 `source_cell_mismatch`；等价判据与既有文案本身不改）
红测：`tests/test_packaging_rules_audit_unparsable_formula_red.py`
血缘：`packaging-cost-minimum-charge.md` §5.3（逐字等价的判据与退出码 2）、§7（对账工具问题码表）、
§8（非目标：不改表达式数值与冻结黄金值）；工具 `tech_app/tools/extract_packaging_rules.py`。
本批 changelog 条目号：`## 441`。

## 0. 一句话目标

对账工具碰到**它自己解析不出来**的公式原文时，必须说"这一条我没能校验"（新码
`unparsable_formula`、退出码 1），并且说清是**哪一边**读不懂；不许再说成
"expression 与 <cell> 原文不逐字等价（改写不等于等价）" + 退出码 2（来源或引用损坏）。

## 1. 现状缺口（代码级，逐条实测；不是推断）

`tech_app/tools/extract_packaging_rules.py:479-492`：

```python
compare = verbatim_compare(str(item.get("expression") or ""), declared_source,
                           item.get("variable_map") or {}, cell, literals)
if compare.get("unmapped"):
    add("unmapped_variable", where, ...)
elif not compare.get("equivalent"):                 # ← unparsable 也落进这一支
    target = workbook_source or declared_source
    if target:
        add("source_cell_mismatch", where,
            "expression 与 %s 原文不逐字等价（改写不等于等价）" % cell, EXIT_BROKEN)
```

`verbatim_compare()`（`packaging_cost.py:811-832`）在**任一侧规范化失败**时返回
`reason = "unparsable"`：`_canonical_formula()`（`:768-779`）对
`_tokenize` 抛 `FormulaError` 的原文返回 `None`，而 `_tokenize` 只认数字、名字、`+-*/()`、比较符
—— 区间 `:`、`%`、`$`、`^`、`&`、`'Sheet'!`、数组常量等一律解析不了。

于是三种**完全不同的现实**在报告里长得一模一样（本机实测，同一份合成夹具）：

| 夹具 | 今天的码 | 今天的退出码 | 真相 |
| --- | --- | --- | --- |
| 两边都解析得出、规范串不同（`0.75` vs `0.74`） | `source_cell_mismatch` | 2 | 确实改写了 ✅ |
| 来源原文含区间 `=SUM(H2:H10)/J2` | `source_cell_mismatch` | 2 | **解析器读不懂**，谁也没改写 ❌ |
| 快照没写 `source_formula`（工作簿该格有公式） | `source_cell_mismatch` | 2 | **快照漏字段** ❌ |

第三行是最容易被踩的：`declared_source = str(item.get("source_formula") or "")`，漏写就是空串，
`_canonical_formula("")` 返回 `None` → `unparsable` → 报"改写不等于等价"。

`verbatim_compare()` 手里其实**有答案**（`resolved` 与 `source` 哪个是空串就说明哪一侧读不懂），
但它没有把这个信息暴露成字段，工具也没去看 `reason`，于是：

- 错误结论：把"我读不懂 / 快照漏字段"说成"你改写了公式"；
- 错误档位：退出码 2（来源或引用损坏）比"口径不一致"（1）更重，`main()` 只在 `ok` 时允许回写，
  门禁/CI 会按"快照坏了"红给一条其实没问题的公式；
- 无法分辨：修的人只能去改一条本来没错的公式，或者把 `verbatim: false` 当挡箭牌（那是给**申报过的
  改写**用的，不是给"工具读不懂"用的）。

真实快照现状（本机实测，只读运行工具、不带 `--write`）：`报价逻辑-0903.xlsx` +
`packaging_cost_rules.json` 今天 `exit_code=0`，即**这条缺口目前是潜伏的**：现有条目的
`source_formula` 恰好都落在解析器能力内（`PKG-C-LABOR` 走的是申报过的 `declared_deviation`）。
一旦换来源、换工作簿版本，或某条来源公式里出现区间/百分号/`$` 绝对引用，今天就会立刻
把"读不懂"报成"抄错了"。

## 2. 契约

### 2.1 `verbatim_compare()` 把"哪一侧读不懂"交出来

`tech_app/backend/services/packaging_cost.py::verbatim_compare()` 的返回体**新增**键
`unparsable`：

- 类型 `list[str]`，元素只允许 `"expression"` / `"source"`；
- 两侧都读不懂 → `["expression", "source"]`（**固定顺序**：先 `expression` 后 `source`）；
- 只有一侧读不懂 → 只含那一侧；
- 两侧都读得懂 → `[]`（包含"不等价"与"等价"两种）；
- 判据写死为"该侧 `_canonical_formula()` 返回 `None`"，不是"看着不像 Excel"、不是字符串长度；
- `reason` 仍为 `"unparsable"`（**不新增** `reason` 取值）；`equivalent` / `unmapped` / `resolved` /
  `source` / `reason` 六个既有键的语义与判据逐字不变。

### 2.2 对账出口：读不懂有它自己的码

`tech_app/tools/extract_packaging_rules.py::audit_rules()` 的逐字对账分支：

- `compare["reason"] == "unparsable"` → 报 **`unparsable_formula`**，`exit_code = 1`；`where` 的取法
  与既有逐字对账同一个（`"<formula_code>(<sheet>!<cell>)"` 那一套不变）；
- `detail` 必须**点名哪一边**，三种写法覆盖 `expression` / `source` / `expression+source`
  （例如"表达式与 <cell> 原文至少一侧解析不出规范串（读不懂，不是改写）：source"）；
- 同一处**不得**再报 `source_cell_mismatch`：两种现实不许共用一个码；
- 真正的不等价（两侧都解析得出、规范串不同）→ 仍 `source_cell_mismatch` + `exit_code = 2`，
  detail 文案逐字不变；
- `unmapped_variable` 优先级不变：有未映射变量时先报它，同一处不再报 `unparsable_formula`；
- `source_cell_has_no_formula`（该格没有公式）/ `cached_without_formula` / `hidden_sheet_source` /
  `declared_deviation` / 逐列证据各路径**一字不动**。

### 2.3 码表要跟着说

- 工具 docstring 头部的判定清单（`tech_app/tools/extract_packaging_rules.py:23-45` 那段）补上
  `unparsable_formula`；
- `docs/specs/packaging-cost-minimum-charge.md` §7 的表补一行：

| `problems[].code` | 触发条件 | `exit_code` |
| --- | --- | --- |
| `unparsable_formula` | `expression` 或 `source_cell` 原文至少一侧解析不出规范串（**读不懂，不是改写**） | 1 |

## 3. 非目标

- 不许把"读不懂"折成通过（`ok=True`），也不许折成 `unmapped_variable` 或 `source_cell_mismatch`；
- 不许改 `source_cell_mismatch` 的判据、文案、退出码；不许改 `EXIT_OK/MISMATCH/BROKEN/SOURCE_CHANGED/
  WRITE_REFUSED` 五个数值；
- 不许放宽 `verbatim_compare()` 的等价判据（不许"子串相同就算等价"、不许"解析失败就当等价"），
  不许新增 `reason` 取值；
- 不许动复算（`recompute`）、缓存值对账、逐列证据（`_audit_evidence`）、`declared_deviation`；
- 不许改快照 JSON `tech_app/agent_knowledge/rules/packaging_cost_rules.json`、不许改工作簿、
  不许新增 `verbatim: false` 条目来绕过本码。

## 4. 红测映射（`tests/test_packaging_rules_audit_unparsable_formula_red.py`，8 条）

| 用例 | 夹具 | 期望 | 现状 |
| --- | --- | --- | --- |
| U1 | 来源原文 `=SUM(H2:H10)/J2`（区间） | 码集含 `unparsable_formula`、**不含** `source_cell_mismatch`、退出码 1 | 红（今天 `source_cell_mismatch` / 2） |
| U2 | 同上 | `detail` 点名 `source` 侧、且不再出现"改写不等于等价" | 红 |
| U3 | 快照 `source_formula` 为空、工作簿该格有公式 | 码集含 `unparsable_formula`、**不含** `source_cell_mismatch`、退出码 1 | 红 |
| U4 | `verbatim_compare()` 四态：只 source / 只 expression / 两侧 / 都读得懂 | 新键 `unparsable` 逐态相等（顺序固定）；`reason` 仍是 `unparsable`（都读得懂时为 `""` / `"differs"`） | 红（今天没有该键） |
| U5 | 源码与文档守卫 | 工具 docstring 头部 + `docs/specs/packaging-cost-minimum-charge.md` §7 都写了 `unparsable_formula`，且表里退出码列是 `1` | 红 |
| U6 | 真不等价（`0.75` vs `0.74`，两边都解析得出） | 仍 `source_cell_mismatch` + 退出码 2，且**不**报 `unparsable_formula` | 护栏（今天绿） |
| U7 | 表达式含未映射变量 | 仍 `unmapped_variable`，**不**报 `unparsable_formula` | 护栏（今天绿） |
| U8 | 干净快照；以及 `source_cell` 指到工作簿里不存在的格 | 干净 → `ok=True` / `0`；不存在的格 → `source_cell_has_no_formula` 仍在、退出码仍 2 | 护栏（今天绿） |

## 5. 实测（本机，HEAD 工作副本）

```text
tests.test_packaging_rules_audit_unparsable_formula_red   Ran 8 … FAILED (failures=5)
  U1 来源原文 =SUM(H2:H10)/J2     → 今天报 source_cell_mismatch / 退出码 2      （红）
  U2 同一句 detail               → 今天说「改写不等于等价」、不点名哪一侧        （红）
  U3 快照漏写 source_formula     → 今天报 source_cell_mismatch / 退出码 2      （红）
  U4 verbatim_compare() 四态     → 今天没有 unparsable 键（None）              （红）
  U5 源码头部与 §7 码表          → 今天两处都没有 unparsable_formula            （红）
  U6 真不等价（0.75 vs 0.74）→ 仍 source_cell_mismatch + 2                    （护栏绿）
  U7 未映射变量 → 仍 unmapped_variable、不叠加读不懂                          （护栏绿）
  U8 干净快照 ok/0；该格没有公式 → source_cell_has_no_formula + 2             （护栏绿）

不回归（对账工具三批，全部离线）：
tests.test_packaging_cost_minimum_charge_red   Ran 47 OK (skipped=1)
```

真实快照 + 真实工作簿（`报价逻辑-0903.xlsx`）今天 `exit_code = 0`：本缺口**目前潜伏**，
现有条目的 `source_formula` 都落在解析器能力内；换来源/换工作簿版本后立刻会现形（§1 末段）。

## 6. 交付边界

本批只写 Spec + 红测（业务实现不在本批）；未改业务实现、未改既有测试、未放宽任何断言、
未起服务、未发 HTTP、未连 PG / 34、未写业务数据、未跑真 DWG；未 push / MR / tag / Release / 未部署。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 440`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 交出「哪一侧读不懂」 | `packaging_cost.py:verbatim_compare()` | 返回体**新增** `unparsable`（`[]` / `["expression"]` / `["source"]` / `["expression","source"]`，顺序固定）；判据写死为「该侧 `_canonical_formula()` 返回 `None`」；`equivalent` / `unmapped` / `resolved` / `source` / `reason` 六个既有键的语义与取值逐字不变（`reason` 仍是 `unparsable` / `differs` / `""` / `unmapped_variable` / `bad_source_cell`） |
| §2.2 读不懂有它自己的码 | `tools/extract_packaging_rules.py::audit_rules()` 的逐字对账分支 | 新增 `unparsable_formula` + `EXIT_MISMATCH`(1)，detail 逐字「表达式与 <cell> 原文至少一侧解析不出规范串（读不懂，不是改写）：<侧>」；同一处**不再**报 `source_cell_mismatch`；真不等价仍 `source_cell_mismatch` + 2（文案逐字未动）；`unmapped_variable` 优先级不变 |
| §2.2 来源侧两处原文 | 同分支 | 来源侧不只看快照申报的 `source_formula`，**也看工作簿该格的原文**（工具本来就读了 `_cell_formula()`）：任一解析不出规范串 → 记 `source` 侧读不懂。依据见下面的出入说明 |
| §2.3 码表跟着说 | 工具 docstring 头部判定清单 + `docs/specs/packaging-cost-minimum-charge.md` §7 | 两处都补 `unparsable_formula`（§7 表里退出码列是 `1`） |
| §3 非目标 | 未动 | `EXIT_*` 五个数值、`source_cell_mismatch` 判据 / 文案 / 码、复算与缓存对账、逐列证据、`declared_deviation`、快照 JSON、工作簿全部未改；未新增 `reason` 取值 |

**红测夹具的一处出入（实现按红测走，记在这里）**：Spec §1 的表格把 U1 的"来源原文含区间"记成
**快照的 `source_formula`**，而红测 `test_u1` 实际把区间放在**工作簿单元格**（`cells_from({"AN2":
"=SUM(H2:H10)/J2"})`），快照的 `source_formula` 仍是可解析的 `=H2*I2/1000000*0.74/J2`。
因此 U1 在改前的**真实**现象不是 §5 写的「报 `source_cell_mismatch` / 退出码 2」，而是
**什么都不报**（`problems=set()`、`ok=True`）—— 比"报错"更危险的一种：读不懂的来源被当成通过。
实现据此把「工作簿该格原文」也纳入来源侧的解析判定（这正是工具本来就读的那份原文），
U1–U8 八条全绿；U3（快照漏字段）仍是 `source` 侧读不懂，两条现实不再共用一个码。

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_rules_audit_unparsable_formula_red
Ran 8 tests ... OK        （红基：Ran 8 ... FAILED (failures=5) —— U1/U2/U3/U4/U5 红，U6/U7/U8 绿护栏）

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_column_evidence_red tests.test_packaging_cost_engine_red \
  tests.test_packaging_cost_minimum_charge_red tests.test_packaging_cost_red_closure_red \
  tests.test_packaging_cost_rule_routing_red tests.test_packaging_cost_rule_snapshot_red
（连同本批红测）OK (skipped=1)

./open-claude/.venv/bin/python tech_app/tools/extract_packaging_rules.py --check
结论：通过（exit_code=0）      # 真实工作簿 + 真实快照，未因本批新增误报
```
