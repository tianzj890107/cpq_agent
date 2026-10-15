# 报价完成门禁的成本版本判定必须只认落库的交接包

血缘：`cpq_quote_gate.py:126 quote_step_completion_gate`（第 3–6 步完成门禁）、
`cpq_wf.py:875 complete_step`（步骤完成 API = 最终写闸，判据取第 2 步落库包）、
`cpq_quote_workspace.py:60 save_pricing`（定价时把 `source_cost_fingerprint` 写进报价）、
`cpq_quote_workspace.py:29 read_snapshot`（第 3–6 步读快照时把第 2 步包投影进来并给
`packaging_cost_fingerprint`）、`cpq_cost_contract.py:14 cost_fields`（指纹唯一算法）。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_quote_gate_fingerprint_red.py`
本批 changelog 条目号：`## 2026-10-15 报价完成门禁成本版本判定权威化`

## 0. 一句话目标

第 3–6 步完成前的只读预检（`POST /api/quote/step-gate`）判「成本版本有没有变」时，
必须和最终写闸（`complete_step`）一样，只看**落库的第 2 步交接包**；
不许拿浏览器回传的那份包（JSON 往返过一次）去算成本指纹。

## 1. 现场证据（34 线上实测，2026-10-15，不是推断）

酒盒原报价 `session_id=522ba91eaaf7` 第 3 步，销售填写 25% 毛利率、点「按此毛利率重算」后，
后端已按 `成本 ÷ (1 − 毛利率) = 63.13686022599918` 落库，但点「确认，进入下一步」被拦：

| 事实 | 出处 |
| --- | --- |
| 预检请求体里的报价指纹 `a16b91bb…83f2`，与落库的第 2 步包指纹、与落库报价的 `source_cost_fingerprint` **三者一致** | 34 实测抓包 + `GET /wf/card/step-data?step_no=3` |
| 但预检仍回 `packaging_quote_stale`「成本版本已变化，原定价不可继续使用。」 | `POST /api/quote/step-gate` 响应 |
| 服务端按**落库包**算的指纹 `a16b91bb…83f2`；把同一份包经浏览器 JSON 往返一次（`JSON.stringify`）再算，得到 `62d4126c…19ff` | 本机 `cost_fingerprint` 分别作用于两种输入 |
| 差异首点是 `0.0`：Python `json.dumps` 写 `0.0`，JS 把它读成整数后写回 `0`（`"anti_scratch": 0.0` → `"anti_scratch": 0`） | 逐字符对比（250592 vs 250170 字符） |
| 落库的第 2 步包与浏览器回传的包**解析后逐键相等**（856645 字符一致） | 34 实测 |
| 最终写闸 `complete_step` 用的是**落库包**（`step_snapshot(session_id, 2)`），因此同一张卡在写闸上不会报这一条 | `cpq_wf.py:920-935` |
| 成本页面上「成本版本已变化」的正确用法仍在（真换成本时预检与写闸都要拦） | `cpq_quote_workspace.py:92` |

即：这是**只读预检独有的误报**——同一份成本，落库版与浏览器往返版算出两个指纹，
预检拿往返版去比落库版，必然不等。

## 2. 契约

### C1 预检的成本包只认落库的第 2 步交接包
预检请求里给了 `session_id`、且负载带 `packaging_package` 时，
判据用的包一律换成 `cpq_wf.step_snapshot(session_id, 2)["packaging_package"]`（只读）；
负载里那份（浏览器往返版）只作为**取不到落库包时的兜底**。

### C2 指纹算法一个字节都不改
`cpq_cost_contract.cost_fields` 的指纹算法、字段清单、`version` 取值都不动——
落库报价里已写的 `source_cost_fingerprint` 必须继续有效，不靠重算历史数据来「修」。

### C3 读不到落库包不改判定口径
读快照失败、会话不存在、第 2 步还没有包装包时，按调用方给的那份包继续判，
**不新增阻断、不抛异常、不写任何表**（预检是只读的）。

### C4 真换了成本仍然要拦
落库包换了（或报价是拿更早的成本算的）时，预检与写闸都要继续报
`packaging_quote_stale`；本 Spec 只去掉误报，不去掉拦截。

### C5 写闸不动
`cpq_wf.complete_step` 的第 3–6 步门禁实现保持原样（它本来就取落库包）。
本批不允许把「谁算指纹」这件事分叉成第二套逻辑。
