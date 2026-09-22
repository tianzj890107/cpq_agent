# 包装图纸零件：连通分量必须按「端点相接」，不许按「包围盒相交」吞并

血缘：`packaging-dwg-parts-extraction.md`（## 226「零件 = 连通分量」）定了**零件是什么**，
但没定**怎么分组**；本层就是那个缺口。下游 `packaging-parts-true-outline.md`（件内最大闭合环）、
`packaging-parts-material-attribution.md`、`packaging-parts-downstream-process-and-cost.md`
全部建在分量之上，所以这一层错了，上面每一层都在错的输入上收敛。

状态：Spec + 红测（已实现）（A/B/C 组与真样本 D 组已转绿；实现由并行会话落地在
`cad_ir/geometry.py` + `packaging_parts.py` + `main.py` + `app.js`，已提交 `66a532f`，见 changelog `## 304`（「零件下游做不下去的两处根因定位并立契约…」））
红测：`tests/test_packaging_parts_components_red.py`

## 0. 一句话目标

把"零件"的分组判据从**包围盒相交**换成**几何相接**（线段端点、圆弧/圆端点、折线顶点），
让真图上"一条长斜线把半张图框进自己 bbox"不再把不相关的件合并成一块；
并且把"被丢掉的分量有多少、为什么"从后端一路显示到 2.1 零件树。

## 1. 现状缺口（本机实测，逐条可复现）

### 1.1 分组判据是 bbox 相交

```python
# tech_app/backend/services/cad_ir/geometry.py:131
def boxes_touch(first, second, tolerance): ...          # 两个包围盒有重叠/相接即 True
# tech_app/backend/services/cad_ir/geometry.py:139
def components_of(rows, tolerance):                     # 并查集，按 boxes_touch 合并
```

一条从 `(0,0)` 到 `(1000,1000)` 的斜线，bbox 覆盖整个 `1000×1000` 方块；
落在方块里的任何不相干实体都会被并进同一件。实测（3 条互不相接的实体）：

| 输入 | 今天的结果 |
| --- | --- |
| `diag(0,0)-(1000,1000)` + `sq1(900,900)-(950,950)` + `sq2(920,900)-(960,940)` | **1 个分量**，`entity_ids` 三条全在（应为 3） |

### 1.2 真图 `酒盒.dwg` 的后果

`402` 个分量里，`4` 个是吞并块（长边 / 面积远超单件）：

| bbox (mm) | 面积 (mm²) | 含实体数 | 命运 |
| --- | --- | --- | --- |
| 4451.8 × 3117.9 | 13 880 251 | **77** | `area_over_max` → 永久丢弃 |
| 3927.8 × 967.9 | 3 801 857 | **6** | `area_over_max` → 永久丢弃 |
| 1706.0 × 713.3 | 1 216 853 | **78** | `area_over_max` → 永久丢弃 |
| 1706.0 × 713.3 | 1 216 853 | **71** | `area_over_max` → 永久丢弃 |

- 这 4 块**不是图框**：图框是另外两个整版闭合件（已被 `packaging-parts-true-outline.md` 正确挡掉）；
- 它们内部合计 **232 条实体**，其中真正的零件被一起丢掉，且丢弃**不留痕**；
- 全图分量最大长边 4451.8mm，而单件上限 `max_edge_mm = 1200`；
- `filtered_total = 192` 里，"真·碎线噪声"和"被吞并的真零件"混在同一个数字里，谁也分不开。

（9-22 落地后实测：`components_of()` 改按端点相接，`酒盒.dwg` 分量从 402 涨到 **1163**；
检查 1163 个分量里"成员全为 LINE/ARC/CIRCLE/POLYLINE"的那些，**0 个**内部端点不连通。
剩下的超大分量（`4451.8 × 3117.9` / `1706.0 × 713.3`）在端点口径下**确实自成一个连通体**，
它们不是假象，只是必须按 §2.4 记账、不许静默消失 —— 所以 §2.3 判"内部必须连通 + 大件必须留痕"，
不判"不许有大件"。）

（顺带：这就是 2.1 上"零件看起来不完整/数量不对"的根因。`max_parts=64` 的截断
另有一笔账 `truncated = 146`，与吞并**不是**同一件事，见 §2.4。）

## 2. 口径（可直接验收）

### 2.1 分量分组判据 = 端点相接

- `components_of(rows, tolerance)` 改为：**两个实体相接 ⇔ 存在一对端点距离 ≤ `tolerance`**；
- 端点来源（与 `packaging-parts-true-outline.md` §2 落盘的坐标一致）：
  `LINE.start/end`、`ARC` 两端点、`CIRCLE`（无端点 → 只与自己同类相接时按圆心 + 半径判同圆）、
  `LWPOLYLINE/POLYLINE` 的 `attributes.points` 首尾、`SPLINE` 的 `fit_points` 首尾；
- 一个实体的端点集合为**空**（IR 没落坐标）时：**不许**用 bbox 兜底把它并进任何分量
  （宁可是单件不可信，也不许吞并）；这类实体在 `stats.ungroupable_total` 里计数；
- `bbox` 只用于**输出**（分量包围盒、面积、`edge_over_max` / `area_over_max` 判据），
  **不再参与分组**；
- 排序/编号确定性不变：分量按"成员最小下标"排序，`component_id = "cmp:%d"`。

### 2.2 不许吞并，也不许把真件拆碎

| 场景 | 期望 |
| --- | --- |
| 斜线 + 落在其 bbox 内的独立方块 | **2 个（或更多）分量**，斜线单独一件 |
| `(0,0)-(100,0)` 与 `(100,0)-(100,50)` | **1 个分量**（端点重合，照旧合并） |
| 首尾相接的 4 条 LINE 组成矩形 | **1 个分量**（不得因为不在 `polyline` 类型上而拆散） |
| 端点差 0.5mm 的两条线 | 在 `tolerance = 1.436e-05` 下 **2 个分量**（不许无限放宽）；真图 402 分量在 0.10mm 容差下数量不变，这就是"线本来就接上了"的证据 |

### 2.3 真图验收

`酒盒.dwg` 上：

- `len(components) >= 402`（允许变多，**禁止变少**）；
- **结构性不变量（核心）**：任何 `len(entity_ids) >= 2` 的分量，其成员必须能用"端点相接"
  串成**一个**连通体；即"分量内部再次按端点相接分组，子组数必须为 1"。
  违反它的分量就是吞并块 —— 这一条**不依赖**图层名、不依赖阈值、不依赖图框识别，可离线复核；
- 长边 > `max_edge_mm(1200)` / 面积 > `max_area_mm2(1_000_000)` 的分量**允许存在**
  （那本来就是整版展开料，本来就该被挡在零件之外），但必须满足两件事：
  1. 出现在 `filtered[]` 里且带 `entity_total` 与 `reason`；
  2. 在 `filtered_reason_mix` 里按 `edge_over_max` / `area_over_max` 记账，且各项之和 == `filtered_total`；
- `stats.ungroupable_total` 必须存在（可为 0，但键必须在）。

（圆盘盒 `圆盘盒.dwg` 的对照：14 分量 / 12 个含闭合环，改动后这些数不许下降。）

**为什么把"大件"从拒绝改成"允许但要记账"**：实测 `酒盒.dwg` 上 `4451.8 × 3117.9`（77 条）
与 `1706.0 × 713.3`（78 / 71 条）这三块，在"端点相接"口径下**仍然自成一个连通体**（组内 40 / 14 / 57
个互不相接的子组，即它们本来就是"一次连线连出来的整版"），不是纯 bbox 假象；
要求"真图上不存在长边 > 1200 的分量"会把**正确的分组**也判成错的。
真正的缺陷是"它们被丢掉时不留位置与原因"，所以口径落在**可核对**而不是"消灭大件"。

### 2.4 被丢掉的东西必须看得见（后端 → 2.1）

- `extract()` 的 `stats` **新增**（既有键一个都不许去掉/改名）：
  `filtered_reason_mix`（`{reason: count}`，`reason` ∈ `REASON_CODES`）、
  `filtered_edge_over_max_total` / `filtered_area_over_max_total` /
  `filtered_area_under_min_total` / `filtered_no_curve_entity_total`、
  `truncated`（保留原义：因 `max_parts` 未列出的件数）；
- `filtered[]` 里的每一条必须能**定位**：带 `component_id` / `bbox` / `reasons`（今天已有），
  并且带 `entity_total`（成员实体数）—— 没有它就没法判断"这一块是不是吞并块"；
- `summarize()` 必须透出 `filtered_total` 与 `filtered_reason_mix`；
- `GET /api/projects/{pid}/requirement/packaging-parts` 的 `summary` 里必须能读到这两项；
- 2.1 零件树（`tech_app/frontend/app.js`）必须把三笔账**分三句**说清楚，禁止合并成一句：
  - 列出的零件数；
  - 因上限未列出的件数（`truncated > 0` → `还有 N 件未列出（只显示前 M 件）`，**今天已有，不许改文案口径**）；
  - 被过滤掉的分量数 + 原因前两位（`另有 K 个图元分组未成为零件（面积超限 X / 长边超限 Y …）`）。

### 2.5 冻结面

- `PART_CODE_FORMAT` / `REASON_CODES` / `DEFAULT_OPTIONS` 的取值与顺序不变；
- `component_id` 的格式 `"cmp:%d"` 不变；
- `packaging-dwg-parts-extraction.md` 的 `filtered[]` / `available` / `unavailable` 结构不变，
  本层只**加**键；
- 不许改 `packaging-parts-true-outline.md` 的"件内最大闭合环"口径，也不许改
  `packaging-product-outline-and-die-layer-roles.md` 的产品级候选口径。

## 3. 验收标准

| 组 | 断言 |
| --- | --- |
| A 合成 | 斜线 + 独立方块 → 分量数 ≥ 2；端点重合两条线 → 1 个；首尾相接矩形 4 条线 → 1 个；端点差 0.5mm → 2 个 |
| A 不留兜底 | 无坐标实体 → 各自单独成件，`stats.ungroupable_total` 计数，且不得与任何其他件合并 |
| B 指标 | `summarize()` 有 `filtered_total` / `filtered_reason_mix`；`extract().stats` 有 §2.4 全部新键；既有键逐一保留 |
| C 前端 | `app.js` 同时出现"未列出"与"未成为零件"两套提示（不许只有前者）；`main.py` 读接口透出 `filtered_reason_mix` |
| D 真样本（gated） | `酒盒.dwg`：分量 ≥ 402、**所有多成员分量内部端点连通（子组数 == 1）**、超大分量必在 `filtered[]` 里带 `entity_total`、`filtered_reason_mix` 各项之和 == `filtered_total`；`圆盘盒.dwg` 分量 ≥ 14 |

## 4. 明确不做

- 不做拼版/套裁识别，不做图框自动裁剪；
- 不引入 `shapely` / `numpy` / `networkx` 等新依赖（并查集已够）；
- 不为了让数量好看而放宽 `max_edge_mm` / `max_area_mm2`（阈值是冻结值）；
- 不改 `tests/` 下任何既有文件；不 commit / push / tag / Release / 部署。

## 5. 命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_components_red -v   # 当前必红
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v   # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_red -v      # 不回归（有已记录冲突）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_product_outline_red -v    # 不回归
CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -m unittest \
  tests.test_packaging_parts_components_red.RealSampleComponents -v                        # 真样本 D 组
```
