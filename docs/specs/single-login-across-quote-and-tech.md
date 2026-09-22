# 报价与技术工艺合并为一套登录与鉴权 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_single_login_across_quote_and_tech_red.py`

## 1. 目标

一体化服务（`cpq_suite_server.py`，8010）对用户是**一个站点、一个登录入口、一套角色**。
报价助手、配置助手、规则助手、技术工艺四个入口共用同一张令牌、同一份角色定义、同一处
鉴权判定；技术工艺不再存在"另有一套账号/另有一处不校验"的缺口。

同时**保留**技术工艺的私有化独立模式（`CPQ_SSO=false`）：没有配置报价 CPQ 的部署，
技术工艺自己的本地登录与用户管理继续完整可用。

## 2. 现状（哪里没做一起）

端口与静态资源早就是一套：`cpq_suite_server.py:47-78` 把三个 Agent 在进程内挂到
`/agents/{quote|config|rule}`，把技术工艺作为子进程拉到 `127.0.0.1:8012` 再反向代理
（`_proxy_tech`，`cpq_suite_server.py:158`）。**但鉴权是三处互不相干的东西**：

1. **`/agents/*` 完全不验票。** `cpq_suite_server.py:137` 的 `_dispatch_agent()` 命中
   前缀后直接把请求交给 Agent 模块，`do_GET`/`do_POST` 里它是**第一个**分支
   （`:507`、`:539`）。实测：无 `Authorization`、甚至伪造令牌，`/agents/quote/api/settings`
   与 `/agents/quote/api/send` 都回 200 —— 未登录也能改全局模型与 API Key、能驱动 Agent。
2. **两套账号与两张票。** CPQ 侧 `cpq_auth.py`（Postgres `cpq_wf_user` /
   `cpq_wf_login_session`，前端 `localStorage.cpq_auth_token`）；技术工艺侧
   `tech_app/backend/services/auth.py` 的 HMAC 令牌（前端读 `authToken` /
   `cad_engine_token`，由 `tech_app/frontend/cpq-sso.js:30` 镜像）。技术工艺每个 `/api/*`
   经 `auth_guard`（`tech_app/backend/main.py:377`）回调 8010 的 `/auth/me` 验票，报价侧的
   `/agents/*` 却没有任何校验。
3. **前端各带各的票。** 报价四个页面（`报价首页.html`、`确认需求解析结果.html`、
   `XBOM智能体-配置BOM生成.html`、`规则助手-规则配置.html`）对 `/agents/*` 共 46 处调用
   **全部是裸 `fetch`**，不带 `Authorization`；`报价首页.html` 另有 6 处同源技术工艺
   `/api/*` 调用也是裸 `fetch` —— 这两类在 SSO 模式下要么无票放行、要么必然 401。

**角色也不齐。** CPQ 只有 `sales_mgr` / `process_mgr` / `finance_mgr`
（`cpq_auth.py:33`），没有"工艺技术总监"；技术工艺这边 3.2 审核要 `REVIEW_ROLES`、
3.3 发布要 `DIRECTOR_ROLES`，都指向 `process_director`
（`tech_app/backend/services/auth.py:41-42`）。于是只能靠
`auth.enable_cpq_single_manager()`（`:80`，由 `main.py:521` 在
`CPQ_SSO_ENABLED and CPQ_MANAGER_FULL_TECH` 时调用）把工艺经理升成全权，
3.1 → 3.2 → 3.3 由同一个人做完，三级职责分离不成立。

## 3. 契约

### C1 唯一身份源

- 一体化部署下，身份只来自配置报价 CPQ 的登录系统（`cpq_auth` + Postgres）。
- SSO 模式下技术工艺**不得**签发或接受自己的 HMAC 令牌作为身份，也不得把本地账号库
  当作权限来源；两个助手的鉴权结果必须来自同一次 `cpq_auth.whoami(token)` 语义。
- 允许保留 `cpq-sso.js` 的令牌镜像（十几个页面在脚本求值时读 `authToken`），但镜像的
  必须还是**同一张 CPQ 票**，不得引入第二张。

### C2 `/agents/*` 必须验票

- 每个 `/agents/{quote|config|rule}/...` 请求在处理前必须校验令牌；无票、坏票、过期票
  一律 `401`，响应体为 `{"ok": false, "error": "请先登录"}` 这一类可读文案。
- 鉴权发生在**调用 Agent 处理函数之前**：被拒绝的请求不得产生任何副作用（不写历史、
  不写库、不落盘、不触发模型调用）。
- 登录服务/数据库不可用导致无法验票时回 `503`（"登录服务暂不可用"），
  不得回 401 —— 那会把在线用户静默踢出去。

### C3 令牌的携带方式

- 接受 `Authorization: Bearer <token>`；同时接受 `?token=<token>`（供发不出请求头的
  场景，与 `tech_app/backend/main.py:395` 既有约定一致），两者走同一条校验。
- `OPTIONS` 预检不验票（浏览器预检不带 `Authorization`），返回 204 后仍需保留既有
  CORS 响应头。
- 非 `/agents/*` 的路径不受本批影响：静态页、`cpq_auth.js` 等前端资源不得因为本批被
  401 挡住。

### C4 服务内部的调用

- 技术工艺把设置变更广播给报价侧（`tech_app/backend/services/llm_settings.py:413` 的
  `_post_quote_settings()` → `/agents/quote/api/settings`）属于服务间调用。它必须改带
  一个由一体化服务在拉起子进程时生成、通过环境变量注入的内部令牌
  （`CPQ_INTERNAL_TOKEN` / 请求头 `X-Internal-Token`），不能靠"来源是 127.0.0.1 就放行"。
- 该令牌缺失时按"未配置"处理并**明确告警**（"技术工艺改了模型但报价侧不会生效"），
  不允许静默 `except: pass` 继续假装成功。
- 内部令牌只授予设置同步这一个用途，不得让外部用户借它跳过用户级权限。

### C5 前端统一带票

- `cpq_auth.js` 暴露一个低层请求助手（接口名：`window.cpqAuthFetch(url, options)`）：
  有令牌时始终附加 `Authorization: Bearer <cpq_auth_token>`，返回**原始 Response**
  （不做 JSON 解析，因为 `/api/send` 是 SSE、`/api/export/docx` 是二进制）。
- 上述四个页面中所有指向 Agent 基址的调用（当前共 46 处）必须改走该助手，
  不得再出现裸 `fetch(...AGENT_URL...)` / `fetch(src.base ...)` / `fetch(modeBase(...) ...)` /
  `fetch(peerBase(...) ...)`。
- `报价首页.html` 中 6 处同源技术工艺 `/api/*` 调用同样必须带票（SSO 模式下它们今天
  必然 401）。
- 收到 401 时必须走已有的未登录路径（拉起 CPQ 登录框），不得把 401 的响应体当作业务
  数据继续渲染；登录成功后（`cpq-auth-change`）四个页面都必须重新拉取受保护数据，
  不能停在空列表。

### C6 保留私有化独立模式

- `CPQ_SSO=false` 且 `AUTH_ENABLED=true` 时：`/api/login`、`/api/register`、
  `/api/users`、`auth.html`、`account.html` 与本地账号库继续完整可用
  （不得删除、不得改语义）。
- 两种模式互斥且互不冒充：SSO 模式下本地登录必须继续被明确拒绝（现状 409），
  不得"看起来能用"；独立模式下不得要求 CPQ 存在。
- 独立模式下技术工艺**没有** CPQ 票，任何"必须持 CPQ 票"的判定都不得让页面白屏
  或让业务不可用。

### C7 角色补齐（工艺技术总监）

- `cpq_auth.ROLES` 增加：`tech_director` = `工艺技术总监`；注册表单可选（`/auth/roles`
  由该字典派生，自动出现）。不新增角色表、不引入数据库取值约束变更。
- `tech_app/backend/services/cpq_sso.py` 的 `ROLE_MAP` 增加
  `tech_director` → `process_director`；`TECH_ROLE_LABEL` 增加
  `process_director` → `工艺技术总监`（403 文案要说人话）。
- 未识别角色仍回落 `viewer`（安全默认不变）。

### C8 职责分离可恢复

- `CPQ_MANAGER_FULL_TECH=false` 时，启动不得把工艺经理升成全权：
  `process_manager` 不在 `REVIEW_ROLES` / `DIRECTOR_ROLES` 中，
  1.3 需求审核、3.2 报告审核、3.3 报告发布由 `process_director` 承担。
- `CPQ_MANAGER_FULL_TECH=true`（默认）保持今天的行为不变（工艺经理全权）。
- `/api/me` 返回的 `sso.required_role_name` 必须与开关一致：全权模式下是"工艺经理"，
  关掉之后不得再声称"只能工艺经理使用"。

### C9 能力位与前端放行

- `/api/me` 的 `sso` 增加布尔能力位 `can_review`（`REVIEW_ROLES`）与 `can_publish`
  （`DIRECTOR_ROLES`）；`can_write` / `can_cost` 语义不变。
- `tech_app/frontend/cpq-sso.js` 的写拦截按能力放行对应路径（否则工艺技术总监点
  「审核通过」「发布」会被**前端伪 403** 挡下，和当初财务经理点「测算」是同一个坑）：
  - `can_review` → `/versions/<n>/approve|reject`
  - `can_publish` → `/process-report/review`、`/process-report/publish`、
    `/requirement/review`、`/process-report/distribution`
- 拦截始终只是"提前把结果告诉用户"，最终判定仍在后端 `_require`。

### C10 不得降低既有安全

- 不新增任何"验不了票就当成系统管理员/工艺经理"的兜底。
- 不改 `/wf/*`（`cpq_suite_server.py:337`）与 `/auth/*` 既有权限判定语义。
- 不把权限判定搬到前端当作唯一防线。
- 不删除、不迁移、不清空任何历史会话、项目、任务、账号、上传文件与数据库记录；
  不修改 `open-claude` 包内文件。

### C11 非目标

- 不合并进程：技术工艺仍是 8010 拉起的内部子进程（单端口、单来源，用户侧只看到一个
  站点）。把 FastAPI 与 `http.server` 合成一个进程属于重写，风险与收益不成比例，
  本批不做。
- 独立运行的 Agent 服务（47292 / 47294 / 47296，`file://` 调试用）不在本批范围；
  一旦被一体化服务代管，就必须由一体化服务统一验票。
- 不改业务逻辑、不改数据库 schema（只扩充 `ROLES` 字典取值）。

## 4. 验收场景

1. 未登录直接 `GET /agents/quote/api/settings` → 401 且响应体提示先登录；Agent 侧没有
   任何调用记录（无副作用）。
2. 未登录 `POST /agents/quote/api/send` → 401，不产生任何会话/历史/模型调用。
3. 伪造或过期令牌 → 401；有效令牌 → 正常进入 Agent 处理。
4. `?token=<有效票>` → 正常进入 Agent 处理（发不出请求头的场景）。
5. 登录服务不可用 → 503，而不是 401。
6. `OPTIONS /agents/quote/api/settings` → 204，不带票也能过预检。
7. `GET /cpq_auth.js` 等前端资源无票仍可访问。
8. 四个页面里不存在任何不带票的 Agent 调用；SSO 模式下报价首页读取技术工艺
   `/api/projects/*` 不再 401。
9. 在 CPQ 注册页可选到「工艺技术总监」；该角色登录技术工艺后，
   `/api/me` 的 `can_review` / `can_publish` 为真、`can_write` 为假。
10. `CPQ_MANAGER_FULL_TECH=false` 启动后，工艺经理不再能审核/发布报告，
    工艺技术总监可以；`=true` 时行为与今天一致。
11. `CPQ_SSO=false` + `AUTH_ENABLED=true` 时，本地登录、注册、用户管理全部照旧可用。
12. 技术工艺改设置后，报价侧 `/agents/quote/api/settings` 仍被同步（内部令牌），
    且日志中没有明文 Key。

## 5. 接口与文件名（实现提示词使用）

- 新角色码：`tech_director`；中文名：`工艺技术总监`；映射到技术工艺 `process_director`。
- 新前端助手：`window.cpqAuthFetch(url, options)`，定义在仓库根 `cpq_auth.js`。
- 新环境变量：`CPQ_INTERNAL_TOKEN`；新请求头：`X-Internal-Token`。
- 新增能力位：`sso.can_review`、`sso.can_publish`。
- 涉及文件：`cpq_suite_server.py`、`cpq_auth.py`、`cpq_auth.js`、
  `tech_app/backend/services/cpq_sso.py`、`tech_app/backend/services/auth.py`、
  `tech_app/backend/services/llm_settings.py`、`tech_app/backend/main.py`、
  `tech_app/frontend/cpq-sso.js`、`报价首页.html`、`确认需求解析结果.html`、
  `XBOM智能体-配置BOM生成.html`、`规则助手-规则配置.html`。
