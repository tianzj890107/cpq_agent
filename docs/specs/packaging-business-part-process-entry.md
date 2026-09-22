# 规格：业务部件「按权威清单排工序」的入口必须能点（`## 414` 的界面那一半）

依赖：`docs/specs/packaging-business-part-process-by-authority-route.md`（后端 §C4/§C5 已经能排、
能读、说得清口径）、`docs/specs/packaging-business-part-size-cost-entry.md`（同一颗面板、同一种接法：
能算给按钮、不能算只给原因；`CadInlineAnalysis` 的 `endpointBase` 注入点）、
`docs/specs/packaging-business-part-downstream-entry.md`（§C2 的动作区分家：绑了闭合几何件走几何那两颗
按钮，没绑才轮到权威清单这条路）。

状态：Spec + 红测（已实现）（现状：`tech_app/frontend/app.js` 的 `openPackagingBusinessPart()`
（`:2624`）在「这一件没绑几何」时给的是 `## 409` 那行原因 + `## 413` 那颗
「成本测算（按权威尺寸）」（`:2707` / `:2739`），**工艺那一半一个入口都没有**；后端
`POST/GET …/requirement/packaging-business-parts/{code}/process`（`## 414`）已经上线，
前端完全不知道它 —— 于是没几何的业务部件在界面上仍然"排不了工艺"，尽管权威清单里
尺寸与材料原文都写着）
红测：`tests/test_packaging_business_part_process_entry_red.py`
行号基线：HEAD `3b3e09c`

## 0. 一句话目标

在 2.1 右栏选中一件**没有几何**的业务部件时：只要权威清单里有长度 / 宽度 / 材料原文，就给一颗
「工艺推荐（按权威清单）」按钮，点了在同一个内嵌面板里出工序明细；缺哪一样就还是只给原因。

## 1. 现状缺口（代码级）

1. `openPackagingBusinessPart()`（`:2624`）的动作区只有两支：`target.ok`（几何那条路）给两个按钮，
   否则给一行原因 + （`## 413`）那颗按权威尺寸算成本——**工序那一半没有任何入口**。
2. `packagingBusinessPartSizeCost()`（`:2518`）的 `endpointBase` 只指到
   `.../packaging-business-parts/{code}`（成本路由）；`## 414` 新加的 `.../process` **没有人调**。
3. 业务件不能走 `selectPackagingPart()` / `packagingBusinessPartAnalyze()`（它们去零件文档里找
   `DWG-Pxx`，业务编码必然 404）——所以新入口不能复用它们。

## 2. 契约

### C1 新增顶层纯函数 `packagingBusinessPartProcessTarget(row) -> {ok, code, message, part_code}`

- 落点：`tech_app/frontend/app.js` **顶层**（可被 `node -e` 抽出来真跑）；
- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`；
- 判据顺序固定（与 `packagingBusinessPartSizeCostTarget()` 同形，多一道材料门槛）：
  1. `row` 不是对象 / `business_part_code` 去空后为空 →
     `{ok: false, code: "business_part_missing", part_code: "",
       message: "这一件没有业务部件编码，不能排工艺。"}`；
  2. 权威尺寸不过（`authority.length_mm` / `width_mm` 任一取不到、或 ≤ 0）→
     `{ok: false, code: "authority_size_missing", part_code: <编码>,
       message: "这一件没有权威尺寸（长度/宽度），先在平面图里确认几何映射，或补录权威尺寸后再排工艺。"}`；
  3. 材料原文不过（`authority.material_text`，兜底 `row.material`，去空后为空）→
     `{ok: false, code: "material_missing", part_code: <编码>,
       message: "这一件在权威清单里没有材料原文，补上材料后再排工艺。"}`；
  4. 否则 `{ok: true, code: "", message: "", part_code: <编码>}`；
- 数字串认（`"300"` 同 `300`），坏值（`{}` / `[]` / `"abc"`）按"没有"处理；
- 任何输入都不抛错。

### C2 `openPackagingBusinessPart()` 的动作区

- `target.ok`（绑到闭合几何件）这一支**一个字不改**（`## 409` 的两个按钮与说明照旧）；
- `!target.ok` 这一支：既有那行原因（`data-qqBusinessDownstreamReason="1"`）与 `## 413` 那条
  `data-qqBusinessSizeCost="1"` 说明 + `#packagingBusinessPartCostBySize` 按钮**逐字保留、
  相对顺序不变**，其后**追加**：
  - `packagingBusinessPartProcessTarget(row).ok` 时 →
    一句 `data-qqBusinessProcess="1"` 的说明（「这一件没有几何：按权威清单的原文与尺寸排工序。」）
    + 按钮 `#packagingBusinessPartProcessByAuthority`（文案「工艺推荐（按权威清单）」），
    点击 → `packagingBusinessPartProcessByAuthority(<编码>)`；
  - 不 ok → 什么都不加（现状逐字不变）；
- 面板其余部分（权威资料 / 绑定状态 / 证据 / 缩略图 / 底部那句 note）一个字不改。

### C3 `packagingBusinessPartProcessByAuthority(partCode)`

- 与 `packagingBusinessPartSizeCost()`（`## 413`）**同形**：先 `exitBoardViewHost()`、
  `setRightPane("analysis", "<编码> · 工艺推荐（按权威清单）")`，再
  `window.CadInlineAnalysis.open("process", {host, projectId, part: {part_id: <编码>, name},
  endpointBase: () => "<API>/api/projects/<pid>/requirement/packaging-business-parts/<编码>",
  onClose})`；
- **不**调 `selectPackagingPart()`、**不**调 `packagingBusinessPartAnalyze()`、不新写渲染；
- 没有编码 → `{ok: false, error: {code: "no-part-code", …}}`；没有 host / 没有
  `window.CadInlineAnalysis` → `{ok: false, error: {code: "no-analysis-host", …}}`（与 `## 413`
  逐字同形）。

### C4 冻结面与一处**重指**

- `packagingBusinessPartSizeCostTarget()` / `packagingBusinessPartSizeCost()` /
  `packagingBusinessPartDownstreamTarget()` / `packagingPartAnalyze()` / `selectPackagingPart()`
  一字不动；几何那两颗按钮仍在；
- 后端（`main.py` / `packaging_parts.py` / `process.py`）一行不改；
- `## 412` 红测里 `E4` 那条**批次级冻结**（"前端只多出那一条声明的业务件成本请求"）按本批 Spec §C3
  **重指**为"前端多出的两条声明的业务件请求"（成本一条 + 工艺一条，各一处）；
  **重指不等于放宽** —— 多出来的必须逐个点名，且不许有第三条。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增顶层纯函数 `packagingBusinessPartProcessTarget()`、
   `packagingBusinessPartProcessByAuthority()`，以及 `openPackagingBusinessPart()` 的 `!ok` 那一支；
2. `tests/test_packaging_business_part_cost_by_authority_size_red.py`：**只**改 `E4` 那条计数与注释
   （上一批的重指流程），别的一条都不许动；
3. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改其它 `tests/` 文件（含本批红测）、不许放宽任何断言；
- 不许把「没有权威尺寸 / 没有材料原文」渲染成一个能点但必然失败的按钮；
- 不许把业务件的工序明细显示成几何件的（编码 / 口径那句话由后端给，前端不许改写）；
- 不许在前端算工序 / 算工时、不许默认料厚、不许造几何特征；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_process_entry_red -v
node --check tech_app/frontend/app.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_process_by_authority_route_red \
  tests.test_packaging_business_part_size_cost_entry_red \
  tests.test_packaging_business_part_cost_by_authority_size_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_panel_red \
  tests.test_packaging_parts_downstream_red
```

## 6. 已记录的边界

1. 本批只做**入口**：点了之后那套渲染仍由既有 `CadInlineAnalysis` 负责，工序明细仍由后端
   `outline_process()` 编制；
2. 面板里**不算**工序数 / 工时 —— 数字仍然全部来自后端；
3. 权威尺寸与几何**并存**时三颗按钮都在（几何那两颗 + 权威清单那一颗）：哪一颗都合规，
   口径各由后端那句 assumption 说清（与 `## 413` §6 边界 3 同一条纪律）；
4. `process_text`（工艺路线原文）在界面上**不重复渲染** —— 它由后端进 `note` / `grounding`，
   读回体里也有，面板按既有形状显示。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_process_entry_red
# 实现前：Ran 20 tests … FAILED (failures=14, errors=1)   ← 15 红（A1–A8 / B1–B5 / C4 / C6）
# 实现后：Ran 20 tests … OK                              ← 5 条护栏（B6 / C1–C3 / C5）红基即绿
node --check tech_app/frontend/app.js                    # OK
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §C1 纯函数 | `:2506 packagingBusinessPartProcessTarget(row)`：四键 `{ok, code, message, part_code}`；判据 = 无编码 `business_part_missing` → 权威长度/宽度缺或 ≤0 `authority_size_missing` → 材料原文（`authority.material_text` 兜底 `row.material`）空 `material_missing` → `ok`；体内无 `document.` / `window.` / `fetch(` / `localStorage`（`node -e` 真跑） |
| §C2 动作区 | `openPackagingBusinessPart()`：`:2765` 算出 `processTarget`；`!target.ok` 那一支 `:2784` 在 `## 413` 那条之后**追加** `data-qqBusinessProcess="1"` 那一行 + `#packagingBusinessPartProcessByAuthority`（「工艺推荐（按权威清单）」），`:2808` 只在 `processTarget.ok` 时绑点击；`target.ok` 那一支（`## 409` 的两颗几何按钮）与 `## 413` 那条**一个字未改**、相对顺序不变 |
| §C3 发起 | `:2574 packagingBusinessPartProcessByAuthority(partCode)`：`exitBoardViewHost()` → `setRightPane("analysis", "<code> · 工艺推荐（按权威清单）")` → `CadInlineAnalysis.open("process", {host, projectId, part, endpointBase: () => "<…>/requirement/packaging-business-parts/<code>", onClose})`；**不**调 `selectPackagingPart()` / `packagingBusinessPartAnalyze()`、不新写渲染；没有 host 回 `no-analysis-host`、没有编码回 `no-part-code` |
| §C4 冻结面与重指 | 几何那条 `packagingBusinessPartDownstreamTarget()` 体内仍无 `authority` / `length_mm` / `width_mm`；几何两颗按钮与 `## 413` 那颗成本按钮仍在；`main.py` / `packaging_parts.py` 一行未改；`## 412` 红测 `E4` 的计数**重指** 3 → 4 并逐个点名两颗按钮（注释写明"重指不等于放宽"），`## 414` 红测 `F4` 同样重指 |

**顺带做的一件事**：`## 414`（上一批，后端）的 `F4` 原本是"前端一处都不许多"的批次级冻结（计数 3），
本批按 Spec §C4 把它**重指**为 4；两处重指都在注释里点名了多出来的那两颗按钮（`## 413` 的成本入口、
本批的工艺入口），并且 `C6` 仍然锁"各只许有一处"。

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_process_entry_red \
  tests.test_packaging_business_part_process_by_authority_route_red \
  tests.test_packaging_business_part_size_cost_entry_red \
  tests.test_packaging_business_part_cost_by_authority_size_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_panel_red tests.test_packaging_parts_downstream_red
  → Ran 164 … OK
```

未改后端、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。
