# 2026-10-12 至 2026-10-16

## 2026-10-12 区域优先自动选件规范与红测

- 新增区域优先自动归属、最终图纸阅读顺序及尺寸一致性 spec；最新26件手选仅作核对参考，不硬编码答案，不使用全图重分配或邻件交换。
- 新增合成红测覆盖完整区域/碎片排序、未知图层证据、区域顺序及旧尺寸覆盖；之后用户明确要求自行实现，已在本地修改业务代码，未部署或推送。
- 首次执行4条均按预期失败：70分打平、unknown错误加分、区域排序接口缺失、旧尺寸覆盖。名字归属与圆盘盒完整性仍需补集成测试。
- 用户明确授权自行实现后，本地修复unknown边界加分、已验证完整区域排序分、相同实体候选去重并保留来源；新解析按最终区域设置展示序号且保留稳定ID，尺寸显示排除携带旧候选身份的值。
- 后续完成外置标题关联、原始区域独立命名、服务器原始实体注册表验证、尺寸归档/候选身份、无身份旧值显示修正及当前预览视窗修复；多闭合组成不使用单个并集尺寸，未知额外图元保留待归属。稳定业务ID保留，显示编号按区域阅读顺序。
- 酒盒离线新解析26件自动绑定，与最新26件人工参考25件实体集合相同；原BP05改为用户指出错列在BP06的完整区域，左右盖自动选307.07×528.89完整候选，BP13完整候选正确。未向解析输入提供答案。
- 圆盘盒两独立方案完成离线解析，排模文本排除及多部分保留已验证；未建立人工金标，不宣称全部零件准确。最终15组相关回归171条全部通过；Python/JS语法及diff检查通过。新增本地验证报告。未提交、推送或部署，线上原人工项目未改写。

## 线上酒盒自动候选与下游验收

- 新增 `docs/reports/online-packaging-e2e-20261012.md`：区分 d16265d 全链路检查与线上 01e4423 独立新解析复验。
- 历史人工 19 件参考中，新解析只有 3 件实体集合完全相同，19 件预期候选全部存在；记录排序、归属、未知角色加分及尺寸门禁问题。
- 记录内部草稿财务部分成本、66 项缺口、全零单件完成计数、图形刷新及候选尺寸裁切问题。正式成本确认与报价回传未通过，不宣称全流程成功。
- 原人工校正项目未修改；本任务只新增验收文档，没有生产代码、部署、提交或推送。

## 2026-10-12 区域优先自动选件入库（本地提交，推送/部署见下条）

- 入库范围：6 个既有文件改动（`packaging_business_part_resolver.py`、`packaging_part_visual_review.py`、
  `packaging_parts.py`、`packaging_sections.py`、`frontend/app.js`、`tests/test_packaging_layout_spatial_model_combination_red.py`
  的 1 处期望值重指并带注释）+ 5 个新增（本 Spec、`tests/test_packaging_layout_first_auto_selection_red.py`、
  两份报告、本 changelog）。恒定不入库未跟踪项不变：`拆分程序/`、`.~亿纬锂能DA梳理.xlsx`。
- 提交前全量分片：`./open-claude/.venv/bin/python -W ignore scripts/run_tests_sharded.py --shards 8 --jobs 4`
  → `Ran=7247 failures=0 errors=0 skipped=29 —— 全部通过`。
- **入库前收口的一条红**（并行会话的相关回归 171 条未覆盖到）：
  `tests/test_packaging_part_size_evidence_guard::test_unconfirmed_dwg_size_survives_document_and_blocks_cost`
  在分片里 `FAILED (failures=1)`：自动归属把解析器刚给出的区域尺寸证据抹成 `size_source='none'`，
  未确认尺寸连来源都说不出。修法：`packaging_parts.set_geometry_binding()` 的「改选归档旧尺寸证据」只对
  **人工改选**（`bound_by == 'manual'`）生效 —— 自动归属不再反手抹掉解析器自己的区域尺寸
  （`geometry_region` / `bbox_only` 保留，成本门禁照旧 `BUSINESS_PART_SIZE_UNCONFIRMED`）；
  「人工改选必须归档并失效旧尺寸」的红测保持绿。未删断言、未改期望值、未放宽判据。
- 未做：线上 34 的人工校正项目未改写；未建立圆盘盒人工金标，不宣称自动选件全量准确。

## 2026-10-12 区域优先自动选件：提交、双远端推送与 34 部署记录

- 提交 `b35e02e`：12 个文件（6 改 + 6 新增，665 insertions / 18 deletions）。
  恒定不入库未跟踪项不变：`拆分程序/`、`.~亿纬锂能DA梳理.xlsx`。
- 双远端：GitHub `28f3d53..b35e02e`；GitLab（当日 DNS 不可用，走 `172.16.5.150` +
  `HostKeyAlias=gitlab.boulderaitech.com`）`28f3d53..b35e02e`；三方 `ls-remote` 回读一致
  `b35e02ef2d72627a3973f635f72918a675f5b916`。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz` → `01e442365 → b35e02ef2`；
  `build.commit=b35e02ef2d72`（branch=ytbz）、8010 pid=`3665601`、health `status=ok`。
  真转两份样本 `converter_role=primary`（酒盒 6711 实体/8 层、圆盘盒 3457/32，`dxf + preview` 齐全）。
  隔离端到端自检 `verdict=ok`：酒盒八步 8/8、263 件、`closed_ratio=0.510`、可算 13/可挤出 13；
  圆盘盒 312 件、`closed_ratio=0.840`、可算 101/可挤出 262；两条权威实样路线 `confirm=confirmed`；
  隔离目录已删，运行目录项目数 84 → 84 未污染。
- 能力口径不变：DWG 编排能力完成，真实转换能力未验收；线上原人工校正项目未改写。

## 2026-10-12 线上全流程复跑（酒盒 / 圆盘盒）与新 Spec+红测（本地，未推送/未部署）

- 在原有两个项目上把没跑完的全流程接着跑完（不新建项目、不改原人工校正项目、不删任何数据），
  每一步落盘留证：酒盒 `6beeeebb19a1`、圆盘盒 `9ab4fe6ce416`。
- 酒盒这条线：2.1 八步 8/8、`drawing-flow → packaging-business-parts → box-match →
  box-match/decision → packaging-bom → packaging-route → packaging-route/confirm →
  integration/send-to-finance → cost-review` 全部 200；本轮补齐了 1.1 需求单三步审批
  （`submit-confirmation → confirm → review`，`status=approved`）、技术工艺总结
  （`summary/recommend` → 模型产出并 `summary/confirm`）与 3.1 报告草稿刷新。
- 酒盒仍停在两处：① `POST /cost-review/confirm`（以及 `/send-to-quote`、`/return-to-process`）
  400「包装成本明细与总额未对齐」——账本按「组成」发号（`DWG-BP10/BP12::section:<hash>`）被当成
  未知零件，`reconciled=false`，且这道硬门禁在签名放行之前，签 waiver 也过不去；
  ② `POST /process-report/submit-review` 409「工艺可行性结论为空；各工艺阶段汇总结论为空」
  （3.1 汇总正文没有把 2.3 带进来，已有既有红测覆盖，本批不重复出 Spec）。
- 圆盘盒这条线：解析八步 `completed`，语义层已算出成品尺寸候选
  `inner_length = inner_width = 396.6`（`needs_confirmation`、`board=pending`、`confidence=0.8`），
  但 `field_write` 的 `written=[]` —— 候选没进需求单；下游因此全链
  `field_missing` → `box-match/decision` 409 `box_type_not_confirmable：YT-DWG-ROUND-10PC（needs_input）`
  → `packaging-bom/route/cost` 409 `box_type_not_confirmed` → `send-to-finance` 400「请先完成参数推荐」。
  对照酒盒能走下去的真正原因：需求文本里**人又把尺寸/材料写了一遍**（`field_sources=user_text`）。
- 新增 Spec + 红测（只写 Spec 与测试，不含业务实现）：
  · `docs/specs/packaging-cost-ledger-section-reconciliation.md` +
    `tests/test_packaging_cost_ledger_section_recon_red.py`（10 条，当前 8 红）；
  · `docs/specs/packaging-2-1-candidate-fields-into-downstream.md` +
    `tests/test_packaging_candidate_fields_adoption_red.py`（11 条，当前 10 红、1 绿为反例守卫）。
- 两个红测文件都实际跑过并按预期失败；三组回归基线实际通过：
  `test_packaging_single_cost_ledger_red` 18 OK、`test_packaging_cost_engine_red` 81 OK、
  `test_e2e_packaging_downstream_handoff_red` 12 OK、`test_packaging_manual_field_confirmation_red` 13 OK、
  `test_packaging_semantics_red` 59 OK（skip 1）、`test_packaging_product_outline_red` 21 OK（skip 1）。
- 线上只跑流程与只读排查，未改代码、未删数据；本轮未提交、未推送、未部署。
