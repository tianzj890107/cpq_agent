# 规格：平面图按**实体折线**画（开口件不再只画包络框）

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§12 边界 4：平面图按分量 bbox 画、
不是逐段折线 —— 本批就是那句话的收口）、
`docs/specs/packaging-cad-plan-true-outline-polygons.md`（闭合件的真轮廓多边形：本批**不动**它的
优先级）、`docs/specs/packaging-cad-plan-drawing-coordinates.md`（`drawing_bbox` 与 y 轴翻转口径）、
`docs/specs/packaging-parts-true-outline.md`（第 1 层已把折线顶点落到 `attributes.points`）、
`docs/specs/packaging-silent-degradation-disclosure.md`（截断了就必须说出来）。

状态：Spec + 红测（已实现）（现状：`CAD IR` 的折线顶点早就在（`cad_ir/parser.py:342` 的
`attributes.points`、`:317-318` 的样条 `fit_points`、`:378-379` 的直线 `start`/`end`），但
`packaging_parts.extract()` **一件都不往零件文档里带**：`kept.append({...})`（`:1681`）里没有段，
`geometry_evidence_of()`（`:3251`）自然也没有 —— 于是前端
`packagingCadPlanComponentSvg()`（`app.js:1959`）对开口件只能画 `<rect>`（包络框）；真样本
`酒盒.dwg` 的 402 个分量里 400 个是开口件，平面图上看到的全是方框）
红测：`tests/test_packaging_cad_plan_polyline_segments_red.py`
行号基线：HEAD `2a87717`

## 0. 一句话目标

平面图里，凡是**件内有可用顶点坐标**的分量，就按实体的**顺序顶点连线**画折线（一段一条
`<polyline>`），不再只画包络框；闭合件的真轮廓多边形优先权不变；真的没有坐标才退回包络框。
段数 / 每段点数超上限时**截断并披露**（节点带 `data-segments-truncated`、面板给一句人话），
绝不让"画了一部分"看起来像"画全了"。

## 1. 现状缺口（代码级）

1. `packaging_parts.extract()` 的 `kept.append({...})`（`:1681`）不带任何顶点坐标 —— 只带
   `bbox` / `outline`（闭合环）；
2. `geometry_evidence_of()`（`:3251`）只透传 `drawing_bbox` 与 `outline_points`（且
   `outline_points` **仅闭合件**），所以前端拿不到开口件的任何点；
3. `packagingCadPlanComponentSvg()`（`app.js:1959`）的分支只有两支：闭合件 `<polygon>`、
   其余 `<rect>`。

## 2. 契约

### C1 新增纯函数 `packaging_parts._component_segments(members) -> {segments, segments_total, truncated}`

- 段来源**只有三种**（按 `entity_id` 升序、同一实体多点串只出一段；其余类型今天没有落坐标 →
  一段都不出，**不猜**、不用 `bbox` 编点）：
  1. 折线：`attributes.points`（≥ 2 点）；
  2. 直线：`attributes.start` + `attributes.end`（两点都给得出）；
  3. 样条：`attributes.fit_points`（≥ 2 点；**是拟合点的顺序连线，不是重新拟合的曲线**）；
- 坐标只认有限数（`(x, y)` 两元组/两元素列表）；有任何一点不可用 → 这一段整段丢掉；
- 上限与截断（**两个都是常量**）：`PLAN_SEGMENT_MAX = 24`（段数）、
  `PLAN_SEGMENT_POINTS_MAX = 64`（每段点数）：
  - 段内点数 > 64 → **均匀抽稀**到 64（首尾必留，索引 = `round(i * (n - 1) / 63)`），`truncated: True`；
  - 段数 > 24 → 取**前 24 段**（按 C1 的确定顺序），`truncated: True`；
  - 两者都没触发 → `truncated: False`；
- `segments_total` = **截断前**的可用段数（不是点数）；
- 纯函数：无 IO、确定性、不抛错（`members` 不是列表 / 元素不是 dict 一律按"没有"处理）。

### C2 `extract()` 的行与 `geometry_evidence_of()` 的透传

- `extract()` 的每个 kept 行加三键：`segments`（C1 的段串）、`segments_total`、`segments_truncated`；
- `geometry_evidence_of()` 的每个分量加同样三键（`segments` 缺省 `[]`、
  `segments_total` 缺省 `0`、`segments_truncated` 缺省 `False`）；
- 该文档里**别的键一个字不动**（`drawing_bbox` / `outline_points` / 尺寸 / 角色 / 层都不变）。

### C3 前端纯函数 `packagingCadPlanSegmentPolylines(component) -> string[]`

- 落点：`tech_app/frontend/app.js` **顶层**（可被 `node -e` 抽出来真跑）；
- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`；
- 每个可用段 → 一串 `x,-y`（平面图的 viewBox 已经翻过 y 轴，与
  `packagingCadPlanOutlinePoints()` 同口径）；不足 2 点、或任一点不可用的段**丢掉**；
- 一段都没有 → `[]`；任何输入都不抛错。

### C4 前端纯函数 `packagingCadPlanTruncationNote(component) -> string`

- `segments_truncated` 非真 → `""`；
- 否则 → `这一件的折线被截断（原 <segments_total> 段，图上 <已画段数> 段），形状仅供定位。`
  （`已画段数` = C3 的结果长度；`segments_total` 取不到时用已画段数，**不写 0**）；
- 落点与纪律同 C3。

### C5 `packagingCadPlanComponentSvg()` 的三段优先

- 顺序固定：闭合件的真轮廓 `<polygon>`（`packagingCadPlanOutlinePoints()`，**一个字不改**）→
  C3 的折线（每段一条 `<polyline>`，**共用同一条 data 属性串**，
  另加 `data-segment="<序号>"` 与 `data-segments-truncated="1|0"`）→ 包络 `<rect>`（原样兜底）；
- `packagingBusinessPartOutlineHtml()` 的 `<svg>` 之后**追加** C4 的那句话（有关联件截断时；
  没有就一个字不加）；
- 高亮 / 缩放 / 点选那套 `data-*`（`data-component-id` / `data-component-ref` /
  `data-business-part` / `data-bbox` / `data-layer` / `data-role`）在折线上**逐字同形** ——
  点一条折线必须和点方框一样能选中这一件。

### C6 冻结面

- 闭合件的 `<polygon>` 优先权不变（有环就画环，不许被折线顶掉）；
- `packagingCadPlanOutlinePoints()` / `packagingCadPlanComponentBox()` /
  `packagingCadPlanViewBox()` / `packagingCadPlanRange()` / `fitPackagingCadPlan()` /
  `highlightPackagingBusinessPart()` 一字不动；
- 后端 `extract()` 的尺寸 / 角色 / 过滤 / 材料归属口径一字不动；`cad_ir/parser.py` 一行不改；
- 不改任何既有断言 / 期望值（唯一允许的既有测试改动见 §3 第 3 条：只加夹具依赖名）。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：新增两个常量 + `_component_segments()`，
   `extract()` 的 kept 行加三键，`geometry_evidence_of()` 透传三键；
2. `tech_app/frontend/app.js`：新增两个顶层纯函数，`packagingCadPlanComponentSvg()` 的第三支，
   `packagingBusinessPartOutlineHtml()` 的截断那句话；
3. `tests/test_packaging_business_part_plan_click_and_bound_outline_red.py`：**只把本批两个新函数名
   加进那段 node 抽函数的依赖清单**（`deps` 数组）—— 该清单是测试**夹具**不是断言，
   本批一条断言都没动（`packagingCadPlanComponentSvg()` 多了一支，夹具要能把它跑起来）；
4. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把 `bbox` 拆成点冒充折线（没有坐标就是没有）；
- 不许在浏览器里拟合曲线 / 求交 / 算几何（前端只做"把后端给的点串成折线"）；
- 不许为了好看把截断藏起来（`segments_truncated` 必须同时进数据与界面）；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cad_plan_polyline_segments_red -v
node --check tech_app/frontend/app.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_plan_click_and_bound_outline_red \
  tests.test_packaging_cad_plan_true_outline_polygons_red \
  tests.test_packaging_cad_plan_drawing_coordinates_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_outline_red tests.test_packaging_parts_components_red \
  tests.test_packaging_parts_extraction_red
```

## 6. 已记录的边界

1. 段只来自**已经落盘的顶点坐标**（折线 / 直线 / 样条拟合点）：圆、弧、椭圆、填充今天在 IR 里
   只有半径 / 角度 / 计数，没有顶点 → 这些件仍然画包络框（`geometry_evidence` 不带段），
   这是事实不是漏；
2. 上限按"每件 ≤ 24 段 × 每段 ≤ 64 点"算，真样本 64 件的上限约 25k 点 —— 文档体积有界，
   超上限的件**截断 + 披露**，不静默；
3. 闭合件同时带环点与段（环点优先画）；本批不改闭合件那条路；
4. 平面图里**段与段之间的空隙**（实体本来就不相连）如实画出来 —— 不许在浏览器里把端点接起来
   冒充闭合环（那正是 `packaging-parts-true-outline.md` 反复强调的"别编"）。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cad_plan_polyline_segments_red
# 实现前：Ran 23 tests … FAILED (failures=19)   ← 19 红（A1–A7 / B1–B3 / C1–C6 / D1/D2/D4）
# 实现后：Ran 23 tests … OK                     ← 4 条护栏（B4 / D3 / D5 / D6）红基即绿
node --check tech_app/frontend/app.js           # OK
```

| 契约 | 落点 |
| --- | --- |
| §C1 纯函数 | `packaging_parts.py:61 PLAN_SEGMENT_MAX = 24` / `:62 PLAN_SEGMENT_POINTS_MAX = 64` / `:452 _segment_of()`（折线 `attributes.points` → 直线 `start`+`end` → 样条 `fit_points`；坐标不齐整段丢）/ `:483 _decimate()`（均匀抽稀、首尾必留）/ `:491 _component_segments()`（`entity_id` 升序、段数 24 / 每段 64 两个上限、`segments_total` 记截断前段数） |
| §C2 行与证据 | `extract()` 的 kept 行加三键（`:1681` 一带）、文档行透传（`:1844`）、`geometry_evidence_of()`（`:3319`）每个分量带 `segments` / `segments_total` / `segments_truncated`（缺省 `[]` / `0` / `False`）；其余键一个未动 |
| §C3/§C4 前端纯函数 | `app.js:1965 packagingCadPlanSegmentPolylines()`（每段一串 `x,-y`，不可用段整段丢）/ `:1984 packagingCadPlanTruncationNote()`（没截断返回 `""`） |
| §C5 三段优先 | `app.js:1993 packagingCadPlanComponentSvg()`：`<polygon>`（环点）→ `<polyline>`（每段一条、data 属性与方框同形 + `data-segment` + `data-segments-truncated`）→ `<rect>`（兜底）；`app.js:1886 packagingBusinessPartOutlineHtml()` 在 `<svg>` 后追加截断那句话（去重、`data-qqOutlineTruncated="1"`） |
| §C6 冻结面 | `packagingCadPlanOutlinePoints()` / `packagingCadPlanComponentBox()` / `packagingCadPlanRange()` / `packagingCadPlanViewBox()` / `fitPackagingCadPlan()` / `highlightPackagingBusinessPart()` 体内都不含 `segments`；`cad_ir/parser.py` 一行未改；闭合件的 `<polygon>` 优先权不变 |

唯一改到的既有测试：`tests/test_packaging_business_part_plan_click_and_bound_outline_red.py` 的
`deps` 数组加两个函数名（夹具依赖，**断言一字未动**）—— 见 §3 第 3 条。

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_plan_click_and_bound_outline_red \
  tests.test_packaging_cad_plan_true_outline_polygons_red \
  tests.test_packaging_cad_plan_drawing_coordinates_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_outline_red tests.test_packaging_parts_components_red \
  tests.test_packaging_parts_extraction_red
  → Ran 119 … OK (skipped=1)

./open-claude/.venv/bin/python -W ignore -m unittest discover -s tests -p 'test_packaging_*.py'
  → Ran 2058 … FAILED (failures=5, skipped=8)   ← 仍是那 5 条既有挂账，本批未引入新红
```

未连 PG / 34、未写生产数据、未调模型、未 push / MR / tag / Release / 未部署。
