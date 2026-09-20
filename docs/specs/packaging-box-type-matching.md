# 规格：盒型匹配与人工确认 —— 包装第 4 批

> 批次：包装 8 批计划的**第 4 批**。依赖第 1 批（四行业注册表）、第 2 批（包装需求模板
> 3.1–3.6）、第 3 批（包装知识库扩展表 + 12 个盒型演示数据）已完成。
> 红测：`tests/test_packaging_box_type_matching_red.py`。
> 数据依据：`裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx` 的 01盒型（12 条，含尺寸区间、
> 配合间隙、面纸克重、闭合方式、V 槽）与 `kb_packaging_match_weight`（5 维权重与硬门槛）。

本批只做「客户需求 → 候选盒型 → 工艺经理确认」这一段。**不做**参数化部件展开与 BOM
（第 5 批）、工艺路线生成（第 6 批）、成本公式求值（第 7 批）、利润与报价单（第 8 批）。

## 1. 背景与真实问题

第 3 批已经把 12 个盒型与 5 维权重灌进知识库，但到现在为止**没有任何代码消费它们**：

- `kb_repo` 只提供 `packaging_box_types()` / `packaging_part_templates()` /
  `packaging_process_templates()` / `packaging_insert_accessories()`，**没有**
  `packaging_match_weights()` —— 权重表灌了却读不出来；
- 仓库里不存在任何盒型匹配实现，五维打分与硬门槛一条也没有落地；
- 匹配结果、淘汰原因、人工确认都没有落库：工艺经理确认完刷新页面就丢，谁在什么时候把
  哪个盒型改成了哪个也查不到；
- 没有「输入不全就别装成匹配成功」的约束：包装需求里 `fit_clearance`（配合间隙）是选填，
  `closure_type`（闭合方式）是必填，两者缺一都不该给出可以据以报价的高分。

红测要先把这四件事钉住，再交给实现方。

## 2. 匹配引擎

### 2.1 输入（都来自第 2 批包装需求 3.2 / 3.3）

| 输入键 | 必需性 | 用途 |
| --- | --- | --- |
| `inner_length` / `inner_width` / `inner_height` | 需求必填 | 尺寸范围 |
| `closure_type` | 需求必填 | 闭合方式（硬门槛） |
| `v_groove` | 需求必填 | V 槽 |
| `face_paper_gsm` | 需求必填 | 面纸克重 |
| `fit_clearance` | **需求选填** | 配合间隙（给了才是硬门槛） |

必需性口径唯一来自 `industry_templates`（第 2 批的 `SpecField(required=True)`），不得在
匹配引擎里另写一份必填清单。

### 2.2 维度闭集与权重一律读表

- 维度闭集 = `kb_packaging_match_weight.dimension` 的当前行，本批为
  `size_range` / `fit_clearance` / `face_paper_gsm` / `closure_type` / `v_groove`。
- 每个维度的 `weight` 与 `hard_gate` **必须从 `kb_packaging_match_weight` 读**
  （新增 `kb_repo.packaging_match_weights()`），不得在代码里写死数字，也不得复制一份常量表。
- 总分 `total_score = Σ(weight_i × score_i) / Σweight_i`（权重和不为 1 时按和归一化，
  结果始终落在 `[0, 1]`）。
- 演示权重为 0.30 / 0.25 / 0.15 / 0.20 / 0.10，硬门槛为 `fit_clearance` 与
  `closure_type`；这些是**待业务确认的演示值**，改业务口径只改表、不改代码。

### 2.3 每个维度的判分口径

统一约定：`score` ∈ `[0, 1]`，缺失输入不得计满分。

1. **size_range**（`hard_gate = 0`）
   - 需求 L/W/H 三轴与盒型 `size_l_min/max`、`size_w_min/max`、`size_h_min/max` 比。
   - 单轴：落在 `[min, max]` → `1.0`；超出 → `max(0, 1 - 超出量 / 区间宽度)`。
   - 该维得分 = 三轴得分的**最小值**；任一轴超出时给候选打 `out_of_range = true`。
2. **fit_clearance**（`hard_gate = 1`）
   - 需求没给 `fit_clearance` → 该维**既不计分也不淘汰**，计入 `missing_inputs`。
   - 给了 → `|需求间隙 - 盒型 fit_clearance| <= 0.5`（mm，常量
     `FIT_CLEARANCE_TOLERANCE_MM = 0.5`，口径来自权重表该行的 `rule_expr`）→ `1.0`；
     超差 → **淘汰**，`reject_reason = "fit_clearance_out_of_tolerance"`。
   - 盒型 `fit_clearance` 缺失或无法解析成数字 → 该维无法判定，候选标记
     `undecidable_dimensions` 含该维，不淘汰、不计分。
3. **face_paper_gsm**（`hard_gate = 0`）
   - 盒型 `face_paper_gsm` 形如 `"157-250"`（区间）或单值 `"200"`。
   - 需求克重落在区间内 → `1.0`；超出 → 与 size_range 同一衰减律
     `max(0, 1 - 超出量 / 区间宽度)`（对应权重表 `rule_expr` 的「取相邻档」）。
   - 盒型该值无法解析 → 该维 `0` 分并记 `reject_reason = "gsm_unparsable"`（非硬门槛，
     不建议淘汰，只在分项里如实体现）。
4. **closure_type**（`hard_gate = 1`）
   - 两侧都可能是多值：分隔符闭集 `["/", "+", "、", ",", "，", ";", "；"]`，切分后 trim、
     去空、去重。
     例：需求 `磁吸` vs 盒型 `磁吸/天地盖` → 交集 `{磁吸}` 非空 → 通过。
   - 交集非空 → `1.0`；为空 → **淘汰**，`reject_reason = "closure_type_mismatch"`。
   - 需求该项缺失（第 2 批必填）→ 见 2.4。
5. **v_groove**（`hard_gate = 0`）
   - 需求与盒型同值（`是`/`否`，允许 `Y/N`、`true/false` 归一）→ `1.0`。
   - 需求 `否`、盒型 `是` → `0.6`（权重表 `rule_expr` 明确）。
   - 需求 `是`、盒型 `否` → `0.0` + `reject_reason = "v_groove_required_but_unsupported"`
     （非硬门槛，不淘汰，但要能解释为什么排后面）。
   - 无法识别 → 该维 `0`，记 `undecidable_dimensions`。

### 2.4 缺输入不许装成匹配成功

- **缺失必填维度**（`closure_type` 缺，或 L/W/H 任一缺）→ 该候选
  `status = "needs_input"`，`can_confirm = false`，并把缺失键写进结果的
  `missing_inputs`。缺失维度**按 0 分计入**总分，绝不许按满分或"跳过该维"处理。
- **缺失选填维度**（`fit_clearance` 缺）→ 候选仍可为 `status = "matched"`、
  `can_confirm = true`，但必须出现在 `missing_inputs`，且结果标
  `inputs_complete = false`。
- `missing_inputs` 里**只允许出现**匹配真正需要的 7 个键（2.1 表），不得把整份需求单的
  所有空字段倒进去。

### 2.5 输出契约

```json
{
  "engine_version": "packaging_match_v1",
  "dimensions": [{"dimension": "size_range", "weight": 0.3, "hard_gate": false}],
  "inputs_complete": false,
  "missing_inputs": ["fit_clearance"],
  "candidates": [
    {
      "box_type_code": "YT-RB-01001-A",
      "name": "天地盖盒（全盖）",
      "family": "01天地盖",
      "status": "matched",
      "can_confirm": true,
      "total_score": 0.83,
      "dimension_scores": {"size_range": 1.0, "closure_type": 1.0},
      "out_of_range": false,
      "reject_reasons": [],
      "undecidable_dimensions": [],
      "applicable_industries": "化妆品/数码/茶叶",
      "business_status": "标准"
    }
  ],
  "suggested_box_type": "YT-RB-01001-A",
  "needs_new_tooling": false
}
```

- 排序键固定为：`status`（`matched` → `needs_input` → `rejected`）→ `out_of_range`
  （False 在前）→ `total_score` 降序 → `box_type_code` 升序。**同样输入必须给出同样顺序。**
- 只要存在任一「尺寸完全在区间内」的候选，`out_of_range` 的候选就不得排在第一位。
- 全部候选都被淘汰、或没有任何候选时：`needs_new_tooling = true`，并给出
  `new_tooling_reason`（`all_rejected` / `no_box_type` / `missing_required_input`）。
- `applicable_industries` / `business_status` 本批**不作为评分维度**（维度闭集由表决定），
  但要原样带出供工艺经理参考。
- 匹配引擎是**纯函数、确定性、不调模型、不联网**：同样输入重复调用结果逐字相同。

## 3. 落库与人工决策

### 3.1 新表（`da_schema.sql`）

```sql
CREATE TABLE IF NOT EXISTS wip_packaging_box_match (
    project_id          TEXT NOT NULL,
    requirement_no      TEXT NOT NULL DEFAULT '',
    industry            TEXT NOT NULL DEFAULT 'packaging',
    engine_version      TEXT NOT NULL,
    matched_at          TEXT NOT NULL,
    inputs_json         TEXT,   -- 匹配时 2.1 七个键的取值快照，用于 stale 判定
    candidates_json     TEXT,   -- 全部候选（含分项分与淘汰原因）
    missing_inputs_json TEXT,
    suggested_box_type  TEXT,
    decision            TEXT NOT NULL DEFAULT 'pending'
                        CHECK (decision IN ('pending','confirmed','returned','new_tooling')),
    confirmed_box_type  TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    note                TEXT,
    updated_at          TEXT,
    PRIMARY KEY (project_id, requirement_no)
);

CREATE TABLE IF NOT EXISTS wip_packaging_box_match_audit (
    audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT NOT NULL,
    requirement_no TEXT NOT NULL DEFAULT '',
    action         TEXT NOT NULL,   -- matched / confirmed / switched / returned / new_tooling
    box_type_code  TEXT,
    actor          TEXT,
    at             TEXT NOT NULL,
    detail_json    TEXT
);
```

### 3.2 决策四态

| 决策 | `decision` | 前置校验 |
| --- | --- | --- |
| 确认推荐 | `confirmed` | 该盒型必须是 `status = "matched"` 且 `can_confirm = true` 的候选 |
| 换成别的候选 | `confirmed` | 同上（`confirmed_box_type` 与被换的盒型都要进审计，`action = "switched"`） |
| 退回补充需求 | `returned` | 必须列出缺哪些键（`missing_inputs` 非空） |
| 新制评估 | `new_tooling` | 允许在没有可用候选时使用 |

- 对 `rejected` / `needs_input` 候选做「确认」→ 必须被拒（`409`），并说明为什么
  （`box_type_not_confirmable` + 该候选的 `reject_reasons`）。
- `needs_new_tooling = true` 时，决策落库为 `new_tooling`，并返回可建卡的描述
  `new_tooling_task`（含 `industry="packaging"`、来源 `project_id` / `requirement_no`、
  盒型族与原因）；**不在本批真实建工作流卡**（见 §5）。
- 重复提交**同一个**决策 → 幂等：不新增审计行以外的副作用，`confirmed_at` 保持首次值。

### 3.3 确认保护与失效

- 重新匹配（再次跑 `match_box_types` 并落库）只更新 `candidates_json` / `inputs_json` /
  `missing_inputs_json` / `matched_at`，**不得覆盖** `confirmed_box_type` /
  `decision` / `confirmed_by` / `confirmed_at`。
- 重新解析需求（第 2 批的 AI 抽取再跑一遍）同样不得覆盖人工确认的盒型。
- 关键匹配输入（2.1 的七个键）与 `inputs_json` 快照不一致 → 读回时带
  `stale = true` 与 `stale_reasons = ["inner_length", ...]`；确认值本身不动，由工艺经理
  重新确认。

### 3.4 审计只增不改

- `wip_packaging_box_match_audit` 只允许 INSERT；仓库层**不提供**更新/删除该表的函数。
- 每次决策写一行：`action`、`box_type_code`、`actor`、`at`、`detail_json`（含当时的分数与
  为什么选它）。
- 审计按 `audit_id` 升序读回，历史不被后续决策改写。

## 4. 接口

新增（沿用既有 `/api/projects/{project_id}/requirement/...` 形状）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/projects/{project_id}/requirement/box-match` | 跑匹配并落库（`pending`；已有确认时只更新候选与输入快照） |
| `GET` | `/api/projects/{project_id}/requirement/box-match` | 读当前记录（含 `stale` / `stale_reasons` / 审计入口） |
| `POST` | `/api/projects/{project_id}/requirement/box-match/decision` | 决策：`confirmed` / `returned` / `new_tooling` |

- 三个接口都只对 `industry = "packaging"` 的需求单生效；其它行业调用 → `400`。
- 决策角色：`process_manager` / `process_director` / `admin`（常量 `BOX_MATCH_DECIDE_ROLES`
  定义在 `packaging_match` 服务模块里，`main.py` 的接口直接引用它，不得再抄一份）；
  其它已登录角色只读，决策 → `403`。
- 前端：`requirement-confirm.html` / `requirement-confirm.js`（1.2 需求确认页，工艺经理主责）
  增加盒型匹配面板：候选列表 + 总分 + 分项分 + 淘汰原因 + 四个决策按钮 + `stale` 提示；
  缓存戳按既有约定递增（`requirement-confirm.js?v=...`）。

### 4.5 命名契约（红测与实现共用，不得改名）

新增模块 `tech_app/backend/services/packaging_match.py`：

| 名称 | 说明 |
| --- | --- |
| `ENGINE_VERSION = "packaging_match_v1"` | 引擎版本，写进落库记录 |
| `MATCH_INPUT_KEYS` | §2.1 的 7 个键，顺序与表一致 |
| `FIT_CLEARANCE_TOLERANCE_MM = 0.5` | 配合间隙容差常量 |
| `match_box_types(inputs: dict) -> dict` | 纯函数匹配，不读库、不落库、不调模型 |
| `run_box_match(project_id, requirement_no="") -> dict` | 读需求单字段 → 匹配 → 落库（`pending`） |
| `load_box_match(project_id, requirement_no="") -> dict` | 读回记录 + `stale` / `stale_reasons` |
| `decide_box_match(project_id, requirement_no, decision, box_type_code=None, *, actor=None, note="") -> dict` | 四态决策 |
| `BoxMatchError(message, status_code=409)` | 业务错误，供接口映射状态码 |
| `BOX_MATCH_DECIDE_ROLES` | `frozenset({"process_manager", "process_director", "admin"})`，接口引用同一个 |

`da_repo` 新增：

| 名称 | 说明 |
| --- | --- |
| `save_box_match(record: dict) -> None` | 按 (`project_id`, `requirement_no`) 幂等 upsert |
| `load_box_match(project_id, requirement_no="") -> Optional[dict]` | 读单条 |
| `update_box_match_decision(project_id, requirement_no, **fields) -> None` | 只更新决策列 |
| `append_box_match_audit(project_id, requirement_no, action, box_type_code=None, actor=None, detail=None) -> int` | 只 INSERT |
| `box_match_audit(project_id, requirement_no="") -> list[dict]` | 按 `audit_id` 升序 |

`kb_repo` 新增：`packaging_match_weights() -> list[dict]`（按 `dimension` 升序）。

需求单读取沿用既有 `store.load_requirement(project_id)`，不得另写一套需求读取。

除此之外，每次决策还必须调用一次既有的 `store.audit(project_id,
"workflow:packaging_box_match_<decision>", {...})`，让项目时间线能看到这次人工决策
（`detail` 至少含 `box_type_code`、`actor`、`score`）。

## 5. 非目标

- 不展开部件与 BOM（第 5 批）、不生成工艺路线（第 6 批）、不算成本或价格（第 7、8 批）。
- 不改 5 维之外的匹配口径（`applicable_industries` / `business_status` 只带出不评分）。
- 不新增工作流任务卡、不写 `cpq_wf`、不写 Postgres：本批只落本地 SQLite 的
  `wip_packaging_box_match*`，真实建卡与闭环留给第 8 批。
- 不调大模型、不联网；匹配是确定性纯函数。
- 不改第 2 批的包装需求字段定义与必填口径。

## 6. 历史兼容

- 两张新表都是新增表，不动既有表结构；老库升级只 `CREATE TABLE IF NOT EXISTS`。
- 没有 `wip_packaging_box_match` 行的项目：`GET` 返回 `decision = "none"` / `candidates = []`，
  不报错。
- 三个原行业（半导体/电池/电器）不产生也不消费该表的数据。

## 7. 可自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_box_type_matching_red
```

红测分组：A 权重与维度读表 / B 五维判分与边界 / C 硬门槛淘汰 / D 缺输入与不撒谎 /
E 排序与确定性 / F 落库与四态决策 / G 确认保护与 stale / H 审计只增不改 /
I 接口与角色门禁 / J 非回归。

## 8. 人工验收

1.2 需求确认页选择包装需求 → 盒型匹配面板出现候选与分项分 → 确认一个盒型 → 刷新后仍是
该盒型 → 改 3.2 尺寸后再看，出现 `stale` 提示但确认值未被改掉。

## 9. 不允许减少的既有能力

三行业需求确认页的既有行为与字段；第 2 批包装需求模板的 64 个字段与 10 个必填；
第 3 批知识库的行业隔离与幂等；`kb_repo` 既有读函数签名与默认不过滤行为；
1.2 需求确认页既有的确认（submit/confirm/return-to-draft）流程与角色门禁。
