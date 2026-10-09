# 发布保障（批次 2）：分片全量回归 + 稳健共享测试件 + CI 有效门禁

血缘：承接 `chain-consistency-batch1.md`（批次 1 的纪律是「先跑红、改完复跑不回归」）、
`packaging-stage-order-equals-dependency.md`（§2.3「编号表只能一处派生」）、
`packaging-cost-readiness-panel.md`（契约测试的写法基线）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测；业务实现由 Codex 在本地落地，按本文 §5 未提交/未推送/未部署，红测 13 项已全绿）
红测：`tests/test_release_assurance_batch2_red.py`
复核基线：2026-10-09 实测（§1 的每个数字都可复现）

## 0. 一句话目标

把「全量回归」从 10~11 分钟的长尾动作变成 3 分钟级的常规动作，并把三类**会淹没真实回归的
假红**（脆弱的 JS 抠取、冻结计数、手抄编号表）收敛成共享、稳健、可枚举的测试件；
CI 的语法检查从 4 个文件扩到全部一方 Python，并保持「CI 不部署」这条边界。

## 1. 现状缺口（代码事实 + 10-9 实测）

### 1.1 T1：没有分片回归运行器，全量只能串行

- `scripts/` 下没有分片/并行运行器（`ls scripts/` 只有部署、导入、`reclaim_test_tmpdirs.py` 等）；
  全量只能 `python -m unittest discover -s tests -p 'test_*.py'`，实测 10~11 分钟（432 模块 / 约 6970 项）。
- 回归纪律已在 `AGENTS.md`/changelog 里写了「全量是提交前置条件」，但缺 3 分钟级的执行器，
  客观上仍在鼓励「只跑相关模块」。
- 临时目录已有统一闸门（`tests/_tmp_guard.py`：「一次运行一个根」+ `atexit` 整根删），
  所以分片运行器**必须给每个分片独立的根**，否则多进程会互相删对方的临时目录。

### 1.2 T3：JS 抠取是每个测试各自实现的括号计数（真实脆弱点）

`tests/` 里至少有 17 份各自实现的 `js_function` / `_js_function` / `function_body`
（`test_home_cards_equal_height_red.py:70`、`test_tech_stage_context_nine_stages_red.py:39`、
`test_chat_echo_must_pair_with_agent_output_red.py:87` …），实现都是「找 `function <name>(`
再从 `{` 数括号到 0」。它不区分字符串 / 注释 / 模板串 / 正则，函数签名或函数体里出现
`}` 字面量就会抠错，进而让多个模块**集体报红**——这类红不是业务回归，但会淹没真实回归。

### 1.3 T4：冻结计数断言散落各处（419 处）

`grep` 实测 `assertEqual(len(...), <数字>)` 形态 **419 处**。其中一部分是**有意的口径冻结**
（如 `test_cpq_eval_production_backed.py:389`「五阶段口径被改坏了」、`=13`「13 子步骤口径被改坏了」），
但用「计数」表达时，**新增一个合法条目也会红**，与「防偷加接口」的本意不符。

### 1.4 T5：前端编号表仍是手抄，缺逐行比对

- 后端唯一事实源：`tech_app/backend/services/workflow_stages.py`（5 阶段 × 13 子步骤）。
- `tech_app/frontend/tech-workbench.js:19` 仍是**手抄**的 `const STAGES = [...]`（9 行）。
- `tests/test_packaging_stage_order_red.py`（10-9 实测 **7 OK**）覆盖的是**行序**与另外四个
  手抄文件（`workflow.js` / `requirement-create.js` / `requirement-confirm-page.js` /
  `report-publish-result.js`）的派生约束；**没有任何测试把 `tech-workbench.js` 的 9 行与后端逐行比对**。
- 10-9 只读比对结果：9 行与后端首个子步骤**当前 0 处漂移** —— 也就是说这是一条「还没被守住」的护栏，
  不是已存在的缺陷；本批要把它变成可执行的逐行比对。

### 1.5 T12：CI 语法检查只覆盖 4 个文件

`.gitlab-ci.yml` 的 `python_contract` 实测是：

```yaml
- python -m unittest discover -s tests -p 'test_*.py'
- python -m py_compile cpq_suite_server.py cpq_auth.py cpq_wf.py cpq_tech_bridge.py
```

仓库一方 Python 共 **660** 个文件，`py_compile` 只点了 4 个；也没有空白/冲突标记检查。
（好消息：CI 里没有任何 `deploy` / `ssh` / `systemctl` / `git pull` —— 这条边界要坚持。）

## 2. 契约

### 2.1 T1：`scripts/run_tests_sharded.py`

纯本地、不联网、不改仓库。公开 API：

- `discover_modules(root="tests") -> list[str]`：返回**排序后**的点分模块名，集合必须等于
  `tests/test_*.py` 的文件集合（不多不少）；
- `plan_shards(modules, shards) -> list[list[str]]`：确定性（同输入同输出）、**两两不相交**、
  **并集等于输入集合**；`shards <= len(modules)` 时每个分片非空；分片大小尽量均衡；
- `shard_env(index, base_tmp) -> dict`：给第 `index` 个分片一套环境变量，其中 `TMPDIR`
  指向**该分片独有的**根（不同分片的根互不相同），供 `tests/_tmp_guard.py` 隔离；
- CLI：
  - `--list` → 每行一个模块名，退出码 0；
  - `--shards N --dry-run` → 打印 N 条分片命令（每条含 `-m unittest` 与该分片的 `TMPDIR`），**不执行任何测试**，退出码 0；
  - `--shards N [--json]` → 在**独立进程**里跑每个分片，聚合 `Ran / failures / errors / skipped`；
    任一分片非零 → 整体非零；`--json` 输出机器可读汇总（含每个分片的命令、退出码、计数）。

纪律：**分片不得把多个模块塞进同一个 `unittest` 进程的不同 shard 里共享 TMPDIR**；
「3 分钟」是优化目标，不作为验收硬指标（验收的是分片正确性与隔离性）。

### 2.2 T3：`tests/support/js_source.py`

`function_body(source: str, name: str) -> str` —— **唯一**的 JS 函数体抠取实现，必须正确处理：

1. 嵌套 `{}`；2. 单/双引号字符串里的 `{` `}` `//`；3. 模板串 `` ` ` `` 及其 `${...}`（含嵌套大括号）；
4. 行注释 `// ...` 与块注释 `/* ... */` 里的括号；5. 正则字面量（如 `/}/`、`/[/{]/`）。

找不到函数名 → 返回 `""`（不抛异常）。既有 17 份私有实现**本批不动**，由后续批次逐个迁移过来
（先有唯一实现，再谈替换）。

### 2.3 T4：`tests/support/frozen.py`

- `frozen_diff(actual, allowed) -> {"extra": [...], "missing": [...]}`：两侧都按**字符串排序**去重；
- `assert_closed_set(case, name, actual, allowed)`：`set(actual) == set(allowed)` 才通过；
  失败信息必须**分开列出「新增（extra）」与「缺失（missing）」**，让人一眼看出是"多加了接口"
  还是"删了口径"。

语义：**用集合枚举替代计数断言**——新增合法条目时改 `allowed` 即可，删/改则必然红。
419 处迁移是后续批次的事；本批只交付这个可复用的断言件。

### 2.4 T5：`tests/support/stage_table.py`

- `frontend_rows(path) -> list[dict]`：解析 `tech-workbench.js` 的 `STAGES` 数组，
  每行给出 `id / phase / phaseTitle / no / subTitle / page`；
- `backend_rows()`：从 `tech_app/backend/services/workflow_stages.py` 取每个 `stage_id` 的
  **首个子步骤**行（`process`→3.1、`cost`→4.1）；
- `assert_parity(case)`：9 行逐字段比对，任何差异必须指出**具体的 id 与字段**。

本批先交付「逐行比对」；把前端改成派生（删掉手抄表）留给后续批次，避免一次动到前端加载时序。

### 2.5 T12：`scripts/ci_checks.py` + CI 接线

`scripts/ci_checks.py`（纯本地、不联网）：

- `--compile-all [--root DIR]...`：对一方 Python 做 `compileall`；
  **不带 `--root` 时**默认覆盖 `cpq_*.py`、`tech_app/`、`scripts/`、`tests/` 四个根；
  有语法错 → 非零，并打印出错文件；
- `--whitespace [--base REF]`：等价 `git diff --check <base>...HEAD`；
  给了 `--base` 就按该 ref 取差异，**`--base` 为空串或未给则退化为对工作区差异**；
  有空白错 → 非零并打印文件:行；
- `--json` 给机器可读汇总。

Python API（红测直接 pin，CLI 只是包装）：

- `compile_all(roots) -> {"ok": bool, "checked": int, "failures": [path]}`；
- `whitespace(base=None, cwd=None) -> {"ok": bool, "offenders": ["file:line", ...]}`。

CI 接线（改 `.gitlab-ci.yml` 的 `python_contract`）：**必须**调用
`python scripts/ci_checks.py --compile-all --whitespace --base "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"`，
且该 job 保持 `allow_failure` 缺席（失败即拦合入）；CI 里**不得**出现 deploy / ssh / scp /
rsync / systemctl / git pull。

## 3. 不做什么

- 不改任何业务实现、不动 `tech_app/frontend/*.js` 的运行时代码（T5 只新增比对件）；
- 不迁移既有 17 份 JS 抠取、不迁移既有 419 处计数断言（后续批次分批做）；
- 不把 `dwg_real_samples`（人工触发的真实样本 Job）并入 `python_contract`；
- 不让分片运行器访问网络、不写生产目录、不改 CI 的部署边界。

## 4. 允许修改范围

1. 新增 `scripts/run_tests_sharded.py`；
2. 新增 `scripts/ci_checks.py`；
3. 新增 `tests/support/__init__.py`、`tests/support/js_source.py`、`tests/support/frozen.py`、
   `tests/support/stage_table.py`；
4. 修改 `.gitlab-ci.yml` 的 `python_contract`（只加检查，不改 rules/stages/部署边界）。

## 5. 禁止事项

- 不许改 `tests/` 下任何既有测试文件（含本批红测）；
- 不许改 `tests/_tmp_guard.py` 的既有语义（分片只许给它独立的根）；
- 不许改 `tech_app/frontend/tech-workbench.js` 与 `workflow_stages.py`；
- 不许在 CI 里新增部署 / 生产访问 / Runner 配置变更；
- 不许 commit / push / tag / Release / 部署。

## 6. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_release_assurance_batch2_red -v      # 本批：红 → 绿
./open-claude/.venv/bin/python scripts/run_tests_sharded.py --list | wc -l                 # 模块数
./open-claude/.venv/bin/python scripts/run_tests_sharded.py --shards 4 --dry-run           # 只看计划
./open-claude/.venv/bin/python scripts/ci_checks.py --compile-all --json
./open-claude/.venv/bin/python -m unittest \
    tests.test_packaging_stage_order_red tests.test_cpq_eval_ci_contract -v                # 不回归
```

## 7. 验收清单（本批不做、只登记）

- [ ] 全量回归在分片下与串行的**结论一致**（同红同绿）——实现后做一次串并比对；
- [ ] 「全量回归是提交前置条件」写进 `AGENTS.md`/changelog 的执行口径；
- [ ] 既有限制：分片不会让 `CPQ_DWG_REAL_SAMPLES` 之类的真实样本用例自动开跑。

## 8. 实现记录（2026-10-09，Codex）

按 §2 契约逐条落地，只动 §4 允许范围：

- `scripts/run_tests_sharded.py`（新增）：`discover_modules` / `plan_shards` / `shard_env` 三个纯函数 +
  `--list` / `--shards N --dry-run` / `--shards N [--json]` 三段 CLI。分片按**排序后轮转**分配，
  确定性、两两不相交、并集等于全集；每个分片一个独立进程 + 独立 `TMPDIR` 根，跑前 `makedirs`。
- `scripts/ci_checks.py`（新增）：`compile_all(roots)` / `whitespace(base, cwd)` 两个 API；CLI
  `--compile-all` 默认覆盖 `cpq_*.py` / `tech_app/` / `scripts/` / `tests/` 四个根，`--whitespace`
  等价 `git diff --check`（`--base` 为空则退化为工作区差异）。
- `tests/support/{__init__,js_source,frozen,stage_table}.py`（新增）：唯一 JS 函数体抠取
  （字符串 / 注释 / 模板串 / `${...}` / 正则里的括号不算结尾）、集合封闭断言（分开列出新增与缺失）、
  前后端编号表逐行比对（按 id 逐字段）。
- `.gitlab-ci.yml` 的 `python_contract`：`py_compile` 四文件改为
  `python scripts/ci_checks.py --compile-all --whitespace --base "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"`，
  失败即拦合入（无 `allow_failure`）；CI 仍无 deploy / ssh / scp / rsync / systemctl / git pull。

实跑（2026-10-09，`./open-claude/.venv/bin/python -W ignore -m unittest`）：

- `tests.test_release_assurance_batch2_red`：改前 `Ran 13 … FAILED (failures=13)`，改后 `Ran 13 … OK`；
- `scripts/run_tests_sharded.py --list | wc -l` = 434 = `ls tests/test_*.py | wc -l`（不多不少）；
- `scripts/ci_checks.py --compile-all --json` → `{"ok": true, "checked": 663, "failures": []}`；
- `tests.test_packaging_stage_order_red tests.test_cpq_eval_ci_contract`：`Ran 31 … OK`（不回归）。

§7 登记的两项（分片与串行的结论比对、「全量回归是提交前置条件」写进执行口径）仍留给后续批次。
