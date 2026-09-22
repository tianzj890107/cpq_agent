# 成本就绪结论必须按缺口严重度分层：`blocking` 决定正式/暂定，`advisory` 只披露

血缘：承接 `packaging-cost-engine.md`（§2.1 就绪门）、`e2e-packaging-downstream-handoff-report.md`
（`readiness` 键的出处）、`tech-cost-confirm-gaps-and-waiver.md`（豁免签字那条路）、
`packaging-cost-gaps-closure.md`（缺口只登记不拍板）。

状态：Spec + 红测（已实现）（`verdict` 只由 blocking / 静默按 0 / 尚未测算决定；advisory 进
`advisories` + `advisory_total` 只披露；金额按严重度拆两数，`affected_amount_total` 仍是两者之和）
红测：`tests/test_packaging_cost_readiness_severity_layering_red.py`

## 0. 一句话目标

`GAP_RESOLUTIONS` 里的 `severity`（`blocking` / `advisory`）必须在**结论**上起作用：
阻断缺口决定"能不能算正式"，提示缺口只负责"看得见"。今天这个字段除了进不了结论，还是死的。

## 1. 现状缺口（代码事实）

`tech_app/backend/services/packaging_cost.py:1737-1760`：

```python
blocking = [row for row in evidence if row["severity"] == "blocking"]
silent   = [row for row in evidence if row["silent_zero_fallback"]]
reasons = []
if blocking:  reasons.append("%d 项阻断缺口未清零" % len(blocking))
if silent:    reasons.append(...)
verdict = READINESS_PROVISIONAL if (gaps or silent) else READINESS_FORMAL   # ← 这里是"任何缺口"
```

于是：

1. **`severity` 白算**：`blocking_total` / `advisory` 之分算出来了，`verdict` 只读 `gaps` 的**长度**；
2. **一条纯提示缺口就能把成本打成"暂定"**：`loss_rate_missing`（advisory）、
   `freight_rule_missing`（advisory）、`below_moq`（advisory）任一在，`formal_ready=False`；
3. **代价落在出口**：`formal_cost_or_raise()`（1772-1788）对 `formal_ready=False` 一律
   `CostError(409, "packaging_cost_not_formal")`，要 POC 签字才放行 —— 提示性缺口按这个口径
   等于"必须签字"，而 Spec 里 `advisory` 的定义从来不是这个意思；
4. **金额也混在一起**：`affected_amount_total` 把 advisory 的金额算进同一个数
   （34 那次跑：9 条 advisory `loss_rate_missing` 影响 5.4831 元/件 ≈ 成本的 31%），
   读的人分不出"这 5.48 是阻断的还是提示的"。

## 2. 契约

### 2.1 结论只由阻断决定

`packaging_cost_readiness_gate(cost)`：

| 条件 | `verdict` |
| --- | --- |
| `blocking_total > 0` | `provisional` |
| 任一行命中静默按 0（`silent_zero_total > 0`） | `provisional` |
| `built is False` 且无缺口 | `provisional`（"成本尚未测算"，既有行为） |
| **只有 advisory 缺口** | `formal` |

`formal_ready` 仍等于 `verdict == "formal"`。

### 2.2 提示缺口必须看得见，不许静默

- 新增 `advisory_total`（int）与 `advisories`（结构化缺口证据列表，形状同 `gaps`）；
- 纯 advisory 时 `verdict == "formal"`，但 `advisories` 必须非空、`reasons` 里必须出现
  「N 项提示缺口」这样一句 —— 页面与报告读得到"这份正式成本带了 9 条提示"；
- `gaps` 键保持原样（全部缺口的原始清单），既有读端不破。

### 2.3 金额按严重度拆开，旧键保持兼容

新增 `blocking_amount_total` / `advisory_amount_total`（`round(..., 6)`）；
`affected_amount_total` 仍等于两者之和（既有读端不破）。
`unquantified_total` 口径不变。

### 2.4 出口按同一把尺子

`formal_cost_or_raise(cost, waiver=None)`：

- `verdict == "formal"`（含"只带 advisory"）→ 直接返回 gate，**不得**要求豁免签字；
  返回体里 `advisories` 必须原样带出；
- `verdict == "provisional"` → 行为一个字不改：有 `signed_by` / `reason` 的 waiver 才放行
  （返回 `waived=True` + `waived_by` + `waiver_reason`），否则 `CostError(409,
  "packaging_cost_not_formal")`。

### 2.5 不许放宽的两个方向

- 静默按 0 一律仍是 `provisional`（§2.1 第二行），不因为"severity=advisory"就放行；
- `severity` 的取值表（`GAP_RESOLUTIONS`）本批不许改。

## 3. 允许修改范围

只改 `tech_app/backend/services/packaging_cost.py`：`packaging_cost_readiness_gate()`
与 `formal_cost_or_raise()`。**不加开关、不加配置项、不加新路由**。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件；
- 不许改 `GAP_RESOLUTIONS` / `SILENT_ZERO_RESOLUTIONS` / `READINESS_VERSION` 的字面；
- 不许把 `gaps` 改短（提示缺口不许从 `gaps` 里消失，只是结论不再是暂定）；
- 不许改前端、不许改回传正文、不许改 `packaging_handoff`；
- 不许 commit / push / tag / Release / 部署。

## 5. 验收

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_readiness_severity_layering_red -v   # 红→绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_red_closure_red -v                  # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red -v                       # 不回归（J6 存量红除外）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red -v                      # 不回归
```
