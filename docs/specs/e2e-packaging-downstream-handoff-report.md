# 包装零件 → 工艺 → 成本 → 财务 → 报告闭环 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_e2e_packaging_downstream_handoff_red.py`

## 1. 线上证据

包装专用链路已生成 64 个零件、BOM、12 道工序和 `7.274860582846279 CNY/件`，但通用
`_integration_ir()` 只读 legacy `DesignIR`，所以 2.2 报“请先完成 2.1”。人工桥接后，正确财务任务
`TP-95255258` 虽被 FI1 领取，打开成本工作台仍显示“项目不存在”。回报价时存在多个候选，
带 `source_task_id` 反而无法消歧；绕过该字段后能回报价，却无法关闭原财务任务。
报告草稿又只读 legacy IR，形成 0 零件、0 成本的空报告。

## 2. 统一下游投影

增加唯一的项目制造快照适配层，向 2.2、2.3、报告提供统一结构：
`parts`、`bom`、`process_route`、`cost`、`requirement_revision`、`source_fingerprints`。
包装来源读取 packaging parts/BOM/route/cost；其他行业继续读取 legacy IR。业务层不得分别猜来源。

### 2.1 成本权威性与缺口

本次线上试算的 24 个缺口包括：灰板厚度缺少 GSM/单位换算、装帧布/磁铁/海绵绒布无权威价、
损耗率缺失、印刷无公式、模具分摊依据缺失，以及包装内容公式仍引用未绑定变量
`lengthmm/widthmm/heightmm/usageqty/gsm`。成本服务必须返回结构化缺口（规则、变量、物料、影响金额、
补数入口），区分 `formal` 与 `provisional`。缺口未清零时可以按 POC 豁免流转，但不能标成正式成本，
不能静默以 0 代替，也不能在报价或报告中隐藏。

## 3. 任务与 ACL

1. 发送财务任务时，任务 `session_id/project_id` 必须是技术项目 ID，报价 session 只能放关联字段。
2. 任务创建与项目参与权在同一事务完成。角色池财务在已发送财务状态下可读；领取人可执行成本写操作。
3. 销售经理对有来源报价的技术项目可读；总监按既有审核接口拥有相应写权限。
4. ACL 拒绝返回 403；真实不存在才返回 404，禁止用 404 隐藏“已领取但无权”的项目。

## 4. 唯一关联与原子回传

- `business_case_id` 是消歧主键；显式给定后只能选该实例，不得再把来源任务候选并入导致冲突。
- 回传报价与关闭来源任务是一个可重试事务/状态机：二者全成或保持可恢复状态。
- 重试不得新增重复报价卡、重复财务任务或重复会话消息。

## 5. 包装报告

报告草稿必须从统一制造快照读取 64 个零件、BOM、12 道工序、成本与 24 个缺口；缺口如实展示。
不得以 legacy IR 为空为由判定“2.1 未完成”。总监审核/发布后，报告版本和回报价任务共享同一
`business_case_id`，销售可继续原报价而非新建空卡。

## 6. 验收

PE1 发送后 FI1 能打开并写成本；成本回传后原财务任务关闭且只生成一个销售待办；
报告草稿非空、来源指纹可追溯；全链路没有手工替换 project id 或省略 source task 的绕行。

## 7. 实现记录（`## 280`）

### 7.1 统一制造快照（§2）

- 新增 `tech_app/backend/services/manufacturing_snapshot.py`（唯一适配层，只读）：
  `SNAPSHOT_VERSION="manufacturing-snapshot/1"`、六个标准分区
  `parts / bom / process_route / cost / requirement_revision / source_fingerprints`、
  `load_snapshot()` / `summarize()` / `blocked_message()` / `as_design_ir()`。
  包装来源读 packaging parts / bom / route / cost 四份文档；其余行业仍读 legacy `DesignIR`，
  但套进同一套键，业务层只有一种读法。`source_fingerprints` 是稳定 JSON 的 sha256
  （`loaded_at` 不入指纹）。
- `as_design_ir()` 是**唯一**把包装零件投影成既有 `DesignIR` 的点：逐件走
  `packaging_parts.as_ir_part()`，不新建第二套零件语义。
- `main.py` 的 `_integration_ir()`（2.2）与 `_cost_review_ctx()`（2.3）改读统一快照，
  不再 `store.load_ir()` 一棵树上吊死。
- `report_workflow.py` 的 `report_source_payload()` / `source_is_current()` /
  `prerequisite_issues()` / `new_report()` / `_technical_result()` 全部改读统一快照；
  报告草稿对包装项目给出零件/BOM/工序/成本与缺口要点。
  注意：`report_source_payload()` 仍**不含**顶层成本分区（既有红测
  `test_tech_summary_report_includes_cost_review_red` 要求审核依据摘要只取
  `device_name / ir / steps / summary`，本批只追加 `manufacturing` 与 `source_fingerprints`）。
  `loaded_at` 不进报告依据，否则 `source_is_current()` 会永远为假。

### 7.2 任务 ACL 与原子回传（§3 / §4）

- `cost_flow.py`：`COST_TASK_SOURCE="cost_task_assignee"`、`grant_task_project_access()`
  （任务创建/指派时**同时**落参与者记录与任务记录，幂等）、`claimed_task_by()`、
  `task_access_records()`；`main.py` 的 `/integration/send-to-finance` 在指派到人时调用它。
- `project_access.py`：`claimed_task_project_access()`（领取人依据，纯读），并接进
  `can_read()`；`require_project_access()` 对「已领取但不可读」（归档等）返回 **403**
  而不是 404（`CLAIMED_FORBIDDEN_MESSAGE`）。
- `cost_flow.py`：`explicit_business_case_is_authoritative()`（显式实例号即唯一落点，
  来源候选只作为 `ignored_candidates` 如实回给界面）、
  `assert_distinct_project_and_quote_session()`（技术项目号与报价会话号混成一个值即 409）、
  `handoff_operation_id()` / `load_handoff_state()` / `save_handoff_state()` /
  `source_task_closed()` / `record_handoff_operation()` / `resume_incomplete_handoff()`
  （回传与关任务是同一操作号下的可重试状态机；重试只补关任务，不重发报价）。
  `integration_send_to_quote_body()` 现在会先 `resume_incomplete_handoff()`，
  并在返回体里给出 `handoff_operation_id` / `source_task_closed`。

### 7.3 成本权威性（§2.1）

- `packaging_cost.py`：`READINESS_VERSION="packaging-cost-readiness/1"`、
  `GAP_RESOLUTIONS`（缺口码 → `missing_variable` / `resolution_action` / `entry` /
  `severity`）、`gap_variables()`、`gap_evidence()`（规则 + 变量 + 物料 + `affected_amount`
  + 补数入口）、`reject_silent_zero_fallback()`（缺口点名变量、明细行却照样出金额 ⇒ 静默按 0）、
  `packaging_cost_readiness_gate()`（`formal` / `provisional` + 阻断/静默计数）、
  `formal_cost_or_raise()`（暂定成本必须有 POC 签字才放行）。
- `compute_project()` / `load_cost()` / 未测算分支的结果里新增 `readiness` 键；
  既有键一个未动、金额一个未改。

### 7.4 未做 / 边界

- 未改任何 `tests/`；未改成本表达式、费率、权重、门槛判据；未连 PG、未写生产数据。
- 报告草稿的 `evaluation_items` / `stage_results` 仍由 3.1 汇总流程维护（本批只保证
  零件/BOM/工序/成本与缺口**进得来**，不代替人工写结论）。
- 财务侧「开始动手即落参与权」的钩子只做在 `/integration/send-to-finance`（指派到人时）；
  角色池任务由报价侧领取，领取人身份回传后才会落记录 —— 未新增领取回调路由。
