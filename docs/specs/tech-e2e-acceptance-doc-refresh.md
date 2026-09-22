# Spec：第 22 步端到端人工验收清单必须与当前产品一致

状态：Spec + 红测（已实现）
红测：`tests/test_tech_e2e_acceptance_doc_current_red.py`

## 背景（用户反馈）

`docs/specs/tech-agent-recovery-22-e2e-scenarios.json` 是「第 22 步 · 端到端场景」的人工验收
清单，但它的若干 manual 步骤描述的交互已经被后续批次改掉，照它验收会得到错误结论：

| 清单里还写着 | 当前产品实际是 |
| --- | --- |
| e2e-03「左侧操作栏的上一步 / 下一步 / 主要操作可用且随 stage 变化」 | 上一步 / 下一步在**右侧底栏**（`tech-workbench.html` 的 `tech-workbench-bottom`，`#techPrev` / `#techNext`） |
| e2e-04「点「零件清单」→ 右侧清单」 | 零件清单是**右侧看板常驻区块**，不再是从左侧按钮打开的临时视图 |
| e2e-09「左侧上下文卡显示各自的 page_context」 | 可见上下文卡已删除；`page_context` 仍然存在，但它是**发给 Agent 的请求字段**（见 `assembly-integration.js` 的 `page_context: '2.2 组装与整合'`），页面上没有这张卡 |
| e2e-10「用左侧『失败重试』…重发」 | 常驻「失败重试」入口已下线；失败以**普通会话输出**呈现，并提供**就近重试** |

这不会直接造成线上故障，但会让「按第 22 步验收」得到错误结论。

## 范围

- 只改 `docs/specs/tech-agent-recovery-22-e2e-scenarios.json`（验收清单正文）。
- 不改：任何业务代码、测试文件、`automated` 里引用的既有测试路径。

## 验收要求

- **R1 结构不缩水**：仍是 `step: 22`，仍是 10 个场景、id 顺序 `e2e-01 … e2e-10` 不变，
  每个场景的 `title` / `manual` / `expected` / `depends_on` 都保留；`automated` 里引用的
  测试文件必须真实存在（不存在的要改成现存文件，不许直接删掉引用）。
- **R2 过期话术清零**：清单任何位置不得再出现「失败重试」「左侧上下文卡」「点「零件清单」」
  「左侧操作栏的上一步」这类已下线交互的描述。
- **R3 如实描述当前交互**：清单要出现并正确使用「右侧底栏」（上下步）、「常驻」
  （零件清单）、「会话输出」（错误呈现）与「重试」（仍要能重发同一条命令）；
  e2e-09 要说明 `page_context` 是发给 Agent 的请求上下文，而不是页面元素。
- **R4 语义不弱化**：验收强度不能因为改写而下降 —— 每个场景仍要写明可观察的预期结果
  （`expected`），人工步骤仍要可执行、可判定；不得把两条场景合并或改成「略」。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_e2e_acceptance_doc_current_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`

## 备注

本批的「实现」是文档改写，不改业务代码；红测是清单与产品的**一致性测试**（结构化断言
10 个场景、引用存在、过期话术消失、当前话术出现），因此改写后可长期防止清单再次漂移。
