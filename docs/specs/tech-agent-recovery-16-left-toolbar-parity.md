# 技术工艺 Agent 能力恢复第 16 步：统一左侧基础按钮 Spec

## 范围

把技术工艺左侧会话栏（`#techChatPane`）的操作按钮补齐到与报价 Agent（`cpq` 工作台左侧
`quick-actions` 条）同级，且**按当前 stage 动态变化**，不得只为 2.1–2.3 写死。

左侧会话栏至少有十类入口：

| 类别 | 说明 |
| --- | --- |
| 附件 | 补充资料（复用既有 `＋` 能力菜单 / 任务文件入口） |
| 当前步骤 AI 执行 | 触发本 stage 的 AI 主流程（等价底栏 primary 的 AI 动作） |
| 上一步 | 切到上一个 stage（复用 `applyStage`） |
| 下一步 | 切到下一个 stage（复用 `applyStage`） |
| 转交任务 | 把当前任务转交出去（复用既有 stage 转交能力或会话入口，不新增路由） |
| 当前步骤主要操作 | 等价底栏 `STAGE_ACTIONS[stage].primary` |
| 当前步骤次要操作 | 等价底栏 `STAGE_ACTIONS[stage].secondary` |
| 结果入口 | 复用既有 `#ocResultActions` 结果 chip 组 |
| 任务进度 | 复用既有 `#ocTaskProgressHost` |
| 失败重试 | 上一步操作失败后可重试（复用最近一次动作，不重新发明流程） |

硬要求：

- 按钮的文案 / 可见 / 可用全部来自**九阶段描述表**（每个 stage 一条），不查 iframe DOM、不写死 selector。
- 点击一律复用既有通道：`applyStage(...)` 切步、`TechBoardBridge.executeAction(...)` /
  `navigateView(...)` 驱动看板、既有 `＋` 菜单做附件；不新增 `@app.` 路由、不新增业务实现。
- 父壳仍不承载业务表单：结果与详情留在右侧看板。

## 1. 结构（`tech_app/frontend/tech-workbench.html`）

在 `#techChatPane` 内（会话线程与输入区之间）新增唯一一条左侧操作栏：

```html
<nav class="tech-chat-actions" id="techChatActions" aria-label="当前步骤操作">
  <button type="button" id="techChatAttach">附件</button>
  <button type="button" id="techChatAiRun">AI 执行</button>
  <button type="button" id="techChatPrev">上一步</button>
  <button type="button" id="techChatNext">下一步</button>
  <button type="button" id="techChatTransfer">转交任务</button>
  <button type="button" id="techChatPrimary">主要操作</button>
  <button type="button" id="techChatSecondary">次要操作</button>
  <button type="button" id="techChatRetry">失败重试</button>
</nav>
```

- 结果入口与任务进度**复用既有节点**：`#ocResultActions`、`#ocTaskProgressHost`（不得新建第二套）。
- 附件按钮复用既有 `#ocPlus` / `#ocCapabilityMenu` 能力菜单（点击等价于打开该菜单），不新建上传实现。

## 2. 行为（`tech_app/frontend/tech-workbench.js`）

新增九阶段描述表 `STAGE_CHAT_ACTIONS`，键必须是九个 stage id 全覆盖：

```
requirement-create / requirement-confirm / requirement-review /
drawing / process / cost / summary / report-review / report-publish
```

每条至少给出：`ai`（AI 执行动作名或 null）、`primary`、`secondary`、`transfer`（转交动作名或 null）、
`prev` / `next`（是否允许切步）。`ai` / `primary` / `secondary` 必须引用既有 `STAGE_ACTIONS`
里的语义化动作名，不得复制业务逻辑。

- `techChatAiRun` / `techChatPrimary` / `techChatSecondary` → `TechBoardBridge.executeAction(动作名)`；
- `techChatPrev` / `techChatNext` → 既有 `applyStage(prev/next, {project})`；
- `techChatTransfer` → 当前 stage 有转交动作时经看板桥触发它；没有时把任务转交意图带进会话输入区（不新增路由、不新增弹窗）；
- `techChatAttach` → 打开既有能力菜单；
- `techChatRetry` → 重跑**最近一次**经桥发出的动作（记住最近一次 `{kind, name, payload}`），无失败记录时禁用；
- 按钮的可见 / 可用按 stage 与看板回传的 `action-state` 决定，看板未就绪时禁用并给出提示。

## 3. 边界

- 不新增 / 删除路由与既有动作；`storage`、数据模型不改；
- 父壳不得用 `contentDocument` / `contentWindow.document` 查询 iframe 内按钮；
- 不改九个 stage id 与 URL，不动历史会话与项目数据；
- 结果与详情仍只在右侧看板展开，父壳不建 Drawer / Modal。

## 4. 验收标准

1. `tech-workbench.html` 的 `#techChatPane` 内存在左侧操作栏与 8 个控件 id，并复用 `#ocResultActions` / `#ocTaskProgressHost`；
2. `tech-workbench.js` 的九阶段描述表覆盖全部九个 stage id（不只 2.1–2.3）；
3. 8 个控件都有对应处理；主 / 次 / AI 经 `TechBoardBridge.executeAction`，上一步 / 下一步经 `applyStage`；
4. 附件复用既有能力菜单，不新增上传实现或路由；
5. 失败重试复用最近一次动作，不是新流程；
6. 无新增 `@app.` 路由；`tech-workbench.js` 无 iframe DOM 查询；
7. 既有 `STAGE_ACTIONS` / 底栏按钮 / 结果 chip / 任务进度宿主不被删除。

## 5. 对应测试

`tests/test_tech_left_toolbar_parity_red.py`
