# 平面图点业务部件要开业务部件面板；业务部件面板要画出**绑定分量**的形状

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（业务部件层、平面图交互、
`data-business-part` 归属）、`docs/specs/packaging-cad-plan-true-outline-polygons.md`
（闭合件画多边形、开口件画包络矩形；形状渲染与取框入口）。

状态：Spec + 红测（已实现）（点选分流走纯函数；面板画绑定分量的形状；15 条红测转绿）
**2026-09-23 变更**：§C2「面板画绑定分量的形状」已被
`docs/specs/packaging-2-1-right-pane-single-part-figure.md`（右栏只画「这一件」的图）取代 ——
右栏那块图改成这一件的全部图元（按图层着色、可拖拽缩放），`packagingBusinessPartOutlineHtml`
作为纯函数保留但不再是右栏那块图的来源；本文件 §C1 / §C3 不变。
红测：`tests/test_packaging_business_part_plan_click_and_bound_outline_red.py`

## 1. 目标与验收主路径

1. 在 2.1 平面图上点一个**已绑定业务部件**的图元 → 打开该业务部件面板（今天走的是几何零件
   通道：拿业务编码 `JWXR21-P03` 去查 `/packaging-parts/{code}`，必然 404，右栏只剩一句
   报错文案）。
2. 业务部件面板的轮廓区**画出它绑定的几何分量的形状**（闭合件真轮廓、开口件包络），并如实标注
   这是"绑定分量的形状、业务尺寸以权威资料为准"（今天这里只有一句 `PACKAGING_BINDING_COPY`
   的状态文字，永远看不到形状）。
3. 形状只来自**已加载**的平面图文档（`currentPackagingCadPlan.geometry_evidence.components`）：
   不在浏览器端算几何、不新开接口、不复制第二套渲染。

## 2. 契约

### C1 点选落点按"有没有业务归属"分流

平面图的点选落点由纯函数 `packagingCadPlanClickTarget(businessPartCode)` 决定：

| 输入 | 输出 |
| --- | --- |
| 非空字符串（去掉首尾空白后仍非空） | `{ kind: "business", code: <去空白后的编码> }` |
| 空 / 未定义 / 纯空白 | `{ kind: "unbound", code: "" }` |

`renderPackagingCadPlan()` 的点击处理必须用它：`kind == "business"` → `openPackagingBusinessPart(code)`；
`kind == "unbound"` → 既有 `notePackagingPartPanel(PACKAGING_CAD_PLAN_UNBOUND)`（未归属图元的提示不变）。
**不许**再拿 `data-business-part` 的值去调 `selectPackagingPart()`（那是几何零件通道）。

### C2 业务部件面板画绑定分量的形状

两个纯函数：

- `packagingBusinessPartComponents(binding, components)` → 按 `binding.component_ids` 从证据层
  分量里**按 `component_id` 精确取**（顺序按证据层顺序，去重），取不到返回 `[]`；
- `packagingBusinessPartOutlineHtml(binding, doc)` → 用 C2 取到的分量，复用平面图已有的
  `packagingCadPlanComponentBox()`（取框）与 `packagingCadPlanComponentSvg()`（闭合画多边形、
  开口画矩形）与 `packagingCadPlanViewBox()` 拼一个只读 `<svg>`；取不到分量、没有坐标、
  或一件都画不出来时返回**空串**。

`openPackagingBusinessPart()` 的轮廓区：`packagingBusinessPartOutlineHtml(...)` 非空 → 渲染它，
并在下面加一行常量 `PACKAGING_BOUND_OUTLINE_NOTE` 的说明；为空 → 保持既有
`PACKAGING_BINDING_COPY[status]` 的状态文案（不留白、不假装有形状）。

### C3 冻结面

- 绑定判据/状态机/容差/原因码、`bbox` 与 `drawing_bbox` 语义、`outline_points` 的产出不动；
- 几何零件通道（`selectPackagingPart()` / 右栏零件面板 / 单件详情接口）一个字不改；
- 平面图的其余交互（整张图 viewBox、缩放、按绑定分量高亮、未归属提示、点选反查）不动；
- 新增的纯函数不许引用 `document` / `sessionStorage` / `window`（要被 node 直接执行）；
- 不新增接口、不新增依赖、不在浏览器端做几何求解。

## 3. 允许修改范围

`tech_app/frontend/app.js` 一个文件：新增 `packagingCadPlanClickTarget()` /
`packagingBusinessPartComponents()` / `packagingBusinessPartOutlineHtml()` 与常量
`PACKAGING_BOUND_OUTLINE_NOTE`；改 `renderPackagingCadPlan()` 的点击分支与
`openPackagingBusinessPart()` 的轮廓区。

后端、`main.py`、`index.html`、样式文件本批都不改。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测），不许放宽断言让它变绿；
- 不许把业务编码当成几何零件编码去查（今天的 404 就是这条）；
- 不许给没有绑定的业务部件画任何形状，也不许用权威尺寸在浏览器端造一个矩形冒充几何；
- 不许改几何零件面板的三态文案口径（`packaging-parts-selectable-panel.md`）；
- 不许改样式表与设计变量、不许连生产库、不许新增依赖。

## 5. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_part_plan_click_and_bound_outline_red -v
node --check tech_app/frontend/app.js
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cad_plan_true_outline_polygons_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cad_plan_drawing_coordinates_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_panel_red
```

## 6. 现状缺口（源码实测，不是推断）

- `renderPackagingCadPlan()` 的点击处理：`const owner = node.getAttribute("data-business-part") || "";`
  → `if (owner) { selectPackagingPart(owner); return; }` —— `data-business-part` 装的是**业务部件编码**
  （`## 368` 的文档口径），而 `selectPackagingPart()` 走的是
  `GET .../requirement/packaging-parts/{code}`（几何零件文档），业务编码在那里不存在 →
  必然 404，右栏只显示"读取零件详情失败"。
- `openPackagingBusinessPart()` 的轮廓区只写一句状态文案：
  `outlineHost.innerHTML = '<div class="view-3d-placeholder">' + PACKAGING_BINDING_COPY[status] + '</div>'`
  —— 业务部件即使绑定了闭合件（真轮廓点已经随 `## 372` 进了证据层、平面图文档也已加载），
  面板里也永远看不到形状。

## 7. 与既有 Spec 的关系（不重复立第二套）

- 平面图交互（整张图的 viewBox/缩放/高亮/未归属提示）以
  `packaging-business-parts-and-cad-plan-view.md` §6.2 为准；本批只改"点到的图元属于业务部件时开哪个面板"。
- 形状渲染（闭合件多边形、开口件包络、取框入口）以
  `packaging-cad-plan-true-outline-polygons.md` §C2 与 `packaging-cad-plan-drawing-coordinates.md` §C3 为准；
  本批复用同一套函数，不新写渲染。
- 业务部件的权威资料展示（尺寸/材料/排版/工艺/备注/映射状态）以 `## 368` 的落点为准，本批只补轮廓区。

## 8. 落地状态（2026-09-22）

**落点**（只 `tech_app/frontend/app.js`）

| 落点 | 内容 | 契约 |
| --- | --- | --- |
| `packagingCadPlanClickTarget(businessPartCode)`（新，纯函数） | 去空白后非空 → `{kind:"business", code}`；空 → `{kind:"unbound", code:""}` | C1 |
| `renderPackagingCadPlan()` 点击分支 | `kind == "business"` → `openPackagingBusinessPart(code)`；否则既有 `notePackagingPartPanel(PACKAGING_CAD_PLAN_UNBOUND)`；删掉 `selectPackagingPart(owner)`（业务编码不再进几何零件通道） | C1 |
| `packagingBusinessPartComponents(binding, components)`（新，纯函数） | 按 `binding.component_ids` 从证据层按 `component_id` 精确取（顺序跟证据层），取不到回 `[]` | C2 |
| `packagingBusinessPartOutlineHtml(binding, doc)`（新，纯函数） | 复用 `packagingCadPlanComponentBox()` / `packagingCadPlanComponentSvg()` / `packagingCadPlanViewBox()` 拼只读 `<svg>`；没绑定 / 没坐标 / 拼不出 → 空串 | C2 |
| `PACKAGING_BOUND_OUTLINE_NOTE`（新常量） | 「这是绑定分量的形状；业务尺寸以权威资料为准。」 | C2 |
| `openPackagingBusinessPart()` 轮廓区 | 有形状 → SVG + 上面那句说明；画不出来 → 既有 `PACKAGING_BINDING_COPY[status]` 状态文案 | C2 |

后端、`main.py`、`index.html`、样式文件本批都没改；绑定判据 / 尺寸 / 证据层键一个没动。

**判定口径**：点谁开谁只按"这个图元有没有业务归属"分流；面板里的形状一律来自**已加载**的平面图
文档（`currentPackagingCadPlan.geometry_evidence.components`），不在浏览器端算几何、不给未绑定的
业务部件造形状。新函数都不含 `document`/`sessionStorage`/`window.`/`fetch(`，红测里用 node 真跑。

**复跑**

```
tests.test_packaging_business_part_plan_click_and_bound_outline_red  → Ran 15 OK
（实现前同一条命令：Ran 15, failures=11 —— A 组 3 红、B 组 2 红、C 组 5 红、D 组护栏 1 红）
node --check tech_app/frontend/app.js                                → 通过
本次相邻 8 个模块（面板/零件/平面图/业务部件/图纸两列）合计            → Ran 125 OK
tests/test_packaging_*.py 全域（86 个模块）→ Ran 1589, failures=5, skipped=8
（5 条仍是 B3 / B4 / A2 / F2 / C1 那批既有挂账，与本批无关）
```

**为什么 D 组那条护栏也红**：它要求三个新纯函数都不引用 DOM/全局，实现前函数还不存在 ——
补上函数体后它自然转绿（不是放宽断言）。

## 9. 已记录的边界

1. 画的是**绑定分量**的形状，不是"业务部件自己的图纸"：业务部件本身没有几何，只有权威尺寸与资料；
   面板上必须写明这一点（`PACKAGING_BOUND_OUTLINE_NOTE`）。
2. 未绑定（`unbound`）或证据层没有坐标时，面板仍是状态文案 —— 不画、不猜、不在前端造形状。
3. 一个业务部件绑了多个分量（真样本上并列歧义件常绑几十个同尺寸分量）时，面板会把它们都画出来；
   本批不做"挑一个代表"的收敛（那是 `packaging-business-parts-binding-size-source.md` §9 边界 1
   记的业务口径）。
