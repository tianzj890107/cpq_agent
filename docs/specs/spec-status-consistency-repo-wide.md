# 规格：Spec「状态」行与红测事实一致（全仓，不限快速报价系列）

血缘：`spec-status-consistency.md`（第一批只覆盖 `quick-quote-*.md`，其 §3 把其它系列记为
「历史存量，另批再收」）——本 Spec 就是那个「另批」。

状态：Spec + 红测（已实现）
红测：`tests/test_spec_status_truth_red.py`
依赖：`docs/specs/spec-status-consistency.md` §1.1（两个字面量）、
`tests/test_spec_status_consistency_red.py`（快速报价系列的既有守卫，本批不动）。

## 0. 为什么有这一条（2026-09-22 实测，不是推断）

`## 293` 的全库红面对账给出的结论是"没有能做而没做的"，但**读 Spec 头得到的结论恰恰相反**。
对账当天实测：

```
docs/specs/*.md                                  231 份
  写了「状态：」行                                 107 份
    写法机器读不了（TDD Red／待实现／Spec（待实现）／**未实现**）      73 份
      其中 48 份逐字写着 `状态：TDD Red，等待 DeepSeek 实现。`
    写着「未实现 / TDD Red / 待实现」                  65 份
      其中 63 份的红测当前早已全绿（结论与事实相反）
    有 `红测：` 行                                   41 份（另 66 份没有）
  完全没写状态行                                    124 份（历史存量，见 §4）
```

两个具体后果，都不是"文档不好看"：

- **重复排查、重复写红测**：`quote-home-industry-carryover.md`（`## 214` 落地）、
  `quote-packaging-box-library-selection.md`（20 条早已全绿）、`repo-leftovers-and-sample-data.md`
  等仍然写着「未实现」；任何按 Spec 头判断的人都会把它们当成待办。
- **能力声明失真**：`tech-step-status-*` / `integration-*-tab-*` / `chat-*` 这一大批写着
  `TDD Red，等待 DeepSeek 实现。`，而它们的红测（同一批已入库）**现在全绿**。

这和 `spec-status-consistency.md` §0 记的是同一种漂移，只是范围从 13 份扩到 107 份。

## 1. 契约

### 1.1 状态行必须是两个合法字面量之一（全仓）

任何**写了** `状态：` 行的 `docs/specs/*.md`：该行（允许前缀 `- `、允许 `**`）去掉强调符之后，
必须以这两个字面量之一**开头**：

```
状态：Spec + 红测（未实现）
状态：Spec + 红测（已实现）
```

后面允许追加说明（例如 `状态：Spec + 红测（未实现）（F1 与 E5 断言互斥，见 changelog ## 256）`）。
**不许**再出现 `TDD Red，等待实现。`、`待实现（红测已落地）`、`Spec（待实现）`、`**未实现**`
这类等价但机器读不了的写法。

### 1.2 声明了状态就必须点名存在的红测文件

同一份 Spec 必须有一处 `红测：` 行，其反引号里点名的 `tests/*.py` 路径必须真实存在。
（`红测：` 行里允许同时写命令、`Ran N OK` 这类备注；判据只看 `tests/*.py` 那部分。）

### 1.3 声明「未实现」必须写明原因，且红测当前真的在失败

- `状态：Spec + 红测（未实现）` 后面**必须**带括号说明（为什么还没做／卡在哪个已记录的冲突）；
- 该 Spec 点名的每一个红测文件当前必须**失败**（`unittest` 非零退出）。

反向（声明「已实现」→ 必须全绿）本批**不**由守卫逐份跑：107 份里有 5 份点名的红测带着
**已记录的测试侧冲突**（见 §2.2），逐份跑会既慢又把"冲突"误报成"没实现"。那个方向由
`## 293` 记录的全库红面对账流程覆盖（8 路并行跑全部 `tests/test_*_red.py`）。

### 1.4 范围

- **在范围**：`docs/specs/*.md` 里**已经写了** `状态：` 行的每一份（本批对账 107 份）；
- **不在范围**：完全没写状态行的 124 份历史存量（`tech-agent-recovery-*` 早期批次、纯前端
  样式 Spec 等）——要给它们补状态行是另一批；本批不因为它们失败，也不假装它们已对账。

### 1.5 对账结果（本批，2026-09-22）

| 项 | 数 | 说明 |
| --- | --- | --- |
| 写了状态行的 Spec | 107 | 对账前 = 对账后，份数不变 |
| 改成 `已实现` | 105 | 点名的红测当前全绿（9 份另带 §2.2 的冲突备注） |
| 保留 `未实现` | 2 | `quick-quote-12-case-maintenance.md`、`tech-model-call-row-merged-and-summary-detail.md`，都补了原因 |
| 本批实际改动文件 | 89 | 66 份「状态行 + 补 `红测：` 行」，23 份「只改状态行」 |

## 2. 本批做了哪些具名修正

### 2.1 从「未实现／等待实现」改成「已实现」的典型

| Spec | 之前 | 事实 |
| --- | --- | --- |
| `quote-home-industry-carryover.md` | `Spec + 红测（**未实现**）` | `## 214` 已落地，`Ran 28 OK` |
| `quote-packaging-box-library-selection.md` | `（**未实现**，20 条用例：18 红 / 2 绿护栏）` | `Ran 20 OK` |
| `quote-agent-industry-alignment.md` / `quote-industry-mismatch-notice.md` / `quote-markup-gate-advice.md` / `quote-nonstandard-path.md` / `quote-tech-handoff-button.md` | 同上 | 各自的红测当前全绿 |
| `deploy-build-identity.md` | `Spec + 红测（**未实现**）` | `## 295` 起 34 的 `/api/health` 已带 `build.commit` |
| `spec-status-consistency.md` | `Spec + 红测（**未实现**）` | 它自己的守卫 `Ran 4 OK` |
| `repo-leftovers-and-sample-data.md` | `Spec + 红测（**未实现**）` | 红测全绿 |
| ~20 份 `TDD Red，等待 DeepSeek 实现。`（`chat-*` / `tech-step-status-*` / `integration-*-tab-*` / `quote-tech-*` 等） | `TDD Red，等待…` | 同一批红测早已入库并全绿 |

### 2.2 保留「未实现」或带冲突备注的 5 份（都不是本批能自行裁决的）

| Spec | 声明 | 冲突来源 |
| --- | --- | --- |
| `quick-quote-12-case-maintenance.md` | 未实现 | F1 与 E5 断言互斥、不可同时成立（`## 256`），已上报测试侧 |
| `tech-model-call-row-merged-and-summary-detail.md` | 未实现 | 该交互已被 `## 133` 退役，红测保留为冲突锚点 |
| `packaging-requirement-confirm-order-guard.md` | 已实现 + 备注 | 该红测 5 条 ERROR 是打桩元数冲突（`## 289`），实现已落地 |
| `packaging-quote-send-recovery.md` | 已实现 + 备注 | 该红测 c1 是夹具自遮挡（`## 272`），实现已落地 |
| `packaging-cost-finance-access.md` / `packaging-parts-outline-chaining.md` / `packaging-manual-field-confirmation.md` 等 | 已实现 | 点名的红测里另有一条 `## 262` / `## 266` / `## 273` 记录的测试侧冲突 |

### 2.3 顺带修掉的格式残缺

- 5 份 Spec 的**状态行原本跨两行**（`tech-chat-composer-flush-bottom.md`、
  `tech-home-three-tabs-and-todo-tasks.md`、`quick-quote-1-mode-and-case-model.md`、
  `packaging-parse-to-downstream-seams.md`、`packaging-bom-part-size-provenance.md`），
  被截断的续行现在收进同一行的括号说明里；
- 其中「本 Spec 取代 `tech-home-timeline-and-publish-closure.md` §7.4」这类**前提**没有丢，
  只是从被截断的行尾移进状态行的括号说明。

## 3. 红测映射（`tests/test_spec_status_truth_red.py`，7 条）

| 组 | 覆盖 |
| --- | --- |
| A | 每个写了状态行的 Spec 都以两个字面量之一开头；写了状态行的份数不低于 100（防规则空转）；状态可机械判定 |
| B | 每个写了状态行的 Spec 都有 `红测：` 行，且点名的 `tests/*.py` 真实存在 |
| C | 声明「未实现」的 Spec 必须写明原因；其点名的红测当前必须失败；不得出现"全部 Spec 都声明未实现"这种显然失真 |

## 4. 非目标

- 不给 124 份没写状态行的历史存量 Spec 补状态行（另批；本批不假装它们已对账）；
- 不逐份跑「声明已实现 → 必须全绿」（成本与误报，理由见 §1.3；由全库红面对账流程覆盖）；
- 不改任何业务实现、不改任何既有红测的期望值、不动 §2.2 那 5 条冲突的裁决权；
- 不把状态做成自动生成的索引（继续只要求"字面量与事实一致"，避免再造一份事实源）。
