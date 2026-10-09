# 真并行分片回归：把「分片」从名义变成同时跑

血缘：承接 `release-assurance-batch2.md`（批 2 的 `T1` 交付了分片与隔离，但没有并发）、
`10-待拍板优化清单.md`（`T1`：全量回归并行分片，目标 3 分钟级）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测；业务实现落地在 `scripts/run_tests_sharded.py` 的线程池并发，按本文 §5 未提交/未推送/未部署，红测 5 项已全绿）
红测：`tests/test_parallel_sharded_regression_red.py`
复核基线：2026-10-09 实测（§1 每条都可指到行）

## 0. 一句话目标

`scripts/run_tests_sharded.py` 现在**只分片、不并行**：436 个模块切成 N 份，却在一个
`for` 循环里**逐个** `subprocess.run`，所以墙钟时间 ≈ 串行和，仍然是 10 分钟级。
本批把它改成**真的同时跑**（受 `--jobs` 限制的线程池），并保持分片隔离与聚合口径不变。

## 1. 现状缺口（代码事实 + 10-9 实测）

- `scripts/run_tests_sharded.py::run_shards()` 的循环体只有**阻塞**调用：
  ```python
  for index, modules in enumerate(plan):
      ...
      proc = subprocess.run([sys.executable, "-m", "unittest"] + list(modules), ...)
  ```
  实测该函数内的调用集合为 `{subprocess.run, shard_env, _parse_totals, os.makedirs, ...}`，
  **没有** `ThreadPoolExecutor` / `concurrent.futures` / `ProcessPool`；
- 因此 `--shards 8` 的墙钟 ≈ `--shards 1`：分片只改了「怎么切」，没改「是否同时跑」；
- 批 2 的 Spec `release-assurance-batch2.md` §2.1 与红测只要求**分片正确性 + 隔离性 +
  与串行结论一致**，从未要求并发 —— 这是**规格漏项**，不是实现违约；
- 本机 8 核、436 个模块；隔离用的 `TMPDIR/CPQ_TEST_SHARD*` 已经按分片独立（并发的前提已具备）。
- **实测（10-9，全量）**：串行 628.79s；`--jobs 8` 216~239s，但会超订、出 2 条**负载相关**
  红（`packaging_drawing_flow_red::e26_concurrent_reruns` 的并发竞态、
  `dwg_conversion_quality_repair_red::e5_real_oda` 的真实 ODA 转换超时）；
  `--jobs 4` **292.30s、Ran=7067 failures=0 errors=0 skipped=28 全绿**。故默认取半核。

## 2. 契约

### 2.1 `run_shards(plan, base_tmp, *, json_out=False, jobs=1) -> dict`
- 返回结构**逐字不变**：`{"ok": bool, "shards": [{index, command, returncode, modules, ok,
  ran, failures, errors, skipped}, ...], "totals": {ran, failures, errors, skipped}}`；
- `shards` 列表**恒按 `plan` 的 index 升序**（并发执行也要确定性输出）；
- `jobs == 1`：保持现有**串行**行为（可回归对照）；
- `jobs is None` 或 `jobs <= 0`：取 `max(1, cpu_count // 2)`（**不是**跑满核 —— 见 §1 实测：跑满核会超订，
  让自带子进程的用例在负载下抖动），并**不超过 `len(plan)`**；显式 `--jobs N` 仍按 N 跑；
- `jobs > 1`：用 **线程池**（`concurrent.futures.ThreadPoolExecutor`）并发跑分片 ——
  分片本体是 `subprocess`（I/O 阻塞），线程即可真正并行；**不得**用进程池（会丢掉
  `mock` 注入的 `subprocess.run`，也让每个分片再开进程池，收益反降）；
- 聚合口径不变：`totals` 为各分片之和；`ok` 为「所有分片退出码 0」；
- 隔离口径不变：每个分片仍用 `shard_env(index, base_tmp)` 得到**独有** `TMPDIR`。

### 2.2 CLI
- 新增 `--jobs`（默认 `0` = 自动取 CPU 数）；`--shards` 语义不变；
- `--dry-run` 仍不执行任何用例；`--json` 仍输出机器可读汇总；
- 非 `--json` 时每分片一行汇总打印保持可读（并发下可乱序打印，但聚合行不变）。

### 2.3 判据红线
- 并发**不得**牺牲隔离：不同分片的 `TMPDIR` 必须互不相同（沿用既有 `shard_env`）；
- 并发**不得**改变结论：同一 `plan` 在 `jobs=1` 与 `jobs=N` 下 `totals` 必须一致；
- 「3 分钟」仍是**目标**，不是硬指标；本批验收的是**真的同时跑**（墙钟显著小于串行和）。

## 3. 不做什么

- 不改 `discover_modules` / `plan_shards`（分片确定性已满足）；
- 不改 `tests/_tmp_guard` 的临时目录闸门口径；
- 不引入第三方依赖（只用标准库 `concurrent.futures`）；
- 不在 CI 里加部署；不动业务代码。

## 4. 允许修改范围

1. `scripts/run_tests_sharded.py`（`run_shards` 并发化 + `--jobs`）；
2. 本批新增 Spec 与红测。
（`discover_modules` / `plan_shards` / `shard_env` / `_parse_totals` 的行为不许变。）

## 5. 禁止事项

- 不许把 `jobs>1` 实现成「仍然串行但打印成并行」；
- 不许用 `ProcessPoolExecutor`（会破坏 `subprocess.run` 的可注入性）；
- 不许改 `tests/` 下任何既有文件（含本批红测）；
- 不许 commit / push / tag / Release / 部署。

## 6. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_parallel_sharded_regression_red -v
./open-claude/.venv/bin/python -m unittest tests.test_release_assurance_batch2_red -v   # 不回归
./open-claude/.venv/bin/python scripts/run_tests_sharded.py --shards 8 --dry-run   # 默认并发 = 半核
time ./open-claude/.venv/bin/python scripts/run_tests_sharded.py --shards 8   # 默认并发，全量验收
```

## 7. 验收清单（登记）

- [ ] `jobs=1` 与 `jobs=N` 的 `totals` 一致（实现后各跑一次比对并留档）；
- [x] 实测墙钟（本机 8 核 / 436 模块）：串行 628.79s → `--jobs 4` 292.30s（全绿）/
  `--jobs 8` ~225s（出 2 条负载相关红）。3 分钟级未达标（约 4.9 分钟），最慢是自带 ODA/xvfb/node
  子进程的用例；后续要么给这些用例单独串行尾片，要么降 `jobs`。
- [ ] 负载相关红本身是否产品缺陷：`packaging_drawing_flow` 的并发重跑竞态（E26）像**真竞态**，
  建议单独开一批定位（本批只记录，不改产品代码）。
