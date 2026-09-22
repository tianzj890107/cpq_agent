# CPQ 容器部署：运行数据持久化与完整健康检查 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_deployment_runtime_data_persistence_and_health_red.py`

## 1. 背景与已确认缺陷

当前一体化容器会把两类可变业务数据写进容器文件系统：

- `tech_app_launch.py` 将 `DATA_DIR` 设为 `/app/tech_app/tech_data`，其中包含技术工艺
  项目、需求、IR、报告、任务、审计、附件及本地数据库等运行数据；
- `cpq_image_server.py` 将用户上传或替换的产品图片写入 `/app/product_images`。

`docker-compose.yml` 目前只挂载 `cpq_settings.json` 和三个报价助手历史目录，没有挂载
上述两个目录。执行 `docker compose up -d --build` 重建容器后，技术工艺数据会丢失，
产品图片会恢复为镜像构建时的内容。

同时，部署脚本只请求首页 `/`。首页由父服务提供，即使技术工艺子进程没有启动，首页
仍可能返回 200，导致部署脚本误报成功。

## 2. 目标

1. 技术工艺运行数据在容器重建、替换和重启后保持不变；
2. 产品图片在容器重建、替换和重启后保持不变；
3. 部署成功必须同时证明父服务首页和技术工艺子服务健康；
4. 不删除、清空、覆盖或自动迁移任何现有运行数据。

## 3. 持久化契约

`docker-compose.yml` 必须增加以下两个明确的 bind mount，宿主与容器路径一一对应：

```yaml
- ./tech_app/tech_data:/app/tech_app/tech_data
- ./product_images:/app/product_images
```

要求：

- 不使用匿名 volume；否则无法从宿主机直接核对和备份数据；
- 不改变应用当前使用的容器内路径；
- 不在容器启动时清空、覆盖或用镜像样例重新初始化已存在的宿主目录；
- `docker compose config` 展开后必须能看到这两个宿主 bind mount；
- 现有 `cpq_settings.json`、`cpq_history`、`xbom_history`、`rule_history` 挂载保持不变。

## 4. 部署前数据守卫

`scripts/deploy_server.sh` 在执行 `docker compose up` 前必须确认以下路径已经存在：

- `cpq_settings.json`；
- `cpq_history/`、`xbom_history/`、`rule_history/`；
- `tech_app/tech_data/`；
- `product_images/`。

缺少任一路径时必须非零退出，并明确打印缺失路径；不得在脚本中静默创建空目录，因为
空目录挂载会遮住镜像内容，并可能让操作者误以为旧数据仍在。

## 5. 完整健康检查契约

部署脚本只有在以下条件同时满足时才能打印“部署成功”并退出 0：

1. `GET http://127.0.0.1:${CPQ_DEPLOY_PORT}/` 返回 2xx；
2. `GET http://127.0.0.1:${CPQ_DEPLOY_PORT}/api/health` 返回 2xx；
3. `/api/health` 返回的 JSON 中 `status` 严格等于 `ok`。

`/api/health` 经父服务反向代理到技术工艺 FastAPI 子服务，因此它是技术工艺启动完成的
就绪信号。只检查 `/`、只检查容器进程存在或把任意小于 500 的响应视为成功都不合格。

`docker-compose.yml` 同时应配置容器 `healthcheck`，目标为容器内
`http://127.0.0.1:8010/api/health`，并校验 JSON 的 `status == "ok"`。健康检查不得依赖
镜像中未安装的 `curl`；可使用运行镜像已有的 Python 标准库。

达到超时时间仍不健康时，部署脚本必须：

- 打印 `cpq-suite` 最近日志；
- 非零退出；
- 不删除旧数据、不清理 volume、不执行 Docker 全局清理。

## 6. 范围边界

- 不修改 `20260909`、`master`、MR、tag、Release 或 CI 规则；
- 不修改技术工艺业务逻辑、接口路径、认证、角色或数据格式；
- 不启动、停止或部署服务器服务；
- 不读取、改写或提交任何运行数据和密钥；
- 不把 `tech_app/tech_data` 或新增运行图片提交进 Git。

## 7. 验收命令

```bash
python3 -m unittest tests.test_deployment_runtime_data_persistence_and_health_red -v
python3 -m unittest discover -s tests -p 'test_*.py'
docker compose config
git diff --check
```

实现前，第一条命令必须因两个持久化挂载、两个部署前目录守卫、`/api/health` 就绪验证及
Compose healthcheck 尚未实现而失败；实现后全部通过。
