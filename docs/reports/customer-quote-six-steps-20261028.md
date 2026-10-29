# 客户仅提供规格的普通报价入口验收（2026-10-28）

客户输入只需要下面这段文字，不需要成本、来源、毛利率、税率、额外指令或JSON。

**本轮代码已支持这种输入；真实价库尚有缺口，不能声称该真实需求已经六步报价完成。**

## 客户原文

```text
茅台酒盒，客户是贵州茅台。

成品内尺寸：200mm×150mm×80mm。
盖面纸：铜版纸，180克重，亮膜；
底面纸：铜版纸，180克重，哑膜；
盖板材：灰板，厚度2.5mm；
V槽，闭合方式：天地盖；
包装品类：酒盒，盒型：高档酒盒；
配合间隙：1.8mm。
预计首批定1000个。
```

## 已验证行为

1. 真实隔离Chrome从普通报价原聊天输入框输入上述原文并发送，不点击额外入口、不手工补任何内部字段。
2. 受控完整测试价库场景：服务端按全部规格和数量匹配同一成品，读取成本记录、包装毛利/税率及附加费规则。原下一步点击六次，六条步骤均done，Word实际下载，刷新恢复completed。测试库成本记录10.125、毛利25%、每件附加2、税率13%只作为测试夹具，并非真实企业价库记录；演示标记始终保留。
3. 受控缺价场景：原文仍能识别并确认第1步；第2步显示系统缺有效成本，按钮禁止推进，不显示要求客户填写成本的输入框，报价结果为null，刷新保留阻断。
4. 正文的盖/底面纸、180g、不同覆膜、盖灰板2.5mm、尺寸、V槽、天地盖、间隙和1000数量均保留；不会落入旧电池工作温度门槛。
5. 系统记录必须有效、未删除且规格和数量口径完全一致。来源/币种/规则缺失、规格冲突、过期、错用电池规则均拒绝；结构对比用standard_cost不作为订单成本，演示记录也不能被客户端改成正式来源。

上述完整场景仅证明代码路径可以在依据齐备时完成六步，不能证明真实价库已经有这些成本和规则。

## 真实系统只读取证

只读取Postgres的相关成品参数、成本与包装规则，没有调用线上报价写接口，没有修改主数据、历史数据或线上服务。

| 检查项 | 实际结果 |
|---|---|
| 符合有效标志的相关包装参数 | 0行 |
| 可用候选成品的有效成本 | 0行 |
| 包装毛利/税率系数 | 2行 |
| 包装附加费规则 | 0行 |

源文件 `礼盒盒型库_数据样例.xlsx / 01盒型` 第2行明确说明：标准成本基于约200×150×60mm、灰板2.0mm、面纸200g、含普通内托、5000个的参考规格，仅供结构对比，不代表实际报价。因此本需求不能用该标准成本12.5替代本单200×150×80mm、2.5mm盖板、180g、1000个的实际成本。

真实系统当前缺：唯一匹配本单规格及数量的有效成品/成本，以及适用的包装附加费规则。需要内部维护有效业务记录或接入完整成本核算路径；不应该让客户把这些内部参数写进询盘。

## 证据

- [完整价库场景第1步](customer-quote-six-steps-20261028/customer-ready/step1.png)
- [完整价库场景六步完成](customer-quote-six-steps-20261028/customer-ready/step6.png)
- [测试场景实际下载Word](customer-quote-six-steps-20261028/customer-ready/quote.docx)
- [完整场景实际请求与结果](customer-quote-six-steps-20261028/customer-ready/result.json)
- [缺价第2步截图](customer-quote-six-steps-20261028/customer-missing-cost/step2-blocked.png)
- [缺价结果](customer-quote-six-steps-20261028/customer-missing-cost/result.json)
- [真实价库只读结果](customer-quote-six-steps-20261028/live-readonly-probe.json)

复验命令（不启动业务服务、不连接线上写接口）：

```sh
CPQ_BROWSER_ACCEPTANCE=1 CPQ_ACCEPTANCE_ARTIFACT_DIR="$PWD/docs/reports/customer-quote-six-steps-20261028" ./open-claude/.venv/bin/python -W ignore -m unittest tests.test_quote_direct_browser_acceptance.CustomerBrowserAcceptance tests.test_quote_direct_browser_acceptance.CustomerMissingCostBrowserAcceptance
```

验收当时纯文字改动尚未提交、推送或部署；先前已双推的38acbeb是结构化参数版本。10-29用户后续明确授权提交、双推与34部署。新增纯文字红测先实际确认失败，再实现；同步远端首页改动后144项单元/回归通过，2项隔离Chrome验收通过。
