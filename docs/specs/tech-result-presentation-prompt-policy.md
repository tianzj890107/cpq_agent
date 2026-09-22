# 技术工艺 Agent「结果呈现」提示词口径 Spec（第一轮）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_agent_result_presentation_prompt_red.py`

## 背景与目标

两侧政策都写着「右侧看板是唯一真源」，但落地程度差很远：

- 报价侧是双保险：`cpq_agent_server.py:108-110` 的 `cpq_ui` 工具描述写着「不要在聊天里贴大表格」，
  系统提示词 `cpq_agent_server.py:1770-1772` 又写着「绝不允许只把表格写在聊天文字里」
  「聊天里不要贴 Markdown 表格……数据一律在右侧工作台看。这样右侧表格才是唯一真源，
  避免『左边一份、右边一份、对不上』」。
- 技术侧只有一半：`tech_app/backend/services/oc_agent.py:1130-1136` 的 `tech_ui` 工具描述只说
  「驱动技术工艺右侧看板的结构化界面动作」，**系统提示词 `SYSTEM_APPENDIX`（`oc_agent.py:2779`）
  完全没有这条口径** —— 只覆盖 2.1 / 2.2 两个阶段，「硬性要求」只有三条（数值只能来自工具、
  中文简洁结论先行、文件系统只读），全篇 0 次提到 `tech_ui`、0 次「右侧看板」、
  0 条禁止在聊天里贴 Markdown 表格或大段 JSON 的约束。

同时会话栏物理宽度只有 460px（`tech_app/frontend/tech-workbench.css:53`、
`确认需求解析结果.html:52`），报价侧唯一被设计过的会话内表格还要 `min-width: 620px`
外加 `overflow-x: auto` 容器（`确认需求解析结果.html:173-176`、`:1692`）；技术侧 markdown
根本不解析表格，模型一旦贴表就是一行行 `|` 竖线原文。所以结论是**统一到右侧看板**，
而不是给技术侧补表格渲染。

本批只改后端提示词，把这条口径补进 `SYSTEM_APPENDIX`，并补一张九阶段落点表 ——
因为 1.1–1.3 与 3.1–3.3 的看板页只注册了业务动作、没有注册任何视图
（`requirement-create.js` / `requirement-confirm-page.js` / `requirement-review-page.js` /
`summary-result.js` / `report-review-result.js` / `report-publish-result.js` 的 `registerViews` 计数为 0），
笼统写「用 focus_view 放右侧」会让模型在这些阶段发出无效视图命令。

## 产品契约

### 1. 结果呈现硬性要求（必须进 `SYSTEM_APPENDIX`）

- 数据与明细一律在右侧看板；**右侧看板是唯一真源**，不允许聊天与看板各存一份。
- **绝不允许只把表格写在聊天文字里**。
- 聊天里**不要贴 Markdown 表格**（`|---|` 那种），也不要贴大段 JSON / 字段清单。
- 聊天只写 2–3 句结论、依据与下一步，然后给出结果入口。

其中以下三句必须**原样出现**（便于机读校验）：

1. `绝不允许只把表格写在聊天文字里`
2. `不要贴 Markdown 表格`
3. `右侧看板是唯一真源`

### 2. 九阶段落点表（必须覆盖九个 stage id）

落点表必须逐个列出九个内部阶段：`requirement-create`、`requirement-confirm`、
`requirement-review`、`drawing`、`process`、`cost`、`summary`、`report-review`、`report-publish`，
并按「有没有注册看板视图」区分落点方式：

| 阶段 | 看板视图 | 落点方式 |
| --- | --- | --- |
| 1.1–1.3（requirement-*） | 无视图，只有表单/状态 | `fill_fields` 回填 + 刷新看板；不要发 `focus_view` |
| 2.1（drawing） | `parts` / `questions` / `report` / `evidence` / `review` / `files` | `focus_view` 选视图 + 结果入口 |
| 2.2（process） | `drawings` / `params` / `process` | `focus_view` 选视图；刷新由业务工具返回后自动完成 |
| 2.3（cost） | `parts` / `assembly` / `total` / `params` | `focus_view` 选视图；刷新由业务工具返回后自动完成 |
| 3.1–3.3（summary / report-review / report-publish） | 无视图 | 不要发 `focus_view`；刷新由报告类工具返回后自动完成 |

### 3. 视图调用必须带前提

必须原样包含这句：

`只有当右侧看板已注册该视图时才调用 focus_view`

否则将来给 1.x/3.x 补视图（第三批）后，这句约束要能自然覆盖，而不用改口径。

## 范围

允许修改：

- `tech_app/backend/services/oc_agent.py` 的 `SYSTEM_APPENDIX` 文案；
- 当周 changelog。

本批**不碰任何前端文件**（`tech_app/frontend/agent-chat.js` 正被另一个并行批次修改）。

## 禁止事项

- 不改 `tech_ui` 的八个 action 白名单、`TECH_UI_STAGES`、`TECH_UI_VIEWS` 与工具 schema；
- 不删既有三条硬性要求（数值只能来自工具 / 中文简洁结论先行 / 文件系统只读）与 2.1、2.2 阶段指引；
- 不改 `SYSTEM_APPENDIX` 的注入方式（仍由 `self.conv.system_prompt` 拼接）；
- 不给技术侧新增聊天内表格渲染或数据通道，不改前端；
- 不改工具返回文案与既有业务 service；
- 不为迁就实现修改本 Spec 或 Red 测试；
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 验收标准

1. 三句原样口径 + 「只有当右侧看板已注册该视图时才调用 focus_view」都在 `SYSTEM_APPENDIX` 里。
2. 口语化的 JSON 禁令存在（正则 `(不要|禁止|不得)[^。\n]{0,12}JSON`）。
3. 九阶段落点表覆盖全部九个 stage id。
4. 提示词明确要求经 `tech_ui`（`focus_view` / `refresh_view`）把结果落到右侧看板。
5. 既有三条硬性要求、2.1/2.2 阶段指引、注入方式、八项 action 白名单与视图白名单全部不变。
6. 新增 Red 测试转绿，且既有 `tech_ui` 协议测试与全量测试不新增失败。

## 对应测试

`tests/test_tech_agent_result_presentation_prompt_red.py`
