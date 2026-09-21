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

## DWG 转换器（包装图纸）

线上「＋ → 补充需求图纸」上传 DWG 时只能提示「不是位图、请传 PNG」，根因不是代码：`cad_converter`
已实现且红测全绿，**是 8010 没配 `DWG_CONVERTER_*`**，而部署文档从没写过这段——既然没写，漏配就没人在
部署时发现。**本节补齐这一环**：按下面配好、按「上线三件套」验过，才算这台机器上线成功。

本节是**唯一配置口径**。env 名必须取自代码常量（`tech_app/backend/services/cad_converter/service.py`
的 `PROVIDER_ENV` / `BINARY_ENV` / `VERSION_ENV` / `PREVIEW_ENV` / `WRAPPER_ENV` /
`FALLBACK_PROVIDER_ENV` / `FALLBACK_BINARY_ENV` / `FALLBACK_VERSION_ENV` /
`FALLBACK_PREVIEW_ENV` / `FALLBACK_WRAPPER_ENV`），**不要手抄字符串**——手抄一定会漂移。
十个常量一个都不能漏：漏掉 `PREVIEW_ENV` 会让健康检查说"有预览"而实际产物没有（见下节「预览渲染」）。

本机与 34 上的转换器已完成安装与质量对比（两份真实样本二维几何一致、渲染 0 像素差、ODA 的 DXF
结构更干净），业务/法务已确认 ODA 可用于本 CPQ 生产环境。**结论固定：ODA 主用，仅在主转换器
明确失败时回退 LibreDWG。**

### 配置取值（172.16.10.34）

| 常量 | 环境变量名 | 34 取值 |
| --- | --- | --- |
| `PROVIDER_ENV` | `DWG_CONVERTER_PROVIDER` | `oda` |
| `BINARY_ENV` | `DWG_CONVERTER_BINARY` | `/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun` |
| `VERSION_ENV` | `DWG_CONVERTER_VERSION` | `27.1` |
| `WRAPPER_ENV` | `DWG_CONVERTER_WRAPPER` | `/home/data/cpq-tools/xvfb-user/root/usr/bin/xvfb-run -a` |
| `FALLBACK_PROVIDER_ENV` | `DWG_CONVERTER_FALLBACK_PROVIDER` | `libredwg` |
| `FALLBACK_BINARY_ENV` | `DWG_CONVERTER_FALLBACK_BINARY` | `/home/data/cpq-tools/current/bin/dwg2dxf` |
| `FALLBACK_VERSION_ENV` | `DWG_CONVERTER_FALLBACK_VERSION` | `0.14` |
| `PREVIEW_ENV` | `DWG_CONVERTER_PREVIEW_BINARY` | `/home/data/cpq-tools/current/bin/dwg2SVG`（**必配**，见下节） |
| `FALLBACK_PREVIEW_ENV` | `DWG_CONVERTER_FALLBACK_PREVIEW_BINARY` | 同上（回退跳次同样要预览） |
| `FALLBACK_WRAPPER_ENV` | `DWG_CONVERTER_FALLBACK_WRAPPER` | 留空（libredwg 是命令行程序，不需要 xvfb） |
| `LEGACY_PROVIDER_ENV`（旧名，仍兼容） | `CAD_CONVERTER` | **不要再配**；同时配且冲突会写一条 `converter_config_shadowed` 告警 |

口径要点：

- ODA 的 argv 形状固定 7 个位置参数：`<exe> <inDir> <outDir> ACAD2018 DXF 0 1 *.dwg`
  （`0` = 不递归，`1` = 开启 Audit/Repair，`*.dwg` 逐字给出、不经 shell 展开）。
  输出格式 `DXF`、输出版本 `ACAD2018`、`audit_enabled=true`。
- Linux 版 ODA 即使命令行运行也依赖 X Display，**必须经 `xvfb-run -a` 包装**：wrapper 逐项排在
  exe 之前。而 `xvfb-run` 自己还是个 shell 脚本、会按名字去调同目录的 `Xvfb` / `xauth`，所以
  **那个目录必须出现在 8010 进程的 `PATH` 里**（写法见下节「PATH 与运行用户权限」）。漏了这一条
  ODA 会启动即崩溃，然后**静默回退到 LibreDWG** —— 转换照样成功，只有 manifest 的
  `converter_role` 会变成 `fallback`。
- 回退**默认关闭**：不显式配 `DWG_CONVERTER_FALLBACK_PROVIDER` 就没有回退；**绝不把「这台机器
  恰好装了另一个转换器」当成可用回退**。
- 回退**只在主转换器明确失败**时才触发（超时 / 非 0 退出 / 产物缺失 / 质量门槛不过 / 主二进制
  缺失或不可执行 / 主版本不匹配）；`wrapper_invalid`、`binary_is_interpreter` 这类配置问题与
  输入类问题（路径越界、源超限、格式识别失败）**绝不回退**。
- `provider` 名只能取 `KNOWN_PROVIDERS`；配 `oda` 时 `capability()["provider"]` 必须是 `oda`，
  `capability()["fallback"]["provider"]` 必须是 `libredwg`。

### 生效方式

上述变量写在**仓库外**的 env 文件（权限 `0600`，如 `/home/wugefei/CPQ/cpq_env.sh`），或用
`CPQ_ENV_FILE` 指向它，**重启 8010** 后生效；**不得写进仓库**（`.env` 已在 `.gitignore`）。
`8010` 启动时会注入技术工艺子进程，子进程无需另配。

### PATH 与运行用户权限

**这一节在 34 上线时真的漏过，症状就是「DWG 解析不了、只能看 PNG」。**

- `xvfb-run` 所在目录 `/home/data/cpq-tools/xvfb-user/root/usr/bin` 必须在**服务进程**可见的
  `PATH` 里：`xvfb-run` 是个 shell 脚本，它按名字去调同目录的 `Xvfb` / `xauth`；`PATH` 里没有
  这个目录时 `Xvfb` 起不来，Qt 报 `could not connect to display :NN`，ODA 非 0 退出。
- **只写进 env 文件是不够的 —— 服务侧不会生效**：8010 用 `load_dotenv(..., override=False)` 读
  env 文件，而 `PATH` 在进程里**本来就存在**，`override=False` 表示文件里的同名值被**静默忽略**。
  34 实测（2026-09-21，env 文件里已经写了 `export PATH=...xvfb-user/root/usr/bin:...`）：

  ```
  只给 CPQ_ENV_FILE 启动 → in-process PATH = /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  ```

  文件里那一行**对服务没有生效**（它只对「先 `source` 一遍再跑」的命令行工具起作用，见本节最后
  一条），所以启动命令必须**显式前缀**（`$PATH` 由启动它的 shell 展开，不依赖任何个人 shell 的
  rc 文件）。这条链路已经固化进 `scripts/deploy_34_bare.sh`，**上线就用它，不要手敲**：

  ```bash
  cd /home/wugefei/CPQ/cpq_agent
  PATH=/home/data/cpq-tools/xvfb-user/root/usr/bin:$PATH \
  CPQ_ENV_FILE=/home/wugefei/CPQ/cpq_env.sh \
  setsid nohup ./open-claude/.venv/bin/python cpq_suite_server.py --host 0.0.0.0 --port 8010 \
    >> nohup.out 2>&1 < /dev/null &
  ```

  核对（这是唯一能一眼看出有没有生效的地方，`/proc` 里读到的是 exec 期的真实环境）：

  ```bash
  PID=$(pgrep -f 'cpq_suite_server.py --host 0.0.0.0 --port 8010' | head -1)
  tr '\0' '\n' < /proc/$PID/environ | grep '^PATH=' | tr ':' '\n' | head -3
  # 第一行必须是 /home/data/cpq-tools/xvfb-user/root/usr/bin
  ```

  前后对照（34 实测，同一份 `酒盒.dwg`、同一套配置，只差 8010 的 `PATH`）：

  | 8010 的 `PATH` | `converter_role` | `quality.output_version` | `quality.audit_enabled` | DXF 名 | 警告数 |
  | --- | --- | --- | --- | --- | --- |
  | 缺 xvfb 目录 | `fallback` | `""` | `false` | `converted.dxf` | 1520 |
  | 含 xvfb 目录 | `primary` | `ACAD2018` | `true` | `source.dxf` | 0 |

  两行都是 `status=ok`、都有 DXF 与预览 —— **只有 `converter_role` / `output_version` /
  `audit_enabled` 分得出主转换器有没有真的在干活**。所以验收口径是
  「`status` 通过 **且** `converter_role="primary"`、`fallback_used=false`」，不许只看 `status`。
- 运行 8010 的用户对 `/home/data/cpq-tools` 及其子目录必须**可读可执行**（ODA 的 squashfs 内还有
  同目录依赖库要一起可读），不依赖任何个人账号的环境变量。
- 命令行工具（上线三件套）自己**都不读** env 文件，跑之前先把 env 文件 export 进当前 shell，
  否则它们看到的是一台「没装转换器」的机器：

  ```bash
  set -a; . /home/wugefei/CPQ/cpq_env.sh; set +a
  ```

### 预览渲染（2.1 视觉解析的前提，**34 上最容易漏配的一项**）

2.1 图纸解析把图纸交给视觉模型，靠的是转换时同时产出的**预览图**（`converted.svg`）。转换器
和预览渲染器是**两个独立的东西**：

- ODA/`AppRun` 只产出 DXF，**不产预览**；预览由 LibreDWG 的 `dwg2SVG` 渲染 —— 它读的是
  **原始 DWG**（`dwg2SVG --mspace <source.dwg>`），与 DXF 是哪台转换器出的无关。
- 不配 `PREVIEW_ENV` 时，代码按「转换器**同目录**下的 `dwg2SVG`」兜底；ODA 的 squashfs 目录里
  没有这个程序 → 主转换器**只能出 DXF、出不了预览**。实测：

  ```
  preview_binary_for('/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun', '')  →  ''
  preview_binary_for('/home/data/cpq-tools/current/bin/dwg2dxf', '')                            →  '/home/data/cpq-tools/current/bin/dwg2SVG'
  ```

  本机（转换器就是 libredwg）会自动兜到同目录的 `dwg2SVG`，**所以本地看起来一切正常、34 上却
  仍然没有图** —— 这正是「本地跑得通、线上跑不通」的典型。

- 后果有两层，都很难从界面看出来：
  1. 该次转换只写一条「该转换器不支持预览渲染：本次只产出 DXF」的告警，DXF 照出，2.1 拿不到图；
  2. 而 `capability()` 的 `preview_render` 会因为**回退侧**能渲染而报 `true` —— 健康检查说"有
     预览"，实际产物里一份预览都没有。现场「DWG 只能看 PNG / 不是位图」就是这一路的症状。

因此 34 **必须**显式配 `DWG_CONVERTER_PREVIEW_BINARY`（回退跳次再配 `FALLBACK_PREVIEW_ENV`）。

> **配之前先验路径**：显式给的预览路径**不做存在性校验**，写错会把上面那条"只出 DXF 的告警"
> 升级成**转换整体失败**（渲染不出预览 → `DWG_CONVERTER_OUTPUT_MISSING`，fail-closed）。
> 所以先确认它在、且可执行：

```bash
test -x /home/data/cpq-tools/current/bin/dwg2SVG && echo "预览渲染器在位"
printf '' | /home/data/cpq-tools/current/bin/dwg2SVG --version        # 能打印版本即可
```

配好、重启 8010 后，拿一份真实样本走一次转换，manifest 的 `output_files` 里应**两个 role 各一份**：
`role="dxf"` 一份 + `role="preview"` 一份（预览恒为 `converted.svg`；**DXF 的文件名随转换器而定**
—— libredwg 出 `converted.dxf`，ODA 走「按输入文件名出图」所以是 `source.dxf`，两者都对，别因为
名字不同判成"没出 DXF"）。`/api/health` 的 `cad_converter.preview_render` 必须为 `true`，
且与 manifest 里实际产出的预览一致。

### 健康检查与期望输出

```
curl -s http://127.0.0.1:8010/api/health        # JSON 的 status 必须严格等于 ok
```

health 里的 `cad_converter` 段直接来自 `cad_converter.capability()`，34 上的期望值：

| 字段 | 期望 |
| --- | --- |
| `available` | `true` |
| `provider` | `oda`（永远是**主** provider，不随跳次变） |
| `converter_version` | `27.1` |
| `fallback.enabled` | `true` |
| `fallback.provider` | `libredwg` |
| `support_claim` | `orchestration_only` 或 `conversion_available`（**不是** `supported`） |
| `preview_render` | `true`（**必须与产物一致**：报 true 就必须真的出 `converted.svg`） |
| `dwg_supported` | `false`（未通过两份真实样本 L4 E2E 前恒为假） |

> **两份样本必须用两个不同的 `--out` 目录**：每次运行都产出同名 `converted.dxf` / `converted.svg`，
> 共用一个目录会把前一份证据**静默覆盖**掉（实测：同目录连跑两次后只剩后一份 dxf，sha256 已变）。
> 证据覆盖等于没有证据。

`output_version=ACAD2018` 与 `audit_enabled=true` **不在 `capability()` 里**，在每次转换的
manifest 的 `quality.output_version` / `quality.audit_enabled` 上核对（Spec §2.1 允许
「capability 或 manifest」二选一）。

### 上线三件套

```bash
# ⓪ 先把仓库外的 env 文件 export 进当前 shell（三件套都不自己读 env 文件）
set -a; . /home/wugefei/CPQ/cpq_env.sh; set +a
# ① A/B 两层冒烟（未装转换器时必须 SKIP 并打印 A/B 层结论，不得伪装通过）
python tech_app/tools/dwg_conversion_smoke.py
# ② 两份真实样本各跑一次（只读样本、只写 --out）
python tech_app/tools/dwg_sample_e2e.py --sample 裕同包装项目-待开发/酒盒.dwg   --out /tmp/dwg-evidence/jiuhe
python tech_app/tools/dwg_sample_e2e.py --sample 裕同包装项目-待开发/圆盘盒.dwg --out /tmp/dwg-evidence/yuanpanhe
# ③ 生产门禁（verdict 必须为 go 才允许宣称上线）
python tech_app/tools/dwg_deploy_gate.py --env production
```

三件套里 ① 会打印每个样本的完整 manifest，**必须逐份核对 `converter_role`**：

```
"converter_role": "primary",      # ← 必须是 primary；fallback 说明主转换器没生效（先查 PATH）
"fallback_used": false,
"status": "ok",
quality.output_version = "ACAD2018";  quality.audit_enabled = true;  quality.verified = true
output_files: role=dxf 一份 + role=preview 一份
```

### 配好之后自己怎么验证（谁都能跑，不看人）

```bash
# 1) 服务起来了、能力声明对
curl -s http://127.0.0.1:8010/api/health | python -m json.tool | head -40
# 2) 8010 的 PATH 里确实有 xvfb 目录（见「PATH 与运行用户权限」）
PID=$(pgrep -f 'cpq_suite_server.py --host 0.0.0.0 --port 8010' | head -1)
tr '\0' '\n' < /proc/$PID/environ | grep '^PATH=' | grep -c '/home/data/cpq-tools/xvfb-user/root/usr/bin'
# 3) 真转一次（两份样本），看 converter_role 是不是 primary
set -a; . /home/wugefei/CPQ/cpq_env.sh; set +a
cd /home/wugefei/CPQ/cpq_agent
python tech_app/tools/dwg_conversion_smoke.py
# 4) 生产门禁给出可上线结论
python tech_app/tools/dwg_deploy_gate.py --env production
```

34 于 2026-09-21 的实测结果（配置与本文件一致时应能复现）：

| 样本 | `source_sha256`（前 16） | `converter_role` | `status` | entity / layer / dim / text / block | DXF 名 | 预览 sha256（前 16） |
| --- | --- | --- | --- | --- | --- | --- |
| `酒盒.dwg` | `0991c8b0a9646d1f` | `primary`（ODA 27.1） | `ok` | 6711 / 8 / 316 / 127 / 0 | `source.dxf` | `a80cb58eef06a0c7` |
| `圆盘盒.dwg` | `4c70ce7b3774c2a1` | `primary`（ODA 27.1） | `ok` | 3457 / 32 / 141 / 87 / 234 | `source.dxf` | `7c708b81c864fd5b` |

两份预览 sha256 与 `tests/fixtures/dwg_acceptance/2026-09-21.1/manifest.json`（LibreDWG 采集的
金标）**逐位一致**：换主转换器不改变可视化结果，几何统计也逐项相同。

### 宣传口径与两条硬禁令

- 在**两份真实样本 L4 E2E 通过且验收记录经人工审批**之前，只允许写
  「**DWG 编排能力完成，真实转换能力未验收**」，不得写「支持 DWG」。
- **DWG 原始字节不得发给模型**（任何视觉模型都不行）。
- **DWG 不得交给 STEP importer**。
- PNG 截图、视觉模型给的像素推算值、文件名语义都**不是**转换证据；`is_simulated=true` 的产物
  永远不能作为 B 层（真实转换）证据。

### 失败时的正确行为

- 二进制不存在/不可执行 → `available=false` + 非空 `stable_error_code` + 页面可见提示。
- 配了 `DWG_CONVERTER_PROVIDER=oda` 但二进制路径不存在 → **不得**自动改用别的适配器（除显式
  配置的回退链）。
- 主失败走回退 → manifest 必须落 `fallback_used=true` + 非空 `primary_failure_code`。
- 转换整体失败 → 页面与会话必须显式报错，**不得**退化成交给视觉模型看图猜尺寸、不得把 DWG 交给
  STEP importer、不得宣称「已解析」。

## 知识库（cpq_kb）上线

线上「包装知识库检索 503」与「`4.1` 两个零件成本全失败」的根因是同一件事：34 的 Postgres 里
`cpq_kb` **没有建表/没有数据**（报错原文 `relation "cpq_kb.kb_packaging_box_type" does not exist`），
而部署文档从没写过这段——既然没写，漏做就没人在部署时发现。**本节补齐这一环。**

口径：`cpq_kb` 是包装知识库的唯一事实源，建表、版本号、快照、导入都在 `cpq_kb.py`
（DDL 与 `tech_app/backend/storage/da_schema.sql` 的 `kb_*` 定义 1:1，共 **30** 张表 ——
9-21 追加第 30 张 `kb_quick_quote_config` 与第 31 张 `kb_quick_quote_match_weight`，
见「快速报价（标准案例库）」一节）。
技术工艺（8012）不直连 PG，只读快照；`kb_version` 是快照失效的唯一依据。

### 落地顺序（在能连到 PG 的机器上执行）

```bash
# ① 建 schema / 29 张 kb_* 表 / kb_meta / 增量列（幂等，可反复执行）
./open-claude/.venv/bin/python -c "import cpq_kb; cpq_kb.ensure_schema(); print(cpq_kb.kb_version())"

# ② 确认源库先有包装数据 —— 导入源是 tech_app/tech_data/da.db，本机实测它只有 20 张
#    基础表（9 张包装表一张都没有）；直接导入只会建出 9 张**空表**，预检会判
#    empty_required_table。先补一次包装 seed（在 tech_app/ 目录下执行；只动本地 da.db）：
#      python -m backend.storage.da_seed_packaging
#    生产要的是 source_type IN ('workbook','dwg_confirmed') 的权威行，演示行只用于演示与
#    回归；灌演示数据上线会被预检的 demo_only 拦住。

# ③ 导入前留底（只读快照 → JSON，回滚就靠它；不写库、不动 kb_version）
./open-claude/.venv/bin/python -c "import cpq_kb; cpq_kb.export_snapshot('/home/wugefei/CPQ/kb-snapshot-before.json')"

# ④ 先 dry-run 看计划（默认行为：只统计，不连 PG、不写库）
./open-claude/.venv/bin/python scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db --dry-run --json

# ⑤ 确认无误再真正写库（单事务 + 按主键 upsert，幂等；kb_version 只在确有变化时 +1）
./open-claude/.venv/bin/python scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db --confirm

# ⑥ 上线预检：缺表 / 空表 / 全是演示数据 / 未分类行 → no-go，禁止上线
./open-claude/.venv/bin/python tech_app/tools/kb_deploy_preflight.py --env production
```

### 预检口径（`tech_app/tools/kb_deploy_preflight.py`）

判定与取数分离：`preflight()` 是纯函数（不读库、不写库），CLI 只经 `cpq_kb.snapshot()` **只读**取数。
`verdict == "go"` → 退出码 `0`，否则非零（`1` = no-go，`2` = 取不到快照/参数非法）。

| code | 含义 |
| --- | --- |
| `missing_tables` | `KB_TABLES` 里有表不在快照中（缺表就是缺表，不许当空表） |
| `empty_required_table` | 7 张关键包装主体表里有 0 行的 |
| `kb_version_missing` | `kb_version` 为 `None` 或 `0`（导入没生效） |
| `demo_only` | 生产库里某张关键表**全部**是 `source_type='demo'` |
| `unclassified_rows` | 生产库里存在 `source_type='unknown'` 的行 |

可按需加下限：`--min-rows kb_packaging_box_type=12`；`--json` 给 CI 与部署脚本用。

**数据分层**（`cpq_kb.SOURCE_TYPES`）：`demo` = 演示基线（`礼盒盒型库_数据样例.xlsx` 口径），
**不能**进正式测算；`workbook` = 0903 等权威工作簿拆出的规则（`source_ref` 必填，且须
`review_status='reviewed'`）；`dwg_confirmed` = 真实 DWG 解析 + 人工确认（必须带 `source_sha256` /
`parser_version` / `confirmed_by`）；`unknown` = 历史入库未分类，生产预检据此阻断。
`provenance` 与 `unclassified_rows` **只统计 9 张包装扩展表**——其余 `kb_*` 表没有 `source_type`
列，不得因此被判成 unknown。

### 回滚

导入是幂等 upsert，回滚 = 「按导入前那份快照把行改回去」，**不做删表**：

1. 失败/口径不对时，先停手：不要重复 `--confirm`，先跑一次预检留证据
   （`kb_deploy_preflight.py --env production --json > /tmp/kb-preflight-failed.json`）；
2. 用步骤 ③ 的 `kb-snapshot-before.json` 对照当前快照，逐表定位被改动的行
   （`kb_version` 变大说明确有写入）；
3. 需要恢复时按该 JSON 里的行与 `KB_KEYS` 做 upsert 回写（同样走 `cpq_kb`，不要手写 SQL），
   回写后**必须**再跑一次步骤 ⑥ 预检，`verdict == "go"` 才算恢复完成；
4. `ensure_schema()` 只加列不删列，建表本身不需要回滚；表名与既有 20 张的顺序是冻结契约，
   回滚不得改名或改顺序。

## 从报价开始的终验（包装 DWG）

第 1 批（转换器上线）与第 2 批（知识库上线）各自只覆盖一环；**这一节是「从报价开始的包装
全流程」能否验收的唯一判定口径**。口径全在 `tech_app/tools/quote_first_acceptance.py`
（`MODULE_VERSION = "quote-first-acceptance/1"`），本文不许另抄一份判定。

**本节的判定不授权部署**：不改服务器、不连 34、不重启服务。

### 一、验收链路（12 步，`STEPS`）

顺序即执行顺序；每一步的账号必须在 `ROLE_CHAIN`
（`sales_mgr → process_mgr → finance_mgr → process_mgr → sales_mgr`）里，且必须留下证据：

| no | key | 角色 | 前置门禁 | 必须留下的证据 |
| --- | --- | --- | --- | --- |
| 1 | `quote_create` | sales_mgr | 入口为报价（`entry_origin=quote`） | `business_case_id` / `quote_session_id` / `card_id` |
| 2 | `requirement_fill` | sales_mgr | 包装必填 10 项齐 | `requirement_snapshot_version` / `industry` |
| 3 | `box_match` | process_mgr | 盒型候选已确认（或按「没有适配」转新增工艺） | `box_type` / `match_score` / `source_type` |
| 4 | `tech_handoff` | sales_mgr | 「新增工艺」任务已派发 | `source_task_id` / `source_session_id` / `business_case_id` |
| 5 | `dwg_parse` | process_mgr | DWG 原生解析成功，或如实失败 | `converter` / `converter_version` / `ir_version` / `three_d_status` |
| 6 | `params` | process_mgr | 包装族必填齐（或签字带缺口） | `family` / `required_filled` / `required_total` |
| 7 | `bom` | process_mgr | 参数化 BOM 已生成且有引擎版本 | `engine_version` / `item_count` |
| 8 | `route` | process_mgr | 工艺路线已确认并冻结 | `engine_version` / `step_count` / `confirmed_at` |
| 9 | `cost` | finance_mgr | 成本已确认，或带缺口签字 | `rule_version` / `input_snapshot` / `gaps` |
| 10 | `report` | process_mgr | 报告已审核并正式发布（stale 不得发布） | `report_no` / `version` / `published_at` |
| 11 | `back_to_quote` | sales_mgr | 交接落回原报价卡片 | `handoff_id` / `quote_session_id` / `business_case_id` |
| 12 | `history_recover` | sales_mgr | 刷新/重进后全量恢复 | `chat_turns` / `field_evidence` / `versions` |

没有前置门禁、或没有证据的步骤不算验收步骤 —— 第 1/4 步缺了，
`sales_mgr` 就永远看不到「报价 → 工艺 → 财务 → 工艺 → 报价」这条路走没走过。

### 二、门禁清单与 Go/No-Go（`GATE_ITEMS` / `go_no_go`）

`GATE_ITEMS` 10 项业务门禁（与第 1 批 `dwg_deploy_gate` 的 19 项转换器门禁**互补、不重复**，
`kind ∈ ("auto","manual")`）：`entry_from_quote` / `business_case_linked` / `kb_authoritative` /
`dwg_parsed_natively` / `packaging_closure_complete` / `cost_traceable` / `stale_not_published` /
`history_recovery` / `role_chain_handoff`（manual）/ `golden_approved`（manual）。

判定入口是纯函数 `go_no_go(evidence)`（不读库、不联网、不改入参），返回
`{"verdict": "go"|"no_go", "blockers": [...], "claim": str}`：

- 条件全满足且 `real_converter=True` → `go`，`claim = "包装行业 DWG 支持完成"`；
- 只要有一项不满足 → `no_go`，`blockers` 按 `GO_BLOCKERS` 的声明顺序稳定输出；
- 缺步骤 / 入口不是报价 / 行业不是包装 / 知识库只有演示数据 / DWG 非原生解析 /
  包装闭环不完整 / 历史会话没恢复 / stale 结果被发布 / 权限被挡 → 各自对应的 blocker；
- **`real_converter=False`（只跑过 fake converter）永远不是 `go`**，此时唯一允许的声明是
  原文 `DWG 编排能力完成，真实转换能力未验收` —— 绝不许说「支持 DWG」。

用法：

```bash
./open-claude/.venv/bin/python tech_app/tools/quote_first_acceptance.py --template --json
./open-claude/.venv/bin/python tech_app/tools/quote_first_acceptance.py --evidence /tmp/quote-first-evidence.json --json
```

退出码：`go` → `0`，`no_go` → `1`，证据读不到 → `2`。

### 三、验收报告与金标人工审批

报告字段（`REPORT_FIELDS`，顺序即展示顺序）：`module_version` / `golden_version` / `converter` /
`samples` / `steps` / `gate_verdict` / `blockers` / `claim` / `approved_by` / `approved_at` /
`rollback_plan`。每一步的证据随 `steps` / `samples` 一起留档（不要求每个证据键各占一格）。

金标业务小节（`GOLDEN_BUSINESS_SECTIONS`）**必须人工填写**，脚本只采集统计值：
`unit_status` / `cut_layer` / `crease_layer` / `box_type_candidates` / `key_dimensions` /
`pending_confirmations` / `forbidden_hallucinations` / `downstream_snapshot` /
`quote_draft_allowed` / `three_d_status`。

审批沿用 `dwg_acceptance` 的 `approved_by` / `approved_at`（未审批或审批时间不可解析 → 金标
视为无效，`dwg_acceptance.ACCEPTANCE_REASONS` 里的 `unapproved` 即此）。**不得**为了让红测
转绿而自动更新金标快照；`approved_by` / `approved_at` 只能由人工签字。

本批对应的四条命令（前两条随时可跑，后两条是写入动作、必须带审批人）：

```bash
# ① 本批红测（判定口径本身）
./open-claude/.venv/bin/python -m unittest tests.test_quote_first_final_acceptance_red
# ② 生产门禁（verdict 必须为 go）
./open-claude/.venv/bin/python tech_app/tools/dwg_deploy_gate.py --env production --json
# ③ 报告校验（只读比对，不进 0 就是有差异/未审批/记录无效，并输出 diff）
./open-claude/.venv/bin/python tech_app/tools/dwg_acceptance_report.py --verify
# ④ 报告生成（**写入**：金标快照 + 验收记录，缺 --approved-by 一律退出码 2 且不写任何文件）
./open-claude/.venv/bin/python tech_app/tools/dwg_acceptance_report.py \
    --write-baseline --write-record --approved-by <复核人>
```

③ 与 ④ 的分工是硬的：**只有人看过的金标才允许 ④ 落盘**，脚本绝不自动刷绿、不留
`--update-snapshot` / `--force` 这类后门；`--verify` 发现差异时只报差异，不自动修改基线。

### 四、失败回滚

终验失败时**保留**已完成的门禁结果与证据（不改写、不删除），只回退本次部署改动：

1. 先停手留证据：把失败那次的 `--json` 输出与门禁输出存档，不要重复执行部署动作；
2. 只回退本次部署改动（配置 / 容器 / 代码版本），按第 1 批与第 2 批各自的回滚口径走；
3. **不删除**历史项目、会话、上传文件与已发布报告 —— 数据不是回滚对象；
4. 更不得用删除数据的方式让门禁变绿；恢复后重跑本节第三节的三条命令，判定链重新走一遍。

> 声明边界：只有 `go_no_go` 返回 `go` **且**金标人工审批通过，才允许把
> 「包装行业 DWG 支持完成」写进对外说明；否则一律按 `DWG 编排能力完成，真实转换能力未验收`
> 如实声明，并在 `blockers` 里列全未完成项，不许用"跳过该项"凑 go。

## 快速报价（标准案例库）

逆向快速报价的**唯一事实源**是三张表：案例表 `cpq_wf.cpq_qq_standard_case`（报价侧，与
`cpq_wf_*` 同 schema）、配置表 `cpq_kb.kb_quick_quote_config`（知识库侧，`key` / `value_json` /
`version` / `updated_at`）与权重表 `cpq_kb.kb_quick_quote_match_weight`（知识库侧，一行一个相似度
维度：`dimension` / `weight` / `hard_gate` / `tolerance` / `industry` / `source_type` / `source_ref` /
`version` / `review_status`）。建表都是幂等的，可以反复执行；**少任何一张，调用都会明确报错**
（`CaseLibraryUnavailable`），不会回落成"库里没有案例"、也不会回落成"用代码里的默认权重"。

口径与代码位置：`cpq_quick_quote_case.py`（模型 / 准入 / 取数）、`cpq_quick_quote_match.py`
（相似案例检索 / 权重 / 人工选基准），Spec `docs/specs/quick-quote-1-mode-and-case-model.md` 与
`docs/specs/quick-quote-2-case-retrieval.md`。

### 落地顺序（在能连到 PG 的机器上执行）

```bash
# ① 知识库侧：建 kb_quick_quote_config + kb_quick_quote_match_weight + kb_quick_quote_delta_rule
#   （第 30、31、32 张 kb_* 表；幂等，32 张的前后顺序是快照契约）
./open-claude/.venv/bin/python -c "import cpq_kb; cpq_kb.ensure_schema(); print('kb tables', len(cpq_kb.KB_TABLES))"

# ② 相似度权重：把种子灌进 kb_quick_quote_match_weight（幂等；只在确有变化时 +1 kb_version）
./open-claude/.venv/bin/python -c "import cpq_quick_quote_match as m; print(m.seed_weights())"

# ③ 报价侧：建案例表 + 索引 + DWG 通道增量列（幂等，须在 cpq_auth.init() 之后）
./open-claude/.venv/bin/python -c "import cpq_quick_quote_case as q; print(q.init())"
```

> 权重表**必须**灌（第 ② 步），否则 `load_weights()` 会直接抛 `CaseLibraryUnavailable`
> —— 这是刻意的：权重是业务口径，回落成代码里的默认值会把"没人配"伪装成"配好了"。
>
> `seed_weights()` 的口径是**恢复出厂**：它按 `dimension` upsert，业务改过的行会被种子值覆盖
> （幂等指的是"同一份种子重复跑只有第一次涨 `kb_version`"，不是"不覆盖业务改动"）。
> 业务改过权重之后**不要再跑它**；要改权重直接 `UPDATE cpq_kb.kb_quick_quote_match_weight`，
> 代码里没有第二份数字。

### 怎么验证通了（不看人，自己跑）

```bash
# ④ 配置读得到（读的就是 cpq_kb 快照里的 kb_quick_quote_config）
./open-claude/.venv/bin/python -c "import cpq_quick_quote_case as q; print(q.load_config(None))"

# ⑤ 权重读得到（读的就是 cpq_kb 快照里的 kb_quick_quote_match_weight；表空 / 库不可用会明确抛错）
./open-claude/.venv/bin/python -c "import cpq_quick_quote_match as m; print(m.load_weights(None))"

# ⑥ 案例清单接口（8010 上的报价助手，注意是 /agents/quote 前缀）
curl -s -H "X-Internal-Token: $CPQ_INTERNAL_TOKEN" \
  http://127.0.0.1:8010/agents/quote/api/quick-quote/cases | head -c 400

# ⑦ 页面上：报价首页 → 行业选「包装」→ 出现「精准报价 / 快速报价」两个入口，
#    点「快速报价」弹出面板，里面是案例清单与每条的可用性 + 原因。
#    非包装行业不显示快速报价入口（标准案例库是包装案例库）。
```

### 相似案例检索（批 2）：自己跑一遍

批 2 的契约在**模块层**（还没有页面入口，页面接线在批 3/4/5）。下面这段可以直接照抄执行，
用的就是同一份权重表与同一张案例表：

```bash
cd /Users/sher/Boulderaitech/cpq_agent
./open-claude/.venv/bin/python - <<'PY'
import json
import cpq_quick_quote_match as m

inputs = {
    "box_type": "YT-RB-01001-A", "box_family": "01天地盖", "closure_type": "磁吸",
    "inner_length": 200, "inner_width": 150, "inner_height": 80,
    "grey_board_gsm": 1200, "face_paper_gsm": 200, "insert_type": "EVA内托",
    "print_colors": "CMYK", "lamination": True, "hot_stamping": True,
    "v_groove": True, "magnet": True, "quantity": 3000,
}
result = m.match_cases(inputs)          # 权重读 kb_quick_quote_match_weight，案例读案例表
print("权重版本:", result["weights_version"], "| 候选数:", len(result["candidates"]),
      "| 需人工选:", result["requires_manual_selection"])
for row in result["candidates"]:
    print(row["case_code"], row["status"], row["similarity_pct"], row["rank_reason"])
print("建议:", result["suggested_case_code"], "| 已确认:", repr(result["confirmed_case_code"]))
print("0 候选原因:", result["no_candidate_reason"])

# 人工选基准案例（角色必须是 sales_mgr / admin）；选中后才是批 3 字段工作区的输入
# print(json.dumps(m.build_baseline(inputs, result["suggested_case_code"],
#       user={"user_id": "1", "username": "张三", "role_code": "sales_mgr"}), ensure_ascii=False))
PY
```

怎么读结果：

- **候选**：`status="matched"`（命中硬筛选）在前、`needs_input`（硬筛选维度没填）居中，各自按相似度降序；
  未审核 / 演示 / 过期案例照常列出，但排在可用案例之后，`rank_reason` 里点名原因；
- **不替用户决定**：`requires_manual_selection` 恒为 true、`confirmed_case_code` 恒为空，
  选案例必须走 `build_baseline()`（带登录用户）；
- **0 候选**：`no_candidate_reason` 会给中文原因并建议转精准报价 —— 不会因为"库里没有"就静默返回空列表；
- **成交价默认不返回**（`include_deal_price=True` 才带），避免商务敏感信息躺在候选列表里。

### 现在是空的，为什么，以及怎么变成可用

案例库建出来是 **0 行**：本批不代造数据。要让某个案例能用于快速报价，必须同时满足

1. **来源权威**：`source_type IN ('workbook','dwg_confirmed')` —— 演示数据（`demo`）与未分类
   数据（`unknown`）在页面上照常显示，但永远判"来源不权威"，不能用；
2. **人工审核过**：`review_status = 'reviewed'` —— 从报价沉淀出来的案例默认是 `draft`，
   必须人工审（`save_case()` 需要登录用户，客户名必须已脱敏，手机号/邮箱/真人名一律拒收）；
3. **在有效期内**：显式 `valid_until` 优先，否则 `quote_date + 天数`（工作簿 365 天 /
   DWG 实样 180 天 / 演示 0 天 = 当天即过期）。

接口与页面都会把"为什么这条不能用"原样显示出来（`reason_code` + 中文原因），
所以"库里有像的案例但不能用"是可见的，不会假装没有案例。

### 字段工作区与差异价（批 3）：建表 + 灌费率

口径与代码位置：`cpq_quick_quote_workspace.py`（字段闭集 / 校验 / 差异价 / 工作区状态机），
Spec `docs/specs/quick-quote-3-field-workspace-and-delta-price.md`。

```bash
# ① 建第 32 张 kb_* 表：kb_quick_quote_delta_rule（幂等；再跑一次不会变 kb_version）
./open-claude/.venv/bin/python -c "import cpq_kb; cpq_kb.ensure_schema(); print('kb tables', len(cpq_kb.KB_TABLES))"

# ② 灌**示例**费率（source_type=demo + review_status=draft，幂等；只在确有变化时 +1 kb_version）
#    业务必须用真实工作簿口径替换它们：demo 行的差异价 note 会逐条点名
#    「费率来源=演示数据，出价前必须换成权威费率」，不会静默当成权威。
./open-claude/.venv/bin/python -c "import cpq_quick_quote_workspace as w; print(w.seed_rules())"

# ③ 读回来确认（读不到 / 表为空会明确抛 CaseLibraryUnavailable，不回落代码里的默认费率）
./open-claude/.venv/bin/python -c "import cpq_quick_quote_workspace as w; print([r['rule_code'] for r in w.load_rules(None)])"
```

字段工作区的四列对比表（参数 / 基准案例 / 当前报价 / 差异价格）挂在
`确认需求解析结果.html` 的 `#quickQuoteWorkspace` 容器里，由
`tech_app/frontend/quick-quote-panel.js` 的 `renderDiffTable(rows)` 渲染；
行数据只能来自 `diff_table()`，页面不重算价格。

### 快速报价出价与转精准（批 4）：自己跑一遍

口径与代码位置：`cpq_quick_quote_price.py`（六项适用门槛 / 偏差区间 / 卡片快照落库 /
转精准交接包），Spec `docs/specs/quick-quote-4-quick-quote-and-handoff.md`。
**门槛不过就只给一句「建议转精准报价」，不会先算一个价再提示"仅供参考"。**

```bash
cd /Users/sher/Boulderaitech/cpq_agent
./open-claude/.venv/bin/python - <<'PY2'
import cpq_quick_quote_match as m
import cpq_quick_quote_workspace as w
import cpq_quick_quote_price as p

inputs = {"box_type": "YT-RB-01001-A", "box_family": "01天地盖", "closure_type": "磁吸",
          "inner_length": 200, "inner_width": 150, "inner_height": 80,
          "grey_board_gsm": 1200, "face_paper_gsm": 200, "insert_type": "EVA内托",
          "print_colors": "CMYK", "lamination": True, "hot_stamping": True,
          "v_groove": True, "magnet": True, "quantity": 3000}
user = {"user_id": "1", "username": "张三", "role_code": "sales_mgr"}

match = m.match_cases(inputs)                       # 批 2：候选检索
baseline = m.build_baseline(inputs, match["suggested_case_code"], user=user)   # 批 2：选基准
ws = w.new_workspace(baseline, user=user)           # 批 3：字段工作区
rules = w.load_rules(None)                          # 批 3：差异价规则（读 kb_quick_quote_delta_rule）
ws = w.apply_edits(ws, {"quantity": 3000, "face_paper_gsm": 250},
                   source="workspace", user=user, rules=rules)
try:
    quote = p.price(baseline, ws, rules=rules)      # 批 4：过门槛才出价
    print("单价", round(quote["unit_price"], 4), "区间", quote["price_range"])
    print("偏差", quote["deviation"]["est_pct"], "| 提醒", len(quote["warnings"]), "条")
    # print(p.save(quote, user=user, session_id="<卡片 session_id>"))
    # print(p.transfer_to_precise(quote, user=user, session_id="<卡片 session_id>"))
except p.QuickQuoteBlocked as exc:
    print("被门槛拦下：", exc.result["blocking"], "|", exc.result["advice"])
PY2
```

### 文件解析（批 5）：`CPQ_UNIFIED_PARSE_URL` 与能力预检

报价侧**只当客户端**：DWG/DXF 的转换器住在技术工艺侧，报价项目里不装第二套 ODA /
LibreDWG（`cpq_*.py` 里不得出现 `ODAFileConverter` / `dwg2dxf` / `LibreDWG` 字样）。

| 环境变量 | 取值（172.16.10.34） | 说明 |
| --- | --- | --- |
| `CPQ_UNIFIED_PARSE_URL` | `http://127.0.0.1:8010/api/file/parse` | 统一解析服务入口；**唯一来源**，代码里只有这一个默认值（`cpq_quick_quote_file.DEFAULT_PARSE_URL`） |

- 能力预检走 `GET <CPQ_UNIFIED_PARSE_URL 同前缀>/capability`，返回
  `service` / `provider` / `provider_version` / `dwg` / `dxf` / `preview`；
  **服务不可达或返回体不是 dict 一律报错**（`ParseServiceUnavailable`），不返回空能力表；
  `dwg=false` 时 DWG/DXF 明确拒绝（`ParseUnsupported`，带「转人工 / 转精准报价」建议）。
- 文档类（`txt/md/csv/xlsx/xls/pdf/docx/png/jpg/jpeg`）走既有 `/api/extract`，
  **不依赖**统一解析服务 —— DWG 没就绪不该拖死文字需求。

```bash
# 能力预检（在 34 上跑；服务没起会明确报「不可达」，不会假装支持）
./open-claude/.venv/bin/python -c "import cpq_quick_quote_file as f; print(f.parse_url()); print(f.capability())"
```

### 后续批次

批 1–5 已把「案例模型 → 相似检索 → 字段工作区/差异价 → 出价与转精准 → 文件解析客户端」
落齐；**页面把工作区与出价串起来的那一层接线仍是下一步**（批 3 只落了容器与四列表，
批 4/5 的模块入口还没有对应按钮）。

## 包装图纸零件：下游闭环的能力级别（L1 / L2 / L3）

这三条链路（DWG → DXF → CAD IR → 语义 → 零件 → 工艺/成本/3D）的能力必须**按级别**声明，
不许把"能出零件清单"说成"闭环"。

| 级别 | 名称 | 成立条件 | 现在成立吗 |
| --- | --- | --- | --- |
| L1 | 编排 | DWG → DXF → CAD IR → 语义 → 零件文档，2.1 左栏出清单 | 成立（本机两份真实样本已过） |
| L2 | 可信 | L1 + `closed_ratio` 过门槛（`酒盒.dwg ≥ 0.10`、`圆盘盒.dwg ≥ 0.50`）+ 角色可识别 | 成立（本机实测 0.797 / 0.889） |
| L3 | 闭环 | L2 + 可点（零件行 → 右栏面板）+ 可算（工艺/成本过 `processability`）+ 可看（3D 挤出） | **未签字，不得声明** |

**当前能力级别：L2（可信）**；L3 的代码与门禁已就位，但**未签字前不得声明 L3** ——
L3 需要业务对「两张真实样本各至少一件能跑工艺、能挤出 3D」签字（门禁里的
`parts_demo_script` 人工项），签字后才把这一格改成 L3。级别**只升不降**，降级必须写明原因。

自己跑（只读门禁，六项；`parts_demo_script` 是人工项，要显式确认）。

**34 上必须先做准备，否则门禁会探不到转换器（本机跑 `--env local` 不需要这两步）：**

```bash
ssh wugefei@172.16.10.34
cd /home/wugefei/CPQ/cpq_agent
set -a; . /home/wugefei/CPQ/cpq_env.sh; set +a                    # ① 不 source 就没有 DWG_CONVERTER_*
export PATH="/home/data/cpq-tools/xvfb-user/root/usr/bin:$PATH"   # ② xvfb-run 按名字调同目录的 Xvfb
./open-claude/.venv/bin/python tech_app/tools/packaging_parts_gate.py --env local
# 人工项签字后才可能 verdict=go 的完整写法：
./open-claude/.venv/bin/python tech_app/tools/packaging_parts_gate.py --env production \
  --ack parts_demo_script=<签字人>
```

没有 `DWG_CONVERTER_*` 与那条 `PATH` 时，`parts_outline_real_sample` 只能停在
「应用内转换器可用=False」→ 生产环境算 fail —— 这不是能力缺失，是环境没带齐，先补这两步再判。

门禁**只读**：不连库、不调模型、不联网，真实项目数据一个字节不写（转换产物 / 清单 / 审计全落到
临时目录，项目 id 固定为 `packaging-parts-gate`）；`parts_outline_real_sample` 在没有样本或**一个可用
的 DWG 转换器都没有**的本机会 `skip`（`--env production` 下 skip 一律算失败）。它按**应用的能力**判：
优先走 `cad_converter.convert_drawing()`（与 8010 同一条链路），只有应用内转换器不可用时才回退
`dwg2dxf`——所以线上（只有 ODA、没有 libredwg）这一项也能真跑，不会以"没有 dwg2dxf"误报 no-go。

部署自检分两步：**第 6 步**打一遍「零件文档 → 单件详情 → 闭合件试挤出」，但它要一个**真实项目**
（样本项目 id **必须由用户提供**，脚本不猜项目、也不拿生产项目当试验田）；**第 6b 步**
（`2026-09-21` 补）不需要任何项目——它把 `DATA_DIR` 指到临时目录，在隔离目录里建项目 → 建需求草稿 →
跑完整条八步 flow → 读零件文档/单件详情/挤出，跑完删掉临时目录，并核对
`tech_app/data/*/meta.json` 数量前后不变（生产数据一个字节不写）。两步互补：第 6 步证明「**某个真实项目的
数据**是对的」，第 6b 步证明「**这台机器的链路**是通的」；第 6b 步每次部署都会跑，不过就非零退出。

```bash
# 项目 id 从哪儿来：跑过"一键解析图纸"的项目才有零件文档，下列命令直接把 id 打出来
ls -1 tech_app/data/*/packaging_parts.json 2>/dev/null | cut -d/ -f3
CPQ_PARTS_PROJECT_ID=<项目id> bash scripts/deploy_34_bare.sh ytbz
# 不提供时这一步 skip 并打印原因；没有上面那个文件就说明还没人在这个环境点过"一键解析"，
# 先去页面跑一次，别凭空造项目
```
