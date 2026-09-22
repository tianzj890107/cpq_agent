# 技术工艺统一流程状态机、阶段完成条件与准入门禁（批次 5B）

依赖：批次 5A 的「五阶段 × 13 子步骤」口径（`docs/specs/tech-workflow-five-phase-naming.md`）。
本批的子步骤号、阶段标题一律按 5A 的表；stage id 与页面文件不变。

状态：Spec + 红测（已实现）
红测：`tests/test_tech_unified_workflow_projection_red.py`

## 1. 背景与真实问题

今天「这一步做完了没有、下一步能不能点」是**前端猜出来的**：

* `tech-workbench.js:refreshProgress()` 用一堆启发式拼完成态：
  * `if (reqStatus) done.add('requirement-create')` —— 需求单只要存在（含草稿）就算「创建」完成；
  * `if (integration.process && integration.process.steps.length) done.add('process')` ——
    只生成了工序就算「组装与整合」完成，参数是否确认、工艺是否确认、有没有 waiver 全不看；
  * `if (costReview.confirmed) done.add('cost')`、summary / report / publish 三条按报告状态猜；
* 顶部流程条只按「有没有 project」决定未来阶段能否进入，不区分「能看」与「能执行」；
* `catch (e) { state.progress = { done: new Set() } }` —— 读取失败时把完成态**清空成未完成**，
  用户看到「进度全没了」，还以为项目被重置；
* 各业务页（组装、成本、报告）自己再算一套「能不能点」，同一件事在三个地方三份实现。

结果：草稿被当成「已创建」、只生成工序被当成「组装与整合已完成」、读取失败被当成「全没做」，
后续阶段的可执行性也无法统一回答「为什么不能点」。

## 2. 用户角色与用户故事

* 作为工艺工程师，我想点开任何一个步骤（含未来步骤）看看里面是什么，但**不该能执行**
  前置条件不满足的动作；点不动时我要知道「缺什么、谁来做」。
* 作为工艺经理，我要一眼看出「组装与整合」到底完没完成（参数确认 / 工艺确认 / waiver），
  而不是看到「有工序就算完成」。
* 作为财务经理，我要看到成本必须**正式确认**才算完成，「算过但没确认」不算。
* 作为审核人，我要能区分「报告已发布」和「报告已回传报价」这两件事。
* 作为任何用户，当流程状态暂时取不到时，我要看到「刷新失败，以下为上次状态」，
  而不是一片「未完成」。

## 3. 当前流程

1. 前端 `refreshProgress()` 并行取 `/api/projects/{id}/workflow` 与 `/api/projects/{id}/summary`，
   在前端算 9 个 stage 的完成集合，写入 `state.progress.done`。
2. 顶部流程条与卡片标题行只用这个集合与「有没有 project」渲染可用/禁用。
3. 读取失败 → 完成集合被清空。
4. 页面自己的动作可用性各自判断（`cost-review.js` 的 `crCanRun`、`summary-result.js` 的
   `srHasDraft`、`report-publish-result.js` 的 `rpPrimaryAction` 等）。

## 4. 目标流程

后端出一个**唯一**的流程投影（projection），前端只渲染、不拼装：

1. `GET /api/projects/{project_id}/workflow/projection` 返回
   `{ project_id, generated_at, refresh_ok: true, phases: [...], stages: [...], next_action: {...} }`。
2. `phases`：5 个阶段（`no / title / completed / status`）。
3. `stages`：13 个子步骤，每条至少包含
   `{ key, phase_no, phase_title, sub, sub_title, stage_id, view, status, viewable, actionable,
      completed, stale, blocked_reasons: [], missing_requirements: [], required_role,
      primary_action, next_stage }`。
4. 前端新增纯函数模块 `tech_app/frontend/tech-workflow-projection.js`：
   `TechWorkflowProjection.progress(projection)` → `{ done, status, blocked, actionable, stale }`
   （全部按 `key`/`stage_id` 索引），`refreshProgress()` 只调它，不再自己算。
5. 读取失败：保留上一次 `state.progress`，并置「刷新失败」提示位，
   **不得**把完成集合清空。

## 5. 状态定义及状态转换

统一状态枚举（`status`）：

`not_started` → `in_progress` → `generated` → `edited` → `awaiting_confirmation` → `confirmed`
，另有 `blocked` / `stale` / `in_review` / `approved` / `published`。

| 子步骤 | 完成（`completed=true`）条件 | 典型非完成状态 |
|---|---|---|
| 1.1 创建需求 | 需求单**已提交确认**（`pending_review` / `approved`） | 草稿 `draft` → `in_progress`，**不算完成** |
| 1.2 确认需求 | 完整性检查已运行且用户明确点过确认（缺口可带 waiver） | `awaiting_confirmation` |
| 1.3 审核需求 | 有审核权限的人给出「通过」（可带缺口批准） | `in_review` |
| 2.1 图纸解析 | 解析已执行且 IR 已生成；**0 个零件**时必须有人工「确认无零件结果」 | `generated`（有 IR 未确认） |
| 3.1 整合图纸 | 整合分析已执行（没有额外整合图纸时，允许基于 IR 的默认整合结果） | `not_started` |
| 3.2 参数推荐 | 参数推荐已**人工确认**（`params_confirmed`，允许带缺口 + waiver） | `awaiting_confirmation` |
| 3.3 组装工艺 | 组装工艺已确认（含参数最终确认与 waiver 记录） | `generated`（**只生成了工序不算完成**） |
| 4.1 零件成本 | 每个零件成本已测算；被排除的零件必须登记 `excluded_parts`（不能当 0 元） | `in_progress` |
| 4.2 组装成本 | 组装成本已测算 | `not_started` |
| 4.3 汇总 | 成本汇总存在且财务**正式确认**成本 | `awaiting_confirmation` |
| 5.1 汇总结果 | 报告草稿已生成并落库（有编号/编制时间） | `generated` |
| 5.2 结果审核 | 报告已送审且审核结论明确（通过或驳回） | `in_review` |
| 5.3 发布并回传报价 | 报告已正式发布 **且** 已回传报价 | `published`（已发布、**未回传**）→ 完成后 `completed=true` |

派生规则：

* `viewable` 恒为 `true`（有项目即可看，含未来步骤与历史步骤）；
* `actionable = completed || 前置步骤已完成`，且**本步不是 `blocked`**；
* `blocked_reasons`：不可执行的原因，逐条可读（如「请先完成 3.3 组装工艺确认」）；
* `missing_requirements`：本步缺的字段/信息（可带 waiver 的写在 `waiver` 里，不算 blocker）；
* `stale`：本步完成后，上游输入又变了（例如成本已确认后又改了零件尺寸）；
* `required_role`：执行本步主按钮所需的角色/能力；
* `primary_action`：本步唯一主按钮的**动作名**（与看板注册的动作名一致，没有则空串）；
* `next_stage`：本步完成后的下一个子步骤 `key`（最后一步为空）。

## 6. 接口与数据契约

* 新增 `GET /api/projects/{project_id}/workflow/projection`（只读、需登录、不写库）。
  既有 `GET /api/projects/{project_id}/workflow` **保持原样**（向后兼容，不改字段、不删字段）。
* 读不到数据（项目不存在）→ 404；能读到但内部取数失败 → 200 + `refresh_ok:false`
  + `stages` 里保留上一次可计算的状态（拿不到就 `not_started`）+ 顶层 `refresh_error`。
* 投影必须**幂等**：同一项目连续两次请求返回同样的 `stages`（除 `generated_at`）。
* 权限：任何已登录用户都能取投影（它只描述状态与门禁原因），
  但 `required_role` 与 `actionable` 按当前用户计算 —— `actionable=false` 且原因是权限时，
  `blocked_reasons` 必须写明「需要 X 角色」。

## 7. 正常路径

1. 用户打开项目：前端取一次投影；顶部流程条按 `phases` 显示 5 个阶段与完成态；
   卡片标题行按 `stages` 显示当前子步骤的状态点。
2. 用户点未来子步骤：能进入（`viewable`），页面顶部提示「本步还不能执行：<blocked_reasons>」。
3. 用户完成某步后前端只重新取投影，不自己算完成态。

## 8. 异常路径

* 投影接口 5xx / 网络失败：**保留上一次状态**，显示「刷新失败，以下为上次读取到的状态」，
  不清空、不显示成未完成；下一次成功请求恢复正常。
* 项目不存在：404，页面按「没有项目」处理（不创建匿名项目）。
* 某个 stage 的数据损坏（例如报告 JSON 读不出来）：该 stage 记 `blocked` +
  `blocked_reasons` 写明原因，其余 stage 照常返回，不得整份投影失败。

## 9. 并发与幂等

* 投影是只读计算；同一项目重复请求幂等（除时间戳）。
* 状态判定不得依赖「上一次前端缓存」，全部来自后端当前数据。

## 10. 刷新、重试、重复点击和服务重启后的行为

* 刷新页面 / 重试：重新取投影；失败时按第 8 节保留旧值。
* 服务重启：投影是纯计算，无状态，重启后结果一致。
* 重复点击主按钮：可用性由 `actionable` 决定，执行中的按钮禁用仍由各页既有 `busy` 逻辑负责
  （本批不改各页 busy 语义）。

## 11. 权限边界

* 权限只影响 `actionable` 与 `blocked_reasons`，**不影响** `viewable`；
* 阶段 4 仍是财务经理的步骤，1.3/5.2 仍由有权限的人提交；编号与状态机不改变权限判定；
* 「仍要继续」类豁免（waiver）只能让 L2 缺口不再拦同一批，不能让 1.3/5.2 的审核动作越权。

## 12. 历史数据兼容

* 不改任何业务文档结构、不迁移数据；投影是**派生视图**；
* 老项目（没有 3.x/4.x 结果、没有 waiver）必须能取出投影：缺的部分是 `not_started`，
  不是 500；
* 报告已发布但从未回传报价的老项目：5.3 是「已发布、未回传」，不得显示成完成。

## 13. 非目标

* 不改既有 `/api/projects/{id}/workflow` 的字段与语义；不删任何前端启发式以外的既有入口。
* 不改批次 1（项目身份）、批次 2（任务并存/原子领取）、批次 3（回传闭环）、
  批次 4（主数据写入幂等）的契约。
* 不改任何页面的权限实现；不改动作名与看板协议；不做视觉重设计。
* 本批不实现「缺口直达链接」等 UI 增强（后续批）。

## 14. 可自动化验收标准

1. 投影接口存在，`phases` 5 条、`stages` 13 条，字段齐全，`status` 取值在枚举内。
2. 每个子步骤的正/反完成条件各有用例（见第 5 节表格）。
3. 关键反例：草稿不算 1.1 完成；只生成工序不算 3.3 完成；成本未确认不算 4.3 完成；
   已发布未回传不算 5.3 完成。
4. `viewable` 恒真；前置不满足时 `actionable=false` 且 `blocked_reasons` 非空。
5. 权限不足时 `actionable=false` 且原因写明所需角色；`viewable` 仍为真。
6. 同一项目连续两次请求 `stages` 一致（幂等）。
7. 前端有纯函数 `TechWorkflowProjection.progress(projection)`，`refreshProgress()` 调用它
   且不再自己 `done.add(...)`；失败时保留上一次状态并置刷新失败提示。
8. 老项目（无 3.x/4.x 结果）能取到投影，不 500。

## 15. 人工验收场景

1. 新建项目、只保存草稿：顶部 1.1 不得显示完成；1.2 不可执行，提示缺「提交确认」。
2. 图纸解析出 0 个零件：2.1 不得显示完成，提示「需人工确认无零件结果」。
3. 只生成组装工序、未确认参数与工艺：3.3 不得显示完成。
4. 成本算过但没确认：4.3 不得显示完成。
5. 报告已发布、未回传报价：5.3 显示「已发布」，不显示完成；回传后变完成。
6. 断网/接口 500 时刷新：页面保留上次状态并显示「刷新失败」，不清空。
7. 点开未来步骤：能看，但主按钮不可点，且给出原因与所需角色。

## 16. 不允许减少的既有能力

* 既有 `/workflow` 接口、既有前端进度展示、既有各页主按钮与 busy 语义。
* 每个 stage 独立的 `page_context` 与「查不到不退回」的既有约定。
* 权限门禁强度（含 1.3 / 5.2 的审核权限、阶段 4 的财务归属）。
* 既有阶段跳转、URL 恢复、iframe 嵌入与看板协议。
