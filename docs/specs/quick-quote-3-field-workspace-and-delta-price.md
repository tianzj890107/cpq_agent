# 规格：逆向快速报价 第 3 批 —— 字段工作区修改与差异价格计算

状态：Spec + 红测（**未实现**）
红测：`tests/test_quick_quote_field_workspace_red.py`
依赖：批 1（案例模型/来源/有效期）、批 2（候选检索与 `build_baseline()` 基准案例快照）。
**本批假设批 1、批 2 已实现**。

## 0. 本批范围

本批定「销售怎么改那几个差异项、每改一项贵多少钱」。核心是**交互归属**：

- **左侧 Agent** = 自然语言入口：理解需求、解释候选、接受「把数量改成 3000」这类口语修改；
- **右侧工作区** = 权威编辑与确认入口：展示、编辑、校验、确认、保存结构化参数。

不是二选一，而是分工：Agent 的修改进**待确认（pending）**，只有右侧确认后才成为当前值。
原因（业务拍板）：尺寸/数量/材料必须一眼可见；要能对比原案例值和新报价值；
避免 Agent 误解「改成 250」到底是克重、数量还是尺寸；保存与审计更可靠；
下拉框、单位校验、范围校验只能在结构化字段上做。

**本批不出最终报价**：只算「每项的差异价」与差异合计（批 4 才组装快速报价、门槛与转精准）。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| 仓库根 | `cpq_quick_quote_workspace.py` 不存在 |
| `cpq_kb.py:68` | 没有 `kb_quick_quote_delta_rule` —— 「面纸 +50g 贵多少」这类口径无处可查 |
| `确认需求解析结果.html` | 右侧工作台只有既有 6 步的分区表（`render_form` / `render_table`），**没有任何「基准案例 vs 当前报价 vs 差异价格」三列对比表** |
| `tech_app/frontend/quick-quote-panel.js` | 批 1 新建（模式入口），目前不含字段工作区渲染 |
| `cpq_agent_server.py` | Agent 没有任何「写工作区字段」的确定性接口；自然语言改字段没有落点 |
| 全仓 | 没有「未确认修改不得落库」的约束，也没有逐项修改审计 |

## 2. 契约

### 2.1 新模块 `cpq_quick_quote_workspace.py`

```python
ENGINE_VERSION = "quick_quote_workspace_v1"
INDUSTRY = "packaging"

#: 可编辑字段闭集（顺序即工作区展示顺序）。每个字段必须能在批 1 的案例模型里找到同名字段。
FIELD_KEYS = ("box_type", "box_family", "closure_type", "insert_type",
              "inner_length", "inner_width", "inner_height", "fit_clearance",
              "grey_board_gsm", "face_paper_gsm", "material_code",
              "print_colors", "lamination", "hot_stamping", "v_groove", "magnet",
              "window", "ribbon", "quantity", "tooling_fee_amount", "freight_amount")

#: 字段规格（唯一事实源）：右侧工作区靠它渲染控件、做单位与范围校验。
FIELD_SPECS = {key: {"label": str, "group": str, "value_type": "num|bool|text|enum",
                     "unit": str, "min": float|None, "max": float|None,
                     "choices": tuple} …}
FIELD_GROUPS = ("尺寸", "材料", "结构", "表面工艺", "印刷", "数量", "费用")

#: 关键字段的范围/枚举口径（右侧工作区控件与后端校验共用同一份；完整表见实现）。
#: quantity 1–1,000,000 个；inner_* 20–2000 mm；fit_clearance 0–20 mm（配合间隙量级）；
#: face_paper_gsm 60–400 g/m²；grey_board_gsm 400–3000 g/m²；
#: tooling_fee_amount / freight_amount 0–1,000,000 元；
#: print_colors 枚举 ("", "CMYK", "专色", "CMYK+专色")，写入前先把 4C / 四色 归一为 CMYK。

#: 差异价规则表（知识库侧，cpq_kb schema；批 1 已引入 kb_quick_quote_config）。
DELTA_RULE_TABLE = "kb_quick_quote_delta_rule"
DELTA_RULE_KEYS = ("rule_code",)
RULE_KINDS = ("rate", "step", "band", "direct")
EDIT_SOURCES = ("workspace", "agent")
#: 落库角色复用既有闭集，不得新造（Spec §2.4）。
WRITE_ROLES = cpq_packaging_quote.WRITE_ROLES

class WorkspaceError(Exception):
    """带用户可见文案的业务错误；字段级错误放 `.field_errors`。"""

def load_rules(rules=None) -> list
def normalize_value(field_key, value)      # 纯函数，脏值归一
def validate_edits(edits) -> list          # [(field, reason)]，空列表 = 全部合法
def delta_price(field_key, base_value, current_value, *, rules=None,
                base_unit_price=None, base_quantity=None, today=None) -> dict
def new_workspace(baseline, *, user=None) -> dict
def apply_edits(workspace, edits, *, source="workspace", user=None, rules=None) -> dict
def pending_fields(workspace) -> dict
def confirm(workspace, *, user=None, fields=None, rules=None) -> dict
def diff_table(workspace, *, rules=None) -> list
def diff_total(workspace, *, rules=None) -> dict
def agent_patch(workspace, text, *, propose=None, user=None) -> dict
def save(workspace, *, user=None, session_id="") -> dict
def load(session_id) -> dict
```

### 2.2 差异价规则表 `kb_quick_quote_delta_rule`

行形状（与 `kb_packaging_*` 同源：来源分层 + 审核状态 + 版本 + 行业）：

```python
{"rule_code": "QQQ-PAPER-RATE", "field_key": "face_paper_gsm",
 "rule_kind": "rate|step|band|direct", "unit": "元/g/m²",
 "rate": 0.0062,          # rate 用
 "amount": 0.18,          # step / direct 用
 "step_size": 1.0,        # step 用（默认 1.0）
 "breakpoints_json": "[[0,1.15],[1000,1.00],[3000,0.92],[10000,0.85]]",  # band 用
 "industry": "packaging", "source_type": "workbook", "review_status": "reviewed",
 "version": 1, "effective_from": "2026-01-01", "effective_to": ""}
```

计算口径（`delta_price()`，全部确定性）：

| `rule_kind` | 差异价 | 说明 |
| --- | --- | --- |
| `rate` | `delta = (current - base) × rate` | 克重、尺寸这类线性项 |
| `step` | `steps = ceil(|current - base| / step_size)`；`delta = sign(current - base) × steps × amount` | 烫金 / 覆膜 / 内托这类开关或档位；`step_size` 默认 1.0 |
| `band` | `delta = base_unit_price × (factor(current) - factor(base))` | 数量档；`base_unit_price` 来自批 2 的基准快照，`factor` 取「**≥ 该值的最小**断点倍率」（见 §5 回写第 1 条） |
| `direct` | `delta = current - base` | 模具/版费、运输这类绝对值加减项 |

要求：

1. **规则一律读表**：`load_rules(rules=None)` 显式传入只用传入行，`None` → 读
   `DELTA_RULE_TABLE`；读不到或表为空抛 `cpq_quick_quote_case.CaseLibraryUnavailable`
   （复用批 1 异常类）。代码里不得出现金额/费率常量（`DEFAULT_RULES` 只允许作为
   权重表种子的同形数据，且不得被打分逻辑直接引用）。
2. **没有规则的字段不编价格**：`delta_price()` 返回 `priced=False`、`delta=0.0`、`note` 说明
   「该字段不单独计差，仅影响相似度与风险提示」（例如 `box_type`、`closure_type`）。
3. 规则有 `effective_from` / `effective_to` 时按注入的 `today` 生效（与批 1 有效期同口径）；
   过期规则不参与计算并计入 `note`，**不静默按老规则算钱**。
4. 同一字段命中多条规则 → 抛 `WorkspaceError`（口径冲突必须人工解决，不能任选一条）。

### 2.3 工作区状态机

```python
workspace = {
  "engine_version": "quick_quote_workspace_v1",
  "baseline": {… 批 2 `build_baseline()` 的返回 …},
  "base_values": {field: value},      # 基准案例值（只读，永不修改）
  "current": {field: value},          # 已确认的当前值（初始 = base_values）
  "pending": {field: value},          # Agent 建议、尚未确认
  "edits": [{"field", "base", "current", "source", "user_id", "at", "confirmed"}…],
  "confirmed": bool,                  # 是否有未确认的 pending
  "created_at": str, "updated_at": str,
  "user": {"user_id", "username", "role_code"},
}
```

API 语义：

- `new_workspace(baseline, user=None)`：`current = base_values = 案例基准值`，`pending = {}`。
  `base_values` 取 `baseline["base_values"]`；没有时从 `baseline["case_snapshot"]` 里按
  `FIELD_KEYS` 取（显式传入优先），取不到的字段进 `missing_base_fields`，**不猜 0**。
- `apply_edits(workspace, edits, source="workspace", user=None)`：
  - `source="workspace"`（右侧直接改）→ 直接进 `current`；
  - `source="agent"`（Agent 建议）→ 进 `pending`，`confirmed` 置 `False`；
  - 逐条规范化 + 校验（未知字段、越界、非法枚举一律抛 `WorkspaceError` 并带
    `field_errors=[{"field","reason"}]`，**不做部分写入**）；
  - 值等于基准值 → 记一条「还原」编辑并从 `pending` 移除；
  - 纯函数：返回新 workspace，不改入参。
- `confirm(workspace, user=None, fields=None)`：把 `pending`（或点名 `fields`）合并进
  `current` 并清空对应 pending；`user` 必填；`confirmed` 置 `True`。
- `diff_table(workspace, rules=None)`：只输出**有差异的字段**（基准值 ≠ 当前值，含 pending），
  行结构：

```python
{"field_key", "label", "group", "unit",
 "base_value", "current_value", "display_base", "display_current",
 "delta", "delta_text",                          # 例 "+0.27 元" / "-0.31 元"
 "priced", "pending", "rule_code", "rule_version", "formula", "note"}
```

- 展示口径：布尔字段显示「有 / 无」，数值带单位（`200 g/m²` / `200 mm` / `3000 个`），
  `delta_text` 固定「符号 + 两位小数 + 空格 + 单位」（`"+0.27 元"`、`"-0.31 元"`），
  差异为 0 的行不出现在表里。
- `diff_total(workspace, rules=None)`：

```python
{"base_unit_price", "currency", "tax_included",
 "confirmed_delta_total",     # 只看 current
 "preview_delta_total",       # current + pending
 "confirmed_unit_price",      # base_unit_price + confirmed_delta_total
 "preview_unit_price",        # base_unit_price + preview_delta_total
 "items": [diff 行 …],
 "rule_versions": [str …]}
```

### 2.4 自然语言入口 `agent_patch()`

```python
def agent_patch(workspace, text, *, propose=None, user=None) -> dict
```

返回：

```python
{"engine_version", "text",
 "proposed_edits": {field: value},        # 解析成功的建议（尚未确认）
 "unresolved": [{"text", "reason", "candidates": [field …]}],
 "requires_confirmation": True,
 "warnings": [str …],
 "workspace": {… 已把建议写入 pending 的新工作区 …}}
```

要求：

1. `propose` 显式传入时用它解析（离线/联调注入，签名为 `propose(text, workspace) -> dict`）；
   `None` 时用内置确定性解析：识别「把 X 改成 Y」「X 改成 Y」「再增加 X」「去掉 X」，
   字段别名表至少覆盖：数量 / 面纸（面纸克重）/ 灰板 / 内长 / 内宽 / 内高 / 烫金 / 覆膜 /
   V 槽 / 磁铁 / 开窗 / 内托 / 模具费 / 运输。
2. **歧义不猜**：句子里没有字段名（如「改成 250」）→ 不产生任何 `proposed_edits`，
   进 `unresolved` 并给出候选字段清单；单位不合法（如「面纸改成 250 个」）同样不写入。
3. **不直接出报价**：`agent_patch()` 只改 pending，不调用定价模块、不写 `current`、
   不落库；返回里不得出现任何价格/金额（除 `proposed_edits` 里的原始字段值）。
4. Agent 建议必须能被右侧工作区看见并逐项确认：`pending_fields(workspace)` 返回
   当前待确认字段字典（供 UI 标黄显示）。
5. `user` 缺省时沿用工作区创建者（`workspace["user"]`），**不产生匿名修改记录**。

### 2.5 落库与恢复（复用既有卡片快照，不新建表）

```python
def save(workspace, *, user=None, session_id="") -> dict
def load(session_id) -> dict
```

- `save()` 需要 `user` 且角色 ∈ `WRITE_ROLES`（`sales_mgr` / `admin`；复用
  `cpq_packaging_quote.WRITE_ROLES`，不得新造角色闭集）。
- **未确认不得落库**：有 `pending` 时 `save()` 抛 `WorkspaceError`
  （文案点名「先在右侧确认修改」）。
- 落库走既有报价卡片快照：`cpq_wf.merge_step_snapshot(session_id, 2, {"quick_quote": …})`，
  `load()` 用 `cpq_wf.step_snapshot(session_id, 2)` 读回 `quick_quote` 段；**不新建表**。
- 每次修改都要能在 `edits` 里查到：字段、基准值、新值、来源（workspace / agent）、
  操作人、时间。

### 2.6 右侧工作区渲染（`确认需求解析结果.html` + `quick-quote-panel.js`）

- 工作台页面必须有容器 `id="quickQuoteWorkspace"`，并在包装快速报价模式下加载
  `tech_app/frontend/quick-quote-panel.js`。
- `quick-quote-panel.js` 必须提供 `QuickQuotePanel.renderDiffTable(rows)`，渲染四列：
  「参数」「基准案例」「当前报价」「差异价格」；pending 行必须有可见标记（如 `pending` 类名）。
- 表格数据**只能**来自 `diff_table()` 的输出结构（字段名 `field_key` / `display_base` /
  `display_current` / `delta_text` / `pending`），不得在前端重算价格。

## 3. 红测映射（`tests/test_quick_quote_field_workspace_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：字段闭集与规格、规则表名、规则种类、复用批 1 异常类与既有角色闭集 |
| B | 差异价规则：四种 rule_kind 的精确数值、无规则字段不编价、规则冲突抛错、规则读表 |
| C | 工作区状态机：agent 进 pending / workspace 直接进 current、未确认不得落库、确认合并、审计、纯函数 |
| D | 校验与展示：越界/非法枚举抛错且带 `field_errors`、脏值归一、`diff_table` 只列差异行、`delta_text` 格式、用户示例三项数值 |
| E | 自然语言入口：字段名解析、歧义「改成 250」不猜、不产生任何价格字段 |
| F | 落库与恢复：角色校验、`cpq_wf` 快照读写、未确认拒绝 |
| G | 前端：四列表头、`renderDiffTable`、pending 标记、只消费后端结构 |

## 4. 本批非目标

- 不出最终快速报价、不算价格区间与偏差、不做门槛与转精准报价（批 4）；
- 不改 `cpq_packaging_quote.py` 的定价口径（批 4 才复用它的 `WRITE_ROLES`）;
- 不做文件解析（批 5）。

## 5. 实现期回写（2026-09-21，实现时实测发现，实现按红测落地）

实现方在落 `cpq_quick_quote_workspace.py` 时发现 Spec 原文有三处与红测/业务冲突，
按「红测是唯一验收标准、Spec 同步更正」处理，三处均已在正文改好：

1. **`band` 的 `factor` 取「≥ 数量的最小断点倍率」**，不是原文的「≤ 该值的最大断点倍率」。
   以红测注入的 `QTY-BAND = [[0,1.15],[1000,1.00],[3000,0.95],[6000,0.92],[10000,0.85]]`
   为例：`5000` 落在 3000–6000 档 → 取 **6000 的 0.92**；`3000` → 0.95；
   `delta = 9.0 × (0.95 - 0.92) = +0.27`（红测 B4/D7 逐字钉住的业务示例）。
   业务上也只可能这样——数量越少单价越高；按原文「≤」会让 5000 与 3000 同档、差异价为 0。
2. **`fit_clearance` 范围 0–20 mm**，不是与 `inner_*` 并列的 20–2000 mm。
   配合间隙是「内尺寸与成品之间留多少」的量级（批 3/4/5 的夹具一律是 `1.5`）；
   照原文的 `min=20` 会让所有真实值在 `apply_edits()` 里被判越界，工作区根本建不起来。
   `inner_*` 仍为 20–2000 mm，未动。
3. **`insert_type` 是文本字段，不受枚举约束**：工作簿里内托写法不齐
   （`EVA内托` / `植绒内托` / `无内托` / 空），下游硬筛选正是把「空」当缺输入；
   设成 `choices=("EVA内托","植绒内托","无内托")` 会让空串非法、把「没填」误判成「填错」。
   本批只有 `print_colors` 是枚举字段，且**空串合法**（Spec §2.1 原文已含 `""`）。

多处试算仍以红测为准（`tests/test_quick_quote_field_workspace_red.py`，53 条）。
另：`tests/test_quick_quote_field_workspace_red.py::test_c5_confirm_merges_pending` 末尾
「不得改入参」那条断言的期望字面量原本只写了 `quantity`，与同一文件的 D8（Agent 的
`hot_stamping` 修改也必须留在 pending）自相矛盾、无论怎么实现都不可能同时为真；
实现期按 Spec §2.3 原意补齐为**两条待确认项**，见 changelog 实现条目。
