# 2.2 参数推荐：一键生成即补全必填 + 缺项的软闸门 + 这一步归工艺经理

## 背景与用户反馈

用户在 2.2「参数推荐」页连续提出三件事：

1. 「参数推荐那一页为什么没有实现填入所有必填项？」——一键生成之后，报价必填参数仍然
   有空格，智能补全没有跟着跑。
2. 「这种没填好或者没完成的地方，除了没有权限那种的强制不能操作之外，别的都还是允许
   操作，不要硬阻断，而是提示现在有什么什么没完成，确定要继续吗；确定的话就带着缺少的
   继续。」
3. 「一键生成参数推荐之后应该直接补全。」
4. 「参数推荐设置的只有财务经理能操作，这不对，这一步应该是工艺经理操作，之前这一步放
   错了，改回来之后没改权限。」

## 现状缺口（实测，均在源码里核实）

### A. 补全没有跟着生成跑

- `assembly-integration.js` 的 `aiGenerateParamsFully()` 只在
  `aiRequiredGaps().required_missing > 0` 时才调 `aiParamsAutofill()`，而且只在
  `applied > 0` 时才 `aiParamsFinalize(false)`。
- 真正的入口是 `runIntegration`（开始整合分析）→ `aiRunAll()`：它依次生成
  `params` / `process`，**生成完参数之后没有任何补全步骤**。所以从整合分析走进参数页的
  用户，看到的是满屏「必填未给出」，而「一键生成参数推荐」已经被挤成次按钮 —— 补全
  链路压根没有被触发过。
- `aiRequiredGaps()` 只统计 `required` 字段；字典里的非必填空格子即使模型补得出来也
  不会去补。

### B. 缺项是硬阻断

- `aiConfirmParamsAndNext()` 在 `params_final` 为假时直接返回
  `{ ok:false, error:{ code:'required-missing' } }`，人只能回表格里一格一格填。
- `cost-review.js` 的 `crConfirmBlocker()` 同理，`confirmCostReview.run` 拿到原因就
  直接失败返回。
- 这两处都不是权限问题，而是「这一步还有没完成的项」。用户要求：把没完成的说清楚，
  人确认后带着缺口继续；只有权限类（后端 `_require` 的 403）才是硬阻断。

### C. 权限归属写错

- `tech_app/frontend/cpq-sso.js` 的 `COST_URL_PATTERNS` 里仍然列着
  `/integration/params/(autofill|finalize)`，注释写着「整合参数都随成本搬去了 2.3」。
  那是整合参数搬回 2.2（参数推荐）之前的历史写法。
- 后端是对的：`main.py` 的 `/integration/params`、`/params/autofill`、
  `/params/finalize`、`/integration/process` 全部用 `auth.WRITE_ROLES`
  （`engineer` / `process_manager` / `admin`），只有 `/integration/cost` 与
  `/cost-review/*` 用 `auth.COST_ROLES`（`finance_manager` / `admin`）。
- 于是前端把这两个工艺侧接口当成成本接口放行给财务经理，而对工艺经理的描述又把
  「参数推荐」漏掉，屏幕上就只剩「这一步像是财务的」这一种说法。

## 目标

1. 只要参数表里还有空格子（必填或非必填），生成/整合分析结束后自动跑
   智能补全 + 落库，不需要用户再点第二次。
2. 参数页「确认并进入下一页签」、成本页「确认成本」改成**软闸门**：列出没完成什么，
   人点「继续」就带着缺口走下去；点「取消」才停下。权限类判断保持硬阻断。
3. 前端把 `/integration/params*` 还原成工艺经理（`WRITE_ROLES`）的接口，只读横幅与
   拦截注释的说法与后端一致。

## 允许修改范围

- `tech_app/frontend/assembly-integration.js`
- `tech_app/frontend/cost-review.js`
- `tech_app/frontend/cpq-sso.js`
- 本批新增的 spec / 红测 / changelog

## 禁止事项

- 不改 `cpq:tech-board` 信封、六个 state 事件、`tech:command` 方向与 `projectId` /
  `stage` 校验。
- 不新增后端路由、字段、Agent 工具；不改 `auth.WRITE_ROLES` / `auth.COST_ROLES`
  与任何 `_require`。
- 不改 `aiParamsAutofill()` / `aiParamsFinalize()` / `aiConfirmStep()` / `crConfirmCost()`
  的既有实现与接口调用。
- 不放宽权限：后端 403 仍是权威；前端的只读判断仍是硬阻断。
- 不删任何既有动作注册（`autofillIntegrationParams` / `saveIntegrationParamsFinal` /
  `confirmIntegrationParamsFinal` 继续以 `visible:false` 保留）。
- 不提交、不推送、不建 MR/tag/Release、不部署（本 spec 落地时按当次用户指令执行）。

## 实现要点

### 1. 补全跟着生成跑（`assembly-integration.js`）

- 新增 `aiMissingParamFields()`：读 `aiData.param_checklist.groups[].fields`，返回
  `!filled && !generated` 的全部字段（不只必填）；字典没加载时退回
  `aiRequiredGaps()` 的必填缺口。
- 新增 `aiAutoFillParams()`：`aiParamsAutofill()` → `applied > 0` 时
  `aiParamsFinalize(false)`；返回 `{ applied, unresolved }`。生成链路与整合分析链路
  共用这一份，不写第二套。
- `aiGenerateParamsFully()`：生成后只要 `aiMissingParamFields().length` 不为 0 就调
  `aiAutoFillParams()`（不再只看必填）。
- `aiRunAll()`：生成 `params` 之后、生成 `process` 之前，调一次
  `aiAutoFillParams()`（有空格子时）。这样从「开始整合分析」进来的人也拿到补全结果。

### 2. 软闸门（`assembly-integration.js` / `cost-review.js`）

- 新增 `aiAskProceed(why)`：一个 Promise 化的确认框 —— 把 `why`（缺什么）原样摆在
  框里，按钮「仍要继续」/「取消」；确认 resolve(true)，取消 resolve(false)。
  不使用 `window.confirm`，不新增后端接口，不记住状态。
- `aiConfirmParamsAndNext()`：`aiParamsFinalize(true)` 之后若 `params_final` 仍为假，
  用 `aiAskProceed(...)` 问一次；确认则 `aiParamsFinalize(false)`（把已填的落库）
  再 `aiConfirmStep('params')` 并切页；取消则返回 `required-missing` 的失败。
- `cost-review.js` 新增同样的 `crAskProceed(why)`，`confirmCostReview.run`：
  `crReadOnly()` 为真时仍是硬阻断（权限），其余 `crConfirmBlocker()` 的原因走
  `crAskProceed` 软闸门。

### 3. 权限归属（`cpq-sso.js`）

- `COST_URL_PATTERNS` 删掉 `/integration/params/(autofill|finalize)`，注释改成
  「参数推荐（整合参数）归 2.2 工艺经理」。
- `showReadonlyBar()` 的财务分支把「参数推荐」明确写进工艺经理的步骤里。

## 验收标准

- `python3 -m unittest tests.test_tech_params_autofill_and_soft_gates_red -v` 全绿。
- 回归：`tests.test_integration_params_tab_single_primary_and_auto_fill_red`、
  `tests.test_tech_integration_params_step_ownership_red`、
  `tests.test_cost_review_single_primary_and_drop_run_step_red`、
  `tests.test_tech_board_bridge_protocol_red` 全绿。
- 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 0 失败。
- `node --check` 覆盖 `assembly-integration.js`、`cost-review.js`、`cpq-sso.js`；
  `git diff --check` 通过。
- 浏览器：① 点「开始整合分析」后参数页不再满屏「必填未给出」，模型推得出来的都填好并
  落库；② 还有缺项时点「确认并进入下一页签」弹确认框，点「仍要继续」带着缺口进入
  组装工艺，点「取消」不动；③ 财务经理在 2.2 是只读、在 2.3 可操作；工艺经理在
  2.2（含参数推荐）可操作。
