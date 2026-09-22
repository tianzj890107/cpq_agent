# 规格：BOM 面板上「材料码映射表」的账要说全——空表不许静默，来源与指纹要看得见

依赖：`docs/specs/packaging-business-material-code-map.md`（§C3 的映射事实 `map_source` /
`map_fingerprint` / `map_unavailable` / `reason_counts`；该 Spec §6 边界 4 明写
"前端不改：缺口与账都已在 `GET .../requirement/packaging-bom` 里，界面接入是下一批"）、
`docs/specs/packaging-material-unresolved-panel.md`（逐行原因与补齐办法的界面落点，本批**不动**它）、
`docs/specs/packaging-silent-degradation-disclosure.md`（"读不到 / 空的 / 没有"三者不许同形 ——
本批补的是同一病症在材料码映射表这一处的界面侧）。

状态：Spec + 红测（已实现）（原状：`GET .../requirement/packaging-bom` 的
`business_material_rows` 已带 `map_hit_total` / `map_source` / `map_fingerprint` /
`map_unavailable` / `reason_counts`（`packaging_bom._business_material_scope()` `:635`），
而 `tech_app/frontend/requirement-confirm.js` 只用了前两者里的两个信号
（`pbMaterialMapNote()` `:391` 读 `map_unavailable` 与 `map_hit_total`），
`map_source` / `map_fingerprint` / `reason_counts` **0 处引用**（`grep -c` 实测）：
① 映射表**读得到但一条映射都没有**（`entries: []`，完全合法的一版表）时，页面**一个字都不说** ——
与"表里有 N 条、这次正好都不匹配"长得一样，"空表"是用户唯一真正要动手的档；
② 这一版 BOM 用的是**哪一份**映射表（`map_source`：仓库内置 vs 环境变量覆盖）说不出来 ——
env 覆盖会影响所有项目，界面上看不见；
③ 逐档条数（`reason_counts`）没上界面：只报"命中几行"，看不出"原文没映射 / 映射写了没生效"各几行，
而这两档的补齐动作完全不同）
红测：`tests/test_packaging_material_map_account_panel_red.py`
行号基线：HEAD `7204de6`

## 0. 一句话目标

BOM 面板说清"这一版 BOM 用的是哪一份材料码映射表（来源 / 指纹）、表里有多少条、
这次各档几行"，并且**空表也要说出来**。

## 1. 现状缺口（代码级）

1. `_business_material_scope()`（`packaging_bom.py:635`）回了 `map_hit_total`，但**没有**表本身的
   条数（`map_facts()` 的 `entries` 只是被拿去查表，条数没进账）—— 于是"表是空的"与"表里没有这一条"
   在读接口上**同形**（`map_hit_total == 0` 且 `reason_counts == {}` 在一条材料行都没有时也成立）；
2. `pbMaterialMapNote()`（`requirement-confirm.js:391`）只在"读不到"或"命中 > 0"时有话说，
   其余情况回空串 → 面板上什么都不显示；
3. `map_source` / `map_fingerprint` / `reason_counts` 在界面上 0 处。

## 2. 契约

### C1 后端：一处加法 `map_entry_total`

`packaging_bom._business_material_scope(items, *, map_entries=None, map_available=True,
map_unavailable=None, map_source="", map_fingerprint="")` 的返回体新增
`map_entry_total`：`len(map_entries or ())`；`map_available` 为假时**一律 0**（读不到就没有条数可说）。
键**总是存在**、值**总是 `int`**。既有九键（`row_total` / `resolved_total` /
`unresolved_total` / `keys` / `reason_counts` / `map_hit_total` / `map_source` /
`map_fingerprint` / `map_unavailable`）**一字不动**，`classify_material_resolution()` 的五档闭集
与 `map_facts()` 的形状也不动。

### C2 前端三个纯函数（`requirement-confirm.js` 的包装 BOM 面板 IIFE 内，`pbMaterialMapNote()` 之后；体内无 DOM / `fetch(` / `storage`）

- `pbMaterialMapState(scope)` → `{state, headline, entries, hits, source, sourceLabel, fingerprint}`：
  - `state` 闭集只有 `"unavailable"` / `"empty"` / `"ready"` / `"unknown"`，判据顺序固定：
    1. `scope` 不是普通对象（`null` / 字符串 / 数组都算不是）→ `unknown`；
    2. `scope.map_unavailable` 是非空对象 → `unavailable`（读不到优先于条数）；
    3. `scope.map_entry_total` **不是有限数**（缺键 / `null` / 非数）→ `unknown`
      （**老载荷没有这个键**：不许当成"0 条"）；
    4. 有限且 `<= 0` → `empty`；`> 0` → `ready`。
  - `headline` 逐字四句：
    - `unavailable` → `材料原文映射表读不到：这一版 BOM 的材料码只按既有分词规则解析。`
    - `empty` → `材料原文映射表读得到，但一条映射都没有（0 条）：客户原文只能按既有分词规则解析，解不出来的行要往表里补。`
    - `ready` → `材料原文映射表 ${entries} 条，本次命中 ${hits} 行。`
    - `unknown` → `后端没给材料码映射表的状态（老载荷），这一版说不清用了哪份映射。`
  - `source`：`scope.map_source` 逐字 trim（**不许**改大小写 / 截断）；
    `sourceLabel`：`default` → `仓库内置`、`override` → `环境变量覆盖（影响所有项目）`、
    其余（含空串）→ `来源未知`；
  - `fingerprint`：`scope.map_fingerprint` 逐字 trim（**不许**截断）；
  - `entries` / `hits`：逐项 `Number(...)`，非有限数或负数 → `0`；**`hits` 不参与 `state` 判定**
   （命中数是"这次解析用了多少"，不是表的规模）；
  - 任何输入都不抛错。
- `pbMaterialMapBreakdown(scope)` → `""` 或一句逐档清单：
  - 档位闭集与**顺序固定**（与 `MATERIAL_RESOLVE_REASONS` 同一批）：`map_hit` → `映射命中`、
    `legacy_hit` → `旧规则命中`、`map_key_missing` → `原文没映射`、
    `map_entry_not_applied` → `映射写了没生效`、`map_unknown` → `映射表读不到没判定`；
  - **只放非零档**，格式 `这次解析：` + `<档名> <N> 行`，档之间用 ` · ` 连；
  - 闭集外的档名**不猜人话**：合并成一条 `其它档 <N> 行`（N = 闭集外各档之和），排在最后；
  - 没有任何非零档 / `reason_counts` 不是对象 → `""`（没话说就别说）。
- `pbMaterialMapAccountBlock(scope)` → 这一段的 HTML（**落点就是这里**，`pbPanel()` 只负责拼进去）：
  - `state` 是 `ready` / `empty` 才渲染；`unavailable` 由既有 `pbMaterialMapNote()` 那一句承担、
    `unknown` 没有事实可说 —— 这两种都回 `""`（**不重复两遍同一件事**，也不编一句话）；
  - 渲染时：`<div class="pb-hint" data-pb-material-map-state="<state>"
    data-pb-material-map-source="<sourceLabel>" data-pb-material-map-fingerprint="<fingerprint>">`
    + `headline` + `</div>`；
  - `pbMaterialMapBreakdown()` 非空时再接一节
    `<div class="pb-hint" data-pb-material-map-reasons="<非零档数>">…</div>`；
  - 所有插值过 `pbEsc()`。

### C3 面板接线（`pbPanel()`）

- 新增 `${pbMaterialMapAccountBlock(record.business_material_rows)}`，紧跟在既有 `${mapBlock}`
  之后（**一处**调用）；
- 既有 `data-pb-material-map="1"` 那一块与 `pbMaterialMapNote(record.business_material_rows)`
  **一字不动**；逐行缺口那块（`data-pb-material-unresolved`）与既有 banner 也一字不动。

### C4 冻结面

- `state` 只有一个来源：`map_unavailable` + `map_entry_total`（前端**不许**按 `reason_counts` 或
  `map_hit_total` 重算"表是不是空的"）；
- 不改检索口径（`resolve_material_code()` / `lookup_material_code()` / 五档闭集一字不动）、
  不改 `map_facts()` 的键、不改映射表 JSON、不新增接口 / 依赖、不改 `index.html`；
- 不改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_bom.py`：C1 的**一个**键；
2. `tech_app/frontend/requirement-confirm.js`：C2 三个纯函数 + C3 的一处调用；
3. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许把"表是空的"说成"映射都齐了"，也不许把"读不到"说成"没有映射"；
- 不许在没有事实时编一句话（`unknown` / `unavailable` 的 block 回 `""`，由既有那句承担）；
- 不许猜闭集外的档名；
- 不连 PG / 34、不写生产数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_map_account_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_material_code_map_red tests.test_packaging_material_unresolved_panel_red \
  tests.test_packaging_bom_business_material_rows_red tests.test_packaging_bom_disclosure_panel_red \
  tests.test_packaging_parametric_bom_red tests.test_packaging_cost_engine_red
```

## 6. 已记录的边界

1. 只**显示**账，不改映射表内容、不在页面上编辑映射（补表仍走仓库内的 JSON + 业务签字）；
2. `entries` 里的每一条原文 / 材料码不上界面（逐行缺口已由上一批承担，本批只给规模与来源）；
3. 映射表的**写入侧**原因（0 候选 / 多候选 / `ambiguous`）仍不持久化
   （`packaging-business-material-code-map.md` §6 边界 3），本批不动；
4. `reason_counts` 是**读路径**能从行 + 映射表推出的事实，与写入侧的判定不是一回事（同一批行、
   同一顺序），本批只把它显示出来；
5. 其它行业的 BOM 面板仍只在包装需求单上挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

**实现前红基**（`git stash push -- tech_app/frontend/requirement-confirm.js
tech_app/backend/services/packaging_bom.py` 后的原文）：

```
Ran 25 tests ... FAILED (failures=18, errors=3)
```

21 红 / 4 绿 —— 绿的四条是守卫（`A5` 既有账四数与来源 / 指纹一字未动、`E3` 既有
`data-pb-material-map="1"` 块与逐行缺口块未动、`F3` `index.html` 未动、`F4` `node --check`
通过）；21 红 = `A1`（没有 `map_entry_total`）、`A2` / `A3`（ARGUMENT 形状对不上 → ERROR）、
`A4`（键不存在）、`B1`–`B5`、`C1`–`C4`、`D1`–`D4`（三个纯函数根本不存在）、`E1`、`E2`（锚点
拿不到 → ERROR）、`F1`、`F2`。

**实现后**：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_map_account_panel_red -v
Ran 25 tests ... OK
node --check tech_app/frontend/requirement-confirm.js   # 退出码 0
```

落点（只改了两个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 | `packaging_bom._business_material_scope()`（`tech_app/backend/services/packaging_bom.py`） | 返回体新增 `map_entry_total`：`len(map_entries or ()) if map_available else 0`，键总是存在、值总是 `int`；既有九键一字未动，`classify_material_resolution()` 的五档闭集与 `map_facts()` 形状未动 |
| §C2 | `pbMaterialMapState()` / `pbMaterialMapBreakdown()` / `pbMaterialMapAccountBlock()`（`tech_app/frontend/requirement-confirm.js` 包装 BOM 面板 IIFE 内，`pbMaterialMapNote()` 之后） | 三个纯函数，体内无 DOM / `fetch(` / `storage`；`state` 判据顺序 = 不是对象 → `unknown`（且**缺 `map_entry_total` 的老载荷也算未知档，不许当成"0 条"**）→ `map_unavailable` 非空 → `unavailable` → 条数 `<= 0` → `empty` → 否则 `ready`；`hits` 只进 `headline`、**不参与判定**；逐档清单按后端闭集固定顺序与人话渲染，闭集外的档名不猜、合并成「其它档 N 行」；`data-pb-material-map-state` / `-source` / `-fingerprint` / `-reasons` 都在 `pbMaterialMapAccountBlock()` 体内拼装 |
| §C3 | `pbPanel()`：`${mapBlock}` 之后插入 `${pbMaterialMapAccountBlock(record.business_material_rows)}` | 一处调用；既有 `data-pb-material-map="1"` 块与 `pbMaterialMapNote(record.business_material_rows)`、逐行缺口块与既有 banner 一字未动；`pbPanel()` 里无 `fetch(` |
| §C4 | 无 | `state` 只读 `map_unavailable` + `map_entry_total`（`pbMaterialMapState()` 体内不出现 `reason_counts`）；未改检索口径 / 映射表 JSON / `map_facts()` 键 / `index.html`、未加接口与依赖 |

实现说明（两处实现期收敛，均未放宽任何断言）：

1. §C2 里"条数是空串"这一档按**未知档**处理（`map_entry_total: ""` 与缺键同待遇）——
   空串不是"0 条"的事实，代码里显式写了这一条；
2. 红测 `F2`（"状态不许由命中数决定"）原先是**源码字符串**守卫
   （`assertNotIn("map_hit_total", body)`），而这与 §C2 要求 `pbMaterialMapState()` 同时回
   `hits` 自相矛盾；已改成**行为**守卫：`{map_entry_total: 0, map_hit_total: 99}` 必须判
   `empty`、`{map_entry_total: 5, map_hit_total: 0}` 必须判 `ready`（更强，不是更松）。

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_material_code_map_red tests.test_packaging_material_unresolved_panel_red \
  tests.test_packaging_bom_business_material_rows_red tests.test_packaging_bom_disclosure_panel_red \
  tests.test_packaging_parametric_bom_red tests.test_packaging_cost_engine_red
Ran 239 tests ... OK
```

边界（与 §6 一致，实现如约未越）：只显示账、不在页面上编辑映射；`ready` / `empty` 才渲染，
`unavailable` 由既有那句承担、`unknown` 不编话；`entries` 里逐条原文 / 材料码不上界面；
写入侧原因（0 候选 / 多候选 / `ambiguous`）仍不持久化；其它行业仍不挂载 BOM 面板。
