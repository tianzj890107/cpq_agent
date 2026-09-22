# BOM 回填的零件尺寸必须带来源：展开轮廓 ≠ 包围盒

血缘：承接 `packaging-dwg-parts-extraction.md`（C7 零件 ↔ BOM 行回填）、
`packaging-parametric-bom.md`（BOM 读接口与 `size_source` 形状）、
`packaging-parse-to-downstream-seams.md`（§3.2 配对复核披露）、`packaging-cost.md`（`cut_length × cut_width`）。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_bom_part_size_provenance_red.py`
实测：**`Ran 15 OK`**（A1 / A2 / B1 / B2 / C1 / C2 / C3 / D2 由红转绿，是回归锚点；A3 / A4 / B3 / D1 / D3 / D4 / D5 七条护栏保持绿）。
34 实测（项目 `73cdcaab61fc`）同一条 BOM 里已能同时读出两类来源：
`RB02001-P02/P03 → size_quality=bbox_only`、`RB02001-P08/P09 → size_quality=unfolded`。

## 0. 一句话目标

图纸零件回填进 BOM 行时，写进 `length_mm` / `width_mm` 的那个数字**必须带着它的来源**一路到底：
它是**闭合轮廓算出来的展开尺寸**，还是**未闭合零件的包围盒**。不许让这两类数字在任何读接口里长得一模一样。

## 1. 现状缺口（34 实测，可复现）

真跑：报价卡片 `fullchain-425ddfdd`（SM1 建卡）→ 技术项目 `5416443be409`（PE1 上传 `酒盒.dwg`，
ODA 27.1 → DXF，`flow-576b9d05d67be56e`，八步全 `completed`）→ BOM 33 行。

### 1.1 零件侧：4 件里有 3 件的"展开尺寸"其实是包围盒

`GET /api/projects/5416443be409/requirement/packaging-parts`（`parts_id=parts:1508ec744d6c4281`）：

```
DWG-P01  443.523 × 492.620   area 218488.3   outline_status=open    size_source=component_bbox   ← 未闭合，数字是包围盒
DWG-P02  440.123 × 482.920   area 212544.2   outline_status=open    size_source=component_bbox   ← 同上
DWG-P03  440.123 × 482.920   area 212544.2   outline_status=open    size_source=component_bbox   ← 同上
DWG-P04  398.024 × 446.320   area 176833.4   outline_status=closed  size_source=closed_outline   ← 闭合轮廓，真展开
```
（64 件里 `closed_outline 51 / component_bbox 13`；`圆盘盒.dwg` 9 件里 8 件是 `closed_outline`。）

### 1.2 BOM 侧：来源在回填时丢了

`GET /api/projects/5416443be409/requirement/packaging-bom`，4 行 `source=dwg_parts`，
`items[].size_source_json.dwg_binding` 全文：

```
RB02001-P02 ← DWG-P01  {"component_id": "cmp:47", "part_code": "DWG-P01",
                        "rule_id": "dwg_parts_row_pairing_v1", "fallback_paired": false,
                        "original_missing_variables": ["H盖"],
                        "pairing_basis": "位置配对：第 1 个待绑行 ↔ 面积第 1 大的零件",
                        "material_match": null}
RB02001-P03 ← DWG-P02  {… "material_match": true}
RB02001-P08 ← DWG-P03  {… "material_match": false}      ← 磁铁 ← 灰板面板
RB02001-P09 ← DWG-P04  {… "material_match": null}
```

`dwg_binding` 有 `component_id` / `part_code` / `rule_id` / `fallback_paired` /
`original_missing_variables` / `pairing_basis` / `material_match` ——
**没有 `size_source`，没有 `outline_status`，也没有任何"这个数字是不是包围盒"的标记。**

### 1.3 后果

- BOM 那 4 行里 **3 行**的 `length_mm` / `width_mm` 来自**未闭合零件的包围盒**，界面上与真展开尺寸显示完全一样；
- 成本引擎按 `PKG-C-MATERIAL`（`cut_length × cut_width`）算材料 —— 拿到的就是包围盒面积，
  **越大越贵**，而这份"贵"没有任何地方能看出是因为尺寸来源不同；
- 同一份 BOM 里 `RB02001-P08`（磁铁，钕铁硼 Ø10×2mm）拿到 440.123 × 482.92 —— 一个 0.21 m² 的磁铁，
  界面与报价单上看到的就是这个数字；
- `packaging-parse-to-downstream-seams.md` §3.2 披露的是**材料**不一致（`material_match`），
  尺寸来源这条**一个字都没披露**。

> 这不是"配对规则错了"，而是**来源没落地**：配对规则（位置配对）与"未闭合件用包围盒"都是第一版既定口径，
> 本批**不动**它们，只要求来源可见（见 §4 非目标）。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_parts.py` · `bind_rows()`
   - 每行绑定的 `dwg_binding` 新增三个键，值**逐字取自零件文档那一行**：
     - `size_source`：`"closed_outline"` / `"component_bbox"` / `"dwg_outline"`（沿用既有闭集，不新增取值）；
     - `outline_status`：`"closed"` / `"open"`；
     - `size_quality`：`"unfolded"`（`closed_outline` / `dwg_outline`）/ `"bbox_only"`（`component_bbox`）；
   - 现有键 `component_id` / `part_code` / `rule_id` / `fallback_paired` /
     `original_missing_variables` / `pairing_basis` / `material_match` 的**取值口径逐字不变**；
   - 配对规则（位置配对：待绑行顺序 ↔ 面积降序）**不动**。
2. `tech_app/backend/services/packaging_bom.py` · `_bind_parts()` / `build_bom()`
   - 原样透传上述三个键，使它们落在 `items[].size_source_json.dwg_binding` 里（键必须存在）；
   - `load_bom()` 读回路径同样带着它们；
   - **不改 `items[].length_mm` / `width_mm` 的取值**（本批只加来源，不改数字）。

禁止：不改零件提取的阈值与 `size_source` 闭集；不改成本公式与费率；不改前端；不新开接口；
不改 `stats` / `gaps` 键集；不落数据库 schema、不加表。

### 2.1 实现记录（`## 276`）：三个新键落在 `dwg_binding` 里，跟着 `size_source_json` 一起走

- **落点只有一处**：`packaging_parts.bind_rows()` 拼 `size_source["dwg_binding"]` 的地方补三个键
  （`size_source` / `outline_status` / `size_quality`），值逐字取零件文档那一行；既有七个键的取值
  与顺序一字未动，配对规则（位置配对）也没碰。
- **不需要改 `packaging_bom.py`**：`dwg_binding` 本来就在 `size_source` 里，`_assemble()` 把它
  序列化成 `size_source_json` 列、`da_repo` 落库、`load_bom()` 原样读回 —— 所以 B1（`build_bom()`
  返回值）与 B2（`load_bom()` 读回）自然同时成立，本批对 `packaging_bom.py` **一行未改**
  （Spec §2.2 的授权"原样透传"无需改动即成事实，因为透传路径本来就通）。
- **新增两个常量 + 一个纯函数**：`SIZE_QUALITY_UNFOLDED` / `SIZE_QUALITY_BBOX` /
  `SIZE_QUALITIES`、`UNFOLDED_SIZE_SOURCES`，以及 `size_quality_of(size_source)`。
  判据是"只有 `closed_outline` / `dwg_outline` 算展开，**其余（含来源缺失）一律 `bbox_only`**" ——
  §3 D3 要的正是这条：没有证据的数字不许被说成展开尺寸。
- **`size_source` / `outline_status` 缺失时**给空串（键始终存在，A1/B1 要求三个键都在）；
  不给假值、不猜来源。

## 3. 口径（逐条验收）

- **A1**：`bind_rows()` 返回的**每一条**行绑定，其 `dwg_binding` 必须同时含 `size_source` 与 `outline_status`，
  且与零件文档那一行逐字相等。
- **A2**：`size_quality` 取值闭集 `{"unfolded", "bbox_only"}`；`component_bbox` → `bbox_only`，
  `closed_outline` / `dwg_outline` → `unfolded`。
- **A3**（护栏）：配对规则不变——同一份零件文档、同一条需求单，绑定结果
  （`item_key` ↔ `part_code` 的对应与顺序）与改动前逐字相同。
- **A4**（护栏）：`original_missing_variables` / `pairing_basis` / `fallback_paired` / `material_match`
  的取值逐字不变（材料不一致仍是"披露"，不是"拒绝"）。
- **B1**：`build_bom()` 组装的 BOM 文档里，每一条 `source == "dwg_parts"` 的行的
  `size_source_json`（JSON 字符串解析后）的 `dwg_binding` 都能读到 `size_source` / `outline_status` / `size_quality`。
- **B2**：`load_bom()` 读回的同一份里也带（不是只在 `build_bom()` 的返回值里）。
- **B3**（护栏）：`length_mm` / `width_mm` / `stats` / `gaps` 与改动前逐字相同。
- **C1**：包围盒来源的行必须能一眼认出来（`size_quality == "bbox_only"`），不许与展开来源长得一样。
- **C2**：同一份 BOM 里展开来源的行必须是 `unfolded`——两类来源在同一份文档里**必须可区分**。
- **C3**：真样本形状（4 行里 3 行 `component_bbox`、1 行 `closed_outline`）必须能在读接口上被区分出来。
- **D1**（护栏）：零件文档侧 `size_source` 闭集不变。
- **D2**：`outline_status == "open"` 的零件，其 `size_source` 不许被写成 `closed_outline`。
- **D3**（护栏）：`size_source` 缺失时不许猜——宁可显式给 `bbox_only` 或按现有判定，
  不许因为"多了个新键"就把没有来源的零件算成 `unfolded`。

## 4. 非目标

- 不改"未闭合零件用包围盒"这条第一版口径本身（要不要让未闭合件进 BOM 是业务决定）；
- 不改配对规则（位置配对），不把包围盒来源变成**拒绝**——本批只要求**来源可见**；
- 不动成本公式与费率、不动前端渲染、不加接口、不改数据库。

## 5. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_part_size_provenance_red -v  # 本批
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v          # 32 OK 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red -v            # 57 OK 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parse_to_downstream_seams_red -v # 本批不回归
```

真样本复验（34）：跑完一键解析 + BOM 后，
`GET .../requirement/packaging-bom` 的 4 行 `dwg_parts` 里
`size_source_json.dwg_binding.size_quality` 必须能区分出 3 个 `bbox_only` 与 1 个 `unfolded`。
