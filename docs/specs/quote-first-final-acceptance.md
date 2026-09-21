# 规格：从报价开始的包装 DWG 终验（E2E 链路 / Go-No-Go / 报告与回滚）

批次：新五批（上线闭环）**第 5 批（终验）**。红测：`tests/test_quote_first_final_acceptance_red.py`。

前置（已完成，不在本批范围）：
- 第 1 批：`tech_app/tools/dwg_deploy_gate.py`（19 项转换器上线门禁，含 `converter_rollout_documented`）
  与 `DEPLOYMENT.md` 的「DWG 转换器（包装图纸）」小节（配置取值 / 生效方式 / 上线三件套 / 两条硬禁令）。
- 第 2 批：`tech_app/tools/kb_deploy_preflight.py` 与 `DEPLOYMENT.md` 的「知识库（cpq_kb）上线」小节
  （建表 → 留底 → dry-run → 写入 → 预检 → 回滚）。
- 第 3 批：从报价开始的入口分级与落点恢复通道。
- 第 4 批：包装专属参数族与技术侧包装闭环口径。
- 既有：`tech_app/backend/services/dwg_acceptance.py`（DWG 能力声明与金标审批，`approved_by` /
  `approved_at` / `golden_version` / 原因码闭集）与 `tests/fixtures/dwg_acceptance/<版本>/` 基线目录。

**本批目标**：不再新增业务能力，而是把「从报价开始的包装全流程」变成**可执行、可取证、可判定**的
一份终验：一条 12 步的验收链路、一份与第 1 批互补的门禁清单、一个纯函数的 Go/No-Go 判定、
报告模板与失败回滚口径。**本批不授权部署**：不改服务器、不连 34、不重启服务。

---

## 1. 现状取证（实测）

1. **验收链路没有唯一口径**：从报价开始的五角色交接（销售 → 工艺 → 财务 → 工艺 → 销售）目前散在
   现场记录与会话里，仓库里没有任何一处声明"这一步的账号、前置门禁、必须留下的证据"。全仓
   `grep -rn "ROLE_CHAIN\|go_no_go\|GO_BLOCKERS"` → 0。
2. **门禁清单只管转换器**：`dwg_deploy_gate.GATE_ITEMS` 已 19 项，覆盖许可 / 版本 / 健康检查 /
   临时目录 / 磁盘 / 超时 / 并发 / 恶意隔离 / 模型故障 / 迁移回滚 / 老项目 / 非包装回归 / STEP 回归 /
   密钥 / 本机依赖 / CI 分层 —— 但**没有一条**覆盖"项目是否真的从报价开始""知识库是否权威数据"
   "包装闭环是否真的走通""历史会话是否恢复""stale 结果是否被当成有效报价"。
3. **能力声明有现成机制但没接业务链路**：`dwg_acceptance.SUPPORT_CLAIMS =
   ("orchestration_only", "conversion_available", "supported")` 已禁止 "real" 这类模糊声明，
   金标目录 `tests/fixtures/dwg_acceptance/2026-09-21.1/` 也已在位（manifest + 两份样本 JSON，
   业务结论留空待人工复核）—— 缺的是"这一步/这份证据属于哪条验收链路"的连接。
4. **失败回滚与验收报告没有模板**：第 1/2 批各自写了回滚口径，但没有一份把"整条终验失败时怎么退"
   与"通过时要留哪几项"合起来的口径。

---

## 2. 验收链路（唯一入口）

新增 `tech_app/tools/quote_first_acceptance.py`，纯只读模块（不连服务器、不写业务数据）：

```python
MODULE_VERSION = "quote-first-acceptance/1"
ROLE_CHAIN = ("sales_mgr", "process_mgr", "finance_mgr", "process_mgr", "sales_mgr")
STEPS = (...12 项...)
```

`STEPS` 的每一项都是 `{"no", "key", "title", "role", "gate", "evidence"}`：

| no | key | 角色 | 前置门禁（gate） | 必须留下的证据（evidence） |
| --- | --- | --- | --- | --- |
| 1 | `quote_create` | sales_mgr | 项目入口为报价（`entry_origin=quote`） | `business_case_id` / `quote_session_id` / `card_id` |
| 2 | `requirement_fill` | sales_mgr | 包装必填 10 项齐 | `requirement_snapshot_version` / `industry` |
| 3 | `box_match` | process_mgr | 盒型候选已确认 | `box_type` / `match_score` / `source_type` |
| 4 | `tech_handoff` | sales_mgr | 「新增工艺」任务已派发 | `source_task_id` / `source_session_id` / `business_case_id` |
| 5 | `dwg_parse` | process_mgr | DWG 原生解析成功，或如实失败 | `converter` / `converter_version` / `ir_version` / `three_d_status` |
| 6 | `params` | process_mgr | 包装族必填齐（或签字带缺口） | `family` / `required_filled` / `required_total` |
| 7 | `bom` | process_mgr | 参数化 BOM 已生成且有引擎版本 | `engine_version` / `item_count` |
| 8 | `route` | process_mgr | 工艺路线已确认并冻结 | `engine_version` / `step_count` / `confirmed_at` |
| 9 | `cost` | finance_mgr | 成本已确认，或带缺口签字 | `rule_version` / `input_snapshot` / `gaps` |
| 10 | `report` | process_mgr | 报告已审核并正式发布 | `report_no` / `version` / `published_at` |
| 11 | `back_to_quote` | sales_mgr | 交接落回原报价卡片 | `handoff_id` / `quote_session_id` / `business_case_id` |
| 12 | `history_recover` | sales_mgr | 刷新/重进后全量恢复 | `chat_turns` / `field_evidence` / `versions` |

要求：

1. `STEPS` 的顺序即执行顺序，`no` 从 1 连续到 12；`key` 唯一。
2. 每项的 `role` 必须属于 `ROLE_CHAIN` 的取值集合；四类角色之外的账号不得出现在链路里。
3. 每项的 `gate` 与 `evidence` 都必须非空 —— 没有前置条件或没有证据的步骤不算验收步骤。
4. `REPORT_FIELDS` 必须含 `steps` 与 `samples`：每一步的证据随这两项一起留档
   （不要求在报告里每个证据键各占一格），所以"要留证据"和"报告能装下"是同一件事。

---

## 3. 门禁清单（与第 1 批互补，不重复）

```python
GATE_ITEMS = ((gate_id, kind, title), ...)      # kind ∈ ("auto", "manual")
```

必须覆盖以下 10 项（id 与标题可自拟，语义不得少）：

| id | kind | 语义 |
| --- | --- | --- |
| `entry_from_quote` | auto | 两个真实样本项目都是从报价创建（`entry_origin=quote`） |
| `business_case_linked` | auto | 技术项目 meta 里有 `business_case_id` 且回传落回原卡片 |
| `kb_authoritative` | auto | 知识库预检通过：无 `demo_only` / `unclassified_rows` |
| `dwg_parsed_natively` | auto | 至少一份样本由服务端转换器原生解析（不是"看 PNG 猜尺寸"） |
| `packaging_closure_complete` | auto | 盒型 → 参数 → BOM → 路线 → 成本 五段都留下了引擎版本 |
| `cost_traceable` | auto | 成本可解释到知识库记录与规则版本；缺口进入 gaps 而不是被抹平 |
| `stale_not_published` | auto | `derived_results_stale=true` 时不得审核发布，除非有实名 stale waiver |
| `history_recovery` | auto | 刷新 / 重进 / 切换角色后会话、字段、证据、版本完整恢复 |
| `role_chain_handoff` | manual | 五个角色按 `ROLE_CHAIN` 交接，权限错误只提示一次且不阻断合法流转 |
| `golden_approved` | manual | 金标业务小节由人工复核并签字（`approved_by` / `approved_at`） |

要求：`GATE_ITEMS` 至少 10 项、id 唯一、`kind` 属于闭集；**不得**复制
`dwg_deploy_gate.GATE_ITEMS` 的既有 id。

---

## 4. Go / No-Go（纯函数）

```python
GO_BLOCKERS = ("steps_incomplete", "entry_not_from_quote", "industry_not_packaging",
               "kb_not_authoritative", "dwg_not_native", "packaging_closure_incomplete",
               "real_converter_unverified", "history_not_recovered",
               "stale_results_published", "permission_blocked")

def go_no_go(evidence: dict) -> dict:
    """返回 {"verdict": "go"|"no_go", "blockers": [...], "claim": str}。"""
```

`evidence` 的键（缺键一律按"不满足"处理）：

`samples` / `steps`（完成的 step key 集合）/ `entry_origin` / `industry` /
`kb_source_types`（知识库实际使用的来源集合）/ `dwg_converted_native`（bool）/
`packaging_closure`（bool）/ `real_converter`（bool）/ `history_recovered`（bool）/
`stale_published`（bool）/ `permission_ok`（bool）

判定口径：

1. 所有条件满足且 `real_converter=True` → `verdict="go"`、`blockers=[]`、
   `claim="包装行业 DWG 支持完成"`。
2. `real_converter=False` → `verdict="no_go"`，`blockers` 含 `real_converter_unverified`，
   `claim="DWG 编排能力完成，真实转换能力未验收"` —— **只完成 fake converter 测试绝不允许
   说「支持 DWG」**。
3. `kb_source_types` 里只要出现 `demo` 或为空 → `kb_not_authoritative`。
4. 缺少任一 step、`entry_origin != "quote"`、`industry != "packaging"`、
   `dwg_converted_native` / `packaging_closure` / `history_recovered` / `permission_ok` 为假、
   `stale_published` 为真 → 各自对应的 blocker。
5. `blockers` 里只能出现 `GO_BLOCKERS` 闭集内的值；顺序按 `GO_BLOCKERS` 的声明顺序稳定输出。
6. 纯函数：同输入同输出、不改入参、不读库、不读环境变量。

---

## 5. 验收报告模板与金标业务小节

```python
REPORT_FIELDS = (...)              # 报告必须有的字段，顺序即展示顺序
GOLDEN_BUSINESS_SECTIONS = (...)   # 金标里必须由**人工**填写的业务小节
```

1. `REPORT_FIELDS` 至少含：`golden_version` / `converter` / `samples`（含 `source_sha256`）/
   `steps` / `gate_verdict` / `blockers` / `claim` / `approved_by` / `approved_at` / `rollback_plan`。
2. `GOLDEN_BUSINESS_SECTIONS` 至少含（对应终验计划里列出的那几项）：
   `unit_status`（单位状态）、`cut_layer` / `crease_layer`（刀线与压痕线）、`box_type_candidates`
   （盒型候选）、`key_dimensions`（关键尺寸及证据）、`pending_confirmations`（必须出现的待确认项）、
   `forbidden_hallucinations`（明确禁止出现的幻觉字段）、`downstream_snapshot`
   （BOM/路线/成本输入快照）、`quote_draft_allowed`（是否允许生成报价草稿）、`three_d_status`（3D 状态）。
3. 金标变化必须人工审查：沿用 `dwg_acceptance` 的 `approved_by` / `approved_at` 口径，
   **不得**自动更新快照来让测试转绿。

---

## 6. 部署文档

`DEPLOYMENT.md` 新增「从报价开始的终验」小节，必须写明：

1. 验收链路（`STEPS` 的 12 步，含每步角色与证据）。
2. 门禁清单与 Go/No-Go 判定口径（含 §4.2 的那句 claim 原文）。
3. 验收报告模板字段与金标人工审批要求。
4. 失败回滚策略：终验失败时**保留**已完成的门禁结果与证据、只回退本次部署改动，
   不删除历史项目/会话/上传文件；不得用删除数据的方式让门禁变绿。
5. 本批对应命令（红测 + 门禁 + 报告生成），与第 1/2 批的上线三件套并列。

---

## 7. 红测

`tests/test_quote_first_final_acceptance_red.py`，五组共 20 条：

| 组 | 覆盖 |
| --- | --- |
| A（5） | 验收链路：模块/版本常量、`STEPS` 12 步且序号连续、`key` 唯一、角色属于 `ROLE_CHAIN`、gate/evidence 非空且证据键有报告落点 |
| B（4） | 门禁清单：≥10 项、id 唯一、kind 闭集、10 项语义齐全、不与 `dwg_deploy_gate` 的 id 重复 |
| C（4） | Go/No-Go：全满足 → go、`real_converter=False` → 编排 claim、缺步骤 → `steps_incomplete`、闭集 + 纯度 |
| D（4） | 报告与金标：`REPORT_FIELDS` 必含项、`GOLDEN_BUSINESS_SECTIONS` 必含 10 小节、金标沿用 `dwg_acceptance` 审批口径、金标目录在位 |
| E（3） | 文档与护栏：`DEPLOYMENT.md` 有终验小节（链路/Go-No-Go/报告/回滚四要素）、`dwg_acceptance.SUPPORT_CLAIMS` 仍不含 "real"、`dwg_deploy_gate.GATE_ITEMS` 不减项 |

验证方式：纯函数行为（`go_no_go`）+ 常量契约（`STEPS` / `GATE_ITEMS` / `REPORT_FIELDS` /
`GOLDEN_BUSINESS_SECTIONS`）+ 文档断言 + 既有机制护栏。全部离线：
不连服务器、不调模型、不写业务数据、不执行部署。

实现前实测（2026-09-21，`./open-claude/.venv/bin/python -m unittest tests.test_quote_first_final_acceptance_red`）：

```
Ran 20 tests in 0.012s

FAILED (failures=17)
```

- 红 17 条（A 5 / B 4 / C 4 / D 3 / E 1），失败原文一致指向同一条硬缺口：
  `缺少 tech_app/tools/quote_first_acceptance.py（Spec §2）—— 终验链路、门禁清单与 Go/No-Go 判定必须有一处唯一口径，现在散在现场记录里`；
  `DEPLOYMENT.md` 仍缺终验小节；`D3` 读到的 `quote_first_acceptance.py` 源码为空串，
  说明金标审批口径（`approved_by` / `approved_at`）尚无落点。
- 绿 3 条护栏：`E2`（`dwg_acceptance.SUPPORT_CLAIMS` 仍不含 `real`）、
  `E3`（`dwg_deploy_gate.GATE_ITEMS` 仍 19 项且含 `converter_rollout_documented`）、
  `D4`（金标目录 `tests/fixtures/dwg_acceptance/2026-09-21.1/` 在位，manifest 指向的两份
  样本 JSON 真实存在）。

也就是说：本批新增的只是一个**判定入口 + 文档小节**，红测失败是"入口尚未存在"，
不涉及任何既有机制改动；护栏全绿证明前三批的既有口径没有被稀释。

---

## 8. 禁止事项

- **不授权部署**：不改服务器、不连 34、不重启服务、不执行 `deploy_server.sh`。
- 不把 `dwg_deploy_gate` / `kb_deploy_preflight` / `dwg_acceptance` 的既有口径与 id 改掉或复制一份。
- 不自动更新金标来让红测转绿；`approved_by` / `approved_at` 只能由人工填写。
- 不改既有红测；不为通过门禁而伪造样本、成本、知识库行或报告。
- 不装新依赖；不删历史项目/会话/上传文件。

---

## 9. 五批之后的收尾口径

- 终验是**声明**的唯一依据：只有 `go_no_go` 返回 `go` 且金标人工审批通过，才允许把
  「包装行业 DWG 支持完成」写进对外说明；否则只能按 §4.2 的 claim 原文如实声明。
- 若第 1–4 批中任何一批的实现在终验时未合入，本批的 `blockers` 必须如实列出对应项，
  不许用"跳过该项"凑出 go。
