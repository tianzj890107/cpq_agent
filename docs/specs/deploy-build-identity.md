# 规格：部署版本身份（`/api/health` 暴露 build commit + 部署脚本落 stamp + 与 HEAD 对账）

状态：Spec + 红测（已实现）
红测：`tests/test_deploy_build_identity_red.py`
依赖：`tech_app/backend/main.py` 的 `/api/health`、`scripts/deploy_34_bare.sh`、`DEPLOYMENT.md`。
现场证据（2026-09-21 实测，不是推断）：验收与复验期间，**只能靠功能差异反推 34 上跑的是哪个
commit** —— `/api/health` 里没有版本信息，`curl` 出来的只有健康与转换器能力；于是「34 与分支
HEAD 是否一致」这件事只能靠翻部署日志、或再跑一次部署把 HEAD 对齐（当晚为了让
`HEAD == 已部署` 一致，多跑了一次部署）。

## 0. 一句话目标

让「这台机器上跑的是哪一版代码」变成**一条命令能读出来的事实**：`/api/health` 回 `build`
段，部署脚本把它当次部署的 commit 写进 stamp、并**自己核对** stamp 与仓库 HEAD 一致；
外部任何人 `curl` 一次就能与 `git rev-parse --short HEAD` 对账。

## 1. 现场缺口

| 位置 | 现状 |
| --- | --- |
| `/api/health`（`tech_app/backend/main.py:896`） | 只有 `status` / 模型 / `cad_converter` / `auth_enabled` / `sso_enabled`，**没有版本段** |
| `scripts/deploy_34_bare.sh` | 结论行只打印 `7312eca → 5ec90a6` 这类文本，**不落任何机器可读的版本文件**；重启后无从查询 |
| 仓库 | 没有任何 `build.json` / stamp 机制；`git` 在运行目录里可用，但服务本身不读它 |
| 结果 | 「34 是否已部署到 HEAD」只能靠人肉比对与再部署兜底 |

## 2. 契约

### 2.1 新增 `tech_app/backend/services/build_identity.py`

```python
STAMP_ENV = "CPQ_BUILD_STAMP"          # stamp 文件路径（部署脚本注入到服务启动环境）
STAMP_FILENAME = "cpq_build.json"      # 缺省文件名，落在**部署目录之外**（不脏工作区）
BUILD_KEYS = ("commit", "branch", "ref", "deployed_at", "source")
UNKNOWN = "unknown"

def stamp_path(*, env=None, repo_root=None) -> pathlib.Path
def git_head(repo_root=None) -> str          # 短路返回：拿不到就返回 UNKNOWN，不抛
def build_info(*, env=None, repo_root=None) -> dict
```

`build_info()` 的**唯一口径**：

1. 读 `stamp_path()`（env `CPQ_BUILD_STAMP` 优先；缺省 `<repo_root>/../cpq_build.json`）：
   JSON 对象且 `commit` 是非空字符串 → `{"commit", "branch", "ref", "deployed_at",
   "source": "stamp"}`（缺的键补空串）；
2. stamp 不存在 / 不是 JSON / 不是对象 / `commit` 缺失或空 → 回退 `git rev-parse HEAD`
   （`repo_root` 目录里跑，失败即空）→ 成功时 `source = "git"`、`branch` 取
   `git rev-parse --abbrev-ref HEAD`；
3. stamp 与 git 都拿不到 → `commit = branch = ref = ""`、`deployed_at = ""`、
   `source = UNKNOWN`；
4. **任何情况都不抛异常**（health 绝不能因为版本读不到而 500）；
5. 返回值的键集**恒等于** `BUILD_KEYS`，所有值都是字符串（`deployed_at` 用 stamp 里的原文）。

### 2.2 `/api/health` 增加 `build` 段

- 顶层新增 `"build": build_identity.build_info()`，键名固定 `build`；
- 其余字段一个不动（这是免登录接口，前端与守卫都读它）；
- 读不到版本时 `build.commit` 必须是 `""` 或 `"unknown"`，**不许**让 health 报错。

### 2.3 `scripts/deploy_34_bare.sh`

- 取到代码后（第 2 步之后）写 stamp：路径 `${CPQ_BUILD_STAMP:-$(dirname "$REPO")/cpq_build.json}`，
  内容 `{"commit": <full sha>, "branch": <当前分支>, "ref": <本次部署的 ref>, "deployed_at": <ISO 时间>}`；
  写法用脚本里已有的 `python3`/`python -` 内联（与写 env 文件同一套路，不引入新依赖）；
- 读回 stamp 的 `commit` 存进变量 **`BUILD_COMMIT`**，`git rev-parse HEAD` 存进 **`HEAD_COMMIT`**，
  两者**必须比对**：不一致 → `fail`（非零退出）。变量名是指定值（脚本自检与红测都按它读）；
- 启动命令必须带上 `CPQ_BUILD_STAMP=<stamp 路径>`（与既有 `PATH` 前缀同一处写法）；
- 结论行必须打印 `build.commit`（可读的一行），便于事后对账。

### 2.4 `DEPLOYMENT.md` 登记

必须登记：stamp 的路径与生成者、`CPQ_BUILD_STAMP` 的作用、以及**外部核对命令**：

```bash
curl -s http://127.0.0.1:8010/api/health | python3 -c "import json,sys; print(json.load(sys.stdin)['build'])"
cd <部署目录> && git rev-parse --short HEAD     # 两者必须一致
```

## 3. 红测映射（`tests/test_deploy_build_identity_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：`STAMP_ENV` / `STAMP_FILENAME` / `BUILD_KEYS` / `UNKNOWN` |
| B | `build_info()` 行为：读 stamp、缺文件回退 git、坏 JSON 不抛、缺 `commit` 回退、空串回退、env 优先、键集恒定、不抛异常 |
| C | health：源码里 `build` 段存在且调用 `build_info()`，其余键不动 |
| D | 部署脚本：`CPQ_BUILD_STAMP`、写 stamp、比对 HEAD、不一致 fail、结论打印 build commit |
| E | `DEPLOYMENT.md`：登记 stamp 路径、`CPQ_BUILD_STAMP` 与外部核对命令 |

## 4. 非目标

- 不引入构建流水线 / 版本号体系（只暴露**已部署的 commit**，不改版本策略）；
- 不改鉴权、端口、数据目录、转换器 env（`cpq_env.sh` 的既有变量一个不动）；
- 不读、不改、不提交任何运行数据（stamp 落在仓库外，且不进 Git）；
- 不在红测里启动服务、不连 PG、不发 HTTP（`git` 用真实仓库根，其余用临时目录 fixture）。
