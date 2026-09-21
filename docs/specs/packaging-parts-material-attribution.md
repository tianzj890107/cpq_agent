# 包装图纸零件：材料与厚度的归属（覆盖率）

状态：Spec + 红测（未实现）
红测：`tests/test_packaging_parts_material_attribution_red.py`

血缘：承接 `packaging-dwg-parts-extraction.md`（零件提取）、`packaging-parts-true-outline.md`（真实轮廓）、
`packaging-parts-downstream-process-and-cost.md`（第 3 层 `processability` 三道门槛）、
`packaging-parts-downstream-acceptance.md`（第 5 层指标与门槛）。
本 Spec **不放宽**第 3 层任何一条拒绝口径，只解决"真图上绝大多数零件根本取不到材料/厚度"。

## 0. 一句话目标

真刀模图上材料/厚度是**成组**写着的（一张图一条整体材料说明 + 分件引出标注 + 分区分组注记），
今天"一件一件按最近标注取"在真图上必然覆盖不到。本 Spec 把归属改成分层取法：

**件级引出标注 > 成组注记 > 图层名 > 需求整盒口径**，每层都必须留出处；
哪一层都取不到就继续留空（第 3 层照旧拒绝并说清缺什么），**绝不用无出处的默认值硬算**。

## 1. 现状缺口（34 上真跑证据，逐条可复现）

项目 `f1417060ae9d`（`酒盒.dwg`，`parts_id=parts:b435f8c89cf9bbeb`，
`GET /api/projects/f1417060ae9d/requirement/packaging-parts`）：

| 事实 | 值 |
| --- | --- |
| `part_total` | 64 |
| `material` 非空 | 12（18.8%） |
| `thickness_mm` 非空 | 8（12.5%） |
| `processable_ratio` | **0.062**（4 件） |
| 被拒 | 47 × `PACKAGING_PART_MATERIAL_UNKNOWN`（+ 13 × `PACKAGING_PART_NOT_CLOSED`，归 `packaging-parts-outline-chaining.md`） |

### 1.1 两档取法在真图上必然取不到（`packaging_parts.py:119-330`）

现口径只有两档：① 件级最近标注（半径 `max(25% × 对角线, 50mm)`）；② 图级兜底（**全图该字段只有一个值**时才用）。

- 图级兜底在真图上恒不成立：酒盒一张图里有 6 种材料文本（`350g粉灰`、`2.5mm灰板`、
  `1.8mm 灰板裱光银纸`、`名称：内盒2灰板 材料：2mm灰板`、`左盒盒背灰板`、`底板：2.5MM灰板`）；
- 件级只覆盖"贴着标注"的 12 件，其余 52 件轮廓附近没有任何标注 → 恒空。

### 1.2 半径被件尺寸放大 → 大件吞掉图级标注（误归属，比留空更危险）

`NOTE_DISTANCE_RATIO = 0.25` 对 443×492 的件给出 **166mm** 半径。实测这些归属是错的：

| 件 | 展开尺寸 | outline | 拿到的材料 | `distance_mm` |
| --- | --- | --- | --- | --- |
| `DWG-P02` | 440.1×482.9 | open | `右盒盒背灰板` / `1.8mm 灰板裱光银纸` | 3.8 / 35.6 |
| `DWG-P03` | 440.1×482.9 | open | `右盒盒背灰板` / `1.8mm 灰板裱光银纸` | 2.0 / 50.0 |
| `DWG-P09` | 217.1×482.9 | open | `1.8mm 灰板裱光银纸` | 50.0 / 50.0 |
| `DWG-P10` | 217.1×482.9 | open | `右盒盒背灰板` / `1.8mm 灰板裱光银纸` | 2.0 / 62.7 |

这 4 件是整版大件（`component_bbox` 尺寸、`outline=open`），拿到的其实是**图纸整体材料说明**。
它们带着材料进第 3 层就会以错误前提算成本 —— 所以"取不到留空"比"最近即赢"更安全。

### 1.3 需求 3.3 的材料口径完全没参与

需求里已有 `grey_board` / `grey_board_thickness` / `face_paper` / `face_paper_gsm` / `lining_paper`
（`industry_templates.PACKAGING_SPEC` 3.3），`packaging_parts` 读都不读 → 本该有的"整盒默认口径"这一层是空的。

### 1.4 整句被当成材料名

实测行里出现 `material = "名称：内盒2灰板 材料：2mm灰板"`、`"右盒盒背灰板"`、
`"1.8mm 灰板裱光银纸"` —— 材质族、规格、厚度全糊在一句话里，下游（BOM 行配对、
成本材料分类 `_material_class`）拿不到可比字段。

## 2. 口径：四层归属（优先级从高到低，逐条留痕）

```python
MATERIAL_ATTRIBUTION_RULE_ID = "part_material_attribution_v1"
ATTRIBUTION_KINDS = ("part_note", "group_note", "layer_name", "requirement_default")
NOTE_DISTANCE_MAX_MM = 150.0        # 件级半径的硬上限（防止 0.25×对角线 在整版件上失控）
GROUP_NOTE_RADIUS_MM = 300.0        # 成组注记的覆盖半径
PARTITION_KEYWORDS = ("左盒", "右盒", "内盒", "外盒", "盒盖", "底盒", "底板", "围条", "内托", "面纸", "衬纸")
REQUIREMENT_MATERIAL_FIELDS = ("grey_board", "grey_board_thickness",
                               "face_paper", "face_paper_gsm", "lining_paper")
AMBIGUOUS_DISTANCE_MM = 5.0         # 最近两条候选距离差 <= 该值且取值冲突 → 弃权
```

| 层 | 来源 | `kind` | 可填 | 采用条件（全部满足才可采用） |
| --- | --- | --- | --- | --- |
| 1 | 件级引出标注 | `part_note` | material + thickness | `outline_status == "closed"`；距离 ≤ `min(0.25×对角线, NOTE_DISTANCE_MAX_MM)`；该注记不落在 ≥2 件的件级半径内（同时贴多件 → 属于图级/成组，见层 2） |
| 2 | 成组注记（KV 文本 / 分区注记） | `group_note` | material + thickness | 注记能解析出 `材料：X` 或命中 `PARTITION_KEYWORDS`；覆盖范围 = 与注记点距离 ≤ `GROUP_NOTE_RADIUS_MM` 的**闭合件**，覆盖集合逐件写进 `attribution.covers` 与同一 `evidence_ref` |
| 3 | 图层名带材料 | `layer_name` | 只填 material | 图层名命中 `MATERIAL_KEYWORDS`（`灰板层` / `面纸层`…）；**不许**给 thickness |
| 4 | 需求整盒口径 | `requirement_default` | material + thickness | 前 3 层都没定下该字段时，用需求 3.3 的整盒口径兜底；必须写 `needs_confirmation=true` 与 `assumption_refs` |
| — | 都取不到 | `null` | — | 留空 + `attribution.notes` 记 `no_evidence` |

### 2.1 硬纪律（不许被本 Spec 放宽）

1. **无出处的值一律不填**：任何一层都必须带 `evidence_ref`（层 4 写需求字段名，如 `requirement.grey_board_thickness`）。
2. **`open` / `unavailable` 件不许取得任何材料或厚度**（层 1/2 都要求 `closed`；层 4 同样只服务闭合件）。
   理由见 §1.2：非闭合件的"最近标注"是偶然命中，实测 9 件全错。
3. **歧义即弃权**：同一件被**同一层**的多条**取值不同**的候选命中，且最近两条的距离差
   `<= AMBIGUOUS_DISTANCE_MM` 时，该字段留空并记 `ambiguous:<候选数>`；**不许**取"最近那条"了事。
4. **跨层不许倒挂**：件级标注永远最可信 —— 层 2/3/4 不许覆盖层 1 已定下的字段。
5. 层 4 兜底必须**可见**：行上带 `needs_confirmation=true`、`assumption_refs=["requirement.grey_board_thickness"]`；
   下游工艺/成本结论里必须出现"按需求整盒口径"字样（见 §5）。

## 3. 结构化材料文本

`material` 必须是**短标签**（材质族 + 规格），`thickness_mm` 必须是独立浮点：

| 原文 | `material` | `thickness_mm` |
| --- | --- | --- |
| `350g粉灰` | `粉灰 350g` | `None` |
| `2.5mm灰板` | `灰板` | `2.5` |
| `1.8mm 灰板裱光银纸` | `灰板裱光银纸` | `1.8` |
| `名称：内盒2灰板 材料：2mm灰板` | `灰板`（`partition="内盒2"`） | `2.0` |
| `右盒盒背灰板` | `灰板`（`partition="右盒"`） | `None` |
| `底板：2.5MM灰板` | `灰板`（`partition="底板"`） | `2.5` |

规则：
- `名称：`/`材料：`/`材质：` 这类 KV 前缀必须切掉，**不许把整句塞进 `material`**；
- 分区词（`PARTITION_KEYWORDS`）从原文里摘出来写进 `attribution.partition`，**不从 `material` 里删掉材质词**；
- 解析不出材质族时只填厚度、解析不出规格时只填材质 —— 宁可少填。
- 原文（截断到 60 字）保留在 `material_source.text`，供人工核对。

## 4. 指标与门槛

`packaging_parts.summarize(doc)` 必须返回（第 5 层既有五个之外新增）：

| 指标 | 定义 |
| --- | --- |
| `material_known_ratio` | `material` 非空件数 / `part_total` |
| `thickness_known_ratio` | `thickness_mm` 非空件数 / `part_total` |
| `material_default_ratio` | `material_source.kind == "requirement_default"` 的件数 / `part_total` |
| `attribution_kind_mix` | `{"part_note": n, "group_note": n, "layer_name": n, "requirement_default": n, "none": n}` |

真实样本门槛（`酒盒.dwg`，本机样本 + 任一可用转换器；改门槛必须改本文件）：

| 门槛 | 值 | 今天 |
| --- | --- | --- |
| `material_known_ratio` | `>= 0.75` | 0.188 |
| `thickness_known_ratio` | `>= 0.75` | 0.125 |
| `processable_ratio` | `>= 0.70` | 0.062 |
| 误归属 | `open` 件的 `material`/`thickness_mm` **必须全空** | 9 件有值（全错） |
| 兜底可见 | `needs_confirmation` 件数 == `requirement_default` 件数 | 无该字段 |

## 5. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：新增 §2 常量与 `attribute_materials()`（纯函数）、
   §3 结构化解析、`extract()` 接受 `options["requirement"]` 并调用归属、`summarize()` 补 §4 指标。
2. `tech_app/backend/services/packaging_drawing_flow/steps.py`：`parts_extract` 步骤把需求 3.3 材料字段
   作为 `options["requirement"]` 传进 `extract()`（源码里必须出现 `requirement`）。
3. `tech_app/backend/main.py`：零件详情/列表接口透出 `material_source` / `thickness_source` / `attribution` /
   `needs_confirmation`（不新增路由，只补字段）。
4. `tech_app/backend/services/packaging_cost.py`：结论里带上 `assumption_refs`（用到需求整盒口径时必须明示）。
5. `changelog/changelog_9_21_25.md`：按周记录。

## 6. 禁止事项

- 不许放宽第 3 层三道拒绝码（`PACKAGING_PART_NOT_CLOSED` / `PACKAGING_PART_MATERIAL_UNKNOWN` /
  `PACKAGING_PART_NOT_FOUND`）与"缺料不许给默认料厚"；
- 不许改 `LOOP_TOLERANCE_MM` / `OUTLINE_STATUSES` / `SIZE_SOURCES` / `DEFAULT_OPTIONS` / `PART_CODE_FORMAT`；
- 不许改 `tests/` 下任何文件（含本批红测与第 1～5 层红测）；
- 不许在 `packaging_parts` 里调模型、联网或读需求以外的外部状态；
- 不许把 `requirement_default` 标注成 `part_note`（兜底必须看得见）；
- 不许 commit / push / tag / Release / 部署。

## 7. 红测

`tests/test_packaging_parts_material_attribution_red.py`（实现前必须失败）：

- A 常量与闭集：`ATTRIBUTION_RULE_ID` / `ATTRIBUTION_KINDS` / `NOTE_DISTANCE_MAX_MM` / `AMBIGUOUS_DISTANCE_MM` /
  `PARTITION_KEYWORDS` / `REQUIREMENT_MATERIAL_FIELDS` 在位；`attribute_materials` 可调用。
- B 四层归属：件级（含半径硬上限、同注记贴多件时不得采用）、成组注记（一条覆盖多件、`covers` 逐件）、
  图层名（只给 material）、需求兜底（带 `needs_confirmation`/`assumption_refs`）、优先级不倒挂、
  歧义弃权、`open` 件恒空、结构化文本切分。
- C 指标：`summarize()` 含 §4 四个新指标；`part_total=0` 时比值 `0.0`、`attribution_kind_mix` 全 0。
- D 护栏：三道拒绝码字面不变；`as_ir_part` / `processability` 行为不变；`extract()` 仍确定性。
- E 真实样本门槛（本机有样本才跑）：§4 表格逐项断言。

## 8. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_material_attribution_red -v   # 红→绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_red -v             # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_gate_red -v        # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v         # 不回归
```
