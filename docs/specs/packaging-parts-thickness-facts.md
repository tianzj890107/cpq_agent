# 包装图纸零件：料厚事实（克重不给厚 + 厚度跨材料串味 + 人工补料厚）

血缘：承接 `packaging-parts-material-attribution.md`（第 3 层材料/厚度归属）、
`packaging-parts-downstream-process-and-cost.md`（第 3 层 `processability` 三道门槛）、
`packaging-parts-3d-extrusion.md` / `packaging-parts-solid-coverage.md`（第 4 层挤出覆盖率）、
`packaging-cost-gaps-closure.md`（成本侧「厚度 × 密度 → 克重」，本层是它的**反向**）。

状态：Spec + 红测（已实现）（由并行会话落在 `packaging_parts.py` + `main.py` + `app.js`，已提交 `66a532f`，
见 changelog `## 305`（「覆盖率口径的诚实性…」，同批由并行会话提交）；离线 15 条 + 真样本 2 条实测 `Ran 17 OK`）
红测：`tests/test_packaging_parts_thickness_facts_red.py`

## 0. 一句话目标

零件下游（工艺路线、成本、3D 挤出）**唯一共同的卡点是 `thickness_mm`**；本层只补这一件事：
**克重能推的必须推出来并留痕，推不出来的必须看得见并补得进去，材料对不上的必须拒绝并记冲突** ——
不许默认料厚，也不许把「面纸」贴上「灰板」的厚度。

## 1. 现状缺口（本机实测，同一份 `酒盒.dwg`，逐条可复现）

复现命令（约 10 秒）：`PYTHONPATH=. ./open-claude/.venv/bin/python` 里跑
`parse_dxf(Path("酒盒.dxf").read_bytes())` → `S.analyze(ir)` → `P.extract(ir, sem)`
→ `X.extrude_all(doc["parts"])`；样本可用 `/opt/homebrew/bin/dwg2dxf -o 酒盒.dxf 酒盒.dwg` 得到。

| 事实 | 实测值 |
| --- | --- |
| 零件总数 | 64 |
| 有 `material` | 21（`material_source.kind`：`part_note` 9 / `group_note` 12） |
| 有 `thickness_mm` | **9**（`group_note` 7 / `part_note` 2） |
| `processability()` 通过 | **9**；其余 = `PACKAGING_PART_MATERIAL_UNKNOWN` 51 + `PACKAGING_PART_NOT_CLOSED` 4 |
| `extrude_all()` | `ok_total=9`、`unsupported_total=55`、`unsupported_reason_mix={"outline_open":4,"thickness_unknown":51}` |

### 1.1 「有材料、没料厚」的 12 件（克重写不进料厚）

这 12 件的 `material` 都是**克重写法**（`\d+g`），没有任何 `mm`，`_note_thickness()` 因此返回 `None`：

| material 标签 | 件数 | 出处原文（节选） |
| --- | --- | --- |
| `PET光银 225g` | 8 | `名称：右盖面纸 材料：225G铜版底PET光银` |
| `粉灰 350g` | 3 | `350g粉灰` |
| `白卡底PET光银裱A9 E坑 235g` | 2 | `235g白卡底PET光银裱A9 E坑` |
| `灰板`（无 mm，仅点名） | 1 | `左盒盒背灰板` |

### 1.2 已有料厚的 9 件里，2 件是**跨材料串味**（比缺料厚更危险）

> 2026-09-22 修订：下表是**旧分量集（402 个分量）**下的实测；`packaging-parts-component-chaining.md`
> 落地后分量数变为 1163，`DWG-P31` / `DWG-P47` 这两个编号已不对应同一批件，真图当前实测
> `thickness_conflict_total = 1`。规则本身（§2.2 跨材料不许采用）不变，红测改为"≥1 且无残留"。

| part_code | material | thickness_mm | thickness_source.text |
| --- | --- | --- | --- |
| `DWG-P31` | `PET光银 225g` | **2.0** | `名称：内盒2灰板 材料：2mm灰板` |
| `DWG-P47` | `PET光银 225g` | **2.0** | `名称：内盒3灰板 材料：2mm灰板` |

`225G 铜版底 PET 光银` 是**面纸**，拿到的却是同半径另一条**灰板**成组注记的 2mm。
`attribute_materials()` 的层 2 对 `material` 与 `thickness_mm` **各自独立取最近注记**，
没有任何"这一条注记的材质词和这一件的材料是不是一回事"的判据 ——
于是这 2 件带着来源看着齐全、数值却错的料厚，一路通过 `processability()` 和 `extrude_all()`。
（`packaging-parts-solid-coverage.md` 把「覆盖率必须是真值」当 KPI，这两件会**虚高**覆盖率。）

### 1.3 没有人工补料厚的入口

- `grep -rn "thickness" tech_app/backend/main.py` 只有**读**零件文档；
  `packaging_parts.py` 里有 `save_part_process` / `save_part_cost`，
  **没有** `set_manual_thickness` / `save_part_thickness` 一类写入口；
- 前端 `tech_app/frontend/app.js` 的零件树只有「材料 / 厚度」两列**显示**，没有补录控件；
- 需求 3.3 的整盒兜底口径（`grey_board_thickness` 等）只能给"整盒同厚"，救不了逐件。

结论：42 件连材料都没有、12 件有材料没料厚、2 件料厚串味 —— **55 件不可挤出里 51 件是同一件事**，
而这件事今天既不会自己算，也没人能补。

## 2. 口径（可直接验收）

### 2.1 克重 → 料厚：唯一合法路径是「克重 ÷ 密度」

- 换算式**必须逐字**为：`thickness_mm = gsm / (density * 1000)`，`gsm` 单位 `g/㎡`、
  `density` 单位 `g/cm³`，结果四舍五入到 **3 位小数**；
- `gsm` 只从**该件已采纳的材料注记文本**里取（`_GRAMMAGE`，`\d+g` / `\d+克`）；
  `2mm` / `1.8mm` / `2.5MM` / `35mm` 一律**不是**克重（沿用 `quick-quote-10-material-gsm-attribution.md` §C2）；
- `density` 只从调用方传入的材料表取（见 2.3），**取不到就保持 `None`**；
- 生效时 `thickness_source` 必须是：
  `{"kind": "derived_from_gsm_density", "text": <注记原文>, "evidence_ref": <注记证据>,
    "gsm": <数值>, "density": <数值>, "material_code": <材料表主键>, "distance_mm": <数或 null>}`；
- 生效时该件 `needs_confirmation` 必须为 `True`（推导值可以用于 3D 预览与待确认报价，
  **不得**被当成已确认事实）。

### 2.2 料厚不许跨材料串味

- 候选注记带材质词（`note["material"]` 非空）时，只在它与该件已定材料**材质词集合有交集**时才可采用；
  「材质词集合」= `MATERIAL_KEYWORDS` 里出现在该标签中的那些词；
- 不相交 → **不采用**该料厚，件保持 `thickness_mm=None`，并在这件的 `attribution` 里追加一条：
  `{"reason": "thickness_material_conflict", "note_ref": <evidence_ref>, "note_text": <原文>,
    "note_material": <注记材质标签>, "part_material": <件的材料标签>}`；
- 注记不带材质词（例如显式 `厚度2MM`）时**不判冲突**，照旧采用；
- 该件的材料**未定**时也不判冲突（没得比），照旧采用 —— 与今天行为一致，防误伤；
- 同一件既可能有多条冲突，按 `(note_ref, note_text)` 排序去重。

### 2.3 材料表是**入参**，不是零件层去读库

- `extract(ir, semantics=None, *, options=None)` 的签名与既有键**一个字都不许改**；
  新增信息一律走 `options["material_table"]`（可选，缺省 `None`）；
- `options["material_table"]` 是 `[{material_code, name, grade, density}, ...]` 的纯数据；
  材质词匹配只在 `name` / `grade` / `spec` 三个字段里找 `MATERIAL_KEYWORDS`，
  全表唯一命中且 `density > 0` 才用；命中 ≥2 条 → 弃权（宁可不推，见 §2.4）；
- `packaging_parts` 全程**不读库、不联网、不读需求以外的外部状态**（与
  `packaging-parts-material-attribution.md` §禁止事项一致）；由链路步骤（`parts_extract`）从知识库
  取材料表后作为 `options` 注入。

### 2.4 推不出来必须**看得见**，不许静默

- 件级 `attribution` 新增 `thickness_unresolved`（`[{reason, ...}]`），`reason` ∈
  `("density_missing", "material_ambiguous", "no_grammage", "material_unknown")`，
  只在"有材料注记、却仍然没有料厚"的件上出现：
  - 材料有克重、材料表里找不到 / 找不到唯一密度 → `density_missing` / `material_ambiguous`；
  - 材料标签里没有克重也没有 mm → `no_grammage`；
  - 连材料都没有 → `material_unknown`；
- `summarize()` **新增**四个键（既有键一个都不许去掉、不许改名、不许换类型）：
  `thickness_known_total` / `thickness_unknown_total` / `thickness_conflict_total` /
  `thickness_manual_total`；
- `thickness_source.kind` 的合法闭集扩为：
  `("part_note", "group_note", "layer_name", "requirement_default",
    "derived_from_gsm_density", "manual")`。

### 2.5 人工补料厚（生产入口）

- 新增纯函数 `set_manual_thickness(row, thickness_mm, *, bound_by, reason="")`：
  返回**副本**，写 `thickness_mm` 与
  `thickness_source = {"kind": "manual", "text": reason, "bound_by": bound_by, "distance_mm": null}`，
  并清掉该件 `attribution` 里同字段的 `thickness_unresolved` / `thickness_material_conflict`；
  `thickness_mm <= 0` 或非有限数 → `ValueError`（不许用 0 表示"没填"）；
- 新增落库 `save_part_thickness(project_id, part_code, thickness_mm, *, bound_by, reason="")`
  与读回 `load_part_thickness(project_id, part_code)`（版本化，照 `save_parts` 的 `meta_backend.get_doc/put_doc` 范式）；
- 路由：`POST /api/projects/{pid}/requirement/packaging-parts/{part_code}/thickness`
  （写权限**直接引用** `packaging_match.BOX_MATCH_DECIDE_ROLES`，不许另抄一份），
  `GET .../packaging-parts/{part_code}/thickness` 纯读；非法数值 → 400，件不存在 → 404；
- 前端 `tech_app/frontend/app.js` 的零件树：料厚为空的行必须能看到"补料厚"入口，
  提交后行内 `thickness_mm` 与来源（`manual`）就地更新，不许整页重载。

## 3. 验收标准

| 组 | 断言 |
| --- | --- |
| A 推导 | `材料：灰板 1500g` + 材料表 `灰板 density=0.75` → `thickness_mm == 2.0`、`kind == "derived_from_gsm_density"`、`gsm/density/material_code` 齐、`needs_confirmation is True` |
| A 不猜 | 材料表里没有对应 material 或 `density` 缺失 → `thickness_mm is None`、`thickness_source is None`、`attribution.thickness_unresolved[0].reason == "density_missing"` |
| A 不误伤 | 整表两条同名材料（`material_ambiguous`）→ 保持 `None` |
| B 串味 | 件材料 `PET光银 225g` + 命中它的成组注记 `材料：2mm灰板` → `thickness_mm is None`、`attribution.thickness_material_conflict` 非空且带 `note_ref`；件材料 `灰板` 的同一条注记 → 照旧 `2.0`（防误伤） |
| B 幂等 | 同一份 IR 两次 `extract()` 逐字相同（含新键） |
| C 指标 | `summarize()` 四个新键存在且自洽（`known + unknown == part_total`），既有键原样保留 |
| D 入口 | `main.py` 有 `PACKAGING_PART_THICKNESS_PATH` 与 `BOX_MATCH_DECIDE_ROLES`；`app.js` 有补料厚调用；`set_manual_thickness` / `save_part_thickness` 存在且 `manual` 来源可读回 |
| E 真样本（gated） | `酒盒.dwg`：`thickness_conflict_total >= 1` 且**逐件复核没有残留串味**（2026-09-22 修订：分量数 402 → 1163 后原写死的"≥2"变成 1，计数是样本相关的，契约是"识别到 + 无残留"）；任何带料厚的件 `thickness_source.kind` 必在闭集内；`thickness_unknown_total >= 40`（不许靠猜把数字填满） |

## 4. 明确不做

- 不引入纸类经验厚度表（`225g → 0.2mm` 这种"行业经验值"**不许**进代码、不许当默认值）；
- 不放宽第 3 层三道门槛里的任何一条（缺料厚照旧拒绝）；
- 不改 `packaging-cost.py`（成本侧的「厚度 × 密度 → 克重」已由 `packaging-cost-gaps-closure.md` 收口，
  本层不碰）；
- 不改圆盘盒的 `_note_thickness` 显式 `厚度2MM` 路径；
- 不改 `tests/` 下任何既有文件；不 commit / push / tag / Release / 部署。

## 5. 命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_thickness_facts_red -v   # 当前必红
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_material_attribution_red -v  # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_3d_red -v                   # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v           # 不回归
CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -m unittest \
  tests.test_packaging_parts_thickness_facts_red.RealSampleThickness -v                             # 真样本 E 组
```
