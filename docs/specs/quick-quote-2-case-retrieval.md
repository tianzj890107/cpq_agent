# 规格：逆向快速报价 第 2 批 —— 相似案例检索与候选选择

状态：Spec + 红测（**未实现**）
红测：`tests/test_quick_quote_case_retrieval_red.py`
依赖：批 1（`docs/specs/quick-quote-1-mode-and-case-model.md`，案例模型 + 来源/审核/有效期准入）。
**本批假设批 1 已实现**（`cpq_quick_quote_case.py` 已在仓库根，`quote_eligibility()` /
`CASE_FIELDS` / `CASE_SOURCES` / `default_config()` 可用）。

## 0. 本批范围

把「标准报价案例库」变成可检索的候选列表：

```
需求输入 → 硬筛选（盒型 / 结构）→ 相似度打分（尺寸 / 材料 / 工艺 / 数量）
→ 3～5 个候选（带相同项 / 差异项 / 来源 / 审核 / 排名理由）→ 人工选一个基准案例
```

**只做检索与人工选择**：不做字段工作区与差异价（批 3）、不出最终快速报价与门槛（批 4）、
不接文件解析（批 5）。本批**不生成任何价格**，候选里的价格一律来自案例库原始值。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| 仓库根 | `cpq_quick_quote_match.py` 不存在 |
| `cpq_packaging_match.py:25` | 报价侧已有**五维盒型匹配**（对盒型库 `kb_packaging_box_type`），但它匹配的是「盒型」不是「案例」，输入键是 `MATCH_INPUT_KEYS`（7 个），**没有数量档、没有材料克重、没有内托/磁铁/丝带，也没有人工选基准这一步** |
| `cpq_kb.py:68` | 没有 `kb_quick_quote_match_weight` 权重表 —— 相似度口径无处可查、无法被业务调整 |
| `cpq_agent_server.py:872` | `_handle_match_products()` 是「产品/盒型」候选，返回的是产品行，不是可复用案例 |
| 全仓 | 没有任何「为什么这个案例排第一」的可解释输出（相同项 / 差异项） |

结论：可以复用 `cpq_packaging_match.py` 的**形状**（注入式纯函数、权重读表、硬门槛淘汰、
确定性排序），但匹配目标要从「盒型库」换成「标准报价案例库」，并把**数量档**和
**结构/工艺族**纳入打分。

## 2. 契约

### 2.1 新模块 `cpq_quick_quote_match.py`

```python
ENGINE_VERSION = "quick_quote_case_match_v1"
INDUSTRY = "packaging"

#: 第一版匹配输入键（顺序即契约）。批 5 的文件解析结果必须映射到这套键上。
QUICK_MATCH_INPUT_KEYS = (
    "box_type", "box_family", "closure_type",
    "inner_length", "inner_width", "inner_height",
    "grey_board_gsm", "face_paper_gsm",
    "insert_type", "print_colors", "lamination", "hot_stamping", "v_groove", "magnet",
    "quantity",
)

#: 硬筛选维度：先按盒型和结构淘汰，再算相似度（Spec §2.3）。
HARD_GATE_DIMENSIONS = ("box_type", "box_family", "closure_type", "insert_type")

#: 参与加权的相似度维度（顺序即契约；权重一律读表，代码里不得出现数字权重）。
DIMENSIONS = ("size_range", "grey_board_gsm", "face_paper_gsm",
              "print_colors", "lamination", "hot_stamping",
              "v_groove", "magnet", "quantity")

WEIGHT_TABLE = "kb_quick_quote_match_weight"   # cpq_kb schema（批 1 已加 kb_quick_quote_config）
WEIGHT_TABLE_KEYS = ("dimension",)
DEFAULT_TOP_N = 5
MIN_CANDIDATES = 3

#: 权重表种子（与 DEFAULT_CONFIG 同值；业务可改表，代码不改数字）。
DEFAULT_WEIGHTS = (
    {"dimension": "size_range",     "weight": 0.30, "hard_gate": 0, "tolerance": 0.20},
    {"dimension": "grey_board_gsm", "weight": 0.10, "hard_gate": 0, "tolerance": 0.30},
    {"dimension": "face_paper_gsm", "weight": 0.10, "hard_gate": 0, "tolerance": 0.30},
    {"dimension": "print_colors",   "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "lamination",     "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "hot_stamping",   "weight": 0.08, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "v_groove",       "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "magnet",         "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "quantity",       "weight": 0.12, "hard_gate": 0, "tolerance": 0.0},
)
# 权重表行形状（与 kb_packaging_match_weight 同源）：dimension / weight / hard_gate /
# tolerance / industry / source_type / source_ref / version / review_status

class QuickQuoteMatchError(Exception):
    """带用户可见文案的业务错误（含「选不了这个案例」的原因）。"""

def load_weights(weights=None) -> list
def match_cases(inputs, cases=None, weights=None, *, top_n=None, today=None,
                config=None, include_deal_price=False) -> dict
def explain(inputs, case, weights=None) -> dict
def build_baseline(inputs, case_code, cases=None, *, user, weights=None,
                   today=None, config=None) -> dict
```

- `load_weights(weights=None)`：`weights` 显式传入只用传入行（离线测试）；`None` → 读
  `WEIGHT_TABLE`；读不到抛 `cpq_quick_quote_case.CaseLibraryUnavailable`（**复用批 1 的异常类，
  不得新造**）。表为空同样抛错，不回落 `DEFAULT_WEIGHTS`。
- 批 1 的 `quote_eligibility()` / `case_missing_fields()` / `find_case()` / `load_cases()`
  **必须复用**（`cpq_quick_quote_match.quote_eligibility is cpq_quick_quote_case.quote_eligibility`
  成立），不得在批 2 重写一份准入判定。

### 2.2 打分口径（确定性、可解释）

每个候选给出 `score` 明细，`similarity` 是加权和（0～1，返回时同时给 `similarity_pct` 百分数）：

| dimension | 取值 | 打分 |
| --- | --- | --- |
| `size_range` | 三边尺寸 | 对 L/W/H 各算相对差 `rel = |cur - base| / max(base, 1)`；`score = mean(max(0, 1 - rel / tolerance))`，`tolerance` 读权重行（默认 0.20） |
| `grey_board_gsm` / `face_paper_gsm` | 克重 | 相等 → 1；否则 `max(0, 1 - rel / tolerance)`，`tolerance` 默认 0.30 |
| `print_colors` | 印刷色数 | 归一后相等 → 1，不同 → 0（例如 `4C` == `CMYK`，见 §2.5） |
| `lamination` / `hot_stamping` / `v_groove` / `magnet` | 布尔 | 相等 → 1，不同 → 0；**一边缺失 → 0.5**（缺输入不算命中，也不当冲突） |
| `quantity` | 数量 | 同档（同一 `qty` 档）→ 1；跨档按档位序号距离衰减 `max(0, 1 - step_gap / 3)`（最多 3 档之外记 0） |

硬筛选（任一命中即淘汰，进 `rejected` 而不是 `candidates`）：

| 维度 | 判定 | `reason_code` |
| --- | --- | --- |
| `box_type` | 归一后不同（`""` / None 视为缺失，不进硬淘汰，转 `needs_input`） | `box_type_conflict` |
| `box_family` | 归一后不同（同上处理缺失） | `box_family_conflict` |
| `closure_type` | 归一后不同（同上处理缺失） | `closure_conflict` |
| `insert_type` | 双方都有值且不同 | `insert_conflict` |

**缺失输入不得静默当成冲突，也不得悄悄给高分**：硬筛选维度缺失时该案例
`status="needs_input"`、`reason_code="missing_input"`，排在命中的候选之后、淘汰的之前。

### 2.3 `match_cases()` 返回结构

```python
{
  "engine_version": "quick_quote_case_match_v1",
  "dimensions": [{"dimension", "weight", "hard_gate", "tolerance"}, …],
  "inputs_complete": bool, "missing_inputs": [...],
  "candidates": [{
      "case_code", "case_version", "status",            # matched / needs_input
      "reason_code",                                    # "" = 命中；"missing_input" = 缺输入
      "similarity", "similarity_pct", "score_breakdown": {dim: score},
      "standard_price", "currency", "tax_included",
      "quote_date", "valid_until", "expired", "expiring_soon",
      "source_type", "review_status", "eligible", "eligibility_reason",
      "same_items": [{"field", "label", "value"}…],
      "diff_items": [{"field", "label", "base", "current", "delta_text"}…],
      "rank_reason": str,          # 「为什么排在这个位置」的中文一句话
  }, …],
  "rejected": [{"case_code", "reason_code", "reason", "detail"}…],
  "suggested_case_code": str,      # 只是建议；eligible 候选中相似度最高者，同分按 case_code 升序
  "confirmed_case_code": "",       # 必须为空：不替用户决定
  "requires_manual_selection": True,
  "few_candidates": bool,          # 命中候选 < MIN_CANDIDATES
  "no_candidate_reason": str,      # 一个候选都没有时的中文原因 + 建议
  "weights_version": str,          # 权重口径版本（权重行的 version 去重后拼接）
}
```

要求：

1. 候选数量 `<= top_n`（默认 `DEFAULT_TOP_N=5`）；**不足 3 个如实返回**并置
   `few_candidates=True`，不编造候选、不提高阈值凑数。
2. 排序：`status` 命中在前、缺输入居中；同档按 `similarity` 降序，仍同分按 `case_code` 升序。
3. `eligible=False` 的案例（未审核 / 演示 / 过期）**可以**出现在候选里（销售需要知道"库里有像的但
   不能用"），但必须排在所有 `eligible=True` 的候选之后，并在 `rank_reason` 里点名原因；
   `suggested_case_code` 只在 `eligible=True` 中产生。
4. **成交价默认不返回**：`include_deal_price=False`（默认）时候选里不得出现 `deal_price` 键；
   `include_deal_price=True` 时才带。成交价是商务敏感信息，不能默认躺在候选列表里。
5. `no_candidate_reason` 键始终存在：**0 候选时非空**且必须给出可执行建议
   （「该需求与现有标准案例差异较大，建议转精准报价」这类），有候选时为空字符串。
6. `status="needs_input"` 的候选（硬筛选维度缺输入）必须带 `reason_code="missing_input"`；
   `status="matched"` 的候选 `reason_code=""`。
6. 纯函数：不改入参（`inputs` / `cases` / `weights`），不落库、不调模型、不联网。

### 2.4 人工选择基准案例 `build_baseline()`

```python
def build_baseline(inputs, case_code, cases=None, *, user, weights=None,
                   today=None, config=None) -> dict
```

- `user` 是**必填关键字参数**：没有它直接抛 `QuickQuoteMatchError`。
  角色必须是 `cpq_packaging_quote.WRITE_ROLES`（`sales_mgr` / `admin`，复用既有闭集，
  不得新造角色），否则抛 `QuickQuoteMatchError`。
- `case_code` 必须是 `eligible=True` 的案例，否则抛 `QuickQuoteMatchError` 并带
  `reason_code`（未审核 / 过期 / 来源不权威…）；**不允许"先选上再说"**。
- 返回基准案例快照（批 3 的工作区输入）：

```python
{
  "engine_version": "quick_quote_case_match_v1",
  "case_code", "case_version", "case_snapshot": {… 案例原始字段 …},
  "selected_by": {"user_id", "username", "role_code"},
  "selected_at": "<ISO8601>",            # 由 today/now 注入或真实时间；测试注入保持确定性
  "base_price": float, "base_currency": "CNY", "base_tax_included": bool,
  "base_quantity": float,                # 基准案例的数量档
  "base_unit_price": float,              # 基准案例在该档的单价（quantity_tiers 里那一档）
  "source_type": str, "review_status": str,
  "valid_until": str,
}
```

### 2.5 取值归一（与批 1 同口径，不得各写一份）

- `print_colors`：`"4C"` / `"CMYK"` / `"四色"` 归一为 `"CMYK"`；`"PANTONE 877C"` 保留原值；
  空值 → `""`。
- `closure_type`：按 `cpq_packaging_match` 已有的分隔符闭集（`/ + 、 , ， ; ；`）拆开比较 ——
  `"磁吸"` 与 `"磁吸/卡扣"` 视为**兼容**（交集非空即不淘汰）。
- 数量：`"3000 个"` / `"3,000"` → `3000.0`。
- 数值字段支持 `"200g"` / `"200 mm"` 这类带单位字符串（复用批 1 `normalize_case()`）。

## 3. 红测映射（`tests/test_quick_quote_case_retrieval_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：模块/常量/权重表名/与批 1 同一异常类与同一 `quote_eligibility` 对象 |
| B | 权重只从表来：注入权重变化 → 排序变化；表空/读不到 → 抛错；代码里无数字权重 |
| C | 硬筛选：盒型 / 盒族 / 闭合方式 / 内托冲突各自淘汰并给 reason_code；缺失输入 → needs_input |
| D | 相似度与排序：完全一致 → 100%；候选 ≤5；排序规则；不 eligible 的排最后 |
| E | 候选可解释性：相同项 / 差异项 / 排名理由 / 来源 / 审核 / 报价日期 / 有效期；成交价默认不返回 |
| F | 人工选择：`requires_manual_selection`、`confirmed_case_code` 为空、`build_baseline` 需要用户与角色、不可选案例抛错 |
| G | 边界：0 候选给原因 + 转精准建议；<3 候选给 `few_candidates`；输入不改；复用批 1 准入 |

## 4. 本批非目标

- 不生成报价、不算差异价（批 3、批 4）；
- 不调用技术工艺、不生成 BOM/工艺路线；
- 不接 DWG / 文件解析（批 5）；
- 不改 `cpq_packaging_match.py`（盒型库匹配保持原样，本批是新模块）。
