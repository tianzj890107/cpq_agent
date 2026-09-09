# DeepSeek 实现提示词：统一技术工艺与成本测算双栏工作台

你在仓库 `cpq_agent` 的 `20260909` 分支工作。请先完整阅读：

1. `AGENTS.md`
2. `docs/specs/unified-tech-cost-workbench.md`
3. `tests/test_unified_tech_cost_workbench_red.py`
4. `changelog/changelog_9_7_11.md`

## 任务

实现统一的技术工艺/成本测算工作台：左侧是整个项目生命周期持续存在的 Agent 会话，右侧承载需求、图纸解析、工艺方案、成本测算、汇总、审核、发布全部步骤。步骤切换不得整页刷新左侧会话。

采用 Spec 指定的渐进式架构：新增统一壳 `tech-workbench.html/css/js`，右侧以同源受控 outlet（优先 iframe + `embed=1`）复用现有步骤页面；不要复制或重写后端业务逻辑。

## 必须完成

- 建立九个稳定 stage 的白名单路由及三阶段步骤条。
- 左侧只创建一个 `techChatPane`，复用现有 Agent 会话能力并绑定 URL 的 `project`。
- 右侧只创建一个 `techWorkspaceOutlet`，仅允许加载仓库内同源步骤 URL。
- 所有子页面支持 `embed=1`：隐藏重复导航、重复会话、页面级头部和固定页脚，只保留业务工作区。
- 子页面使用事件 `cpq:tech-workbench:navigate` 请求导航；父页面严格校验 `event.origin === location.origin` 和 stage 白名单。
- 支持 `popstate`、刷新恢复以及 `project`、`stage`、`task_id` 参数透传。
- 报价首页技术工艺入口、技术待办、成本任务和旧页面直达入口汇聚到统一工作台。
- 旧链接不得失效，不得循环重定向。
- 同步更新本周 changelog，写最终行为和实际验证结果，不创建日报。

## 禁止事项

- 不改变数据库 schema、后端 API、角色权限、成本公式、审批门禁和回传报价语义。
- 不删除或清空历史会话、项目、任务、附件、缓存目录或数据库数据。
- 不通过复制业务逻辑制造第二套步骤实现。
- 不接受跨域 `postMessage`，不允许 outlet 加载任意 URL。
- 不删除或弱化红测；如果契约需要合理调整，先说明原因并保证验收意图不降低。
- 不创建 MR、tag、Release，不部署，不直接 push `master`。

## 重点复用文件

- `tech_app/frontend/index.html`：现有 2.1 左会话/右工作台布局基准。
- `tech_app/frontend/agent-chat.js`、`agent-chat.css`：会话能力。
- `tech_app/frontend/workbench.css`：现有工作台视觉变量与组件。
- `tech_app/frontend/workflow-navigation.js`：现有步骤状态与导航语义。
- `报价首页.html`：技术工艺入口和待办路由。
- `cpq_tech_bridge.py`、`tech_app/backend/services/cpq_bridge.py`：现有 CPQ/技术项目映射，只读理解，非必要不要修改。

## 验证命令

先确认红测基线失败，再实现并运行：

```bash
python3 -m unittest tests.test_unified_tech_cost_workbench_red
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m py_compile cpq_suite_server.py cpq_tech_bridge.py
git diff --check
```

还需人工浏览器验证：从报价首页进入；依次切换九个 stage；输入未发送的会话草稿后切换步骤；刷新、前进和后退；打开旧链接；验证成本测算、审核、发布和回传报价；检查窄屏布局；发送伪造跨域消息确认被拒绝。

## 交付格式

完成后请返回：修改文件清单、关键状态/路由设计、测试结果、未完成项和风险。不要只说“已完成”，不要声称未实际执行的浏览器或部署验证。
