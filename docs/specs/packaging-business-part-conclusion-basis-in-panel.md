# 规格：业务部件结论的「口径四键」必须出现在内嵌面板上（`## 412`/`## 414` 的界面那一半）

依赖：`docs/specs/packaging-business-part-cost-by-authority-size.md`（§C4 的读回体已经带
`size_source` / `size_source_ref` / `size_text` / `geometry`）、
`docs/specs/packaging-business-part-process-by-authority-route.md`（§C5 同样四键）、
`docs/specs/packaging-part-conclusion-business-identity-in-panel.md`（同一块面板、同一种接法：
一个顶层纯函数 + 一个正文行 + 两处正文最前面各调一次）、
`docs/specs/packaging-silent-degradation-disclosure.md`（说不出"这份金额/这份工序是按什么尺寸、
哪条路算的"就是静默降级）。

状态：Spec + 红测（已实现）（现状：后端 `## 412`/`## 414` 的读回体早就带那四键，前端
`inline-analysis.js` 只消费 `parts_id` / `stale` / `business_*`（`:157`），
**四键一个消费者都没有** —— 于是面板上"按权威尺寸算的、没与 CAD 几何核过"这句话
只写在结论的 `assumptions` 里（成本那条），工艺那条连这句话都看不到；
更糟的是 `generate()`（`:235`）只拿 `task.result` 覆盖 `state.plan` / `state.analysis`，
而两条业务件路由的**任务返回值**里根本没有那四键 → 生成完那一刻口径行无处可取）
红测：`tests/test_packaging_business_part_basis_in_panel_red.py`
行号基线：HEAD `0477ffc`

## 0. 一句话目标

在内嵌面板上，**业务部件**那两条路（按权威尺寸算成本 / 按权威清单排工序）的正文最前面多一行
「按权威尺寸算的（300×200MM；来源：酒盒 报价资料.xlsx#Sheet1!B12）；这一件没有绑 CAD 几何。」；
几何零件那条路**一个字都不多说**；生成完立刻就有这一行（不许等刷新）。

## 1. 现状缺口（代码级）

1. `inline-analysis.js:157` 只把读回体算成 `state.businessNote`；`size_source` / `size_source_ref` /
   `size_text` / `geometry` 没有消费者。
2. `generate()`（`:235`）用 `poll()` 拿到的 `task.result` 直接覆盖 `state.plan` / `state.analysis` ——
   而两条业务件路由的**任务返回值**（`main.py` 的 `packaging_business_part_cost()` /
   `packaging_business_part_process()` 的 `job()`）里没有那四键，于是"刚生成完"那一刻面板上
   说不出这份结论的口径。
3. `renderProcess()`（`:370`）/ `renderCost()`（`:481`）的正文里只有
   `businessIdentityRow(state)`（业务清单漂移那一行），没有"按哪套尺寸算的"。

## 2. 契约

### C1 新增顶层纯函数 `packagingBusinessPartBasisNote(data) -> {text, level}`

- 落点：`tech_app/frontend/app.js` **顶层**（可被 `node -e` 抽出来真跑）；
- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`；
- 判据固定：
  1. `data` 不是对象，或 `size_source` 去空后 **≠** `"authority_dimensions"`（含几何件那条路的
     空串）→ `{text: "", level: ""}`（几何件一个字都不多说）；
  2. 否则：`inside` = 非空的 `size_text`、非空的 `来源：<size_source_ref>` 两段（缺哪段少哪段、
     用 `；` 连接）；
     - `geometry` 以 `bound:` 开头且后面去空后非空 → `level: "authority_bound"`、
       尾句 `；这一件另绑了几何件 <编码>，本结论有意按权威尺寸算。`
       （`<编码>` 取冒号后那段，**同一句里的编码只出现一次**）；
     - 否则 → `level: "authority_unbound"`、尾句 `；这一件没有绑 CAD 几何。`
       （空 `geometry` 与 `unbound` 同形：后端在这条路上只会给这两个值之一）；
  3. `text` = `按权威尺寸算的` + （`inside` 非空时 `（<inside>）`）+ 尾句；
- `level` 闭集 = `("", "authority_unbound", "authority_bound")`；任何输入都不抛错。

### C2 `inline-analysis.js` 接线

- `state.basisNote = null` 进 `open()` 的初始状态；`load()` 里**紧接**
  `state.businessNote = packagingPartBusinessIdentityNote(data)` 之后加一处
  `state.basisNote = packagingBusinessPartBasisNote(data)`；
- `generate()` 里 `poll()` 之后、`render(state)` **之前**加一处
  `state.basisNote = packagingBusinessPartBasisNote(result)`（生成完立刻有新口径，不必刷新）；
- 新增 `businessBasisRow(state)`：与 `businessIdentityRow(state)` **同形** ——
  `data-inline-basis-note="<level>"`、空文案时**一个节点都不渲染**；
- `renderProcess()` / `renderCost()` 的正文最前面各调一次，**紧接** `businessIdentityRow(state)`
  之后（业务清单漂移那句仍在它前面）。

### C3 两条业务件路由的任务返回值补上那四键

- `main.py` 的 `packaging_business_part_cost()` 的 `job()` 返回值加 `size_source` /
  `size_source_ref` / `size_text` / `geometry`（值取 `inputs` 与 `_packaging_business_geometry_label(row)`）；
- `packaging_business_part_process()` 的 `job()` 返回值加同样四键；
- **只加键**：既有键（`part_code` / `analysis` / `summary` / `line` / `plan` / `validation` /
  `coverage` / `part_id`）一个都不动；几何那两条路由（`packaging_part_cost()` /
  `packaging_part_process()`）**一字不动**。

### C4 冻结面

- 几何零件那条路（`size_source == ""`）在面板上仍然**一句话都不多说**（C1 第 1 条判据）；
- `businessIdentityRow()` / `conclusionVersionNote()` / `renderShell()` / `renderControls()` /
  `collectProcessEdits()` / `collectCostEdits()` 一字不动；
- `app.js` 的业务件路由引用计数不变（仍是 `## 415` 重指后的 4）；后端不改任何既有键、
  不改 `packaging_parts.py`、不改成本 / 工艺算法；
- 不改 `tests/` 下任何文件（本批红测除外 —— 本批不动任何既有守卫）。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增顶层纯函数 `packagingBusinessPartBasisNote()`；
2. `tech_app/frontend/inline-analysis.js`：`state.basisNote`、`businessBasisRow()`、`load()` /
   `generate()` 各一处赋值、`renderProcess()` / `renderCost()` 各一处；
3. `tech_app/backend/main.py`：两条业务件路由的任务返回值各补四键；
4. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把口径那句话在前端**改写 / 猜测**：文案逐字来自本地表，证据（尺寸原文 / 来源）逐字来自后端；
- 不许给几何零件那条路也加这一行（它没有口径四键，"多说"就是编）；
- 不许在 `generate()` 之后重新 `GET` 一次（那会把"生成完立刻可见"变成"再读一次"的假动作）；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_basis_in_panel_red -v
node --check tech_app/frontend/app.js && node --check tech_app/frontend/inline-analysis.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_part_conclusion_business_identity_in_panel_red \
  tests.test_packaging_business_part_cost_by_authority_size_red \
  tests.test_packaging_business_part_process_by_authority_route_red \
  tests.test_packaging_business_part_size_cost_entry_red \
  tests.test_packaging_business_part_process_entry_red \
  tests.test_packaging_parts_panel_red
```

## 6. 已记录的边界

1. 本批只把**已经回了的**四键显示出来：`size_source` 闭集今天只有 `authority_dimensions` 一项，
   将来多出口径（例如几何轮廓那一套）时，这一行按同一判据扩展，不在前端另写判断；
2. 工序那条**没有** assumption 文案（`## 414` 的 `assumptions[0]` 只在成本那支渲染），所以这一行
   是工艺那条唯一的口径披露点；
3. 绑定状态那行（`## 410`/`## 411` 的业务清单漂移）与本行**分家**：前者说"照哪一版清单"，
   后者说"按哪套尺寸"。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_basis_in_panel_red
# 实现前：Ran 19 tests … FAILED (failures=15)   ← 15 红（A1–A8 / B1–B4 / C1–C3）
# 实现后：Ran 19 tests … OK                     ← 4 条护栏（B5 / C4 / C5 / C6）红基即绿
node --check tech_app/frontend/app.js && node --check tech_app/frontend/inline-analysis.js   # OK
```

| 契约 | 落点 |
| --- | --- |
| §C1 纯函数 | `tech_app/frontend/app.js:2534 packagingBusinessPartBasisNote(data)`：两键 `{text, level}`；`size_source != "authority_dimensions"` → `{"", ""}`；两段证据（`size_text` / `来源：<size_source_ref>`）缺哪段少哪段；`bound:<编码>` → `authority_bound` + 那半句、否则 `authority_unbound` + 「这一件没有绑 CAD 几何。」；体内无 `document.` / `window.` / `fetch(` / `localStorage` |
| §C2 接线 | `inline-analysis.js`：`:64 businessBasisRow(state)`（`data-inline-basis-note`、空文案不渲染）；`:84 basisNote: null`；`:170 load()` 里一处、`:258 generate()` 里一处（用 `task.result`，**不**重读）；`:398 renderProcess()` / `:506 renderCost()` 正文最前面、**紧接** `businessIdentityRow(state)` 之后各调一次 |
| §C3 任务返回值 | `main.py` 两条业务件路由的 `job()` 返回值各补四键：成本 `:8823`（`part_code` / `analysis` / `summary` / `line` 一个不动）、工艺 `:8952`（`part_code` / `part_id` / `plan` / `validation` / `coverage` 一个不动）；几何那两条路由一字未动 |
| §C4 冻结面 | 几何那条路（`size_source == ""`）在面板上仍一句话都不说（A3 明写）；`businessIdentityRow()` / `conclusionVersionNote()` / `renderShell()` / `renderControls()` / `collect*Edits()` 一字未动；`app.js` 的业务件路由引用仍是 4 处（C6）；`packaging_parts.py` 未改（C5） |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_part_conclusion_business_identity_in_panel_red \
  tests.test_packaging_business_part_cost_by_authority_size_red \
  tests.test_packaging_business_part_process_by_authority_route_red \
  tests.test_packaging_business_part_size_cost_entry_red \
  tests.test_packaging_business_part_process_entry_red tests.test_packaging_parts_panel_red
  → Ran 134 … OK

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_downstream_red tests.test_packaging_parts_downstream_readback_red \
  tests.test_packaging_parts_conclusion_version_readback_red \
  tests.test_packaging_part_conclusion_business_identity_red \
  tests.test_packaging_business_part_downstream_entry_red tests.test_packaging_parts_outline_red
  → Ran 101 … OK
```

未连 PG / 34、未写生产数据、未调模型、未 push / MR / tag / Release / 未部署。
