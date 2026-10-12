# Spec：成本账按「组成（section）」发号时，明细必须仍然归得回父件

状态：Spec + 红测（**未实现**）
红测：`tests/test_packaging_cost_ledger_section_recon_red.py`（本批新增，10 条；当前 8 红）
血缘：`docs/specs/packaging-single-cost-ledger.md`（§分类与数量：单件行按整单明细 `part_code` 归集、
明细与各类别及整单总额对账，失配须披露并禁止确认）、
`docs/specs/packaging-multipart-business-part-complete.md`（一个业务零件可由多个独立物理部分组成）、
`docs/specs/e2e-packaging-downstream-handoff-report.md`（2.3 → 3.x 的下游交接）。
证据来源：34 线上全流程复跑（2026-10-12），项目 `6beeeebb19a1`。

## 0. 一句话目标

一个业务零件由多个物理部分组成时，公式账为每个组成单独发号（`<父件号>::<section_id>`）。
这批行必须**归回父件**参与单件与整单对账，而不是被当成"未知零件"把整本账判成对不上。

## 1. 现状缺口（34 实测，可复现）

| 读数（线上接口原文） | 实测 |
| --- | --- |
| `GET /api/projects/6beeeebb19a1/cost-review` | `reconciled=false`、`ready=false` |
| `reconciliation_errors`（8 条，逐字） | `unknown_part:DWG-BP10::section:5b917c0cdfad`、`...DWG-BP10::section:d63d17878cb8`、`DWG-BP12::section:286cb7948964 / 2f738b764241 / 3f12787ffae4 / 8af333be2a8f / d2e56484c7c1 / eedbe3c67740` |
| `counts` | `{parts: 28, parts_costed: 26, missing: [DWG-BP10, DWG-BP12], zero: [其余 26 件], assembly_costed: false}` |
| `parts_total` | 材料 / 人工 / 加工 / 模具 / 包装 / 运输 / 其他 **全 0** |
| `final` | 只有 `processing 0.929`、`packaging 3.091`、`freight 0.12`，`total 4.1407813971912395` |
| `POST /cost-review/confirm`（带 `waiver` 签字） | **400**「包装成本明细与总额未对齐，不能确认或发送，请重新测算并核对」 |
| `POST /cost-review/send-to-quote`、`POST /cost-review/return-to-process` | 同上 **400**（三条去向同一道门） |
| 连带 | 需求单三步审批（`submit-confirmation` → `confirm` → `review`）**已通过**、技术工艺总结已生成并确认，3.1 报告仍**送不出**（`submit-review` 409「工艺可行性结论为空；各工艺阶段汇总结论为空」，见 §5） |

这不是"零件真的不存在"：`DWG-BP10 / DWG-BP12` 是**有多个物理部分的件**，公式账为每个部分单独发号
（`packaging_sections.section_input_rows()`：`business_part_code = 父件号 + '::' + section_id`，
`packaging_sections.py:283`）；对账侧只拿父件号去比，于是

- 子件号 → 判成未知零件（`unknown_part:`）；
- 父件在 `grouped` 里查不到明细 → `has_cost=false` → 进 `missing`，`subtotal=0`。

`_assert_packaging_ledger()`（`cost_flow.py:308`）在 `waiver` 判定**之前**执行，所以这条硬门禁
签字也绕不过 —— 现状下这两个件多组成，就等于整条 2.3 → 3.x 走不下去。

## 2. 契约

### 2.1 C1：子件号归回父件

`packaging_cost_ledger.project(doc, ledger)` 归集明细行时，`part_code` 落在
`<父件号>::<section_id>` 形态（`::` 为分隔符，`<父件号>` 必须在 `doc['business_parts']`
的 `business_part_code` 集合里）的行，必须归到 `<父件号>` 这一件上：

- 该行的金额照旧按它自己的 `cost_category` 计入父件对应类别；
- 父件的 `items` / `subtotal` / `unit_cost` / `item_count` 都按**全部组成累加**后计算（用量仍取父件用量）；
- `unknown_part:` 只对"父件号也不在清单里"的情况报出，不许对合法子件号报。

### 2.2 C2：真未知零件仍然拦住确认

`unknown_part:` 不许被放宽：`part_code` 既不在清单、其父件号也不在清单的行必须仍然报
`unknown_part:<原样 part_code>`，`reconciled=false`。为让 C1 转绿而删掉这条守卫，视为未实现。

### 2.3 C3：归集之后总额与明细仍逐分对齐

修好归属之后：`parts_total[类别] + project_expenses[类别] == final[类别]`（沿用
`part_amount_exceeds_category_total` / `category_total_mismatch` 两道既有判定），
`final['total']` 仍取账本 `total_cost`。归属齐全时 `project_expenses` 必须归零，不许用残差兜。

### 2.4 C4：准入状态随证据走

- 归属齐全但仍有件没算成本（`missing` 非空）或算出来是 0 元（`zero` 非空）→ `ready` 仍为
  `false`，但 `reconciled` 可以为 `true`（两者是两件事：**账对不对**与**够不够格确认**）；
- 也就是说：**不许**为了让 `confirm` 放行而把 `missing` / `zero` 一并抹掉；
- `waiver` 的既有口径不变（缺口带签字可继续），但硬门禁只应拦"账对不上"，不应拦
  "件多组成"。

### 2.5 C5：口径只改归集，不改账

不得改：成本公式与费率、`apply_loss` 口径、`loss_base_scope`、`packaging_sections` 的发号规则、
`cost_review.confirm_gaps` 的缺口分级、任何权限与路由。子件号是对账**读侧**的表示，
写侧（各组成各自的材料/工艺行）一个字都不许动。

## 3. 验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_ledger_section_recon_red -v
# 目标：10 OK（当前 8 红：A 组 4 + B 组 2 + C 组相关）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_single_cost_ledger_red -v              # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_e2e_packaging_downstream_handoff_red -v        # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red -v                   # 不回归
```

线上复验（隔离或复跑同一项目，不改历史数据）：

1. `GET /cost-review` → `reconciled=true`，`reconciliation_errors=[]`；
2. `DWG-BP10 / DWG-BP12` 由 `missing` 变为有金额（或如实留在 `missing` 但不再是 `unknown_part`）；
3. `POST /cost-review/confirm` 不再出现「明细与总额未对齐」（其余缺口按 L2 走 `waiver`）。

## 4. 禁止事项

- 不许改本批红测文件、不许改 `packaging-cost-minimum-charge.md` 已裁决的口径。
- 不许把 `unknown_part:` 改成警告（它必须仍然拦人）。
- 不许在 `project()` 之外再写一套"对账结果"给页面看（`packaging-single-cost-ledger.md`：
  只有一本账）。

## 5. 相邻问题（不在本批，已另有 Spec）

同一趟复跑还暴露了 3.1 的两处内容缺口：`process-report/submit-review` 409
「工艺可行性结论为空；各工艺阶段汇总结论为空」——3.1 汇总页把"经济可行性"写死成占位句、
阶段汇总没有 2.3 行。**这一条已由 `tests/test_tech_summary_report_includes_cost_review_red.py`
（Spec 见其文件头）覆盖**，本批不重复出 Spec、不改它。
