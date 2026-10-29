# 34部署记录：ytbz/ca8eeef（2026-10-29）

- 应用提交：`ca8eeefbad44ffb7b712d5ccc461cada7458294b`，双远端ytbz均已推送并独立回读确认。
- 原提交：`947f62c9f33261c01d8310a508d28df4720b62bd`；服务器只fast-forward，不改写历史。
- 环境：http://172.16.10.34:8010/；部署时间：2026-10-29T10:18:05.775060+08:00。
- 备份：`/home/wugefei/CPQ/deploy_backups/ytbz-ca8eeefbad44-20261029-101759`。停写窗口内备份全部持久化目录、设置/密钥文件及原版本stamp，未修改业务库成本数据。
- 8010 PID 2108305，8012 PID 2108414，沿用各自原工作目录、启动命令、PATH及外部env。
- 首页与/api/health均200，status=ok，build.commit精确等于本次应用提交。首页新智能体名称、客户默认规格、报价页、新JS及两个品牌PNG均200；报价页及JS响应与本地已提交文件SHA256一致。
- 设置及密钥文件指纹未变；原有持久化文件无缺失；ODA仍available，文本模型仍deepseek-v4-flash。本轮未创建新的业务报价或解析历史项目。

| 持久化目录 | 部署前文件数 | 部署后文件数 |
|---|---:|---:|
| cpq_history | 244 | 244 |
| xbom_history | 62 | 62 |
| rule_history | 15 | 15 |
| tech_app/tech_data | 3147 | 3147 |
| product_images | 20 | 20 |
| tech_app/data | 34 | 34 |
| cpq_data | 1 | 1 |

真实价库仍缺匹配成品成本和包装附加费规则：此发布不代表该单已生成真实报价；缺价继续在第2步明确提示。

部署记录随后作为纯文档提交双推；运行应用版本保持上述ca8eeef，记录提交不替换应用代码或stamp。
