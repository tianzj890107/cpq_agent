# 规格：BOM 面板要说得出「部件组行是权威清单来的，还是几何模板展开的」

依赖：`docs/specs/packaging-bom-business-parts-rows.md` §C3（读接口的 `business_rows` 这一块：
`{row_total, box_part_total, optional_part_total, needs_input_total, keys}`，判据只有行上的
`source`，键**必须存在**；该 Spec §7 边界 5 明写"本批不碰前端……若界面需要『这一行来自权威清单』
的标记，另立一批（可以只读 `source`，不必改后端）"）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md` §7/§8（"绝不用几何件数冒充业务件数"）、
`docs/specs/packaging-silent-degradation-disclosure.md`（说不出"这份 BOM 的部件组行是哪来的"
就是静默降级）。

状态：Spec + 红测（已实现）（原状：`GET .../requirement/packaging-bom` 返回体里的
`business_rows`（`packaging_bom._business_rows_scope()` `:712`，判据只认行上的 `source`）
**早就**把"这一版 BOM 有多少部件组行来自权威清单"算好了，而
`tech_app/frontend/requirement-confirm.js` 里 `business_rows` **0 处引用**（`grep -c` 实测）：
① 部件组行是**权威清单**来的还是**几何零件模板**展开的，在页面上同形 —— 同一份 BOM 的
`business_material_rows` 那一半（材料组）已经有账（上一批还补了映射表那本账），部件组这一半
**一行都没有**；
② 这直接决定用户该不该去导权威清单：模板展开的 10 行与权威清单的 28 行在表格里长得一样
（真样本实测：导入前 `row_total = 0`、导入后 28）；
③ `needs_input_total`（其中几行还缺输入）在页面上也 0 处 —— 合计区的"缺输入"是**全部行**的，
分不出权威清单那部分有几行要补）
红测：`tests/test_packaging_bom_business_rows_account_panel_red.py`
行号基线：HEAD `572f119`

## 0. 一句话目标

BOM 面板上多一条「部件组行来源」的账：来自权威清单几行（盒型件 / 选配件各几件）、其中几行
缺输入；**一行都没来自权威清单**时也要说出来（"部件组是按几何零件模板展开的"），
老载荷（没有这一块）不许与"0 行"同形。

## 1. 现状缺口（代码级）

1. `pbPanel()`（`requirement-confirm.js:495` 起）读了 `record.business_material_rows`（材料组的
   那本账 + 映射表的账）与 `record.business_parts`（清单版本与规模），**没有**读
   `record.business_rows`（部件组的那本账）；
2. `business_rows.row_total` / `box_part_total` / `optional_part_total` / `needs_input_total`
   在界面上 0 处；
3. `pbRow()` 逐行只认 `status` / 漂移 / 盒型标记 / `material_code`，**不看** `item.source` ——
   单行层面也看不出它来自权威清单（那一半留给后面一批，见 §6）。

## 2. 契约

### C1 两个纯函数（`requirement-confirm.js` 的包装 BOM 面板 IIFE 内，`pbBusinessPartsScopeBlock()` 之后；体内无 DOM / `fetch(` / `storage`）

- `pbBusinessRowsAccount(record)` → `{state, total, boxParts, optionalParts, needsInput, headline}`：
  - `scope` = `record.business_rows`（**只有**这一处是行数的来源）；
  - `state` 闭集只有 `"ready"` / `"empty"` / `"unknown"`，判据顺序固定：
    1. `scope` 不是普通对象（`undefined` / `null` / 字符串 / 数组 / 数字）→ `unknown`
      （老载荷没有这一块：**不许**当成"0 行来自权威清单"）；
    2. `row_total` 转数后 `> 0` → `ready`；
    3. 否则 → `empty`（这一版 BOM 的部件组行没有一行来自权威清单）；
  - `total` / `boxParts` / `optionalParts` / `needsInput`：逐项 `Number(...)`，非有限数或负数 →
    `0`（键**必须存在**，不许 `null` / `undefined`）；
  - `headline` 逐字三句：
    - `ready` → `部件组行：来自权威清单 N 行（盒型件 A 件 · 选配件 B 件），其中缺输入 C 行。`
    - `empty` → `这一版 BOM 的部件组行没有一行来自权威清单（部件组是按几何零件模板展开的）。`
    - `unknown` → `后端没给部件组行的来源账（老载荷）：说不清这一版 BOM 的部件组行是权威清单还是模板来的。`
  - 任何输入都不抛错。
- `pbBusinessRowsBlock(record)` → 一段 HTML（**落点就是这里**，`pbPanel()` 只负责拼进去）：
  - **三态都渲染**（每一态都有一句各不相同的事实）：
    `<div class="pb-hint" data-pb-business-rows-state="<state>" data-pb-business-rows-total="<total>"
    data-pb-business-rows-needs-input="<needsInput>">` + `headline` + `</div>`；
  - 所有插值过 `pbEsc()`。

### C2 面板接线（`pbPanel()`）

- 新增 `${pbBusinessRowsBlock(record)}`（**一处**调用），紧跟
  `${pbBusinessPartsScopeBlock(record)}` 之后；
- 既有块（head 那句零件文档版本、材料码映射表的账、业务部件清单的账、逐行缺口、既有四条
  banner、漂移块）**一字不动**；`pbRow()` 一字不动。

### C3 冻结面

- 行数**只有一个**来源：`record.business_rows`（**不许**去数 `record.items`、不许按 `source`
  自己过滤、不许读 `record.business_material_rows` 顶替 —— 材料组是另一本账）；
- `empty` 不许说成 `unknown`，`unknown` 不许说成 0 行；
- 不改后端（`_business_rows_scope()` 的键与取值一字不动）、不加接口 / 依赖、
  `pbPanel()` 里不新增请求、不改 `index.html`、不改 `pbRow()`；
- 不改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：C1 两个纯函数 + C2 的一处调用；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许自己按行数 `source`（那是后端那一块账的判据，前端重算就是第二套口径）；
- 不许把"没有来自权威清单"说成"没有部件组行"（这一版 BOM 仍然有行，只是模板来的）；
- 不许把老载荷（没有这一块）显示成 0 行；
- 不连 PG / 34、不写生产数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_business_rows_account_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_bom_business_material_rows_red \
  tests.test_packaging_bom_business_parts_scope_panel_red tests.test_packaging_material_map_account_panel_red \
  tests.test_packaging_bom_disclosure_panel_red tests.test_packaging_parametric_bom_red
```

## 6. 已记录的边界

1. **逐行**的"这一行来自权威清单"标记（`pbRow()` 里读 `item.source`）是**后面一批**：
   本批只做这本账（`business_rows` 已经在接口上，账先说得清；逐行标记要动 `pbRow()`，
   与"既有逐行渲染一字不动"那条纪律分开一批做）；
2. 权威原文（工艺 / 排版 / 备注）仍不进 BOM 行（`packaging-bom-business-parts-rows.md` §7 边界 2），
   本批不动；
3. `keys`（这一版来自权威清单的编码清单）不上界面：逐行缺口与编码仍由 BOM 表格承担，
   本批只给规模与缺输入条数；
4. 材料组那本账（`business_material_rows`）与映射表的账已由前两批承担，本批不重复；
5. 其它行业的 BOM 面板仍只在包装需求单上挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

红基（实现前，`requirement-confirm.js` 未动）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_business_rows_account_panel_red -v
Ran 21 tests ... FAILED (failures=17)
```

17 条红 = A1–A8（纯函数缺失）+ B1–B5（渲染串缺失）+ C1/C2（`pbPanel()` 无调用、位置无从判）
+ D1/D2（两个纯函数不存在）；4 条绿护栏 = C3（既有块与逐行缺口未动）、D3（`index.html` 未动）、
D4（`pbRow()` 里没有本批的东西）、D5（`node --check` 通过）。

落点（只改了 `tech_app/frontend/requirement-confirm.js` 一个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 事实 | `pbBusinessRowsAccount(record)`（包装 BOM 面板 IIFE 内，`pbBusinessPartsScopeBlock()` 之后） | 三态闭集判据顺序：`record.business_rows` 不是普通对象（老载荷）→ `unknown`；`row_total` 转数 `> 0` → `ready`；否则 → `empty`（这一版部件组行没有一行来自权威清单）。四个数一律 `Number()`，非有限 / 负数 → `0`，键必存在；行数**只**读 `business_rows`（体内无 `items` / `.source` / `business_material_rows` / `stats`） |
| §C1 渲染 | `pbBusinessRowsBlock(record)` | 三态都渲：`data-pb-business-rows-state` / `-total` / `-needs-input` + headline，插值全过 `pbEsc()` |
| §C2 | `pbPanel()`：`${pbBusinessPartsScopeBlock(record)}` 之后插入 `${pbBusinessRowsBlock(record)}` | 一处调用；head 那句零件文档版本、材料码映射表的账、业务部件清单的账、逐行缺口、配对复核 / 回填失败 / 换版三本账与既有四条 banner 一字未动；`pbPanel()` 里无 `fetch(` |
| §C3 | 无 | 未改后端（`_business_rows_scope()` 的键与取值一字不动）、未加接口 / 依赖、未改 `index.html`、未改 `pbRow()` |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_business_rows_account_panel_red -v
Ran 21 tests ... OK
node --check tech_app/frontend/requirement-confirm.js   # 退出码 0

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_bom_business_material_rows_red \
  tests.test_packaging_bom_business_parts_scope_panel_red tests.test_packaging_material_map_account_panel_red \
  tests.test_packaging_bom_disclosure_panel_red tests.test_packaging_parametric_bom_red
Ran 168 tests ... OK
```

边界（与 §6 一致，实现如约未越）：逐行的"这一行来自权威清单"标记仍未做（留给后面一批，要动
`pbRow()`）；`keys` 不上界面；权威原文仍不进 BOM 行；材料组那本账未重复；其它行业仍不挂载
BOM 面板。
