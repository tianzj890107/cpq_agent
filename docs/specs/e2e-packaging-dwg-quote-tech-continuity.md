# 包装精准报价 → DWG → 零件/BOM 连续性 Spec

状态：**已实现（2026-09-22；红测 `tests/test_e2e_packaging_dwg_continuity_red.py` 10 条全绿）**

## 1. 线上证据

项目 `a786afcf996e` 上传真实 `酒盒.dwg` 后，ODA 成功转换 AC1027→AC1032，得到 6711 个实体、
64 个零件、127 条文字和 316 个尺寸；零件 API 可返回 `DWG-P01` 等真实尺寸，界面却只显示
“零件清单”标题。报价描述已有完整包装参数，一键解析需求仍因“无附件”把字段写成待确认。
需求先审批后再解析 DWG 时，字段回写又被不可变门禁拦截。盒型匹配还因“磁吸”与
“双开门磁吸”枚举不相等，淘汰权威案例 `YT-DWG-WINE-700ML`，反而选中通用案例。

## 2. 行业与来源

1. 报价创建技术任务时必须保存 `entry_origin=quote`、报价文本快照、行业、业务实例和任务关系；
   不得写成 `internal_test`。
2. 包装项目的产品匹配只能读取包装知识库，不能查询/展示锂电产品。
3. 需求抽取输入为“报价文本 + 附件解析 + 用户补充”的合并证据；没有附件不等于没有需求。

## 3. DWG 路由与审批顺序

- `.dwg` 必须确定性进入 drawing-flow，不得先送通用视觉 `/parse` 再报错。
- 解析任务重试幂等，服务重启后仍能轮询原任务；失败需给出阶段、日志与可重试入口。
- 审批前要求完成图纸解析；若审批后新增/替换权威图纸，创建需求修订版并使旧审批失效，
  不得静默拒绝字段写回，也不得覆盖已审批快照。

## 4. 零件可见性与语义安全

1. drawing-flow 完成后前端必须重新拉取包装零件端点，64 行应在当前看板中分页/虚拟滚动展示；
   点击零件仍在看板内展开。
2. 展示数量、截断数量、单位、材料、长宽厚、闭合状态、证据来源；列表与 API 汇总一致。
3. `closure_type` 使用受控同义词规范化；“磁吸”可匹配“双开门磁吸”，同时保留原始值与命中规则。
4. DWG 零件 `role=unknown` 时，不得按行号/面积顺序静默绑定到 BOM 角色。必须先完成人工映射，
   或让该 BOM 行保持 `unbound`；每个绑定保存证据和操作者。

## 5. 验收

从包装报价创建技术项目后，无需重复粘贴需求；上传 `酒盒.dwg` 后能在 2.1 看见 64 个可点击零件；
权威酒盒案例排名高于通用案例；未知角色不会自动获得“盖壁长边”等业务名称。

## 6. 实现记录（`## 281`）

### 6.1 报价来源与抽取证据（§2.1、§2.3）

- `requirement_service.py`：`QUOTE_TEXT_KEYS` / `quote_requirement_text()` /
  `quote_source_clues()` / `extraction_evidence()` / `assert_quote_origin_link()`
  （新码 `REQUIREMENT_QUOTE_ORIGIN_MISSING`）。`PUT /requirement` 保存前把入口分级从
  `internal_test` 纠正成 `quote` 并留痕 —— 报价侧是在建项之后才把溯源键写进需求单的。
- `main.py`：`extract-documents` 的输入改用 `extraction_evidence()`（报价原文 + 附件解析 +
  用户补充三路合并）；三路全空才 `skipped`，`reason` 与 `evidence_sources` 一并回传。

### 6.2 行业隔离（§2.2）

- `packaging_match.industry_scoped_candidates()` + `main.assert_industry_scoped_candidates()`：
  判据是**数据行上的 `industry` 列**，不是提示词；跨行业候选剔除并审计
  `workflow:box_match_candidates_scoped`（`candidate_out_of_industry`），
  剔除后一件不剩 → 409（宁可不给候选，也不显示别的行业的产品）。
  接进 `/requirement/box-match` 的 run 与 decide 两条路由。

### 6.3 DWG 路由、解析前置与需求修订（§3）

- `main.dispatch_project_drawing_parse()`：图纸入口的**唯一**分发器 —— `.dwg/.dxf` →
  `drawing_flow`、位图 → `vision`、三维交换格式 → `blocked_3d`、其余 → `blocked_other`；
  建项响应带 `drawing_parse` 并审计 `project:drawing_parse_dispatched`；
  `/drawing-flow/run` 收到非 drawing_flow 的原图直接 400（不再跑完一串步骤才报看不懂的失败）。
- `requirement_service.drawing_parse_prerequisite()`；`review_requirement()` 在 `approve`
  前把关：包装项目 `.dwg/.dxf` 缺解析且无 waiver → 409 `REQUIREMENT_DRAWING_NOT_PARSED`；
  有 waiver 放行并审计 `workflow:requirement_approved_without_drawing_parse`。
- `requirement_service.create_revision_for_authoritative_drawing()`：审批后新增/替换权威图纸
  = 一次需求修订（`revision` +1、旧审批快照**追加**留档不覆盖、需求回到 `draft` 等重新确认）；
  `POST /attachments` 与 `POST /source` 共用这一处，`_reset_approved_requirement_after_input_change()`
  改为委托它。不放宽 `EDITABLE_STATUSES`。

### 6.4 零件可见性与语义安全（§4）

- `app.js`：`refreshPackagingPartsAfterDrawingFlow()`（drawing-flow 终态后重新拉取零件端点）、
  `openPackagingPartInBoard()`（点击零件留在当前看板内展开，走 `selectPackagingPart`，
  不跳页、不换视图）；零件行 click 改走它。
- `packaging_match.py` / `cpq_packaging_match.py`（报价侧第二份实现逐字同步）：
  `CLOSURE_SYNONYMS` / `CLOSURE_CANONICAL` / `normalize_closure_type()` /
  `closure_match_evidence()`；`_dimension_closure()` 改按规范形比较，「磁吸」可匹配
  「双开门磁吸」，原始值与命中规则留在 `evidence.closure_type`。
- `packaging_bom.py` / `packaging_parts.py`：`reject_unknown_role_autobind()` +
  `binding_record()` + `BINDING_METHODS`；`role=unknown` 的件**尺寸照旧回填**（几何事实），
  但业务角色保持 `unbound`，每行带 `binding_evidence` / `binding_method` / `bound_by` /
  `part_role`；`bind_rows()` 结果新增 `role_unbound` / `role_unbound_total`。

### 6.5 未做 / 边界

- 未改任何 `tests/`；未改成本表达式、费率、权重、门槛判据；未连 PG、未写生产数据。
- 「64 件分页/虚拟滚动展示」的**渲染上限**仍由既有零件树决定（本批只保证跑完重拉与可点击，
  不改前端渲染口径）；`GET /requirement/packaging-parts` 的既有分页参数未动。
- 已知既有红（与本批无关，各自 Spec 已记）：`packaging-parts-outline.md` §9 的
  `DDegrade::test_d1`（`odd_endpoints` vs `no_closed_loop`）、
  `packaging-manual-field-confirmation.md` §2.4 的 `CGates::test_c8`。
