# Spec：尺寸标注线不许进零件形状（也不许进轮廓环 / 件内折线 / 件尺寸）

状态：Spec + 红测（已实现）（2026-09-23 由并行会话落地：`packaging_parts.py` 新增
`annotation_entity_ids()`（`ANNOTATION_LAYERS` / `ANNOTATION_ARROW_MAX_MM` / `ANNOTATION_TOLERANCE_MM` /
`ANNOTATION_REASONS` 闭集，按 `dimensions` 的目标点与尺寸界线跨度判掉尺寸线 / 尺寸界线 / 箭头，逐件写
`annotation_filtered`，`stats` 只加键不改既有口径），`app.js` 补一句只读后端文档的披露
`packagingPartAnnotationFilteredLine()`；本机实测 `Ran 13 … OK`）
红测：`tests/test_packaging_dimension_annotation_not_in_part_shape_red.py`
血缘：`docs/specs/packaging-cad-plan-polyline-segments.md`（件内折线的来源与截断披露）、
`docs/specs/packaging-parts-true-outline.md`（轮廓环求法）、
`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md`（点一件直接看样子）、
`docs/specs/packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §2.3（图框、标题栏、图例、尺寸线单独归
`annotation/frame`，不得进入部件区域 —— 本批把这条从"规格说明"落成"可测行为"）。
本批 changelog 条目号：`## 484`（落地条目同为 484，见 changelog）。

## 0. 用户原话（2026-09-23）

> 现在还有一个问题 那些白色的线是用来标注尺寸用的 不能也显示在这个零件里

## 1. 实测证据（本机只读，用仓库已缓存的真样本 converted.dxf，不重跑 DWG 转换）

| 读数 | 实测 |
| --- | --- |
| 酒盒.dwg → DXF → CAD IR（`tech_app/data/cad-ir-realsample/conversions/47c39d…/converted.dxf`） | 实体 6569：`LINE 5598` / `DIMENSION 316` / `ARC 311` / `SPLINE 310` / `ELLIPSE 21` / `HATCH 11` / `POLYLINE 2`；图层 8 个（`0` 3725、`DESIGN` 2492、`CUTTER` 308、`图层 2` 82、`SAMPLE` 52、`轮廓线` 48…） |
| `ir["dimensions"][0]` | `{"entity_id":"ent:model:10A4","layer":"0","dim_type":"linear","declared_value":219.64350000000013,"target_entity_ids":["point:1501.715760,3388.310285","point:1721.359260,3388.310285"]}` —— 标注实体自己**不带任何线几何**，它的尺寸线/尺寸界线在图上就是**普通 LINE** |
| `packaging_parts.extract(ir, None)` | 263 件；`by_role={"unknown":263}`、`closed_total=134`、`open_total=129` |
| **件内混进标注线的实锤（DWG-P02）** | 成员 27 条，其中**只有 19 条**构成轮廓环；另外 8 条是：`54C3`(层 DESIGN, 88.70mm)、`54C7`(层 0, 267.94mm)、`54CA`(层 0, **219.64mm** = 该图一条标注的 `declared_value` 219.6435)、`54D1/54D2/54D3`(层 DESIGN, 88.84/88.84/214.49)、`54E3`(层 0, **5.02mm** 箭头)、`54F0` |
| 更严重的一件（DWG-P54） | 成员 84 条，**只有 6 条**是轮廓边，其余 **78 条**是尺寸链的尺寸线/尺寸界线 |
| 为什么会被并进同一件 | 分组是**端点相接**连通（`cad_ir/geometry.components_of`）；尺寸界线的一端正好落在零件角点上 ⇒ 标注线与零件永久同组 |
| 件内折线的来源 | `_component_segments(members)`：**把件内全部实体的顶点逐条折线化**，不区分轮廓 / 件内几何 / 标注 ⇒ 2994 行里那条 219.64 的尺寸线就画进了零件形状 |
| 前端画法 | `packagingCadPlanComponentSvg()` 逐段原样画 `segments`；角色 `unknown` ⇒ `#8b949e` 浅灰细线（用户描述为"白色的线"）；零件形状面板不做任何过滤 |
| 可检出的判据（实测） | 5598 条 LINE 里 **1716 条**长度与某个 `declared_value` 相等（±0.5mm）⇒ **只按"等长"判标注会误伤真几何**，判据必须更窄（见 §2.1） |
| 最小离线复现（合成夹具，确定性） | 闭合矩形 200×300 + 3 条接在角点上的尺寸界线/尺寸线（其中一条长 200 = 该维度 `declared_value`）组成**一个** component ⇒ 现在：`outline.points` 变成 **6 点、多绕到 y=-50**、`segments` 4 段含 3 条标注线 ⇒ **件尺寸被撑成 200×350** |

结论：这不只是"多画了几条线"，**标注线会改轮廓环、进而改件尺寸**（`unfolded_length_mm/width_mm` 与
`area_mm2` 都来自那个环），所以必须在**分组前后就把标注实体摘出去**，而不是"画的时候少画一点"。

## 2. 契约

### 2.1 C1：标注实体判据（后端纯函数，保守、可复现、不许误伤真几何）

新增纯函数 `packaging_parts.annotation_entity_ids(ir, entities)` → `{entity_id: reason}`，判据按顺序：

1. **尺寸界线**：线段任一端点与任一 `ir.dimensions[*].target_entity_ids` 里的 `point:x,y` 重合，
   或落在这些点两两连线上（容差 `max(0.5mm, ir.geometry.tolerance)`）→ `dimension_extension`；
2. **尺寸线 / 箭头（传染）**：一条线段的两端，若各自除自身外**只**与"已判定的标注线段"相接、
   或本来就是悬空端点 → `dimension_line`；长度 ≤ `ANNOTATION_ARROW_MAX_MM = 6.0` 且与已判定的
   尺寸界线同端点 → `dimension_arrow`；
3. **尺寸定义点图层**：实体图层名（去首尾空白、转大写）∈ `ANNOTATION_LAYERS = ("DEFPOINTS",)` → `annotation_layer`；
4. **禁止**：只凭"长度等于某个 `declared_value`"判标注 —— 真样本 1716/5598 命中，单凭它必然误伤；
   长度只允许作为 ①②③ 的**辅助**条件。

输出即逐条留痕（`entity_id` → `reason`），reason 闭集：
`("dimension_extension", "dimension_line", "dimension_arrow", "annotation_layer")`。

### 2.2 C2：标注实体进不去这四处（这是本批的验收核心）

判成标注的实体**不许**进入：

- 连通分量分组（`geometry.components[*].entity_ids`）—— 否则会把标注线永久粘进件；
- 求环（`_outline_evidence`）—— 否则 `outline.points` 会绕着尺寸界线多走一圈；
- 件内折线（`part["segments"]`）—— 用户能看到的"白线"就是这里画的；
- 件尺寸（`unfolded_length_mm/width_mm/area_mm2` 与 `outline.bbox`）—— 它们都从环/成员算，
  摘不干净就会被撑大。

反向判据（同样重要）：**真几何一条都不许少** —— 同一条件内线，只要它的端点在轮廓上且接线正常
（不是尺寸界线、不在 `DEFPOINTS`），即使长度恰好等于某个 `declared_value`，也必须原样保留。

### 2.3 C3：必须说得出来（不许静默丢图元）

- 文档级：`doc["stats"]["annotation_filtered_total"]` = 被剔除的标注实体总数；
- 件级：`part["annotation_filtered"] = [{"entity_id": …, "reason": …}, …]`，按 `entity_id` 升序、
  确定性（同一份 IR 两次跑逐字相同）；没有剔除的件给空列表；
- 前端：业务部件行的 `annotation_filtered` 非空时，右侧零件面板给一行
  `data-qq-annotation-filtered="1"` 的披露 —— 文案 `已剔除标注线 N 条（尺寸线/尺寸界线/箭头，不是零件几何）`，
  `N = 0` 时**整块不出现**（不留空节点）。

### 2.5 C5：件身份按**未过滤成员**判（实施时收紧，见 §5.1 授权）

判成标注的实体只许影响**形状**（轮廓环 / 件内折线 / 件尺寸），不许影响**件身份**：
"有没有这一件"（`area_under_min` / `edge_over_max` / `area_over_max` 三条门槛）、
件角色（`role`）、层名（`layers`）、`entity_ids` 一律按分量的**原始成员**判。

为什么必须这么收紧（本机只读实测，圆盘盒.dwg，门禁样本）：

| 口径 | `part_total` | `closed_total` | `role_known_total` |
| --- | --- | --- | --- |
| 按**原始成员**判身份（今天的实现） | 312 | 255 | 9 |
| 按**过滤后成员**判身份 | 304 | 247 | 6 |

按过滤后成员判时：8 个纯标注簇被 `area_under_min` 掉（312 → 304），另外 3 件真 CUTTER 件因为
自己那条 CUTTER 边被判成标注而丢掉角色（9 → 6）。这两条数字都是**既有地板**
（`packaging-parts-gate-threshold-recalibration.md` §C1 的 `role_known_total >= 8`、
`packaging-parts-list-visibility-and-kinds.md` §6），所以"删对了线"绝不能换来"件没了 / 角色没了"
—— 与 §2.2 反向判据同一条纪律。

回退规则据此收紧：摘标注后**求不出环** ⇒ 整件回退（保留原始成员与原来的环、`annotation_filtered`
记空），不许把一件闭合件判成开口；但"件身份不动"这件事**永远**成立（不依赖回退）。

### 2.4 C4：单一真源（护栏）

- 零件形状（右栏 `#packagingPartOutline`）与左栏 CAD 平面图画的图元一律来自后端文档
  （`outline_points` / `segments` / 场景），**前端不做过滤、不自己判标注**；
- 左栏整图 CAD 平面图**照旧显示标注**（那是图纸本来有的东西）；本批只要求"标注不许当成零件的形状一部分"；
- 不动 `SEMANTICS_VERSION`、不动角色闭集、不动成本/工艺口径。

## 3. 本批不做

- 不新增 `annotation` 语义角色（图层角色闭集与 `packaging_layer_rules.json` 本批不动）；
- 不改 `_largest_loop` 的算法本身（只在它之前把标注实体摘掉）；
- 不改前端几何渲染方式（仍逐段画后端给的折线）；
- 不做"标注文字/尺寸数值"的识别与显示。

## 4. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_dimension_annotation_not_in_part_shape_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v      # 26 OK 不回退
./open-claude/.venv/bin/python -m unittest tests.test_packaging_2_1_parts_row_layout_and_shape_viewport_red -v
```

真样本复核（实现后，只读）：`extract()` 在缓存的酒盒 CAD IR 上跑，`DWG-P02` 的 `segments`
不许再出现 219.64 / 267.94 / 88.70 / 5.02 这四段，且 `annotation_filtered_total` ≥ 8。

## 5. 红测清单（`tests/test_packaging_dimension_annotation_not_in_part_shape_red.py`）

- A 组（合成夹具真跑 `extract()`）：A1 标注线不进件内折线；A2 轮廓环不含标注点（4 点矩形）；
  A3 件尺寸 200×300 不被撑成 350；A4 `annotation_filtered_total`；A5 逐件留痕（升序 + 闭集 reason）；
  A6 真几何不误伤；A7 只按"等长"不许判标注；
- B 组（源码/前端守卫）：B1 判据函数与 reason 闭集存在；B2 折线段来自剔除后的成员；
  B3 `stats` 键存在；B4 前端披露钩子；B5 前端仍从后端文档取 `outline_points` / `segments`（护栏）。

纪律：只读仓库 + `python` 真跑 `extract()`（用仓库内夹具与本机已缓存的 IR，不重跑 DWG 转换）；
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。禁止为了让红测转绿而修改本文件；口径变化请改 Spec。

## 5.1 授权：本批实现里两处比 §2.1/§2.2 更严的地方（不是放宽）

1. **件身份按原始成员判（§2.5）**：§2.2 第 1 条写的是"标注实体不许进入连通分量分组"。实现**不重
   分组**（IR 的 `geometry.components[*].entity_ids` 与 `part["entity_ids"]` 一个字不改），只把标注
   从"形状"里摘掉；件的门槛、角色、层名按原始成员判。实测依据见 §2.5 那张表：按过滤后成员判身份会
   打穿两条既有地板。
2. **回退（摘完求不出环 ⇒ 整件回退）**：真样本上判据会误伤真几何（见 §7），所以"摘完就没了环"的
   件整件回退、不留痕。这条比 §2.2 更保守：宁可多画几条线，也不许把闭合件判成开口。

两处都不许被后续批次"顺手放开"（放开会让 §2.5 的两条地板立刻回头）。

## 6. 落地状态（2026-09-23，Codex 实现）

| 项 | 实测 |
| --- | --- |
| 本批红测（`Ran 13`） | **OK**（红基：HEAD 工作副本 + 本红测 = `Ran 13 … FAILED (failures=11)`，另 2 条为护栏） |
| 反向对照 A（判据清空 → `{}`） | 6 红：A1/A2/A3/A4/A5/A7 |
| 反向对照 B（关掉"用过滤后成员判形状"） | 6 红：A1/A2/A3/A4/A5/A7 |
| 反向对照 C（件身份改回按过滤后成员判） | 4 个既有模块 `Ran 60 … FAILED (failures=6)`（312→304、9→6、D1/F2/B1/B3）；改回后 `Ran 60 … OK` |
| 保护网（同 4 模块，改回后） | `Ran 60 tests … OK`（77.3s，真样本真跑） |
| 酒盒缓存 IR（263 件） | `kept 263 / closed 134 / open 129`、`annotation_filtered_total = 3`（2 件带留痕）——与 HEAD **件数、闭合数、尺寸逐字一致** |
| 圆盘盒.dwg（门禁样本） | `312 / 255 / role_known 9`，`annotation_filtered_total = 0`（该样本没有"接在尺寸目标点上"的标注线） |

改动文件：`tech_app/backend/services/packaging_parts.py`（`annotation_entity_ids()` 判据 + `extract()`
的形状替换与回退 + `stats["annotation_filtered_total"]`）、`tech_app/frontend/app.js`
（`packagingPartAnnotationFilteredLine()` 披露）。**未动** `_outline_evidence` / `_largest_loop` /
`_component_segments` 的既有口径，未动 `SEMANTICS_VERSION` / 角色闭集 / 成本 / 工艺。

## 7. 已知缺口（本批**没有**解决，必须另立一批）

§4 那条真样本复核（`DWG-P02` 的 `segments` 不许再出现 219.64 / 267.94 / 88.70 / 5.02）**没达成**。
实测（本机只读，`tech_app/data/cad-ir-realsample/conversions/47c39dc1ab6738fc48c8…` 缓存的酒盒 `converted.dxf`）：

- `cmp:139`（按面积排第 2，即 `DWG-P02`）27 条成员，判据命中 **0** 条；环仍是 **19 个点**、
  `area_mm2 = 117846.31`、`unfolded = 308.834 × 446.32`。
- 这一件的**真件**是 4 条线构成的矩形 `54C7/54C8/54C9/54CA`（`219.643 × 267.937`，4 元环、58 960mm²）；
  其余 23 条是**手画的尺寸框**（不是 `DIMENSION` 实体）：两条尺寸线 `54C0`（竖，向外偏移 89.19）与
  `54C1`/`54D3`（横，向外偏移 88.84）、6 条延长线 `54C2`/`54C3`/`54D1`/`54D2`/`54EF`/`54F0`（都从零件角点向外）、
  引线三角 `54CE`/`54CF`/`54D0`、延长段 `54DF`/`54E0`/`54E1`，以及 8 条 5.025mm 箭头
  `54D4`/`54D5`/`54D6`/`54D7`/`54E2`/`54E3`/`54E4`/`54E5`。
- 判据看不见它们的原因：它们的端点到 `ir["dimensions"][*].target_entity_ids` 里**任何**目标点的最近
  距离是 **46.9–170.0mm** —— 这张图上的尺寸线是**手工画的普通 LINE**（不是 `DIMENSION` 实体展开），
  所以 §2.1 第 1 条（端点落在尺寸目标点上）在这里一条都不命中。
- 更根本的一层是**选环**，不是枚举：这一件给出 62 个候选环（把预算从 20 000 个状态放大到 5 000 000
  个状态、500 000 个环，候选环仍是同样的 62 个 ⇒ 不是预算不够），**真件那个 4 元环就在里面**
  （节点 `(-4865,3045)/(-4865,3313)/(-4645,3313)/(-4645,3045)`、4 条边
  `54C7/54C8/54C9/54CA`、面积 58 960mm²）。`_largest_loop` 选"面积最大"，而尺寸框的环
  （19 条边、117 834mm²）比它大 —— 于是外面那圈标注线被当成了件轮廓。
- 赢下来的那个 19 条边的框环里有 **8 条就是上面那 5.025mm 的箭头**。只把"≤ 6mm"的段摘掉再选环，
  这一件立刻回到 4 点矩形（58 851mm²）—— 但这条路**不能直接上**：
  同一份 IR 上这样一刀会改 7 件（`cmp:47`/`cmp:139` 是正确的框→件，另有 `cmp:52`/`cmp:876`/
  `cmp:892`/`cmp:908`/`cmp:1076` 的环也跟着变），圆盘盒样本上会改 5 件
  （`cmp:3139` 20 627→20 412、`cmp:3157` 19 210→19 007…），而 §2.1 第 4 条明确**禁止只凭长度**判标注。
  要走这条，必须先有"箭头对（两条短段共点 + 与长段非共线）"+ 门槛 + 两份样本的对照，另立 Spec。

**下一步（另立 Spec 的入口）**：要么把尺寸框识别从"尺寸实体"改成"几何形态"（箭头对 + 与角点共点的
外向延长线 + 平行偏移线），要么让环枚举能给出"被框住的里层环"并按件身份挑环。两条都要先有跨两份
真样本的判据与门槛，禁止在没有门槛前先改 `_largest_loop`。
