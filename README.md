# 配置报价 CPQ · 报价管理

EIMOS 产品平台「**配置报价管理(CPQ) → 报价管理**」菜单的新内容,以及配套开发文件。

## 内容

| 文件 | 说明 |
| --- | --- |
| `报价首页(1).html` | **当前首页**(报价管理主页面,serve.py 根路径 `/` 返回此页)。顶部三个标签 = 三种助手模式(**报价助手 / 配置助手 / 规则助手**),点击切换引导语、占位符与「新建」按钮。发送需求时先做**意图识别**(大模型 `/api/intent`,失败退回关键词)分辨目标,再跳转对应页面:报价→`确认需求解析结果.html`、产品配置→`XBOM智能体-配置BOM生成.html`、规则→(暂未上线)。**新建报价 / 对话框发送前会先弹「项目信息确认」框**(填 项目名称/项目编码/客户名称 + **需求描述文本框 + 上传文档按钮**),点「确认并进入」才跳转。
**门槛**:项目名称/客户名称必填;**若既没上传文档也没输入需求描述,会提醒"请上传需求文档或输入需求描述",不放行;一旦有文档或有需求文本即直接开始报价。**这些值经 sessionStorage(`cpq:projectName`/`cpq:projectCode`/`cpq:customer`)带到工作台——工作台顶部「当前项目」显示该项目名(不再写死波兰项目),并作为权威值填入第 1 步测算基本信息的 客户/项目名称。需求文本+附件名经 sessionStorage(`cpq:requirement`/`cpq:files`)带到下一页。左侧导航「历史记录」「设置」**与工作台一致**(同一 Agent 服务:`/api/sessions`、`/api/models`、`/api/settings`);首页点某条历史 → 经 sessionStorage `cpq:openSession` 跳到工作台自动载入该会话。 |
| `报价首页.html` | 旧版首页(保留备用)。 |
| `确认需求解析结果.html` | **报价助手工作台**(承接首页跳转)。左侧与报价助手 Agent 对话,右侧步骤条 + 固定表单/表格由 Agent 通过 `cpq_ui` 工具驱动;按「亿纬锂能POC(简化)」7 步引导。**右侧所有分区可点击编辑**(含系统计算项);**进度条各步可点击**回看/修改已完成步骤(改后点「保存修改并重算」回传 Agent);左侧对话框上方有**快捷动作**按钮(确认进入下一步 / 确认并填入推荐 / 上一步);已去掉子步骤高光。 |
| `XBOM智能体-配置BOM生成.html` | **配置助手工作台**(承接首页「配置助手」跳转)。中间聊天(第 6 步起对话框缩窄)+ 「配置BOM生成过程」6 步卡,右侧是放大的**可编辑配置BOM表**:每行末带一个**「配置规则」下拉**(选项池=`md_clm_distribution_rule`,AI 给每行填推荐规则作默认选中,用户可改/选「(无)」)。参数在第 1 步识别、静默保存**不显示**。由 `xbom_agent_server.py` 通过 `xbom_ui` 驱动(`render_rules`=候选池、`render_bom`=行含 `规则` 字段、`render_params`=静默存);「确认配置BOM」回传 `{参数,配置BOM,每行规则}`,确认后出**「导入数据库」**按钮(POST `/api/import` → 写 `xbom_config_bom`/`xbom_config_bom_line`)。 |
| `xbom_agent_server.py` | **配置助手 Agent 服务**(端口 47294)。复用 `open-claude/` 引擎,注入 `xbom_ui`(步骤卡/配置BOM表)+ `sql_query`(只读查库);系统提示词=《配置助手.xlsx·智能体配置流程》的 6 步(识别参数→识别可配置模块→物料归集→提取配置规则→生成配置BOM→确认配置BOM)。第 1–5 步自动跑并在聊天给「思考/规划/执行/结果」,第 6 步起交互:右侧先 `render_rules`(**只查 `md_clm_distribution_rule` 产品配单规则**做每行下拉候选池)→ 再 `render_bom`(每行 `规则` 字段填该行推荐规则);参数 `render_params` 静默存不显示。接口同 cpq(send/new/meta/models/settings/extract/sessions)**加 `/api/import`**(配置BOM+参数入库到 `xbom_config_bom`/`xbom_config_bom_line`,自动建表,不污染样例/生产表)。 |
| `规则助手-规则配置.html` | **规则助手工作台**(承接首页「规则助手」跳转,布局参考 `报价规则.html`:左 520px 聊天 + 右侧宽表)。右侧是**一张规则表**(列 复选框/规则ID/规则名称/规则描述/Groovy规则公式,行可编辑,Groovy 用可编辑 code-display),底部动作条 批量删除 / 公式校验 / **生成 Groovy 公式** / **导入数据库**(绿色)。两步合到这张表:①上传规则文档→`rule_result(stage="import")` 把每条规则录入行(自动编 R001…、名称、描述、目标库存 `data-target`);②点「生成 Groovy 公式」→后端 `sql_query` 查库参数 + 各行描述→`rule_result(stage="groovy")` 按 rule_name 回填最后一列。全部生成后点**「导入数据库」**(POST `/api/import` → 写独立 `rule_agent_rules` 表)。由 `rule_agent_server.py` 驱动。 |
| `rule_agent_server.py` | **规则助手 Agent 服务**(端口 47296)。复用 `open-claude/` 引擎,注入 `sql_query`(只读查库)+ `rule_result`(stage=import/groovy/final;import 只需 rule_name,可无公式)。系统提示词=**严格两步**:第一步只忠实抽取文档规则填表(不查库、不生成 Groovy),第二步才查库拿参数依据生成 Groovy(风格贴近库里 rule_expression,变量用业务字段名,db_evidence 注明来源)。接口同 cpq(send/new/meta/models/settings/extract/sessions)+ `/api/convert` + **`/api/import`**(=`_import_rules`,把右侧规则表写入独立 `rule_agent_rules` 表:batch_id/rule_id/rule_name/rule_desc/target_table/groovy_formula/created_at,自动建表、跳过全空行、**不污染业务规则库表**)。历史 `rule_history/`、设置 `rule_settings.json`。 |
| `cpq_agent_server.py` | **报价助手 Agent 服务**(端口 47292)。复用 `open-claude/` 引擎,注入 `cpq_ui`(工作台)+ `sql_query`(只读查库)两个工具,系统提示词编码 7 步报价流程;SSE 接口 `/api/send`、`/api/new`、`/api/meta`,历史接口 `/api/sessions`、`/api/session`、`/api/session/open`、`/api/session/delete`,设置接口 `/api/models`、`/api/settings`,首页**意图识别** `/api/intent`(一次轻量大模型调用,返回 quote/config/rule),**附件提取** `/api/extract`(PDF/Word/Excel → 文字)(含 CORS)。 |
| `database/亿纬锂能_da.sqlite` | **当前知识库**(SQLite,报价+配置助手共用):BOM 头/行(CLM_BASE_INFO/CLM_LINE_INFO)、20 份变体样例BOM(sample_power_bom_orders/lines)、定价/加价因子(md_clm_pricing_factor/md_clm_pricing_surcharge_factor)、规则(md_clm_material_price_rule/md_clm_distribution_rule)、字段清单(quote_/config_/rule_assistant_fields)。**Schema 见 `database/数据库Schema说明.md`**;两个 Agent 启动时把库表结构拼进系统提示词,**以 schema 为上下文自行生成 SQL、用 `sql_query` 只读查询**取数。 |
| `quote_bom.db` | 旧知识库,**已弃用**(数据已迁入 `database/亿纬锂能_da.sqlite`),保留仅作参考。 |
| `cpq_history/` | **本地历史报价**(运行时生成,`.gitignore` 忽略)。每个报价会话一个 JSON,含展示事件流(用于回放聊天+工作台)与原始消息(用于续聊)。**存磁盘,重启服务后仍可在页面「历史记录」里找回并继续。** |
| `cpq_settings.json` | **本地模型设置**(运行时生成,`.gitignore` 忽略,**可能含各 provider 的 API Key 明文,勿入库**)。保存当前模型、采样参数(temperature/max_tokens/thinking)与各 provider 的 API Key;服务启动时自动加载。 |
| `cpq_data/*.json` | 早期模拟数据,**已弃用**(现改为查 `quote_bom.db`),保留仅作参考。 |
| `open-claude/` | open-claude 引擎副本(Agent 运行时,未修改)。 |
| `报价业务流程.xlsx` | 报价业务流程说明(需求资料)。 |
| `cpq_suite_server.py` | **一体化服务(推荐)**:单端口(默认 8010)同时提供**静态前端 + 三个智能体 API**。原样 import 三个 agent 模块并各建 Bridge,把 `/agents/quote|config|rule/api/*` 前缀剥掉后直接交给对应模块的 Handler(SSE/历史/设置/导入数据库全部复用原实现,各自的 settings/history 文件不变);其余路径按 serve.py 逻辑发静态文件(`/`→报价首页),并**拒绝下载** settings(API Key)/history/database/.py 等敏感文件。EIMOS iframe 地址不变。 |
| `serve.py` | 纯静态服务(旧,已被 cpq_suite_server.py 取代,保留可单独用):根路径 `/` 即返回 `报价首页(1).html`,供 EIMOS iframe 内嵌。 |

## 启动

**方式一(推荐)——一体化服务,一条命令全起**:

```bash
cd 配置报价CPQ
open-claude/.venv/Scripts/python cpq_suite_server.py            # 默认端口 8010
# 或指定端口: open-claude/.venv/Scripts/python cpq_suite_server.py --port 8020
```

- 首页 `http://127.0.0.1:8010/`(EIMOS iframe 地址不变);三个智能体 API 在同端口
  `/agents/quote/api/*`(报价)、`/agents/config/api/*`(配置)、`/agents/rule/api/*`(规则)。
- 页面默认**同源**访问这些前缀;`localStorage['cpq:agentUrl'/'xbom:agentUrl'/'cpq:ruleAgentUrl']` 仍可覆盖成任意地址。
- 必须用 `open-claude/.venv` 里的 Python(依赖 anthropic/openai/pdfplumber 等都在这个 venv)。

**方式二——四个进程分开跑(旧,仍可用,页面用 file:// 打开时也走这些端口)**:

```bash
cd 配置报价CPQ
python serve.py                 # 静态页面,默认端口 8010 -> http://127.0.0.1:8010/
python cpq_agent_server.py      # 报价助手 Agent,默认端口 47292(需 API Key)
python xbom_agent_server.py     # 配置助手 Agent,默认端口 47294
python rule_agent_server.py     # 规则助手 Agent,默认端口 47296
```

注意:分开跑时页面是从 serve.py(8010) 加载的、默认仍连同源 `/agents/*`,需要设置
`localStorage['cpq:agentUrl']='http://127.0.0.1:47292'` 等覆盖(或直接改用方式一)。

## 版本管理(git)

本目录已是独立 git 仓库(main 分支)。`.gitignore` 已排除:各助手 settings(**含 API Key 明文,勿入库**)、
history 会话、`open-claude/`(它是独立仓库 <https://github.com/tianzj890107/open-claude> agentic 分支,
克隆本仓库后需自行放到 `open-claude/` 并在其中建 `.venv`)、SQLite WAL 临时文件。

## 附件识别(PDF / Word / Excel)

上传的需求文档会**在后端提取文字**后再喂给 Agent(浏览器只能直接读纯文本):

- **首页**上传 / **工作台**对话框回形针上传,都支持 `.pdf` `.docx` `.xlsx` 及 `.txt/.md/.csv/.json`。
- 文本类由浏览器直接读;`.pdf/.docx/.xlsx` 转 base64 发到 `POST /api/extract`,后端用
  **pdfplumber / python-docx / openpyxl** 提取文字(含表格),截断上限 12 万字。
- 首页上传的文件,其提取文字经 sessionStorage `cpq:fileContents` 带到工作台,并入首轮消息;
  工作台内上传则直接并入当条消息。旧版 `.doc/.xls` 提示另存为 `.docx/.xlsx`;图片暂不支持提取。
- 依赖:`pip install pdfplumber python-docx openpyxl`(缺库时接口返回友好错误,不影响其它功能)。

## 用 Qwen / DeepSeek 等非 Anthropic 模型

- **必须在运行本服务的 Python 环境里装 `openai` 包**:`pip install openai`(Qwen/DeepSeek 走 OpenAI 兼容协议)。
  否则一切换到这些模型就会报「The 'openai' package is required」。若用 venv,注意装进那个 venv。
- 在页面「设置」里**选中 Qwen/DeepSeek 模型 + 填对应 API Key(如 DashScope/百炼)并保存**;主对话与首页意图识别都会随之切到该模型。
- 若**只配了非 Anthropic 的 Key、没显式选模型**,服务启动会自动改用那个已配 Key 的模型(而不是默认 Claude),避免一发送就 401/403。
- 若 Anthropic Key 被禁用会收到 `403 Request not allowed`——那是因为当前模型仍是 Claude,请按上面切到你有 Key 的模型。意图识别失败会自动回退关键词匹配、不阻塞跳转。

## 数据来源(两端)

Agent 每步结论都基于两端数据,并在对话里向用户说明依据:

- **需求文档端** —— 用户上传的需求文档 / 对话框描述(客户、产品型号、数量、目的地、交期、
  付款、质量专控、碳足迹、非标要求、贸易术语等具体值)。
- **数据库端** —— `database/亿纬锂能_da.sqlite`(见上表)。Agent 用 `sql_query` 工具执行**只读 SELECT**
  (仅允许单条 SELECT/WITH/PRAGMA,写操作被拒),**以系统提示词末尾的完整 schema 为上下文生成 SQL**;字段口径查 `quote_assistant_fields`,
  BOM 查 `CLM_BASE_INFO/CLM_LINE_INFO`(多层用递归 CTE),加价/定价查 `md_clm_pricing_*`,规则查 `md_clm_material_price_rule/md_clm_distribution_rule`。**不再读任何 json / quote_bom.db**。

## 报价助手 Agent(7 步流程)

**步骤名严格对应 xlsx「亿纬锂能POC(简化)」页的「输出 ↔ Agent 步骤名称」**(跳过“客户需求解析”那行——它没有
Agent 步骤名、是系统解析初稿,不算 Agent 步骤;所以 Agent 第 1 步就是“确认需求配置”)。**从第 1 步开始、
一步一步走,用户对第 1 步点「确认,进入下一步」前不许跳到定价**(系统提示词强约束)。

**写死的表结构 + 预渲染(不再实时造结构)**:每个分区的字段/列**写死**,来源 = 数据库 `quote_assistant_fields`
(不读 excel、不带任何示例数据)。后端把固定表单目录经 `/api/meta` 的 `forms` 下发,**前端进第 1 步就把 6 个
固定分区的空骨架预渲染出来**(`prerenderStep`);Agent **只往固定分区填值**(render_form 用 `values`、render_table 用 `rows`),
`_enforce_fixed_template` 兜底强制套结构、丢弃多余字段。分区目录:
第1步 s1_basic/s1_dest/s1_products/s1_techparams/s1_payment/s1_logistics(6 个,已去掉交期分解/备品备件,
字段取自 `quote_assistant_fields` 业务对象=价格测算单)、第4步 s4_detail/s4_order_sum/s4_prod_sum、
第5步 s5_deviation/s5_order_sum、第6步 s6_basic(报价单);计算类分区(第2步 s2_bom_<产品编码>/s2_cost、
第3步 s3_pricing、第6步 s6_detail、第7步 s7_bpm)列在代码里写死。

各步字段口径查 `quote_assistant_fields`:

1. **确认需求配置** —— 从需求文档解析客户具体值,查 `quote_assistant_fields`(价格测算单)拿字段口径,
   渲染 6 个分区(测算基本信息/目的地/产品列表/技术参数/付款里程碑/物流);**整步一次推荐**:进第 1 步就把 6 个分区一次性填好。
2. **定价-基础成本** —— 按技术参数匹配 BOM 与料工费,展示 BOM 清单和基础成本(5.6 / 5.8 元/W)。
   BOM 头/行在 `CLM_BASE_INFO`/`CLM_LINE_INFO`(`ref_bom_header_id`),多层用**递归 CTE**展开(见系统提示词)。
   BOM/料工费/定价/加价等结果**必须 render 到右侧工作台,聊天里不贴表格**(保证左右一致)。
3. **定价-利润加成** —— 定价 = 基础成本 + 技术溢价(+1.5) + 市场调节(-0.2) → 6.9 / 7.1。
4. **报价-其他加价项** —— 基础/非标/财务商务/物流/其他五类加价明细与汇总(旧名"定价-其他加价项",按更新后的 POC 页改名)。
5. **报价测算复核** —— 价格偏差视图(报价、EXW、建议报价、指导价、价格底线、BG底价、偏差/偏差额/偏差率)。
6. **报价方案** —— 生成报价单(单号/类型/模板/报价形式/报价明细/折扣)。
7. **输出报价单** —— 渲染完整报价单文档 + BPM 审批流环节。**最后一步底部有「生成报价单(Word)」按钮**:
   前端收集各步表单/表格数据 POST `/api/export/docx`，后端用 python-docx 拼成 .docx 下载。
   （第 4 步「报价-其他加价项」已去掉「整单加价汇总」表，只保留 加价明细 + 产品加价汇总。）

交互协议:页面在「确认,进入下一步」时把右侧工作台最新数据以 `【表单确认】…{JSON}` 回传,
Agent 以回传数据为准重算并推进;用户改数会触发受影响分区重渲染。

## 历史记录与设置(工作台左侧导航)

- **历史记录**(`ti-history` 图标)—— 打开左侧「历史报价」抽屉。每次对话过程(聊天气泡、
  操作轨迹、工作台分区)都以**展示事件流**的形式随对话增量写入 `cpq_history/<id>.json`,
  **存磁盘,重启服务后仍在**。点某条历史即 `POST /api/session/open`:后端把该会话装载为当前
  会话(载回原始消息可继续对话),前端用事件流重建聊天与右侧工作台;可删除单条历史。
  「新建报价」清掉首页带来的旧需求并开一张空白单。
- **设置**(`ti-settings` 图标)—— 模型 / 采样参数 / API Key。
  - **模型**:下拉按 provider 分组,已内置 **Qwen(通义千问 DashScope)** 与 **DeepSeek**,
    以及 Claude 各型号(未配置 Key 的会标「未配置 Key」)。非 Anthropic 模型经
    `open-claude` 的 OpenAI 兼容适配器走通,`cpq_ui`/`sql_query` 工具同样可用。
  - **API Key**:填当前模型所属 provider 的 Key(如 DeepSeek、Qwen),仅保存在本地
    `cpq_settings.json`;后端写进对应环境变量即时生效,重启后自动加载。
  - **采样参数**:temperature(**默认关闭**——部分新版 Claude 不支持 temperature,勾选后才下发)、
    最大输出 tokens、扩展思考(仅 Claude 生效)及思考预算。保存即对下一条消息生效。
  - 接口:`GET /api/models`、`GET/POST /api/settings`。

## 与 EIMOS 的接线

前端改动都在 `eimos/` 仓库内(平台构建需要),CPQ 模块自身的页面/服务放在本文件夹:

1. **菜单改写** —— `eimos/src/hooks/useMenuStore/injectCpqQuote.ts`
   前端拿到后端下发的菜单后,定位「配置报价管理(CPQ)」目录下的「报价管理」子项,
   把它的 `path` 改写为内嵌页 `/pro/eimos/cpqquote`(在 `useMenuStore.init` 中调用,
   与 i-Agent / 技术工艺管理 同套机制,单独 try/catch,失败不影响其余菜单)。
2. **内嵌页** —— `eimos/src/pages/CpqQuote/index.tsx`
   iframe 内嵌本服务地址,路由 `/eimos/cpqquote`(见 `eimos/src/routes.tsx`)。

### iframe 地址覆盖(按优先级)

1. `localStorage['eimos:cpqQuoteUrl']` —— 本地调试覆盖
2. `process.env.CPQ_QUOTE_URL` —— 构建期注入
3. `http://127.0.0.1:8010/` —— 默认(本 `serve.py`)

生产环境把 `报价首页(1).html` 部署到任意静态站点,再用 `CPQ_QUOTE_URL` 指向该地址即可。
