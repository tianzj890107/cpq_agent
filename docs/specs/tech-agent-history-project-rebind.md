# Spec：技术工艺左侧会话跟随「打开的项目」重新绑定并回放历史

## 背景（用户实测）

技术工艺统一工作台里，打开一个已有项目后左侧会话带不回来，并且会话里出现
`⚠ 读取历史会话失败：Not Found`。

两条独立成因（都已实测，不是猜测）：

1. **本地服务进程过旧（环境问题，不是代码缺陷）**
   - `tech_app/backend/main.py:1856` 的 `GET /api/projects/{project_id}/agent/history`
     由提交 `dd12eeb`（2026-09-14 14:18）引入。
   - 本机 8010（`cpq_suite_server.py`，PID 91007）与其子进程 8012（`tech_app_launch.py`，
     PID 91022）都启动于 2026-09-11 10:03，且 `tech_app_launch.py:99` 用
     `uvicorn.run("backend.main:app", …)` **没有 reload**。
   - 实测：`127.0.0.1:8012/openapi.json` 只有 `agent/{meta,send,new,settings}`，没有 `history`；
     `GET /api/projects/fcac7095bded/agent/history` → `404 {"detail":"Not Found"}`（FastAPI 默认 404），
     而 `…/agent/meta` → `401`（路由存在，只是没登录）。内网 172.16.10.34:8010 上
     `…/agent/history` → `401`，说明**已部署版本接口是有的**。
   - 结论：`Not Found` 只在本地旧进程上出现；**重启技术工艺服务即可消失**，代码不需要改。

2. **左侧会话的项目绑定只在脚本加载时取一次（代码缺陷）**
   - `agent-chat.js:19` 是 `const projectId = new URLSearchParams(location.search).get("project")`，
     整个会话宿主一辈子只认这一个项目；`loadHistory()` 还带 `historyLoaded` 一次性闸门。
   - 统一工作台换项目走 `tech-workbench.js applyStage(stage, { project })` → 只改 `state.project`、
     重挂 iframe、`history.pushState()` 改 URL，**不重载页面**。
   - 于是：历史抽屉（`techHistoryRestore` → `applyStage`）打开项目时，URL 变了、右侧看板是
     新项目的，左侧会话却仍是旧项目的对话（或空会话），永远不会去拉新项目的历史 —— 用户看到
     的「会话带不回来」。反向也一样：从项目 A 切到项目 B，左侧还留着 A 的对话、A 的任务文件、
     A 的进度卡，属于串项目。

## 范围

- 只改「左侧会话的项目绑定与历史回放」这一层：
  `tech_app/frontend/agent-chat.js`（把项目绑定变成可重绑 + 暴露统一入口）、
  `tech_app/frontend/tech-workbench.js`（切项目 / 历史恢复 / popstate 时同步给会话）。
- 不动：`/api/projects/{id}/agent/*` 后端路由与 `oc_agent.load_history()`、桥的协议与白名单、
  九阶段流程、右侧看板与业务动作、看板动作注册表、`#ocTaskProgressHost` 之外的会话 DOM 结构。

## R1 会话宿主导出可重绑的项目入口

- R1.1 `agent-chat.js` 的项目绑定改为可变（`let projectId`），不再是 `const` 一次性快照。
- R1.2 `window.ocTechAgent` 新增 `setProject(projectId)`；父壳只调这一个入口，不直接改会话内部状态。
- R1.3 缺少该入口（旧壳 / 独立 2.1 页 / 脚本未加载）时父壳安全跳过，不抛错、不阻断切步。

## R2 重绑 = 换项目后重新回放历史，绝不重置会话

- R2.1 项目 id 变化时：`historyLoaded` 复位为 `false`，重新 `loadHistory()`（`GET …/agent/history`），
  再 `loadMeta()`，并刷新该项目自己的文件 / 零部件匹配 / 结果入口计数。
- R2.2 **禁止**在重绑路径调用 `/agent/new` 或 `resetTaskFlow()` —— 换项目不是重置任务，
  后端已持久化的会话与项目结果一个都不能动。
- R2.3 项目 id 未变化时幂等返回：不重复请历史、不重复渲染、不闪。
- R2.4 重绑时清掉上一个项目留在会话里的可见内容：`.oc-amsg` / `.oc-ubub` 消息、
  `taskProgressCards` 映射、`#ocTaskProgressHost` 里的任务卡 DOM、`hasParsedIR` 与结果条；
  任务进度宿主本身是页面结构（保留），只清它的内容。
- R2.5 重绑到空项目（`""`）时不发请求，连接状态回到「未连接 / 未选择项目」。
- R2.6 用户正在输入但未发送的草稿保留，不被重绑清掉。

## R3 统一工作台在所有换项目路径上同步

- R3.1 `applyStage()` 在 `state.project` 生效后把当前项目同步给会话（含历史抽屉恢复、
  看板 `tech_ui.set_stage`、上下一步、导航入口）。
- R3.2 `popstate`（浏览器前进 / 后退换项目）同样同步。
- R3.3 同步发生在 `pushState()` 之后，保证会话读到的 URL 与 `state.project` 一致。

## R4 既有能力不得缩水

- R4.1 后端 `GET /api/projects/{project_id}/agent/history` 只读路由仍在，未被改成写接口。
- R4.2 `loadHistory()` 仍走 `api("/history")`，仍渲染 `user` / `assistant` / `tool_use` / `tool_result`
  四类事件，`tech_ui` 仍跳过。
- R4.3 首屏（URL 自带 `?project=`）仍照旧自动回放历史，不需要父壳调用。

## 验收

- 红测：`tests/test_tech_agent_history_project_rebind_red.py`（先红后绿）。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败。
- 本地人工：重启技术工艺服务后，用历史抽屉从「无项目」打开一个项目、以及从项目 A 切到项目 B，
  左侧会话都应显示目标项目自己的完整历史（含工具轨迹），且不出现 `⚠ 读取历史会话失败`。

## 本批不做

- 不修 `resetTaskFlow()`（新对话）残留任务卡 DOM 的既有问题（与本批「换项目」不同路径）。
- 不改 `Not Found` 的文案兜底：路由存在，重启服务即恢复；前端不再为旧进程做兼容。
