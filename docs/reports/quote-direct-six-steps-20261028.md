# 普通报价提示词 → 原下一步 → 六步完成验收（2026-10-28）

分支：`feat/poc-1030`。本次为**本地隔离 Chrome 验收**，不是34线上验收或真实客户报价。

## 实际执行及边界

- 真实 Chrome（Chrome/156.0.8078.49）加载本分支实际普通报价页面、实际 `quote-direct.js`；在原聊天输入框通过Chrome输入文本、点击发送，不直接调用后端准备函数。
- 随后连续点击原页面 `#btnNext` 六次，执行原 `confirmStep`。没有点击「本单直接报价」、没有额外弹窗、没有补填任何字段。
- 浏览器请求通过CDP转入真实 `cpq_suite_server.Handler`、`cpq_direct_quote.command` 与 `cpq_wf.complete_step`。使用受控事务存储代替Postgres，隔离登录态和已建测试卡代替启动流程，不启动业务HTTP服务、不连接线上、不新建或修改线上业务数据。
- 请求序列严格为一次 `prepare` 和六次 `complete`；六条步骤均 `done`，卡片 `completed`；没有技术工艺接口或派工请求。已实际下载Word，刷新后由已保存数据恢复完成状态。
- 本分支没有包装专用成本引擎。提示词里的成本为明确的演示假设，并非库内查到的真实报价成本；产品编码为用户提供的演示标识，本次不证明主数据或酒瓶适配性。正式使用必须替换为实际核实成本、来源及用途。

## 结果

| 项目 | 结果 |
|---|---|
| 数量 | 1000 |
| 每件未税成本（演示假设） | 10.125 CNY |
| 毛利率 | 25% |
| 每件未税其他加价 | 2 CNY |
| 未税单价 | 15.50 CNY |
| 未税合计 | 15500.00 CNY |
| 税率 / 税额 | 13% / 2015.00 CNY |
| 含税合计 | 17515.00 CNY |
| Word | 实际下载完成；保留“演示报价，不用于正式交易”，不含内部成本或毛利 |
| 刷新 | 完成状态和报价恢复；无新增确认审计 |

## 已实际跑通的提示词

复制整段到本分支普通报价第1步输入框，发送后核对右侧内容，连续使用原来的下一步。此提示词只用于演示流程验收。

```text
茅台酒盒普通报价六步演示验收。成本仅为演示假设。
【直接报价参数】{"1": {"product_name": "茅台酒盒", "product_code": "YT-RB-01001-A", "customer": "测试客户", "specification": "200×150×80mm；天地盖；配合间隙1.8mm；V槽；盖/底180g铜版纸亮/哑膜；灰板2.5mm", "quantity": 1000}, "2": {"unit_cost": "10.125", "currency": "CNY", "cost_source": "仅本地流程验收假设成本，非真实成本单", "confirmed": true, "demo": true}, "3": {"margin_percent": "25"}, "4": {"addon_per_unit": "2", "tax_percent": "13"}}【/直接报价参数】
```

## 证据与复验

- [第1步截图](quote-direct-six-steps-20261028/step1.png)
- [六步完成截图](quote-direct-six-steps-20261028/step6.png)
- [实际下载的演示Word](quote-direct-six-steps-20261028/quote.docx)
- [步骤请求、结果与完整提示词](quote-direct-six-steps-20261028/result.json)

```sh
CPQ_BROWSER_ACCEPTANCE=1 CPQ_ACCEPTANCE_ARTIFACT_DIR="$PWD/docs/reports/quote-direct-six-steps-20261028" ./open-claude/.venv/bin/python -W ignore -m unittest tests.test_quote_direct_browser_acceptance.BrowserAcceptance
```

新增提示词解析/准备红测先实际失败（缺少 `parse_prompt` / 未知 `prepare`），实现后通过；本批合同与相关工作流、首页回归共120项通过，另有1项真实Chrome端到端验收通过。未提交、未推送、未部署。
