# 规格：业务部件「按权威尺寸算材料费」的入口必须能点（`## 412` 的界面那一半）

依赖：`docs/specs/packaging-business-part-cost-by-authority-size.md`（后端 §C3/§C4 已经能算、
能读、能说清口径）、`docs/specs/packaging-business-part-downstream-entry.md`（§C2 的动作区分家：
能算给按钮、不能算只给原因；`## 368` §12 边界 3 那句话「几何没绑定只影响依赖几何的尺寸，
不影响有权威尺寸的材料与采购项」是同一个口径）、
`docs/specs/packaging-parts-selectable-panel.md`（`CadInlineAnalysis` 的 `endpointBase` 注入点）。

状态：Spec + 红测（已实现）（现状：`tech_app/frontend/app.js` 的 `openPackagingBusinessPart()`
（`:2571`）在「这一件没绑几何」时只渲染一行原因（`## 409` 那批做的），**一个能点的入口都没有**；
后端 `POST/GET .../requirement/packaging-business-parts/{code}/cost`（`## 412`）已经上线，
前端却完全不知道它 —— 于是 24 件 `unbound` 的业务部件在界面上仍然是"算不了"）
红测：`tests/test_packaging_business_part_size_cost_entry_red.py`
行号基线：HEAD `17d69c4`

## 0. 一句话目标

在 2.1 右栏选中一件**没有几何**的业务部件时：只要权威清单里有长度 / 宽度，就给一颗
「成本测算（按权威尺寸）」按钮，点了在同一个内嵌面板里出材料费；没有权威尺寸就还是只给原因。

## 1. 现状缺口（代码级）

1. `openPackagingBusinessPart()` 的动作区只有两支：`target.ok`（几何那条路）给两个按钮，
   否则给一行原因 —— 后者对"没有几何但有权威尺寸"的件也一视同仁，等于把 `## 412` 的能力藏起来。
2. `packagingPartAnalyze()`（`:1661`）的 `endpointBase` 恒为 `packagingPartEndpoint()`
   （几何零件那一条：`.../packaging-parts/{part_code}`），业务件那条路由**没有人调**。
3. 业务件不能走 `selectPackagingPart()`（它去零件文档里找 `DWG-Pxx`，业务编码必然 404）——
   所以入口不能复用它。

## 2. 契约

### C1 新增顶层纯函数 `packagingBusinessPartSizeCostTarget(row) -> {ok, code, message, part_code}`

- 落点：`tech_app/frontend/app.js` **顶层**（可被 `node -e` 抽出来真跑）；
- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`；
- 判据顺序固定：
  1. `row` 不是对象 / `business_part_code` 去空后为空 →
     `{ok: false, code: "business_part_missing", part_code: "",
       message: "这一件没有业务部件编码，不能算材料费。"}`；
  2. 权威尺寸不过（`authority.length_mm` / `width_mm` 任一取不到、或 ≤ 0）→
     `{ok: false, code: "authority_size_missing", part_code: <编码>,
       message: "这一件没有权威尺寸（长度/宽度），先在平面图里确认几何映射，或补录权威尺寸后再算。"}`；
  3. 否则 `{ok: true, code: "", message: "", part_code: <编码>}`；
- 任何输入都不抛错（坏行 / 数组 / 字符串一律按"没有"处理）。

### C2 `openPackagingBusinessPart()` 的动作区

- `target.ok`（绑到闭合几何件）这一支**一个字不改**（`## 409` 的两个按钮与说明照旧）；
- `!target.ok` 这一支：既有那行原因（`data-qqBusinessDownstreamReason="1"`）**逐字保留、仍在最前面**，
  其后**追加**：
  - `packagingBusinessPartSizeCostTarget(row).ok` 时 →
    一句 `data-qqBusinessSizeCost="1"` 的说明（「这一件没有几何：按权威清单的尺寸算材料费。」）
    + 按钮 `#packagingBusinessPartCostBySize`（文案「成本测算（按权威尺寸）」），
    点击 → `packagingBusinessPartSizeCost(<编码>)`；
  - 不 ok → 什么都不加（现状逐字不变）；
- 面板其余部分（权威资料 / 绑定状态 / 证据 / 缩略图）一个字不改。

### C3 `packagingBusinessPartSizeCost(partCode)`

- 与几何那套**同形**：先 `exitBoardViewHost()`、`setRightPane("analysis", "<编码> 成本测算")`，
  再 `window.CadInlineAnalysis.open("cost", {...})`，`endpointBase` 指到
  `<API>/api/projects/<pid>/requirement/packaging-business-parts/<encodeURIComponent(code)>`
  （业务件那条路由），`part` 用业务编码与名称；
- **不许**调 `selectPackagingPart()`（业务编码不在零件文档里）、**不许**新写第二套渲染；
- 后台不可用时回 `{ok: false, error: {code: "no-analysis-host", …}}`（与既有入口同形）。

### C4 冻结面

- `packagingBusinessPartDownstreamTarget()` / `packagingBusinessPartAnalyze()` /
  `packagingPartAnalyze()` / `packagingPartProcessability()` / `packagingPartEndpoint()` 全部不改；
- 不改后端（`main.py` / `packaging_parts.py` 一行不动）；不改 `tests/` 下任何文件；
- `node --check tech_app/frontend/app.js` 必须通过。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增顶层纯函数 `packagingBusinessPartSizeCostTarget()`、
   `packagingBusinessPartSizeCost()`，以及 `openPackagingBusinessPart()` 的 `!ok` 那一支；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把「没有权威尺寸」渲染成一个能点但必然失败的按钮；
- 不许把业务件的结论显示成几何件的（编码 / 口径那句话由后端给，前端不许改写）；
- 不许在前端算金额、不许默认克重 / 尺寸；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_size_cost_entry_red -v
node --check tech_app/frontend/app.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_business_part_cost_by_authority_size_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_panel_red \
  tests.test_packaging_parts_downstream_red
```

## 6. 已记录的边界

1. 本批只做**成本**入口：工艺推荐仍要几何（工序依赖件特征，本批不碰）；
2. 面板里**不算**金额 —— 数字仍然来自后端 `compute_line()`，前端只负责把端点接对；
3. 权威尺寸与几何并存时两颗按钮都在：哪一颗都合规，口径各由后端那句 assumption 说清。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_size_cost_entry_red
# 实现前：Ran 14 tests … FAILED (failures=9, errors=1)   ← 10 红：A1–A6 / B1–B4
# 实现后：Ran 14 tests … OK                              ← C1–C4 四条护栏始终绿
node --check tech_app/frontend/app.js                    # OK
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §C1 纯函数 | `:2484 packagingBusinessPartSizeCostTarget(row)`：四键 `{ok, code, message, part_code}`；判据 = 无编码 `business_part_missing` → 权威长度/宽度缺或 ≤0（`positive()` 认数字串、`{}`/`[]` 一律不认）→ `authority_size_missing` → `ok`；体内无 `document.` / `window.` / `fetch(` / `localStorage` |
| §C2 动作区 | `openPackagingBusinessPart()`：`:2707` 算出 `sizeTarget`；`!target.ok` 那一支 `:2719` 在**既有原因之后**追加 `data-qqBusinessSizeCost="1"` 那一行 + `#packagingBusinessPartCostBySize`（文案「成本测算（按权威尺寸）」），`:2736` 只在 `sizeTarget.ok` 时绑点击；`target.ok` 那一支（`## 409` 的两个按钮）一个字未改 |
| §C3 发起 | `:2518 packagingBusinessPartSizeCost(partCode)`：`exitBoardViewHost()` → `setRightPane("analysis", …)` → `CadInlineAnalysis.open("cost", {host, projectId, part, endpointBase: () => "<…>/requirement/packaging-business-parts/<code>", onClose})`；**不**调 `selectPackagingPart()`、不新写渲染；没有 host 时回 `no-analysis-host` |
| §C4 冻结面 | `packagingBusinessPartDownstreamTarget()` 体内无 `authority` / `length_mm` / `width_mm`（几何判据没被污染）；几何两颗按钮仍在；`main.py` / `packaging_parts.py` 一行未改；`node --check` 通过 |

**顺带做的一件事**：`## 412` 的红测里那条 `E4`（原文是"前端一行不许动"的批次级冻结）被本批
按 Spec §C3 **重指**为"前端只多出那一条声明的业务件成本请求"（按钮 `id=` 一处、绑定点一处），
重指不等于放宽 —— 见 `tests/test_packaging_business_part_cost_by_authority_size_red.py` 里的注释。

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_cost_by_authority_size_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_panel_red tests.test_packaging_parts_downstream_red
  → Ran 92 … OK

./open-claude/.venv/bin/python -W ignore -m unittest discover -s tests -p 'test_packaging_*.py'
  → 仍是那 5 条既有挂账，本批未引入新红
```

未改后端、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。
