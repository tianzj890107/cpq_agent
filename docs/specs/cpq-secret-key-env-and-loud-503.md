# 账号级密钥的加密密钥：配置文件要真被读到 + 缺密钥必须是可执行的 503（## 93）

## 1. 背景（线上实测，非推断）

技术工艺「模型设置 → 我的模型与密钥」保存时报「登录服务暂不可用」。根因不是登录服务挂了，
是 **8010 这台机器从来没配过 `CPQ_USER_SECRET_KEY`**：

- `cpq_auth.set_user_llm()` 写库前对 model 与 Key 一律 `cpq_user_secrets.seal()`（`cpq_auth.py:536-538`）；
- `cpq_user_secrets._key()` 读不到环境变量就抛 `SecretKeyMissing`（`cpq_user_secrets.py:56-60`）；
- 该异常不是 `cpq_auth.AuthError`，落到 CPQ 的兜底分支 → stderr 打一行
  `[cpq-suite] /auth 出错: …` + 回 `500 服务异常，请稍后重试`（`cpq_suite_server.py:516-519`）；
- 技术工艺把它包成 `CpqAuthUnavailable`，前端显示成「登录服务暂不可用」——
  看着像登录服务故障，实际是缺少一个环境变量。

同时，`cpq_suite_server.py` **从不调用 `load_dotenv()`**（只有 `tech_app/backend/config.py:9` 调），
所以配置文件这条路在 8010 上根本不存在：只能靠手工 export，换台机器/换个人就复发。

密钥一旦启用就不能再变：`get_user_llm()` 对解不开的密文是**明确抛错**而不是当成"没设置"
（`cpq_auth.py:490-506`）。所以任何一次重启都必须带上同一把密钥，否则已经存过个人 Key 的账号
连读都会失败。这一点必须写进部署文档。

## 2. 目标

1. 8010 进程启动时真的读取配置文件（与 `tech_app/backend/config.py` 同一套语义），
   让"把变量写进文件"成为一等配置方式，不再依赖手工 export；
2. 缺少加密密钥时，接口回 **503 + 可执行文案**（点名 `CPQ_USER_SECRET_KEY`），
   不再伪装成 500「服务异常」；
3. `DEPLOYMENT.md` 把这件事写成部署前置检查，并写清"密钥启用后不可更换"。

## 3. 契约

### C1 `cpq_suite_server.py` 启动即读配置文件

- 在**任何会读环境变量的模块被 import 之前**调用 `load_dotenv(...)`
  （`import cpq_auth` / `cpq_wf` / 三个 agent 模块都会在导入期读 `CPQ_PG_*`、`CPQ_INTERNAL_TOKEN` 等）。
- 路径规则：
  1. 设了环境变量 `CPQ_ENV_FILE` → 用它指向的文件（便于把密钥放在仓库外，例如
     `/home/wugefei/CPQ/cpq_env.sh`，`python-dotenv` 认 `export KEY=VALUE` 写法）；
  2. 否则用仓库根 `.env`（`Path(__file__).resolve().parent / ".env"`）。
- 文件不存在不算错误（与 `config.py` 一致）。
- **不覆盖已存在的环境变量**（`override=False`）：`set -a; . cpq_env.sh; set +a` 这种显式导出优先。
- `.env` / env 文件都不入库（`.gitignore:10` 已有 `.env`；不得新增例外）。

### C2 加密密钥缺失 → 503 + 可执行文案

`cpq_suite_server.py` 的 `/auth` 异常分支新增一条专用分支（排在通用 `except Exception` 之前）：

```python
except cpq_user_secrets.SecretKeyMissing as e:
    print(f"[cpq-suite] 账号级密钥不可用: {e}", file=sys.stderr)
    self._send_json(503, {"ok": False, "error": "账号级模型与密钥的加密密钥 CPQ_USER_SECRET_KEY 未配置或不可用："
                                                "请在 8010 的启动环境里配置 32 字节 base64/hex 的 "
                                                "CPQ_USER_SECRET_KEY 后重启服务；启用后不可更换。"})
```

- `error` 文案必须**同时**含 `CPQ_USER_SECRET_KEY` 与「未配置」（红测按这两个 token 断言），
  并给出下一步；**不得**出现密钥材料本身的值。
- 覆盖所有会 `seal/open` 的入口，至少：`PUT /auth/my/llm`、`PUT /auth/internal/user-llm`。
- stderr 保留一行（含异常原文），便于在 `nohup.out` 里定位。
- **不放松**：通用 `except Exception` 仍是 `500 服务异常，请稍后重试`——不能把所有异常都变成 503。

### C3 读取路径不受影响（既有行为不得改）

- `GET /auth/my/llm`、`GET /auth/internal/user-llm` 不 `seal`，缺密钥时仍应 200（返回空设置）。
- `DELETE /auth/my/llm/keys/{provider}`：该账号本来没有 Key 时不会 `seal`，因此不是失败（现状如此，本批不改）。
- 平台默认模型/参数、解析/生成/报价/Agent 均不受影响。

### C4 `DEPLOYMENT.md` 写成部署前置检查

- 「部署前检查」补 `CPQ_USER_SECRET_KEY` 与 `CPQ_INTERNAL_TOKEN` 两项，并写明：
  - `CPQ_USER_SECRET_KEY`：32 字节 base64 或 hex；账号级模型与 API Key 的加密材料；
    **启用后不可更换**（换掉之后已存过个人 Key 的账号连读都会失败）；
  - `CPQ_INTERNAL_TOKEN`：服务间通道（知识库快照、账号级设置的内部读写）；
  - 配置方式：`set -a; . <不在仓库内的 env 文件>; set +a` 或 `CPQ_ENV_FILE=<该文件>`，
    文件权限 0600，git pull/checkout 碰不到它；不得把密钥写进仓库。
- 「线上实例现状」那一节（裸进程）补一句：`8010` 重启前先确认这两个变量在场。

### C5 技术工艺侧文案不再只暗示"登录服务故障"

`tech_app/backend/main.py:515-522` 的 `CpqAuthUnavailable` 处理文案改成中性表述并保留上游原文
（例如「账号级模型与密钥暂时读不到：{exc}」），使 CPQ 返回的
`CPQ_USER_SECRET_KEY 未配置…` 能原样被用户看到。`cpq_auth_client` 已经带上上游 detail
（`cpq_auth_client.py:151-153`），这一条只改前缀措辞，不改状态码（仍是 503）。

### C6 不放松

- 未配置密钥时**绝不**明文落库（## 90 契约）；
- 账号级设置的读取失败仍必须明确失败、不得静默回落全局 Key（## 90 契约）；
- 不新增任何"缺密钥也放行"的开关。

## 4. 验收

| 编号 | 场景 | 通过标准 |
| --- | --- | --- |
| A1 | 启动读配置 | `CPQ_ENV_FILE` 指向的文件里的变量，import 后即可用；已 export 的同名变量不被文件覆盖 |
| A2 | 缺密钥写账号级设置 | `PUT /auth/my/llm` → 503，error 含 `CPQ_USER_SECRET_KEY` 与「未配置」 |
| A3 | 缺密钥经内部通道写 | `PUT /auth/internal/user-llm`（带内部令牌）→ 同样 503 + 文案 |
| A4 | 其它异常 | 仍是 500 +「服务异常，请稍后重试」 |
| A5 | 读取 | 缺密钥时 `GET /auth/my/llm`、`GET /auth/internal/user-llm` 仍 200 |
| A6 | 配置齐备 | `PUT /auth/my/llm` 正常 200（不回归） |
| A7 | 文档 | `DEPLOYMENT.md` 前置检查含两个变量 + 密钥不可更换的告警 |

## 5. 不在本批

- 线上补密钥与重启 8010（需要用户单独授权；本批只改代码与文档）；
- 密文损坏（能解出但认证失败）的映射：那是数据问题不是服务不可用，仍按通用 500；
- 把环境变量来源做成一等配置中心（本批只支持"文件 + 显式导出"两种）。
