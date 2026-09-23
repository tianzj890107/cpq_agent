# 1.1/1.2 的「顺序陷阱」：进路要挡住"图纸还没解析"，退路（退回草稿）要一直走得通

血缘：`packaging-drawing-flow`（八步）第 8 步「字段写入」+ `e2e-packaging-dwg-quote-tech-continuity.md` §4.4、
`packaging-manual-field-confirmation.md`（人工确认口径）。本层补**一道顺序门禁 + 一个退路**。

- 状态：Spec + 红测（已实现）（后端 `assert_requirement_drawing_parsed(drawing_parse_prerequisite(...))` 挂在
  1.1/1.2 两处；前端 `CF_RETURNABLE_STATUSES` / `RR_RETURNABLE_STATUSES` 退路已落地）
- 红测：`tests/test_packaging_requirement_confirm_order_guard_red.py`（13 条，当前 13 OK）

## 0. 一句话目标

今天「图纸解析」的第 8 步（字段写入，把图纸里读出来的字段写回 1.1 草稿）**要求需求单处于可编辑草稿**；
而 1.1 提交确认 / 1.2 确认**不检查**图纸有没有解析。于是先走需求流程的人一定会踩：

```
先 submit-confirmation / confirm / review  →  再跑 drawing-flow
   → 第 8 步 field_write 必 blocked（REQUIREMENT_NOT_EDITABLE）
   → 提示"请先退回草稿"  →  退回草稿 → 重跑八步 → 又要重新提交/确认/审核
```

这段提示里"请先退回草稿"在**前端**其实是走不通的（§1.2 第二条）：只要需求已经是 `approved`，
1.2 的「驳回」与 1.3 的「驳回」都会被页面自己的状态前置挡掉，能调
`POST /requirement/return-to-draft` 的入口一个都不剩 —— 只点按钮的人会被永久卡在第 8 步。

## 1. 线上证据（34，2026-09-22，Codex 自建需求：项目 `7267eff7d68a` / 会话 `e59e1b382478`）

- 第一次（先确认后跑图）：`drawing-flow/run` → **7/8 completed**，第 8 步
  `field_write` `status=blocked`、`error_code=REQUIREMENT_NOT_EDITABLE`、
  `error_message=需求已提交，不能直接修改；请先退回后再编辑`；
  `GET /drawing-flow` 的 `preconditions` 里也写着同一句（`severity=blocking`）。
- 按提示 `POST /requirement/return-to-draft`（approved → draft）后重跑：**8/8 completed**
  （`file_preflight / dwg_convert / cad_ir_parse / packaging_semantics / parts_extract /
  field_write / pending_confirm / downstream_prepare`），零件 64 件。
- 第二次（人工确认材料后再确认，再重跑图）：又是 **7/8**，同一个 `field_write` 又 blocked —— 这个坑
  **每次改完需求再重跑图纸解析都会踩一遍**，不是一次性事故。
- 代码事实：`requirement_service.drawing_parse_prerequisite()`（判据=有没有零件）**只被 1.3 审核用**
  （`review_requirement` 里 `required and not done` → 409 `REQUIREMENT_DRAWING_NOT_PARSED`，
  带 waiver 才放行）；`submit_requirement_confirmation()`（1.1）与 `confirm_requirement()`（1.2）
  里**一次都没有**调用它。

### 1.2 退路缺口：`approved` 之后前端没有任何按钮能退回草稿（同一次 34 实测）

- 后端支持退回：`requirement_service.RETURNABLE_TO_DRAFT_STATUSES = ("pending_confirmation",
  "pending_review", "approved")`，`return_requirement_to_draft()` 对 `approved` 放行（draft 幂等）。
- 前端**唯一**调用 `/requirement/return-to-draft` 的地方是 1.2 页的「× 驳回」
  （`tech_app/frontend/requirement-confirm-page.js`：`cfAct('return')`）；而 `cfAct()` 在分派 kind
  **之前**就写着 `if (cfRequirement.status !== 'pending_confirmation') { cfToast('当前需求尚未提交至确认环节，
  请先返回上一步点击"提交"。', true); return … }` —— `approved` 时按钮**可见、可点、点下去什么都不发生**，
  只弹一句与实际状态不符的提示。
- 1.3 页同样出不去：`requirement-review-page.js` 的 `rrSubmit()` 以
  `rrRequirement.status !== 'pending_review'` 前置拒绝，审核通过后连"驳回"都提交不了。
- 结论：走错顺序（先 1.2/1.3 再 2.1）的人，只能靠**直接调 API** 退回草稿 —— 这正是"只有前端按钮的人
  跑不通"的那一步（08-24 那轮 Codex 就是这么绕过去的）。

## 2. 口径（逐条）

1. 1.1 `submit_requirement_confirmation` 与 1.2 `confirm_requirement` **都必须**先调用同一份
   `drawing_parse_prerequisite(project_id)`（不另写一份判据）。
2. `required=False` 或 `done=True` → 逐字保持今天的行为（不新增任何拦截）。
3. `required=True 且 done=False` → 抛 `RequirementSaveError`，`status_code=409`、
   `code=REQUIREMENT_DRAWING_NOT_PARSED`，消息与 1.3 那条一致（同一句"请先跑「一键解析图纸」再…"），
   **不写任何落盘、不改状态**。
4. 带 **waiver**（`{"reason": "…"}` 非空）时放行，并按既有 1.1/1.2 口径留痕：
   1.1 → `workflow:requirement_submitted_waived`，1.2 → `workflow:requirement_confirmed_waived`；
   缺口本身仍以服务端算出为准，不由 waiver 内容改写。
5. 三个关口（1.1 / 1.2 / 1.3）用的是**同一个** `drawing_parse_prerequisite()`；1.3 现有行为逐字不变。
6. 判据只看"有没有零件"（包装 parts 文档或 legacy IR 有件），**不看**是谁点的按钮，也不看
   `drawing-flow` 的步骤状态表 —— 人工重试、重放都不影响结论。

### 2.2 退路口径（同批）

7. 前端"退回草稿"的可用状态**必须**与后端 `RETURNABLE_TO_DRAFT_STATUSES` 同一份口径
   （`pending_confirmation` / `pending_review` / `approved`），不许在页面上再写一个更窄的状态集。
8. 1.2 的「驳回 / 退回草稿」在 `approved` 时**必须仍然可提交**；把 `pending_confirmation` 当作
   "所有动作"的前置是一刀切，必须按动作分别判（`confirm` 与 `return` 各有自己的前置）。
9. 1.3 页在 `approved` 时必须给出同样的出口（可复用同一份状态集），不许只留一句"当前需求不在待审核状态"。
   两个页面共用的状态集**只落一处**，另一处引用它，不各写一份。
10. 后端一行不改：`return_requirement_to_draft()` 的放行集合、幂等与留痕逐字保持（§1.2 已验证）。

## 3. 允许修改范围

1. `tech_app/backend/services/requirement_service.py`（**唯一落点**：两处调用 + 一处共用校验）
2. `tech_app/frontend/requirement-confirm-page.js`（`cfAct()` 按 kind 分别判前置 + 退路状态集）
3. `tech_app/frontend/requirement-review-page.js`（`approved` 时的退路出口，引用同一份状态集）
4. `changelog/changelog_9_21_25.md`

## 4. 禁止事项

- 不许放宽 1.3 的既有拦截；不许把三处判据拆成三份实现（必须共用 `drawing_parse_prerequisite`）。
- 不许改 `drawing_parse_prerequisite` 的判据（仍以"有没有零件"为准）。
- 不许新增路由、不许动数据库 schema、不许把 waiver 变成"静默放行"（必须留痕）。
- 不许在 `required=False / done=True` 时产生任何新的错误码或提示。
- 退路不许把 `approved` 塞进 `EDITABLE_STATUSES`（那是"编辑"口径，不是"退回"口径）；不许放宽 1.3
  对"非 pending_review 不许 approve/reject"的拒绝；不许让退回动作跳过 `return_requirement_to_draft()`。

## 5. 红测

`tests/test_packaging_requirement_confirm_order_guard_red.py`（实现前必须失败；不连 PG、不发 HTTP）：

- **A 1.1 拦截**：A1 图纸没解析（`drawing_parse_prerequisite` 打桩 `required=True/done=False`）时
  `submit_requirement_confirmation()` 抛 409 `REQUIREMENT_DRAWING_NOT_PARSED`，且**没有**任何落盘；
  A2 图纸已解析（`done=True`）时逐字保持今天的行为（落盘为 `pending_confirmation`）。
- **B 1.2 拦截**：B1 `pending_confirmation` 状态下同样被拦（同一个 code）；B2 `done=True` 时正常进入 `pending_review`。
- **C 共用一份判据**：C1 `submit_requirement_confirmation` 与 `confirm_requirement` 两个函数体里
  都出现 `drawing_parse_prerequisite(`（源码级，防"只修一处"）；C2 带 waiver 时放行且留痕（审计动作名逐字）。
- **D 不回归**：D1 `review_requirement` 里那条 1.3 拦截仍在（源码级）；D2 非包装/非 dwg 项目
  （`required=False`）时 1.1/1.2 一步都不拦。

### 5.2 E 组：退路（§2.2，实现前必须失败）

红基（2026-09-22 在 `fa513e4` 上实跑）：`Ran 13 tests … FAILED (failures=6)`，
红的正是 A1 / B1 / C1 / E1 / E2 / E3；E4 / E5 是护栏（今天就是绿的，不许被改红）。
实现落地后同一份文件 `Ran 13 tests … OK`（见 §8）。

- **E1 状态集唯一且够宽**：`requirement-confirm-page.js` 里声明一个退回可用状态集
  （`CF_RETURNABLE_STATUSES` / `RETURNABLE_STATUSES` 等同一语义的具名常量），字面量同时含
  `'pending_confirmation'`、`'pending_review'`、`'approved'`；且该名字在 `cfAct(` 的函数体里被引用。
- **E2 一刀切必须消失**：`cfAct(` 的函数体里**不得**再出现"以 `pending_confirmation` 挡掉一切"的写法 ——
  即 `status !== 'pending_confirmation'` 必须落在 `kind === 'confirm'` 那一支（或等价的分支判据）之后。
- **E3 1.3 页也有出口**：`requirement-review-page.js` 在 `approved` 时能调用
  `/requirement/return-to-draft`（源码级：该文件出现 `return-to-draft`，且引用同一份退回状态集）。
- **E4 后端保持**（护栏，今天就是绿的）：`return_requirement_to_draft()` 对 `approved` → draft、
  对 `draft` 幂等不写库、对同一份 `RETURNABLE_TO_DRAFT_STATUSES` 之外的状态 409。

## 6. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_requirement_confirm_order_guard_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_manual_field_confirmation_red \
  tests.test_packaging_parse_to_downstream_seams_red tests.test_packaging_parts_downstream_red
```

## 7. 本层不做的

- 不改「字段写入」这一步本身（它仍然只写可编辑草稿）；
- 不做"解析结果先存草稿补丁、等需求解锁再回写"的队列（另立一批）；
- 不重做 1.2/1.3 的整页交互，也不新增"撤回/重开"等第二个动作名 —— 退路仍叫退回草稿；
- 不碰 34 的部署与推送。

## 8. 实现记录（2026-09-22）

两半都按 §2 的口径落地，逐条对着写：

1. 后端：`requirement_service.assert_requirement_drawing_parsed(prerequisite, waiver=…)` 是**唯一**判定处
   （`required=False / done=True` 直接放行；`required=True 且 done=False` 抛 409 +
   `REQUIREMENT_DRAWING_NOT_PARSED`；带 waiver 放行、留痕仍走各关口既有的审计动作）；
   `submit_requirement_confirmation()` 与 `confirm_requirement()` 各自先算
   `drawing_parse_prerequisite(project_id)` 再喂给它 —— 三处（含 1.3）判据同源。
2. 前端：1.2 页 = `CF_RETURNABLE_STATUSES = ['pending_confirmation','pending_review','approved']`
   （与后端 `RETURNABLE_TO_DRAFT_STATUSES` 同口径），`cfAct()` 里确认与退回**分别**判前置；
   1.3 页 = `RR_RETURNABLE_STATUSES`（引用同一份口径）+ `rrCanReturn()`，审核通过之后也能退回草稿。
3. 复验命令与结果：

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_requirement_confirm_order_guard_red -v
# Ran 13 tests … OK
```

4. 34 现场复验（同一天的 `## 313` 全流程真跑）：按"先解析再确认/审核"的正确顺序，
   1.1 提交确认 → 1.2 通过确认 → 1.3 审核通过**三个关口全部 200**，没有被这道新门禁误拦。

## 9. 实现记录（2026-09-22，Codex 实现；changelog `## 289`）

状态：Spec + 红测（已实现）（`tests/test_packaging_requirement_confirm_order_guard_red.py` 的 5 条 ERROR 属打桩元数冲突，见 changelog `## 289`；测试侧待处置）
红测：`tests/test_packaging_requirement_confirm_order_guard_red.py`

原文记的"测试侧待处置"已于 2026-09-22 处置：`blocked_prerequisite()` / `parsed_prerequisite()` 两个
打桩补上 `project_id=""` 形参、`ctx.exception.code` 改读 `stable_error_code`（断言一行未动），
同一份文件 `Ran 13 tests … OK`（见 §8）。

### 9.1 后端（`tech_app/backend/services/requirement_service.py`）

- 新增**共用校验** `assert_requirement_drawing_parsed(prerequisite, *, waiver=None)`：
  `required=False` 或 `done=True` **直接放行**（不新增任何提示/错误码）；`required=True 且 done=False`
  且 waiver 的 `reason` 为空 → `RequirementSaveError(message, 409,
  code=REQUIREMENT_DRAWING_NOT_PARSED)`，`message` 逐字取 `drawing_parse_prerequisite()` 算出的那条
  （与 1.3 同一句）；waiver 的 `reason` 非空才放行（`{"reason": "  "}` 仍被挡），留痕走各关口既有的
  `workflow:requirement_submitted_waived` / `workflow:requirement_confirmed_waived` 审计。
- 1.1 `submit_requirement_confirmation()`、1.2 `confirm_requirement()` 在**状态校验之后、落盘之前**
  各调一次 `drawing_parse_prerequisite(project_id)` 并把结果交给上面那份共用校验 —— 三处关口
  （1.1 / 1.2 / 1.3）判据只有一份。被挡住时在抛之前没有任何 `store.save_requirement`。
- 1.3 `review_requirement()` 逐字未动；`RETURNABLE_TO_DRAFT_STATUSES`、`return_requirement_to_draft()`
  一行未动；未新增路由、未动 schema、未改 `drawing_parse_prerequisite()` 判据。

### 9.2 前端

- `requirement-confirm-page.js`：新增具名常量 `CF_RETURNABLE_STATUSES`（
  `pending_confirmation` / `pending_review` / `approved`，与后端同一份口径）；`cfAct()` 的**一刀切**
  `status !== 'pending_confirmation'` 改成**按动作分别判前置**：`kind === 'confirm'` 判
  `pending_confirmation`，`kind === 'return'` 判 `CF_RETURNABLE_STATUSES` —— `approved` 之后
  「× 驳回」恢复可用（这正是"请先退回草稿"的前端出口）。
- `requirement-review-page.js`：新增 `RR_RETURNABLE_STATUSES`（引用 1.2 页同名常量，取不到时才取
  同一份字面量；状态集只落一处）、`rrReturnToDraft()`（`POST /requirement/return-to-draft`，带
  comment，走既有看板事件）、`rrMountReturnDraft()`（按状态把「× 退回草稿」挂进 `.footer-right`，
  复用本文件既有的 `rrRender` 包装写法，不动渲染模板）。1.3 对"非 `pending_review` 不许
  approve/reject"的拒绝**原样保留**（`rrSubmit()` 未动）。

### 9.3 验证

- `tests.test_packaging_requirement_confirm_order_guard_red`：E1 / E2 / E3 / C1 / D1 / D2 **已转绿**。
  **A1 / A2 / B1 / B2 / C2 仍红**，原因在**测试侧夹具**：同文件里 `blocked_prerequisite()` 与
  `parsed_prerequisite()` 是 **0 参**打桩，而 `D2` 用的是 `lambda pid:`（1 参），
  `drawing_parse_prerequisite(project_id)` 与 `drawing_parse_prerequisite()` 两种调用元数**不存在
  同时满足两者的写法**（`mock.patch.object` 传函数时不做 autospec，元数即调用元数）。
  修法（**两处一行**，不改任何断言）：给这两个 helper 补上 `project_id` 形参。
  按"不许改红测"的纪律未自行修改，只在这里与 changelog 记录。
- **生产实现独立复算**：用形参与真实签名一致的打桩重跑 A1/A2/B1/B2/C2 的全部断言
  （含 409 / `stable_error_code` / 未落盘 / 解析完成行为不变 / waiver 留痕 / 空 reason 仍挡）→
  **14/14 PASS**。
- 回归（全绿）：`packaging_manual_field_confirmation` / `packaging_parse_to_downstream_seams` /
  `packaging_parts_downstream` / `e2e_packaging_dwg_continuity` /
  `tech_requirement_agent|confirm|review|stage_waiver` / `drawing_flow_requirement_state` /
  `packaging_quote_draft_and_card_visibility`，以及引用这两个页面的 12 套源代码级红测；
  `packaging_drawing_flow_red` 只剩**既有红** `CGates::test_c8`（Spec `packaging-manual-field-
  confirmation.md` §2.4 已记）。
- `node --check` 两个页面脚本均通过。
