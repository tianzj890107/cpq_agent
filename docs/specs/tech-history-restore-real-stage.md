# Spec：历史记录 / 首页卡片恢复项目时必须落到真实当前阶段

状态：Spec + 红测（已实现）
红测：`tests/test_tech_history_restore_real_stage_red.py`

## 背景（用户反馈的高风险缺陷）

`tech_app/frontend/tech-workbench.js:1144` 的 `techStageFromProject()` 只判断「需求状态 →
是否有报告 → 是否有 IR」，只要有 IR、还没出报告就一律返回 `process`（2.2）。它没有判断：

- 2.2 参数是否确认（`plan.params_confirmed`）、工艺是否确认（`plan.process_confirmed`）；
- 是否已发送财务（`plan.finance_handoff`）；
- 2.3 是否已有成本（逐件成本 / `ready`）、成本是否已确认（`cost_review.confirmed`）；
- 是否已有「退回工艺经理复核」的记录（`cost_review.actions[].kind === 'return-to-process'`）。

后果：实际做到 2.3（甚至财务已经测完成本）的项目，从历史记录重新打开会退回 2.2 组装与
整合；财务经理重新打开自己的项目也进 2.2 而不是 2.3。

同一条判定在仓库里还有**第二份现役拷贝**：`报价首页.html` 的 `techStageFromFlow()`
（首页项目清单 / 卡片点开时用它拼 `stage=`，见 `openTechProject()`），缺陷与后果一致。
（`tech_app/frontend/home.js` 的 `homeCurrentTarget()` 是第三份，但 `home.html` 已不再加载
`home.js`，属停用代码，本批不动。）

## 范围

- 新增共享纯函数文件 `tech_app/frontend/tech-stage-restore.js`，导出
  `window.TechStageRestore.fromSignals(flow, projectData, signals)`，返回 9 个 stage id 之一。
- 两个现役入口改为委托它，自己不再保留判定分支：
  `tech_app/frontend/tech-workbench.js`（`techStageFromProject()` + `techHistoryRestore()`）、
  `报价首页.html`（`techStageFromFlow()` + `openTechProject()`）；两页都要引入新脚本。
- 不改：`tech-board-bridge.js` / `tech-board-runtime.js` 的 stage 白名单与协议、
  各 stage 页面本身、任务类型分支（`tech_new_product` / `tech_cost` / `tech_cost_return`
  的既有落点）、后端任何路由与数据。

## 契约（共享判定，唯一一份）

```
TechStageRestore.fromSignals(flow, projectData, signals) -> stageId
  flow        /api/projects/{id}/workflow 原文（requirement / report）
  projectData /api/projects/{id} 原文（ir / meta.has_ir / stages.parsed / has_ir）
  signals     {
                integration: {},   // /api/projects/{id}/summary → aggregate.steps.integration
                cost_review: {},   // 同上 → aggregate.steps.cost_review（2.3 评审状态）
                cost_detail: {},   // /api/projects/{id}/cost-review 原文（ready / parts[].has_cost）
              }                     // 任一取不到时传 {}，函数必须容错
```

判定顺序（固定，不许各调用点自行改写）：

1. `status ∈ {draft, rejected}` → `requirement-create`
2. `status === 'pending_confirmation'` → `requirement-confirm`
3. `status === 'pending_review'` → `requirement-review`
4. 有报告：`in_review` → `report-review`；`approved` / `published` → `report-publish`；其余 → `summary`
5. 无需求状态（`status` 为空）且无 IR → `requirement-create`（没有需求单也没有解析结果的老项目）
6. `cost_review.actions` 含 `kind === 'return-to-process'` → `summary`
   （报价侧同义任务的既有落点就是第 5 大步，两处必须一致）
7. 2.3 已开始 → `cost`：`cost_review.confirmed` / `cost_detail.ready` 为真、
   `cost_detail.parts[]` 有 `has_cost`、`integration.finance_handoff` 非空、
   或 `cost_review` 有实质内容（`confirmed_by` / `confirmed_at` / `received_at` / `note` /
   `actions` 任一非空）
   —— 注意 **`integration.cost.items`（2.2 的整机成本）不算 2.3 已开始**。
8. 有 IR（`ir.parts` 非空 / `meta.has_ir` / `meta.stages.parsed` / `has_ir` /
   `stages.parsed`）→ `process`
9. 否则 → `drawing`

返回的 id 必须落在既有白名单内：
`requirement-create / requirement-confirm / requirement-review / drawing / process /
cost / summary / report-review / report-publish`。

约束：`tech-stage-restore.js` 必须是**纯函数模块**（不碰 DOM、不发请求、只用入参），
两个调用点各自负责把 `/workflow`、`/summary`、`/cost-review` 取回来传进去
（三个都是既有只读接口；`/cost-review` 的 GET 无角色限制）。

## 验收要求

- **R1 唯一的判定实现**：`tech-workbench.js` 与 `报价首页.html` 里都不得再出现
  「`hasIr ? 'process' : 'drawing'`」这类自建分支，两个入口都必须调用共享函数。
- **R2 行为正确**：判定矩阵（见红测）逐例通过，特别是
  「工艺已确认 + 已发送财务」→ `cost`、「2.3 已确认 / 已有逐件成本」→ `cost`、
  「已退回工艺经理」→ `summary`、「只有 2.2 整机成本」→ 仍是 `process`。
- **R3 取数真实**：两个入口都要把 `/summary`（必要时 `/cost-review`）的原文交给共享函数，
  不再只凭 IR 猜阶段。
- **R4 不缩水**：9 个 stage id 与白名单不变；任务类型分支不变；`applyStage()` /
  `techWorkbenchUrl()` 等既有落点方式不变；报告与需求相关分支行为与今天一致。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_history_restore_real_stage_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`
- 语法：`node --check tech_app/frontend/tech-stage-restore.js`、
  `node --check tech_app/frontend/tech-workbench.js`
