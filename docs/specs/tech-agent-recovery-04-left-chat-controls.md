# 技术工艺 Agent 能力恢复 4：恢复 2.1 左侧会话基础控件 Spec

## 1. 范围与前置条件

本 Spec 是技术工艺 Agent 能力恢复的第 4 步。实现时假定第 0–3 步已经完成，即父壳与右侧看板已有标准桥协议和语义化动作注册表。

本步只恢复统一父壳左侧 `#techChatPane` 中被迁移遗漏的入口和结果按钮容器，不实现第 5 步的具体看板导航，不迁移零件清单、零件详情或其他业务内容。

## 2. 恢复的基础控件

### 2.1 输入区“＋”入口

在左侧 Agent 输入框前恢复 `#ocPlus` 按钮：

- `type="button"`；
- 有可访问名称和 `aria-expanded`；
- 打开 `#ocCapabilityMenu`；
- 菜单属于左侧会话 DOM，不创建业务详情弹层；
- 再次点击、按 Escape 或点击菜单外部可关闭；
- 关闭后焦点回到 `#ocPlus`。

菜单只恢复原 2.1 的四个入口：

- `data-tech-capability="upload"`：补充需求图纸；
- `data-tech-capability="evidence"`：解析视图；
- `data-tech-capability="import3d"`：导入已有 3D 模型；
- `data-tech-capability="review"`：版本与校核审查。

本步只要求入口、标签、可访问性和统一分派点存在；具体导航和右侧视图切换由第 5 步实现。

### 2.2 解析结果快捷按钮

在左侧消息线程中恢复唯一且持久的 `#ocResultActions`，包含：

- `#ocPartsAction`：零件清单，带动态数量；
- `#ocQuestionsAction`：待澄清问题，带动态数量与警示语义；
- `#ocReportAction`：解析报告。

要求：

- 尚无解析结果时整组隐藏，但节点不能从 DOM 删除；
- 有解析结果时显示在最新一轮消息之后；
- 新消息、刷新结果或重复渲染只移动同一组节点，不复制按钮；
- 数量来自右侧看板通过第 0–3 步协议返回的状态快照，父壳不得读取 iframe DOM 计数；
- 按钮只发出语义化 capability/result 事件，本步不规定第 5 步的目标视图实现。

### 2.3 任务文件入口

恢复 `#ocFilesAction`：

- 位于左侧会话可见区域；
- 显示任务文件数量 `#ocFilesCount`；
- 数量来自桥协议状态，不请求或复制第二份文件数据；
- 只作为右侧任务文件视图的语义入口，不在父壳渲染文件清单。

### 2.4 执行进度区域

保留或恢复 `#ocTaskProgressHost`，用于把右侧业务任务的状态显示在会话流中：

- 支持 queued/running/succeeded/failed；
- 每个任务以 taskId 去重；
- 进度只增量追加，不因轮询覆盖中间步骤；
- 失败信息可见，不显示 API Key 或敏感请求头；
- 本步只建立宿主和状态渲染入口，不改后端任务实现。

## 3. 页面所有权

父壳只恢复“入口”和“摘要”，不得把下列业务 DOM 从右侧看板搬入父层：

- `#secUpload`、`#secEvidence`、`#secImport3d`；
- `#secParts`、`#secQuestions`；
- `#tree`、`#partDetail`、`#viewer`；
- `#analysisPanel`、`#analysisHost`；
- 版本、型号核验、校验修正正文；
- 文件清单正文。

禁止在父壳新增零件 Drawer、零件详情 Modal、3D Modal 或覆盖右侧看板的业务浮层。点击零件后在看板内部展开属于第 6 步，不在本步实现。

## 4. 展示范围

- 上述 2.1 专属能力入口只在 `stage=drawing` 时显示或可用；切到其他阶段时隐藏或禁用，不能冒充其他步骤能力。
- `#ocPlus` 可以保留为后续步骤扩展的统一入口，但本步四个菜单项只在 drawing 阶段出现。
- 切换阶段、刷新父壳或恢复历史项目不得复制控件。
- 不清空已有 Agent 消息、输入草稿、滚动位置或项目绑定。

## 5. 可访问性与交互

- 所有入口使用原生 button，具备 `type="button"` 和明确的 `aria-label`/可见文字。
- 菜单有 `role="menu"`，菜单项有 `role="menuitem"`。
- 结果容器使用 `aria-label="图纸解析结果"`。
- 动态数量和进度更新使用适当的 `aria-live`，不能抢占输入焦点。
- hover、focus-visible、disabled 状态清楚，沿用当前品牌色和浅色选中规则。

## 6. 数据与实现限制

- 不修改或删除任何后端接口、service、store、Agent 工具及历史数据。
- 不恢复旧的子页面第二会话栏；`embed=1` 仍只有父壳一个 Agent 会话。
- 不把旧 `index.html` 的整个 Drawer 复制进父壳。
- 不通过 `iframe.contentDocument`、CSS selector 或跨层 `.click()` 获取数量或执行入口。
- 不新建第二套附件、文件、零件或报告数据状态。
- 本步不得提前实现零件列表/详情在右侧看板中的导航逻辑；只保留统一分派接口供第 5 步承接。

## 7. 验收标准

1. 父会话栏具有唯一 `#ocPlus`、`#ocCapabilityMenu`、`#ocResultActions`、`#ocFilesAction` 和 `#ocTaskProgressHost`。
2. 四个原 2.1 能力入口恢复且仅在 drawing 上下文出现。
3. 零件清单、待澄清问题、解析报告三个结果按钮恢复，数量由桥状态更新。
4. 任务文件入口和数量恢复，但父壳不包含文件正文。
5. 父壳不包含任何被禁止的业务详情 DOM，也不创建业务 Drawer/Modal。
6. 重复刷新和消息追加不会复制控件。
7. 本步不减少后端能力，不改业务数据和历史数据。

