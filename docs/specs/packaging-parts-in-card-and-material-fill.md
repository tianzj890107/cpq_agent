# 卡片上看得见拆出来的零件 + 缺材料的件补得进去（第 7 层）

血缘：`packaging-parts-list-visibility-and-kinds.md`（零件文档读得全）、
`packaging-part-role-manual-mapping.md`（角色人工映射"看得见 + 做得动"）、
`packaging-parts-thickness-facts.md` §2.5（**料厚**的人工补录范式）、
`packaging-parts-coverage-truthfulness.md` §2.2（缺口原因账）、
`e2e-packaging-dwg-quote-tech-continuity.md` §4（技术看板里 64 件必须渲染出来）、
`packaging-downstream-block-code-parity.md`（下游阻断要能判分支）。

状态：Spec + 红测（已实现）（卡片第 6 步渲染零件表、件级「补材料」入口已由并行实现轮落进工作区
——**未提交**，见 §7 复核 `Ran 13 tests … OK`；§6 里那份 `FAILED (failures=10)` 保留为写 Spec 当时的历史记录）
红测：`tests/test_packaging_parts_in_card_and_material_fill_red.py`
本批 changelog 条目号：`## 315`。

## 0. 一句话目标

零件拆出来之后有两个洞，合起来让"看见零件 → 把它做下去"这条链在**报价卡片**上断掉：

1. **看不见**：卡片第 6 步快照里即使有零件表，报价卡片页也渲染不出来（`FORMS` 目录里没有
   第 6 步任何一节，恢复逻辑对未知节**直接 `return`**），零件只活在技术看板的 2.1 面板里；
2. **补不进去**：64 件里 51 件卡在 `PACKAGING_PART_MATERIAL_UNKNOWN`，而**材料**没有任何人工
   补录入口（只有料厚有），唯一出路是"回需求里补全再整体重跑八步"；`processability()` 的
   文案也正是这么写的。

本层补这两件事：**卡片看得见零件（含可算性/不可算原因）** + **材料也能人工补录，补完只重算这一件**。

## 1. 现场证据（34，2026-09-22，Codex 只点按钮真跑）

`SM1` 报价会话 `c0239386c1c4` → `PE1` 技术项目 `a42e5e60a720`（`酒盒.dwg`，`entry_origin=quote`、
`business_case.source_session_id = c0239386c1c4`）→ 需求单 `REQ-A42E5E60A720` → 卡片
`3991598492811269436`（6 步全 `done`）。

| 事实 | 值 | 读法 |
| --- | --- | --- |
| 零件件数 | 64 | `GET /api/projects/a42e5e60a720/requirement/packaging-parts` |
| 可算件数 | **9 / 64**（`processable_ratio=0.141`） | 同上 `summary` |
| 不可算原因账 | `{"PACKAGING_PART_MATERIAL_UNKNOWN": 51, "PACKAGING_PART_NOT_CLOSED": 4}` | 同上 |
| 缺材料原因账 | `{"no_material_note": 39, "no_closed_outline": 4}` | 同上 `material_gap_mix` |
| 缺料厚原因账 | `{"material_missing": 39, "grammage_only": 11, "no_thickness_note": 1, "no_closed_outline": 4}` | 同上 `thickness_gap_mix` |
| 卡片第 6 步快照键 | `['packaging_parts', 's6_quote', 's6_quote_markdown']`，`packaging_parts.数据` **64 行** | `GET /wf/card/step-data?session_id=c0239386c1c4&step_no=6` |
| 报价侧固定表单目录 | `forms` **17 节**，`step` 只到 **5**；**没有** `packaging_parts`，**没有**第 6 步任何一节 | `GET /agents/quote/api/meta?industry=packaging` |
| 卡片页读过技术项目接口吗 | `grep -c "api/projects" 确认需求解析结果.html` → **0** | 源码 |

代码级事实（本机 HEAD，逐条可复现）：

- `确认需求解析结果.html` 的 `wfRestoreStepData()`：`const f = FORMS.find(x => x.section_id === sid);
  if (!f) return;` —— 快照里出现一个 `FORMS` 不认识的 section（例如 `packaging_parts`），
  **无声丢弃**，页面上什么都不渲染；
- 同一函数对**已知**节的处理是：`Array.isArray(data)` → `renderTableSection(...)`，
  即"表格渲染"这条路已经存在，只是被 `FORMS` 目录挡住了；
- `packaging_parts.processability()`（`packaging_parts.py:1951`）的缺料分支文案逐字是
  `这一件缺材料/厚度：material（请在需求里补全后重跑解析）` —— 对**只缺材料**的 39 件来说，
  这句话把用户指向唯一出路"整体重跑八步"，而那条路本身还有顺序陷阱
  （`packaging-requirement-confirm-order-guard.md`）；
- `grep -rn "packaging-parts/{part_code}" tech_app/backend/main.py` 只有 `thickness` 一条写路由，
  `material` 写路由 **0 个**；`packaging_parts.py` 有 `set_manual_thickness` / `save_part_thickness` /
  `load_part_thickness`，**没有** `set_manual_material` / `save_part_material` / `load_part_material`；
- `summarize()` 有 `thickness_manual_total`，**没有** `material_manual_total`
  （于是"材料是人工补的"在汇总里不可见，也无法与图纸证据分开数）。

## 2. 口径（可直接验收）

### 2.1 卡片第 6 步必须渲染零件表（**不靠脚本注入**）

1. 报价卡片页（`确认需求解析结果.html`）在渲染第 6 步时，遇到快照里
   `{kind: "table", title, 数据: [...], summary: {...}}` 形状的**未知** section，必须走
   `renderTableSection()` 渲染出来，**不许**因为 `FORMS.find()` 取不到就 `return`；
   `FORMS` 里**已知**的 section 行为逐字不变。
2. 第 6 步「图纸拆出来的零件」这张表的列必须**至少**含：
   `零件号`、`名称`、`角色`、`材料`、`厚度(mm)`、`展开长(mm)`、`展开宽(mm)`、`轮廓状态`、
   `可算`、`不可算原因`；行数 = 零件文档的 `part_total`（不许按页大小截断）。
3. `可算` / `不可算原因` **必须**与 `packaging_parts.processability()` 同判据同文案
   （前端不许自己写第二套"能不能算"的规则，也不许把 `unprocessable_reason_mix` 的稳定码
   翻译成另一套词）。
4. 卡片页必须能**按会话号自己找到技术项目**并读零件端点：
   `GET /api/projects?scope=all` 里按 `business_case.source_session_id === currentSessionId`
   命中 `project_id`，再读 `GET /api/projects/{project_id}/requirement/packaging-parts`；
   命中不到（卡片还没有技术项目）时表区显示"还没有图纸零件"，**不许**报错、不许阻塞其它步骤。

### 2.2 材料也要能人工补录（料厚那套的镜像）

1. **纯函数** `packaging_parts.set_manual_material(row, spec, *, bound_by, reason="")`
   （与 `set_manual_thickness` 同范式）：
   - 返回**副本**，绝不原地改入参；
   - 写 `material = {"spec": <spec>, "grade": "", "material_code": ""}`（保持行的
     `material` 字典形状，`_material_spec()` 读得到）与
     `material_source = {"kind": "manual", "text": <reason>, "bound_by": <who>, "evidence_ref": "",
     "distance_mm": None}`；
   - 清掉该件 `attribution` 里同字段的待办（`material_unresolved` 一类），`attribution.kind`
     为空时补成 `manual`（与料厚逐字同形）；
   - `spec` 去空格后为空 → `ValueError`（**不许**用空串表示"没填"）。
2. **落库** `save_part_material(project_id, part_code, spec, *, bound_by, reason="")` +
   `load_part_material(project_id, part_code)`，走独立文档通道（`DOC_KEY_MATERIAL`），
   同一 `(part_code, spec, 人, 理由)` 幂等（不新增版本、不重复写审计），与料厚同口径。
3. **路由**（与料厚逐字同形状）：
   - `GET /api/projects/{pid}/requirement/packaging-parts/{part_code}/material`（纯读，
     没补过回 `manual: false`，**不 404**）；
   - `POST` 同路径，权限直接引用 `packaging_match.BOX_MATCH_DECIDE_ROLES`（不另抄一份角色清单）；
     非法输入 → **400** + `PACKAGING_PART_MATERIAL_INVALID`；件不存在 → 404；
     成功 → 落零件行副本 + 人工材料文档 + 审计 `workflow:packaging_part_material_bound`。
4. **补完不用重跑八步**：补过的行喂 `processability()`，`missing_variables` 里**不再含**
   `material`（只缺料厚时 `ok` 仍为 `False` 且只报 `thickness_mm`；材料 + 料厚都补齐 → `ok=True`）。
5. **汇总要分开数**：`summarize()` 新增 `material_manual_total`（口径与 `thickness_manual_total`
   逐字一致：`material_source.kind == "manual"` 的件数）；`material_known_total` 把人工补的算进去，
   `material_evidence_ratio` **不许**被人工值抬高（人工 ≠ 图纸证据）。

### 2.3 卡住的时候要说清"怎么补"

1. `processability()` 的 `PACKAGING_PART_MATERIAL_UNKNOWN` 文案必须：
   - 逐条列出 `missing_variables`（已有，保持）；
   - **按缺什么分别给可执行下一步**：缺材料 → 指向件级「补材料」；缺料厚 → 指向件级「补料厚」；
     两者都缺 → 两条都给。**不许**只写"请在需求里补全后重跑解析"；
   - 稳定码 `PACKAGING_PART_MATERIAL_UNKNOWN` **一个字不许改**（下游按码判分支）。
2. 前端 `tech_app/frontend/app.js` 零件行：材料为空的行必须和 `part-thickness-fix` 一样长出
   **补材料** 控件（同一个渲染循环里），点它走 `POST .../material`，成功后就地刷新该行与计数。

## 3. 本层不做的

- 不改 `reject_unknown_role_autobind()`，不借"补材料"自动贴业务角色；
- 不放宽 `PACKAGING_PART_NOT_CLOSED`（未闭合件仍然 409，不许拿包围盒硬排工艺）；
- 不改既有 17 节固定表单的 id / step / 列（只允许**新增**第 6 步的只读节，或纯前端通用渲染）；
- 不改 `SET`/成本公式/费率/权重，不连 PG，不写生产数据，不碰 34 的部署与推送；
- 不做"批量补材料/批量补料厚"（本轮只做逐件；批量另立一批）。

## 4. 红测分组（`tests/test_packaging_parts_in_card_and_material_fill_red.py`）

- **A 组 卡片可见性（3 红 + 1 护栏）**
  A1 未知 `kind=table` 节不许被 `FORMS.find()` 静默丢（`wfRestoreStepData` 的早退分支必须
  先判 `kind === 'table'`）；A2 卡片页有按 `source_session_id` 反查项目 + 读零件端点；
  A3 列清单含 `角色` / `可算` / `不可算原因`；A4 护栏：既有 17 节固定表单 id 一个不少。
- **B 组 材料人工补录（5 红）**
  B1 `set_manual_material` 纯函数存在且副本语义（原行不被改）、空值 `ValueError`；
  B2 `save_part_material` / `load_part_material` / `DOC_KEY_MATERIAL` 存在且幂等；
  B3 路由 `GET`/`POST` 两条都在，POST 走 `BOX_MATCH_DECIDE_ROLES`、400 码
  `PACKAGING_PART_MATERIAL_INVALID`、审计 `workflow:packaging_part_material_bound`；
  B4 行为：补过材料 + 料厚的行 `processability()` 转 `ok=True`；只补材料仍只报 `thickness_mm`；
  B5 `summarize()` 新增 `material_manual_total`，且人工值不抬高 `material_evidence_ratio`。
- **C 组 卡住文案与入口（2 红）**
  C1 `PACKAGING_PART_MATERIAL_UNKNOWN` 文案含"补材料 / 补料厚"这类可执行下一步，且不再只指向
  "重跑解析"；C2 `app.js` 材料为空的行有 `part-material-fix` 同范式控件。
- **D 组 护栏（2 绿）**
  D1 未闭合件仍 `PACKAGING_PART_NOT_CLOSED`；D2 `reject_unknown_role_autobind()` 仍在。

## 5. 验收

按用户路径（只点前端按钮）：

1. 技术看板 `tech-workbench.html?project=a42e5e60a720&stage=drawing` 左栏看到 **64 件**；
2. 报价卡片 `确认需求解析结果.html?session_id=c0239386c1c4` 第 6 步**直接**看到同一张 64 行的
   零件表（含 `角色`/`可算`/`不可算原因`），不依赖任何脚本注入；
3. 对只缺材料的件点「补材料」，再跑这件下游 → `200 succeeded`；不必回需求、不必重跑八步；
4. 对未闭合件（`DWG-P01`/`DWG-P02`）仍 **409 `PACKAGING_PART_NOT_CLOSED`**，文案不变。

## 6. 红基（2026-09-22 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_in_card_and_material_fill_red
  → Ran 13 tests … FAILED (failures=10)
```

红的 10 条正是待实现项：`A1`（`wfRestoreStepData` 仍会静默丢掉未知 `kind=table` 节）、
`A2`（卡片页 `api/projects` 0 命中）、`A3`（缺 `角色` / `可算` / `不可算原因` 这些列）、
`B1`–`B5`（`set_manual_material` / `save_part_material` / `load_part_material` /
`DOC_KEY_MATERIAL` / 路由常量 / `material_manual_total` 全都不存在）、
`C1`（文案只有"请在需求里补全后重跑解析"）、`C2`（`part-material-fix` 不存在）。
绿的 3 条是护栏：`A4`（既有 17 节固定表单 id 一个不少）、`D1`（未闭合件仍
`PACKAGING_PART_NOT_CLOSED`）、`D2`（`reject_unknown_role_autobind` 仍在）。

## 7. 落地状态（2026-09-22 实现轮完成并本地提交）

实现已落盘并**本地提交**（分支 `ytbz`，未 push / 未部署）：`tech_app/backend/main.py`（`packaging-parts/{part_code}/material`
读写路由等，+64）、`tech_app/backend/services/packaging_parts.py`（`set_manual_material` /
`save_part_material` / `load_part_material` / `material_manual_total` 等，+126）、
`tech_app/frontend/app.js`（+55）、`确认需求解析结果.html`（第 6 步零件表接入 `api/projects`，+107）。

复核实跑（本次，工作区含上述实现）：

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_in_card_and_material_fill_red
  → Ran 13 tests … OK
```

即 §6 的 10 条红测已全部转绿、3 条护栏未被动过。**本 Spec 的红基一行不改**——它是写 spec 当时的
历史记录；上面这份复核只说明缺口已被后续实现轮收口。验收仍按 §5 在 34 上人工走一遍。

本批落地后的回归（同一命令，`./open-claude/.venv/bin/python -m unittest`）：

```
tests.test_packaging_parts_*（17 份）                → Ran 297 … FAILED (failures=1)
    唯一一条红是既有 `test_packaging_parts_outline_red::DDegrade::test_d1`（## 266，与本批无关）
tests.test_packaging_parametric_bom_red     tests.test_packaging_drawing_flow_red     tests.test_quote_tech_unified_tool_list_conversation_red     tests.test_packaging_semantics_red                → Ran 205 … 仅既有 ## 262 的 c8 一条红
tests.test_drawing_flow_frontend_wiring_red     tests.test_drawing_board_two_column_parts_and_3d_red     tests.test_packaging_parts_downstream_gate_red     tests.test_packaging_parts_downstream_readback_red → Ran 56 … OK
tech_app/frontend/app.js + 两个根页面内联脚本            → node --check 全过
```

实现与 Spec 的两处**有意偏差**（都已核对，不改 Spec 口径）：

1. §2.1 第 3 条要求「卡片 `可算`/`不可算原因` 与 `processability()` 同判据同文案」。实现把这条
   判据做成**后端**的：读接口随响应下发 `part_columns`（列定义唯一来源 `packaging_parts.CARD_COLUMNS`）
   与逐行 `processable_text` / `unprocessable_reason`（取值即 `processability()` 的 `ok` 与 `message`）。
   卡片页只渲染后端给的字段 —— 这样前端**确实没有**第二套"能不能算"的规则；页面里那份
   `PACKAGING_PART_COLUMNS` 只是后端未下发时的兜底显示标签。
2. §2.2 第 3 条写「成功 → 落零件行副本 + 人工材料文档 + 审计」。实现与既有的「补料厚」**逐字同形**：
   行副本只随响应返回并就地刷新前端（不写回零件文档，保持 `parts_id` 不变），落库的是独立文档
   `packaging_part_material`，另写 `workflow:packaging_part_material_bound` 审计。这与料厚那条
   已验收的语义一致，也是 Spec §2.2 第 2 条「独立文档通道」的要求。
