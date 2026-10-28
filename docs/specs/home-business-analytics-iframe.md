# 首页经营分析智能体

2026-10-28：用户要求在现有首页新增经营分析 tab，提交提示词后通过 iframe 嵌入指定页面，保持体验一致。本次沿用用户对 `feat/poc-1030` 的分支授权及直接实现授权。

## 验收范围

- 可见入口依次为报价智能体、技术工艺智能体、经营分析智能体；配置 / 规则继续隐藏。
- 经营分析沿用 FUXI 品牌、输入框、tab 激活态和 URL `assistant=analytics`；所有模式按钮统一为向上箭头、无障碍名称为“发送需求”，输入为空不加载 iframe。
- 点击 tab 只切换模式，不提前请求分析端。点击统一箭头发送按钮 / Enter 才加载 iframe，不弹报价项目信息框、不查 CPQ 历史、不创建项目。
- 使用 `http://pdt-dev.eimos.com/api/ontology-agentic-analytics`；通过 URLSearchParams 的 `prompt` 字段完整传递中文、换行、&、#、? 等正文；使用已确认支持的 `theme=light&layout=single`。
- 按最新全屏体验要求：提交后 iframe 铺满浏览器内容区，隐藏首页导航、品牌、输入区和三个 tab；无外层圆角卡片 / 大段说明。顶部只保留 44px 轻量栏，包含“返回首页”、当前智能体名称和“更多”入口。不是浏览器原生 Fullscreen API，无需额外全屏权限。
- “返回首页”恢复原首页与当前分析模式，保留输入正文、iframe 及其会话，不重新请求目标端。首页提供“继续上次分析”入口，直接恢复全屏且不重载。
- 加载 / 超时提示不挤占 iframe 高度；load 事件后收起提示，不显示开发层面的持久说明。重新加载、独立打开、需求查看与复制收进“更多”，键盘可访问。
- 全屏时锁定宿主页面滚动，返回时解除；窄屏 iframe 使用剩余视口高度，不受原 480px 最小高度约束。
- iframe 提供 title、加载提示、15 秒超时提示、重试和独立打开；onload 只说明页面加载事件发生，不宣称分析成功。
- 根据用户 10-28 后续指令，首页所有模式统一隐藏下方列表区（包括列表 tab、查询、新建和分页）；保留代码、后端和历史数据。切到报价 / 技术工艺恢复其上传和发送；切回分析保留当前 iframe，重复点击不重载；编辑后再分析更新 URL。
- 经营分析仅发送文本，隐藏原附件入口，不能把上个助手的文件误发给分析端。
- 不修改后端、远程分析服务、认证、项目数据和部署设置。

## 外部接口事实与限制

已只读核对目标页面和 `workbench.js?v=162`：HTTP 200；当前响应头未发现 X-Frame-Options 或 CSP frame-ancestors；脚本支持 theme / layout，未发现读取提示词 query 的逻辑。用户已明确确认使用 `prompt` 参数；本地不得宣称目标端已自动回填或执行。保留可查看 / 复制的原始正文和独立打开入口。跨源嵌入无法读取目标 DOM，也不能可靠识别其业务错误。

实现前实际运行 3 项红测，均按预期失败（缺少第三个 tab、iframe 接入与控制模块）。实现后补充真实 submitRequirement 分流验证，4 项经营分析测试通过；外部浏览器嵌入、认证与自动分析未做验收。

后续统一命名 / 隐藏列表 / 统一按钮需求：修改契约后先实际运行，7 项中 3 项按预期失败，覆盖旧名称和经营分析专用按钮；实现后上述综合 45 项再次全部通过，包含报价 / 工艺 / 分析往返时列表始终隐藏及统一箭头按钮。

全屏体验改造：先实际运行新增契约，5 项中 2 项按预期失败（缺少全屏布局、宿主导航仍展示）。实现后综合 46 项通过，覆盖隐藏 / 恢复宿主导航与 tab、滚动锁定 / 解除、加载提示收起、返回正文保留、继续分析不重新赋值 iframe src。未进行浏览器视觉验收。

综合复验命令：`python -m unittest tests.test_home_business_analytics_iframe_red tests.test_home_fuxi_quote_tech_prefill_red tests.test_tech_home_quote_shell_red tests.test_tech_home_three_tabs_and_todo_tasks_red tests.test_tech_home_project_cards_and_agent_history_red tests.test_home_cards_equal_height_red -v`，45 项全部通过。内联脚本和 home-analytics.js 经 Node vm.Script 语法检查通过，git diff --check 通过。

## 测试

先运行新增红测，记录实现前的实际缺口，再实现并复验。Node 隔离测试 URL 编码、空白拦截、加载 / 超时 / 重试、返回编辑和 tab 恢复；静态检查第三个可见 tab、iframe 和发送分流。复跑原首页相关契约、脚本语法与 diff 检查。浏览器视觉和目标端自动接收提示词另列验收，不以本地契约测试替代。
