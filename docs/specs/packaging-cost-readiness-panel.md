# 规格：成本面板上必须看得见「正式 / 暂定」裁决与逐条缺口的分层

依赖：`docs/specs/packaging-cost-engine.md` §2.1（`readiness` 是缺口的**唯一**裁决点）、
`docs/specs/packaging-cost-readiness-severity-layering.md`（§2.2 blocking / advisory 分层 +
"提示必须看得见，不许静默"；该 Spec §4 明写"不许改前端"，把界面接入留在后面）、
`docs/specs/packaging-cost-finance-access.md`、`docs/specs/packaging-silent-degradation-disclosure.md`
（说不出"这份成本是正式的还是暂定的"就是静默降级）。

状态：Spec + 红测（已实现）（原状：后端 `packaging_cost.load_cost()` 的每一份返回体都挂着
`readiness`（`_with_readiness()` `:2553`；`packaging_cost_readiness_gate()` `:1984` 给出
`verdict` / `formal_ready` / `gap_total` / `blocking_total` / `advisory_total` / `reasons` /
逐条结构化缺口 `gaps[]`（`gap_evidence()` 带 `code` / `detail` / `missing_variable` /
`resolution_action` / `resolution_entry` / `severity` / `affected_amount`）），
而 `tech_app/frontend/requirement-confirm.js` 里 `readiness` / `verdict` / `severity` /
`resolution_action` / `blocking` / `advisory` **全部 0 处引用**（`grep -c` 实测）：
① 用户看不出这份成本是**正式**还是**暂定**——而这正是"能不能拿去报价"的唯一结论；
② `pcGapLine()`（`:996`）把**每一条**缺口都写成 `待询价：<detail>`，于是
`part_size_missing`（缺展开尺寸）/ `no_formula:print`（印刷费手填列）/
`tooling_basis_missing:T-PKG-DIE-REFUND`（分摊基数待商务裁决）都被说成"待询价"——
**缺的是尺寸 / 公式 / 口径，不是价格**；
③ 后端已经分好的 blocking / advisory 两档在界面上被摊成一条平列；
④ 每条缺口的"要补什么变量、找谁补（入口）"（`missing_variable` / `resolution_action` /
`resolution_entry`）在前端 0 处 —— 看到缺口的人不知道下一步做什么）
红测：`tests/test_packaging_cost_readiness_panel_red.py`
行号基线：HEAD `4a67805`

## 0. 一句话目标

成本面板顶部说清"这份成本是正式还是暂定、为什么"，缺口的每一条说清
"是阻断还是提示、缺什么变量、谁来补"，并且**不再把所有缺口都叫待询价**。

## 1. 现状缺口（代码级）

1. `pcPanel()`（`requirement-confirm.js:960`）读了 `cost.built` / `cost.gaps` / 各类合计，
   **没有**读 `cost.readiness` —— 裁决在接口上、在页面上不存在；
2. `pcGapLine()`（`:996`）只要一条缺口就打 `待询价：` 前缀（`gap.detail || gap.code`），
   `severity` / `missing_variable` / `resolution_action` / `resolution_entry` 一个都不读；
3. `packaging-cost-readiness-severity-layering.md` §2.2 要求的"提示必须看得见"在界面上没有落点。

## 2. 契约

### C1 四个纯函数（`requirement-confirm.js` 的包装成本面板 IIFE 内；体内无 DOM / `fetch(` / `storage`）

- `pcReadinessVerdict(cost)` → `{verdict, formal, headline, reasons, counts}`：
  - `verdict`：闭集只认 `"formal"` / `"provisional"`，其余（缺失 / 空 / 别的字符串）→ `""`（未知档）；
  - `formal`：`verdict === "formal"`；
  - `headline`：
    - `formal` → `正式成本：缺口已清零，可用于正式报价。`
    - `provisional` → `暂定成本：有阻断缺口或还没测算，出价前必须走放行留痕（POC 豁免签字）。`
    - 未知档 → `这份成本没有正式/暂定的裁决（后端没给 verdict），别当成正式成本。`
  - `reasons`：`cost.readiness.reasons` 逐字（trim 后丢空项、保序）；非数组 → `[]`；
  - `counts`：`{gap_total, blocking_total, advisory_total, unbound_total}`，逐项 `Number(...)`，
    非有限数或负数 → `0`（**不许** `null` / `undefined`）；
  - `cost` 不是对象 / `readiness` 不是对象 → 未知档 + `[]` + 全 `0`，**不抛错**。
- `pcGapSeverityLabel(severity)` → 人话（闭集两档，其余未知档）：
  | 输入 | 返回 |
  | --- | --- |
  | `blocking` | `阻断（挡住正式成本）` |
  | `advisory` | `提示（不影响正式/暂定）` |
  | 其它 / 空 / 非字符串 | `未知档（后端没给严重度）` |
- `pcGapActionText(gap)` → `""` 或一句：
  - `vars` = `gap.missing_variable`（数组，逐项 trim、去空）；`action` = `gap.resolution_action`（trim）；
    `entry` = `gap.resolution_entry`（trim）；
  - 三者全空 → `""`（没话说就别说，**不许**编一句动作）；
  - 否则按 `要补：<vars 用「、」连>` + `；<action>` + `（入口：<entry>）` 拼，
    **缺的部分整段跳过**（不留空串、不留 `（）`）。
- `pcGapPrefix(code)` → `"待询价"` 或 `"待补输入"`：
  - `待询价` 的**闭集**只有价格/费率类：`material_price_missing` / `material_price_unit_missing` /
    `material_price_unit_mismatch` / `rate_missing` / `freight_rule_missing`；
  - 其余（含空串、`part_size_missing`、`no_formula:print`、`tooling_basis_missing:*` 之类）→ `待补输入`。

### C2 面板渲染（`pcPanel()`）

- 顶部新增裁决条：`data-pc-readiness="<verdict || 'unknown'>"`，正文 = `headline`
  + `阻断 N · 提示 M · 缺口合计 K`（N/M/K 取自 `counts`）；
  - `reasons` 逐条渲染成 `data-pc-readiness-reason="<下标>"`（逐字，不加前缀）；
  - **没测算过也要显示**（`built=false` 时后端给的是 `provisional` + "成本尚未测算"，不许隐藏）。
- 缺口区按严重度**分层**（数据源优先 `cost.readiness.gaps`，它逐条带 `severity`；
  老载荷没有这份证据时退回 `cost.gaps`，严重度按未知档）：
  1. blocking 组：`data-pc-gap-blocking="<N>"`（N = 该组行数，`0` 时整组不渲染）；
  2. advisory 组：`data-pc-gap-advisory="<M>"`（同样 `0` 时不渲染）；
  - 每一条 + 每条缺口：`data-pc-gap="<code>"` + `data-pc-gap-severity="<severity 原文>"`，
    正文 = `pcGapPrefix(code)` + `：` + `detail`（退 `code`）+ 严重度人话（`pcGapSeverityLabel`）
    + `pcGapActionText(row)`（非空才渲染）；
  - 两组都空时**一个节点都不多**（与今天"没有缺口"的页面一致）。
- 既有 banner（过期 / BOM 读不到 / 路线版本读不到 / 规则快照 / 回传漂移）与 `pcAuditBlock()`
  **一字不动**。

### C3 冻结面

- 裁决**只有一个来源**：后端 `readiness.verdict`（前端不许按 `gaps` 自己判正式/暂定）；
- 不改后端、不改 `packaging_cost_readiness_gate()` / `gap_evidence()` 的任何字段与取值；
- 不加接口、不加依赖；`pcPanel()` 里不新增请求；不改 `index.html`；不改 `tests/` 下任何文件。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：C1 四个纯函数 + `pcPanel()` 的裁决条与缺口分层；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许在前端重算 verdict（"阻断缺口 > 0 就是暂定"这类判定归后端）；
- 不许把所有缺口都说成"待询价"（`待询价` 只留给价格 / 费率类的那五个码）；
- 不许在 `resolution_action` 为空时编一句动作；不许留空 `（）` / `要补：`；
- 不许把未知 `severity` 归到 `blocking` 或 `advisory` 任一一档；
- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不连 PG / 34、不写生产数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_readiness_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_readiness_severity_layering_red tests.test_packaging_cost_engine_red \
  tests.test_packaging_cost_gaps_red tests.test_packaging_cost_column_evidence_red \
  tests.test_packaging_cost_red_closure_red tests.test_packaging_bom_disclosure_panel_red \
  tests.test_packaging_material_unresolved_panel_red tests.test_packaging_handoff_audit_pending_panel_red
```

## 6. 已记录的边界

1. 只**显示**裁决与缺口分层，不提供"在页面上签豁免"的入口（放行留痕仍走既有回传路径）；
2. `affected_amount` / `blocking_amount_total` 本批不上界面（金额口径已由合计区承担）；
3. `content_binding_source` 的界面落点另提（`packaging-cost-content-binding-source-disclosure.md`
   只在接口上）；
4. 缺口条目排序**保持后端给的顺序**（前端不排序，避免出现第二套口径）；
5. 其它行业的成本面板仍只在包装需求单上挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

**实现前红基**（`git stash push -- tech_app/frontend/requirement-confirm.js` 后的原文）：

```
Ran 26 tests ... FAILED (failures=23)
```

23 红 / 3 绿 —— 绿的三条是环境与冻结面护栏（E6 既有 banner 一字不动 + 面板不发请求、
F3 `index.html` 未动、F4 `node --check` 通过）；
23 红 = A1–A6、B1–B2、C1–C5、D1–D3（四个纯函数根本不存在）
+ E1–E5（面板里 0 处 `data-pc-readiness` / `data-pc-readiness-reason` /
`data-pc-gap-blocking` / `data-pc-gap-advisory` / `data-pc-gap`）+ F1、F2。

**实现后**：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_readiness_panel_red -v
Ran 26 tests ... OK
node --check tech_app/frontend/requirement-confirm.js   # 退出码 0
```

落点（只改了 `tech_app/frontend/requirement-confirm.js` 一个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 | `pcReadinessVerdict()` / `pcGapSeverityLabel()` / `pcGapActionText()` / `pcGapPrefix()`（包装成本面板 IIFE 内，`pcGapLine()` 原位置之前） | 四个纯函数，体内无 DOM / `fetch(` / `storage`；`verdict` 闭集只认后端原文的 `formal` / `provisional`，其余（含缺失）→ 未知档；`counts` 四键 `Number()` + 非有限 / 负数 → `0`；严重度闭集外一律未知档（不归 blocking / advisory 任一一档）；`pcGapPrefix()` 的「待询价」闭集只有价格 / 费率那五个码 |
| §C2 | `pcPanel()`：`readinessBlock` 插在 `${pcHandoffDriftBanner(handoff)}` 之后、`${head}` 之前；缺口区由 `gapRow()` 逐条渲染，再按 `gapSeverity()` 分成 `blockingGapBlock` / `advisoryGapBlock`（在 `${totals}` 之后） | `data-pc-readiness="<verdict||unknown>"` + `data-pc-readiness-reason="<下标>"` 逐条；`data-pc-gap-blocking="<N>"` / `data-pc-gap-advisory="<M>"`（`0` 时整组不渲染）；每条 `data-pc-gap="<code>"` + `data-pc-gap-severity="<severity 原文>"`，正文 = `pcGapPrefix()` + detail + `pcGapSeverityLabel()` + `pcGapActionText()`；数据源优先 `cost.readiness.gaps`，老载荷退回 `cost.gaps`；既有 banner 与 `pcAuditBlock()` 一字未动 |
| §C3 | 无 | 裁决只读 `readiness.verdict`（`pcReadinessVerdict()` 体内出现 `readiness`、**不出现** `gaps`）；未加接口、未改后端与 `index.html`、未加依赖、`pcPanel()` 里无 `fetch(` |

实现说明：原来那个"**每一条都打 `待询价：`**"的 `pcGapLine()` 被并入 `pcPanel()` 的
`gapRow()`（§C2 要求 `data-pc-gap` / `data-pc-gap-severity` 由面板落笔），行为改为
"前缀按码分档 + 严重度人话 + 要补什么 / 找谁补"；同一文件里没有第二处渲染缺口的地方，
删掉旧函数不留死代码。

复跑命令与结果：

```
# Spec §5 不回归（含本文件上其它面板批次）
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_readiness_severity_layering_red tests.test_packaging_cost_engine_red \
  tests.test_packaging_cost_gaps_red tests.test_packaging_cost_column_evidence_red \
  tests.test_packaging_cost_red_closure_red tests.test_packaging_bom_disclosure_panel_red \
  tests.test_packaging_material_unresolved_panel_red tests.test_packaging_handoff_audit_pending_panel_red
Ran 226 tests ... OK
```

边界（与 §6 一致，实现如约未越）：只显示不提供"页面上签豁免"入口、不上
`affected_amount` / `blocking_amount_total`、不排序（保持后端给的顺序）、
`content_binding_source` 仍只在接口上、非包装行业仍不挂载成本面板。
