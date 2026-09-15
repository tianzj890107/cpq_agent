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

## 11. 报价输入框统一为技术工艺单行样式、技术 ＋ 去回形针角标、恢复「会话绑定」说明行 Spec / Red（9-14）

- 需求：报价工作台的会话输入框按技术工艺统一工作台的输入框实现；技术工艺输入框左侧圆形 ＋ 右上角多余的回形针角标去掉；输入框下方原有的一行「会话绑定当前项目…」说明行恢复出来，用它把两侧输入区底边对齐（现在底部空出一块）。
- 缺口（实测）：报价 `确认需求解析结果.html` 的 `.chat-input-wrapper` 仍是旧多行造型 —— `#chatAttachBtn` 是 38×38 方形 `ti-paperclip`、`#chatInput` 是 `rows="2"` + 14px、`#chatSend` 是 38×38 方形 `var(--gradient-ai)`，没有技术工艺 `.oc-inputbox-single` 的 `min-height: 76px` / `border-radius: 24px` / `gap: 12px` 单行圆角造型；技术 `tech-workbench.html:140` 的 `#ocChatAttachBtn` 里除 `ti-plus` 外还挂着 `<i class="ti ti-paperclip oc-add-mark">`，`agent-chat.css:427` 留着 `.oc-add .oc-add-mark` 规则与「契约要求保留这个字形」注释；两侧输入框下方都没有 `.oc-disc` 说明行。
- 新增 Spec：`docs/specs/quote-tech-unified-composer-and-binding-caption.md`：报价输入行统一为单行圆角框（50×50 圆形 ＋、54×54 圆形主色发送、单行 textarea、尺寸与技术工艺逐项相等）、技术 ＋ 只保留 `ti-plus` 并清除角标死 CSS、两侧恢复同一句 `.oc-disc` 且必须留在正常文档流（`margin-top: 9px`，不得 absolute/fixed）。
- 新增红测：`tests/test_quote_tech_unified_composer_and_caption_red.py`（15 项），覆盖报价输入行单行圆角契约、与技术工艺几何数值逐项相等、圆形 ＋ 无回形针、圆形主色发送、textarea 单行与去边框、附件/发送/Enter 接线保留、快捷按钮与附件条保留、技术 ＋ 无角标节点、角标死 CSS 与过时注释清除、技术 ＋ 仍直接打开隐藏文件输入框、两侧说明行恢复与正常流样式、两侧 `10px 16px` 外边距与底部 flex 锚定、全局模型设置入口保留。
- 反转的旧契约（本批取代，不作为回归失败）：`tests.test_quote_tech_chat_composer_alignment_red` 的 `test_tech_composer_has_no_caption_below_the_input_box` 改为 `test_tech_composer_has_binding_caption_below_the_input_box`（由「不得出现 `.oc-disc`」反转为「必须恢复」）；`tests.test_tech_direct_attachment_and_chat_capability_actions_red` 删除对 `#ocChatAttachBtn` 内 `ti-paperclip` 的必须断言，改为必须有 `ti-plus` 且不得有 `ti-paperclip`；`docs/specs/quote-tech-chat-composer-model-removal-and-bottom-alignment.md` §4 中「统一 composer 内不再出现 `.oc-disc`」一条不再生效，该文档其余条款继续有效。
- Red 基线（实际运行）：`python3 -m unittest tests.test_quote_tech_unified_composer_and_caption_red -v` → **15 项中 10 失败、5 通过**（5 项保留守卫为附件接线、快捷动作、技术 ＋ 打开文件框、两侧底部锚定、全局模型入口）；反转后的 `tests.test_quote_tech_chat_composer_alignment_red + tests.test_tech_direct_attachment_and_chat_capability_actions_red` → 13 项中 **2 失败**（即上述两条反转断言）。
- 全量（实际运行）：`python3 -m unittest discover -s tests -p 'test_*.py'` → **521 项、16 个失败点、7 跳过**，16 个失败点全部来自本批（新红测 10 个方法 + 2 条反转断言，含子测试拆分），其余既有测试保持通过。
- 边界：未修改任何业务实现；未改消息渲染、流式输出、历史会话、附件上传接口、Agent 工具、九阶段流程与右侧业务看板；未动报价 `#quickActions` 四个快捷按钮与 `#attachChips`；未动技术工艺工具栏 `#ocFilesAction`「任务文件」；未改工具返回文案与业务 service；未提交、未推送、未创建 MR/tag/Release、未部署。

## 12. 报价输入框统一为技术工艺单行样式、技术 ＋ 去回形针角标、恢复「会话绑定」说明行 实现（9-14）

- `确认需求解析结果.html`：
  - `.chat-input-wrapper` 由「display:flex + 旧 gap」改为与技术工艺 `.oc-inputbox-single` 逐项同几何的单行圆角框：`gap: 12px`、`min-height: 76px`、`border-radius: 24px`、`padding: 10px 12px 10px 20px`，配色改用本页 token（`var(--border-color)` 描边、`var(--bg-page)` 底），`:focus-within` 描边变 `var(--color-primary)`。
  - `#chatAttachBtn` 由 38×38 方形回形针改为 50×50 圆形 `+`（`.chat-attach-btn` 改 `border-radius: 50%`、去边框、补 hover / focus-visible / disabled）；`#chatSend` 由 38×38 方形渐变改为 54×54 圆形 `var(--color-primary)`；`#chatInput` 改 `rows="1"`，`.chat-input` 去边框 / 透明底 / `flex: 1`、`font-size: 15px`、`line-height: 24px`，保留 `max-height` 限制。
  - `.chat-input-wrapper` 之后补回 `<div class="oc-disc">会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。</div>`，并在本页 `<style>` 增加同口径 `.oc-disc`（`margin-top: 9px`、`font-size: 11px`、`text-align: center`、文字色用本页 `var(--text-tertiary)`），说明行留在正常文档流，不用绝对定位或负边距。
  - 报价业务内容与接线一律未动：`#quickActions` 四个快捷按钮、`#attachChips` 附件条、`#chatFileInput` 的 `type="file" multiple hidden` 与 accept 列表、`$('chatAttachBtn').onclick = () => $('chatFileInput').click()`、`#chatSend` 的 `sendFromInput()`、`#chatInput` 的 Enter / Shift+Enter 监听全部保持。
- `tech_app/frontend/tech-workbench.html`：`#ocChatAttachBtn` 删除第二个 `<i class="ti ti-paperclip oc-add-mark">` 角标，只保留 `ti-plus` 主字形；`.oc-inputbox-single` 之后恢复同一句 `.oc-disc` 说明行。
- `tech_app/frontend/agent-chat.css`：删除 `.oc-add .oc-add-mark` 规则与「契约要求保留这个字形」过时注释，`.oc-add` 本体尺寸（50×50 / `border-radius: 50%`）不变；顺手去掉随角标一起失效的 `position: relative`。
- 取代的旧契约按本批反转：技术工艺 composer 必须恢复 `.oc-disc`（原「不得出现」断言作废）；`#ocChatAttachBtn` 不再要求 `ti-paperclip`。未恢复输入框内的模型按钮（`ocModelSelect` 仍不存在）。
- 测试（实际运行）：`python3 -m unittest tests.test_quote_tech_unified_composer_and_caption_red -v` → **15/15 通过**（Red 基线 5 通过 / 10 失败、14 个失败断言全部转绿）；反转断言 `tests.test_quote_tech_chat_composer_alignment_red + tests.test_tech_direct_attachment_and_chat_capability_actions_red` → **13/13 通过**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **521 项、0 失败、7 跳过**；`node --check tech_app/frontend/agent-chat.js`、`node --check tech_app/frontend/tech-workbench.js` 与报价页内联脚本 `node --check` 通过；`git diff --check` 通过。
- 边界：未改消息渲染、流式输出、历史会话、附件上传接口、Agent 工具、九阶段流程与右侧看板；未动技术工艺工具栏 `#ocFilesAction`「任务文件」入口；未把技术工艺 `--oc-*` 变量引入报价页；未用 `display:none` / `visibility:hidden` 掩盖角标，节点与死 CSS 均为真删；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 13. 技术工艺步骤状态行并入统一标题行 Spec / Red（9-14）

- 需求：技术工艺每一步都有一行步骤状态（「已打开项目 <pid>（补充说明✓，佐证文件 0 个）」/「就绪」/「本步已确认」/「创建中」等），嵌入统一工作台后它变成标题行下方单独的一行（2.2/2.3 还同一句话出现两次）；要求这一行直接去掉，文字原样搬进标题行 —— 也就是「组装与整合 + 整合图纸 / 参数推荐 / 组装工艺」那一行。
- 定位（实测）：统一工作台标题行是 `.tech-workspace-context#techContextHeader`（`#techContextTitle` + `#techContextNotice` + `#techSubstepsBar`），`#techContextNotice` 目前只承载未就绪/失败提示；阶段页各自的状态行是 2.1 `index.html` 的 `.title-row > #status`、2.2 `assembly-integration.html` 的 `#status` + `.ai-status#aiPanelStatus`、2.3 `cost-review.html` 的 `#status` + `.ai-status#crStatus`、1.1/1.2/1.3 需求页模板里的 `.status-badge`；`tech-embed.js` 只隐藏了 `.form-title` 与 `.ai-tabs`，状态徽标留在原位；`tech-board-bridge.js` 的 `STATE_EVENTS` 白名单里没有任何承载步骤状态的事件。
- 新增 Spec：`docs/specs/tech-step-status-moved-to-context-title-row.md`：新增看板 → 父壳标准状态事件 `board-status`（`type: 'state'`，`payload: { text, level }`，复用既有信封与校验），运行时导出 `TechBoardRuntime.publishStatus(text, level)`，父壳桥加入白名单；阶段页把同一段文字原样上报；嵌入态用 `.tech-embed` 作用域隐藏 `.title-row .status-badge` / `#status` / `.ai-status`；父壳把 `payload.text` 原样写进 `#techContextNotice`，未就绪/失败提示优先、清除后回落到最近一次状态、切换 stage 丢弃上一步文本。
- 新增红测：`tests/test_tech_step_status_in_context_row_red.py`（10 项），覆盖运行时事件与 `publishStatus` 导出、父壳桥白名单、父壳订阅并把 `payload.text` 写进 `#techContextNotice`、错误提示优先级与切 stage 清理、2.1/2.2/2.3 三个状态写入口原样上报、1.1/1.2/1.3 三个需求页上报状态徽标、嵌入态隐藏规则必须限定 `.tech-embed` 且无全局隐藏规则，以及标题行三节点保留、既有六个 state 事件不变、状态文案不变三条保留守卫。
- Red 基线（实际运行）：`python3 -m unittest tests.test_tech_step_status_in_context_row_red -v` → **10 项中 7 失败、3 通过**（3 项保留守卫为标题行三节点、既有 state 事件白名单、状态文案不变）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **531 项、11 个失败点、7 跳过**，11 个失败点全部来自本批新增红测。
- 边界：本批只新增 Spec、红测与周 changelog，未修改任何前端/后端实现；未改状态文案、九阶段 stage id、顶部流程条、右侧业务卡结构、底栏代理与左侧会话；未新增第二套通信通道，也未改既有六个 state 事件语义；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 14. 技术工艺步骤状态并入统一标题行 实现（9-14）

- 需求：技术工艺每一步那行步骤状态（「已打开项目 <pid>（补充说明✓，佐证文件 N 个）」/「就绪」/「本步已确认」/「创建中」…）不再在阶段页单独占一行，同一段文字原样并入统一工作台标题行 `#techContextHeader`；独立打开阶段页时页内状态行照旧显示。
- 看板协议：`tech-board-runtime.js` 的 `EVENT` 新增 `BOARD_STATUS: 'board-status'`，并新增 `publishStatus(text, level)`（内部复用既有 `publish()`，payload 放 `text` + `level`：`'info' | 'error'`），在 `window.TechBoardRuntime` 上导出；`tech-board-bridge.js` 的 `STATE_EVENTS` 白名单加入同名事件，信封 `namespace/version/type/requestId/projectId/stage` 与 origin/source/projectId/stage 校验一律不变。
- 阶段页上报：2.1 `app.js` 的 `status(msg, busy)`（把 `(busy ? "处理中 · " : "") + msg` 提取为 `text` 后上报，文案一字未改）、2.2 `assembly-integration.js` 的 `aiStatus(message, error)`、2.3 `cost-review.js` 的 `crStatus(message, error)` 在写本地元素的同时上报同一段文字，level 由 `error` 决定；1.1/1.2/1.3 需求页（`requirement-create.js` / `requirement-confirm-page.js` / `requirement-review-page.js`）在渲染后读取 `.title-section .title-row .status-badge` 的可见文本原样上报。全部带 `window.TechBoardRuntime && typeof publishStatus === 'function'` 守卫，独立打开（无运行时）不通信。
- 嵌入态隐藏（仅 `.tech-embed` 作用域）：`tech-embed.js` 注入样式补 `.tech-embed .title-row .status-badge`、`.tech-embed #status`、`.tech-embed .ai-status` 三条 `display:none !important`，只去掉单独的状态行；`.title-row` 内的度量、文件名、错误与业务提示、`.title-section` 其余内容均保留，2.1 的 `#partsMetric` / `#confidenceMetric` 不受影响。
- 父壳渲染：`tech-workbench.js` 的 `state` 增 `boardStatus` / `boardNotice`；`setBoardNotice(message)` 渲染 `boardNotice || boardStatus`，未就绪/失败提示优先、清除后回落到最近一次步骤状态、都没有时 `hidden`；订阅 `board-status` 时只把 `payload.text` 原样存进 `state.boardStatus` 并刷新提示位，不覆盖固定大标题、不影响子页签；`mountStageFrame()` 在断开旧看板后清空 `boardStatus` / `boardNotice`，避免上一步文本带到下一步。
- 测试（实际运行）：`python3 -m unittest tests.test_tech_step_status_in_context_row_red -v` → **10/10 通过**（Red 基线 3 通过 / 7 失败、11 个失败断言全部转绿）；回归 `tests.test_tech_board_bridge_protocol_red + tests.test_quote_tech_chat_composer_alignment_red` → **12/12 通过**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **531 项、0 失败、7 跳过**；`node --check` 覆盖 `tech-board-runtime.js`、`tech-board-bridge.js`、`tech-workbench.js`、`tech-embed.js`、`assembly-integration.js`、`cost-review.js`、`requirement-create.js`、`requirement-confirm-page.js`、`requirement-review-page.js`，`app.js` 以 `node --input-type=module --check` 通过；`git diff --check` 通过。
- 边界：未改任何状态文案与 `status()` / `aiStatus()` / `crStatus()` 调用点语义；未改九阶段 stage id、页面映射、顶部流程条、右侧业务卡结构、底栏代理与左侧会话；未改既有六个 state 事件名与语义，未放宽 `tech:command` 校验强度；未用 `iframe.contentDocument` 或子页面 selector 直接读 DOM；未改后端路由、Agent 工具与业务数据；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 15. 技术工艺右侧看板业务按钮统一到左侧会话操作栏 Spec / Red（9-14）

- 需求：右侧看板（阶段页页内按钮 + 参数推荐/组装工艺操作行 + 成本三个去向按钮 + 业务卡底栏主/次按钮）的业务入口全部统一到左侧会话操作栏，保留全部功能同时不重复；每个状态只保留一个主按钮；删掉左侧「附件」按钮（输入区圆形 ＋ 已是唯一上传入口）；所有按钮必须有悬浮 tooltip。
- 缺口（实测）：左侧 `#techChatActions` 写死了 `techChatAttach` / `techChatAiRun` / `techChatSecondary` / `techChatBulk`，父壳只认识 `STAGE_ACTIONS` / `STAGE_CHAT_ACTIONS` 里少数动作名，看板实际注册的 38 个动作大部分在左侧没有入口；同一动作被渲染三次（左侧 `techChatPrimary` + `techChatBulk` 都是 `.primary`、右侧底栏 `#techPrimary`/`#techSecondary`、阶段页页内按钮）；2.2 参数推荐/组装工艺的生成、保存、确认、智能补全、保存补填、确认参数已齐只在 `aiRenderActions()` 里生成，没有注册成看板动作；看板动作快照没有 role/order/hint，父壳无法选出唯一主按钮，也无法生成 tooltip；嵌入态仍显示 `#btnParse` / `#aiStart` / `#aiActions` / `#aiOpsCard .ai-ops` / `#crRunAll` / `#crOpsCard .ai-ops` / `#btnAiExtract`。
- 新增 Spec：`docs/specs/tech-board-actions-into-left-session-toolbar.md`：`tech-board-runtime.js` 的 `entryState()` 透传归一化后的 `role`（`'primary'` / `'aux'`，非白名单降级为 `'aux'`）/`order`/`hint`；父壳删除 `STAGE_ACTIONS`、`STAGE_CHAT_ACTIONS`，改为 `STAGE_CHAT_FLOW`（九阶段只保留 prev/next/transfer 壳导航标记）+ 看板快照驱动的 `boardActionEntries()` / `primaryActionName()` / `actionTooltip()` / `syncChatActionList()`，动态按钮带 `data-tech-action` 并插到 `#techChatPrimary` 之后，全文件只允许一处 `variant: 'primary'`；tooltip 同时写 `title` 与 `aria-label`，禁用时说明原因（执行中 / 当前不可用 / 请先打开项目）；2.2 新增 8 个看板动作（生成参数推荐、生成组装工艺、保存参数、确认参数推荐、确认组装工艺、智能补全、保存补填、确认参数已齐）全部复用既有 `aiGenerate` / `aiSaveEdits` / `aiConfirmStep` / `aiParamsAutofill` / `aiParamsFinalize`，并按 `aiTab` 决定 `visible`；嵌入态用 `.tech-embed` 作用域隐藏第二份入口（只藏按钮行，保留产品名称/数量输入与说明），上传入口 `#aiUploadBtn`/`#aiDrawingInput` 仍留在看板内（跨文档 `input.click()` 会丢 user activation）。
- 新增红测：`tests/test_tech_board_actions_into_left_toolbar_red.py`（17 项），覆盖附件按钮删除、静态业务按钮改动态渲染、渲染 helper 存在、唯一 `variant: 'primary'`、主按钮由 `role === 'primary'` 决定且排除自身、tooltip 规则与 `aria-label`、父壳不再写死 38 个动作名、`STAGE_CHAT_FLOW` 覆盖九阶段、底栏业务按钮退役、看板运行时 role/order/hint 透传、每阶段 role/order 声明与静态 primary ≤1、2.2 新增动作复用既有实现并按 `aiTab` 定可见性、嵌入态 `.tech-embed` 作用域隐藏且不隐藏输入与说明、底层实现函数与上传入口保留。
- 反转的旧契约（本批取代，不作为回归失败）：`tests.test_tech_left_toolbar_parity_red` 的 `TOOLBAR_IDS` 去掉 `techChatAttach`/`techChatAiRun`/`techChatSecondary`，`KEPT_IDS` 去掉 `techPrimary`/`techSecondary` 并新增底栏业务按钮退役断言，`STAGE_CHAT_ACTIONS` 断言改为 `STAGE_CHAT_FLOW`，`executeAction` 守卫由「必须复用 STAGE_ACTIONS」改为「必须来自看板快照」；`tests.test_tech_primary_quick_actions_and_bulk_part_analysis_red` 中「批量入口写死在父壳」改为「批量入口仍注册在 2.1 看板且带 role、由看板快照动态渲染到左侧」。
- Red 基线（实际运行）：`python3 -m unittest tests.test_tech_board_actions_into_left_toolbar_red -v` → **17 项中 3 通过、14 项失败（49 个断言失败点）**（3 项通过为底层实现保留、既有左侧通道保留、上传入口保留三条守卫）；反转后的 `tests.test_tech_board_actions_into_left_toolbar_red + tests.test_tech_left_toolbar_parity_red + tests.test_tech_primary_quick_actions_and_bulk_part_analysis_red` 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **548 项、51 个失败点、7 跳过**（49 个来自本批新增红测，2 个来自本批反转的旧契约，其余全绿）。
- 边界：本批只新增 Spec、红测并更新被取代的旧断言，未修改任何前端/后端实现；未改业务算法、`cpq:tech-board` 信封、六个 state 事件、`tech:command` 方向与 projectId/stage 校验；未删节点与既有实现函数（嵌入态只隐藏按钮）；未新增后端路由或 Agent 工具；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 16. 会话气泡统一白底 + 边框、运行过程去噪可展开、思考过程折叠块 Spec / Red（9-14）

- 需求：报价与技术工艺两个 Agent 的会话外观统一按报价那样（白底气泡 + 气泡边框、背景一律白色）；报价轨迹行「生成 / 更新「XX」表单 · 来源：…」不再灰底，改白底带边框；技术工艺侧反复出现的无内容「处理中…已完成」去掉，有过程的要能点击展开；两端运行过程中都要有「默认隐藏、点击展开」的模型思维链，两个 Agent 输出风格统一。
- 缺口（实测）：
  - 技术 `.oc-amsg` 只有 `display:flex; gap:11px`，没有白底气泡、边框和助手 label 行；报价 `.tool-activity.trace` 仍用 `var(--bg-secondary)` 灰底。
  - `tech-board-runtime.js:247/259/269` 的 `task-progress` / `task-failed` / `task-completed` payload 只有 `{action, phase}`，没有 label / taskId / log；`agent-chat.js:1190 renderTaskProgress()` 把 label 兜底成「处理中」、建卡键是 `taskId || label || 'task'`，于是所有事件落进同一张空卡并在「进行中 → 已完成」之间反复翻转。
  - 供应商流本来就有 `thinking_delta`，但技术 `oc_agent.py::_stream_once()` 与报价 `cpq_agent_server.py::_stream_once()` 只转发 `text_delta / tool_use_* / message_end`，思维链被静默丢弃；两端都没有折叠块；报价系统提示强制的「🤔 思考 / 📋 规划 / ⚙️ 执行」段落现在落在正文气泡里。
- 新增 Spec：`docs/specs/chat-white-bubble-and-expandable-run-progress.md`（助手气泡与卡片白底 + 1px 边框、助手 label 行、轨迹行去灰底、看板 `task-*` payload 补 `label`/`taskId` 并把 `pollTask` 已有 detail 经 `TechBoardRuntime.publish` 转给父壳、空卡不渲染、步骤区改原生 `<details class="oc-task-fold">` 默认折叠 + `userToggled` 记忆）；`docs/specs/chat-collapsible-thinking-trace.md`（两端新增 `thinking` SSE 帧、`appendThinking()` / `appendThinkingText()` 生成默认折叠的「思考过程」、报价 `splitReasoningSections(raw) → { body, thinking }` 把思考/规划/执行段收进折叠块且保留 ✅ 结果段、思维链不写入会话事件不进历史回放、不新增路由、系统提示词不改）。
- 新增红测：`tests/test_chat_white_bubble_and_expandable_run_progress_red.py`（10 项）、`tests/test_chat_collapsible_thinking_trace_red.py`（16 项，含用 node 直接执行 `splitReasoningSections` 的行为校验：有标记时正文只留 ✅ 结果段、无标记时原文一字不改）。
- Red 基线（实际运行）：`python3 -m unittest tests.test_chat_white_bubble_and_expandable_run_progress_red` → **10 项、13 个失败点**；`python3 -m unittest tests.test_chat_collapsible_thinking_trace_red` → **16 项中 10 失败、6 通过**（6 项为既有流式分支保留、思维链不落库、不新增路由、历史回放既有分支这些守卫）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **574 项、34 个失败点、7 跳过**（除本批 23 个失败点外，其余来自并行进行中的左侧操作栏批次）。
- 边界：本批只新增 Spec 与红测，未修改任何前端/后端实现；未改既有 SSE 事件语义、未新增后端路由、未改任务轮询与桥协议、未动系统提示词；未删除历史会话或业务数据；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 17. 助手卡片融合风格（技术白卡 + 报价蓝色身份行 + 状态图标）Spec / Red（9-14）

- 背景：用户复核后重新确认融合口径——**保留技术工艺的卡片，内容默认展开；只有「思考过程」折叠；保留卡片右上角的运行中 / 已完成状态图标；同时保留报价「报价单智能体」那行蓝色身份字**。据此撤销上一批「技术 label 灰色、运行过程默认折叠」的写法。
- 三轮拍板结果：① 状态 chip 放在标题行右侧，不做卡片外悬浮角标；② 报价侧同样加同一个状态 chip；③ 工具卡「详情」（原始入参 JSON 排障入口）继续默认折叠。
- 取代：`docs/specs/chat-white-bubble-and-expandable-run-progress.md` 与 `tests/test_chat_white_bubble_and_expandable_run_progress_red.py` 已删除，替换为 `docs/specs/chat-fused-assistant-card-style.md` 与 `tests/test_chat_fused_assistant_card_style_red.py`；`docs/specs/chat-collapsible-thinking-trace.md` 补「身份行 → 思考过程 → 正文」插入顺序约定，其余条款不变。changelog `## 16.` 中该两文件名与红测基线数字随之作废，仅作历史记录保留。
- 新增 Spec：`docs/specs/chat-fused-assistant-card-style.md`：`.oc-amsg` 白底 + `1px solid var(--oc-border-2)` + 14px 圆角 + `11px 14px` 内边距；`.oc-art` / `.oc-task-card` / `.oc-intent-card` 同步改白底 + 1px 边框（`.oc-intent-card` 去掉 `var(--oc-bg-2)` 灰底）；新增蓝色身份行 `.oc-alabel`（`#0060E6` + 6px `linear-gradient(135deg,#0060E6,#0050C4)` 圆点，取报价 `--color-secondary` / `--gradient-ai` 同值，不依赖技术侧未定义 token）与右对齐状态 chip `.oc-alabel-state`（`is-running #e0edff/#0050C4 ◌`、`is-succeeded #dcfce7/#15803d ✓`、`is-failed #fee2e2/#b91c1c ⚠`，沿用任务卡状态配色）；`addAssistant()` 在文本节点前插入身份行，新增 `setAssistantState(ctx, state)` 就地翻转同一张 chip，`done` 置已完成、`error` 置失败；`pushSystem()` 告警卡不带 chip；报价 `.message-ai` / `.tool-activity.trace` 改白底 + 1px `var(--border-color)`、`.message-label-state` 同款三态 chip 并接到 `ensureStreamBubble` / `finishStreamBubble` / `addErrorBubble`；用户气泡 `.oc-ubub` / `.message-user` 保持主色实心不动。
- 新增红测：`tests/test_chat_fused_assistant_card_style_red.py`（14 项），覆盖技术白卡与内层卡白底 1px 边框、蓝色身份行与 6px 圆点、身份行仅由 `addAssistant()` 插入且 `pushSystem()` 不带 chip、chip 右对齐与三态配色字形齐全、`setAssistantState` 就地翻转且 `done`/`error` 接线、正文/工具轨迹/步骤默认展开（禁止再出现 `oc-task-fold` 与「过程详情」）、任务卡步骤不包 `details`、只有 `oc-thinking` 与 `oc-art-detail` 可折叠、看板 `task-*` payload 带 `label`/`taskId` 且看板页把真实 detail 转父壳、空卡不渲染、报价白卡与轨迹行、报价身份行 chip 三态接线与技术侧一致、用户气泡保持主色实心、既有实现与桥事件保留、不得新增后端路由。
- Red 基线（实际运行）：`python3 -m unittest tests.test_chat_fused_assistant_card_style_red -v` → **14 项中 11 失败（16 个失败点）、3 通过**（3 项通过为「内容默认展开」守卫、用户气泡主色守卫、既有实现与桥事件守卫）；`tests.test_chat_collapsible_thinking_trace_red` 仍为 **16 项中 10 失败、6 通过**；两文件合计 **30 项、26 个失败点**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **578 项、37 个失败点、7 跳过**（除本批 26 个失败点外，其余来自并行进行中的左侧操作栏批次）。
- 边界：本批只改 Spec / 红测（含删除本会话上一批自建的两个未跟踪文件），未修改任何前端/后端实现；未改桥协议、看板动作注册表、任务轮询与后端接口；未删除任何已跟踪文件、历史会话或业务数据；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 17. 技术工艺右侧看板业务按钮统一到左侧会话操作栏 实现（9-14）

- 看板协议：`tech-board-runtime.js` 的 `entryState()` 增加并归一化透传 `role`（与 `'primary'` 全等才算 primary，其余一律降级 `'aux'`）、`order`（可转成有限数字时取数字，否则 `null`）、`hint`（String）；静态条目与 `getState()` 动态返回都支持。既有 6 个 state 事件名、`cpq:tech-board` 信封与 `actionSnapshot()` 全量下发一律不变，不新增事件。
- 父壳渲染：`tech-workbench.js` 删除 `STAGE_ACTIONS` / `STAGE_CHAT_ACTIONS` / `syncActionBar()` / `renderActionButton()` / `openChatFilePicker()` / `chatActionEntry()` / `chatActionLabel()` / `boardActionReady()`，改为 `STAGE_CHAT_FLOW`（九阶段各一条 `{ prev, next, transfer }` 壳导航布尔标记，不含任何业务动作名）+ 唯一渲染路径 `boardActionEntries()` / `primaryActionName()` / `actionTooltip()` / `syncChatActionList()`。动态按钮用 `document.createElement('button')` 建、带 `data-tech-action="<动作名>"`、用 `insertAdjacentElement('afterend')` 插到 `#techChatPrimary` 之后，每次同步先清掉上一批 `[data-tech-action]` 再重建；主按钮只认 `role === 'primary'`（按 order、注册顺序取第一个，没有就隐藏，不猜不兜底），全文件只剩一处 `variant: 'primary'`；点击统一走 `TechBoardBridge.executeAction(name, { label, role })`，切 stage / 重挂 iframe 丢弃上一步按钮。
- tooltip：`setChatButton()` 同时写 `title` 与 `aria-label`；文案规则 <label>；有 `hint` 时 `label：hint`；禁用时 `label（原因）`，原因优先级 `busy → 执行中`、`enabled === false → hint || 当前不可用`、无项目 `→ 请先打开项目`；`#techChatPrimary` 额外标注「当前步骤主操作」，动态按钮用 `setAttribute('aria-label', ...)` 写入。
- `tech-workbench.html`：删除静态 `#techChatAttach` / `#techChatAiRun` / `#techChatSecondary` / `#techChatBulk` 与底栏 `#techPrimary` / `#techSecondary`；`#techChatPrimary` 保留为唯一主按钮槽位；`#ocFilesAction` / 5 个 `data-tech-capability` / `#ocResultActions` / `#ocTaskProgressHost` / `#ocChatAttachBtn` / `#ocChatFileInput` / `#techPrev` / `#techNext` / `#techNowLabel` 全部保留。
- 看板阶段页（只补 role / order / hint 元数据 + 注册缺失动作，不改业务实现）：1.1 `submitRequirement` primary/10，1.2 `confirmRequirement` primary/10，1.3 `submitRequirementReview` primary/10，2.1 `parseDrawing` primary/10 + `runAllPartProcesses` aux/20 + `modelLookup` / `verify` / `searchComponents` aux/30/40/50，2.2 `runIntegration` / `sendIntegrationToFinance` 由 `getState()` 按 `aiAnalyzed()` 动态返回 primary / aux（order 10/20），2.3 `runCostReview` primary/10 + `confirmCostReview` aux/20 + 三个去向 aux/30/40/50，3.1 / 3.2 / 3.3 各自 primary/10。2.2 新增 8 个动作 `generateIntegrationParams` / `generateIntegrationProcess` / `saveIntegrationParams` / `confirmIntegrationParams` / `confirmIntegrationProcess` / `autofillIntegrationParams` / `saveIntegrationParamsFinal` / `confirmIntegrationParamsFinal`，全部 `deferred: true` 且分别复用既有 `aiGenerate('params'|'process')` / `aiSaveEdits('params')` / `aiConfirmStep('params'|'process')` / `aiParamsAutofill()` / `aiParamsFinalize(false|true)`，按 `aiTab === 'params' | 'process'` 决定 `visible`，参数推荐专属按钮只在参数页签可见。
- 嵌入态去重：`tech-embed.js` 注入样式新增 7 条 `.tech-embed` 作用域隐藏规则（`#btnParse` / `#aiStart` / `#aiActions` / `#aiOpsCard .ai-ops` / `#crRunAll` / `#crOpsCard .ai-ops` / `#btnAiExtract`），每条都带 `.tech-embed` 前缀、无全局隐藏规则；只隐藏按钮行，卡片里的产品名称与数量输入、说明文字全部保持可见；页内 `onclick` / `disabled` 读写与底层实现函数（`parseDrawing` / `startAllPartProcesses` / `aiRunIntegration*` / `aiGenerate` / `aiSaveEdits` / `aiConfirmStep` / `aiParamsAutofill` / `aiParamsFinalize` / `crRunAll` / `crConfirmCost` / `crRunOp` / `rcExtractRequirementFields` / `/requirement/extract-documents` / `srSave` / `/process-report/*`）一个未删；上传入口 `#aiUploadBtn` / `#aiDrawingInput` 仍留在 2.2 看板内（跨文档 `input.click()` 会丢 user activation）。
- 取代的旧契约按 Spec §6 反转：`tests.test_tech_left_toolbar_parity_red`（`TOOLBAR_IDS` / `KEPT_IDS` 去掉已退役按钮、`STAGE_CHAT_ACTIONS` 断言改 `STAGE_CHAT_FLOW`、`executeAction` 改为必须来自看板快照）、`tests.test_tech_primary_quick_actions_and_bulk_part_analysis_red`（批量入口不再写死在父壳，改为 2.1 看板注册 + 看板快照动态渲染到左侧）。
- 测试（实际运行）：`python3 -m unittest tests.test_tech_board_actions_into_left_toolbar_red -v` → **17/17 通过**（Red 基线 3 通过 / 14 失败，49 个失败断言全部转绿）；`python3 -m unittest tests.test_tech_result_entries_board_views_red -v` → 4/4 通过；`node --check` 覆盖 `tech-board-runtime.js`、`tech-board-bridge.js`、`tech-workbench.js`、`tech-embed.js`、`assembly-integration.js`、`cost-review.js`、`requirement-create.js`、`requirement-confirm-page.js`、`requirement-review-page.js`、`summary-result.js`、`report-review-result.js`、`report-publish-result.js`，`app.js` 以 `node --input-type=module --check` 通过；`git diff --check` 通过。
- 待契约方反转的旧断言（Spec §6 清单未覆盖；本批按 §4.1 / §4.3 删除 `STAGE_ACTIONS`、`STAGE_CHAT_ACTIONS`、`syncActionBar()` 与底栏业务按钮后必然失败，共 11 个失败点 / 9 个文件，等契约方按本批语义反转）：`test_tech_left_toolbar_parity_red::test_attach_reuses_the_hidden_file_input_not_a_menu`（应改查输入区圆形 ＋ 的接线）、`test_tech_primary_quick_actions_and_bulk_part_analysis_red::test_cost_bulk_action_uses_one_click_all_copy_and_existing_pipeline`（「一键测算全部成本」应改查 2.3 看板）、`test_tech_board_action_registry_red::test_parent_stage_actions_use_business_names_not_selectors`、`test_tech_agent_remove_stage_context_card_red::test_unified_chat_action_bar_and_board_action_routing_remain`、`test_tech_assembly_action_button_states_red`（2 个）、`test_tech_context_substeps_and_frame_status_red::test_iframe_load_still_syncs_action_bar`、`test_tech_right_workspace_quote_rounded_card_red::test_existing_stage_and_bridge_contract_is_preserved`、`test_quote_tech_agent_shell_parity_red`（2 个）、`test_tech_cost_report_handoff_continuity_red::test_report_publish_parent_exposes_sales_handoff_action`。实现未为迁就这些断言恢复 `STAGE_ACTIONS` / `STAGE_CHAT_ACTIONS` / `syncActionBar()` / 底栏业务按钮或左栏附件按钮。
- 边界：未删任何底层实现函数与既有节点（嵌入态只隐藏按钮行）；未改 `cpq:tech-board` 信封、既有六个 state 事件名与语义、`tech:command` 方向与 `projectId` / `stage` 校验强度；父壳未用 `contentDocument` / `contentWindow.document`；未新增后端路由、Agent 工具与接口字段；未改九阶段 stage id、页面映射、顶部流程条、标题行、结果入口与任务进度宿主；独立打开阶段页（无 `embed=1`）时页内按钮与页脚照旧；未清空、迁移或覆盖任何历史业务数据。

## 18. 标题行步骤状态恢复蓝底蓝字胶囊、红色只留给失败提示 Spec / Red（9-14）

- 问题：统一工作台标题行提示位 `#techContextNotice` 把步骤状态原文（`已打开项目 fcac7095bded（补充说明✓，佐证文件 0 个）`、`就绪`、`本步已确认`…）渲染成红字，与改造前的蓝色胶囊不一致。
- 定位（实测）：`tech_app/frontend/tech-workbench.css:531` 的 `.tech-context-notice` 只有 `color: var(--color-red, #d92d20)`，没有背景与圆角，本意只服务「看板未就绪 / 失败提示」，`flex: 1 1 auto` 还会拉满整条标题行；`tech_app/frontend/tech-workbench.js:1212`（`bindBoardBridge` 的 `board-status` 分支）只搬 `payload.text`、**丢掉 `payload.level`**，`setBoardNotice(message)`（`:288`）也没有等级参数，于是 info 级状态继承了失败态红色。页内独立打开时的状态是 `workbench.css:17` 的 `.status-badge`（`--color-primary-light` 底 + `--color-primary` 字 + 全圆角），两侧视觉不一致。
- 新增 Spec：`docs/specs/tech-step-status-blue-pill-in-title-row.md`：① 基础态 `.tech-context-notice` 改 `flex: 0 1 auto` + `padding: 2px 10px` + `border-radius: 999px` + `background: var(--color-primary-light, #E6F0FD)` + `color: var(--color-primary, #0060E6)`，保留 `overflow/text-overflow` 省略号，基础规则内不得再出现红色 token（父壳只声明了 `--color-primary`，浅蓝必须带 hex 兜底）；② 新增失败态修饰类 `.tech-context-notice.is-error`（`var(--color-red-light, #fee2e2)` 底 + `var(--color-red, #d92d20)` 字）；③ 父壳 `state` 增提示位等级字段，`board-status` 分支按 `payload.level === 'error' ? 'error' : 'info'` 归一，桥的 `error` / `task-failed` 显式 `setBoardNotice(message, 'error')`，`setBoardNotice(message, level)` 用 `classList.toggle('is-error', …)` 切换，文案仍是 `state.boardNotice || state.boardStatus` 且颜色跟随当前真正显示的那条；`applyStage()` 切步时连等级一起清空。
- 新增红测：`tests/test_tech_step_status_blue_pill_red.py`（12 项），覆盖基础态浅蓝底 + 主色蓝字 + hex 兜底、基础规则无红色 token、胶囊圆角与内边距与省略号、胶囊不拉满整行、失败态独立修饰类、`[hidden]` 保留、`board-status` 分支必须读 `payload.level` 且保留 `payload.text`、失败提示显式标 error 且用 class 切换、`setBoardNotice` 优先失败提示并回落步骤状态、`applyStage` 连等级一起清、父壳不得把业务文案写成字面量或给状态文案拼前后缀、协议与各阶段页页内状态行及 `tech-embed.js` 隐藏规则不得改动。
- Red 基线（实际运行）：`python3 -m unittest tests.test_tech_step_status_blue_pill_red -v` → **12 项中 9 失败（10 个失败点，含 1 个子断言）、3 通过**（3 项通过为「`[hidden]` 规则保留」「父壳不改写业务文案」「协议与页内状态行不变」三项保护守卫）；回归 `tests.test_tech_step_status_in_context_row_red` + `tests.test_tech_board_bridge_protocol_red` → **16/16 通过**。
- 边界：本批只新增 Spec、红测与周 changelog，未修改任何前端/后端实现；未改 `board-status` 事件名、`payload` 形状与 `level` 白名单，未改 `TechBoardRuntime.publishStatus` 调用点；未改九阶段 stage id、页面映射、`#techContextTitle` / `#techSubstepsBar`，未改阶段页 `.status-badge` / `#status` / `.ai-status` 与 `tech-embed.js` 隐藏规则；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 19. 助手卡片融合风格（技术白卡 + 报价蓝色身份行 + 状态 chip）与思考过程折叠块 实现（9-14）

- 技术工艺会话（`agent-chat.css` / `agent-chat.js`）：`.oc-amsg` 改白底 `#ffffff` + `1px solid var(--oc-border-2)` + 14px 圆角 + `11px 14px` 内边距；`.oc-art` / `.oc-task-card` 由 `.5px` 改 1px 并写死白底，`.oc-intent-card` 去掉 `var(--oc-bg-2)` 灰底改白底 + 1px；新增蓝色身份行 `.oc-alabel`（`#0060E6` 字 + `6px` `linear-gradient(135deg,#0060E6,#0050C4)` 圆点，取报价 `--color-secondary` / `--gradient-ai` 同值字面量）与右对齐状态 chip `.oc-alabel-state`（`is-running #e0edff/#0050C4 ◌ 运行中`、`is-succeeded #dcfce7/#15803d ✓ 已完成`、`is-failed #fee2e2/#b91c1c ⚠ 失败`，与任务卡四态同配色）。`addAssistant()` 在 `.oc-abody` 文本节点前插入身份行（默认运行中），新增 `setAssistantState(ctx, state)` 只改同一张 chip 的 class 与文案；`handleEvent()` 的 `done` 翻「已完成」、`error` 翻「失败」，外层网络错误同样翻失败，历史回放创建的气泡直接置「已完成」；`pushSystem()` 告警卡不带 chip。
- 默认展开：正文 / `.oc-art` 工具轨迹 / `.oc-task-card` 步骤 / 结果卡 / `.oc-intent-card` 全部默认可见，只保留 `details.oc-thinking`（思考过程）与 `details.oc-art-detail`（工具详情）两个折叠项；未引入 `oc-task-fold` 与「过程详情」。
- 任务事件去噪：`tech-board-runtime.js::runEntry()` 三处 `publish(EVENT.TASK_PROGRESS / TASK_COMPLETED / TASK_FAILED)` 补 `label: entryState(name).label` 与 `taskId: context.taskId || ''`（失败事件保留原 `message`）；`app.js::pollTask()` 与 `inline-analysis.js::poll()` 在既有 `agent:task-progress` 事件之外，把同一份 `{ label, taskId, status, progress, log, error }` 经 `window.TechBoardRuntime && TechBoardRuntime.publish(按 status 取 task-progress / task-completed / task-failed, 'board-task', detail)` 转给父壳，独立打开无运行时行为不变；`renderTaskProgress()` 去掉「处理中」兜底，`label / taskId / log / progress` 全空直接 return，不建空卡。
- 报价会话（`确认需求解析结果.html`）：`.message-ai` 改白底 + `1px solid var(--border-color)`，`.tool-activity.trace` 由灰底 `.5px` 改白底 1px 12px 圆角，新增 `.message-label-state` 同款三态 chip，「报价单智能体」文案保留；`ensureStreamBubble()` 生成 `is-running` chip、`finishStreamBubble()` 翻 `is-succeeded`、`addErrorBubble()` 翻 `is-failed`；用户气泡 `.oc-ubub` / `.message-user` 保持主色实心不动。
- 思考过程（思维链）：技术 `tech_app/backend/services/oc_agent.py::_stream_once()` 与报价 `cpq_agent_server.py::_stream_once()` 新增 `thinking_delta` → `{"type": "thinking", "text": ...}` 透明转发；思维链不写入会话事件、不落库、不进历史回放、不新增路由、不改系统提示词。技术 `agent-chat.js` 新增 `handleEvent()` 的 `thinking` 分支与 `appendThinking(ctx, text)`（空文本 return，首帧在文本节点前插入 `details.oc-thinking`），报价新增 SSE `case 'thinking'` → `appendThinkingText(text)`（同样插在 `.message-text` 之前）。新增 `.oc-thinking` / `.thinking-block` 白底 + 1px 边框样式（无灰底 token），插入顺序固定「身份行 → 思考过程 → 正文」。
- 报价正文收拢：`splitReasoningSections(raw) → { body, thinking }` 把行首 `🤔 思考 / 📋 规划 / ⚙️ 执行` 到下一个标记或 `✅` 为止的段落移入折叠块，`✅ 结果` 及之后留在正文；一个标记都没命中时正文一字不改。`finishStreamBubble()` 在 `renderMarkdown()` 之前调用该函数。
- 实现说明：`splitReasoningSections` 的契约红测会把函数体单独取出、在 node 里直接执行（`tests/test_chat_collapsible_thinking_trace_red.py`），因此实现写成「装配函数 + 内层自包含实现」并在脚本加载时装配一次到同名全局：函数体单独执行也能得到可用实现，页面里同名引用即实现本身。
- 验证：`python3 -m unittest tests.test_chat_fused_assistant_card_style_red` → 14/14 通过；`tests.test_chat_collapsible_thinking_trace_red` → 16/16 通过；回归 `tests.test_tech_step_status_in_context_row_red`、`tests.test_tech_board_bridge_protocol_red`、`tests.test_quote_tech_chat_composer_alignment_red` → 22/22 通过；`node --check` 覆盖 `agent-chat.js` / `inline-analysis.js` / `tech-board-runtime.js`，`node --input-type=module --check < app.js` 通过，`python3 -m py_compile` 覆盖 `oc_agent.py` 与 `cpq_agent_server.py` 通过。
- 已知契约冲突（未改测试，等待 Codex 更新）：`tests/test_quote_tech_ai_message_white_surface_red.py` 三条断言与本批新 Spec 直接冲突——`test_quote_ai_message_uses_system_white_surface` 要求 `.message-ai` 背景为 `var(--bg-page)` 且不得出现 hex/rgba/gradient，`test_quote_ai_message_is_not_a_bordered_tail_bubble` 要求 `.message-ai` 不得出现 `border`，`test_tech_plain_ai_message_remains_without_colored_container_background` 要求 `.oc-amsg` 背景不得出现 hex；而新 Spec（`docs/specs/chat-fused-assistant-card-style.md`）明确要求 `.message-ai` / `.oc-amsg` 白底 + `1px solid` 边框。该文件改动前为通过状态，属被新 Spec 取代的过期契约。
- 边界：只改 `agent-chat.css` / `agent-chat.js` / `tech-board-runtime.js` / `app.js` / `inline-analysis.js` / `确认需求解析结果.html` / `tech_app/backend/services/oc_agent.py` / `cpq_agent_server.py` 与本周 changelog；未新增文件；未改 `cpq:tech-board` 信封、六个 state 事件、`tech:command` 方向与 origin / projectId / stage 校验；未改 `pollTask()` 轮询与提交逻辑、后端任务接口，未新增后端路由与 SSE 通道，未改系统提示词；未删 `pushTaskStep` / `toneOf` / `sanitizeTaskDetail` / `setTaskStatus` / `taskProgressHost` / `.oc-task-card` / `.oc-task-steps` / `.oc-art-detail` 与报价 `addToolActivity` / `showStage` / `showTyping` / `describeTool` 及其调用点；思维链不落库、不进历史回放、不计 token；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 20. 技术工艺业务动作按钮一律可点、点了再给真实原因（含 2.2/2.3 永久灰修复）Spec / Red（9-14）

- 问题：2.2「确认工艺并发送财务」与 2.3「确认成本」在统一工作台左侧会话区永久灰掉、点不动，也没有任何原因，间接导致「回传销售经理继续报价」整条链路走不下去。
- 定位（实测）：`assembly-integration.js:1513` 的 `sendIntegrationToFinance.getState()` 直读页内按钮 `enabled: Boolean(button) && !button.disabled`，而 `:847` 的 `financeBtn.disabled = !ready` 要求参数/工艺已确认、参数已齐且无缺项四项同时成立；`cost-review.js:779` 的 `confirmCostReview.getState()` 同样直读 `#crConfirm`，而 `:385` 的 `disabled = !ready` 里 `ready = crData.ready && !crBusy && !readOnly`。按钮一灰，`run()` 里已经写好的结构化错误（`assembly-integration.js:1485`、`cost-review.js:773`）永远跑不到。父壳还把 `entry.enabled === false` 直接变成 `disabled`，并在 tooltip 里写「当前不可用」。
- 定位（实测，第二处）：看板收尾不重新发布快照 —— `crConfirmCost()`（`cost-review.js:602`）与 `crRunOp()`（`:482`）的 finally 只做 `crBusy = false; crRender();`，`action-state` 只在 `updateActionState()` 与注册时发布，父壳缓存里的 `enabled` 一直停在上一次的值，业务状态变好也不会亮。
- 定位（实测，第三处）：`cost-review.js:34` 的 `CR_COST_ROLES = ['finance_mgr','admin']` 与后端权威白名单 `tech_app/backend/services/auth.py:55` 的 `COST_ROLES = {"finance_manager","admin"}` 不一致，真正的财务负责人被前端误判只读。
- 定位（实测，第四处，静默返回）：`aiOpenFinanceDialog()`（`assembly-integration.js:907`）开头 `if (aiBusy) return;`、父壳 `runBoardAction` 的 `if (!state.project) return;` —— 点了没反应，用户只会当成系统坏了。
- 新增 Spec：`docs/specs/tech-business-actions-clickable-then-error.md`：① 业务动作 `enabled` 只表达「这一步有这个动作」，前置条件不再灰按钮，忙闲只由 `busy` 表达；② `run()` 必须自己判前置并返回 `{ ok: false, error: { code, message } }`，message 复用页内既有 `why` 文案；③ 父壳失败必须写进标题行提示位（error 等级，接上一批的 `.is-error`）并经 `window.ocTechAgent.notice` 同步一条会话提示，禁止静默 return；④ `tech-board-runtime.js` 新增并导出 `refreshState()`（只重发快照、不写 `overrides`），`assembly-integration.js` / `cost-review.js` 渲染后经 `aiPublishState()` / `crPublishState()` 调用（同帧合并一次）；⑤ 新增 `aiFinanceBlocker()` / `crConfirmBlocker()` 作为唯一判定，页内 `why` 与动作共用；⑥ `CR_COST_ROLES` 与后端 `auth.COST_ROLES` 对齐，只读身份不再拦在按钮前面（后端 `_require` 仍是权威）。
- 新增红测：`tests/test_tech_business_actions_clickable_then_error_red.py`（24 项），覆盖父壳不再按 `enabled === false` 禁用与 tooltip 不再写「当前不可用」、`runBoardAction` 不静默返回且失败按 error 等级上报并同步会话、`agent-chat.js` 导出 `notice`、运行时导出 `refreshState()` 且不写 overrides、`entryState()` 字段契约不变、2.2/2.3 的 `getState()` 不得直读页内按钮 `disabled` 且 `enabled` 一律 `true`、发送财务与确认成本的 blocker 单源 + 结构化失败、`aiOpenFinanceDialog()` 忙时返回结构化错误、两页渲染后重新发布快照、前后端成本角色码一致且不得再出现 `finance_mgr`，以及桥事件白名单 / 后端路由与 `_require(..., auth.COST_ROLES)` / 页内按钮自身 disabled 语义三条保护守卫。
- Red 基线（实际运行）：`python3 -m unittest tests.test_tech_business_actions_clickable_then_error_red -v` → **24 项中 20 失败（37 个失败点，含子断言）、4 通过**（4 项通过为 `entryState()` 字段契约、页内按钮 disabled 语义、桥协议与后端路由与 `_require`、2.3 只读未拦在动作前四项保护守卫）。
- 边界：本批只新增 Spec、红测与周 changelog，未修改任何前端/后端实现；未改 `cpq:tech-board` 信封、事件白名单、`payload` 形状与 `entryState()` 字段契约；未改后端任何路由、权限判定与 403 语义；未改页内按钮自身的 disabled 语义；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 21. 技术工艺左侧会话操作栏去掉通用刷新与导航按钮 Spec / Red（9-14）

- 问题：左侧会话操作栏每一页都混进两类非业务按钮 —— ① `tech-workbench.html:121-124` 的静态 `#techChatPrev` / `#techChatNext` / `#techChatTransfer` / `#techChatRetry`（上一步 / 下一步 / 转交任务 / 失败重试），与顶部流程条 `#techPrev` / `#techNext` / 大步骤按钮 / 子页签重复；② 每个 stage 页的刷新动作 `getState()` 都返回 `visible: true`，于是每页多出一颗「刷新需求看板 / 刷新整合看板 / 刷新成本看板 / 刷新汇总报告 / 刷新审核报告 / 刷新发布报告」。
- 定位（实测）：刷新动作共九处 —— `app.js:2549 refreshData`、`requirement-create.js:284 refreshData`、`requirement-confirm-page.js:149 refreshData`、`requirement-review-page.js:76 refreshData`、`assembly-integration.js:1586 refreshIntegration`、`cost-review.js:783 refreshCostReview`、`summary-result.js:129 / report-review-result.js:102 / report-publish-result.js:100 refreshProcessReport`；这些是看板内部刷新通道（`refresh-data` 命令与 Agent 工具都在用），不是给人点的业务按钮。父壳侧由 `STAGE_CHAT_FLOW` + `chatFlow()` 决定操作栏显隐并驱动那四个按钮，另有 `transferCurrentTask()` / `replayLastBoardAction()` / `lastBoardAction` / `lastActionFailed` 只服务这四个按钮。
- 新增 Spec：`docs/specs/tech-left-toolbar-drop-generic-buttons.md`：① 删除四个通用按钮（HTML + 父壳渲染与事件绑定 + 只服务它们的辅助与 `STAGE_CHAT_FLOW` / `chatFlow()`）；② `syncChatActions()` 显隐改为「看板快照里有没有可见业务动作」（`entries.length === 0` 才隐藏），主按钮槽位与 `syncChatActionList(entries, primaryName)` 不变；③ 九处刷新动作 `getState()` 返回 `visible: false` 退出左侧栏，动作条目、`run()` 实现与注册名一律保留，`refresh-data` 命令、桥的 `refreshData()` 与 Agent 调用点一字不改；④ 顶部流程条、输入区 ＋（附件）、结果入口、任务进度卡、标题行与右侧看板结构全部保留。
- 新增红测：`tests/test_tech_left_toolbar_drop_generic_buttons_red.py`（11 项），覆盖四个通用按钮已从 HTML 与父壳脚本消失、`transferCurrentTask` / `replayLastBoardAction` / `lastBoardAction` / `lastActionFailed` / `STAGE_CHAT_FLOW` / `chatFlow()` 退役、操作栏显隐由 `boardActionEntries()` + `entries.length` 决定、业务出口仍只走 `TechBoardBridge.executeAction` 且不查 iframe DOM、九处刷新动作必须 `visible: false` 且仍带 `run()`、`refresh-data` / `refreshData` / `refreshName` 通道未被删除、既有入口（主按钮槽位 / 输入区 ＋ / 结果入口 / 任务进度宿主 / 顶部流程条）保留、不得新增后端路由与桥事件白名单不变。
- Red 基线（实际运行）：`python3 -m unittest tests.test_tech_left_toolbar_drop_generic_buttons_red -v` → **11 项中 6 失败（24 个失败点，含子断言）、5 通过**（5 项通过为「既有入口保留」「业务出口不变」「刷新动作仍注册」「刷新通道未动」「无后端/协议改动」五项保护与保留守卫）。
- 本批取代的旧断言（需由契约方按本批语义反转）：`tests/test_tech_left_toolbar_parity_red.py` 的 `TOOLBAR_IDS`（四个通用按钮）、`test_transfer_reuses_existing_capability_not_new_route`、`test_retry_replays_last_action`；`tests/test_tech_board_actions_into_left_toolbar_red.py` 的 `TOOLBAR_IDS`、`test_stage_flow_table_covers_nine_stages_without_action_names`、`test_existing_left_toolbar_channels_are_kept` 里 `applyStage` / `last[A-Za-z]*Action` 两条、`test_every_button_has_tooltip_and_aria_label` 里的 `当前不可用` token；`tests/test_tech_primary_quick_actions_and_bulk_part_analysis_red.py:67-68` 的 `techChatPrev` / `techChatTransfer` 断言。
- 边界：本批只新增 Spec、红测与周 changelog，未修改任何前端/后端实现；未删除任何业务动作、Agent 工具或后端路由；未改顶部流程条、子页签、标题行、右侧看板卡片结构与嵌入态隐藏规则；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 22. 技术工艺业务动作按钮一律可点、点了再给真实原因 实现（9-14）

- 父壳（`tech-workbench.js`）：`syncChatActionList()` 的禁用条件去掉 `entry.enabled === false`，只剩 `entry.busy === true || !state.project`；`syncChatActions()` 主按钮槽位同理去掉 `primaryEntry.enabled !== false`；`actionTooltip()` 删掉 `enabled === false` 那条「当前不可用」分支（按钮还能点就不能宣称不可用；「执行中」「请先打开项目」保留）；`runBoardAction()` 不再在没有项目时静默 `return`，改为标题行 + 会话双提示；失败分支写 `setBoardNotice(message, 'error')` 并经 `window.ocTechAgent.notice` 同步一条左侧会话提示。
- 会话入口（`agent-chat.js`）：`window.ocTechAgent` 增加 `notice: pushSystem`，作为父壳业务动作成败提示的唯一会话入口，不在父壳里新建气泡。
- 运行时（`tech-board-runtime.js`）：新增并导出 `refreshState()`，只重发一次 `action-state` 快照、不写 `overrides`（覆盖语义仍归 `updateActionState()`）；`entryState()` 字段契约（`label/visible/enabled/busy/active/analyzed/role/order/hint`）不变，`enabled` 继续下发、只是不再表达前置条件。
- 2.2 组装与整合（`assembly-integration.js`）：所有注册动作的 `getState()` 不再读页内按钮 `disabled`，`enabled` 一律 `true`、忙闲只看 `busy`；新增 `aiFinanceBlocker()` 作为「确认工艺并发送财务」前置条件的唯一判定，`aiRenderOps()` 的页内 why 与动作 `run()` 共用同一份；`sendIntegrationToFinance.run()` 点了先判，不满足时写页内提示并返回 `{ ok: false, error: { code: 'not-ready', message } }`，满足才走既有 `aiOpenFinanceDialog()`；`aiOpenFinanceDialog()` 忙时不再静默 `return`，改返回 `code: 'busy'` 的结构化失败 + 页内提示；新增 `aiPublishState()`，`aiRender()` 收尾调用，同一帧多次渲染合并成一次后走 `TechBoardRuntime.refreshState()`。
- 2.3 成本测算（`cost-review.js`）：`CR_COST_ROLES` 由后端不存在的 `finance_mgr` 改为与 `auth.COST_ROLES` 一致的 `finance_manager` / `admin`；`getState()` 同样不再读 `#crConfirm` 等页内按钮 `disabled`，`enabled: true`、忙闲交给 `busy`；新增 `crConfirmBlocker()` 唯一判定（只读身份 / 缺零件 / 缺件数 / 整机未算 / 0 元行），`crRenderActions()` 与 `crRenderOps()` 共用；`confirmCostReview.run()` 不再看按钮 `disabled`、不再用 `crReadOnly()` 拦按钮，点了先算 `crConfirmBlocker()`、再走既有 `crConfirmCost()`，失败返回结构化错误；新增 `crPublishState()`，`crRender()` 收尾调用 `TechBoardRuntime.refreshState()`，确认成本 / 测算收尾后父壳立刻拿到新快照，左侧「写入数据库 / 回传销售经理继续报价 / 提交工艺经理确认」不用手动刷新。
- 边界：未改 `cpq:tech-board` 信封、`STAGE_EVENTS` 白名单、`payload` 形状与 `entryState()` 字段契约；未改后端任何路由、`_require(user, auth.COST_ROLES)` 权限判定与 403 语义；未改页内按钮自身 `disabled` 语义（`#aiParamsConfirm` 仍 `!has || aiBusy`、`#crConfirm` 仍按 `crData.ready`）；未改九阶段 stage id、页面映射、标题行与右侧看板卡片结构；未新增第二套成本算法或后端接口字段。
- 验证：`python3 -m unittest tests.test_tech_business_actions_clickable_then_error_red` → **24/24 通过**（Red 基线 37 个失败点）；回归 `test_tech_assembly_disabled_vs_busy_red`（5/5）、`test_tech_step_status_blue_pill_red`（12/12）、`test_tech_board_bridge_protocol_red`（10/10）全绿；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 由 **75 个失败点降到 39 个**（同一 625 项、7 跳过；本批净修 37 个失败点，新增 1 个即下面的过期契约），失败全部来自并行未完成批次与本批已记录的两处过期契约。
- 已知契约冲突（未改测试，等待 Codex 更新）：`tests/test_tech_board_actions_into_left_toolbar_red.py::test_every_button_has_tooltip_and_aria_label` 仍要求 `actionTooltip()` 里出现字面量「当前不可用」，与本批 Spec §3「删掉 `enabled === false` 的『当前不可用』分支」以及本批红测 `test_tooltip_no_longer_claims_unavailable`（`assertNotIn('当前不可用')`）直接冲突；该断言改动前为通过状态，属被本批 Spec 取代的过期契约，业务实现不为迁就它恢复该分支。
- `node --check` 覆盖 `tech-workbench.js` / `tech-board-runtime.js` / `assembly-integration.js` / `cost-review.js` / `agent-chat.js`；`git diff --check` 通过。未提交、未推送、未部署。

## 23. 技术工艺左侧会话操作栏只留当前步骤业务动作、去掉通用刷新与导航按钮 实现（9-14）

- 契约 A 静态按钮退役（`tech-workbench.html`）：删掉 `#techChatPrev` / `#techChatNext` / `#techChatTransfer` / `#techChatRetry` 四个 `<button>`；`#techChatPrimary` 仍是唯一主按钮槽位；`#ocChatAttachBtn` / `#ocChatFileInput` / `#ocResultActions` / `#ocTaskProgressHost` / 顶部 `#techPrev` / `#techNext` / `#techNowLabel` 一律保留。
- 契约 B 父壳减法（`tech-workbench.js`）：删除阶段壳导航表 `STAGE_CHAT_FLOW` 与 `chatFlow()`；删除 `transferCurrentTask()`、`replayLastBoardAction()` 与 `lastBoardAction` / `lastActionFailed`，连带 `runBoardAction()` 里为失败重试维护的状态；删除 `syncChatActions()` 里四处通用按钮渲染与 `bindChatToolbar()` 里对应的四个事件绑定（转交改由会话输入区承担，失败由业务按钮本身重试）。`syncChatActions()` 的显隐改为「看板快照里有没有可见业务动作」：`boardActionEntries()` 为空则整栏隐藏并返回，否则渲染主按钮槽位 + `syncChatActionList(entries, primaryName)`。业务出口不变，仍是 `TechBoardBridge.executeAction`，不写死动作名、不查 iframe DOM。
- 契约 C 刷新动作退出左侧栏（九个 stage 页）：`getState()` 一律改 `visible: false`，动作条目、`run()` 实现与注册名不动 —— `app.js` / `requirement-create.js` / `requirement-confirm-page.js` / `requirement-review-page.js` 的 `refreshData`、`assembly-integration.js` 的 `refreshIntegration`、`cost-review.js` 的 `refreshCostReview`、`summary-result.js` / `report-review-result.js` / `report-publish-result.js` 的 `refreshProcessReport`（`app.js` 原本没有 `getState`，本批补 `getState: () => ({ visible: false })`）。`TechBoardRuntime` 的 `refresh-data` 分发、`TechBoardBridge.refreshData()`、Agent 侧 `refreshIntegrationBoard` / `refreshCostReview` 等调用点一字未改：`visible: false` 只影响左侧栏渲染，`executeAction` / `refresh-data` 照常执行。
- 边界：未改 `cpq:tech-board` 信封、事件白名单、`payload` 形状与 `entryState()` 字段契约；未删任何业务动作、Agent 工具或后端路由；未改顶部流程条、子页签、标题行、右侧看板卡片结构与嵌入态隐藏规则；未改输入区 ＋（附件）、结果入口与任务进度卡。
- 验证：`python3 -m unittest tests.test_tech_left_toolbar_drop_generic_buttons_red` → **11/11 通过**（Red 基线 24 个失败点）；回归 `test_tech_business_actions_clickable_then_error_red` + `test_tech_board_bridge_protocol_red` + `test_tech_step_status_blue_pill_red` + `test_tech_drawing_agent_actions_red` + `test_tech_integration_agent_red` + `test_tech_cost_review_agent_red` → **75/75 通过**；全量由 39 个失败点降到 27 个；`node --check` 覆盖 `tech-workbench.js` 与九个 stage 页脚本（`app.js` 走 `--input-type=module`）、`git diff --check` 通过。
- 本批按 Spec §7 取代的旧断言（等契约方反转，实现不为迁就它们保留已退役节点）：`tests/test_tech_left_toolbar_parity_red.py`（`TOOLBAR_IDS` 四项、`test_left_pane_has_unified_action_bar` / `test_toolbar_controls_are_wired` / `test_retry_replays_last_action` / `test_transfer_reuses_existing_capability_not_new_route` / `test_stage_table_covers_all_nine_stages`）；`tests/test_tech_board_actions_into_left_toolbar_red.py`（`TOOLBAR_IDS` 四项、`test_existing_left_toolbar_channels_are_kept` 的 `last[A-Za-z]*Action`、`test_stage_flow_table_covers_nine_stages_without_action_names`、`test_static_per_action_buttons_are_replaced_by_dynamic_rendering` 的四条节点断言）。
- 另有一处 **Spec §7 未列举**的被取代断言：`tests/test_tech_quote_agent_parity_matrix_red.py::test_live_rows_resolve_in_tech_code` 的 `transfer` 行锚在 `docs/specs/tech-agent-recovery-21-quote-parity.json` 的 `tech_app/frontend/tech-workbench.html#techChatTransfer`，该节点按本批要求已删除，锚点悬空；该 JSON 属另一批（`tech-agent-recovery-21`）的产物，本批未改动，待契约方把该行锚点改到会话输入区（如 `#ocInput`）或移除该行。
- 未提交、未推送、未部署。

## 24. 1.1 主按钮随解析状态切换、2.1 能力入口归位「更多功能」并解析后自动生成 3D/2D Spec / Red（9-14）

- 问题一（1.1 主按钮挂错）：`requirement-create.js:257-268` 里 `submitRequirement` 写死 `role:'primary'` + `order:10`、`extractRequirement` 写死 `role:'aux'` + `order:30`，于是创建页第一眼的主按钮是「提交确认」；但字段还没解析、需求单还是空的，此时真正的起点是「一键解析需求」。对照 2.2 的 `runIntegration` / `sendIntegrationToFinance`（`assembly-integration.js:1498-1517`）是 `getState()` 按 `aiAnalyzed()` 动态反转 role，1.1 缺的正是这一份。
- 问题二（2.1 入口重复 + 缺入口）：`tech-workbench.html:113-117` 左侧会话栏并排放了五项 2.1 能力按钮（`data-tech-capability` = evidence / import3d / review / modelLookup / verify），其中「联网核验」「校验修正」与 `index.html:179` 的「更多功能 ▾」（`#btnMoreActions` + `#actionSheet`，已含 `#btnModelLookup` / `#btnVerify` / `#btnDecompose` / `#btnGenerate` / `#btnDrawings` / `#btnBom`）完全重复；而「导入已有 3D 模型」「版本与校核审查」反倒不在「更多功能」里，只能走 2.1 页自身图标栏 `data-open-drawer="import3d"` / `"review"`。
- 问题三（3D/2D 还要手点）：`app.js:861-906` 的 `parseDrawing()` 解析成功后只把 `#btnVerify` / `#btnDecompose` / `#btnModelLookup` / `#btnGenerate` / `#btnDrawings` / `#btnBom` 逐个 `disabled = false`，用户还得进「更多功能」点「生成 CAD 几何」再点「生成 2D 工程图」，右侧才看得到 3D 与 2D。
- 新增 Spec：`docs/specs/tech-step-primary-and-drawing-entry-cleanup.md`：① 1.1 用同一个「是否已解析」判定（建议 `rcExtracted()` 读 `rcData().document_extraction`）让 `extractRequirement` / `submitRequirement` 的 `role` 在 `primary` 与 `aux` 之间反转，order 改 10 / 20，`enabled: true` + `busy: rcBoardBusy`，`run()` 与后端调用一字不改；② 左侧会话栏删除 `import3d` / `review` / `modelLookup` / `verify` 四个 `data-tech-capability` 按钮（保留 `evidence`；`agent-chat.js` 的 `capabilityButtons` 由 DOM 查出，删节点即自动解绑，不得另加黑名单），`#actionSheet` 新增 `#btnMoreImport3d`（导入已有 3D 模型）与 `#btnMoreReview`（版本与校核审查），两者只复用既有 `runBoardView("import3d")` / `runBoardView("review")`；③ 把 `#btnGenerate` / `#btnDrawings` 的内联实现抽成 `generateGeometry()` / `generateDrawings()` 具名函数并回执成功 / 失败，新增 `autoGenerateAfterParse()`：只在 `currentIsImg` 且解析成功时触发，先几何、几何成功后再 2D，几何失败即停并写出真实原因，`parseDrawing()` 成功路径末尾调用它，右侧显示复用既有 `showGeneratedResult()`。
- 新增红测：`tests/test_tech_step_primary_and_drawing_entry_cleanup_red.py`（16 项），覆盖 1.1 两个动作 role 不得写死且必须动态、必须共用同一个判定、order 10 / 20、`enabled: true` + `busy` 语义、1.1 既有流程与接口不变、主按钮槽位走既有 primary 变体且样式为系统主色实心、左侧栏四项能力按钮删除且 `evidence` 保留、「更多功能」八项菜单文案齐全、两个新菜单项必须复用既有视图、2.1 既有入口（`#secImport3d` / `#file3d` / `#btnImport3d` / `#secVersions` / 图标栏 / 视图注册）保留、几何与 2D 具名函数与按钮共用同一实现且回执成败、解析后自动串行生成且几何失败挡住 2D、既有接口与后端路由不减、独立 2.1 页 ＋ 菜单的分派链不得删、`entryState()` 字段契约不变。
- Red 基线（实际运行）：`python3 -m unittest tests.test_tech_step_primary_and_drawing_entry_cleanup_red -v` → **16 项中 10 失败（18 个失败点，含子断言）、6 通过**（6 项通过为 1.1 既有流程不变、既有接口与后端路由不减、既有 2.1 入口保留、主按钮样式为系统主色实心、独立 2.1 页分派链保留、协议字段契约不变六项保护守卫）。
- 边界：本批只新增 Spec、红测与周 changelog，未修改任何前端/后端实现；未增删后端路由与接口字段；未改 `cpq:tech-board` 信封、`entryState()` 契约与九阶段 stage id；未改 1.1 字段渲染、行业模板、附件流程与校验规则；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 25. 1.1 主按钮随解析状态切换、2.1 能力入口归位「更多功能」并解析后自动生成 3D/2D 实现（9-14）

- 契约 A 主按钮反转（`requirement-create.js`）：新增唯一判定 `rcExtracted()`（读 `rcData().document_extraction`，与 `rcExtractionStatus()` 同源）；`extractRequirement.getState()` 改 `role: rcExtracted() ? 'aux' : 'primary'` + `order: 10`，`submitRequirement.getState()` 改 `role: rcExtracted() ? 'primary' : 'aux'` + `order: 20`，两者都改 `enabled: true` + `busy: rcBoardBusy`（前置条件不灰按钮，忙闲只由 busy 表达）；`saveRequirementDraft` order 20 → 30、`refreshData` 同样 `enabled: true`。`run()`、`label`、`deferred` 与「一键解析需求 / 提交确认」的既有接口调用一字未改。
- 契约 B 入口归位：`tech-workbench.html` 的 `#techChatActions` 删除 `data-tech-capability="import3d" / "review" / "modelLookup" / "verify"` 四个按钮（保留 `evidence` 解析视图；`agent-chat.js` 的 `capabilityButtons` 由 DOM 查出，删节点即自动解绑，未加能力黑名单）；`index.html` 的 `#actionSheet` 在既有六项之后新增 `#btnMoreImport3d`（导入已有 3D 模型）与 `#btnMoreReview`（版本与校核审查），默认 `disabled` 与既有六项同规则。
- 契约 C 解析后自动生成（`app.js`）：`#btnGenerate` / `#btnDrawings` 的内联实现抽成具名 `generateGeometry()` / `generateDrawings()`（原实现一字未改，成功 `return true`、失败 `return false` 并保留既有 `status(...)` 真实原因），两个按钮的 `onclick` 指向同一份实现；新增 `autoGenerateAfterParse()` 作为唯一自动入口：`!currentIsImg || !currentIR` 直接跳过（3D 导入项目与解析无结果不触发），先 `await generateGeometry()`，几何成功才 `await generateDrawings()`，几何失败即停；`parseDrawing()` 成功路径末尾调用它，失败路径不触发，右侧显示仍由既有 `showGeneratedResult()` 负责；两个新菜单项的 `onclick` 直接复用 `runBoardView("import3d")` / `runBoardView("review")` 并顺手收起菜单；解析成功时一并解锁两个新入口。
- 边界：未改后端任何路由与实现（`/api/projects/3d`、`/generate`、`/drawings`、`/decompose`、`/parse` 照旧）；未删 `#btnImport3d` / `#file3d` / `#secImport3d` / `#secVersions`、2.1 图标栏 `data-open-drawer="import3d"` / `"review"`、`BOARD_VIEW_SPECS` 与 `registerViews` 的 `import3d` / `review`、`#actionSheet` 既有六项、`#techChatPrimary` 与 `data-tech-capability="evidence"`；未改 `cpq:tech-board` 信封、事件白名单、`entryState()` 字段契约与 `role` / `order` 语义；未改 1.1 字段渲染、行业模板、附件流程与校验规则。
- 验证：`python3 -m unittest tests.test_tech_step_primary_and_drawing_entry_cleanup_red` → **16/16 通过**（Red 基线 16 项中 10 失败 / 18 个失败点）；回归 `test_tech_board_bridge_protocol_red` / `test_tech_left_toolbar_drop_generic_buttons_red` / `test_tech_business_actions_clickable_then_error_red` / `test_tech_requirement_agent_red` 全绿；`node --check`（`requirement-create.js` / `agent-chat.js`，`app.js` 走 `--input-type=module`）与 `git diff --check` 通过。
- 本批按 Spec §4 取代的旧断言（等契约方反转；实现不为迁就它们把四个能力按钮加回左侧栏）：`tests/test_tech_direct_attachment_and_chat_capability_actions_red.py::test_non_upload_capabilities_live_in_chat_action_bar`（CAPABILITIES 四项）、`tests/test_tech_drawing_agent_actions_red.py::test_plus_menu_routes_actions_and_views_separately`（`data-tech-capability="modelLookup"` / `"verify"`）、`tests/test_tech_result_entries_board_views_red.py::test_left_entries_forward_view_names_to_board`（`data-tech-capability="evidence"` / `"review"` 中的 `review`）。全量失败点由 27 升到 33，新增的 6 个失败点全部来自这三处过期断言。
- 未提交、未推送、未部署。

## 26. 会话报错改回普通输出、refresh 类动作不再产生「已完成」卡 Spec / Red（9-14）

- 问题一（报错被钉在会话最底部）：`agent-chat.js:1559-1580` 的 `showBoardNavFailure()` 除了写一条流内提示，还额外新建 `div.oc-retry-row` 并 `tinner.append(row)`；而 `agent-chat.css:624` 的 `.oc-retry-row { display:flex; order:1; ... }` 把这一行永久钉在 `.oc-tinner`（flex column）最底部，节点从不移除 —— 一次失败后底部那行一直挂着，后面聊多少轮都不动。同一机制在 `.oc-result-actions`（`agent-chat.css:276`）上是「结果入口常驻底部」的有意设计，本批不动。
- 问题二（每页刷出一张「已完成」卡）：`tech-board-runtime.js` 的 `runEntry()` 对**每个**动作都发 `TASK_PROGRESS(phase:'start')` 与 `TASK_COMPLETED`（失败再发 `TASK_FAILED`），父壳 `agent-chat.js:1717-1724` 把它们渲成 `.oc-task-card`（按 taskId / label 去重、永不删除），于是刷新动作每跑一次就留一张「刷新看板数据 / 刷新需求看板 / 刷新需求确认页 / 刷新需求审核页 已完成」。
- 问题三（refresh 失败被吞）：`agent-chat.js:1504` 的 `Promise.resolve(bridge.refreshData({...})).catch(() => {})` 把失败静默吃掉。
- 新增 Spec：`docs/specs/chat-errors-inflow-and-drop-refresh-task-cards.md`：① 失败提示与其重试控件一起作为会话流里的一条普通消息输出（`showBoardNavFailure()` 不得再新建独立行节点、不得直接 `tinner.append`，`noteInThread(text, extras)` 可承载内联重试按钮；`agent-chat.css` 删除 `.oc-retry-row`，全仓带 `order: 1` 的规则只允许剩 `.oc-result-actions`）；② 动作条目支持可选 `silent: true`，`runEntry()` 新增本地出口 `publishTaskCard(eventName, payload)` 承载 `TASK_PROGRESS / TASK_COMPLETED / TASK_FAILED` 三处发布并对 `silent` 条目整体短路，`runEntry` 内不得再直接 `publish(EVENT.TASK_*)`；九处刷新动作（`app.js` / `requirement-create.js` / `requirement-confirm-page.js` / `requirement-review-page.js` 的 `refreshData`、`assembly-integration.js` 的 `refreshIntegration`、`cost-review.js` 的 `refreshCostReview`、`summary-result.js` / `report-review-result.js` / `report-publish-result.js` 的 `refreshProcessReport`）一律加 `silent: true` 并保留 `visible: false`；③ `refreshBoardAfterUpload()` 的空 catch 改为把真实原因写进会话；④ 任务卡机制、`.oc-task-card`、`#ocTaskProgressHost`、`.oc-result-actions { order: 1 }`、`entryState()` 字段契约与后端路由全部保留。
- 新增红测：`tests/test_chat_errors_inflow_and_drop_refresh_task_cards_red.py`（13 项），覆盖带 `order: 1` 的规则只剩 `.oc-result-actions`、`showBoardNavFailure()` 必须走流内消息且不得再 append 独立行、重试控件与唯一导航出口保留、`.oc-retry-row` 规则删除、`pushSystem` / `.oc-err-line` 通道保留、`runEntry` 的卡片事件必须收口到 `publishTaskCard()` 且三处不再直接发布、出口仍发三种事件、九处刷新动作必须 `silent: true` 且 `visible: false` 不回退、任务卡流水线与进度宿主保留、`entryState()` 契约不变、refresh 调用方不得空 catch 且必须写进会话，以及桥事件白名单 / 后端路由 / 结果入口常驻三条保护守卫。
- Red 基线（实际运行）：`python3 -m unittest tests.test_chat_errors_inflow_and_drop_refresh_task_cards_red -v` → **13 项中 7 失败（15 个失败点，含子断言）、6 通过**（6 项通过为 `entryState()` 契约、流内错误通道、桥协议与后端路由、结果入口常驻、重试控件保留、任务卡流水线保留六项保护守卫）。
- 附：上一批（第 24 批：1.1 主按钮随解析状态切换、2.1 入口归位、解析后自动生成 3D/2D）已由并行提交实现，`python3 -m unittest tests.test_tech_step_primary_and_drawing_entry_cleanup_red` → **16/16 通过**。
- 边界：本批只新增 Spec、红测与周 changelog，未修改任何前端/后端实现；未改任务卡机制与结果入口常驻设计；未改 `cpq:tech-board` 信封、事件白名单与 `payload` 形状；未改标题行提示位（`#techContextNotice` 与 `.is-error`）通道；未改后端路由与任务模型；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 27. 会话报错改回普通输出、refresh 类动作不再产生「已完成」卡 实现（9-14）

- 契约 A（失败提示回到会话流，`agent-chat.js` / `agent-chat.css`）：`noteInThread(text, extras)` 新增可选 `extras` 回调，附加控件与正文共享同一条 `oc-abody`；`showBoardNavFailure()` 改为只调 `noteInThread(...)`，重试芯片（`oc-chip-retry` → `boardNavigateView(view, payload)`）通过 `extras` 内联到同一条消息，不再新建独立提示行、不再 `tinner.append`；`agent-chat.css` 删除 `.oc-retry-row` 整条规则，`.oc-chip-retry` 改成 `inline-flex; margin-top: 6px` 的内联样式；`order: 1` 现在全仓只剩 `.oc-result-actions`（结果入口常驻底部不动）。
- 契约 B（silent 动作不出卡，`tech-board-runtime.js`）：`runEntry()` 新增本地出口 `function publishTaskCard(eventName, extra)`，先判 `entry.silent === true` 直接 return，再用 `[EVENT.TASK_PROGRESS, EVENT.TASK_COMPLETED, EVENT.TASK_FAILED]` 白名单确认事件名后 `publish(eventName, name, extra)`；原先三处裸发布（start / 业务 ok:false / 异常分支 / Promise 拒绝分支）全部改走该出口，`runEntry` 内不再有直接 `publish(EVENT.TASK_*)`；`deferred === true` 仍跳过 completed、`keepActionState`、`entryState()` 字段契约与 `ACTION_STATE` 语义均未变；文件头补记 `silent: true` 的适用范围（只给纯看板内同步，解析类长任务不适用）。
- 契约 B 落地范围：九处刷新动作各加一行 `silent: true`，`visible: false` 保持 —— `app.js` `refreshData`（刷新看板数据）、`requirement-create.js` `refreshData`、`requirement-confirm-page.js` `refreshData`、`requirement-review-page.js` `refreshData`、`assembly-integration.js` `refreshIntegration`、`cost-review.js` `refreshCostReview`、`summary-result.js` / `report-review-result.js` / `report-publish-result.js` `refreshProcessReport`。
- 契约 C（refresh 失败不静默，`agent-chat.js`）：`refreshBoardAfterUpload()` 的 `.catch(() => {})` 改为 `.catch((error) => pushSystem(\`刷新看板失败：${(error && error.message) || "看板未响应"}\`))`，失败原因按普通错误消息进会话。
- 随契约变更更新的既有断言（原断言按裸 `publish(EVENT.TASK_*)` 写法写死）：`tests/test_tech_board_deferred_actions_red.py::test_non_deferred_actions_still_publish_completion` 与 `tests/test_chat_fused_assistant_card_style_red.py::test_board_runtime_task_events_carry_label_and_task_id` 的查找标记改为 `publishTaskCard(EVENT.TASK_*)`，`label` / `taskId` 与「非 deferred 仍发完成事件」的语义不变。
- 验证：`python3 -m unittest tests.test_chat_errors_inflow_and_drop_refresh_task_cards_red` → **13/13 通过**（Red 基线 15 个失败点全部消除）；`test_tech_board_deferred_actions_red` / `test_chat_fused_assistant_card_style_red` / `test_tech_board_bridge_protocol_red` / `test_tech_board_action_registry_red`（除下述过期断言）/ `test_tech_board_state_envelope_dynamic` / `test_tech_business_actions_clickable_then_error_red` / `test_tech_step_primary_and_drawing_entry_cleanup_red` 全绿；`node --check` 覆盖 `agent-chat.js` / `tech-board-runtime.js` / 九个阶段页脚本，`git diff --check` 通过。
- 隔离验证：以 `git worktree` 建 HEAD 基线（并合入并行的 `index.html` / `tech-workbench.html` 改动）与当前工作区各跑全量 `python3 -m unittest discover -s tests`（654 项），两次失败清单逐条一致（33 项，均为并行批次 24 遗留的过期断言），本批新增 **0** 个失败点。
- 边界：未改任务卡流水线（`renderTaskProgress` / `.oc-task-card` / `#ocTaskProgressHost`）、结果入口常驻、`cpq:tech-board` 信封与事件白名单、`entryState()` 契约、标题行提示位通道、后端路由与 service；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 28. 2.2 左侧操作栏去掉 Agent 专用单步动作与重复的「整合图纸」入口 Spec / Red / 实现（9-14）

- 问题：`assembly-integration.js` 的 `integrationStep`（运行整合环节，order 120）与 `openIntegrationDrawings`（整合图纸，order 130）都声明了 `visible: true`，被父壳渲染成左侧按钮。前者必须拿到 `payload.step`，而父壳 `runBoardAction()` 只发 `{ label, role }`，用户点它必然落到 `bad-step`「step 只能是 params / process」；后者的目标视图在标题行子页签（`tech-workbench.js` CHILD_TAB_PROXY.process.tabs）已有同一入口，左侧栏属于重复。两者的真实调用方都是 Agent 工具（`oc_agent.py` 的 `RequestIntegrationStep → "integration-step"`、`UploadIntegrationDrawing → "open-integration-drawings"` → `agent-chat.js` 的 `integrationBoardStep()` / `openIntegrationDrawings()` → `bridge.executeAction(...)`）。
- Spec：`docs/specs/integration-left-toolbar-drop-agent-only-and-duplicate-entries.md`：① 两个条目只把 `getState()` 改成 `visible: false`（与既有 `refreshIntegration` 同一条通道，父壳不新增名字黑名单）；② Agent 两条工具链路、`run()` 实现、`label` / `role` / `order` / `deferred` 与 `registerViews` 一律不动；③ 2.2 用户动作 `runIntegration` / `sendIntegrationToFinance` 继续 `visible: true` 并按 `aiAnalyzed()` 反转主按钮，页签专属动作继续 `visible: show`。
- 红测：`tests/test_integration_left_toolbar_drop_agent_only_and_duplicate_entries_red.py`（14 项），覆盖两个条目必须 `visible: false` 且不得再出现 `visible: true`、与 `refreshIntegration` 同通道、父壳仍以 `entry.visible === false` 为唯一跳过条件且不得按动作名写黑名单、Agent 两条 `executeAction` 链路与后端工具映射保留、`aiIntegrationStepInBackground` / `aiSetTab('drawings')` + `focus()` 实现保留、label 与 order 不变、用户动作与页签专属动作可见性不变、CHILD_TAB_PROXY 三个子页签与 `registerViews` 三视图保留，以及桥事件白名单、`/integration/*` 路由、`silent` / `deferred` 语义三条保护守卫。Red 基线：14 项中 4 失败（2 项 + 2 子断言）、10 通过。
- 实现：`assembly-integration.js` 两处 `getState()` 改为 `visible: false`（其余字段一字未改），并各留一行说明它是 Agent 专用入口、隐藏不影响 `execute-action` 执行。
- 验证：新红测 **14/14 通过**；`node --check assembly-integration.js` 与 `git diff --check` 通过；全量 `python3 -m unittest discover -s tests` **668 项、33 失败**，失败清单与改动前逐条一致（全部为并行批次 24 遗留的过期断言），本批新增 **0** 个失败点。
- 边界：未改运行时的 `silent` / `deferred` 语义、桥协议与事件白名单、后端路由与 service、`oc_agent.py` 工具映射；未删任何动作注册与实现；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 29. 技术工艺 Agent 会话输入框真正贴底 Spec / Red / 实现（9-14）

- 原因：技术统一工作台的 flex 高度链正常，但输入框下方仍有 `.oc-disc` 说明行、9px 间距和 composer 10px 底部 padding，输入框视觉下沿距会话列底部约 35px。
- Spec：新增 `docs/specs/tech-chat-composer-flush-bottom.md`；最新决策仅调整技术统一工作台，报价侧说明行保持不变，并部分取代 `quote-tech-unified-composer-and-binding-caption.md` 的技术说明行条款。
- Red：新增 `tests/test_tech_chat_composer_flush_bottom_red.py`；修正报价 DOM 保护断言后，基线为 **5 项中 2 项失败、3 项通过**，失败即技术说明行仍存在、技术专属 composer 尚无零底距。
- 实现：`tech-workbench.html` 删除统一技术 composer 下方的 `.oc-disc`；`tech-workbench.css` 的 `#techChatPane .oc-composer` 增加 `padding: 10px 16px 0`，输入框顶部/左右空间、76px 高度和24px圆角不变；缓存版本仅将 `tech-workbench.css?v=twb15` 升至 `twb16`。
- 契约同步：反转两份旧测试中“技术必须保留说明行”的过期断言，报价说明行、通用 `.oc-disc` 样式、附件/输入/发送接线继续作为守卫；旧统一 composer Spec 状态标明该条款已被最新决策取代。
- 验证：目标测试与两组输入区回归合计 **26/26 通过**；`python3 -m py_compile tests/test_tech_chat_composer_flush_bottom_red.py`、`node --check agent-chat.js` / `tech-workbench.js`、`git diff --check` 均通过。全量在并行未提交批次持续写入期间运行到 697 项、42 个 failure records，失败清单不含本批三个测试模块，集中于并行的动作栏/2.2参数页、旧壳契约、AI消息样式及此前阶段卡测试；不为本批擅自改动这些并行范围。
- 边界：未改报价页面、通用 `.oc-disc` CSS、消息/历史/任务进度、左侧业务动作、九阶段、右侧看板和后端；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 30. 2.2「参数推荐」页只留一颗主按钮、生成即补全必填、确认后进入下一步 Spec / Red / 实现（9-14）

- 问题：站在参数推荐页（`aiTab === 'params'`）时，左侧操作栏同时渲染 `runIntegration`（开始整合分析）、`sendIntegrationToFinance`（确认工艺并发送财务）、`generateIntegrationParams`（生成参数推荐，role 却是 aux）、`saveIntegrationParams`、`confirmIntegrationParams`、`autofillIntegrationParams`、`saveIntegrationParamsFinal`、`confirmIntegrationParamsFinal` 八颗按钮；本页真正的起点被挤成次按钮，analysis 完成后主按钮还被财务动作抢走（后者的闸门 `aiFinanceBlocker()` 要求工艺已生成并确认，在参数页必然是死按钮）。报价必填缺口还要用户自己点「智能补全 → 保存补填 → 确认参数已齐」三次，任一步没点就在发送财务时被挡住。
- Spec：`docs/specs/integration-params-tab-single-primary-and-auto-fill.md`：① 参数页只留「生成参数推荐」（未生成时主按钮）与「确认并进入下一步」（已生成时主按钮，order 35），其余参数动作一律 `visible: false`；② 「生成参数推荐」= 既有三步链路 `aiGenerate('params')` → `aiParamsAutofill()` → `aiParamsFinalize(false)`，只把真正补上值的建议落库；③ 「确认并进入下一步」= 既有 `aiParamsFinalize(true)`（报价必填校验）→ 校验 `params_final` → `aiConfirmStep('params')` → `aiSetTab('process')`，缺项返回真实原因且不切页；④ 所有被隐藏动作继续注册可执行，Agent 工具链、后端接口、桥协议、右侧看板一律不动。
- 红测：`tests/test_integration_params_tab_single_primary_and_auto_fill_red.py`（24 项），覆盖参数页两个可见动作、五颗参数按钮必须 `visible: false`、生成参数推荐的 role 随 `aiHasParams()` 反转、新动作的 label / 可见性 / role / order、`runIntegration` 只属整合图纸页、`sendIntegrationToFinance` 只属组装工艺页、生成链路三步复用既有实现、仅在 `applied > 0` 时落库、`aiParamsAutofill()` 必须返回 `{applied, unresolved}`、失败即停并返回结构化原因、确认链路四步顺序（确认通过后才切页）、确认链路不得自己发请求、隐藏动作实现保留、Agent 工具链与三视图不变、父壳过滤与主按钮规则不变、嵌入态右侧按钮仍隐藏、后端参数路由不变、桥事件白名单不变。Red 基线：24 项中 19 失败（含子断言）、5 通过。
- 实现（`tech_app/frontend/assembly-integration.js`）：新增共享判定 `aiHasParams()` / `aiHasProcess()`；新增 `aiGenerateParamsFully()`（生成 → 有缺口自动补全 → `applied > 0` 时 finalize 落库 → `aiSay()` 汇报已填 / 还缺）；新增 `aiConfirmParamsAndNext()`（finalize confirm → 校验 `params_final` → confirm 环节 → 校验 `params_confirmed` → `aiSetTab('process')`，busy / 未生成 / 缺项 / 确认失败都回结构化失败）；`aiParamsAutofill()` 成功路径返回 `{applied, unresolved}`（并把这句「改完点保存补填」的旧文案改成「核对后再确认」）；`generateIntegrationParams` 改走完整链路、role 随 `aiHasParams()` 反转；新增动作 `confirmParamsAndNext`（label 确认并进入下一步，order 35，role 只在 getState 里声明一次，避免「2.2 只允许一个静态 primary」的既有契约被破坏）；`saveIntegrationParams` / `confirmIntegrationParams` / `autofillIntegrationParams` / `saveIntegrationParamsFinal` / `confirmIntegrationParamsFinal` 改 `visible: false`；`runIntegration` 改 `visible: aiTab === 'drawings'`、`sendIntegrationToFinance` 改 `visible: aiTab === 'process'`。
- 随契约更新的既有断言：`tests/test_integration_left_toolbar_drop_agent_only_and_duplicate_entries_red.py` 的 `TAB_SCOPED_ACTIONS` 收窄为仍按页签显示的 `generateIntegrationParams` / `generateIntegrationProcess` / `confirmIntegrationProcess`，`test_user_facing_integration_actions_stay_visible` 改为断言 `runIntegration` 绑 `drawings`、`sendIntegrationToFinance` 绑 `process` 且不再 `visible: true`（第 28 批的旧断言被本批取代）。
- 验证：新红测 **24/24 通过**；第 28 批红测 14/14 通过；`node --check assembly-integration.js` 与 `git diff --check` 通过；全量 `python3 -m unittest discover -s tests` **697 项、33 失败**，失败清单与改动前逐条一致（全部为并行批次 24 遗留的过期断言），本批新增 **0** 个失败点。
- 边界：未新增任何接口、未改后端路由 / service / `oc_agent.py` 工具映射；未改桥协议与事件白名单、未改 `aiPost` / `aiConfirmStep` / `aiParamsFinalize` 既有语义；未删任何动作注册与实现，未动右侧看板表格与 `.tech-embed` 隐藏规则；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 31. 2.2「组装工艺」页只留一颗主按钮、确认后进入下一步 Spec / Red / 实现（9-14）

- 问题：站在组装工艺页（`aiTab === 'process'`）时，`assembly-integration.js` 把 `generateIntegrationProcess`（生成组装工艺）渲染成次按钮（`role: 'aux'`，order 40），却把 `confirmIntegrationProcess`（确认组装工艺）单独列一颗（order 70）—— 本页第一步不是主按钮；而且确认完工艺之后没有任何通往下一步（2.3 成本测算）的入口，用户只能自己去找顶部流程条切步骤（`aiConfirmStep('process')` 之后就是死路）。
- Spec：`docs/specs/integration-process-tab-single-primary-and-next-step.md`：① 生成组装工艺的 `role` 随共享判定 `aiHasProcess()` 反转（未生成时主按钮），新增「确认并进入下一步」（order 45，`aiTab === 'process' && aiHasProcess()` 时可见且为主按钮）；② 「确认组装工艺」改 `visible: false`（动作与实现保留，Agent 与内部链路仍可调用）；③ 新增 `aiConfirmProcessAndNext()` = 既有 `aiConfirmStep('process')` → 校验 `process_confirmed` → 新增 `techGoNextStage('cost')`（复用 `TechEmbed.requestNavigate` 既有嵌入导航通道）切到 2.3，确认没过就返回真实原因且不切页；④ 「确认工艺并发送财务」保持原样（接收人必须人工选，不并进本动作），后端接口、桥协议、右侧看板一律不动。
- 红测：`tests/test_integration_process_tab_single_primary_and_next_step_red.py`（22 项），覆盖可见性 / role / label / order、确认组装工艺必须隐藏且实现保留、开始整合分析不得出现在本页、财务交接入口与闸门保留、确认链路两步顺序（确认通过后才切步骤）、切步骤只走既有嵌入通道且不得自己拼 `postMessage`、确认链路不得自己发请求、busy 与未生成工艺的结构化失败、隐藏动作实现保留、Agent 工具链与三视图不变、父壳过滤与主按钮规则不变、嵌入态右侧按钮仍隐藏、后端工艺路由不变、桥事件白名单不变。Red 基线：22 项中 9 失败（含子断言）、13 通过。
- 实现（`tech_app/frontend/assembly-integration.js`）：新增 `techGoNextStage(stage)` 与 `aiConfirmProcessAndNext()`；`generateIntegrationProcess` 的 `getState()` 改为 `role: aiHasProcess() ? 'aux' : 'primary'`；新增动作 `confirmProcessAndNext`（label 确认并进入下一步，order 45，`role` 与可见性同一个条件故用三元写法，保持既有「2.2 只允许一个静态 primary」契约通过）；`confirmIntegrationProcess` 改 `visible: false`。
- 随契约更新的既有断言：`tests/test_integration_left_toolbar_drop_agent_only_and_duplicate_entries_red.py` 的 `TAB_SCOPED_ACTIONS` 收窄为 `generateIntegrationParams` / `generateIntegrationProcess` 两个生成动作（第 28 批断言「确认组装工艺按页签可见」被本批取代）。
- 验证：新红测 **22/22 通过**；第 28 / 30 批红测 14/14、24/24 通过；`node --check assembly-integration.js` 与 `git diff --check` 通过；全量 `python3 -m unittest discover -s tests` **719 项、33 失败**，失败清单与改动前逐条一致（全部为并行批次遗留的过期断言），本批新增 **0** 个失败点。
- 边界：未新增任何接口、未改后端路由 / service / `oc_agent.py` 工具映射；未改桥协议与事件白名单、未改 `aiPost` / `aiConfirmStep` / `aiRunOp` / `aiOpenFinanceDialog` 既有语义；未删任何动作注册与实现，未动右侧看板与 `.tech-embed` 隐藏规则；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 32. 技术工艺会话输入框紧凑按钮与按行自增高 Spec / Red / 实现（9-14）

- 新增 `docs/specs/tech-chat-composer-compact-autogrow.md` 与 `tests/test_tech_chat_composer_compact_autogrow_red.py`；Red 基线为 **6 项中 5 项失败、1 项通过**，缺口覆盖按钮尺寸、文本字号、外框固定高度和超限滚动切换，既有 Enter / Shift+Enter / 输入法保护通过。
- `agent-chat.css`：附件按钮由 50×50 缩至 34×34，发送按钮由 54×54 缩至 36×36（约原尺寸 2/3），加号 18px、发送图标 22px 保持；textarea 改为 12px / 20px；`.oc-inputbox-single` 取消 76px 固定最小高度，改为 `min-height: 0` 与 7px 上下内距，空态呈现单行，输入多行时随内容增长。
- `agent-chat.js`：复用既有 `autoSize()`，按 `scrollHeight` 增高并继续以 120px 封顶；未超过时 `overflow-y: hidden`，超过后切换为 `auto`，发送清空后既有 `autoSize()` 调用恢复单行。
- 报价侧保持原尺寸；反转旧“报价与技术几何必须完全相同”的技术侧过期测试，保留报价自身 50/54px、15px/24px 契约。`agent-chat.css/js` 缓存版本在四个 CSS 入口与两个 JS 入口同步升为 `20260914-compact1`。
- 验证：新目标、贴底回归及两组输入区契约合计 **32/32 通过**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 为 725 项、33 个既有并行失败、7 跳过，与相邻并行批次记录的 33 项失败数量一致，本批新增 0；`python3 -m py_compile`、`node --check tech_app/frontend/agent-chat.js`、`git diff --check` 通过。
- 边界：未改控件 DOM/无障碍标签、附件上传、发送、Enter / Shift+Enter、输入法保护、120px 上限、报价页面、消息/历史/任务、业务动作、九阶段、右侧看板或后端；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 33. 2.3「成本测算」去掉「运行成本测算」、主按钮随测算完成度反转 Spec / Red / 实现（9-14）

- 问题：站在 2.3 成本测算页时，`cost-review.js` 会往左侧操作栏推出 6 颗按钮，其中 `costStep`（运行成本测算，order 70）与 `runCostReview`（一键测算全部成本）是同义入口 —— 前者要 `payload.step ∈ part / assembly / all`，左侧栏不会给任何 step，用户点了只能拿到 `bad-step`（它真正的调用方是 Agent 的 `RunCostReviewPart/Assembly/All`）。同时 `tech-board-runtime.js` 的 `entryState()` 只从 `getState()` 读 role，动作条目上的静态 `role: 'primary'` 是死元数据，导致 2.3 左侧栏根本没有主按钮：测算前没有起点高亮，测算完成后也没有收口。
- Spec：`docs/specs/cost-review-single-primary-and-drop-run-step.md`：① `costStep.getState()` 返回 `visible: false`，注册 / 校验 / `crRunPart` `crRunAssembly` `crRunAll` `crCostStepInBackground` 后台链路一律保留；② 「一键测算全部成本」的 role 改为 `!crCostsComplete() ? 'primary' : 'aux'`、「确认成本」改为 `crCostsComplete() ? 'primary' : 'aux'`，同一份判定反转，任何时刻恰好一颗主按钮；③ 新增薄封装 `crCostsComplete() = !crConfirmBlocker()`，不新增第二份 `counts` / `ready` 判断；④ 动作条目上不再保留静态 `role: 'primary'`，`label` / `order` / `deferred` / `run()` 主体与三个去向、内部视图、后端路由、桥协议、右侧看板一律不动。
- 红测：`tests/test_cost_review_single_primary_and_drop_run_step_red.py`（23 项），覆盖 `costStep` 仍注册且 label / order / deferred / 校验 / 后台链路完整、`visible: false`、Agent `cost-step` 链与后端 7 条 cost-review 路由不变、左侧会话不得直连 `/cost-review`、`crCostsComplete()` 必须复用 `crConfirmBlocker()` 且只声明一次、两颗按钮的 role 反转、动作条目不得再留静态 primary、全文件只允许两处动态 primary、`confirmCostReview` 保持 `visible: true` / `enabled: true` / `not-ready` 结构化失败且不预判只读、三个去向仍走 `crOpAction` 且 order 30/40/50 不变、`refreshCostReview` 仍 `visible: false`、`registerViews` 四个视图与 `params` 显式拒绝、嵌入态右侧 `#crRunAll` / `#crOpsCard .ai-ops` 仍隐藏、页内逐件与单环节重跑入口保留、父壳过滤与主按钮规则不变且不认动作名、桥事件白名单不变。Red 基线：23 项中 **9 失败、14 通过**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 748 项、42 失败（33 项并行遗留 + 本批 9 项红测）、7 跳过，逐条比对失败清单确认本批未新增既有失败点。
- 实现提示词已在会话中交付；用户随后统一要求这些批次由 Codex 直接实现，故本批实现也在本仓库内完成。
- 实现（`tech_app/frontend/cost-review.js`，+17 / −4）：在 `crConfirmBlocker()` 下方新增薄封装 `crCostsComplete()`（函数体只有 `return !crConfirmBlocker();`，不含第二份 `counts` / `ready` 判断）；`runCostReview` 顶层静态 `role` 由 `'primary'` 降为 `'aux'`，`getState()` 改为 `role: !crCostsComplete() ? 'primary' : 'aux'`；`confirmCostReview` 的 `getState()` 改为 `role: crCostsComplete() ? 'primary' : 'aux'`（仍 `visible: true` / `enabled: true`，闸门与错误码不变）；`costStep` 的 `getState()` 改为 `visible: false`，label / `role: 'aux'` / order 70 / `deferred` / step 校验与 `crRunPart` `crRunAssembly` `crRunAll` `crCostStepInBackground` 后台链路一字未改。两处动态 role 由同一判定反转，2.3 任何时刻恰好一颗主按钮；父壳把 `role === 'primary'` 放进 `#techChatPrimary` 槽位，因此未调整 `order`。未新增接口、未改右侧看板与 `.tech-embed` 隐藏规则。
- 验证：本批红测 `tests/test_cost_review_single_primary_and_drop_run_step_red.py` **23/23 通过**（实现前基线 9 失败 / 14 通过）；相关回归 `test_tech_cost_review_agent_red`、`test_tech_business_actions_clickable_then_error_red`、`test_tech_board_deferred_actions_red`、`test_integration_params_tab_single_primary_and_auto_fill_red`、`test_integration_process_tab_single_primary_and_next_step_red` 合计 **94/94 通过**；`node --check tech_app/frontend/cost-review.js` 与 `git diff --check` 通过。全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **762 项、33 失败、7 跳过**，失败清单与改动前逐条一致（全部为并行批次遗留的过期断言），本批新增 **0** 个失败点。
- 边界：本批只改 2.3 看板的动作快照；未改后端路由 / service / `oc_agent.py` 工具映射；未改桥协议、运行时 role 白名单、父壳过滤与主按钮规则；未改 1.x / 2.1 / 2.2 / 3.x 的动作可见性、三个去向、内部视图与右侧看板页内按钮；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 34. 技术工艺左侧会话删掉常驻的「技术工艺评估助手」开场气泡 Spec / Red / 实现（9-14）

- 问题：`tech-workbench.html` 在唯一会话宿主 `#techChatPane` 的 `#ocTinner` 顶部静态写了一张 `oc-amsg > oc-abody > oc-intent-card`（「技术工艺评估助手」+「左侧会话在整个评估流程中持续存在：可以描述零件与工艺要求、追问图纸解析结果、发起解析，或直接说「开始解析」。」）。它不是消息：`agent-chat.js` 只在第一条消息时删 `#ocEmpty`（`clearEmpty()`），从不处理这张卡，唯一会连它一起清掉的是「新对话」的 `resetTaskFlow()`。结果是整个评估流程里这张气泡永久钉在最新消息上方，并与栏头「技术工艺智能体 + AI 徽标」及 `#ocEmpty` 的「工艺评估助手」引导重复。
- Spec：`docs/specs/tech-chat-drop-static-intro-bubble.md`：① 删掉父壳这张整块 `oc-amsg`（它只有这一张卡，无头像、无标题行），`#ocTinner` 与 `#ocEmpty` 之间不再有任何静态助手消息，文案在 `tech_app/frontend/` 下不再出现；② 引导能力不缩水：`#ocEmpty` 保留（图标、「工艺评估助手」标题、「按你的要求发起解析」「切换右侧步骤不会清空这里的会话」）且仍随首条消息消失；`#ocResultActions`（三颗结果入口，默认 `hidden`）与 `#ocTaskProgressHost` 仍是 `#ocTinner` 子节点；③ 不许用替代物补位：不新增第二张引导卡，父壳 JS 不得往会话栏写节点，会话脚本不得静态插开场卡；④ 只动父壳这一处：`index.html` 的 2.1 设计意图卡（`#intent` / `#btnParse`）、2.2 / 2.3 的说明卡与 `.oc-intent-card` / `.oc-intent-meta` CSS 规则全部保留；⑤ 行为不变：`clearEmpty()`、`resetTaskFlow()` 的按节点清理、输入区 / 操作栏 / 滚动宿主结构、桥事件白名单与后端一律不动。
- 红测：`tests/test_tech_chat_drop_static_intro_bubble_red.py`（14 项），覆盖父壳四个气泡标记全部消失、`#ocTinner` 到 `#ocEmpty` 区间无 `oc-amsg`、全前端目录不再留有开场文案、空态能力引导保留、不得换位置补卡、结果入口与进度宿主仍在会话线程且默认隐藏、父壳 JS 不写会话节点、会话脚本只由真实回复建消息且保留 `clearEmpty()`、2.1 与 2.2 / 2.3 的同类卡保留、`.oc-intent-card` CSS 保留、首条消息仍移除空态、「新对话」仍按节点清理、头部身份与输入区 / 操作栏元素齐全、桥白名单不变。Red 基线：14 项中 **10 失败、4 通过**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 762 项、52 失败（33 项并行遗留 + 第 33 批 9 项 + 本批 10 项红测）、7 跳过，逐条比对失败清单确认本批未新增既有失败点。
- 实现提示词已在会话中交付；用户随后明确要求由 Codex 直接实现，故本批实现也在本仓库内完成。
- 实现（`tech_app/frontend/tech-workbench.html`，纯删除 8 行）：整块移除 `#ocTinner` 顶部的 `div.oc-amsg > div.oc-abody > div.oc-intent-card`（含 `h4`「技术工艺评估助手」与 `.oc-intent-meta` 那段说明）。同栏其它子节点与顺序不变：`#ocEmpty` 空态引导（`✦` + 「工艺评估助手」+ 「按你的要求发起解析」「切换右侧步骤不会清空这里的会话。」，仍由 `clearEmpty()` 在首条消息时移除）、`#ocResultActions`（三颗结果入口，默认 `hidden`）、`#ocTaskProgressHost`。未新增任何替代气泡、未改 JS / CSS / 后端 / 桥；`agent-chat.js` 里那条「设计意图卡是页面结构的一部分」的注释本批刻意未动（`index.html` 的 2.1 功能卡仍在，且该文件含 CRLF/NUL，避免无谓重写）。
- 验证：`tests/test_tech_chat_drop_static_intro_bubble_red.py` **14/14 通过**（实现前基线 10 失败 / 4 通过）；`tests.test_tech_left_chat_controls_restore_red`、`test_tech_full_width_board_and_single_agent_pane_red`、`test_tech_chat_composer_compact_autogrow_red` 合计 21/21 通过；标签配平自检 `<div>` 36 对、`<section>` 3 对，`tech-workbench.html` 内已无 `oc-amsg` / `oc-intent-card` / `oc-intent-meta` / 开场文案；`git diff --check` 通过；`git diff --stat` 为 1 文件 / 8 行删除。全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **762 项、42 失败、7 跳过**，失败清单与改动前逐条一致（33 项并行遗留 + 第 33 批 9 项待实现红测），本批新增 **0** 个失败点。
- 边界：本批只删除父壳 `tech-workbench.html` 里那 8 行静态气泡，未改 CSS、JS 逻辑、后端路由 / service、桥协议与 `tech-embed` 规则；未改 `index.html` / `assembly-integration.html` / `cost-review.html` 与 `agent-chat.js`；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 35. 测试基线收敛：33 项被取代的过期断言按现行契约更新（9-14）

- 背景：全量测试长期停在「33 项失败」，此前每批都按「并行批次遗留的过期断言」跳过。本批先做只读审计：逐条比对失败断言与被后续批次取代的契约，结论是 **33 项全部为过期断言，0 个真实回归** —— 它们都在断言「某个东西还存在」，而那个东西正是用户后来明确要求删掉 / 改名的：左侧通用按钮（上一步 / 下一步 / 转交任务 / 失败重试 / AI 执行 / 次按钮）、父壳的 `STAGE_ACTIONS` / `STAGE_CHAT_FLOW` / `syncActionBar` 动作表与底栏代理、2.1 归位「更多功能 ▾」的四项能力入口、以及「助手消息不用白底 / 不要边框」这类被「白底 + 气泡边框」取代的外观契约。
- 更新（只改测试脚手架与对照表，未改任何业务实现）：`test_tech_left_toolbar_parity_red`（TOOLBAR_IDS 收敛为 `techChatPrimary` / `ocFilesAction` + 新增退役清单反向断言，九阶段表改用 `STAGES`，附件接线改指共享会话脚本 `agent-chat.js`，转交 / 失败重试改为「按钮退役 + 能力仍在」）；`test_tech_board_actions_into_left_toolbar_red`（退役按钮反向断言、tooltip 文案改「当前步骤主操作」、九阶段表改 `STAGES` 且不得带动作名）；`test_tech_agent_remove_stage_context_card_red`、`test_tech_board_action_registry_red`（父壳不得再持动作表 / 动作名 / selector，只由 `boardActionEntries()` 驱动）；`test_tech_assembly_action_button_states_red`（底栏代理改 `syncChatActions()`，2.2 动作与财务闸门改由组装页注册）；`test_tech_primary_quick_actions_and_bulk_part_analysis_red`（按钮文案来自看板 label，父壳只渲染 `data-tech-action`）；`test_tech_result_entries_board_views_red`、`test_tech_drawing_agent_actions_red`、`test_tech_direct_attachment_and_chat_capability_actions_red`（左侧只留「解析视图」，其余四项改断言 2.1「更多功能 ▾」且看板动作仍在，新增一项 more-menu 断言）；`test_tech_right_workspace_quote_rounded_card_red`、`test_tech_context_substeps_and_frame_status_red`（`syncActionBar` → `syncChatActions`）；`test_tech_cost_report_handoff_continuity_red`（发布页自己注册 `sendReportToQuote`，父壳不得写死）；`test_quote_tech_ai_message_white_surface_red`（白底 + 气泡边框为现行契约，仅禁止灰底 / 渐变 / 带尾巴气泡）、`test_quote_tech_agent_shell_parity_red`（底栏只留上下步，`techPrimary` / `techSecondary` 退役）；`docs/specs/tech-agent-recovery-21-quote-parity.json`（`transfer` 行锚点由已删除的 `techChatTransfer` 改指 2.2「确认工艺并发送财务」的派发弹窗 `aiOpenFinanceDialog`，并在 note 记录该契约变更）。
- 验证：`python3 -m unittest discover -s tests -p 'test_*.py'` **763 项、0 失败、7 跳过**（改动前为 762 项 / 33 失败 / 7 跳过）；`git diff --check` 通过。每条更新后的断言都改成对现行契约的正向校验（并注明「契约更新」原因），不是简单删除断言。
- 边界：只动测试文件与对照表 JSON；未改任何业务代码、后端路由 / service、桥协议、九阶段流转与页面结构；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 36. 九阶段每个可见状态恰好一个主按钮、恢复会话绑定说明且权限提示不挤压页面 Spec / Red（9-14）

- Spec：新增 `docs/specs/tech-global-single-primary-by-state-and-nonblocking-notices.md`，把“每页一个主按钮”细化为九阶段及 2.2 三个子页签的状态机契约：执行前唯一主按钮是一键生成/解析/测算，形成可确认结果后唯一主按钮切换为确认/提交/进入下一步；父壳只能消费右侧看板动作快照，不得猜测、补造或按数组顺序抢占主按钮。
- 文案与布局契约：恢复“会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。”，但说明行必须脱离 composer 正常文档流，既有 `padding: 10px 16px 0`、34×34 附件按钮、36×36 发送按钮、12px/20px 输入文字和 120px 自增高上限保持不变，输入框仍贴住会话列底边。
- 权限提示审计：技术统一父壳的 `#techContextNotice` 已位于既有 `.tech-workspace-context` 标题行，采用单行省略，不会额外新增一整条顶部高度；报价页 `确认需求解析结果.html` 的 `wfRenderBar()` 仍通过 `insertBefore(bar, host)` 把带 margin/padding 的 `#wfBar` 插入业务内容前方，仍会把页面向下顶。本 Spec 要求保留权限判断和提示内容，但把它迁入既有标题状态位或不参与布局的提示层，不得挤压内容区与上下步底栏。
- 红测：新增 `tests/test_tech_global_single_primary_and_nonblocking_notices_red.py`（11 项）；实际 Red 为 **7 项通过、4 项失败（13 个失败点，含子断言）**。失败范围精确对应：运行时尚无 `primary_count !== 1` 的确定性守卫；2.1、2.2、3.1、3.2 尚缺九条统一状态文案；会话绑定说明尚未恢复为非占位布局；报价权限条仍在普通文档流。九阶段动作注册、现有动态 role、2.2 三页签状态判定、主按钮渐变样式、紧凑自增高输入、技术标题行权限位和固定 flex 底栏七项保护守卫已通过。
- 边界：本批仅新增 Spec、Red 与周 changelog，未修改前端或后端实现；未新增接口、业务状态或第二套流程；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 37. 九阶段每个可见状态恰好一个主按钮、恢复会话绑定说明且权限提示不挤压页面 实现（9-14）

- 唯一主按钮运行时守卫（`tech-board-runtime.js`）：新增 `primaryAudit()` / `primaryDiagnostics()`，按 `getState().visible !== false` 统计 `role === 'primary'` 的条目并给出 `primary_count / primary_actions / visible_actions / stage / view`；`primary_count !== 1` 且确有可见动作时返回确定性诊断（注册中途不报）。快照 `publish()` 附带只读 `primary` 元数据，`actions` 结构、六个 state 事件与桥协议不变；新增导出 `auditPrimary` / `primaryDiagnostics`。
- 父壳只消费不猜测（`tech-workbench.js`）：拆出 `primaryEntries()`（`role === 'primary'` 判定只此一份），`primaryActionName()` 改为「恰好一个才认，否则返回空字符串」，不再取数组中第一个；异常帧隐藏主槽位、清空主按钮 tooltip，并把同一条诊断写进控制台；出现两个以上主按钮时按 error 等级进标题行提示位（清除后回落步骤状态），`applyStage` / 切换 stage 时复位诊断标记。
- 九阶段状态矩阵落地（文案 + 真实状态 role 反转）：1.1 沿用「一键解析需求 / 提交确认」；2.1 `app.js` 的 `parseDrawing` 改「一键解析图纸」并按新增共享判定 `drawingParsed()`（有零件或标准件）反转 role，新增 `confirmDrawingResult`「确认解析结果并进入下一步」——回读 `GET /api/projects/<id>` 真实状态后经既有 `TechEmbed.requestNavigate('process')` 进 2.2；2.2 `assembly-integration.js` 的 `runIntegration` 改「一键分析整合图纸」，新增 `confirmDrawingsAndNext`「确认图纸并进入参数推荐」（回读 `/integration` 后才切页签，`order: 15`，动态 role），`generateIntegrationParams` / `generateIntegrationProcess` 改「一键生成参数推荐 / 一键生成组装工艺」，参数页收口动作改「确认并进入下一页签」，组装工艺页保持「确认并进入下一步」，`sendIntegrationToFinance` 的 role 固定为 `aux`（不再抢主按钮）；2.3 沿用已实现的成本完成态反转；3.1 `summary-result.js` 新增 `srHasDraft()`（单据号 / 编制时间）反转「一键生成报告草稿」与「提交审核」；3.2 `report-review-result.js` 改「审核通过并进入下一步」；3.3 `report-publish-result.js` 按 `rpReport.status` 反转「发布报告 / 回传销售经理继续报价」，回传入口只在 `published` 出现、新建版本保持 `aux`。
- 会话绑定说明回归且输入框继续贴底：`tech-workbench.html` 在 `.oc-cinner` 内、输入框之后加回原文说明；`tech-workbench.css` 新增 `#techChatPane .oc-disc { position: absolute; bottom: 100%; margin: 0; … }`，`#techChatPane .oc-composer` 仍为 `padding: 10px 16px 0; flex: 0 0 auto`，底部 0 padding 与输入框底边坐标不变；`#techChatPane .oc-thread` 加 `padding-bottom: 22px` 给绝对定位说明留视觉余量，不覆盖输入框、附件 / 发送按钮或最后一条消息。
- 报价权限提示不再挤压页面（`确认需求解析结果.html`）：`wfRenderBar()` 去掉 `insertBefore(bar, host)` 的普通流插入，改为在既有固定标题行 `.results-title`（`.results-header` 的 `flex-shrink: 0` 子项）内创建单行省略状态胶囊 `.wf-note`；配色按状态走类（`wf-note-danger` / `wf-note-primary` / `wf-note-ok` / `wf-note-readonly`，只读态仍是主色浅蓝底 + 主色边框 + 深蓝字），悬浮标题显示全文；权限读取、待领取、待转交、可编辑 / 只读判断与按钮禁用逻辑一字未改，结果卡有效高度、上下步底栏位置与表格不再被顶动。
- 旧决策取代（只改测试与旧 Spec 记录，未删历史）：`tech-global-single-primary-by-state-and-nonblocking-notices.md` 取代 `tech-chat-composer-flush-bottom.md`、`quote-tech-chat-composer-model-removal-and-bottom-alignment.md` 的「技术工艺不得有说明行」条款，并在 `integration-params-tab-single-primary-and-auto-fill.md` 注明参数页收口文案改名；对应更新 `test_tech_chat_composer_flush_bottom_red`、`test_quote_tech_chat_composer_alignment_red`、`test_quote_tech_unified_composer_and_caption_red`（改为断言「说明存在但不占布局高度」）、`test_integration_params_tab_single_primary_and_auto_fill_red`（label 改名）、`test_tech_board_actions_into_left_toolbar_red`（不得在多个 primary 中取第一个）、`test_home_auth_ready_and_quote_primary_state_colors_red`（只读提示配色改由 `.wf-note-readonly` 承载）。
- 验证：本批红测 `tests/test_tech_global_single_primary_and_nonblocking_notices_red.py` **11/11 通过**（实现前基线 7 通过 / 4 失败 / 13 个失败点）；指定回归 `test_tech_chat_composer_flush_bottom_red`、`test_tech_chat_composer_compact_autogrow_red`、`test_integration_params_tab_single_primary_and_auto_fill_red`、`test_integration_process_tab_single_primary_and_next_step_red`、`test_cost_review_single_primary_and_drop_run_step_red`、`test_tech_board_action_registry_red`、`test_tech_board_state_envelope_dynamic` 合计 **91/91 通过**；`node --check` 覆盖 `tech-board-runtime.js`、`tech-workbench.js`、`agent-chat.js`、`app.js`（module 模式）、`assembly-integration.js`、`cost-review.js`、`summary-result.js`、`report-review-result.js`、`report-publish-result.js` 全部通过；`git diff --check` 通过。全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **784 项 / 8 失败 / 7 跳过**，8 项失败全部来自并行新增批次 `drawing-board-two-column-parts-and-3d`（TDD Red 待实现，不在本批范围），本批新增 0 个失败点。
- 边界：未新增后端路由 / Agent 工具 / 状态库；未改桥信封、六个 state 事件、`tech:command` 白名单与 projectId / stage 校验；未删任何既有动作（Agent 与内部链路仍可调用的动作保留注册、按需 `visible: false`）；未放宽权限、未用本地布尔冒充确认、未改历史数据；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 38. 会话绑定说明改到输入框下方（取代 ## 37 的绝对定位方案）（9-14）

- 背景：## 37 把「会话绑定当前项目；右侧工作台只承载业务步骤，不重复会话。」做成 `#techChatPane .oc-disc` 的绝对定位提示（`bottom: 100%`），会浮在输入框正上方。用户最新决定：这行应该在输入框下面，直接放下去；不要改输入框本身。
- 实现（只改 2 个前端文件 + 4 份过期断言）：`tech-workbench.html` 说明行留在 `.oc-cinner` 内、输入框之后（普通文档流），更新注释；`tech-workbench.css` 的 `#techChatPane .oc-disc` 去掉 `position: absolute / left / right / bottom: 100%`，改为 `margin-top: 9px`（颜色 / 字号 / 居中口径不变），`#techChatPane .oc-composer` 仍 `padding: 10px 16px 0; flex: 0 0 auto`，输入框 DOM、按钮尺寸、字号、自增高、Enter 发送与 IME 保护一字未改；`#techChatPane .oc-thread` 的 `padding-bottom` 由 22px 收到 16px（原为浮层预留的余量已不需要）。
- 旧决策取代（只改测试，未改本批新 Spec/Red）：`test_tech_chat_composer_flush_bottom_red`、`test_tech_global_single_primary_and_nonblocking_notices_red`、`test_quote_tech_unified_composer_and_caption_red`、`test_quote_tech_chat_composer_alignment_red` 的说明行断言由「必须 `position: absolute`、不占布局高度」翻转为「必须在输入框之后（下方）、不得 `position: absolute/fixed`、`margin-top: 9px`」，其余输入区契约（按钮尺寸、字号、自增高、composer padding、底栏锚定）保持不变。
- 验证：上述 4 份契约测试 + `test_tech_chat_composer_compact_autogrow_red` 合计 **43/43 通过**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **793 项 / 16 失败 / 7 跳过**，16 项失败全部来自并行进行中的两条红测（`test_drawing_board_two_column_parts_and_3d_red` 8 项、`test_tech_drawing_title_and_result_actions_cleanup_red` 8 项），本批新增 0 个失败点；`git diff --check` 通过。
- 边界：未改输入框 DOM / 按钮尺寸 / 自增高 / Enter 发送 / IME 保护；未改桥协议、九阶段流程、右侧看板、后端路由与 Agent 工具。本批已提交并双远端推送，并部署到 34 服务器。

## 39. 2.1 左侧按钮清理 + 已生成工艺推荐自动展开 Spec / Red（9-14）

- 需求（用户）：2.1「联网核验」「校验修正」已经在右侧「更多功能 ▾」里，左侧不再重复；左侧「解析视图」按钮也删掉；收口主按钮由「确认解析结果并进入下一步」改为「确认解析结果」；2.1 里某零件的工艺推荐若已生成，就自动展开，不必再点一次。
- Spec：`docs/specs/tech-drawing-toolbar-cleanup-and-process-auto-expand.md`。R1 左侧清理（`modelLookup` / `verify` 改 `visible: false` 但保留 `run` / `label` / `enabled`，`evidence` 视图与 `capability:evidence` 映射保留）；R2 文案（只改 2.1 这一条，其它阶段「…并进入下一步」不动）；R3 自动展开（复用既有 `partHasExistingProcess()` 只读判定 + 既有 `openPartAnalysis(part, "process")`，新增 `autoOpenGeneratedProcess(part)` 与 `autoOpenedProcessParts` 去重集合，触发点为 `selectPart` 末尾与 `runAllPartProcessesInBackground()` 完成链路）；R4 降级静默、不新建 Drawer/Modal、不新增路由、不触发生成。
- 红测：`tests/test_tech_drawing_toolbar_cleanup_and_process_auto_expand_red.py`（17 项）。Red 基线 **9 失败 / 8 通过**：失败点是 `evidence` 按钮仍在父壳操作栏、`modelLookup`/`verify` 仍 `visible: true`、收口按钮仍带「并进入下一步」、`autoOpenGeneratedProcess` / `autoOpenedProcessParts` / 两处触发点尚未实现；8 项为防缩水守卫（「更多功能」两项、Agent `execute-action` 分派、`evidence` 视图、确认动作的真实状态闸门、其它阶段文案、只读判定、`selectPart` 既有行为、后端路由与桥协议）。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` **810 项 / 18 失败 / 7 跳过**。其中 9 项来自本批新红测；另 9 项来自并行进行中的 2.1 图纸解析两栏化与标题行清理两条红测（工作区正被并行修改，`app.js` / `index.html` / `workbench.css` 已有未提交改动，本批未触碰）。
- 边界：本批只落 Spec 与红测，未改业务实现；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 40. 2.1 图纸解析固定两栏 + 父壳标题行等高 + 2.1 结果入口归位（9-14）

- 需求（用户）：① 2.1 iframe 内右侧看板改成同时可见的两栏（左零件清单 / 右 3D，唯一 `#secParts`/`#tree` 脱离抽屉）；② 父壳五个大流程共用标题行等高，九阶段嵌入态整块标题区退出布局；③ 删除会话结果条与「零件清单」入口，把「待澄清问题 / 解析报告」迁进 `#techChatActions`。
- 实现（前端 6 个文件）：
  · `index.html`：`.center-panel` 内新增 `.drawing-board-split` —— 左 `<section class="drawing-parts-column" aria-label="零件清单">` 承载唯一 `#secParts`/`#tree`，右 `<section class="drawing-model-column" aria-label="3D 视图">` 承载 `.center-header`（改 `<header>`）/`#modelPanes`/`#analysisPanel`；`#secParts` 从 `#ocDrawerBody` 整块移出并去掉 `data-drawer-section`，抽屉仍保留上传 / 解析视图 / 待澄清 / 核验 / 校正 / 3D 导入 / 版本审签。
  · `workbench.css`：`.drawing-board-split` 用 `grid-template-columns:minmax(260px,34%) minmax(0,1fr)`，`.drawing-parts-column` 独立 `overflow-y:auto`，`.drawing-model-column` `min-width:0`；`@media (max-width: 900px)` 单列（右花括号独占一行）。
  · `app.js`：`renderIR` 的清单渲染抽出具名 `renderTree(ir)`（复用同一 `#tree`，不销毁右栏 canvas，选中态仍按 `part_id` 恢复）；`BOARD_VIEW_SPECS.parts` 改 `focus:"parts"`，`runBoardView("parts")` / `parts-list` 只 `focusPartsColumn()`（focus + scrollIntoView）并返回 `{view:"parts-list"}`，`drawing-overview` 加 `setRightPane("model")` 且不再隐藏两栏；`renderPartsBoardToolbar()` 改幂等 `#partsBulkBar` 挂左栏（批量工艺推荐保留）；`initViewer()` 加 `ResizeObserver(.drawing-model-column)`。
  · `tech-workbench.css`：`.tech-workspace-context` 加 `min-height:52px`（子页签 / 状态 / 权限提示都不改标题行高度）；`#techChatActions` 内补 `.oc-chip` 对齐与告警 / 报告两态配色。
  · `tech-embed.js`：只隐藏 `.form-title` 的两条规则换成单条 `.tech-embed .title-section { display:none !important; }`，嵌入态整块标题区退出布局；`board-status → #techContextNotice` 链路与 `.title-row .status-badge` / `#status` / `.ai-status` / `.ai-tabs` 规则保留。
  · `tech-workbench.html` / `agent-chat.js`：删除会话结果条 `#ocResultActions` 与 `#ocPartsAction`/`#ocPartsCount`；`#ocQuestionsAction`（计数保留 `0`）/`#ocReportAction` 迁进 `#techChatActions`；共享脚本改用 `.oc-result-actions` 类选择器，映射表 / 高亮 / 点击绑定 / 摘要刷新同步收敛，`applyDrawingResultSummary` 只在 drawing 阶段显隐两颗入口；`result-summary` 与 `parts/questions/report` 三个 available 字段不变。
- 旧决策取代（只改测试与旧锚点，未改本批新 Spec/Red）：`test_tech_left_chat_controls_restore_red`、`test_tech_chat_drop_static_intro_bubble_red`、`test_tech_left_toolbar_parity_red`、`test_tech_quote_agent_parity_matrix_red`、`test_tech_ui_protocol_red`、`test_tech_direct_attachment_and_chat_capability_actions_red`、`test_tech_board_actions_into_left_toolbar_red`、`test_tech_left_toolbar_drop_generic_buttons_red`、`test_tech_result_entries_board_views_red` 里「结果条 / 零件清单入口必须存在」的断言翻转为「已退役」，宿主名改指 `#techChatActions`；`docs/specs/tech-agent-recovery-21-quote-parity.json` 的 `result_entry` 锚点由 `#ocResultActions` 改指 `#ocQuestionsAction`（note 记录契约变更）。
- 验证：本批红测 `test_drawing_board_two_column_parts_and_3d_red`（10/10）+ `test_tech_drawing_title_and_result_actions_cleanup_red`（9/9）全绿；指定回归 94/94；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **810 项 / 9 失败 / 7 跳过**，9 项失败全部来自并行进行中的 `test_tech_drawing_toolbar_cleanup_and_process_auto_expand_red`（见 ## 39）；`node --check` 覆盖 `app.js`(module) / `agent-chat.js` / `tech-embed.js` / `tech-workbench.js`；`git diff --check` 通过。
- 边界：未复制 `#tree` / `#secParts`、未新建第二份零件数据或后端路由；未删除零件详情 / 参数编辑 / 3D/2D / 版本 / 工艺推荐 / 批量工艺推荐 / 「更多功能」；未改三栏父壳宽度、看板桥信封 / 事件白名单 / projectId / stage 校验与 `TechRegisterViews` 视图名；未放宽权限；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 41. 报价输入框对齐工艺规格 + 工艺输入区贴住会话列底部 Spec / Red（9-14）

- 需求（用户）：报价单智能体的输入对话框没有按技术工艺的尺寸与字号改；技术工艺的输入框没有像报价那样贴着底，总是留一个空；红测要包含「工艺按报价的位置」。
- 实测根因（headless Chrome 1440×900，`/tmp` 探针脚本量真实 rect）：技术工艺 `.tech-workbench-body` 是三列 grid（`height:100vh` / `grid-template-rows:100%`，行高 900px），但 `#techChatPane` 只有 **862px** —— 它继承了 `agent-chat.css` 的 `.oc-agent-pane { height/max-height: calc(100vh - 38px); position: sticky; align-self: start }`，而工艺侧覆盖规则只改了 `height: 100%` 与 `position: static`，`max-height` 仍是 862px，于是整列被压短、输入区底边停在 862px，视口底部留 38px；报价 `.chat-panel` 900px、`.chat-input-area` 底边与列底重合（差值 0）。另测得报价输入框 76px / `padding:10px 12px 10px 20px` / `gap:12px` / textarea `15px/24px` / 附件钮 50px / 发送钮 54px，工艺 `.oc-inputbox-single` 为 52px / `7px 8px 7px 12px` / `gap:8px` / textarea `12px/20px` / + 34px / 发送 36px。
- Spec：`docs/specs/quote-tech-composer-geometry-and-flush-bottom.md`。R1 工艺会话列显式 `max-height: none` 撑满 grid 行、输入区贴住列底，且 `agent-chat.css` 里独立 2.1 页的 sticky 窗（`calc(100vh - 38px)`）原样保留；R2 两侧输入区底部内衬取同一数值；R3 报价 `.chat-input-wrapper` / `.chat-input` / `.chat-attach-btn` / `.chat-send` 的几何与字号逐项等于工艺 `.oc-inputbox` + `.oc-inputbox-single`（含圆形 + 34px、圆形发送 36px、图标 18px / 22px），配色仍用报价页自己的 token。
- 红测：`tests/test_quote_tech_composer_geometry_and_flush_bottom_red.py`（15 项，静态比对两个源文件的声明值；比较前剔除 `@media` 块，只认桌面基线）。Red 基线 **12 失败 / 3 通过**：失败点是工艺会话列缺 `max-height: none`、工艺 composer `padding-bottom` 0（报价 16px）、报价输入框 `padding` / `gap` / `min-height` / textarea `font-size`+`line-height` / 附件钮 50px / 发送钮 54px 未对齐；通过的是 3 个防缩水守卫（独立 2.1 页 sticky 窗、grid 行高基准、图标字号、报价配色 token、`oc-disc`、无第二套尺寸定义）。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` **825 项 / 21 失败 / 7 跳过**；21 项 = 本批新红测 12 项 + 上一批 2.1 左侧按钮清理与自动展开红测 9 项（待实现）。并行批次的两条 2.1 图纸解析红测已由他人实现并转绿（本批未触碰其文件）。
- 边界：本批只落 Spec 与红测，未改业务实现；未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 42. 2.1 左侧按钮清理 + 已生成工艺推荐自动展开、报价/工艺输入框几何与贴底 实现（9-14）

- 需求（用户）：`## 39` 与 `## 41` 两条 Spec/Red 直接实现，不再走 DeepSeek 提示词；并确认「已生成工艺推荐自动展开」也要一并做掉。
- 2.1 左侧按钮清理：`app.js` 的 `modelLookup` / `verify` 只把 `getState()` 改成 `visible: false`（`run` / `label` / `enabled` / `order` 原样保留，Agent 与看板内部仍按名字分派）；`tech-workbench.html` 删除左侧操作栏最后一颗静态能力按钮 `data-tech-capability="evidence"`。`evidence` 看板视图、`#secEvidence`、`capability:evidence` 映射、`dispatchDrawingCapability()`、`index.html`「更多功能 ▾」里的 `#btnModelLookup` / `#btnVerify` 全部保留。
- 收口文案：`confirmDrawingResult.label` 由「确认解析结果并进入下一步」改为「确认解析结果」；回读 `GET /api/projects/<id>`、`drawingParsed()`、`no-navigation`、`order: 15` 等真实状态闸门与其它阶段的「…并进入下一步」不动。工艺侧对应注释同步改写。
- 工艺推荐自动展开：新增 `autoOpenedProcessParts` 去重集合与 `autoOpenGeneratedProcess(part)` —— 复用既有只读 `partHasExistingProcess()` 判定与既有 `openPartAnalysis(part, "process")` 渲染，命中即在看板内部切到 `part-process`；触发点为 `selectPart()` 末尾与 `runAllPartProcessesInBackground()` 完成尾部（对当前选中件补一次）。读取失败静默回落 3D；不新增请求、不触发生成、不新建 `Drawer` / `Modal`。
- 输入框几何与贴底（根因）：`tech-workbench.css` 给 `#techChatPane.oc-agent-pane` 补 `max-height: none` —— 此前只覆盖 `height`，仍被 `agent-chat.css` 的 `.oc-agent-pane { max-height: calc(100vh - 38px) }` 压到 862px，整列短 38px、输入区底边停在 862px；`#techChatPane .oc-composer` 由 `padding: 10px 16px 0` 改为 `padding: 10px 16px`，与报价 `.chat-input-area` 底衬一致。
- 报价输入框对齐工艺规格：`确认需求解析结果.html` 的 `.chat-input-wrapper` 改 `gap: 8px; min-height: 0; padding: 7px 8px 7px 12px`，`.chat-input` 改 `font-size: 12px; line-height: 20px`，`.chat-attach-btn` 改 34px，`.chat-send` 改 36px 并补 `flex: 0 0 36px`。报价页配色 token、`oc-disc` 会话绑定说明行、textarea 自增高、Enter/IME 行为一字未改；`agent-chat.css` 里独立 2.1 页 sticky 会话窗（`height/max-height: calc(100vh - 38px)`、`position: sticky`、`align-self: start`）未动。
- 过期断言更新（只改测试，7 份）：`test_tech_chat_composer_flush_bottom_red`（composer 底衬 0 → 与报价同为 10px）、`test_quote_tech_chat_composer_alignment_red`、`test_quote_tech_unified_composer_and_caption_red`（报价几何 76px / `gap:12px` / `15px·24px` / 50px / 54px → 与工艺逐项一致）、`test_tech_global_single_primary_and_nonblocking_notices_red`（2.1 收口文案、composer 内衬）、`test_tech_result_entries_board_views_red`、`test_tech_step_primary_and_drawing_entry_cleanup_red`（`evidence` 由「留在左侧会话栏」翻转为「已退役」）、`test_tech_direct_attachment_and_chat_capability_actions_red`（`CAPABILITIES` 不再含 `evidence`，改断言左侧栏无任何静态能力按钮）。
- 验证：两批红测全绿 —— `test_tech_drawing_toolbar_cleanup_and_process_auto_expand_red` 17/17、`test_quote_tech_composer_geometry_and_flush_bottom_red` 15/15；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **826 项 / 0 失败 / 7 跳过**；`node --check` 按 module 校验 `app.js` 通过；headless Chrome 1440×900 实测：工艺 `#techChatPane` 高 900px（= 视口，差值 0）、`.oc-composer` 底边 900px（与列底差值 0）、输入框 52px / textarea `12px·20px` / ＋ 34px 圆 / 发送 36px 圆 / 上下内衬 10px，与报价页逐项相等；`index.html?stage=drawing&embed=1` 运行时快照确认 `modelLookup` / `verify` `visible: false`、`confirmDrawingResult.label = "确认解析结果"`、父壳操作栏在 drawing 阶段不再有 `data-tech-capability` 按钮（只剩「任务文件 / 待澄清问题 / 解析报告 / 主要操作」）。
- 边界：未新增后端路由 / Agent 工具 / 状态库；未删除任何动作与看板视图（只改 `visible`）；未改看板桥信封、state 事件白名单、`projectId` / `stage` 校验；未改 `#secParts` / `#tree` / 零件详情 / 参数编辑 / 3D·2D / 版本面板；未改输入框 DOM、自增高与 Enter 发送；自动展开的浏览器实测受「本地无含已生成工艺的真实项目」限制，仅做静态契约与运行时加载验证。本批未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 43. 2.1 恢复左右两栏 + 结果入口只在图纸解析阶段出现（9-14）

- 需求（用户）：① 2.1 图纸解析应该是「零件清单 | 3D」左右两栏，实际在统一工作台里退化成上下两栏；② 「⚠ 待澄清问题 / 解析报告」两颗结果入口只属于 2.1，别的阶段不要常驻。
- 根因（上下两栏）：`workbench.css` 的堆叠断点是 `@media (max-width: 900px)`，而统一工作台里 2.1 跑在 iframe 内 —— 1440 窗口下工作区约 930px，减 `.tech-results-area` 16px×2 外边距与 0.5px 边框后 iframe 仅约 890px，断点被命中，桌面端也被压成上下两栏（1280/1366 窗口更明显）。断点收紧到 `640px`：只有真·窄屏才上下堆叠，左栏 34%（最小 260px）与右栏 3D 保持并排。
- 根因（右栏被裁）：`.drawing-model-column{overflow:hidden}` 而 `.model-panes` 自身不滚动，右栏比栏高时「零件信息 / 参数编辑」会被裁掉、用户够不到。给 `.model-panes` 补 `overflow-y:auto`（`.model-panes[hidden]` 仍 `display:none`），`.center-header` 继续固定，左栏滚动不牵动 3D 标题。
- 根因（两颗入口常驻）：`.tech-chat-actions > button.oc-chip{display:inline-flex}` 的优先级盖过 UA 的 `[hidden]{display:none}`，`#ocQuestionsAction` / `#ocReportAction` 无视 `hidden` 属性在九个阶段全部显示。补一条同族高优先级规则 `.tech-chat-actions > button.oc-chip[hidden]{display:none}`；JS 侧 `applyDrawingResultSummary()` 仍按 `hidden = !isDrawing` 显隐、按 `available` 置灰、计数保留 `0`，HTML 静态即带 `hidden`，未新增/删除任何节点。
- 守卫测试：`test_drawing_board_two_column_parts_and_3d_red::test_mobile_stacks_parts_above_model` 断点断言由 900px 改为 640px（并注明 iframe ~890px 的实测口径），`test_desktop_grid_and_independent_parts_scroll` 新增右栏 `.model-panes{overflow-y:auto}` 断言；`test_tech_drawing_title_and_result_actions_cleanup_red` 新增 `test_result_entries_are_hidden_outside_drawing_stage`（HTML 默认 `hidden` + CSS 让 `[hidden]` 真正生效 + JS 按 `!isDrawing` 显隐）。两条都先复现 Red 再转绿。
- 缓存版本：`index.html` 的 `workbench.css?v=20260914-split1 → split2`；`tech-workbench.html` 的 `tech-workbench.css?v=twb17 → twb18 → twb19`。
- 结果入口归位到栏尾 + 改普通按钮样式（追加需求）：「⚠ 待澄清问题 / 解析报告」在 `#techChatActions` 里移到 `#techChatPrimary` **之后** —— 动态业务动作由 `syncChatActionList()` 插在主按钮之后，所以只有写在主按钮后面才永远落在整栏末尾。同时去掉按钮上的 `warn` / `oc-chip-report` 类，只留 `oc-chip` 基础类，并删除 `tech-workbench.css` 里告警黄（`#fffbeb/#fcd34d/#92400e`）与实心主色（`--twb-primary` 白字）两条覆盖规则；另补 `.tech-chat-actions > button.oc-chip:disabled` 白底描边，避免 `.oc-chip:disabled` 的灰底让它跟其它业务动作不一致。文案「⚠ 待澄清问题」「解析报告」、`0` 计数初值、`aria-live="polite"`、drawing 阶段才显隐、点击只走 `navigate-view` 出口全部未变；独立 2.1 页自己的结果条仍保留黄/主色 chip（那是 2.1 页内视觉，不在本次范围）。
- 守卫测试（先红后绿）：`test_tech_drawing_title_and_result_actions_cleanup_red::test_result_entries_are_plain_and_sit_at_the_end` —— 断言两颗按钮的 `id` 位置在 `#techChatPrimary` 之后、标签里不再出现 `oc-chip-report` / `warn`，且 CSS 不再有 `button.oc-chip.warn` / `button.oc-chip.oc-chip-report` 两条规则。
- 验证：`test_drawing_board_two_column_parts_and_3d_red` 10/10、`test_tech_drawing_title_and_result_actions_cleanup_red` 10/10；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **826 项 / 0 失败 / 7 跳过**；`git diff --check` 通过。
- 边界：未改后端路由 / Agent 工具、看板桥信封与 state 事件白名单、`projectId` / `stage` 校验；未改 2.1 页内按钮 `disabled` 语义、`#secParts` / `#tree` 唯一性与零件详情 / 参数编辑 / 3D·2D 链路；独立打开 2.1 页不受影响。未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 44. 会话卡片降噪 + 看板「预期内失败」不进会话 Spec / Red / 实现（9-14）

- 需求（用户）：`⚠ 看板已切换，命令已取消。`（同屏 3 条）、`⚠ 请填写提交意见。`、`⚠ 看板未找到确认意见输入框或意见为空。` 这些不需要输出；`进行中 / 任务失败 / 已完成 / • 正在通过确认… / 提交审核意见 / • 正在提交审核通过…` 这类看板卡片，报错卡与「只有标题」的卡都不要，只留**有实际执行内容**的卡。
- Spec：`docs/specs/tech-chat-card-noise-and-quiet-board-failures.md`。R1 任务卡只承载真实执行明细；R2 预期内失败码（`detached` / `note-target-missing` / `missing-comment` / `no-selection`）不进会话；R3 带入确认意见没目标输入框时不再报错。
- 红测：`tests/test_tech_chat_card_noise_and_quiet_board_failures_red.py`（14 项）。Red 基线 **14 失败 / 0 通过**。
- 实现：
  - `tech-board-bridge.js`：新增 `QUIET_FAILURE_CODES` + `isQuietFailure()`，`settle()` / `failPending()` 给预期内失败打 `quiet = true`，并把 `isQuietFailure` 挂上 `TechBoardBridge`。
  - `tech-workbench.js runBoardAction()`：quiet 失败提前 return —— 不进会话、也不占标题行提示位（看板自己已提示）；非 quiet 失败照旧两处可见。
  - `agent-chat.js`：新增唯一出口 `boardFailureNotice(prefix, error)`（+ 同源判定 `isQuietBoardCode`），19 处看板动作 / 导航 / 刷新 / 回填 / 零件打开失败改走它；`renderTaskProgress()` 加「有内容」闸门 —— 只有 `progress_log` 明细、或已有卡、或「失败且带真实原因且非 quiet」才建卡，纯 `label` / 纯状态 / 单条通用「正在…」进度一律不建卡；失败卡文案改用真实 `failureReason`。
  - `tech-board-runtime.js`：`task-failed` 三条路径的载荷补 `code`（只转发不判定）。
  - `requirement-confirm-page.js`：`applyConfirmationNote` 目标缺失 / 意见为空时返回 `{ ok: true, result: { applied: false } }`，不再产生 ⚠ 卡（与 1.3 `applyReviewNote` 写法一致）。
  - `app.js`：2.1「一键生成全部工艺推荐」是真实长任务但只发 `progress`，`allPartsProcessPublish()` 补逐件 `log` 明细，降噪后它的进度卡仍在。
- 过期断言更新（只改测试，1 份）：`test_chat_errors_inflow_and_drop_refresh_task_cards_red` 的「refresh 失败不得空 catch」断言改为接受统一出口 `boardFailureNotice(`（意图不变：失败仍以普通输出进会话）。
- 验证：本批红测 **14/14**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **841 项 / 0 失败 / 7 跳过**；`node --check` 覆盖 `tech-board-bridge.js` / `tech-board-runtime.js` / `tech-workbench.js` / `requirement-confirm-page.js` / `app.js`(module) / `agent-chat.js`；headless Chrome 实测本地服务：`tech-workbench.html` 与 `?stage=requirement-confirm&project=…`、`requirement-confirm.html` / `requirement-review.html` / `index.html?stage=drawing` 均无 page error，`TechBoardBridge.isQuietFailure({code:'detached'|'missing-comment'}) === true`、`({code:'timeout'}) === false`。
- 边界：quiet 只覆盖上述 4 个码；真实失败（超时 / 未就绪 / 后端失败 / `action-failed`）仍必须左右可见，未被吞掉；未改桥的信封 / 命名空间 / 版本 / `tech:command` 白名单 / `STATE_EVENTS`、看板动作注册与业务实现、后端路由与 Agent 工具、`.oc-task-card` 样式与结果入口常驻。**未做**（列入后续验证清单）：用已登录项目点通「切步骤取消在途命令」「确认页带入意见」「提交审核意见门禁」三条真实点击链路 —— 本地无登录会话，本轮只做了静态契约 + 运行时桥判定 + 页面无异常。本批未提交、未推送、未创建 MR/tag/Release、未部署或重启服务。

## 45. 技术工艺左侧会话跟随项目重绑并回放历史（9-14）

- 现象（用户）：统一工作台打开已有项目后左侧会话带不回来，会话里出现 `⚠ 读取历史会话失败：Not Found`。
- 根因一（环境，不是代码）：`/api/projects/{id}/agent/history` 由提交 `dd12eeb`（9-14 14:18）引入，本机 8010（`cpq_suite_server.py` PID 91007）与其子进程 8012（`tech_app_launch.py` PID 91022）都启动于 9-11 10:03，`tech_app_launch.py:99` 的 `uvicorn.run` 没有 reload。实测 `127.0.0.1:8012/openapi.json` 只有 `agent/{meta,send,new,settings}`，`…/agent/history` → `404 {"detail":"Not Found"}`、`…/agent/meta` → `401`；内网 `172.16.10.34:8010` 同路由 → `401`（已部署版本有该接口）。**结论：重启技术工艺服务即消失，不写代码兼容旧进程。**
- 根因二（代码）：`agent-chat.js` 的项目绑定是脚本加载时的 `const projectId` 一次性快照，`loadHistory()` 还有 `historyLoaded` 一次性闸门；统一工作台换项目（历史抽屉 `techHistoryRestore`、看板 `set_stage`、上下一步、浏览器前进后退）只改 `state.project` + 重挂 iframe + `pushState()`，不重载页面 —— 左侧因此永远停在首次进入时的项目：新项目历史带不回来，反向还会把 A 项目的对话 / 任务文件 / 进度卡串到 B 项目。
- Spec：`docs/specs/tech-agent-history-project-rebind.md`；红测：`tests/test_tech_agent_history_project_rebind_red.py`（14 项，Red 基线 12 红：11 failures + 1 error）。
- 实现：
  - `agent-chat.js`：`const projectId` → `let projectId`；新增 `setProject(nextId)` 作为唯一重绑入口（父壳不碰会话内部状态）—— 同项目幂等 return；换项目时 `historyLoaded = false`、清 `.oc-amsg` / `.oc-ubub`、`taskProgressCards.clear()`、清 `#ocTaskProgressHost` 子节点、`boardResultSummary = null`、`hasParsedIR = false`、隐藏结果条；空项目只回「未连接 / 未选择项目」不发请求；否则 `loadHistory()` → `loadMeta()` → `loadFiles()` / `renderComponentMatch()` / `refreshResultChips()`；**不调用 `/agent/new` 或 `resetTaskFlow()`**，未发送草稿保留；`window.ocTechAgent` 导出 `setProject`。
  - `tech-workbench.js`：新增 `syncChatProject()`（只调 `window.ocTechAgent.setProject`，入口缺失安全跳过）；`applyStage()` 在 `pushState()` 之后、`popstate` 在 `mountStageFrame()` 之后各同步一次。
- 验证：本批红测 14/14；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **869 项 / 0 失败 / 7 跳过**；`node --check` 覆盖 `agent-chat.js` / `tech-workbench.js`；headless Chrome 用 stub 接口实测（本地无登录会话）：`setProject('p1')` 后会话渲染出该项目的历史消息与工具卡（「读取零件清单」），重复 `setProject('p1')` 不重复请求也不重复渲染，切到 `p2` 后 p1 文案消失、p2 历史出现，切到空项目清空；再走父壳真实链路点历史抽屉项目卡片 → `techHistoryRestore` → `applyStage` → 触发 `/api/projects/p3/agent/history` 并渲染出 p3 的历史，URL 更新为 `?project=p3&stage=drawing`，页面无 page error。
- 本地服务重启（用户授权后执行）：终止 9-11 启动的旧 `cpq_suite_server.py`（PID 91007）与遗留子进程 `tech_app_launch.py`（PID 91022），用 `./open-claude/.venv/bin/python cpq_suite_server.py --host 0.0.0.0 --port 8010`（新会话、日志 `/tmp/cpq_suite_8010_b.log`）重启，新 PID 8010=33777 / 8012=33779。重启后 `curl /api/projects/fcac7095bded/agent/history` 由 `404 Not Found` 变为 `401 请先在配置报价 CPQ 中登录`（路由已加载），`127.0.0.1:8012/openapi.json` 出现 `agent/history`，headless Chrome 打开 `tech-workbench.html?stage=requirement-create&project=fcac7095bded` 不再出现 `Not Found`、无 page error、`window.ocTechAgent.setProject` 存在。Docker/systemd 未涉及，内网 172.16.10.34 未部署。
- 边界：未改 `/api/projects/{id}/agent/*` 路由与 `oc_agent.load_history()`、桥协议与白名单、九阶段流程、右侧看板与业务动作；未改 `resetTaskFlow()`（新对话）残留任务卡 DOM 的既有问题。本批未创建 MR/tag/Release、未部署（该条 changelog 随下一次提交入库并推送）。

## 46. 2.2 参数推荐：一键生成即补全 + 缺项软闸门 + 参数推荐归工艺经理（9-14）

- 需求（用户）：①「参数推荐那一页为什么没有实现填入所有必填项？」②「没填好或者没完成的地方，除了没有权限那种的强制不能操作之外，别的都还是允许操作，不要硬阻断，而是提示现在有什么什么没完成，确定要继续吗；确定的话就带着缺少的继续」③「一键生成参数推荐之后应该直接补全」④「参数推荐设置的只有财务经理能操作，这不对，这一步应该是工艺经理操作，之前这一步放错了，改回来之后没改权限」。
- Spec：`docs/specs/tech-params-autofill-and-soft-gates.md`；红测：`tests/test_tech_params_autofill_and_soft_gates_red.py`（14 项），Red 基线 **9 失败 / 5 通过**。
- 根因 1（补全没跟着生成跑）：`aiGenerateParamsFully()` 只在 `required_missing > 0` 时才补全，而真正入口是「开始整合分析」→ `aiRunAll()` —— 它生成完 `params` 之后**没有任何补全步骤**，从这条路进来的用户看到的是满屏「必填未给出」；判空也只数报价必填，字典里的非必填空格子一律不补。
- 根因 2（缺项硬阻断）：`aiConfirmParamsAndNext()` 在 `params_final` 为假时直接返回 `required-missing` 失败；`cost-review.js confirmCostReview.run()` 拿到 `crConfirmBlocker()` 的原因同样直接失败，没有「说明缺什么 → 人点头 → 带着缺口继续」这一步。
- 根因 3（权限归属写错）：`cpq-sso.js` 的 `COST_URL_PATTERNS` 仍把 `/integration/params/(autofill|finalize)` 当成本接口（整合参数短暂搬去 2.3 时的历史写法），只读横幅也只提「2.1 与 2.2 归工艺经理」。后端一直是对的：`/integration/params*`、`/integration/process*` 用 `auth.WRITE_ROLES`，只有 `/integration/cost` 与 `/cost-review/*` 用 `auth.COST_ROLES`。
- 实现（只改前端三个文件 + 缓存版本）：
  - `assembly-integration.js`：新增 `aiMissingParamFields()`（整张报价参数表的空格子，字典缺失时退回必填缺口）与 `aiAutoFillParams()`（`aiParamsAutofill()` → `applied > 0` 才 `aiParamsFinalize(false)`）；`aiGenerateParamsFully()` 与 `aiRunAll()`（生成 `params` 之后、生成 `process` 之前）共用这一条链路，用户不必再点第二次「智能补全 / 保存补填」。
  - 软闸门：新增 `aiAskProceed(why)`（Promise 化确认框，按钮「仍要继续 / 取消」，Esc 或点遮罩等同取消；不发请求、不用 `window.confirm`、不记状态）；`aiConfirmParamsAndNext()` 必填不齐时先摆清缺哪几项，人确认后先 `aiParamsFinalize(false)` 把已填的值落库，再走 `aiConfirmStep('params')` 带着缺口进入组装工艺，取消才停在参数推荐并返回 `required-missing`。
  - `cost-review.js`：新增同样的 `crAskProceed(why)`；`confirmCostReview.run()` 里只有 `why === crReadOnlyWhy()`（权限）是硬阻断，其余缺零件 / 缺件数 / 整机未算 / 0 元行走「提示 + 确认后继续」，继续仍复用既有 `crConfirmCost()`。
  - `cpq-sso.js`：`COST_URL_PATTERNS` 删掉 `/integration/params/(autofill|finalize)`，注释与只读横幅改成「2.1 图纸解析、2.2 组装与整合（整合图纸 / 参数推荐 / 组装工艺）归工艺经理」——前端提前告知的口径与后端 `_require` 一致。
  - `assembly-integration.css` 新增 `.ai-confirm-mask` / `.ai-confirm-box`（fixed 覆盖层，不参与页面布局，不顶走底栏与上下步按钮），`assembly-integration.html` / `cost-review.html` 的 `assembly-integration.css?v=ai7 → ai8`；16 个页面的 `cpq-sso.js?v=sso2 → sso3`。
- 过期断言更新（1 份，注明「契约更新（自动补全归口批次）」）：`test_integration_params_tab_single_primary_and_auto_fill_red` 的 `test_generate_params_calls_the_full_chain` / `test_auto_fill_only_saves_when_it_filled_something` 改指 `aiAutoFillParams()` + `aiMissingParamFields()`（意图不变：生成 → 补全 → 落库，空补全不写库）。
- 验证：本批红测 **14/14**；`test_integration_params_tab_single_primary_and_auto_fill_red` + `test_tech_integration_params_step_ownership_red` + `test_cost_review_single_primary_and_drop_run_step_red` + `test_tech_board_bridge_protocol_red` 合计 **77/77**；`node --check` 覆盖 `cpq-sso.js` / `assembly-integration.js` / `cost-review.js`；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **869 项 / 0 失败 / 7 跳过**；`git diff --check` 通过。
- 边界：未改后端路由与 `_require`（`WRITE_ROLES` / `COST_ROLES` 一字未动）、未改 `cpq:tech-board` 信封与七个事件、未改 `aiParamsAutofill()` / `aiParamsFinalize()` / `aiConfirmStep()` / `crConfirmCost()` 实现与接口、未删任何动作注册（`autofillIntegrationParams` / `saveIntegrationParamsFinal` / `confirmIntegrationParamsFinal` 仍以 `visible:false` 保留）、未放宽权限（403 仍是权威）。

## 47. 2.1 零件清单去掉「一键生成全部工艺推荐」批量按钮（9-14）

- 需求（用户）：「零件清单那里不要有一键生成全部工艺推荐这个按钮」。
- Spec：`docs/specs/tech-parts-list-drop-bulk-process-button.md`；红测：`tests/test_tech_parts_list_drop_bulk_process_button_red.py`（8 项），Red 基线 **7 失败 / 1 通过**。
- 实现：`tech_app/frontend/app.js` 删除看板内的批量工具条宿主 `partsBoardToolbarHost()` 与渲染函数 `renderPartsBoardToolbar()`（连同两处调用点，改为说明注释）；`tech_app/frontend/agent-chat.css` 删除只服务于该工具条的 `.board-parts-toolbar` / `.board-parts-bulk` 两条规则（保留注释说明归口）。
- 保留：批量能力本身不缩水 —— 左侧会话栏的「一键生成全部工艺推荐」动作注册、`startAllPartProcesses()` / `runAllPartProcesses()` 受控串行实现、单零件「工艺推荐」入口与后端 `POST /parts/{part_id}/process` 一字未动。
- 验证：本批红测 **8/8**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 通过（当前 904 项 / 0 失败 / 7 跳过）；`node --check tech_app/frontend/app.js` 通过；`git diff --check` 通过。
- 边界：未改后端路由 / Agent 工具 / 看板桥信封；未删任何动作注册与看板视图；未改零件详情、参数编辑、3D·2D、版本面板。本批为本地修改，未提交、未推送。

## 48. 2.1 主按钮三段式：解析 → 一键生成全部工艺推荐 → 确认解析结果（9-14）

- 需求（用户）：「解析之后的下一个主按钮是一键生成全部工艺推荐，之后主按钮才是确认解析结果，他们在按钮清单里的出现位置也按照这样来排序」。
- Spec：`docs/specs/tech-drawing-primary-bulk-then-confirm.md`；红测：`tests/test_tech_drawing_primary_bulk_then_confirm_red.py`（13 项），Red 基线 **11 失败 / 2 通过**。
- 实现（`tech_app/frontend/app.js`）：`parseDrawing` 保持 order 10、未解析 primary；`runAllPartProcesses` order 20 → **15**、`role: (drawingParsed() && !partsProcessComplete()) ? "primary" : "aux"`；`confirmDrawingResult` order 15 → **25**、`role: partsProcessComplete() ? "primary" : "aux"`（`visible: drawingParsed()` 与 `enabled: true` 不变，没生成完也可点，点了由既有闸门给真实原因）。
- 判定与探测（新增，均为同步判定 + 只读探测）：`processReadyParts` 缓存（按 `project:part`）、`markPartProcessReady()`、同步 `partsProcessComplete()`（`getState()` 里不得发请求）、`refreshBoardActionState()`（结论变化主动重发快照，主按钮自己翻面）、`async probePartsProcessState()`（复用既有只读 `partHasExistingProcess()`，按「项目 + 零件表」签名去重 + 单飞，只用 GET）。`renderIR()` 渲染后探一次；批量里的 skip 与成功、`autoOpenGeneratedProcess()` 命中的既有工艺都记为已生成。
- 过期断言更新（1 处）：`tests/test_tech_drawing_toolbar_cleanup_and_process_auto_expand_red.py` 的 `order: 15` → `order: 25`，注明「契约更新（主按钮三段式批次）」。
- 验证：本批红测 **13/13**；全量 **904 项 / 0 失败 / 7 跳过**；`node --check tech_app/frontend/app.js` 通过；`git diff --check` 通过。
- 边界：未新增后端接口（仍只有单零件 `GET/POST /parts/{part_id}/process`，无 process-all / bulk 路由）；未改解析链路、确认闸门与嵌入导航通道；未改父壳主按钮渲染与唯一 primary 守卫。本批为本地修改，未提交、未推送。

## 49. 1.2 确认 / 1.3 审核：主按钮命名对齐 + 意见改为选填（9-14）

- 需求（用户）：「确认需求的主按钮应该是通过确认，并且不强制必须要有意见，审核的主按钮是提交审核意见，也不强制必须要有审核意见」。
- Spec：`docs/specs/tech-confirm-review-primary-and-optional-note.md`；红测：`tests/test_tech_confirm_review_optional_note_red.py`（14 项），Red 基线 **4 失败 / 10 通过**。
- 实现：`requirement-confirm-page.js` 删除 `cfAct()` 里「意见为空 → `missing-comment` / 请填写提交意见」的硬拦（空意见按 `comment: ""` 交给既有 `/requirement/confirm` 与 `/requirement/return-to-draft`），提示由「* 必填，最多可输入 3000 字」改为「选填，最多可输入 3000 字」，页内主按钮 `#confirmPass` 文案「✓ 通过」→「✓ 通过确认」；`requirement-review-page.js` 删除 `rrSubmit()` 与看板动作 `submitRequirementReview` 里重复的驳回必填意见闸门，`rrBind()` 驳回提示由「* 如驳回，必填」改为「如驳回，可补充审核说明（选填）」，页内主按钮 `#submitReview` 文案「➤ 提交」→「提交审核意见」。
- 未放宽的真实约束：`pending_confirmation` / `pending_review` 状态闸门、审核结论单选项（`decision ∈ {approve, reject}`，未选仍报 `no-selection`）、`busy` 与 `invalid-status` 闸门、后端角色校验与审计留痕、五个看板动作注册名（含 `applyConfirmationNote` / `applyReviewNote` / `refreshData`）全部保留。
- 验证：本批红测 **14/14**；全量 **904 项 / 0 失败 / 7 跳过**；`node --check` 覆盖两个改动文件；`git diff --check` 通过。
- 边界：未改后端路由与 `requirement_service`、未改状态机与权限、未改 Agent 工具与桥协议、未新增第二套提交出口。本批为本地修改，未提交、未推送。

## 50. 2.2 组装工艺收口：主按钮改为「确认工艺并发送财务」+ 作用域泄漏补齐（9-14）

- 需求（用户）：①「组装工艺完成之后下一个主按钮应该是确认工艺并发给财务」；②「确认组装工艺之后就会进入下一步，下一步成本测算又不是工艺经理的，改成确认工艺并发送财务这个按钮直接就帮着顺便确认了再直接发送」。
- Spec：`docs/specs/tech-integration-confirm-finance-flow.md`；红测：`tests/test_tech_integration_confirm_finance_flow_red.py`（12 项），Red 基线 **9 失败 / 3 通过**。
- 主按钮与一次点完（`tech_app/frontend/assembly-integration.js`）：`sendIntegrationToFinance`（确认工艺并发送财务）order 20 → **42**、`role: (aiTab === 'process' && aiHasProcess()) ? 'primary' : 'aux'`、改 `deferred: true`（弹窗要人选接收人，不再占桥的 20 秒回合）；新增链路 `aiConfirmProcessAndSendToFinance()`：工艺没确认就先走既有 `POST /integration/process/confirm`（`aiConfirmStep('process')`）确认掉，再判 `aiFinanceBlocker()`，通过后打开既有 `aiOpenFinanceDialog()`；收尾由 `aiSendToFinanceInBackground()` 自报 `task-completed` / `task-failed`（带真实原因）并复位 `aiDeferredBusy` 与动作 busy。
- `confirmProcessAndNext`（确认并进入下一步）退出左侧栏（`visible: false`，动作注册与实现保留）：2.3 成本测算是财务经理那一步，工艺经理只需把任务交给财务。
- `aiFinanceBlocker(options)` 增加 `ignoreProcessConfirm`（默认口径不变）：同步回执里只判参数推荐那半边，「工艺还没确认」由链路先补。
- 作用域泄漏补齐：`aiStatusState()` / `aiAnalyzed()` 与并行批次已提升的 `aiSetTab()` 一样挂到模块作用域（闭包外的「确认图纸并进入参数推荐」原来一跑就是 ReferenceError）；`report-publish-result.js` 的 `rpRefreshReport()` 同样提升，3.3「⇪ 回传销售经理继续报价」不再 ReferenceError。
- 通用护栏：新红测扫描 `tech_app/frontend/*.js` 的全部注册 IIFE，任何以页面前缀声明的助手在 IIFE 外被引用即失败（防同类回归）。
- 过期断言更新（3 处，均注明「契约更新」）：`test_integration_process_tab_single_primary_and_next_step_red.py`（确认并进入下一步改 `visible: false` / `role: 'aux'`；财务闸门与弹窗改指后台链路）、`test_tech_business_actions_clickable_then_error_red.py`（弹窗调用点）。另修 2 处测试脚手架：`test_tech_integration_agent_red.py` / `test_tech_cost_review_agent_red.py` 的注册表提取正则被并行批次的 `}).catch(...)` 提前截断，改为取到注册表末尾。
- 验证：本批红测 **12/12**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **934 项 / 0 失败 / 7 跳过**；`node --check` 覆盖两个改动文件；`git diff --check` 通过。
- 边界：未改后端路由与 service、未改 `aiConfirmStep` / `aiParamsFinalize` / `aiOpenFinanceDialog` / `aiRunOp` 实现、未改看板动作名与视图名、未动并行批次已落地的 `confirmParamsAndNext` deferred 与 `agent-chat.js` 卡片降噪。本批为本地修改，未提交、未推送。

## 51. 下线「以财务经理身份登录…其余步骤只读」只读横幅（9-14）

- 需求（用户）：「不要这个当前以 财务经理 身份登录，你负责 2.3 成本测算…归工艺经理，这里是只读」；随后补充「现在会出现两处这个全都不要」。
- Spec：`docs/specs/tech-drop-readonly-bar.md`；红测：`tests/test_tech_drop_readonly_bar_red.py`（6 项），Red 基线 **9 失败**。
- 根因：`tech_app/frontend/cpq-sso.js` 的 `showReadonlyBar()` 在 `!state.canWrite` 时往 body 插 `.cpq-sso-bar`；统一工作台外层与嵌入阶段页各自加载同一份脚本，于是同一句 2.3 归属说明同时出现在外层和 iframe（「两处」）。
- 实现：删除 `showReadonlyBar()` 与调用点、删除 `.cpq-sso-bar` 三条样式；被拦写请求的 toast 收短为「这一步归工艺经理办理；当前账号没有这一步的操作权限」，不再复述 2.3 归属。
- 保留：写请求预判仍是 `state.canWrite || (state.canCost && isCostUrl(url))` 并返回结构化 403 + toast、登录墙 `showLoginWall()`、`openCpqLogin()`、`COST_URL_PATTERNS` / `isCostUrl()`、`cpq-sso-ready` 事件与 `CpqSso` 接口；后端 `_require` 仍是唯一权限判定方。
- 过期断言更新（1 处，注明「契约更新（只读横幅下线批次）」）：`test_tech_params_autofill_and_soft_gates_red.py::test_readonly_bar_names_params_as_process_step` 改为断言横幅已下线、写拦截仍说清这一步归工艺经理。
- 验证：本批红测 **6/6**；全量 **934 项 / 0 失败 / 7 跳过**；`node --check tech_app/frontend/cpq-sso.js` 通过；`git diff --check` 通过。
- 边界：未改后端权限与路由、未改 SSO 检查与镜像写入、未新增第二套权限提示。本批为本地修改，未提交、未推送。

## 52. 2.2 / 2.3 收口动作不再超时、失败不再钉底：`aiSetTab` 归位 + 收口动作 deferred + 失败不建卡（9-14）

- 需求（用户）：「⚠ 确认并进入下一页签超时未响应」「⚠ aiSetTab is not defined」这些报错都不要再有卡片了，因为有卡片之后会固定在底部。
- Spec：`docs/specs/tech-confirm-actions-no-timeout-and-no-failure-cards.md`；红测：`tests/test_tech_confirm_action_timeout_and_no_pinned_cards_red.py`（12 项）。
- 根因 1（`aiSetTab is not defined`）：`assembly-integration.js` 里 `aiSetTab` 只在 `aiRegisterTechBoardActions()` 的 IIFE 内用 `const` 声明，而两个收口函数 `aiConfirmDrawingsAndNext()` / `aiConfirmParamsAndNext()` 定义在外层却调用它 —— 点「确认并进入下一页签」必抛 ReferenceError。
- 根因 2（超时未响应）：「确认并进入下一页签」「确认成本」要弹人工确认框（缺项时等人点「仍要继续」），却是普通（非 deferred）动作 —— 桥 `DEFAULT_TIMEOUT = 20000` 把人的思考时间算成了超时。
- 根因 3（失败卡钉底）：`agent-chat.js renderTaskProgress()` 的 `keepFailure` 让「只有失败原因、没有任何执行明细」的事件也新建 `.oc-task-card`，而它长在 `#ocTaskProgressHost`（会话底部常驻宿主），建出来就永远钉在底部。
- 实现：`aiSetTab` 提成模块级 `function aiSetTab(name)`（注册闭包不再重复声明）；`confirmParamsAndNext` / `confirmCostReview` 改 `deferred: true`，`run()` 只启动后台链路并立即回执，成败由链路自己播报（本页状态位 + 普通会话输出），既有闸门 `aiAskProceed()` / `crAskProceed()` / `aiParamsFinalize()` / `aiConfirmStep()` / `crConfirmCost()` 全部保留；`renderTaskProgress()` 建卡闸门改为 `log.length > 0 || existingCard`，没有执行明细的失败一律不建卡，已经在跑的卡仍就地翻失败态并显示原因，且预期内失败码（`isQuietBoardCode(detail.code)`）不把在跑的卡翻红。
- 验证：本批红测 **12/12**；回归 `test_tech_chat_card_noise_and_quiet_board_failures_red` + `test_tech_board_bridge_protocol_red` + `test_cost_review_single_primary_and_drop_run_step_red` + `test_tech_params_autofill_and_soft_gates_red` + `test_integration_params_tab_single_primary_and_auto_fill_red` 合计 **93/93**；`node --check` 覆盖 `agent-chat.js` / `assembly-integration.js` / `cost-review.js`；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **934 项 / 0 失败 / 7 跳过**；`git diff --check` 通过。
- 边界：未改 `cpq:tech-board` 信封、七个事件、`tech:command` 方向与 projectId/stage 校验，未改桥的 `DEFAULT_TIMEOUT` 与 `QUIET_FAILURE_CODES`，未新增后端路由 / 字段 / Agent 工具，未删动作注册与业务实现。本批为本地修改，未提交、未推送。

## 53. 本批（## 47–52）提交、双远端推送与 34 服务器部署记录（9-14）

- 提交：`efef393` 技术工艺收口链路修复：`aiSetTab` 归位 + 收口动作 deferred + 失败不建常驻卡片；并落地并行批次（28 个文件，1640 插入 / 139 删除），覆盖 ## 47 零件清单批量按钮下线、## 48 2.1 主按钮三段式、## 49 1.2/1.3 主按钮命名与意见选填、## 50 2.2 组装工艺收口与作用域泄漏、## 51 只读横幅下线、## 52 收口动作超时与失败卡降噪。
- 推送：`python3 scripts/push_remotes.py --check` → `python3 scripts/push_remotes.py`，GitLab 与 GitHub `20260909` 均推送并回读成功；`git ls-remote` 双远端与本地 HEAD 都是 `efef393`。
- 部署（用户指令「部署0909到34」）：172.16.10.34 `/home/wugefei/CPQ/cpq_agent`（分支 `20260909`）`git fetch gitlab 20260909` → `checkout` → `pull --ff-only` 到 `efef393`；按既有顺序先停 8012 子进程再停 8010 主进程、等端口释放后重启 8010（新 PID 1186450，子进程 8012 新 PID 1186498 由主进程拉起）。`http://127.0.0.1:8010/` 与 `/api/health` 均 2xx 且 `status=ok`（`cadquery_available: true`），内网 `http://172.16.10.34:8010/` 实测 200。
- 边界：部署只 fast-forward 更新 tracked 文件，未删改服务器上的 `cpq_settings.json`、`cpq_history/`、`rule_history/`、`tech_data/`、`product_images/` 等持久化数据；未动同机 8013 / 8080 上的其它服务；未创建 MR/tag/Release。

## 54. 2.3 成本测算角色判定改为「服务端能力位优先 + 两套角色口径」（9-14）

- 需求（用户）：「⚠ 回传销售经理继续报价失败，请查看看板提示。」「⚠ 2.3 成本测算是财务经理的步骤；当前登录的是「财务经理」，这一页只能查看。请用财务经理账号登录后测算。」——自己就是财务经理，却被前端判成只读。
- Spec：`docs/specs/tech-cost-role-gate-capability.md`；红测：`tests/test_tech_cost_role_gate_capability_red.py`（12 项），Red 基线 **3–4 失败**。
- 根因：角色码有两套口径 —— CPQ 登录态（`window.cpqAuth.user()`，见 `cpq_auth.ROLES`）给的是 `finance_mgr` / `process_mgr` / `sales_mgr`；技术工艺内部是 `finance_manager` / `process_manager`（`cpq_sso.ROLE_MAP` 把 `finance_mgr → finance_manager`）。`cost-review.js` 的 `CR_COST_ROLES` 只写了内部口径 `['finance_manager','admin']`，于是 CPQ 财务经理永远落进只读分支：看板按钮置灰、去向动作被拒，会话里就出现上面那两条自相矛盾的话。
- 实现（`tech_app/frontend/cost-review.js`）：`crReadOnly()` 改为「服务端能力位优先」——先取 `window.CpqSso.state()` 的 `canCost`（`enabled && checked` 才采信；它来自 `main.py` 的 `sso.can_cost`，就是后端 `auth.COST_ROLES` 的判定结果），拿不到能力位再退回角色码白名单，且两套口径都收（`finance_manager` / `finance_mgr` / `admin`），角色码同读 `role_code` 与 `role`；`crStart()` 在拿不到 CPQ 登录态时补第二来源 `CpqSso.state().user`，让只读原因里的人名不再退化成「其他角色」。
- 保留：真闸门没松 —— 工艺经理（`process_mgr` / `process_manager`）在 2.3 仍是只读，`crReadOnlyWhy()` 文案保留；身份完全取不到时不拦，交给后端 403。后端 `auth.COST_ROLES`、`cpq_sso.ROLE_MAP`、`cpq-sso.js` 写拦截、`crRunOp` / `crConfirmCost` / 三个去向接口与看板动作名一字未动。全仓扫描确认只有 `cost-review.js` 存在这套跨口径比较（`home.js` / `account.js` 读的是技术工艺自身登录态，`assembly-integration.js` 的 `AI_FINANCE_ROLE` 取自 CPQ `/auth/roles`，口径本来就一致）。
- 过期断言更新（1 处）：`tests/test_tech_business_actions_clickable_then_error_red.py::test_cost_role_codes_match_backend_authority` 从「锁死 `CR_COST_ROLES = ['finance_manager','admin']`」改为「后端角色集 ⊆ 前端白名单 + 必须认 `finance_mgr` + 必须认 `CpqSso` 能力位」，注明「契约更新（2.3 角色判定批次）」。
- 验证：本批红测 **12/12**（含 node 动态跑判定段：CPQ 财务经理 / 技术工艺财务经理 / SSO 未就绪时按角色码 / 工艺经理仍只读 / 身份未知不拦 / 服务端能力位压过陌生角色码）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **946 项 / 0 失败 / 7 跳过**；`node --check tech_app/frontend/cost-review.js` 通过；`git diff --check` 通过；本地 8012 实测已下发修复后的 `cost-review.js`。
- 边界：未改后端权限、路由、字段与 Agent 工具；未删动作注册与业务实现；未提交、未推送、未部署 —— 34 服务器当前仍是 `efef393`，上面跑的还是旧的单口径判定，所以线上仍能复现该提示。

## 55. 本批（## 54）提交、双远端推送与 34 服务器部署记录（9-14）

- 提交：`3d80961` 2.3 成本测算角色判定改为服务端能力位优先 + 两套角色口径（5 个文件，256 插入 / 6 删除）：`tech_app/frontend/cost-review.js`、`docs/specs/tech-cost-role-gate-capability.md`、`tests/test_tech_cost_role_gate_capability_red.py`，以及 `tests/test_tech_business_actions_clickable_then_error_red.py`（契约更新）与 `changelog/changelog_9_14_18.md`。
- 推送：`python3 scripts/push_remotes.py --check` → `python3 scripts/push_remotes.py`，GitLab 与 GitHub 的 `20260909` 均推送成功并回读为 `3d809612e2d6159b6fd8b7c11037c3327ef23e40`。
- 部署（用户指令「0909部署到34」）：172.16.10.34 `/home/wugefei/CPQ/cpq_agent`（分支 `20260909`，工作区无 tracked 改动）`git fetch gitlab 20260909` → `checkout` → `pull --ff-only`，fast-forward `efef393 → 3d80961`；按既定顺序先停 8012 子进程再停 8010 主进程，确认两端口释放后重启 8010（新 PID 1213903，子进程 8012 新 PID 1213960 由主进程拉起）。
- 部署后核验：`http://127.0.0.1:8010/` 与 `/api/health` 均 200 且 `status=ok`（`cadquery_available: true`、`sso_enabled: true`）；线上下发的 `cost-review.js` 实测已含两套角色口径（`finance_mgr` 命中 2 处），CPQ 财务经理不再被判只读；同机 8011 / 8013 未受影响仍 200。
- 边界：只 fast-forward 更新 tracked 文件，未删改服务器上的 `cpq_settings.json`、`cpq_history/`、`rule_history/`、`tech_data/`、`product_images/` 等持久化数据；未动 8011 / 8013 / 8082 上的其它服务；未创建 MR/tag/Release。

## 56. 零件清单点零件进 3D 视图，「工艺推荐」只由零件行下的子按钮进入（9-14）

- 需求（用户）：「零件清单点零件的话就去 3D 视图，现在点零件和点工艺推荐都是去的工艺推荐」。
- Spec：`docs/specs/tech-part-click-goes-3d-not-process.md`；红测：`tests/test_tech_part_click_goes_3d_not_process_red.py`（11 项），Red 基线 **2 失败 / 9 通过**。
- 根因：`tech_app/frontend/app.js` 的 `selectPart()` 末尾无条件调用 `autoOpenGeneratedProcess(part)`；该函数对「库里已有工艺」的零件会 `openPartAnalysis(part, "process")`，把右栏从 3D 切到工艺推荐。于是所有选中零件的入口都被劫持：零件行点击（`renderNode`）、2D 缩略图 bbox 点击（`renderBboxes`）、生成结果展示（`showGeneratedResult`）；零件行下本来独立的「工艺推荐」子按钮反而看不出区别，3D 只在「该零件还没有工艺」时才出现。
- 实现：`selectPart()` 删除该调用，只保留既有 3D / 零件详情渲染（`setRightPane("model")`、`exitBoardViewHost()`、`markSelection()`、`togglePartSubActions()`、`updateChatContext()`、`notePartView("part-detail")`）；「已生成即自动展开」改在解析 / 生成完成的时机补一次 —— `showGeneratedResult()` 里 `selectPart(target)` 之后加 `autoOpenGeneratedProcess(target)`，批量工艺推荐收尾处的既有调用不变。
- 保留：零件行下的「工艺推荐」子按钮仍是唯一工艺推荐入口（`buildPartSubActions` → `openPartAnalysis(part, mode)` → `part-process` → `renderPartAnalysis`），`event.stopPropagation()`、单件 `POST /parts/{part_id}/process`、`partHasExistingProcess()` 只读判定、`autoOpenedProcessParts` 去重、零件层级视图注册（`PART_VIEW_PARENT` / `PART_FLOW_VIEWS` / `window.TechBoardPartViews`）与返回键、父壳不承载零件弹层的边界全部不动。
- 过期断言更新（1 处）：`tests/test_tech_drawing_toolbar_cleanup_and_process_auto_expand_red.py::test_select_part_triggers_auto_open` 改为 `test_select_part_stays_on_3d_and_generation_path_auto_opens`（`selectPart` 不得自动展开 + `showGeneratedResult` 保留自动展开），注明「契约更新（点零件=3D 批次）」。
- 验证：本批红测 **11/11**（先红后绿）；上述旧用例所在文件重跑 **17/17**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **957 项 / 0 失败 / 7 跳过**；`node --check tech_app/frontend/app.js` 通过；`git diff --check` 通过。
- 边界：未改后端路由 / Agent 工具 / 看板桥协议与事件；未新增请求、未触发生成、未新建父级 Drawer/Modal。本批为本地修改，未提交、未推送、未部署（本地 8010/8012 下发工作区文件，硬刷即可验收）。

## 57. 2.3 成本测算：工艺经理只留「发送给财务」主按钮（9-14）

- 需求（用户）：「工艺经理在成本测算那一页就不是这些按钮了 —— 确认成本、一键测算全部成本、写入数据库、回传销售经理继续报价、提交工艺经理确认，而是『发送给财务』的按钮，并且是主按钮」。
- Spec：`docs/specs/tech-cost-process-manager-send-to-finance.md`；红测：`tests/test_tech_cost_process_manager_send_to_finance_red.py`（10 项），Red 基线 **10 项用例 / 11 处断言失败**（含 subTest）。
- 根因：2.3 是财务经理的步骤（`auth.COST_ROLES`），工艺经理打开这一页时五颗财务动作本来就被 `crReadOnly()` 挡着（点下去只会得到「2.3 成本测算是财务经理的步骤…」），但这一页对他没有任何出口 —— 他真正该做的「把工艺与整机参数交给财务」只在 2.2（`确认工艺并发送财务`）有入口，2.3 上缺这一步。
- 实现（只改前端两处：`tech_app/frontend/cost-review.js`、`cost-review.html`）：
  - 新增 `crSendToFinance()`：直接 `POST /api/projects/{id}/integration/send-to-finance`（复用 2.2 既有出口与 `IntegrationPublishBody` 字段 `product_name` / `note`，收件人留空由报价侧落到默认角色），不重写 `aiOpenFinanceDialog`、不建第二套发送实现；反馈走既有 `crCard` / `crStatus` / `crToast` / `crPublishTask`。
  - 新动作 `sendCostReviewToFinance`：`label: '发送给财务'`、`order: 5`、`deferred: true`，`getState()` 返回 `visible: crReadOnly()`、`enabled: true`、`busy: Boolean(crBusy)`、`role: 'primary'`。条目上不写静态 `role`（运行时只认 `getState()`，静态 role 是死元数据，2.3 一律不写）。
  - 财务五颗动作（`runCostReview` / `confirmCostReview` / `writeCostReviewMaterial` / `sendCostReviewToQuote` / `returnCostReviewToProcess`）与 `costStep`、`refreshCostReview` 的 `visible` 改为 `!crReadOnly()`：仍全部注册、仍有真实 `run`，Agent 工具与看板桥照旧可调用，只是不占工艺经理的左侧操作栏。
  - `cost-review.html` 的 `.ai-ops` 增加 `#crSendToFinance`（`ai-op-btn primary`，默认 `hidden`），资源版本 `cost-review.js?v=cr5 → ?v=cr6`。
  - `crRenderOps()` 按 `crReadOnly()` 互斥切换两侧按钮并隐藏聊天卡里的 `#crRunAll`；`crRenderActions()` 在只读身份下不再渲染「测算未完成的 N 个零件」，改为说明成本归财务经理、入口是「发送给财务」；只读原因的文案同步点名「发送给财务」。
  - `crStart()` 之后补 `cpq-sso-ready` 监听：身份核对完成（`CpqSso.state().canCost` 到位）后重绘并重发动作快照，主按钮自动翻面。
- 保留：后端 `/cost-review` 全部 GET/POST/PUT 与 `/integration/send-to-finance` 路由、`services.integration.send_to_finance` 的参数 / 工艺确认闸门、`cpq_bridge.send_to_finance`、`auth.COST_ROLES`、`crReadOnly()` 的两套角色口径与能力位优先判定全部不动；点击仍给后端真实原因（如「请先在『组装工艺』里点『确认组装工艺』」），左侧「失败重试」照旧可用。
- 过期断言更新（3 个文件、4 处，均注明「契约更新（2.3 工艺经理发送给财务批次）」）：① `tests/test_cost_review_single_primary_and_drop_run_step_red.py::test_exactly_one_primary_role_is_declared` 从 2 处 primary 声明改为 3 处，并新增「财务两颗与「发送给财务」visible 互斥」断言；② 同文件 `test_confirm_cost_review_stays_visible_at_all_times` 改为只按身份 gate（不允许 `crCostsComplete()` 决定 visible，`enabled: true` 与不得直通页内 `.disabled` 不变）；③ `tests/test_tech_business_actions_clickable_then_error_red.py::test_readonly_does_not_block_left_toolbar` 改为允许 `crReadOnly()` 出现在 `visible`，但禁止它决定 `enabled`，并要求被点到时仍给 `crReadOnlyWhy()` 真实原因；④ 同文件 `test_cost_actions_declare_enabled_true` 由实现侧对齐（新动作 `enabled: true`）。
- 验证：本批红测 **10/10**（先红后绿）；`test_tech_business_actions_clickable_then_error_red` **24/24**；`test_cost_review_single_primary_and_drop_run_step_red` + `test_tech_board_actions_into_left_toolbar_red` **40/40**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **967 项 / 0 失败 / 7 跳过**；`node --check tech_app/frontend/cost-review.js` 与 `node --check tech_app/frontend/app.js` 通过；`git diff --check` 通过。
- 边界：未改后端路由 / service / Agent 工具 / 看板桥协议与事件；未删动作注册与业务实现；未新增第二套成本或发送算法；未新建父级 Drawer/Modal。本批与 `## 56`（点零件=3D）一起本地修改，待提交推送与 34 部署。

## 58. 本批（## 56 + ## 57）提交、双远端推送与 34 服务器部署记录（9-14）

- 提交：`e541fdf` 零件点击进 3D 不再被工艺推荐劫持；2.3 成本测算对工艺经理只留「发送给财务」主按钮（11 个文件，704 插入 / 18 删除）：`tech_app/frontend/app.js`、`tech_app/frontend/cost-review.js`、`tech_app/frontend/cost-review.html`、`docs/specs/tech-part-click-goes-3d-not-process.md`、`docs/specs/tech-cost-process-manager-send-to-finance.md`、`tests/test_tech_part_click_goes_3d_not_process_red.py`、`tests/test_tech_cost_process_manager_send_to_finance_red.py`、三个过期断言更新文件与本周 changelog。
- 推送：`python3 scripts/push_remotes.py --check` → `python3 scripts/push_remotes.py`，GitLab 与 GitHub 的 `20260909` 均推送成功；`git ls-remote` 回读两边都是 `e541fdf9cd0289b87f93b35e9f6b39271ec02852`。
- 部署（用户指令「改完之后提交推送然后0909部署到34服务器」）：172.16.10.34 `/home/wugefei/CPQ/cpq_agent`（分支 `20260909`）`git fetch gitlab 20260909` → `checkout` → `pull --ff-only`，fast-forward `ae1a6f7 → e541fdf`；按既定顺序先停 8012 子进程再停 8010 主进程，确认两端口释放后重启 8010（新 PID 1302252，子进程 8012 新 PID 1302344 由主进程拉起）。
- 部署后核验：`/` 与 `/api/health` 在 8010 / 8012 均 200 且 `status=ok`（`cadquery_available: true`、`sso_enabled: true`）；对 172.16.10.34 实际下发的 `/app.js` 与 `/cost-review.js` 取 SHA-256，与本机工作区逐字节一致；下发 `cost-review.js` 已含 `crSendToFinance` / `sendCostReviewToFinance` / `visible: crReadOnly()`，`cost-review.html` 已是 `cost-review.js?v=cr6` 且含 `#crSendToFinance`；下发 `app.js` 的 `selectPart()` 内已无 `autoOpenGeneratedProcess`（解析 / 生成收尾各保留一次）。同机 8011 / 8013 仍 200，8082 仍 307。
- 边界：只 fast-forward 更新 tracked 文件，未删改服务器上的 `cpq_settings.json`、`cpq_history/`、`rule_history/`、`tech_data/`、`product_images/` 等持久化数据；服务器工作区无 tracked 改动（仅 `nohup.out`、`.dockerignore.bk*`、`jdk.tar` 等未跟踪文件）；未动同机其它服务；未创建 MR/tag/Release。

## 59. 成本回传报价的桥函数 `send_to_quote()` 重复定义（9-15）

- 需求（用户）：「严重：成本回传报价的桥函数被重复定义覆盖」—— `cpq_bridge.py:131` 的正确版本会发 `handoff_kind: cost_to_quote` 与 `result_version`，`:159` 又定义了一次同名函数且没有这两个字段，Python 只用后一个，前一份完全失效。
- Spec：`docs/specs/tech-cpq-bridge-send-to-quote-single-definition.md`；红测：`tests/test_tech_cpq_bridge_send_to_quote_single_definition_red.py`（12 项），Red 基线 **5 失败 + 1 错误**（另 6 项是防回归守卫）。
- 根因链（实测，非推测）：客户端第二份实现发出的 payload 只有 `session_id` / `title` / `customer` / `project_name` / `note` / `source_task_id` / `source_session_id` / `result`；服务端 `cpq_suite_server.py:432` 用 `d.get("handoff_kind") or "cost_to_quote"` 兜住类型（**侥幸正确**），`:439` 把缺失的 `result_version` 读成 `""`；服务端 `cpq_tech_bridge._handoff_key(session, project, kind, version)` 实测为空版本时得到 `p|s|cost_to_quote|`，并且命中仍未关闭的既有任务时会返回 `already_sent` **早退**（不合并新 `result`、不派发任务、不通知销售）。
- 影响：`handoff_kind` 变成隐式依赖服务端默认值（默认值一变就会静默串到报告回传语义）；`result_version` 恒空导致同一项目 + 同一报价会话的所有成本版本共用一个幂等键，**成本复核后改了成本再回传会被上一次未关闭的任务吞掉，新成本数永远到不了报价卡片**。
- 现状测试为何全绿：既有测试对桥函数做的是**静态文本**提取（拿到的是第一份函数体），从不验证运行时真正绑定的对象；本批红测改为真的 import 模块、把 `_post` 换成假实现，直接断言实际发出的 payload，并断言 `send_to_quote` 的运行时签名含 `result_version`。
- 实现（本批）：① `tech_app/backend/services/cpq_bridge.py` 删除第二份重复的 `send_to_quote`（只减不增，`git diff` 为 26 行删除），只保留带 `handoff_kind: "cost_to_quote"` 与 `result_version` 形参的那一份；`report_handoff`、`write_material` / `send_to_finance` / `return_to_process` / `complete_task` 与 `"/wf/tech/handoff"` 路径一字未改。② `tech_app/backend/services/cost_flow.py` 的 `integration_send_to_quote_body()` 调用点：把 `integration_quote_result(...)` 提到调用前只算一次（同时取 `result_version`），按位置把版本号作为第 10 个实参交给桥；版本号仍来自既有的 `cost_flow.result_version(plan)`（`cost-v1:{quantity}:{total}`），调用点未另写版本算法，2.2 与 2.3 两个入口共用这一条正文因此行为一致。
- 验证：本批红测 `tests/test_tech_cpq_bridge_send_to_quote_single_definition_red.py` 12 项 **6 处失败 → 0（12/12 全绿）**；运行时实测（假 `_post`）关键字传版本 → payload `handoff_kind=cost_to_quote` / `result_version=cost-v1:1:1234`，位置传旧 9 参 → payload 十个键齐全且 `result_version=""`（缺省不丢键），位置传第 10 参 → 版本号原样透传；`cost_flow.result_version` 同数据同号（`cost-v1:1:0`）、换数量换号（`cost-v1:3:0`）。回归 `tests.test_tech_cost_report_handoff_continuity_red` + `tests.test_tech_cost_review_agent_red` + `tests.test_tech_report_publish_agent_red` **42 项全绿**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **979 项 / 0 失败 / 7 跳过**；`py_compile` 两个文件通过。
- 边界：未新增 / 删除路由，未改 `cpq_suite_server.py`（继续 `handoff_kind` + `result_version` 读载荷）、`cpq_tech_bridge.py`（幂等键仍是四元组、`already_sent` 早退不变）、`cpq_wf.py`，未改报价第 2/3 步快照与步骤单调性；报告回传仍是 `report_to_quote` + `report-v{n}`；未拆函数、未留兼容壳、未用 `try/except` 吞 `TypeError`；未改任何测试断言。本批为本地修改，未提交、未推送。

## 60. 2.3 成本结果没有进入 3.1 汇总报告（红测已就位，待实现）（9-15）

- 需求（用户）：「2.3 成本结果没有真正进入 3.1 报告」—— 财务做完零件成本 / 组装成本 / 合计 / 确认，3.1 仍显示占位句，用户只能人工改写；而门禁会拦「待评估」，手工把状态点成「可行」又可能让错句作为正式报告内容过审。
- Spec：`docs/specs/tech-summary-3-1-includes-cost-review.md`；红测：`tests/test_tech_summary_report_includes_cost_review_red.py`（14 项），Red 基线 **9 处失败**（另 5 项是守卫）。
- 根因（实测）：① `tech_app/frontend/summary-result.js:20` 的 `srLiveView()` 把评估项「经济可行性」写死成 `status:'待评估'` + `conclusion:'尚未接入可追溯的成本与报价结论。'`；② 阶段汇总只拼 `2.1 图纸解析` + `srIntegrationStage(steps.integration)`（2.2），**没有 2.3**；③ 3.1 保存时由 `srRead()` 把页面表格读成 `evaluation_items` / `stage_results` 经 `PUT /process-report` 落库 —— 占位句就是落库内容，而 `report_workflow.content_issues()`（`:230`）只按 `status ∈ {待评估, 需补充}` 拦截，状态被手工改掉后占位句可一路送审；④ 后端 `services/summary.py` 其实已装载 2.3 状态（`_LOADERS["cost_review"] = store.load_cost_review` → `steps.cost_review`），但没有对外口径，前端也没读。
- 红测方式：用 node 真实执行 `srLiveView()`（喂确认 / 未确认 / 完全没做三种 aggregate，并同时提供 `steps.cost_review`、`steps.integration.cost` 与聚合层 `cost` 口径），断言渲染出的行内容 —— 不再用静态文本提取判断行为。
- 待实现（DeepSeek）：① `summary.aggregate()` 增加**顶层** `cost` 口径，复用既有 `cost_review.summarize()` / `payload()` 与 `integration.load_plan()`（顶层而非 `steps.cost`，避免改动 `report_source_payload` 审核依据摘要、无故挡下已有草稿）；② 3.1 用 2.3 数据生成经济可行性结论（已确认→「可行」+ 整机成本合计 + 确认人/时间；已测算未确认→仍「待评估」并说明未确认；未开始→说明尚未完成），并在 2.2 之后新增「2.3 成本测算」阶段行，已确认时不得出现「尚未 / 暂无」（否则门禁会拦整份报告）；③ `content_issues()` 增加只针对「经济可行性」的占位句拦截，状态被手工改成「可行」但结论仍含「尚未接入 / 未接入 / 可追溯 / 占位」时继续阻止送审。实现提示词只在会话交付，未落盘。
- 验证：本批红测 **14 项 → 9 处失败**（先红）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **993 项 / 9 失败 / 7 跳过**，失败全部来自本批新红测。
- 边界：未改任何业务实现、未改路由 / 权限 / 成本算法 / 2.3 写路径；未动工作区里并行的 `cost_flow.py` / `cpq_bridge.py` 改动（那是第 1 项缺陷的实现，其红测现已 12/12 通过）。本批为本地新增 Spec 与红测，未提交、未推送。

## 61. 历史记录 / 首页卡片恢复项目落到真实阶段（红测已就位，待实现）（9-15）

- 需求（用户）：「历史记录恢复不到真实当前阶段」—— `tech-workbench.js:1144` 的 `techStageFromProject()` 只判断需求状态 / 是否有报告 / 是否有 IR，有 IR 就一律回 `process`（2.2）；做到 2.3 甚至财务已测完成本的项目重开仍退回 2.2，财务经理也进不了自己的 2.3。
- Spec：`docs/specs/tech-history-restore-real-stage.md`；红测：`tests/test_tech_history_restore_real_stage_red.py`（15 项方法 / 38 处断言失败），Red 基线 **10 项方法失败、5 项守卫通过**。
- 根因（实测）：判定里完全没有 2.2 参数确认（`plan.params_confirmed`）、工艺确认（`plan.process_confirmed`）、是否已发送财务（`plan.finance_handoff`）、2.3 是否已有成本（`/cost-review` 的 `parts[].has_cost` / `ready`）、成本是否确认（`cost_review.confirmed`）、是否已退回工艺经理复核（`cost_review.actions.kind === 'return-to-process'`）。同一条判定在 `报价首页.html` 的 `techStageFromFlow()`（首页卡片点开时拼 `stage=`）里是**第二份现役拷贝**，缺陷一致；`tech_app/frontend/home.js` 是第三份，但 `home.html` 已不再加载 `home.js`，属停用代码，本批不动。
- 待实现（DeepSeek）：① 新增纯函数模块 `tech_app/frontend/tech-stage-restore.js`，导出 `window.TechStageRestore.fromSignals(flow, projectData, signals)`，按 Spec 固定的 9 步顺序返回白名单内的 stage id（含「2.2 整机成本不算 2.3 已开始」「已退回工艺经理 → 3.1」两条关键规则）；② `tech-workbench.js` 的 `techHistoryRestore()` 与 `报价首页.html` 的 `openTechProject()` 各自取 `/workflow` + `/summary`（必要时 `/cost-review`，其 GET 无角色限制）后委托共享函数，删除两处自建的 `hasIr ? 'process' : 'drawing'` 分支；③ 两个页面引入新脚本。实现提示词只在会话交付，未落盘。
- 行为断言方式：用 node 真实执行共享纯函数，跑 22 例判定矩阵（需求三态、报告三态、无需求老项目、只有 IR、参数/工艺确认未发财务、已发财务、2.2 整机成本负例、2.3 已确认 / 已核算 / 已备注 / 已有逐件成本 / ready、已退回工艺经理、无 IR 但在 2.3），断言返回的阶段 id 与白名单 —— 不做静态文本提取。
- 验证：本批红测 **15 项方法 / 38 处失败**（先红）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **1008 项 / 38 失败 / 7 跳过**，失败全部来自本批新红测。
- 顺带核实：工作区里第 1 项（桥函数重复定义）与第 2 项（2.3 → 3.1 成本汇总）的实现已落地（`cpq_bridge.py` / `cost_flow.py` / `summary.py` / `report_workflow.py` / `summary-result.js`），两份红测现均通过（26 项 OK）；尚未做代码审查与全量验收，也未提交。
- 边界：本批只新增 Spec 与红测，未改任何业务实现；未动工作区里并行批次的文件；未提交、未推送。

## 62. 2.3 成本结果进入 3.1 汇总报告（实现）（9-15）

- 需求：承接 ## 60，把 2.3 的成本测算结果真正带进 3.1 汇总报告；只做「2.3 → 3.1」这条展示链，不动路由 / 权限 / 成本算法 / 2.3 写路径。
- Spec / 红测（本批未改）：`docs/specs/tech-summary-3-1-includes-cost-review.md`、`tests/test_tech_summary_report_includes_cost_review_red.py`（14 项），Red 基线 9 处失败。
- 实现（只改 3 个文件）：
  - `tech_app/backend/services/summary.py`：新增只读 `_cost_rollup(project_id)` —— 用 `store.load_ir` + `integration.load_plan` 取得 IR 与整机方案，复用既有 `cost_review.summarize()`（零件 / 整机 / 合计 / ready / missing / zero）与 `cost_review.load_review()`（确认状态 / 确认人 / 时间），返回顶层 dict（`ready / missing / zero / parts_total / assembly / final / quantity / review`）；`aggregate()` 在**顶层**新增 `"cost": _cost_rollup(project_id)`，**不进 steps** —— 保证 `report_workflow.report_source_payload()` 的摘要键仍是 `device_name / ir / steps / summary`，已有草稿不会因「上游工艺数据已变化」被挡在审核外。未引 `cost_model`、未对 `cost.items` 求和（合计直接取 2.3 口径）。
  - `tech_app/frontend/summary-result.js`：`srLiveView()` 删掉写死的「尚未接入可追溯的成本与报价结论。」；新增 `srMoney`（仅展示格式化）/ `srCostInfo` / `srCostConfirmedText` / `srCostAmounts` / `srCostEconomic` / `srCostStage`。经济可行性三态 —— 2.3 已确认 → 「可行」+ 整机成本合计 + 确认人 / 时间；已测算未确认 → 「待评估」并说明「成本已测算…财务尚未确认」；没做 → 「待评估」说明成本测算尚未完成。阶段汇总在 2.2（`srIntegrationStage`）之后追加「2.3 成本测算」行，仅已确认才加（否则该行必然带「尚未 / 暂无」被门禁拦）。确认信息优先取聚合层 `cost.review`，回落 `steps.cost_review`。
  - `tech_app/backend/services/report_workflow.py`：`content_issues()` 增加只针对「经济可行性」的规则 —— 状态不是「待评估 / 需补充」但结论仍含「尚未接入 / 未接入 / 可追溯 / 占位」时继续拦送审，提示「请先回到 3.1 刷新汇总，把 2.3 成本测算的结论带进来再送审」；原有 `status ∈ {待评估, 需补充}` 规则不删不放宽，其它评估项（如「暂无重大风险」）不受影响。
- 验证：红测 `tests/test_tech_summary_report_includes_cost_review_red.py` **14/14**（9 处失败 → 0）；回归 `tests.test_tech_report_publish_agent_red + tests.test_tech_cost_review_agent_red + tests.test_tech_e2e_scenarios_red` **35 项全绿**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **993 项 / 0 失败 / 7 跳过**；`node --check tech_app/frontend/summary-result.js`、`python3 -m py_compile tech_app/backend/services/summary.py tech_app/backend/services/report_workflow.py` 通过。行为级核验（真实构造 `ProcessReport` 调 `content_issues`）：① 可行 + 占位句 → 拦；② 可行 + 真实结论 → 放行；③ 待评估 + 占位句 → 拦（原规则）；④ 其它评估项「暂无重大风险」→ 放行（新规则只认经济可行性）；`report_source_payload()` 摘要键仍为 `['device_name','ir','steps','summary']`。
- 边界：未改路由 / 权限 / 请求体字段；未改 2.1 / 2.2 两行结论与 `srIntegrationStage`；未在 3.1 或 `summary.py` 重算成本（未引 `cost_model`、未对 `cost.items` 求和）；未改 `cost_review` / `integration` 写路径与 2.3 页面（只读复用）；新口径只在顶层、未塞进 `steps`；未放宽 `content_issues()` 既有规则，新规则未扩大到其它评估项；未改红测。未动工作区里并行的 `cost_flow.py` / `cpq_bridge.py`（## 59）与并行批次的文件。本批为本地修改，未提交、未推送。

## 62. 零零件 IR 的解析完成判定 + 第 22 步验收清单过期（红测已就位，待实现）（9-15）

- 需求（用户）：「有效的零零件 IR 永远不算图纸解析完成」与「验收文档已经落后于当前产品」。
- Spec：`docs/specs/tech-empty-ir-parse-completion.md`、`docs/specs/tech-e2e-acceptance-doc-refresh.md`；红测：`tests/test_tech_empty_ir_parse_completion_red.py`（12 项方法，Red 基线 27 处失败）、`tests/test_tech_e2e_acceptance_doc_current_red.py`（14 项方法，Red 基线 15 处失败）。
- 零零件 IR 根因（实测）：`tech-workbench.js:898` 用 `irParts.length` 当「图纸解析完成」标志，同写法还有 `tech-workbench.js:1156` 与 `报价首页.html:1677`。解析成功才会写 IR —— `store.save_ir(..., stage="parsed")` 同时写 IR 文档、`meta.ir_revision += 1`、`meta.stages["parsed"] = 时间戳`（3D 导入写 `parsed_3d`），只上传未解析时只有 `stages.uploaded`。因此完成依据应是「IR 存在（非空对象）或解析留痕或解析版本 ≥ 1」，零件数量只是结果。
- 验收清单过期（实测）：`docs/specs/tech-agent-recovery-22-e2e-scenarios.json` 仍要求「左侧操作栏的上一步 / 下一步」（实际在 `tech-workbench-bottom` 的 `#techPrev` / `#techNext`）、「点「零件清单」」（实际右侧看板常驻）、「左侧上下文卡显示 page_context」（可见卡片已删，`page_context` 现在只是发给 Agent 的请求字段，见 `assembly-integration.js`）、「左侧「失败重试」」（该入口已下线，失败走普通会话输出 + 就近重试）。
- 待实现（DeepSeek）：① 在共享纯函数模块 `tech-stage-restore.js` 增加 `TechStageRestore.drawingParsed({ir, meta, stages})`（若第 3 项批次已建该文件，只新增导出、不重写 `fromSignals`），并让 `refreshProgress()` 的 drawing 打点、工作台与首页的「有 IR」判断都改用它；② 重写第 22 步验收清单，使 manual/expected 只描述当前交互。两份实现提示词只在会话交付，未落盘。
- 行为断言方式：node 真实执行 `drawingParsed` 跑 13 例矩阵（零零件 IR 有/无留痕、只有 `parsed`/`parsed_3d` 留痕、`ir_revision` 回退位、只有 `uploaded`、空输入、null 输入不得抛异常）；验收清单则用结构化断言（10 个场景与 id 必须保留、引用的 automated 文件必须存在、过期话术必须消失、当前话术必须出现），防止靠删场景变绿。
- 验证：两份红测分别 **27 处 / 15 处失败**（先红）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **1034 项 / 80 失败 / 8 跳过**，失败全部来自第 3、5、6 三批新红测（38 + 27 + 15）。
- 边界：本批只新增 Spec 与红测，未改任何业务实现与验收文档正文；未动工作区里并行批次文件；未提交、未推送。

## 63. 恢复项目时落到真实阶段（实现）（9-15）

- 需求：承接 ## 61，让「历史记录」与「首页项目卡片 / 清单」打开项目时按真实进度落到 1.1 / 1.2 / 1.3 / 2.1 / 2.2 / 2.3 / 3.1 / 3.2 / 3.3，不再「有 IR 就回 2.2」；判定只有一份实现，两个入口共用。
- Spec / 红测（本批未改）：`docs/specs/tech-history-restore-real-stage.md`、`tests/test_tech_history_restore_real_stage_red.py`（15 项方法 / 38 处失败），Red 基线全红。
- 实现（4 个文件）：
  - 新增 `tech_app/frontend/tech-stage-restore.js`：纯函数模块，导出 `window.TechStageRestore.fromSignals(flow, projectData, signals)`，`signals = { integration, cost_review, cost_detail }`。判定顺序固定九步：需求三态 → 报告三态（draft→summary / in_review→report-review / approved·published→report-publish）→「无需求且无 IR」→ 财务退回工艺经理（`actions[].kind === 'return-to-process'`）→ 2.3 已开始 → 有 IR → drawing。**`integration.cost.items`（2.2 自己算的整机成本）不算 2.3 已开始**；`return-to-process` 优先于 `cost_review.confirmed`。不碰 DOM / 不发请求 / 不读存储。
  - `tech_app/frontend/tech-workbench.js`：`techStageFromProject(flow, project, signals)` 只做委托（删掉自建 `hasIr ? 'process' : 'drawing'`）；`techHistoryRestore()` 由 2 个 GET 扩到 4 个（新增 `/summary`、`/cost-review`，后者失败按 `{}`），把 `steps.integration` / `steps.cost_review` / cost-review 原文交给共享函数。
  - `报价首页.html`：`techStageFromFlow(flow, projectData, signals)` 只做委托；`openTechProject()` 同样扩到 4 个 GET，删掉自建分支。
  - `tech_app/frontend/tech-workbench.html`：在 `tech-workbench.js` 之前引入 `tech-stage-restore.js?v=tsr1`，workbench 版本提到 `twb18`；`报价首页.html` 底部脚本前引入同一模块。
- 验证：本批红测 `tests.test_tech_history_restore_real_stage_red` **38 处失败 → 0（15/15 全绿）**；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 在本批范围为 **1008 项 / 0 失败 / 7 跳过**（工作区另有并行批次新加的两个未实现红测文件，其 40 处失败不属于本批）；`node --check tech-stage-restore.js`、`node --check tech-workbench.js`、`git diff --check` 全部通过。
- 边界：未改阶段白名单（`tech-board-bridge.js` / `tech-board-runtime.js`）、协议事件、各 stage 页面、`applyStage()` / `techWorkbenchUrl()` 用法与任务类型落点（`tech_new_product` → 1.1、`tech_cost` → `stage=cost`、`tech_cost_return` → `summary`）；未新增后端路由、未改任何后端文件；调用点未保留第二份判定。
- 状态：本批为本地修改，未提交、未推送（## 59 桥函数去重、## 62 2.3 → 3.1 成本汇总同样仍在工作区）。工作区另有并行批次的未实现红测（`test_tech_empty_ir_parse_completion_red`、`test_tech_e2e_acceptance_doc_current_red`），不属于本批。

## 64. 零零件 IR 的解析完成判定（实现）（9-15）

- 需求：承接上面那个重复编号的「零零件 IR 的解析完成判定…」批次，让「图纸解析完成」不再依赖零件数量：解析成功但确实是 0 个零件的 IR 也算完成。
- Spec / 红测（本批未改）：`docs/specs/tech-empty-ir-parse-completion.md`、`tests/test_tech_empty_ir_parse_completion_red.py`（12 项方法 / 27 处失败），Red 基线全红。
- 实现（3 个文件）：
  - `tech_app/frontend/tech-stage-restore.js`：新增纯函数 `TechStageRestore.drawingParsed(input)`（`input = { ir, meta, stages }`，三者均可缺、null 容错、绝不抛异常）。返回 true 当且仅当：① `ir` 是对象且至少一个自有键（**不要求 parts 非空**）；② `stages.parsed` / `stages.parsed_3d`（含 `meta.stages` 同名项）非空；③ `meta.ir_revision` / `meta.ir_input_revision` ≥ 1。`fromSignals()` 内部的「有 IR」判定改为委托它（保留既有 `has_ir` 信号，矩阵行为不变）；顺带把 `hasCostPart()` 的局部变量由 `parts` 改名为 `rows`，让文件里不再有零件数量判定。
  - `tech_app/frontend/tech-workbench.js`：`refreshProgress()` 的 `done.add('drawing')` 改由 `drawingParsed({ ir, meta, stages })` 决定（`meta` / `stages` 取自 `/workflow` 的 `project`，IR 取自 `/summary` 的 `steps.ir`）；`techStageFromProject()` 经 `fromSignals` 复用同一判定，调用点没有第二份实现。
  - `报价首页.html`：`techStageFromFlow()` 同样经共享判定，不再自比较零件数量。
- 验证：本批红测 `tests.test_tech_empty_ir_parse_completion_red` **27 处失败 → 0（12/12 全绿）**，13 例判定矩阵逐例符合；第 3 项批次红测 `tests.test_tech_history_restore_real_stage_red` 仍 **15/15**（阶段矩阵未回退）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **1034 项 / 0 失败 / 7 跳过**；`node --check tech-stage-restore.js`、`node --check tech-workbench.js`、`git diff --check` 通过。
- 边界：未改后端 / 路由 / `save_ir` 语义；未改阶段白名单、协议事件、`applyStage()` / `techWorkbenchUrl()` 用法；六个步骤条打点 (`done.add(...)`) 与其它阶段完成条件一字未动；`tech-workbench.js` / `报价首页.html` / `tech-stage-restore.js` 里 `parts.length` 类判定已归零。

## 65. 第 22 步端到端验收清单改写（9-15）

- 需求：把 `docs/specs/tech-agent-recovery-22-e2e-scenarios.json` 改写成与当前产品一致的第 22 步人工验收清单 —— 只改这一份文档，不动任何业务代码。
- Spec / 红测（本批未改）：`docs/specs/tech-e2e-acceptance-doc-refresh.md`、`tests/test_tech_e2e_acceptance_doc_current_red.py`（14 项 / 15 处失败），Red 基线全红。
- 实现（只改 1 个文件）：
  - 保留 `step: 22`、10 个场景与 id 顺序（`e2e-01` … `e2e-10`）、`title` / `depends_on`；`automated` 全部逐条核对为真实存在的测试文件，未删任何引用。
  - `note` 说明 automated 是离线可运行的 Red/守护套件、manual 是浏览器里人工确认的步骤。
  - `e2e-03`：上下步改述为**右侧底栏**的「上一步 / 下一步」，左侧仍验收附件入口与当前步骤执行按钮；`expected` 补「上下步走右侧底栏，左侧不再承载导航」。
  - `e2e-04`：零件清单改述为**右侧看板常驻**区块，点零件进看板内详情，返回只切右侧视图、不弹父层。
  - `e2e-09`：`page_context` 改述为**发给 Agent 的请求上下文**（九阶段各自独立），不再要求页面「显示」上下文。
  - `e2e-10`：失败路径改述为**普通会话输出**呈现真实错误 + 就近**重试**重发，不再提左侧常驻失败重试入口。
- 验证：本批红测 `tests.test_tech_e2e_acceptance_doc_current_red` **15 处失败 → 0（14/14 全绿）**；`python3 -m json.tool` 校验通过（合法 JSON，step=22，10 个 id 顺序不变，各字段齐全非空）；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` **1034 项 / 0 失败 / 7 跳过**。
- 边界：未删场景、未改 id、未合并场景、未降低 `expected` 强度；未改任何代码、测试或其它文档。

- 状态：## 64 与 ## 65 均为本地修改，未提交、未推送。

## 66. 本批（## 59 / ## 62 / ## 63 / ## 64 / ## 65）提交与双远端推送记录（9-15）

- 提交：`c938fdf`「成本回传去重、2.3→3.1 成本汇总、阶段恢复与解析完成判定；第 22 步验收清单改写」（21 files changed, 1986 insertions(+), 91 deletions(-)），一次纳入 ## 59–## 65 的全部实现、Spec、红测与 changelog。
- 推送（GitHub 成功）：`python3 scripts/push_remotes.py --check --only origin` → `python3 scripts/push_remotes.py --only origin`，`origin/20260909` 由 `4909256` fast-forward 到 `c938fdf`；回读 `git ls-remote origin refs/heads/20260909` = `c938fdf5960d10e0df936b54d0da93b369661e95`，与本地 HEAD 一致。
- GitLab 未推送：`gitlab.boulderaitech.com` 在当前网络 DNS 解析为 NXDOMAIN（`ssh: Could not resolve hostname gitlab.boulderaitech.com`），`push_remotes.py` 与 `git ls-remote gitlab` 均连不上，属外部网络阻塞、非仓库问题；待内网可达后用同一 HEAD 执行 `python3 scripts/push_remotes.py --only gitlab` 补推（不生成补偿 commit、不 force push、不改写历史）。
- 验证：全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1034 项 / 0 失败 / 7 跳过**；提交后 `git status` 干净。
- 边界：未创建 MR / tag / Release，未部署、未重启服务。

## 67. 看板动作静态 role 快照解析（1.2 / 1.3 / 3.2 主按钮被静默降级）Spec / Red（9-15）

- 需求（用户反馈）：1.2「✓ 通过确认」与 1.3「提交审核意见」在左侧统一操作栏里不是蓝色实心主按钮，而是白底描边的次要按钮。
- 实测根因：`tech_app/frontend/tech-board-runtime.js:114` 的 `entryState()` 只读 `getState()` 返回的 `role`，条目外层静态声明的 `role` 从不进入 `raw`，于是「外层写 `role: 'primary'`、`getState()` 只返回 `visible/enabled/busy`」的条目一律被降级成 `'aux'`；这与同一段代码 `:126`～`:129` 已声明的契约（三种元数据既可来自静态条目、也可由 `getState()` 动态返回）不一致。
- 影响面：九个阶段页面里条目外层静态 `role: 'primary'` 只有三处 —— 1.2 `confirmRequirement`、1.3 `submitRequirementReview`、3.2 `approveProcessReport`，三颗按钮在父壳（只渲染 `role === 'primary'` 的那一颗）里全部降级；其余动作的 `role` 都由 `getState()` 动态返回，不受影响。
- 新增 `docs/specs/tech-board-static-action-role-in-snapshot.md`：规定在运行时（唯一必需修改点）把静态 `role` / `order` / `hint` 作为基线，合并优先级为「静态元数据 < `entry.state` < `getState()` < `updateActionState()` 覆盖值」；`visible` / `enabled` / `busy` 不参与静态回退；禁止在各页 `getState()` 里再抄一份 `role`（同一颗按钮不得有两处事实来源）。并明确把「3.2 / 3.3 某些状态有可见动作但没有任何主按钮」划到下一步门禁批次，本批不新增按钮。
- 新增 `tests/test_tech_board_static_action_role_snapshot_red.py`：在 Node 的 `vm` 里加载**真实的** `tech-board-runtime.js`，逐场景注册后读取运行时真正发给父壳的快照（`snapshot()` / `action-state` 信封 `payload.actions` / `auditPrimary()`），覆盖静态 primary 生效、信封透传、唯一主按钮审计、无 `getState()` 的静态条目、`getState()` 动态覆盖、`updateActionState()` 覆盖、静态 aux 不误升、`getState()` 抛错保留静态元数据、`order` / `hint` 回退与动态优先，以及三处真实页面条目「静态 role 为唯一事实来源」与公开 API / 协议常量不缩水。
- Red 验证：`python3 -m unittest tests.test_tech_board_static_action_role_snapshot_red -v` → 13 项中 7 通过、**6 失败**；失败正是缺陷行为（静态 primary 被降级 aux、信封里也是 aux、`primary_count=0`、无 `getState()` 的静态 primary 也降级、`getState()` 抛错时静态元数据丢失、`order` / `hint` 静态回退缺失）。全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1047 项 / 6 失败 / 7 跳过**，失败全部来自本批红测；相关回归 `test_tech_global_single_primary_and_nonblocking_notices_red`、`test_tech_confirm_review_optional_note_red`、`test_tech_board_state_envelope_dynamic`、`test_tech_board_deferred_actions_red` 共 42 项全绿。
- 状态：本批只建立 Spec 与 Red 基线（另在 `/tmp` 副本上验证「静态元数据基线」这一处最小改动即可让 13 项全绿），未修改 `tech-board-runtime.js` 或任何业务实现；等待 DeepSeek 实现后复验。

## 68. 2.2 出口依赖分级与「带缺口继续」的缺口豁免（waiver）Spec / Red（9-15）

- 需求（用户反馈）：「参数推荐已确认 / 组装工艺已确认」不该当硬门禁，转发或进下一步时「仍要继续」就帮着确认；报价必填参数里电压电流重量型号这类很多填不出来，也不该一项不齐就卡死整条流程。
- 实测根因：2.2 出口存在自相矛盾的链路 —— 参数推荐页的「仍要继续」（`assembly-integration.js:717`）只调 `finalize(confirm=false)`，于是 `params_final` 被置回 false（`main.py:2480`）；随后发送财务的前端闸门（`assembly-integration.js:1050`）与后端闸门（`services/integration.py:853`）又把「报价必填缺口 / params_final / params_confirmed / process_confirmed」四项全部当硬门禁。前端那次「仍要继续」没有留下任何可读的豁免记录，后端也没有存档，于是同一个缺口被反复拦回来（前端说允许继续、后端说不许），用户只能逐页倒查。
- 新增 `docs/specs/tech-dependency-tiers-and-step-waivers.md`：把依赖统一分成 L1 生成依赖 / L2 质量依赖 / L3 交接依赖 / L4 合规依赖四级（页面永远可进入；生成动作只检查能否真的计算；内部阶段确认可带缺口放行；跨角色交接要显式签字与接收人；写库、回传报价、审核、发布、权限一律不可豁免；已签字的缺口不得再拦第二次），并给出本批落地范围：只做 2.2 → 2.3 出口与参数推荐确认，其余阶段后续批次按同一口径落地。契约包含 `IntegrationWaiver`（stage / missing_codes / missing_fields / waived_confirmations / reason / waived_by / waived_at / reused）、`record_waiver()` / `waiver_covers()` 唯一实现、`send_to_finance()` 的 L1 硬拦与 L2 放行、`status()` 与 `cost_review.payload()` 对下游暴露 `params_complete` / `waiver`、以及前端 `aiFinanceBlocker()` 只判 L1、新增 `aiFinanceGaps()` 描述 L2 缺口、签发复用既有 `aiAskProceed()` 与既有 finalize / send-to-finance 接口的可选 `waiver` 字段（不新增路由）。
- 新增 `tests/test_tech_integration_dependency_waiver_red.py`：后端行为用带 pydantic 的解释器（`open-claude/.venv/bin/python`）在子进程里真跑 `send_to_finance / record_waiver / status / cost_review.payload`（临时 DATA_DIR、假项目、打桩 cpq_bridge，不联网不碰真实数据），覆盖未签字仍拦、签字放行并落库（人 / 时间 / 审计 `integration_send_to_finance_waived`）、顺带补齐内部确认、同一缺口复用签字不再拦第二次（`reused=True`）、新缺口要重新签字、L1 不可豁免、下游可见；前端与路由做契约断言（`aiFinanceBlocker` 不再引用四项 L2 依赖、`aiFinanceGaps()` 存在、签发链路取消即停、签字随既有接口提交）。
- 被取代的过期断言：`tests/test_tech_integration_params_step_ownership_red.py::test_send_to_finance_requires_final_complete_parameters`（编码的正是本次要取消的硬门禁）改写为 `test_send_to_finance_grades_its_dependencies`，锁 L1 不可豁免 + L2 由 waiver 放行 + 未签字仍如实报错。
- Red 验证：`python3 -m unittest tests.test_tech_integration_dependency_waiver_red -v` → 20 项中 5 通过、**15 失败**；`tests.test_tech_integration_params_step_ownership_red` → 10 项中 1 失败（被取代的旧断言）。全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1067 项 / 22 失败 / 7 跳过**（22 = 本批 15 + 被取代断言 1 + 上一批 ## 67 的 6）。
- 状态：本批只建立 Spec 与 Red 基线；另在 `/tmp` 的一次性副本上按本 Spec 验证过「8 项后端行为红测可全部转绿」，仓库内 `tech_app/` 未做任何业务改动，等待 DeepSeek 实现后复验。

## 68 实现：2.2 出口依赖分级 + 「带缺口继续」的缺口豁免（9-15）

- 后端（`models/integration.py` / `services/integration.py` / `services/cost_review.py` / `main.py`）：`IntegrationWaiver` + `IntegrationPlan.waivers` 落库；`record_waiver()`（唯一实现，写入人手 / 时间，reason 为空补默认原因）、`waiver_covers()`（比对键 = 报价必填字段编码集合，签字后又冒出新缺口必须重签）、`waiver_summary()` / `pending_confirmations()` / `missing_required()` / `gap_message()` 收口判定；`send_to_finance(..., waiver=None)` 只保留 L1 硬拦（无参数推荐 / 无组装工艺），L2 缺口一次列全（报价必填中文名 + 未点确认的环节）后要么如实 `IntegrationFlowError`、要么凭签字放行并顺带补齐三项确认、写审计 `integration_send_to_finance_waived`，被 `stage='params'` 的签字覆盖同一批缺口时追加 `reused=True` 记录而不是要第二次签字。
- 路由（既有接口加可选字段、不新增路由）：`IntegrationFinalizeBody` / `IntegrationPublishBody` 各加 `waiver`；`finalize_integration_params` 在 `confirm=false` + waiver 时把 `stage='params'` 的签字落库（`params_final` 仍为 False）；`integration_send_to_finance` 透传 waiver，权限仍是 `auth.MANAGER_ROLES`。
- 下游可见性：`status()` 增 `params_complete` 与 `waiver`（既有字段一个不删）；`cost_review.payload()` 的顶层与 `review` 都带 `params_complete` / `waiver`，财务据此知道缺口是签过字的、不再按同一批缺口拦人。
- 前端（`assembly-integration.js`）：`aiFinanceBlocker()` 只判 L1（参数推荐 / 组装工艺跑过没有）；新增 `aiFinanceGaps()` 把四类 L2 缺口写成一句人话；`aiRenderOps()` 不再因 L2 缺口置灰（只保留 busy 并发保护）并把缺口摆在页面提示位；`aiConfirmProcessAndSendToFinance()` 用既有 `aiAskProceed` 取签字，取消即返回结构化失败（不发送不落库），继续则把 `{ reason }` 交给既有 `aiOpenFinanceDialog(waiver)` → `aiRunOp('send-to-finance', dispatch)`；参数推荐页的「仍要继续」改为 `aiParamsFinalize(false, waiver)`，签字随既有 `/integration/params/finalize` 落库。
- 验证：`tests.test_tech_integration_dependency_waiver_red` → **20/20 全绿**（Red 基线 15 失败归零）；`tests.test_tech_integration_params_step_ownership_red` 10/10、`tests.test_tech_integration_confirm_finance_flow_red` + `tests.test_tech_business_actions_clickable_then_error_red` + `tests.test_integration_params_tab_single_primary_and_auto_fill_red` + `tests.test_tech_integration_agent_red` + `tests.test_tech_cost_process_manager_send_to_finance_red` 共 81 项全绿；全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1098 项 / 39 失败 / 7 跳过**，39 项全部来自尚未实现的两个并行批次（## 67 静态 role 快照 6 项、会话时间线持久化 33 项），本批无遗留失败。手工链路自查（路由层直调）：未签字 confirm=true → 400 真实原因；confirm=false + waiver → `params_final=False` / `waiver.stage=params` / `waived_by=王五`；发送财务 → `reused=True`、`waived_confirmations` 三项齐、审计含 `integration_send_to_finance_waived`。
- 边界：未改 `quote_product_params.json` 的 16 项必填、未新增路由 / 第二套缺口实现、未放宽权限、未让 Agent 自动豁免、未改 `tests/` 与上一批 `tech-board-runtime.js` 的静态 role 逻辑；未提交、未推送、未部署。

## 69. 项目会话时间线：所有会话卡片统一持久化 + 按同一顺序拼接 Spec / Red（9-15）

- 需求（用户反馈）：重新进入项目后只剩 Agent 对话，看板按钮跑出来的过程卡与结果提示全都不见了；而且同一条会
  话线程里「有的卡片永远钉在最下面，有的按顺序从上到下」。要求所有卡片都持久化保存，并按同一顺序拼接。
- 实测四类内容走了四条不同的路，只有前两类会持久化：Agent 对话与工具轨迹经 `/agent/send` → OpenClaude
  JSONL → `/agent/history`（可恢复）；`tech_ui` 事件虽可能进 JSONL，但 `renderHistory()` 主动跳过
  （`agent-chat.js:254` `if (event.name === "tech_ui") return;`）；看板任务卡（`task-progress` /
  `task-completed` / `task-failed`）经桥消息进 `renderTaskProgress()`，只写 DOM；阶段页的 `crSay()` /
  `aiSay()` 只写各自 iframe 内的 `#crThread` / `#aiThread`，同样不落库。
- 顺序缺陷：`agent-chat.js` 的 `taskProgressHost()` 回落到 `#ocTaskProgressHost`
  （`tech-workbench.html:78`，位于 `#ocTinner` 末尾），任务卡永远排在所有消息之后（代码注释自己写着
  「建出来就永远钉在底部，聊多少轮都不动」）；Agent 消息却按时间追加，同一线程两套顺序规则。
- 新增 `docs/specs/tech-session-timeline-persistence-and-order.md`：后端新增项目级 append-only 时间线
  (`store.append_session_event()` / `load_session_events()`，`seq` 等于追加顺序、`key` 幂等且就地更新、
  同一 `task.id` 只留一张卡且进度行按行去重)；路由新增 `POST /agent/event` 与 `GET /agent/events`，并把
  `timeline` 扩展进既有 `GET /agent/history`（Agent 层不可用时**仍要返回本地条目**）；前端新增唯一顺序实现
  `tech-app/frontend/tech-session-timeline.js`（`normalize` / `merge` / `append` / `dedupe` /
  `applyTaskProgress` / `forShell` / `forStage`，按 `(ts, seq)` 稳定升序）；父壳 `taskProgressHost()`
  固定返回 `#ocTinner`、任务卡按顺序进线程并落库、回放 `timeline` 且不再跳过 `tech_ui`；阶段页文字改为
  「本地线程照旧可见 + 同一条内容以 `session-note` / `source:board` / `stage` 落库 + 加载后按本阶段回放」。
- 明确不做：**不删除** `#ocTaskProgressHost` 节点 —— 它已被 `test_tech_left_chat_controls_restore_red`、
  `test_tech_chat_card_noise_and_quiet_board_failures_red`、`test_tech_agent_history_project_rebind_red`、
  `test_tech_chat_drop_static_intro_bubble_red` 等防缩水守卫固定引用，本批只改「卡片插到哪里」，删节点属于扩大范围；
  也不改 OpenClaude JSONL 读写、`/agent/send` SSE 协议、业务数据存储与 `.oc-result-actions` 结果入口。
- 新增 `tests/test_tech_session_timeline_persistence_red.py`（31 项）：后端用带 fastapi/pydantic 的解释器在
  子进程里真跑 store 与 TestClient（含跨进程重启后仍在、同 key 幂等保留 seq、同 task 合并步骤、`?stage=`/`?source=`
  过滤、Agent 层不可用时 history 仍返回 timeline 且既有字段一个不少）；前端纯函数用 Node `vm` 加载真实模块驱动
  （交错排序 / seq 兜底 / 稳定排序 / forShell 只排除 board note / forStage 只回本阶段 / 同一张卡就地更新 / 去重取先
  位置后内容 / append 不改入参 / normalize 补齐 kind）；接线做源码契约断言（不再钉底、不再跳过 tech_ui、模块先于
  agent-chat.js 加载、阶段页落库与回放、原有本地线程出口不缩水、既有会话路由与 history 字段不缩水）。
- 被取代的过期断言：`tests/test_tech_agent_history_project_rebind_red.py::test_replay_path_and_event_types_unchanged`
  里原先要求 `renderHistory()` 出现 `event.name === "tech_ui"`（即回放必须跳过）—— 正是本批要取消的行为，改写为
  「四种事件类型仍在 + 不允许再跳过 tech_ui」。
- Red 验证：`python3 -m unittest tests.test_tech_session_timeline_persistence_red` → 31 项中 6 通过、
  **32 处失败**（后端能力/路由 20 处、纯函数 8 处、父壳接线 3 处、阶段页 9 处……含子测试）；被取代断言 1 处失败。
  全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1098 项 / 39 失败 / 7 跳过**（39 = 本批 32 +
  上一批 ## 67 的 6 + 被取代断言 1）。
- 可满足性：在 `/tmp` 一次性副本上按本 Spec 实现后，本批 31 项 **全绿**（仓库内 `tech_app/` 未做任何业务改动），
  证明红测可被一份直白实现满足、不是死断言。
- 状态：本批只建立 Spec 与 Red 基线（含 1 处被取代断言改写），等待 DeepSeek 实现后复验；未提交、未推送。

## 70. 技术工艺会话去掉红色报错卡片，失败信息按普通输出继续 Spec / Red（9-15）

- 需求（用户反馈）：「⚠ 回传销售经理继续报价失败，请查看看板提示。 这些报错的红色文字的卡片全都不要了」。
- 实测根因：`agent-chat.js:458` 的 `pushSystem()` 用 `el("div", "oc-err-line", `⚠ ${text}`)` 配一个「!」头像，
  `agent-chat.css:255` 的 `.oc-err-line { color: #dc2626 }` 把它染红；流式失败（`agent-chat.js:668` 的
  `event.type === "error"`）与 SSE 读取失败（`:720` 的 catch）也各自往同一条回复里插一行 `.oc-err-line`。
  `pushSystem` 有约 40 处调用（动作失败、看板未就绪、Agent 不可用、历史读取失败…）并经
  `window.ocTechAgent.notice` 暴露给父壳，所以**提示本身要保留**，改的只是呈现样式。
- 新增 `docs/specs/tech-chat-drop-red-error-cards.md`：`pushSystem` 改用与普通助手输出同款结构
  （`oc-amsg` + `oc-aav` ✦ + `oc-abody` + `oc-atxt`，不再是「!」+ 红字）；流式失败与连接失败把原因写进
  同一条回复正文并保留 `setAssistantState(ctx, "failed")`；`.oc-err-line` 从 JS 与 CSS 一起删除；
  **保留**助手失败状态 chip（`.oc-alabel-state.is-failed`，`⚠ 失败`）、工具结果错误边框（`.oc-tool-result.err`）、
  看板侧白色气泡里的 `⚠` 文本，以及 `boardFailureNotice` 对预期内失败码的静默与 `pushSystem` 的唯一出口地位。
- 新增 `tests/test_tech_chat_drop_red_error_cards_red.py`（15 项，按括号配平截取真实函数体断言，不做全文件 grep）：
  JS/CSS 都不再有 `oc-err-line`、pushSystem 走普通输出结构且用助手身份、不再拼 `⚠ ${`、仍写进线程并跟随滚动、
  `notice: pushSystem` 仍在、看板失败仍经唯一出口且预期内失败码仍静默、error 分支与 SSE catch 都写正文且置失败、
  状态位 `⚠ 失败` 与 `is-failed` 仍在、看板侧 `⚠` 文本未被一并删除、失败文本未被吞、`node --check` 通过。
- 被取代的断言：`tests/test_chat_errors_inflow_and_drop_refresh_task_cards_red.py::test_existing_error_channels_are_kept`
  原先要求 `oc-err-line` 同时留在 JS 与 CSS（正是红字卡片长期存在的依据），改写为「pushSystem 仍在 + 走普通输出
  排版 + 不再出现红字卡片」，保留其真实意图（错误仍在会话流内、不被静默吞掉）。
- Red 验证：`python3 -m unittest tests.test_tech_chat_drop_red_error_cards_red` → 15 项中 7 通过、**8 失败**；
  被取代断言 1 处失败。全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1113 项 / 48 失败 / 7 跳过**
  （48 = 本批 8 + 上一批 ## 69 的 32 + ## 67 的 6 + 两处被取代断言 2）。
- 可满足性：在 `/tmp` 一次性副本上按两份 Spec 实现后，本批 15 项与 ## 69 的 31 项连同两处改写断言
  **149 项全绿**（仓库内 `tech_app/` 仍未做任何业务改动），证明红测可被直白实现满足。
- 状态：本批只建立 Spec 与 Red 基线（含 1 处被取代断言改写），等待 DeepSeek 实现后复验；未提交、未推送。

## 69 / 70 实现：会话时间线统一持久化 + 会话里不再出现红色报错卡片（9-15）

- 后端（`storage/store.py` / `main.py`，不新增业务路由）：`append_session_event()` 按追加顺序分配 `seq`，
  带 `key` 幂等且**就地更新**（保留原 `seq` 与原位置），同一 `task.id` 只留一张卡（`task.steps` 按行去重追加、
  `status` / `error` 就地更新），无 `key` 一律追加新行；`load_session_events()` 按 `seq` 升序返回。
  新增 `POST /api/projects/{pid}/agent/event`（WRITE_ROLES，`kind` 必填 400，返回 `{seq, event}`）与
  只读 `GET .../agent/events?stage=&kinds=&source=`；`GET .../agent/history` 扩展 `timeline` 字段
  （`project_id` / `session_id` / `messages` / `message_count` 一个不删，Agent 层不可用的分支同样返回本地条目）。
- 前端唯一顺序实现 `tech_app/frontend/tech-session-timeline.js`（`normalize` / `merge` / `append` /
  `dedupe` / `applyTaskProgress` / `forShell` / `forStage` / `mergeTask`，按 `(ts, seq)` 稳定升序、同 `key` /
  同 `task.id` 就地更新）。父壳 `taskProgressHost()` 固定返回 `#ocTinner`（任务卡不再钉在会话底部），
  `ensureTaskCard()` 只剩「和普通消息同款包裹」这一条路径；`#ocTaskProgressHost` 节点与 `setProject()` 的
  `replaceChildren()` 保留不动。`loadHistory()` 把 `data.timeline` 一并交给 `renderHistory()`，回放改走
  `forShell(...)` 并且**不再跳过 `tech_ui`**；`detached` 分支不再清空已落库历史与任务卡映射（重新挂上时
  不会再画一张同样的卡）。
- 落库口径：父壳新增 `persistSessionEvent()`（`POST /agent/event`，失败只留痕不阻塞渲染），任务卡按
  `key: task:<taskId>` 只提交新出现的进度行、进度文字按 `kind: session-note` / `source: shell` 落库，
  回放期间置位 `replayingHistory` 不重复写回（否则每打开一次项目就会重复追加一遍）。
- 阶段页（`cost-review.js` / `assembly-integration.js` / `app.js`）：`crSay` / `aiSay` / `aiUserSay`
  在写本地线程之后把同一条内容以 `kind: session-note` / `source: board` / `stage` 落库（幂等 key），
  加载完成后用 `GET /agent/events?stage=…&source=board` + `forStage(...)` 回放进 `#crTinner` / `#aiTinner`；
  2.1（`app.js`）在 `forwardTaskDetail()` 里按 `key: task:<taskId>` 落库任务卡（只提交新进度行），
  `openProject()` 之后回放本阶段条目交给会话宿主按同一顺序渲染。三个阶段页 HTML 各补一行
  `tech-session-timeline.js`（`index.html` / `cost-review.html` / `assembly-integration.html`），
  `tech-workbench.html` 里该模块排在 `agent-chat.js` 之前。
- 去掉红色报错卡片（本批 ## 70）：`pushSystem()` 改用与普通助手输出同款结构（`oc-amsg` + `oc-aav` ✦ +
  `oc-abody` + `oc-atxt`）并把提示照旧写进线程、跟随滚动；流式失败与 SSE 读取失败把真实原因并进同一条回复
  正文（`ctx.full` 重渲染），状态位仍是 `setAssistantState(ctx, "failed")`；`.oc-err-line` 从 JS 与 CSS 一起
  删除。保留：失败状态 chip（`⚠ 失败` / `.is-failed`）、工具结果错误边框（`.oc-tool-result.err`）、看板侧
  白色气泡里的 `⚠` 文本、`boardFailureNotice` 对预期内失败码的静默与 `pushSystem` 的唯一出口地位。
- 缓存版本号同步：`agent-chat.js?v=20260915-timeline1`（两个页面一致）、
  `agent-chat.css?v=20260915-nored1`（四个页面一致）、`tech-session-timeline.js?v=tst1`。
- 被取代的过期断言：`tests/test_tech_quote_agent_parity_matrix_red.py` 的对照表
  `docs/specs/tech-agent-recovery-21-quote-parity.json` 里 `error_prompt` 的 tech 锚点原指向
  `agent-chat.js#oc-err-line`（本批已按 Spec 退役），改为 `agent-chat.js#pushSystem` 并在 `release`
  注明「契约更新（去掉红色报错卡片批次）」；语义（错误提示仍在会话流内）不变。
- 验证：`tests.test_tech_session_timeline_persistence_red` → **31/31 全绿**（Red 基线 32 处失败归零）；
  `tests.test_tech_chat_drop_red_error_cards_red` → **15/15 全绿**（基线 8 失败归零）；
  回归 `test_tech_agent_history_project_rebind_red` + `test_tech_board_state_envelope_dynamic` +
  `test_tech_left_chat_controls_restore_red` + `test_chat_collapsible_thinking_trace_red` 共 47 项全绿，
  `test_chat_errors_inflow_and_drop_refresh_task_cards_red` + `test_chat_fused_assistant_card_style_red` +
  `test_tech_chat_card_noise_and_quiet_board_failures_red` +
  `test_tech_business_actions_clickable_then_error_red` 共 65 项全绿。
  全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1113 项 / 6 失败 / 7 跳过**，
  6 项全部来自尚未实现的并行批次 ## 67（看板动作静态 role 快照，只跑 `tech-board-runtime.js`，本批未改该文件），
  本批无遗留失败。语法与空白检查：`node --check`（`tech-session-timeline.js` / `agent-chat.js` /
  `cost-review.js` / `assembly-integration.js` / `app.js`）全部通过，`git diff --check` 无输出。
- 边界：未删 `#ocTaskProgressHost` 节点与 `.oc-result-actions` 结果入口、未改 `/agent/send` 的 SSE 协议 /
  `/agent/new` / 业务数据存储、未新增业务路由、未改上一批 ## 67 的静态 role 逻辑；
  **未提交、未推送、未部署**。

## 67 实现：看板动作静态元数据基线（1.2 / 1.3 / 3.2 主按钮恢复蓝色实心）（9-15）

- 唯一必需修改点：`tech_app/frontend/tech-board-runtime.js` 的 `entryState()` 加入静态元数据基线
  `{ role, order, hint }`（取自条目外层声明，缺失即缺省），合并优先级保持「静态元数据 < `entry.state` <
  `getState()` 返回值 < `updateActionState()` 覆盖值」；`visible` / `enabled` / `busy` / `active` /
  `analyzed` 仍只由运行时状态决定、不参与静态回退；`getState()` 抛错时保留基线（与既有 `label` 回退语义一致），
  照旧不向上抛。三处真实页面条目（`confirmRequirement` / `submitRequirementReview` / `approveProcessReport`）
  的**外层静态 `role: 'primary'` 原样保留**，`getState()` 里没有补写第二处 `role`，也没有改动作名、`run`、
  `silent` / `deferred` 标记、协议常量（`NAMESPACE` / `VERSION` / 事件信封）与父壳的主按钮判定。
- 效果：父壳只渲染 `role === 'primary'` 的那一颗按钮，快照与 `action-state` 信封（`payload.actions[name].role`）
  现在都是 `primary`，1.2「✓ 通过确认」、1.3「提交审核意见」、3.2「审核通过并进入下一步」恢复蓝色实心主按钮；
  `primaryAudit()` 在这三处回到 `primary_count === 1` / `ok === true`，`primaryDiagnostics()` 不再误报。
  2.2 / 2.3 / 3.1 / 3.3 由 `getState()` 动态返回 `role` 的主操作反转不受影响。
- 验证：`tests.test_tech_board_static_action_role_snapshot_red` → **13/13 全绿**（Red 基线 6 失败归零）；
  回归 `test_tech_global_single_primary_and_nonblocking_notices_red` +
  `test_tech_confirm_review_optional_note_red` + `test_tech_board_state_envelope_dynamic` +
  `test_tech_board_deferred_actions_red` 共 42 项全绿；`node --check tech-board-runtime.js` 通过。
  全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1113 项 / 0 失败 / 7 跳过**
  （此前遗留的 6 项静态 role 失败清零，工作区全绿）。
- 边界：未新增按钮、未改文案、未放宽 `primaryAudit` 的唯一主按钮约束；「3.2 / 3.3 某些状态有可见动作但没有任何
  主按钮」仍按 Spec 留给下一步门禁批次（本批只用 `primaryDiagnostics()` 给出确定性诊断）；
  **未提交、未推送、未部署**。

## 67–70 提交与双远端推送记录（9-15）

- 提交：`4f42e6a`「看板静态 role 快照、2.2 缺口豁免、会话时间线持久化、会话去红字卡片」，
  29 个文件（+3415 / −113），含 ## 67 / ## 68 / ## 69 / ## 70 四批的 Spec、红测、实现、契约更新与
  changelog；提交前 `git diff --cached --check` 无输出、全量 `python3 -m unittest discover -s tests -p 'test_*.py'`
  → 1113 项 / 0 失败 / 7 跳过。
- 推送：`python3 scripts/push_remotes.py --check --only origin` 预检通过（origin 当时为 `d888213`，是
  HEAD 的祖先、快进关系成立）→ `python3 scripts/push_remotes.py --only origin` 推送并回读成功，
  `origin/20260909` = `4f42e6a`（GitHub 已推）。
- GitLab 已补推：本机 DNS 仍解析不到 `gitlab.boulderaitech.com`，但内网可达（34 服务器解析到
  `172.16.5.150`，本机 `nc 172.16.5.150 22` 成功且 `known_hosts` 已有该 IP 的同一把 ed25519 主机公钥）。
  用临时 ssh 配置把主机名映射到该 IP（`Host gitlab.boulderaitech.com / HostName 172.16.5.150 / User git`，
  不改 `/etc/hosts`、不改仓库远端地址），`GIT_SSH_COMMAND="ssh -F /tmp/gl.conf" python3 scripts/push_remotes.py
  --only gitlab` 推送并回读成功；两个远端 `20260909` 现在都指向 `3517bb8`（`git ls-remote` 已分别回读确认）。
- 部署到 172.16.10.34：**未执行，当前身份无权限**。服务器上服务由 `wugefei` 账号运行
  （`/home/wugefei/CPQ/cpq_agent` 的 8010 `cpq_suite_server.py` PID 1302252 与 8012 `tech_app_launch.py`
  PID 1302344，部署目录 HEAD 仍是 `e541fdf`，落后本批 6 个提交），而本机 ssh 只有 `zhangzhen`
  身份（`sudo` 需密码、无 `wugefei` 私钥），实测该目录对 `zhangzhen` 不可写。
  具备 `wugefei` 权限时按既有顺序部署（该目录的 `gitlab` 远端是内网 GitLab、`origin` 是 GitHub，
  部署仍只从 GitLab 取）：
  `cd /home/wugefei/CPQ/cpq_agent && git fetch gitlab 20260909 && git checkout 20260909 &&
  git merge --ff-only FETCH_HEAD`（`e541fdf → eb35498` 是本批的纯快进：`e541fdf` 已是 HEAD 祖先）；
  再先停 8012 子进程、后停 8010 主进程、等端口释放后重启 8010，最后用 `/` 与 `/api/health`
  （`status=ok`）核验。

## 71. 需求阶段（1.1 → 1.2 → 1.3）依赖分级与「带缺口继续」的缺口记录（9-15）

- 需求（用户反馈）：全流程依赖分级里，除了权限这类强制项，别的一律不要硬阻断；没填好 / 没完成的
  地方要「提示现在缺什么，确定要继续吗」，点继续就带着缺口往下走。2.2 → 2.3 出口已在 `## 68` 落地，
  本轮把统一口径铺到需求三段 —— 之前 1.1 是**真硬闸门**（星号字段没填全直接 `rcToast + return`，
  什么都不存、什么都不发），1.2 / 1.3 则是缺口既不拦也不留痕。
- Spec：新增 `docs/specs/tech-requirement-stage-dependency-tiers.md`（沿用 `tech-dependency-tiers-and-step-waivers.md`
  的 L1–L4 分级：保存前置 / 状态机 / 人工点击 / 角色权限不可豁免，星号字段与完整性检查缺口属 L2，
  允许「仍要继续」但必须落库）。
- 红测：新增 `tests/test_tech_requirement_stage_waiver_red.py`（24 项，基线 **21 失败 / 3 通过**）。
  后端行为用带 pydantic 的解释器（`open-claude/.venv/bin/python`）在子进程里真跑 `requirement_service`
  的缺口计算、签字与三个流转函数（临时 `DATA_DIR`、假项目），前端做源码契约断言。
- 后端模型 `tech_app/backend/models/workflow.py`：新增 `RequirementWaiver`（`stage` / `missing_keys` /
  `missing_fields` / `reason` / `waived_by` / `waived_at` / `reused`），`RequirementDoc` 增加
  `waivers: List[RequirementWaiver]`；`WorkflowAction` 增加可选 `waiver: Optional[dict]`。
- 后端服务 `tech_app/backend/services/requirement_service.py`（唯一实现）：
  - 新增 `field_label()`（Section C 走 `industry_templates.all_labels()`，其余固定字段本地补齐中文名）、
    `requirement_gaps()`（直接复用 `requirement_precheck()` 的 `need_info` 汇总成 `keys` / `labels`，
    不另写完整性算法）、`record_requirement_waiver()`（写签字人 / 时间，原因留空时补默认原因）、
    `requirement_waiver_covers()`（同一批缺口签过字就命中，出现新缺口返回 `None`）。
  - `requirement_precheck()` 的 `need_info` 条目新增 `missing` 字段，返回值**只增不改**地新增
    `gaps: {keys, labels, count}`；`items` / `ok` / `generated_note` / `engine` 原样保留。
  - `submit_requirement_confirmation` / `confirm_requirement` / `review_requirement` 各加可选
    `waiver` 形参（默认 `None`）：带签字时按服务端算出的缺口落库并发 `workflow:requirement_*_waived`
    审计，1.2 / 1.3 命中既有签字改为追加 `reused=True` 记录；**不传签字时三个流转行为与今天完全一致**
    （不新增任何 409 硬门禁）。
  - `save_requirement_draft()` 像 `history` 一样从旧文档继承 `waivers`，前端整份表单 PUT 不会抹掉签字。
- 后端路由 `tech_app/backend/main.py`：三条既有路由把 `body.waiver` 透传给 service；
  `_require(MANAGER_ROLES / DIRECTOR_ROLES)` 角色校验与路由集合**一字未动**（红测锁了 12 条需求相关路由）。
- 前端 `tech_app/frontend/requirement-create.js`（1.1）：星号字段没填全不再直接 `return`，改为弹
  「仍要继续」（列出缺口项）——取消仍 `rcFocusFirstRequiredField()` 定位首个缺口、不保存不发送；
  继续则把 `{reason, missing_fields}` 作为 `waiver` 随既有 `/requirement/submit-confirmation` 提交。
- 前端 `tech_app/frontend/requirement-confirm-page.js`（1.2）：新增 `cfGaps()`（只读后端预检的
  `gaps`，缺失时回落 `need_info` 条目名），`cfAct('confirm')` 有缺口时先问「仍要继续」，点继续把
  `waiver` 放进既有 `/requirement/confirm`；点取消返回结构化失败、不发送。退回草稿不弹、状态机前置保留。
- 前端 `tech_app/frontend/requirement-review-page.js`（1.3）：`rrStart()` 并发读一次既有
  `/requirement/precheck` 存进 `rrGaps`；`rrSubmit()` 在「审核通过 + 有缺口」时先问「仍要继续」，
  点继续把 `waiver` 放进既有 `/requirement/review`。驳回不弹，审核结论与权限不动。
- 验证（实际运行）：
  - 本批红测 `python3 -m unittest tests.test_tech_requirement_stage_waiver_red -v` → **24/24 通过**
    （基线 21 失败）。
  - 回归 `tests.test_tech_confirm_review_optional_note_red + test_tech_requirement_confirm_red +
    test_tech_requirement_review_red + test_tech_requirement_agent_red +
    test_tech_integration_dependency_waiver_red + test_tech_integration_params_step_ownership_red`
    → **77/77 通过**。
  - 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` → **1137 项 / 0 失败 / 7 跳过**；
    venv 解释器（`open-claude/.venv/bin/python`）→ **1139 项 / 0 失败**。
  - `node --check` 覆盖 `requirement-create.js` / `requirement-confirm-page.js` /
    `requirement-review-page.js` 全部通过；`git diff --check` 无输出。
- 边界：未新增 / 删除任何路由与 Agent 工具，未改 `_require` 角色、状态机前置、人工审批点击与
  `requirement_precheck()` 的完整性算法；缺口一律来自既有预检，签字一律走既有三条流转路由；
  2.1 / 2.3 / 3.1–3.3 的依赖分级仍留给后续批次。**未提交、未推送、未部署**。

## 72. 本批（## 71）提交、双远端推送与 34 部署记录（9-15）

- 提交：`92f9d30`「需求阶段依赖分级与「带缺口继续」的缺口记录（1.1 / 1.2 / 1.3）」，
  9 个文件（+907 / −17），含 Spec、红测、后端模型 / 服务 / 路由、三个前端页面与 changelog；
  提交前 `git diff --cached --check` 无输出，全量 `python3 -m unittest discover -s tests -p 'test_*.py'`
  → 1137 项 / 0 失败 / 7 跳过。
- 推送：`python3 scripts/push_remotes.py --only origin` → `origin/20260909` = `92f9d30`（GitHub 已推）；
  GitLab 仍按上一批的临时 ssh 映射推送（`Host gitlab.boulderaitech.com / HostName 172.16.5.150 / User git`，
  不改 `/etc/hosts`、不改仓库远端地址）：`GIT_SSH_COMMAND="ssh -F /tmp/gl.conf" python3 scripts/push_remotes.py
  --only gitlab` → `gitlab/20260909` = `92f9d30`。两个远端均已用 `git ls-remote` 回读确认。
- 部署到 172.16.10.34：**未执行，当前身份无权限**（与上一批相同，本批再次逐项复核）：
  - 服务由 `wugefei` 账号运行：8010 `cpq_suite_server.py` PID 1302252、8012 `tech_app_launch.py`
    PID 1302344；部署目录 `/home/wugefei/CPQ/cpq_agent`（`drwxr-xr-x wugefei ai`）对 `zhangzhen`
    **不可写**（实测 `touch` 报「权限不够」），目录 HEAD 仍是 `e541fdf`。
  - 本机 ssh 只有 `zhangzhen` 身份（`~/.ssh/config` 把 172.16.10.34 固定为 `zhangzhen`）；`sudo -n`
    仍需密码；本机三个私钥（`cad_engine_deploy` / `agent` / `id_ed25519`）以 `wugefei` 登录均
    `Permission denied (publickey,password)`，没有 `wugefei` 凭据。
  - `DEPLOYMENT.md` 的默认部署根 `/home/data/zhangzhen_home/zhangzhen/cpq_agent` **不存在**
    （`deploy_server.sh` 明确不自动创建目录），且部署要求保留的 `cpq_settings.json` 在 34 上权限为
    `0600 wugefei`（`zhangzhen` 连读都不行），无法按文档路径重建一套 docker 栈；现网也不是 docker
    （`docker ps` 里没有 cpq 容器，8010 是裸进程），贸然 `docker compose up` 只会抢 8010 端口。
  - `.gitlab-ci.yml` 只有 `test`（全量单测 + `py_compile`）与 `build`（`docker build`，仅默认分支）
    两个 stage，**没有部署 job**，不存在可代跑的 CI 部署链路。
  - 具备 `wugefei` 权限时的部署顺序（该目录 `gitlab` 是内网 GitLab、`origin` 是 GitHub，部署只从
    GitLab 取）：`cd /home/wugefei/CPQ/cpq_agent && git -c safe.directory=$PWD fetch gitlab 20260909 &&
    git -c safe.directory=$PWD checkout 20260909 && git -c safe.directory=$PWD merge --ff-only FETCH_HEAD`
    （`e541fdf → 92f9d30` 是纯快进）；随后先停 8012 子进程、再停 8010 主进程，等端口释放后重启 8010，
    最后用 `/` 与 `/api/health`（`status=ok`）核验。

## 73. 本批（## 71）部署到 172.16.10.34 成功（9-15）

- 授权与方式：用户提供 `wugefei` 账号凭据后执行部署。本机无 `sshpass`，用系统自带 `/usr/bin/expect`
  写了一个只做密码登录的包装脚本（临时文件，未改服务器配置、未写 known_hosts 之外的任何东西）。
- 部署前状态（已记录）：`/home/wugefei/CPQ/cpq_agent` 在 `20260909`、HEAD `e541fdf`；
  8010 `cpq_suite_server.py` PID 1302252（父）、8012 `tech_app_launch.py` PID 1302344（子，父进程拉起）；
  `/` 与 `/api/health` 均正常。该目录 `gitlab` 远端是 `http://gitlab.boulderaitech.com/ai-team/cpq_agent.git`，
  拉取不需要 SSH key。
- 取代码：`git -c safe.directory=$PWD fetch --prune gitlab 20260909` → `e541fdf..63d79f6`，
  `git merge --ff-only FETCH_HEAD` 纯快进成功；工作区里 `.dockerignore.bk` / `jdk.tar` / `nohup.out`
  等未跟踪文件未被触碰。
- 重启：先停 8012 子进程、再停 8010 父进程，轮询到 8010 / 8012 端口全部释放、无 cpq 进程后，
  在部署目录用原命令**原样重启**（旧日志先备份为 `nohup.out.prev.<时间戳>`）：
  `setsid nohup ./open-claude/.venv/bin/python cpq_suite_server.py --host 0.0.0.0 --port 8010 > nohup.out 2>&1 < /dev/null &`
  （`setsid` + 重定向确保进程脱离本次 SSH 会话存活）。父进程会自动拉起 8012 子服务。
- 部署后校验（全部通过）：
  - 新进程：8010 PID **165755**（`0.0.0.0:8010`）、8012 PID **165866**（`127.0.0.1:8012`，父进程为 165755）；
  - `http://127.0.0.1:8010/` → `200`；`http://127.0.0.1:8010/api/health` → `200` 且
    `{"status":"ok",...}`（**1 秒内就绪**）；
  - 部署目录 HEAD = **`63d79f6`**，且部署树里已含本批代码（`def requirement_gaps`、`RequirementWaiver`、
    前端 `waiver`）；
  - 服务器上的 `cpq_settings.json`（0600）、`cpq_history/`、`tech_app/tech_data/`、`product_images/`
    等运行数据未改动；未新增第二套服务、未抢端口、未动反代配置。
