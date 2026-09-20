# 报价与技术工艺右上角模型入口及业务名称 Spec

## 目标

报价智能体和技术工艺智能体右上角都应显示当前真实模型，并且点击模型名称即可打开可修改模型的设置卡片。技术工艺不得因为 Agent 会话进程未就绪而把模型名称覆盖成“Agent 未就绪”。技术工艺当前项目区域应展示人能识别的项目、任务名称，不直接把内部 ID 当成名称。

## 已确认的前端缺口

1. 技术工艺 `#techModelInfo` 是普通 `span`，没有键盘语义和点击事件。
2. 技术工艺加载了 `llm-settings-panel.js`，但 `tech-workbench.html` 没有加载 `llm-settings-panel.css`，设置内容即使挂载也没有完整前端样式。
3. Agent meta 返回 `available:false` 或请求失败时，`agent-chat.js` 会用“未连接 / Agent 未就绪”覆盖 `#techModelInfo`，把“会话可用性”和“当前模型配置”错误地混成同一状态。
4. 报价页 `#modelInfo` 同样只是不可点击文字；模型设置只能从左侧设置按钮进入。
5. 技术工艺 `updateProjectLabel()` 错把 `/api/projects/{id}` 的包装响应当作扁平对象读取；实际项目元数据在 `data.meta`。它也没有读取 `/requirement` 的 `requirement.title`，并始终把原始 `task_id` 拼到标题。
6. 技术工艺已有左侧设置按钮、消息、账户和历史抽屉，均已有前端入口；本次扫描中除模型文字入口、模型面板 CSS、模型 fallback、项目/任务名称外，未发现同一右上角区域还有仅占位而完全未绑定的入口。本次不扩展到各 iframe 子页面的独立旧导航。

## 模型显示契约

- `#techConnText` 只表达 Agent 会话连接状态：“连接中 / 已连接 / 未连接”。
- `#techModelInfo` 始终表达 `/api/llm/settings` 的当前 `text_model` 对应可读 label；优先显示 `text_options[].label`，没有 label 才显示模型 id。
- Agent meta 可用且返回明确运行模型时，可以用运行模型刷新显示；Agent 不可用或 meta 请求失败时必须保留设置接口得到的模型，不能写入“Agent 未就绪”“未连接”。
- 模型尚未配置时显示“未配置模型”，不得伪造模型名。
- 报价页 `#modelInfo` 继续显示报价助手当前真实模型；若流程尚未返回模型，应从报价既有模型配置中初始化，不长期留空。

## 右上角交互契约

- 报价 `#modelInfo` 与技术工艺 `#techModelInfo` 均改为可点击、可聚焦的按钮式控件，保留在流程文字之后。
- 支持鼠标点击、Enter 和 Space；提供 `type="button"`、清晰的 `aria-label` 和 focus-visible。
- 点击报价模型入口复用报价既有 `openSettings()` 模态卡片。
- 点击技术工艺模型入口复用 `LlmSettingsPanel.mount()`；左侧 `#techSettings` 与右上模型入口调用同一个打开函数，不能复制两套表单。
- 技术工艺页面必须显式加载 `llm-settings-panel.css`，设置卡片应有完整布局、权限只读态、保存状态和关闭交互。
- 保存后立即刷新右上角模型文字；继续遵守接口返回的 `editable/secrets_editable` 权限。

## 项目与任务名称契约

项目显示优先级：`data.meta.project_name`、`data.requirement.title`、`data.meta.device_name`、`data.meta.source_filename`、最后“未命名项目”。原始 project id 只允许放在 tooltip，不作为主文字。需求标题通过既有 `/api/projects/{project_id}/requirement` 获取。

存在 `task_id` 时，通过既有 `/wf/task?task_id=...` 获取任务，按 `task.title`、`task.source_label`、`task.task_kind_label`、`task.task_no`、最后“关联任务”的优先级显示。主标题可以采用“项目名称 · 任务名称”，不得继续显示 `项目 24cb1092c547 · 任务 3981511113266173888` 这类原始内部 ID。接口失败不应覆盖已取得的项目名称，原始 ID 可放在 tooltip。

## 边界与验收

- 只修改前端 HTML/CSS/JS，不修改后端 API、数据库及权限；复用现有四个接口。
- 不写死 `qwen3.5-plus` 或任何模型名称，不创建第二套技术模型设置表单。
- Agent 未就绪时连接状态可以是未连接，但模型仍显示真实配置。
- 报价与技术工艺右上模型均可点击和键盘操作并打开既有设置卡片。
- 技术工艺完整加载共享设置面板 CSS，保存后刷新模型文字。
- 项目和任务显示业务名称，不以原始 ID 充当主文字。

