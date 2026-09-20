# 技术清单认证就绪加载与报价主色状态统一

## 1. 问题

1. 直接打开 `报价首页.html?assistant=tech` 时，首页内联脚本先执行 `loadCards('tech')`，但页面末尾的 `cpq_auth.js` 尚未执行，导致 `cpqAuth is not defined`。错误随后被误报成登录或后端服务异常。
2. 报价 Agent 写入看板且标记为推测的值使用橙色边框、浅橙背景，与系统主色不一致。
3. 当前账号角色不能操作报价步骤时，左侧会话 `wfGateBubble` 和右侧看板 `wfBar` 同时展示相同提示；右侧提示又使用橙色。

## 2. 目标行为

### 2.1 技术工艺清单

- 技术项目读取前必须等待 `window.cpqAuth` 完成加载和首次登录态刷新；不得直接访问未定义的裸变量 `cpqAuth`。
- 直接进入 `?assistant=tech`、刷新页面和正常点击技术工艺标签都应读取 `/api/projects`。
- 请求继续通过全局唯一的 `window.cpqAuth.api('/api/projects')` 携带登录凭证。
- 认证模块未加载、未登录、认证接口失败和项目接口失败必须保留各自真实错误，不得把 `ReferenceError` 包装成笼统的后端服务异常。
- `cpq-auth-ready` / `cpq-auth-change` 后，技术清单应能从失败或未加载状态重试，而不是只渲染旧数组。

### 2.2 Agent 回填看板状态色

- 报价 Agent 写入看板后使用的 `.guessed` 状态改为系统主色：
  - 浅背景使用 `var(--color-primary-light)`；
  - 边框使用 `var(--color-primary)`；
  - 文字使用 `var(--color-primary-active)`。
- 不在该状态写死橙色、琥珀色或 warning 变量。
- 只改变视觉语义，不改变 guessed 标记、字段值、表格写入、人工编辑或提交行为。

### 2.3 角色不匹配提示

- `WF.loaded === true && WF.canEdit === false` 时，只在右侧看板的 `#wfBar` 显示一条权限提示。
- 不再为角色不匹配调用 `showGateBubble('handoff')`，也不向左侧会话插入重复权限消息。
- 右侧只读提示使用系统主色浅蓝背景、蓝色边框和深蓝文字，不使用橙色/黄色。
- 未登录和工作流服务不可用属于不同准入错误；本次不强制删除它们既有的登录按钮或必要会话提示。
- 权限校验、按钮禁用、后端角色校验和“转交任务”能力必须保留。

## 3. 非目标

- 不修改角色定义、步骤负责人、工作流接口或鉴权后端。
- 不放宽任何操作权限，不允许只读角色提交。
- 不改变真正的风险、失败、低置信度等 warning/error 颜色。
- 不修改技术工艺项目数据、历史会话和登录 Token。

## 4. 验收

- `python3 -m unittest tests.test_home_auth_ready_and_quote_primary_state_colors_red -v` 全部通过。
- 直接刷新 `/报价首页.html?assistant=tech` 不再出现 `cpqAuth is not defined`，认证就绪后可加载清单。
- Agent 回填字段为浅蓝背景、蓝色边框。
- 用非当前步骤负责人账号打开报价：只有右侧看板一条蓝色只读提示，左侧会话没有重复提示，操作仍被禁止。
