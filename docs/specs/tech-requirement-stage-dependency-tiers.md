# Spec：需求阶段（1.1 → 1.2 → 1.3）依赖分级与「带缺口继续」的缺口记录

状态：Spec + 红测（已实现）
红测：`tests/test_tech_requirement_stage_waiver_red.py`

## 背景（用户反馈）

用户对全流程依赖分级的原话（见 `tech-dependency-tiers-and-step-waivers.md` 的统一口径）：

> 这种没填好或者没完成的地方，类似的地方，除了没有权限那种的强制不能操作之外，
> 别的都还是允许操作，不要硬阻断，而是提示现在有什么什么没完成，确定要继续吗？
> 确定的话就带着缺少的继续。

2.2 → 2.3 的出口已经在上一批（`## 68`）落地了豁免；需求三段仍是各写一套，且 1.1 是**真硬闸门**：

| 环节 | 现状 | 问题 |
| --- | --- | --- |
| 1.1 创建 → 1.2 确认 | `requirement-create.js` 的 `rcPersist(true)`：星号字段没填全直接 `rcToast(...) + return`，**什么都不保存、什么都不发送** | 硬阻断；用户没有「带缺口继续」的出口 |
| 1.2 确认 → 1.3 审核 | `requirement-confirm-page.js` 的 `cfAct('confirm')` 直接把 `/requirement/confirm` 打出去，**不看 `cfPrecheck`** | 不拦也不记：缺口没有留痕，审核人看不到「确认人签过哪几个缺口」 |
| 1.3 审核 → 2.1 解析 | `requirement-review-page.js` 的 `rrSubmit()` 只看 `pending_review` 与审核结论 | 业务批准与「技术上能不能解析」没有分开记，缺口同样无留痕 |

后端 `services/requirement_service.py` 三处流转（`submit_requirement_confirmation` /
`confirm_requirement` / `review_requirement`）都没有 `waiver` 入口，`RequirementDoc`
也没有任何缺口字段 —— 前端即便想签，也无处可落。

## 本批范围（只做 1.1 / 1.2 / 1.3 三个出口）

### 依赖分级（沿用统一口径）

| 依赖 | 级别 | 本批之后 |
| --- | --- | --- |
| 没有可保存的需求单（`PUT /requirement` 失败） | L1 | 仍然拦（保存本身就是生成动作） |
| 需求名称 / 需求描述为空 | L1 | 仍然拦（保存动作的最低输入） |
| 页面星号字段 / 完整性检查缺口 | **L2** | **不硬阻断**：弹「仍要继续」，点继续就带着缺口提交，缺口 + 签字落库 |
| 待澄清问题（`need_info` 条目）未全部处理 | **L2** | 同上 |
| 用户没有明确点「通过确认 / 提交审核意见」 | L4 | 仍然拦（人工审批不可被默认通过） |
| 状态机前置（`pending_confirmation` / `pending_review`） | L1 | 仍然拦 |
| 角色权限（1.1/1.2 需工艺技术经理，1.3 需工艺技术总监） | L4 | **不可豁免**，路由 `_require(...)` 不动 |

硬规则：

- 三个出口**都不新增 409 硬门禁**：缺口只负责「提示 + 记录」，人点了继续就继续；
- 记录必须可追溯：缺口字段（编码 + 中文名）、签字人、签字时间、原因说明；
- **同一批缺口已经签过字，后续出口不再重复索要签字**（`reused=True` 追加一条即可）；
- 出**新缺口**要重新签字（比对键是缺口编码集合）；
- 权限、人工审批点击、状态机前置一律不可豁免。

### 一、后端：缺口与豁免记录

`tech_app/backend/models/workflow.py`：

```
class RequirementWaiver(BaseModel):
    stage: str                          # 'submit_confirmation' | 'confirm' | 'review'
    missing_keys: StrList = []          # 缺口字段编码；同一批缺口的比对键
    missing_fields: StrList = []        # 缺口字段中文名，给人看
    reason: str = ""                    # 允许为空，服务端补默认原因
    waived_by: Optional[str] = None
    waived_at: Optional[str] = None
    reused: bool = False                # 本次是复用已有签字，而不是重新签一次
```

`RequirementDoc` 增加 `waivers: List[RequirementWaiver] = Field(default_factory=list)`；
`save_requirement_draft()` 必须像 `history` 一样从旧文档继承 `waivers`（否则前端 PUT 一次
整份表单就把签名抹掉）。

`tech_app/backend/services/requirement_service.py`（唯一实现，路由与 Agent 共用）：

```
requirement_gaps(project_id, doc) -> {"keys": [...], "labels": [...], "items": [...]}
record_requirement_waiver(project_id, doc, stage, *, gaps=None, reason, actor) -> RequirementWaiver
requirement_waiver_covers(doc, keys) -> Optional[RequirementWaiver]
```

- `requirement_gaps()` 直接复用 `requirement_precheck()` 的 `need_info` 结果，不另写一套
  完整性算法；返回值里的 `keys` / `labels` 是稳定的比对键与中文名。
- `record_requirement_waiver()` 写入 `waived_by`（真实姓名优先）与 `waived_at`；`reason` 为空
  时补默认原因，形如「带缺口继续：<中文缺口>，已在 <stage> 由本人签字放行」。
- 三个流转函数各加可选 `waiver` 形参（默认 `None`，向后兼容）：

| 函数 | 传了 waiver | 没传 waiver |
| --- | --- | --- |
| `submit_requirement_confirmation(..., waiver=None)` | 按服务端算出的缺口落一条 `stage='submit_confirmation'`；`store.audit("workflow:requirement_submitted_waived")` | 行为与今天一致（不拦、不记） |
| `confirm_requirement(..., waiver=None)` | 落一条 `stage='confirm'`；缺口已被上一条签字覆盖时改为追加 `reused=True` 的记录；`store.audit("workflow:requirement_confirmed_waived")` | 行为与今天一致 |
| `review_requirement(..., waiver=None)` | 落一条 `stage='review'`；同样支持复用；`store.audit("workflow:requirement_reviewed_waived")` | 行为与今天一致 |

### 二、后端：`/requirement/precheck` 暴露结构化缺口

`requirement_precheck()` 的返回值**只增不改**：新增

```
"gaps": {"keys": [...], "labels": [...], "count": N}
```

`items` / `ok` / `generated_note` / `engine` 一个字段都不删（确认页已有渲染依赖它们）。

### 三、路由：`WorkflowAction` 增加可选 `waiver`

`tech_app/backend/main.py` 的 `WorkflowAction` 增加 `waiver: Optional[dict] = None`，并把
`body.waiver` 透传给三个既有路由（`/requirement/submit-confirmation`、`/requirement/confirm`、
`/requirement/review`）。**不新增任何路由**，`_require(...)` 的角色校验原样保留。

### 四、前端：三个出口都改成「提示 + 带着缺口继续」

- `requirement-create.js`（1.1）：`rcPersist(true)` 里星号字段没填全时，不再直接 `return`；
  改为弹「仍要继续」对话框（缺口逐项列出）——
  - 取消：`rcFocusFirstRequiredField()` 定位首个缺口，不保存、不发送（与今天一致）；
  - 继续：照常保存并提交，把 `{reason, missing_fields}` 作为 `waiver` 放进
    `/requirement/submit-confirmation` 的请求体；
  - 保留 `rcRequiredFieldEntries` / `rcRequiredFieldErrors` / `rcFocusFirstRequiredField`。
- `requirement-confirm-page.js`（1.2）：`cfAct('confirm')` 在发送前读 `cfPrecheck.gaps`
  （新增字段），有缺口且未被既有签字覆盖时弹「仍要继续」；点继续把 `waiver` 放进
  `/requirement/confirm` 请求体；点取消返回结构化失败、不发送。退回草稿不弹。
- `requirement-review-page.js`（1.3）：`rrStart()` 并发读一次 `/requirement/precheck`
  存进 `rrGaps`；`rrSubmit()` 在 `decision === 'approve'` 且存在缺口时弹「仍要继续」，
  点继续把 `waiver` 放进 `/requirement/review` 请求体。驳回不弹。

三处都**不新增接口、不新增第二份缺口实现**：缺口一律来自后端 `/requirement/precheck`，
签字一律走既有三条流转路由。

## 明确不在本批范围

- 2.1 / 2.3 / 3.1 / 3.2 / 3.3 的依赖分级落地（延续上一批的统一口径，后续批次做）。
- 任何新的硬门禁：本批只做「提示 + 记录 + 复用」，不放宽 1.3 的人工结论与角色权限。
- `requirement_precheck()` 的完整性算法本身：一个字不改，只在结果里多带一份结构化缺口。

## 验收要求

- **R1 模型**：`RequirementWaiver` 存在且字段齐全；`RequirementDoc.waivers` 默认空列表。
- **R2 签名落库**：1.1 带 `waiver` 提交 → `doc.waivers[-1]` 的 `stage='submit_confirmation'`，
  `missing_keys` 与服务端算出的缺口一致，`waived_by`/`waived_at` 非空，`reason` 非空，
  审计含 `workflow:requirement_submitted_waived`。
- **R3 不硬阻断**：不传 `waiver` 时三个流转**行为与今天一致**（不抛错、不新增记录）。
- **R4 1.2 记录**：1.2 带 `waiver` 确认 → 状态进 `pending_review`，多一条 `stage='confirm'`，
  审计含 `workflow:requirement_confirmed_waived`。
- **R5 同一缺口复用**：1.2 的缺口与 1.1 签字完全一致时，1.2 追加的记录 `reused=True`。
- **R6 新缺口重新签**：字段补齐后缺口集变化时，记录如实反映当下缺口，不复用旧签字。
- **R7 1.3 记录**：1.3 带 `waiver` 通过 → 状态 `approved`，多一条 `stage='review'`。
- **R8 预检只增不改**：`items` / `ok` / `generated_note` / `engine` 保留，新增 `gaps`。
- **R9 前端**：1.1 不再直接硬 `return`，有「仍要继续」与 `waiver` 请求体；1.2 / 1.3 在
  确认 / 通过前弹「仍要继续」并把 `waiver` 交给既有路由；取消时不发送。
- **R10 不缩水**：既有路由集合、`_require` 角色、Agent 工具名与映射、
  `rcRequiredFieldEntries` / `cfAct` / `rrSubmit` 等函数名全部保留。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_requirement_stage_waiver_red -v`
  （后端行为用带 pydantic 的解释器子进程真跑；本机为 `open-claude/.venv/bin/python`，
  无可用解释器时该项自动跳过并在报告里说明）
- 相关回归：`python3 -m unittest tests.test_tech_confirm_review_optional_note_red
  tests.test_tech_requirement_confirm_red tests.test_tech_requirement_review_red
  tests.test_tech_requirement_agent_red tests.test_tech_integration_dependency_waiver_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`
