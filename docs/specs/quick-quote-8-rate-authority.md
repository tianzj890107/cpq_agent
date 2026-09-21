# 规格：逆向快速报价 第 8 批 —— 差异价费率权威化、出价守卫与工作台触发点

状态：Spec + 红测（**未实现**）
红测：`tests/test_quick_quote_delta_rule_authority_red.py`
依赖：批 1（案例模型）、批 3（字段工作区与差异价）、批 4（出价与转精准）。

## 0. 本批要解决的问题

批 3 的差异价规则来自 `cpq_kb.kb_quick_quote_delta_rule`。**今天线上这 4 条全是演示数据**：

```
rule_code            field_key        rule_kind  source_type  review_status
QQQ-DEMO-HOTSTEP     hot_stamping     step       demo         draft
QQQ-DEMO-LEN-RATE    inner_length     rate       demo         draft
QQQ-DEMO-PAPER-RATE  face_paper_gsm   rate       demo         draft
QQQ-DEMO-QTY-BAND    quantity         band       demo         draft
```

现状只把这件事写进每条差异项的 `note` 文本（`_rule_note()`），**没有任何一等概念**：

- 出价接口给的价里没有一个字段说明"这价是按演示费率算的"；
- `save()` 会照样把它落成正式报价版本；
- 页面上没有标记，销售看不出这价能不能对外用。

本批把"费率来源是否权威"变成**可判定、可展示、可拦截**的东西。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| `cpq_quick_quote_workspace.py` | 只有 `_rule_note()` 的**文本**告警，没有 `rule_authority()` / `authority_summary()`；`delta_price()` 行里没有权威字段 |
| `cpq_quick_quote_price.py` | `price()` 出参没有费率权威段；`save()` 不区分"试算"与"正式"，演示费率也能落库 |
| `tech_app/frontend/quick-quote-panel.js` | 没有 `renderQuote()`、没有出价 / 转精准按钮、没有演示费率的显眼标记 |
| `DEPLOYMENT.md` | 没写"demo 费率怎么换成权威口径" |

## 2. 契约

### 2.1 `cpq_quick_quote_workspace` 新增权威判定

```python
#: 能用于**正式报价**的费率来源（唯一闭集）。演示数据与未分类永远不行。
AUTHORITATIVE_RATE_SOURCES = ("workbook",)
#: 权威原因的稳定码（`ok` 排第一）。
RATE_AUTHORITY_REASONS = ("ok", "demo_rate", "not_reviewed",
                          "source_not_authoritative", "no_source")
RATE_AUTHORITY_LABELS = {...}      # 每个码一句中文，接口与前端共用

def rule_authority(rule) -> dict
def authority_summary(rules=None) -> dict
```

`rule_authority(rule)` 出参：`{"authoritative": bool, "reason_code": str, "reason": str}`。
判定顺序（先命中先返回，同输入同输出）：

1. 规则不是 dict / 缺 `rule_code` → `no_source`；
2. `source_type == "demo"` → `demo_rate`；
3. `source_type` 不在 `AUTHORITATIVE_RATE_SOURCES` → `source_not_authoritative`；
4. `review_status != "reviewed"` → `not_reviewed`；
5. `ok`。

`authority_summary(rules=None) -> dict`：

```python
{"rule_total": int, "authoritative_total": int, "authoritative": bool,
 "blocked_by": [{"reason_code", "label", "count", "codes": [rule_code 升序]}],
 "headline": str, "detail": str}
```

- `rules=None` → 走 `load_rules(None)`（读不到照样抛 `CaseLibraryUnavailable`，不回落）；
- `blocked_by` 只统计非权威规则，`count` 降序 → `reason_code` 升序（与批 6 同一条确定性口径）；
- `authoritative = rule_total > 0 and authoritative_total == rule_total`；
- 有 `demo_rate` 时 `headline` **必须**含「演示数据」；`detail` 必须写清"
  演示费率只用于流程试算，出价前必须换成权威工作簿费率"。

`delta_price(...)` 每行**新增** `"rate_authority"`（就是 `rule_authority(row)` 的结果），
既有键（`priced` / `delta` / `rule_code` / `rule_version` / `note` …）一个不动。

### 2.2 `cpq_quick_quote_price`：出价段与正式性守卫

- `price()` 出参**新增** `"rate_authority"`（§2.1 的 `authority_summary` 结果），既有键不动；
- 存在非权威费率时，`price()["warnings"]` **必须**至少有一条含「演示数据」或「非权威」字样的
  告警（说明这价是按什么费率算的）；
- 新增 `is_formal(quote) -> bool`：`quote["rate_authority"]["authoritative"]` 为真、
  且 `rate_authority.authoritative_total > 0`、且没有任何费率告警 → `True`，否则 `False`。
- `save(quote, *, user=None, session_id="", previous=None, formal=False)`：
  - 缺省 `formal=False`：**允许落库，但落的是"试算"** —— 快照里必须带 `rate_authority` 与
    `formal=False`，方便事后看出这价是按演示费率算的；
  - `formal=True` 且 `is_formal(quote)` 为假 → 抛 `QuickQuoteError`
    （文案必须含「费率」与「权威」），**不落库**；
  - 其余行为（角色校验、`session_id` 必填、版本递增）一个字不改。

### 2.3 前端触发点：出价与转精准

`tech_app/frontend/quick-quote-panel.js`：

- 新增 `window.QuickQuotePanel.renderQuote(quote)`：渲染单价 / 区间 / 偏差 / 依据 / 告警；
  - 根节点带 `data-qq-quote` 与 `data-qq-rate-authority="authoritative"|"trial"`
    （`is_formal` 视角：非权威一律 `trial`）；
  - 告警逐条渲染成 `data-qq-warning` 节点 —— **演示费率的告警必须显著**（不得折叠隐藏）；
  - 两个动作入口：`data-qq-action="save_quote"`（出价）与 `data-qq-action="transfer_precise"`
    （转精准报价）；
- 模块导出里必须有 `renderQuote`；`QUOTE_ACTIONS = ("save_quote", "transfer_precise")`
  是唯一闭集，页面字符串必须同值。

### 2.4 部署文档

`DEPLOYMENT.md` 必须登记：

- 差异价费率表 `kb_quick_quote_delta_rule` 的现状（4 条 `demo` / `draft`）；
- 换成权威费率的**具体流程**（谁能改、改成什么：`source_type='workbook'` +
  `review_status='reviewed'`，并写 `source_ref` 指向工作簿与工作表）；
- 换成权威口径之前，页面上这价长什么样（`data-qq-rate-authority="trial"` +
  演示数据告警），以及**不能**拿它当正式报价。

## 3. 红测映射（`tests/test_quick_quote_delta_rule_authority_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：两个闭集 + `RATE_AUTHORITY_LABELS` 覆盖全部码 |
| B | `rule_authority()` 五种判定（demo / 未审核 / 非 workbook / 无来源 / 权威） |
| C | `authority_summary()` 聚合、确定性排序、headline/detail 文案 |
| D | `delta_price()` 行带 `rate_authority`，既有键全部保留 |
| E | `price()` 带 `rate_authority` + 演示告警；权威费率时无演示告警；`is_formal()` 两侧 |
| F | `save()`：`formal=True` 非权威被拒且不落库；缺省落库带 `rate_authority` / `formal` |
| G | 前端：`renderQuote` + `data-qq-*` 属性 + 两个动作 + 告警不隐藏 |
| H | `DEPLOYMENT.md` 登记费率权威化流程 |

## 4. 非目标

- 不替业务决定费率数值（本批只判"这份费率能不能用"）；
- 不改差异价公式、门槛判据、偏差口径；
- 不改批 5 的解析客户端与批 6 的案例库话术；
- 不把 demo 费率"自动升格"成权威 —— 权威化必须有人签字（`review_status='reviewed'`）。
