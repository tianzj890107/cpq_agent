# 包装回传报价：落点冲突必须可恢复（409 + 结构化 detail）且恢复字段真的收得到

血缘：承接 `packaging-quote-close-loop.md`（第 8 批回传）、`quote-first-project-entry.md`（报告侧同类 P0）、
`cpq_case_link.py`（落点四态：linked / multiple_candidates / create_new / no_candidate）。

- 状态：Spec + 红测（未实现）
- 红测：`tests/test_packaging_quote_send_recovery_red.py`
- 依赖：`tech_app/backend/main.py`（回传路由与桥接错误出口）、`packaging_handoff.py`、`cpq_bridge.py`

## 0. 一句话目标

包装回传在「这张报价卡片认不回来」时，必须和报告侧**同一形状**：HTTP **409** +（`code` / `candidates` /
`business_case_id`），并且请求模型**真的收得到** `business_case_id` / `create_new` / `create_reason`
—— 不许出现「文案让你填新建原因、接口里却无处可填」。

## 1. 现状缺口（34 实测，可复现）

项目 `f1417060ae9d`（包装，酒盒.dwg，成本已算 6.412359 元/件）：

```
POST /api/projects/f1417060ae9d/requirement/packaging-quote/send
     （PE1，allow_gaps=True，reason 已写）
→ 500 {"detail": "没有找到这条业务实例对应的报价卡片。确认要新建报价卡片时，请填写新建原因后重试。",
       "trace_id": "7af4d5dd5c804aff"}
```

先做一次「明确新建」的合法恢复，再重复同一次回传：

```
POST /api/projects/f1417060ae9d/quote-link/recover
     {"create_new": true, "create_reason": "..."}                 → 200（meta 里写下 create_new=true）
POST /api/projects/f1417060ae9d/requirement/packaging-quote/send  → 500，逐字同一句
```

### 1.1 代码事实（三处）

1. **请求模型缺字段**：`main.py:146 PackagingQuoteSendAction` 只有
   `requirement_no / scenario / allow_gaps / reason`；同类 `ReportQuoteAction` 有
   `business_case_id / create_new / create_reason`（`main.py:165-167`，注释里写着"否则那句
   『请填写新建原因后重试』在界面上根本无处执行（现场 P0）"）。**报告侧修过，包装侧没修。**
2. **桥明明支持**：`cpq_bridge.send_to_quote(..., create_new: bool = False, create_reason: str = "")`
   已经是既有参数；`cpq_case_link.decide(..., create_new=..., create_reason=...)` 就是靠它放行
   `create_new` 结局。包装这条链一次都没传。
3. **错误出口漏了一种异常**：`main.py:6979 _packaging_handoff_flow` 只捕
   `packaging_handoff.HandoffError`；`cpq_bridge.BridgeRejected` 直接冒到全局异常处理器
   （`main.py:7659` 的 `RuntimeError` → **500 + 纯字符串**），而报告侧
   `main.py:7073 _report_flow` 捕了它并走 `_bridge_http_error()`（`main.py:3547`：落点冲突 → 409 +
   `cpq_bridge.conflict_detail(exc)`）。所以同一类失败，报告侧是可操作的 409，包装侧是不可操作的 500。

## 2. 口径

### 2.1 请求模型与透传

`PackagingQuoteSendAction` 增 `business_case_id` / `create_new` / `create_reason`（名字、默认值与
`ReportQuoteAction` 逐字一致），并原样透传到 `packaging_handoff.send_to_quote(...)` →
`cpq_bridge.send_to_quote(create_new=..., create_reason=...)`。
服务层签名同步加这两个关键字参数（默认 `False` / `""`），既有调用点行为不变。

### 2.2 复用项目 meta 里已有的恢复留痕

`/quote-link/recover` 已经把 `create_new` / `create_reason` 写进项目 meta 的 `business_case`
文档（`main.py:1396`）。回传时若请求体没带、而 meta 里已有一次**明确新建**留痕，服务层必须复用它，
不要求用户重新输入同一件事（人已经答过一次的问题不许再问一遍）。

### 2.3 落点冲突必须是 409 + 结构化 detail

`_packaging_handoff_flow` 必须捕获 `cpq_bridge.BridgeRejected`，并复用既有出口
`_bridge_http_error()`：

- 落点冲突（`no_candidate` / `multiple_candidates`）→ **409**，detail 里带
  `code` / `candidates` / `business_case_id`（`cpq_bridge.conflict_detail` 的形状，逐字复用，
  不新写一份）；
- 其它业务拒绝 → 400；服务不可用 → 503（既有 `BridgeUnavailable` 口径不变）。

### 2.4 不许放宽的

- `allow_gaps=False` 时有缺口仍然 409 `cost_gaps_unresolved`（第 8 批冻结口径）；
- 放行必须写明原因（`gap_reason_required`）；多候选时即使带了 `create_new` 也不许自动挑一张
  （`cpq_case_link.decide` 的既有规则，本批不动）。

## 3. 验收（红测逐条对应）

- A 组：请求模型 / 服务层签名（三个字段在位，默认值形状与报告侧一致）；
- B 组：透传行为（`create_new` / `create_reason` 真的进了桥的入参）；
- C 组：meta 恢复留痕复用；
- D 组：错误口径（`_packaging_handoff_flow` 捕 `BridgeRejected`；409 + 结构化 detail 走同一份出口）；
- E 组：回归护栏（缺口门禁、多候选规则不变）。

## 4. 命令与期望

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_send_recovery_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red     # OK
./open-claude/.venv/bin/python -m unittest tests.test_quote_first_project_entry_red      # 22 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_downstream_blockers_red  # OK
```

## 5. 本批不做

- 不改 `cpq_case_link.decide` 的四态规则；
- 不改 `require_project_access` 的口径；
- 不新增报价卡片或任务类型。
