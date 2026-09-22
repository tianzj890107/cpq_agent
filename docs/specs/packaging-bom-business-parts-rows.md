# 包装 BOM 的部件组行改由业务部件清单遍历（`## 368` §7 剩下的那一半，切片 1）

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md` §7/§8（BOM 只遍历 `business_parts`）、
`docs/specs/packaging-parametric-bom.md`（BOM 七类与读接口）、
`docs/specs/packaging-dwg-parts-extraction.md` C7（几何回填）、
`docs/specs/packaging-bom-box-type-provenance.md` §2.3（行上盖盒型章）。

状态：Spec + 红测（已实现）（2026-09-22 落地：`business_part_rows()` + `_assemble()` 关键字参数 + `load_bom().business_rows`；23 OK）
红测：`tests/test_packaging_bom_business_parts_rows_red.py`

## 1. 现场缺口（代码级 + 真样本实测，不是推断）

1. `packaging_bom.py` 的 `_assemble()` 第 2 组的部件行**只**来自盒型模板展开
   （`expanded["parts"]` → `_part_item()`，`source="kb_packaging_part_template"`）；
   全文件里 `business_part_code` 出现 **0** 次 —— 权威清单里的 28 件部件
   （`JWXR21-P01…P28`）**一件都进不了 BOM 的部件组**。
2. 真样本 `裕同包装项目-待开发/酒盒 报价资料.xlsx`（表 `零部件排版工艺`）导入后本机实测：
   28 件业务部件，**每件都有权威尺寸**（`125.2x54mm` … `307.07x528.89mm`）与材料原文
   （`225G太阳铜版底PET光银`、`1.8MM双灰裱225G太阳铜版底PET光银` …）；其中 2 件是外购件
   （`顶托EVA` = 「外购，用量1个」、`磁铁` = 「外购，用量8/套」）。
3. 业务部件版本今天只以**披露**形式跟进 BOM（`source_versions.business_parts_id/hash`
   + 顶层 `business_parts` 块），行本身仍按模板走 —— `## 368` §7 的
   「BOM、单件工艺、单件成本、整包成本只遍历 `business_parts`；酒盒权威版本应产生 28 个业务部件，
   不能产生 263/402 行任务」**只做了披露那一半**。
4. 几何绑定在真样本首轮是 **28/28 `unbound`**（`## 368` §12.1 实测 2 `bound` / 2 `partial` /
   24 `unbound`）——而这些件的**权威尺寸与材料照样可用**；§7 明确要求
   「几何未绑定只阻断依赖几何的尺寸/工艺，不应把有权威尺寸的材料和采购成本全部阻断」，
   但今天没有任何下游表格消费它们。

本批只切**部件组**这一段（`box_part` / `optional_part`）；材料组的来源是下一批的话题（见 §7 边界 1）。

## 2. 契约

### C1 `packaging_bom.business_part_rows(business_doc) -> list[dict]`（纯函数）

- 入参：业务部件文档（含 `business_parts`）。不是 dict / `business_parts` 不是 list / 清单为空
  → `[]`（**不抛异常**，键不存在也算空）。
- 逐件一行，**保持文档顺序**（= 权威清单 `sequence_no` 升序；不重排、不按面积排）：
  - `business_part_code` 去空白后为空 → **跳过**（不许造 `item_key`）；同一编码重复出现时
    **只留第一条**（`item_key` 必须唯一：BOM 行的主键是
    `(project_id, requirement_no, bom_category, item_key)`）。
  - `bom_category`：`authority.process_text` 去掉首尾空白后含 `外购` → `"optional_part"`；
    否则 `"box_part"`（闭集里只许这两个，别的一律不许出现）。
  - `is_optional`：同一个判据 —— `"optional_part"` → `1`，否则 `0`。
  - `item_key` / `part_code`：都逐字给业务编码（`part_code` 是既有列，读回来认得出这件是谁）。
  - `item_name`：`name`；空 → 回落到编码。
  - `material`：`authority.material_text` **原文**（不许改写、不许截断、不许猜材料码）；
    `material_code` 一律 `""`（权威清单没有材料码，映射留给材料那一批）。
  - `length_mm` / `width_mm`：`authority.length_mm` / `authority.width_mm` 是**真的数字**
    （`int` / `float`）且 `> 0` 才给，否则 `None` —— 字符串（含 `"307.07"` 这种"看起来像数"的）、
    布尔、`NaN`、`0`、负数一律当"没有"。（尺寸由导入器解析成数字落库；这里再替上游 `float()`
    一次就是替它猜，猜错的那一件没人看得见。）
  - `quantity`：`authority.quantity` 能当数字读才给（数字 / 数字字符串），否则 `None`
    （真样本该列是空字符串）；这里只做展示口径，不参与任何计算。
  - `unit`：`"件"`。
  - `status`：长宽都算得出 → `"computed"`；否则 `"needs_input"`。
  - `missing_variables`：缺的键按 `("length_mm", "width_mm")` 顺序列；全有 → `[]`。
  - `size_source_json`：`{"kind": "authority_workbook", "sheet": …, "row": …}`
    （`sheet` / `row` 从 `authority.source` 取，空值不放进去；一个都拼不出 → `{}`），
    用 `_json_text()` 序列化（与其它行同一口径）。
  - `source`：`"packaging_business_parts_authority"`（新取值，与
    `kb_packaging_part_template` / `dwg_parts` 并列）。
  - **不写** `role`（业务角色留给人工映射，`packaging-part-role-manual-mapping` 口径不变）；
    **不写** `size_length_expr` / `size_width_expr` / `size_height_expr`（权威清单没有表达式）。
- 纯函数：体内不出现 `kb_repo.` / `da_repo.` / `store.`，不读文件、不联网、不落库。

### C2 `build_bom()` 接线

- 取一版业务部件文档：延迟 import `packaging_parts.load_business_parts(project_id)`
  （读不到 → `None`，**绝不抛**，也不许因此把 BOM 算失败）。
- `_assemble(expanded, box, data, requirement_no, business_parts=None)`：
  - `business_part_rows(business_parts)` **非空** → 第 2 组的部件行就是这些行
    （模板展开的部件行**不再**进入这一版 BOM）；
  - 空 / 没传 → **逐字保持今天的行为**（`_part_item()` 的模板行）。
  - 其余四组（`finished` / `process` / `tooling` / `packaging`）逐字不变；
    末尾统一盖 `box_type_code` / `industry` / `engine_version` 的循环不变。
  - **材料组**：本批当时逐字不变（只切部件组）；**已被 `## 378`
    （`docs/specs/packaging-bom-business-material-rows.md`）supersede** —— 那条接缝现在也按
    权威清单的去重材料原文收。
- 参数必须是**关键字带默认值**：既有调用方（红测按位置传 4 个参数）不改一行。
- `_bind_parts()` 照旧跑：业务行有权威尺寸就是 `computed`，不进几何回填；
  缺尺寸的业务行照旧按既有口径可被回填（`dwg_binding` 留痕不变）。
- 锁定行照旧由 `save_packaging_bom()` 保留（本批不许碰锁定语义）。

### C3 `load_bom()` 的披露

新增键 `business_rows`（**必须存在**）：

```json
{"row_total": 28, "box_part_total": 26, "optional_part_total": 2,
 "needs_input_total": 0, "keys": ["JWXR21-P01", "…"]}
```

- 判据只有一处：`source == "packaging_business_parts_authority"` 且
  `bom_category ∈ PART_CATEGORIES`；`keys` 升序；没有业务行时 `0 / 0 / 0 / 0 / []`。

### C4 冻结面

- 不许改 `expand_parts()`、模板展开与 `_part_item()` 的模板行口径、BOM 七类闭集
  （`BOM_CATEGORIES` / `PART_CATEGORIES`）；
- 不许新增数据库列、不许改 schema：业务行的编码复用既有 `part_code` 列、
  来源复用既有 `source` 列、尺寸来源复用既有 `size_source_json` 列；
- 不许把权威原文（工艺 `process_text` / 排版 `layout_text` / 备注 `note`）塞进任何列；
- 不许改 `_item_out()` 的既有键、角色映射、锁定语义、材料组来源；
- 没有业务部件清单时，`build_bom()` / `load_bom()` 的输出逐字不变。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_bom.py`（`business_part_rows()`、`BUSINESS_ROW_SOURCE`、
   `PURCHASED_KEYWORDS`、`_assemble()` 的关键字参数、`build_bom()` 取清单、
   `load_bom()` 的 `business_rows`）。
2. changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许自动补尺寸 / 编材料码 / 猜业务角色；不许把模板行的部件名对应到权威件上；
- 不许删既有行、不许改锁定行语义、不许把 `business_parts` 的披露块挪走；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_business_parts_rows_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parametric_bom_red \
  tests.test_packaging_bom_part_size_provenance_red \
  tests.test_packaging_bom_box_type_provenance_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_extraction_red \
  tests.test_packaging_cost_engine_red
```

## 6. 落地状态（2026-09-22，已实现）

| 契约 | 落点 |
| --- | --- |
| C1 纯函数 | `tech_app/backend/services/packaging_bom.py`：`BUSINESS_ROW_SOURCE` / `PURCHASED_KEYWORDS` / `_positive_number()` / `_authority_size_source()` / `business_part_rows()`（不读库、不读文件） |
| C2 接线 | `_assemble(expanded, box, data, requirement_no, *, business_parts=None)` 第 2 组行改由清单生成；`build_bom()` 走 `_load_business_parts(project_id)`（读不到回 `None`，绝不抛） |
| C3 披露 | `_business_rows_scope(items)` + `load_bom()` 的 `business_rows` 键（判据只有行上的 `source`） |

复跑原文：

```
实现前（未实现的代码）：
  Ran 23 tests in 0.696s
  FAILED (failures=15, errors=1)        # 16 红 / 7 绿护栏

实现后：
  Ran 23 tests in 0.685s
  OK

相邻不回归（parametric_bom / bom_part_size_provenance / bom_box_type_provenance /
business_parts_and_cad_plan_view / parts_extraction / cost_engine）：
  Ran 205 tests in 8.019s
  FAILED (failures=1)                   # 唯一失败是既有挂账 bom_part_size_provenance::B3（见 §7 边界 6）

tests/test_packaging_*.py 全域（91 个模块）：
  Ran 1678 tests in 64.584s
  FAILED (failures=5, skipped=8)        # 与上一批逐条相同的 5 条既有挂账
```

真样本端到端（本机真链路：种子 KB + 临时 SQLite + meta 沙盘 + 真工作簿导入）：

```
导入前：部件组行 = 10（模板展开），business_rows.row_total = 0
导入后：部件组行 = 28，source 只有 packaging_business_parts_authority
        business_rows = {"row_total": 28, "box_part_total": 26, "optional_part_total": 2,
                         "needs_input_total": 0, "keys": ["JWXR21-P01" … "JWXR21-P28"]}
        外购件 = ["JWXR21-P27", "JWXR21-P28"]（顶托EVA / 磁铁）
        第一行 = {"item_key": "JWXR21-P01", "part_code": "JWXR21-P01", "item_name": "左盖面纸",
                  "material": "225G太阳铜版底PET光银", "length_mm": 307.07, "width_mm": 528.89,
                  "status": "computed", "source": "packaging_business_parts_authority",
                  "size_source_json": "{\"kind\": \"authority_workbook\", \"sheet\": \"零部件排版工艺\", \"row\": 4}"}
        其余五组逐字不变：finished 1 / material 4 / process 11 / tooling 2 / packaging 3
        source_versions.business_parts_id = business-parts:0d6914ef7d5cc35a
```

## 7. 已记录的边界

1. ~~**材料组仍是模板来源**~~ —— **已被 `## 378` supersede（2026-09-22）**：材料组现在也按权威清单
   的去重材料原文收（`docs/specs/packaging-bom-business-material-rows.md`），
   材料码仍走既有唯一解析口径、解析不到就进 `material_unresolved`（不编码）。
   当时担心的"暴增且无从收口"改由那条 Spec 的 §C3 披露两本账（`resolved_total` / `unresolved_total`）回答。
2. **权威原文不入库**：BOM 行表没有放长文本的列（`note` 是"备注"语义，不许挪用），
   所以工艺 / 排版 / 备注原文**不进 BOM 行**；下游要看原文走业务部件文档那一路。
3. **重复编码只留第一条**：BOM 行的主键定死了 `item_key` 唯一。权威清单里如果出现同一个编码两行，
   是本批**不许猜**的清单缺陷（该留痕到哪里由清单那一批裁决），本批只保证不会写出两行同主键。
4. **几何回填照旧**：业务行缺尺寸时仍可能被 `bind_rows()` 按既有位置配对回填 ——
   与模板行同等待遇，不因为它是权威件就特殊化（配对口径的收敛是 `packaging-match-*` 那条线的事）。
5. 本批**不碰前端**：面板照旧读 `items`，因此业务部件行会自然出现在既有 BOM 表格里；
   若界面需要"这一行来自权威清单"的标记，另立一批（可以只读 `source`，不必改后端）。
6. 全仓仍是红的那 5 条与本批无关（`packaging_bom_part_size_provenance::B3` 等，见各自的
   Spec 边界，都是红测夹具缺陷 / 待业务裁决）；本批前后条数不变。
