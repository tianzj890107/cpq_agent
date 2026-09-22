# 规格：成本单读回来还要说得出「这一单绑了哪几项包材」（把绑定那一份落库）

依赖：`docs/specs/packaging-cost-content-binding-source-disclosure.md` §2.2（写侧结果体的 `content_binding`
五个键；该 Spec §6 边界明写「让读侧的 `unbound_*` 也可回放需要另一批（给成本表加这一列），本批不越界」）、
`docs/specs/packaging-cost-content-binding-panel.md` §6 边界（同一句话：`unbound_*` 可回放仍需要另一批）、
`docs/specs/packaging-cost-input-version-pinning.md` §2.2（**同一条纪律的先例**：`source_versions` 读侧只读存的
那一份，"不许读时现取"）、`docs/specs/packaging-silent-degradation-disclosure.md`（"算时说得清、读回来就说不清"
就是静默降级）。

状态：Spec + 红测（已实现）（原状：`packaging_cost.compute_project()` 算完的结果体带
`content_binding`（`{source, bound_total, unbound_total, bound_codes, unbound_codes}`），**但这一份从来没落库** ——
`_rehydrate()`（`:2647`）读回时改成现算：`bound_content_codes_detail({})`（喂一个空 payload）+ 从
`row.get("gaps_unbound_to_order")` 取缺口，而后者**不是** `wip_packaging_cost_estimate` 的列（`SELECT *` 读不回）。
实测（真引擎真库，`tests/test_packaging_cost_engine_red.py` 的夹具，见 §3）：
算完 `{source: none, bound_total: 0, unbound_total: 7, unbound_codes: [7 个包材项]}` →
读回 `{source: none, bound_total: 0, unbound_total: 0, bound_codes: [], unbound_codes: []}`，
就绪门 `readiness.unbound_total` 7 → 0，reasons 里那句「包材绑定数据源缺失：7 条包材缺口只披露不阻断」**也一起消失**）
红测：`tests/test_packaging_cost_content_binding_replay_red.py`
行号基线：HEAD `5a8bb87`

## 0. 一句话目标

成本单落库时把**算出来的**那一份绑定账原样存下来，读回来时原样回放：同一张成本单，重新打开后
「这一单绑了哪几项包材、被降级披露的是哪几项」与刚算完时**逐字相同**；老成本单没记过这一份时，
老实地报「没记下来」，而不是报「一项都没绑上」。

## 1. 现状缺口（实测，不是推断）

`tech_app/backend/services/packaging_cost.py`：

```python
# _rehydrate()（:2647）
"content_binding": _content_binding_of(
    bound_content_codes_detail({}).get("codes") or (),      # ← 读侧又去问了"当前的数据源"
    row.get("gaps_unbound_to_order") or []),                # ← 这一列不存在 → 永远 []
```

1. **写侧算过的那一份被丢掉**：`content_binding` 不在 `da_repo.save_packaging_cost()` 的
   `_PACKAGING_COST_COLUMNS` 里，也不在它 `row.update({...})` 的显式键里 → 根本没进库；
2. **读侧现算，且算的是"现在"**：`bound_content_codes_detail({})` 是**当前**数据源的回答；
   把这一句换成"回答 authoritative + 一个包材项"的桩，读一条算时是 `none` 的成本单会读到
   `source: authoritative` + `bound_codes: ['PKG-CT-DIVIDER']` —— 与
   `packaging-cost-input-version-pinning.md` §2.2 那条纪律（"读侧只读存的那一份，不许读时现取"）
   在绑定这一轴上完全相反；
3. **被降级披露的包材项在刷新后消失**：`unbound_total` 7 → 0、`unbound_codes` 7 项 → `[]`，
   于是面板上「被降级披露的包材项：…」那一行（`packaging-cost-content-binding-panel.md` §C2）
   重开成本单就没了；
4. **就绪门那句解释也没了**：`readiness.unbound_total` 7 → 0，`reasons` 里
   「包材绑定数据源缺失：7 条包材缺口只披露不阻断」不再出现 —— 判决旁边"为什么这些缺口没进阻断"
   这句话只在算完的那一眼看得见。

## 2. 契约

### C1 落库（写侧）

- `wip_packaging_cost_estimate` 新增列 `content_binding_json`（`TEXT`）：
  `da_schema.sql` 的 `CREATE TABLE` 与 `tech_app/backend/storage/da_db.py` 的 `_ADDED_COLUMNS`
  **两处同时**加（老库靠 `ALTER TABLE ... ADD COLUMN` 补上；不改类型、不删列）；
- `da_repo.save_packaging_cost()` 用**与 `gaps_json` / `assumptions_json` / `source_versions_json`
  同一写法**存 `estimate.get("content_binding")`：`_box_match_json(...)`；取不到存 `{}`（不是 `NULL`）。

### C2 读回（读侧逐字回放）

- `_rehydrate()` 的 `content_binding` **逐字等于**落库那一份的五个键
  （`source` / `bound_total` / `unbound_total` / `bound_codes` / `unbound_codes`）；
- 读侧**不许**再调 `bound_content_codes_detail()` / `bound_content_codes()`，也**不许**按当轮的数据源
  重算这一份（"读的就是算时那一份"，与 `source_versions` 同一条纪律）；
- 五个键**必须都在**，类型不变（`source` 字符串、两个计数非负整数、两个清单字符串列表）。

### C3 老成本单（没这一列 / 解不出 / 不是对象）

- 五个键仍在，但**来源报空**：`source = ""`；
- **不许**报 `"none"`（"没有权威数据源"是**算的那一刻**的结论，老成本单不知道这件事），
  也**不许**报 `"authoritative"`；两个计数给 `0`、两个清单给 `[]`；
- 前端**不用改**：`pcContentBinding()`（`requirement-confirm.js:1241`）已经把空来源渲染成
  「未知档」（"后端没给包材绑定来源（content_binding.source），「包材没有缺口」这句话不成立。"）。

### C4 就绪门

- `_with_readiness()` 的 `unbound_total`：**有** `gaps_unbound_to_order` 就用它的条数（既有口径一字不动）；
  **没有**（读侧回放的成本单）时退回 `content_binding.unbound_total`（回放的那一份），再没有给 `0`；
- 算完那一趟两个数逐字相等（写侧 `content_binding.unbound_total` 就是
  `len(gaps_unbound_to_order)`）→ 口径不变；
- `content_binding_source` 的取值口径不变（仍然逐字取 `content_binding.source`；老成本单自然是 `""`）。

### C5 冻结面

- 不改 `content_binding` 的键名、键序与闭集（`source ∈ {none, authoritative}` 的**写侧**口径不动）；
- 不改 `bound_content_codes_detail()` / `bound_content_codes()` / `_content_binding_of()` 的形状与取值；
- 不改前端（`pcContentBinding()` 已能认空来源）、不加接口 / 依赖、不改报告与就绪门的裁决口径；
- 不改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言。

## 3. 红基实测（复现命令与原始数字）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_content_binding_replay_red -v
```

真引擎真库（`tests/test_packaging_cost_engine_red.py` 的 `CostCase` 夹具），同一条成本单：

| | 算完（写侧） | 读回（现状） |
| --- | --- | --- |
| `content_binding.source` | `none` | `none` |
| `content_binding.bound_total` | `0` | `0` |
| `content_binding.unbound_total` | **7** | **0** |
| `content_binding.unbound_codes` | **7 项**（`PKG-CT-BAG` / `PKG-CT-BOARD` / `PKG-CT-CORNER-PAPER` / `PKG-CT-CORNER-TOP` / `PKG-CT-CRAFT-PAPER` / `PKG-CT-DIVIDER` / `PKG-CT-LABEL`） | **`[]`** |
| `readiness.unbound_total` | **7** | **0** |
| `readiness.reasons` | 含「包材绑定数据源缺失：7 条包材缺口只披露不阻断」 | **不含** |

## 4. 允许修改范围

1. `tech_app/backend/storage/da_db.py`：`_ADDED_COLUMNS` 加一项；
2. `tech_app/backend/storage/da_schema.sql`：`wip_packaging_cost_estimate` 加一列；
3. `tech_app/backend/storage/da_repo.py`：`save_packaging_cost()` 加一个显式键；
4. `tech_app/backend/services/packaging_cost.py`：`_rehydrate()` 的回放分支 + `_with_readiness()` 的退回口径；
5. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 5. 禁止事项

- 不许在读侧重算绑定来源（那是"读时现取"，与 `source_versions` 那条纪律相反）；
- 不许把"老成本单没记过"显示成 `none`（"没有权威数据源"）或 `0 项被降级披露`；
- 不许改 `content_binding` 的键 / 闭集 / 计数口径，不许改前端与就绪门的裁决；
- 不连 PG / 34、不写生产数据、不 push / MR / tag / Release / 部署。

## 6. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_content_binding_replay_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_content_binding_source_disclosure_red \
  tests.test_packaging_cost_content_binding_panel_red \
  tests.test_packaging_cost_input_version_pinning_red \
  tests.test_packaging_cost_gaps_scoped_to_order_contents_red \
  tests.test_packaging_cost_readiness_panel_red tests.test_packaging_cost_engine_red
```

## 7. 已记录的边界

1. `bound_codes`（这一单绑上的包材项）今天恒空集 —— 本批只保证它**读回来与算完一致**，
   不造权威数据源（那是 `packaging-cost-content-binding-source-disclosure.md` §2.1 的边界）；
2. 老成本单**不回填**（不猜、不按现数据源补），只在读接口上报空来源；
3. 成本明细行（`wip_packaging_cost_item`）不动；
4. 别的场景（`scenario_code`）按同一 (项目, 需求单, 场景) 各自的成本单各自回放。

## 8. 落地状态（2026-09-22，Codex 实现）

红基（实现前，四个后端文件未动）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_content_binding_replay_red -v
Ran 15 tests ... FAILED (failures=9)
```

9 条红 = A1–A3（真库往返：读回来与算完不一致 / 那一行没有这一列 / 就绪门那句不见了）
+ B1–B4（回放 / 老成本单 / 解不出 / 读侧仍在问当前数据源）+ C1（回放的条数没进就绪门）
+ D3（`_ADDED_COLUMNS` 与建表语句都没有这一列）；6 条绿护栏 = C2（既有条数口径）、
C3（老成本单不凭空说条数）、D1（键集合与类型）、D2（写侧闭集仍是 `none` / 空集）、
D4（前端未动）、D5（既有键逐字）。

落点（四个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 迁移 | `tech_app/backend/storage/da_db.py` 的 `_ADDED_COLUMNS` 末尾 | 加 `("wip_packaging_cost_estimate", "content_binding_json", "TEXT")`（老库 `ALTER TABLE ADD COLUMN`） |
| §C1 建表 | `tech_app/backend/storage/da_schema.sql` 的 `wip_packaging_cost_estimate` | 同一列写进 `CREATE TABLE IF NOT EXISTS`（新库直接有） |
| §C1 落库 | `da_repo.save_packaging_cost()` | 加显式键 `"content_binding_json": _box_match_json(estimate.get("content_binding") or {})`（与 `gaps_json` / `source_versions_json` 同一写法；取不到存 `{}`） |
| §C2/§C3 读回 | `packaging_cost._replayed_content_binding(row)`（新增，`_content_binding_of()` 之后）+ `_rehydrate()` 的 `content_binding` | 只 `_loads(row.get("content_binding_json"))`：有就用存的那一份（五个键逐字回放），没有 / 解不出 / 不是对象 → `source=""` + 计数 0 + 空清单，**不再**调 `bound_content_codes_detail()` |
| §C4 就绪门 | `packaging_cost.packaging_cost_readiness_gate()` | `unbound_total`：有 `gaps_unbound_to_order` 就用它的条数（既有口径，逐字不动）；没有才退回 `content_binding.unbound_total`，再没有给 0 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_content_binding_replay_red -v
Ran 15 tests ... OK

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_content_binding_source_disclosure_red \
  tests.test_packaging_cost_content_binding_panel_red tests.test_packaging_cost_input_version_pinning_red \
  tests.test_packaging_cost_gaps_scoped_to_order_contents_red tests.test_packaging_cost_readiness_panel_red \
  tests.test_packaging_cost_engine_red
Ran 151 tests ... OK

packaging 全域：discover -s tests -p 'test_packaging_*.py' → Ran 2384 ... FAILED (failures=5, skipped=8)
  （仍是那 5 条既有挂账，本批未引入新红）
```

真引擎真库往返（`tests/test_packaging_cost_engine_red.py` 的夹具，同一条成本单，§3 那张表的修后列）：

| | 算完 | 读回（修后） |
| --- | --- | --- |
| `content_binding.unbound_total` | 7 | **7** |
| `content_binding.unbound_codes` | 7 项 | **7 项（逐字同序）** |
| `readiness.unbound_total` | 7 | **7** |
| `readiness.reasons` | 含那句 | **含那句** |

另外两条实测：把 `bound_content_codes_detail` 换成回答 `authoritative` 的桩之后，读回仍是存的
`none` 且**该桩的调用次数为 0**；老库演练（手工建一张只有 `estimate_id` / `project_id` 两列的
`wip_packaging_cost_estimate` 并插入一行）→ `init_db()` 之后列补上了（`TEXT`）、**老数据行原样还在**。

边界（与 §7 一致，实现如约未越）：`bound_codes` 今天仍是空集（不造权威数据源）；老成本单不回填、
不猜；成本明细行不动；前端一个字没改（空来源仍走既有「未知档」那句）。
