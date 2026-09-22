# 用户数据统一维护在 Postgres（配置报价 CPQ 为唯一权威）Spec

状态：Spec + 红测（已实现）
红测：`tests/test_user_data_unified_in_pg_red.py`

## 1. 目标

全平台只有**一份**用户数据：配置报价 CPQ 的 `cpq_wf` schema（线上 Postgres）。账号、角色、
会话、账号级模型与 API Key 全部由一体化服务（`cpq_suite_server.py`，8010）读写；技术工艺
（8012）不再有本地用户文件、不再直连数据库，只通过 CPQ 的 `/auth/*` HTTP 拿身份、拿名单、
拿账号级设置。

本批已由用户拍板的四项决定：

1. 技术工艺**不直连 PG**，统一走 CPQ 的 HTTP 接口；
2. 用户主键口径统一成 `user_id`；
3. CPQ 角色字典新增 `admin`（系统管理员）；
4. "所有用户数据"包含账号级模型与 API Key，且**加密存**。

## 2. 现状（为什么现在不是"一份"）

| 内容 | 配置报价 CPQ | 技术工艺 |
| --- | --- | --- |
| 账号 | PG `cpq_wf.cpq_wf_user`（`cpq_auth.py:135-146`） | 本地 JSON `DATA_DIR/_auth_users.json`（`tech_app/backend/storage/meta_backend.py:124-141`） |
| 会话 | 服务端表 `cpq_wf_login_session`（`cpq_auth.py:148-158`） | 自包含 HMAC 票，服务端无状态（`tech_app/backend/services/auth.py:122-131`） |
| 角色 | 4 个业务角色（`cpq_auth.py:29-41`） | 10 个角色（`tech_app/backend/services/auth.py:34-36`） |
| 账号级模型/密钥 | 无 | 本地 JSON `DATA_DIR/_user_llm.json`（`tech_app/backend/services/user_llm.py`） |
| 用户管理接口 | 只有 `GET /auth/users?role=`（`cpq_suite_server.py:366-372`） | `GET/POST /api/users`、`PUT /api/users/{username}/role`、`PUT /api/me`（`tech_app/backend/main.py:583-686`） |
| 主键 | `user_id`(bigint) | `username`(字符串) |

由此产生的具体缺口：

- 用户数据分裂在两地，PG 里没有技术工艺的自注册账号，JSON 里没有 CPQ 的账号；同一批人
  在两处各有一份，角色还可能互相矛盾。
- CPQ 侧**没有任何**用户管理能力：不能改角色、不能改显示名、不能改密码、不能停用账号
  （`cpq_suite_server.py:337-378` 只有 roles / me / register / login / logout / users）。
- CPQ 角色字典里没有 `admin`，技术工艺的角色授予却只有 `admin` 能做
  （`tech_app/backend/main.py:656/661/675`）——“谁批角色申请”在统一后悬空。
- 账号级模型与密钥是**明文落在磁盘文件**里（`user_llm.py` 的 `_save()`），且与账号主数据
  不在同一处，账号改名/停用后这条设置成为孤儿。
- 技术工艺的票是自包含的：改角色、停用账号、强制下线都不能即时生效（`TOKEN_TTL_HOURS`
  默认 12 小时，`tech_app/backend/config.py:222`）。

## 3. 契约

### C1 唯一权威
用户、角色、会话、账号级模型与 API Key 只存 `cpq_wf`（PG）。CPQ 一体化服务是唯一读写出口。
技术工艺**不得**直连 PG：`tech_app/` 下不得出现 `import psycopg` / `psycopg.connect`
（`tech_app/backend/services/cpq_sso.py` 顶部注释已写死这条，需要继续保持）。

### C2 角色字典
`cpq_auth.ROLES` 增加两项，原 4 项不动：

- `admin` → 系统管理员（用户管理、角色授予、账号停用）
- `viewer` → 只读用户（自助注册落地角色）

`cpq_sso.ROLE_MAP` 增加 `admin → admin`、`viewer → viewer`；既有
`sales_mgr → viewer` / `process_mgr → process_manager` / `finance_mgr → finance_manager` /
`tech_director → process_director` 与 `FALLBACK_ROLE = "viewer"` 全部保持不变
（`tests/test_single_login_across_quote_and_tech_red.py` 的
`RoleParityRedTest.test_existing_mappings_unchanged` 是保护性约束）。

### C3 表结构增量

```
cpq_wf.cpq_wf_user              -- 既有表，只加列
  + requested_role  varchar(32)                        -- 注册时申请的业务角色
  + is_system       boolean NOT NULL DEFAULT false      -- 历史归档账号（不可登录、不可改角色）
    status 取值：active | disabled | archived

cpq_wf.cpq_wf_user_llm_setting  -- 新表
  user_id      bigint PRIMARY KEY REFERENCES cpq_wf_user(user_id) ON DELETE CASCADE
  model_cipher text          -- 账号选择的模型（密文）
  keys_cipher  text          -- {"provider": "key"} 的 JSON（密文）
  updated_at   timestamptz NOT NULL DEFAULT now()
```

`cpq_auth._USER_COLS` 同步增加 `requested_role`、`is_system`（出接口时 `is_system` 转 bool，
`user_id` 继续转字符串）。**不加** DB 取值约束（`CHECK`）——角色是字典取值，加角色不改 schema，
`TestCpqRolesAreDefinedInCodeNotSchema` 是保护性约束。

### C4 主键口径
用户主键是 `user_id`（bigint，出接口一律字符串）。所有用户管理端点用 `user_id` 定位
（`PUT /auth/users/{user_id}`），`username` 只作登录名与展示。技术工艺侧所有"这个人是谁"
的落库字段按 `user_id` 走；本批先覆盖新接口与账号级设置，项目 owner / 任务 `target_user_id` /
审计 actor 的存量改造放到批次 2（见 §6）。

### C5 用户管理接口（`admin` 专用）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/auth/users?role=&status=` | 列表。**非 admin**：只回 `user_id/username/display_name/role_code/role_name`；**admin**：另回 `email/status/requested_role/is_system/last_login_at/created_at` |
| POST | `/auth/users` | 建号：`username/password/display_name/role_code/email?/requested_role?` |
| PUT | `/auth/users/{user_id}` | 改 `display_name/email/status/role_code/requested_role`；`role_code` 变更即"批准角色申请" |
| PUT | `/auth/users/{user_id}/password` | 管理员重置口令（不需要旧密码） |

非 admin 调用以上任意一条 → 403，文案说明这一步只有系统管理员能做。`is_system=true` 的账号
不允许改角色（沿用 `tech_app/backend/main.py:681` 的既有约束）。

### C6 本人接口（只需登录）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| PUT | `/auth/my/profile` | 改自己的 `display_name` |
| PUT | `/auth/my/password` | `current_password` + `new_password`（验旧密码，新密码 ≥8 位） |
| GET | `/auth/my/llm` | 自己的账号级模型摘要：`{user_id, model, has_model, keys:{provider:{configured,hint}}}`，**永不回明文** |
| PUT | `/auth/my/llm` | `{model?, api_key?, api_key_provider?}`：缺键=不改，空串=清除，`api_key` 必须与 `api_key_provider` 成对 |
| DELETE | `/auth/my/llm/keys/{provider}` | 删除自己在某 provider 的个人 Key |

**越权防线（本 Spec 的硬性安全契约）**：以上"本人"端点一律以票上的 `user_id` 为准，请求体里
出现的 `user_id` / `username` / `actor` 一律忽略——不报错、也绝不生效。`GET /auth/my/llm`
看不到别人的任何字节（连打码都不给）。

### C7 自助注册收紧
`POST /auth/register` 一律以 `role_code = "viewer"` 落地，`requested_role` 记录申请值：

- 请求体带 `role_code = "admin"`（或 `requested_role = "admin"`）→ 400，文案说明管理员账号只能由
  现有管理员创建；
- 返回文案："注册成功，当前为只读权限；请由系统管理员授予业务角色。"
- 指定任意角色的建号能力只存在于 C5 的 admin 接口。

**这是一处行为收紧**（现在 `cpq_suite_server.py:351-356` 允许自助注册为任意角色，加了 `admin`
之后那条路等于"谁都能把自己变成管理员"）。因此 C13 必须同步提供首个管理员的引导路径。

### C8 账号级密钥加密

- 新模块 `cpq_user_secrets.py`：`seal(plain: str) -> str`、`open(sealed: str) -> str`，用 AEAD
  （AES-GCM 或 ChaCha20-Poly1305）。密文自包含 nonce 与 tag，格式固定
  `v1:<b64 nonce>:<b64 ciphertext+tag>`，两段 base64 均为标准 base64。
- 密钥材料来自环境变量 `CPQ_USER_SECRET_KEY`（base64 或 hex，解出必须 32 字节）。未配置、
  长度不对、解不开 → 抛 `SecretKeyMissing`，**绝不静默明文落库，也绝不静默改用明文**。
- 同一明文两次 `seal()` 结果**必须不同**（每次新随机 nonce）；密文被改动一个字节后 `open()`
  必须抛错（AEAD 认证）。
- 库内 `model_cipher` / `keys_cipher` 一律密文；`keys_cipher` 明文形态是 `{"provider": "key"}` 的
  JSON。日志、审计、错误信息、接口响应（除内部通道）一律不得出现明文。
- 打码复用 `cpq_shared_settings.mask` 的口径（`hint` = 前 7 + `…` + 后 4，短于 15 位回"已配置"）。

### C9 账号级设置的内部通道
技术工艺的 LLM 调用发生在**后台任务线程**里（`tasks.submit(actor=...)`），没有用户请求上下文，
拿不到用户票。因此账号级设置需要一个服务间通道，复用已有的内部令牌机制
（`cpq_suite_server.py:71-73` 的 `CPQ_INTERNAL_TOKEN` / `X-Internal-Token`，
`tech_app/backend/services/llm_settings.py:531-535` 已在用同一个头）：

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/auth/internal/user-llm?username=<login>` | `X-Internal-Token` 严格相等（恒定时间比较） | 回 `{user_id, username, model, api_keys:{provider:明文}}` |
| PUT | `/auth/internal/user-llm` | 同上 | body `{username, model?, api_key?, api_key_provider?}`，语义与 C6 的 PUT 一致 |

明文只从这条通道出（回环、服务间）。内部令牌缺失或不等 → 403，且不得回任何用户数据。
`GET /auth/me` 与 `GET /auth/users` 继续只回打码/公开字段。

### C10 技术工艺侧改造（HTTP 客户端）

- 新模块 `tech_app/backend/services/cpq_auth_client.py`，只做 HTTP：

  | 函数 | 用途 |
  | --- | --- |
  | `user_overrides(username) -> {"model": str, "api_keys": {provider: key}}` | 供解析层取明文（内部通道） |
  | `set_model(username, model)` | 写账号级模型（内部通道 PUT） |
  | `set_key(username, provider, key)` | 写账号级 Key（内部通道 PUT） |
  | `delete_key(username, provider)` | 删账号级 Key（内部通道 PUT，空串） |
  | `summary(username) -> {"model","has_model","keys":{provider:{"configured","hint"}}}` | 给界面用的打码摘要 |
  | `invalidate(username="")` | 写成功后清缓存（不传 = 全清） |
  | `CpqAuthUnavailable` | CPQ 不可达或返回异常时抛的异常类型 |

- 进程内 TTL 缓存（默认 60 秒，可用环境变量覆盖），命中期内不再回调 CPQ（一次页面加载会打
  十几个 `/api` 请求，别把 CPQ 当热路径——与 `cpq_sso.py:51-56` 同一考虑）。
- 失败策略：CPQ 可达 → 刷新缓存；不可达 → 用缓存（过期也用，避免正在跑的任务被外部抖动打断）；
  **连缓存都没有 → 抛 `CpqAuthUnavailable`，明确失败，绝不静默改用全局模型/Key**
  （延续 `per-account-model-and-api-key.md` C9）。HTTP 面把该异常转成 503，文案说明"登录服务
  暂不可用"，而不是 401（避免把在线的人当成未登录）。
- `tech_app/backend/services/user_llm.py` 保留 `get/set_model/set_key/summary/personal_key`
  五个函数名与语义（`tests/test_per_account_model_and_api_key_red.py:758` 是保护性约束），
  内部改为调用 `cpq_auth_client`；**不再读写 `DATA_DIR/_user_llm.json`**，`_save()` 的原子写与
  `0600` 随之作废（改为由 CPQ 侧负责）。
- 模型 / provider 白名单仍只有 `llm_settings.MODEL_PROVIDERS` / `PROVIDERS` 那一份，校验
  仍在 `user_llm`（`per-account-model-and-api-key.md` C4 不变）：CPQ 只管存，不复制一份白名单。

### C11 技术工艺的 HTTP 面

- `/api/my/settings`（GET/PUT）与 `/api/my/settings/keys/{provider}`（DELETE）**路径与响应形状不变**
  （`{"username","model","has_model","keys","effective_model","effective_source","key_source","providers"}`），
  内部改为带**用户自己的 CPQ 票**转发 `/auth/my/llm`；报价端 404 时前端隐藏面板的逻辑不变。
- `/api/me` 的 `sso` 块增加 `user_id`（来自 CPQ 票），供前端调 `/auth/users/{user_id}`；
  `can_write` / `can_cost` / `can_review` / `can_publish` / `role_name` / `required_role_name`
  一律不变（`tests/test_single_login_across_quote_and_tech_red.py:531-556` 是保护性约束）。
- `/api/users`、`/api/users/{username}/role`、`/api/login`、`/api/register`、`PUT /api/me`
  **路由保留**（不得删除 API 面），但在 SSO 模式下明确回 409 并把入口指向配置报价 CPQ
  （"用户与权限在配置报价 CPQ 中维护"），不允许再返回本地用户数据。
- `PUT /api/me` 在 SSO 模式下转发 CPQ 的 `/auth/my/profile` 与 `/auth/my/password`；SSO 模式下
  技术工艺不再签发自己的令牌。

### C12 本地用户表退役与启动守卫

- `tech_app/backend/storage/meta_backend.py` 不再读写 `_auth_users.json`；
  `JsonMetaBackend._users_path/get_user/put_user/list_users` 与 `store.get_user/save_user/list_users`
  不得再返回本地文件里的用户（改为在 SSO 模式下明确报错，或整体移除并改调用点）。
- `AUTH_ENABLED=true` 且 `CPQ_SSO=false`：**启动时明确失败**，文案给出两条出路
  （设 `CPQ_SSO=true` + `CPQ_AUTH_BASE_URL`，或设 `AUTH_ENABLED=false` 做本地开发）。
  理由：用户数据已经只有 PG 一处，"本地账号库"不复存在，留着这条路只会得到一个"登录页永远
  密码错误"的假象。
- `AUTH_ENABLED=false`（默认）与 `AUTH_AUTO_ADMIN=true` 的本地开发模式照旧可用
  （隐式 system/admin，没有账号级设置 → 回落全局默认）。

### C13 迁移与首个管理员

新脚本 `scripts/migrate_users_to_pg.py`：

- 读 `DATA_DIR/_auth_users.json`（不存在 → 明说"没有本地用户文件"并以 0 退出）；
- 口令散列**无损转换**：`pbkdf2$<iters>$<b64 salt>$<b64 dk>` →
  `pbkdf2_sha256$<iters>$<hex salt>$<hex dk>`（同算法、同迭代数，只是编码不同；两边
  `_ITERS`/`_PBKDF2_ROUNDS` 都是 200000）。已经是 CPQ 格式的原样保留；无法解析的账号
  **不导入**、列入报告（不静默丢弃、不生成空密码）；
- 默认 `--dry-run`（只打印计划，不写库）；`--apply` 才写；同名账号已存在 → 跳过并报告；
  `is_system=true` 的账号导入为 `status='archived'`；
- `--promote <username>`：把一个已存在账号提升为 `admin`（首个管理员的引导路径）；
- **同一趟也搬账号级设置**：读 `DATA_DIR/_user_llm.json`（批次 ## 86 的账号级模型与
  明文 Key），按 `username` 找到 `user_id` 后经 `cpq_user_secrets.seal()` 写入
  `cpq_wf_user_llm_setting`；账号在 PG 里找不到 → 记为 `skipped` 并给出去向。
  不搬这一份，现有账号的个人模型与个人 Key 会在换存储的那一刻凭空消失；
- **不删除、不改写原文件**（`_auth_users.json` 与 `_user_llm.json` 都原样留在盘上）；
  报告写 `DATA_DIR/_auth_users.migrated.json`（每个账号的 `imported / skipped / rejected`
  与原因，账号级设置单独一节）。

`cpq_auth.init()` 之后的首个管理员引导：用户表为空且 `CPQ_ADMIN_USER` / `CPQ_ADMIN_PASSWORD`
都非空且密码不是默认弱口令 → 创建 `admin`；否则只打印可操作提示（不自动建号、不让启动失败）。

### C14 依赖与配置增量

- `requirements.txt` 增 `cryptography==50.0.0`（已验证 `open-claude/.venv` 里就是 50.0.0）。
- 新环境变量：`CPQ_USER_SECRET_KEY`（账号级密钥加密）、`CPQ_ADMIN_USER` / `CPQ_ADMIN_PASSWORD`
  （首个管理员引导）、`CPQ_USER_LLM_CACHE_SECONDS`（可选，账号级设置缓存 TTL）。
- `tech_app_launch.py:77-81` 已注入 `CPQ_SSO=true` / `CPQ_AUTH_BASE_URL`；
  `cpq_suite_server.py:675` 已把 `CPQ_INTERNAL_TOKEN` 传给子进程——两处保持。

## 4. 验收场景

1. 技术工艺进程里搜不到 `import psycopg` / `psycopg.connect`，也没有任何本地用户文件读写。
2. `cpq_auth.ROLES` 含 `admin`/`viewer`；`cpq_sso.ROLE_MAP` 含 `admin → admin`、
   `viewer → viewer`；既有 4 条映射与 `FALLBACK_ROLE` 不变。
3. 用户表 DDL 含 `requested_role`、`is_system`；存在 `cpq_wf_user_llm_setting` 表（`user_id` 主键、
   `model_cipher`、`keys_cipher`、`updated_at`）；DDL 里没有 `CHECK (role_code`。
4. 未登录调 `/auth/users`、`/auth/my/llm` 一律 401；非 admin 调 `POST /auth/users`、
   `PUT /auth/users/{user_id}`、`PUT /auth/users/{user_id}/password` 一律 403。
5. admin 调 `PUT /auth/users/{user_id}` 时，落库作用对象是**路径里的 user_id**。
6. `PUT /auth/my/profile`、`PUT /auth/my/password`、`PUT /auth/my/llm` 的请求体里塞别人的
   `user_id`/`username`，生效对象仍然是票上的人。
7. `POST /auth/register` 带 `role_code=admin` → 400；正常注册落地 `role_code=viewer` +
   `requested_role`。
8. `seal/open` 往返一致；同一明文两次密文不同；密文里不含明文；篡改一个字节后 `open` 抛错；
   未配置 `CPQ_USER_SECRET_KEY` 时 `seal` 抛 `SecretKeyMissing`。
9. 写库参数里没有明文 Key（`set_user_llm` 落的是 `seal()` 的结果）。
10. `convert_password_hash` 把技术工艺的散列转成 CPQ 格式后，原口令仍能通过
    `cpq_auth.verify_password`，错口令不能通过；已经是 CPQ 格式的原样返回。
11. `scripts/migrate_users_to_pg.py` 默认 dry-run（不写库），`--apply` 才写，`--promote` 存在；
    原 `_auth_users.json` 不被改写。
12. `X-Internal-Token` 不对时 `/auth/internal/user-llm` 拒绝；对了才回明文（仅该通道）。
13. SSO 模式下技术工艺 `/api/users*` 回 409 并指向 CPQ；`/api/me` 的 `sso` 块含 `user_id`。
13b. 迁移脚本同时搬账号级设置（明文 Key 经 `seal()` 加密后入库）；本批之后
    `DATA_DIR/_user_llm.json` 即使还在也不再影响任何一次解析（换存储不丢设置）。
14. CPQ 不可达且无缓存时，账号级设置明确失败（503 / `CpqAuthUnavailable`），不静默用全局 Key。

## 5. 接口与文件名（实现提示词使用）

```
新增  cpq_user_secrets.py                       seal / open / SecretKeyMissing
新增  tech_app/backend/services/cpq_auth_client.py
新增  scripts/migrate_users_to_pg.py            --dry-run(默认) / --apply / --promote
                                                 convert_password_hash() / 账号级设置搬迁
改    cpq_auth.py                                ROLES / DDL / _USER_COLS / create_user /
                                                 update_user / set_password / change_password /
                                                 list_users(status) / get_user_llm /
                                                 set_user_llm / delete_user_llm_key /
                                                 find_user / bootstrap_admin
改    cpq_suite_server.py                        /auth/users(C5) /auth/my/*(C6) /
                                                 /auth/internal/user-llm(C9) /auth/register(C7)
改    tech_app/backend/services/user_llm.py      改调 cpq_auth_client，去掉 _user_llm.json
改    tech_app/backend/services/cpq_sso.py       ROLE_MAP 增 admin / viewer
改    tech_app/backend/main.py                   /api/users* 409、/api/my/settings 转发、
                                                 /api/me 增 user_id、启动守卫(C12)
改    tech_app/backend/storage/meta_backend.py   用户表退役(C12)
改    requirements.txt                           cryptography==50.0.0
```

## 6. 批次与不在本批

**本批（## 90）**：C1–C14 的**后端与存储**通路——PG 成为唯一用户数据源、admin 角色、
用户管理接口补齐、账号级密钥加密入 PG、技术工艺改走 HTTP、本地用户表与本地密钥文件退役、
迁移脚本。

**批次 2**：`user_id` 下沉到存量业务字段——项目 `owner`（`tech_app/backend/storage/store.py:332`、
`services/auth.py:210-219` 的 `can_edit_project`）、任务 `target_user_id`
（`tech_app/backend/main.py:92`、`:3013`、`:6141`、`models/cost_review.py:85`）、审计 actor
（`store.audit`）、`cpq_tech_bridge` 的回传对象，以及对应的存量回填脚本。

**批次 3**：前端收口——`tech_app/frontend/account.html` 的"用户与权限"改走 `/auth/users` +
`PUT /auth/users/{user_id}`、CPQ 侧新增用户管理界面（建号/改角色/停用/重置口令）、登录框补
"改资料/改密码"。本批只保证后端接口就绪。

## 7. 覆盖关系

- 本 Spec 覆盖 `per-account-model-and-api-key.md` 的 **C3**（账号级设置落
  `DATA_DIR/_user_llm.json`）与 **C14**（文件损坏回落全局）两条：存储改为 PG 密文 +
  内部通道，损坏语义变为"CPQ 不可达时用缓存，无缓存则明确失败"。该文件其余契约
  （优先级、白名单唯一来源、账间隔离、审计口径、面板文案）全部继续有效。
- 本 Spec 覆盖 `single-login-across-quote-and-tech.md` 的 **C6**（保留私有化独立模式）：
  用户数据只剩 PG 一处后，`AUTH_ENABLED=true` 且 `CPQ_SSO=false` 不再是一条可用路径
  （改为启动失败），但本地登录/用户管理的**路由**保留为明确的 409，不删除 API 面。
