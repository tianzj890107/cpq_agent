# 知识库统一维护在 Postgres（`cpq_kb`）+ 技术工艺经 HTTP 快照读取 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_kb_in_pg_http_snapshot_red.py` `tests/test_quote_packaging_box_selection_red.py`

## 1. 目标

知识库（零部件 / 物料 / 工序 / 路线 / 设备 / 供应商 / 费率 / 计价系数 / 标准件）只有**一份**：
配置报价 CPQ 的 Postgres。技术工艺（8012）不再读写本地 SQLite `da.db` 的知识库，改为通过
一体化服务（8010）的 HTTP 快照接口取数，并在进程内缓存。

本批已由用户拍板的三项决定：

1. **新建 `cpq_kb` schema**（不并入报价侧 `master_data` —— 粒度不同：那边是成品/材料主数据，
   这边是零部件 + 工序 + 费率）；
2. **把 `da.db` 里的数据直接导入**（本批范围＝`kb_*` 全表，见 C9 的范围说明）；
3. **匹配必须打到正确的目标**：技术工艺的匹配只认 `cpq_kb` 这一份数据；快照拿不到时必须
   报错，**不得**把"库是空的"伪装成"没有可复用零件"。

## 2. 现状（为什么现在不是"一份"）

| 内容 | 位置 | 证据 |
| --- | --- | --- |
| 知识库 `kb_*` | 技术工艺本地 SQLite `<DATA_DIR>/da.db` | `tech_app/backend/config.py:181`、`tech_app/backend/storage/da_db.py:4` |
| 数据目录 | `tech_app/tech_data`（本地与线上各一份） | `tech_app_launch.py:71`、`docker-compose.yml:29` |
| 技术工艺 → CPQ | 只走 HTTP，不直连 PG | `tech_app/backend/services/cpq_sso.py:10`、`tech_app/backend/services/cpq_bridge.py:4-13`、服务端 `cpq_tech_bridge.py`、路由 `/wf/tech/*`（`cpq_suite_server.py:655-700`） |
| 服务间通道 | `X-Internal-Token`（fail-closed） | `cpq_suite_server.py:71-73`、`cpq_suite_server.py:178-185` |

由此产生的具体故障（线上实测，2026-09-16）：

- 9/14 那个冰箱项目匹配报告写着 `"library_size": 0`，6 个零件全部 `decision:"new"`、
  `candidates:[]`（`tech_app/tech_data/c7e71a8ece61/component_match.json`）；原因是那份
  `da.db` 的 `kb_component=0`、`kb_material=0`、`kb_process_step=0` —— 知识库整库为空。
- 平台**没有任何自动灌种子**：`da_seed` / `da_mock` 只被 `python -m` 与测试调用，启动路径
  无人调用。换一次数据目录就等于知识库归零，且不报错。
- 8 月那份库（顶层 `da.db`，20 张 `kb_*` 表 613 行）与 9 月的库各存一份，谁都不是权威。

## 3. 契约

### C1 schema 与表

- 新增 PG schema：环境变量 `CPQ_KB_SCHEMA`，默认 `cpq_kb`（沿用 `cpq_wf` / `CPQ_WF_SCHEMA`
  的写法，见 `cpq_auth.py:33`）。
- `kb_*` 20 张表 1:1 搬过去：列名、主键、唯一键与 `tech_app/backend/storage/da_schema.sql`
  的 `kb_*` 定义一致（不重命名、不合并、不改类型语义）。
- 建表语句幂等（`CREATE SCHEMA/TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` 增量列），与 `cpq_auth.py:143-181` 同套路。
- 新增 `cpq_kb.kb_meta`（单行表）：`singleton boolean primary key default true`、
  `kb_version bigint not null`、`updated_at timestamptz`。

### C2 版本号

- `kb_version` 是快照失效的唯一依据；任何一次写入（导入、维护页增删改）后必须 `+1`。
- 读侧不得依赖时间戳或文件 mtime 判新旧。

### C3 导入器 `scripts/import_da_kb_to_pg.py`

- 默认 **dry-run**（只统计不写库），`--confirm` 才真正写；参考 `scripts/push_remotes.py --check` 的先例。
- 源库一律**只读打开**（`sqlite3.connect("file:<path>?mode=ro", uri=True)`）：**不得**写回源
  文件、**不得**触发 checkpoint、**不得**删除 `-wal`/`-shm`。导入前后源文件 sha256 必须一致。
  （现实教训：任何以可写方式打开 WAL 库的进程，一旦关闭就会把 WAL 合并进主体并删掉
  `-wal`/`-shm` —— 分析工具必须只读。）
- 必须能看到 WAL 里已提交的数据（本批 `kb_*` 实测不受 WAL 影响，但实现不得把这个巧合
  写成前提）。
- 幂等：按主键 upsert；连续执行两次，行数不变、`kb_version` 只在确有变化时递增。
- 单事务：失败整体回滚，不留下半张表的数据。
- 输出（`--json` 时最后一行 JSON）：每表行数、`kb_version`、`--dry-run` 标记。

### C4 快照端点（CPQ 侧）

```
GET /wf/tech/kb/snapshot[?since=<kb_version>]
```

- 鉴权：只认 `X-Internal-Token`（`cpq_suite_server.py:178-185` 的 fail-closed 口径）；
  无票/票不对 → 401/403，且**不得**有副作用。
- 返回：`{"ok":true,"kb_version":N,"unchanged":bool,"tables":{...}}`。
  `since` 等于当前版本时 `unchanged=true` 且**不返回** `tables`（省流量）。
- PG 不可用 / schema 或表缺失 → **503**，`error` 写明原因。**严禁**返回 `tables:{}` 或
  `kb_version:0` 让调用方误判成"库里没有零件"。

### C5 技术工艺客户端 `tech_app/backend/services/cpq_kb_client.py`

- 只走 HTTP：`CPQ_AUTH_BASE_URL`（`tech_app/backend/config.py:211`）+ `X-Internal-Token`
  （`CPQ_INTERNAL_TOKEN`，由 `cpq_suite_server.py:880` 注入子进程）。
- 缺 token / 连不上 / 非 200 / 响应不是 JSON / `ok` 非真 → 抛 `KbUnavailable`（异常带原因）。
- 不得静默降级成"空知识库"。

### C6 `kb_repo` 数据源切换

- `tech_app/backend/storage/kb_repo.py` 的所有读函数（`list_components` / `get_component` /
  `list_materials` / `get_material` / `current_price` / `effective_rate` / `effective_factor` /
  `list_process_steps` / `get_process_step` / `get_route` / `list_equipment` / `list_suppliers` /
  `recommend_components` / `recommend_routes` / `find_standard_part` …）**签名不变**，数据源
  改为进程内快照缓存：首次访问拉取、按 `kb_version` 失效、可 `refresh_kb(force=True)`。
- `kb_repo` 的**读路径**不得再用 SQL 查 `kb_*`：`SELECT ... FROM kb_*` 全部消失，改成读快照。
- `save_*` 写函数保留原样，仅供遗留种子脚本 `da_seed` / `da_seed_battery` / `da_mock`
  使用（`da_mock.py:1484-1561`、`da_seed.py:406-481`），**运行时不得调用**；
  本批不新增第二个运行时写入路径（知识库的运行时事实源只有 `cpq_kb`）。
- **判定口径逐字不变**：`envelope_of` / `_envelope_score` / `_param_score` / `_feature_score` /
  `_match_type` / `ENVELOPE_TOLERANCE=0.20` / `WEIGHTS` / `MATCH_THRESHOLD=0.35` 一行不改。
  换数据源只换"数据从哪来"，不换"怎么算"。（尺寸口径是否放宽是单独一件事，见第 6 节。）

### C7 失败必须响亮

- 快照不可用时：`kb_repo.list_*` 抛 `KbUnavailable`；`component_match.match_part` /
  `match_project` 同样抛错（在写任何报告之前）。
- **严禁**产出 `library_size: 0` 的"未匹配（按新制评估）"报告 —— 这正是本批要消灭的假象。
- 技术工艺对应 HTTP 面返回 5xx + 可读原因（沿用 `tech_app/backend/main.py` 现有错误返回方式）。

### C8 不变量

- `tech_app/**` 不得 import `psycopg`（保持"技术工艺不直连 PG"，见第 2 节分工）。
- 一致性回归：同一份 `kb_*` 数据，走快照与走旧 SQLite，逐零件结果（`component_code` +
  `score` + `match_type` + `decision`）必须**完全一致**（golden 夹具见 C11）。

### C9 本批范围（明确边界）

- **入库**：`kb_*` 全表（实测 16 张表有数据、613 行；4 张空表也建好）。
- **不入库**：`src_*` / `wip_*`（项目 / 需求 / IR / 零件 / 工艺 / 成本 / 报告）。它们是
  技术工艺的业务数据且目前只有演示与调试内容，是否迁 PG 单独一批再定。
- **不入库**：零部件图纸二进制（`kb_library` 的 `KB_DIR` 文件树）。本批只把
  `kb_component_drawing` 行搬过去；文件存储方案（对象存储/共享卷）单独再定。
- 迁完后 `tech_app/tech_data/da.db` 只作历史存档，不再被读。

### C10 配置与部署

- 新增环境变量：`CPQ_KB_SCHEMA`（默认 `cpq_kb`）、复用 `CPQ_INTERNAL_TOKEN`；
  导入器复用 `cpq_db` 的 `CPQ_PG_*`。
- 部署不再需要往服务器拷 `da.db`；首次上线流程改为"跑一次导入器 → 校验行数"。

### C11 测试夹具

- `tests/fixtures/cpq_kb_snapshot_20260916.json`：从 2026-09-16 那份 `da.db` 只读导出的
  `kb_*` 快照（16 表 613 行），用于红测与一致性回归。**它是测试夹具，不是事实源**；
  事实源是 `cpq_kb`。

## 4. 验收场景

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| A1 | `--dry-run` 对源库 | 输出 16 表 613 行；源文件 sha256 与 mtime 不变；`-wal`/`-shm` 不被删除 |
| A2 | `--confirm` 后核对 | PG 每表行数与源逐表一致；再跑一次行数不变 |
| A3 | 快照端点 | 无票 401/403；有票 200 + `kb_version` + `tables`；`since=当前版本` → `unchanged=true` 且无 `tables`；存储报错 → 503 且 `error` 非空 |
| A4 | 技术工艺取数 | 本地 SQLite 知识库为空、快照有数据时，`list_components()` 返回 20 条，`match_part` 能出候选 |
| A5 | 失败响亮 | 快照 503 时 `list_components()` / `recommend_components()` 抛 `KbUnavailable`；不产生 `library_size: 0` 报告 |
| A6 | 一致性 | 三条既有 IR 的命中（编码/评分/类型）与旧 SQLite 口径逐条一致 |
| A7 | 边界 | `tech_app/**` 无 `psycopg`；`kb_repo` 不再 import `da_db` |

## 5. 接口与文件名（实现提示词使用）

- 新增：`cpq_kb.py`（schema/DDL/快照查询/导入）、`scripts/import_da_kb_to_pg.py`、
  `tech_app/backend/services/cpq_kb_client.py`。
- 改动：`cpq_suite_server.py`（`/wf/tech/kb/snapshot` 分支）、
  `tech_app/backend/storage/kb_repo.py`（数据源改快照缓存）。
- 不动：`tech_app/backend/storage/da_db.py`、`da_repo.py`（业务数据仍走本地）、
  `component_match.py` / `cost_lookup.py` / `process_lookup.py`（调用签名不变）、
  `cpq_tech_bridge.py`。

## 6. 不在本批（写清楚，避免误期待）

1. `src_*` / `wip_*` 迁 PG；
2. 零部件图纸文件（缩略图/STEP/2D）的集中存储；
3. 匹配口径调整：`ENVELOPE_TOLERANCE=0.20` 的硬淘汰对家电/钣金大件偏紧（1800×450×60 的
   门板对 1200×595×22 的库内件会被直接淘汰）。**入库演示库后，9/14 那个冰箱项目仍然匹配
   不到**，这是口径问题不是数据源问题，需要工艺/产品单独拍板；
4. 知识库维护页面（录入真实零部件/工序/费率的 UI）；
5. KB 快照之外的增量推送（本批只做整包快照 + 版本号）。

## 7. 固定接口与命令（红测按这些名字断言）

- 导入器：

  ```
  python scripts/import_da_kb_to_pg.py --source <da.db> [--confirm] [--json]
  ```

  `--json` 时最后一行输出：

  ```json
  {"ok": true, "dry_run": true, "kb_version": 0,
   "tables": {"kb_component": 20, "kb_component_feature": 57, "...": 0},
   "rows": 613}
  ```

  未给 `--confirm` 时 `dry_run` 必须为 true，且不写库。

- CPQ 侧模块 `cpq_kb.py`：`SCHEMA`、`ensure_schema(conn=None)`、`snapshot(since=None)`、
  `import_from_sqlite(path, *, confirm=False)`、`kb_version()`。

- 快照端点响应（200）：

  ```json
  {"ok": true, "kb_version": 7, "unchanged": false, "tables": {"kb_component": [ ... ]}}
  ```

  `since` 命中当前版本时：`{"ok": true, "kb_version": 7, "unchanged": true, "tables": {}}`。
  未授权 → 401（`{"ok": false, "error": "..."}`）；PG/schema 不可用 → 503 且 `error` 非空。

- 技术工艺客户端 `tech_app/backend/services/cpq_kb_client.py`：
  `fetch_snapshot(since=None) -> dict`，失败抛 `KbUnavailable`。

- `kb_repo.refresh_kb(force=False)`：强制/按版本刷新进程内快照缓存。
