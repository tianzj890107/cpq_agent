# Spec：手画的尺寸框不许当零件轮廓（箭头对判据 + 选环 + 环下标与边表成对）

状态：Spec + 红测（已实现）（2026-09-23 由本批落地：`packaging_parts.py` 新增
`DIMENSION_ARROW_PAIR_COS` / `DIMENSION_ARROW_PAIR_STRAIGHT` / `_short_pair_is_arrow()` /
`_arrow_pair_ids()`，`annotation_entity_ids()` 增第 ④ 条判据（尺寸箭头对），`extract()` 的"摘标注后重选环"
收紧为**只在一进一出都闭合的件上生效**，并修掉 `_outline_evidence()` 里"环下标配错边表"的证据归属缺陷；
本机实测 `Ran 16 … OK`）
红测：`tests/test_packaging_dimension_frame_not_part_ring_red.py`
血缘：`docs/specs/packaging-dimension-annotation-must-not-enter-part-shape.md`（§2.1 的 ① ② ③ 判据与
§7"另立 Spec 的入口"—— 本批就是那条入口，**不推翻**它任何一条）、
`docs/specs/packaging-parts-true-outline.md`（件内求环与尺寸口径）、
`docs/specs/packaging-business-parts-outline-bbox-broken-link.md`（闭合比例是既有地板）、
`docs/specs/packaging-parts-gate-threshold-recalibration.md`（圆盘盒 `closed_total` / `role_known_total` 地板）、
`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md`（点一件直接看样子）。
本批 changelog 条目：`## 486`。

## 0. 用户原话（2026-09-23）

> 那些白色的线是用来标注尺寸用的 不能也显示在这个零件里

上一批（`## 485`，Spec `packaging-dimension-annotation-must-not-enter-part-shape.md`）按"尺寸实体"把标注
摘了一部分，但**点开那一件，形状里仍带着外面那圈白框**：白框不是"多画了几笔"，而是**选环选错了** ——
`_largest_loop()` 按面积最大挑，挑了外面那个尺寸框。

## 1. 实测证据（本机只读：仓库已缓存的真样本 `converted.dxf`，不重跑 DWG 转换）

| 读数（酒盒.dwg） | 实测 |
| --- | --- |
| 全图标注判定（`annotation_entity_ids`） | 438 条；只开 ① ② ③（把 ④ 打桩成 `{}`）= 88 条 ⇒ **④ 补了 350 条** |
| `cmp:139`（`DWG-P02`→`DWG-P23`） | 成员 **27** 条：真件是 4 条线 `54C7/54C8/54C9/54CA`，其余 23 条是**手画的尺寸框**（两条尺寸线 `54C0`/`54C1`/`54D3`、6 条从零件角点向外 88.84 / 89.19mm 的延长线、引线三角 `54CE`/`54CF`/`54D0`、延长段 `54DF`/`54E0`/`54E1`、**8 条 5.0249mm 箭头** `54D4`/`54D5`/`54D6`/`54D7`/`54E2`/`54E3`/`54E4`/`54E5`） |
| 为什么 ① ② ③ 一条都不命中 | 这些线的端点到 `ir["dimensions"][*].target_entity_ids` 里**任何**目标点的最近距离是 **46.9–164.95mm**（`54C1`/`54C2`/`54D3` 46.9 最近、`54D0` 164.95 最远）—— 尺寸是**手工画的普通 LINE**，不是 `DIMENSION` 实体展开 |
| 更根本的一层是**选环** | `cmp:139` 给出 **62** 个候选环（把预算从 `MAX_LOOP_STATES = 20000` / `MAX_LOOP_CYCLES = 256` 放到 5 000 000 / 500 000，候选环仍是**同样的 62** 个、`budget_exhausted=false` ⇒ 不是预算不够）；**真件那个 4 元环就在里面**（面积 **58 851.004mm²**） |
| `_largest_loop` 挑错 | 未摘标注那趟的最大环是**尺寸框的 19 条边环**：`117 846.31mm²`、件尺寸被撑成 **308.834 × 446.32**（其中 8 条边就是上面那 5.0249mm 箭头） |
| 摘掉箭头对之后（本批） | `cmp:139` 的清洁成员只剩 19 条边、2 个环，最大环 = 4 元环 **58 851.004mm²**，件尺寸 **219.643 × 267.94**、`outline.entity_ids = 54C7/54C8/54C9/54CA` |
| 箭头对形态（真样本实测） | 8 条箭头长度全 = **5.0249mm**；4 对各自**共用顶点**、远端夹角 cos = **-0.9802**（⇒ 判据阈值取 `DIMENSION_ARROW_PAIR_COS = -0.9`，且**完全共线**（cos = -1.0）不算） |
| 同一刀在**圆盘盒.dwg**（门禁样本）上的附带 | **零**：`312 / 255 / role_known 9 / closed_ratio 0.817`、`annotation_filtered_total = 0`，逐件环与尺寸**一个字不变** |
| 酒盒逐件对照（HEAD → 本批） | 环/尺寸变了 **2 件**（`cmp:139`、`cmp:47`，都是"框 → 真件"）；只有证据 `outline.entity_ids` 变了 **47 件**；只有编号/名字变了 32 件；`part_total 263 / closed 134 / open 129 / closed_ratio 0.51` **逐字不变**，`annotation_filtered_total 3 → 43`（6 件带留痕：`cmp:139` 8、`cmp:47` 8、`cmp:130` 10、`cmp:138` 8、`cmp:64` 5、`cmp:801` 4） |
| 另 4 件带留痕的件（`cmp:130`/`cmp:138`/`cmp:64`/`cmp:801`） | 环**没变**（仍 6 点、108.914 × 274.816），只是"画出来的形状"里不再画标注：`segments_total` 82→74 / 54→46 / 41→37 / 42→38 |
| 顺带查实的证据缺陷 | `outline.entity_ids` 以前**指错实体**：`loops_original` 的下标活在 `unique`（按边键排序）里，却被拿去索引未排序的 `edges` —— 酒盒 374 处、圆盘盒 538 处 `entity_ids` 指到了别的实体上（形状/尺寸/状态都不受影响）。本批修掉（见 §2.2） |
| 最小离线复现（合成夹具，确定性） | 零件矩形 200×300（面积 60 000）+ 向外延长线 + 两段 5mm 内折箭头 ⇒ 尺寸框环 **68 000 > 60 000**，今天会赢；本批必须仍然选零件的 60 000 |

结论：**"摘标注"只是必要条件，选环才是本批的靶心**；而"只凭长度 ≤ 6mm 摘"这条路（`## 485` §7 已否掉）
会误伤 5 件真零件与圆盘盒 5 件，所以判据必须是**形态**（两段短段共点 + 远端近似反向）。

## 2. 契约

### 2.1 C1：尺寸箭头对判据（④，纯函数、保守、可复现）

在 `annotation_entity_ids()` 的 ① ② ③ 之后**追加**第 ④ 条（`result.setdefault`，先判定的优先）：

- 新常量：`DIMENSION_ARROW_PAIR_COS = -0.9`（远端夹角 cos 上界）、
  `DIMENSION_ARROW_PAIR_STRAIGHT = -0.999`（**完全共线**下界，不含）、
  长度上界复用既有 `ANNOTATION_ARROW_MAX_MM = 6.0`；
- 新纯函数 `_short_pair_is_arrow(first, second, shared)`：两段共用顶点 `shared`，且各自远端与
  `shared` 构成的向量**近似反向**，判据是 `DIMENSION_ARROW_PAIR_STRAIGHT < cos ≤ DIMENSION_ARROW_PAIR_COS`；
  两段都必须 `0 < length ≤ ANNOTATION_ARROW_MAX_MM`（长度只是**辅助**，单独一条短段绝不是证据）；
- 新纯函数 `_arrow_pair_ids(components, segments, ends, lengths)`：**只在同一个连通分量内**配对（相邻两张图
  共用一个角点时不许跨件连坐）；输入顺序不影响结果；
- ④ 只补漏、不覆盖：`reason` 仍落在 `ANNOTATION_REASONS` 闭集，命中记 `dimension_arrow`；
- 禁止：把"≤ 6mm 的短段"整体当标注（`## 485` §7 的实测反例：会改 7 件酒盒件 + 5 件圆盘盒件）。

### 2.2 C2：环必须是**零件自己**的环（形状 + 证据）

- 形状：摘掉标注后的清洁成员里能求出环，且**未摘之前这一件也有环** ⇒ 形状（`outline.points` / `area_mm2` /
  `unfolded_*` / `segments`）用清洁那趟的结果 —— 尺寸框不许再赢过零件（`cmp:139`：19 点 117 846.31 →
  4 点 58 851.004）；
- 证据：`outline.entity_ids` 必须**真的**是构成这个环的实体 —— 环的下标与边表必须**成对**使用
  （`loops_original` ↔ 未折叠 `edges`、`loops_collapsed` ↔ 折叠后 `unique`；没有重复边时两张表条数相同
  但**顺序不同**）。混用会让"证据"指到别的实体上，属于**必须修**的正确性缺陷（实测：酒盒 374 处、
  圆盘盒 538 处）；
- 反向判据：零件自己的边**一条都不许少**（夹具里 `(0,0)-(200,0)` 与 `(200,0)-(200,300)` 必须仍在
  `segments` 里）；箭头段不许出现在 `segments` 里。

### 2.3 C3：只许改**形状**，不许改闭合状态 / 件身份 / 件数

- 本来就求不出环的件（`outline is None`）**不许**因为"摘掉几条线"变成闭合件 —— 那条路直接回退、原样保留，
  并且**不留痕**（`annotation_filtered` 记空，不许声称摘过）；
- 摘完反而求不出环的件（一进一出不都是闭合）整件回退，保留原来的环与画法（`## 485` §2.5 的回退规则）；
- 件数 / 闭合数 / 开口数 / 角色 / 层名 / `entity_ids`（成员证据）一律按**原始成员**判：
  酒盒 `263 / 134 / 129`、圆盘盒 `312 / 255 / role_known 9` **逐字不变**。

### 2.4 C4：必须说得出来（留痕，不许静默改形状）

- 文档级 `stats["annotation_filtered_total"]`、件级 `part["annotation_filtered"] = [{entity_id, reason}]`
  按 `entity_id` 升序、确定性；没有剔除的件给空列表；
- 只有**真的换了形状**的件才允许留痕（回退件不留痕），因为这一栏的含义是"这件画出来少了几条线"。

### 2.5 C5：单一真源（护栏）

- 判据、选环、留痕全在后端；前端照旧只读后端文档；
- 不动 `STEP_IDS` / `_PRODUCES` / `SEMANTICS_VERSION` / `FIELD_WHITELIST` / 角色闭集 / 成本与工艺口径；
- 不动 `MAX_LOOP_STATES` / `MAX_LOOP_CYCLES` / `MIN_LOOP_EDGES` / `LOOP_TOLERANCE_MM` /
  `OUTLINE_BBOX_COVER_RATIO`（本批不改预算，选环仍是"面积最大"）。

## 3. 本批不做

- 不改 `_find_cycles` 的枚举算法（不换"里层环优先/被框住的环"这类挑环策略）；
- 不识别尺寸文字 / 数值，不解析 `DIMENSION` 实体的展开线；
- 不动前端（用户看到的"白线"来自后端 `segments`，本批改的就是它）；
- 不新增图层角色、不改 `packaging_layer_rules.json`。

## 4. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_dimension_frame_not_part_ring_red -v   # 16 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_dimension_annotation_not_in_part_shape_red \
    tests.test_packaging_parts_extraction_red \
    tests.test_packaging_parts_outline_red tests.test_packaging_product_outline_red \
    tests.test_packaging_parts_outline_chaining_red tests.test_packaging_open_outline_part_needs_a_way_out_red \
    tests.test_packaging_cad_plan_true_outline_polygons_red tests.test_packaging_part_outline_hatches_coexist_red \
    tests.test_packaging_business_parts_outline_bbox_link_red \
    tests.test_packaging_business_part_plan_click_and_bound_outline_red                        # 不回退
```

真样本复核（实现后，只读）：酒盒缓存的 CAD IR 上 `cmp:139` 的 `outline.points` = 4、
`area_mm2 ≈ 58 851`、`unfolded = 219.643 × 267.94`；圆盘盒 `312 / 255 / role_known 9` 与
`closed_ratio 0.817` 逐字不变。

## 5. 红测清单（`tests/test_packaging_dimension_frame_not_part_ring_red.py`，16 条）

- A 组（判据，真跑纯函数）：A1 内折 5mm 那两段才是箭头；A2 单独一条 5mm 短段不许判；A3 90° 的 V 不是箭头；
  A4 完全共线的两段不算箭头；A5 确定性 + 乱序等价；A6 `ANNOTATION_ARROW_MAX_MM` 与
  `DIMENSION_ARROW_PAIR_COS` 必须在位且为负；
- B 组（形状，真跑 `extract()`）：B1 环是零件那 4 条边（60 000，不是框的 68 000）；B2 件尺寸 200×300、
  面积不等于框；B3 箭头段不许画进件内折线、零件自己的边一条不少；B4 逐件留痕（升序 + 闭集 reason）；
  B5 件身份（成员证据）按原始成员报；
- C 组（真样本，缺缓存/缺转换器即 skip）：C1 酒盒 `cmp:139`/`cmp:47` 是 4 元环 58 851.004、
  `134 / 129` 冻结值不动；C2 圆盘盒 `312 / 255 / 9 / 0` 零附带；
- D 组（护栏）：D1 配对纯函数与两个常量在源码里；D2 `if annotation_rows and outline is not None:` 与
  `elif annotation_rows:` 两处守卫在位；D3 本 Spec 在位且写明门槛与零附带读数。

纪律：A / B / D 组离线（合成夹具真跑 `extract()` 与纯函数、源码守卫），C 组要真样本；不连 PG / 34、
不发 HTTP、不写业务数据。禁止为了让红测转绿而修改本文件；口径变化请改 Spec。

## 5.1 授权：本批三处比 `## 485` §7 更严/更窄的地方（不是放宽）

1. **判据用"箭头对"而不是"≤ 6mm"**：`## 485` §7 明说"禁止只凭长度判标注"，本批把入口写成形态判据 +
   两份真样本对照，所以酒盒只改 2 件（而不是那 5 件）且圆盘盒零附带。
2. **"摘完变闭合"的件一律回退且不留痕**：`## 485` 的回退只覆盖"摘完求不出环"，这里再补一条
   "本来就求不出环的件不许因为摘标注而闭合"（`cmp:115` 一类），并**不留痕**（不许声称摘过）。
3. **修 `outline.entity_ids` 的归属缺陷**：只改**证据**（47 + 16 件的 `entity_ids`），形状 / 尺寸 / 状态 /
   统计一个字段没动 —— 这条是正确性修复，不是口径变更（对照读数见 §6）。

三处都不许被后续批次"顺手放开"（放开会让 §1 的 2 件 / 0 件附带立刻回头）。

## 6. 落地状态（2026-09-23，Codex 实现）

| 项 | 实测 |
| --- | --- |
| 本批红测（`Ran 16`） | **OK** |
| 红基（HEAD 工作副本 `## 485` + 本红测） | `Ran 16 … FAILED (failures=9)`：A1、A6、B1–B4、C1、D1、D2（D3 是 Spec 在位守卫） |
| 反向对照 A（④ 判据整条停用 → `{}`） | 6 红：A1、B1–B4、C1；酒盒 `annotation_filtered_total 43 → 3`、`cmp:139` 环回 **19 点 / 117 846.31 / 308.834 × 446.32** |
| 反向对照 B（放开"完全共线"：`cos ≤ DIMENSION_ARROW_PAIR_COS`） | 1 红：A4（共线的两段被当成箭头） |
| 反向对照 C（`if annotation_rows and outline is not None:` → `if annotation_rows:`） | 本红测 2 红（C1、D2）；酒盒 `closed 134 → 137 / open 129 → 126 / closed_ratio 0.51 → 0.521 / annotation_filtered_total 43 → 81`，`cmp:115` 变成 closed（14 点环）；既有模块 `test_packaging_business_parts_outline_bbox_link_red` D1 红（31 条里 1 红） |
| 保护网（本批改动后，真样本真跑） | `test_packaging_dimension_annotation_not_in_part_shape_red` + `…_refresh_line_business_count_red` + `…_2_1_first_paint_is_2d_not_3d_red` + `test_packaging_parts_extraction_red` → `Ran 64 OK`；outline 家族 8 模块 → `Ran 121 OK (skipped=1)` |
| 酒盒缓存 IR（263 件） | `263 / 134 / 129 / closed_ratio 0.51` **与 HEAD 逐字一致**；`annotation_filtered_total 3 → 43`；环/尺寸变 2 件、只有证据变 47 件、只有编号变 32 件 |
| 圆盘盒.dwg（门禁样本） | `312 / 255 / role_known 9 / closed_ratio 0.817` 与 `annotation_filtered_total 0` **与 HEAD 逐字一致**（零附带） |

改动文件：`tech_app/backend/services/packaging_parts.py`（④ 判据 + `extract()` 的形状替换与回退 +
`_outline_evidence()` 的 `original_edges`）。**未动** `MAX_LOOP_STATES` / `MAX_LOOP_CYCLES` /
`MIN_LOOP_EDGES` / `LOOP_TOLERANCE_MM` / `OUTLINE_BBOX_COVER_RATIO` / `_find_cycles` 的枚举口径，
未动前端 / 语义层 / 成本 / 工艺。

## 7. 已知缺口（本批**没有**解决，必须另立一批）

- **`cmp:115` 一类"框比零件大、且框不闭合"的件仍按几何包络报**：本批只把"摘掉箭头对之后还能求出环"的件
  救了回来；框里**没有**箭头对（或箭头对形态不满足 ④）的件仍会走原路（`## 485` §2.5 的回退）。
- **多边形近似环没动**：`cmp:52` / `cmp:876` / `cmp:892` / `cmp:908` / `cmp:1076` 的环由弧段近似拼出来，
  本批一个字没改（`## 485` §7 提到的 5 件里，本批只动了前两件）。
- **`outline.entity_ids` 的"证据"仍是"环边实体"而不是"这件由哪些实体构成"**：后者是
  `part["entity_ids"]`（未过滤成员）；公开零件行没有 `entity_total` 键，所以右栏零件面板那行"实体条数"
  目前是空的（`app.js` 读 `part.entity_total`）—— 要补就得给公开行加键，属于**契约变更**，另立一批。
- **挑环策略本身没换**：仍是"面积最大"。本批能成立，是因为"摘掉箭头对之后尺寸框不再闭合"；真正"被框住的
  里层环"这类件要靠另立 Spec 的挑环策略（`## 485` §7 第二条入口）。
