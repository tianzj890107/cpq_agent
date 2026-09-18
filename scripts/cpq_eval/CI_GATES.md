# CPQ 回归数据集 CI 门禁

执行入口是 `scripts/cpq_eval/ci_gates.py`，按执行层拆成四类门禁，**互相独立**，任意一类
失败都不会被另一类的通过掩盖。`.gitlab-ci.yml` 里已经接入四个独立 job。

## 门禁分层

| 门禁 | 覆盖执行层 | 是否联网 | CI 行为 |
| --- | --- | --- | --- |
| `fast` | `simulation` + `production_unit` | 否 | 必跑 |
| `production_http` | `production_http`（真实路由 + 真鉴权依赖） | 否 | 必跑 |
| `recorded_provider` | `recorded_provider`（固定 fixture，不访问真实模型） | 否 | 必跑 |
| `postgres_integration` | `postgres_integration`（隔离 PostgreSQL） | 仅回环 / service container | 有环境才跑，`--strict` 下缺环境即失败 |

本地/CI 通用命令：

```bash
python3 -m scripts.cpq_eval.ci_gates --list
python3 -m scripts.cpq_eval.ci_gates --group offline            # fast + http + provider
python3 -m scripts.cpq_eval.ci_gates --gate postgres_integration --strict
```

`--group offline` 在缺隔离 PostgreSQL 时也能跑。`postgres_integration`：

- 不传 `--strict`（本地）：缺 `CPQ_EVAL_PG_HOST` 时打印 `SKIP` 并以 0 退出，报告里 integration
  单列 skipped，**不算 passed**；
- 传 `--strict`（CI job）：缺环境、案例**全部 skip**、或任一 mutation **survived** 都返回非零
  —— 不允许拿「全部 skip」冒充通过。

## GitLab CI job

```yaml
cpq_eval_fast:            # python -m scripts.cpq_eval.ci_gates --gate fast
cpq_eval_production_http: # python -m scripts.cpq_eval.ci_gates --gate production_http
cpq_eval_recorded_provider: # python -m scripts.cpq_eval.ci_gates --gate recorded_provider
cpq_eval_postgres:        # services: postgres:16-alpine (alias cpq-eval-pg)
                          # python -m scripts.cpq_eval.ci_gates --gate postgres_integration --strict
```

`workflow.rules` 覆盖 **Merge Request** 与 **默认分支（master）**；按仓库约定，开发分支
`20260909` 不自动跑 CI（这一点在报告里如实说明，不假称已受保护）。

## PostgreSQL service 与安全合同

父层与子层共用 `scripts/cpq_eval/pg_guard.py` 里的**同一份** `guard()`，不会一边允许、一边
拒绝。CI service 场景必须同时满足：

- `CPQ_EVAL_INTEGRATION=1` 且 `CPQ_EVAL_PG_CI=1`；
- 只读 `CPQ_EVAL_PG_*`，**绝不**回退到生产 `CPQ_PG_*`；
- host 是回环地址，或在 service alias 白名单内（默认 `cpq-eval-pg`）的 DNS 名；
- maintenance database 在白名单内（默认 `postgres`）；
- 应用连接库必须匹配 `^cpq_eval_it_[0-9a-f]{10}$`，子进程还会用 `SELECT current_database()`
  二次核对；
- 命中 `pdt / prod / production / 172.16.10.34 / 172.16.5.181 / :8010 / metabase` 立即拒绝。

每条用例自建 `cpq_eval_it_<hex>` 临时库，`finally` 里 `DROP DATABASE ... WITH (FORCE)`，
只删自己建的库。子进程超时 / 崩溃 / 返回格式错误时，父进程用 `cleanup_orphan` 对**同一个**
child_db 兜底清理；数据库名不匹配严格正则一律拒绝，禁止模糊匹配批量 DROP，清理失败进报告。

## 退出码

| 情况 | 退出码 | 含义 |
| --- | --- | --- |
| 全部执行过的门禁通过 | 0 | passed |
| 有门禁失败（含 P0 failed / invalid） | 非 0 | failed |
| `postgres_integration` 缺环境、未传 `--strict` | 0（打印 `SKIP`） | skipped，不计 passed |
| `postgres_integration` 缺环境 / 全 skip / mutation survived，传了 `--strict` | 非 0 | failed |
| runner 数据集校验不通过（invalid） | 非 0 | failed |
