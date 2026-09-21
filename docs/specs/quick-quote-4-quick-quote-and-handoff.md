# 规格：逆向快速报价 第 4 批 —— 快速报价生成、风险提示与转精准报价

状态：Spec + 红测（已实现）
红测：`tests/test_quick_quote_generation_red.py`
依赖：批 1（案例模型）、批 2（候选 + `build_baseline()`）、批 3（字段工作区 + 差异价）。
**本批假设前三批已实现**。

## 0. 本批范围

把「基准案例 + 差异项」组装成一份**有依据、有区间、有有效期、有门槛**的快速报价，
并给出超范围时的出口（一键转精准报价）。

```
快速报价 = 基准案例价格 + 尺寸修正 + 材料修正 + 工艺增减 + 数量档修正
          + 模具/版费修正 + 运输修正
```

**本批不进入技术工艺**：`transfer_to_precise()` 只产出交接包并复用既有「转技术工艺」
任务口径（`cpq_wf.TASK_KIND_TECH_NEW`），实际派发仍由用户在界面上点那个按钮完成。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| 仓库根 | `cpq_quick_quote_price.py` 不存在 |
| `cpq_packaging_quote.py:214` | 只有**精准报价**的定价入口 `price(package, …)`：吃的是技术工艺成本包（`packaging_package`）。快速报价没有基准价 + 差异项的入口，也没有"偏差范围"概念 |
| `cpq_packaging_quote.py:74` | 报价版本表 `cpq_wf_quote_version` 记录的是精准报价版本；快速报价的基准案例 + 差异依据不落任何地方 |
| `cpq_wf.py:51` | 已有 `TASK_KIND_TECH_NEW = "tech_new_product"`（「转技术工艺」支线，`确认需求解析结果.html:799` 的按钮就是它）。**缺的是把快速报价已填数据整包带过去** |
| `cpq_tech_bridge.py:620` | `HANDOFF_KINDS` 是既有闭集；本批**不得**新增 kind，也不得改这个闭集 |
| 全仓 | 没有快速报价的适用门槛与"差异较大就转精准"的出口 |

## 2. 契约

### 2.1 新模块 `cpq_quick_quote_price.py`

```python
ENGINE_VERSION = "quick_quote_v1"
INDUSTRY = "packaging"
QUOTE_KEY = "quick_quote_price"        # 卡片第 2 步快照里的段名
QUOTE_STEP = 2

#: 适用门槛（顺序即展示顺序）。
GATE_KEYS = ("case_reviewed", "case_not_expired", "box_compatible",
             "size_within_threshold", "quantity_in_range", "no_unknown_process")
GATE_LABELS = {"case_reviewed": "命中已审核案例", …}   # 每项中文标签
ADVICE_TRANSFER = "当前需求与标准案例差异较大，快速报价可能失真，建议转精准报价。"

#: 转精准报价的必填字段（缺一不可，缺了不许带残缺数据过去）。
TRANSFER_REQUIRED_FIELDS = ("box_type", "closure_type", "inner_length", "inner_width",
                            "inner_height", "face_paper_gsm", "grey_board_gsm",
                            "print_colors", "quantity")
TARGET_PRECISE = "precise_quote"
#: 落库/转交角色复用既有闭集（Spec §2.4）。
WRITE_ROLES = cpq_packaging_quote.WRITE_ROLES
#: 税率复用既有口径，不得新造。
DEFAULT_TAX_RATE = cpq_packaging_quote.DEFAULT_TAX_RATE

class QuickQuoteError(Exception):
    """带用户可见文案的业务错误。"""

class QuickQuoteBlocked(QuickQuoteError):
    """门槛未过：`.result` 里带门槛结果、拦下哪几项与转精准建议。"""

def gate_config(config=None) -> dict
def gate_check(baseline, workspace, *, today=None, config=None, rules=None) -> dict
def price(baseline, workspace, *, today=None, config=None, rules=None) -> dict
def quote_fingerprint(quote) -> str
def transfer_to_precise(quote, *, user=None, session_id="") -> dict
def save(quote, *, user=None, session_id="") -> dict
def find_quote(quote_id="", *, session_id="") -> dict
```

### 2.2 配置（批 4 只**加键**，不动表）

在 `cpq_quick_quote_case.default_config()` 上补下列键（表结构不变），`gate_config()` 复用同一份：

```python
{"size_diff_threshold": 0.15,        # 三边单边相对差上限
 "quantity_min": 100, "quantity_max": 100000,
 "base_deviation_pct": 0.05,         # 基准偏差
 "per_miss_deviation_pct": 0.02,     # 每个「无规则差异项」增加的偏差
 "max_deviation_pct": 0.20,          # 偏差上限
 "tax_rate": 0.13}                   # 与 cpq_packaging_quote.DEFAULT_TAX_RATE 同值
```

### 2.3 适用门槛 `gate_check()`

```python
{"passed": bool,
 "items": [{"key", "label", "passed", "detail"}…],   # 六项，顺序同 GATE_KEYS
 "blocking": [key…],                                  # 未通过的项
 "advice": str,                                       # 未通过时 = ADVICE_TRANSFER
 "size_diff": {"max_rel": float, "threshold": float},
 "unknown_process_fields": [field…]}
```

| key | 通过条件 | 不通过 detail 要点 |
| --- | --- | --- |
| `case_reviewed` | 基准案例 `review_status == "reviewed"` | 案例未审核 |
| `case_not_expired` | `today <= valid_until` | 案例报价已过期，需重新询价 |
| `box_compatible` | 当前 `box_type` / `closure_type` 仍等于基准案例值 | 盒型/结构已改，请重新检索候选案例（不许"改了盒型还按老案例算"） |
| `size_within_threshold` | 三边相对差 `max_rel <= size_diff_threshold` | 点名哪一边差多少 |
| `quantity_in_range` | `quantity_min <= 数量 <= quantity_max` | 点名区间与当前值 |
| `no_unknown_process` | 结构/工艺字段（`lamination` / `hot_stamping` / `v_groove` / `magnet` / `window` / `ribbon` / `insert_type`）里，任何**相对基准新增**的项都必须有差异价规则 | 点名哪些工艺没有标准依据，必须转精准 |

### 2.4 快速报价 `price()`

`price()` 先跑 `gate_check()`：未通过直接 `raise QuickQuoteBlocked(ADVICE_TRANSFER)`，
异常对象 `.result` = 门槛结果（**不得**先算一个价格再提示"仅供参考"）。

通过后返回：

```python
{
  "engine_version": "quick_quote_v1",
  "quick_quote_id": str,                # 幂等 id（见 quote_fingerprint）
  "version_no": int,                    # 同 id 重复保存只递增，不覆盖历史
  "case_code": str, "case_version": int,
  "base_price": float, "base_unit_price": float,
  "base_currency": "CNY", "tax_included": bool, "tax_rate": 0.13,
  "delta_items": [批 3 diff 行 …],       # 修改项 + 每项加减金额
  "delta_total": float,
  "unit_price": float,                  # = base_unit_price + delta_total
  "unit_price_taxed": float,            # = unit_price × (1 + tax_rate)（案例含税则同值）
  "price_range": {"low": float, "high": float},
  "deviation": {"est_pct": float, "est_amount": float, "basis": str},
  "valid_until": str, "rule_versions": [str …],
  "gate": {… gate_check 结果 …},
  "warnings": [str …],
  "risk_notice": str,
  "requirement": {field: value},        # 已填字段快照（= 工作区 current 的字段值）
  "transfer_available": True,
  "basis": [str …],                     # 完整依据（基准案例 / 基准价格 / 每项加减 / 规则版本）
  "created_at": str,
}
```

口径：

1. `unit_price` 是**不含税**口径（除非基准案例 `tax_included=True`，此时 `unit_price` 即含税，
   `unit_price_taxed == unit_price`）；`tax_included` 原样继承基准案例，不猜。
2. 偏差：`est_pct = min(max_deviation_pct, base_deviation_pct
   + per_miss_deviation_pct × 无规则差异项数)`；`est_amount = unit_price × est_pct`；
   `price_range = [unit_price × (1 - est_pct), unit_price × (1 + est_pct)]`。
   「无规则差异项」= 批 3 `diff_table()` 里 `priced=False` 的行 —— 这类差异**没有价格依据**，
   必须扩大偏差区间并在 `warnings` 里点名，**不得当成 0 差异悄悄放过**。
3. `risk_notice` 必须包含：基准案例编号、基准报价日期/有效期、预估偏差百分比、以及
   「按区间报价并注明依据」这句提醒。
4. `basis` 至少包含：基准案例编号与版本、基准价格、每一项差异的字段与金额、用到的规则版本。
5. 到期不足 `expiry_warn_days` 的案例：允许出价，但 `warnings` 必须提示即将过期。

### 2.5 落库与版本（复用卡片快照）

```python
def save(quote, *, user=None, session_id="") -> dict
def find_quote(quote_id="", *, session_id="") -> dict
```

- `save()`：`user` 必填且 ∈ `WRITE_ROLES`，`session_id` 必填；写
  `cpq_wf.merge_step_snapshot(session_id, 2, {"quick_quote_price": quote})`；**不新建表**。
- `quote_fingerprint(quote)`：由「基准案例编码 + 案例版本 + 当前字段值 + 规则版本」组成
  （与 `cpq_packaging_quote.quote_fingerprint` 同思路：sha256 十六进制前 16 位）。
  同指纹重复保存 → 同一个 `quick_quote_id`（只更新 `version_no`）；字段或规则变了 → 新 id。
- `find_quote()` 从第 2 步快照的 `quick_quote_price` 段读回；没有 → `{}`（不编造）。

### 2.6 一键转精准报价 `transfer_to_precise()`

```python
def transfer_to_precise(quote, *, user=None, session_id="") -> dict
```

- 门槛未过的报价**不允许**转（`QuickQuoteError`）：先按 `ADVICE_TRANSFER` 走人工。
- `user` 必填且 ∈ `WRITE_ROLES`；`session_id` 必填。
- 要带过去的字段取自 `quote["requirement"]`（`price()` 产出时已带工作区 `current` 的值）；
  缺 `TRANSFER_REQUIRED_FIELDS` 里的任一字段 → 抛 `QuickQuoteError` 并点名缺哪些，
  **不得发出请求**（"不能重新输入"的前提是这次带过去的数据是完整的）。
- 复用既有「转技术工艺」口径：调用 `cpq_wf.send_task(session_id, user,
  task_kind=cpq_wf.TASK_KIND_TECH_NEW, payload=…)`，**不得**新增 `handoff_kind`、
  不得改 `cpq_tech_bridge.HANDOFF_KINDS`、不得直接调用技术工艺服务。
- 返回：

```python
{"engine_version", "transfer_id", "task_kind": "tech_new_product",
 "task_id": str, "target": "precise_quote",
 "payload": {"quick_quote": {… 快速报价整包 …}, "requirement": {已填字段…},
             "baseline_case": {…}, "delta_items": […], "price": {…}},
 "input_fields_complete": True, "missing_inputs": [], "created_at"}
```

- `payload["requirement"]` 必须是**已填好的字段值**（尺寸 / 材料 / 工艺 / 数量 / 盒型），
  技术工艺侧不需要重新问一遍；`payload["quick_quote"]` 带门槛结果、偏差与依据，
  让接手人能看到这条快速报价是怎么来的。

## 3. 红测映射（`tests/test_quick_quote_generation_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：门槛键/标签、必填字段、复用 `WRITE_ROLES` 与 `DEFAULT_TAX_RATE`、不新增 handoff kind |
| B | 配置：新增键默认值、`gate_config()` 与 `default_config()` 同源、税率复用 |
| C | 门槛：六项逐项通过/不通过、`blocking`、`advice` 文案、尺寸/数量边界 |
| D | 定价与依据：`unit_price = base + delta`、含税口径、`basis` 完整、`delta_items` 与批 3 一致 |
| E | 偏差与区间：无规则差异项扩大偏差并进 `warnings`；区间上下界；`risk_notice` 要点 |
| F | 落库与版本：角色/会话校验、`quick_quote_price` 段、指纹幂等与版本递增 |
| G | 转精准报价：门槛未过拒绝、必填字段缺失拒绝、`task_kind` 复用、payload 带全已填数据、不动既有闭集 |
| H | 非回归：`cpq_packaging_quote` 定价口径未改、`cpq_tech_bridge.HANDOFF_KINDS` 未变 |

## 4. 本批非目标

- 不做技术工艺实现（BOM / 工艺路线 / 成本测算 / 审批 / 报告回传）；
- 不改精准报价的定价公式与报价单口径；
- 不接文件解析与 DWG（批 5）；
- 不自动派发任务：`transfer_to_precise()` 只产出交接包。

## 5. 实现期回写（2026-09-21，实现时实测发现，实现按红测落地）

`tests/test_quick_quote_generation_red.py::test_e3_deviation_capped` 原写法的字段组合与
Spec §2.3 第 6 条**互相矛盾**，无论怎么实现都不可能同时成立：

- 原文用 `window=True` 堆偏差。而 `window` 属于 §2.3 第 6 条的「结构/工艺字段」，
  基准值 `False → True` 属于「相对基准**新增**」且 `kb_quick_quote_delta_rule` 里没有
  `window` 的规则 → 必须被 `no_unknown_process` 拦下，`price()` 根本不该出价；
- 于是「偏差封顶」这条断言永远跑不到（同一份红测的 C8 恰恰钉死了「新增工艺无规则 → 拦下」）。

实现期按 Spec §2.3 的口径（也是 C8 的口径）落地，并把 E3 改成用
「**删项**（`magnet` / `v_groove` 由 True 改为 False，属删除不是新增，不触发第 6 条）+
无规则的费用项（`freight_amount`）」堆偏差，再用 `config={"max_deviation_pct": 0.10}`
把上限压到 10% 真正验证封顶 —— 断言只增不减，并加了说明注释。

另一处口径澄清（实现期定稿）：第 6 条的「相对基准新增」按**新增**判定 ——
布尔字段 `False/空 → True`、文本字段 `空 → 非空`；
删除（`True → False`）与「换成另一个非空值」不算新增，只按 §2.4 第 2 条扩大预估偏差区间
（这类差异没有价格依据时也不得当成 0）。
