# 链路一致性（批次 1）：单一缺口裁决源 + 金额/税唯一投影 + 版本失效

血缘：承接 `packaging-cost-readiness-severity-layering.md`（severity 分层：blocking 决定正式、
advisory 只披露）、`packaging-cost-readiness-panel.md`（`readiness` 是缺口的唯一裁决点）、
`e2e-packaging-downstream-handoff-report.md`（`readiness` 键的出处）、
`tech-cost-confirm-gaps-and-waiver.md`（2.3 签字与豁免）、
`packaging-cost-input-version-pinning.md` + `packaging-route-bom-version-pinning.md`（版本钉扎）、
`packaging-quote-close-loop.md`（交接到报价与定价）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测；业务实现由 Codex 在本地落地，
按本文 §5 未提交/未推送/未部署，红测 9 项已全绿）
红测：`tests/test_chain_consistency_batch1_red.py`
复核基线：2026-10-09 实跑（本文档 §1 的每个数字都可复现）

## 0. 一句话目标

同一份包装成本，在 **2.3 成本确认页 / 财务交接包 / 报价卡片与 Word** 三个出口必须给出
**同一个**「能不能用」结论和**同一个**金额投影；成本依据（BOM / 路线 / 业务件清单 / 规则快照）
变化后，旧结论必须失效，不得继续当当前成本用。

本批只做**一致性收口**，不重做已经绿的能力（见 §1.3 验收清单）。

## 1. 现状缺口（代码事实 + 10-9 实测）

### 1.1 T2：缺口的「能不能用」有两个源（真实分歧，已复现）

`packaging_cost.packaging_cost_readiness_gate()`（`tech_app/backend/services/packaging_cost.py:2038`）
是 severity 分层后的裁决点：只有 advisory 缺口时 `verdict=formal`、`formal_ready=True`。

但 `cost_review.summarize()`（`tech_app/backend/services/cost_review.py:241`）判 `ready` 用的是
**布尔** `packaging_result.get("has_gaps")`，完全不看 severity：

```python
"ready": bool(parts) and not missing and assembly["has_cost"] and not zero
         and not bool((packaging_result or {}).get("has_gaps"))     # ← 任何缺口都算「不可用」
         and not bool((packaging_result or {}).get("stale")),
```

实测（成本只有 1 条 advisory 缺口 `loss_rate_missing`）：

```
gate verdict = formal   formal_ready = True   blocking = 0   advisory = 1
summarize ready = False
```

同一份成本，工艺/财务页说「不可用」，报价门禁说「正式」。

`cost_review.confirm_gaps()`（`:242`）同样不看 severity：对**每一条** gap 都产
`packaging:gap:<code>:<index>` 比对键。于是**一条提示缺口就会让签字集合变大、要求重新签**——
而 Spec 里 advisory 的定义从来不是「必须签字」。

### 1.2 T2b：`stale` 只在一个源里生效（真实分歧，已复现）

`stale`（BOM / 路线 / 业务件清单 / 规则快照漂移）在 `packaging_cost.py:2864-2871`
由 `_input_drift()` / `_business_parts_drift()` 算出并挂在成本结果上。

- `cost_review.summarize()` 认 `stale`（`... and not packaging.get("stale")`）；
- **`packaging_cost_readiness_gate()` 不认 `stale`** → 一份依据已变化、但没有缺口的旧成本
  仍然 `formal_ready=True`，`formal_cost_or_raise()` 直接放行。

这就是「工序 / BOM / 排模或费率变化后，旧成本被继续当成当前成本」的那条路。

### 1.3 T9：金额/税没有唯一投影（真实分歧，用户可见，已复现）

`cpq_packaging_quote.price()`（`cpq_packaging_quote.py:216`）里：

```python
"untaxed_total": numbers["net_unit_price"] * quantity,      # 未取整浮点 × 数量
"taxed_total":   numbers["taxed_unit_price"] * quantity,    # 未取整浮点 × 数量
```

而展示/导出由 `_money()`（`"%.2f"`）**逐字段各自取整一次**（`_money_rows()` → `document()`）。
两者不保证自洽。实测用例（`total_cost=0.7035` / 毛利率 `0.3` / 税率 `0.13` / 数量 `3`）：

```
taxed_unit_price = 1.13565   → 报价单展示 1.14
taxed_total      = 3.40695   → 报价单展示 3.41
客户按展示单价复算：  1.14 × 3 = 3.42  ≠  3.41
```

`_money_rows(quote)` 实测确实同时给出 `含税单价 1.14` 与 `含税总额 3.41` —— 缺陷直接印在报价单上。
目前没有任何单一入口函数承载金额渲染，`_money()` 被模块内多处直接调用。

### 1.4 已实现、本批只做验收（禁止重做）

| 能力 | 证据 | 10-9 实跑 |
| --- | --- | --- |
| 缺口 severity 分层 | `packaging_cost_readiness_gate()` | `test_packaging_cost_readiness_severity_layering_red` **8 OK** |
| 回传协议版本 | `packaging_handoff.HANDOFF_VERSION = "pkg-quote-handoff-v1"`；`package_fingerprint()` 对 10 组（含 `bom`/`route`/`cost`）取稳定摘要 | — |
| 输入版本钉扎 | `packaging_cost.rule_snapshot_version()` + 每行 `rule_snapshot_version`；`source_versions` | `test_packaging_cost_input_version_pinning_red` **7 OK**、`test_packaging_route_bom_version_pinning_red` **14 OK** |
| 同源工艺路线（`## 534`，本地未提交） | `tech_app/backend/services/da_process_routing.py`、`cpq_process_routing.py` | `tests/test_da_process_routing_live_red.py`（并行会话产物，本批不碰） |

## 2. 契约

### 2.1 单一缺口裁决源：新增 `readiness_verdict()` 四态

`packaging_cost.readiness_verdict(cost, waiver=None) -> dict`，**唯一对外结论入口**：

| `state` | 条件 |
| --- | --- |
| `blocking_gaps` | `blocking_total > 0` **或** `silent_zero_total > 0` **或** `built is False` **或** `stale is True` |
| `demo_waived` | 命中 `blocking_gaps`，但 `waiver` 带 `signed_by`/`by` 或 `reason` |
| `advisory_only` | `formal` 且 `advisory_total > 0` |
| `formal_eligible` | `formal` 且 `advisory_total == 0` |

返回体至少含：`state`、`version`（新常量，建议 `packaging-cost-verdict/1`）、`gate`（原
`packaging_cost_readiness_gate()` 结果**原样**，不改它的字段与口径）、`waived_by`、`reasons`、
`stale_reasons`。

**`stale` 纳入阻断**是本批唯一的口径新增：依据已变化的旧成本不得再算 `formal_eligible`。

### 2.2 单一源的下游义务

- `cost_review.summarize()` 的 `ready` 必须等于 `packaging_cost_readiness_gate(...)["formal_ready"]`
  （advisory 不阻断，`stale` 阻断）；
- `cost_review.confirm_gaps()` 的比对键必须按 severity 分类：**advisory 不得进入「必须重签」的阻断集合**，
  但必须仍可披露（放入独立的 advisory 清单，不得静默丢弃）；
- `cpq_quote_gate.py` / `cpq_packaging_quote.py` 现有对 `readiness.verdict` 的读取保持不变（已经是单一源）。

### 2.3 金额/税唯一投影：新增 `money_view()`

`cpq_packaging_quote.money_view(quote) -> dict`：**纯函数、不改入参、不重算权威数字**，是报价金额的唯一渲染入口。

返回体：

```python
{
  "version": "packaging-quote-money/1",
  "currency": "CNY",
  "quantity": 3.0,
  "fields": {                      # 闭集，值是一律 2 位小数的字符串
      "cost_total": "0.70", "margin_price": "1.01", "addon_total": "0.00",
      "subtotal_unit": "1.01", "discount_amount": "0.00", "net_unit_price": "1.01",
      "tax_amount": "0.13", "taxed_unit_price": "1.14",
      "untaxed_unit_price": "1.01", "untaxed_total": "3.02", "taxed_total": "3.41",
  },
  "authority": "total",            # 总额是权威数字；单价是渲染值
  "reconciles": False,             # 展示单价 × 数量 是否恰好等于展示总额
  "difference": "0.01",            # 不自洽时的差额（自洽时为 "0.00"）
}
```

硬约束：

1. `fields` 的键集 = **既有** `_MONEY_FIELDS` 闭集（`cpq_packaging_quote.py:69`，11 个字段），
   不多不少——不新造一套常量；
2. 每个值 = 对应权威字段**只取整一次**（`"%.2f"`），不得对已取整值再参与任何算术；
3. 总额一律取权威数字（`quote["taxed_total"]` / `quote["untaxed_total"]`），
   **禁止由展示单价 × 数量重算**；
4. `reconciles` / `difference` 必须如实计算并披露，不得静默；
5. `_money_rows()`（`:519`）、`_doc_sections()`（`:524`）、`sections()`（`:616`）与卡片/接口导出
   必须消费 `money_view()`，不得各自 `"%.2f"`；
6. 报价单（`document()` 产出的 markdown/sections）里，当 `reconciles=False` 时必须出现一句
   口径披露（例如「总额以系统计算为准，与单价×数量的尾差 ≤ 0.01×数量」），不得静默。

### 2.4 尾差口径【待你拍板，本批不替你选】

`reconciles=False` 时，业务上必须二选一，金额口径由你定；本批只实现「唯一投影 + 如实披露」：

- **① 单价权威**：先定 2 位单价，`总额 = 单价 × 数量`（客户复算永远对得上；金额会变几分）；
- **② 总额权威**：总额不动，单价为渲染值（金额不变；报价单需披露尾差）。

选定后作为独立一批改金额，本批的红测只钉住「单一投影 + 显式披露」，两种口径下都应通过。

## 3. 历史兼容（不得一次性判废）

- advisory-only 的历史成本从「必须签字」变为 `formal` 后，**已签字的记录不得自动判废**，只在新结论上生效；
- 新增的 `stale → 阻断` 对**历史存量成本**：只在读取时给结论，不迁移、不回填、不删旧行；
- `money_view()` 不改变任何权威数字，只统一渲染，因此不影响既有报价版本的可读回。

## 4. 允许修改范围

1. `tech_app/backend/services/packaging_cost.py` —— 新增 `readiness_verdict()`（不改
   `packaging_cost_readiness_gate()` 的既有字段与 verdict 口径）；
2. `tech_app/backend/services/cost_review.py` —— `summarize()` / `confirm_gaps()` 改为消费 gate；
3. `cpq_packaging_quote.py` —— 新增 `money_view()`（键集复用既有 `_MONEY_FIELDS`），
   `_money_rows()` / `_doc_sections()` / `sections()` 与 `document()` 改走 `money_view()`。

## 5. 禁止事项

- 不许改 `packaging_cost_readiness_gate()` 的 verdict 口径与字段集（severity 分层已冻结）；
- 不许改 `tests/` 下任何文件（含本批红测与既有冻结红测）；
- 不许改任何权威金额数字、不许重算税率、不许引入新的取整层；
- 不许动前端、数据库、交接包字段集；
- 不许 commit / push / tag / Release / 部署。

## 6. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_chain_consistency_batch1_red -v   # 本批：红 → 绿
./open-claude/.venv/bin/python -m unittest \
    tests.test_packaging_cost_readiness_severity_layering_red \
    tests.test_packaging_cost_readiness_panel_red \
    tests.test_packaging_cost_input_version_pinning_red \
    tests.test_packaging_route_bom_version_pinning_red \
    tests.test_packaging_quote_close_loop_red -v                                          # 不回归
```

## 7. 验收清单（本批只验收、不实现）

- [ ] 同源工艺路线：`da_process_routing` 只读桥在服务不可用时如实 `unavailable`，不虚构路线；
- [ ] 回传协议版本：`HANDOFF_VERSION` 与 `package_fingerprint()` 对 BOM/路线变化敏感；
- [ ] 输入版本钉扎：`stale` + `stale_reasons` 在 BOM / 路线 / 业务件清单三条轴上各自可解释；
- [ ] 缺口分层：`test_packaging_cost_readiness_severity_layering_red` 保持 8 OK。

## 8. 实现记录（10-9，Codex 本地；按 §5 未提交/未推送/未部署）

只动了 §4 允许修改的 3 个文件，未改任何 `tests/`、未动金额权威数字、未改 gate 字段与口径：

- `tech_app/backend/services/packaging_cost.py`
  · 新增 `VERDICT_VERSION = "packaging-cost-verdict/1"`、`VERDICT_STATES` 与
    `readiness_verdict(cost, waiver=None)`（§2.1 四态；`gate` 原样透传，`stale` 纳入阻断、
    原因进 `stale_reasons`）；
  · `formal_cost_or_raise()` 的放行结论改取 `readiness_verdict()`（**放行/拒绝返回的仍是 gate 本体**）
    —— 这就是 §1.2「依据漂移的旧成本仍被放行」那条路；实测 stale 成本现在 409
    `packaging_cost_not_formal`，带 `signed_by`/`reason` 时仍可豁免。
- `tech_app/backend/services/cost_review.py`
  · `summarize()["ready"]` 改取 `readiness_verdict(...)["formal_ready"]`（advisory 不阻断、stale 阻断）；
  · `confirm_gaps()` 按 severity 分家：提示缺口**不进** `codes`/`blocking_codes`（不再逼重新签字），
    另给 `advisory_codes` / `advisory_fields` / `advisory_count` 披露；`codes` / `fields` 仍逐位对齐。
- `cpq_packaging_quote.py`
  · 新增 `MONEY_VERSION = "packaging-quote-money/1"` 与 `money_view(quote)`（§2.3：键集 = 既有
    `_MONEY_FIELDS`、每值只取整一次、总额取权威数字、`reconciles`/`difference` 如实计算）；
  · `_money_rows()` / `_doc_sections()` / `sections()` 的金额行全部改走 `money_view()`；
  · `document()` 在 `reconciles=False` 时输出一句口径披露（含「含税总额…以系统计算为准…尾差…请以总额为准」）。
    Spec §2.4 的尾差口径（① 单价权威 / ② 总额权威）**仍未拍板**，本实现两种口径下都成立。

验证（本机 `./open-claude/.venv/bin/python -W ignore -m unittest`）：

- 本批红测 `tests.test_chain_consistency_batch1_red` → `Ran 9 … OK`（改前 `failures=5, errors=3`）；
- §6 不回归五模块 → `Ran 151 … OK`；
- 更宽一圈（`packaging_cost_*` / `packaging_quote_*` / `cost_review_*` / `tech_cost_confirm_*` /
  `packaging_handoff_*` 共 42 模块）→ `Ran 755 … OK (skipped=1)`；
- 全量 `unittest discover -s tests -p 'test_*.py'` → `Ran 7003 … OK (skipped=28)`（零红）。
