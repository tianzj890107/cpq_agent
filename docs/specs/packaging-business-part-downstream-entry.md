# 规格：业务部件行的「单件工艺 / 成本」必须真能发起（`## 368` §12 边界 2 剩下的那一半）

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§12 边界 2：「业务部件行的
『单件工艺 / 成本』只给了说明文案，尚未按 `business_parts_id` 落到下游任务表 —— 这是 Spec §7
『BOM / 工艺 / 成本只遍历 business_parts』剩下的那一半」；§12.1 的导入入口）、
`docs/specs/packaging-parts-downstream-process-and-cost.md`（几何件那套下游入口与
`processability()`，本批**不新写第二套**）、
`docs/specs/packaging-silent-degradation-disclosure.md`（说不出"为什么不能算"就是静默降级）。

状态：Spec + 红测（已实现）（原状：业务部件面板的动作区（`app.js:2574-2577`）只有一句说明文案
「单件工艺 / 成本按业务部件版本另跑；几何没绑定只影响依赖几何的尺寸，不影响有权威尺寸的材料与采购项。」
—— **一个能点的按钮都没有**；几何零件那套入口（`#packagingPartProcess` / `#packagingPartCost`）
只认 `DWG-Pxx`，业务部件编码（真样本 `JWXR21-P01…`）没有入口，也没说清"到底缺什么"）
红测：`tests/test_packaging_business_part_downstream_entry_red.py`
行号基线：HEAD `e280c73`

## 0. 一句话目标

在 2.1 右栏选中一个业务部件时：**绑到闭合几何件**的 → 直接给「生成工艺推荐 / 成本测算」两个按钮，
点了复用几何件那套既有下游入口；**没绑 / 绑到的件没闭合** → 不给空按钮，直接写明「为什么现在不能算、
下一步做什么」。

## 1. 现状缺口（代码级）

1. 业务部件面板的动作区（`openPackagingBusinessPart()` 里 `#packagingPartActions`）写死了那句说明文案，
   **没有任何动作**；用户点不到工艺 / 成本。
2. 几何零件面板那一套（`packagingPartActionsHtml()` `:1413` → `packagingPartProcessability()`
   → `packagingPartAnalyze()` `:1661` → `CadInlineAnalysis`）只吃 `part_code = DWG-Pxx`；
   业务部件编码在零件文档里查不到，直接调会 404 / 判成"不可算"。
3. 两边的数据其实早就在内存里（`packagingBusinessParts.business_parts[].geometry_binding.component_ids`
   与零件文档行上的 `component_id` 同一命名空间），只是没有人做这个**只读的**映射 ——
   于是"能算的算不了，不能算的也看不出为什么"。

## 2. 契约

### C1 新增顶层纯函数 `packagingBusinessPartDownstreamTarget(row, partsDoc) -> dict`

- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`（可被 `node -e` 抽出来真跑）；
- 入参：业务部件行 + 零件文档（`{parts: [...]}`）。返回 `{ok, part_code, code, message}`（四键固定）；
- 目标分量集合 = `row.geometry_binding.component_ids`（并接受 `row.geometry_component_ref`
  作为兜底），去空、全部 `String()`；
- 判据顺序固定：
  1. `row` 不是对象 / 编码（`business_part_code`）为空 → `{ok:false, code:"business_part_missing",
     message:"这一件没有业务部件编码，不能发起下游。"}`；
  2. 集合为空 / 零件文档里没有 `component_id`（或 `geometry_component_ref`）命中集合的行 →
     `{ok:false, code:"geometry_unbound", message:"这一件还没在 CAD 图中定位到几何件；先在平面图里
     确认几何映射，或按权威尺寸补录后再算。"}`；
  3. 命中的几何件里**没有** `outline_status === "closed"` 的 →
     `{ok:false, code:"outline_open", message:"这一件绑定的几何件还没有闭合轮廓（尺寸来自包围盒），
     先把轮廓补出来再算。"}`；
  4. 否则 → `{ok:true, part_code: <闭合件的 part_code>}`，多件命中时取 `part_code` 升序第一个
     （确定性，不按遍历顺序碰运气）。

### C2 业务部件面板的动作区（`openPackagingBusinessPart()`）

- 用 C1 算出目标（`currentPackagingParts` 为零件文档）：
  - `ok` → 渲染两个按钮：`#packagingBusinessPartProcess`（「生成工艺推荐」）、
    `#packagingBusinessPartCost`（「成本测算」），容器带 `data-qqBusinessDownstream="1"`，
    点击 → `packagingBusinessPartAnalyze(mode, part_code)`；
  - `!ok` → **不**渲染按钮，渲染一行原因（`data-qqBusinessDownstreamReason="1"`）=
    C1 的 `message`（说清为什么不能算 + 下一步）；
- 既有那句说明文案「单件工艺 / 成本按业务部件版本另跑；几何没绑定只影响依赖几何的尺寸，
  不影响有权威尺寸的材料与采购项。」**逐字保留**，跟在上面两者之后（它说的是"口径"，
  不是"下一步"）；
- 面板其余部分（权威资料 / 绑定状态 / 证据 / 缩略图）一个字不改。

### C3 `packagingBusinessPartAnalyze(mode, partCode)` 复用既有入口

- `await selectPackagingPart(partCode)`（既有取行 + 渲染 + 高亮）→ `return packagingPartAnalyze(mode)`；
- **不许**新写第二套分析接口 / 渲染 / 端点；`mode` 取值仍是 `"process"` / `"cost"`。

### C4 冻结面

- 不改后端（不加接口、不改路由、不改 `processability()`）；不改几何零件面板
  （`packagingPartActionsHtml()` / `packagingPartProcessability()` / `packagingPartAnalyze()`
  的判据与文案一字不动）；
- 不改 `selectPackagingPart()`、不改 `PUT .../geometry-binding` 的入口；
- 新代码不得落在 `packagingPartActionsHtml` → `packagingPartSolidReason` 切片里出现
  `3D 预览` / `packagingPartSolid`（`test_packaging_business_parts_and_cad_plan_view_red.py` 的既有守卫）；
- `node --check tech_app/frontend/app.js` 必须通过。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增顶层纯函数 `packagingBusinessPartDownstreamTarget()`；
   `openPackagingBusinessPart()` 的动作区；新增 `packagingBusinessPartAnalyze()`；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许在没有几何事实时**猜**一个几何件，也不许用权威尺寸去冒充"几何已闭合"；
- 不许把"不能算"渲染成一个能点但必然失败的按钮；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_downstream_entry_red -v
node --check tech_app/frontend/app.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_business_part_panel_evidence_red \
  tests.test_packaging_parts_downstream_red \
  tests.test_packaging_parts_panel_red \
  tests.test_packaging_part_detail_read_failure_red
```

## 6. 已记录的边界

1. 本批只把**能算的那一半接上**：单件结论仍落在几何零件那套下游任务表上（`DWG-Pxx` 的身份），
   业务部件身份到任务表的写入（`business_parts_id` 落任务行）仍是下一批；
2. 没绑定的件**不给**按钮 —— 这是披露，不是禁用；要算先走既有 `PUT .../geometry-binding`
   或补录尺寸；
3. 业务部件的**材料 / 采购项**不受几何绑定影响（既有口径），本批不动那条线。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_downstream_entry_red
# 实现前：Ran 14 tests … FAILED (failures=10)   ← T1–T10
# 实现后：Ran 14 tests … OK                     ← S1–S4 四条护栏始终绿
node --check tech_app/frontend/app.js           # OK
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §C1 纯函数 | `packagingBusinessPartDownstreamTarget(row, partsDoc)`（顶层，`:2422`）：四键 `{ok, part_code, code, message}`；判据顺序 = 无编码 `business_part_missing` → 命不中几何件 `geometry_unbound` → 命中但无闭合件 `outline_open` → `ok`；多件命中按 `part_code` 升序取首（`localeCompare`，确定性）；分量集合 = `geometry_binding.component_ids` + 兜底 `geometry_component_ref`，全部 `String().trim()` 去空。体内无 `document.` / `window.` / `fetch(` / `localStorage` |
| §C2 动作区 | `openPackagingBusinessPart()`：`packagingBusinessPartDownstreamTarget(row, currentPackagingParts || {})` → `ok` 渲染 `#packagingBusinessPartProcess` / `#packagingBusinessPartCost`（容器 `data-qqBusinessDownstream="1"`，各带 `data-qqBusinessDownstreamMode`，点 `packagingBusinessPartAnalyze(mode, target.part_code)`）；`!ok` 渲染 `data-qqBusinessDownstreamReason="1"` 一行 = C1 的 `message`，不给按钮；既有那句说明文案「单件工艺 / 成本按业务部件版本另跑；…」**逐字保留**，`actions.innerHTML = downstream + note` 把两者拼在后面 |
| §C3 复用入口 | `packagingBusinessPartAnalyze(mode, partCode)`（顶层，`:2458`）：空编码直接回 `{ok:false, error:{code:"no-part-code", …}}`，否则 `await selectPackagingPart(code)` → `return packagingPartAnalyze(mode)`；`mode` 仍是 `"process"` / `"cost"`，未新写第二套接口 / 渲染 / 端点 |
| §C4 冻结面 | 后端 `main.py` 一行未改；`packagingPartActionsHtml()` / `packagingPartProcessability()` / `packagingPartAnalyze()` 判据与文案一字不动；`selectPackagingPart()` 与 `PUT .../geometry-binding` 未改；新函数落在 `packagingBusinessPartRows()` 之后（`packagingPartProcess` 标记 +4000 字窗口之外），`packagingPartActionsHtml` → `packagingPartSolidReason` 切片里仍无 `3D 预览` / `packagingPartSolid` |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_business_part_panel_evidence_red \
  tests.test_packaging_parts_downstream_red \
  tests.test_packaging_parts_panel_red \
  tests.test_packaging_part_detail_read_failure_red \
  tests.test_packaging_business_parts_binding_size_source_red
  → Ran 97 … OK
```

未改后端、未改 `tests/` 下任何文件、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。
