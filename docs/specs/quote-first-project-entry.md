# 规格：项目必须从报价开始（统一入口 / 落点分种类 / 恢复通道）

批次：新五批（上线闭环）**第 3 批**。红测：`tests/test_quote_first_project_entry_red.py`。

前置（已完成，不在本批范围）：批次 6 的 `cpq_case_link`（`decide` / `resolve` / `CaseLinkError`）、
`store.load_business_case` / `save_business_case`、`cpq_tech_bridge.send_to_quote` 的事务 + 幂等五元组、
`/wf/tech/handoff` 对 `business_case_id` / `create_new` / `create_reason` 的原样透传、
`tech-task.js` 的报价来源写入、`cpq_kb` 知识库（第 2 批）。

**本批目标**：把「项目必须从报价开始」变成可判定、可恢复的两件事 ——
①技术项目**建项时**就带上报价来源（实例号 / 会话 / 来源任务），入口分「正式」与「内部测试」两种并留痕；
②一旦来源缺失，4→5 与 5.3 两条回传不再把用户堵在一句无法执行的文案上，而是给出**结构化的恢复出口**
（列候选让人选 / 明确新建并写原因）。

---

状态：Spec + 红测（已实现）
红测：`tests/test_quote_first_project_entry_red.py`

## 1. 现状取证（实测）

1. **现场 P0：独立技术项目卡死在 5.3**。服务器实测（项目 `96a959b0264c`，报告已发布）回传销售报
   「没有找到这条业务实例对应的报价卡片。确认要新建报价卡片时，请填写新建原因后重试。」
   请求模型 `ReportQuoteAction`（`tech_app/backend/main.py:148`）只有
   `note` / `target_type` / `target_role_code` / `target_user_id` / `source_task_id` ——
   **没有** `create_new` / `create_reason`；发布页也没有这两个交互，用户无处填写，只能反复点重试。
2. **根因在桥接层丢了结构化字段**（本次实测）：
   - `cpq_suite_server.py:763` 已经用 409 回 `{"code": ..., "candidates": [...], "error": ...}`；
   - `tech_app/backend/services/cpq_bridge.py:44` 的 `_post` 把 409 收敛成
     `BridgeRejected(str(message))` —— **只剩一句文案，`code` 与 `candidates` 全丢**；
   - 三处翻译口径一致地把 `BridgeRejected` 变成 **400 + 纯字符串**：
     `main.py:3385`（`_bridge_call`）、`cost_flow.py:50`（`bridge_call`）、
     `report_workflow.py:796`（`_bridge_call`）；
   - 前端 `tech_app/frontend/workflow.js:30` 的 `apiError` **只返回字符串**，`api()` 抛的是
     `new Error(...)` → `report-publish-result.js:54` 读的 `error.code` **恒为 undefined**。
   结果：服务端已经判出「无候选 / 多候选」，界面永远只能显示一句「回传失败」。
3. **技术侧独立建项入口没有任何来源门禁**：`POST /api/projects`（`main.py:1208` `upload_project`）
   的表单字段只有 `file` / `files` / `note` / `attachments`；不带任何报价线索也能建项。
   现场那次独立建项就是这么来的（技术首页上传图纸 → 建项目 → 后面必然卡在回传）。
4. **报价 → 技术建项没有带实例号**：`tech-task.js:287` 的 `linkProject` 只写
   `source_task_id` / `source_task_no` / `source_session_id` / `customer_name`
   （`grep -c business_case_id tech_app/frontend/tech-task.js` → **0**）；
   `requirement_service.QUOTE_SOURCE_KEYS` 也只有 5 个键（不含 `business_case_id`）。
   全仓 `save_business_case` 的**生产调用点数为 0**（只有 `store.py` 定义、`tests/` 与
   `scripts/cpq_eval/prodkit.py` 使用）→ 技术项目 meta 里其实从来没写过实例号，
   落点只能靠 `source_session_id` 兜底。
5. **没有历史项目恢复入口**：全仓 `grep -rn "entry_origin\|internal_test\|classify_entry\|quote-link"`
   → 无（`project_access.py:179` 的 `_quote_link_visible` 是只读可见性判断，不是恢复动作）。

---

## 2. 入口分级（统一入口）

**契约**：新增唯一入口判定纯函数 `tech_app/backend/services/entry_origin.py`：

```python
def classify_entry(*, business_case_id: str = "", source_task_id: str = "",
                   source_session_id: str = "", source: str = "") -> dict
```

返回且仅返回这四个键：

| 键 | 取值 | 说明 |
| --- | --- | --- |
| `origin` | `"quote"` \| `"internal_test"` | 有任一报价线索 = `quote`；全空 = `internal_test` |
| `internal_test` | `bool` | 与 `origin` 同步，便于前端直接判断 |
| `clues` | `list[str]` | 非空线索的键名，顺序固定 `business_case_id` / `source_task_id` / `source_session_id` / `source` |
| `reason` | `str` | `internal_test` 时非空（说清"这不是正式入口"），`quote` 时为空串 |

要求：

1. 纯函数：同输入同输出、不改入参、不读库、不读环境变量。
2. `source` 只要非空（例如 `"CPQ 报价 · 新增工艺"`）也算线索 —— 它是 `tech-task.js` 已经在写的溯源键。
3. `main.upload_project` 新增三个可选表单字段 `business_case_id` / `source_task_id` /
   `source_session_id`，响应体必须新增 `entry_origin`（`classify_entry` 的原样返回）。
4. 建项时把入口事实落进项目 meta（`store.save_business_case(project_id, {...}, author=...)`），
   至少含 `entry_origin` / `internal_test` 与收到的线索；**内部测试入口必须留痕**
   （`store.audit(project_id, "project:internal_test_entry", {...})`）。
5. 技术侧界面在 `internal_test` 时必须明确提示"这不是正式入口，正式流程应从报价发起"，
   不允许默默建出一张将来回传不了的项目。

---

## 3. 报价 → 技术建项必须携带实例号

1. `tech-task.js` 的 `linkProject` 写入 `business_case_id`（取自 `task.payload.business_case_id`
   或 `task.payload.quote.business_case_id`），并保留既有四个键不变。
2. `requirement_service.QUOTE_SOURCE_KEYS` 增加 `business_case_id`（**只追加，不改既有 5 个键**）。
3. `POST /api/projects` 收到 `business_case_id` 时，必须通过 `store.save_business_case` 落到项目 meta，
   使 `cost_flow.business_case_of(project_id)` 在第一次回传之前就能读到它（现在只能靠"上一次回传"）。

---

## 4. 落点冲突的结构化出口（桥 + HTTP + 前端）

**契约**：业务冲突的三个字段必须一路活到界面。

1. `cpq_bridge.BridgeRejected` 增加 `code` / `candidates` / `status`（默认 `400`），构造保持向后兼容
   （`BridgeRejected(message)` 仍然可用）。
2. `cpq_bridge._post` 在 409 时把响应体的 `code` / `candidates` 原样带上，`status=409`。
3. 三处桥接翻译（`main._bridge_call`、`cost_flow.bridge_call`、`report_workflow._bridge_call`）
   与两个 FastAPI 出口（`main._cost_flow`、`main._report_flow`）**只在落点冲突时**改用
   结构化 detail + 409：
   `code in ("no_candidate", "multiple_candidates")` →
   `HTTPException(409, detail={"code": code, "candidates": [...], "message": 文案})`；
   其余 `BridgeRejected` 一律保持既有 400 + 纯字符串（**不改其它路由的响应形状**）。
   为此 `CostFlowError` / `ReportWorkflowError` 也要带上 `code` / `candidates`
   （否则 FastAPI 层拿不到这两个字段，4.1–4.3 与 5.3 都弹不出恢复框）。
4. 前端 `workflow.js`：`api()` 抛出的错误对象必须带 `code` / `candidates` / `status`
   （`apiError` 可以继续返回文案，但结构化字段不能被丢掉）。
5. 两个出口都要有恢复交互（见 §5 / §6），文案统一为：
   - 无候选：「没有找到这张报价卡片。可以『选择已有报价卡片』或『新建报价卡片』（必须写原因）。」
   - 多候选：「这条回传命中了多张报价卡片，请先选定要落回的那一张。」

---

## 5. 5.3 回传销售的恢复通道

1. `ReportQuoteAction` 追加 `business_case_id: str = ""` / `create_new: bool = False` /
   `create_reason: str = ""`。
2. `report_workflow.send_to_quote` 追加同名三个关键字参数并**透传**给
   `cpq_bridge.report_handoff(..., create_new=..., create_reason=...)`；
   `business_case_id` 显式传入优先，未传时保持既有口径
   （`cost_flow.business_case_of(project_id)`）。
3. 路由 `POST /api/projects/{project_id}/process-report/send-to-quote` 把 body 三字段原样转发。
4. 返回体与成本侧（`cost_flow`）对齐，补 `business_case_id` / `candidates` / `recovery`
   （`recovery` 四键：`recovered_from_project_id` / `recovery_reason` / `recovered_by` / `recovered_at`），
   前端才能显示"这一版是新建的还是认回的、谁在什么时候恢复的"。
5. `report-publish-result.js`：POST body 带 `business_case_id`；捕获落点冲突时
   - `no_candidate` → 弹「新建报价卡片」确认框（原因必填）→ 带 `create_new=true` + `create_reason` 重试；
   - `multiple_candidates` → 列出候选（会话号 / 标题 / 匹配方式）让人选定后重试；
   - **不许**在用户没确认的情况下自动重试成新建。

---

## 6. 历史项目一次性恢复

**契约**：新增 `POST /api/projects/{project_id}/quote-link/recover`，入参
`business_case_id=""` / `quote_session_id=""` / `source_task_id=""` / `source_session_id=""` /
`create_new=False` / `create_reason=""` / `note=""`：

1. 至少要有一个线索，或 `create_new=true`；`create_new=true` 时 `create_reason` 必须有内容（去空白后非空），
   否则 400 —— 与 `cpq_case_link.decide` 的既有四态语义**完全一致**，不新开一套判定。
2. 动作只写**技术侧事实**：`store.save_business_case(project_id, {...}, author=user)` 合并写入 +
   `store.audit(project_id, "quote_link:recovered", {...})`；**本接口不建、不改任何报价卡片**——
   卡片的新建仍然只能由回传命令带 `create_new` + `create_reason` 完成。
3. 返回体：`business_case_id` / `quote_session_id` / `entry_origin` /
   `recovery{recovered_from_project_id, recovery_reason, recovered_by, recovered_at}`。
4. 幂等：同一份线索重复提交，meta 与审计不产生第二份不同记录（同值重写无副作用）。

---

## 7. 红测

`tests/test_quote_first_project_entry_red.py`，五组共 22 条：

| 组 | 覆盖 |
| --- | --- |
| A（6） | 入口分级：模块/函数存在、有线索=quote、无线索=internal_test、纯净性、`upload_project` 表单字段与 `entry_origin`、内部测试入口留痕 |
| B（3） | 报价→技术建项携带实例号：`tech-task.js` 写 `business_case_id`、`QUOTE_SOURCE_KEYS` 追加、建项落 meta |
| C（6） | 落点冲突结构化出口：`BridgeRejected` 三字段、`_post` 保真、`main._bridge_call` 409+结构化（普通拒绝仍 400 字符串）、前端 `api()` 带 code/candidates/status、成本与报告两条 service 同契约（业务错误带 code/candidates + `_cost_flow`/`_report_flow` 翻 409 结构化）；外加**受控假库护栏**（带线索的 `cost_to_process` 仍落原卡片） |
| D（5） | 5.3 恢复通道：`ReportQuoteAction` 三字段、`send_to_quote` 签名 + 透传、路由转发、返回体对齐、发布页的「新建 + 原因 / 多候选」交互 |
| E（2） | 恢复接口存在 + 入参模型七键 + 只写技术侧事实 |

验证方式：AST / 源码契约断言（后端与前端的接口形状）+ 纯函数行为（`classify_entry`）+
受控假库真跑（`tests/fixtures/wf_handoff_harness.py`，护栏：带线索的 `cost_to_process` 仍落原卡片）。
全部离线：不连 Postgres、不调模型、不起服务、不读写生产数据。

实现前实测（交付当次）：

```
Ran 22 tests  FAILED (failures=21)
```

红点 21 条：A1–A6（`entry_origin.py` 不存在）、B1–B3（`tech-task.js` 不写
`business_case_id`；`QUOTE_SOURCE_KEYS` 5 键；建项不落 meta）、C1–C4 与 C6（`BridgeRejected()`
连关键字参数都不接受、`_post` 丢 409 载荷、`_bridge_call` 恒 400 纯字符串、前端 `api()`
不挂 `code`/`candidates`）、D1–D5（`ReportQuoteAction` 无三字段、`send_to_quote` 不透传、
路由不转发、返回体无 `candidates`/`recovery`、发布页无恢复交互）、E1–E2（无恢复路由）。

绿 1 条护栏：C5 —— 带线索的 `cost_to_process` 目前仍正确落在原报价卡片上（本批只补出口，
不许动这段裁决）。

---

## 8. 禁止事项

- **不改**`cpq_case_link.decide` 的四态语义（`linked` / `multiple_candidates` / `create_new` / `no_candidate`），
  不把 `no_candidate` 改成"静默新建"。
- **不改**`cpq_tech_bridge` 的落点裁决实现与五元组幂等键；本批只在**桥接层与界面层**补出口。
- **不改**其它路由（非落点冲突）现有的 `BridgeRejected → 400 + 字符串` 响应形状。
- **不新建**报价卡片：本批所有恢复动作都只写技术侧关联事实。
- 不绕过角色门禁、不给用户加写权限、不伪造产品/盒型/价格/成本。
- 不装新依赖；不连 34、不写生产库、不重启服务。

---

## 9. 与后续批次的接口

- 第 4 批（包装参数/BOM/工艺/成本闭环）从本批的 `business_case_of(project_id)` 取稳定实例号，
  不再自己猜来源。
- 第 5 批（服务器终验）用本批的 `entry_origin` 作为"项目确实从报价开始"的证据之一，
  并按 §5/§6 的恢复出口做一次真实的「无候选 → 明确新建」演练。
