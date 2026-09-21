# 规格：报价版本必须有生产入口（卡片第 5 步落版本 + 第 5 步读回历史）

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_quote_version_persistence_red.py`

血缘：承接 `packaging-quote-close-loop.md`（第 8 批 §2.6 报价版本不变式 / §G 报价版本红测 / 验收
「技术工艺重算成本后再次回传：报价里出现版本 2，版本 1 仍能打开」）与
`packaging-quote-draft-and-card-visibility.md`（本批前一份 §1.4 把这条登记为"需要单独一批"）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

让**卡片上真的落得下、也读得回**报价版本：第 5 步「报价方案」确认时把当次报价落成一版
（只增不改、同指纹幂等、换成本出新版），重开这张卡片能在第 5 步看到历史版本 —— 也就是用户说的
"从报价走到零件拆出来、最后再回去"里的那个**回去**。

## 1. 现状缺口（代码级证据，可逐条复现）

### 1.1 `save_version()` 全仓没有生产调用点（P0）

第 8 批把版本表、幂等键、`versions()` / `latest()` / `restore()` 都写好了，**但没有任何生产路径调用它**：

```
$ grep -rn "save_version(" cpq_*.py tech_app --include="*.py" | grep -v tests
cpq_packaging_quote.py:682:def save_version(conn, quote: dict, *, user: Optional[dict] = None) -> dict:
```

全仓生产代码里 `save_version(` 只出现 **1** 次，就是它自己的定义；`cpq_wf.py`（卡片步进）、
`cpq_suite_server.py`（`/wf/card/step-done`、`/wf/card/step-data` 的处理方）里是 **0** 次。
后果：`cpq_wf_quote_version` 表永远空，第 8 批 §2.6 的"只增不改 / 同指纹复用 / 换成本出新版"这套
不变式**没有生产入口**，第 8 批验收第 3 条（"报价里出现版本 2，版本 1 仍能打开"）在真实卡片上
不可能被满足。

### 1.2 卡片第 5 步的快照是**同名覆盖**的，回去只剩一份现值（P0）

`cpq_wf.complete_step()` 对同一 `(card_id, step_no)` 直接 `UPDATE … data_snapshot = …`：做完第 5 步、
技术侧重算成本再回传第 5 步，写进去的是同一格快照 —— 前一次报价**被覆盖**，页面上再也拿不到。
这正是用户要的"到最后再回去"反过来的那一面：回去只看得到最后一次。

### 1.3 读路径也没有版本可读（P1）

`GET /wf/card/step-data?session_id=…&step_no=5` 目前只返回 `cpq_wf.step_snapshot(...)` 这一份现值，
没有任何字段带出版本列表；`cpq_packaging_quote.versions()` / `latest()` 同样没有调用点。
（`restore()` 也一样：全仓无调用点，历史进入时恢复报价这条路没接上。）

## 2. 允许修改范围（实现方）

1. `cpq_wf.py`
   - `complete_step()`：`step_no == 5` 且快照里带得出包装报价（见 §3.1 的判据）时，在**同一事务连接**
     上调用 `cpq_packaging_quote.save_version(conn, quote, user=user)`；成功则返回体里加
     `quote_version`（`version_no` / `quote_version_id` / `already_saved` / `quote_fingerprint`）；
   - 落版本失败**不得**让第 5 步确认整体失败：捕获 `PricingError`，不落版本并在卡片留痕里写一行
     原因（角色不匹配 / 报价不可解析），步骤照旧 `done`；
   - 版本相关只读查询复用 `cpq_packaging_quote.versions()` / `latest()`，不新写 SQL。
2. `cpq_suite_server.py`
   - `/wf/card/step-data`（GET）：`step_no == 5` 时在返回体里加
     `packaging_quote_versions`（`versions()` 原样，新的在前）与 `latest_quote_version`（`latest()`）；
     其它步不加。**读取路径只读**，不许出现任何写入。
3. `确认需求解析结果.html`
   - 第 5 步渲染历史版本列表（版本号 / 时间 / 成本 / 未税单价 / 是否草稿），键名与接口一致
     （`packaging_quote_versions`）；草稿版本必须带"草稿·不得对外"字样。
4. 不改 `cpq_packaging_quote.py` 里 `save_version` / `versions` / `latest` / `restore` 的签名与语义
   （§2.6 已冻结）。

### 2.5 实现记录（`## 275`）：落版本的三处落点与两处判据收紧

- **§2.1 落版本位置**：`cpq_wf.complete_step()` 里、`UPDATE cpq_wf_card_step` 之后、返回卡片之前，
  `step_no == QUOTE_VERSION_STEP`（`= 5`）且 `_packaging_quote_of(snap)` 取到报价时，调用
  `cpq_packaging_quote.save_version(conn, quote, user=user)`；落在**同一个 `conn`** 上
  （`complete_step` 由调用方传连接时是调用方那个事务，自建时是本模块既有连接），
  `save_version` 自身不 commit，只捕 `PricingError`（`role_not_allowed` 等）→ 跳过落版本、
  `_log(..., "quote_version_skipped", ...)` 留痕、步骤照旧 `done`，返回体加 `quote_version`
  （`version_no` / `quote_version_id` / `already_saved` / `quote_fingerprint`）。
- **两处判据收紧（都写进代码，不是文档）**：
  1. `_quote_like()` 除"键在不在"外，还要求这份报价**同时**带 `cost_total` 与 `quote_quantity`。
     只认键会让一份**没定价过**的整包（只有 `cost.total_cost`）落成一条 `cost_total=0` 的假报价，
     而版本表是只增不改的——宁可这一版不落，也不往里塞垃圾。真落版本的那一份由报价页在确认第 5 步时
     随快照带回（见 §2.3）。
  2. 归档用的 `quote_session_id` **强制**取当前卡片的 `session_id`（不信任快照里的值），
     `business_case_id` 缺省时回落到卡片上那一个——版本是"这张卡片的这一版"，读回路径
     （`/wf/card/step-data`）就是按卡片会话号查的。
- **§2.2 读回**：`cpq_wf.quote_version_state(session_id)`（只读；函数级 import
  `versions()` / `latest()` 避开模块级循环）供 `/wf/card/step-data` 在 `step_no == 5` 时
  带出 `packaging_quote_versions` / `latest_quote_version`；读取分支里没有任何写入（B1 护栏）。
- **§2.3 前端**：报价页定价成功后把这一版存进 `LAST_PACKAGING_QUOTE`，`wfCompleteStep` 第 5 步
  随快照带 `packaging_quote`（定价发生在浏览器，不带回去服务端无从知道"这一版算的是什么"）；
  `wfRestoreStepData(5)` / `showStep(5)` 渲染版本列表，草稿行带「草稿·不得对外」。
- **未做（§6 原文照旧）**：`restore()` 的自动回填、版本回滚/删除、跨会话对比、技术侧与
  `/agents/quote` 侧的版本读取面——本批只接卡片第 5 步这一条。

## 3. 契约（可验收）

### 3.1 什么时候落版本

第 5 步 `step-done` 的快照里能取到一份包装报价时落版本；取不到的卡片（非包装行业、或第 1–4 步）
**不许**落版本。判据（择一即可，但必须落在代码里而不是文档里）：快照里有 `packaging_quote` /
`packaging_package` 键，或快照的 `s5_*` 分区里同时有 `cost_total` 与 `quote_quantity`。

### 3.2 落版本必须复用既有实现与既有不变式

- 只能经 `cpq_packaging_quote.save_version()` 写 `cpq_wf_quote_version`；
- 同 `quote_fingerprint` 重复确认 → `already_saved=True`，版本数不增；
- 成本变了 → 新版本且带 `previous_version_no` / `previous_cost_total`；
- 旧版本行不被 UPDATE / DELETE（只增不改，§2.6）。

### 3.3 角色与失败语义

- 只有 `WRITE_ROLES`（`sales_mgr` / `admin`）落版本；其它角色做第 5 步（含代做）时**跳过落版本**、
  留痕写明原因，步骤照旧完成 —— 不许因为落不了版本把整步卡死；
- 缺口包落的是草稿报价（`pricing_profile` / 缺口字段原样保留），正式版本仍被
  `cost_gaps_unresolved` 拦（本批不动这道门）。

### 3.4 读回

`GET /wf/card/step-data?session_id=…&step_no=5` 返回 `packaging_quote_versions`（≥2 版时按版本号
降序、旧版本仍可读）与 `latest_quote_version`；重复确认后重开该卡片，页面能列出全部版本并打开旧版本
的报价单内容（第 8 批验收第 3 条）。

## 4. 禁止事项

- 不许改 `save_version` / `versions` / `latest` / `restore` 的既有签名与不变式（§2.6 冻结）；
- 不许在读取路径（`/wf/card/step-data`、任何 GET）里写版本；
- 不许对 `cpq_wf_quote_version` 用 UPDATE / DELETE，不许用新行覆盖旧行的 `document_md` /
  `inputs_json` / 金额；
- 不许在 `cpq_packaging_quote.py` 之外新写 `INSERT INTO cpq_wf_quote_version` 绕过 `save_version`；
- 不许改任何既有红测（含 `test_packaging_quote_close_loop_red.py` 的 G 组字面常量与
  `test_packaging_quote_draft_and_card_visibility_red.py`）；
- 不许改成本/定价口径、不许改数据库 schema、不许改前端其它步骤的字段形状；
- 不许 commit / push / tag / Release / 部署 / 重启服务 / 写生产库。

## 5. 验收

- 红测：`./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_version_persistence_red`
  —— 实现前必须红（本文件登记的四条：生产调用点、调用点位置、第 5 步读回、前端渲染）；
- 手点（34）：第 5 步确认 → 卡片里出现版本 1 → 技术侧改成本再确认第 5 步 → 版本 2 出现且版本 1
  仍能打开；同一次确认点两次不出现版本 3。

## 6. 本批不做（写在这里，别当成已通）

- `restore()` 的历史进入恢复（重开卡片自动回填旧报价）与版本回滚/删除入口（只增不改，不做回滚）；
- 跨会话/跨业务实例的版本对比、版本导出；
- 报价版本的其它读取面（技术侧项目页、`/agents/quote` 侧路由）—— 本批只接卡片第 5 步这一条。
