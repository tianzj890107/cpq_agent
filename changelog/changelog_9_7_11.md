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

- 实现完成（9-9，会话授权直接实现）：在 `确认需求解析结果.html` 主样式末尾新增精确选择器规则，仅作用于 `.ai-badge`、`.step-node.active`、`#qaFillStep`、`#btnNext` 四个目标；常态为 `background:var(--color-primary-page)`、`color:var(--color-primary)`、`border:1px solid var(--color-primary-border)`，悬浮为 `background:var(--color-primary-active)`、`color:white`、`border-color:var(--color-primary-active)`，按钮悬浮选择器使用 `:hover:not(:disabled)` 并在该组规则中置 `filter:none` 抵消旧 `brightness(1.06)`，禁用态保留 `opacity/cursor:not-allowed` 反馈。
- 未改动四元素 DOM id、文案、onclick、步骤计算与禁用逻辑，未触碰其他 `.btn-primary`（设置保存、历史修改等）及任何后端/API/数据库/权限逻辑。
- 验证：`tests.test_quote_agent_emphasis_hover_red` 4/4 通过；仓库工作流 9/9、统一工作台 11/11、技术主页统一 5/5、全局品牌色 5/5（共 30 项）全部通过；`py_compile tests/test_quote_agent_emphasis_hover_red.py` 与 `git diff --check` 通过。浏览器人工验收未授权执行；未提交、未推送、未创建 MR/tag/Release，未部署。
