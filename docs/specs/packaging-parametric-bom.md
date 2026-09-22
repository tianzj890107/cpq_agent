# 规格：参数化部件展开与包装 BOM —— 包装第 5 批

> 批次：包装 8 批计划的**第 5 批**。依赖第 1 批（四行业注册表）、第 2 批（包装需求模板
> 3.1–3.6）、第 3 批（包装知识库扩展表 + 12 盒型/31 部件模板/23 工艺模板）、第 4 批（盒型
> 五维匹配与工艺经理确认）已完成。
> 红测：`tests/test_packaging_parametric_bom_red.py`。
> 数据依据：`裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx` 的 01盒型 / 02-盒型-部件构成，
> 与 `报价逻辑-0903.xlsx` 的 BOM 分类口径。

本批只做「确认盒型 → 参数化展开部件尺寸 → 组装包装 BOM」。**不做**工艺路线生成与顺序校验
（第 6 批）、成本公式求值（第 7 批）、利润与报价单（第 8 批）。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_parametric_bom_red.py`

## 1. 背景与真实问题

第 3 批把 31 条参数化部件模板灌进了 `kb_packaging_part_template`，但到现在为止：

- **没有任何展开能力**：模板只是躺在知识库里，没有任何代码读 `size_expr`；
- **仓库里没有任何安全表达式求值器**（全仓搜索 `ast.parse` / `safe_eval` / `evaluate_expression`
  零命中），所以「L+4t+2c」这类公式目前无处可算；
- **表达式口径不统一**，必须分类对待，实测 31 条里有四类：
  1. 纯算术：`L+4t+2c`、`W`、`L+2t`、`W内-2t`；
  2. 带中文别名/派生量：`H盖`（盖高）、`L外`/`W外`（外盒）、`L内`/`W内`（内盒）、`铰链宽40`；
  3. 带外部量：`盖展开尺寸 + 包边 + 出血3mm`、`整体展开 + 包边 + 出血3mm`；
  4. 纯文字/标准件：`标准件`（磁铁）、`长度 = W/3 + 40`（带赋值前缀）、
     `（L-4）×（W-4）× 8`（全角括号 + 三段尺寸）。
- **覆盖不全**：31 条部件模板只覆盖 **3 个**盒型（`YT-RB-01001-A` 10 条 /
  `YT-RB-02001-A` 10 条 / `YT-RB-03001-A` 11 条），23 条工艺模板只覆盖 **2 个**盒型。
  另外 9 个盒型展开必须是**明确缺口**，不得编造 BOM。
- 现有 `wip_part` 以 `ir_id`（设计 IR）为主键，包装没有 IR；`wip_bom_item` 的分类只有
  「原材料/中间品/耗材辅料/工序产出」，装不下包装 BOM 的七类。

## 2. 数据契约

### 2.1 输入

1. **第 4 批确认的盒型**：`wip_packaging_box_match.decision = 'confirmed'` 的
   `confirmed_box_type`。没有确认盒型 → 明确报错，不得自己挑一个候选。
2. **需求 3.2 内尺寸**：`inner_length` / `inner_width` / `inner_height`（来自需求单
   `data`，沿用第 2 批字段，不改字段定义）。
3. **人工变量覆盖** `overrides`：工艺经理为无法自动绑定的变量填值。

### 2.2 变量绑定（口径写死在这张表里，实现不得另加隐式默认值）

| 变量 | 含义 | 来源 | 缺失时 |
| --- | --- | --- | --- |
| `L` | 成品内长 | 需求 `inner_length` | 整体报错（缺内尺寸无法展开） |
| `W` | 成品内宽 | 需求 `inner_width` | 同上 |
| `H` | 成品内高 | 需求 `inner_height` | 同上 |
| `t` | 灰板厚度 | 盒型 `grey_board_thickness` 解析出的**首个数值**（如 `2.0（1.5/2.5可选）`→`2.0`） | 该部件 `needs_input` |
| `c` | 配合间隙 | 需求 `fit_clearance`，缺失时取盒型 `fit_clearance` | 该部件 `needs_input` |
| 仅覆盖变量 | 见下方 `OVERRIDE_ONLY_VARIABLES` 闭集 | **只来自 `overrides`** | 该部件 `needs_input`，并列出 `missing_variables` |

```python
#: 只能由工艺经理覆盖，绝不从 L/W/H/t/c 推断
OVERRIDE_ONLY_VARIABLES = frozenset({
    "H盖", "H内", "L外", "W外", "L内", "W内",
    "盖展开尺寸", "盒身展开尺寸", "整体展开", "外盒展开", "内盒展开", "包边",
})
#: 可自动绑定的变量
AUTO_VARIABLES = frozenset({"L", "W", "H", "t", "c"})
```

- **内联默认值**：变量名若形如 `<别名><数字>`（`铰链宽40`）或 `<别名><数字>mm`（`出血3mm`），
  数字是**该变量的默认值**，别名是变量名 —— 即 `铰链宽40` → `铰链宽 = 40`、
  `出血3mm` → `出血 = 3`。这类变量不需要 `overrides` 即可参与计算，溯源标
  `source = "literal_default"`；它们**不属于** `OVERRIDE_ONLY_VARIABLES`，但被 `overrides`
  覆盖时同样按第 3 条处理。
- `bind_variables` 返回 `{变量名: {"value": float, "source": "requirement" | "box_type" |
  "override" | "literal_default"}}`，只放**已绑定**的变量；未绑定的一律不出现（由求值器报
  `missing_variables`）。
- `bind_variables` 只负责 5 个自动变量 + `overrides`；**内联默认值由展开阶段从表达式里
  收集**，`expand_parts` 返回的 `variables` = `bind_variables` 的结果再并上这些内联默认值
  （`source = "literal_default"`）。
- `overrides` 优先级最高，可覆盖 `L`/`W`/`H`/`t`/`c` 以及内联默认值；被覆盖的变量在溯源里标
  `source = "override"`。
- **不允许**用 `H` 顶替 `H盖`、用 `L` 顶替 `L外` 之类的"看起来合理"的推断：口径未确认前
  一律走 `needs_input`，交工艺经理补。

### 2.3 表达式语法（白名单，不是"够用的正则"）

- 允许：数字、变量名（英文/中文）、`+ - * / ( )`、比较运算符 `> < >= <= = == <>`（只在
  `IF` 条件里用；单独的布尔结果不参与尺寸计算）、函数 `MIN` `MAX` `IF` `IFERROR` `ROUND`。
- 归一化（**只做这些**）：全角括号 `（）`→半角、全角逗号 `，`→`,`、`×`→`*`、`÷`→`/`、
  全角减号 `－`→`-`、去掉表达式首部 `X =` 形式的单个赋值前缀（`长度 = W/3 + 40` →
  `W/3 + 40`；只有「`=` 左侧是单个变量名」时才算赋值前缀，`==`/`>=`/`<=`/`<>` 不算）、
  去掉 `3mm` 这类纯单位后缀（按 §2.2 转成内联默认值）。归一化后连续空白压成单个空格，
  不做别的改写。
- **禁止**：属性访问（`.`）、下标（`[` `]`）、字符串字面量、幂运算（`**`）、
  `lambda`、分号、换行、`import`/`eval`/`exec`/`__`/`open` 等任何名字、以及白名单外的函数。
  违规必须抛错，**绝不执行**。
- 除零 → 抛错；用 `IFERROR` 才允许兜住。
- 引用了未绑定的变量 → 抛错，并把缺失的变量名放进 `FormulaError.missing_variables`。
- 尺寸一律 `mm`，结果保留 **1 位小数**（`round(x, 1)`，半上进位）。

### 2.4 展开结果（每个部件都要能回答"这个尺寸怎么来的"）

```json
{
  "engine_version": "packaging_bom_v1",
  "box_type_code": "YT-RB-01001-A",
  "variables": {"L": {"value": 200, "source": "requirement"},
                "t": {"value": 2.0, "source": "box_type"},
                "c": {"value": 1.8, "source": "box_type"}},
  "parts": [
    {"part_code": "RB01001-P01", "status": "computed",
     "size_expr": "L+4t+2c  ×  W+4t+2c",
     "size_length_expr": "L+4t+2c", "size_width_expr": "W+4t+2c", "size_height_expr": "",
     "length": 211.6, "width": 161.6, "height": null,
     "quantity": 1, "missing_variables": []},
    {"part_code": "RB01001-P02", "status": "needs_input",
     "size_length_expr": "L+4t+2c", "size_width_expr": "H盖",
     "missing_variables": ["H盖"]}
  ],
  "expanded_count": 6, "needs_input_count": 4
}
```

- 部件状态闭集：`computed` / `needs_input` / `locked`。
- 每个部件都带 `size_mode`，闭集 `expression` / `standard_part`：
  - `expression`：三个尺寸表达式正常求值；
  - `standard_part`：表达式为空、或归一化后**既不含数字也不含运算符**（`标准件`），
    说明这是外购标准件 —— 三个尺寸一律 `null`，`missing_variables = []`，`status = "computed"`，
    不报错、不估算。
- `needs_input` 的部件**不给任何尺寸数字**（`length`/`width`/`height` 留空），只给
  `missing_variables`，不许填 0、不许填估算值。已能算出的那一维**必须保留**（如 `RB01001-P02`
  的长度 211.6 保留、宽度缺 `H盖` 为 `null`）——`length`/`width`/`height` 中**至少一维为
  `null`** 时整体 `status` 必须是 `needs_input`。
- 某一维的表达式**为空**表示该部件没有这一维（如 `RB01001-P04` 没有高度），固定给 `null`，
  **不算缺失**、不影响 `status`；只有「表达式存在但引用了未绑定变量」才算缺失。
- `variables` 里排除内联默认值以外的未绑定变量；`expanded_count` 计 `status = "computed"`
  的部件数，`needs_input_count` 计 `needs_input` 数。

### 2.5 BOM 七类（按 `报价逻辑-0903.xlsx` 的口径收紧）

`bom_category` 是**闭集**，本批 7 类：

| 类 | 代码 | 本批内容 | 本批边界 |
| --- | --- | --- | --- |
| 成品 | `finished` | 1 行，对应确认的盒型本身 | 只标盒型与总数量 |
| 盒型部件 | `box_part` | `is_optional = 0` 的部件展开结果 | 尺寸可空（`needs_input`） |
| 材料 | `material` | 部件用到的材料（灰板/面纸/内托/配件…），关联 `kb_material(industry='packaging')` | 只分类与关联，不算钱、不查价 |
| 工艺 | `process` | 该部件在 `kb_packaging_process_template` 里的工序引用 | **不生成路线、不排顺序**（第 6 批） |
| 包材 | `packaging` | `kb_packaging_logistics_rule` 的外箱/托盘等 | 只带出规则引用，不算钱 |
| 工装/模具 | `tooling` | 命中「含刀模制程」的工序：`烫金` / `丝印` / `击凹凸` / `模切` / `装配线`（口径出自 0903「问题点」） | 只标「涉及工装 + 待分摊」，不算钱、不定寿命 |
| 可选部件 | `optional_part` | `is_optional = 1` 的部件（如丝带拉手） | 与 `box_part` 分开列，默认不影响报价 |

- 某类**没有数据时就不出该类行**，不造空行。
- 每行都带 `industry = 'packaging'`、`source`（盒型 / 需求 / 知识库表名）、`engine_version`。

每类的 `item_key` 与生成规则（红测按此精确断言，实现不得改口径）：

| 类 | `item_key` | 生成规则 |
| --- | --- | --- |
| `finished` | 盒型编码 | 恰好 1 行，`item_name` = 盒型名，`quantity` = 需求 `quote_quantity`（缺失则 `null`） |
| `box_part` | 部件编码 | `is_optional = 0` 的部件各 1 行，`quantity` 取模板数量 |
| `optional_part` | 部件编码 | `is_optional = 1` 的部件各 1 行 |
| `material` | 材料文本原文 | 部件 `material` **去重**后各 1 行；`material_code` 用「`material` 首个空白分词在 `kb_material.name` 里唯一包含」解析，唯一命中才填，0 个或多个命中 → `null`（不报错，计入 `stats.material_unresolved`） |
| `process` | `工序名` | 按盒型取 `kb_packaging_process_template`，**按 `step_name` 去重**后各 1 行，其余列取该工序首条（`seq` 最小） |
| `tooling` | `tooling:{part_code}:{seq}` | 只取 `step_name` 或 `work_content` 命中 `烫金`/`丝印`/`击凹凸`/`模切`/`装配线` 的工序，逐条出（不去重）；`component` 写命中的关键词，**不含任何价格/寿命字段** |
| `packaging` | `rule_code` | `kb_packaging_logistics_rule` 全量，逐条出 |

### 2.6 缺口与不编造

- 盒型没有部件模板（12 个盒型里有 9 个）→ `BomError(409)`，`code = "no_part_template"`，
  消息里带盒型编码；不得返回空 BOM 冒充成功。
- 没有确认盒型 → `BomError(409)`，`code = "box_type_not_confirmed"`。
- 非包装行业 → `BomError(400)`。
- 缺内尺寸（`inner_length` 等）→ `BomError(409)`，`code = "missing_requirement_input"`，
  列出缺的键。
- 锁定不存在的 `item_key` → `BomError(404)`，`code = "item_not_found"`。

## 3. 落库

### 3.1 新表（`da_schema.sql`）

```sql
CREATE TABLE IF NOT EXISTS wip_packaging_bom_item (
    project_id      TEXT NOT NULL,
    requirement_no  TEXT NOT NULL DEFAULT '',
    industry        TEXT NOT NULL DEFAULT 'packaging',
    engine_version  TEXT NOT NULL,
    generated_at    TEXT NOT NULL,
    bom_category    TEXT NOT NULL CHECK (bom_category IN (
                        'finished', 'box_part', 'material', 'process',
                        'packaging', 'tooling', 'optional_part')),
    item_key        TEXT NOT NULL,
    item_name       TEXT,
    part_code       TEXT,
    component       TEXT,
    material        TEXT,
    material_code   TEXT REFERENCES kb_material(material_code) ON DELETE SET NULL,
    quantity        REAL,
    unit            TEXT,
    size_length_expr TEXT,
    size_width_expr  TEXT,
    size_height_expr TEXT,
    length_mm       REAL,
    width_mm        REAL,
    height_mm       REAL,
    size_source_json TEXT,
    status          TEXT NOT NULL DEFAULT 'computed' CHECK (status IN (
                        'computed', 'needs_input', 'locked')),
    missing_variables TEXT,
    is_optional     INTEGER NOT NULL DEFAULT 0 CHECK (is_optional IN (0, 1)),
    locked          INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0, 1)),
    locked_by       TEXT,
    locked_at       TEXT,
    note            TEXT,
    updated_at      TEXT,
    PRIMARY KEY (project_id, requirement_no, bom_category, item_key)
);
```

### 3.2 重算与人工锁定

`size_source_json` 是每一维尺寸的溯源，键固定为 `length` / `width` / `height`：

```json
{"length": {"expr": "L+4t+2c", "normalized": "L+4t+2c",
            "variables": {"L": "requirement", "t": "box_type", "c": "box_type"}},
 "width": {"expr": "W+4t+2c", "normalized": "W+4t+2c",
           "variables": {"W": "requirement", "t": "box_type", "c": "box_type"}},
 "height": null}
```

- 没有该维（表达式为空）→ 该项为 `null`；`needs_input` 的维度也照写（`expr` 保留、变量溯源给出已绑定的部分）。
- 标准件（`size_mode = "standard_part"`）三维一律 `null`。

- 重算遵循既有 `wip_*` 约定：**同一 `(project_id, requirement_no)` 整体替换**，不留旧行。
- `locked = 1` 的行**必须原样保留**（含人工改过的尺寸与 `item_name`），不参与替换；
  其余行重建。
- 锁定/解锁都要记 `locked_by` / `locked_at`，并写一条项目审计
  `store.audit(project_id, "workflow:packaging_bom_item_locked" | "..._unlocked", {...})`。
- 锁定后该行 `status` 变 `locked`；解除锁定后回到 `computed` / `needs_input`
  （按当前展开结果重新判定），并清空 `locked_at`。重复锁定/解锁同一状态必须幂等
  （`locked_at` 不变，不重复写审计）。

## 4. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/projects/{project_id}/requirement/packaging-bom` | 按确认盒型展开并落库 |
| `GET` | `/api/projects/{project_id}/requirement/packaging-bom` | 读回 BOM（含缺口与统计） |
| `POST` | `/api/projects/{project_id}/requirement/packaging-bom/lock` | 锁定/解锁单个 BOM 行 |

- 三个接口只对 `industry = 'packaging'` 生效；其它行业 → `400`。
- 展开/锁定是工艺侧写权限：复用第 4 批的 `BOX_MATCH_DECIDE_ROLES`
  （`process_manager` / `process_director` / `admin`），其它已登录角色只读、写 → `403`。
- `overrides` 只能通过 `POST` 请求体传入，服务端不替用户填默认值。
- 前端：`requirement-confirm.js` 增加 BOM 面板（七类分组、部件尺寸、`needs_input` 与缺失变量、
  锁定/解锁按钮、重算按钮），缓存戳按既有约定递增。
- 请求/响应：

| 接口 | 请求体 | 响应 |
| --- | --- | --- |
| `POST .../packaging-bom` | `{"requirement_no": str = "", "overrides": {变量: 数值} = {}}` | `{"bom": <load_bom 结构>}` |
| `GET .../packaging-bom?requirement_no=&pid=` | — | `{"bom": <load_bom 结构>}` |
| `POST .../packaging-bom/lock` | `{"requirement_no": str = "", "item_key": str, "locked": bool = true}` | `{"bom": <load_bom 结构>}` |

`load_bom` / `build_bom` 返回同一结构（第 3 批 `packaging_match.load_box_match` 的既有风格）：

```json
{
  "built": true,
  "box_type_code": "YT-RB-01001-A",
  "requirement_no": "REQ-PKG-0001",
  "engine_version": "packaging_bom_v1",
  "generated_at": "2026-09-21T10:00:00",
  "items": [{"bom_category": "box_part", "item_key": "RB01001-P01", "status": "computed", "...": "..."}],
  "gaps": {"needs_input": ["RB01001-P02"], "missing_variables": {"RB01001-P02": ["H盖"]},
           "material_unresolved": ["装帧布/充皮纸"]},
  "stats": {"total": 31, "by_category": {"finished": 1, "box_part": 9, "optional_part": 1,
            "material": 4, "process": 11, "tooling": 2, "packaging": 3},
            "computed": 6, "needs_input": 4, "locked": 0, "material_unresolved": 1}
}
```

（上例的 31 行是 `YT-RB-01001-A` 在无覆盖变量时的真实分布：`box_part` 的 9 个里 5 个
`computed`、4 个 `needs_input`，加 1 个 `optional_part` → `computed` 6、`needs_input` 4。）

- 没有 BOM 行 → `built = false`、`items = []`、`stats.total = 0`，不报错。

### 4.5 命名契约（红测与实现共用，不得改名）

新增 `tech_app/backend/services/packaging_formula.py`：

| 名称 | 说明 |
| --- | --- |
| `FormulaError(message, missing_variables=None)` | 表达式错误；未绑定变量时带 `missing_variables` |
| `evaluate(expression: str, variables: dict, *, precision: int = 1) -> float` | 受控求值 |
| `ALLOWED_FUNCTIONS` | `frozenset({"MIN","MAX","IF","IFERROR","ROUND"})` |
| `normalize_expression(text: str) -> str` | 2.3 的归一化 |

新增 `tech_app/backend/services/packaging_bom.py`：

| 名称 | 说明 |
| --- | --- |
| `ENGINE_VERSION = "packaging_bom_v1"` | 写进落库行 |
| `BOM_CATEGORIES` | 2.5 的 7 类闭集 |
| `BOM_WRITE_ROLES` | **直接引用** `packaging_match.BOX_MATCH_DECIDE_ROLES`（同一对象，不得另抄一份） |
| `AUTO_VARIABLES` / `OVERRIDE_ONLY_VARIABLES` | 2.2 的两组变量名 |
| `bind_variables(box_type: dict, inputs: dict, overrides: dict) -> dict` | 变量绑定 |
| `expand_parts(box_type_code: str, inputs: dict, *, overrides=None) -> dict` | 纯展开（只读知识库） |
| `build_bom(project_id, requirement_no="", *, overrides=None) -> dict` | 读确认盒型 → 展开 → 组装七类 → 落库 |
| `load_bom(project_id, requirement_no="") -> dict` | 读回 + 统计 |
| `lock_bom_item(project_id, requirement_no, item_key, *, actor=None, locked=True) -> dict` | 锁定/解锁；返回与 `load_bom` 相同的结构 |
| `BomError(message, status_code=409, code="")` | 业务错误，`code` 取 2.6 的闭集 |

`da_repo` 新增：`save_packaging_bom(project_id, requirement_no, items) -> int`、
`load_packaging_bom(project_id, requirement_no="") -> list[dict]`。

`box_type` 是 `kb_packaging_box_type` 的一行；`inputs` 是需求字段映射，至少含
`inner_length` / `inner_width` / `inner_height` / `fit_clearance` / `quote_quantity`
（缺哪个就按 §2.2 表处理）；`overrides` 是「变量名 → 数值」。`expand_parts` 必须先解析
`size_length_expr` / `size_width_expr` / `size_height_expr`，**不得只解析 `size_expr`**
（`size_expr` 只是展示用的人读文本）。

## 5. 非目标

- 不生成工艺路线、不校验工序顺序、不排工时（第 6 批）；`process` 类只做引用。
- 不算材料/加工/物流成本，不查价格、不算最低开机费与模具摊销（第 7 批）。
- 不加包装*业务*品类（`applicable_industries`）匹配、不做内托选型推荐。
- 不写 Postgres、不建工作流任务卡、不调大模型、不联网。
- 不改第 2 批包装需求字段与第 3 批演示数据。

## 6. 历史兼容

- `wip_packaging_bom_item` 是新增表，不动既有表结构。
- 既有 `wip_bom_item` 与 `da_repo.save_bom` 行为逐字不变（包装 BOM 不走它）。
- 没有 BOM 行的项目：`GET` 返回 `items = []` + `built = false`，不报错。
- 三个原行业不产生也不消费该表数据。

## 7. 可自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
```

红测分组：A 表达式引擎安全与正确 / B 变量绑定 / C 部件展开 / D BOM 七类 /
E 锁定与重算 / F 落库与缺口 / G 接口与角色 / H 非回归。

## 8. 人工验收

1.2 需求确认页（已确认盒型）→ BOM 面板出现七类分组 → `needs_input` 部件显示缺失变量 →
填变量覆盖后重算得到尺寸 → 锁定某部件 → 改需求尺寸后重算，锁定行不变、其余行更新。

## 9. 不允许减少的既有能力

第 4 批的盒型匹配与四态决策；第 3 批知识库的行业隔离与幂等；第 2 批包装需求模板 64 字段 /
10 必填；既有 `wip_part` / `wip_bom_item` / `save_bom` 的语义与调用方；三行业链路。
