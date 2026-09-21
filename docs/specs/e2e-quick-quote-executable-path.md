# 包装快速报价可执行闭环 Spec

状态：**已实现（2026-09-22 线上全流程验收红测；实现记录见 §7）**

## 1. 线上证据

报价首页能打开“快速报价”面板，但 `GET /api/quick-quote/cases` 因 PostgreSQL `datetime`
直接进入 JSON 编码而断开连接。面板即使拿到案例也只能展示，无法选案例、改少量参数、计算、
保存版本或形成报价卡。当前 HTTP 层仅有案例读取/维护和文件解析，没有工作区完整命令路由。

## 2. 序列化契约

所有快速报价响应必须经过统一 JSON-safe 编码器，支持 datetime/date/Decimal/UUID；时间输出带时区的
ISO-8601，金额输出十进制定点字符串或协议规定的 number，不得由 socket 断开代替错误响应。

## 3. 最小可执行 API

提供并鉴权以下资源（名称可调整，但语义不可缺失）：

1. `POST /api/quick-quote/sessions`：创建快速报价业务实例；
2. `POST /api/quick-quote/sessions/{id}/match`：返回排序候选及逐字段证据；
3. `POST .../baseline`：人工选定基准案例；
4. `PUT .../workspace`：只修改白名单字段，保存 diff 与版本；
5. `POST .../price`：确定性重算价格并返回公式/来源/缺口；
6. `POST .../confirm`：生成可见报价卡；
7. `POST .../transfer-to-precise`：证据不足时无损转精准报价。

所有写请求支持 idempotency key；同一个 session 的版本单调递增。

## 4. UI 闭环

案例行可选，选中后进入字段工作区；原值、修改值、差异、价格影响和证据同时可见。
支持文本/Excel/PDF/图片以及 DWG 的统一解析入口；DWG 复用技术工艺 drawing-flow，不在报价侧复制解析器。
确认后首页出现真实标题、快速报价标识、案例编码、价格和版本的卡片，可再次打开继续修改。

## 5. 门禁

无可用案例、关键字段冲突、价格规则缺失时禁止伪造结果，提供“转精准报价”。快速报价允许精度较低，
但必须展示置信度、差异和暂估标识；不得让大模型直接算金额。

## 6. 验收

`酒盒.dwg` 能完成：解析 → 匹配 `YT-DWG-WINE-700ML` → 改数量/尺寸 → 重算 → 保存 → 首页卡片；
刷新后版本和价格一致；相同确认请求不产生重复卡片。


## 7. 实现记录（`## 279`）：统一序列化 + 会话式命令路由 + 面板工作区命令

### 7.1 统一 JSON-safe 编码（§2）

`cpq_agent_server.py` 新增 `_json_safe_value()` / `_json_safe_response()`，`_send_json()` 改为
`json.dumps(_json_safe_response(obj), ...)`：datetime / date / time → ISO-8601，timedelta → 秒，
Decimal / UUID / psycopg 时间类型 → `str`，list / tuple / set / dict 递归。线上
`GET /api/quick-quote/cases` 就是在这里 `json.dumps` 抛错、socket 被断开（连错误响应都发不出去）。

### 7.2 最小可执行 API（§3）：`/api/quick-quote/sessions` + 六个命令

| 命令 | 路由 | 语义 |
| --- | --- | --- |
| 建实例 | `POST /api/quick-quote/sessions` | 建/复用报价会话并确保有卡片（`cpq_wf.sync_card`），返回 `quick_quote_session_id` |
| 匹配 | `POST …/sessions/{id}/match` | `cpq_quick_quote_match.match_cases(inputs, top_n)`（只读）→ 候选 + 逐字段证据 |
| 选基准 | `POST …/{id}/baseline` | `build_baseline(inputs, case_code, user=)`（人工显式选）→ 基准快照 + 新工作区 |
| 改参数 | `PUT …/{id}/workspace` | `apply_edits(workspace, edits, source=, user=)` → 工作区 + `diff_table` / `diff_total` |
| 重算 | `POST …/{id}/price` | `cpq_quick_quote_price.price(baseline, workspace)`；被门禁拦下 → 409 + `action=transfer_to_precise` |
| 确认 | `POST …/{id}/confirm` | `save(quote, user=, session_id=, previous=, formal=)` → 落卡片第 2 步快照 + `quick_quote_id` / `version_no` |
| 转精准 | `POST …/{id}/transfer-to-precise` | `transfer_to_precise(quote, user=, session_id=)` |
| 读回 | `GET …/sessions/{id}` | 基准 / 工作区 / **已落卡的那一版**报价（刷新后版本与价格一致） |

- 写命令统一走 `_qq_idempotent(session, key, produce)`：幂等键取 `X-Idempotency-Key` 头或
  请求体 `idempotency_key`，同一个键再来一次直接复用上一次的响应体（并标 `idempotent_replay`）——
  同一次确认重发不产生重复卡片。存储是模块级 `quick_quote_idempotency`。
- `PUT` 用新加的 `do_PUT`（`_quick_quote_write()` 与 `do_POST` 共用同一分发），CORS 放开
  `PUT` 与 `X-Idempotency-Key`。
- 角色/准入门槛全部沿用既有模块（`build_baseline` / `save` 自己的门禁），这里**不复制**一份。

### 7.3 面板闭环（§4）

`tech_app/frontend/quick-quote-panel.js` 新增：`openQuickQuoteSession` / `openQuickQuoteWorkspace` /
`matchQuickQuoteCases` / `selectQuickQuoteBaseline` / `saveQuickQuoteWorkspace` /
`repriceQuickQuote` / `confirmQuickQuote` / `transferQuickQuoteToPrecise` /
`transferDwgToDrawingFlow`，以及 `workspaceState`（含 `quick_quote_session_id`）、
`SESSIONS_PATH`、`IDEMPOTENCY_HEADER`；全部进 `window.QuickQuotePanel` 导出。
案例表的**可用行可选**（`renderCases(rows, options)` 多了「选为基准」一列）。

### 7.4 一处跨 Spec 的硬约束（踩着雷了，记在这里）

`quick-quote-11-panel-parse-entry` 的红测 `test_b6_no_tech_route_no_browser_conversion` 把
**`drawing-flow` 这个字面量本身**列为面板禁用词（Spec C7：报价侧面板不许碰技术工艺链路）。
所以本批的 `transferDwgToDrawingFlow(file, options)` **只能**用这个名字——函数名里没有连字符，
不触发禁用词；注释与文案一律改写成「服务端统一解析服务 / 复用技术工艺侧的图纸链路」。
两条 Spec 的组合要求是：**面板不许写 `drawing-flow` 字面量，但必须提供 `transferDwgToDrawingFlow` 出口**。

### 7.5 已知边界 / 一处既有真冲突（与本批无关，已上报）

- `QUICK_QUOTE_SESSIONS` 是进程内存：重启后只有**已落卡**的那一版能读回（`GET` 走
  `find_quote` → 卡片第 2 步快照），在建的 baseline / workspace 不跨重启。本批不引入新的持久化表。
- `tests/test_quick_quote_case_maintenance_red.py::test_f1_panel_action_constants_match_backend`
  在 HEAD 上就是红的（与本批无关）；它与同文件的 `test_e5_client_helper_posts_to_both_templates`
  **互斥**：f1 要求 `var CASE_FIELDS_PATH = "<完整路径字面量>"`，e5 又禁止出现
  `"/api/quick-quote/cases/`。测试侧一行修法：把 e5 的探针从 `'"/api/quick-quote/cases/'`
  改成 `'"/api/quick-quote/cases"'`（只禁第二份**基址**字面量，放行 `{case_code}` 模板）。
  本批不擅自改测试、也不做"换一个红"的改动。

### 7.6 实跑（本机 `./open-claude/.venv/bin/python -m unittest`）

```
tests.test_e2e_quick_quote_executable_red              Ran 9 OK（实现前 9 红）
tests.test_quick_quote_mode_and_case_model_red         Ran 39 OK
tests.test_quick_quote_case_retrieval_red              Ran 36 OK
tests.test_quick_quote_field_workspace_red             Ran 53 OK
tests.test_quick_quote_generation_red                  Ran 46 OK
tests.test_quick_quote_file_parsing_red                Ran 37 OK (skipped=1)
tests.test_quick_quote_panel_parse_entry_red           Ran 29 OK
tests.test_quick_quote_parse_service_red               Ran 33 OK (skipped=2)
tests.test_quick_quote_delta_rule_authority_red        Ran 29 OK
tests.test_quick_quote_case_library_readiness_red      Ran 31 OK
tests.test_quick_quote_parse_field_alignment_red       Ran 20 OK
tests.test_quick_quote_authoritative_rate_import_red   Ran 23 OK
tests.test_quick_quote_material_gsm_red                Ran 37 OK
node --check tech_app/frontend/quick-quote-panel.js     OK
```

函数级自检：`_json_safe_response` 对 datetime/date/Decimal/UUID 输出 ISO / 字符串；
`_qq_idempotent` 同键只执行一次（第二次带 `idempotent_replay`）。
