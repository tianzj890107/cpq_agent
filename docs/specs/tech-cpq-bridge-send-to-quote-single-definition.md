# Spec：成本回传报价的桥函数 `send_to_quote()` 只能有一份实现，且必须带 `handoff_kind` / `result_version`

## 背景（用户反馈的真实缺陷，最高优先级）

`tech_app/backend/services/cpq_bridge.py` 里 `send_to_quote()` **定义了两遍**：

- `:131` 的一份会发 `handoff_kind: "cost_to_quote"` 与 `result_version`（形参同名）；
- `:159` 的一份没有这两个字段，签名里也没有 `result_version`。

Python 只保留**最后一个**定义，所以第一份是死代码：成本回传实际发出的 payload 里
既没有 `handoff_kind`，也没有 `result_version`。现有测试全绿，是因为它们做的是**静态
文本**提取（拿到的是第一份函数体），从未验证运行时真正绑定的对象是哪一份。

## 缺陷链（实测，不是推测）

1. 客户端 `tech_app/backend/services/cpq_bridge.py::send_to_quote`（当前生效的是第二份）
   发 `POST /wf/tech/handoff`，payload 只有 `session_id` / `title` / `customer` /
   `project_name` / `note` / `source_task_id` / `source_session_id` / `result`。
2. 服务端路由 `cpq_suite_server.py:432` 读 `d.get("handoff_kind") or "cost_to_quote"` ——
   **省略 handoff_kind 时靠默认值侥幸正确**，但这是隐式依赖：默认值一变就会静默串到
   报告回传语义上（`report_handoff` 用的是 `report_to_quote`）。
3. `cpq_suite_server.py:439` 读 `d.get("result_version", "")` → 客户端不带就是 `""`。
4. 服务端 `cpq_tech_bridge.py::send_to_quote` 用
   `_handoff_key(session_id, tech_project_id, handoff_kind, result_version)` 做幂等键
   （实测 `p|s|cost_to_quote|cost-v1:1:100`），并在命中 `open` / `claimed` 的既有任务时
   **直接返回 `already_sent` 早退**：不合并新的 `result`、不派发新任务、不通知销售。
   版本号恒为空时，同一项目 + 同一报价会话的所有成本版本共用一个幂等键，
   于是「成本复核后改了成本再回传」会被前一次仍未关闭的任务吞掉，新成本数永远到不了
   报价卡片 —— 这正是用户说的「同一成本版本重复回传时幂等判断可能失效 / 成本回传可能
   被下游混淆」。

## 范围

- 只改两处前端调用链文件：`tech_app/backend/services/cpq_bridge.py`（客户端桥）与
  `tech_app/backend/services/cost_flow.py`（2.2 / 2.3 的发送报价调用点）。
- 服务端一律不动：`cpq_suite_server.py` 的 `/wf/tech/handoff` 路由、
  `cpq_tech_bridge.send_to_quote`（含 `_handoff_key` 幂等算法、`_dispatch_handoff_task`、
  `_step2_snapshot`、`_merge_report_snapshot`）、`cpq_wf` 的步骤单调性保护。
- 报告回传不动：`report_handoff` 继续发 `handoff_kind: "report_to_quote"` 与
  `report-v{n}` 版本号（`report_workflow.send_to_quote` 用 `cpq_bridge.report_handoff`）。

## 验收要求

- **R1 唯一实现**：`cpq_bridge.py` 里 `def send_to_quote(` 只出现一次；
  `/wf/tech/handoff` 字面量只出现两次（成本回传 + 报告回传）。
- **R2 运行时绑定正确**：把模块真正 import 起来后，`send_to_quote` 的**签名**必须含
  `result_version`，`inspect.getsource` 必须含 `handoff_kind` 与 `"cost_to_quote"`。
  这是本批的核心 —— 静态文本提取过不了这条。
- **R3 发出的 payload 带字段**：把 `_post` 换成假实现，`send_to_quote(...,
  result_version="cost-v1:1:1234")` 实际发出的 payload 必须含
  `handoff_kind == "cost_to_quote"` 且 `result_version == "cost-v1:1:1234"`；
  带上 `handoff_kind` 后不依赖服务端默认值。
- **R4 老签名仍可用**：`cost_flow.py` 现在按位置传 9 个参数
  （token, project_id, title, customer, product_name, note, source_task_id, result,
  source_session_id），这条调用路径必须继续成立，且同样带上
  `handoff_kind == "cost_to_quote"` 与显式 `result_version` 键（缺省 `""`）。
- **R5 版本号随成本变化**：调用点必须把成本结果里的版本号交给桥
  （`cost_flow.result_version(plan)` 已是既有实现，形如 `cost-v1:{quantity}:{total}`，
  已随 `integration_quote_result` 回传）；同一份成本数据重复回传版本号不变，
  成本复核后重算则版本号变化 —— 服务端幂等键因此能区分「重复点击」与「新版本」。
- **R6 不缩水**：`report_handoff` / `write_material` / `send_to_finance` /
  `return_to_process` / `complete_task` 的字段与调用点一字不少；路由、
  `internal_audit` / `store.audit` 留痕、报价第 2/3 步快照结构不变。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_cpq_bridge_send_to_quote_single_definition_red -v`
- 相关既有测试：`python3 -m unittest tests.test_tech_cost_report_handoff_continuity_red tests.test_tech_cost_review_agent_red tests.test_tech_report_publish_agent_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`
- `python3 -m py_compile tech_app/backend/services/cpq_bridge.py tech_app/backend/services/cost_flow.py`
