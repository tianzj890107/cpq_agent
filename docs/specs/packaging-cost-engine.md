# 规格：包装专用成本引擎 —— 包装第 7 批

> 批次：包装 8 批计划的**第 7 批**，也是**风险最高**的一批。依赖第 1 批（四行业注册表）、
> 第 2 批（包装需求模板 3.1–3.6）、第 3 批（包装知识库扩展表）、第 4 批（盒型匹配与确认）、
> 第 5 批（参数化部件与包装 BOM）、第 6 批（工艺路线与标准工时）已完成。
> 红测：`tests/test_packaging_cost_engine_red.py`。
> 口径来源：`报价逻辑-0903.xlsx` 的**可见** Sheet `报价-工费率`（151 个公式）、`包装运输` /
> `包装运输 (2)`（各 27 个公式）、`成本细分`（25 个公式，10 个报告分组）。

本批只做「已确认盒型 + 包装 BOM + 已确认工艺路线 → 包装成本」。**不做**利润/毛利率/未税售价/
报价单（第 8 批）、不做产能排程、不做拼版优化、不改三个原行业。

## 1. 背景与真实问题

### 1.1 现有三行业走的是固定系数模型，包装不能照搬

`tech_app/backend/services/cost_model.py` 的 `generic_v1` 是：

```
材料 = Σ(数量 × 单价)
人工 = 材料 / 1.13 / 0.791 × ((1 - 0.791) × 0.3556)
制费 = 材料 / 1.13 / 0.791 × ((1 - 0.791) × 0.1778)
加工 = 材料 / 1.13 / 0.791 × ((1 - 0.791) × 0.0944)
```

即**只有材料是逐项算的，其余三项由材料按固定系数推导**。包装不能这样：同样的材料成本，普通
天地盖和复杂手工书型盒的加工费会算出接近的值。0903 的成本表本身就是**逐部件 × 逐成本类别**
的明细（24 个类别列 + 逐行损耗率），与固定系数完全不同。

### 1.2 0903 里有两套口径，本批只采用其中一套

| Sheet | 口径 | 行 2 的复膜 | 行 2 的烫金 | 总成本 |
| --- | --- | --- | --- | --- |
| `报价-行业标准` | 面积/用量 × 单价（近似） | 1.2934294398230088 | 0.55499999999999994 | 60.049408129741252 |
| `报价-工费率` | 工时 × 费率 + 用量 × 单价 + 最低收费 | 1.3766112580048271 | 1.3432666666666671 | 77.685201899648021 |

两套口径对同一行给出两个答案。**本批采用「报价-工费率」**，理由：

- 它与第 6 批路线的 `standard_seconds`、`kb_cost_rate` 的工时费率能直接对接；
- 它是唯一**完整公式化**的一套（`报价-行业标准` 的印刷/烫金/模切等列大量是手填数字）；
- 「报价-行业标准」是同一业务的粗算版，两套并存会让「同一输入两个答案」。

「报价-行业标准」与 `成本测算明细.xlsx` 的两张隐藏 Sheet 一并列为**已知差异，本批不采用**，
不得把两套口径混进同一次计算。

### 1.3 现状缺口（实测）

- 全仓没有任何包装成本引擎：`tech_app/backend/services/` 下只有 `packaging_match.py`（第 4 批）、
  `packaging_formula.py` + `packaging_bom.py`（第 5 批）、`packaging_route.py`（第 6 批）。
- `kb_packaging_cost_formula` 的 7 条种子 `expression` 是**中文散文**（`Σ(部件展开面积㎡ × …)`），
  `formula_version='draft-1'`、`review_status='draft'` —— 它**不可执行**，本批不得把它当公式跑。
- `wip_cost_estimate` / `wip_cost_item` 是设计 IR 口径（`cost_type` 只有
  `material/manufacturing/technical/logistics` 四类），装不下包装的 24 个类别；本批另建表，
  **不动**这两张表，也不动 `out_cost_result`。
- 包材（纸箱/平卡/隔卡/胶袋/护角/卡板…）与工装/刀模在库里只有物流规则 3 条与
  `ACCESSORIES.tooling_cost`，**没有可算的包材明细与工装寿命规则**。
- 第 5 批的 `tooling` 类 BOM 行只标「涉及工装 + 待分摊」，**工时、寿命、金额都没有**。

## 2. 数据契约

### 2.1 输入

1. 第 4 批确认的盒型；2. 第 5 批 BOM（部件 / 材料 / 工艺 / 工装 / 包材行）；
3. 第 6 批已确认工艺路线（`standard_seconds`、`needs_standard_time`、`automation`）；
4. 需求：`quote_quantity`、3.4 表面工艺字段、可选 `loss_rate` / `proof_base` /
   `imposition_count` / `tooling_*` / `quote_scenario`；
5. 知识库：`kb_material` + `kb_material_price` + `kb_material_property`、`kb_cost_rate`、
   `kb_cost_factor`、`kb_packaging_cost_formula`、`kb_packaging_logistics_rule`、
   新增 `kb_packaging_cost_content`、`kb_packaging_tooling_rule`。

### 2.2 profile 路由（不许改三个原行业）

```python
COST_PROFILE = "packaging_v1"      # 包装
GENERIC_PROFILE = "generic_v1"     # 半导体 / 电池 / 电器（cost_model.py，逐字不动）

def profile_for(industry): return COST_PROFILE if industry == "packaging" else GENERIC_PROFILE
```

- `industry != 'packaging'` 调用本引擎 → `CostError(400, "not_packaging")`；
- 三个原行业的 `cost_model.normalize()` / `breakdown()` / `as_rows()` 结果**逐字不变**；
- 两个 profile 最终输出同一个成本**明细协议**，报价/审批/历史不需要两套接口（第 8 批）。

### 2.3 成本类别闭集（`COST_CATEGORIES`，24 条）

按 0903 `报价-工费率` 的列序（S → AP）逐条对齐，**不得改名、不得增删**：

| # | 列 | `code` | 名称 | # | 列 | `code` | 名称 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | S | `material` | 材料价 | 13 | AE | `visidi_uv` | 视高迪UV |
| 2 | T | `print` | 普通印刷 | 14 | AF | `texture` | 压纹 |
| 3 | U | `print_uv` | UV印刷 | 15 | AG | `emboss_deboss` | 击凹/凸 |
| 4 | V | `lamination` | 复膜 | 16 | AH | `mounting` | 裱纸 |
| 5 | W | `transfer_film` | 覆转移膜 | 17 | AI | `die_cutting` | 啤/切 |
| 6 | X | `hot_stamp_flat` | 热烫-平压 | 18 | AJ | `folding` | 折页/装钉 |
| 7 | Y | `hot_stamp_round` | 热烫-圆压 | 19 | AK | `v_groove` | V槽 |
| 8 | Z | `cold_stamp` | 冷烫 | 20 | AL | `auto_mount` | 机贴盒/贴双面胶 |
| 9 | AA | `silk_screen` | 丝印 | 21 | AM | `double_tape` | 双面胶 |
| 10 | AB | `varnish` | 过光油 | 22 | AN | `glue` | 胶水 |
| 11 | AC | `anti_scratch` | 防刮花光/哑油 | 23 | AO | `labor` | 人工/全检/包装 |
| 12 | AD | `pet_oil` | PET环保吸塑油 | 24 | AP | `other` | 其他 |

**项目级类别**（`PROJECT_COST_CATEGORIES`，不计入任何部件行）：

| `code` | 名称 | 来源 |
| --- | --- | --- |
| `packaging` | 包装 | `AT` = Σ 包材明细单件成本 |
| `freight` | 运输 | `AU` = MAX(最低运费 ÷ 数量, 托盘运费 ÷ 每托装数 ÷ 装载率) |

**报告分组**（`REPORT_GROUPS`，对应 `成本细分` Sheet 的 10 列，只做汇总不做计算）：
`材料 = material + glue`、`印刷 = print + print_uv`、`覆膜 = lamination`、
`烫金 = hot_stamp_flat + hot_stamp_round + cold_stamp`、`丝印 = silk_screen`、`裱纸 = mounting`、
`模切 = die_cutting`、`开槽 = v_groove`、`手工 = labor`、`包装 = packaging + freight`。

### 2.4 变量白名单（`LINE_VARIABLES`）

变量名→来源，**闭集**。表达式里出现白名单外的变量 → `CostError(409, "unknown_variable:<名>")`，
**绝不用 0 或猜测值兜底**。

| 变量 | 来源 | 缺省行为 |
| --- | --- | --- |
| `cut_length` / `cut_width` | 部件展开长 / 展开宽 +5mm 咬口（§2.5） | 部件缺尺寸 → 缺口 `part_size_missing` |
| `machine_length` / `machine_width` | 默认 = 开料长 / 开料宽−5；`需求.imposition` 可覆盖 | 用默认值并记 `assumptions` |
| `imposition_count` | 需求 `imposition_count`（默认 1） | 用默认值并记 `assumptions` |
| `gsm` | `kb_material_property.gsm`，否则从 `kb_material.grade` 解析（`157g`→157） | 解析不到 → 缺口 `material_gsm_missing` |
| `ton_price` | `kb_material_price.price` × 1000（元/kg → 元/吨） | 无有效价格 → 缺口 `material_price_missing` |
| `front_colors` / `back_colors` | 需求 `print_colors` / 背面色彩（可空） | 空 = 0 |
| `proof_base` | 需求 `proof_base`，否则 0 | 0 = 不计校版 |
| `quote_quantity` | 需求 `quote_quantity` | 缺失或 ≤0 → 缺口 `quantity_missing` |
| `tax_factor` | `1 + kb_cost_factor.F-PKG-TAX-VAT`（默认 1.13） | — |
| `setup_minutes` / `capacity_per_hour` / `equipment_rate` / `labor_rate` / `times` | `kb_cost_rate` + `FORMULA_CATALOG` 的默认值（§2.6） | 费率行缺失 → 缺口 `rate_missing:<rate_code>` |
| `film_price` / `film_thickness_um` / `film_kg_price` | 覆膜材料参数（1.7 元/㎡、18µm、18.5 元/kg） | — |
| `hot_area_mm2` / `foil_price` | 烫金面积（需求 `hot_area_mm2`）+ 箔价 8.5 元/㎡ | 面积缺失 → 缺口 `hot_area_missing` |
| `glue_unit_price` | 0.74 元/㎡ | — |
| `length_mm` / `width_mm` / `height_mm` / `usage_qty` / `material_price` / `units_per_pack` / `loss_uplift` / `yield_divisor` | 包材明细行（§2.10） | 装数为 0 或空 → 缺口 `invalid_units_per_pack` |
| `ink_thickness_mm` / `ink_unit_price` | 印刷墨层厚度 4µm / 油墨单价 115 元/kg（§2.6.1） | — |
| `labor_seconds` / `overhead_seconds` | 第 6 批路线工序工时合计 / 制费工时（默认 0，§2.6.2） | 工序 `standard_seconds` 为空 → 缺口 `step_time_missing:<工序>` |
| `overhead_rate` | `kb_cost_rate.RATE-PKG-OVERHEAD` | 本批不摊（`overhead_seconds=0`） |
| `tooling_cost` / `tooling_lifetime` / `committed_volume` / `amortized_quantity` / `refund_threshold` | 工装规则 + 需求（§2.9） | 缺寿命/承诺量 → 按 `amortized_quantity`，仍缺 → 缺口 |
| `min_freight` / `pallet_freight` / `units_per_pallet` / `loading_rate` | 物流规则（§2.10） | 装载率文本解析失败 → 缺口 `invalid_loading_rate` |

### 2.5 上机尺寸、模数与开料尺寸

实测 0903 的 `H 上机长` / `I 上机宽` / `J 模数` 是**人工选的印刷标准纸尺寸与拼版数**
（例：展开 871×667.5 的部件用 889×700 的纸、模数 1；铭牌 100×60 用 393×550、模数 25），
**不是从部件尺寸推出来的**。本批**不做拼版优化**，口径写死为：

```
开料长 = 部件展开长                    （部件无尺寸 → 缺口）
开料宽 = 部件展开宽 + 5                （5mm 咬口/出血；需求 cut_width_allowance 可覆盖）
上机长 = 开料长                        （需求 machine_length 可覆盖）
上机宽 = 开料宽 − 5                    （需求 machine_width 可覆盖）
模数   = 需求 imposition_count，默认 1  （缺省时记 assumptions）
```

每个用默认值的变量都写进该行的 `assumptions`（形如 `machine_length=默认=开料长`），
并在 `gaps.defaulted` 里留痕；**默认值不等于缺口**，不阻断计算。

### 2.6 公式目录（`FORMULA_CATALOG`）

每条：`formula_code` / `cost_category` / `expression`（DSL）/ `minimum_charge`（元/批）/
`rounding`（小数位）/ `rate_code` / `source_ref`。表达式只允许
`packaging_formula.evaluate()` 的白名单字符与 5 个函数（`MIN` `MAX` `IF` `IFERROR` `ROUND`）；
`precision` 取本条 `rounding`（4 位或 2 位）。

| `formula_code` | 类别 | 表达式（DSL） | 最低收费 | `source_ref` |
| --- | --- | --- | --- | --- |
| `PKG-C-MATERIAL` | material | `cut_length*cut_width/1000000*gsm/1000000*ton_price/tax_factor/imposition_count + cut_length*cut_width/1000000*gsm/1000000*ton_price/tax_factor*proof_base/quote_quantity` | 0 | `报价逻辑-0903.xlsx/报价-工费率/S2` |
| `PKG-C-PRINT-UV` | print_uv | `(setup_minutes/60 + quote_quantity/imposition_count/capacity_per_hour)*(equipment_rate+labor_rate)/quote_quantity + machine_length*machine_width/1000000*ink_thickness_mm/1000*ink_unit_price/tax_factor/imposition_count` | 0 | `…/报价-工费率/U2` |
| `PKG-C-LAMINATION` | lamination | `machine_length*machine_width/1000000*film_price/tax_factor/imposition_count + machine_length*machine_width/1000000*film_thickness_um/1000*film_kg_price/imposition_count + (setup_minutes/60 + quote_quantity/imposition_count/capacity_per_hour)*(equipment_rate+labor_rate)/quote_quantity` | 200 | `…/报价-工费率/V2` |
| `PKG-C-HOT-STAMP-FLAT` | hot_stamp_flat | `hot_area_mm2/1000000*foil_price + (setup_minutes/60 + quote_quantity/imposition_count/capacity_per_hour)*(equipment_rate+labor_rate)/quote_quantity` | 150 | `…/报价-工费率/X2` |
| `PKG-C-MOUNTING` | mounting | `(setup_minutes/60 + quote_quantity/imposition_count/capacity_per_hour)*(equipment_rate+labor_rate)/quote_quantity` | 0 | `…/报价-工费率/AH13` |
| `PKG-C-DIE-CUT` | die_cutting | `(setup_minutes/60 + quote_quantity/imposition_count/capacity_per_hour)*(equipment_rate+labor_rate)/quote_quantity` | 100 | `…/报价-工费率/AI2` |
| `PKG-C-V-GROOVE` | v_groove | `(setup_minutes/60 + quote_quantity/capacity_per_hour)*(equipment_rate+labor_rate)/quote_quantity*times` | 120 | `…/报价-工费率/AK5` |
| `PKG-C-GLUE` | glue | `machine_length*machine_width/1000000*glue_unit_price/imposition_count` | 0 | `…/报价-工费率/AN2` |
| `PKG-C-LABOR` | labor | `labor_seconds/3600*labor_rate + overhead_seconds/3600*overhead_rate` | 0 | 见 §2.6.2 |

#### 2.6.1 各条公式的费率与默认参数（来自 0903 同一单元格）

| 公式 | `rate_code` | 默认参数 |
| --- | --- | --- |
| `PKG-C-PRINT-UV` | `RATE-PKG-EQUIP-PRINT` | `setup_minutes=30`、`capacity_per_hour=12000`（副版 8000）、`equipment_rate+labor_rate=591+666`、`ink_thickness_mm=4`、`ink_unit_price=115` |
| `PKG-C-LAMINATION` | `RATE-PKG-EQUIP-SURFACE` | `setup_minutes=30`、`capacity_per_hour=5500`、`equipment_rate+labor_rate=197+145`、`film_price=1.7`、`film_thickness_um=18`、`film_kg_price=18.5` |
| `PKG-C-HOT-STAMP-FLAT` | `RATE-PKG-EQUIP-SURFACE` | `setup_minutes=200`、`capacity_per_hour=5000`、`equipment_rate+labor_rate=193+115`、`foil_price=8.5` |
| `PKG-C-MOUNTING` | `RATE-PKG-LABOR-LAMINATE` | `setup_minutes=30`、`capacity_per_hour=3500`、`equipment_rate+labor_rate=209+126` |
| `PKG-C-DIE-CUT` | `RATE-PKG-EQUIP-MOULD` | `setup_minutes=120`（薄料 60）、`capacity_per_hour=6500`（小件 900）、`equipment_rate+labor_rate=197.52+190.06`（小件 169.84+19.94） |
| `PKG-C-V-GROOVE` | `RATE-PKG-EQUIP-VGROOVE` | `setup_minutes=60`、`capacity_per_hour=3000`、`equipment_rate+labor_rate=195+111`、`times=2` |
| `PKG-C-GLUE` | — | `glue_unit_price=0.74` |

同一 `rate_code` 的 `equipment_rate` / `labor_rate` / `capacity_per_hour` 取自
`kb_cost_rate`（`rate_type='equipment_dep'` 取 `equipment_rate`，`rate_type='labor'` 取
`labor_rate`）；**知识库里的费率缺失 → 缺口 `rate_missing:<rate_code>`，不许用 0903 的常量顶上**。
0903 的常量只作为 `FORMULA_CATALOG` 的**默认参数**，且必须在 `assumptions` 里标注 `source=0903`。

#### 2.6.2 为什么人工不用 0903 的 AO 单元格

`报价-工费率!AO15` 的公式是 `(36+2)*40/180 = 8.444444444444445`，分母 180 与「秒 ÷ 3600 ×
元/小时」量纲不符，单位不可核。本批人工费走**标准工时 × 工时费率**（第 6 批路线 + `kb_cost_rate`）：

```
labor = Σ_工序 ( step.standard_seconds / 3600 × labor_rate(step) )
```

- 工序 → 费率映射（`STEP_RATE_MAP`）：`手裱`→`RATE-PKG-LABOR-HANDMOUNT`、
  `机裱`→`RATE-PKG-LABOR-LAMINATE`、`组装`/`检验`/`清洁包装`→`RATE-PKG-LABOR-ASSEMBLY`、
  其余工序 → 不计人工（材料/设备成本已在对应类别里）；
- 每个工序行单独出一条 `labor` 明细行（`step_no` 写进 `source_ref`），便于第 8 批追溯；
- 工序 `standard_seconds = null`（第 6 批的「待补工时」）→ 缺口 `step_time_missing:<工序名>`，
  **不许按 0 计**；
- `kb_cost_rate.minimum_charge` 生效：按 `MAX(minimum_charge/quote_quantity, 计算值)`；
- 制费（`overhead_seconds`）本批**不摊**（`kb_cost_rate.RATE-PKG-OVERHEAD` 只登记不参与），
  第 8 批再定；`overhead_seconds` 默认 0。

### 2.7 最低收费

一行金额 = `MAX(minimum_charge / quote_quantity, 表达式求值结果)`（`minimum_charge = 0` 时就是表达式
结果）。命中最低收费时该行 `min_charge_applied = true`，并在 `basis` 里写明门限金额与数量。

### 2.8 损耗

- **逐行损耗率**：行 `loss_rate` 优先取需求 `loss_rate`；需求未填时取
  对应类别/材料的 `kb_cost_factor`（`factor_type='scrap'`）：灰板类 → `F-PKG-LOSS-GREYBOARD`、
  面纸/衬纸类 → `F-PKG-LOSS-PAPER`；都取不到 → 缺口 `loss_rate_missing`，**该行不参与计算**。
- **损耗基数范围** `loss_base_scope`（可配置，默认 `material_process_and_labor`）：

| 取值 | 含损耗的类别 |
| --- | --- |
| `material_only` | 只 `material` |
| `material_process` | `material` + 24 类里的加工类（1.3 的成本表除 `labor`/`other` 外全部） |
| `material_process_and_labor`（默认） | 上述 + `labor` + `other` |
| `material_process_labor_packaging` | 上述 + `packaging` + `freight` |

- 默认值 `material_process_and_labor` **忠实复现 0903**（`AQ=SUM(S:AO)` 含 `AO 人工`，`AS=AQ×(1+AR)`），
  但 0903 的说明页写的是「损耗核算进材料和制程」——**两者不一致**，写进 Spec 并存，
  第一版按公式复现；业务确认人工不该计损耗时只换 `loss_base_scope`，不动公式。
- **包装与运输默认不参与损耗**（`AT` / `AU` 在 `AS` 之外相加），这是 0903 的确定口径。
- 行 `amount_with_loss = amount × (1 + loss_rate)`（该行类别在 `loss_base_scope` 内）否则 `= amount`。
- 项目级 `loss_amount = Σ(amount_with_loss − amount)`。

### 2.9 工装 / 刀模（`tooling`）

涉及工序（`TOOLING_PROCESSES`，与第 5 批 `TOOLING_KEYWORDS` 同源）：
`烫金` / `丝印` / `击凹凸` / `模切` / `装配线`。

新增知识库表 `kb_packaging_tooling_rule`（§3），按 `mode` 分五种，**每种只出一种分摊方式**：

| `mode` | 单件分摊 | 说明 |
| --- | --- | --- |
| `one_off` | `tooling_cost / amortized_quantity` | 一次性收取，按本单分摊量摊 |
| `lifetime`（默认） | `tooling_cost / tooling_lifetime` | **按模具预计使用寿命**分摊，与订单量无关 |
| `committed` | `tooling_cost / committed_volume` | 按项目承诺总量分摊 |
| `refund` | 同 `one_off`，另写 `refund_status` | 累计数量 ≥ `refund_threshold` → `refund_status='refundable'`，否则 `'chargeable'` |
| `customer_supplied` | `0` | 客户自备模具，只记 `note`，不进成本 |

- 分摊量/寿命缺失或 ≤ 0 → 缺口 `tooling_basis_missing:<tooling_code>`，**不许按 0 或 1 顶替**；
- 工装行落在项目级明细（`part_code` 为空、`source_ref` 写 `tooling_code`），
  **不摊进任何部件行**，避免与 `material`/`die_cutting` 重复计费；
- `refund` 模式的返还金额由第 8 批处理（报价侧冲减），本批只给状态。

### 2.10 包材与运输

**包材**：新增知识库表 `kb_packaging_cost_content`（§3），逐条套 `FORMULA_CATALOG` 里的包材
公式（`PKG-P-*`），单件成本 = `表达式结果 / units_per_pack`（装数为 0/空 → 缺口
`invalid_units_per_pack`，该行不计入）。项目级 `packaging = Σ 包材行`。

包材公式（11 条，`source_ref` 指 `报价逻辑-0903.xlsx/包装运输`）：

| `formula_code` | 物料 | 表达式（DSL） |
| --- | --- | --- |
| `PKG-P-CARTON` | 彩盒/纸箱 | `((width_mm+height_mm+25.4)*(length_mm+width_mm+50.8)*2*material_price/tax_factor/645160 + 0.1+0.06+0.12)*loss_uplift/yield_divisor*usage_qty/units_per_pack` |
| `PKG-P-PAD` | 平卡 | `((length_mm+6)*(width_mm+6)*material_price/tax_factor/645160 + 0.04)*loss_uplift/yield_divisor*usage_qty/units_per_pack` |
| `PKG-P-DIVIDER` | 隔卡 | `(length_mm+6)*(width_mm+6)/1000000*gsm/1000000*material_price/tax_factor*usage_qty/units_per_pack` |
| `PKG-P-BAG` | 胶袋 | `(length_mm+50)*(width_mm+50)*height_mm/100/1000*bag_unit_price*material_price*usage_qty/units_per_pack` |
| `PKG-P-CRAFT-PAPER` | 双胶纸 | `(length_mm+6)*(width_mm+6)/1000000*gsm/1000000*material_price/tax_factor*usage_qty/units_per_pack` |
| `PKG-P-STRAP` | 牛皮纸轧带 | `(length_mm+6)*(width_mm+6)/1000000*gsm/1000000*material_price/tax_factor*usage_qty/units_per_pack` |
| `PKG-P-CORNER-TOP` | 顶部护角 | `length_mm/1000*material_price*usage_qty/units_per_pack` |
| `PKG-P-CORNER-PAPER` | 纸护角 | `length_mm/1000*material_price*usage_qty/units_per_pack` |
| `PKG-P-LABEL` | 通用标签 | `material_price*usage_qty/units_per_pack` |
| `PKG-P-BOARD` | 盖板 | `material_price/tax_factor*usage_qty/units_per_pack` |
| `PKG-P-PALLET` | 卡板 | `material_price/tax_factor*usage_qty/units_per_pack` |

公式里的 `645160`、`+0.1+0.06+0.12`、`+0.04`、`loss_uplift=1.03`、`yield_divisor=0.9`、
胶袋单价 `bag_unit_price=0.185` **逐字取自 0903**，作为 `FORMULA_CATALOG` 的常量参数记录；
Spec 不对这些常量做业务解释（工作簿未给），只在 `source_ref` 里留单元格坐标。

**运输**：`freight = MAX(min_freight/quote_quantity, pallet_freight/units_per_pallet/loading_rate)`。

- `min_freight` / `pallet_freight` / `units_per_pallet` / `loading_rate` 取运输方式匹配到的
  `kb_packaging_logistics_rule` 行（需求 `shipping_mode` 命中 `shipping_mode`；命中多条取
  `rule_code` 升序第一条；一条都不命中 → 缺口 `freight_rule_missing`）；
- `loading_rate` 是文本（`≥85%` / `不适用`）→ 解析成 `0.85`；`不适用` 或解析失败 →
  该分支当 0 处理并记 `assumptions`（只有 `min_freight` 分支生效）；
- `units_per_pallet` 为 0/空 → `pallet_freight` 分支无效（不除零）。

### 2.11 数量阶梯与场景

- **一个场景一行**：`scenario_code` 来自需求 `quote_scenario`（默认 `default`）；
- 场景字段：`quantity_tier`（默认 = `quote_quantity` 的文本）、`trial_or_mass_production`
  （需求 `是否首批试产` → `trial` / `mass`）、`included_components`（默认 `all`）、
  `tooling_charge`（工装口径，见 §2.9）、`refund_condition`、`gross_margin_rate`
  （**只存不算**，第 8 批用）；
- **MOQ 硬门槛**：`quote_quantity < 盒型.moq` → 缺口 `below_moq`（带 `moq` 与 `quote_quantity`），
  **仍然出成本**（成本本身可算），但 `has_gaps=true`，第 8 批据此拒绝直接出正式报价；
- 多数量阶梯：同一 `(project, requirement)` 允许并存多个 `scenario_code`；按数量降序返回
  `cost_curve`（第 7 批只出成本，不出售价）。

### 2.12 三层汇总

```
项目层  total_cost = Σ(部件行 amount_with_loss) + tooling_total + packaging + freight
  └─ 部件层  Σ 该部件所有行（含损耗）
       └─ 成本项层  一行 = (部件 × 成本类别) 或项目级行
```

- `load_cost` 同时返回**成本细分等价**的 `categories`（24 类别逐项 Σ(amount_with_loss)）与
  `report_groups`（10 分组），**不许只返回 `total_cost`**；
- 等价关系（0903 `成本细分` 的 SUMPRODUCT）：
  `categories[类别] == Σ_行(amount_with_loss where category=类别)`；
  `report_groups[组] == Σ(组成员 categories)`；`Σ(report_groups) == total_cost`。

### 2.13 缺口闭集

| 码 | 触发 | 后果 |
| --- | --- | --- |
| `not_packaging` (400) | 非 packaging 行业 | 拒绝 |
| `box_type_not_confirmed` (409) | 无确认盒型 | 拒绝 |
| `bom_not_built` (409) | 无包装 BOM | 拒绝 |
| `route_not_confirmed` (409) | 无已确认路线 | 拒绝（人工费无从取工时） |
| `quantity_missing` (409) | 数量缺失或 ≤ 0 | 拒绝 |
| `material_price_missing` | 材料无有效价格 | 该行不出金额，进 gaps |
| `material_gsm_missing` | 解析不到克重 | 该行不出金额，进 gaps |
| `part_size_missing` | 部件无展开尺寸 | 该行不出金额，进 gaps |
| `rate_missing:<rate_code>` | 费率缺失 | 该行不出金额，进 gaps |
| `loss_rate_missing` | 取不到损耗率 | 该行不出金额，进 gaps |
| `step_time_missing:<工序>` | 工序待补工时 | 该工序人工行不出金额 |
| `tooling_basis_missing:<code>` | 工装寿命/分摊量缺失 | 工装行不出金额 |
| `invalid_units_per_pack` | 包材装数 0/空 | 该包材行不出金额 |
| `invalid_loading_rate` | 装载率解析失败 | 运输只走最低运费分支 |
| `freight_rule_missing` | 无匹配运输规则 | 运输行不出金额 |
| `below_moq` | 数量 < MOQ | 出成本但 `has_gaps=true` |
| `unknown_variable:<名>` (409) | 表达式引用白名单外变量 | 拒绝（不许兜底） |
| `invalid_formula:<code>` (409) | `reviewed` 公式解析失败 | 拒绝（fail closed） |
| `no_formula:<code>` | 类别既无公式、也没给人工金额（v1 只有 9 个类别有公式，`print` 等按 0903 就是手填列） | 该行不出金额，进 gaps，**不阻断**其它类别 |

- **只要出现任何缺口，`has_gaps = true`**，且不许把缺口金额当 0 静默计入合计；
  `total_cost` 是「已算出的部分」之和，`gaps` 逐条带 `code` / `where` / `detail`。
- 缺料价/缺费率时**绝不允许模型或引擎编造精确价格**（本批不调模型，此条同时是对第 8 批的约束）。

## 3. 落库

### 3.1 知识库新增两表（`da_schema.sql`，只追加）

```sql
-- 包材明细（0903 的「包装运输」Sheet）：逐条单件包装成本
CREATE TABLE IF NOT EXISTS kb_packaging_cost_content (
    content_code     TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    category         TEXT,                 -- 纸箱/平卡/隔卡/胶袋/护角/卡板/标签…
    material_spec    TEXT,
    length_mm        REAL,
    width_mm         REAL,
    height_mm        REAL,
    gsm              REAL,
    usage_qty        REAL,                 -- 用量
    material_price   REAL,                 -- 材料单价
    units_per_pack   REAL,                 -- 装数
    formula_code     TEXT,                 -- FORMULA_CATALOG 的 PKG-P-*
    industry         TEXT NOT NULL DEFAULT 'packaging',
    source           TEXT, version TEXT, effective_from TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    note             TEXT, created_at TEXT, updated_at TEXT
);

-- 工装/刀模规则：寿命、承诺量、达量返还
CREATE TABLE IF NOT EXISTS kb_packaging_tooling_rule (
    tooling_code     TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    process_code     TEXT,                 -- 烫金/丝印/击凹凸/模切/装配线
    mode             TEXT NOT NULL CHECK (mode IN (
                         'one_off', 'lifetime', 'committed', 'refund', 'customer_supplied')),
    tooling_cost     REAL,
    tooling_lifetime REAL,
    refund_threshold REAL,
    refundable       INTEGER NOT NULL DEFAULT 0 CHECK (refundable IN (0, 1)),
    industry         TEXT NOT NULL DEFAULT 'packaging',
    source           TEXT, version TEXT, effective_from TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    note             TEXT, created_at TEXT, updated_at TEXT
);
```

`kb_packaging_logistics_rule` **只加一列**：`pallet_freight REAL`（托盘/整车单托运费；
空 = 该分支无效）。既有 3 行照旧，不得改它们的其他字段。

### 3.2 结果表（`da_schema.sql`，只追加）

```sql
CREATE TABLE IF NOT EXISTS wip_packaging_cost_estimate (
    estimate_id      TEXT PRIMARY KEY,          -- pkgcost:<project>:<requirement>:<scenario>
    project_id       TEXT NOT NULL,
    requirement_no   TEXT NOT NULL DEFAULT '',
    scenario_code    TEXT NOT NULL DEFAULT 'default',
    industry         TEXT NOT NULL DEFAULT 'packaging',
    engine_version   TEXT NOT NULL,
    cost_profile     TEXT NOT NULL,             -- packaging_v1
    quote_quantity   REAL,
    currency         TEXT NOT NULL DEFAULT 'CNY',
    tax_rate         REAL,
    loss_base_scope  TEXT NOT NULL DEFAULT 'material_process_and_labor',
    quantity_tier    TEXT,
    trial_or_mass_production TEXT,
    included_components TEXT,
    material_total   REAL NOT NULL DEFAULT 0,
    process_total    REAL NOT NULL DEFAULT 0,
    labor_total      REAL NOT NULL DEFAULT 0,
    tooling_total    REAL NOT NULL DEFAULT 0,
    packaging_total  REAL NOT NULL DEFAULT 0,
    freight_total    REAL NOT NULL DEFAULT 0,
    other_total      REAL NOT NULL DEFAULT 0,
    subtotal         REAL NOT NULL DEFAULT 0,   -- 含损耗的部件行合计
    loss_amount      REAL NOT NULL DEFAULT 0,
    total_cost       REAL NOT NULL DEFAULT 0,
    has_gaps         INTEGER NOT NULL DEFAULT 0 CHECK (has_gaps IN (0, 1)),
    gaps_json        TEXT,
    assumptions_json TEXT,
    computed_at      TEXT,
    created_at       TEXT, updated_at TEXT,
    UNIQUE (project_id, requirement_no, scenario_code)
);

CREATE TABLE IF NOT EXISTS wip_packaging_cost_item (
    item_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    estimate_id      TEXT NOT NULL REFERENCES wip_packaging_cost_estimate(estimate_id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    part_code        TEXT,                      -- 项目级行为空
    part_name        TEXT,
    cost_category    TEXT NOT NULL,             -- COST_CATEGORIES / PROJECT_COST_CATEGORIES
    formula_code     TEXT,
    formula_version  TEXT,
    content_code     TEXT,                      -- 包材行
    tooling_code     TEXT,                      -- 工装行
    rate_code        TEXT,
    quantity_basis   TEXT,                      -- 按单件/按批/按寿命/按承诺量
    quantity         REAL, unit TEXT,
    unit_price       REAL,
    amount           REAL NOT NULL DEFAULT 0,   -- 未计损耗
    min_charge_applied INTEGER NOT NULL DEFAULT 0 CHECK (min_charge_applied IN (0, 1)),
    loss_rate        REAL,
    amount_with_loss REAL NOT NULL DEFAULT 0,
    expression       TEXT,                      -- 求值用的表达式（可解释）
    inputs_json      TEXT,                      -- 输入变量快照
    source_ref       TEXT,                      -- 0903 单元格 / 工序 step_no / 材料码
    source           TEXT NOT NULL DEFAULT 'kb' CHECK (source IN ('kb', 'formula', 'human')),
    note             TEXT,
    UNIQUE (estimate_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_packaging_cost_item ON wip_packaging_cost_item(estimate_id, cost_category);
```

- **重算**：同一 `(project_id, requirement_no, scenario_code)` 先删该 estimate 的 `item` 行、
  重建，`estimate` 行 upsert；
- **不覆盖历史**：`wip_packaging_cost_estimate` 是「当前值」；第 8 批才做报价版本。
  `重算` 写项目审计 `workflow:packaging_cost_rebuilt`；
- 三张表新表，不动 `wip_cost_estimate` / `wip_cost_item` / `out_cost_result`。

## 4. 接口与命名契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/projects/{project_id}/requirement/packaging-cost` | 生成/重算并落库 |
| `GET` | `/api/projects/{pid}/requirement/packaging-cost` | 读回（含 `categories` / `report_groups` / `gaps`） |
| `GET` | `/api/projects/{pid}/requirement/packaging-cost/items` | 成本明细行（可按 `cost_category` / `part_code` 过滤） |
| `GET` | `/api/projects/{pid}/requirement/packaging-cost/curve` | 多场景成本曲线（按数量降序） |

- 只对 `industry = 'packaging'` 生效；其它行业 → `400`；
- 读路由路径参数写 `{pid}`（避免顶掉「43 条单参数 GET 路由」基线）；
- 生成/重算写权限 `COST_WRITE_ROLES` **直接引用** `packaging_match.BOX_MATCH_DECIDE_ROLES`
  （同一对象）；其它已登录角色只读、写 → `403`；
- 响应统一 `{"cost": <load_cost 结构>}`，明细 `{"items": [...]}`，曲线 `{"curve": [...]}`。

### 4.5 命名契约（红测与实现共用，不得改名）

新增 `tech_app/backend/services/packaging_cost.py`：

| 名称 | 说明 |
| --- | --- |
| `ENGINE_VERSION = "packaging_cost_v1"` | 写进落库行 |
| `COST_PROFILE` / `GENERIC_PROFILE` | §2.2 |
| `COST_CATEGORIES` | §2.3 的 24 条（`tuple[tuple[str, str], ...]`，(code, 中文名)） |
| `PROJECT_COST_CATEGORIES` | §2.3 的 2 条 |
| `REPORT_GROUPS` | §2.3 的 10 组（`dict[str, tuple[str, ...]]`） |
| `LINE_VARIABLES` | §2.4 白名单 |
| `FORMULA_CATALOG` | §2.6（`dict[str, dict]`，含表达式/最低收费/取整/费率/来源） |
| `STEP_RATE_MAP` | §2.6.2 工序 → `rate_code` |
| `TOOLING_PROCESSES` | §2.9 |
| `TOOLING_MODES` | §2.9 五种模式 |
| `LOSS_BASE_SCOPES` | §2.8 四种取值 |
| `DEFAULT_LOSS_BASE_SCOPE = "material_process_and_labor"` | §2.8 默认基数范围 |
| `loss_base_categories(scope) -> tuple[str, ...]` | 某个基数范围下「含损耗」的类别闭集（**永不含** `packaging` / `freight`） |
| `default_loss_rate(material_text, *, rows=None) -> float\|None` | 按材料文本取 `kb_cost_factor` 的 `scrap` 费率；取不到返回 `None`（由调用方转缺口），**不许默认 0** |
| `COST_WRITE_ROLES` | 直接引用 `packaging_match.BOX_MATCH_DECIDE_ROLES` |
| `resolve_formula(formula_code, *, rows=None) -> dict` | 目录 + `reviewed` 覆盖（§4.6） |
| `compute_line(category_code, variables, *, formula_code=None, amount=None) -> dict` | 纯函数，返回金额/最低收费命中/表达式/输入。给了 `amount` 就直接采用（`source='human'`，用于人工录入/报工/回归），不套公式、**不取整**（人工录入就是事实）；公式算出的金额按该条 `rounding` 取整 |
| `expression_variables(expression) -> list[str]` | 取表达式里的变量名（按出现顺序），用于白名单校验 |
| `apply_loss(amount, loss_rate, *, category, loss_base_scope="material_process_and_labor") -> float` | 单行损耗；类别不在 `loss_base_scope` 内时原样返回 |
| `summarize(lines, *, packaging=0.0, freight=0.0, loss_base_scope=...) -> dict` | 纯函数汇总：`subtotal`（Σ 含损耗部件行）、`loss_amount`、`tooling_total`、`categories`、`report_groups`、`total_cost` |
| `compute_tooling(rule, *, quote_quantity, amortized_quantity=None, committed_volume=None, cumulative_quantity=None) -> dict` | §2.9 五种模式；缺基准时 `amount=None` + `gap` |
| `compute_content(formula_code, variables) -> dict` | §2.10 单条包材单件成本（= 表达式 ÷ `units_per_pack`） |
| `compute_packaging(rows, *, tax_factor, loss_uplift, yield_divisor) -> dict` | 包材合计 `{"amount":…, "lines":[…]}` |
| `parse_loading_rate(text) -> float\|None` | `≥85%` → `0.85`；`不适用` / 解析失败 → `None` |
| `compute_freight(rule, *, quote_quantity) -> dict` | §2.10 运输 = `MAX(min_freight/数量, pallet_freight/每托装数/装载率)` |
| `compute_project(project_id, requirement_no="", *, scenario=None) -> dict` | 组装 → 逐行算 → 汇总（不落库） |
| `build_cost(project_id, requirement_no="", *, scenario=None, actor=None) -> dict` | 算 + 落库 |
| `load_cost(project_id, requirement_no="", *, scenario=None) -> dict` | 读回 + categories + report_groups + gaps |
| `cost_items(estimate_id) -> list[dict]` | 明细行 |
| `cost_curve(project_id, requirement_no="") -> list[dict]` | 多场景曲线 |
| `CostError(message, status_code=409, code="")` | 业务错误 |

`da_repo` 新增：`save_packaging_cost(project_id, requirement_no, scenario, estimate, items)`、
`load_packaging_cost(project_id, requirement_no="", scenario="")`、
`load_packaging_cost_items(estimate_id)`、`packaging_cost_estimates(project_id, requirement_no="")`。

`kb_repo` 新增：`packaging_cost_contents()`、`packaging_tooling_rules()`。

`da_seed_packaging` 新增：`COST_CONTENTS`（11 条，对齐 0903 `包装运输` 的 11 行）、
`TOOLING_RULES`（5 条，五种模式各一条）；`kb_packaging_logistics_rule` 的种子行补
`pallet_freight`。**不得改动** `MATERIALS` / `LOGISTICS_RULES` / `COST_RATES` /
`COST_FACTORS` / `COST_FORMULAS` / `BOX_TYPES` / `PART_TEMPLATES` / `PROCESS_TEMPLATES` /
`ACCESSORIES` / `MATCH_WEIGHTS` 的既有内容（第 3/5/6 批红测逐条断言）。

### 4.6 `reviewed` 公式覆盖目录

`kb_packaging_cost_formula` 里 `review_status = 'reviewed'` 且 `expression` 能通过 DSL 校验的行，
**覆盖**同 `formula_code` 的内置目录条目（表达式 + `minimum_charge` + `rounding`），
并在行的 `note` 里记 `source=kb:<formula_code>`。

- `review_status != 'reviewed'`（含种子的 7 条 `draft`，其表达式是中文散文）→ **绝不执行**；
- `reviewed` 行的表达式解析失败 → `CostError(409, "invalid_formula:<code>")`，**fail closed**，
  不许静默回退到内置目录（否则业务以为改生效了、其实是旧口径）；
- `reviewed` 行的 `expression` 通过与 `packaging_formula.evaluate` 相同的校验，**不新增任何函数**。

## 5. 非目标

- 不算利润、毛利率、技术溢价、市场调节、折扣、税金、未税售价与报价单（第 8 批）。
  0903 的 `AX = AV/(1-AW)`、`成本细分` 的 `Q 毛利率` / `R 未税售价` 属第 8 批。
- 不做拼版优化（不推 上机尺寸/模数），不做产能排程、设备日历、派工报工。
- 不做「同一 `formula_code` 的两套口径并存」：本批只有 `packaging_v1`（工费率口径）。
- 不采用「报价-行业标准」与 `成本测算明细.xlsx` 的两张隐藏 Sheet（含 865 + 374 处 `#REF!`）。
- 不调大模型、不联网、不起进程、不写 Postgres / PDT。
- 不改第 2–6 批契约与演示数据、不动三行业既有成本链路。

## 6. 历史兼容

- 三张结果表 + 两张知识库表 + 一个新增列，全部新增；既有表结构不动。
- 三个原行业不产生也不消费这些表的数据；`cost_model.py` 逐字不变。
- 没有成本测算的项目：`GET` 返回 `built = false` + 空明细，不报错。
- `kb_packaging_logistics_rule` 新增列对既有行是 `NULL`，`pallet_freight` 为空即该分支无效。

## 7. 可自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red
```

红测分组：A 类别与公式闭集 / B 0903 黄金样例 / C 最低收费 / D 损耗 / E 工装模具 /
F 包材与运输 / G 三层汇总 / H 缺口 / I 场景与阶梯 / J 落库与接口 / K 非回归。

## 8. 人工验收

1.2 需求确认页（已确认盒型 + 已建 BOM + 已确认路线）→ 成本面板出现 24 类别明细与 10 报告分组 →
改数量 → 最低收费行与分摊行金额随之变化 → 把某材料的 `kb_material_price` 停用 → 该项变缺口且
合计不含该金额 → 页面明确提示「待询价」而不是给一个数字。

## 9. 不允许减少的既有能力

三行业 `generic_v1` 成本结果与接口；第 6 批路线生成/确认/版本；第 5 批 BOM 七类与锁定；
第 4 批盒型匹配四态决策；第 3 批知识库行业隔离与幂等 seed；第 2 批 64 字段 / 10 必填；
既有 `wip_cost_estimate` / `wip_cost_item` / `out_cost_result` 的语义与调用方。
