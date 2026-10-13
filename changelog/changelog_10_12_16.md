# 2026-10-12 至 2026-10-16

## 2026-10-13 包装工艺参考件相似度与AI推荐统一展示（本地）

- 新Spec `packaging-process-reference-presentation`与实际失败红测；取消包装工序固定70%及默认60%的可见置信度，库内路线统一参考件口径，AI生成工序单列AI推荐，不展示标准工艺标签。原审批、工时、来源引用、成本和门禁保留。
- 参考件使用实际检索相似度，展开名称/编码、材料/尺寸/名称评分、缺失特征、匹配方式与参考工序；没有真实评分显示未评分，不以名称命中补100%。AI推荐保留参考依据及推荐理由，缺逐工序理由如实披露。
- 零件内嵌与独立工艺页共用展示组件；旧数据无需重跑，多组成分别恢复来源，非包装置信展示保持不变。新记录增加逐工序recommendation，不迁移或覆盖历史业务记录。
- 实现前6项中5项真实失败、非包装护栏通过；最终9项新测与相关回归两组138项/160项全部通过。JS语法、Python编译及diff检查通过；未提交、未推送、未部署，未做线上浏览器验收。

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

## 2026-10-12 成本账组成归集与 2.1 候选字段采纳通道（本地实现，未提交/未推送/未部署）

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
- 本轮只改本地代码与文档，未提交、未推送、未部署；34 上运行的仍是 `f5d9f8752`。

## 2026-10-12 独立续跑与修复验收

- 新增线上全流程续跑报告：酒盒b2cbb06895b2/6beeeebb19a1、圆盘盒方案1f54f28c383e6/9ab4fe6ce416，两边均有报价/技术卡片。酒盒线上23/26与人工参考一致（另有旧BP05纠正），尺寸门禁及组成子件对账阻断回传；圆盘盒缺输入阻断盒型和下游，未发布正式报价。
- 健康接口复验线上为81933af（Spec/红测提交），实际组成对账和候选采纳实现仍在未提交工作区。原52条定向测试通过；真实线上账本离线回放对账通过但ready仍false。
- 新增独立采纳验收测试3条，实跑2失败1通过：冲突状态被采纳、已confirmed候选采纳后来源仍attachment。页面采纳入口尚缺，线上新路由404。不把API和旧测试通过当全流程验收完成。
- 本轮只新增验收报告及补充测试，保留并行实现改动；没有修改业务实现、提交、推送或部署。

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

## 2026-10-12 酒盒自动候选串件修复（本地，未部署）

- 新 Spec `packaging-candidate-cascade-selection.md`；三项红测先跑全部失败，再实现同名跨视图复合限制、未封顶证据排序、冲突不得退完整候选严格子集；人工归属不改。
- 前后端排序同步，置信度显示仍封顶 85%；新增完整候选冲突提示。不使用零件编号、固定候选序号、样图尺寸特例。
- 相关 16 模块 174 项通过；随后补充前后端分数一致性测试，新测试文件 4 项通过；JS 语法、diff 检查通过。
- 原始酒盒 DXF 新解析和线上候选内存回放均绑定 26 个图纸件；25 件与最新人工实体集合一致，BP05 用用户指出的完整区域，不沿用旧残缺选择。底板 4 实体、内盒2面纸 62 实体自动选对。
- 圆盘盒重新解析保持绑定数量；两个方案各 1 件底座灰板围板由跨区复合变为本地轮廓，未有人工金标，不声称圆盘盒全部正确。磁铁、内卡无图形仍为结构推导记录。
- 逐件记录 `docs/reports/packaging-candidate-cascade-acceptance-20261012.md`。未提交、推送、部署，未写生产项目；已有自动绑定不会刷新即改，部署后需新解析验证。

## 2026-10-12 整图多方案与工艺入口修复（本地，未部署）

- 新 Spec `packaging-multi-scheme-parse-and-process-entry.md`、红测 `test_packaging_multi_scheme_entry_red.py`；初始4个缺陷全部实际跑红，追加失败假完成、手动跨方案关联、非采用方案成本异常、左右方案分区分别复现红点。旧整图拒绝契约更新为“整图可解析、未选方案不能混算”，保留防混算断言。
- 整图方案按标题、件名和空间空白带隔离；支持上下/左右排布，不写样图坐标、件数或答案。方案内重建几何，排除包住多个方案标题的大图框，恢复此前被大图框误当内孔的圆件。保留实体身份、给新增分量及业务编号加方案作用域。
- 零件/排模带方案标签；前端采用方案按钮沿用既有紧凑样式，后端角色/hash门禁和选择来源留痕，完整 alternatives 保留。公共件及已采用方案参与整单 BOM、路线与成本，不允许把两方案相加；非采用方案成本读取返回明确409，不报未处理异常。纯排模方案共用业务件，排模关联前后端都限制本方案/公共件。
- 批量工艺入口及可用态统一读取业务清单，不遍历几何碎片。业务解析失败不再报完成，几何进度明确称区域；既有尺寸/材料证据门禁保持，不伪造可计算数据。
- 完整圆盘盒DXF实测：方案1 23条/20定位（3条纸管未定位）、方案2 17条/17定位，排模6/5条，两个方案已绑定图元交集0；选用方案后分别23/17条，不混成40条的一套BOM。酒盒28条/26定位，逐件图元集合未退化。定位不等于尺寸确认，不声称圆盘盒已逐件人工验准或工艺/成本已全跑通。
- 业务17模块280项（1项跳过，其余通过）；新增16项通过。Spec自检格式问题修正后7项自检+16项新增联合复测通过；Python编译、两个前端JS语法、diff检查通过。记录 `docs/reports/packaging-multi-scheme-acceptance-20261012.md`。
- 本次未提交、未推送、未部署，未启动服务/浏览器验收，未调用真实工艺模型，未写线上项目。部署后需重新解析现有整图项目才能生成新业务清单。

## 2026-10-12 排版候选读取及零件自动关联（本地，未部署）

- Spec `packaging-layout-candidates-auto-association.md`、红测 `test_packaging_layout_candidates_auto_association_red.py`。初始4项中3项实际失败，人工保留护栏通过；新测试扩展6项。不把框外位置本身当成确认排模，不写样图坐标。
- 复用CAD图框检测，包含最多独立件名的主框之外重复件名输出为“排模/草稿候选”，进入3.2同一layout_rows，不新增业务件、不产生模数/纸张尺寸/用量。支持四条LINE构成的图框。
- 盖/地、内外圈等明写并列名称展开，按受控术语和组成名称关联本方案内唯一现有件；歧义不取第一件、缺匹配披露。自动关联可只读刷新，人工及来源不明的历史关联保留。
- 3.2展示已关联件名及缺口，已关联无需再次保存；无模数候选不显示确认排模按钮，仍可展开调整关联；沿用现有样式。
- 实图：酒盒11候选全部自动关联，圆盘盒11明文排模7条自动关联，4条没有唯一对应名称保留缺口；零件清单数量及几何归属未增加。旧酒盒需要部署后新解析生成候选。
- 相关9模块173项（1项跳过，其余通过），Spec自检7项通过；Python编译、前端JS语法、diff空白检查通过。未提交/推送/部署、未写生产项目、未调用模型、未做浏览器视觉验收。

## 2026-10-12 排模开料与材料分摊（本地，未部署）

- 新Spec与红测：packaging-sheet-cut-cost-allocation；整张材料费复用既有公式，按所需展开面积分摊回主账，校版用纸计一次，避免重复乘用量。加工/人工仍走既有工艺公式。
- 3.2增加原始尺寸标注预览、核实开料参数和各件排出数量；机器尺寸与开料尺寸分开，附近标注不直接当纸张尺寸。未排模件支持配置余量，默认长+0、宽+5mm，不借项目模数。
- 酒盒11条候选可读标注，实看883.62×269.92的高度只是局部，不能自动作为整张高度。保留尺寸角色及数量缺口，不伪造可算数据。
- 新增13项通过，业务14模块308项无失败（跳过2项）；全局Spec自检2项因其他并行文档声明不规范失败，未改动他人文档。JS语法与diff检查通过。详见docs/reports/packaging-sheet-cut-cost-acceptance-20261012.md。
- 未提交、推送、部署或写生产项目；未做线上全流程验收。

## 2026-10-12 通用演示准备与原生/相似路线（本地）

- 新Spec packaging-demo-native-and-analogy及红测。显式演示准备优先原生路线，未命中复用DA相似检索与包装模型；不针对酒盒名称/编号写特例。
- 缺工时补明示30秒估算，保留已批准非空标准工时；本件加工尺寸、单排、人工费率写入演示参数。缺图形尺寸用明示100×100mm占位，未知克重200g/㎡，不写CAD确认。仅演示装配启用，原纯函数默认仍拒绝无尺寸。
- 明文排模缺数量/尺寸可生成带mock来源的横排估算；草稿候选不升格。演示脚本增加逐件工艺生成与失败非零退出，不伪报全流程完成。
- 合成项目已验证逐件原生路线进入同一材料/人工成本账。本地桥接未配置，未声称酒盒全部真实库命中，未实际调用在线模型、写PG或部署；不是线上全流程验收。
- 本批新增6项原生/相似/估算护栏及1项工艺到成本集成测试；相关7模块93项全部通过。

## 2026-10-12 实际PG演示知识库补齐与成本验证

- 按用户本轮授权，真实cpq_kb新增25条包装demo材料及25条对应价格，版本4→6，已有同码数据未覆盖，未升格权威来源；写前备份，重复执行新增0，版本仍6。新增可复验脚本packaging_demo_kb_fill.py。
- 实际酒盒DXF解析结果+PG知识库在临时项目算通：28/28有成本，缺失/零金额0，对账一致、阻断0；22件原生参考路线、6件明示演示兜底。使用固定测试需求，金额不作为真实客户报价。未实际在线调用模型。
- 修复PG时间证据JSON序列化、演示用量投影及未关联本单包材空金额误阻断；未知/已关联包材缺口仍阻断，不移除披露。
- 数据已写PG，代码仅本地未部署；没有修改线上项目、会话或工艺主数据、没有重启服务。详细边界见docs/reports/packaging-demo-kb-cost-acceptance-20261012.md。
- 最终相关8模块104项全通过，编译及diff检查通过；独立读回确认25材料/25正数价格，知识库版本6。

## 2026-10-13 取消已选CAD件的人工尺寸确认门禁（本地）

- 新Spec packaging-auto-geometry-cost-inputs及红测；初始自动单件/多组成两项实际失败后实现。前端成本、工艺及零件面板二次门禁，后端单件输入与BOM统一自动采用已选CAD候选尺寸，不要求再人工确认。
- 标注/人工已确认尺寸优先，自动几何估算单列来源；多组成各用自己的尺寸，原确认值与原文档不覆盖。不把自动估算标为verified_cad_dimension。无图元/无归属/无有效尺寸仍拒绝；auto_geometry_inputs=false保留严格模式。
- 新增前端实际函数执行与严格模式护栏。相关主回归70项通过，严格模式另1项通过，JS语法及diff检查通过；较宽回归另有两个分组断言及一个旧前端入口计数断言失败，未声称全仓测试通过。未部署、未改线上项目或数据库。

## 2026-10-13 原有包装操作入口自动准备与采用（本地）

- 沿用原有工艺、BOM、路线与成本入口，不新增贯通演示按钮或独立启动API；自动准备缺失输入，整合参数与路线复用同一包装链路，保留多组成尺寸和估算来源。
- 用户可见的采用来源、参数及工艺采用人统一为“自动采用”，不附加demo；旧来源文字仍可识别，后台mock来源、估算记录及非人工审核审计不删除。
- 真实酒盒解析结果与PG读取快照的隔离验证：28/28件成本可生成并对账，报价仅草稿；没有在线浏览器全流程或真实模型调用验证，没有部署。
- 核对并修复旧入口冻结遗漏排模方案切换及两份Spec声明错误；四处入口冻结精确枚举scheme新增入口，不放宽其它限制。相关回归两组114项与104项分别通过（有重叠）；语法、编译和diff检查通过。酒盒隔离复验28/28、缺失0、对账一致。发布分支/目标待确认，未提交推送部署；见docs/reports/packaging-original-click-auto-flow-20261013.md。
- 用户确认后将包装仓库约定、双远端推送和MR脚本统一为ytbz；34演示环境允许明确授权后的精确提交部署，不merge、不创建tag/Release，保留20260909与未跟踪用户资料。发布前17模块237项回归全部通过。
- 34b0d98已双推并部署34，健康版本核对一致，酒盒及圆盘盒ODA主转换无回退；新建酒盒线上解析28业务件、26几何归属。线上发现工艺准备仍误用碎片尺寸，新增两项实际失败红测后修复完整候选尺寸优先及规则版本失效，待回归及再次部署复验。
- 同次线上发现未知材料虽已有估算价却因空文本被单件工艺拒绝；实际失败红测后仅在估算模式使用“未识别材料（估算）”及独立来源，不伪造真实材质，严格模式保留缺口。
- 上述完整候选尺寸、未知材料估算入口及准备版本失效3项红测转绿，相关18模块255项回归通过。
- 线上日志发现数量“待人工确认”被转float导致500；实际失败红测后将明确缺失占位词按缺失估算处理，保留original_value，不覆盖真实值。候选尺寸与未知材料修复847763c已部署，进一步占位修复待发布；页面和完整回传仍在验收。
- 酒盒线上28/28有成本、无缺失零金额、对账一致，成本79.824094元/只，数量1000，FI1确认无豁免通过。回销售400暴露原工程任务与本次财务任务身份混用；新Spec/实际失败红测后分离报价定位与财务完成身份，同事务校验项目/类型/领取人、幂等关闭本人财务任务，不关闭PE1任务。成本提示区分算齐估算与缺金额，保留非正式报价披露；这些续修待发布复验。
- 财务任务身份、成本提示及占位续修相关13模块187项通过；包含旧交接事务/幂等、实例定位及新版跨卡片任务、跨项目/领取人/未领取护栏。编译、JS语法和diff检查通过。
- 2cd99b7已双推并部署34；原FI1回传已成功，原报价9864c5fea7bd进入3、SM1已领取，财务任务4006756526441108922真实关闭。页面因20秒同步回执误报超时并把已完成成本卡标中断；新实际失败前端行为红测后将三个去向动作改为deferred，真实终态按动作名自报，待发布复验。
- 销售续验发现常规cost_to_quote只保存产品行、丢失包装包，导致第3步误报未取得成本。两项实际失败红测后保留包装快照；同一已交接命令只补确实缺失的包装分区，不覆盖已有内容、不新增任务/消息、不回退步骤，待发布复验。
- 去向进度/终态单列动作名，不再共用costStep污染已完成成本卡；实际失败红测转绿。快照补齐及deferred续修相关8模块226项通过，JS语法及diff检查通过。

## 2026-10-13 一键生成工艺推荐并发与缓存 · 业务件可见编号（本地）

- 两处新Spec与红测：`packaging-process-batch-concurrency-and-cache`、`packaging-business-part-visible-identity-code`。新增红测 `test_packaging_process_batch_concurrency_red`（11项，A组用node真跑前端并发/退避，C/D组真跑后端并行与真实HTTP读取）、`test_packaging_business_part_identity_code_red`（3项）。
- 提速（不改任何工艺判定/门禁/成本口径）：前端「一键生成全部工艺推荐」由严格串行改为**有界并发3**（吃满后端 `TASK_WORKERS=4`），轮询由固定1200ms改为**600ms起、×1.5退避、封顶1200ms**；各件既有判定（已有有效工艺跳过、stale 才重跑、失败计 failures、`succeeded/partial/interrupted` 终态）逐字不变。
- 后端 `packaging_sections.recommend_process` 对**各组成并行**调用模型（默认4，`PACKAGING_SECTION_WORKERS` 可覆盖），按输入顺序确定性重排，合并 steps/warnings/open_questions/coverage 与串行逐字一致。
- `da_process_routing` 加**进程内 TTL 缓存**（默认300s，`DA_ROUTING_CACHE_TTL_SECONDS` 覆盖）：只在 `cache_scope()` 内生效，作用域由单个工艺请求处理期进入、进程级计数覆盖组成并行工作线程；作用域外直接调用 `for_row`/`_fetch` 仍各自读桥（不跨调用记忆），只缓存成功结果，`clear_cache()` 可清空。
- 可见编号取稳定身份码：业务件列表行与面板标题改取 `business_part_code`，不再取版面顺序号 `display_code`；一件一个号（列表号 == 工艺面板头号 == URL 号）。身份码、排序、落库、下游绑定一律未动。
- 并发度可关：前端批量并发可用 `window.PACKAGING_PROCESS_BATCH_CONCURRENCY` 覆盖（1 = 回到逐件串行，峰值并发 1）；组成并行用 `PACKAGING_SECTION_WORKERS` 覆盖（1 = 串行）。
- 验证：两新测 14项全绿。护栏未回归：`test_packaging_batch_real_execution_red`、`test_packaging_layout_first_auto_selection_red`、`test_packaging_route_feature_similarity_red`、`test_packaging_analogical_process_red`、`test_da_process_routing_live_red`、`test_packaging_part_route_constrained_match_red`、`test_drawing_board_two_column_parts_and_3d_red`、`test_drawing_flow_frontend_wiring_red`（119项 OK）。读 app.js 的50个模块672项中仅7项失败，经 `git stash` 复核均为本批改动前既有失败，本批新增0失败。
- 未提交、未推送、未部署；未改任何测试期望值，未动并行会话的实现。
