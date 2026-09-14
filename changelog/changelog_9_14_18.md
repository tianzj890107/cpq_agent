# 变更日志（9-14 ~ 9-18）

## 8. 技术清单认证就绪加载与报价主色提示 Spec / Red（9-14）

- 新增 `docs/specs/home-auth-ready-and-quote-primary-state-colors.md`：技术清单必须等待全局认证模块就绪后带凭证读取项目；报价 Agent 回填字段与角色只读提示统一使用系统蓝色；角色不符只保留右侧看板提示，不再向左侧会话重复插入同义消息。
- 新增 `tests/test_home_auth_ready_and_quote_primary_state_colors_red.py`：覆盖直接进入技术助手时的认证等待、受保护的 `window.cpqAuth.api`、认证变化后重试、`.guessed` 主色样式、角色不符不创建 `wfGateBubble`、右侧提示蓝色边框/背景以及权限与转交能力保留。
- 当前缺口：首页 `initAssistantMode()` 在页面末尾 `cpq_auth.js` 执行前立即调用技术 `loadCards()`，直接访问裸 `cpqAuth`；认证 ready/change 仅重绘旧数据不重试项目读取。报价 `.guessed` 使用琥珀色，角色不符同时生成左侧会话 gate bubble 和右侧 `wfBar`，右侧提示也是黄橙色且没有主色边框。
- Red 验证：`python3 -m unittest tests.test_home_auth_ready_and_quote_primary_state_colors_red -v` → 7 个测试中 1 通过、**6 失败**；失败覆盖认证等待/受保护调用、认证事件后的技术项目重试、回填字段主色化、移除角色不符的重复会话提示及右侧提示蓝色边框/背景，权限校验和转交能力保护项已通过。
- 状态：本批仅建立 Spec / Red 基线，未修改业务实现；等待 DeepSeek 实现后复验。

## 7. 报价 / 技术工艺输入框移除模型按钮并底部对齐 Spec / Red（9-14）

- 新增 `docs/specs/quote-tech-chat-composer-model-removal-and-bottom-alignment.md`：技术工艺统一工作台输入框只保留附件、文本输入和发送；框内模型按钮及其状态绑定彻底移除，但左右全局模型设置入口、同一份模型/API Key 配置继续保留。
- 新增 `tests/test_quote_tech_chat_composer_alignment_red.py`：锁定框内模型控件及死代码删除、全局设置入口保留、统一工作台输入框下方说明占位移除、报价与技术工艺 composer 使用相同 `10px 16px` padding，以及两边继续由纵向 flex 布局锚定底部。
- 当前缺口：技术工艺仍渲染 `#ocModelSelect` / `#ocModelSelectLabel` 并在 `agent-chat.js` 同步、绑定该按钮；`.oc-composer` 使用 `6px 16px 14px`，输入框下还有 `.oc-disc` 说明与 9px 间距，共同把技术输入框本体向上顶。
- Red 验证：`python3 -m unittest tests.test_quote_tech_chat_composer_alignment_red -v` → 6 个测试中 2 通过、**4 失败**；失败准确覆盖框内模型按钮及两份前端脚本死绑定、模型专用 CSS、技术 composer 额外底部偏移和输入框下方说明占位。底部 flex 锚定与两端全局模型设置入口保护测试已经通过。
- 状态：本批仅建立 Spec / Red 基线，未修改业务实现；等待 DeepSeek 实现后复验。

## 4. 成本/报告回传工艺经理与销售经理闭环 Spec / Red（9-14）

- 新增 `docs/specs/tech-cost-and-report-handoff-to-process-or-sales.md`：定义成本确认后的“提交工艺经理进入第 5 大步确认/报告”与“回传销售经理续接报价第 3 步”两条去向，以及已发布报告再次回传销售时补充完整报告且不得倒退/重复任务的闭环。
- 新增 `tests/test_tech_cost_report_handoff_continuity_red.py`：覆盖可见去向按钮、当前任务关闭、完整成本/参数/工艺交接包、工艺经理待办落到 `summary`、原报价 session 续接、报价第 2 步快照、报告专用回传路由、完整报告数据和幂等防重复。
- 当前缺口：成本回工艺被当成 `process` 返工支线，交接包仅含成本摘要且未显式关闭来源 claimed 任务；来源报价缺失时会以技术项目号同步孤立报价卡片；报告页无稳定可见回传按钮并错误复用 integration 回传，只传 note，缺报告内容和重复流转保护。
- Red 验证：`python3 -m unittest tests.test_tech_cost_report_handoff_continuity_red -v` → 14 个测试方法中 3 通过、**11 失败**（子测试统计共 12 个失败断言）；失败覆盖两个可见去向、task_id 续传/关闭、完整交接包、工艺经理落到 `summary`、真实报价会话、报告专用路由/可见按钮/幂等与父壳动作。
- 状态：本批仅建立 Spec / Red 基线，未修改业务实现；等待 DeepSeek 实现后复验。

## 3. 整合参数归位第 3 大步 Spec / Red（9-14）

- 新增 `docs/specs/tech-integration-params-belong-to-major-step-3.md`：明确第 3 大步“组装与整合”的“参数推荐”必须完成报价必填项的推荐、智能补全、保存和最终确认；第 4 大步“成本测算”只保留零件成本、组装成本和汇总。
- 新增 `tests/test_tech_integration_params_step_ownership_red.py`：锁定成本步骤移除“整合参数”页签/侧栏/视图/业务代码，现有 autofill/finalize 能力归回组装整合并使用工艺写权限，以及发送财务前必须通过参数完整性与最终确认闸门。
- 当前缺口：父壳和 `cost-review` 明确把“整合参数”放在 2.3；2.2 参数推荐缺项时提示财务补齐，autofill/finalize 也限定 `COST_ROLES`，`send_to_finance` 不检查 `params_final` 或报价必填缺失项。
- Red 验证：`python3 -m unittest tests.test_tech_integration_params_step_ownership_red -v` → 10 个测试方法中 3 通过、**7 失败**（含子测试共 14 个失败断言）；失败准确覆盖成本页签/视图未移除、2.2 未接管智能补全/最终确认、旧提示仍把缺项推给财务、权限仍属 `COST_ROLES`、发送财务缺少 `params_final` 与必填完整性闸门。
- 状态：本批仅建立 Spec / Red 基线，未修改业务实现；等待 DeepSeek 实现后复验。

## 2. 技术工艺首页项目卡片与 Agent 完整会话恢复 Spec / Red（9-14）

- 新增 `docs/specs/tech-home-project-cards-and-agent-history.md`，明确历史 Drawer、首页“我的清单 / 全部清单”必须复用同一真实 `/api/projects` 项目集合；“我的清单”按登录账号与项目 `owner` 过滤，“全部清单”展示全部可见项目，不能让真实历史项目只存在于 Drawer。
- 新增 `tests/test_tech_home_project_cards_and_agent_history_red.py`，锁定首页技术项目鉴权读取、owner 映射和归属过滤，以及项目级 Agent 历史只读接口、用户/助手/工具轨迹完整回放和刷新不重置契约。
- 当前缺口：统一首页技术项目仍以裸 `fetch('/api/projects')` 读取并在“我的清单”跳过技术归属过滤；后端 `/agent/meta` 只提供 `message_count`，前端只执行 `loadMeta()`，没有完整会话读取和渲染链路。
- Red 验证：`python3 -m unittest tests.test_tech_home_project_cards_and_agent_history_red -v` → 9 项中 3 通过、**6 失败**；失败分别命中首页裸 `fetch`、缺少 owner 映射/本人过滤、缺少 Agent 历史 GET 路由、缺少历史序列化、缺少前端历史读取/渲染及初始化恢复，符合实现前预期。
- 状态：本批仅建立 Spec / Red 基线，未修改业务实现；等待 DeepSeek 实现后复验。

## 1. 技术工艺 Agent 左侧会话移除「阶段上下文卡」 实现（9-14）

- 需求：技术工艺统一工作台左侧会话标题下会动态插入一张阶段上下文卡（如「创建需求 / 1.1 创建需求 / 阶段说明 / 保存草稿 · 提交确认」），与左侧统一操作栏、右侧业务看板重复；要求彻底移除所有阶段的该卡（不是 CSS 隐藏，也不是只处理 1.1），同时保留发给模型的不可见 `pageContext`。
- `tech_app/frontend/agent-chat.js`：删除阶段卡全部 UI 实现 —— `contextHost()`、`renderStageContext()`、`#ocStageContext` / `.oc-stage-context*` DOM 创建、卡内按钮与 `cpq:tech-agent:stage-action` 派发；`setStageContext(context)` 改为只保存模型请求用的语义上下文（以 `pageContext` 判定有效性），不再触发任何渲染。保留 `stageContext` 变量、`standalonePageContext()`、`currentPageContext()` 与 `cpq:tech-agent:stage-context` 接收通道，Agent 请求仍按当前 stage 注入 `page_context`；文件内 4 个 NUL 哨兵计数不变。
- `tech_app/frontend/tech-workbench.js`：`stageAgentContext()` 不再构造 `context.actions`，只返回 `stage / project / taskId / label / pageContext / hint`；删除 `STAGE_AGENT_CONTEXT` 九条仅供阶段卡使用的 `actions: [...]` 配置；删除 `cpq:tech-agent:stage-action` 监听与转发（`runBoardAction` 仍服务底栏与统一操作栏，未删除）。`STAGE_AGENT_CONTEXT` 九个 `pageContext` 全部保留且互不重复。
- `tech_app/frontend/agent-chat.css`：删除 `.oc-stage-context` / `-head` / `-title` / `-step` / `-hint` / `-actions` / `-btn` 全套专用样式与对应注释。
- `docs/specs/tech-agent-recovery-21-quote-parity.json`：第 21 步对照表 `current_step` 行的 live 锚点原为 `agent-chat.js#ocStageContext`，随卡片删除已失效；改为 `tech-workbench.html#techStepsBar`（顶部九阶段流程条，当前步骤的可见宿主），`release` 说明同步为「九阶段 page_context 仍注入会话请求，但不再渲染可见阶段卡」。此改动是保持对照表锚点不悬空的必要连带，未改任何实现语义。
- 测试（实际运行）：`python3 -m unittest tests.test_tech_agent_remove_stage_context_card_red -v` → **7/7 通过**（Red 基线 7 失败 / 0 通过）；相关回归 `tests.test_tech_stage_context_nine_stages_red + test_tech_context_substeps_and_frame_status_red + test_tech_full_width_board_and_single_agent_pane_red + test_tech_quote_agent_parity_matrix_red + test_tech_left_toolbar_parity_red + test_tech_left_chat_controls_restore_red + test_tech_direct_attachment_and_chat_capability_actions_red + test_tech_primary_quick_actions_and_bulk_part_analysis_red + test_tech_tool_trace_business_line_detail_red + test_tech_board_bridge_protocol_red + test_tech_ui_protocol_red` → 98/98 通过；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → 460 项、**0 失败**、7 跳过（改前为 7 失败）；`node --check tech_app/frontend/tech-workbench.js`、`node --check tech_app/frontend/agent-chat.js` 通过；`git diff --check` 通过。
- 边界：未改后端、未新增 / 删除 `@app.` 路由；保留 `STAGES` 与九阶段路由、顶部大流程与右侧子步骤导航、九个 `pageContext`、`currentPageContext()` 请求注入、`#techChatActions` 统一操作栏及其附件 / AI 执行 / 主次操作 / 批量工艺 / 批量成本 / 前后步骤 / 转交 / 重试、`STAGE_ACTIONS`、`TechBoardBridge.executeAction`、右侧 1.1 表单的 `saveDraft` / `submitRequirement`、`submit-confirmation`、会话历史 / 草稿 / 任务进度 / 结果入口 / `tech_ui` 协议；未用 `display:none` / `visibility:hidden` / 改名保留同类卡片规避测试；未改 Spec 与本批 Red 测试。
- 交付状态：本记录写入时实现**尚未提交、尚未推送**；未创建 MR/tag/Release、未部署、未启动或重启服务；工作区另有 `报价首页.html` 的用户未提交微调，未触碰。

## 4. 技术工艺首页项目卡片与 Agent 完整会话恢复 实现（9-14）

- 需求：技术工艺历史 Drawer 能看到真实项目，但统一首页「我的清单 / 全部清单」没有可靠打通（裸 `fetch('/api/projects')`、技术侧跳过归属过滤）；从 Drawer 打开历史项目只请求 `/agent/meta`，左侧会话为空。
- `报价首页.html`（技术项目加载与清单）：
  - `loadCards()` 技术分支改为 `cpqAuth.api('/api/projects')`（带统一登录凭证），兼容接口返回数组或 `{projects: [...]}` 包装；非 2xx / 鉴权失败 / 服务异常由 `cpqAuth.api` 抛出真实错误并记入 `SESSION_ERROR`。
  - 映射保留 `project_id` / `owner` / `owner_display_name` / `project_name` / `device_name` / `source_filename` / `created_at` / `updated_at` / `status`（`owner`、`ownerName` 进卡片模型）。
  - 读取失败不再伪装成空数据：`renderCards()` 在 `SESSIONS[mode] === null` 时显示「读取<助手名>数据失败：<真实原因>」。
  - 「我的清单」按 `project.owner === 当前登录 username`（`cpqAuth.user()` 的 `username`/`user_name`）严格过滤；删除技术侧跳过归属的旧分支；未登录引导登录。「全部清单」展示后端可见的全部技术项目；不再用 localStorage 猜测技术项目归属。三个入口（我的清单 / 全部清单 / 历史 Drawer）共用同一 `/api/projects` 事实源与同一 `project_id`，点击走既有 `openTechProject` → `tech-workbench.html?project=<id>&stage=<真实阶段>`。
- `tech_app/backend/services/oc_agent.py`（只读历史 + 会话续接）：
  - 新增 `load_history(project_id)` 与 `_serialize_history()` / `_history_text()`：复用 open-claude `SessionStore` 的 `latest_session_id()` + `load_session()` 读取磁盘上该项目最近一次会话，按原始顺序还原为可渲染事件 —— `user` / `assistant` 文本、`tool_use`（id / name / input）、`tool_result`（tool_use_id / content / is_error）；字符串与分块两种 content 都兼容，无法识别的块跳过，工具结果正文超 20000 字截断。只返回消息事件，不含系统提示词、模型 Key 或工作目录。
  - `ProjectAgent.__init__` 创建 `Conversation` 时带上 `resume_session_id=latest_session_id(cwd)`：服务重启 / ProjectAgent 重建后接着最近一次会话继续，新消息仍写回同一文件，「刷新不丢、继续发送追加在末尾」才成立；`/agent/new` 的 `reset()` 仍新建会话（重置语义不变）。
  - `load_history` 优先取在线会话的 session（重置后新会话未落盘即返回空），无在线会话时回退磁盘最近一次会话。
- `tech_app/backend/main.py`：新增只读路由 `GET /api/projects/{project_id}/agent/history`（`Depends(current_user)` + `_agent_project` 校验项目存在），返回 `{project_id, session_id, messages, message_count}`；会话层不可用（`AgentUnavailable`）时与 `/agent/meta` 一致返回 `{available:false, reason, messages:[], message_count:0}`，不抛 500。未新增 / 删除其它 `@app.` 路由。
- `tech_app/frontend/agent-chat.js`：新增 `loadHistory()` / `renderHistory()` 与 `historyLoaded` 幂等守卫；打开已绑定 projectId 时请求 `api("/history")`，按后端顺序回放 —— 用户消息复用 `addUser`（`.oc-ubub`）、助手文本复用 `addAssistant` + `renderMarkdown`（`.oc-amsg` / `.oc-atxt`）、工具调用复用 `addToolCard`、`tool_result` 回填对应工具卡；`tech_ui` 结构化事件回放时跳过。非 2xx / `available:false` 显示明确错误，绝不静默展示空会话；空历史才保留空态。初始化改为 `loadHistory().then(() => loadMeta())`；`window.ocTechAgent.resetTask` 改为直接引用 `resetTaskFlow`（初始化路径不再出现任何自动重置形态）。文件内 4 个 NUL 哨兵计数不变。
- 测试（实际运行）：`python3 -m unittest tests.test_tech_home_project_cards_and_agent_history_red -v` → **9/9 通过**（Red 基线 6 失败 / 3 通过）；相关回归 `tests.test_tech_history_quote_drawer_parity_red + test_tech_full_width_board_and_single_agent_pane_red + test_tech_backend_capability_preservation_red + test_tech_quote_agent_parity_matrix_red + test_home_cards_equal_height_red + test_tech_home_quote_shell_red + test_tech_agent_remove_stage_context_card_red + test_tech_stage_context_nine_stages_red` → 63/63 通过；函数级自检（临时目录 + 受控 SessionStore）：无在线会话读到磁盘会话 4 条事件、重置后在线新会话读到 0 条、`Conversation(resume_session_id=...)` 恢复 2 条并续用同一 session id；`node --check tech_app/frontend/agent-chat.js`、`node --check tech_app/frontend/tech-workbench.js` 通过；`git diff --check` 通过。
- 边界：未改 `/api/projects`、`/agent/meta`、`/agent/send`、`/agent/new` 与 SSE 协议；未改九阶段流程、stage 路由、`pageContext` 注入、左侧统一操作栏、右侧看板、项目阶段恢复与材料/工艺/成本/报告/审批接口；未建第二套会话库、未迁移或清空任何历史会话与项目数据；打开历史项目不调用 `/agent/new`；未提交、未推送、未创建 MR/tag/Release、未部署。

## 5. 整合参数归位第 3 大步，成本测算只保留成本页签 实现（9-14）

- 需求：统一工作台第 4 大步「成本测算」错误承载「整合参数」（报价必填项的智能补全 / 保存 / 确认），第 3 大步「组装与整合」却能带着缺失必填项确认并发送财务，界面还提示「去 2.3 补齐」。本轮把这份职责移回第 3 大步，成本步骤只保留成本页签。
- `tech_app/frontend/tech-workbench.js`：父壳 `CHILD_TAB_PROXY.cost.tabs` 由四项收窄为严格三项（零件成本 / 组装成本 / 汇总），删除 `params` 页签代理；`process` 与其余九阶段路由不变。
- `tech_app/frontend/cost-review.html`：删除左侧导航条 `#crGoParams`「整合参数」入口与标题栏 `data-cr-tab="params"` 页签按钮（该页已无仅服务整合参数的 DOM）；零件成本 / 组装成本 / 汇总、一键测算全部成本、确认成本、写入数据库、发送至报价、退回工艺经理全部保留。
- `tech_app/frontend/cost-review.js`：整体删除 `crRenderParams` / `crAutofill` / `crFinalize`、`crTab === 'params'` 动作分支、`CR_TABS.params`、`renderers.params`、`#crGoParams` 绑定与对 `/integration/params/autofill`、`/integration/params/finalize` 的调用；`registerViews` 里的 `params` 改为显式拒绝的兼容别名（返回 `moved-to-integration` 结构化失败、`visible:false`），旧调用拿到可识别结果而不是打开不存在的页签；`parts / assembly / total` 三视图与 `runCostReview` / `costStep` / 三个去向动作不变。
- `tech_app/frontend/assembly-integration.js`（第 3 大步接管）：
  - 新增 `aiRequiredGaps()` / `aiRequiredCard()`：「参数推荐」页签展示 `required_total` / `required_filled` / `required_missing`，并逐项列出尚未填写的报价必填字段；
  - 新增 `aiParamsAutofill()`：以当前整合需求为 note 调 `POST /api/projects/{id}/integration/params/autofill`，轮询既有任务，用 `QuoteParams.applyFills()` 把建议回填当前参数表（只回填前端，不落库），并汇报成功补全数与 unresolved；进度经既有 `TechBoardRuntime` 任务事件上报，不阻塞父壳；
  - 新增 `aiParamsFinalize(confirm)`：「保存补填」(`confirm:false`) 与「确认参数已齐」(`confirm:true`) 复用 `POST .../integration/params/finalize`，`values` 取自 `QuoteParams.collect()`；
  - 参数页签动作区按状态显示生成 / 重新生成参数推荐、智能补全、保存补填、确认参数已齐与「已填 N/M、还缺 N 项、是否已最终确认」；参数推荐确认按钮（`#aiParamsConfirm`）保留；
  - 删除所有把缺项推给财务的文案（含 `aiConfirmStep` 的「由财务经理在 2.3『整合参数』里补齐」、发送财务任务日志中的「整合参数…在那一步完成」、`aiRunAll` 的「三步依次进行」与「2.3 补齐报价参数」、`aiCollectParams` 头注释、顶部「整合参数搬去了 2.3」注释），改为「必填参数在当前『参数推荐』页签补齐 / 确认后才能发送财务」；
  - 发送财务按钮的 UI 闸门同步后端：`params_confirmed && process_confirmed && params_final && !required_missing`，待办原因逐项说明；
  - `integrationStep` 不再接受 `cost`（成本测算只在成本步骤发起），保留 `params` / `process` 与既有 deferred 回执协议。
- `tech_app/backend/main.py`：`autofill_integration_params` / `finalize_integration_params` 的权限由 `auth.COST_ROLES` 改为技术工艺写权限 `auth.WRITE_ROLES`（`"需要工程师及以上权限"`），注释与错误文案改述为第 3 大步「组装与整合 · 参数推荐」，不再出现「2.3 / 财务经理负责」；两个路由与请求体结构保持不变；重新生成参数推荐时一并失效 `params_final` / `params_final_by` / `params_final_at`；真正成本接口（`generate_integration_cost` 等）仍用 `auth.COST_ROLES`。
- `tech_app/backend/services/integration.py`：`send_to_finance()` 在原有「参数推荐已生成 + 组装工艺已生成 + 两个确认」之上新增两道闸门 —— `product_params.missing_required(plan.params)` 有缺项即抛 `IntegrationFlowError` 并列出缺失字段（指回本步「参数推荐」补填），`plan.params_final` 未最终确认同样拒绝流转；删除「那些参数由财务在 2.3 的『整合参数』补齐」的旧注释。
- 测试（实际运行）：`python3 -m unittest tests.test_tech_integration_params_step_ownership_red -v` → **10/10 通过**（Red 基线 3 通过 / 7 失败、14 个失败断言全部转绿）；相关回归 `tests.test_tech_integration_agent_red + test_tech_cost_review_agent_red` → 25/25 通过，`test_tech_board_action_registry_red + test_tech_board_bridge_protocol_red + test_tech_board_deferred_actions_red + test_tech_backend_capability_preservation_red` → 24/24 通过；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **479 项、0 失败、7 跳过**（改前 14 失败全部来自本批 Red），`./open-claude/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` → 481 项、0 失败；`node --check tech_app/frontend/tech-workbench.js`、`node --check tech_app/frontend/assembly-integration.js`、`node --check tech_app/frontend/cost-review.js` 通过；`git diff --check` 通过。缓存版本只递增本批涉及的三个脚本：`tech-workbench.js?v=twb16`、`assembly-integration.js?v=ai14`、`cost-review.js?v=cr5`。
- 边界：未删除 `autofill` / `finalize` 路由，未新增第二套参数推荐接口，未用 CSS 隐藏整合参数；成本步骤仍只用 `auth.COST_ROLES`，未扩大成本写权限，未新增成本算法；历史项目已存的参数与 `params_final` 不迁移、不清空、继续有效；未改 Agent 工具与 `tech_ui` 协议、`TechBoardRuntime` / `TechBoardBridge`、九阶段路由、项目历史与会话；工作区中并行批次（`报价首页.html`、`oc_agent.py`、`agent-chat.js` 及本文件 `## 2.` / `## 3.` / `## 4.` 条目）保持原样未触碰；未提交、未推送、未创建 MR/tag/Release、未部署。

## 6. 技术工艺会话输入框重构为「+ / 输入 / 模型 / 发送」单行 实现（9-14）

- 需求：统一工作台左侧会话的输入区是一整块浅灰高输入框，左侧裸回形针旁边还有一颗占位的「任务文件 —」胶囊，输入框内没有模型切换入口。本轮把它收成单行白底布局，并把「任务文件」移出输入框。
- `tech_app/frontend/tech-workbench.html`：
  - 输入区改为 `.oc-inputbox.oc-inputbox-single`：左圆形 `+`（`#ocChatAttachBtn`，仍是 `ocChatFileInput` 的同一次点击直传入口）、中间 `#ocInput`、右侧 `#ocModelSelect`（当前模型名 + ChevronDown）与圆形 `#ocSend`；placeholder 改为「输入你的问题…（Enter 发送，Shift+Enter 换行）」，下方说明文字保留。
  - 「任务文件」（`#ocFilesAction` + `#ocFilesCount`）从输入框内移到会话上方统一工具栏 `#techChatActions`（紧随「附件」），功能仍是查看已上传文件，与输入区 `+` 的新增上传是两个入口。
- `tech_app/frontend/agent-chat.css`：新增 `.oc-inputbox-single`（白底、1px 边框、24px 圆角、min-height 76px）、`.oc-add`（50px 浅灰圆形 `+`，hover / focus-visible / disabled 齐备）、`.oc-model-select`（无边框轻量按钮，40px，主色文字 + ChevronDown，超长模型名省略号）与 `.oc-inputbox-single .oc-send`（54px 圆形主色按钮，白纸飞机居中）；主色仍取 `--oc-accent → --color-primary`，未新写十六进制。独立 2.1 页与 2.2 页继续用原 `.oc-inputbox`，不受影响。
- `tech_app/frontend/tech-workbench.css`：`.tech-chat-actions > button.oc-files-action` 补对齐与活动态描边，使其与工具栏其余胶囊一致；`.oc-files-action` 原有 hover / focus-visible / disabled 样式保留。
- `tech_app/frontend/agent-chat.js`：输入区模型按钮点击时优先调用父壳 `window.techOpenModelSettings`（打开同一张「模型设置」卡片、同一份状态），独立 2.1 页回退到本页同一个 `llm-settings-panel` 弹层 —— 没有第二套模型设置实现；`techShellModel()` 把同一份模型名同步到 `#ocModelSelectLabel`；输入框 keydown 增加输入法保护（`isComposing` / `keyCode === 229` 时 Enter 不发送），Enter 发送 / Shift+Enter 换行语义不变。文件内 4 个 NUL 哨兵计数不变。
- `tech_app/frontend/tech-workbench.js`：暴露 `window.techOpenModelSettings` 复用既有 `openTechModelSettings`；`refreshTechModelLabel()` 在读取 `/api/settings` 后同时写 `#ocModelSelectLabel`，并在打开 / 关闭设置卡片时同步该按钮的 `aria-expanded`。
- 测试（实际运行）：`python3 -m unittest tests.test_tech_direct_attachment_and_chat_capability_actions_red tests.test_tech_left_chat_controls_restore_red tests.test_tech_left_toolbar_parity_red` → 29/29 通过；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → 493 项、**12 失败全部来自并行批次 `tests/test_tech_cost_report_handoff_continuity_red.py`（成本/报告回传闭环，未实现）**、7 跳过，本次改动 0 新增失败；`node --check`（`tech-workbench.js` / `agent-chat.js`）与 `git diff --check` 通过。缓存版本只递增本批资源：`tech-workbench.css?v=twb15`、`tech-workbench.js?v=twb17`、`agent-chat.css / agent-chat.js?v=20260914-composer1`（四个引用页一致）。
- 边界：未改消息发送 / 流式 / 会话恢复 / 附件上传接口与状态、未改后端与 Agent 协议、未新增第二套模型设置或文件状态、未换 UI / icon 库、未删除 `.oc-files-action` 的查看能力；圆形 `+` 以 `+` 为主字形并保留回形针角标（既有契约要求 `#ocChatAttachBtn` 内含 `ti-paperclip`）；未提交、未推送、未创建 MR/tag/Release、未部署。

## 8. 成本结果与已发布报告回传工艺经理/销售经理并续接报价 实现（9-14）

- 需求：成本确认后的两个正式去向要落地到真实业务 —— 提交工艺经理（进第 5 大步「工艺评估报告」）与回传销售经理继续报价；已发布报告还要能再次回传销售，且不得让报价步骤倒退或重复建任务。
- `tech_app/backend/models/cost_review.py`：`CostActionBody` 增加 `source_task_id`，统一工作台从待办带进来的 task_id 用于完成去向时关闭来源 claimed 任务。
- `tech_app/backend/services/cost_flow.py`：
  - 新增 `process_handoff_package()`：成本结果 → 工艺经理的完整交接包（tech_project_id / quote_session_id / source_task_id·no / 产品与数量 / params + params_final / process.steps / part_costs / assembly_cost / cost_breakdown / cost_confirmation / handoff_version），字段全部投影自既有 plan / review / summarize，不复制成本算法。
  - `return_to_process()` 语义改为「提交工艺经理确认」并要求成本已确认；`send_to_quote()` 改称「回传销售经理继续报价」；两者都按 `source_task_id` 调 `close_source_task()`（经桥接 → `cpq_wf.complete_claimed_task`，幂等）。
  - `integration_quote_result()` 扩为完整结论：保留 params / cost / quantity / params_final，新增 cost_breakdown、cost_confirmation、process 路线、part_costs、parts_total、assembly_cost、tech_project_id / quote_session_id / source_task_id、handoff_kind / result_version。
- `tech_app/backend/services/cpq_bridge.py`：`return_to_process` 透传 `source_task_id`；新增 `complete_task`（/wf/tech/complete-task）与 `report_handoff`（/wf/tech/handoff + report/handoff_kind/result_version）。
- `cpq_suite_server.py`：/wf/tech/return-process 透传 source_task_id；/wf/tech/handoff 透传 report / handoff_kind / result_version / source_task_no；新增 /wf/tech/complete-task 路由，校验统一由 `cpq_wf` 做。
- `cpq_wf.py`：新增 `complete_claimed_task()`（领取人/管理角色、任务归属卡片、状态必须 claimed、重复完成幂等）、`advance_step_no()`（报价步骤单调前进：`max(current_step, target)`）、`merge_step_snapshot()`（已推进过报价时只合并快照、不动步骤状态）。
- `cpq_tech_bridge.py`：`send_to_quote()` 重写 —— 不再用技术 project_id 调 `cpq_wf.sync_card`，改为认回原报价卡片（来源任务 / 报价会话号），否则用 `ensure_quote_session()` 建**真实**报价会话（12 位 hex + 复用 `cpq_agent_server.save_history` 初始化可打开记录）；加入幂等键（tech_project_id + quote_session + handoff_kind + result_version）、`current_step <= 2` 才走 `complete_step`、否则只合并第 2 步快照并更新当前销售任务 payload；`return_to_process()` 增加 source_task_id 与「提交确认」文案。
- `tech_app/backend/services/report_workflow.py`：新增 `report_package()` 与 `send_to_quote()`（报告语义专用），要求 `status == published`，交接包含 report_no / version / title / reviewed_by·at / published_by·at / distribution_scope·cc / summary / conclusion / risks / attachments / report_url / pdf_url，并带上完整技术结果；幂等与步骤单调性由桥接层保证；审计动作 `workflow:report_sent_to_quote`。
- `tech_app/backend/main.py`：新增 `POST /api/projects/{project_id}/process-report/send-to-quote`（`_require(MANAGER_ROLES)`）；2.3 三个去向路由透传 `source_task_id`；注释与文案统一为「回传销售经理继续报价 / 提交工艺经理确认」。
- 前端：`cost-review.html` 两个去向按钮改文案并保留三个去向；`cost-review.js` 从 URL 读 `task_id`/`tech_task` 并随去向带回，去向提示与看板动作标签同步更新；`report-publish-result.js` 新增可见按钮 `#rpSendToSales`（仅 published 显示、二次确认）并改走 `/process-report/send-to-quote`（不再调用 `/integration/send-to-quote`）；`tech-workbench.js` 的 `report-publish` 主/次操作为 `publishProcessReport` + `sendReportToQuote`；`cpq-tech-inbox.js` 与 `报价首页.html` 的 `tech_cost_return` 待办路由从 `stage=process` 改为 `stage=summary`，`报价首页.html` 另把 2.2/返工落点写成显式常量。
- 测试（实际运行）：`python3 -m unittest tests.test_tech_cost_report_handoff_continuity_red` → **14/14 通过**；`tests.test_tech_cost_review_agent_red tests.test_tech_report_publish_agent_red` → 28/28；`tests.test_tech_integration_agent_red tests.test_tech_backend_capability_preservation_red tests.test_tech_board_action_registry_red tests.test_tech_board_bridge_protocol_red` → 25/25；`tests.test_tech_home_project_cards_and_agent_history_red tests.test_tech_integration_params_step_ownership_red` → 19/19；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → 499 项、**4 失败全部来自并行新批次 `tests/test_quote_tech_chat_composer_alignment_red.py`（输入框移除模型按钮，未实现）**、7 跳过；`./open-claude/.venv/bin/python` 同口径 501 项 / 同 4 项失败。`node --check`（cost-review / report-publish-result / tech-workbench / cpq-tech-inbox）与 `py_compile`（cpq_wf / cpq_tech_bridge / cpq_suite_server / main / cost_flow / report_workflow / cpq_bridge / cost_review 模型）通过，`git diff --check` 通过。
- 边界：未删除 `cost-review/*`、`process-report/*` 既有路由与 `TASK_KIND_TECH_COST` / `TASK_KIND_TECH_COST_RETURN`、`complete_step`、`send_task`、Agent 工具与 `tech_ui` 协议、报价六步与角色权限；未新增第二套工作流库或成本/报告算法；未清空或迁移任何历史任务、会话、成本与报告数据；未提交、未推送、未创建 MR/tag/Release、未部署。

## 9. 报价 / 技术工艺输入框移除模型按钮并底部对齐 实现（9-14）

- `tech_app/frontend/tech-workbench.html`：`.oc-inputbox.oc-inputbox-single` 内删除整个 `#ocModelSelect` 按钮（含 `#ocModelSelectLabel`、ChevronDown 图标与说明注释），输入框只剩 `#ocChatAttachBtn` + `#ocChatFileInput` + `#ocInput` + `#ocSend`，`#ocInput` 继续 `flex: 1` 吃满腾出的宽度；删除统一 composer 下方的 `.oc-disc` 说明行（通用 `.oc-disc` 样式保留，独立 2.1 / 2.2 页仍可用）；单行输入区注释改为「+ / 输入 / 发送」，并注明模型切换沿用头部 / 导航的「模型与参数设置」入口。
- `tech_app/frontend/agent-chat.css`：`.oc-composer` padding 由 `6px 16px 14px` 改为 `10px 16px`，与报价 `.chat-input-area` 一致，去掉把技术输入框向上顶的额外底距；删除 `.oc-model-select` / `:hover` / `:focus-visible` / `.oc-model-select .ti` / `.oc-model-select-label` 五条只服务已移除按钮的规则；同步把「+ / 输入 / 当前模型 / 发送」的两处注释改述为「+ / 输入 / 发送」。
- `tech_app/frontend/agent-chat.js`：`techShellModel()` 不再写 `#ocModelSelectLabel`（只保留右上角 `#techModelInfo`）；删除 `$("ocModelSelect")?.addEventListener(...)` 整段绑定与相关注释。`#techModelInfo`、`#ocModelPill`、设置卡片入口、`agent-chat.js` 的模型写入逻辑与后端设置接口均未改。
- `tech_app/frontend/tech-workbench.js`：删除 `syncComposerModelLabel()` 及其两处调用与注释；`closeTechSettings()` / `openTechModelSettings()` 不再维护 `#ocModelSelect` 的 `aria-expanded`；`window.techOpenModelSettings` 保留为头部 / 导航「设置」共用的唯一模型设置入口。`#techModelInfo`、`#techModelSettingsMask`、`#techModelSettings`、`#techModelSettingsBody` 与 `refreshTechModelLabel()` 全部保留。
- 缓存版本：统一递增为 `agent-chat.css?v=20260914-composer2` / `agent-chat.js?v=20260914-composer2`，四个入口（`tech-workbench.html`、`index.html`、`assembly-integration.html`、`cost-review.html`）保持一致。
- 测试（实际运行）：`python3 -m unittest tests.test_quote_tech_chat_composer_alignment_red -v` → **6/6 通过**（Red 基线 2 通过 / 4 失败全部转绿）；回归 `tests.test_tech_direct_attachment_and_chat_capability_actions_red + test_tech_left_chat_controls_restore_red + test_tech_left_toolbar_parity_red + test_unified_model_settings_and_api_keys_red` → **36/36 通过**（无 `#ocModelSelect` 过期契约冲突）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **499 项、0 失败、7 跳过**；`node --check tech_app/frontend/agent-chat.js`、`node --check tech_app/frontend/tech-workbench.js` 通过；`git diff --check` 通过。
- 边界：未用 `display:none` / `visibility:hidden` / 零宽 / 移出屏幕等任何方式隐藏旧按钮，是真正删除 DOM、CSS 与绑定；未删除或改写全局模型设置、API Key 后端与弹窗，未新建第二套模型状态或第二张设置卡片；未改后端接口、未改附件上传 / Enter 发送 / Shift+Enter 换行 / 输入法保护 / 消息与流式响应 / 历史会话 / 九阶段流程 / 右侧看板；未改独立 2.1 / 2.2 页面的输入逻辑；未提交、未推送、未创建 MR/tag/Release、未部署。

## 10. 技术清单认证就绪与报价主色状态统一 实现（9-14）

- `报价首页.html`：
  - 新增 `waitForHomeAuth()`：`window.cpqAuth.ready()` 已就绪则立即返回，否则监听一次 `cpq-auth-ready`；模块缺失或 8s 超时抛明确错误（`homeAuthCode` = `auth-missing` / `auth-timeout`），监听器与定时器在 settle 时清理，不会无限等待。
  - `loadCards('tech')` 在请求前 `await waitForHomeAuth()`，项目读取改为 `window.cpqAuth.api('/api/projects')`，不再出现裸变量 `cpqAuth`；未登录单独抛「尚未登录」，项目接口失败包装成「项目接口读取失败：…」，认证未就绪、未登录、鉴权失败、项目失败四类原因不再被一句「请检查后端服务」盖掉（列表错误文案改为「请确认登录状态后重试」）。
  - `cpq-auth-ready` / `cpq-auth-change` 在 quote/tech 模式下改为真正重新 `loadCards(currentMode)`，从失败或未加载状态可重试，不再只 `renderCards()` 渲染旧数组。
  - `loadCards` 增加按模式的在途去重（`LOAD_INFLIGHT`）：初始化与 `cpq-auth-ready` 同时触发时合并为一次请求，Promise 结束即释放，失败不缓存，登录态变化后仍会重试。
- `确认需求解析结果.html`：
  - `.guessed`（Agent 推测值）由琥珀色改为系统主色：`background: var(--color-primary-light)`、`border-color: var(--color-primary)`、`color: var(--color-primary-active)`；guessed 标记、字段值、人工编辑与提交行为不变，真正的 warning/error 颜色不受影响。
  - `wfGate()` 在「已登录 + `WF.loaded` + `!WF.canEdit`」时不再调用 `showGateBubble('handoff')`，改为清理旧 `#wfGateBubble`，由 `wfRenderBar()` 在右侧看板给唯一一条提示；未登录（`login`）与服务不可用（`unavailable`）的会话提示保持不变。
  - `wfRenderBar()` 只读角色分支改为主色语义（浅蓝底 + 蓝色边框 + 深蓝文字），并在进入分支前重置 `borderColor`，避免残留蓝色边框；`#wfBar` 只读提示成为角色不匹配的唯一可见入口。
  - 权限未放宽：按钮仍按 `WF.canEdit` 禁用，后端角色校验未动，「转交任务」入口与 `GATE_BLOCKED` 保护保持原样。
- 测试（实际运行）：`python3 -m unittest tests.test_home_auth_ready_and_quote_primary_state_colors_red -v` → **7/7 通过**（Red 基线 1 通过 / 6 失败全部转绿）；回归 `tests.test_tech_home_project_cards_and_agent_history_red + tests.test_tech_home_quote_shell_red + tests.test_tech_requirement_confirm_red + tests.test_unified_model_settings_and_api_keys_red` → 32/32 通过；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **506 项、0 失败、7 跳过**；两个页面的内联脚本 `node --check` 通过；`git diff --check` 通过。
- 边界：未改角色定义、步骤负责人、`/wf/*` 接口、鉴权后端、登录 Token 与 API Key；未放宽任何操作权限；未删除技术项目、报价卡片或历史会话，也未迁移业务数据；未提交、未推送、未创建 MR/tag/Release、未部署。
