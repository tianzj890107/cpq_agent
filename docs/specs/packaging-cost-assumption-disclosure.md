# 规格：成本明细行「用了几个默认值、哪几个」要说得出、存得下、读得回

依赖：`docs/specs/packaging-cost-engine.md` §2.4 / §2.5（**唯一权威口径**：「每个用默认值的变量都写进
该行的 `assumptions`（形如 `machine_length=默认=开料长`），并在 `gaps.defaulted` 里留痕」「0903 的常量
只作为 `FORMULA_CATALOG` 的默认参数，且必须在 `assumptions` 里标注 `source=0903`」）、
`docs/specs/packaging-cost-content-binding-replay.md` §C1/§C2（**同一条纪律的先例**：算出来那一份要落库、
读侧逐字回放，不许读时现取）、`docs/specs/packaging-silent-degradation-disclosure.md`（「算时说得清、
读回来就说不清」就是静默降级）。

状态：Spec + 红测（已实现）（原状：`compute_line()` 确实逐行产出 `assumptions`，但**出不了这一层** ——
`_line_to_item()`（`packaging_cost.py:1813`）只在 `("formula_source", "rule_snapshot_version")` 上做透传，
`_item()`（`:1792`）的键表里没有 `assumptions`，`wip_packaging_cost_item` 也没有这一列。实测（真引擎真库，
`tests/test_packaging_cost_engine_red.py` 的夹具）：算完 34 行明细里 **13 行**带着非空 `assumptions`
（合计 **69 条**，其中 1 条标注 `source=0903`），落库后 `SELECT * FROM wip_packaging_cost_item`
的行里 **含 `assumptions` 键的 = 0 行**，读回来的 `items[i]` 同样一条都没有；前端 `requirement-confirm.js`
里 `assumptions` **0 处引用** —— 一行公式「用了几个 0903 常量、哪几个」在页面上、在报告里都看不见）
红测：`tests/test_packaging_cost_assumption_disclosure_red.py`
行号基线：HEAD `09b2359`

## 0. 一句话目标

成本明细行算出来的那一份默认值清单（`机器宽=默认=开料宽`、`labor_rate=0903=190.06` 这种），
**存得下、读得回、说得出**：同一张成本单重开时，「这一行用了几个默认值、哪几个是 0903 常量」
与刚算完那一趟**逐字相同**，并且前端成本面板上能一次看完整单的这本账。

## 1. 现状缺口（实测，不是推断）

`tech_app/backend/services/packaging_cost.py`：

```python
# _line_to_item()（:1813）：明细行 → 落库前的 item；只透传两个追溯字段
for key in ("formula_source", "rule_snapshot_version"):
    if line.get(key) is not None:
        item[key] = line.get(key)          # ← 没有 assumptions

# _item()（:1792）：键表里没有 assumptions（只有 **extra 兜底，而 _line_to_item 没往 extra 里放）
```

1. **算得出来，出不了单行**：`_merge_variables()`（`:1089`）逐条产出 `"%s=0903=%s"` / `"%s=默认=%s"`，
   `compute_line()` 把它挂在 `line["assumptions"]`；`_line_to_item()` 不取它 → 明细行丢；
2. **明细表根本没有这一列**：`da_repo._PACKAGING_COST_ITEM_COLUMNS`（`:917`）里没有 `assumptions_json`，
   `wip_packaging_cost_item` 也没有（`da_schema.sql` 的建表块与 `da_db._ADDED_COLUMNS` 都没有）；
3. **整单那一份不是这本账**：顶层 `assumptions`（`compute_project()` `:2158` 起）只 `extend` 了运输那一条
   （`:2464`），部件的默认值一条都不进 —— 实测整单 `assumptions = []`、落库 `assumptions_json = '[]'`，
   与明细行里那 69 条默认值完全脱节；
4. **前端一处都没有**：`tech_app/frontend/requirement-confirm.js` 里 `assumptions` / `formula_source` /
   `has_gaps` 各 **0 处**（`grep -c` 实测）—— 成本面板能看见金额与缺口，看不见「这个金额里有几个是默认值
   顶上去的」。

## 2. 契约

### C1 落库（写侧）

- `_line_to_item()` 把 `line.get("assumptions")` **逐字**带进 item 的 `assumptions` 键（字符串列表；
  取不到给 `[]`，**不许** `None`）；`_item()` 的既有键表**一个键不动**；
- `wip_packaging_cost_item` 新增列 `assumptions_json`（`TEXT`）：`da_schema.sql` 的 `CREATE TABLE` 与
  `tech_app/backend/storage/da_db.py` 的 `_ADDED_COLUMNS` **两处同时**加（老库靠 `ALTER TABLE ... ADD COLUMN`
  补上；只加列，不改类型、不删列）；
- `da_repo.save_packaging_cost()` 把 `assumptions_json` 放进 `_PACKAGING_COST_ITEM_COLUMNS`，并用
  **与 estimate 行 `assumptions_json` / `gaps_json` 同一写法**写：`_box_match_json(item.get("assumptions") or [])`
  （取不到存 `"[]"`，不是 `NULL`）；
- 写入的**顺序就是** `compute_line()` 给的那个顺序（先 `=0903=` 后 `=默认=`，见 §D2），不许重排、不许去重、
  不许改文案。

### C2 读回（读侧逐字回放）

- `_rehydrate()`（`:2647`）把每一行的 `assumptions_json` 解回来挂到该行 `assumptions` 上：
  `items[i]["assumptions"]` **逐字等于**算完那一趟 `document["items"][i]["assumptions"]`；
- 读侧**不许现算**：不许调 `compute_line()` / `compute_content()` / `_merge_variables()` 去重建这一份，
  也不许按当轮的费率或规则快照重推（与 `source_versions` / `content_binding` 同一条纪律）；
- 老明细行（列不存在 / `NULL` / 解不出 / 解出来不是列表）→ `assumptions = []`，**键仍在**，不许抛错。

### C3 整单账（顶层计数）

- `load_cost()` 顶层新增两个键，**只从回放后的明细行**算（不现算、不许 null、不许抛错）：
  - `assumptions_total`：全部明细行 `assumptions` 条数之和；
  - `assumptions_0903_total`：其中含 `=0903=` 的条数（"这一单有几个数是 0903 的常量"）；
- `built = false`（还没算过）时两个键也在，给 `0`；
- 既有顶层 `assumptions` 的语义**一字不动**（照旧 `_loads(row.get("assumptions_json"), [])` 逐字回放
  estimate 行那一份）—— 本批**不**把明细行的默认值并进顶层 `assumptions`（那是另一个口径，另批再议）。

### C4 前端（成本面板）

`tech_app/frontend/requirement-confirm.js` 新增两个纯函数（顶层具名函数，`node -e` 可单独抽出来跑）：

- `pcAssumptions(cost, items)`：返回 `{ total, source0903, rows, summary }`
  - `total` / `source0903` **逐字取后端**（`assumptions_total` / `assumptions_0903_total`；非有限数给 `null`），
    前端**不许**自己按 `=默认=` / `=0903=` 数一遍，也不许把取不到的计数当 0 假装正常；
  - `rows` 按明细行 `seq` 升序展开，元素是 `{ part, text }`：`part = item.part_name || item.part_code || "项目级"`，
    `text` 是假设原文（逐字，不改写、不截断、不加序号）；
  - `summary` 三句**逐字**（红测按这三句断言）：
    1. 两个计数都取得到且 `total > 0` → `这一单有 {total} 条默认值，其中 {source0903} 条来自 0903 常量。`
    2. 两个计数都取得到且 `total === 0` → `这一单没有用默认值顶上去的参数。`
    3. 计数取不到（`null`）→ `后端没给这本账（assumptions_total / assumptions_0903_total）：这一单用了几个默认值是未知，不是 0。`
- `pcAssumptionsBlock(cost, items)`：渲染一块 `data-pc-assumptions="<total>"` 的 `.pc-hint`：
  摘要句 + 逐条 `<div class="pc-hint" data-pc-assumption="<i>">部件 · 假设原文</div>`；
  一条都没有时**不画空表**；
- `pcPanel()`（`:1482`）模板里在 `${pcContentBindingBlock(cost)}` **之后**加一行
  `${pcAssumptionsBlock(cost, items)}`（既有各块一个字不动、顺序不动）。

### C5 冻结面（本批不许动的）

- 公式表达式 / 费率 / `minimum_charge` / `rounding` / `defaults` / `_IMPLICIT_DEFAULTS` 一个数不改；
- `assumptions` 的文案格式 `<name>=0903=<value>` / `<name>=默认=<value>` 逐字不变（`compute_line()` 那一层
  一个字不改）；
- 明细行既有键（`_item()` 的键表）与 `_PACKAGING_COST_COLUMNS`（estimate 行）**一个不少、一个不多**；
- `inputs_json` / `expression` / `formula_source` / `rule_snapshot_version` / `amount` / `amount_with_loss`
  逐字不变。

## 3. 实测数字（2026-09-22，真引擎真库，`CostCase` 夹具）

| 量 | 值 |
| --- | --- |
| 明细行数 | 34 |
| 带非空 `assumptions` 的行数 | 13（11 条包材行 + 2 条工装行） |
| `assumptions` 条数合计 | 69 |
| 其中 `=0903=` | 1（`bag_unit_price=0903=0.185`） |
| 落库行含 `assumptions` 键 | 0 |
| `compute_line("die_cutting", {"imposition_count": None})["assumptions"]` | 10 条（4 条 `=0903=` + 6 条 `=默认=`，§D2 逐字） |
| 前端 `requirement-confirm.js` 里 `assumptions` | 0 处 |

## 4. 允许修改范围

1. `tech_app/backend/services/packaging_cost.py`（`_line_to_item()` / `_rehydrate()` / `load_cost()` 的两个计数 /
   两个返回体的键）；
2. `tech_app/backend/storage/da_repo.py`（`_PACKAGING_COST_ITEM_COLUMNS` + `save_packaging_cost()` 一处显式键）；
3. `tech_app/backend/storage/da_db.py`（`_ADDED_COLUMNS` 一行）与 `tech_app/backend/storage/da_schema.sql`
   （明细表建表块一行）；
4. `tech_app/frontend/requirement-confirm.js`（两个新纯函数 + `pcPanel()` 模板一行）。

**禁止**：改 `compute_line()` / `_merge_variables()` 的输出文案与顺序；改任何公式、费率、最低收费与损耗口径；
改 `tests/` 下任何文件（含本批红测），也不许改期望值让它变绿；连 PG / 34、发 HTTP、写生产数据。

## 5. 验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_assumption_disclosure_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red          # 81 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_content_binding_replay_red  # 15 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_input_version_pinning_red   # 不回退
node --check tech_app/frontend/requirement-confirm.js
```

## 6. 边界（本批不做，另批再议）

- **不**把明细行的默认值并进顶层 `assumptions`（顶层仍是 estimate 行那一份，见 §C3）；
- **不**改前端成本明细表（`pcItemRows()`）的行结构，也不在明细表里加一列假设 —— 这本账单独成块；
- **不**给缺口打「哪些是被默认值顶上去的」这类新标签（`gaps.defaulted` 今天不存在，属另一批）；
- 材料价格的单位换算与权威化（`packaging-material-price-unit-truth.md` §6 边界 2/3）不在本批。

## 7. 落地状态（2026-09-22，Codex 实现）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 带出 | `packaging_cost._line_to_item()` | 末尾原样带上 `line["assumptions"]`（`[str(x) for x in rows] if isinstance(rows, list) else []`）：不重排、不去重、不改文案，取不到给 `[]` |
| §C1 迁移 | `tech_app/backend/storage/da_db.py` 的 `_ADDED_COLUMNS` 末尾 | 加 `("wip_packaging_cost_item", "assumptions_json", "TEXT")`（老库 `ALTER TABLE ADD COLUMN`） |
| §C1 建表 | `tech_app/backend/storage/da_schema.sql` 的 `wip_packaging_cost_item` | 同一列写进 `CREATE TABLE IF NOT EXISTS`（在 `inputs_json` 与 `source_ref` 之间） |
| §C1 落库 | `da_repo._PACKAGING_COST_ITEM_COLUMNS` + `save_packaging_cost()` | 列清单加 `assumptions_json`，显式键 `_box_match_json(item.get("assumptions") or [])`（与 estimate 行 `assumptions_json` 同一写法；取不到存 `"[]"`） |
| §C2 读回 | `packaging_cost._rehydrate()` 开头 + `_loads()` | 每行 `item["assumptions"] = _loads(item.get("assumptions_json"), [])`（不是列表 → `[]`）；读侧不调 `compute_line()` / `_merge_variables()` |
| §C3 计数 | 新增 `packaging_cost._assumption_counts(items)`；`compute_project()` 返回体与 `_rehydrate()` 各挂一次；`load_cost()` 的 `built=false` 分支给 0 | `assumptions_total` / `assumptions_0903_total` 只数已经算好 / 回放好的明细行；顶层 `assumptions` 语义一字不动 |
| §C4 面板 | `requirement-confirm.js` 新增 `pcAssumptions()` / `pcAssumptionsBlock()`，`pcPanel()` 模板在 `${pcContentBindingBlock(cost)}` 之后挂 `${pcAssumptionsBlock(cost, items)}` | 计数逐字取后端（取不到给 `null` + 「未知」句）；条目按 `seq` 升序、部件空 → 「项目级」；空账一句中文、不画空表 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_assumption_disclosure_red -v
Ran 18 tests ... OK        （红基：Ran 18 ... FAILED (failures=15)，3 条绿为 B5 / D2 / D3 冻结守卫）

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_engine_red tests.test_packaging_cost_content_binding_replay_red \
  tests.test_packaging_cost_input_version_pinning_red tests.test_packaging_cost_route_version_read_failure_red \
  tests.test_packaging_cost_red_closure_red tests.test_packaging_cost_minimum_charge_red \
  tests.test_packaging_cost_rule_snapshot_red tests.test_packaging_cost_rule_routing_red \
  tests.test_packaging_cost_column_evidence_red tests.test_packaging_quote_close_loop_red \
  tests.test_packaging_cost_readiness_panel_red tests.test_packaging_cost_content_binding_panel_red
Ran 416 tests ... OK (skipped=1)

node --check tech_app/frontend/requirement-confirm.js   # OK

packaging 全域：discover -s tests -p 'test_packaging_*.py' → Ran 2402 ... FAILED (failures=5, skipped=8)
  （仍是那 5 条既有挂账：part_role_mapping A2 / bom_part_size_provenance B3 /
    parse_to_downstream_seams B4 / quote_send_recovery C1 / route_bom_version_pinning F2，本批未引入新红）
```

真引擎真库往返（`tests/test_packaging_cost_engine_red.py` 的夹具，同一条成本单，§3 那张表的修后列）：

| | 算完 | 读回（修后） |
| --- | --- | --- |
| 明细行带非空 `assumptions` 的行数 | 13 | **13** |
| `assumptions` 条数合计 | 69 | **69** |
| 其中 `=0903=` | 1 | **1** |
| `wip_packaging_cost_item.assumptions_json` | — | **34 行都有这一列，逐字等于算完那一份** |
| `load_cost()["items"][i]["assumptions"]` | — | **逐字回放（把 `compute_line` / `_merge_variables` 换成抛错的桩，读回一字不差）** |

边界（与 §6 一致，实现如约未越）：顶层 `assumptions` 仍是 estimate 行那一份（没把明细并进去）；
成本明细表（`pcItemRows()`）行结构未动；没给缺口加新标签；公式、费率、最低收费、损耗口径一个数没改。
