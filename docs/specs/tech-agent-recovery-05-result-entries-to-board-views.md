# 技术工艺 Agent 能力恢复 5：结果按钮改为“导航右侧看板” Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_result_entries_board_views_red.py` `tests/test_tech_result_entries_board_views_dynamic.py`

## 1. 范围与前置条件

本 Spec 是《技术工艺 Agent 能力恢复》计划第 4 章“恢复 2.1 被丢失的功能”的**第 5 步**。实现时假定第 0–4 步已经完成：

- 第 0–3 步：后端能力守护、`cpq:tech-board` 标准协议（父壳 `TechBoardBridge`、看板 `TechBoardRuntime`）与业务动作注册表；
- 第 4 步：父壳左侧会话栏恢复 `#ocPlus` / `#ocCapabilityMenu` / `#ocResultActions` / `#ocPartsAction` / `#ocQuestionsAction` / `#ocReportAction` / `#ocFilesAction` / `#ocTaskProgressHost`。

本步只做一件事：**把左侧会话栏的入口从“父壳自己弹抽屉”改成“导航右侧看板视图”**。父会话栏只提供入口，实际内容始终在右侧看板内部。本步不实现零件详情、工序或成本视图（第 6、8、9 步负责），不新增后端能力，不改数据结构。

## 2. 唯一导航原则

> 父壳只负责导航和会话；所有业务数据、零件详情、工艺详情、成本详情必须在右侧看板内部展开。

据此，本步要求：

- 左侧入口点击后只发出**看板视图导航**；不打开父级 Drawer / Modal / 覆盖右侧看板的浮层。
- 右侧看板收到导航后，在**看板内容区**呈现对应内容（复用该页既有面板、既有函数、既有接口）。
- 返回、切换视图都只改变右侧看板内部状态：不关闭父壳、不清空或覆盖左侧会话、不重建会话宿主。
- 只有模型设置、登录、消息这类全局功能可以使用父级模态卡片。

## 3. 左侧入口 → 看板视图

父会话栏必须有一张**显式**的入口映射表 `TECH_BOARD_VIEW_ENTRIES`（具名、可读、单一来源），键是左侧控件，值是看板视图名：

| 左侧入口 | 控件 | 看板视图 |
| --- | --- | --- |
| 零件清单 | `#ocPartsAction` | `parts` |
| 待澄清问题 | `#ocQuestionsAction` | `questions` |
| 解析报告 | `#ocReportAction` | `report` |
| 解析视图 | `#ocCapabilityMenu` 的 `data-tech-capability="evidence"` | `evidence` |
| 版本与校核 | `#ocCapabilityMenu` 的 `data-tech-capability="review"` | `review` |
| 任务文件 | `#ocFilesAction` | `files` |

要求：

- 映射表的键就是左侧控件：结果按钮与任务文件用控件 id，`＋` 菜单项用 `capability:<name>`（如 `capability:evidence`）。
- 六个入口都必须走同一条具名出口 `boardNavigateView(view, payload)`；父壳不再持有 `openDrawer` / `DRAWER_GROUPS` / `#ocDrawer` 等业务抽屉代码。
- `boardNavigateView` 通过 `TechBoardBridge.navigateView(view, payload)` 发送 `navigate-view` 命令；桥缺失或未就绪时在会话里给出可见提示，不静默失败。
- 导航失败（看板未注册该视图、超时、业务报错）必须显示真实原因，并提供重试入口。
- 左侧“＋”菜单中不属于视图导航的入口（补充需求图纸、导入已有 3D 模型）同样不得在父壳打开业务抽屉；它们同样只能表达意图（导航到看板对应入口或调用看板动作），具体表单与文件选择留在右侧看板内。本步的验收只强制上表六个视图；这两项若看板暂未注册视图，父壳必须给出可见的“看板暂不支持该入口”提示，而不是打开父层抽屉。

## 4. 右侧看板视图注册

2.1 看板 `app.js` 通过 `TechBoardRuntime.registerViews` 注册上表六个视图名，每个视图提供 `run(payload)`（可选 `getState()` / `label`）：

- `parts`：零件清单；
- `questions`：待澄清问题；
- `report`：解析报告；
- `evidence`：解析视图（识别依据、原始图纸片段）；
- `review`：版本与校核（版本列表、型号核验、校验修正）；
- `files`：任务文件。

要求：

- 每个视图必须复用看板页面**既有**的面板、具名函数与既有后端接口，不复制业务实现、不新建第二套数据源；页面原有按钮继续调用同一份实现。
- 视图切换只改变看板内部呈现，并通过协议的 `action-state`（含当前 `view.active`）回传给父壳，供左侧入口高亮与状态显示；父壳不自行猜测当前视图。
- 导航到不存在的视图时，按协议返回结构化失败（`unknown-action`），父壳显示可见错误。
- 报告视图若复用既有 `report.html` 入口，必须保证内容仍落在右侧看板内，且协议通道不会因此失效（不能在父壳新开窗口或父层弹层）。

## 5. 数据与实现限制

- 不新增或删除任何后端接口、service、Agent 工具；不修改数据结构与历史数据。
- 父壳不得通过 `contentDocument`、CSS selector、跨层 `.click()` 读取或操作看板内容。
- 父壳不得渲染业务正文：`#secParts`、`#secQuestions`、`#secEvidence`、`#tree`、`#partDetail` 等业务 DOM 不得出现在父壳（第 4 步已确立，本步继续执行）。
- 不得为导航另建状态副本：当前视图、可用性、计数都来自看板回传。
- 不删除旧页面中央的任一能力；旧 URL 直达仍按既有 `tech-embed.js` 汇聚到统一工作台。

## 6. 验收标准

1. 点击“零件清单 / 待澄清问题 / 解析报告 / 解析视图 / 版本与校核 / 任务文件”，请求分别落在 `navigate-view: parts / questions / report / evidence / review / files`。
2. 父壳不再存在业务抽屉：`agent-chat.js` 中不存在 `ocDrawer`、`DRAWER_GROUPS`、`openDrawer`，也不存在 `.click()`、`contentDocument`。
3. 六个视图名在看板侧全部注册，且每个视图有真实的 `run` 实现（不是空实现或占位返回）。
4. 导航成功时左侧入口状态与右侧 `view.active` 一致；导航失败时父壳显示真实错误并可重试。
5. 左侧会话不因导航被清空、重建或覆盖；右侧看板内部返回上一层的操作不关闭父壳。
6. 批次 0–1 后端守护测试与全量测试不回归；未新增、删除或替代任何后端接口与历史数据。

## 7. 不在本步范围

- 零件清单 → 零件详情 → 工艺 → 成本的看板内部层级（第 6 步）；
- 2.1 Agent 解析与修改闭环的工具接线（第 7 步）；
- 2.2 / 2.3 / 3.x 的导航目标与工具（第 8–11 步）；
- 九阶段左侧快捷按钮统一、模型配置统一与全流程回归（第 12–14 步）。
