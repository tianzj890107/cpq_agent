# 规格：报价助手行业化（需求门禁 + 产品技术参数）—— 包装对齐第 1 批

状态：Spec + 红测（**未实现**）
红测：`tests/test_quote_agent_industry_alignment_red.py`

相关规格：
`docs/specs/packaging-requirement-template.md`（包装需求模板 64 字段 / 10 必填）、
`docs/specs/global-industry-registry-and-packaging.md`（行业注册表唯一事实源）、
`docs/specs/packaging-box-type-matching.md`（盒型库匹配）、
`docs/specs/packaging-knowledge-base-mock-seed.md`（包装 7 张扩展表）。

---

## 1. 现场问题（实测，不是推断）

### 1.1 问题一：包装需求被半导体口径的门禁拦死

在报价助手（`cpq_agent_server.py`，默认端口 47292）输入：

```text
数码天地盒  尺寸（mm）30*30*20；盖面纸 铜版纸，亮膜；底面纸 铜版纸，哑膜；盖板材 灰板，厚度2.5mm
```

界面回：

```text
⚠ 需求信息不齐，暂不进行任何操作（不查库、不推荐产品、不填表）——缺少：工作温度。
产品匹配必须同时给出 尺寸、应用范围/使用场景、工作温度 三项。
```

**根因**：门禁在代码里硬编码成半导体/水表口径。

- `cpq_agent_server.py:866` `_STEP1_REQUIRED = (("max_dimension","尺寸"), ("application_scope","应用范围/使用场景"), ("operating_temperature","工作温度"))`
- `cpq_agent_server.py:869` `_step1_missing(req)` 只认这三项，**不接受行业参数**；
- 两处调用点 `cpq_agent_server.py:790`（`match_products` 工具）与 `cpq_agent_server.py:1041`（`/api/step1/match` 的 `phase="intent"`）都把「缺工作温度」当成「需求不齐」。

后果：包装需求里「工作温度」本就不适用，**信息其实已经齐了**却被判不齐，链路停在意图识别，
既不查库也不推荐。这不是阈值调参问题，是**门禁来源错误**——它读的是一份全局硬编码清单，
而不是行业模板。

同时确认（`grep -c industry cpq_agent_server.py` → `0`）：报价助手**完全没有行业概念**，
`cpq_industries.INDUSTRY_KEYS` 里的 `packaging` 在报价侧从未被读取。

### 1.2 问题二：④产品技术参数 是电池口径

右侧工作台第 1 步的「④ 产品技术参数」（文档位 `s1_techparams`）来源被钉死为
`clm_calc_product_tech`：

- `cpq_agent_server.py:191` `"s1_techparams": ("table", "④ 产品技术参数", ("价格测算单","产品技术参数"), True)`
- `cpq_agent_server.py:1429` `("s1_techparams", "clm_calc_product_tech", "产品技术参数")`

`clm_calc_product_tech` 是《亿纬锂能 DA 梳理.xlsx》里的成品参数表（产品族：锂原电池 /
锂离子电池包 / 储能系统 / 光伏组件，见 `tech_app/tools/build_quote_product_params.py`）。
包装行业选出来的盒型，其技术参数**不可能**落进这张电池表。

要求：包装行业下，④产品技术参数 调整为**盒型库的技术参数**。

### 1.3 已具备但未接线的基础（本批只做接线，不重造）

| 既有资产 | 位置 | 现状 |
| --- | --- | --- |
| 行业注册表 | `cpq_industries.py`（`packaging` 已注册） | 报价侧 0 引用 |
| 包装需求模板 64 字段 / 10 必填 | `tech_app/backend/services/industry_templates.py:165` `PACKAGING_SPEC` | 报价侧 0 引用 |
| 包装需求完整性检查 | `requirement_service.requirement_precheck()` 已按 `industry='packaging'` 分支 | 技术工艺侧可用，报价助手未用 |
| 盒型库 | `kb_packaging_box_type`、`kb_repo.packaging_box_types()` | 已有，未接报价助手 |
| 包装报价引擎 | `cpq_packaging_quote.py`（`price()` / `recompute()`） | **无任何模块 import** |
| 包装报价面板 | `tech_app/frontend/packaging-quote-panel.js` | **无任何页面引用** |

---

## 2. 设计原则（冻结）

1. **字段与必填项的唯一来源是行业**：行业注册表（有哪些行业）+ 行业模板
   （`industry_templates.field_keys/required_keys/section_checks`）+ 盒型库（包装的技术参数）。
   任何模块**不得**另抄一份清单。
2. **不许静默降级**：行业未知/缺模板时，按 `industry_templates.DEFAULT_INDUSTRY`（`semiconductor`）
   处理并**如实说明**，不许把包装塞进半导体字段，也不许放过门禁。
3. **不许反向放水**：门禁只能"换来源"，不能"取消检查"。包装行业缺 `box_type` / `quote_quantity` /
   三边内径等必填项时，仍必须拦住。
4. **未选定产品不填参数**：没有选到盒型时，④产品技术参数保持空表 + 待选状态，
   **不许**编造尺寸区间、克重、闭合方式。

---

## 3. 契约 A：行业化的需求完整性门禁

### 3.1 冻结接口

`cpq_agent_server.py` 必须提供（模块级，红测直接调用）：

```python
STEP1_REQUIRED_BY_INDUSTRY: dict            # industry -> tuple[(key, label), ...]
def step1_required(industry) -> tuple       # 返回该行业的必填 (key, label) 序列
def step1_missing(req, *, industry=None) -> list   # 返回缺失项的中文标签
```

- `step1_required(industry)` 的取值规则（冻结）：
  - **`packaging` 必须由 `industry_templates` 派生**：`key` 取自
    `industry_templates.field_keys("packaging")`，必填集合取
    `required_keys("packaging")`，`label` 取 `labels("packaging")[key]`（中文标签，
    不得回退成原始 key）。
  - **`semiconductor` / `battery` / `appliance` 沿用既有三项**
    `max_dimension` / `application_scope` / `operating_temperature`，逐字不变。
    注意这三个键**不是**需求模板字段（`required_keys('semiconductor')` 是另外 14 个键），
    它们是报价助手产品匹配的门禁输入 —— 把它们换成需求模板必填项就是回归，禁止。
  - 未知 / 空 / 历史键（`flexible`）→ 按 `industry_templates.DEFAULT_INDUSTRY` 处理，不抛异常。
- `step1_required("packaging")` 的 key 集合必须**恰好**等于
  `set(industry_templates.required_keys("packaging"))`（当前 10 项），并且**不得**包含
  `operating_temperature` / `application_scope` / `max_dimension`。
- 行业清单唯一来源是 `cpq_industries.INDUSTRY_KEYS`，不得在 `cpq_agent_server.py` 里另写行业数组。
- 本批**禁止**改动 `industry_templates` 的字段/必填清单（那是技术工艺侧的事实源）。
- `step1_missing(req, industry=...)` 与 `step1_missing(req)`（不传行业）都要能用；
  不传行业时按 `industry_templates.DEFAULT_INDUSTRY`。
- 行业键的规范化走 `industry_templates.normalize()` / `cpq_industries`：未知/空 → 默认行业，
  且**不得**抛异常。

### 3.2 调用点

`match_products` 工具（`:790`）与 `/api/step1/match` 的 `phase="intent"`（`:1041`）都必须
把当前需求的行业传进门禁。行业来源优先级（冻结）：

1. 请求体显式 `industry`（非空且合法）；
2. 已识别的需求里 `industry` 字段；
3. `industry_templates.DEFAULT_INDUSTRY`。

### 3.3 问题一的验收场景

需求文本：`数码天地盒 尺寸（mm）30*30*20；盖面纸 铜版纸，亮膜；底面纸 铜版纸，哑膜；盖板材 灰板，厚度2.5mm`

- 行业 = `packaging`，且 `step1_required` 不含 `operating_temperature` 时：
  门禁**不得**因「工作温度」返回 `stage="intent"`；
- 该需求若只缺包装必填项（例如 `quote_quantity`），必须照常拦住，且 `missing` 用**中文标签**；
- 半导体行业下同一份 `req` 缺 `operating_temperature` 时，仍必须拦住（不许为了包装把半导体一起放开）。

---

## 4. 契约 B：④产品技术参数 行业化

### 4.1 冻结接口

`cpq_agent_server.py` 必须提供：

```python
def tech_param_columns(industry) -> list          # ④产品技术参数的表头定义
def tech_param_source(industry) -> str            # 该行业技术参数的事实源标识
def tech_param_row(industry, product) -> dict     # 由选中的产品/盒型生成一行技术参数
```

- `tech_param_source("semiconductor")` 必须仍是 `"clm_calc_product_tech"`（**既有行为不变**）。
- `tech_param_source("packaging")` 必须是盒型库来源（`"kb_packaging_box_type"`）。
- `tech_param_columns("packaging")` 必须来自盒型库表结构/`kb_repo.packaging_box_types()` 的键，
  至少覆盖：`box_type_code`、`name`、`family`、尺寸区间（`size_l_min`/`size_l_max`/
  `size_w_min`/`size_w_max`/`size_h_min`/`size_h_max`）、`fit_clearance`、
  `grey_board_thickness`、`face_paper_gsm`、`closure_type`、`part_count`、`v_groove`、
  `hand_mount_ratio`、`standard_seconds`、`automation_level`、`applicable_industries`、
  `business_status`。
  **禁止**出现电池专有键（`cell_type` / `nominal_voltage` / `energy_density` / `pv_*` 等）。
- `tech_param_columns("semiconductor")` 必须仍等于 `product_params` 既有列定义（逐字不变）。
- `tech_param_row("packaging", product)`：`product` 为空 / 没有 `box_type_code` 时返回 `{}`
  （**不是**编造默认值）；有 `box_type_code` 时按盒型库记录生成，缺列留空字符串。
- `_PRODUCT_SOURCES`（`s1_techparams` → 事实源）必须行业化：包装行业**不得**映射到
  `clm_calc_product_tech`。

### 4.2 输出形状

```json
{"industry": "packaging", "source": "kb_packaging_box_type",
 "columns": [{"key": "box_type_code", "label": "盒型编码"}, {"key": "closure_type", "label": "闭合方式"}],
 "row": {"box_type_code": "YT-RB-01001-A", "name": "天地盖盒（全盖）", "closure_type": "天地盖"}}
```

- `columns[].label` 必须是中文（业务用户直接读），不得把原始 key 当标签。
- 不得把 `clm_calc_product_tech` 的列名混进包装列定义。
- **下发通道冻结**：渲染 `s1_techparams` / `s2_techparams` 的 `ui` 事件必须同时带
  `techparams_columns`（= `tech_param_columns(industry)` 的结果）与既有 `techparams_row`。
  前端表头只允许用 `techparams_columns`，不得再依赖前端常量。
- 服务端源码里必须**同时**保留 `clm_calc_product_tech`（半导体）与 `kb_packaging_box_type`
  （包装）：换源是"按行业分流"，不是"把电池表删掉"。

---

## 5. 契约 C：报价侧与工艺侧字段同源（防漂移）

- 报价助手前端 `确认需求解析结果.html` 的**产品技术参数表头必须由后端下发**：读 `ui` 事件里的
  `techparams_columns`（冻结字段名），前端**不得**再硬编码一份电池列表头；
  工艺侧 `tech_app/apps/tech-process/index.html` 同样不得硬编码第二份字段清单。
- 两处前端都**不得**出现第二份"包装字段清单"：包装字段只允许来自
  `industry_templates.PACKAGING_SPEC`（后端）与 `requirement-create.js` 的 `RC_PACKAGING_SPECS`（前端）。
- 行业键必须来自 `cpq_industries.INDUSTRY_KEYS`，不得在前端另写行业数组。

---

## 6. 非目标

- 不实现包装定价算法（`cpq_packaging_quote.price()` 已有，接线由后续批次负责）。
- 不改成本公式、不改费率口径、不新增知识库表。
- 不改技术工艺侧的需求模板本体（第 2 批已完成）。
- 不为通过流程填 mock 业务值；不把文件名/项目名当行业判据。

---

## 7. 可自动化验收

```bash
./open-claude/.venv/bin/python -m unittest tests.test_quote_agent_industry_alignment_red -v
```

分组：A 门禁行业化（11）/ B 产品技术参数（11）/ C 前后端同源（6）/ D 不回归（5）。

## 8. 人工验收

1. 报价助手选「包装」行业，输入 1.1 的数码天地盒需求 → **不再**出现「缺少：工作温度」，
   进入产品匹配；
2. 选一个盒型 → 右侧「④ 产品技术参数」显示盒型库字段（盒型编码/尺寸区间/闭合方式/灰板厚度…），
   不再出现电池字段；
3. 包装行业下**不选**盒型 → 技术参数表保持空表 + 待选提示，不出现编造值；
4. 切回半导体行业 → 门禁仍是三项、④产品技术参数仍是原电池表（逐字不变）。

## 9. 不允许减少的既有能力

- 半导体/电池/电器三行业的门禁、④产品技术参数、产品信息列表行为**逐字不变**；
- `test_packaging_requirement_template_red`、`test_industry_registry_unified_red`、
  `test_packaging_box_type_matching_red`、`test_packaging_quote_close_loop_red` 必须保持通过。
