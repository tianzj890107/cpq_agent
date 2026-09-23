# Spec：投影里每一步的 `status` / `completed` 必须与它自己的两个原因字段一致 —— 已完成的行不再挂「请先完成 X」，草稿期的 1.2 / 1.3 不再报「进行中」

状态：Spec + 红测（已实现）（原状：实现提示词只在会话交付、业务实现不在本批；红基见 §4；
`## 461` 已落地，落点、一处既有测试的重指与实测见 §6）
红测：`tests/test_tech_projection_step_state_must_agree_with_its_reasons_red.py`
血缘：`tech-unified-workflow-projection.md` §5（`blocked_reasons` = **不可执行**的原因；
`actionable = completed || 前置步骤已完成`；1.2 的典型未完成态是 `awaiting_confirmation`、1.3 是 `in_review`）、
`packaging-stage-order-equals-dependency.md` §2.1（行序 = 依赖顺序，1.1 → 2.1 → 1.2 → 1.3，**本批不动**）、
`packaging-requirement-confirm-order-guard.md`（为什么必须有这个顺序）。
本批 changelog 条目号：`## 461`。

## 0. 一句话目标

投影的 `stages[]` 里，一步的 `status` / `completed` 与它自己的 `blocked_reasons` / `missing_requirements`
**必须说同一件事**。今天有两处是互相打架的：

1. **已完成的行挂着"请先完成 1.1"**：`_rows_for()` 的前置循环对**每一行**都追加
   `请先完成 {prior} {sub_title}`，不看这一行是不是 `completed=true`。而按
   `packaging-stage-order-equals-dependency.md` §2.1 的行序（1.1 → 2.1 → 1.2 → 1.3），
   2.1 的"前置"就是 1.1 —— 于是**正常首次流程**（1.1 存草稿 → 2.1 解析 8/8）里，
   2.1 每一眼都是 `generated / completed=true` 却挂着 `["请先完成 1.1 创建需求"]`。
   已完成的步骤本来就跨过了那道门（`actionable = completed || 前置步骤已完成`），
   再挂着这句是自相矛盾的 —— 这正是用户看到"顺序全乱了"的那一眼。
2. **需求还是草稿，1.2 / 1.3 就都写着"进行中"且一个字不说缺什么**：
   `_judge()` 里 1.2 / 1.3 的兜底分支是 `in_progress` + `missing=[]`，
   于是 `draft` 时三行并排显示"进行中"（1.1 / 1.2 / 1.3），既看不出先后，
   也看不出"卡在哪、缺什么"。

**本批不改顺序**（行序与 `packaging-requirement-confirm-order-guard` 的先后是设计，见 §1.3）；
改的是"每一步怎么自述自己"。

## 1. 真实跑证据（34，2026-09-23；本机进程内复现，同一份判据）

用户会话里那次"从头到尾"真跑留下的新项目 `4b1213500624`（`酒盒.dwg`，需求 = `draft`，
`GET /api/projects/4b1213500624/workflow/projection`）：

| key | status | completed | blocked_reasons | missing_requirements |
| --- | --- | --- | --- | --- |
| 1.1 创建需求 | `in_progress` | false | `[]` | `[]` |
| 2.1 图纸解析 | `generated` | **true** | **`["请先完成 1.1 创建需求"]`** | `[]` |
| 1.2 确认需求 | `in_progress` | false | `["请先完成 1.1 创建需求"]` | **`[]`** |
| 1.3 审核需求 | `in_progress` | false | `["请先完成 1.1 创建需求"]` | **`[]`** |

本机用同一支判据（`wp._rows_for(PID, facts, role)`，facts 按上面那份形状构造）
逐字复现：

```text
=== requirement = draft role = （工艺工程师）
  1.1  status=in_progress  completed=False  missing=[]  blocked=[]
  2.1  status=generated    completed=True   missing=[]  blocked=['请先完成 1.1 创建需求']
  1.2  status=in_progress  completed=False  missing=[]  blocked=['请先完成 1.1 创建需求']
  1.3  status=in_progress  completed=False  missing=[]  blocked=['请先完成 1.1 创建需求']
  已完成却挂着前置阻塞的行： ['2.1']
=== requirement = pending_confirmation
  1.2  status=awaiting_confirmation  completed=False  missing=['需求确认尚未提交']
  1.3  status=in_progress           completed=False  missing=[]            ← 还没送审，却"进行中"
=== requirement = pending_review
  1.3  status=in_review             completed=False  missing=['需求审核尚未给出结论']
=== requirement = approved
  1.3  status=approved              completed=True
```

两个根因都在 `tech_app/backend/services/workflow_projection.py`：

```python
# _rows_for()：不管本行完成没完成，只要前置有一步没完成就追加一句
        for prior in keys[:index]:
            if not done_by_key.get(prior):
                blockers.append(f"请先完成 {prior} {prior_spec['sub_title']}")
                break

# _judge()：1.2 的兜底（draft 走这里）
        if req_status == "pending_confirmation":
            return {"status": "awaiting_confirmation", "completed": False,
                    "missing": ["需求确认尚未提交"]}
        return {"status": "in_progress", "completed": False}          # ← draft 也报"进行中"，且不说缺什么

# _judge()：1.3 的兜底（draft / pending_confirmation 走这里）
        return {"status": "not_started" if not req_status else "in_progress", "completed": False}
```

### 1.3 为什么这个顺序不是"乱"（本批不改）

行序按 `packaging-stage-order-equals-dependency.md` §2.1 是 **1.1 → 2.1 → 1.2 → 1.3**：
图纸解析第 8 步 `field_write` 要把图纸里读出的字段**回写进那张需求单**，所以它必须落在
「1.1 存草稿」之后、「1.2 确认」之前 —— 先确认/审核再解析，`field_write` 必 `blocked /
REQUIREMENT_NOT_EDITABLE`（34 实测 7/8）。**顺序本身有据，本批一个字不改**；
本批修的是"已完成的行还挂着前置提示""没开始的步骤报进行中"这两句假话 ——
它们才是让人以为"顺序全乱了"的那部分。

## 2. 契约

### 2.1 已完成的子步骤不得再挂前置阻塞

- 对任一 `completed=true` 的行：`blocked_reasons` 里**不得**出现前置类文本（`请先完成 …`）；
  等价判据：`row["completed"] is True` ⇒ `not any(b.startswith("请先完成") for b in row["blocked_reasons"])`；
- 理由：`tech-unified-workflow-projection.md` §5 明确 `blocked_reasons` 是**不可执行**的原因，
  而 `actionable = completed || 前置步骤已完成` —— 已完成的行不存在"前置没做完"这件事；
- **未完成的行必须逐字保留今天的前置文本**（不许为了修这条把前置提示整段删掉），
  `draft` 时 1.2 / 1.3 仍要写 `["请先完成 1.1 创建需求"]`、`pending_confirmation` 时 1.3 仍要写
  `["请先完成 1.2 确认需求"]`。

### 2.2 需求还没走到那一步时，1.2 / 1.3 不许报「进行中」，且必须说清缺什么

按需求单 `status` 逐条定死（`missing_requirements` 只要求非空、可读，不锁自由文案）：

| 需求 `status` | 1.2 预期 | 1.3 预期 |
| --- | --- | --- |
| 空（没有需求单） | `not_started` | `not_started` |
| `draft` | **`not_started`**（今天 `in_progress`）+ `missing` 非空 | **`not_started`**（今天 `in_progress`）+ `missing` 非空 |
| `pending_confirmation` | `awaiting_confirmation` + `missing=["需求确认尚未提交"]`（逐字不变） | **`not_started`**（今天 `in_progress`）+ `missing` 非空 |
| `pending_review` | `confirmed / completed`（逐字不变） | `in_review` + `missing=["需求审核尚未给出结论"]`（逐字不变） |
| `approved` | `confirmed / completed`（逐字不变） | `approved / completed`（逐字不变） |

- 1.1 的口径**逐字不变**：`draft` = `in_progress` 且 `completed=false`（§5 表）、
  `pending_confirmation` / `pending_review` / `approved` = `confirmed / completed=true`；
- 1.2 / 1.3 合法的 `status` 取值收敛为：1.2 ∈ {`not_started`, `awaiting_confirmation`, `confirmed`}；
  1.3 ∈ {`not_started`, `in_review`, `approved`}。

### 2.3 冻结点（一个字不改）

- 行序（`workflow_stages.STAGES` 的 `_STAGE_ROWS` 顺序）、子步骤号、阶段号、标题；
- 2.1 / 3.1 / 3.2 / 3.3 / 4.1 / 4.2 / 4.3 / 5.1 / 5.2 / 5.3 的判据与文案；
- `_phases()` 的聚合规则（取第一个未完成子步的状态）；`_next_action()` 的挑选规则
  （先"未完成且可执行"、再"第一个未完成"）；
- `actionable` 的算法、权限与 `required_role`、`viewable`、`stale`、`primary_action`、`next_stage`；
- 接口形状与状态枚举；前端渲染；`packaging-requirement-confirm-order-guard` 的两道门禁。

## 3. 红测映射（`tests/test_tech_projection_step_state_must_agree_with_its_reasons_red.py`，10 条）

| 用例 | 夹具 | 期望 | 现状 |
| --- | --- | --- | --- |
| A1 | 包装项目 + 需求 `draft` + 解析产物（= 34 上 `4b1213500624`） | 2.1 `completed=true` 时，`blocked_reasons` 里没有 `请先完成 …` | 红（今天挂着「请先完成 1.1 创建需求」） |
| A2 | 同上，全 13 行 | 通用不变量：任何 `completed=true` 的行都不挂前置文本 | 红（2.1） |
| A3 | `draft` | 1.2 = `not_started` + `missing` 非空 | 红（今天 `in_progress` + `[]`） |
| A4 | `draft` | 1.3 = `not_started` + `missing` 非空 | 红（今天 `in_progress` + `[]`） |
| A5 | `pending_confirmation` | 1.3 = `not_started` + `missing` 非空（还没送审） | 红（今天 `in_progress` + `[]`） |
| A6 | `draft`，行级 | 1.2 / 1.3 两行（含 `blocked_reasons` / `missing_requirements`）都说得清 | 红 |
| B1 | `draft` | 1.1 仍是 `in_progress` / `completed=false`（§5 表逐字不变） | 护栏（今天绿） |
| B2 | `pending_confirmation` / `pending_review` / `approved` | 1.1 / 1.2 / 1.3 的 `confirmed` / `approved` / `completed` 逐字不变 | 护栏（今天绿） |
| B3 | `draft` / `pending_confirmation` | 未完成行的前置文本逐字保留 | 护栏（今天绿） |
| B4 | 合成 rows / 无需求 / IR 项目 | `_phases()` 聚合、无需求时 1.1 `not_started`、2.1 无解析产物 `not_started` + 「图纸还没有解析」、IR 有零件 2.1 完成 —— 全不变 | 护栏（今天绿） |

## 4. 实测（本机，HEAD 工作副本）

```text
tests.test_tech_projection_step_state_must_agree_with_its_reasons_red   Ran 10 … FAILED (failures=6)
  A1  2.1 completed=true 却挂着 ['请先完成 1.1 创建需求']                        （红）
  A2  同上，通用不变量                                                          （红）
  A3  1.2 draft：'in_progress' != 'not_started'，missing=[]                     （红）
  A4  1.3 draft：'in_progress' != 'not_started'，missing=[]                     （红）
  A5  1.3 pending_confirmation：'in_progress' != 'not_started'，missing=[]      （红）
  A6  draft 时 1.2 / 1.3 两行都说不清缺什么                                      （红）
  B1–B4 1.1 草稿口径 / 三步完成口径 / 未完成行的前置文本 / _phases 与 2.1 判据   （护栏绿）
```

不回归（本批未改业务实现，逐条复跑）：

```text
tests.test_packaging_cost_stage_must_see_the_parsed_parts_red          Ran 8  OK
tests.test_packaging_integration_stage_must_count_its_own_analysis_red Ran 8  OK
tests.test_packaging_parts_filtered_two_books_must_agree_red           Ran 7  FAILED (failures=5, skipped=1)  # `## 460` 自己的红基，与本批无关
tests.test_spec_status_truth_red                                       Ran 7  FAILED (failures=2)  # 另外两份在写的 Spec（并发会话）点名的红测还不存在，与本批无关
```

（`tech_unified_workflow_projection_red` 里那 1 条 `RequirementCompletionTest` 失败是既有挂账
—— 它按 1.3 在 2.1 之前排的老行序断言 `actionable=true`，与 `packaging-stage-order-equals-dependency.md`
的行序冲突，见 `changelog ## 455` 与本节 §1.3；本批既不修它也不动行序。）

## 5. 交付边界

本批只写 Spec + 红测（业务实现不在本批）；未改业务实现、未改既有测试、未放宽任何断言、
未起服务、未发 HTTP、未连 PG / 34、未写业务数据；未 push / MR / tag / Release / 未部署。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 461`）

只改一个业务文件 `tech_app/backend/services/workflow_projection.py`（两处兜底），外加把
`tests/test_packaging_tech_projection_2_1_must_see_drawing_flow_red.py` 的 B6 重指到**真正未完成**的行
（见 §6.2，断言强度不变）。

| 契约 | 落点 | 实现 |
| --- | --- | --- |
| §2.1 已完成的行不挂前置阻塞 | `_rows_for()` | 前置循环包进 `if not completed:`；未完成行的文本逐字不变（`请先完成 {prior} {sub_title}`，仍是"第一个没完成的前置步"） |
| §2.2 1.2 不许报「进行中」 | `_judge("1.2")` 兜底 | 草稿 / 已退回 → `not_started` + `missing=["需求单当前是「draft」，还没提交确认"]`；`pending_confirmation` 的 `awaiting_confirmation` + 逐字文案不变 |
| §2.2 1.3 不许报「进行中」 | `_judge("1.3")` 兜底 | 无需求单 → `not_started`（无 `missing`，逐字不变）；草稿 / 已退回 / `pending_confirmation` → `not_started` + `missing=["需求单当前是「…」，还没送审"]`；`pending_review` 的 `in_review`、`approved` 的 `approved / completed` 不变 |
| §2.3 冻结点 | 未动 | 行序、子步骤号、`actionable` 算法、权限、`viewable` / `stale` / `primary_action` / `next_stage`、`_phases()` / `_next_action()`、接口形状与 `STATUS_ENUM`（`not_started` 早就在枚举里，没加新值）全部未动 |

### 6.1 复跑命令与结果（本机 `./open-claude/.venv/bin/python -m unittest`）

```text
tests.test_tech_projection_step_state_must_agree_with_its_reasons_red
    # Ran 10 … OK                        （红基 Ran 10 … FAILED (failures=6)）

tests.test_tech_unified_workflow_projection_red \
tests.test_packaging_tech_projection_2_1_must_see_drawing_flow_red \
tests.test_packaging_integration_stage_must_count_its_own_analysis_red \
tests.test_packaging_cost_stage_must_see_the_parsed_parts_red \
tests.test_tech_empty_ir_parse_completion_red \
tests.test_tech_home_timeline_and_publish_closure_red \
tests.test_tech_project_acl_scope_red tests.test_packaging_parts_downstream_red \
tests.test_packaging_drawing_flow_red tests.test_spec_status_truth_red
    # Ran 214 in 80.555s … FAILED (failures=1, skipped=1)
    #   唯一那条是**既有挂账**、与本批无关：`tech_unified_workflow_projection_red`
    #   `RequirementCompletionTest.test_confirmed_requirement_completes_confirm_and_opens_review`
    #   —— 它按"1.3 排在 2.1 之前"的老行序断言 `actionable=true`，与
    #   `packaging-stage-order-equals-dependency.md` §2.1 的行序冲突（`changelog ## 455`、本节 §1.3）。
    #   改前改后都是同一条（已逐条比对）。
```

### 6.2 一处既有测试的重指（如实记录；**没有放宽断言**）

`tests/test_packaging_tech_projection_2_1_must_see_drawing_flow_red.py` 的 B6 原来断言
`rows_none["1.2"]["blocked_reasons"]` 含「请先完成 2.1 图纸解析」。那份 fixture 的需求是
`pending_review`，`1.2` 因此是 `confirmed / completed=true` —— 它挂着这句**正是**本批要修掉的
那处自相矛盾（旧实现不分完成与否一律追加；`## 447` 写 B6 时只是"碰巧"绿）。本批把断言重指到同一份
`rows_none` 里真正未完成的 `1.3`，并顺手钉住"`1.2` 已完成"这个前提：

- 断言强度不变：仍是"2.1 没完成时，前置文案逐字是 `请先完成 2.1 图纸解析`"；
- 该文件其余 11 条（A1–A5、B1–B5、B7）一个字没动，仍全绿；
- 若不重指，本批与 `## 447` 的这条守卫直接冲突（本 Spec §2.1 要求"任何 `completed=true` 的行不得挂
  前置文本"）。

### 6.3 红测自身缺陷（如实记录）

- 本批红测钉的是"完成与否 × 前置文案"与"1.2 / 1.3 在四种需求状态下的 `status` + `missing` 非空"，
  **没有**锁 `missing` 的自由文案（本 Spec §2.2 明说只要求非空可读），也没有钉 `_next_action()` 在
  `draft` 下的具体指向 —— 这两条留给人工复核。
- 本 Spec 头部写的 changelog 条目号是 `## 461`，与同批落地的
  `packaging-business-parts-outline-bbox-broken-link.md`（也是 `## 461`）**撞号**（那份 Spec §7 已记录
  同一撞号）。本批按自己的号写 `## 461`。

未 push / MR / tag / Release / 部署，未起服务、未发 HTTP、未连 PG / 34、未写业务数据。

### 6.4 人工复核（红测覆盖缺口之外，2026-09-23）

§6.3 记的两条缺口（`missing` 的自由文案、`draft` 下 `_next_action()` 的指向）逐条复核。同一支判据
（`wp._rows_for` / `wp._judge` / `wp._next_action`，包装项目 + 解析产物在场）扫五种需求状态：

```text
(无需求)              next=1.1  1.1 not_started  1.2 not_started+missing  1.3 not_started(无 missing)  2.1 generated
draft                 next=1.1  1.1 in_progress  1.2 not_started+missing  1.3 not_started+missing
pending_confirmation  next=1.2  1.1 confirmed    1.2 awaiting_confirmation 1.3 not_started+missing
pending_review        next=1.3  1.2 confirmed    1.3 in_review+missing
approved              next=3.1  1.3 approved
通用不变量：completed=true 的行挂前置文本 → 无（§2.1 成立）
```

- 与 §2.2 表逐格对上；`missing` 文案读得出"需求单当前是「…」"；
- `_next_action()` 不再指回 2.1，而是顺着 1.1 → 1.2 → 1.3 → 3.1 往前走（规则本身没改，只是行不再谎报）。
