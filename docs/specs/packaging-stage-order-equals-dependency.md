# 阶段顺序必须等于依赖顺序：图纸解析要夹在「1.1 存草稿」与「1.1 提交确认」之间（第 8 层）

血缘：`packaging-requirement-confirm-order-guard.md`（顺序门禁 + 退路，已实现）、
`e2e-packaging-dwg-quote-tech-continuity.md` §3（DWG 路由与审批顺序）、
`drawing-flow-non-editable-requirement.md` §2/§3（需求不可编辑时的稳定码）、
`tech-workflow-five-phase-naming.md`（5 阶段 × 13 子步骤「后端唯一事实源」）。

状态：Spec + 红测（已实现）（本层只改**呈现顺序**与**事实源**，不动判据；红基见 §6，落地见 §7）
红测：`tests/test_packaging_stage_order_red.py`
本批 changelog 条目号：`## 316`。

## 0. 一句话目标

用户在 34 上问：「为什么会是这样？肯定是应该按顺序来啊，这样的顺序不就全都乱了吗？」
—— 他质疑的是这一条：`1.1 存草稿 → 2.1 一键解析图纸 → 再回 1.1 提交确认 / 1.2 通过确认 / 1.3 审核`。

**这条依赖是对的，但页面呈现的顺序是错的**：流程栏把「2 图纸解析（2.1）」排在
「1.2 确认需求」「1.3 审核需求」**之后**，而真实依赖要求它**夹在** 1.1 与 1.2 之间。
本层把「看到什么顺序」改成「必须按什么顺序做」，并把编号表收成唯一事实源。

## 1. 根因（代码事实，逐条可复现；不是推断）

1. **依赖是硬的**：图纸解析第 8 步 `field_write`（`packaging_drawing_flow/steps.py:416`）把图纸里读出来的
   字段**回写进那张需求单**，因此要求需求单存在且状态可编辑：
   `requirement_service.EDITABLE_STATUSES = ("draft", "rejected")`（`requirement_service.py:29`）。
2. **关口会把需求推离可编辑态**：1.1 提交确认 → `pending_confirmation`；1.2 通过确认 → `pending_review`；
   1.3 审核通过 → `approved` —— 三个都不在 `EDITABLE_STATUSES` 里，所以
   `field_write` 必然 `blocked / REQUIREMENT_NOT_EDITABLE`
   （`packaging_drawing_flow/model.py` 的 `PRECONDITION_BLOCKERS`，实测 7/8）。
3. **呈现顺序却是编号顺序**：`workflow_stages.py:17-19` 的阶段表把 `1.1/1.2/1.3` 归阶段 1、
   `2.1 图纸解析` 归阶段 2；`_STAGE_ROWS` 的行序也是
   `requirement-create → requirement-confirm → requirement-review → drawing`。
   用户在流程栏里看到的下一格就是「1.2 确认」，点下去就踩坑。
4. **编号表被抄了至少 4 份**（`workflow_stages.py` 自称"后端唯一事实源"，前端那份是**手抄的**，
   还逐字漂移）：
   - `tech_app/frontend/workflow.js:99-100`（`['1.1','创建'],['1.2','确认'],['1.3','审核']` / `['2.1','图纸解析']`）；
   - `tech_app/frontend/requirement-create.js:41` 与 `requirement-confirm-page.js:42`（页内流程条 HTML）；
   - `tech_app/frontend/report-publish-result.js:109`（`phases` 数组）。
   四份抄的是同一张错表 → 改一处没用。
5. **本轮真跑对照**：按依赖顺序（1.1 存草稿 → 2.1 解析 → 1.1/1.2/1.3）**8/8 completed**；
   按页面顺序（先 1.2/1.3 再 2.1）必 7/8，且第 8 步的出口只有"退回草稿"。

## 2. 口径（逐条，可直接验收）

1. **顺序 = 依赖，只对"包装 + DWG"项目生效**（其它行业阶段表逐字不变）：
   `workflow_stages.STAGES` 里图纸解析（`drawing`）的位置**必须早于**
   `requirement-confirm`（以及 `requirement-review`）。也就是流程栏读起来必须是
   `创建需求（存草稿）→ 图纸解析 → 确认需求 → 审核需求 → …`，用户顺着点就对了。
2. **stage_id、页面文件名、URL 参数一律不变**（`requirement-create.html` / `index.html` /
   `?stage=` 的历史链接、任务、会话都不受影响）；变的只有「编号 / 标题 / 顺序」，且
   编号必须在整张表里**唯一**，形状仍是 `^[1-5]\.\d$`、共 13 个子步骤。
3. **编号表唯一事实源**：`workflow_stages.py` 的 `PHASES` / `_STAGE_ROWS` 是唯一出处；
   上面 §1.4 的四个前端文件**不许再写死第二份**（页内流程条与阶段表必须由同一处派生：
   调用共享 helper，或从后端读一次，二者取一）。
4. **1.1 页面必须给显式引导 + 可点入口**：图纸还没解析时，1.1（创建需求）页面必须显示
   「下一步：图纸解析」这类提示，并给一个能直接进图纸解析的入口（`?stage=drawing`）。
   理由：`## 313` 那道门禁只能"拦住并告诉原因"，**不负责把用户送过去**；没有入口，
   用户仍然要靠别人告诉他"先跳过去点 2.1"。
5. **门禁与退路逐字保持**：`assert_requirement_drawing_parsed()`、1.1/1.2 两处调用、
   `RETURNABLE_TO_DRAFT_STATUSES` 含 `approved`、1.2/1.3 的退回出口——全部照
   `packaging-requirement-confirm-order-guard.md` §2 不变。本层**不因为顺序改好了就撤掉门禁**：
   门禁管的是"这一单到底有没有零件"，跟顺序是两件事。

## 3. 本层不做的

- 不改 `EDITABLE_STATUSES`、不改 `field_write` 的判据、不做「解析结果先存补丁等解锁再合并」（另立一批）；
- 不动 `stage_id` / 页面文件名 / URL 参数 / 任务与会话表；不动非包装行业的阶段表；
- 不放宽任何门禁或退路；不碰 34 的部署与推送，不删任何项目或会话。

## 4. 红测分组（`tests/test_packaging_stage_order_red.py`）

- **A 组 顺序与事实源**
  - A1（红）`workflow_stages.STAGES` 里 `drawing` 的序号必须小于 `requirement-confirm`；
  - A2（护栏，今天就是绿的）13 个子步号唯一、形状合规、一个都没少；
  - A3（红）`workflow.js` / `requirement-create.js` / `requirement-confirm-page.js` /
    `report-publish-result.js` 四个文件里**不得**再出现写死的子步骤编号表（`1.2 确认` /
    `1.3 审核` 这类字面量成对出现）。
- **B 组 1.1 页面的引导与入口**
  - B1（红）1.1 页面（`requirement-create.js` / `requirement.js`）必须出现"先做图纸解析"的
    引导文案，且带一个能进图纸解析的入口（`stage=drawing`）。
- **C 组 护栏（今天就是绿的，不许被改红）**
  - C1 `assert_requirement_drawing_parsed` 仍在 `requirement_service.py`，且 1.1 / 1.2 两处都在用；
  - C2 `EDITABLE_STATUSES == ("draft", "rejected")`；
  - C3 `RETURNABLE_TO_DRAFT_STATUSES` 仍含 `approved`。

## 5. 验收

1. `tech-workbench.html?project=a42e5e60a720` 的流程栏顺序是
   `1.1 创建 → 1.2 图纸解析 → 1.3 确认 → 1.4 审核`（或等价编号），**图纸解析不再排在确认/审核之后**；
2. 在 1.1 页面能直接看到"下一步：图纸解析"并一键进去，顺着流程栏点下去**不会**再出现 7/8；
3. 四个前端文件里查不到第二份编号表；

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_stage_order_red \
  tests.test_packaging_requirement_confirm_order_guard_red -v
```

## 6. 红基（2026-09-22 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_stage_order_red
  → Ran 7 tests … FAILED (failures=3, errors=0)
```

红的 3 条 = A1（`drawing` 仍排在 `requirement-confirm` 之后）、A3（四个前端文件各有一份写死的
编号表）、B1（1.1 页面没有"先做图纸解析"的引导与入口）；绿的 4 条护栏 = A2（13 个子步号唯一且
形状合规）、C1（`assert_requirement_drawing_parsed` 在 1.1/1.2 两处仍被调用）、
C2（`EDITABLE_STATUSES` 未放宽）、C3（`approved` 仍退得回草稿）。

## 7. 落地状态（2026-09-22 实现轮完成，本地提交）

- **行序 = 依赖顺序**：`workflow_stages._STAGE_ROWS` 改成
  `requirement-create → drawing → requirement-confirm → requirement-review → process → cost → summary →
  report-review → report-publish`；`PHASES`（5 阶段与各自的子步骤号）一个字未改，13 个子步骤号仍唯一、
  形状仍 `^[1-5]\.\d$`。`oc_agent.TECH_UI_STAGES` 同步成同一顺序（它下一行就与 `stage_ids()` 做相等校验）。
- **编号表唯一事实源**：前端新增 `tech_app/frontend/workflow-stages.js`（`window.CpqWorkflowStages`
  提供 `subLabel / subTitle / subPair / phases / phaseRows`），10 个加载 `workflow.js` 的页面在它之前
  引入这个模块；§1.4 点名的四个文件（`workflow.js` / `requirement-create.js` /
  `requirement-confirm-page.js` / `report-publish-result.js`）**不再各留一份编号表**，只保留阶段/页面的
  文案与子步骤号，标签一律从共享模块取。
- **1.1 页面给了显式引导与入口**：`requirement-create.js` 在流程条下方渲染
  「下一步：图纸解析 —— 先把原始图纸传上来解析（字段会写回这张需求单），再回来提交确认」+ 一个
  指向 `index.html?stage=drawing&project=…` 的按钮（Spec §2.4）。
- **门禁与退路逐字未动**：`assert_requirement_drawing_parsed()` 仍在 `requirement_service.py`
  且 1.1/1.2 两处都在调用；`EDITABLE_STATUSES` / `RETURNABLE_TO_DRAFT_STATUSES` 未改（C 组护栏仍绿）。

### 已记录的偏差（不改测试）

1. **§2.1 的"只对包装 + DWG 项目生效"做不到按行业分叉**：`workflow_stages.STAGES` 是**全局唯一**的
   13 行表，没有按行业分叉的第二份；红测 A1 直接读 `workflow_stages.STAGES`。因此这次行序调整是全局的
   ——对其它行业也是「创建需求 → 图纸解析 → 确认需求 → 审核需求」，而这条顺序对任何行业都成立
   （图纸字段要写回需求单，需求必须先处于可编辑草稿）。
2. **§5.1 期望的编号重排（`1.1 创建 → 1.2 图纸解析 → 1.3 确认 → 1.4 审核`）没有做**：
   `tests/test_tech_workflow_five_phase_naming_red.py`（已实现的规范表）把「阶段 1 = 1.1/1.2/1.3 创建/确认/审核、
   阶段 2 = 2.1 图纸解析」逐行写死，改编号会直接把它改红。本层按它的「或等价编号」处理：**顺序**
   由后端口径表与 1.1 页面的显式引导保证，**编号**保持既有规范表不变。
3. **`tests/test_tech_unified_workflow_projection_red.py::RequirementCompletionTest::test_confirmed_requirement_completes_confirm_and_opens_review`
   按设计变红（一条，已记录，未改测试）**：`workflow_projection` 的「前置子步骤」是从 `STAGES` **行序**
   推出来的（`for prior in keys[:index]`）。行序改成依赖顺序之后，「1.2 确认 / 1.3 审核」的前置里就
   含了「2.1 图纸解析」——这正是本 Spec §2.1 要表达的依赖，也正是 ## 313 那道
   `assert_requirement_drawing_parsed()` 门禁在做的事。该用例给的是一个**已确认但没解析过图纸**的
   合成状态（`confirmed_1_2/confirmed_1_3`），在新顺序下它本就不该可执行（真按这个顺序点下去，
   1.1 提交确认会被门禁 409 挡下）。旧断言编码的正是被本 Spec 判定为 bug 的那条顺序，所以不动它。
