# 规格：材料价格的「计价单位」必须被核对、留痕、并在单位不对时拦住这一行

依赖：`docs/specs/packaging-cost-engine.md`（§2.3 变量表把 `ton_price` 定义成
`kb_material_price.price` × 1000，即「元/kg → 元/吨」，这是引擎的**冻结合同**）、
`docs/specs/packaging-cost-gaps-closure.md`（§5 表：`material_price_missing` 要采购/业务
"给计价单位与单价 —— 今天材料表按 kg，公式按 gsm × 元/吨，单位不匹配要一起给"）、
`docs/specs/packaging-silent-degradation-disclosure.md`（说不出"这条单价是什么单位"
就是静默降级）。

状态：Spec + 红测（已实现）（原状：`tech_app/backend/services/packaging_cost.py:1706`
的 `_material_price()` 取回整条价格行后，调用点只读 `price`（`:2114-2119`）——
`kb_material_price.unit` 这一列**一个字都没被看过**；而 `:2119` 的
`"ton_price": (price * 1000) if price is not None else None` 已经把"单价必须是 元/kg"
写死进算式。于是采购把单位写成「吨」（或「元/米」「元/张」，或干脆空着）时，引擎会
**照旧 ×1000** —— 吨价被放大 1000 倍、空单位被当成 kg 用，**行上不留任何痕迹**；
同一份数据换个单位，金额差三个数量级，而结果看起来完全正常。
种子里 12 条价格全是 `unit: "kg"`（`da_seed_packaging.py` 的 `"price": {"price": 6.5,
"unit": "kg"}`），所以今天没暴露 —— 但 `unit` 是可写列，暴露只是时间问题。
另：`GAP_RESOLUTIONS["material_price_missing"]`（`:1813`）的
`resolution_action` 是"在物料主数据里补该材料的权威单价"，**没说单位** —— 与 §5 表的
"计价单位与单价要一起给"不一致，拿到缺口的人补完还是可能补错单位）
红测：`tests/test_packaging_material_price_unit_truth_red.py`
行号基线：HEAD `dc1e05f`

## 0. 一句话目标

一条材料价格行**说得出自己的计价单位**；单位是 kg 时逐字不变，单位没写时如实披露
（不拦算，但留痕），单位写了却不是 kg 时**拦住这一行**并点名要改成什么 —— 绝不 ×1000 猜。

## 1. 现状缺口（代码级）

1. `_material_price()`（`:1706`）只做"取最新有效价格"；调用点（`:2113-2119`）只取
   `price_row["price"]` → `kb_material_price.unit` 是**死列**；
2. `_MATERIAL_EXPR`（`:148`）与 `ton_price`（`:2119`）把「元/kg」当默认前提，
   **没有任何一处声明与核对**；
3. 缺口登记（`GAP_RESOLUTIONS` `:1813`）没写单位要求 → 业务补完仍是缺口；
4. 材料行的 `inputs_json` 里没有 `price_unit` / `price_unit_status` → 事后回查
   "这一版按什么单位算的"问不出来（`gsm_source` 已有先例，`## 266` 那批就是为这个加的）。

## 2. 契约

### C1 常量 + 纯函数（`packaging_cost.py` 顶层）

- `MATERIAL_PRICE_UNIT_EXPECTED = "kg"` —— 公式要求的规范单位，**不由入参覆盖**；
- `MATERIAL_PRICE_UNIT_ALIASES`：把写法归一成规范单位的**闭集小表**，只认 `kg` / `千克` /
  `公斤`（大小写与首尾空白先归一）；**不许**把 `吨` / `t` / `ton` / `元/kg` 之类当别名；
- `MATERIAL_PRICE_UNIT_STATUSES = ("ok", "unit_missing", "unit_mismatch")` —— 闭集三值；
- `material_price_unit_status(price_row) -> (status, unit)`（顶层纯函数）：
  - `price_row` 不是 dict → `("unit_missing", "")`（没核对过，不许说成 ok）；
  - `unit` 取不到 / 不是字符串 / 去空白后为空 → `("unit_missing", "")`（数字、布尔、`null` 都不是「写了的单位」）；
  - 归一后命中别名 → `("ok", "kg")`；
  - 其余 → `("unit_mismatch", <原文 trim>)`（原文**逐字**回给用户，不翻译、不改写）；
  - **不抛错**，不吃除 `price_row` 之外的任何东西，不读库、不联网。

### C2 行级：核对、留痕、拦住（`compute_project()` 的材料行）

- 材料行 `variables` / `inputs_json` 新增两键：
  `price_unit`（规范单位或 `""`）与 `price_unit_status`（C1 闭集）；
- 分支顺序（只有**新增**的两条插进既有链，既有四条不变）：
  1. `length/width` 缺失 → `part_size_missing`（不变）；
  2. 材料或价格缺失 → `material_price_missing`（不变）；
  3. **新**：`price_unit_status == "unit_mismatch"` → 缺口
     `material_price_unit_mismatch`，**不出金额**（`amount is None`），
     `detail` 里点名材料原文、单位原文（如「吨」）与公式要求的单位；
  4. `gsm` 缺失 → `material_gsm_missing`（不变）；
  5. 否则 → `compute_line()`（不变）；
- **新**（不拦算，只披露）：`price` 有值但 `price_unit_status == "unit_missing"` 时，
  追加缺口 `material_price_unit_missing`（`severity="advisory"`），金额照旧算出来 ——
  今天的行为不悄悄改，但"单位没核对过"必须在缺口里说出来；
- `unit_mismatch` 与 `material_price_missing` **互斥**（价格在，只是单位不对）；
- `ton_price` 的算式、`_MATERIAL_EXPR`、`compute_line()` 的输入映射**一个字不改**。

### C3 缺口登记（`GAP_RESOLUTIONS`）

两条新码各四键齐备（`missing_variable` / `resolution_action` / `entry` / `severity`），
`entry` 都指向 `kb_material_price`：

| 码 | severity | 动作（要点） |
| --- | --- | --- |
| `material_price_unit_mismatch` | `blocking` | 把 `kb_material_price` 的单位改成 元/kg（或先换算成 元/kg 再入表）；点名公式按 元/kg → 元/吨（`ton_price = 单价 × 1000`） |
| `material_price_unit_missing` | `advisory` | 给这条价格补上计价单位（元/kg）后再算 |

既有 10 条码的 `resolution_action` / `missing_variable` / `entry` / `severity` **一字不动**。

### C4 冻结面

- 不改 `_material_price()` 的取价口径（`kb_repo.current_price(material_code, industry=...)`
  的最新有效价优先、`price_type` 过滤、行业继承）；
- 不改 `_MATERIAL_EXPR` / `ton_price` / `tax_factor` / 最低收费 / 取整 / 损耗任何一处；
- 不改 `material_price_missing` / `material_gsm_missing` / `part_size_missing` /
  `loss_rate_missing` 的判定与文案；
- 不改任何 `kb_*` 种子数据（尤其不许"顺手把单位都写成 kg"）；
- 不加依赖、不联网、不调模型、不写库；不改前端；不改 `tests/` 下任何文件。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_cost.py`：C1 常量与纯函数、C2 材料行的两键 +
   两条分支、C3 两条登记；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许给**默认单位**（`""` 当归 kg、`吨` 当 kg 都算默认）：读不到单位就是读不到；
- 不许在单位不对时"按系数硬换算"出一个金额（`吨 → ×1`、`元/米 → ×宽度`…都算自己拍板）；
- 不许把 `unit_missing` 说成"单位正确"，也不许把它升级成 blocking（今天它不拦算，
  本批只叫它显形；要不要拦由业务另批裁决）；
- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_price_unit_truth_red -v
```

不回归（冻结面，逐条必须与今天逐字相同）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_engine_red tests.test_packaging_cost_gaps_red \
  tests.test_packaging_cost_rule_routing_red tests.test_packaging_cost_rule_snapshot_red \
  tests.test_packaging_cost_column_evidence_red tests.test_packaging_cost_part_usage_red \
  tests.test_packaging_cost_red_closure_red tests.test_packaging_cost_readiness_severity_layering_red \
  tests.test_packaging_parametric_bom_red
```

## 6. 已记录的边界

1. 本批只核对**单位**，不判断"这个价是不是权威价"（`price_type` / `confidence` 的权威化
   在 `quick-quote-8-rate-authority.md` 那条线，另批）；
2. 单位换算表（吨 / 米 / 张 / 平方米 → 元/kg）**不做**：换算要密度、幅宽、每张面积，
   那是业务口径，不是引擎能补的；
3. `unit_missing` 是 advisory：今天它照旧算钱，本批只让它显形并留痕；要不要拦由业务裁决；
4. 与货币无关：`美元/kg` 这类**非人民币**单位落在 `unit_mismatch`（公式里没有汇率），
   本批不引入汇率；
5. 种子里 12 条价格全是 `kg`，所以本批在演示数据上**零金额变化**（这正是护栏要守的）。

## 7. 落地状态（2026-09-22，Codex 实现）

**实现前红基**（`git stash push -- tech_app/backend/services/packaging_cost.py` 后的原文）：

```
Ran 21 tests ... FAILED (failures=13, errors=7)
```

20 红 / 1 绿 —— 唯一绿的是 C6（种子价格的 `unit` 全是 `kg`，本批没动种子）；
13 FAIL = A1–A8（纯函数不存在）+ B1 的两键断言 + C1–C3、C5；7 ERROR = B2–B6（夹具/接线）
与 C4、C7（`module()` 先要函数存在）。

**实现后**：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_price_unit_truth_red -v
Ran 21 tests ... OK
```

落点（只改了 `tech_app/backend/services/packaging_cost.py` 一个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 | `MATERIAL_PRICE_UNIT_EXPECTED` / `MATERIAL_PRICE_UNIT_ALIASES` / `MATERIAL_PRICE_UNIT_STATUSES` + `material_price_unit_status(price_row)`（`_material_price()` 之前） | 顶层纯函数：只吃价格行、返回 `(status, unit)` 两元组；`unit` 不是字符串 / 空 → `unit_missing`；归一后命中 `kg`/`千克`/`公斤` → `ok`；其余 → `unit_mismatch` 并逐字回原文；不读库、不联网、不抛错 |
| §C2 | 材料行：`price_row` 提升为局部变量、`unit_status/price_unit` 核对一次；`variables` 增 `price_unit` / `price_unit_status`；elif 链插 `unit_mismatch`（`amount is None`）；链后追加 `unit_missing`（advisory，不拦算） | 既有四条分支与文案、`ton_price` 算式、`compute_line()` 映射一字未动 |
| §C3 | `GAP_RESOLUTIONS` 增 `material_price_unit_mismatch`（blocking）/ `material_price_unit_missing`（advisory），各四键齐备、`entry=kb_material_price` | 既有 10 条登记的四个字段逐字未变（红测 C3 冻结对照） |
| §C4 | 无 | 未改取价口径、未改种子、未加依赖、未动前端与 `tests/` |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_engine_red tests.test_packaging_cost_gaps_red \
  tests.test_packaging_cost_rule_routing_red tests.test_packaging_cost_rule_snapshot_red \
  tests.test_packaging_cost_column_evidence_red tests.test_packaging_cost_part_usage_red \
  tests.test_packaging_cost_red_closure_red tests.test_packaging_cost_readiness_severity_layering_red \
  tests.test_packaging_parametric_bom_red
Ran 274 tests ... OK

# 包装全域（回归哨兵）
./open-claude/.venv/bin/python -W ignore -m unittest discover -s tests -p "test_packaging*red*.py"
Ran 2210 tests ... FAILED (failures=5, skipped=8)   # 正是既有 5 条挂账，与实现前逐条同名
```

边界（与 §6 一致，实现如约未越）：不给默认单位、不做单位换算、`unit_missing` 不升级成
blocking、不引入汇率；种子里 12 条价格全是 `kg`，所以演示数据上金额零变化（B1 守）。
