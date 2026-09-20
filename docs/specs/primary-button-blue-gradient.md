# CPQ 主操作按钮同色系渐变 Spec

状态：TDD Red，等待 DeepSeek 实现。

## 背景

品牌主色已经统一为 `#0067D1`，但部分报价与技术工艺工作台按钮仍使用单一纯色背景，层次感弱。本需求在不改变品牌主色、不引入紫色或竞争蓝的前提下，用同色系渐变恢复主操作按钮的视觉层次。

本需求是 `quote-agent-emphasis-hover.md` 的增量视觉修订：`强行填满本步骤` 和 `确认，进入下一步` 仍保持“常态浅色描边、hover 深色底白字”的状态语义，但常态浅背景及 hover 深背景均改为同色系渐变。

## 渐变 Token

报价智能体和技术工艺工作台必须具备等价的三类渐变：

| 用途 | 渐变 |
| --- | --- |
| 填充主按钮常态 | `linear-gradient(135deg, #0067D1 0%, #0057B8 100%)` |
| 填充主按钮 hover | `linear-gradient(135deg, #0057B8 0%, #004A9F 100%)` |
| 描边按钮常态 | `linear-gradient(135deg, #FFFFFF 0%, #F4F9FE 100%)` |

允许通过 CSS 变量复用，推荐命名：

- `--gradient-primary`；
- `--gradient-primary-hover`；
- `--gradient-primary-soft`。

大小写、空格和百分比写法可以不同，但端点、方向和状态语义必须等价。

## 覆盖范围

### 报价智能体

- `#qaFillStep`；
- `#btnNext`；
- 既有填充型 `.btn-primary` 和 `.quick-action-btn.primary` 应复用主按钮渐变，精确覆盖规则仍优先保证上述两个按钮处于描边常态；
- 两个指定按钮常态使用柔和白到浅蓝渐变、品牌蓝文字、既有 1px 品牌边框；
- 两个指定按钮 hover（且非 disabled）使用深蓝渐变、白字、深蓝边框。

### 技术工艺统一工作台

- `.tech-wb-btn.primary` 默认常态使用 `#0067D1 → #0057B8`，hover 使用 `#0057B8 → #004A9F`；
- 后续五大流程导航需求要求 `#techNext` 精确覆盖默认主按钮样式：常态使用白到浅蓝的 `--gradient-primary-soft`、品牌蓝文字和浅蓝边框，hover 才使用 `--gradient-primary-hover` 深蓝渐变白字；`#techPrimary` 继续使用填充型主按钮渐变；
- disabled 状态不得被 hover 覆盖。

## 视觉和工程约束

- `--color-primary` 必须继续是 `#0067D1`，不得以本需求为由更换品牌主色；
- 不使用紫色、青色、黑色或新的竞争蓝作为渐变端点；
- 渐变仅用于主操作按钮背景，不应用到危险、驳回、删除、成功、警告按钮；
- 不给所有普通按钮统一套渐变，次要按钮继续保持描边/中性背景；
- 不通过图片、SVG、canvas 或 JavaScript 动态绘制渐变；
- 不改变按钮尺寸、文案、id、点击处理、禁用判断、业务优先级和布局；
- 顶部 `AI` 徽标和当前步骤数字不是本次“按钮渐变”目标，继续遵守其现有状态设计。

## 修改范围

- `确认需求解析结果.html` 中相关主题 token 与按钮 CSS；
- `tech_app/frontend/tech-workbench.css` 中相关主题 token 与主按钮 CSS；
- 不需要修改 HTML 结构或 JavaScript。

## 验收标准

1. 报价与技术工艺均定义主按钮、hover 和柔和描边背景三类同色系渐变。
2. `#qaFillStep`、`#btnNext` 常态为白到浅蓝渐变、蓝字和 1px 边框；hover 为深蓝渐变白字。
3. `.tech-wb-btn.primary` 常态与 hover 分别使用规定的两级蓝色渐变。
4. 主色仍为 `#0067D1`，没有引入紫色或其他品牌色。
5. disabled、危险、成功、警告和次要按钮语义不变。
6. 不改变任何按钮行为或后端逻辑。
7. 新增 Red 测试转绿，已有测试继续通过，`git diff --check` 通过。

## 授权边界

本需求不授权提交、push、MR、merge、tag、Release、部署、服务启动或浏览器操作。
