# cpq_agent 部署说明

> 本文是受控操作说明，不构成自动部署授权。只有用户在当前任务明确指定环境和版本后才能执行。

## 发布链路

开发分支为 `20260909`，GitLab `master` 是唯一发布主线。正式部署优先使用已经在 `master` 上定版并推送的 tag；临时验证部署必须明确记录 commit。

目标服务端口为 `8010`，技术工艺子服务内部使用 `8012`，产品图片子服务使用 `8011`。部署不得删除或覆盖服务器上的模型设置、历史会话、上传文件和数据库数据。

## 部署前检查

1. 明确目标环境和 tag/commit。
2. 确认目标已存在于 GitLab，工作区没有未提交修改。
3. 运行相关测试、Python 语法检查和 `git diff --check`。
4. 备份并保留服务器 Git 忽略的设置、历史和上传目录。
5. 确认 `8010` 的当前进程或容器，避免启动第二套服务抢占端口。
6. 配置 `CPQ_USER_SECRET_KEY`（32 字节的 base64 或 hex）：账号级模型与 API Key 的
   **加密材料**。账号级设置以密文落在 `cpq_wf.cpq_wf_user_llm_setting`，没有它就保存不了
   （接口按 503 明确拒绝，绝不明文落库）。**启用后不可更换** —— 换掉之后已经存过个人 Key
   的账号连读取都会失败。
7. 确认 `CPQ_INTERNAL_TOKEN` 在场：服务间通道（知识库快照 `/wf/tech/kb/snapshot`、
   账号级设置的内部读写）只认它。8010 启动时会自动生成一把并注入技术工艺子进程；
   多实例或需要固定令牌时再用环境变量显式指定。

这两个变量放在**仓库外**的 env 文件里（例如 `/home/wugefei/CPQ/cpq_env.sh`，权限 `0600`），
不要写进仓库（`.env` 已在 `.gitignore`）。两种等价用法：

```bash
set -a; . /home/wugefei/CPQ/cpq_env.sh; set +a    # 显式导出，优先于文件内容
# 或：export CPQ_ENV_FILE=/home/wugefei/CPQ/cpq_env.sh   # 由 8010 自己读这个文件
```

`cpq_suite_server.py` 启动时按「`CPQ_ENV_FILE` → 仓库根 `.env`」的顺序读取（`export KEY=VALUE`
写法也认），**已 export 的同名变量优先、不被文件覆盖**；文件不存在不算错误。env 文件放在仓库
之外，`git pull` / `git checkout` 碰不到它，重启时也不会丢。

## 服务器约定

- 主机：`172.16.10.34`
- 默认目录：`/home/data/zhangzhen_home/zhangzhen/cpq_agent`（可用 `CPQ_DEPLOY_ROOT` 覆盖）
- GitLab：`git@gitlab.boulderaitech.com:ai-team/cpq_agent.git`
- 服务端口：`8010`

获得明确授权后，在服务器部署目录执行：

```bash
CPQ_DEPLOY_REF=vX.Y.Z bash scripts/deploy_server.sh
```

脚本只允许 fast-forward/确定 tag；部署前会逐一确认以下宿主路径已存在，缺一即非零退出。脚本不会自动创建空目录，避免空目录遮住镜像内容、让人误以为旧数据还在：

- `cpq_settings.json`；
- `cpq_history/`、`xbom_history/`、`rule_history/`；
- `tech_app/tech_data/`（技术工艺项目、需求、IR、报告、任务、审计、附件与本地库）；
- `product_images/`（上传/替换的产品图片）。

`docker-compose.yml` 用 bind mount 把上述运行数据挂到容器内同一路径，容器重建、替换、重启都不会丢数据；不使用匿名 volume。Docker 守护进程异常时停止并报告，不自行重启整台主机或清理 Docker 数据。

部署成功必须同时满足三个条件：`http://127.0.0.1:8010/` 返回 2xx；`http://127.0.0.1:8010/api/health` 返回 2xx；`/api/health` 的 JSON 字段 `status` 严格等于 `ok`。该接口经父服务反向代理到技术工艺 FastAPI 子服务，是子服务启动完成的就绪信号，只看首页会在子服务未启动时误报成功。容器自身也配置了同样的 healthcheck（用运行镜像自带的 Python 标准库解析 JSON，不依赖 `curl`）。达到超时仍不健康时会打印 `docker compose logs --tail=100 cpq-suite` 并非零退出，不删除旧数据、不清理 volume。

## 线上实例现状（172.16.10.34，2026-09-16 只读核对）

上面那套是**容器化**路径；这台机器上真正在跑的是另一条，换机器或换人前请重新核对本节。

- 服务形态：**裸进程**，不走 `docker compose`。`8010` 由 `wugefei` 运行
  `./open-claude/.venv/bin/python cpq_suite_server.py --host 0.0.0.0 --port 8010`，父进程再拉起子进程
  `tech_app_launch.py --host 127.0.0.1 --port 8012`（父进程退出会带走子进程）。
- 代码目录：`/home/wugefei/CPQ/cpq_agent`（该目录 `origin` 指向 GitHub），当前 `4b35a25`；
  另有 `/home/wugefei/CPQ2/cpq_agent`（带 `Dockerfile` / `docker-compose.yml`，不是当前线上实例）。
- 本文默认目录 `/home/data/zhangzhen_home/zhangzhen/cpq_agent` 在这台机器上**不存在**；照上面那条
  `CPQ_DEPLOY_REF=... bash scripts/deploy_server.sh` 直接执行会先失败在目录与 `origin` 校验
  （脚本要求部署目录 `origin` 是 CPQ GitLab）。
- 权限：`zhangzhen` 账号对上面两个目录**不可写**，`sudo` 需要密码。部署与重启必须在 `wugefei` 账号
  （或等价授权）下进行。
- **8010 重启前先确认 `CPQ_USER_SECRET_KEY` 与 `CPQ_INTERNAL_TOKEN` 在场**（`tr '\0' '\n' < /proc/<8010 pid>/environ | grep CPQ_`）：
  缺加密密钥时保存账号级模型与密钥会回 503（文案点名 `CPQ_USER_SECRET_KEY`），而重启时若漏掉那把密钥，
  已经存过个人 Key 的账号连读都会失败；env 文件放在仓库外并用 `CPQ_ENV_FILE` 指过去，可避免"重启后忘了 export"。
- 发布门禁：`scripts/deploy_server.sh` 要求目标 commit 是 GitLab `master` 的祖先。开发分支 `20260909`
  上的临时验证部署**必须先合并到 `master`**（或显式改用下面裸进程路径），否则脚本会以
  「目标不在 GitLab master 历史中」拒绝。

裸进程形态的临时验证部署（**须先获得明确授权**；重启会打断在途任务）：

```bash
cd /home/wugefei/CPQ/cpq_agent
git fetch --prune origin
git checkout --detach "$CPQ_DEPLOY_REF"     # 例：git checkout --detach a4bd13c
# 重启：先 `ps -o args= -p <8010 的 pid>` 把当前启动命令行原样抄下来，停掉旧进程后按同一命令行重启；
# 不要改写参数，也不要另起第二套端口。
curl -s http://127.0.0.1:8010/api/health   # JSON 的 status 必须严格等于 ok
```

上面那三条健康检查（首页 2xx、`/api/health` 2xx、`status == "ok"`）对裸进程这条路径同样适用。
