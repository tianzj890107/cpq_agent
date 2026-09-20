# 技术工艺五大流程导航 Spec

状态：TDD Red，等待 DeepSeek 实现。

## 目标

技术工艺统一工作台顶部不再展示九个内部 stage，也不再显示右上角“当前：1 · 接受工艺评估需求”。顶部流程导航改为与报价智能体一致的单层大步骤，只显示五个用户可理解的大流程；1.2、1.3、3.2、3.3 等内部 stage 继续存在并正常流转，只是不在顶部拆成独立步骤。

## 五个可见大流程

顶部只允许按以下顺序显示五项：

1. `1 创建工艺评估需求`
2. `2 图纸解析`
3. `3 工艺方案/组装整合`
4. `4 成本测算`
5. `5 输出工艺评估结果`

不得再显示 `1.1 创建`、`1.2 确认`、`1.3 审核`、`2.1`、`2.2`、`2.3`、`3.1`、`3.2`、`3.3` 等小步骤按钮，也不显示阶段标题分组、胶囊组或组间箭头。

## 大流程与内部 stage 映射

内部九阶段继续作为业务状态和 URL 事实源：

| 可见大流程 | 内部 stage |
| --- | --- |
| 1 创建工艺评估需求 | `requirement-create`、`requirement-confirm`、`requirement-review` |
| 2 图纸解析 | `drawing` |
| 3 工艺方案/组装整合 | `process` |
| 4 成本测算 | `cost` |
| 5 输出工艺评估结果 | `summary`、`report-review`、`report-publish` |

约束：

- `STAGES` 中九个 stage、页面映射和顺序不得删除或改名；
- 在 1.1、1.2、1.3 任一内部页面时，顶部高亮大流程 1；
- 在 3.1、3.2、3.3 任一内部页面时，顶部高亮大流程 5；
- 大流程完成态必须由其内部 stage 的真实完成状态聚合，不能因用户点击顶部步骤而冒充完成；
- 底部上一步/下一步继续按九个内部 stage 顺序推进，因此 1.1 的下一步仍是 1.2、1.2 的下一步仍是 1.3，3.1/3.2/3.3 同理；
- 点击顶部大流程时进入该大流程的入口 stage：1→`requirement-create`、2→`drawing`、3→`process`、4→`cost`、5→`summary`，同时继续遵守无项目保护和现有权限/业务门禁。

## 删除冗余“当前阶段”信息

- 从 `tech-workbench.html` 删除 `#techPhaseLabel`；
- 从 `tech-workbench.js` 删除对 `techPhaseLabel` 的读写和 `当前：...` 文案生成；
- 不以其他 id 或伪元素重新显示同义文案；
- 底栏 `#techNowLabel` 可以继续显示当前内部步骤，帮助用户理解上一步/下一步会流向哪里。

## 右上角流程与模型

右侧项目标题栏最右侧保持与报价智能体相同的信息结构：

- 状态点；
- `技术工艺流程`；
- 紧邻其后的 `#techModelInfo`。

模型必须来自现有 Agent meta/设置运行数据，显示形式与报价一致，例如 `· qwen3.5-plus`；不得写死模型，不得把模型移到进度栏或“当前阶段”位置。

## 下一步按钮状态

`#techNext` 对齐报价智能体 `#btnNext`：

### 常态

- 使用 `var(--gradient-primary-soft)` 白到浅蓝渐变；
- 品牌蓝文字；
- `1px solid var(--color-primary-border)` 或技术壳中的等价品牌浅蓝边框；
- 常态不得为深蓝实心填充。

### hover（仅非 disabled）

- 使用 `var(--gradient-primary-hover)` 深蓝同色系渐变；
- 白色文字；
- 深蓝边框；
- disabled 时不得出现 hover 选中态。

`#techPrimary` 是当前内部页面的业务主操作代理，不等于“下一步”，可以继续使用填充型主按钮渐变；不得为了修改 `#techNext` 而降低 `#techPrimary` 的操作层级。

## 修改范围

允许修改：

- `tech_app/frontend/tech-workbench.html`；
- `tech_app/frontend/tech-workbench.js`；
- `tech_app/frontend/tech-workbench.css`；
- `docs/specs/primary-button-blue-gradient.md` 中 `#techNext` 的后续覆盖口径；
- `tests/test_primary_button_blue_gradient_red.py` 仅在需求方维护阶段调整，DeepSeek 实现时不得修改测试。

## 禁止事项

- 不修改后端 API、数据库、权限、审批、成本公式或任务流；
- 不删除、合并或重命名九个内部 stage；
- 不跳过 1.2、1.3、3.2、3.3；
- 不改变 URL、刷新恢复、浏览器前进后退和 postMessage 同源白名单；
- 不写死模型名称；
- 不修改左侧 Agent 会话、导航、历史数据或 iframe 子页面业务实现；
- 不执行提交、push、MR、merge、tag、Release、部署或服务重启。

## 验收标准

1. 顶部只有五个指定大流程，顺序和文案准确。
2. 九个内部 stage 全部保留，隐藏小步骤仍通过底部按钮逐一流转。
3. 1.1/1.2/1.3 聚合到大流程 1，3.1/3.2/3.3 聚合到大流程 5。
4. `techPhaseLabel` 和“当前：...”从顶部彻底移除。
5. “技术工艺流程”后紧邻真实运行模型。
6. `#techNext` 常态浅色描边渐变，hover 深蓝渐变白字，disabled 不响应 hover。
7. `#techPrimary` 及内部业务动作不受影响。
8. 新增 Red 测试转绿，既有测试保持通过，`node --check` 和 `git diff --check` 通过。
