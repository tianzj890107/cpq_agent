# 规格：逆向快速报价 第 12 批 —— 案例库的维护写路径（补字段 / 审核 / 停用）与面板动作接线

状态：Spec + 红测（**未实现**）
红测：`tests/test_quick_quote_case_maintenance_red.py`
依赖：批 1（案例模型与准入 `case_eligible()`）、批 6（`library_readiness()` / `case_fix_plan()`）、
批 8（`cpq_quick_quote_price.WRITE_ROLES` 写权限闭集）。
前序证据：2026-09-21 现场实测（`changelog ## 248` / `## 249`）——案例库 2 条 `draft`、缺
「标准单价」→ `eligible_total = 0`；页面把补齐路径写在 `readiness.next_actions` 上，**点下去
什么也不发生**。

## 0. 一句话目标

把「案例库有资产但 0 条可用」从**只说话**变成**能动手**：销售 / 工艺能在产品里（不是 SSH 跑
运维脚本）补齐缺的字段、审到「已审核」，资格判定立刻回流到页面；每一步留痕（谁、什么时候、
把哪个字段从什么改成什么、为什么）。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| `cpq_agent_server.py` | 只有 `GET /api/quick-quote/cases`（只读）。没有任何写路由能改案例字段或审核状态 |
| `cpq_quick_quote_case.py` | `save_case()` 是唯一写入入口（整行覆盖）；没有「局部补字段」「审核状态流转」的纯函数，也没有审计条目 |
| `tech_app/frontend/quick-quote-panel.js` | `renderReadiness()` 按 `next_actions` 画按钮（`data-qq-action="fill_case_fields"` / `"review_case"`），但 `open()` 只有 `onPrecise` 一条外部回调，**没有 `onAction`** |
| `报价首页.html` | `openQuickQuotePanel()` 只传 `onPrecise` → 上面两个按钮是**死按钮**（点了不发任何请求） |
| 现状替代路径 | 只能靠 `scripts/import_dwg_quick_quote_cases.py --price … --review-status reviewed --confirm`（运维脚本），不是产品内路径 |

结论：**「谁批准这条案例可以拿来出价」在产品里没有落点**。案例能不能用于报价本质是一次业务
审批，不该等价于"某人记得去服务器上跑一条命令"。

## 2. 契约

### 2.1 新增常量（`cpq_quick_quote_case.py`）

```python
#: 案例维护的错误码闭集（纯函数与接口共用，不许各写一份字符串）。
CASE_MAINTENANCE_ERRORS = ("unknown_field", "immutable_field", "invalid_value",
                           "illegal_transition", "reason_required", "case_not_found")
#: 可改字段 = CASE_FIELDS 去掉身份列与版本（身份列只读）。
CASE_IMMUTABLE_FIELDS = ("case_code", "case_version", "version")
CASE_EDITABLE_FIELDS = tuple(k for k in CASE_FIELDS if k not in CASE_IMMUTABLE_FIELDS)
#: 审核状态机（键集必须等于 CASE_REVIEW_STATUSES）。
CASE_TRANSITIONS = {"draft": ("reviewed", "retired"),
                    "reviewed": ("draft", "retired"),
                    "retired": ()}
#: 需要写原因的目标状态（退回草稿、停用都要理由）。
CASE_REASON_REQUIRED = ("draft", "retired")
#: 两条写路由的路径模板（唯一事实源；服务端不许另写一份字面量）。
QUICK_QUOTE_CASE_FIELDS_PATH = "/api/quick-quote/cases/{case_code}/fields"
QUICK_QUOTE_CASE_REVIEW_PATH = "/api/quick-quote/cases/{case_code}/review"
```

### 2.2 `case_edit_patch(case, values, *, today=None) -> dict`

纯函数，只算「要写什么」，**不碰库**：

```python
{"patch": {"standard_price": 23.4},                       # 只含真正变化的键
 "changed": [{"field": "standard_price", "before": 0, "after": 23.4,
              "label": "标准单价"}],
 "blocked": [{"field": "case_code", "reason_code": "immutable_field",
              "label": "案例编号", "detail": "案例编号不允许修改"}]}
```

规则：

1. 键不在 `CASE_EDITABLE_FIELDS` 且不是身份列 → `blocked.unknown_field`；
2. 键在 `CASE_IMMUTABLE_FIELDS` → `blocked.immutable_field`（与"未知字段"分开报）；
3. 值域校验失败 → `blocked.invalid_value`：
   - 金额列（`standard_cost` / `standard_price` / `deal_price`）>= 0，且小数位不超过 4；
   - 尺寸 / 克重（`inner_*` / `grey_board_gsm` / `face_paper_gsm`）> 0；`fit_clearance` >= 0；
   - `currency`：三位大写字母（`CNY` / `USD`）；
   - `quote_date` / `valid_from` / `valid_until`：ISO `YYYY-MM-DD`；同批里若同时给出
     `valid_from` 与 `valid_until`，必须 `valid_from <= valid_until`；
   - 布尔列（`magnet` / `ribbon` / `window` / `v_groove` / `lamination` / `hot_stamping` /
     `tax_included`）：只收 `bool` / `0` / `1`；
   - `industry` 只收非空字符串；`source_type` 只收 `CASE_SOURCE_TYPES` 闭集内取值；
4. 新值与现值相等 → 既不进 `patch` 也不进 `changed`（**幂等**：同一批重放不产生新变更）；
5. `patch` 键顺序 = `CASE_FIELDS` 顺序（确定性输出，便于审计 diff）；
6. `values` 为 `None` / 空 dict → 三块都为空，不抛异常；
7. **不因单字段非法而丢弃整批**：合法字段照样进 `patch`，非法字段只进 `blocked`；
   调用方据此决定是否落库（见 §2.4 第 5 条）。

### 2.3 `case_review_patch(case, status, *, reviewer, reason="", today=None) -> dict`

纯函数，状态机 + 留痕（`review_reason` 只在非空时写）：

```python
{"patch": {"review_status": "reviewed", "reviewed_by": "PE1",
           "reviewed_at": "2026-09-21", "review_reason": ""},
 "changed": [{"field": "review_status", "before": "draft", "after": "reviewed",
              "label": "审核状态"}],
 "blocked": []}
```

规则：

1. 目标状态不在 `CASE_REVIEW_STATUSES` → `blocked.invalid_value`；
2. 目标状态不在 `CASE_TRANSITIONS[当前状态]` → `blocked.illegal_transition`
   （`retired` 是终态：`retired → *` 一律非法）；
3. 目标状态在 `CASE_REASON_REQUIRED` 且 `reason` 为空 / 纯空白 → `blocked.reason_required`；
4. `reviewer` 为空 / 纯空白 → `blocked.invalid_value`，`detail` 必须是「审核人必填」；
5. 目标状态与当前状态相同 → 空 `patch`、空 `changed`、空 `blocked`（幂等）；
6. `today` 缺省 `dt.date.today()`；`reviewed_at` 写 ISO 日期字符串。

### 2.4 两条写路由（`cpq_agent_server.py`）

- `POST /api/quick-quote/cases/{case_code}/fields`，body `{"values": {...}}`；
- `POST /api/quick-quote/cases/{case_code}/review`，body `{"status": …, "reason": …}`；
- 路径**模板**必须取自 `cpq_quick_quote_case.QUICK_QUOTE_CASE_FIELDS_PATH` /
  `QUICK_QUOTE_CASE_REVIEW_PATH`；服务端匹配用「模板 → 正则」生成，不许另写一份路径字面量；
- 成功出参：`{"ok": true, "case": {…更新后的案例…}, "changed": [...], "blocked": [],
  "readiness": {…与 GET /api/quick-quote/cases 同源的 library_readiness()…}}`；
- 失败出参：`{"ok": false, "code": …, "error": …}`；HTTP 码：`404` `case_not_found`、
  `403` 角色不够、`400` 其余（`code` 取 `blocked` 第一条 `reason_code`）；
- **先校验后写**：`blocked` 非空时不得有任何落库动作；
- 写权限复用既有闭集 `cpq_quick_quote_price.WRITE_ROLES`（**不新造闭集**）；判定函数放在
  `cpq_quick_quote_case` 里（`case_write_allowed(user) -> bool`），路由只调用它，
  不许在路由里重写角色判断。

### 2.5 前端接线（`tech_app/frontend/quick-quote-panel.js` + `报价首页.html`）

- 面板新增模块常量 `CASE_FIELDS_PATH` / `CASE_REVIEW_PATH`，值与 `cpq_quick_quote_case`
  的两个模板同值（不许再写第二份字面量）；
- `open()` 支持 `onAction(action, row)`；面板自己认这两个动作：
  `fill_case_fields` → 调 `options.onCaseFill(case)`；`review_case` → 调
  `options.onCaseReview(case)`；
- 两个回调都没给时，按钮必须带 `data-qq-action-pending="1"` —— 让"还没接线"在 DOM 上
  可见，而不是静默无反应；
- `报价首页.html` 的 `openQuickQuotePanel()` 必须传 `onAction`（或 `onCaseFill` /
  `onCaseReview`），并在动作完成后刷新案例表与 readiness 文案。

### 2.6 资格回流（唯一验收口径）

用 §2.2 + §2.3 把 fixture 里那两条 `draft` 案例（`QQ-YT-DWG-WINE-700ML` /
`QQ-YT-DWG-ROUND-10PC`）各补一次（写 `standard_price`、审到 `reviewed`）之后：

- `library_readiness(cases)["eligible_total"]` 必须由 `0` 变 `2`、`verdict` 由 `no_eligible`
  变 `ready`，`blocked_by` 为空；
- 两条案例的 `changed` 里都必须同时出现 `standard_price` 与 `review_status`。

## 3. 红测映射（`tests/test_quick_quote_case_maintenance_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：错误码闭集、不可变列、状态机键集与 `CASE_REVIEW_STATUSES` 一致、两条路径模板 |
| B | `case_edit_patch()`：未知字段、身份列、各值域分支、合法与非法混批、等值幂等、键序确定性、空输入 |
| C | `case_review_patch()`：六种流转、终态、原因必填、审核人必填、同状态幂等、`reviewed_at` 注入 |
| D | 回流：两条真实案例补价 + 审核 → `verdict=ready`、`eligible_total=2`、`blocked_by` 空 |
| E | 服务端：两条路由与路径模板同值、先校验后写、权限复用 `WRITE_ROLES`、成功 / 失败出参键 |
| F | 前端：两条动作各有处理分支、无回调时标 `data-qq-action-pending`、首页传 `onAction`、常量同值 |

## 4. 非目标

- 不替业务定**价格数值**（本批只解决"能不能填、填了算不算数"）；
- 不改准入判据（批 1 `case_eligible()`、批 6 `library_readiness()` 口径一律不动）；
- 不改 `save_case()` 的整行写入语义（本批新增的是**局部更新**路径）；
- 不做多级审批流 / 案例版本分支（留给后续批次）；
- 不碰 `cpq_wf` 既有行：红测全部离线（`mock` 掉库，不连 PG、不写业务数据）。
