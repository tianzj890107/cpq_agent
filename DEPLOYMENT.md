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
