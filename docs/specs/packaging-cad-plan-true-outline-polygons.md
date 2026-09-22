# 平面图按**真实闭合轮廓**画多边形（开口件仍画包络矩形）

依赖：`docs/specs/packaging-cad-plan-drawing-coordinates.md`（`drawing_bbox` 只给画图、`bbox`
仍是绑定来源）、`docs/specs/packaging-parts-true-outline.md`（闭合件的环与它的 `points` 口径）。

状态：Spec + 红测（已实现）（闭合件 134/134 画多边形，开口件仍画包络矩形；环点合计 1777）
红测：`tests/test_packaging_cad_plan_true_outline_polygons_red.py`

## 1. 目标与验收主路径

1. 2.1 平面图对**闭合件**画真实轮廓（多边形），不再把它画成一个包络矩形：图纸上有环的件要长得
   像它自己。
2. 开口件（求不出环）仍画包络矩形 —— 不假装开口件有轮廓（第 1 层口径不许回退）。
3. 载荷守住：只带**闭合件**的环点（真样本 134 件 / 1777 点 / 单件最多 32 点），不许把 5598 条
   开放轮廓的折线全带进证据层 —— 证据层不是第二份 CAD IR。

## 2. 契约

### C1 证据层带闭合件的环点

`geometry_evidence_of(parts_doc)` 产出的每个 component 新增：

| 键 | 含义 |
| --- | --- |
| `outline_points` | `outline_status == "closed"` 时逐字取零件行的 `outline.points`（`[[x, y], …]`，图纸坐标）；开口/求不出环/老文档 → `null` |

既有键逐字不动（`component_id` / `entity_ids` / `bbox` / `drawing_bbox` /
`unfolded_length_mm` / `unfolded_width_mm` / `outline_status` / `size_source` / `area_mm2` /
`layers` / `role` / `geometry_component_ref`），`limit` 分片与两笔总数不变。

### C2 平面图：闭合件画多边形、其余画矩形

前端只允许一个"取这一件的环点串"的入口 `packagingCadPlanOutlinePoints(component)`：
只认 `outline_status == "closed"` 且 `outline_points` ≥ 3 个点，逐点校验收成 `x,-y`
（平面图的 viewBox 已经翻过 y 轴，与 `packagingCadPlanViewBox()` 同一套约定）；任何一点不可用
就返回空串。`packagingCadPlanComponentSvg()`：

- 有环点串 → `<polygon>`；
- 否则 → 既有的包络矩形（`drawing_bbox`）。

**两者必须带同一套数据属性**：`class="packaging-cad-plan-entity"`、`data-component-id`、
`data-component-ref`、`data-business-part`、`data-bbox`、`data-layer`、`data-role`，
再加上同一份角色配色 `PACKAGING_CAD_LAYER_COLORS` —— 于是
`highlightPackagingBusinessPart()`（点选高亮 + 缩放到部件范围）与点选反查无需改动。

### C3 冻结面

- 不改绑定判据/状态机/容差/原因码（`## 370`），不改 `bbox` 与 `drawing_bbox` 的语义（`## 371`）；
- 不改抽取与过筛（`outline_status` / `size_source` / 环的求法一个字不动）；
- 不改右栏零件面板（它早已用 `payload.outline.points` 画过轮廓）；
- 不在浏览器端做几何求解（不算面积、不补点、不闭化折线）；不新增接口与依赖。

## 3. 允许修改范围

| 文件 | 改什么 | 契约 |
| --- | --- | --- |
| `tech_app/backend/services/packaging_parts.py` | `geometry_evidence_of()` 新增 `outline_points`（只闭合件、不复制、不改口径） | C1 |
| `tech_app/frontend/app.js` | 新增 `packagingCadPlanOutlinePoints()`；`packagingCadPlanComponentSvg()` 优先画 `<polygon>`、否则画 `<rect>` | C2 |

`main.py` 不变（几何读接口原样透传 `geometry_evidence_of()`）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测），不许放宽断言让它变绿；
- 不许给开口件编点、不许用 `drawing_bbox` 造 4 点矩形冒充轮廓（那就又回到"看着像闭合件"）；
- 不许把开放轮廓折线带上（证据层不是 CAD IR；单件详情另有只读接口）；
- 不许改渲染粒度到"逐条实体折线"（那是下一层的事，见 §9 边界 1）；
- 不许改平面图交互（高亮/缩放/点选反查）、配色口径与技术侧 3D；不连生产库、不加依赖。

## 5. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cad_plan_true_outline_polygons_red -v
node --check tech_app/frontend/app.js
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cad_plan_drawing_coordinates_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_panel_red
```

## 6. 实测证据（2026-09-22，真样本 `裕同包装项目-待开发/酒盒.dwg`）

- 闭合件 134 件，**每一件**的 `outline.points` 都非空；点数合计 **1777**，单件最少 4 点、最多 32 点。
- 开口件 129 件，`outline.points` **0 件非空**（开口件只有 `outline.bbox`）——
  所以"只给闭合件带点"既不会漏、也不会多带。
- 证据层当前 `components` 里没有 `outline_points`：平面图因此只能画 263 个包络矩形，
  闭合件看不出真实形状。

## 7. 与既有 Spec 的关系（不重复立第二套）

- 环的求法与 `points` 的含义以 `packaging-parts-true-outline.md` 为准；本批只做**透传与绘制**。
- 画图框的来源与"只给画图、不参与绑定"的边界以 `packaging-cad-plan-drawing-coordinates.md` 为准；
  本批在它之上把闭合件从矩形升级为多边形。
- 点选高亮/缩放/未绑定提示仍以 `packaging-business-parts-and-cad-plan-view.md` §6.2 为准，本批不改。

## 8. 落地状态（2026-09-22）

**落点**

| 落点 | 内容 | 契约 |
| --- | --- | --- |
| `packaging_parts.geometry_evidence_of()` | 每件新增 `outline_points`：`outline_status == "closed"` 时逐字取 `row["outline"]["points"]`，否则 `null`；其余键、`limit` 分片、两笔总数不动 | C1 |
| `tech_app/frontend/app.js`：`packagingCadPlanOutlinePoints(component)`（新） | 环点串的唯一入口：非闭合 / 点数 < 3 / 任一点不可用 → 空串；否则逐点 `x,-y` | C2 |
| `tech_app/frontend/app.js`：`packagingCadPlanComponentSvg()` | 有环点串 → `<polygon>`；否则 → 既有包络 `<rect>`（`packagingCadPlanComponentBox()`，`## 371`）；两种形状共用**同一套** data 属性（`data-component-id` / `data-component-ref` / `data-business-part` / `data-bbox` / `data-layer` / `data-role`）与同一份角色配色 | C2 |

`main.py` 未改；绑定判据 / 状态机 / `bbox` / `drawing_bbox` 语义、右栏零件面板的轮廓渲染、
平面图交互（高亮、缩放、点选反查）与技术侧 3D 全部未动。

**判定口径**：`outline_points` 是**绘图数据**（闭合件的环点，图纸坐标），不参与绑定、不参与成本、
不改 `outline_status` 与 `size_source`；开口件永远没有它 —— 图上仍是包络矩形，页面不假装它有轮廓。

**复跑**

```
tests.test_packaging_cad_plan_true_outline_polygons_red  → Ran 13 OK
（实现前同一条命令：Ran 13, failures=6）
node --check tech_app/frontend/app.js                    → 通过
tests/test_packaging_*.py 全域（85 个模块）+ 图纸两列/接线两条
                                                         → Ran 1596, failures=5, skipped=8
（5 条仍是 B3 / B4 / A2 / F2 / C1 那批既有挂账，与本批无关）
```

**真样本实测**：134 件闭合件全部带 ≥ 3 个环点（合计 1777 点、单件最多 32 点），129 件开口件
0 件带点；每件环点的包络与 `drawing_bbox` 逐轴相等（误差 < 0.01mm），所以多边形与高亮/缩放的
`data-bbox` 不会错位。

## 9. 已记录的边界

1. **不是逐条实体折线**：真图有 402 个连通分量、5598 条开放轮廓；把 `attributes.points` 的折线
   逐条带上属于下一层（证据层分片与载荷预算要单独设计）。
2. 环点只在闭合件上；开口件在图上仍是一个矩形 —— 与第 1 层"开口件退回分量包络并写
   `outline_reason`"的结论一致，不是本批的回退。
3. 老文档（行上没有 `outline.points`）→ `outline_points` 为 `null` → 该件仍画矩形，不猜。
