# Spec：1.2 确认 / 1.3 审核的主按钮命名与「意见非必填」

## 背景（用户要求）

- 确认需求的主按钮是「通过确认」，并且**不强制必须要有意见**。
- 审核的主按钮是「提交审核意见」，同样**不强制必须要有审核意见**。

现状两个页面都用「意见为空」当硬闸门：

- `requirement-confirm-page.js` `cfAct()`：`comment` 为空直接 `cfToast('请填写提交意见。')`
  并返回 `{ok:false, error:{code:'missing-comment'}}`，所以左侧主按钮「通过确认」
  在不填意见时根本走不到后端。
- 页内提示还写着「* 必填，最多可输入 3000 字」，与「非必填」口径相反。
- `requirement-review-page.js` `rrSubmit()`：`decision === 'reject'` 且意见为空时
  同样硬拦（`missing-comment` / `请填写审核说明。`）；看板动作
  `submitRequirementReview` 里还有一份重复的同款闸门。
- 页内主按钮文案是「➤ 提交」，与左侧主按钮「提交审核意见」不一致。

后端本来就允许空意见：`WorkflowAction.comment: str = ""`
（`tech_app/backend/models/workflow.py`），`confirm_requirement` / `review_requirement`
都不校验意见是否为空。所以本批只对齐前端，不放开任何真实审批约束。

## 范围

- `tech_app/frontend/requirement-confirm-page.js`
- `tech_app/frontend/requirement-review-page.js`
- 不动：后端路由与 service（`/requirement/confirm`、`/requirement/return-to-draft`、
  `/requirement/review` 原样保留）、状态机闸门（`pending_confirmation` /
  `pending_review`）、角色与留痕（`store.audit` 仍写）、看板动作注册名
  （`confirmRequirement` / `returnRequirementDraft` / `submitRequirementReview`）、
  Agent 工具与桥协议。

## R1 确认需求（1.2）

- R1.1 `cfAct('confirm')` 在意见为空时**不再拦截**：不得出现 `missing-comment` 与
  `请填写提交意见。`；空意见按 `comment: ""` 提交给既有接口。
- R1.2 页内提示不再写「必填」，改为「选填，最多可输入 3000 字」。
- R1.3 页内主按钮文案与左侧主按钮统一为「通过确认」（保留 `id="confirmPass"`），
  「× 驳回」保持次按钮不变。
- R1.4 状态闸门保留：`cfRequirement.status !== 'pending_confirmation'` 仍返回失败。

## R2 审核需求（1.3）

- R2.1 `rrSubmit()` 不再因意见为空拦截（删除 `missing-comment` 与 `请填写审核说明。`）；
  仍是「选中 approve → 提交审核通过、选中 reject → 提交审核退回」，`comment` 允许为空串。
- R2.2 看板动作 `submitRequirementReview` 删除重复的意见闸门；保留 `busy`、
  `no-selection`（未选审核结果）与 `invalid-status`（非 `pending_review`）闸门，
  以及 `#submitReview` 的 enabled 判定。
- R2.3 驳回提示不再写「必填」；审核结论仍由单选项决定（`decision ∈ {approve, reject}`）。
- R2.4 页内主按钮文案与左侧主按钮统一为「提交审核意见」（保留 `id="submitReview"`）。

## R3 能力不缩水

- 确认页仍调用 `/requirement/confirm` 与 `/requirement/return-to-draft`，并在 body 里
  带 `comment`。
- 审核页仍调用 `/requirement/review`，并在 body 里带 `decision` 与 `comment`。
- 页面重绘后的状态上报（`cfPublishTaskEvent` / `rrPublishTaskEvent`）、留痕渲染
  （`rrHistoryHtml`）、带入意见动作（`applyConfirmationNote` / `applyReviewNote`）不变。
- 后端 `review_requirement` 仍校验决策合法性与角色，`confirm_requirement` 仍校验角色；
  两者都仍写审计留痕。

## 验收

- 红测：`tests/test_tech_confirm_review_optional_note_red.py`（先红后绿）。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败。
- 浏览器：1.2 不填意见点「通过确认」→ 直接进入 1.3；1.3 选「通过」不填意见点
  「提交审核意见」→ 直接提交；选「驳回」不填意见也可提交（意见变为选填留痕）。
