# 业务部件 ↔ 几何分量的绑定必须按「件的权威尺寸」，不许读一个不存在的 `bbox` 键

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（业务部件层与 `bind_geometry()` 的
判据 / 状态机 / 权限 / 下游版本绑定）、`docs/specs/packaging-parts-true-outline.md`（第 1 层：
「闭合件尺寸取环、开口件退回分量 bbox」的**唯一**尺寸口径）。

状态：Spec + 红测（已实现）（判据改读件权威尺寸；真样本 0/28 → 27/28 有候选，落地见 §8）
红测：`tests/test_packaging_business_parts_binding_size_source_red.py`

## 1. 目标与验收主路径

1. 把 `酒盒 报价资料.xlsx` 的 28 个业务部件与 `酒盒.dwg` 的几何分量**真的绑上**：真样本上
   `bound + partial + ambiguous ≥ 1`（今天恒 `0`，28 件全 `unbound`）。
2. 绑定的尺寸**只有一个来源** —— 几何行自己的权威尺寸；不再出现"判据读一个从来不存在的键 →
   每件都 `size_unknown` → 绑定这一层等于没接上"。
3. 拿不准仍不许猜：拿不到尺寸报 `size_unknown`，尺寸对不上报 `size_mismatch`，两者必须分得开；
   不许用业务尺寸反推几何尺寸、不许降低门槛把不匹配的件硬绑上。

## 2. 契约

### C1 几何件的权威尺寸只有一个定义

一件几何件的尺寸按第 1 层口径（`packaging-parts-true-outline.md`）取：

- 闭合件（`outline_status == "closed"`）→ 环尺寸，落在 `unfolded_length_mm` / `unfolded_width_mm`，
  `size_source == "closed_outline"`；
- 开口件 / 求不出环的件 → 分量 bbox 尺寸，同样落在 `unfolded_length_mm` / `unfolded_width_mm`，
  `size_source` 为 `component_bbox` / `dwg_outline`；
- 单位没确认（`unit_status != "confirmed"`）→ 两个字段为 `null`（**不给数**）。

绑定判据**必须**消费这份尺寸。`parts` 行上**没有** `bbox` 键（`extract()` 只在 `kept` 内部结构里
留 bbox），任何以 `component["bbox"]` 为**唯一**来源的判据在真图上恒为 `size_unknown`。

### C2 证据层必须把这份尺寸带出来

`geometry_evidence_of(parts_doc)` 产出的每个 component 至少带：

| 键 | 含义 |
| --- | --- |
| `unfolded_length_mm` / `unfolded_width_mm` | C1 的件尺寸（可为 `null`，不许编） |
| `outline_status` | `closed` / `open` / `unavailable` |
| `size_source` | 该尺寸的来源（与 `parts` 行逐字一致） |
| `component_id` / `entity_ids` / `layers` / `role` / `geometry_component_ref` | 既有回查引用，不改 |

`bbox` 键可以保留（老调用方兼容），但**不得**是唯一尺寸载体；`limit` 分片时两笔总数
（`component_total` / `kept_component_total`）仍按全量真值给。

### C3 判据与状态机一个字不改

沿用 `packaging-business-parts-and-cad-plan-view.md` §2：

- 两轴相符（含长宽对调）→ `bound`，`confidence = 0.9`；
- 只对一轴 → `partial`，`confidence = 0.5`，原因 `one_axis_only`；
- 并列多个分量同分 → `ambiguous`（同尺寸左右件**不合并**）；
- 没有命中 → `unbound`；
- 容差仍是 `±max(2mm, 5%)`（`_binding_tolerance()`），一个数都不动；
- 一个业务部件可绑多个分量、一个分量默认只属于一个业务部件（撞车仍写 `shared` 记录）。

### C4 原因码要能区分「拿不到尺寸」与「尺寸对不上」

`unbound` 时：

- 候选里**没有任何**可比尺寸（`unfolded_*` 与 `bbox` 都缺）→ `size_unknown`；
- 至少有一个候选给了尺寸、但都对不上 → `size_mismatch`；
- 一个候选都没有（几何分量列表为空）→ 既有 `no_component_size_match`（保留，不改字面量）。

这三个码与既有的 `one_axis_only` / `component_bbox_missing` / `no_authority_binding` 一起登记进
**绑定原因码闭集** `BUSINESS_BINDING_REASONS`（`REASON_CODES` 是**过筛**原因码，是另一套闭集，
不许混用、不许把绑定的码塞进去）。`component_bbox_missing` 只保留码位：判据改读件尺寸后绑定路径
不再发它（命中就说明尺寸有来源），老文档与人工路径仍可用。

### C5 命中件必须留下尺寸证据

每个 binding 记录除既有键（`business_part_code` / `status` / `component_ids` / `entity_ids` /
`bbox` / `confidence` / `reasons` / `bound_by` / `bound_at` / `rule_id` / `geometry_component_ref`）
外，新增 `size_sources`：命中分量的尺寸来源去重升序（例如 `["closed_outline"]`）。没有命中时为空列表。
既有键一个不改、键名不变。

### C6 冻结面

- 业务件仍**只**来自权威清单（`packaging_part_authority.import_workbook()`）；不因为几何里
  有 263 个连通分量就造业务件；
- `DWG-Pxx` 仍是几何证据编号，不是业务身份；
- 绑定文档版本 / `business_parts_id/hash` / 下游 `source_versions` 的语义不变；
- 不新增第三方依赖、不改费率与成本公式、不改页面（本批无前端改动）。

## 3. 允许修改范围

| 文件 | 改什么 | 契约 |
| --- | --- | --- |
| `tech_app/backend/services/packaging_parts.py` | ① 新增件尺寸唯一取值入口（读 `unfolded_*`，`bbox` 降为兜底）；② `_axis_pair_score()` 用它并把来源带回命中项；③ `geometry_evidence_of()` 透传 C2 的键；④ 无命中时的原因码按 C4 分三档；⑤ binding 记录新增 `size_sources` | C1–C5 |

本批**不改** `main.py`（路由与入参不变）、**不改**任何前端文件、**不改**任何测试文件。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测），不许放宽断言让它变绿；
- 不许改绑定容差 `_binding_tolerance()`、`bound/partial/ambiguous` 的判定顺序与置信度；
- 不许用业务尺寸当几何尺寸（拿不到尺寸时按 C4 报 `size_unknown`，不许猜、不许默认值）；
- 不许因为"要绑上更多件"而把 `unfolded_*` 拿去和业务尺寸做换算（单位换算 / 缩放 / 加减）；
- 不许改权威清单导入器 `packaging_part_authority.py` 与第 1 层 `extract()` 的尺寸口径；
- 不许连生产库、不许写业务数据、不许新增依赖。

## 5. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_binding_size_source_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_red
```

## 6. 实测证据（2026-09-22，真样本 `裕同包装项目-待开发/酒盒.dwg` + `酒盒 报价资料.xlsx`）

几何侧：`components = 263`（`kept`）、`closed = 134 / open = 129 → closed_ratio = 0.51`；
业务侧：权威清单 28 行（`JWXR21-P01…P28`）。

| 判据用的尺寸 | 两轴命中 | 单轴命中 |
| --- | --- | --- |
| `component["bbox"]`（现状，`parts` 行没有这个键 → 恒 `None`） | **0 / 28** | **0 / 28** |
| 件权威尺寸 `unfolded_length_mm/width_mm` | **19 / 28** | 8 / 28（含两轴未满的件） |

命中不是"容差凑出来的"：多数件与几何环尺寸**逐位相等或差 < 0.3mm**（例 `JWXR21-P21`
145.0×126.0 ↔ `cmp:192` 126.0×145.0 差 0.000；`JWXR21-P25` 80.5×34.3 ↔ `cmp:766` 34.3×80.5 差 0.000；
`JWXR21-P03` 217.1×482.9 ↔ `cmp:136` 217.062×482.92 差 0.038），说明业务表的"尺寸"列与 DWG 闭合轮廓
是**同一个量**（展开/部件尺寸），不是"成品尺寸 vs 展开尺寸"两把尺子。

未找到候选的 1 件是外购/内托件（`JWXR21-P26` 687.6×228.0，原因码 `size_mismatch`）。

**但"找到候选"不等于"自动定死"**：真图把同一个件画了很多遍（例：34.3×80.5 的分量出现
**55 次**、60.0×120.8 出现 13 次），既有并列规则（`packaging-business-parts-and-cad-plan-view.md` §2
"并列多个分量同分 → `ambiguous`，不合并"）于是给出：

| 状态 | 件数 |
| --- | --- |
| `ambiguous`（候选 ≥ 2 且尺寸并列） | 26 |
| `bound`（唯一候选） | 1（`JWXR21-P27`） |
| `unbound` | 1（`JWXR21-P26`） |

`ambiguous` 的件**并不缺数据**：`component_ids` 已列出全部尺寸相符的分量（例如 55 个重复分量），
只是状态保守地说"分不清哪一个是本体"。这一条属于**下一步的判据口径**（重复分量算不算同一个件的多次
出现），已记 §9 边界 1 —— 本批**没有**动它。

## 7. 与既有 Spec 的关系（不重复立第二套）

- 绑定的**判据、状态机、容差、权限、下游版本绑定**仍以
  `packaging-business-parts-and-cad-plan-view.md` §2 为唯一口径；本批只修"尺寸从哪来"这一处。
- 件的尺寸仍以 `packaging-parts-true-outline.md` §2 为唯一口径（闭合取环、开口退回 bbox）；
  本批只是把这口径**接进绑定**，没有新造第三种尺寸。
- 已有几何零件文档（老版本、行上不带 `unfolded_*`）按 C1/C4 走 `size_unknown`，**不**回填、
  **不**改历史文档。

## 8. 落地状态（2026-09-22）

**落点**

| 落点 | 内容 | 契约 |
| --- | --- | --- |
| `packaging_parts._component_size(component)`（新） | 件权威尺寸的**唯一**取值入口：`unfolded_length_mm/width_mm` 优先（来源取行上的 `size_source`），`bbox` 降为兜底（来源 `component_bbox`），两者都没有 → `(None, None, "")` | C1 |
| `packaging_parts._axis_pair_score()` | 改调 `_component_size()`；返回仍是 `(命中轴数, 原因)`，容差与对调判定一个字没改 | C1、C3 |
| `packaging_parts.bind_geometry()` | 命中项记 `size_source`；binding 记录新增 `size_sources`（去重升序）；无命中时按"有候选但对不上 / 有候选但没尺寸 / 没有候选"分报 `size_mismatch` / `size_unknown` / `no_component_size_match`；不再发 `component_bbox_missing` | C4、C5 |
| `packaging_parts.BUSINESS_BINDING_REASONS`（新） | 绑定原因码闭集（6 个），与过筛 `REASON_CODES` 分开 | C4 |
| `packaging_parts.geometry_evidence_of()` | 每个分量透传 `unfolded_length_mm` / `unfolded_width_mm` / `outline_status` / `size_source` / `area_mm2`；`bbox` 与既有回查键一字不动；`limit` 分片与两笔总数不变 | C2 |

**判定口径**：绑定结论 = "业务件尺寸 ↔ 件权威尺寸"的两轴/一轴相符度；件权威尺寸仍由第 1 层决定
（闭合取环 → `closed_outline`，开口退回 bbox → `component_bbox`/`dwg_outline`），本批只把这份尺寸
接进判据与证据层，没有新造第三种尺寸、没有换算、没有默认值。

**复跑**

```
tests.test_packaging_business_parts_binding_size_source_red  → Ran 19 OK
（实现前同一条命令：Ran 18, failures=13 —— 13 红 5 绿）
tests.test_packaging_business_parts_and_cad_plan_view_red    → Ran 14 OK
tests.test_packaging_parts_extraction_red                    → Ran 32 OK
tests.test_packaging_parts_outline_red                       → Ran 20 OK
tests.test_packaging_parts_panel_red                         → Ran 19 OK
tests/test_packaging_*.py 全域（83 个模块）→ Ran 1549, failures=5, skipped=8
（这 5 条是 `## 338` / `## 356` 等已在 Spec 里记过的既有挂账：B3 / B4 / A2 / F2 / C1，不在本批范围）
```

**真样本实测（判定前后对照）**：`bound_total` 0 → 1、`ambiguous_total` 0 → 26、`unbound_total`
28 → 1；`size_sources` 实测出现 `closed_outline`（13 件）与 `component_bbox`（含混合 5 件），
说明环尺寸真的被用上了。

## 9. 已记录的边界

1. **重复分量造成的"全表 ambiguous"（本批实测，下一步的判据口径）**：真图把同一个件画了很多遍
   （34.3×80.5 的分量 55 个、60.0×120.8 13 个、42.0×108.0 13 个），既有并列规则因此把 26/28 件
   标成 `ambiguous`（`component_ids` 已列出全部相符分量，数据不缺，只是状态保守）。要收敛这一步，
   需要先裁决"重复分量算不算同一个件的多次出现 / 是否按分量分组取一组代表"，属业务口径，本批不动。
2. **一个分量被多件认领**：真样本上 `cmp:136` 同时落进 `P03/P04`（左右同规格内层灰板）与
   `P05`（外层衬板，尺寸差 4.74mm 在 5% 容差内）。第一版按既有口径留 `shared` 记录（实测 67 条）
   并把状态报 `bound`/`ambiguous`，**不**自动降级，人工确认闭环（`PUT .../geometry-binding`）
   仍是唯一收口手段。
3. **平面图仍按分量 bbox 画**：`geometry_evidence.components[].bbox` 在真实 `parts` 行里本来就
   没有（本批只把**尺寸**透传出来），`packagingCadPlanViewer` 依旧画不出东西 —— 这要等 CAD IR 把
   轮廓/折线坐标带进证据层，与 `packaging-business-parts-and-cad-plan-view.md` §12 边界 4 同一条。
4. **外购件 / 内托件**（EVA、磁铁、内托）本来就可能在 DWG 里没有轮廓：它们保持 `unbound` +
   `size_unknown`/`size_mismatch`，按
   `packaging-business-parts-and-cad-plan-view.md` §6「几何未绑定只阻断依赖几何的尺寸/工艺」处理，
   不阻断材料与采购成本。
5. 本批**不**动 `extract()` 的 `closed/open` 口径，也**不**改业务清单的尺寸原文（
   `product_size_text` 永远保留）。
