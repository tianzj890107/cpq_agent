# 规格：包装知识库扩展表、行业维度与演示数据导入 —— 包装第 3 批

> 批次：包装 8 批计划的**第 3 批**。依赖第 1 批（四行业注册表，`packaging` 已入册）与
> 第 2 批（包装需求模板 3.1–3.6）已完成。
> 红测：`tests/test_packaging_knowledge_base_seed_red.py`。
> 数据依据：`裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx` 四个 Sheet
> （01盒型 12 条 / 02-盒型-部件构成 31 条 / 03-盒型+部件-工艺路线与工时 23 条 /
> 04-内托与配件库 12 条），与 `报价逻辑-0903.xlsx` 的成本分类口径。

本批**只建立可靠数据与行业隔离**：建表、灌演示数据、让检索按行业收口。**不做**盒型匹配
打分（第 4 批）、参数化部件展开与 BOM 计算（第 5 批）、工艺路线生成（第 6 批）、成本公式
求值（第 7 批）。演示数据可以被后续批次消费，但它本身不参与任何计算。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_knowledge_base_seed_red.py`

## 1. 背景与真实问题

### 1.1 知识库的事实源已经搬家

`kb-in-pg-http-snapshot` 之后，知识库唯一事实源是 CPQ 侧 Postgres `cpq_kb`：

- DDL 与整包快照在 `cpq_kb.py`（`KB_TABLES` / `KB_KEYS` / `_DDL_TEMPLATE` / `snapshot()`）；
- 技术工艺 `tech_app/backend/storage/kb_repo.py` 经 `services/cpq_kb_client.py` 拉
  `GET /wf/tech/kb/snapshot` 快照，在内存里过滤；本地 SQLite `da.db` **只是导入源**；
- `scripts/import_da_kb_to_pg.py` 从 `da.db` 只读取 `kb_*` 全表，按 `KB_KEYS` 幂等 upsert。

因此包装数据必须同时落在**三处**才真正可用：`da_schema.sql`（SQLite 表，供种子与导入）、
`cpq_kb.py`（PG DDL + `KB_TABLES`/`KB_KEYS`，供快照）、`da_seed_packaging.py`（数据）。
只改一处会出现「本地有、快照没有」或「快照有、导入器不认识」的静默缺口。

### 1.2 现在没有任何行业维度

`kb_*` 20 张表**没有一张带行业列**，`kb_repo` 的 `list_materials` / `current_price` /
`effective_rate` / `effective_factor` / `recommend_components` / `recommend_routes` 全部
在全库上过滤。加入包装物料、包装费率后，半导体 / 电池 / 电器的检索会直接命中包装数据 ——
例如包装的「手裱工时费率」会被电池的成本测算当成通用费率取走。

### 1.3 包装数据完全不存在

`da_seed.py` 是通用机加工口径、`da_seed_battery.py` 是电池口径，两者都没有盒型、部件公式、
裱糊工时、内托配件。包装这条线在库里一条也命中不上。

## 2. 数据契约

### 2.1 行业列（共享表）

`da_schema.sql` 与 `cpq_kb.py` 的 DDL 都为「行业主体表」增加

```sql
industry TEXT            -- 空/NULL = 通用（任何行业都可见）；否则 = 行业键
```

主体表清单（11 张）：
`kb_component`、`kb_standard_part`、`kb_equipment_class`、`kb_equipment`、
`kb_process_step`、`kb_process_route`、`kb_inspection_item`、`kb_material`、
`kb_supplier`、`kb_cost_rate`、`kb_cost_factor`。

- 现有 20 张表的 EAV / 子表（`kb_component_param`、`kb_material_price`、
  `kb_process_route_step` 等）**不加行业列**，行业由父表继承。
- 既有行保持空/NULL：**就是通用**，三行业行为逐字不变。
- 新增/写入的包装行必须写 `industry='packaging'`。
- `cpq_kb._ADDED_COLUMNS` 增加 11 条 `industry` 增量列，保证老库 `ALTER TABLE` 补列。

### 2.2 包装扩展表（7 张）

均含公共列：`industry TEXT NOT NULL DEFAULT 'packaging'`、`source TEXT`、
`version TEXT`、`effective_from TEXT`、`status TEXT NOT NULL DEFAULT 'active'`、
`created_at TEXT`、`updated_at TEXT`。

| 表 | 主键 | 关键列 |
| --- | --- | --- |
| `kb_packaging_box_type` | `box_type_code` | `name`、`name_en`、`family`、`size_l_min/max`、`size_w_min/max`、`size_h_min/max`、`fit_clearance`、`grey_board_thickness`、`face_paper_gsm`、`closure_type`、`part_count`、`v_groove`、`hand_mount_ratio`、`standard_seconds`、`automation_level` |
| `kb_packaging_part_template` | `part_code` | `box_type_code`、`seq`、`name`、`component`、`material`、`quantity`、`size_expr`、`size_length_expr`、`size_width_expr`、`size_height_expr`、`sample_value`、`key_process`、`is_optional`、`note` |
| `kb_packaging_process_template` | (`box_type_code`,`part_code`,`seq`) | `step_name`、`workstation`、`work_content`、`standard_seconds`、`automation`、`control_point`、`parallel_ok` |
| `kb_packaging_insert_accessory` | `accessory_code` | `name`、`material`、`thickness_spec`、`forming`、`tooling_cost`、`unit_cost_min`、`unit_cost_max`、`eco_attr`、`applicable_category`、`moq`、`note` |
| `kb_packaging_cost_formula` | `formula_code` | `cost_category`、`process_code`、`rate_code`、`expression`、`minimum_charge`、`quantity_basis`、`amortization_basis`、`loss_scope`、`rounding`、`source_ref`、`formula_version`、`review_status` |
| `kb_packaging_logistics_rule` | `rule_code` | `units_per_carton`、`carton_size`、`pallet_qty`、`units_per_pallet`、`shipping_mode`、`min_freight`、`loading_rate`、`quantity_tier`、`refund_condition`、`note` |
| `kb_packaging_match_weight` | `dimension` | `weight`、`hard_gate`、`rule_expr` |

- `kb_packaging_match_weight.dimension` 取值闭集：`size_range`、`fit_clearance`、
  `face_paper_gsm`、`closure_type`、`v_groove`（第 4 批五维匹配的权重与硬门槛）。
- `box_type_code` 同时是 `KB_KEYS` 与 SQLite 的 PRIMARY KEY，导入器幂等 upsert 依赖它。

### 2.3 三处必须一致

`da_schema.sql` 的表名/主键与 `cpq_kb.KB_TABLES` / `KB_KEYS` 必须一一对应；包装 7 张表必须
出现在 `KB_TABLES` 里（否则快照不带、技术工艺看不到），必须出现在 `KB_KEYS` 里（否则导入
器退化成 `DO NOTHING`，改了数据也不生效）。

### 2.4 行业过滤规则（唯一口径）

`kb_repo` 的读函数新增关键字参数 `industry: Optional[str] = None`：

1. `industry` 为空 / `None` → **不过滤**，行为与今天逐字一致（三行业默认路径不变）。
2. 传了行业 → 行可见当且仅当 `row.industry` 为空/NULL（通用）**或**等于该行业。
3. 绝不跨行业回落：包装取价取不到时**返回 `None` / 报缺口**，不允许拿同物料编码的
   三行业价格顶上。

需要加 `industry=` 的函数：`list_materials`、`current_price`、`effective_rate`、
`effective_factor`、`list_components`、`recommend_components`、`recommend_routes`
（`recommend_components` 经由 `list_components` 取候选，两者都要收口）。

### 2.5 包装专用查询

新增 4 个读函数（都只读快照，`industry` 固定 `'packaging'`）：

- `packaging_box_types() -> list[dict]`
- `packaging_part_templates(box_type_code: str) -> list[dict]`
- `packaging_process_templates(*, box_type_code=None, part_code=None) -> list[dict]`
- `packaging_insert_accessories() -> list[dict]`

## 3. 演示数据（`da_seed_packaging.py`）

新增 `tech_app/backend/storage/da_seed_packaging.py`，照 `da_seed_battery.py` 的既有形状：

- `seed_packaging(*, overwrite: bool = False) -> dict`，返回各表写入条数；
- `seed_all(*, overwrite: bool = False) -> dict` = `da_seed.seed_all` + `seed_packaging`；
- `python -m backend.storage.da_seed_packaging [--force] [--packaging-only]`。

数据量（与样例工作簿逐条对齐）：

| 表 | 条数 | 来源 Sheet |
| --- | --- | --- |
| `kb_packaging_box_type` | **12** | 01盒型 |
| `kb_packaging_part_template` | **31** | 02-盒型-部件构成 |
| `kb_packaging_process_template` | **23** | 03-盒型+部件-工艺路线与工时 |
| `kb_packaging_insert_accessory` | **12** | 04-内托与配件库 |

外加：≥3 条包装物料（灰板 / 面纸 / 内衬纸 等，`industry='packaging'`）及其价格；
≥5 条包装费率（含 ≥2 条带最低收费 `minimum_charge`）；包装的损耗率与税率系数；
7 条成本公式占位（`review_status='draft'`，本批不参与计算）与物流规则。

硬要求：

1. **幂等**：连续执行两次，行数与 `kb_version` 语义一致（第二次不新增、不改写）。
2. **不覆盖用户数据**：默认 `overwrite=False` 时，已存在的行（含被人工改过的）整行保留；
   只有 `overwrite=True` 才覆盖。
3. **不碰 PG / 不联网**：只写本地 SQLite 源库；不得 import `psycopg`、不得调用
   `cpq_kb_client.fetch_snapshot`。演示数据与线上 `cpq_kb` 之间只走既有导入器。
4. 每条包装行都带 `industry='packaging'`、`source`、`version`、`effective_from`、
   `status='active'`；`source` 写明来自样例工作簿的哪个 Sheet。
5. 部件模板必须保留可解析的尺寸公式（`size_expr` 原样，另拆出
   `size_length_expr` / `size_width_expr`），不得只存示例数值。

## 4. 非目标

盒型匹配打分、五维权重调参、参数化 BOM 展开、工艺路线生成与顺序校验、成本公式求值、
利润与报价单、PG 侧真写（本批只保证 DDL/导入器认识包装表，导入仍由既有
`scripts/import_da_kb_to_pg.py` 执行）。不改三行业的既有数据与默认行为。

## 5. 历史兼容

- 既有 20 张表加列后，老行 `industry` 为空 = 通用，三行业检索结果逐字不变。
- 老库升级走 `cpq_kb._ADDED_COLUMNS` 的 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，
  不重建表、不搬数据。
- 快照里缺 `industry` 键的行按通用处理，不抛错。

## 6. 可自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_knowledge_base_seed_red
```

红测分组：A 表结构与行业列 / B 演示数据与幂等 / C 行业隔离检索 / D 导入器与快照 / E 非回归。

## 7. 人工验收

`python -m backend.storage.da_seed_packaging --packaging-only` 连跑两次，第二次不新增行；
用 `scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db --dry-run --json` 看到
7 张包装表出现在统计里，且源库 sha256 不变。

## 8. 不允许减少的既有能力

三行业既有 `kb_*` 行与检索结果；`kb-in-pg-http-snapshot` 的快照契约与 `KB_TABLES` 前 20 张
表的顺序；`da_seed` / `da_seed_battery` 的幂等与默认不覆盖语义；导入器默认 dry-run 与源库
只读。
