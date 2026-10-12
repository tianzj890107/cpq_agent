# Spec：2.1 解析出的「待确认」字段必须有采纳通道，采纳后盒型匹配才看得到

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_candidate_fields_adoption_red.py`（13 条，已全绿）
血缘：`docs/specs/packaging-manual-field-confirmation.md`（人工录入/确认的字段能让门禁转绿 —— 本批补的是它的**另一半**：图纸候选怎么变成"人工确认"）、
`docs/specs/packaging-product-outline-and-die-layer-roles.md`（产品级圆 → `inner_length = inner_width =` 圆直径）、
`docs/specs/packaging-box-type-matching.md`（盒型匹配的 7 个输入键）、
`docs/specs/packaging-drawing-semantics.md`（§5 字段来源与看板）。
证据来源：34 线上全流程复跑（2026-10-12），项目 `9ab4fe6ce416`（圆盘盒-方案1-上半.dwg）。

## 0. 一句话目标

图纸解析把成品尺寸算出来了（`needs_confirmation`），但**没有任何人能把它确认进需求单**，
下游又只读需求单 —— 于是"解析成功"和"缺内长"同时成立。本批只补这一条采纳通道，
以及它的三条守卫（不许自动采纳、不许覆盖人工值、单位未确认不许确认绝对尺寸）。

## 1. 现状缺口（34 实测，可复现）

同一趟一键解析（`run_id=flow-960f9ae3e7f8ef23`，`status=completed`，八步全绿）：

| 步骤 / 读数 | 实测 |
| --- | --- |
| `packaging_semantics` | `semantics_hash=42bc0064…`；`box_type = round_tube`（`needs_confirmation`, `confidence=0.4`）；**`inner_length = inner_width = 396.6000000000058`**（`status=needs_confirmation`、`board=pending`、`confidence=0.8`、`evidence_refs=1`，落在产品级圆直径带内） |
| `field_write.detail` | `written = []`；`pending = [box_type, inner_length, inner_width, insert_type]`；`missing = [box_family, carton_size, closure_type, …, inner_height, …, v_groove]`（18 项） |
| `inheritance.unresolved_gaps` | 23 条（`inner_height` = "内高尚未就绪" 等） |
| `downstream.box_match.blocking` | 4 条 `field_missing`，`source=requirement`：内长 / 内宽 / 内高 / 闭合方式 |
| `POST /requirement/box-match/decision` | **409** `box_type_not_confirmable：YT-DWG-ROUND-10PC（needs_input）`（唯一候选 `total_score=0`、`can_confirm=false`） |
| `POST packaging-bom` / `packaging-route` / `packaging-cost` | **409** `box_type_not_confirmed`（Spec §2.1 / §2.13） |
| `POST /integration/send-to-finance` | **400**「请先完成参数推荐：财务要按整机 BOM 与参数核算成本」 |

对照实验（同一批复跑）：项目 `6beeeebb19a1`（酒盒）能一路走到 2.3，是因为需求文本里**人又写了一遍**
「成品内尺寸220.5×90×90mm；面纸225g；EVA内托；V槽是」（`field_sources=user_text`）
——**不是图纸读出来的**。圆盘盒的需求文本只写"实际尺寸、材料和零件从…dwg读取"，于是整条链断在这里。

根因（两处，缺一不可）：

1. `packaging_drawing_flow/steps.py:668` 的 `_manual_accept_fields()` 只把
   `field_sources[key] == "manual"` 且当前有值的字段交给确认通道；图纸候选的
   `field_sources` 从来不是 `manual`，`accept` 因此恒为那些人工字段（本例为空）→ 候选不落库。
2. `packaging_semantics.apply_to_requirement(accept=...)` 本身**已经**支持"按字段确认"
   （`provenance.py:170-186`：`key in accepted` → `origin=user_confirmed`、`status=confirmed`、
   `data[key]=候选值`、`field_sources[key]=manual`），但全仓库**没有任何路由或界面**能把
   "采纳这批候选"送进来（`main.py` 里 `packaging_semantics` 只有只读调用点）。

## 2. 契约

### 2.1 C1：采纳入口 `adopt_requirement_candidates(project_id, keys, *, actor, note="", semantics=None)`

`tech_app/backend/services/packaging_semantics/__init__.py` 导出（写盘仍只经 `provenance`）：

```
def adopt_requirement_candidates(project_id, keys, *, actor, note="", semantics=None) -> dict
```

- `semantics` 为 `None` 时读当前项目语义文档（`load_semantics`）；测试/路由可显式传入。
- 只采纳语义文档里 `status in ("needs_confirmation", "confirmed")` **且 `value` 非空**的字段；
- 采纳一个字段 = 调 `provenance.apply_to_requirement(project_id, semantics, accept=<本次通过校验的键>, author=actor)`；
- 返回 `{"adopted": [...], "rejected": [{"field", "code"}], "conflicts": [{"field", "code"}], "warnings": [...]}`，
  三个列表都按 `field` 排序（可断言）。

### 2.2 C2：三条守卫（拒绝码闭集，逐字）

| 场景 | 结果 |
| --- | --- |
| 语义文档里没有这个键 | `rejected`，`code="candidate_unknown"` |
| 候选 `value` 为空 / `status=missing` | `rejected`，`code="candidate_missing"`，**不许凭空造值** |
| 候选 `status` 不在 `("needs_confirmation", "confirmed")`（例如 `conflict`） | `rejected`，`code="candidate_status_not_adoptable"`；冲突候选必须先由人裁定，不许借采纳翻成 confirmed |
| 需求单上已有人工值（`field_sources[key]=="manual"`）且与候选**不等** | `conflicts`，`code="manual_conflict"`；保留人工值，候选只作备选（`alternatives`） |
| 需求单人工值与候选**相等** | 正常 `adopted`（把来源与看板补齐为已确认），不算冲突 |
| 字段属绝对尺寸（`inner_length / inner_width / inner_height / carton_size`）而锚点单位未确认（`anchor.unit_status != "confirmed"`） | `rejected`，`code="unit_unconfirmed"`；既有口径"单位未确认时图纸绝对尺寸不许 confirmed"一个字都不许放宽 |
| 非尺寸字段（如 `box_type`、`insert_type`） | 不受单位门禁影响 |

采纳写入必须留痕：`field_provenance[key]` 记 `origin="user_confirmed"`、`status="confirmed"`、
`value=<采纳值>`、`adopted_by=<actor>`、`adopted_at=<时间>`；`field_sources[key]="manual"`
（候选此前是 `needs_confirmation` 还是 `confirmed` 都一样，采纳即人确认）；
并有审计动作（动作名里同时含 `candidate` 与 `adopt`）。

### 2.3 C3：解析本身永远不自动采纳

`packaging_drawing_flow` 的 `field_write` 步**不得**把 `needs_confirmation` 候选写进需求单：
`field_write` 的 `detail.written` 必须不含这类字段，`detail.pending` 必须如实列出它们。
（现状即如此，本批用红测把它钉死，防止"为了让下游过而偷偷写值"。）

### 2.4 C4：路由

`tech_app/backend/main.py` 新增具名常量与 POST 路由，调用同一份服务入口：

```
PACKAGING_FIELD_CANDIDATE_ADOPT_PATH = "/api/projects/{pid}/requirement/packaging-semantics/candidates/adopt"
```

- 权限与需求编辑一致：`auth.MANAGER_ROLES`（写的是需求单，不是成本、不是盒型决策）；
- 纯写需求草稿 + 审计；**不改盒型匹配记录、不自动跑下游、不确认需求单**（采纳 ≠ 需求审批）。

### 2.5 C5：采纳之后下游读得到

采纳后 `packaging_match._requirement_inputs(project_id)` 返回的输入字典里对应键**非空**
（盒型匹配读的就是需求单）。这是 C1 的唯一目的；下游任何一步的判据都不许另开一条"读语义文档"的旁路。

### 2.6 不改的东西

不得改：`MATCH_INPUT_KEYS` 这 7 个键、盒型决策的立场（`needs_input` 不许因此变 `matched`）、
`_manual_accept_fields` 的既有语义（人工字段仍然优先）、语义文档 `SEMANTICS_VERSION`、
字段看板的状态闭集、成本与费率、任何页面文案占位口径。

## 3. 验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_candidate_fields_adoption_red -v
# 目标：11 OK（当前 10 红、1 绿的反例守卫）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_manual_field_confirmation_red -v   # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_red -v                  # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_product_outline_red -v            # 不回归
```

线上复验（同一项目 `9ab4fe6ce416`，或另建一个同样只给 DWG 的项目）：

1. 解析后在 2.1 采纳 `inner_length / inner_width`（396.6）；
2. `GET /requirement` → 两键有值、`field_sources=manual`、`field_provenance.status=confirmed`；
3. `POST /requirement/box-match` → `blocking` 里不再有 `inner_length / inner_width` 的 `field_missing`；
4. 若 `closure_type / inner_height` 仍缺，`box_match` 应**如实**继续拦（不许为过门而编值）。

## 4. 禁止事项

- 不许改本批红测文件；不许把 `needs_confirmation` 直接当 `confirmed` 用。
- 不许在 `field_write` 或盒型匹配里"自动采纳"候选（C3）。
- 不许把"采纳"实现成需求单提交/审批（那是 1.2 / 1.3 的职责）。
- 不许给 `box_type_not_confirmable` 加"签字放行"绕过 —— 决策仍要 `matched && can_confirm`。

## 5. 实现及测试记录（2026-10-12）

- 实现三处：`packaging_semantics/provenance.py` 新增 `adopt_candidates()`（校验三条守卫后，
  值仍由既有 `apply_to_requirement(accept=...)` 写，再补记 `adopted_by/adopted_at` 与审计）、
  `packaging_semantics/__init__.py` 导出 `adopt_requirement_candidates()`、
  `main.py` 新增具名常量 `PACKAGING_FIELD_CANDIDATE_ADOPT_PATH` 与 POST 路由
  （`auth.MANAGER_ROLES`，只写需求草稿）。
- 单位门禁读图纸链路的 `anchor.current_anchor(project_id)['unit_status']`；**读不到一律按未确认**
  处理（保守方向，绝对尺寸不采纳）。
- `tests/test_packaging_candidate_fields_adoption_red.py` 13 条由 10 红转全绿（含 1 条反例守卫：
  解析自己仍不许自动采纳；另 2 条钉住本轮修正：冲突态候选不可采纳、已 confirmed 的候选被采纳后
  来源仍记 `manual`）；回归 `test_packaging_manual_field_confirmation_red` 13 OK、
  `test_packaging_semantics_red` 59 OK（skip 1）、`test_packaging_product_outline_red` 21 OK（skip 1）、
  `test_packaging_drawing_flow_red` 54 OK（skip 1）、`test_drawing_flow_parse_terminal_signal_red` 30 OK。
- 红测里 C2 那条原本按「常量出现处的上下文里要有 `@app.post`」判，实现后常量定义在装饰器之前，
  判定改成更严的 `@app.post(PACKAGING_FIELD_CANDIDATE_ADOPT_PATH)` 字面量（仍只认这一种写法）。
