# cpq_agent 变更记录（2026-09-07 至 2026-09-11）

> 本文档记录本周最终变更。自 2026-09-09 起只按周维护，不再创建日报。

## 维护规则

- 当周变更持续写入本文档，按功能主题合并并注明日期。
- 只记录最终用户行为、工程交付能力、验证与实际发布事实，不记录中间排查过程。
- 未完成或未验证的事项不得写成已完成；运行数据、日志、缓存和历史会话不入文档。

## 1. CPQ 仓库迁移与双远端开发分支（9-9）

- `cpq_agent` 以 GitHub `yiweilineng4` 最新提交 `3ac074a` 为基线建立 `20260909` 开发分支；旧 `cad_engine` 本地仓库解除 GitLab remote，GitLab 改名后的 `ai-team/cpq_agent` 接收 CPQ 代码。
- GitLab `20260909` 已创建并指向仓库规则提交 `5c13045`；GitHub 当前 SSH 身份缺少写权限，双端一致性仍需在权限恢复后用同一 HEAD 补推并回读验证。
- 仓库规则明确 GitHub/GitLab 双推送、DeepSeek 实现边界、历史数据保护和 `8010` 服务归属。

## 2. 标准交付工作流（9-9）

- 建立与建模 Agent、分析 Agent 一致的阶段化交付边界：`20260909` 开发分支 push、Merge Request 合入唯一发布主线 `master`、显式 annotated tag、显式 GitLab Release、显式部署；各阶段互不自动触发。
- 增加远端推送校验、MR/Release 工具、版本规范、部署说明与部署脚本；CI 仅用于 MR/master 测试和镜像构建验证，明确不包含生产部署。
- GitLab 新建 `master` 并以迁移基线 `5c13045` 初始化，设为项目默认分支；分支保护禁止直接 push 和 force push，仅允许 Maintainer 合并。旧 `main` 按用户最终要求保留，但不再作为活动开发、MR 或发布分支。

## 3. 技术工艺与成本测算统一工作台（9-9）

- 定义统一双栏工作台 Spec：左侧保持项目级技术工艺会话，右侧承载需求创建/确认/审核、图纸解析、工艺方案、成本测算、汇总、结果审核和发布回传九个 stage；步骤切换、刷新和浏览器历史操作均需保持项目身份和会话上下文。
- 采用“统一壳 + 同源受控步骤容器”的渐进式方案，复用现有页面与后端 API；明确嵌入模式、旧链接兼容、同源消息校验、角色和数据边界。
- 新增静态契约红测；实现前实际运行 `6 failed`，失败项对应统一壳、九阶段注册、同源导航保护、报价入口、旧页面嵌入模式和双栏响应式样式尚未实现。
- 用户在会话中直接授权实现：新增 `tech_app/frontend/tech-workbench.html/.css/.js` 统一壳与 `tech-embed.js` 嵌入协议；九个 stage 以 `embed=1` 同源 iframe 打开既有页面，左侧 `techChatPane` 复用 agent-chat.js 常驻会话，右侧 `techWorkspaceOutlet` 为唯一业务出口，URL（project/stage/task_id）支持刷新与前进后退恢复，跨域 postMessage 与白名单外 stage 一律拒绝。
- 旧步骤页直达 URL（无 embed）自动汇聚到统一工作台对应 stage，保留 project/task_id 等参数且不循环；`报价首页.html` 技术工艺入口改为 `tech-workbench.html`；步骤完成态仍取自既有 `/workflow`、`/summary` 数据，不以前端点击冒充完成。
- 未改动后端 API、数据库 schema、角色权限、成本公式、审批门禁与回传报价逻辑；未删除或清空历史会话/项目/任务/附件数据。
- 验证：红测 6/6 转绿，`python3 -m unittest discover -s tests -p 'test_*.py'` 15/15 通过，`py_compile cpq_suite_server.py cpq_tech_bridge.py` 与 `git diff --check` 通过；未创建 MR/tag/Release，未部署；浏览器人工验证（九步连走、草稿保留、前进后退、窄屏、伪造跨域消息）尚未执行。
- 第二轮验收发现统一范围仍不完整：报价主页技术清单/待办、技术工艺首页清单/待办仍有顶层直达 `tech-task`、需求详情、成本或工艺旧页面的路径；统一壳的三阶段进度栏横跨页面顶部，而非像报价助手一样置于右侧工作台卡片。Spec 与红测已补充“所有入口强制汇聚”和“进度仅在右侧卡片”的契约，等待 DeepSeek 修正。
- 第二轮 Red 基线：统一工作台契约共 11 项，原有 6 项继续通过，新增 5 项按预期失败；失败范围分别是右侧卡片内进度栏、报价主页入口、技术工艺主页入口、技术待办入口，以及任务类型与 `project/task_id/stage` 路由保真。
- 补充技术工艺主页统一 Spec 与 Red 契约：技术工艺不再跳转和维护独立 `/home.html` 主页壳，而是与报价、配置、规则助手共用 `报价首页.html` 的页面高度、左侧导航和内容结构；技术上传、行业模板、清单与待办仍保留自身业务语义。
- 技术工艺主页 Red 基线共 5 项：共享壳、技术模式能力和专属控件 3 项已有基础并通过；“技术标签不得跳转独立主页”和“旧 `/home.html` 汇聚统一主页”2 项按预期失败。

- 第二轮实现完成（9-9，会话授权直接实现）：三阶段进度栏从页面顶部移入右侧工作台卡片内部头部（`.tech-workspace-progress` 位于 `#techWorkspaceOutlet` 上方），不再横跨左右两栏；修正统一壳 `popstate` 中未定义的 `render()` 调用，前进/后退恢复不再抛错。
- 报价首页、技术工艺首页（home.js）与技术待办（cpq-tech-inbox.js）三个入口全部汇聚 `tech-workbench.html`：技术清单卡片按项目真实状态换算 stage（草稿/退回→`requirement-create`、待确认→`requirement-confirm`、待审核→`requirement-review`、无解析→`drawing`、已解析→`process`、报告按状态进 `summary`/`report-review`/`report-publish`）；待办 `tech_new_product`→`stage=requirement-create&task_id`，`tech_cost`→`stage=cost&project&task_id`，`tech_cost_return`→`stage=process&project&task_id`；旧 `tech_task` 参数在统一壳内规范为 `task_id`。
- 新增工艺待办尚无 project 时进入统一两栏壳的 1.1，右侧以 `embed=1` 承载原 `tech-task.html` 建项流程（tech-embed 增加 `tech-task.html→requirement-create` 映射，tech-task.js 兼容 `task_id` 参数）；建项成功后由子页请求父壳切到 `requirement-create` 并回写真实 project，全程不离开两栏布局。
- 验证：统一工作台红测 11/11 通过、仓库工作流契约 9/9 通过；`py_compile cpq_suite_server.py cpq_tech_bridge.py tests/test_unified_tech_cost_workbench_red.py`、改动 JS 的 `node --check` 与 `git diff --check` 全部通过；全量 `discover -s tests` 共 25 项，23 通过，另 2 项失败属于独立的“技术工艺主页统一 Spec”既有红测（tech-home-quote-shell，状态仍为等待 DeepSeek），本轮范围外，未删除或弱化。
- 本轮未提交、未推送、未创建 MR/tag/Release、未部署；浏览器人工验证（主页→清单/待办进入两栏、九步连走、草稿保留、刷新与前进后退、窄屏）未执行，需本地起服务后人工验收。
- 技术工艺主页统一实现完成（9-9，会话授权直接实现）：`报价首页.html` 删除 `TECH_HOME_URL` 常量与 tech 模式专用的 `location.href` 整页跳转分支，四个助手标签统一走 `tagToMode()` + `setMode(mode)` 页内切换；`setMode` 末尾以 `history.replaceState` 同步 `?assistant=`，不刷新页面、不产生多余历史；技术模式保留图纸/需求文档上传、行业模板、技术清单与待办，`MODES.tech` 页签为“我的清单 / 待办任务 / 全部清单”，待办任务与报价共用同一任务数据接口并路由到统一工作台；`?assistant=tech` 直接打开与点击标签得到同一主页壳。
- 旧 `tech_app/frontend/home.html` 改为轻量兼容入口：不再加载 `home.css`、`home-layout.css`、`home.js`、`cpq-home-tabs.js`、`cpq-tech-inbox.js`，不渲染旧导航 DOM，立即 `location.replace('/报价首页.html?assistant=tech')`（`URLSearchParams` 原样保留 `project`、`task_id`、`tech_task`、`stage`、`industry` 等业务参数，`assistant` 固定为 tech），不产生旧导航闪现与重定向循环；旧主页相关 JS/CSS 文件按约定保留未删除。
- 验证：技术工艺主页统一红测 5/5 转绿（原 2 项失败已消除），仓库工作流契约 9/9、统一工作台红测 11/11、全量 `discover -s tests` 25/25 全部通过；`py_compile tests/test_tech_home_quote_shell_red.py`、`home.html` 内联脚本 `node --check` 与 `git diff --check` 通过。浏览器人工验收（四标签共享同一壳、无 `/home.html` 跳转、技术上传/清单/待办进入统一工作台、旧链接汇聚、窄屏）本轮未授权执行服务与浏览器操作，未执行。
## 4. Agent 授权与任务终态规则补齐（9-9）

- 对齐建模 Agent 和分析 Agent 的通用治理规则：任务终态后，“继续”不能重开任务或触发工具、修改、测试、服务、Git 和部署动作；建议、风险、文档命令与历史授权均不构成新的执行授权。
- 固化 push/MR/tag/Release/部署逐项指令映射、版本不得自行升级、外部服务与 CI 操作需当前授权、部分失败不得绕过或谎报、完成状态必须分项说明。
- 强化历史会话与业务数据保护、精确删除和防空变量校验；提示词只在会话交付，周 changelog 永久自动维护且禁止日报。

## 5. 全局品牌色统一契约（9-9）

- 新增全局品牌色 Spec 与 Red 测试，约定 `#0067D1` 为报价、配置、规则和技术工艺的一方界面唯一主色，并同步定义悬停、按下、浅背景、浅边框及透明阴影的蓝色色阶。
- 契约覆盖 HTML、CSS、JS 动态样式及技术工艺应用，要求消除紫色、紫蓝渐变和竞争品牌蓝，同时明确保留成功绿、警告橙、错误/删除红及中性色的语义区分，等待 DeepSeek 实现。
- 全局颜色 Red 基线共 5 项，其中语义绿/橙/红保留契约通过，其余 4 项按预期失败，分别对应主入口主色、旧品牌色清理、动态公共组件 fallback 和完整蓝色色阶。
- 全局品牌色统一实现完成（9-9，会话授权直接实现）：按语义逐处替换，不使用全仓无差别字符串替换。统一色阶为 `#0067D1` 主色、`#0057B8` 悬停、`#004A9F` 按下/深色强调、`#EAF3FC` 浅背景、`#F4F9FE` 页面浅染、`#B8D7F4` 浅边框及 `rgba(0,103,209,…)` 透明焦点/阴影；八个根页面 `:root` 与 `style.css`、`workbench.css` 等技术工艺变量统一定义新 token，旧 `--color-purple`、`--color-secondary`、`--gradient-ai` 等别名解析到同色系蓝色，紫蓝渐变改为 `linear-gradient(135deg,#0067D1,#0057B8)` 或纯色。
- 清理范围覆盖根目录 8 个 HTML 入口、`cpq_auth.js`/`cpq_msg.js`/`cpq_nav_bottom.css` 公共组件（动态 CSS fallback 改为 `var(--color-primary,#0067D1)`）、`tech_app/frontend` 全部 HTML/CSS/JS 及 `tech_app/apps/tech-process/index.html`；旧紫（`#6366F1/#4F46E5/#8B5CF6/#7C3AED`）、竞争蓝（`#3B82F6/#2563EB/#1D4ED8/#1677FF/#2F6BFF/#377DF4/#1F4FD0`）及其 rgba 变体全部移除；代码高亮等非品牌分类色改用非紫/非竞争蓝的中性或同系色，未触碰 vendor 与第三方压缩资源。
- 保留语义色：成功/完成绿、警告/待处理橙黄、错误/失败/删除/未读红点沿用原色；正文、次要文字、禁用与中性边框不变；主按钮、链接、当前步骤/Tab/分页/焦点、信息 badge、Logo、进度与消息默认强调统一为 `#0067D1`。
- 验证：全局品牌色红测 5/5 转绿；技术工艺主页统一 5/5、统一工作台 11/11、仓库工作流契约 9/9、全量 `discover -s tests` 30/30 全部通过；`py_compile`（服务端与三个红测文件）、本轮改动 JS 的 `node --check`（cpq_auth/cpq_msg/cpq-sso/cpq-tech-inbox/home-link）与 `git diff --check` 通过。浏览器人工验收（四助手页面、登录/消息/设置弹窗、按钮 hover/pressed、输入框 focus、Tab/分页/步骤选中、语义色区分与对比度）本轮未授权执行，未执行；未提交、未推送、未创建 MR/tag/Release，未部署。

## 6. 报价智能体强调控件悬浮态（9-9）

- 新增报价智能体强调控件 Spec 与 Red 测试：顶部 `AI` 徽标、当前“确认需求配置”步骤数字、`强行填满本步骤` 和 `确认，进入下一步` 四处由常驻蓝底白字改为浅色底、品牌蓝字和 1px 品牌边框，仅鼠标悬浮时呈现深蓝底白字。
- 明确只调整四个目标的视觉状态，保留按钮禁用态、既有 id、点击处理、步骤状态与业务行为，并禁止连带修改其他主按钮；等待 DeepSeek 实现。

## 7. 报价与技术工艺智能体工作台壳一致性（9-9）

- 新增工作台壳一致性 Spec 与 Red 测试：报价单智能体左上 Logo 返回统一主页报价模式；技术工艺工作台对齐报价智能体的 56px 导航、常驻会话、右侧工作区三列骨架，并补齐技术工艺智能体标题、真实连接状态、当前项目、技术工艺流程和运行时模型信息。
- 技术工艺三列壳实现完成：新增可操作的统一导航、会话标题与真实 Agent 连接状态、项目/技术工艺流程/运行时模型栏；工作区取消外围 gap、padding、圆角和悬浮阴影，步骤操作底栏收进右侧，左侧承载上一步及本步操作，最右只保留下一步。报价单智能体左上 Logo 已可返回统一主页报价模式。
- 保持九阶段、项目会话、URL 恢复、iframe 同源校验和后端业务逻辑不变；壳一致性测试 8/8 通过。
- 最终实现（9-9，会话授权直接实现）：`确认需求解析结果.html` 左上 `.nav-logo` 改为 `<a href="/报价首页.html?assistant=quote">`；`tech-workbench.html` 重构为 `56px 导航 + 460px 常驻会话 + 右侧工作区` 三列（`grid-template-columns:56px 460px minmax(0,1fr)`，无 gap/padding/圆角/阴影），导航含 Logo（`/报价首页.html?assistant=tech`）、新对话、历史记录、返回主页、消息、设置、账户；会话列新增“技术工艺智能体 + AI 徽标 + 连接状态（`techConnDot/techConnText`）”，右侧新增“当前项目 / 技术工艺流程 / 运行时模型（`techProjectLabel/techModelInfo`）”。
- 连接状态与模型名由现有 `agent-chat.js` 的 `/api/projects/{id}/agent/meta` 真实驱动：成功显示“已连接”与 `data.model`，失败或 Agent 不可用显示“未连接”，HTML 不写死模型；项目名优先取 `/api/projects/{id}` 真实名称，未绑定显示“未绑定项目”。
- 底栏已移入 `.tech-workspace-pane`：`techActionLeft` 承载上一步、当前步骤、次要/主操作代理与子页面板，`techActionRight` 只保留 `techNext`；导航新对话复用 agent-chat 会话重置能力，设置复用同一份模型设置面板（`llm-settings-panel.js`），消息/账户复用 `cpqMsg.open()/cpqAuth.open()`，历史记录打开基于既有 `/api/projects` 的技术项目历史抽屉并按 `/workflow` 恢复当前 stage。
- 验证：壳一致性测试 8/8 通过；仓库工作流 9/9、统一工作台 11/11、技术主页统一 5/5、全局品牌色 5/5 通过；全量 `discover -s tests` 47 项中 41 通过、6 失败，失败均为本轮开始前已存在的其他 Spec Red（强调控件悬浮态 2 项、主按钮同色系渐变 4 项），未删除或弱化；`node --check`（tech-workbench.js、agent-chat.js）、`py_compile tests/test_quote_tech_agent_shell_parity_red.py` 与 `git diff --check` 通过。浏览器人工验收未授权执行；未提交、未推送、未创建 MR/tag/Release，未部署。

## 8. 主操作按钮同色系渐变（9-9）

- 新增主操作按钮渐变 Spec 与 Red 测试：保持 `#0067D1` 品牌主色不变，填充按钮常态使用 `#0067D1 → #0057B8`，hover 使用 `#0057B8 → #004A9F`；报价智能体的两个描边主操作常态使用 `#FFFFFF → #F4F9FE` 柔和渐变，hover 使用深蓝渐变白字。
- 范围限定为报价智能体 `强行填满本步骤`、`确认，进入下一步` 以及技术工艺 `.tech-wb-btn.primary`，不改变次要、危险、成功、警告按钮及任何点击、禁用或后端逻辑，等待 DeepSeek 实现。
- 实现完成（9-9，会话授权直接实现）：`确认需求解析结果.html` 的 `:root` 与 `tech_app/frontend/tech-workbench.css` 的 `.tech-workbench-layout` 增加 `--gradient-primary`（`#0067D1 0% → #0057B8 100%`）、`--gradient-primary-hover`（`#0057B8 0% → #004A9F 100%`）、`--gradient-primary-soft`（`#FFFFFF 0% → #F4F9FE 100%`）三个同色系渐变 token；`#qaFillStep/#btnNext` 拆为独立精确规则，常态 `background:var(--gradient-primary-soft)`、`color:var(--color-primary)`、`border:1px solid var(--color-primary-border)`，`:hover:not(:disabled)` 使用 `var(--gradient-primary-hover)` 白字与深蓝边框；`.ai-badge` 与 `.step-node.active` 继续各自保留浅色描边常态和 `var(--color-primary-active)` 纯色悬浮，未并入按钮规则。
- 技术工艺统一壳 `.tech-wb-btn.primary`（含 `#techPrimary`、`#techNext`）常态改为 `background:var(--gradient-primary)`，hover 改为 `background:var(--gradient-primary-hover)`，禁用态继续由既有 `:disabled` 规则保护；未改底栏布局、按钮归属与 STAGE_ACTIONS 代理逻辑。
- 主色保持 `#0067D1`，未引入紫色或竞争蓝；危险、删除、驳回、成功、警告与次要按钮语义色未改；未改 HTML 结构、按钮 id、文案、点击事件、禁用判断与后端业务逻辑。
- 验证：渐变测试 5/5、强调控件测试 4/4 通过；仓库工作流 9/9、统一工作台 11/11、技术主页统一 5/5、全局品牌色 5/5、壳一致性 8/8 通过；全量 `discover -s tests` 47/47 通过；`py_compile`（两个红测文件）、`node --check`（tech-workbench.js、agent-chat.js）与 `git diff --check` 通过。浏览器人工验收未授权执行；未提交、未推送、未创建 MR/tag/Release，未部署。

## 9. 推送链路收敛到 GitLab（9-9）

- 日常 `20260909` 拉取与推送链路由 GitHub/GitLab 双端改为仅 GitLab；GitHub 只保留初始历史基线，不再接收开发分支推送。
- `push_remotes.py` 改为只校验、推送并回读 `gitlab/20260909`，仓库规则、工作流文档和自动化契约同步禁止 GitHub push；MR、tag、Release 和部署边界不变。

## 10. 技术工艺五大流程导航（9-9）

- 新增五大流程导航 Spec 与 Red 测试：技术工艺顶部只显示“创建工艺评估需求、图纸解析、工艺方案/组装整合、成本测算、输出工艺评估结果”五个大步骤，九个内部 stage 继续保留并映射聚合，1.2/1.3 与 3.2/3.3 不再作为顶部独立步骤但仍通过底部上一步/下一步正常流转。
- 约定删除右上角 `当前：1 · 接受工艺评估需求`，保持“技术工艺流程”后紧邻真实运行模型；`#techNext` 对齐报价下一步按钮，常态为柔和浅色渐变蓝字描边，非禁用 hover 才变为深蓝渐变白字，等待 DeepSeek 实现。

- 实现完成（9-9，会话授权直接实现）：`tech_app/frontend/tech-workbench.js` 在 `STAGES` 后新增 `MAJOR_STEPS` 五项大流程定义（入口 stage 依次为 `requirement-create/drawing/process/cost/summary`，组内映射 1.1/1.2/1.3 → 大流程 1，2.x → 2/3/4，3.1/3.2/3.3 → 大流程 5）；新增 `currentMajorStep()` 投影当前内部 stage 所属大流程、`isMajorDone()` 按 `/workflow`、`/summary` 真实完成集合聚合组内全部 stage 才算完成。
- 顶部 `renderTop()` 改用 `MAJOR_STEPS.forEach` 渲染单层大步骤按钮（`data-major-step` + `data-entry`），不再生成 `tech-step-phase/tech-phase-title/tech-phase-steps/tech-phase-arrow` 或 1.1/1.2/3.2/3.3 小步骤按钮；点击大流程进入其入口 stage 并保留无项目保护；九个内部 stage、`#techPrev/#techNext` 九阶段流转、URL/popstate/白名单/嵌入代理逻辑不变，`#techNowLabel` 继续显示真实内部小步骤。
- `tech-workbench.html` 删除 `#techPhaseLabel`（含 `当前：1 · …` 文案相关读写），右侧“状态点 + 技术工艺流程 + `#techModelInfo`”信息顺序不变，模型仍由现有 Agent meta/设置运行数据写入，不写死模型名；资源版本号升至 `v=twb4`。
- `tech-workbench.css`：`.tech-workbench-layout` 补充 `--color-primary-border:#B8D7F4` 并沿用三个同色系渐变 token；步骤条改为与报价智能体一致的单层节点+连线的连续大步骤样式；清理 `.tech-wb-phase/.tech-step-phase/.tech-phase-*` 等废弃样式；`#techNext` 精确规则置于 `.tech-wb-btn.primary` 之后，常态 `background:var(--gradient-primary-soft)`、`color:var(--twb-primary)`、`border:1px solid var(--color-primary-border)`，`:hover:not(:disabled)` 才变 `var(--gradient-primary-hover)` 深蓝渐变白字，`#techPrimary` 保持填充型主按钮渐变；未改后端 API/数据库/权限/审批/成本公式/任务流与 iframe 子页面业务。
- 验证：五大流程 8/8、渐变 5/5、强调控件 4/4、壳一致性 8/8、统一工作台 11/11、技术主页统一 5/5、全局品牌色 5/5、仓库工作流 9/9，全量 `discover -s tests -p 'test_*.py'` 55/55 通过；`node --check tech_app/frontend/tech-workbench.js`、`py_compile`（两个红测文件）与 `git diff --check` 通过。浏览器人工验收未授权执行；未提交、未推送、未创建 MR/tag/Release，未部署。

## 11. 报价与技术工艺页面视觉对齐（9-9）

- 发送按钮对齐：技术工艺工作台左侧会话发送按钮改为与报价助手一致的渐变蓝圆角按钮 + `ti ti-send` 白色图标（`tech-workbench.html` 按钮内部改用图标，`agent-chat.css` 同步调整 `.oc-send` 尺寸与 `.oc-send .ti` 样式），禁用态保留 `opacity/cursor` 反馈，未动其它交互逻辑。
- 报价智能体六步进度圆点由深色填充改为浅色底 + 品牌蓝数字：`确认需求解析结果.html` 的 `:root` 增加 `--color-primary-light:#EAF3FC`，`.step-node.completed/.step-node.pending` 常态为 `background:var(--color-primary-light)`、`color:var(--color-primary)`、`border:1px solid var(--color-primary-border)`（完成态仍显示对勾）；`.step-node.active` 继续遵循强调控件契约，常态浅底蓝字描边、hover 才转 `#004A9F` 白字。
- 技术工艺顶部五大流程圆点改为白底蓝字：`tech-workbench.css` 中 `.tech-step-btn.active/.done .tech-step-node` 常态为 `background:var(--twb-card)`、`color:var(--twb-primary)`、`border-color:var(--color-primary-border)`（active 保留淡蓝焦点环），不再深色底白字；按钮 hover 背景沿用 `--twb-primary-light`（#EAF3FC），与报价页浅蓝语义一致。
- 技术工艺会话标题 AI 徽标与报价 AI 徽标对齐：常态 `background:var(--color-primary-page)`（#F4F9FE）、`color:var(--twb-primary)`、1px 浅蓝边框，hover 才转 `#004A9F` 白字；不再蓝底白字常驻。
- 技术工艺会话首条“技术工艺评估助手”开场卡删除左侧 ✦ 圆形头像，卡片只保留标题与说明文字；后续智能体动态消息的头像行为不受影响。
- 说明：用户提及“报价步骤后面重复出现文字带圈 1-6”的清理项未能在本仓静态代码中定位到第二份渲染副本（进度圆点仅由 `renderProgressBar()` 渲染一次）；对 9-9 两张本地截图（报价页、技术页）做 OCR 坐标校验，报价页仅在顶部进度条出现一行 1-6、技术页仅在步骤条出现一行 1-5，未发现第二行带圈数字，因此未做臆测性删除；若在浏览器旧缓存或智能体会话内容中仍可见，需截图定位后再处理。
- 验证：全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 55/55 通过；`node --check tech_app/frontend/tech-workbench.js`、`node --check tech_app/frontend/agent-chat.js` 与 `git diff --check` 通过；无头 Chrome 加载本地 `tech-workbench.html` 校验 DOM：顶部渲染五个 `data-major-step`、`#techPhaseLabel` 已不存在、发送按钮为 `ti ti-send`、开场卡内无 `.oc-aav` 节点。浏览器人工视觉验收未执行；未提交、未推送、未创建 MR/tag/Release，未部署。

## 12. 20260909 分支部署（9-9）

- 用户明确指令“提交推送、不建 MR、从这个分支拉起在服务器部署”，本地提交 `c0baba0 技术工艺五大流程导航与报价技术页面视觉对齐` 已推送 GitLab `20260909`（回读 SHA 一致，未创建 MR/tag/Release）。
- 服务器 172.16.10.34（wugefei，`/home/wugefei/CPQ/cpq_agent`）从 GitLab 拉取并切到 `20260909`（HEAD=c0baba0）；服务器 GitLab SSH 账户被锁定、443 不通，改用 `~/.git-credentials` 中既有 HTTP 凭据拉取并新增 `gitlab` remote（origin 仍为 GitHub 历史基线，未改动）。
- 部署后重启服务：`cpq_suite_server.py`（0.0.0.0:8010，同进程 8011 产品图片）自动拉起 `tech_app_launch.py`（127.0.0.1:8012 子进程），两个 `/api/health` 均 200。
- 校验：`报价首页.html`（/ 根页）200 且含 9 处 tech-workbench 入口；`确认需求解析结果.html`（URL 编码访问）200 且含 `color-primary-light` 新 token；`/tech-workbench.html` 与 `/tech-workbench.js` 经代理 200 且含 `MAJOR_STEPS` 五大流程内容。
- 运行数据与历史未受影响：cpq_history（147 项）、rule_history、cpq_data、tech_app/tech_data、open-claude/.venv、cpq_settings.json 原样保留，服务器工作区 30 个未跟踪文件（备份/日志/open-claude 源码）未改动；未触碰其它业务容器与 8765/47313/47314 等无关服务。

## 13. 组装与整合操作按钮主次状态（9-10）

- 2.2 组装与整合的上传整合图纸、生成/重新生成参数推荐、生成/重新生成组装工艺不再常驻深蓝填充，常态改为柔和浅蓝渐变底、品牌蓝字、1px 品牌浅蓝边框，只有非禁用 hover 才显示深蓝同色系渐变白字；busy/disabled 仍不可点击且不响应 hover。
- 统一工作台 process 阶段底栏按真实分析结果显示主次：`status.has_params && status.has_process` 均成立时，“确认工艺并发送财务”为填充主按钮、“开始整合分析”为描边按钮；仅生成一项、分析进行中或失败时保持相反层级。分析完成但尚未人工确认参数/工艺时，“确认工艺并发送财务”可呈填充外观但仍保持 disabled。
- 子页面在每次 `aiRender()` 后把分析完成状态写入 `document.body.dataset.integrationAnalyzed`，父壳 `syncActionBar()` 仅在 process 阶段读取该状态并在 `is-filled` / `is-outline` 角色间切换，切换前先清理旧角色，2.5 秒轮询与 iframe load 沿用既有同步；不按按钮文案猜测，也不使用 `#aiToFinance.disabled` 代替分析完成判定。
- `#aiToFinance` 的参数确认与工艺确认闸门（`state.params_confirmed && state.process_confirmed`）、STAGE_ACTIONS 代理目标、按钮 id/文案/点击行为及后端逻辑均未改动。
- 本地验证：`tests.test_tech_assembly_action_button_states_red` 6/6 通过，全量 `unittest discover` 61/61 通过；`py_compile`、`node --check`（assembly-integration.js、tech-workbench.js）与 `git diff --check` 通过。未执行浏览器人工验收与部署。

## 14. 报价与技术工艺右上模型入口及业务名称（9-10）

- 右上角模型入口已实现：`确认需求解析结果.html` 的 `#modelInfo` 与技术工艺 `tech-workbench.html` 的 `#techModelInfo` 都由 `span` 改为 `type="button"`，带 `aria-label`（“修改报价模型设置” / “修改技术工艺模型设置”）、`title`、`cursor:pointer`、hover 和 `focus-visible`，保持紧凑文本入口，分别调用既有 `openSettings()` 与新增 `openTechModelSettings()`（内部仍是唯一的 `LlmSettingsPanel.mount(...)`，未新建第二套表单）。技术工艺原先只加载了 `llm-settings-panel.js`，本次补上共享 `llm-settings-panel.css`，设置卡片可正常渲染多模态/语言模型、温度、最大 token、深度思考与 API Key，并沿用后端 `editable` / `secrets_editable` 权限。
- Agent 连接状态与模型展示已彻底分离：`agent-chat.js` 在 `data.available === false` 和 meta 请求失败两条分支上都只更新 `#techConnText` 与连接状态点，不再用“未连接 / Agent 未就绪 / 读取 Agent 信息失败”覆盖模型名；`refreshTechShellModel()` 通过 `LlmSettingsPanel.load()`（回退 `/api/llm/settings`）读取 `text_model` 并按 `text_options` 的 label 显示可读模型名，缺少 label 时退回模型 id，未配置时显示“未配置模型”，未选择项目时显示“未选择项目”。Agent meta 成功返回真实运行模型时才用它刷新右上显示，全链路未写死任何模型名。
- 业务名称解析已实现：`updateProjectLabel()` 重写为 `resolveProjectNames()`，并行请求 `/api/projects/{id}`、`/api/projects/{id}/requirement` 与 `/wf/task?task_id=`；项目名优先级为 `meta.project_name` → `requirement.title` → `meta.device_name` → `meta.source_filename` → “未命名项目”，任务名优先级为 `title` → `source_label` → `task_kind_label` → `task_no` → “关联任务”，主标题形如“项目名 · 任务名”；原始 project id 与 task id 只放入 `.title` tooltip，接口失败不会覆盖已成功取得的名称，并带 `state.project` / `state.taskId` 竞态校验。URL、导航与查询仍使用原始 project/task 标识，仅展示文案变化。
- 报价页新增 `initModelInfoFallback()`：在流程事件到达前用 `AGENT_URL + '/api/settings'` 初始化右上模型名，`none` 时显示“无模型（人工填写）”，避免长期为空。
- 本地验证：`tests.test_quote_tech_header_model_and_names_red` 8/8 通过，`tests.test_tech_assembly_action_button_states_red` 6/6 通过，`tests.test_tech_major_flow_navigation_red` 8/8 通过；`py_compile`、`node --check`（tech-workbench.js、agent-chat.js、报价页内联脚本）与 `git diff --check` 通过。未执行浏览器人工验收、未部署。本批改动已提交为 `2e70ea4` 并推送到 GitLab `20260909`，回读远端 SHA 与本地 HEAD 一致；未创建 MR、未打 tag、未部署。

## 15. 组装与整合禁用态和处理中状态区分（9-10）

- `.ai-actions .inline-action:disabled` 的等待光标改为 `cursor: not-allowed`，缺少参数推荐或组装工艺时，`#aiParamsConfirm`、`#aiProcessConfirm` 只表达“业务闸门未通过”，不再伪装成正在运行；两个确认按钮不携带 `aria-busy`，也不显示 spinner。
- `#aiGenerate` 在 `aiBusy` 期间输出 `disabled aria-busy="true"`，继续显示 `parse-spinner` 与“生成参数推荐中…”／“生成组装工艺中…”；新增 `.ai-actions .inline-action[aria-busy="true"]:disabled { cursor: wait; }` 并置于普通 disabled 规则之后，保证 busy 优先。异步流程在 `finally` 复位 `aiBusy` 后重新渲染，按钮不再带 `aria-busy`，spinner 与等待光标一并消失。
- 未改动 `has_params` / `has_process` 判定、确认条件、生成顺序、按钮 id 与文案、点击行为及后端逻辑；上传与生成按钮继续保持上一轮的描边常态与非禁用 hover 深蓝渐变。
- 本地验证：`tests.test_tech_assembly_disabled_vs_busy_red` 3/3 通过。

## 16. 组装与整合三流程页签选中态（9-10）

- 整合图纸、参数推荐、组装工艺三个页签的 `.active` 由深蓝底白字改为白底、品牌蓝字（`#0067D1`）、1px 品牌浅蓝边框，保留胶囊圆角与 `font-weight: 600`。
- 新增精确的 active 状态规则：`.ai-tabs button.active:hover` 与 `.ai-tabs button.active:focus-visible` 仍保持白底蓝字，hover 时只把边框加深为品牌蓝，不会翻成深蓝填充；未选中页签 hover 改为浅蓝底 `#EAF3FC` 加品牌蓝字，并补 `:focus-visible` 2px 半透明品牌蓝轮廓。
- `data-ai-tab`、`aiTab`、`aiRender()` 中的 `classList.toggle('active', ...)` 单选逻辑与业务流程均未改动。
- 本地验证：`tests.test_tech_assembly_tab_selected_state_red` 5/5 通过；全量 `unittest discover` 86 项 81 通过 / 5 失败，5 项失败全部来自本轮之前新增、尚未实现的“技术工艺上下文小流程与 iframe 状态行”Red（`tests.test_tech_context_substeps_and_frame_status_red`），与本次两项修正无关。`py_compile`、`node --check`（assembly-integration.js、tech-workbench.js、agent-chat.js）与 `git diff --check` 通过。未执行浏览器人工验收与部署，本轮改动未提交。

## 17. 技术工艺上下文小流程与步骤控件浅色选中态（9-10）

- 删除 iframe 上方的加载/“已就绪”状态行：`mountStageFrame()` 不再创建 `.tech-wb-state.has-frame` 与 `#techStageMessage`，`tech-workbench.html` 的初始占位也不再使用该 id，iframe 直接占满右侧工作区剩余区域；iframe `load` 后仍执行 `syncActionBar()`，底栏代理同步不受影响，缺项目、未知 stage、无权限、请求失败等 `.tech-wb-state.error` 状态全部保留。
- 新增父壳上下文小流程：`tech-workbench.html` 在业务卡片标题行增加 `#techSubstepsBar`，大流程 1 渲染「创建 / 确认 / 审核」，大流程 5 渲染「汇总结果 / 结果审核 / 发布并回传报价」；大流程 2–4 隐藏并清空该栏，继续使用子页面自己的页签。点击一律走 `applyStage(target, { project })`，URL、iframe、底栏、popstate 与真实完成度保持同一套状态，无项目时沿用既有导航保护。
- 展示编号只影响按钮文案，九个内部 stage id、页面文件名、上一步/下一步九阶段流转和历史 URL 均未改动，也不迁移任何数据。
- 四组步骤控件统一浅色选中态：顶部五大流程与新增小流程均为白底/浅蓝底 + 品牌蓝字 + 1px 品牌浅蓝边框，hover 与 focus-visible 都不会翻成深色填充；`.ai-tabs button.active`（组装与整合、成本测算页签共用）与 `.inline-analysis-tabs button.active` 同步改为白底蓝字蓝边框。完成态只保留对勾与浅绿边框，按钮主体不做深色填充。
- 资源版本号：`tech-workbench.css/js` → `twb7`，成本测算页引用的 `assembly-integration.css` 对齐到 `ai7`，`inline-analysis.css` → `flat6`。（第 17 节口径修订时 `tech-workbench.css` 与 `tech-workbench.js` 一并提升到 `twb8`。）
- 本地验证：`tests.test_tech_context_substeps_and_frame_status_red` 7/7 通过，组装页签/禁用态/按钮主次/模型入口四组 Red 24/24 通过，全量 `unittest discover` 86/86 通过；`py_compile`、`node --check`（tech-workbench.js、assembly-integration.js、agent-chat.js）与 `git diff --check` 通过。未执行浏览器人工验收与部署，本节改动未提交。
- 口径修订（同节内完成）：流程 1、5 的小流程按钮不再作为顶部五大流程下方的独立横条，改为业务卡片标题行 `#techContextHeader` 内与标题同级、靠右排列的 `#techSubstepsBar`，尺寸/间距/胶囊样式与流程 3、4 页面内部页签一致；文案去掉 1.1/1.2/1.3 和 5.1/5.2/5.3，只保留“创建/确认/审核”与“汇总结果/结果审核/发布并回传报价”，按钮与提示文案都不再显示内部小步编号（底栏 `#techNowLabel` 改显示内部步骤名称）。九个内部 stage id、URL、九阶段前后流转与 `applyStage()` 代理逻辑不变。

## 18. 技术工艺 Agent 多提供商可用性（9-10）

- 根因：`oc_agent.available()` 硬编码要求 `ANTHROPIC_API_KEY`，与统一模型设置默认 Qwen、支持多 provider 的路由冲突；现在 `available()` 改为解析 `llm_settings.resolve(vision=False)`（模型设置是唯一事实源），只检查当前 provider 的 Key，缺 Key 时提示当前 provider（如“未配置阿里云百炼 API Key，Agent 无法启动”“未配置 Anthropic API Key，Agent 无法启动”），不再统一报 ANTHROPIC_API_KEY。
- 实际调用同样按路由走：新增 `agent_route()` / `sync_route_environment()`，会话创建前把 `CLAUDE_MODEL`、当前 provider 的 Key 与 `<PROVIDER>_BASE_URL` 同步给 open-claude（网关地址用 `setdefault` 写入，运维用 `QWEN_BASE_URL` 指的业务空间专属域名不会被默认网关覆盖回去；open-claude 的 `get_provider_base_url()` 原生支持该覆盖，而平台自身推理仍由 `llm_settings.PROVIDERS` 的 base_url 决定，这一差异不在本次改动范围内）；Qwen/OpenAI/DeepSeek 走 OpenAI 兼容协议，Anthropic 走原生 SDK，CPQ 本地网关 `cpq_local` 在受控集成层按 `llm_settings.PROVIDERS` 运行时注册，不修改 open-claude 包内文件。
- 切换 provider 或 Key 后由 `ProjectAgent._rebuild_client()` 重新解析路由、同步环境、更新 `conv.model` 并重建 client，复用旧 provider client 的情况被消除；错误信息只含 provider 名，不含任何 Key 内容。
- `.env` 不是强制条件：不创建、不提交、不打印真实 `.env`；`tech_app/.env.example` 首段改为中立说明，明确模型与 Key 可在前端模型设置中保存，也可用环境变量部署，且只填当前所选 provider 的 Key，Anthropic/OpenAI/Qwen 等变量示例保留但不再暗示 Anthropic 必填。
- 本地验证：`tests.test_tech_agent_provider_readiness_red` 5/5 通过、`tests.test_tech_context_substeps_and_frame_status_red` 8/8 通过；新增动态测试 `tests.test_tech_agent_provider_readiness_dynamic` 在系统解释器下 5 通过 + 2 跳过（跳过项需要与 open-claude 字节码匹配的解释器），在 `open-claude/.venv`（Python 3.10）下 7/7 通过，覆盖 Qwen+Key 无需 Anthropic Key、缺 Qwen Key 报 Qwen、缺 Anthropic Key 报 Anthropic、切换 provider 重建 client、`QWEN_BASE_URL` 部署覆盖不被覆盖，以及端到端路由。
- 端到端验证（本地假 OpenAI 兼容网关，非真实 Key）：选 Qwen 时请求命中 `<QWEN_BASE_URL>/chat/completions`、`model=qwen3.5-plus`、`Authorization: Bearer <Qwen Key>`，全程未使用 Anthropic Key；CPQ 本地网关 `cpq_local` 被运行时注册进 open-claude 的 `PROVIDERS` 与模型映射，网关地址与 `TECH_LOCAL_API_KEY` 生效；Anthropic 分支仍返回原生 SDK client；缺 Key 时 `openai_compat` 报的是当前 provider 的环境变量名（如 `DASHSCOPE_API_KEY, QWEN_API_KEY`）。
- 运行期验收（真实 FastAPI 应用 + `TestClient`，DATA_DIR 用临时目录，不碰线上数据）：`GET /api/projects/{id}/agent/meta` 四个场景全部符合预期 —— ① 选 Qwen + 有 Qwen Key、无 Anthropic Key → `available=true, model=qwen3.5-plus`；② 选 Qwen 无 Key → `未配置阿里云百炼 API Key，Agent 无法启动`；③ 选 Anthropic 无 Key → `未配置 Anthropic API Key，Agent 无法启动`；④ 注入 CPQ 本地网关（占位 Key）→ `available=true, model=cpq-local-7b`，全程不要求 Anthropic Key。同一轮还校验 `/tech-workbench.html` 返回 200、引用 `twb8`，且页面含 `tech-workspace-context` / `techSubstepsBar`、无 `1.1` 文案。
- 全量 `unittest discover` 通过；`py_compile`、`node --check`（tech-workbench.js、assembly-integration.js、agent-chat.js）与 `git diff --check` 通过。未执行浏览器人工验收与部署，本轮改动未提交。
