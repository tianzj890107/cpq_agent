# 业务部件的 CAD 平面图必须真的画得出来：证据层要把**绘图坐标**带出来

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（平面图查看器与
`geometry_evidence` 的形状）、`docs/specs/packaging-business-parts-binding-size-source.md`
（`bbox` 键的绑定语义：那一步刚把它定为"件尺寸的兜底来源"，本批**不动**它的语义）。

状态：Spec + 红测（已实现）（证据层带 `drawing_bbox`，平面图按它画矩形；真样本 263/263 有坐标）
红测：`tests/test_packaging_cad_plan_drawing_coordinates_red.py`

## 1. 目标与验收主路径

1. 2.1 左栏点业务部件 → 右栏「CAD 平面图」**真的画出图元**（今天恒空白：按分量 bbox 画，
   而这个键在真实文档里永远是 `null`）。
2. 绘图坐标与绑定尺寸**分成两个键**：`drawing_bbox` 只给画图，`bbox` 仍是绑定判据的兜底来源
   （`## 370` 刚定的语义），不因为画图而把未确认单位的原始坐标喂进绑定。
3. 一个分量都没有坐标时不许留白：页面要给一句说得清的空态文案（不是加载中，也不是"没有图元"）。

## 2. 契约

### C1 证据层每件带绘图坐标

`geometry_evidence_of(parts_doc)` 产出的每个 component 新增：

| 键 | 含义 |
| --- | --- |
| `drawing_bbox` | 该分量在**图纸坐标系**里的包络 `[minX, minY, maxX, maxY]`，取自零件行的 `outline.bbox`（闭合件取环的包络、开口件取分量包络）；拿不到给 `null` |

既有键逐字不动：`component_id` / `entity_ids` / `bbox` / `layers` / `role` /
`geometry_component_ref` / `unfolded_length_mm` / `unfolded_width_mm` / `outline_status` /
`size_source` / `area_mm2`；`limit` 分片与两笔总数（`component_total` / `kept_component_total`）
也不变。

### C2 `bbox` 的语义不变（不许被绘图坐标污染）

`bbox` 仍是**绑定**判据的兜底尺寸来源：真实 `parts` 行没有这个键 → 证据层给 `null`。
**不许**把 `drawing_bbox` 写进 `bbox`：单位没确认（`unit_status != "confirmed"`）的行只能拿到
`null`，否则绑定会拿未确认单位的原始坐标去对业务尺寸（`## 370` Spec §C1 明令禁止）。

### C3 平面图按 `drawing_bbox` 画（一个入口）

前端只允许一个"取这一件的画图框"的入口
`packagingCadPlanComponentBox(component)`：**先** `drawing_bbox`、**再** `bbox` 兜底、都没有返回 `null`。
`packagingCadPlanComponentSvg()` 与 `renderPackagingCadPlan()` 的范围计算都必须走它；
`packagingCadPlanViewBox()` 的 y 轴翻转、`fitPackagingCadPlan()` 的缩放、
`highlightPackagingBusinessPart()` 的按绑定分量高亮/缩放全部不变。

绘制粒度仍是**一件一个矩形**（不是逐段折线）：图元级坐标要等 CAD IR 把折线顶点带进证据层，
本批只补"包络从哪来"，不改渲染粒度（`## 368` §12 边界 4 的后半句仍然有效）。

### C4 有图元但一个坐标都没有时给空态

`renderPackagingCadPlan()` 在"有分量、但 `packagingCadPlanComponentBox()` 全为空"时必须显示
常量 `PACKAGING_CAD_PLAN_NO_COORDS` 的文案（页面不许留一块空白画布）；有坐标时行为不变。

### C5 冻结面

- 不改绑定判据、状态机、容差与原因码（`## 370` 的 `_component_size` / `BUSINESS_BINDING_REASONS` 不动）；
- 不改零部件的抽取、过筛、`outline_status`/`size_source` 口径；
- 不新增接口、不新增依赖、不改 `#viewer` / `initViewer()` / `loadSTL()` 的技术侧 3D 行为；
- 不改权限与路由形状（`GET .../requirement/packaging-geometry` 与入参不变）。

## 3. 允许修改范围

| 文件 | 改什么 | 契约 |
| --- | --- | --- |
| `tech_app/backend/services/packaging_parts.py` | `geometry_evidence_of()` 新增 `drawing_bbox`（取自 `row["outline"]["bbox"]`），其余键一字不动 | C1、C2 |
| `tech_app/frontend/app.js` | 新增 `packagingCadPlanComponentBox()` 与 `PACKAGING_CAD_PLAN_NO_COORDS`；`packagingCadPlanComponentSvg()`、`renderPackagingCadPlan()` 改用前者 | C3、C4 |

`main.py` 无需改动（几何读接口原样透传 `geometry_evidence_of()` 的结果）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测），不许放宽断言让它变绿；
- 不许把 `drawing_bbox` 当成绑定尺寸来源（也不许把它写进 `bbox`）；
- 不许在前端做几何求解（不在浏览器里算环、算面积、重算尺寸）；
- 不许改渲染粒度（本批仍是每件一个矩形）、不许改平面图的配色/图层角色口径；
- 不许改技术侧 3D（`initViewer`/`loadSTL`），不许连生产库、不许新增依赖。

## 5. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cad_plan_drawing_coordinates_red -v
node --check tech_app/frontend/app.js
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_binding_size_source_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_panel_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_board_two_column_parts_and_3d_red
```

## 6. 实测证据（2026-09-22，真样本 `裕同包装项目-待开发/酒盒.dwg`）

- `extract()` 的 263 件**每一件**都有 `outline.bbox`（`263/263`），值就是图纸坐标系里的包络；
  `parts` 行依旧**没有** `bbox` 键，所以证据层的 `bbox` 仍是 `null` → 平面图今天画 0 个矩形。
- 263 件的 `outline.bbox` 边长与 `unfolded_length_mm/width_mm` **逐件相等**（闭合 134 件取环的
  包络、开口 129 件取分量包络，误差 > 0.01mm 的 0 件）—— 说明绘图坐标与件尺寸同源，
  所以"画图用 `drawing_bbox`、算绑定仍用件尺寸"不会有第二套几何。

## 7. 与既有 Spec 的关系（不重复立第二套）

- 平面图的**交互与选择语义**（点选高亮、缩放到部件范围、未绑定图元提示）仍以
  `packaging-business-parts-and-cad-plan-view.md` §6.2 为准；本批只补"包络从哪来"。
- `bbox` 的**绑定语义**以 `packaging-business-parts-binding-size-source.md` §C1 为准；
  本批新增的是另一个键（`drawing_bbox`），不重叠。
- 图元级（逐段折线）渲染仍是未做项，见 §9。

## 8. 落地状态（2026-09-22）

**落点**

| 落点 | 内容 | 契约 |
| --- | --- | --- |
| `packaging_parts.geometry_evidence_of()` | 每件新增 `drawing_bbox`（取自 `row["outline"]["bbox"]`，行上没有就是 `null`）；`bbox`、尺寸键、回查键、`limit` 分片与两笔总数一字不动 | C1、C2 |
| `tech_app/frontend/app.js`：`packagingCadPlanComponentBox(component)`（新） | 取画图框的**唯一**入口：`drawing_bbox` → `bbox` → `null`（不猜） | C3 |
| `tech_app/frontend/app.js`：`packagingCadPlanComponentSvg()` | 改走 `packagingCadPlanComponentBox()`（一行矩形，矩形属性/配色/图层角色不变） | C3 |
| `tech_app/frontend/app.js`：`renderPackagingCadPlan()` | 整张图的范围按同一入口算；范围为空时显示 `PACKAGING_CAD_PLAN_NO_COORDS`（新常量）而不是留白 | C3、C4 |

`main.py` 未改（几何读接口原样透传），`_component_size` / `BUSINESS_BINDING_REASONS` /
绑定状态机未改。

**判定口径**：`drawing_bbox` 只回答"这一件画在哪"（图纸坐标系的包络，闭合件取环、开口件取
分量包络）；`bbox` 仍是绑定判据的兜底尺寸来源。两者不互相顶替 —— 单位没确认的行只有 `null`，
绑定照旧按 `## 370` 报 `size_unknown`。

**复跑**

```
tests.test_packaging_cad_plan_drawing_coordinates_red              → Ran 12 OK
（实现前同一条命令：Ran 12, failures=7）
node --check tech_app/frontend/app.js                              → 通过
本次相邻 10 个模块（平面图/面板/零件/下游/接线）合计                → Ran 181 OK
tests/test_packaging_*.py 全域（84 个模块）→ Ran 1561, failures=5, skipped=8
（5 条仍是 B3 / B4 / A2 / F2 / C1 那批既有挂账，与本批无关）
```

**真样本实测**：证据层 263 件全部带 `drawing_bbox`（`263/263`），每一件的包络边长与
`unfolded_length_mm/width_mm` 误差 < 0.01mm —— 平面图从此有东西可画，且与件尺寸同源。

## 9. 已记录的边界

1. 本批仍是**一件一个矩形**：真图上 402 个连通分量、5598 条开放轮廓，逐段折线要等 CAD IR 把
   折线顶点带进证据层（`parser.py` 已落 `attributes.points`，证据层还没透传）。
2. `drawing_bbox` 只信零件行的 `outline.bbox`；行上没有它（老文档）→ `null` → 该件不画，
   页面按 §C4 如实说明，不猜、不回退到别的坐标。
