# 规格：包装工艺路线与标准工时 —— 包装第 6 批

> 批次：包装 8 批计划的**第 6 批**。依赖第 1 批（四行业注册表）、第 2 批（包装需求模板
> 3.1–3.6）、第 3 批（包装知识库扩展表 + 12 盒型 / 23 条工艺模板）、第 4 批（盒型五维匹配与
> 工艺经理确认）、第 5 批（参数化部件展开与包装 BOM）已完成。
> 红测：`tests/test_packaging_process_route_red.py`。
> 数据依据：`礼盒盒型库_数据样例.xlsx` 的 `03-盒型-工艺路线`，与 `报价逻辑-0903.xlsx` 的成本列
> 顺序（印刷 → 覆膜 → 烫金/冷烫 → 丝印 → 过光油/UV → 压纹 → 击凹凸 → 裱纸 → 啤切 → V 槽 → …）。

本批只做「确认盒型 + 包装 BOM → 生成工艺路线与标准工时 → 工艺经理确认并冻结版本」。**不做**
成本公式求值（第 7 批）、利润与报价单（第 8 批）、产线排程与设备日历。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_process_route_red.py`

## 1. 背景与真实问题

第 3 批把 23 条工艺模板灌进 `kb_packaging_process_template`，第 5 批只把它们当作 BOM 的
`process` 类**引用行**（`item_key = step_name`，去重后连工时、设备、质控点都没带出来）。到现在为止：

- **没有路线**：没有任何代码把工序按顺序排出来，也没有标准工时合计；
- **没有顺序校验**：`印刷 → 覆膜 → 烫金 → UV → 压凹凸 → 模切` 这条硬顺序只写在提示词里，
  代码与库里都没有约束，谁先谁后靠人记；
- **需求驱动的表面工艺没落地**：`需求 3.4` 的覆膜/烫金/UV/丝印/压凹凸/模切填了以后，
  对路线**没有任何影响**（模板里的 `表面处理` 是个聚合工序：「覆膜 → 烫金 → 局部UV（选配）」）；
- **没有确认与版本**：工艺经理确认后没有任何冻结痕迹，后续改需求/BOM 也无人知道要重新确认；
- **覆盖不全**：23 条工艺模板只覆盖 **2 个**盒型（`YT-RB-01001-A` 11 道 /
  `YT-RB-02001-A` 12 道），另外 **10 个盒型**没有工艺模板 —— 这必须是明确缺口，不得编路线。
- 三行业既有 `wip_process_plan` / `wip_process_step` 以 `part_id`（设计 IR 的零件）为主键，
  包装没有 IR；本批另建表，**不动**这两张表。

## 2. 数据契约

### 2.1 输入

1. **第 4 批确认的盒型**：`wip_packaging_box_match.decision = 'confirmed'` 的
   `confirmed_box_type`；没有 → `RouteError(409, "box_type_not_confirmed")`。
2. **第 5 批已建的包装 BOM**：`wip_packaging_bom_item` 里存在该 `(project_id, requirement_no)`
   的 `process` 行；没有 → `RouteError(409, "bom_not_built")`（提示先跑第 5 批展开）。
3. **需求 3.4 表面工艺字段**（映射见 §2.3）与 `quote_quantity`（批量工时）。

### 2.2 工序闭集与规范顺序（`PROCESS_CATALOG`）

`PROCESS_CATALOG` 是**闭集**：工序名 → 规范位次（数值越小越先做）。位次由**本 Spec 定义**，
参照 0903 的成本列顺序（印刷 → 覆膜 → 烫金/冷烫 → 丝印 → 过光油/UV → 压纹 → 击凹凸 → 裱纸 →
啤切 → V 槽 → …）。

**不得**改用 `kb_packaging_process_template.seq` 排序：实测该字段是里程碑分组（`YT-RB-01001-A`
的 `seq` 取值只有 10/20/30/40，把 `手裱` 与 `灰板开料` 并列在 10、把 `面纸印刷` 与 `清洁包装`
并列在 40），不是线性工序顺序；`seq` 只在 §2.4.1 去重时用来挑代表行。

位次里有一处必须服从种子相对顺序：`铰链贴合` / `磁铁嵌入` 必须早于 `机裱` / `手裱`
（书型盒的磁铁与铰链要压在面纸下，种子里书型盒的 `铰链贴合(40) → 磁铁嵌入(50) → 手裱(60)`
就是这个相对关系），因此二者位次取 110 / 120，裱纸类取 130 / 140。

| 位次 | 工序 | 位次 | 工序 | 位次 | 工序 |
| --- | --- | --- | --- | --- | --- |
| 10 | 灰板开料 | 70 | 丝印 | 130 | 机裱 |
| 20 | V 槽开槽 | 80 | UV 上光 | 140 | 手裱 |
| 30 | 灰板成型 | 90 | 压凹凸 | 150 | 内托组装 |
| 40 | 面纸印刷 | 100 | 面纸模切 | 160 | 组装 |
| 45 | 表面处理（聚合工序） | 110 | 铰链贴合 | 170 | 检验 |
| 50 | 覆膜 | 120 | 磁铁嵌入 | 180 | 清洁包装 |
| 60 | 烫金 | — | — | — | — |

共 **19** 条。`表面处理` 位次 45（紧跟印刷、在覆膜之前）：它是模板里的聚合工序
「覆膜 → 烫金 → 局部UV（选配）」，只在需求**没有**填任何覆膜/烫金/UV 时保留原样（见 §2.4.2），
所以它与 `覆膜` 不会同时出现在一条路线里。

**硬顺序链**（两两相对顺序不得颠倒，缺项跳过）：

```python
HARD_ORDER_CHAIN = ("面纸印刷", "覆膜", "烫金", "丝印", "UV 上光", "压凹凸", "面纸模切")
```

- `V 槽开槽` 是包装特有工序，与表面工艺链无先后约束（位次 20 已在最前段）。
- 闭集外的工序名一律视为非法（`unknown_process`），不得静默接受。

### 2.3 需求字段 → 表面工序（`SURFACE_REQUIREMENTS`）

| 需求字段 | 需要的工序 |
| --- | --- |
| `print_colors` / `spot_colors` | 面纸印刷 |
| `lamination` | 覆膜 |
| `hot_stamping` | 烫金 |
| `silk_screen` | 丝印 |
| `uv_coating` | UV 上光 |
| `emboss_deboss` | 压凹凸 |
| `die_cutting` | 面纸模切 |

**真值判定**（`_is_required`，写死，不许实现自己发挥）：取值去空白、转小写后，
命中否定闭集 `{"", "否", "无", "不需要", "不要", "没有", "不需", "none", "n", "no", "false",
"0", "—", "-"}` → **不需要**；数值 ≤ 0 → 不需要；其余任何非空取值（`是`、`需要`、`单面`、
`局部UV`、`哑膜`…）→ **需要**。

### 2.4 路线生成规则（`build_route_steps`，确定性纯函数）

1. **基础工序**：取该盒型 `kb_packaging_process_template` 的全部行，按 `step_name` **去重**
   （代表行 = `seq` 最小者；同 `seq` 时取 `part_code` 升序靠前者）。
2. **`表面处理` 是聚合工序**：若需求需要 `覆膜` / `烫金` / `UV 上光` 中**任意一个**，就用
   真正需要的那些工序**替换**它（按规范位次排）；一个都不需要→**保留** `表面处理` 原样
   （`source = "template"`、连同模板工时、位次 45），不臆造也不删除；同时把工序名记进
   `gaps.aggregate_steps`，让「这条路线里还有一道没拆开的聚合工序」在界面上可见。
3. **需求需要但模板没有**的工序（`面纸印刷` / `面纸模切` / `丝印` / `压凹凸`）→ 合成补齐。
   模板已有同名工序时**不重复出**（例如模板已有 `面纸模切`，需求又填了模切，只出 1 道）。
4. **工时口径（不许编）**：
   - 模板原样保留的工序 → `standard_seconds` = 模板值、`needs_standard_time = False`、
     `source = "template"`；
   - 由 `表面处理` 展开出来的工序、以及需求合成出来的工序 → `standard_seconds = null`、
     `needs_standard_time = True`；`source = "template:表面处理"`（展开）或
     `"requirement:<字段名>"`（合成）。**绝不允许**把 `表面处理` 的 12s 按个数摊给覆膜/烫金。
5. **设备与质控**：合成/展开工序的设备取固定表 `SURFACE_STATIONS`（覆膜→覆膜机、烫金→烫金机、
   丝印→丝印机、UV 上光→UV 上光机、压凹凸→压凹凸机、面纸印刷→胶印机、面纸模切→模切机），
   `automation = "自动"`、`parallel_ok = 0`；展开自模板的工序继承该模板行的 `control_point`，
   纯合成工序 `control_point = null`。模板原样工序的 `workstation` / `work_content` /
   `automation` / `control_point` / `parallel_ok` 逐字保留。
6. **排序与编号**：全部工序按 `PROCESS_CATALOG` 位次升序排序（位次相同按工序名升序），
   `step_no = 10 × 序号`（从 10 开始，10/20/30…连续）。
7. **`depends_on`**：等于排序后**前一道**工序的 `step_no`；第一道为 `null`。它表达硬顺序，
   供第 7 批排产使用。

返回结构（`build_route_steps`）：

```json
{
  "engine_version": "packaging_route_v1",
  "box_type_code": "YT-RB-01001-A",
  "steps": [
    {"step_no": 10, "step_name": "灰板开料", "rank": 10, "workstation": "灰板分切机",
     "work_content": "按部件尺寸裁切灰板", "standard_seconds": 10.0,
     "needs_standard_time": false, "automation": "自动", "control_point": "裁切精度±0.2mm，边缘不起层",
     "parallel_ok": 0, "depends_on": null, "source": "template", "requirement_field": null},
    {"step_no": 40, "step_name": "覆膜", "rank": 50, "workstation": "覆膜机",
     "standard_seconds": null, "needs_standard_time": true, "automation": "自动",
     "control_point": "覆膜无气泡、无起皱", "source": "template:表面处理",
     "requirement_field": "lamination", "depends_on": 30}
  ],
  "required_surface": ["面纸印刷", "覆膜", "烫金", "面纸模切"],
  "total_seconds": 181.0, "has_incomplete_time": true,
  "needs_standard_time": ["覆膜", "烫金"]
}
```

### 2.5 工时合计

- `total_seconds` = Σ 所有 `standard_seconds` 非空工序（**单件**，秒）。
- `batch_seconds` = `total_seconds × quote_quantity`；`quote_quantity` 缺失或 ≤ 0 → `null`
  （不猜数量）。
- `has_incomplete_time` = 存在任一 `standard_seconds` 为 `null` 的工序。
- 无表面工艺需求时，`total_seconds` 必须等于盒型 `standard_seconds`（模板合计与盒型标准工时
  一致：`YT-RB-01001-A` = 193.0、`YT-RB-02001-A` = 305.0）。

### 2.6 顺序校验（`validate_order`）

`validate_order(steps) -> list[str]` 返回**违规码列表**（空列表 = 合法），码闭集：

| 码 | 触发条件 |
| --- | --- |
| `unknown_process:<工序名>` | 工序名不在 `PROCESS_CATALOG` |
| `illegal_process_order:<前>:<后>` | `HARD_ORDER_CHAIN` 里同时存在的两步，前者 `step_no` ≥ 后者 |
| `duplicate_step:<工序名>` | 同名工序出现多次 |
| `step_no_not_ascending` | `step_no` 不是严格递增 |

- 生成出来的路线必须**天然合法**（红测逐盒断言 `validate_order(...) == []`）。
- 人工改过或外部导入的路线可能非法；此时 `confirm_route` 必须拒绝
  （`RouteError(409, "route_not_confirmable")`，消息里带违规码）。

### 2.7 缺口闭集

| 情况 | 结果 |
| --- | --- |
| 盒型没有工艺模板（10 个盒型） | `RouteError(409, "no_process_template")`，消息带盒型编码 |
| 没有确认盒型 | `RouteError(409, "box_type_not_confirmed")` |
| 包装 BOM 未建（没有 `process` 行） | `RouteError(409, "bom_not_built")` |
| 路线不存在（未生成就确认/读版本） | `RouteError(404, "route_not_found")` |
| 顺序违规 | `RouteError(409, "route_not_confirmable")` |
| 非 `packaging` 行业 | `RouteError(400)` |

## 3. 落库

### 3.1 新表（`da_schema.sql`）

```sql
-- 一条需求一张路线：同一 (project_id, requirement_no) 整体替换
CREATE TABLE IF NOT EXISTS wip_packaging_process_route (
    project_id        TEXT NOT NULL,
    requirement_no    TEXT NOT NULL DEFAULT '',
    industry          TEXT NOT NULL DEFAULT 'packaging',
    engine_version    TEXT NOT NULL,
    generated_at      TEXT NOT NULL,
    box_type_code     TEXT,
    total_seconds     REAL,
    batch_seconds     REAL,
    has_incomplete_time INTEGER NOT NULL DEFAULT 0 CHECK (has_incomplete_time IN (0, 1)),
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'confirmed')),
    confirmed_by      TEXT,
    confirmed_at      TEXT,
    stale             INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    stale_reasons     TEXT,          -- JSON 数组
    steps_fingerprint TEXT,          -- 工序序列指纹
    surface_json      TEXT,          -- 判定用到的需求表面字段快照
    quote_quantity    REAL,
    updated_at        TEXT,
    PRIMARY KEY (project_id, requirement_no)
);

CREATE TABLE IF NOT EXISTS wip_packaging_process_route_step (
    project_id     TEXT NOT NULL,
    requirement_no TEXT NOT NULL DEFAULT '',
    step_no        INTEGER NOT NULL,
    step_name      TEXT NOT NULL,
    rank           INTEGER,
    workstation    TEXT,
    work_content   TEXT,
    standard_seconds REAL,
    needs_standard_time INTEGER NOT NULL DEFAULT 0 CHECK (needs_standard_time IN (0, 1)),
    automation     TEXT,
    control_point  TEXT,
    parallel_ok    INTEGER NOT NULL DEFAULT 0 CHECK (parallel_ok IN (0, 1)),
    depends_on     INTEGER,
    source         TEXT,
    requirement_field TEXT,
    note           TEXT,
    PRIMARY KEY (project_id, requirement_no, step_no)
);

-- 版本快照：只增不改（仓库层不提供 UPDATE/DELETE）
CREATE TABLE IF NOT EXISTS wip_packaging_process_route_version (
    version_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT NOT NULL,
    requirement_no TEXT NOT NULL DEFAULT '',
    version        INTEGER NOT NULL,
    confirmed_by   TEXT,
    confirmed_at   TEXT NOT NULL,
    box_type_code  TEXT,
    steps_fingerprint TEXT,
    surface_json   TEXT,
    quote_quantity REAL,
    total_seconds  REAL,
    has_incomplete_time INTEGER NOT NULL DEFAULT 0,
    steps_json     TEXT NOT NULL,
    UNIQUE (project_id, requirement_no, version)
);
```

### 3.2 重算、确认与失效

- **重算**：同一 `(project_id, requirement_no)` 先删 `wip_packaging_process_route_step` 全部行、
  再重建；主表按主键 upsert。**`status='confirmed'` 的路线被重算时回到 `draft` 并清空
  `confirmed_*`**（路线内容变了就必须重新确认），但**已冻结的版本快照一律不动**。
- **确认**：`status='draft'` 且 `validate_order == []` 才能确认；写入 `confirmed_by` /
  `confirmed_at`，并**追加**一条版本快照（版本号 = 该 `(project_id, requirement_no)`
  已有快照数 + 1）。重复确认同一份未变化的路线**幂等**（不重复追加版本、`confirmed_at` 不变）。
- **失效判定**（`stale`）：确认后再次读取时，用当前输入重算指纹并与**最近一次冻结版本**比对：

| 差异 | `stale_reasons` |
| --- | --- |
| 工序序列（含 step_no）变了 | `route_changed` |
| 需求表面字段取值变了 | `requirement_changed` |
| `quote_quantity` 变了 | `quantity_changed` |

  `stale = true` 时**保留** `confirmed_*`（不抹掉人工确认），只在读回结果里提示重新确认。
- 每次确认/重算写项目审计：`store.audit(project_id, "workflow:packaging_route_confirmed"
  | "workflow:packaging_route_rebuilt", {...})`。

## 4. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/projects/{project_id}/requirement/packaging-route` | 生成/重算并落库 |
| `GET` | `/api/projects/{pid}/requirement/packaging-route` | 读回路线（含缺口、统计、stale） |
| `POST` | `/api/projects/{project_id}/requirement/packaging-route/confirm` | 工艺经理确认并冻结版本 |
| `GET` | `/api/projects/{pid}/requirement/packaging-route/versions` | 版本快照列表（只读） |

- 只对 `industry = 'packaging'` 生效；其它行业 → `400`。
- 生成/确认是工艺侧写权限：`ROUTE_WRITE_ROLES` **直接引用**第 4 批的
  `packaging_match.BOX_MATCH_DECIDE_ROLES`（同一对象，不得另抄）；其它已登录角色只读、写 → `403`。
- 响应：生成/确认/读回统一 `{"route": <load_route 结构>}`，版本列表 `{"versions": [<快照>]}`。
- 读路由的路径参数写 `{pid}`，避免顶掉批次 7 的「43 条单参数 GET 路由」基线。

`load_route` / `build_route` 返回结构：

```json
{
  "built": true,
  "box_type_code": "YT-RB-01001-A",
  "requirement_no": "REQ-PKG-0001",
  "engine_version": "packaging_route_v1",
  "generated_at": "2026-09-21T10:00:00",
  "status": "draft",
  "confirmed_by": null, "confirmed_at": null,
  "stale": false, "stale_reasons": [],
  "total_seconds": 181.0, "batch_seconds": 181000.0,
  "has_incomplete_time": true,
  "steps": [{"step_no": 10, "step_name": "灰板开料", "...": "..."}],
  "required_surface": ["面纸印刷", "覆膜", "烫金", "面纸模切"],
  "gaps": {"needs_standard_time": ["覆膜", "烫金"], "order_violations": [],
           "aggregate_steps": [], "no_process_template": false},
  "stats": {"step_count": 12, "template_steps": 10, "synthetic_steps": 2,
            "manual_steps": 4, "auto_steps": 8, "confirmed_versions": 0}
}
```

- 没有路线 → `built = false`、`steps = []`、`stats.step_count = 0`（`GET` 不报错；
  但 `confirm` 走 §2.7 的 `route_not_found`）。
- `stats.template_steps` 计 `source` 以 `template` 开头的工序数（含 `template:表面处理`），
  `synthetic_steps` 计 `source` 以 `requirement:` 开头的工序数，两者之和 = `step_count`；
  `manual_steps` 计 `automation = "手工"`、`auto_steps` 计 `automation = "自动"`。

### 4.5 命名契约（红测与实现共用，不得改名）

新增 `tech_app/backend/services/packaging_route.py`：

| 名称 | 说明 |
| --- | --- |
| `ENGINE_VERSION = "packaging_route_v1"` | 写进落库行 |
| `PROCESS_CATALOG` | §2.2 的位次表（`dict[str, int]`） |
| `HARD_ORDER_CHAIN` | §2.2 的硬顺序链 |
| `SURFACE_REQUIREMENTS` | §2.3 的需求字段 → 工序名（`dict[str, str]`） |
| `SURFACE_STATIONS` | §2.4.5 的设备固定表 |
| `ROUTE_WRITE_ROLES` | 直接引用 `packaging_match.BOX_MATCH_DECIDE_ROLES` |
| `required_surface_steps(inputs: dict) -> list[str]` | 需求 → 需要的工序（按位次排序） |
| `build_route_steps(box_type_code: str, inputs: dict) -> dict` | 纯函数（只读知识库） |
| `validate_order(steps) -> list[str]` | §2.6 违规码 |
| `route_fingerprint(box_type_code: str, steps, inputs: dict) -> tuple[str, str, float|None]` | 返回（工序指纹、表面字段快照、数量） |
| `build_route(project_id, requirement_no="") -> dict` | 生成 → 落库 |
| `load_route(project_id, requirement_no="") -> dict` | 读回 + 缺口 + 统计 + stale |
| `confirm_route(project_id, requirement_no, *, actor=None) -> dict` | 确认并冻结版本 |
| `route_versions(project_id, requirement_no="") -> list[dict]` | 版本快照（按版本升序） |
| `RouteError(message, status_code=409, code="")` | 业务错误 |

`da_repo` 新增：`save_packaging_route(project_id, requirement_no, route, steps) -> None`、
`load_packaging_route(project_id, requirement_no="") -> Optional[dict]`、
`load_packaging_route_steps(project_id, requirement_no="") -> list[dict]`、
`append_packaging_route_version(record: dict) -> int`、
`packaging_route_versions(project_id, requirement_no="") -> list[dict]`。

`box_type` 行来自 `kb_packaging_box_type`；`inputs` 是需求字段映射（至少含 `quote_quantity`
与 §2.3 的表面字段）；模板行来自 `kb_repo.packaging_process_templates(box_type_code=...)`。

## 5. 非目标

- 不算材料/加工/人工/包材/运输成本，不查价格、不算最低开机费与模具摊销（第 7 批）。
- 不算利润、加价、折扣、税金与报价单（第 8 批）。
- 不排产能、不排设备日历、不做工序派工与报工。
- 不做「无模板盒型自动生成新路线」的推荐（缺口就是缺口，交工艺经理）。
- 不写 Postgres、不建工作流任务卡、不调大模型、不联网。
- 不改第 2 批需求字段、第 3 批演示数据、第 4 批匹配契约、第 5 批 BOM 契约。

## 6. 历史兼容

- 三张新表都是新增，不动既有表结构。
- 既有 `wip_process_plan` / `wip_process_step` / `services/process.py` 行为逐字不变
  （包装路线不走它们，也不写 `wip_process_step`）。
- 没有路线的项目：`GET` 返回 `built = false` + `steps = []`，不报错。
- 三个原行业不产生也不消费这三张表的数据。

## 7. 可自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_process_route_red
```

红测分组：A 工序闭集与顺序校验 / B 需求驱动的表面工序 / C 路线生成 /
D 标准工时 / E 落库与缺口 / F 确认与版本 / G 接口与角色 / H 非回归。

## 8. 人工验收

1.2 需求确认页（已确认盒型 + 已建 BOM）→ 路线面板出现工序表（含设备、工时、自动化、质控点）→
需求填「烫金+覆膜」后重算 → `表面处理` 消失、出现覆膜与烫金且工时标「待补」→ 工艺经理确认 →
版本列表出现 1 条快照 → 改需求表面字段 → 面板提示需重新确认，且旧版本快照内容不变。

## 9. 不允许减少的既有能力

第 5 批的 BOM 生成/锁定与七类分类；第 4 批的盒型匹配与四态决策；第 3 批知识库的行业隔离与幂等；
第 2 批包装需求模板 64 字段 / 10 必填；既有 `wip_process_plan` / `wip_process_step` /
`services/process.py` 的语义与调用方；三行业链路。
