# 部署自检的「跳过」不许被算成「通过」：三态判决 + 内部令牌失效必须重取并显式失败

血缘：承接 `packaging-parts-downstream-acceptance.md` §6.1（第 6b 步的隔离端到端自检）、
`deploy-build-identity.md`（部署版本身份）、`packaging-parts-selfcheck-diagnostics.md`（自检要指着原因说话）。

- 状态：**已实现**（红测 `tests/test_deploy_selfcheck_skip_vs_pass_red.py` 9 条全绿）
- 红测：`tests/test_deploy_selfcheck_skip_vs_pass_red.py`
- 依赖：`scripts/deploy_34_bare.sh` 第 6b 步（隔离自检与权威实样路线自检）

## 0. 一句话目标

"有一项**没跑**"和"所有项**都通过**"必须在输出里长得不一样，而且都不能是同一个绿色结论：
自检的结果只有三种 —— `ok`（全跑全过）/ `failed`（有真问题）/ `incomplete`（有项没跑成）。

## 1. 现状缺口（34 实测，`925c241` 部署自检输出）

```
== 6b. 下游连通自检（隔离端到端：不需要项目 id，也不写任何项目数据） ==
· 已从运行中的服务进程取到服务间内部令牌，知识库自检可以真跑
· 酒盒.dwg：八步 8/8 completed；零件 64 件（closed_ratio=0.938）；可算 9 / 可挤出 9
· 圆盘盒.dwg：八步 8/8 completed；零件 9 件（closed_ratio=0.889）；可算 1 / 可挤出 8
· 权威实样路线自检：读不到知识库（知识库快照接口返回 HTTP 403：{"ok": false, "error": "内部令牌校验失败"}），跳过
{"isolated_downstream_selfcheck": "ok", "problems": []}
隔离端到端自检通过（建项目 → 需求草稿 → 八步 flow → 零件文档 → 单件详情 → 挤出）
```

- **权威实样路线自检这一项根本没跑**，输出里却仍然是 `"ok"` 与"隔离端到端自检通过" ——
  这正是脚本自己在注释里写的要防的失效模式（"自检没跑"和"自检通过"长得一样）；
- 根因是**内部令牌失效**：脚本取到了令牌（`已从运行中的服务进程取到`），但快照接口回
  `403 内部令牌校验失败`。重启顺序是"先子后父"，取令牌的时机与真正在服务的进程可能不是同一代，
  令牌一变，整项自检就静默降级成 `跳过`；
- 更早一次（`0d8884d`）也出现过同一族问题：取不到令牌时打印"知识库自检会打印原因跳过"，
  总判定同样不受影响。

## 2. 三态判决（硬口径）

1. `isolated_downstream_selfcheck` 的取值闭集必须是 `{"ok", "failed", "incomplete"}`：
   - `ok`：**所有**检查项都真跑且通过；
   - `failed`：任一项有真问题（现状已有）；
   - `incomplete`：任一项**没跑成**（取不到令牌 / 快照 403 / 样本缺失 / 跳过）；
2. 只要存在任一 skip，就**不许**打印"隔离端到端自检通过"，也不许退出码为 0；
3. 输出必须带逐项三态清单（新增键）：

```json
{"isolated_downstream_selfcheck": "incomplete",
 "checks": [{"name": "酒盒.dwg", "status": "pass"},
            {"name": "圆盘盒.dwg", "status": "pass"},
            {"name": "权威实样路线", "status": "skipped",
             "reason": "internal_token_rejected: HTTP 403 内部令牌校验失败"}],
 "problems": [], "skipped": [{"name": "权威实样路线", "reason": "internal_token_rejected"}]}
```

4. `status` 闭集 `{"pass", "failed", "skipped"}`；`skipped` 必须同时出现在 `checks` 与顶层 `skipped` 里
   （顶层是给门禁读的稳定形状，不许只存在于日志文字里）。

## 3. 内部令牌：取到 ≠ 能用

5. 取令牌必须**先验证再使用**：拿到令牌后先打一次知识库快照接口，非 200 就视为"这一代令牌不可用"；
6. 不可用时必须**重新取一次**（重启后拿新进程的令牌）再试；两次都失败才判 `skipped`，
   并把两次的 HTTP 状态与响应体打进输出（不许只写"跳过"）；
7. 如果取到令牌时服务进程刚重启（`8012` 的启动时间晚于取令牌时刻），必须等它就绪再取 ——
   不许把"上一代的令牌"当成"可以真跑"。

## 4. 红测

`tests/test_deploy_selfcheck_skip_vs_pass_red.py`（静态钉住脚本形状 + 一条形状断言，
都是本机可复现；禁止为了让红测转绿改 `tests/`）：

| 组 | 例子 | 现在为什么红 |
| --- | --- | --- |
| A | A1 `incomplete` 出现在第 6b 步；A2 `checks` 逐项三态清单存在；A3 `skipped` 顶层清单存在；A4 `status` 闭集被写死 | 现在脚本里只有 `ok`/`failed` 两态，没有 `incomplete`/`checks`/`skipped` |
| B | B1 令牌取到后必须先验证（脚本里出现快照校验）；B2 校验失败必须重取（出现重试）；B3 失败原因必须带 HTTP 状态与响应体 | 现在取到令牌就直接"可以真跑"，403 只写"跳过" |
| C | C1 有 skip 时不许打印"自检通过"（`自检通过` 必须只在 `ok` 分支）；C2 有 skip 时不许退出 0 | 现在 403 之后仍然打印"通过"、退出 0 |

## 5. 禁止事项 / 不变面

- 不许放宽 `packaging-parts-downstream-acceptance.md` §3 的样本门槛（只许更严）；
- 不许把 skip 改写成 pass（例如"跳过也算过"）；不许删掉权威实样路线自检这一项；
- 不许改 `tests/`（含本文件对应红测）、不许改两份真实 DWG 样本、不许改 8010/8012 的启动方式与
  env 文件口径（`PATH` 前缀、`load_dotenv(override=False)` 两条不变）。

## 6. 实现记录

### 6.1 三态判决（§2.1–§2.4）

- `scripts/deploy_34_bare.sh` 第 6b 步的 `selfcheck.py`：`checks` / `skipped` 两个清单 +
  `add_check(name, status, reason)`（`status` 闭集 `{pass, failed, skipped}`，跳过同时进
  `checks` 与顶层 `skipped`）；判决 `verdict = "failed" if bad else ("incomplete" if skipped else "ok")`，
  输出 `{"isolated_downstream_selfcheck", "checks", "problems", "skipped"}`。
- 退出码 = 判决：`0` = ok、`1` = failed、`2` = incomplete；shell 侧 `case "$SELFCHECK_RC"`
  三态分流，`incomplete` 走 `fail`（非零退出），"自检通过"那句只在 `verdict == ok` 的分支里。
- 逐项粒度：两个样本各一项（缺失 → `skipped:sample_missing`），权威实样路线**按盒型各一项**
  （尺寸区间缺失 → `skipped:size_range_missing`；confirm 不是 confirmed / 抛异常 → `failed`）。

### 6.2 令牌先验证再使用（§3.5–§3.7）

- `selfcheck_fetch_token()`：只从正在服务的进程取（先 8012 再 8010 兜底）；
- `selfcheck_probe_snapshot()`：拿到令牌先打一次 `GET /wf/tech/kb/snapshot`，把 `HTTP <状态> <响应体>`
  打印出来（不写"跳过"两字了事）；非 200 即视为这一代令牌不可用；
- `selfcheck_wait_service_ready()`：重取前先等 `/api/health` 回 `ok`（最多 60s），避免拿到上一代令牌；
- 重试一次（`for _attempt in 1 2`）；两次都不行 → `CPQ_SELFCHECK_KB_SKIP_REASON` 带上
  `internal_token_rejected: 第 1 次：HTTP 403 … / 第 2 次：HTTP 403 …`，`selfcheck.py` 据此把
  「权威实样路线」判 `skipped`，整条自检判 `incomplete`。

### 6.3 实跑

本机（`./open-claude/.venv/bin/python`）：红测 `Ran 9 OK`；
把新第 6b 步单独搬到 34 真跑（真令牌 + 两份真实 DWG + 知识库）：

```
· 已从运行中的服务进程取到服务间内部令牌，快照校验通过（第 1 次），知识库自检可以真跑
· 酒盒.dwg：八步 8/8 completed；零件 64 件（closed_ratio=0.938）；可算 9 / 可挤出 9
· 圆盘盒.dwg：八步 8/8 completed；零件 9 件（closed_ratio=0.889）；可算 1 / 可挤出 8
· 权威实样 YT-DWG-ROUND-10PC：路线 9 道，confirm=confirmed
· 权威实样 YT-DWG-WINE-700ML：路线 10 道，confirm=confirmed
{"isolated_downstream_selfcheck": "ok", "checks": [...4 项全 pass...], "problems": [], "skipped": []}
隔离端到端自检通过（verdict=ok；…）
· 第 6b 步判决 verdict=ok：所有检查项都真跑且通过
```

令牌不可用那一路用注入的 skip 原因在本机验过：`verdict=incomplete`、逐项 `checks` 里
`{"name": "权威实样路线", "status": "skipped", "reason": "internal_token_rejected: 第 1 次：HTTP 403 …"}`、
顶层 `skipped` 非空、退出码 `2`。

### 6.4 未做 / 边界

- 未改 `packaging-parts-downstream-acceptance.md` §3 的样本门槛，未删权威实样路线自检；
- 未改 8010/8012 的启动方式与 env 文件口径（`PATH` 前缀、`load_dotenv(override=False)` 两条不变）；
- 未改任何 `tests/`。
