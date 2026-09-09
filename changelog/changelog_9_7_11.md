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

## 4. Agent 授权与任务终态规则补齐（9-9）

- 对齐建模 Agent 和分析 Agent 的通用治理规则：任务终态后，“继续”不能重开任务或触发工具、修改、测试、服务、Git 和部署动作；建议、风险、文档命令与历史授权均不构成新的执行授权。
- 固化 push/MR/tag/Release/部署逐项指令映射、版本不得自行升级、外部服务与 CI 操作需当前授权、部分失败不得绕过或谎报、完成状态必须分项说明。
- 强化历史会话与业务数据保护、精确删除和防空变量校验；提示词只在会话交付，周 changelog 永久自动维护且禁止日报。
