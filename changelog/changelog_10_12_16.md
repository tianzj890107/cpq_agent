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
- 线上只跑流程与只读排查，未改代码、未删数据；本轮没有改动任何业务实现。
- 提交 `f5d9f87`（5 个文件：2 个 Spec + 2 个红测 + 本周 changelog，747 insertions）。
- 双远端（`ytbz`）：GitLab `865f2b3..f5d9f87`、GitHub `865f2b3..f5d9f87`；`ls-remote` 回读一致
  `f5d9f87524bb08e117aab07a4062d6d0d8611dcb`。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz` → `b35e02ef2 → f5d9f8752`（ref=ytbz）；
  `build.commit=f5d9f87524bb`（branch=ytbz）、8010 pid=4162624、health `status=ok`、
  PATH 含 xvfb 目录；两份真实样本 `converter_role=primary`（酒盒 6711 实体/8 层、圆盘盒 3457/32）；
  隔离端到端自检 `verdict=ok`（两条链路八步 8/8；权威实样路线 `confirm=confirmed`），
  隔离目录已删、运行目录项目数 86 → 86 未污染。代码与部署前一致（本批只含 Spec/红测），
  因此本次部署不改变线上行为。
- 能力声明不变：DWG 编排能力完成，真实转换能力未验收。

## 2026-10-12 成本账组成归集与 2.1 候选字段采纳通道

- 用户明确授权自行实现后，把 2026-10-12 新写的两批 Spec 红测就地转绿，业务代码只在允许范围内改动。
- 成本账组成归集：多部分组成编号（如 `DWG-BP10::section:<hash>`）不再被当成未知零件，按在册零件号、
  部件父号、`::` 前段三级归位后再计入对账；只有归不回去的组成才报 `unknown_part:`。
  红测 `tests/test_packaging_cost_ledger_section_recon_red.py` 10 条全绿。
  这正是线上酒盒 `6beeeebb19a1` 卡在 `POST /cost-review/confirm` 400「包装成本明细与总额未对齐」的根因，
  线上旧版本实测仍为 `reconciled=false`，8 条 `unknown_part:DWG-BP10/BP12::section:*`。
- 2.1 候选字段采纳通道：新增"把 2.1 图纸算出的候选尺寸人工采纳进需求单"的能力，
  记录采纳人/时间/说明与审计动作，单位未确认或字段冲突时不静默写入；
  红测 `tests/test_packaging_candidate_fields_adoption_red.py` 11 条全绿。
  圆盘盒 `9ab4fe6ce416` 的下游阻断（`needs_confirmation` 候选进不了需求单，导致
  `box_type_not_confirmable` → `box_type_not_confirmed` → 送财务 400）由此具备可走通的服务端入口；
  前端采纳按钮仍未做，线上未部署，因此线上行为不变。
- 全量回归：4 分片并行，`7268 ran / 0 failures / 0 errors / 29 skipped`，各分片全部通过。
  相关定向回归 `test_packaging_single_cost_ledger_red` 18、`test_packaging_cost_engine_red` 81、
  `test_e2e_packaging_downstream_handoff_red` 12、`test_packaging_cost_gaps_red` 10、
  `test_packaging_bom_business_parts_rows_red` 23、`test_packaging_manual_field_confirmation_red` 13、
  `test_packaging_semantics_red` 59（skip 1）、`test_packaging_product_outline_red` 21（skip 1）、
  `test_packaging_drawing_flow_red` 54（skip 1）、`test_drawing_flow_parse_terminal_signal_red` 30、
  `test_packaging_parametric_bom_red` 57、`test_packaging_quote_close_loop_red` 96 均通过。
- 两个 Spec 状态由"未实现"更新为"已实现"，并追加本轮实现与测试记录。
- 本批代码与文档随当次提交入库（提交、双远端推送与部署记录见当日后续条目）。

## 2026-10-12 材料相似度推荐与演示输入装配

- 新增 Spec `docs/specs/packaging-material-similarity-recommendation-and-demo-inputs.md`；
  红测 `tests/test_packaging_material_similarity_and_demo_inputs_red.py` 15 条、
  `tests/test_packaging_demo_flow_end_to_end_red.py` 4 条，全部通过。
- 材料推荐按克重、厚度、材质族与字面相似度打分，结论分
  `exact_name / similarity / ambiguous / below_threshold / no_candidate` 五档；前两名接近时
  如实报"歧义"而不是静默取最高分，`approved` 恒为 False，推荐结果只供人确认。
- 缺克重时按 原文克重 → 材料属性 → 牌号 → 厚度×密度 逐级推导；密度或吨价缺失时只走按材质族的
  演示口径，并在载荷里逐条披露 `source=demo_family_price`，不与权威价目混为一谈。
- 演示输入装配：零件开料长宽取几何分量的逐轴包络，材料原文优先取对照资料、缺失时按件名关键词族
  推定；只写 `poc_inputs` / `cost_parameters` 与文档级 `poc_demo` 标记，**不写** `confirmed_size`，
  不覆盖历史版本；源码不含任何真实样本名或零件编号，换一张 DWG 同样走相似度推荐而非硬编码。
- 端到端口径：没有尺寸时材料费为 0 并如实报缺口；补上演示输入后材料费 > 0，并在结果里标明哪几项
  来自演示输入。脚本 `scripts/packaging_demo_prepare.py`（干跑 / `--apply`）写入演示输入并按现有
  顺序重排 BOM → 路线 → 成本，逐件打印尺寸、材料与推荐来源。

## 2026-10-12 包装账 → 整机成本 → 回传销售 的投影补齐

- 新增 Spec `docs/specs/packaging-cost-to-quote-integration-projection.md` 与红测
  `tests/test_packaging_cost_to_quote_integration_projection_red.py`（4 条，2 红转全绿）。
- 定位到的断点：包装项目的包装账算齐并签字之后，集成计划里的"整机成本"仍然是空的 ——
  包装 2.3 只算包装账、从不回填整机成本，回传销售于是被"请先完成整机成本测算"拦死。
  线上项目酒盒实测：包装账对账通过、材料 11.46 元、合计 15.60 元，`确认成本` 通过，
  但 `回传销售` 400 —— "全流程回到销售"这一步在包装链上从未真的通到过。
- 现在：包装项目的整机成本 = 同一份包装账（金额逐字取包装账对账结果，标注
  `source=packaging_ledger` 与来源成本账版本），只在"从来没有整机成本"时补位；
  已有人工或模型测算过的整机成本一个字都不动；包装账缺失或过期仍照旧拒绝。
- 相关回归：`test_packaging_single_cost_ledger_red` 18、`test_packaging_cost_engine_red` 81、
  `test_packaging_cost_gaps_red` 10、`test_e2e_packaging_downstream_handoff_red` 12、
  `test_packaging_quote_close_loop_red` 96、`test_packaging_cost_ledger_section_recon_red` 10、
  `test_cost_review_single_primary_and_drop_run_step_red` 23 全部通过。

## 2026-10-12 包装回传的参数族与成品参数投影

- 新增红测 3 条（`tests/test_packaging_cost_to_quote_integration_projection_red.py` 的 b1–b3，
  其中 2 条由红转绿），Spec 补 §2.5 / §2.6 与第二轮实现记录。
- 问题：包装项目回传时按「其他成品」校验报价必填，去问产品系列 / 产品型号 / 最大尺寸 /
  重量 / 工作温度 —— 这些字段在包装链上没有任何来源。线上实测拒绝文案逐字如此。
- 现在：包装项目的回传必填锁定 `pkg_box` 族（复用 `integration.project_family()`）；
  需求单里已有值的包装族字段直接投影进参数推荐结果并注明来源；需求单没有的字段
  （包装产品名称 / 包装品类）不编值，仍如实留在缺口里，交给「参数推荐」环节或文本抽取补。
- 相关回归：`test_packaging_cost_to_quote_integration_projection_red` 7、
  `test_packaging_tech_param_bom_route_cost_closure_red` 20、`test_quote_agent_industry_alignment_red` 33、
  `test_tech_handoff_atomic_idempotent_red` 35、`test_tech_integration_dependency_waiver_red` 20、
  `test_tech_quote_business_case_linkage_red` 39、`test_tech_waiver_reuse_across_handoffs_red` 11、
  `test_packaging_quote_close_loop_red` 96 全部通过。

## 2026-10-12 包装需求原文直接生成「包装产品名称 / 包装品类」

- 新增 Spec `docs/specs/packaging-requirement-text-derived-params.md` 与红测
  `tests/test_packaging_requirement_text_params_red.py` 11 条（先跑 6 条红：标签不认识、
  词表不认识、空位不填、回传不补；实现后全绿）。
- 定位到的断点：包装需求单 3.1 的前两个必填「包装产品名称 / 包装品类」只写在报价原文里
  （原文写着「包装品类酒盒」），可 1.1 的报文字段抽取从来没认这两个键 —— 需求单里一直空着，
  回传销售被「报价必填的成品参数还缺：包装产品名称、包装品类」拦死，一次已经算齐、账已对平
  的包装成本反而回不到报价侧。
- 现在：报文字段抽取补上这两个键 —— 带「包装产品名称 / 包装品类」标签时逐字抄录；没有标签时
  按**通用包装品类词表**（酒盒 / 礼盒 / 食品盒 / 化妆品盒 … 22 个词，与客户、项目、图号无关）
  最长命中定品类，取含品类词的片段当产品名称；两种品类同长同现视为有歧义、留缺不猜。只填空位，
  人工 / CAD / 模型已填的值一个字不动，来源与原文证据照旧入档。
- 历史项目不用重建需求单：回传销售时按**同一份原文抽取**补投影，需求单已有值优先。原文也认不出的
  必填照旧逐个点名，不编值、不豁免。
- 相关回归：`test_packaging_cost_to_quote_integration_projection_red` 7、
  `test_packaging_round_geometry_and_requirement_facts_red` 与 `test_packaging_full_flow_20260929_red` 共 33、
  包装链路 11 套共 392 全部通过。

## 2026-10-12 包装需求原文直接生成成品参数：提交、双远端推送与 34 部署记录

- 提交 `7ac21dc`（作者 张真 <zhangzhen@boulderaitech.com>）：Spec
  `docs/specs/packaging-requirement-text-derived-params.md`、红测
  `tests/test_packaging_requirement_text_params_red.py` 11 条、
  `requirement_service.py` 报文字段抽取补两个键、`cost_flow.py` 回传投影按同一份原文补齐。
- 双远端：`gitlab/ytbz` 与 `origin/ytbz` 均回读为 `7ac21dc`。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz`，`5e739f3ff → 7ac21dcc9`；
  8010 pid=901523；两份真实样本主转换器（ODA 27.1）直出、`converter_role=primary`；
  隔离端到端自检 verdict=ok；运行目录项目数 92 → 92（未写生产数据）。
- 部署后线上复验（项目 `6beeeebb19a1`）：产品族 `pkg_box`；回传必填缺口 **无**
  （此前逐字为「报价必填的成品参数还缺：包装产品名称、包装品类」）；投影得到
  包装产品名称 `700ML双开门酒盒`、包装品类 `酒盒`；整机成本来源 `packaging_ledger`、
  合计 15.60 元。
- 仍差一步：脚本直调 `send_to_quote` 在报价侧桥接处报「请先登录」——回传需要
  **CPQ 操作人本人会话**（34 已接统一登录，本地登录被拒），浏览器里以 FI1 登录后即可完成；
  这是会话要求，不是数据缺口。

## 2026-10-12 报价卡片回到销售视角（技术整包/零件表撤出、定价块按步骤、第 4 步不再复制第 3 步）

- 新增 Spec `docs/specs/packaging-quote-card-sales-view-parity.md`（C1–C6）与红测
  `tests/test_packaging_quote_card_sales_view_parity_red.py`：先跑红 9 条 —— 整包 dump 的节点与调用、
  零件面的 12 个标识符、两个具名判据缺失（`cardSnapshotSectionSkipped` / `packagingQuotePanelStepVisible`）、
  `s4_markup` 的 `fields`/`rows` 与 `s3_markup` 逐字相同；实现后 18 条全绿。
- 按新 Spec C2 反转三份旧契约对应的红测（口径变化改 Spec、不删断言）：
  `tests/test_packaging_card_step6_parts_must_be_the_business_parts_list_red.py`（A/B/C 组反转 + D 组护栏改指技术侧）、
  `tests/test_packaging_parts_in_card_and_material_fill_red.py` A 组（§2.2/§2.3 与 B/C/D 组不动）、
  `tests/test_packaging_card_parts_lookup_link_key_parity_red.py`（A 组反转 + 写侧护栏保留）；
  三份 Spec 加「取代」指针，`packaging-quote-workspace-render-routing.md` §4 注明卡片部分被 C1 取代。
- 实现 `确认需求解析结果.html`：删 `#packagingPackageContent` 与全部 `renderPackage(...)` 调用（缓存与非缓存两条路）、
  删第 6 步触发点与整段零件面（`ensureCardPackagingParts` / `resolveCardTechProject` / `packagingPartCardRows` /
  `packagingBusinessPartCardRows` / `cardBusinessPartsAccountingText` / `PACKAGING_PART_COLUMNS` 等）、
  未知段通用渲染保留但包装卡片的 `packaging_parts` 段安静跳过（`cardSnapshotSectionSkipped(sectionId, industry)`）、
  `#packagingQuotePanel` 按步骤显示（`packagingQuotePanelStepVisible(step)`：3–5 可见，1/2/6 隐藏，新建时也按当前步设一次）。
  整包仍留在快照与页面状态里（第 6 步门禁、定价、历史版本重建照旧读得到）。
- 实现 `cpq_packaging_quote.py`：`sections()` 的 `s4_markup` 改为只讲加价与折扣（fields：加价合计 / 加价后单价 /
  折扣率 / 折扣金额 / 折后未税单价；rows：沿用第 3 步定价 + 逐条加价或「本版无其他加价」+ 折扣或「本版无折扣」）；
  `s3_markup` 与其它三段、`money_view`、定价引擎、文档八节与版本表一字未动。
- 验证：新红测 18 OK；反转后三份 30 OK；`online_business_closure / quote_workspace_render_routing /
  quote_draft_and_card_visibility / quote_close_loop / drawing_board_two_column_parts_and_3d / parts_extraction /
  quote_send_button_entry / quote_version_persistence / quote_step5_* / e2e_quote_session_completion /
  2_1_result_parts / online_two_dwg_closure / round_geometry` 共 316 项 OK；`test_spec_status_truth_red` 7 OK；
  页面两个内联 `<script>` 块 `node --check` 通过；`git diff --check` 干净。
- 未做：技术项目深链（Spec §5 非目标，没有现成 URL 方案）留待后续批次；未 push、未建 MR/tag/Release、未部署、
  未起服务、未连库、未改任何业务数据。
