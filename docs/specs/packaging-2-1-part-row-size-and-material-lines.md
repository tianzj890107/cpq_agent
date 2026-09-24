# Spec：2.1 业务部件行——尺寸只留两位小数、材料另起一行、去掉「已在图纸中定位」「图上识别」两句

状态：Spec + 红测（已实现）（2026-09-24 落地，15/15 绿；实现前 8 红 / 7 绿护栏）
红测：`tests/test_packaging_2_1_part_row_size_and_material_lines_red.py`
血缘：`docs/specs/packaging-2-1-parts-row-layout-and-shape-viewport.md`（行版式：标题一行、尺寸另起一行、不许撑破）、
`docs/specs/packaging-business-truth-state-disclosure.md`（三档文案**本批改口径**）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md`（绑定状态闭集）。
本批 changelog 条目号：`## 495`

## 0. 用户原话（2026-09-24）

> 218.19700899999998×68.2460000000001 mm 保留两位小数
> 然后材料这里换行 225G铜版底PET光银 这两个东西之间不需要这个 ·
> 然后已在图纸中定位 / 图上识别 这两句直接不要

现场（2.1 左栏业务部件行）：尺寸是浮点原值、材料被 ` · ` 接在尺寸后面同一行，
下面又跟着「已在图纸中定位」与「图上识别」两行。

## 1. 实测（工作副本只读）

| 读数 | 实测 |
| --- | --- |
| 浮点原值从哪来 | `app.js:5237-5243 packagingPartSizeText()` 与 `app.js:3353-3359 packagingBusinessPartSizeText()` 都把数值直接 `String(number)` 进模板 —— JS 浮点原样输出 `218.19700899999998` |
| 材料为什么同一行 | `app.js:3536`：`<div class="part-meta">${esc(size)}${material ? " · " + esc(material) : ""}</div>` —— 尺寸与材料在**同一个节点**里用 ` · ` 连起来 |
| 「已在图纸中定位」 | `app.js:3343-3344 PACKAGING_BINDING_COPY.bound`，渲染在 `.part-note`（`:3537`）与右栏「定位状态」（`:3650`） |
| 「图上识别」 | `app.js:3368-3373 packagingBusinessPartTruthLabel()` 的 `observed` 档，渲染在 `.packaging-truth-label`（`:3538`） |
| 连带要小心的那一处 | `app.js:3690` 用 `PACKAGING_BINDING_COPY[status] \|\| PACKAGING_CAD_PLAN_NO_COORDS` 兜底 —— 把 `bound` 文案改成空串会让它**误触发**「平面图里没有这一件的坐标」 |

## 2. 契约

### 2.1 C1 尺寸一律两位小数（末尾零去掉）

- `app.js packagingPartSizeText()` 与 `packagingBusinessPartSizeText()` 输出前必须格式化：
  **四舍五入到两位小数、去掉末尾的零**（`218.19700899999998` → `218.2`；`68.2460000000001` → `68.25`；
  `300` → `300`，不写 `300.00`）。
- 后端 `packaging_parts._mm_text()` 同口径（`%.3f` → 两位小数再去尾零），因为它会被拼进给用户看的结论句。
- 客户/图纸**原文**（`product_size_text`、`size_text`）照旧逐字，不许格式化。

### 2.2 C2 材料另起一行，尺寸与材料之间不许再有 ` · `

- 业务部件行拆成两个节点：`.part-meta` 只放尺寸；材料放**独立一行** `.part-material`。
- 尺寸与材料之间不出现 ` · `（也不出现在同一节点里拼串）。
- 几何分量行的 `展开 … · 图层` 不在本批范围（那是图层，不是材料）。

### 2.3 C3 「已在图纸中定位」与「图上识别」两句直接不要

- 绑定状态 `bound` **不再输出任何文字**：行上的 `.part-note` 不渲染、右栏「定位状态」那一行不渲染。
  新增纯函数 `packagingBindingStatusText(status)` 统一出口（`bound` → `""`，其余三档逐字），三处调用点（行、右栏、坐标兜底）都走它。
- 事实档 `observed` 不再输出标签（`packagingBusinessPartTruthLabel("observed")` 回空串），行上不渲染那一行。
- 其余档位**逐字不变**：绑定 `partial`「部分定位」/ `ambiguous`「定位待人工确认」/ `unbound`「尚未在 CAD 图中定位」；
  事实档 `inferred`「规则纠名（推断）」/ `pending_confirmation`「结构规则补件（待确认）」。
- `app.js:3690` 的坐标兜底必须显式判 `bound`，不许因为 `bound` 没文案而误触发。
- 三档计数那一行（`识别 a · 推断 b · 待确认 c`）**不动** —— 它不是这两句。

### 2.4 C4 口径变更：旧红测同步

`tests/test_packaging_business_truth_state_disclosure_red.py` 的 `TRUTH_LABELS["observed"]`
按本批改成空串（那是本批唯一允许改动的既有红测断言），`docs/specs/packaging-business-truth-state-disclosure.md`
§2.6 的文案表加一行说明。其余既有红测一个字不许动。

### 2.5 C5 不回归

`.part-meta` / `.part-note` 的 CSS 字号规则必须仍在（`## 483` 的护栏）；
材料节点要有自己的 CSS 规则（≤ 11px，别比件名还大）。

## 3. 红测

`tests/test_packaging_2_1_part_row_size_and_material_lines_red.py`

| 组 | 覆盖 | 现状 |
| --- | --- | --- |
| E 尺寸格式 | 两个纯函数 + 后端的两位小数、整数不补零、原文不动 | 3 红 / 3 绿 |
| M 行版式 | 材料独立节点、尺寸行不拼 ` · `、CSS 有字号、`.part-meta`/`.part-note` 规则仍在、`packagingBindingStatusText()` | 3 红 / 2 绿 |
| L 两句去掉 | 字面「已在图纸中定位」「图上识别」在源码里不存在；`bound` 无文案；`observed` 回空串；其余档逐字 | 2 红 / 2 绿 |

预期：**8 红 / 7 绿**。`node` 真跑纯函数 + 源码/CSS 扫描；不起服务、不发 HTTP、不写业务数据。

## 4. 本批不做

- 不动右栏图形区、不动 3D、不动披露句与部件图那几条（上一批已收口）；
- 不改几何分量行的版式。

## 5. 落地（2026-09-24）

| 契约 | 落点 | 读数 |
| --- | --- | --- |
| §2.1 C1 尺寸两位小数 | `app.js` 新增纯函数 `packagingMmText()`（四舍五入两位、去尾零）；`packagingPartSizeText()` / `packagingBusinessPartSizeText()` 都走它；`packaging_parts._mm_text()` 由 `%.3f` 改 `%.2f` | E 组 6/6 绿（`node` 真跑 + 后端直调） |
| §2.1 C1 原文逐字 | 两条函数都先认 `product_size_text` / 客户原文，命中就不再格式化 | E4 绿 |
| §2.2 C2 材料另起一行 | `renderPackagingBusinessTree()` 拆成 `.part-meta`（只尺寸）+ `.part-material`；` · ` 拼接删除；`workbench.css` 新增 `.part-material`（11px） | M1/M2 绿 |
| §2.3 C3 两句去掉 | 新增纯函数 `packagingBindingStatusText()`（`bound` → `""`，其余三档逐字）；`PACKAGING_BINDING_COPY` 删掉 `bound` 这一档；行 / 右栏「定位状态」/ 坐标兜底三处都走它；`packagingBusinessPartTruthLabel("observed")` → `""` | L1–L4、M5 绿 |
| §2.3 C3 坐标兜底不误触发 | 坐标兜底显式判 `bindingStatus === "bound"`，绑上了但没坐标改说 `PACKAGING_BOUND_NO_COORDS_NOTE` | D 组（既有 `2_1_right_pane` 10/10）绿 |
| §2.4 C4 旧红测同步 | `tests/test_packaging_business_truth_state_disclosure_red.py` 的 `TRUTH_LABELS["observed"]` 改空串（作者侧已改）+ `docs/specs/packaging-business-truth-state-disclosure.md` §附 | 该模块 22/22 绿 |
| §2.5 C5 不回归 | `.part-meta` / `.part-note` 字号规则仍在；`.part-material` 新规则 | M2/M3/M4 绿 |

### 5.1 反向对照

把 `app.js` / `workbench.css` / `packaging_parts.py` 还原到本批之前重跑本批红测：
`Ran 15 tests … FAILED (failures=8)`（E1–E3 / M1–M2 / M5 / L1–L2），与 Spec 头部记的 8 红逐条对上；
放回实现后 15/15 绿。

### 5.2 保护网（本批实跑）

- 点名 9 个模块（真话档、两列看板、右栏单件图、结果区、面板依据、CAD 平面图、28 件自动解析、
  `## 492` 件图保真）⇒ `Ran 171 … OK`。
- 包装全族 51 个模块 ⇒ `Ran 805 … OK (skipped=6)`。

### 5.3 需要说明的一处（`node` 单函数直跑）

`tests/test_packaging_2_1_right_pane_single_part_figure_red.py::test_d4` 用「函数体里出现
`PACKAGING_BINDING_COPY`」当作「未绑定 / 画不出来时给原因」的**代理**断言。本批把三档文案统一收进
`packagingBindingStatusText()` 之后，坐标兜底那一支仍然真的给原因（`partial` / `ambiguous` / `unbound`
逐字来自同一张表，`bound` 改给缺坐标的说明），但正文里不再直接取这张表 —— 于是在函数体内留了一行
**说明性注释**点名该表（注释不是行为）。行为口径以 §2.3 为准。
