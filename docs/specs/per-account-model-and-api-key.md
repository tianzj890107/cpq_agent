# 账号级模型与密钥 Spec（全局默认兜底保留）

状态：Spec + 红测（已实现）
红测：`tests/test_per_account_model_and_api_key_red.py`

## 1. 目标

登录进来的**每个账号**可以选自己的模型、配自己的密钥；不设置时回落到平台默认。
平台默认仍是现有那一份：`cpq_settings.json` 的 `model` / 推理参数 / `api_keys`，
**兜底语义与权限完全不变**。

本 Spec 只改一件事的粒度：模型与 API Key 从"全局一个值"变成
"全局默认 + 每个登录账号可覆盖"。它**不推翻** `docs/specs/unified-model-settings-and-api-keys.md`
里"全局设置只有一份、四个入口共用、不得每个助手各存一份"的结论 —— 账号覆盖是
加在全局之上的可选层，**缺项一律回落**，它本身不构成第二份"全局设置"。

## 2. 概念

| 层 | 内容 | 存储 | 谁能改 |
| --- | --- | --- | --- |
| 全局默认（平台兜底） | 模型、Temperature、最大 Tokens、深度思考、按 provider 的 Key | `cpq_settings.json`（不变） | 模型/参数：`auth.LLM_SETTINGS_ROLES`；全局 Key：`auth.ADMIN_ROLES`（不变） |
| 账号覆盖（新增） | 仅两项：该账号的 `model`、该账号按 provider 的 `api_key` | `DATA_DIR/_user_llm.json`（新增，0600） | 该账号本人 |

账号覆盖**不含** Temperature / 最大 Tokens / 深度思考 —— 本批不做账号级推理参数，
避免每个账号一份参数分叉。推理参数继续全局。

## 3. 生效优先级（每次调用实时解析）

```
模型：账号 model → 全局 model → llm_settings.DEFAULT_MODEL
密钥：账号 api_keys[provider] → 全局 api_keys[provider] → <PROVIDER>_API_KEY 等环境变量
推理参数：全局（本批不做账号级）
```

## 4. 契约

**C1 解析入口不变形。** `llm_settings.resolve(vision=...)` 保持既有签名与返回结构
（`model` / `provider` / `provider_label` / `base_url` / `native` / `api_key`），
新增"发起账号"维度：优先取显式传入的 `user`，其次取上下文里的发起账号（C6），
都为空时走全局兜底。既有调用点不传账号也不改签名。

**C2 生效模型可查。** 新增 `llm_settings.effective(username="")`（或等价函数），返回
`{model, source: "account"|"global", provider, key_source: "account"|"global"|"env"|"missing"}`，
供接口与界面显示"当前用的是谁的模型/Key"，不重复实现优先级。

**C3 账号级存储独立。** 新增模块（建议 `tech_app/backend/services/user_llm.py`），
落盘 `DATA_DIR/_user_llm.json`，权限 `0600`，原子写（同目录临时文件 + `os.replace`，
与 `cpq_shared_settings.save` 同一手法）。结构：

```json
{"users": {"<username>": {"model": "deepseek-v4-pro",
                          "api_keys": {"deepseek": "sk-..."},
                          "updated_at": "2026-09-16 10:00:00"}}}
```

必须**不**写进 `cpq_settings.json`（四个助手共用的全局文件），也**不**写进
`_auth_users.json`：CPQ 单点登录进来的账号在本地没有用户记录，账号级设置不得
依赖"先有本地用户"。读写该模块不得改动全局 `model` 与全局 `api_keys`。

**C4 白名单只有一份。** 账号级模型必须落在既有 `llm_settings.MODEL_PROVIDERS`
（含本地网关模型名）内，provider 必须属于 `llm_settings.PROVIDERS`；
不新建第二份模型/provider 白名单。非法值明确拒绝，不静默改写。

**C5 密钥安全。** 任何 GET 只回 `configured` + `cpq_shared_settings.mask()` 打码；
日志、审计、异常、任务进度文字、页面 DOM 都不得含明文。`auth.public_user()` 与
`GET /api/users`、`GET /api/me` 不得带出账号级 llm 数据（含打码）。
账号 A 的任何接口都不得读到账号 B 的账号级数据（连打码都不行）。

**C6 发起账号的唯一传递通道。**
- HTTP：`auth_guard` 判定出 `request.state.user` 的同一处，把用户名设入上下文
  （建议 `tech_app/backend/services/acting_user.py`：`set_acting_user()` /
  `current_acting_user()`）；无令牌、鉴权关闭、SSO 关闭时按 `auth_guard` 现有的
  默认用户设入，上下文为空时等于全局兜底。
- 异步任务：`tasks.submit(..., actor="<username>")` 新增可选参数，`_run()` 在 worker
  线程内设入上下文（与既有 `_CURRENT_TASK.set()` 同一位置，同一手法）。
  **所有会调用大模型的异步任务都必须传发起人。**
- 没有发起人（定时、系统自愈重跑、历史任务恢复）→ 上下文为空 → 全局兜底。
- 接口自己计算"我的/生效"时必须**显式**使用 `current_user` 的 username，
  不得依赖上下文传递（HTTP 中间件与线程池之间的传播不由业务代码假设）。

**C7 会话与路由。** Agent 会话按项目共享。一轮对话开始时按**发起账号**解析路由，
与该会话上次使用的路由（含账号）不一致就重建 client（复用
`oc_agent.ProjectAgent._rebuild_client`）。历史消息不重算、不删除、不裁剪。
同一项目里 A、B 两个账号交替说话时，每轮各用自己生效的模型/Key。

**C8 能力校验按生效模型。** `ensure_vision_capable()` 用生效模型判断；不满足时报错
必须带生效模型名与来源（账号还是全局），不得静默换模型。

**C9 兜底与失败语义。**
- 全局 `model`、全局 `api_keys` 不得被任何账号级写入修改或删除（兜底保留）。
- 账号选了自己没有 Key 的模型，且全局也没有该 provider 的 Key → 明确失败，
  错误信息含 provider 名与该账号名；**不得**静默改用全局模型或别的 provider。
- 账号清空自己的设置后，行为与今天完全一致（回落全局）。
- 没有任何账号级记录时（含 `_user_llm.json` 不存在），所有行为与今天等价。

**C10 接口。**
- `GET /api/my/settings`：任何登录账号读**自己**的账号级设置 + 生效模型与来源。
- `PUT /api/my/settings`：任何登录账号写自己的。`model` 缺键 = 不改，空串/null =
  清除个人模型（回落全局）；`api_key` 必须与 `api_key_provider` 成对且非空才写。
  请求体里的 `username` / `user` / `actor` 一律忽略，绝不作用于他人。
- `DELETE /api/my/settings/keys/{provider}`：删除自己该 provider 的个人 Key。
- 以上三个接口只读写 `current_user` 对应的账号，不接受指定他人。
- 全局 `GET/PUT /api/settings` 语义、权限、错误文案不变；GET 额外附 `mine` 摘要
  （自己的模型状态、自己各 provider 的 configured/打码）与 `effective_model` /
  `effective_source`，仍然只回打码。

**C11 审计。** 账号级改动记 `store.audit("_global", "user_llm_settings_update", {actor, fields})`，
只记字段名与"是否改了密钥"，不记任何值（沿用 `audit_llm_settings_change` 的口径）。

**C12 前端面板。** 共用面板 `tech_app/frontend/llm-settings-panel.js` 新增
「我的模型与密钥」区（所有登录账号可编辑，写 `/api/my/settings`），保留
「平台默认（不设置时使用）」区（权限同现状：模型/参数 `auth.LLM_SETTINGS_ROLES`，
全局 Key `auth.ADMIN_ROLES`）。面板对 `/api/my/settings` 返回 404/405（报价三端
暂无该接口）时隐藏"我的"区且不报错。保存个人 Key 成功后立即清空输入框。
页头模型名显示**生效模型**，来源为账号时标注「我的」。文案明确"不设置时使用平台默认"。

**C13 不做的事（边界）。**
- 不改报价 / 配置 / 规则三个助手的 `/api/settings`（在另一个服务、另一套鉴权），
  账号级只先在技术工艺生效。
- 不改 `open-claude` 包内文件，不新增第二份全局设置文件，不复活
  `vision_model` / `text_model` 字段。
- 不动全局设置的权限模型，不动报价流程、会话、任务、历史数据与业务接口。

**C14 兼容与回归。** 无账号级记录时逐字节等价现状；账号级设置文件损坏或不可读时
安全降级为"没有账号级设置"（回落全局），不抛 500、不影响对话与任务。

## 5. 验收场景（红测覆盖）

1. A 存账号模型 X → `resolve(user="A")` 得 X；B 没设置 → 全局 G；不传账号 → G。
2. A 存个人 qwen Key → A 的 `resolve().api_key` 是个人 Key；B 仍用全局 Key；
   全局 Key 未被覆盖，`_user_llm.json` 之外的文件内容不变。
3. A 选了自己没 Key 的模型（全局也没该 provider Key）→ 报错含 provider 与账号名，
   且**不**降级成全局模型。
4. A 清空个人模型/Key → 回落全局，与现状一致。
5. `GET /api/my/settings`：alice 看到自己的，bob 看到的是空（不串号）；
   `PUT` 请求体里带 `username: "bob"` 仍只改 alice；
   `DELETE /api/my/settings/keys/{provider}` 只删自己那把。
6. `GET /api/settings`、`/api/my/settings`、`/api/me`、`/api/users` 响应里都没有
   明文 Key（只 `configured` + 打码）。
7. 上下文通道：worker 线程里 `resolve()` 用 `tasks.submit(actor="alice")` 的账号模型；
   没有 actor 的任务用全局模型。
8. 进度文案「调用多模态模型解析图纸（…）」显示**生效模型**，不是全局默认。
9. 前端面板：普通账号能看到并编辑「我的」区；全局区仍按权限只读；
   `/api/my/settings` 404 时"我的"区隐藏且无报错；页头显示生效模型。
10. 回归：没有任何账号级记录时，`resolve()` 与今天逐字段一致。

## 6. 接口名（红测与实现必须一致，不得改名）

服务层：

```python
# tech_app/backend/services/acting_user.py
def set_acting_user(username: str) -> None      # 设入当前上下文（"" = 无发起账号）
def current_acting_user() -> str                # 取发起账号，没有时返回 ""

# tech_app/backend/services/user_llm.py
def get(username: str) -> dict                  # {"model": str, "api_keys": {provider: key}}；无记录 = {}
def set_model(username: str, model: str) -> dict        # model="" 清除该账号的个人模型
def set_key(username: str, provider: str, key: str) -> dict   # key="" 删除该 provider 的个人 Key
def summary(username: str) -> dict              # {"model", "has_model", "keys": {provider: {"configured", "hint"}}}，只打码

# tech_app/backend/services/llm_settings.py
def resolve(*, vision: bool, user: str = "") -> dict      # 新增可选 user，返回结构与今天一致
def effective(user: str = "") -> dict                     # {"model","source","provider","key_source"}
# user_llm.set_* 的入参不是合法模型 / provider 时抛 ValueError，不静默改写。
```

HTTP：

```
GET    /api/my/settings                     任何登录账号，只读自己
PUT    /api/my/settings                     任何登录账号，只写自己
       body: {"model"?: str|null, "api_key"?: str, "api_key_provider"?: str}
       model 缺键=不改，""/null=清除；api_key 非空且带 api_key_provider 才写
DELETE /api/my/settings/keys/{provider}     删除自己该 provider 的个人 Key
```

`tasks.submit(project_id, kind, fn, cad=False, *, dedup_key=None, actor="")`：新增 `actor`，
worker 内 `acting_user.set_acting_user(actor)`。
