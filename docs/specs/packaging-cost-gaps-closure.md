# 包装成本缺口的可清路径：灰板克重按「厚度 × 密度」推导 + 本单不用的包材项

血缘：承接 `packaging-cost-engine.md`（第 7 批）、`packaging-cost-column-evidence.md`、
`packaging-cost-minimum-charge-decision.md`、`packaging-downstream-blockers-close-loop.md`（放行留痕过桥）。

本批只解决**成本缺口里"系统自己就能算出来、却算不出来"的那一类**：材料明明写着厚度和密度，成本引擎只认
克重，于是每一行纸板都永久带一条缺口。凡是要业务给数或给口径的，本批只登记、不改。

## 0. 一句话目标

34 上那份 24 条缺口的清单里，**6 条 `material_gsm_missing` 是引擎能自己算出来的**（`2.0mm 灰板` +
`密度 0.75 g/cm³` → `1500 g/㎡`），把它们从"缺口"变成"可算"，并让推导过程逐行留痕；其余缺口按"谁给、
给什么"逐条登记，不许实现方拍板。

## 1. 实测证据（34 上真跑 + 本机复现，不是推断）

### 1.1 24 条缺口的真实构成（项目 `cbef817fb1da`，酒盒.dwg）

| 缺口 | 条数 | 真实性质（本轮逐条查证后） |
| --- | --- | --- |
| `content_formula_error:PKG-P-*` | 7 | **数据不是 bug**：`报价逻辑-0903.xlsx/包装运输` 的 `D..G` 列本来就空（隔卡 / 胶袋 / 双胶纸 / 护角 / 标签 / 盖板），公式里引用的 `length_mm/width_mm/height_mm/usage_qty` 在那一行**根本没有值**。本机用种子全量复算，7 条一模一样。 |
| `material_gsm_missing` | 6 | **引擎能自己算**：`灰板 2.0mm` 的 `grade` 是 `2.0mm`、材料表 `density = 0.75 g/cm³`，克重 = 厚度 × 密度 × 1000 = **1500 g/㎡**。引擎只认 `gsm`，于是 6 行纸板全部不出金额。 |
| `loss_rate_missing` | 6 | 数据：非纸类材料（装帧布 / 钕铁硼 / 海绵裱绒等）在 `kb_cost_factor` 里没有对应损耗率；纸板类本来就有（`F-PKG-LOSS-GREYBOARD`）。 |
| `material_price_missing` | 3 | 数据：装帧布 / 钕铁硼 / 海绵裱绒 没有价格记录。 |
| `no_formula:print` | 1 | **设计如此**：`print` 在 0903 里是手填列，引擎按 Spec §2.13 只能报缺口、不许凭空给金额（冻结红测 `test_h6` 守）。带印刷的盒子必然带这条缺口 → 正式报价只能走「放行留痕」（`packaging-downstream-blockers-close-loop.md` §3.1）或线下手填。 |
| `tooling_basis_missing:T-PKG-DIE-REFUND` | 1 | 口径未定：`mode=refund` 的规则只有 `refund_threshold` / `refundable`，没有 `amortized_quantity` / `tooling_lifetime`，引擎按"不许按 0 或 1 顶替"报缺口。**分摊基数（本单量 / 承诺量）是商务决定，实现方不许拍。** |

### 1.2 两处更正（我上一轮说错了，以本条为准）

- **`content_formula_error` 不是"变量绑定 bug"**。它是 `kb_packaging_cost_content` 里那 7 行的
  尺寸/用量在**源工作簿里就是空单元格**，Spec（第 7 批）明确要求"记 None、由缺口披露"，不是取错了值。
  本机复算可逐行对照。
- **`packaging_route._AGGREGATE_EXPANSION` 不是死规则**。它比对的 `覆膜 / 烫金 / UV 上光` 正是
  `SURFACE_REQUIREMENTS` 的取值（`lamination → 覆膜`、`hot_stamping → 烫金`、`uv_coating → UV 上光`），
  且这三个工序名都在 `PROCESS_CATALOG` 里；旧说法不成立，本批**不动** `packaging_route.py`。

### 1.3 材料属性表没有接进成本引擎（顺带查明）

`_material_rows()` 只读 `kb_material`，**不 join** `kb_material_property`；而 `EVA 片材`（`grade=38°`）的
厚度 `10mm` 只写在属性表里。所以 EVA 这类"grade 里没有克重/厚度"的材料同样永远报
`material_gsm_missing`。材料表的 `density` 是**列**（`MAT-PKG-EVA.density = 0.94`），跟属性表是两处。

## 2. 允许修改范围（实现方）

只改 `tech_app/backend/services/packaging_cost.py` 一个文件：

1. 新增 `_material_thickness_density(material) -> (thickness_mm, density)`：厚度来源依次为
   `kb_material_property` 的 `thickness`（`value_num`，单位 `mm` 或空）、`grade` / `spec` 里明写的
   `t2.0` / `2.0mm`；密度取 `kb_material.density` 或属性表 `density`。**任一项取不到就返回 None**。
2. `_material_rows()` 把 `kb_material_property` 按 `material_code` join 成 `properties` 列表
   （**只读、不写**；今天没有 `properties` 键的行要变成有键，值可以是空列表）。
3. `_material_gsm(material)` 改成 `_material_gsm_detail(material) -> (gsm, source)`，`source` 取值闭集：
   - `"property"`：属性表 `gsm`；
   - `"grade"`：`grade` 里的 `NNNg`（含 `157g` 这种）；
   - `"derived_from_thickness_density"`：`厚度(mm) × 密度(g/cm³) × 1000`，**必须按此式**，四舍五入到
     0.1；
   - `None`：取不到。
   `_material_gsm()` 保留为兼容包装（返回 gsm、忽略 source），既有调用点不许改行为。
4. `compute_project()` 的材料行：把 `gsm_source` 一起放进该行的 `variables`（于是自动进 `inputs_json`），
   `gsm is None` 时照旧报 `material_gsm_missing`（一个字不改）。

## 3. 口径（逐条验收）

### 3.1 推导只认明写出来的数

- 允许：`thickness`（属性表，单位 `mm` 或空）+ `density`（列或属性表）；
- 单位不是 mm（`cm` / `m` / `µm`）→ **不推导**（照旧缺口）；空单位按 mm 处理并在 assumptions 里记
  `thickness_unit=assumed_mm`；
- `grade` 里的 `g`（`157g`）**优先于**推导：有 gsm 就不推导；
- 任一因子缺失 → **不推导**（不许给灰板一个默认密度、不许用"常见值"顶）；
- 推导值必须可复算：`gsm == round(thickness_mm * density * 1000, 1)`。

### 3.2 留痕

材料行的 `inputs_json` 必须带 `gsm_source`（闭集同上），页面/报告据此能回答"这个克重是哪来的"。

### 3.3 成本合计数只由可算行组成

`material_total` 仍然只累加 `amount is not None` 的行（既有口径不变）；本批只让**原本该能算的 6 行**
从上不了金额变成能上金额，**不许**顺手把缺价格/缺损耗率的行也补上数。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件；
- 不许改 `FORMULA_CATALOG` / `_FORMULA_PROVENANCE` / `RESOLVE` 与 0903 单元格的对应关系；
- 不许改 `gbk_packaging_cost_content` 的种子数据或 DDL；
- 不许把 `material_gsm_missing` / `material_price_missing` / `loss_rate_missing` 降级成 warning 或静默丢弃；
- 不许给 `print` / `T-PKG-DIE-REFUND` 造默认值（那两条要业务给口径，见 §5）；
- 不许改前端、不许改 `cpq_*` 报价侧、不许写库、不许联网、不许调模型；
- 不许 commit / push / tag / Release / 部署。

## 5. 本批不做：要业务给数或给口径的四类（逐条写明谁给什么）

| 缺口 | 要谁给 | 给什么 | 给完之后的行为（实现方后续只需接线） |
| --- | --- | --- | --- |
| `material_price_missing`（3） | 采购 / 业务 | 装帧布、钕铁硼磁铁、海绵裱绒的**计价单位与单价**（今天材料表按 `kg`，公式按 `gsm × 元/吨`，单位不匹配要一起给） | 材料行直接出金额，缺口消失 |
| `loss_rate_missing`（6） | 工艺 / 业务 | 非纸类材料的损耗率（登记进 `kb_cost_factor`，走既有 `applicable_scope` 匹配） | 损耗按登记值计 |
| `no_formula:print`（1） | 业务 / 财务 | 要么给印刷费公式（印张 × 色数），要么明确"印刷费线下报价、技术侧只报缺口" | 保持现状（**已由放行留痕覆盖**，见 `packaging-downstream-blockers-close-loop.md` §3.1） |
| `tooling_basis_missing:T-PKG-DIE-REFUND`（1） | 商务 | 刀模 18000 元的**分摊基数**：按本单量（`quote_quantity`）、按承诺量，还是按寿命 | 规则行补 `amortized_quantity` / `tooling_lifetime` 后，引擎按既有 `mode` 分摊 |
| `content_formula_error:PKG-P-*`（7） | 业务 | 这 7 项**本单用不用**（隔卡 / 胶袋 / 双胶纸 / 护角 / 标签 / 盖板）。"不用"要有一个显式来源（本单包材清单），引擎不能自己判断空单元格等于"不用" | 接入本单清单后，未选中的项跳过并留痕，**既不进成本也不报缺口**（清单存在哪、字段叫什么由业务定，本批不预设） |

## 6. 验收

```bash
# 本批红测（实现前必须真的红）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_gaps_red -v

# 不回归（冻结面，逐条必须仍绿）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red          # 81 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_rule_routing_red    # OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_rule_snapshot_red   # OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_red_closure_red     # 14 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_column_evidence_red # OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_policy_decision_red # 15 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_minimum_charge_red  # 47 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red       # 57 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_downstream_blockers_red  # 见该文件（10 红为预期）
```

## 7. 真样本复核口径（实现方动手前先跑一次，动手后再跑一次）

本机用两份真实 DWG 走完整链路时，`material_gsm_missing` 的条数必须**下降**、且**不出现新增缺口码**；
`material_total` 只会变大（原来上不了金额的行现在能上金额），不许出现变小或负数。
