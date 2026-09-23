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
  完全没写状态行                                    124 份
```

对账后（本批两个提交：`## 301` 先收 107 份已写状态行的，`## 302` 再把剩下 124 份全部补上）：

```
docs/specs/*.md                                  232 份（含本批新增的本 Spec）
  写了「状态：」行                                 232 份（100%）
    已实现                                        230 份（其中 11 份带"已记录的测试侧冲突"备注）
    未实现                                          2 份（都写明原因，红测当前真的失败）
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

- **在范围**：`docs/specs/*.md` 的**全部 232 份**（`## 301` 收已写状态行的 107 份，`## 302`
  把剩下 124 份按同一口径补上状态行 + `红测：` 行）；
- 补状态行时先确定"这份 Spec 由哪个红测守着"：优先取其 `红测：` 行，其次取测试文件 docstring
  里反指的 `docs/specs/<name>.md`，再次取同名 `tests/test_<slug>_red.py`，最后按主题逐份核对
  （`tech-agent-recovery-*` 17 份就是这样对上的：第 4/5/6/7/8…22 步分别对应
  `test_tech_left_chat_controls_restore_red` / `test_tech_result_entries_board_views_red` /
  `test_tech_parts_views_inside_board_red` / `test_tech_drawing_agent_actions_red` /
  `test_tech_requirement_agent_red` … `test_tech_e2e_acceptance_doc_current_red`）。

### 1.5 对账结果（本批，2026-09-22）

| 项 | 数 | 说明 |
| --- | --- | --- |
| 对账后写了状态行的 Spec | 232 / 232（100%） | 对账前只有 107 份 |
| 改成 `已实现` | 230 | 点名的红测当前全绿（11 份另带 §2.2 的冲突备注） |
| 保留 `未实现` | 2 | `quick-quote-12-case-maintenance.md`、`tech-model-call-row-merged-and-summary-detail.md`，都补了原因 |
| `## 301` 改动文件 | 89 | 66 份「状态行 + 补 `红测：` 行」，23 份「只改状态行」 |
| `## 302` 改动文件 | 124 | 本批一次性补上状态行 + `红测：` 行（含 9 份顺带删掉行内 `状态：待实现（红测已就位）`） |

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
| `packaging-requirement-confirm-order-guard.md` | 已实现（13 OK） | 原 5 条 ERROR 是打桩元数冲突（`## 289`），夹具元数已于 9-22 补齐，现 13 条全绿 |
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

### 2.4 `## 302`：剩下 124 份按同一口径补上

- 101 份由"同名红测 / 测试 docstring 反指"直接判定，全部 `已实现`（其中 9 份点名的红测里带着
  `## 133 / ## 226 / ## 262 / ## 266 / ## 273` 记录的测试侧冲突，所以带括号备注）；
- 17 份 `tech-agent-recovery-*` 按主题逐份对到现有红测（见 §1.4）；
- 6 份按主题对上唯一的同名/近名红测：`tech-board-static-action-role-in-snapshot` →
  `test_tech_board_static_action_role_snapshot_red`、`tech-confirm-actions-no-timeout-and-no-failure-cards` →
  `test_tech_confirm_action_timeout_and_no_pinned_cards_red`、`tech-confirm-review-primary-and-optional-note` →
  `test_tech_confirm_review_optional_note_red`、`tech-e2e-acceptance-doc-refresh` →
  `test_tech_e2e_acceptance_doc_current_red`、`tech-summary-3-1-includes-cost-review` →
  `test_tech_summary_report_includes_cost_review_red`、`quote-tech-chat-composer-model-removal-and-bottom-alignment`
  → `test_quote_tech_chat_composer_alignment_red`；
- 顺带删掉 9 份里残留的行内 `Spec 版本：1 · 状态：待实现（红测已就位）`（改成
  `Spec 版本：1（状态行见下）`）——它和文件头的状态行是同一事实的第二个说法，正是本 Spec 要消掉的东西。

## 4. 非目标

- 不再有"没写状态行"的 Spec（`## 302` 之后 `docs/specs/*.md` 232 份全部有状态行）；
- 不逐份跑「声明已实现 → 必须全绿」（成本与误报，理由见 §1.3；由全库红面对账流程覆盖）；
- 不改任何业务实现、不改任何既有红测的期望值、不动 §2.2 那 5 条冲突的裁决权；
- 不把状态做成自动生成的索引（继续只要求"字面量与事实一致"，避免再造一份事实源）。
