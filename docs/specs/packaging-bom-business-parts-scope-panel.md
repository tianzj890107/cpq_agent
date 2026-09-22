# 规格：BOM 面板要说得出「这份 BOM 照哪一版业务部件清单配的」

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md` §7/§8（读接口 `business_parts_id`
与 `business_parts` 这一块：`{available, business_part_total, bound_total, unbound_total,
business_parts_hash, gap}`，键**必须存在**；"绝不用几何件数冒充业务件数"）、
`docs/specs/packaging-business-parts-version-pinning.md` §2.2（`business_parts_stale`；
**比较不了 ≠ 变了**）、`docs/specs/packaging-bom-parts-version-binding.md` §2.4（同一条纪律在
零件文档那条轴上的先例：面板 head 里已经写着 `零件文档版本：<前 12 位>`）、
`docs/specs/packaging-silent-degradation-disclosure.md`（说不出"这份 BOM 照哪一版业务部件算的"
就是静默降级）。

状态：Spec + 红测（已实现）（原状：`GET .../requirement/packaging-bom` 的返回体里，
`business_parts_id` 与 `business_parts`（版本 / 件数 / 已绑 / 未绑 / 读不到时的 `gap`）**早就**
逐字给出来了（`packaging_bom.load_bom()` `:1412-1420`，`_business_parts_scope()` `:1647`），
而 `tech_app/frontend/requirement-confirm.js` 里 `business_parts_id` / `business_parts_hash` /
`business_part_total` **全部 0 处引用**（`grep -c` 实测）：
① BOM 面板 head 只写了「零件文档版本：<前 12 位>」这一把尺子（`:599`）——**另一把尺子**
（业务部件清单是哪一版、共几件、几件绑了几何）在页面上 0 处，谁也说不出来这份 BOM 是照哪一版
业务部件清单配的；
② 清单**读不到**（`gap.code = business_parts_document_unavailable`）与"还没导入"（`business_parts_missing`，
含"已识别几何区域 n 个"）两种态在页面上同形 —— 都是"什么都没显示"；
③ 件数为 **0**（`available` 真而 `business_part_total = 0` 这种自相矛盾的载荷 / 清单里确实一件
都没有）同样什么都不说 —— 与"清单里有 28 件、这份 BOM 正好没配到"看不出差别；
"还没导入"（`business_parts_missing`）与"读不到"（`business_parts_document_unavailable`）
两种态今天在页面上也同形（都是什么都不显示））
红测：`tests/test_packaging_bom_business_parts_scope_panel_red.py`
行号基线：HEAD `02cce2c`

## 0. 一句话目标

BOM 面板上加一条「业务部件清单」的账：照的是哪一版（id / hash 前 12 位）、共几件、
几件绑了几何、几件没绑；读不到 / 还没导入 / 一件都没有，三种态各说各的，**绝不用几何件数冒充**。

## 1. 现状缺口（代码级）

1. `pbPanel()`（`requirement-confirm.js:495` 起）读了 `record.source_versions.parts_hash`
   （零件文档那一把尺子）与 `record.business_parts_stale`（逐行漂移），**没有**读
   `record.business_parts` 这一块；
2. `record.business_parts_id` / `business_parts.business_parts_hash` /
   `business_part_total` / `bound_total` / `unbound_total` / `gap` 在界面上 0 处；
3. `_business_parts_scope()` 给 `blank` 与"有清单但 0 件"两种不同态（前者 `gap` 非空、
   后者 `gap` 为 `business_parts_missing` / 空），页面上无法区分。

## 2. 契约

### C1 两个纯函数（`requirement-confirm.js` 的包装 BOM 面板 IIFE 内，`pbMaterialMapAccountBlock()` 之后；体内无 DOM / `fetch(` / `storage`）

- `pbBusinessPartsScope(record)` → `{state, id, hash, total, bound, unbound, gapCode, gapMessage, headline}`：
  - `scope` = `record.business_parts`（**只有**这一处是件数的来源）；`id` = `record.business_parts_id`；
  - `state` 闭集只有 `"ready"` / `"empty"` / `"unavailable"` / `"unknown"`，判据顺序固定：
    1. `scope` 不是普通对象（`undefined` / `null` / 字符串 / 数组 / 数字）→ `unknown`
      （老载荷没有这一块：**不许**当成"清单 0 件"，也不许当成"读不到"）；
    2. `scope.available !== true` → `unavailable`（读不到 / 还没导入：由 `gap` 说清是哪种）；
    3. `business_part_total` 转数后 `<= 0`（非有限也按 0）→ `empty`；
    4. 否则 → `ready`。
  - `id` / `hash` / `gapCode` / `gapMessage`：逐字 trim（允许为空）；
  - `total` / `bound` / `unbound`：逐项 `Number(...)`，非有限数或负数 → `0`（键**必须存在**，
    不许 `null` / `undefined`）；
  - `headline` 逐字四句：
    - `ready` → `这版 BOM 照的业务部件清单：<id 或 —> · 共 N 件（已绑几何 K 件 · 未绑 M 件）· 版本 <hash 前 12 位 或 —>。`
    - `empty` → `业务部件清单读到了，但一件都没有（0 件）：这版 BOM 只按几何零件配，清单要重导。`
    - `unavailable` → `gapMessage` 非空时**逐字转达**（不许换个说法）；为空时用兜底句
      `业务部件清单读不到：这版 BOM 只按几何零件配，别把这次当成「没有业务部件」。`
    - `unknown` → `后端没给业务部件清单这一块（老载荷）：说不清这版 BOM 照哪一版业务部件算的。`
  - 任何输入都不抛错。
- `pbBusinessPartsScopeBlock(record)` → 一段 HTML（**落点就是这里**，`pbPanel()` 只负责拼进去）：
  - **四态都渲染**（每一态都有一句各不相同的事实，没有别的块替它说）：
    `<div class="pb-hint" data-pb-business-parts-state="<state>" data-pb-business-parts-id="<id>"
    data-pb-business-parts-total="<total>" data-pb-business-parts-gap="<gapCode>">` +
    `headline` + `</div>`；
  - 所有插值过 `pbEsc()`；`hash` 只在上面的 `headline` 里截前 12 位，**不改写** `id` / `gapCode`。

### C2 面板接线（`pbPanel()`）

- 新增 `${pbBusinessPartsScopeBlock(record)}`（**一处**调用），紧跟
  `${pbMaterialMapAccountBlock(record.business_material_rows)}` 之后；
- head 里既有那句「零件文档版本：…」（`partsHash.slice(0, 12)`）、材料码映射表的账、
  逐行缺口块、既有四条 banner 与 `data-pb-business-stale` 那一块**一字不动**。

### C3 冻结面

- 件数**只有一个**来源：`record.business_parts`（**不许**回退到 `record.stats` 的几何件数、
  不许去数 `items` 长度、不许用 `business_rows`）；
- 可用性只读 `scope.available`（**不许**按 `business_parts_stale` / 顶层 `business_parts_id`
  是否为空反推 —— 比较不了 ≠ 变了）；
- `empty` 不许说成 `ready`，`unavailable` 不许说成 `empty`；
- 不改后端（`_business_parts_scope()` 的键与取值一字不动）、不加接口 / 依赖、
  `pbPanel()` 里不新增请求、不改 `index.html`；
- 不改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：C1 两个纯函数 + C2 的一处调用；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许用几何件数（`stats.total` / `items.length`）冒充业务件数；
- 不许把"读不到清单"显示成"没有业务部件"，也不许把"清单 0 件"显示成"读不到"；
- 不许改写后端给的 `gapCode` / `gapMessage` / `id`；
- 不连 PG / 34、不写生产数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_business_parts_scope_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_material_code_map_red tests.test_packaging_material_map_account_panel_red \
  tests.test_packaging_bom_disclosure_panel_red tests.test_packaging_parametric_bom_red
```

## 6. 已记录的边界

1. 只**显示**这一版清单的版本与规模，不在 BOM 面板上提供"重新导入清单"入口
   （导入入口在 2.1 的业务部件面板，`packaging-business-parts-and-cad-plan-view.md` §12.1）；
2. 逐行漂移（哪些行来自上一版清单）仍由既有 `data-pb-business-stale` 那一块承担，本批不动；
3. `bound_total` / `unbound_total` 是**清单自身**的绑定统计（几件绑了几何分量），
   与这份 BOM 的行数不是一回事，本批不把它们与 BOM 行数混算；
4. 其它行业的 BOM 面板仍只在包装需求单上挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

**实现前红基**（`git stash push -- tech_app/frontend/requirement-confirm.js` 后的原文）：

```
Ran 19 tests ... FAILED (failures=15, errors=1)
```

16 红 / 3 绿 —— 绿的三条是守卫（`C3` 既有块（head 那句零件文档版本、材料码映射表的账、
逐行缺口、`data-pb-parts-stale` / `data-pb-parts-unavailable`）一字未动、`D3` `index.html`
未动、`D4` `node --check` 通过）；
16 红 = `A1`–`A8`、`B1`–`B4`（两个纯函数根本不存在）+ `C1`、`C2`（锚点拿不到 → ERROR）、
`D1`、`D2`。

**实现后**：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_business_parts_scope_panel_red -v
Ran 19 tests ... OK
node --check tech_app/frontend/requirement-confirm.js   # 退出码 0
```

落点（只改了 `tech_app/frontend/requirement-confirm.js` 一个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 事实 | `pbBusinessPartsScope(record)`（包装 BOM 面板 IIFE 内，`pbMaterialMapAccountBlock()` 之后） | 四态闭集判据顺序：`record.business_parts` 不是普通对象（老载荷）→ `unknown`；`available !== true` → `unavailable`；件数转数后 `<= 0` → `empty`；否则 `ready`。件数只读 `business_parts`（体内无 `stats` / `items`），可用性只读 `available`（体内无 `stale`）；`gap.message` 非空时**逐字转达**、为空才用兜底句 |
| §C1 渲染 | `pbBusinessPartsScopeBlock(record)` | 四态都渲：`data-pb-business-parts-state` / `-id` / `-total` / `-gap` + headline，插值全过 `pbEsc()`；`hash` 只在 headline 里截前 12 位（与 head 里零件版本同一口径） |
| §C2 | `pbPanel()`：`${pbMaterialMapAccountBlock(record.business_material_rows)}` 之后插入 `${pbBusinessPartsScopeBlock(record)}` | 一处调用；head 里既有「零件文档版本」、材料码映射表的账、逐行缺口、既有四条 banner 与漂移块一字未动；`pbPanel()` 里无 `fetch(` |
| §C3 | 无 | 未改后端（`_business_parts_scope()` 的键与取值一字不动）、未加接口 / 依赖、未改 `index.html` |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_material_code_map_red tests.test_packaging_material_map_account_panel_red \
  tests.test_packaging_bom_disclosure_panel_red tests.test_packaging_parametric_bom_red
Ran 173 tests ... OK
```

边界（与 §6 一致，实现如约未越）：只显示版本与规模，不在 BOM 面板上开"重新导入清单"入口；
逐行漂移仍由既有 `data-pb-business-stale` 承担；`bound_total` / `unbound_total` 是清单自身的
绑定统计，不与 BOM 行数混算；其它行业仍不挂载 BOM 面板。
