# 09 · 工程流程：Spec / 红测 / 提示词、测试与部署门禁

## 1. 工作方式是这套系统的一部分

这个仓库的工程流程本身就是一项**设计决策**，而且是被反复验证有效的：

```
需求
 → Codex 写可验收的 Spec（`docs/specs/*.md`）
 → Codex 写能复现缺口的红测（先跑，确认真失败）
 → Codex 给 DeepSeek 一份实现提示词（目标/上下文/允许改动范围/禁止事项/验收命令）
 → DeepSeek 实现
 → Codex 审查 + 跑测试 + 核对 Spec，有问题继续给修正提示词
 → 提交 → 双远端推送 → 部署 34 → 部署后核对 → 写 changelog
```

边界写得很死（`AGENTS.md`）：**Codex 不写业务实现**；只有测试脚手架、Spec、提示词、仓库配置和用户明确授权的内容可以直接改。

规模：`docs/specs/` **391 份**、`tests/` **432 个模块**、全量约 **6970 项**。

## 2. 测试体系

### 2.1 三层

| 层 | 例子 | 作用 |
| --- | --- | --- |
| **行为测试** | `test_packaging_parts_extraction_red` | 跑真实函数，断言输入输出 |
| **前端契约测试** | 从 `.html` 里抠出 JS 函数丢给 `node -e` 执行 | 前端没有构建链，靠这种办法做真实执行验证（不是正则匹配） |
| **冻结计数测试** | `src.count("packaging-business-parts/") == 8` | 钉住「接口/入口数量」，防止偷偷多开一个写接口 |

第三类是本仓库的特色，也是主要摩擦点：**业务演进必然要改这些数字**。

### 2.2 「精确重指」纪律（最重要的流程规则）

冻结断言要改时，只允许两种做法：

- **精确重指**：把旧的期望值改成新的实测值，**同时在测试里写明「为什么旧断言过期、判据为什么没放宽」，并在对应 Spec 里补附录说明授权来源**；
- 或者不改。

**不允许**的做法：把 `assertEqual` 改成 `assertGreaterEqual` / `assertIn` 这类**放宽判据**的改法。仓库里抓到过一次：`## 526` 把某处「derived / sized 件数精确相等」改成了下限，`## 528` 又改回 `assertEqual` 并精确填写实测值（圆盘盒 36 → 46）。

这条纪律的价值：**「测试变绿」不能来自把标准降低。**

### 2.3 反复出现的失效模式（值得拍板的地方）

近两天出现 4 次同类事故（`## 524` / `## 528` / `## 531` / `## 533` 都是「收口」条目）：

> 某批只跑了「相关模块」（20~22 个模块、300~500 项），标了「未全仓测试」；跑全量立刻暴露 3~10 条红。

红的成因高度一致：**旧冻结断言未同步** + 偶发一两条真实回归（例如零件行丢了「原文尺寸逐字保留」、圆盘盒闭合件数 262 ≠ 255、`packagingQuoteDetailValues` 一度未定义）。

结论：**「相关模块通过」不足以作为提交依据**，全量回归是唯一门槛。而全量要 10~11 分钟，客观上鼓励了「先跑相关」。

## 3. 门禁与部署

### 3.1 部署脚本：`scripts/deploy_34_bare.sh`（720 行）

它是**唯一可执行版本**，因为 8010 的 DWG 转换能力分散在三处，少任何一处都会「主转换器失败 → 静默回退 LibreDWG」，而页面只表现为「不是位图，请传 PNG」。脚本步骤：

0. 部署前状态（要求 tracked 工作区干净，记录 prev）
1. 幂等写 env 文件（变量名从代码常量取，不手抄；只打印变量名不打印值）
2. 取代码：`git fetch gitlab <ref>` + `merge --ff-only`（不产生 merge commit）
2b. 落版本 stamp（`cpq_build.json`：commit / branch / ref / deployed_at）
3. 重启 8010（**先停 8012 子进程、再停 8010 父进程**；启动命令带 `PATH` 前缀）
4. 健康检查 + `/proc` 核对 PATH 含 xvfb 目录
5. **真转两份真实样本**，核对 `converter_role=primary`、`fallback_used=false`
6. （可选）指定项目 id 的下游连通自检
6b. **隔离端到端自检**：建临时项目 → 需求草稿 → 八步 flow → 零件文档 → 单件详情 → 试挤出；跑完删临时目录，并核对运行目录项目数不变
7. 结论 + 能力声明口径 + 门禁命令

### 3.2 上线门禁：`tech_app/tools/dwg_deploy_gate.py`（19 项）

`converter_license`（**manual**）、`converter_version_pinned`、`health_reports_capability`、`tmp_dir_permissions`、`disk_quota_and_cleanup`、`conversion_timeout`、`concurrency_limit`、`malicious_cad_isolation`、`model_failure_isolation`、`db_migration_rollback`、`legacy_projects_open`、`non_packaging_no_regression`、`step_flow_no_regression`、`no_secrets_in_logs_or_fixtures`、`no_dev_machine_dependency`、`ci_separates_adapter_and_real_smoke`、`real_samples_e2e_passed`（**manual**）、`converter_chain_configured`、`converter_rollout_documented`。

设计要点：**17 项自动 + 2 项人工**；人工项 Agent 不得代签；`GATE_ITEMS` 只能追加在末尾（id/kind 冻结）。（源码 `GATE_ITEMS` 上方注释仍写「18 项」，是陈旧注释；以元组实际条数 19 为准。）

### 3.3 版本与发布安全（`AGENTS.md`）

- `push` / `merge` / `tag` / `Release` / `部署` **各自独立授权**，历史授权不延续；
- `master` 只能通过 `20260909 → master` MR 合入，禁止直接 push / 自动合并 / force push；
- CI 只允许在 MR 和 `master` 上跑测试/构建，**禁止在 CI 里部署**；
- 部署必须校验目标 tag/commit 并保护服务器设置与历史数据。

### 3.4 changelog

- 只按**自然周**维护（`changelog/changelog_M_D_D.md`，周一至周五），不写日报；
- 每次实际改动后同步；按用户可见能力和**最终状态**合并记录，不记录排查过程或被覆盖的中间方案；
- 编号连续（本周 `## 521`–`## 534`），完成条目必须写清「提交 sha / 双远端回读 / 部署 commit / 部署后核对 / 能力声明」。

## 4. 已知缺口

1. **全量回归的时间成本（10~11 分钟）与「先跑相关」的诱惑**是当前最主要的工程质量风险。`[本地已实现]` CI 已跑全量，但**开发途中缺少 3 分钟级快反馈**，这才是「先跑相关」的根因（`T1` 未做）。
2. 前端契约测试靠**从 HTML 里抠函数**，函数签名一变（如 `aiParamsAutofill()` → `(options)`）就会让多个模块的抠取失效并集体报红 —— 这类红不是业务回归，但会淹没真实回归。
3. 冻结计数测试数量大，**改接口的成本被放大**。
4. 门禁 `malicious_cad_isolation`、`disk_quota_and_cleanup`、`concurrency_limit` 等是「配置在位」类自动项，**不证明真扛得住恶意图纸**。

## 5. 可优化项（供拍板）

### 产品侧（其实这里是「管理侧」）

| 优化 | 收益 | 代价 | 风险 | 我的建议 |
| --- | --- | --- | --- | --- |
| 明确「**全量回归是提交前置条件**」，相关模块只作快速反馈 | 直接消灭近两天的重复事故 | 每批多 10 分钟 | 低 | **建议立规矩** |
| 门禁两项人工项约定谁签、什么时候签 | 让「真实转换能力」这句话有出口 | 组织决定 | 低 | 建议拍 |

### 技术侧

| 优化 | 收益 | 代价 | 风险 | 我的建议 |
| --- | --- | --- | --- | --- |
| 全量回归**并行化/分片**（按模块分 4~8 个进程），目标 3 分钟内 | 从根上消除「先跑相关」的动机 | 中（要处理临时目录与全局状态隔离） | 中 | **建议做，性价比最高** |
| 前端契约测试改成**按名字 + 括号配对**的稳健抠取（不依赖签名逐字） | 消除签名变更引发的集体误报 | 小 | 低 | **建议做** |
| 冻结计数改成**「集合枚举」断言**（列出允许的入口清单，而不是数个数） | 既防偷加接口，又不因新增而集体报红 | 中（要迁移存量） | 中 | 建议做，分批迁移 |
| `[本地已实现]` `.gitlab-ci.yml` 的 `python_contract` 已在 MR/master 跑全量 `unittest discover -s tests` + `py_compile`——改为**验收**：确认 CI 真的在跑、失败真的阻止合入、语法检查覆盖哪些文件，再补齐缺失范围 | 让规矩自动生效 | 小 | 低 | 不是「新增 CI」，是「让已有 CI 有效」；注意 CI 不许部署这条边界 |
