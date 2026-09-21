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

## 线上实例现状（172.16.10.34，2026-09-21 11:15 部署后核对）

上面那套是**容器化**路径；这台机器上真正在跑的是另一条，换机器或换人前请重新核对本节。

- 服务形态：**裸进程**，不走 `docker compose`。`8010` 由 `wugefei` 运行
  `./open-claude/.venv/bin/python cpq_suite_server.py --host 0.0.0.0 --port 8010`，父进程再拉起子进程
  `tech_app_launch.py --host 127.0.0.1 --port 8012`（父进程退出会带走子进程）。
- 代码目录：`/home/wugefei/CPQ/cpq_agent`，分支 **`ytbz`**，当前 **`d0504f3`**（2026-09-21 部署；此前
  长期停在 `20260909` 的 `c71679b`，2026-09-20 起改为部署 `ytbz`）（该目录 `origin` 指向
  GitHub、`gitlab` 指向内网 `http://gitlab.boulderaitech.com/ai-team/cpq_agent.git`；**部署从
  `gitlab` 取、fetch 不需要 SSH key**）；
  另有 `/home/wugefei/CPQ2/cpq_agent`（带 `Dockerfile` / `docker-compose.yml`，不是当前线上实例）。
- 当前进程（2026-09-21 11:15 重启后）：8010 `cpq_suite_server.py` PID **1515687**（PPID 1，已脱离会话）、
  8012 `tech_app_launch.py` PID **1515764**（父进程 1515687 拉起）；旧日志按次归档为
  `nohup.out.prev.<时间戳>`；本次回滚记录写在 `/home/wugefei/CPQ/deploy_prev_before_d0504f3.txt`
  （内容是部署前的分支与 HEAD）。
- 本文默认目录 `/home/data/zhangzhen_home/zhangzhen/cpq_agent` 在这台机器上**不存在**；照上面那条
  `CPQ_DEPLOY_REF=... bash scripts/deploy_server.sh` 直接执行会先失败在目录与 `origin` 校验
  （脚本要求部署目录 `origin` 是 CPQ GitLab）。
- 权限：`zhangzhen` 账号对上面两个目录**不可写**，`sudo` 需要密码；本机对 `wugefei` 没有可用私钥
  （三个 key 都被拒）。实测可用的路径是**用户提供 `wugefei` 密码**，用系统自带 `/usr/bin/expect`
  写一个只做密码登录的临时包装脚本，把部署脚本从 stdin 管道给远端 `bash -s` 执行（不要用
  `sshpass`，本机没有；不要把密码写进仓库或任何入库文件）。
- 以 `zhangzhen` 身份跑 `git log` 可能报某个松散对象「已损坏」：先看该文件是不是 `-r--------`（mode `400`、
  仅 owner 可读）—— 那是权限不足被 git 误判，不是真损坏；以 `wugefei` 身份 `git fsck --no-progress`
  只应有悬空 blob。
- **8010 重启前先确认加密材料在场**：`/home/wugefei/CPQ/cpq_env.sh`（`0600 wugefei`）里要有
  `CPQ_USER_SECRET_KEY`，重启命令行用 `CPQ_ENV_FILE` 指过去即可（`CPQ_INTERNAL_TOKEN` 不在该文件里，
  由 8010 启动时自动生成并注入子进程，属正常）：
  缺加密密钥时保存账号级模型与密钥会回 503（文案点名 `CPQ_USER_SECRET_KEY`），而重启时若漏掉那把密钥，
  已经存过个人 Key 的账号连读都会失败；env 文件放在仓库外并用 `CPQ_ENV_FILE` 指过去，可避免"重启后忘了 export"。
- 发布门禁：`scripts/deploy_server.sh` 要求目标 commit 是 GitLab `master` 的祖先。开发分支 `20260909`
  上的临时验证部署**必须先合并到 `master`**（或显式改用下面裸进程路径），否则脚本会以
  「目标不在 GitLab master 历史中」拒绝。

裸进程形态的临时验证部署（**须先获得明确授权**；重启会打断在途任务）：

```bash
cd /home/wugefei/CPQ/cpq_agent
git -c safe.directory=$PWD fetch --prune gitlab 20260909
git -c safe.directory=$PWD merge --ff-only FETCH_HEAD   # 纯快进，不产生合并提交；先确认 tracked 改动为 0
# 重启顺序固定「先停 8012 子进程、再停 8010 父进程」，轮询到 8010 / 8012 端口释放后用原命令行重启
# （只多 CPQ_ENV_FILE=/home/wugefei/CPQ/cpq_env.sh 前缀），由父进程重新拉起子进程：
#   mv nohup.out nohup.out.prev.$(date +%Y%m%d-%H%M%S)
#   CPQ_ENV_FILE=/home/wugefei/CPQ/cpq_env.sh setsid nohup ./open-claude/.venv/bin/python \
#     cpq_suite_server.py --host 0.0.0.0 --port 8010 >> nohup.out 2>&1 < /dev/null &
# 不要改写启动参数，也不要另起第二套端口。
curl -s http://127.0.0.1:8010/api/health   # JSON 的 status 必须严格等于 ok
```

部署脚本 `/tmp/deploy_857b7e0_34.sh` 就是按上面这条链路写的（快进 → 归档日志 → 先子后父停服务 →
带 `CPQ_ENV_FILE` 重启 → health 轮询 → 打印新 PID 与 HEAD），可作为模板复用。

上面那三条健康检查（首页 2xx、`/api/health` 2xx、`status == "ok"`）对裸进程这条路径同样适用。
