# Spec：2.1 的两笔账（几何分量 / 业务部件）必须同屏对账 —— 几何分量不许自称「零件」

状态：Spec + 红测（已实现）（原状：页面没有任何一处把「几何区域 N 个」与「业务部件 M 件」放在
一起；几何分量的右栏面板标题直接自称「图纸零件」，顶层计数句也把几何分量叫「件」）
红测：`tests/test_packaging_two_ledgers_reconciliation_red.py`
血缘：`docs/specs/packaging-28-part-auto-resolution-and-2d-board-cleanup.md`（§4：263 个分量是几何事实、
不是客户说的 28 件业务部件 —— 左栏不许回退成几何清单）、`docs/specs/packaging-parts-list-visibility-and-kinds.md`
（§2.5 三笔账分三句说）、`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§2 业务部件是**唯一**部件集合）。
本批 changelog 条目号：`## 477`。

## 0. 一句话目标

用户问「为什么还是解析出来 263 个 / 是不是要重建项目才出二十多个」。事实是**两笔账都是真值**：
263 是 CAD 图上的几何连通分量，28 是业务部件（BOM 口径的采购/加工件）。代码里两笔账分别存在、
也分别能读，但**页面上没有任何一处把它们放在一起**，几何分量的右栏面板还直接写着「图纸零件」
—— 所以人看到 263 就以为解析错了。本批只做**对账与措辞**：把两笔账摆在同一行说清，并让几何分量
不再自称「零件」。不改任何一笔账的取值。

## 1. 实测证据（HEAD `0312614` 工作副本只读 + 隔离真跑）

| 读数 | 实测 |
| --- | --- |
| 酒盒.dwg 真跑（隔离 `DATA_DIR`，`packaging_drawing_flow.run_flow`）几何分量文档 | `kept=263 parts=263`（`packaging_parts` 文档 `total=263`） |
| 同一趟的业务部件清单 | `total=28 bound=26 unbound=2`（`save_business_parts()` 落库，读回 28 行） |
| 差 | **235**；两笔账不是同一件事的两种说法 |
| `grep` 同时出现两个分子的那一行 | **0 处**（`几何区域` 只出现在几何诊断 summary 与空态文案里，与业务账从不同屏） |
| `app.js:4666` 顶层计数句（无业务清单时的兜底路） | `已显示 ${rows.length} 件，共 ${total} 件（${kindTotal} 种形状）` —— 「件」没有限定词 |
| `app.js:4673` 截断句 | `还有 ${missing} 件未列出（只显示前 ${rows.length} 件）` |
| `index.html:206` | `<div id="packagingPartTitle" class="packaging-part-title">图纸零件</div>` |
| `app.js:1242` 面板标题兜底 | `title.textContent = name \|\| "图纸零件";` |
| `app.js:1336` 右栏标签 | `label.textContent = "图纸零件 · 选中后看轮廓与证据";` |
| 点一件几何分量（诊断区里的 `DWG-P01 图纸零件 P01`） | 右栏标题就是「图纸零件 …」—— 263 件里的每一件在右栏都叫「零件」 |
| 左栏业务部件树（业务清单存在时） | `业务部件 ${rows.length} 件（…）`（`## 451` 落地，护栏） |
| 是否要重建项目 | **不用**。两笔账都在同一项目里：几何账在 `packaging_parts` 文档、业务账在 `packaging_business_parts` 文档；重进 2.1 由 `load_business_parts()` 读回 |

## 2. 契约

### 2.1 C1：两笔账的对账纯函数 `packagingTwoLedgersLine(geometryDoc, businessDoc)`

`tech_app/frontend/app.js` 新增纯函数（体内无 `document` / `window` / `fetch(` / `localStorage` /
`sessionStorage`；`node` 可直接 eval 跑）：

```
function packagingTwoLedgersLine(geometryDoc, businessDoc)   // 回一行字，取不到数就回 ""
```

取数口径（**只读载荷，不自己判定几何**）：

- `geometryTotal` = `Number(geometryDoc.total) || Number(geometryDoc.stats.part_total) || 0`；
- `geometryReadProblem` = `geometryDoc.read_problem` 是对象且 `code` 非空；
- `geometryKnown` = `geometryTotal > 0 && !geometryReadProblem`；
- `businessTotal` = `Number(businessDoc.summary.stats.business_part_total)`
  `|| Number(businessDoc.stats.business_part_total) || 0`（两处都读不到就当没有）；
- `boundTotal` = 同上的 `bound_total`（读不到给 `0`）；
- `businessReadProblem` = `businessDoc.read_problem` 是对象且 `code` 非空；
- `businessKnown` = `businessTotal > 0 && !businessReadProblem`。

输出逐字（四种组合 + 空）：

| 情形 | 输出 |
| --- | --- |
| 两笔账都有 | `几何区域 263 个 → 业务部件 28 件（已定位 26 件）` |
| 几何账有、业务账还没有 | `几何区域 263 个 → 还没有业务部件清单` |
| 几何账读不到、业务账有 | `几何区域待确认 → 业务部件 28 件（已定位 26 件）` |
| 几何账有、业务账读不到 | `几何区域 263 个 → 业务部件清单读不到` |
| 两边都取不到数（且都不是读失败） | `""`（**不许**拼出「几何区域 0 个 / 业务部件 0 件」，也不许拼「待确认 → 还没有…」） |

`null` / `undefined` / 字符串 / 缺键一律不抛异常（回 `""` 或上面的可用分支）。

### 2.2 C2：对账行必须落在会话可见的左栏里

`renderTree()` 与 `renderPackagingBusinessTree()` **两条路都要**渲染这一行，节点带
`data-qq-ledger-reconciliation="1"`；空串不渲染（不出现空节点）。业务部件树那一路（清单存在）与
几何兜底那一路（清单缺失）都要有 —— 人在两条路上看到的对账口径必须一致。

### 2.3 C3：几何分量不许自称「零件」

- 顶层几何计数句逐字改成 `已显示 ${rows.length} 个几何分量，共 ${total} 个（${kindTotal} 种形状）`
  （只改量词与限定词，数字与顺序不变）；
- 截断句逐字改成 `还有 ${missing} 个几何分量未列出（只显示前 ${rows.length} 个）`；
- `enterDrawingFlowPanes()` 的 `#viewerPartName` 逐字 `几何分量（图纸零件）· 选中后看轮廓与证据`
  （**必须仍含 `图纸零件` 字面量**：`tests/test_packaging_parts_panel_red.py` 的既有契约不许破）；
- 几何分量面板标题（`index.html` 的 `#packagingPartTitle` 默认文案 与
  `renderPackagingPartPanel()` 的兜底）逐字 `几何分量`，该函数体内不许再出现 `图纸零件`；
- 业务部件通道的措辞不动：`业务部件 ${rows.length} 件（`**逐字保留**。

### 2.4 C4：护栏（本批一个字都不许动）

- 两笔账的取值与身份：`packaging_parts` 的 `parts_id` / `parts_hash` / `part_total`、
  `packaging_business_parts` 的 `business_parts_id` / `business_parts_hash` / `business_part_total` /
  `bound_total` 语义不改；读接口路径与载荷形状不改；
- 几何诊断折叠区仍在、默认收起：`几何诊断 / 映射证据（几何区域 ${total} 个，默认收起）`（`app.js:4544`）逐字保留；
- 诊断行名 `图纸零件 PNN`（`packaging_parts` 生成的件名）不改 —— 改名属解析侧，另批；
- 不做前端几何解析、不调模型、不加依赖、不连 PG。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增 `packagingTwoLedgersLine()`；`renderTree()` 两处措辞 + 对账行；
   `renderPackagingBusinessTree()` 对账行；`enterDrawingFlowPanes()` 一句文案；
   `renderPackagingPartPanel()` 一处兜底。
2. `tech_app/frontend/index.html`：`#packagingPartTitle` 默认文案（一行）。
3. `changelog/changelog_9_21_25.md`：`## 477`。

禁止：改任何测试；改两笔账的取值/键集/身份；改 `renderDrawingEntry()` 的入口判定；改业务部件树的
行形状与绑定文案；改 BOM / 成本 / 工艺 / 门禁；改规则 JSON；新增依赖。

## 4. 未做 / 边界（如实记）

- 本批**不**合并两笔账的数据源：几何分量仍是几何分量（`## 373` 的 263 件口径不变），业务部件仍是
  唯一部件集合；只把「两者关系」说清楚。
- 本批**不**改诊断行的件名（`图纸零件 PNN` 来自解析侧命名），只改**面板与计数句**的措辞。
- 未读金标、未改业务数据、未起服务、未连 PG / 34。

## 5. 红测与反向对照

红测：`tests/test_packaging_two_ledgers_reconciliation_red.py`（L 组纯函数 / W 组接线 / S 组措辞与护栏）。

- 红基（把本批三个文件还原成 HEAD `0312614` 工作副本）：`Ran 17 … FAILED (failures=14)`
  —— 14 红 / 3 绿。红的正好是 L1–L8（没有 `packagingTwoLedgersLine()`）、
  W1/W2/W3（两条渲染路都没有对账行）、S1（计数句没有「个几何分量」）、
  S2（右栏标签还是「图纸零件 · …」）、S3（`#packagingPartTitle` 仍是「图纸零件」）；
  绿的 3 条是护栏：S4（业务部件树与几何诊断 summary 的字面量）、
  S5（诊断行名与 `qqGeometryDiagnostics` 钩子）、S6（对账句不做几何求解、不新增读接口）。
- 反向对照 1（只把 `packagingTwoLedgersLine()` 里 `' 个 → '` 换成 `' 个 => '`）⇒
  `Ran 17 … FAILED (failures=4)`：**L1 / L2 / L4 / L6 四条红**（`几何区域待确认 → …` 那一句
  不含 ` 个 → `，L3 因此不受影响 —— 如实记）。
- 反向对照 2（只把 `index.html` 的 `#packagingPartTitle` 默认文案改回 `图纸零件`）⇒
  **S3 单条红**。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 477`）

| 契约 | 落点 | 复跑结果 |
| --- | --- | --- |
| C1 对账纯函数 | `app.js::packagingTwoLedgersLine(geometryDoc, businessDoc)` | L1–L8 绿；`typeof === "object"` 守卫让字符串/数字入参回 `""`（L7）；反向对照 1 四条红 |
| C2 两条渲染路 | `app.js::renderTree()`（几何兜底路，排在 `geometry-diagnostics` 之前）+ `renderPackagingBusinessTree()` | W1/W2/W3 绿；空串不建节点 |
| C3 措辞 | `app.js` 计数句 / 截断句 / `enterDrawingFlowPanes()` 标签 / `renderPackagingPartPanel()` 兜底 + `index.html:206` | S1/S2/S3 绿；反向对照 2 单条红；既有 `图纸零件` 契约（`test_packaging_parts_panel_red`）未破 |
| C4 护栏 | 两笔账取值/键集/身份、诊断折叠区 summary、业务部件措辞、诊断行名、读接口 | S4/S5/S6 绿（S6 要求 `app.js` 里不出现 `packaging-two-ledgers`，文档指针因此写成中文标题） |

复跑（本机 `./open-claude/.venv/bin/python -m unittest`）与全量保护网见 changelog `## 477`。

### 红基（原文保留，`## 6` 改名前的实测）



```text
./open-claude/.venv/bin/python -m unittest tests.test_packaging_two_ledgers_reconciliation_red
Ran 17 tests … FAILED (failures=14)
```

- 红 14 条：L1–L8（没有 `packagingTwoLedgersLine()`）、S1（计数句没有「个几何分量」）、
  S2（右栏标签还是「图纸零件 · …」）、S3（`#packagingPartTitle` 仍是「图纸零件」）、
  W1/W2/W3（两条渲染路都没有对账行）；
- 绿 3 条是护栏：S4（业务部件树与几何诊断 summary 的字面量）、S5（诊断行名与 `qqGeometryDiagnostics` 钩子）、
  S6（对账句不做几何求解、不新增读接口）；
- 反向对照（待实现后补）：把对账函数的 `→` 改成 `=>` ⇒ L 组单条红；把 `#packagingPartTitle`
  默认文案改回「图纸零件」⇒ S3 单条红。
