# 规格：包装专属参数族与技术侧包装闭环收口

批次：新五批（上线闭环）**第 4 批**。红测：`tests/test_packaging_tech_param_bom_route_cost_closure_red.py`。

前置（已完成，不在本批范围）：`industry_templates.PACKAGING_SPEC`（需求单 3.x 的 64 键包装字段）、
`packaging_match`（盒型五维匹配）、`packaging_bom`（参数化 BOM）、`packaging_route`（工艺路线）、
`packaging_cost` / `packaging_formula`（成本引擎）、`packaging_handoff`（包装成本回传）、
第 2 批的包装知识库（`kb_packaging_*`）与第 3 批的 `business_case_id` 落点。

**本批目标**：把「参数」这一环从电池/通用成品字典里摘出来 —— 包装项目在技术侧
**3.2 参数推荐**与 **4.3 成本清单**里只用包装族字段（来自 `industry_templates.PACKAGING_SPEC`，
单一来源），不再出现工作温度、机械号、电芯型号这类字段；并让包装族与既有包装链路
（盒型匹配 → BOM → 路线 → 成本）的必填/键名口径逐一对齐。

---

## 1. 现状取证（实测）

1. **3.2 的字典里根本没有包装族**：`tech_app/tools/build_quote_product_params.py` 的 `FAMILIES`
   只有 5 个 key，实测：

   ```
   pp.family_keys() -> ['li_primary', 'li_ion_pack', 'ess', 'pv_module', 'other']
   ```

   而 `other`（其他成品）的 14 个字段正是现场看到的那些：

   ```
   pp.fields_for("other") -> ['product_series'(产品系列), 'product_model'(产品型号),
     'product_item_code'(成品编码), 'product_item_name'(成品描述), 'scheme_desc', 'version_ext',
     'machine_model'(机械号), 'max_dimension'(最大尺寸), 'weight'(重量),
     'operating_temperature'(工作温度), 'storage_temperature'(存储温度),
     'application_scope'(应用范围), 'transport_scheme', 'other_nonstd_markup']
   pp.fields_for("other") 的必填 = ['product_series', 'product_model', 'product_item_code',
     'product_item_name', 'max_dimension', 'weight', 'operating_temperature']
   ```

   服务器实测的 3.2 表现（酒盒项目）与此逐项对应：**产品族「其他成品」**，随后要求填写
   产品系列 / 产品型号 / 重量 / 工作温度 / 机械号 —— 这就是 `other` 族。
2. **没有任何调用点按行业选族**：`product_params` 的 `as_prompt(family=None)` / `align(plan, family=None)`
   / `checklist(plan, family=None)` / `missing_required(plan, family=None)` 都**已经支持**显式族，
   但四个调用方全部按默认走：
   - `integration.py:228` `product_params.as_prompt()`（无参）、`:240` `align(result)`（用模型给的族）；
   - `main.py:3083` `product_params.align(params)`、`main.py:3175` `product_params.missing_required(plan.params)`；
   - `cost_review.py:252` `product_params.checklist(plan.params)`、`integration.missing_required(plan)`。
   族到底是谁，全看模型在 `product_family` 里写了什么。
3. **`resolve_family` 会静默退回 `other`**：`product_params.py:47` 对不认识的值一律
   `return DEFAULT_FAMILY`（`other`）—— 模型写「包装盒」「天地盒」都只会得到电池/通用成品字段。
4. **包装字段只有 1.1 在用**：`industry_templates.PACKAGING_SPEC`（实测 6 个 block / **64** 个键、
   必填 **10** 个）目前只被四处置用：1.1 创建页、AI 需求抽取、需求单 PDF、需求完整性预检。
   **3.2 参数推荐与 4.3 成本清单都没有用它**。
5. **包装链路本身已经建好**（本批只为它对齐口径，不重做）：实测既有红测

   ```
   tests.test_packaging_parametric_bom_red        Ran 57 tests  OK
   tests.test_packaging_process_route_red         Ran 57 tests  OK
   tests.test_packaging_box_type_matching_red     Ran 51 tests  OK
   tests.test_packaging_requirement_template_red  Ran 35 tests  OK
   ```

   唯一缺口是**参数这一环还在用电池字典**：`packaging_bom.INNER_DIM_KEYS =
   ("inner_length","inner_width","inner_height")` 要的正是包装字段，而 3.2 交给工艺经理填的
   却是 `weight` / `operating_temperature`。

---

## 2. 包装族（新增，唯一入口）

`tech_app/backend/services/product_params.py` 新增：

```python
PACKAGING_FAMILY = "pkg_box"          # 族 key，一旦定下不得改名（快照与红测都按它取值）
PACKAGING_FAMILY_NAME = "包装盒"       # 展示名

def family_for_industry(industry) -> Optional[str]:
    """行业 → 锁定的产品族；返回 None 表示沿用既有口径（由模型判定 product_family）。"""

def packaging_family_fields() -> list[dict]:
    """包装族字段清单，**只允许**由 industry_templates.PACKAGING_SPEC 派生。"""
```

要求：

1. `family_for_industry("packaging") == PACKAGING_FAMILY`；其它行业（半导体 / 家电 / 电池 /
   空值 / 未知值）一律返回 `None` —— **本批只锁包装**，其它行业行为逐字不变。
2. 纯函数：同输入同输出、不改入参、不读库、不读项目目录。
3. `packaging_family_fields()` 逐条由 `industry_templates.PACKAGING_SPEC` 派生：
   `code = SpecField.key`、`name = SpecField.label`、`required = SpecField.required`、
   `group = SpecBlock.section`（3.1–3.6）。**code 集合必须与
   `industry_templates.field_keys("packaging")` 完全相等（64 个）**。
4. 包装族必须注册进字典：`PACKAGING_FAMILY in family_keys()`，且
   `fields_for(PACKAGING_FAMILY)` / `as_prompt(PACKAGING_FAMILY)` /
   `checklist(plan, PACKAGING_FAMILY)` / `missing_required(plan, PACKAGING_FAMILY)` /
   `field_of(code)` 对包装字段全部可用 —— **不依赖** DA 快照 JSON 里有没有包装字段
   （本地与线上都必须成立；DA 那张 xlsx 里本来就没有包装行）。
5. `resolve_family(PACKAGING_FAMILY)` 与 `resolve_family("包装盒")` 都必须解析到包装族，
   不再退回 `other`。
6. 包装族**不得**包含任何电池/通用成品字段（闭集断言，见 §5 A6）。

---

## 3. 3.2 与 4.3 按行业锁族

1. `integration.recommend_params`：取项目需求单的行业 → `family_for_industry` →
   有值时 `as_prompt(family)` + `align(result, family)`；无值时保持既有调用（`family=None`
   与现在的行为逐字一致）。
2. `integration.missing_required` / `integration.missing_all` 增加 `family` 关键字
   （默认 `None`），并透传给 `product_params` 同名函数。
3. `main.update_integration_params`（保存人工编辑）与 `main.finalize_integration_params`
   （必填补齐门禁）都按同一份行业锁定的族执行 —— 保存时 `align(params, family)`，
   校验时 `missing_required(plan.params, family)`。
4. `cost_review.payload`（4.3 财务看板）的 `product_params.checklist(...)` 与
   `integration.missing_required(...)` 同样按行业锁定的族执行。
5. 行业读取口径唯一：`industry_templates.normalize(requirement.data.industry)`，
   不许各处自己写 `"packaging"` 字面量判断（`packaging_match.PACKAGING_INDUSTRY` 是既有常量，
   可以直接引用它，不要复制）。

---

## 4. 与包装链路的口径对齐

1. `packaging_bom.INNER_DIM_KEYS` 的三个内尺寸键必须全部出现在包装族字段里
   —— 否则 BOM 展开读不到参数化的内尺寸，3.2 填的字段和 BOM 要的字段是两套。
2. 包装族的必填集合必须等于 `industry_templates.required_keys("packaging")`（10 项）
   —— 盒型匹配的必填口径与 3.2 的门禁口径必须同源，不能一边让人补、另一边又判缺。
3. `packaging_match.PACKAGING_INDUSTRY` 传入 `family_for_industry` 必须得到包装族
   —— 行业键只有一份。

---

## 5. 红测

`tests/test_packaging_tech_param_bom_route_cost_closure_red.py`，五组共 20 条：

| 组 | 覆盖 |
| --- | --- |
| A（6） | 包装族：`PACKAGING_FAMILY` 与 `family_for_industry` 存在且纯函数、非包装行业返回 None、族已注册、字段 code 集合 == 64 键、逐字段 name/required 一致、不含电池字段 |
| B（3） | 单一来源：`product_params` 源码里不出现包装字段字面量、`as_prompt(包装族)` 真的带出字段与族名、`checklist` 的必填数 == 10 |
| C（4） | 按行业锁族：`integration.recommend_params` 带族、`main.update_integration_params` 保存时带族、`main.finalize_integration_params` 校验时带族、`resolve_family` 不再退 other |
| D（4） | 成本与链路对齐：`cost_review.payload` 的 checklist / missing_required 带族、`packaging_bom.INNER_DIM_KEYS` ⊆ 包装族字段、包装族必填 == `required_keys("packaging")`、`packaging_match.PACKAGING_INDUSTRY` → 包装族 |
| E（3） | 护栏：`other` 族仍 14 项且逐项不变、`industry_templates` 的 64/10 不变、DA 五个族的相对顺序与集合不变（只允许追加包装族） |

验证方式：纯函数行为（族解析、字段派生）+ AST/源码契约（四个调用点是否真的带族）+
跨模块一致性（BOM 内尺寸键、盒型匹配必填、行业键）+ 基线护栏。全部离线：
不连 Postgres、不调模型、不起服务、不读写生产数据。

实现前实测（交付当次）：

```
Ran 20 tests  FAILED (failures=17)
```

红点 17 条：A1–A6（`PACKAGING_FAMILY` / `family_for_industry` 都不存在，包装族无处可去）、
B1–B3（`product_params` 不从 `industry_templates` 派生，`as_prompt('pkg_box')` 取不到族）、
C1–C4（四个调用点全部按默认族走；`resolve_family("包装盒")` 退回 `other`）、
D1–D4（4.3 清单不带族、行业键与包装族没接上）。

绿 3 条护栏：E1（`other` 族仍 14 项）、E2（需求模板仍 64 键 / 10 必填）、
E3（DA 五族的相对顺序未变）—— 本批只允许**追加**包装族。

---

## 6. 禁止事项

- 不改 `industry_templates.PACKAGING_SPEC` 的 64 个键、10 个必填与键顺序
  （1.1 表单、需求抽取、PDF、预检、前端 `RC_PACKAGING_SPECS` 全按它对齐）。
- 不改 DA 五个族的字段、分组、必填与相对顺序；不动 `quote_product_params.json` 里既有的 46 个字段。
- 不改 `packaging_match` / `packaging_bom` / `packaging_route` / `packaging_cost` 的既有算法、
  闭集与引擎版本常量。
- 不改 `CostFlowError` / `BridgeRejected` 等第 3 批已定的口径；不改既有红测。
- 不装新依赖；不连 34、不写生产库、不重启服务。

---

## 7. 与后续批次的接口

- 第 5 批（服务器终验）用「包装项目 3.2 不再出现电池字段」作为「技术侧包装口径已收口」的证据之一。
- 第 5 批的完整流程里，包装参数由 3.2 产出后进盒型匹配（`packaging_match`）→ BOM
  （`packaging_bom`）→ 路线（`packaging_route`）→ 成本（`packaging_cost`），本批保证这条链的
  **参数来源**与它要的键一致。
