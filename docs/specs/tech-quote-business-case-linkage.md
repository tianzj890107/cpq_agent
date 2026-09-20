# 报价—技术—财务—报告统一业务实例关联与安全恢复（批次 6）

依赖：批次 3 的原子回传命令（`cpq_tech_bridge.send_to_quote` + `cpq_wf_handoff`）
与批次 5A 的五阶段口径。本批**不改**批次 3 的事务与幂等语义，只改「落点怎么定」。

## 1. 背景与真实问题

今天把一条技术支线认回原报价卡片，靠的是三条**散落的、随时会断的**线索：

* `source_task_id` → 查 `cpq_wf_task` 拿 `card_id`（`cpq_tech_bridge.py:826`，`linked_by='task'`）；
* `source_session_id`（需求单 `data.source_session_id` 或 `plan.quote_handoff.session_id`）
  → 只要报价里恰好有这张卡片就认（`linked_by='session'`）；
* 都没有 → **自动新建**一条真实报价会话（`cpq_tech_bridge.py:834-841`，
  `linked_by='new_session'`，`new_card=True`），只在前端给一句「未认回原报价卡片，已新建一张」。

问题：

1. **静默新建**。任务被取消 / 被顶掉 / 需求单里没记会话号，就会凭空多出一张报价卡片；
   销售那单永远停在第 1 步等新产品，客户信息也只剩技术侧填过的部分。
2. **没有任何稳定的业务实例号**。报价 `session_id`、`card_id`、`task_id`、技术项目号、
   Agent 会话、报告版本各写各的，跨系统只能靠"字段碰巧对上"。
3. **技术项目号被拿来当会话号**。`send_to_quote(session_id=...)` 的 `session_id` 是技术项目号，
   落点却要写报价库，两者混在一处（批次 3 已把 `quote_session_id` 与 `tech_project_id` 拆开，
   但没有一个能跨系统、跨重建的实例号）。
4. **多候选 / 无候选都没有出口**：要么撞上第一张，要么静默新建，用户无法选择。

## 2. 用户角色与用户故事

* 作为销售经理，技术侧回传的结论必须落回**我那张报价卡片**；确实找不到时，系统要让我知道、
  由我（或有权限的人）决定怎么办，不能悄悄多出一张单。
* 作为工艺/财务经理，我回传时不该关心会话号：项目上带的业务实例号是什么，落点就是什么。
* 作为运维/审计，任务被取消、被替换、甚至被删掉之后，我仍要能追到这条业务实例的原始报价。
* 作为使用者，多候选时我要看到候选清单并自己选；没有候选时我要被明确问一句
  「要不要新建报价卡片」，而不是事后发现多了一张。

## 3. 当前流程

1. 技术项目上只零散记着 `source_task_id` / `source_session_id`（需求单 data 里）。
2. 回传时按 `task → session → 新建` 三条线索依次尝试，第一次命中就落点。
3. 三条都不中 → 直接新建会话与卡片，前端提示「已新建一张」。

## 4. 目标流程

引入稳定业务实例号 **`business_case_id`**，全链路携带，落点由它决定：

1. **报价侧**：报价卡片创建时产生或绑定 `business_case_id`（`bc_` + 12 位 hex），
   落在 `cpq_wf_card.business_case_id`，同一业务实例的多张卡片共享它。
2. **技术支线**：发起「新增工艺 / 成本测算」任务时把 `business_case_id` 写进任务 payload。
3. **技术项目元数据**：`store.load_meta(pid)["business_case"]` 持久化
   `{business_case_id, quote_session_id, source_task_id, linked_by, linked_at,
     recovered_from_project_id, recovery_reason, recovered_by, recovered_at}`。
4. **成本 / 报告 / 回传任务**：`integration_quote_result`、回传任务的 payload 都带 `business_case_id`。
5. **Agent 会话事件**：每一轮会话记录（`page_context` 同一处的 payload）带 `business_case_id`。
6. **恢复解析**（唯一入口 `cpq_case_link.resolve`）按可靠性取候选：
   `case`（`business_case_id` 命中）> `task`（`source_task_id` 的卡片）> `session`（`source_session_id` 命中）；
   去重后：
   * **唯一候选** → 自动关联（`linked_by` = 命中方式），并把 `business_case_id` 回填到卡片与技术项目；
   * **多个候选** → **停止**，返回候选清单，由有权限的人在报价侧选择，**不写任何数据、不新建**；
   * **无候选** → **停止**，返回「无候选」，**不新建**；只有显式传入「新建」与**恢复原因**时才新建，
     并记录 `recovered_from_project_id`（= 技术项目号）与 `recovery_reason`。
7. **绝不静默新建**：没有显式新建请求时，任何路径都不得创建新的报价会话 / 卡片。
8. **绝不用技术项目号冒充 `quote_session_id`**：新建的会话号必须是报价侧生成的真实会话号，
   且 `quote_session_id != tech_project_id`。

## 5. 状态定义及状态转换

`resolve` 的四种结局（业务可区分，全部返回结构化结果）：

| code | 含义 | 副作用 | 用户可见 |
|---|---|---|---|
| `linked` | 唯一候选，自动关联 | 回填卡片与技术项目的 `business_case_id` | 正常回传 |
| `multiple_candidates` | 多个候选，需人工选择 | **无** | 候选清单（会话号/标题/客户/项目/匹配方式/更新时间） |
| `no_candidate` | 无候选 | **无** | 「找不到原报价卡片：是否新建？」 |
| `create_new` | 人工明确新建 | 新建会话与卡片 + 落恢复留痕 | 明确告知已按本人选择新建 |

`linked_by` 取值：`case` / `task` / `session` / `new_session`（与批次 3 的既有取值兼容，新增 `case`）。

## 6. 接口与数据契约

* 新增 `cpq_case_link.py`：
  * `decide(candidates, *, business_case_id="", create_new=False, create_reason="") -> dict`
    —— **纯函数**，不碰数据库；返回上表四种 `code` 之一 + `quote_session_id` /
    `linked_by` / `business_case_id` / `candidates` / `recovered_from_project_id` / `recovery_reason`。
    规则：候选 ≥2 → 无论 `create_new` 是什么都返回 `multiple_candidates`（不许用"新建"绕过选择）；
    `create_new=True` 且 `create_reason` 为空 → 仍返回 `no_candidate`（必须写清为什么新建）。
  * `resolve(conn, *, business_case_id="", source_task_id="", source_session_id="",
    tech_project_id="", create_new=False, create_reason="", user=None) -> dict`
    —— 查候选（同一连接、只读优先）后调用 `decide`，再按结局落库：
    `linked` 负责**回填实例号**（卡片没有就生成一个）；`create_new` 只负责带出恢复留痕
    （`recovered_from_project_id` / `recovery_reason` / `recovered_by` / `recovered_at`），
    报价会话与卡片仍由 `send_to_quote` 在**同一个事务**里用既有 `ensure_quote_session` 建
    —— 建会话不许搬进判定模块，也不许绕开批次 3 的事务。
  * `CaseLinkError(code, candidates=[], message="")`：`multiple_candidates` / `no_candidate` 用异常抛出，
    便于 `send_to_quote` 整段回滚且把候选带给界面。
* `cpq_wf_card` 新增列 `business_case_id varchar(64)`（幂等 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
  + `CREATE INDEX IF NOT EXISTS`）；`cpq_wf_handoff` 新增同名列（可空）。

契约细节（红测逐条钉死，实现不得改名、不得缺键）：

* **候选对象**（`decide` 的入参与 `candidates` 返回项）是一个 dict，至少含
  `quote_session_id` / `card_id` / `linked_by`（命中方式 `case` | `task` | `session`）/ `title`；
  去重按 `quote_session_id`（同一张卡片只算一个候选），命中方式保留可靠性最高的那个：
  `case` > `task` > `session`。同一张卡片既被实例号命中、又被来源任务指到时，这是
  **一个**候选（`linked_by='case'`），不是「多候选」。
* **`decide` 返回键**固定为：`code` / `quote_session_id` / `linked_by` / `business_case_id` /
  `candidates` / `recovered_from_project_id` / `recovery_reason`，一个都不能少；
  非命中路径的未知项填空串或空列表（不是 `None`）。
* **`decide` 只做判定、不抛异常**：`multiple_candidates` 与 `no_candidate` 也以 `code` 返回，
  异常由 `resolve` 抛。
* **`create_new` 的会话号由 `resolve` 落库后填**：`decide` 在 `create_new` 结局里
  `quote_session_id` 允许为空串，`recovered_from_project_id` 取传入的 `tech_project_id`。
* `decide` 额外接受 `tech_project_id=""`（`create_new` 留痕用）；`decide` 是纯函数：
  不改入参、不碰数据库、同输入同输出。
* `CaseLinkError(code, candidates=[], message="")`：属性 `code` / `candidates` / `message`，
  `str(exc)` 至少包含 `message`；`code` 只会是 `multiple_candidates` 或 `no_candidate`。
* **候选判定排除归档卡片**：`overall_status = 'archived'` 的卡片不进候选、也不自动复活 ——
  实例号指向归档卡片时按「无候选」走人工决定路径。
* `cpq_wf.sync_card(..., business_case_id="")`：为空时生成 `bc_` + 12 位小写 hex 落库；
  传入非空时原样存（恢复新建沿用同一实例号）；同一会话再次 `sync_card` 不得换号。
* 卡片的读取路径（`cpq_wf.get_card` / `card_detail`，即 `_CARD_COLS`）必须带出
  `business_case_id`，前端才能显示这次的落点实例号。
* 技术项目 meta 的 `business_case` 文档键固定为 `business_case_id` / `quote_session_id` /
  `source_task_id` / `linked_by` / `linked_at` / `recovered_from_project_id` /
  `recovery_reason` / `recovered_by` / `recovered_at`；`store.save_business_case(project_id,
  link, author="system")` 合并写入，`store.load_business_case(project_id)` 读回（无则空）。
* `cpq_wf_handoff.business_case_id` 必须与本次回传实际使用的实例号一致（可空）。
* Agent 会话：每一轮 `user` / `assistant` 记录都带 `business_case_id` 键（值取项目 meta，
  没有就是空串）。
* 回传返回体的 `recovery` 固定是 dict：`recovered_from_project_id` / `recovery_reason` /
  `recovered_by` / `recovered_at`；**没走新建时这个 dict 也必须在**（值全为空串），
  否则界面无法区分"没恢复"与"字段缺失"。
* `cpq_tech_bridge.send_to_quote(...)` 新增关键字参数：
  `business_case_id: str = ""`、`create_new: bool = False`、`create_reason: str = ""`，
  其余参数与返回体不变；返回体新增 `business_case_id` / `candidates` / `recovery`。
* `cost_flow.integration_quote_result(...)` 的返回体新增 `business_case_id`
  （取技术项目 meta；没有则空串，绝不现编）。
* 技术项目 meta：`business_case` 由 `store.save_business_case(project_id, link, author)` /
  `store.load_business_case(project_id)` 读写（薄封装，落点仍是既有 meta 文档）。
* Agent 会话记录的每轮 payload 新增 `business_case_id`（来源：项目 meta，缺失即空串）。

## 7. 正常路径

1. 报价创建卡片 → 产生 `business_case_id` → 支线任务 payload 带上它。
2. 技术侧做完 → 回传：`resolve` 用 `business_case_id` 命中唯一卡片 → 自动落回原卡片，
   技术项目 meta 回填 `business_case_id` 与 `quote_session_id`。
3. 前端显示 `linked_by=case`（不再出现 `new_card` 警告）。

## 8. 异常路径

* **多候选**：抛 `CaseLinkError(code='multiple_candidates')`，整段回滚（0 写入），
  界面列候选让人选；选择后重试即 `linked`。
* **无候选且未要求新建**：抛 `CaseLinkError(code='no_candidate')`，0 写入；
  界面明确问「是否新建报价卡片」。
* **无候选且人工要求新建**：新建会话 + 卡片，`recovered_from_project_id` = 技术项目号，
  `recovery_reason` 落库（审计 + 卡片 note），返回 `code='create_new'`。
* **新建失败 / 中途异常**：整段回滚（沿用批次 3 的事务），不留半张卡片。
* **`business_case_id` 指向的卡片已归档**：视为 0 候选（不自动复活归档卡片），走人工决定路径。

## 9. 并发与幂等

* 两个并发回传同一业务实例：批次 3 的 `handoff_key` 唯一约束仍然是裁判（先到者落库，
  后者复用同一条 `handoff_id`）；`business_case_id` 的回填必须幂等（同值重写无副作用）。
* `resolve` 本身只读；写只发生在既定结局里，且都在批次 3 的同一事务里。
* 重复调用 `decide`（同一输入）必须返回同一结果（纯函数）。

## 10. 刷新、重试、重复点击和服务重启后的行为

* 多候选 / 无候选被用户处理后重试：正常 `linked` / `create_new`。
* 服务重启：`business_case_id` 落在报价库与项目 meta 里，重启后仍可追溯。
* 重复点击「发送至报价」：批次 3 的幂等键保证只产生一次交接。

## 11. 权限边界

* 多候选的**选择**与「新建报价卡片」都要求报价侧有权限的角色（销售经理或其上级）；
  技术侧只负责把候选与原因带回来，不替销售做选择。
* `create_new=True` 也必须带 `create_reason`，并记录 `recovered_by`（当前用户）。
* 本批不新增权限模型，只要求把「谁新建的、为什么」写清楚。

## 12. 历史数据兼容

* 老项目 / 老卡片没有 `business_case_id`：**只读可用**，走 `task` / `session` 既有线索；
  首次成功关联时**安全回填**（单一候选才回填），一次不够、绝不猜测；
* 有多张候选卡的老项目：不回填、不新建，走人工选择；
* 不迁移、不改写历史任务 payload、历史会话与审计；不回填历史 `page_context`。

## 13. 非目标

* 不做报价侧「选择候选 / 确认新建」的界面（只出接口与文案，UI 属后续批，人工验收覆盖）。
* 不改批次 3 的事务、幂等键与关闭来源待办的语义。
* 不改批次 4 的主数据写入幂等、批次 5 的流程投影与口径。
* 不改 Agent 会话的其它事件字段；不做数据迁移脚本。

## 14. 可自动化验收标准

1. 正常来源恢复：任务在 → `linked_by='task'`，`business_case_id` 回填到卡片与技术项目。
2. task 丢失（取消 / 替换 / 删除）但实例号在 → `linked_by='case'`，仍落回原卡片。
3. 多候选 → 抛 `multiple_candidates` 且带候选清单；**0 写入**（卡片数、交接数不变）。
4. 无候选 → 抛 `no_candidate`；**不新建**（卡片数不变）。
5. 人工确认新建（带原因）→ 新建成功，`recovered_from_project_id` 与 `recovery_reason` 落库。
6. `create_new=True` 但没有原因 → 视为无候选，不新建。
7. 新建的 `quote_session_id` 不等于技术项目号。
8. `decide` 是纯函数：同一输入两次返回一致。
9. 技术侧回传包（`integration_quote_result`）带 `business_case_id`；项目 meta 能读回同一值。
10. Agent 会话轮次 payload 带 `business_case_id`。
11. 报价建卡即产生实例号：`cpq_wf.sync_card` 落 `bc_` + 12 位 hex，重复同步不换号。
12. 归档卡片不进候选：实例号只指向归档卡片时按「无候选」处理，不自动复活、不自动新建。
13. 护栏：批次 3 的红测全绿（事务与幂等语义不变）。
14. 实例号与来源任务指向**同一张卡片**时算一个候选（`linked_by='case'`），不得报多候选。

## 15. 人工验收场景

1. 报价建卡 → 新增工艺 → 技术侧回传：落回原卡片，界面不出现「已新建一张」。
2. 在报价侧把来源任务取消，再回传：仍落回原卡片（靠实例号）。
3. 造出两张同实例卡片 → 回传时界面列出候选，不自动选、不自动建。
4. 清掉线索 → 回传时界面问「是否新建报价卡片」，选「否」什么都不发生。
5. 选「新建」并填原因 → 新卡片出现，卡片备注与审计里能看到
   「由技术项目 xxx 恢复新建，原因 …」。

## 16. 不允许减少的既有能力

* 批次 3 的原子回传、幂等键、来源待办关闭与 `already_sent` / `already_completed` 语义。
* `linked_by` 既有取值（`task` / `session` / `new_session`）与前端既有展示。
* 技术项目号与报价会话号分离（`tech_project_id` 绝不写入报价库当 `session_id`）。
* 权限门禁（只有财务经理 / 工艺经理能回传；越权整段回滚）。
* 历史项目（无实例号）**有** `task` / `session` 线索时仍能正常回传；没有任何线索时不再
  静默新建，改为明确要求用户选择（「新建」必须带原因）—— 这是本批要修的行为，不是能力减少。
