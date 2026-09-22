# 部署脚本的健康等待窗口：不许因为「冷启动慢」把成功的部署判成失败并跳过 5~7 步

血缘：承接 `deploy-build-identity.md`（部署版本身份与 `scripts/deploy_34_bare.sh`）、
`deploy-selfcheck-skip-vs-pass.md`（自检三态；脚本自己不能制造假结论）。

状态：Spec + 红测（已实现）
红测：`tests/test_deploy_health_wait_red.py`
依赖：`scripts/deploy_34_bare.sh` 第 4 步「健康检查与 PATH 核对」

## 0. 一句话目标

第 4 步等 `/api/health` 的窗口必须**大于本机冷启动的最坏耗时**，超时必须仍然是失败（不许放宽
判据），并且等待期间要能看出「是真的慢」还是「一直没起来」。

## 1. 现状缺口（34 实测，2026-09-22 10:02 那次部署）

第一次跑 `bash scripts/deploy_34_bare.sh ytbz` 的结果：

```
== 4. 健康检查与 PATH 核对 ==
✗ /api/health 的 status 不是 ok；看 nohup.out
```

但服务**其实起来了**：紧接着手工探测 `http://127.0.0.1:8010/api/health` 返回
`status=ok`、`build.commit=7578b36…`，8012 也在位。原因是窗口写死为 `seq 1 40` × `sleep 2`
= **80 秒**，而这次冷启动（uvicorn 首轮导入 cadquery 等重依赖 + `/api/health` 首答自带能力探测）
超过了 80 秒。

代价不只是多跑一次：脚本在第 4 步 `fail` 退出，**第 5 步（真转两份样本）、第 6b 步（隔离端到端
自检）、第 7 步结论都没跑** —— 一次成功的部署被脚本自己的窗口判成失败，而且看上去像"服务坏了"。

## 2. 契约

- **C1** 窗口可配：环境变量 `CPQ_HEALTH_WINDOW_SECONDS`，缺省 `180`（秒）。
- **C2** 窗口缺省值 ≥ 120，且必须 **deadline 驱动**（`while` 比 `date +%s` 差值），不许再写死循环次数。
- **C3** 判据不放宽：仍然只认 `/api/health` 的 `status == "ok"`；超时仍然 `fail`。
- **C4** 超时文案必须指向日志（`nohup.out`）并说明窗口可调（`CPQ_HEALTH_WINDOW_SECONDS`）。
- **C5** 等待期间每 20 秒打一次心跳（含已等秒数 / 窗口），成功时打印实际等待秒数。
- **C6** 脚本仍能通过 `bash -n`；第 4 步之后的步骤编号与语义一字不改。

## 3. 边界

只改 `scripts/deploy_34_bare.sh` 第 4 步的等待实现。不动第 0/1/2/2b/3/5/6/6b/7 步的任何判据，
不放宽 health 的成功条件，不把「起不来」也当通过。
