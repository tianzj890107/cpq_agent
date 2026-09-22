# 2.3「确认成本」的缺口分级与签字放行（0 元行不再硬拦）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_cost_confirm_zero_waiver_red.py`

## 一、要解决的问题

财务在 2.3「确认成本」时，只要有一行算出来是 0 元（模型没给材料明细是常见原因），
点「仍要继续」之后**还是确认不了**：

1. 看板上的「确认成本」按钮是**灰的** —— `crData.ready` 为假（`ready` 把 0 元行也算作"没算全"）；
2. 从左侧操作栏点「确认成本」时，前端确实弹了「成本还有没算完的地方 … 确定要继续吗？」，
   用户点了「仍要继续」，前端把请求发了出去，但后端
   `cost_flow.confirm_review()` 第一件事就是把同一批缺口**再抛一次**：

   ```
   这些行算出来是 0 元：P1、整机。请重算或人工补上材料明细 —— 0 元送到报价那头会变成没有成本的产品
   ```

   于是界面回到「确认失败」，用户看到的现象就是「点了仍要继续还是拦着」。

这与 2.2 / 1.1 / 1.2 / 1.3 已经落地的口径不一致：那些环节的前端弹窗承诺
「平台会记下是你签的字，之后不再按同一批缺口拦你」，后端也确实把签字落库并复用。
2.3 是**唯一一处"前端说可以带缺口继续、后端没有豁免机制"**的环节。

## 二、缺口分级（沿用既有 L0–L4）

| 等级 | 本步的典型场景 | 处理 |
| --- | --- | --- |
| L1 生成依赖 | 零件清单为空（`counts.parts == 0`）—— 没有可确认的对象 | **硬拦，签字也不放行** |
| L2 质量依赖 | 还有零件没算成本 / 整机（组装）成本还没算 / 某一行算出来是 0 元 | 弹一次「仍要继续」，签字后带着缺口确认 |
| L4 合规 | 写入数据库 / 回传销售经理 / 交回工艺经理 / 权限 | 原样不动 |

## 三、契约

### C1 后端：`confirm_review()` 两级判定 + 签字落库

`tech_app/backend/services/cost_flow.py::confirm_review(project_id, user, waiver=None)`：

- 保留 L1 硬拦：`counts["parts"] == 0` 时抛 `CostFlowError("还没有零件可以确认，请先完成成本测算")`，
  **传 waiver 也不行**；
- 缺口一次算全（复用 `cost_review.summarize()`，不另算一份）：未算零件、整机未算、0 元行；
- 缺口存在时：
  - 请求里带了 `waiver`（人在弹窗上按的「仍要继续」）→ 落一条 `stage="cost_review"` 的签字，
    `missing_codes` 以**服务端算出的缺口**为准，`reason` 留空时补默认原因，写签字人与时间，
    并 `store.audit(..., "cost_review_confirm_waived", ...)`；已被同一批签过字时记 `reused=True`，
    不再重复索要；
  - 请求里没带 `waiver`，但库里已有覆盖同一批缺口的签字 → 直接放行（同一批缺口不拦第二次）；
  - 都没有 → 抛 `CostFlowError`，消息一次列全缺口并提示「可以点『仍要继续』带缺口确认」；
- 缺口比对键是**编码集合**：签字之后又冒出新的 0 元行 / 新的未算零件时集合变大，
  覆盖不成立，必须重新签。

### C2 后端：返回体把缺口与签字交给前端

`cost_review.payload()` 的 `review` 里新增：

- `gaps`：`{codes, fields, text, count}` —— 本步缺口的比对键、中文名、一句话与条数；
- `cost_waiver`：最近一条本步签字的摘要（`stage / missing_codes / missing_fields / reason /
  waived_by / waived_at / reused / count`），没有则为 `None`。

既有字段（含 `waiver` = 2.2 的报价必填签字）一个不删、语义不变。

### C3 前端：`cost-review.js` 认签字、不再硬禁用

- 新增**纯函数** `crWaiverCoversGaps(waiver, codes)`：与后端 `waiver_covers()` 同一条规则
  （缺口编码集合 `need ⊆ have`），`waiver` 为空返回 `false`；可脱离页面单独求值；
- `crConfirmBlocker()` 的文案不变，但「没有零件」与只读身份单独作为**硬拦**（不允许「仍要继续」）；
- 右看板「确认成本」按钮只按「有没有零件 + 忙闲 + 只读身份」禁用（0 元行不再置灰），
  点了走与左侧同一段闸门；
- 缺口存在且**未被签字覆盖**时弹一次「仍要继续」（文案不变）；点「仍要继续」→ 带着
  `{reason}` 调既有的 `POST /cost-review/confirm`；点「取消」→ 即停、不发送不落库；
- 缺口已被签字覆盖时**不弹**，只用普通会话输出说明缺口与签字人，然后照旧确认；
- 权限（只读身份）与三个去向的既有校验一律不放宽。

### C4 路由

`POST /api/projects/{project_id}/cost-review/confirm` 增加可选的请求体
（`waiver: {reason}`），老调用（无请求体、Agent 工具）行为不变；权限仍是 `auth.COST_ROLES`。

## 四、禁止事项

- 不放宽 L1（没有零件不给确认）、权限、写入数据库 / 回传报价 / 交回工艺经理的既有校验；
- 不给 Agent 自动豁免：`oc_agent` 的确认成本工具不带 waiver，缺口该报错就报错；
- 不改成本算法与 2.2 的报价必填清单；不新增第二套缺口或签字实现。

## 五、验收

1. 0 元行 + 无签字：`confirm_review()` 抛 `CostFlowError`，消息里点出 0 元行并提示可点「仍要继续」；
2. 0 元行 + 传 waiver：确认成功，`review.confirmed=True`，`waivers` 有一条 `stage="cost_review"`、
   `missing_codes` 覆盖 0 元行、`reason` 有默认原因、`waived_by` / `waived_at` 有值，
   审计含 `cost_review_confirm_waived`；
3. 同一批缺口第二次确认（不传 waiver）不再拦；签字后新冒出的 0 元行仍然拦；
4. 没有零件时传 waiver 仍然拦（L1）；
5. `payload().review` 带 `gaps` 与 `cost_waiver`；
6. 前端纯函数 `crWaiverCoversGaps()` 与后端 `waiver_covers()` 判定一一对应；
7. 端到端（node 真跑 + 真后端探针）：「确认成本」按钮不再因 0 元置灰，点「仍要继续」后确认成功，
   缺口随返回体带出去。
