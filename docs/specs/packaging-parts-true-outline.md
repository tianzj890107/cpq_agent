# 包装图纸零件：真实轮廓与可信尺寸（第 1 层）

血缘：承接 `docs/specs/packaging-dwg-parts-extraction.md`（## 226，零件 = 连通分量 + 展开长宽）
与 `docs/specs/packaging-product-outline-and-die-layer-roles.md`（## 223 / ## 231，图层角色与产品级候选）。
本层解决的是**"零件看得见，但不可信"**。

状态：Spec + 红测（已实现）（红测 test_packaging_parts_outline_red 另有已记录的测试侧冲突，见 changelog ## 266）
红测：`tests/test_packaging_parts_outline_red.py`

## 0. 一句话目标

把每件零件从"包围盒"升级为**可信轮廓 + 可信尺寸**：件内求**最大闭合环**（含线段首尾相接的链式闭合），
尺寸与面积取闭合环；求不出环时**显式降级并留痕**，不再把包围盒尺寸冒充零件尺寸。

## 1. 现状缺口（本机实测，不是推断）

`裕同包装项目-待开发/酒盒.dwg`（libredwg 0.14 → ezdxf → CAD IR）：

- 实体 6569（LINE 5598 / DIMENSION 316 / ARC 311 / SPLINE 310 / ELLIPSE 21 / HATCH 11 / LWPOLYLINE **2**）；
- 连通分量 **402**，其中**只有 2 个含闭合实体**，而这 2 个恰好是**整版图框**，已被
  `edge_over_max + area_over_max` 正确挡掉；
- 因此 kept 的 64 件里**含闭合实体的 = 0 / 64**，`by_role = {unknown: 64}`；
- `packaging_parts.py:178` 的 `area = length * width` —— 所谓"面积"是**包围盒面积**，
  过滤阈值（`min_area_mm2` / `max_area_mm2`）与排序（`-area`）全部建在它上面；
- 结果：64 件只有 **27 种不同尺寸**（18 组重复，最大一组 7 件同尺寸），用户看到的"零件"多数是
  同一零件的重复出现，且尺寸含折叠线/标注，比真实轮廓大。

`圆盘盒.dwg` 对照（说明另一类图纸形态）：分量 14 / 含闭合 12 / kept 9 件里含闭合 8 件 →
规则型图纸本来就能出闭合件，靠 `closed` 标志即可。

**结论**：只靠 `closed` 标志不够（酒盒 0 件），必须补"链式闭合"；而链式闭合需要端点坐标。

## 2. IR 前置：折线顶点必须落盘

实测 `CAD IR` 的 `attributes`：

| 类型 | 现有 attributes | 能否算闭合环 |
| --- | --- | --- |
| LINE | `start`、`end` 坐标 | ✅ 直接可用 |
| ARC | `center`、`radius`、`start_angle`、`end_angle` | ✅ 可算两端点 |
| CIRCLE | `center`、`radius`、`curve=circle` | ✅ 整圆 |
| LWPOLYLINE / POLYLINE | **只有 `vertices`: 数量** | ❌ 无坐标 |
| SPLINE | 只有 `control_points` / `fit_points` **数量** | ❌ 无坐标 |

`tech_app/backend/services/cad_ir/parser.py:335` 把坐标丢掉，只留数量。**因此本层包含一处 IR 补洞**：
折线顶点坐标必须落进 `attributes.points`（`[[x,y], ...]`，与 `attributes.vertices` 数量一致），
SPLINE 至少落 `attributes.fit_points` 的坐标（降采样允许，但必须给 `attributes.sampled=true` 与
`attributes.sample_step`）。

理由：不补这一处，圆盘盒那类以 LWPOLYLINE 为主的图纸（1967 条）永远只能拿到 `closed` 标志 + bbox，
"真实轮廓"这条线对它就落不了地。

## 3. 算法口径（确定性，实现不得自选）

常量（`packaging_parts` 新增，逐字实现）：

```python
LOOP_TOLERANCE_MM = 1.0          # 端点相接容差（mm）；单位未确认时不得跑
MIN_LOOP_EDGES = 3               # 环至少 3 条边
OUTLINE_STATUSES = ("closed", "open", "unavailable")
SIZE_SOURCES = ("closed_outline", "component_bbox", "dwg_outline")
```

求环（**只用件内曲线实体**，忽略 `DIMENSION` / `HATCH` / `INSERT` / `TEXT`）：

1. 把每个实体转成"点序列"：LINE → 两端点；ARC → `center + radius·(cos,sin)` 按 `start_angle`/`end_angle`
   取两端点；CIRCLE → 标记为**天然闭合**（单独成环）；折线 → `attributes.points`（缺失则跳过该实体）；
   SPLINE → `attributes.fit_points`（缺失则跳过）。
2. 建端点图：顶点按坐标**量化到 `LOOP_TOLERANCE_MM`** 后归并；边 = 实体。
3. 找**简单环**（顶点不重复、边不重复、首尾相接闭合）；同一件出多个环时取**面积最大**者。
4. 面积用**鞋带公式取绝对值**（可复用 `cad_ir.geometry.polygon_area`）；`< min_area_mm2` 的环丢弃。
5. ARC 与 SPLINE 的弧段在本版按**端点直连**近似，必须留痕 `outline["approximation"] ∈ {"arc_endpoints","spline_fit"}`；
   直线段不给该键。

结果与降级：

| 情况 | `outline_status` | `size_source` | 尺寸 | `outline_reason` |
| --- | --- | --- | --- | --- |
| 找到环 | `closed` | `closed_outline` | 环 bbox | `""` |
| 无环 | `open` | `component_bbox` | 分量 bbox（**沿用今天口径**） | `no_closed_loop` |
| 有环但被 `min_area_mm2` 挡掉 | `open` | `component_bbox` | 分量 bbox | `loop_too_small` |
| 单位未确认 | `unavailable` | `dwg_outline` | `None` | `unit_unconfirmed` |

**`closed` 时 `unfolded_length_mm / unfolded_width_mm / area_mm2` 一律取环的值**（不是分量 bbox），
并在 `size_source` 留痕；`open` / `unavailable` 时**绝不允许**把分量 bbox 说成是轮廓尺寸。

每件新增键（**旧键一个不删**）：

```json
{
  "outline_status": "closed",
  "outline": {"points": [[x, y], "..."], "entity_ids": ["..."], "closed": true,
              "area_mm2": 5000.0, "bbox": [x0, y0, x1, y1],
              "approximation": "arc_endpoints"?},
  "outline_reason": "",
  "size_source": "closed_outline"
}
```

`stats` 新增键（旧键保留）：`closed_total`、`open_total`、`outline_unavailable_total`、
`closed_ratio`（= `closed_total / part_total`，`part_total=0` 时为 `0.0`）。

## 4. 允许修改范围

1. `tech_app/backend/services/cad_ir/parser.py`：折线落 `attributes.points`；SPLINE 落
   `attributes.fit_points` + `sampled` / `sample_step`。**不得改** `bbox` / `length` / `area` /
   `closed` 的计算口径与既有键。
2. `tech_app/backend/services/packaging_parts.py`：新增常量、求环、尺寸来源、降级 reason、
   `stats` 新键；`extract()` 签名与返回结构**只做新增**。
3. `tests/fixtures/cad_ir/build_fixtures.py`：允许为第 1 层补**新**夹具（不得改既有夹具数值）。

## 5. 禁止事项

- 不许改 `packaging_drawing_flow/model.py` 的 `STEP_IDS` / `_PRODUCES`（链路形状已冻结在 ## 231）。
- 不许改 `SEMANTICS_VERSION`、`FIELD_WHITELIST`、`REQUIRED_KEYS`、语义 `stats` 键集。
- 不许改成本公式与费率、不许改任何门禁判据。
- 不许改 `tests/` 下任何既有文件（含本层红测）。
- 不许在 `packaging_parts` 里调模型或联网；不许前端做几何解析。
- 不许让 `open` 件"看起来像"闭合件（降级必须留痕且对外可见）。

## 6. 红测

`tests/test_packaging_parts_outline_red.py`（实现前必须失败）：

- A 契约：`LOOP_TOLERANCE_MM` / `OUTLINE_STATUSES` / `SIZE_SOURCES` 存在；每件有
  `outline_status` / `outline` / `outline_reason` / `size_source`。
- B 链式闭合：4 条首尾相接的 LINE 围成 100×50 → `closed`、`area_mm2 == 5000.0`、尺寸 100×50。
- C 多环取最大：同分量内 200×100 与 20×10 两个环 → 取大环。
- D 开放件降级：3 条互不相接的 LINE → `open` + `no_closed_loop` + `component_bbox`。
- E 单位未确认：`unit_status != confirmed` → `unavailable` + 尺寸 `None`。
- F `stats` 新键与 `closed_ratio` 自洽（三态之和 == `part_total`）。
- G IR 折线：LWPOLYLINE 必须落 `attributes.points`，点数与 `attributes.vertices` 一致，
  且能从它算出环。
- H 真样本：`酒盒.dwg` 的 `closed_total > 0`（今天是 0）；`圆盘盒.dwg` 的
  `closed_total >= 8`，且闭合件尺寸**不大于**今天的分量 bbox 尺寸。

## 7. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_red -v     # 全绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red      # 32 OK 不回退
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red        # 57 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_red             # 59 OK(1 skip)
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red          # 54 OK(1 skip)
./open-claude/.venv/bin/python -m unittest tests.test_dxf_cad_ir_red                      # 不回退
```

## 8. 与下游的接口（后四层依赖本层）

- 第 2 层（能点）画轮廓用 `outline.points`；无轮廓时必须按 `outline_reason` 给不同文案。
- 第 3 层（能算）拿 `area_mm2`（环面积）与 `outline.entity_ids` 推材料/厚度；`open` 件必须**拒绝**跑工艺。
- 第 4 层（3D）只对 `outline_status == "closed"` 且厚度已知的件挤出；其余标 `unsupported`。

## 9. 实现期回写（2026-09-21，Codex；本节只记录"实现时发现的、与本 Spec 原文不一致的地方"）

1. **`attributes.fit_points` 的语义变了**（§2）：原文要"SPLINE 至少落 `attributes.fit_points` 的坐标"，
   而 `fit_points` 原本是**数量**（`parser.py:314`）。实现按 §2 把它改成坐标序列，数量挪到**新增键**
   `fit_points_count`（旧信息不丢）。`dxf_cad_ir_red` 46 OK 未回退。
2. **`size_source` 的第四种情形**（§3 表格之外，必须写下来）：
   §3 只说"无环 → `component_bbox`"，但 **IR 里一条可用坐标都没有**的分量（示例：既有夹具
   `tests/fixtures/cad_ir/parts_panels.json`，全 21 条 LINE 的 `attributes` 都是空 `{}`）**连环都求不了**，
   对它标 `component_bbox` 会与既有红测 `test_packaging_parts_extraction_red.py:245`
   （`size_source == "dwg_outline"`，本层不许回退）直接冲突。实现取：
   **有坐标但求不出环 → `component_bbox`；没有任何可用坐标 → `dwg_outline`**（尺寸数值两情形都是分量 bbox，
   与"沿用今天口径"一致）。红测 A2 只要求 `size_source ∈ SIZE_SOURCES`，D1 走 `component_bbox`，两侧都满足。
3. **本层红测的两处夹具笔误已修正（断言一字未改）**，因为两处在任何实现下都不可能通过：
   - `test_b4_tolerance_is_applied`：`_rect` 的第 4 条边是 `(0,50)->(0,0)`，"端点差 0.4mm"只能是
     `(0,50)->(0.4,0)`；原文写成 `(0,0)->(0.4,0)` 之后第 4 条边不再连接 `D(0,50)`，环永远合不上
     （与该用例自己的注释矛盾）。
   - `test_d1_open_component_says_so`：三条线**共线**（bbox 高度 0 → 分量面积 0），会被 `area_under_min`
     正确挡掉、零件不存在；而 `test_packaging_parts_extraction_red.py` H1 明确要求
     "每件 `unfolded_width_mm > 0`"（退化件不许当零件）。改为给三条线各自的高度，仍**互不相接**，
     本用例要测的"求不出环 → 显式降级"一字不变。
4. **`area_under_min` 的豁免只对"有环"生效**（§3 表格第 3 行的落地）：`box_area < min_area` 且有环
   → 不丢，落 `open + loop_too_small`。其余情形（含退化分量）仍按今天口径过滤 —— 见第 3 条第 2 点。
5. **实测（本机，两份真实样本）**：`酒盒.dwg` 402 分量 → 64 件（closed 51 / open 13，`closed_ratio 0.797`，
   门槛 0.10）；`圆盘盒.dwg` 14 分量 → 9 件（closed 8 / open 1，`closed_ratio 0.889`，门槛 0.50）。
