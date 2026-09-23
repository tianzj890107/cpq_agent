# Spec：2.1 左栏零件行版式（标题一行 / 尺寸另起一行 / 不出卡片）+ 右栏零件形状可缩放拖拽 + 右栏整块可滚

状态：Spec + 红测（已实现）（2026-09-23 由并行会话落地：`app.js` 的 `packagingPartShapeNextState` / `packagingPartShapeZoomClamp` / `packagingPartShapeTransformCss` / `packagingPartShapeZoomLabel` / `bindPackagingPartShapeInteractions` 与 `PACKAGING_PART_SHAPE_VIEWPORT_CLASS`，`workbench.css` / `drawing-flow.css` 的视口与单一滚动容器规则；本机实测 `Ran 32 … OK`）
红测：`tests/test_packaging_2_1_parts_row_layout_and_shape_viewport_red.py`
血缘：`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md`（点一件直接看样子、三态与 `data-qq-part-shape`）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§6 右栏面板与 CAD 平面图）、
`docs/specs/packaging-business-part-size-cost-entry.md`（业务部件行上的尺寸与动作字号体系）。
本批 changelog 条目号：落地时按当时序号记（当前最大为 `## 482`）。
改动面：**只有前端**（`tech_app/frontend/app.js` + `tech_app/frontend/workbench.css` + `tech_app/frontend/drawing-flow.css`）。
不动后端、不动接口、不动任何数据口径。

## 0. 用户原话（2026-09-23）

> 现在零件清单里面文字没渲染 应该标题一行然后尺寸换行 注意文字大小不要超出来卡片
> 然后右边显示的这个零件本身也没有做缩放或者拖拽功能看起来有点奇怪
> 然后滚动应该是图片也能向上滚动，就是右侧看板应该全都能滚动，现在除开零件视图下面只剩一点点能滚动了
> 这些应该都是前端修改对吧

## 1. 实测证据（HEAD `872898b` 工作副本只读；纯静态 + `grep`）

| 读数 | 实测 |
| --- | --- |
| `app.js::renderPackagingBusinessTree()`（`:3251-3255`） | 业务部件行的 `innerHTML` 把四个块**平铺**在同一层：`<div class="part-icon">` + `<div class="part-name">` + `<div class="part-meta">` + `<div class="part-note">`（+ 行尾 `构成` 按钮） |
| 对照 `app.js::renderNode()`（`:4694-4699`） | 视觉 IR 那条路是**对的**：`.part-icon` + `.part-info{flex:1;min-width:0}` 包住 `.part-name` / `.part-type` / `.recommend` —— 版式靠容器，不靠平铺 |
| `workbench.css:46` | `.part-item{display:flex;align-items:center;gap:var(--space-md);…}` —— 行是**单行 flex**，四个块全挤在一行：编号+件名、尺寸+材料、绑定状态、`构成` 按钮 |
| `workbench.css:46` | `.part-name{font-size:12px}`、`.part-info{flex:1;min-width:0}` 都在；**全库没有任何一条 `.part-meta` 规则** |
| `.part-meta` 现状 | `grep -rn "part-meta" tech_app/frontend/` 只有 `app.js:3253` 一处产出，**0 条 CSS** ⇒ 浏览器默认 `16px`，比同一行的件名（12px）还大 |
| 最长的一行文本（酒盒实测） | `展开 443.523×492.62 mm · DESIGN / SAMPLE` —— 16px 下必然撑破行宽 |
| `workbench.css:138` | `.drawing-parts-column{…;overflow-x:hidden}` ⇒ 撑破的行不是换行、也不是省略号，而是**直接裁掉**（用户看到的"文字没渲染"） |
| `drawing-flow.css:265` | `.packaging-business-part .part-note{font-size:11px}`（状态行字号是有的） |
| `app.js::openPackagingBusinessPart()`（`:3342-3350`） | `ready` 分支直接 `outlineHost.innerHTML = outlineHtml + note`：形状是一张**死图**，没有视口、没有事件、没有复位 |
| `drawing-flow.css:53-60` | `.packaging-part-outline{margin-bottom:10px}`、`.packaging-part-svg{width:100%;max-height:260px}` —— 只约束了静态展示 |
| `grep -n "pointerdown\|wheel\|ctrlKey" app.js` | **0 命中** ⇒ 全文件没有任何缩放 / 拖拽交互 |
| `app.js::packagingBusinessPartOutlineHtml()`（`:1992`） | `<svg class="packaging-part-svg" viewBox="${packagingCadPlanViewBox(range)}" preserveAspectRatio="xMidYMid meet">` |
| `workbench.css:139` | `.drawing-model-column{…;overflow:hidden}` —— 右栏**自己不许滚** |
| `workbench.css:239` | `.model-panes{display:flex;flex:1;min-height:0;…;overflow-y:auto}` —— 真正在滚的是它 |
| `drawing-flow.css:39-44` | `.packaging-part-panel{…;overflow:auto}` —— 面板**再套一层**滚动 |
| `workbench.css:312-316` | `[data-qq-fill]` 下的三条：`#packagingPartPanel{flex:1;min-height:0}` → `.packaging-part-outline{flex:1;…}` → `.packaging-part-outline svg{width:100%;height:100%}` ⇒ 形状块**吃掉全部剩余高度**，`overflow:auto` 的面板里只剩下面 `事实 / 实体证据 / BOM 业务角色` 那一点高度可滚 —— 这就是"除开零件视图下面只剩一点点能滚动了" |
| 三层嵌套滚动现状 | `列(overflow:hidden)` → `#modelPanes(auto)` → `#packagingPartPanel(auto)`：**三套滚动权、两条滚动条**，而图片被 `flex:1` 钉在中间，永远不参与滚动 |

## 2. 契约

### 2.1 C1：左栏业务部件行是「标题一行 / 尺寸一行 / 状态一行」，字号受控、不出卡片

- 行结构：`.part-icon` 之后必须有一个**文本容器** `.part-body`，`.part-name` / `.part-meta` /
  `.part-note`（以及 `构成` 按钮）都在它里面 —— 版式靠容器，不靠 `flex` 平铺，也不用 `<br>` 硬换行；
- 三行各自的字号（**闭集**，不许退回浏览器默认）：
  - `.part-name`（`DWG-P01 内盒1灰板`）= `12px`；
  - `.part-meta`（`展开 443.523×492.62 mm · DESIGN / SAMPLE`）≤ `11px`，且**必须有一条自己的 CSS 规则**
    （现状 0 条 ⇒ 16px）；
  - `.part-note`（绑定状态 / 事实档）≤ `11px`；
- 行允许纵向堆叠：`.part-item.packaging-business-part` 必须 `flex-wrap:wrap` 或 `flex-direction:column`
  （二选一），并且 `.part-body{flex:1;min-width:0}` —— 少了 `min-width:0`，flex 子项不会收缩，照样溢出；
- 文本不许溢出卡片：`.part-body`（或三行各自）必须有 `overflow-wrap:anywhere|break-word` 或
  `word-break:break-word|break-all`；**"被 `overflow-x:hidden` 裁掉"不算换行**；
- 每行给 `title`：完整 `编号 件名` + 完整尺寸串（裁不掉也hover得到）；
- 反向判据（现状即失败）：`.part-meta` 有规则（≥1 条）且 `font-size ≤ 11px`；
  `.part-item.packaging-business-part` 有堆叠声明；`.part-body` 有 `min-width:0` 且有换行声明。

### 2.2 C2：右栏零件形状是一块**可缩放 / 可拖拽 / 可复位**的视口（纯前端，不改 viewBox）

- 视口：`#packagingPartOutline[data-qq-part-shape="ready"]` 里的那张 svg 外面包一层
  `.packaging-part-shape-viewport`（裁剪 + 固定高度上限，见 C3）；`loading` / `unavailable` 两态
  **不出**视口、不出缩放控件（三态口径不动）；
- 交互只用原生事件，不引三方库：
  - **拖拽**：`pointerdown` → `pointermove` → `pointerup` / `pointercancel`；用 `setPointerCapture`，
    拖拽期间宿主标 `data-qq-shape-dragging="1"`，结束后清掉；
  - **缩放**：`wheel` **必须判 `ctrlKey`**（触控板捏合同样给 `ctrlKey`）；裸滚轮**不许**缩放 ——
    它得留给 C3 的整栏滚动；
  - **复位**：视口里有一颗 `id="packagingPartReset"` 的按钮（文案 `适应窗口`），点了回到初始态；
- 状态可读（现场 grep 得到"这块是哪来的"）：宿主上写 `data-qq-shape-zoom`（当前倍数）与
  `data-qq-shape-pan`（`tx,ty`）；
- 缩放/平移**只改 `transform`**，**不改 `viewBox`**、不重算几何：`viewBox` 仍由
  `packagingCadPlanViewBox(range)` 产出（前端不做几何求解的纪律不动）；
- 纯函数（可 `node -e` 抽出来真跑，不碰 DOM）：
  - `packagingPartShapeZoomClamp(k)` → 夹进 `[0.2, 8]`；非有限数 / 非数字 → `1`（算不出来就当没缩放）；
  - `packagingPartShapeNextState(state, action)` → 归一化 + 应用动作，返回新 state：
    - `state` 不是对象或 `k/tx/ty` 非有限 → 初始态 `{k:1,tx:0,ty:0}`；
    - `{type:"pan",dx,dy}` → `{k, tx+dx, ty+dy}`（`k` 不变；`dx/dy` 非有限按 0）；
    - `{type:"zoom",factor,at:{x,y}}` → `k2 = clamp(k*factor)`，**按实际生效的倍数** `r = k2/k` 挪锚点：
      `tx2 = x - (x - tx) * r`、`ty2 = y - (y - ty) * r`（夹到边界时锚点不许跳）；
    - `{type:"reset"}` → 初始态；其余动作 / 非对象动作 → 原样返回归一化后的 state；
  - `packagingPartShapeTransformCss(state)` → `translate(<tx>px, <ty>px) scale(<k>)`（各值保留两位小数、去掉多余 0）；
  - `packagingPartShapeZoomLabel(state)` → `<round(k*100)>%`（`1 → "100%"`）；
  - 这四个函数体内**不许**出现 `document.` / `window.` / `fetch(` / `localStorage` / `getBoundingClientRect`。

### 2.3 C3：右栏看板是**一个**滚动容器，图片跟着一起滚

- 包装 2.1（`.drawing-model-column[data-qq-fill]`）下，纵向滚动权只属于**列**：
  `overflow-y:auto`；
- 同时，内层的两处旧滚动权必须让出来（否则又是双滚动 + 图片上方那块"只剩一点点"）：
  - `[data-qq-fill] #modelPanes` → 不再是自己的滚动容器（`overflow:visible` / `overflow-y:visible`）；
  - `[data-qq-fill] #packagingPartPanel` → **不许**再 `flex:1`；
  - `[data-qq-fill] .packaging-part-outline` → **不许**再 `flex:1`；
  - `[data-qq-fill] .packaging-part-outline svg{height:100%}` → 删掉（svg 不许吃掉栏高）；
- 形状视口的高度是**上限**，不是"吃掉剩余"：`.packaging-part-shape-viewport{ height: min(46vh, 420px);
  overflow:hidden; touch-action:none; }`（数值可等价替换，但必须同时有：`overflow:hidden`、
  带 `vh` 或 `px` 的高度上限、`touch-action`）；
- 结果：滚轮在形状上（不带 `Ctrl`/`⌘`）滚的是**整栏**，`事实 / 实体证据 / BOM 业务角色` 全部随手往下走，
  不再被压成一条缝；
- 非包装项目一个字不受影响：这条巷子的所有覆盖都必须挂在 `[data-qq-fill]` / `[data-qq-no-3d]` 上。

### 2.4 C4：本批明确不动的东西（护栏）

- 三态钩子 `data-qq-part-shape="ready|loading|unavailable"` 与加载态文案
  `正在读取这一件的形状…` 逐字不变；
- 结果口径那句 `业务部件 ${rows.length} 件（…）`、展开开关 `data-qq-part-toggle` 不变；
- 形状数据来源不变（后端坐标）；前端**不**做几何求解、**不**改 `viewBox`；
- `.view-3d-content` 的基础规则不许出现 `display:none`（撤 3D 只在包装巷子，靠 `[data-qq-no-3d]`）。

## 3. 本批不做

- 不给形状加旋转 / 测量 / 标注 / 导出（本批只解决"看不清"和"滚不动"）；
- 不改 `packaging-part-panel` 里的 `事实 / 实体证据 / BOM 业务角色` 内容与顺序；
- 不改任何后端接口、不改 `packaging-parts` 文档结构、不改 CAD IR；
- 不为 3D 链路（非包装）加缩放拖拽。

## 4. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_2_1_parts_row_layout_and_shape_viewport_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_2_1_result_parts_and_shape_only_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red -v
```

真样本人工复核（可选，本批不做红线）：`酒盒.dwg` 解析后左栏每行"标题一行、尺寸另起一行"，
右栏点一件能拖能缩、滚轮能把整栏（含图）滚上去。

## 5. 红测清单（`tests/test_packaging_2_1_parts_row_layout_and_shape_viewport_red.py`）

- A 组（左栏版式）：A1 `.part-body` 容器；A2 `.part-meta` 有规则且 ≤11px；A3 三行字号闭集；
  A4 `.part-body{min-width:0}`；A5 行可堆叠；A6 换行/断词声明；A7 行有 `title`；A8 口径护栏；
- B 组（形状视口纯函数）：B1 缩放夹取；B2 归一化；B3 平移；B4 锚点缩放；B5 夹边界时锚点不跳；
  B6 复位；B7 transform / label 文案；B8 纯函数纪律；
- C 组（交互接线）：C1 视口与复位按钮存在且 `ready` 分支装上交互；C2 事件闭集；
  C3 `setPointerCapture`；C4 裸滚轮不缩放（必须判 `ctrlKey`）；C5 状态钩子；C6 拖拽态钩子；
- D 组（单一滚动容器）：D1 列 `overflow-y:auto`；D2 面板不再 `flex:1`；D3 形状块不再 `flex:1`；
  D4 形状 svg 不再 `height:100%`；D5 视口三件套（裁剪 / 高度上限 / `touch-action`）；
  D6 `#modelPanes` 在 fill 下让出滚动权；
- E 组（护栏）：E1 三态值；E2 加载文案；E3 `.view-3d-content` 基础规则未被改成 `display:none`；
  E4 `viewBox` 仍来自 `packagingCadPlanViewBox(`。

纪律：本红测只读源码 / CSS + `node -e` 抽纯函数真跑；不起服务、不发 HTTP、不连 PG / 34、不写数据。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。

## 5.1 本批对红测文件的一处授权修改：只修序列化，断言一字不动（2026-09-23）

`B1` 的第三条参数是 `float("nan")`，它**过不了 JSON**：`json.dumps` 会把它写成裸 `NaN`
（不是合法 JSON），node 的 `JSON.parse` 直接抛 `SyntaxError` —— 这条用例在**调用被测函数之前**
就失败了，跟实现一点关系没有，32 条里必然留 1 条红（红基 26 FAIL 里就含着它）。

授权改**测试侧取值管道**的三处（断言文本、用例参数、期望值全部一处不动）：

1. `run_cases()` 前置一个 `json_safe()`：`float("nan")` → 哨兵串 `"__NaN__"`（其余值原样）；
2. `EXTRACT_JS` 的 `JSON.parse` 加 reviver：`"__NaN__"` → 真 `NaN`；
3. `EXTRACT_JS` 造调用时改用 `argText()`：`NaN` 写成**字面量**（`JSON.stringify(NaN)` 会变
   `null`，那样测的就是 `null`、不是 `NaN`）。

反向验证（见 §5.2 第 4 行）：把 `packagingPartShapeZoomClamp()` 的非有限数分支从 `1` 改成
`0.3` ⇒ `B1` 单条转红、其余 31 条不动 ⇒ 这条用例真的在跑 `NaN`，不是被绕过或放宽。

## 5.2 反向对照（删掉一处目标改动 ⇒ 只让对应那一条红；跑完还原 + `md5` 核对）

| 反向改动 | 实测 | 还原核对 |
| --- | --- | --- |
| 删掉 `openPackagingBusinessPart()` ready 分支里的 `bindPackagingPartShapeInteractions(outlineHost);` | `Ran 32 … FAILED (failures=1)`，唯一红 = `C1` | `app.js` `md5 edba7257800cf3c1d3d29aa5c5fe16df` 一致 |
| 删掉 `workbench.css` 的 `.part-item.packaging-business-part .part-meta{font-size:11px;…}` | `Ran 32 … (failures=1)`，唯一红 = `A2` | `workbench.css` `md5 b2b859a7f8659118476213f8890d85e9` 一致 |
| `[data-qq-fill] .packaging-part-outline{min-height:0}` 改回 `{flex:1;min-height:0}` | `Ran 32 … (failures=1)`，唯一红 = `D3` | `workbench.css` 同上一致 |
| `packagingPartShapeZoomClamp()` 的非有限数分支 `1` → `0.3` | `Ran 32 … (failures=1)`，唯一红 = `B1` | `app.js` 同上一致 |

不在这张表里的红线（`loading` / `unavailable` 两态不出视口、`viewBox` 仍来自后端、裸滚轮不缩放）
由 `C1` / `E4` / `C4` 直接钉住，改动前后都是绿的 —— 它们防的是"越改越多"，不是本批的验收目标。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 条目 `## 483`）

只改前端三个文件，后端 / 接口 / 数据口径一个字未动。

| 契约 | 落点 | 实测 |
| --- | --- | --- |
| C1 左栏行 | `app.js::renderPackagingBusinessTree()`：`.part-icon` 之后是 `.part-body`，`.part-name` / `.part-meta` / `.part-note` 都在里面；行上给 `title="编号 件名 尺寸"`（`esc()` 不管引号，属性值再补一层 `&quot;`）；`workbench.css` 末尾加 `.part-item.packaging-business-part{flex-wrap:wrap;align-items:flex-start;row-gap:2px}` / `.part-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;overflow-wrap:anywhere;word-break:break-word}` / `.part-meta{font-size:11px}`（原来**一条都没有** ⇒ 16px）/ `.part-note{font-size:11px}` | A1–A8 全绿 |
| C2 视口与交互 | `app.js::openPackagingBusinessPart()` 的 `ready` 分支把形状包进 `.packaging-part-shape-viewport` + 右上角 `#packagingPartReset`（`适应窗口`）与倍数标签，再调 `bindPackagingPartShapeInteractions(outlineHost)`；`loading` / `unavailable` 两态原样（不出视口、不出控件）。`app.js` 末尾新增 `packagingPartShapeZoomClamp` / `packagingPartShapeNextState` / `packagingPartShapeTransformCss` / `packagingPartShapeZoomLabel`（每个函数体**自带**它要的那点数学：红测是"单函数抽出来 `eval`"真跑，引用模块级常量就会 `not defined`）+ `packagingPartShapeNumberText` + `bindPackagingPartShapeInteractions`（`pointerdown`/`pointermove`/`pointerup`/`pointercancel` + `setPointerCapture`；`ctrlKey` 的 `wheel` 才缩放、裸滚轮放行；视口上写 `data-qq-shape-zoom` / `data-qq-shape-pan`，拖拽期 `data-qq-shape-dragging`）；缩放平移**只**写 `transform` | B1–B8 / C1–C6 / E4 全绿 |
| C3 单一滚动容器 | `workbench.css`：`[data-qq-fill]` 下新增列 `.drawing-model-column{overflow-y:auto;overflow-x:hidden}`（原来 `overflow:hidden`）、`#modelPanes{overflow-y:visible}`（原来 `auto`），`#packagingPartPanel` 与 `.packaging-part-outline` 撤掉 `flex:1`，删掉 `.packaging-part-outline svg{height:100%}`；`drawing-flow.css` 新增 `.packaging-part-shape-viewport{height:min(46vh,420px);overflow:hidden;touch-action:none}` + 视口内 svg 撑满 + 右上角倍数/复位条 | D1–D6 全绿 |
| C4 护栏 | 三态钩子 `data-qq-part-shape` 与 `正在读取这一件的形状…` 逐字保留；`业务部件 ${rows.length} 件（` 与 `data-qq-part-toggle` 未动；`.view-3d-content` 基础规则未动（撤 3D 仍只在 `[data-qq-no-3d]`） | A8 / E1 / E2 / E3 全绿 |

复跑口径（本机 `./open-claude/.venv/bin/python -W ignore -m unittest`）：

- `tests.test_packaging_2_1_parts_row_layout_and_shape_viewport_red` → `Ran 32 tests … OK`（红基 `Ran 32 … FAILED (failures=26)`）；
- 保护网一（前端 91 模块）→ `Ran 1485 tests … OK (skipped=4)`；保护网二（包装 166 模块）→ `Ran 2802 tests … OK (skipped=12)`；
- 点名复跑：`test_packaging_2_1_result_parts_and_shape_only_red` / `test_packaging_business_parts_and_cad_plan_view_red` / `test_packaging_authority_disclosure_on_read_red` / `test_packaging_parts_3d_red` / `test_packaging_business_part_plan_click_and_bound_outline_red` / `test_packaging_cad_plan_polyline_segments_red` / `test_packaging_parts_downstream_red` / `test_packaging_parts_panel_red` 等 14 个模块 → `Ran 267 … OK`；
- `node --check tech_app/frontend/app.js` 通过；`git diff --check` 干净。
