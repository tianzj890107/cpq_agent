# 变更日志（10-5 ~ 10-9）

## 555. `## 550~554` 的提交、双远端推送与 34 部署记录（10-10，本地）

- 提交 `3a6c4b2`（`## 550~554 包装相似/借鉴工艺、参照件成本输入、图纸顺序与前端控件统一`），
  入库 49 个文件：30 个既有文件改动 + 19 个新增（5 份 Spec、5 份红测、`packaging_manufacturing.py`、
  `inspect_packaging_drawing.py`、2 份报告、证据截图 4 张、架构复盘第 11 篇）。
  不入库未跟踪项不变：`拆分程序/`、`.~亿纬锂能DA梳理.xlsx`。
- 提交前全量分片：`./open-claude/.venv/bin/python -W ignore scripts/run_tests_sharded.py --shards 8 --jobs 4`
  → **`Ran=7174 failures=13 errors=0 skipped=28`**；13 条红集中在圆盘几何角色覆盖、图框零件环、
  图元矩形、零件环计数与前端缓存版本校验，均为几何/前端在途项，已在提交信息里登记、不隐藏。
- 双远端：GitHub `efe13fe..3a6c4b2`；GitLab 按 `## 545` 办法改问内网 DNS `172.16.99.114` 得到
  `172.16.5.150`，以 `HostKeyAlias=gitlab.boulderaitech.com` 直连推送（未改 remote 配置、未写 hosts、未改凭据）。
  三方一致 `3a6c4b2bdc2469e61d2aeb5b10fe399c024918f5`。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz`，`efe13fe → 3a6c4b2`；`build.commit=3a6c4b2bdc24`、
  8010 pid=`516690`、`/api/health` 200、`/` 200。自检：ODA 27.1 主转换器（酒盒 6711 实体/8 层、
  圆盘 3457/32）；隔离端到端 `verdict=ok`（酒盒八步 8/8、零件 242、closed_ratio=0.603、可算 10/可挤出 10；
  圆盘 441、closed_ratio=0.834、可算 133/可挤出 368；两条权威实样路线 confirmed），未写运行目录（81→81、0→0）。
- 部署后核对：本批 67 项相关红测在 34 上 `Ran 67 ... OK`；`packaging_manufacturing.py` 与
  `packaging_part_route_match.py` 已就位。
- 注意：隔离自检数字随几何改动变化（酒盒 263→242、圆盘 312→441）；圆盘 441 与冻结期望 312 的差异
  正是上述 13 条红里的一条，尚未收口，线上看到的是这一版。

## 554. 包装近期前端控件统一（10-10，本地）

- 新增 `packaging-recent-frontend-consistency.md`；排模关联从无样式原生多选改为可展开复选网格、已选计数与默认回显，分组保存、不推导用量，保存中禁用、失败保留选择重试。沿用 inline-action 与系统蓝色。
- 组成编辑补齐输入/多选边框与焦点态；尺寸补填统一可换行表单，移除散落内联宽度；包装报价组件自带草稿/空态提示、表头和标题样式，不再依赖单一宿主页。候选预览与已有单件按钮保留现有风格。相关资源更新缓存版本。
- 新增6项测试（真实渲染/采集及样式接线）；首轮3项和追加2项实际失败，最终6项与相关50项回归通过，JS语法/diff检查通过。未做浏览器视觉或线上验收，未提交推送部署。

## 553. 包装相似件工艺借鉴与前端来源展示（10-10，本地）

- 新增 `packaging-analogical-process-recommendation.md`；首轮3项红测实际2失败1错误，扩展至8项。独立工艺借鉴检索允许内外/左右不同的同材料件，身份匹配约束不变；最多3条实际路线头行供模型按本件调整，不复制工时、价格或批准状态。
- 相似匹配不再直接照搬工序；精确匹配保留顺序和重复项。空模型输出明确失败。前端既有内嵌工艺详情以可展开中文来源展示参考件及工序，复用现有样式；建议不再误称“库内没有、需新建”。保存方案备注也保留可读参考信息。
- 旧精确保序测试默认夹具从相似匹配改为精确名称匹配，新增相似调整单独覆盖，不删旧断言。8项新增检查及相关119项通过，JS语法/diff检查通过；前端断言为静态接线。未提交、推送、部署，未运行真实模型或浏览器验收，不声称圆盘全流程通过。

## 552. 包装路线命中后工艺与材料成本输入修复（10-9，本地）

- 新增 Spec `packaging-reference-process-and-cost-input-closure.md` 与24项红测，实际失败后修复。包装行业单独标记，保留真实装配角色；业务/几何件与单件/组成推荐均分流包装，不影响其他行业机械工艺。
- DA lookup结构化传入，唯一真实路线按原工序顺序生成建议、保留重复项/编号/来源；相似与草稿不升级批准，不复制参照工时。未命中走包装专用模型提示；空输出/机械工序明确失败，禁止机械兜底。历史机械类结果保留但不得计入包装工序实例。
- 本件及组成已填吨价/税因子/排模/校版参数进入材料公式；跨材料不继承父件价格；灰板可用本件克重，禁止借整盒面纸克重。单件材料费缺输入不再当完整0元。既有克重兜底正例改为实际面纸，新增灰板拒绝负例。
- 真实酒盒底板面纸+只读PG复验已生成正确7道包装工序，未知工时留空；相关227项通过、编译及diff检查通过。扩大回归仍有1项圆盘几何角色覆盖检查失败；圆盘尺寸/材料/组成与真实价格费率缺口未闭环，不声称整单全流程通过。未提交、推送、部署。

## 551. 相似路线接入后的本地包装下游复验（10-9，报告，未通过）

- 新增 `docs/reports/local-packaging-downstream-recheck-20261009.md`。三图用真实模型各复核8次；酒盒28件仅7件通过工艺输入，圆盘上23件/下17件均0件通过，仍有尺寸、材料与组成缺口。
- 真实运行底板面纸推荐，取得通用机械路线；进程内只读接真实PG、传入实际7道包装路线后再次推荐仍错误，证明匹配成功不等于正确工艺输出。发现包装行业提示/默认机械工序与业务件适配的接线问题。
- 单件材料费缺吨价/税因子；隔离项目路线因其余件缺工艺阻断，项目成本因未构建BOM阻断。未造数据绕过、未做线上卡片/浏览器验收、未修改业务实现、未提交推送部署。

## 550. 工序推荐接入名称—材料—尺寸相似度（10-9，本地）

- 新增 `packaging-route-feature-similarity.md` 和红测；用户授权直接接入实际推荐：名称/归一化未命中且无显式业务编码时，经认证只读桥获取包装候选，按名称55%、材料25%、已核实尺寸20%评分；旋转尺寸可比较，方向/编号/件型与已知材料类型冲突拒绝，近分歧义不自动选。
- 真实路线按编码关联 DA 物料规格与材料段；包装来源白名单、200条上限、缺失/弱尺寸不计分。相似路线 approved=false，保留分维依据与原头行，不继承标准尺寸、工时或费率；单件/组成复用相同调用，正式门禁未放宽。
- 首批8项红测实跑失败后修复；只读 PG 证实26条酒盒路线都有物料证据，合成检索实取底板路线“开料→模切”。圆盘解析部分件仍缺核实尺寸和材料，不声称全流程已通过。未提交、推送或部署。
- 最终新增15项与既有相关回归共121项全部通过；改动Python编译、diff空白检查通过。未做线上端到端验收。

## 549. 包装零件 ↔ 库内零件/工艺路线的受约束匹配（10-9，本地）

- 新增 Spec `docs/specs/packaging-part-route-constrained-match.md` 与红测
  `tests/test_packaging_part_route_constrained_match_red.py`（44 项）；按用户点名例外**直接落地实现**：
  新增 `tech_app/backend/services/packaging_part_route_match.py`（编码/逐字名 → 归一化名 →
  受硬约束相似度 → 显式 `unbound`，只用标准库 `difflib`）。
- 接线：`da_process_routing.for_row()` 加外购/采购短路（不调桥接）与归一化名重试一次；
  `packaging_process_instances.collect()` 的外购跳过改引用同一处 `is_external_part`，外购口径只留一处。
- 真实样本（图上 28 件 × DA CLM 26 条酒盒路线名）：26 matched（15 逐字 + 11 归一化，即 5 件
  「忖纸」→「衬纸」、6 件「左盒/右盒」→「左盖/右盖」）+ 2 外购 skipped + 0 unbound；
  反例守卫拦下 `底板`/`底板面纸`、左/右、内盒1/2 误配。
- 不回归：业务件工艺、图纸解析流程、成本引擎、成本收口、DA 只读桥、单件—项目路线成本同源。
- 未提交、未推送、未部署。

## 548. 包装完整曲线、空间顺序、顺序归属和原生尺寸显示（10-9，本地，部分完成）

- 新增 Spec `packaging-drawing-order-circles-and-dimension-scene.md`、红测与独立 CAD 账本/渲染工具；先验原图再对解析，不读 BOM 答案，不写样本尺寸到生产规则。
- 修复 CIRCLE/ARC/ELLIPSE/SPLINE 未进入几何分组；增加真实闭合外轮廓包含验证、完整圆形候选优先与内部碎片降权，保留物理组成分组。
- 件清单按 CAD 空间阅读顺序；候选和组成编辑共用顺序占用门禁，前件可接管后件，后件绑定/尺寸/组成失效并保留历史；前端提示前件占用/后件接管。
- DIMENSION 原生显示图元进入 IR 和场景；单件/候选按关联证据显示原图尺寸并纳入 fit，保持显示与计算证据分离。已独立核实的单组成 CAD 尺寸可供下游使用，不放宽无证据门禁。
- 50 项相关测试通过；扩大回归 125 项中 123 通过、2 条旧样本碎片计数冻结断言失败，未隐藏。JS 语法、diff 空白检查通过。
- 实图上/下方案分别 23 行/21 绑定、17 行/16 绑定，不代表正确率；纸管多视图与物理组成区分、邻件归属仍待完善。上一轮其余报价/首页/成本问题未在本轮修复。整体验收不通过；未提交、推送、部署。

## 547. 架构改造后线上包装全流程复验（10-9，报告）

- 新增 `docs/reports/online-packaging-e2e-20261009.md`：基于线上 `28d22ba` 实际运行酒盒、圆盘上/下两方案精准链路，以及两条快速报价；保留报价和技术工艺实例入口。
- 验收结论：部分 POC 草稿链路可继续，**完整业务验收不通过**。三图八步解析完成，68 件单件工艺和68件单件成本均因尺寸/组成确认409；项目只能得到材料0、带缺口的模板成本。
- 验证财务草稿派发、领取、读写与回原报价；销售第3～6步金额一致，正式导出及报告送审门禁正确拦截。参数推荐沿用已有需求、圆盘多组成/平面预览与PE1首页分页已有改善。
- 记录22项剩余问题，优先为自动归属错误、尺寸展示与下游事实不一致、快报编辑器被重绘移除、快报首页漏卡、精准卡错误名称和完成态；附关键截图。未修改业务实现、标准案例价格或门禁，未提交、推送或部署修复。

## 546. 架构复盘补一份「全流程逻辑」文档（10-9，本地）

- 新增 `docs/reports/architecture-review-20261009/11-全流程逻辑-端到端.md`：**只讲流程**，
  不列优化项、不列缺口清单。内容为三条主链路 + 两条支线：
  链路 A 正向报价（六步卡 + 快照 + 正式门禁）、链路 B 快速报价（相似案例 + 人工选基准 + 差异价，
  全程不进技术工艺）、链路 C 技术工艺（5 阶段 × 13 子步骤，说明「表行序 = 依赖序」及
  `field_write` 为何夹在 1.1 与 1.2 之间）；两条支线（匹配不到标品新建产品、成本交财务测算）。
- 同时写清：2.1 图纸八步（`STEP_IDS` 与 `blocked` 非终态、`run_id` 同附件同快照即同一 run）、
  图元/几何区域/业务件三层口径（263 ≠ 28）、3.x 部件/排模/工艺/成本、两套数量与两套成本口径
  （单件材料费 vs 整单确定性公式、业务件哈希对不上即无成本）、公式引擎与模型「不分工是分开的」、
  两个交接包（`packaging_package` 去 / `FINANCE_HANDOFF_FIELDS` 回）与状态/版本/留痕机制、端到端时序图。
- `README.md` 目录表补第 11 行。仅文档改动；未提交、未推送、未部署。

## 545. `## 536~544` 的提交、双远端推送与 34 部署记录（10-9，本地）

- 提交 `28d22ba`（`## 536~544 批次 1~5 实现与真并行分片回归；DA 工艺路线真实接入、单件—项目路线—公式成本同源`），
  入库 64 个文件：21 个既有文件改动 + 43 个新增（批次 1~5 与真并行分片 5 份 Spec、8 份红测、
  `tests/support/`、`scripts/run_tests_sharded.py`、`scripts/ci_checks.py`、
  `capability_isolation.py`/`da_process_routing.py`/`kb_health.py`/`packaging_observability.py`/`packaging_process_instances.py`、
  三个只读工具、`cpq_process_routing.py`、`docs/reports/architecture-review-20261009/`）。
  不入库未跟踪项不变：`拆分程序/`、`.~南京锂能DA梳理.xlsx`。
- 提交前回归：`./open-claude/.venv/bin/python -W ignore scripts/run_tests_sharded.py --shards 8 --jobs 4`
  → **`Ran=7067 failures=0 errors=0 skipped=28` 全部通过**；改动 Python 的 `py_compile`、
  改动 JS 的 `node --check`、`git diff --check` 全过。
- 双远端：GitLab 与 GitHub 的 `ytbz` 均回读为 `28d22bad48b8eb38b9c3ed3daa0837f04798217c`
  （`31aad47..28d22ba`），与本地 HEAD 三方一致。
  · 本机当日仍解析不到 `gitlab.boulderaitech.com`（`ssh` 报 `Could not resolve hostname`）：
    按 `## 534` 的办法改问内网 DNS `172.16.99.114` 得到 `172.16.5.150`，以
    `HostKeyAlias=gitlab.boulderaitech.com` 直连该 IP 完成推送；未改 remote 配置、未写 `/etc/hosts`、未改凭据。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz`，`5720a3b → 28d22ba`（纯快进）；
  `build.commit=28d22bad48b8…`、`branch=ytbz`、`deployed_at=2026-10-09T13:58:57+0800`、
  8010 pid=`276480`；`/api/health` status=ok、`/`（174,105 B）与 8012 `/api/health` 全 200。
- 部署后核对（部署脚本自检）：ODA 27.1 主转换器直出、`converter_role=primary`、`fallback_used=false`；
  酒盒.dwg 6711 实体 / 8 层、圆盘盒.dwg 3457 实体 / 32 层，dxf + preview 齐全。
  隔离端到端自检 `verdict=ok`：酒盒八步 8/8、零件 263 件（`closed_ratio=0.510`）、可算 13 / 可挤出 13；
  圆盘盒八步 8/8、零件 312 件（`closed_ratio=0.840`）、可算 101 / 可挤出 262；
  两条权威实样路线 `confirm=confirmed`；隔离自检未写运行目录（`tech_app/tech_data` 78 → 78、`tech_app/data` 0 → 0）。
- 部署后核对（只读）：13 个代表文件（`run_tests_sharded.py`、`ci_checks.py`、`capability_isolation.py`、
  `da_process_routing.py`、`packaging_process_instances.py`、`kb_health.py`、`packaging_observability.py`、
  `cpq_process_routing.py`、`packaging_cost.py`、`packaging_parts.py`、`packaging_route.py`、
  `quick-quote-panel.js`、`cad_ir/parser.py`）在 34 与本机 sha256 逐字节一致（不一致数 0）。
- 本提交同时入库并部署了「DA CLM 工艺路线真实接入」与「单件推荐—项目路线—公式成本同源」两条在途改动
  （changelog `## 534`、`## 535`）。未创建 MR/tag/Release，未改任何生产数据，门禁两项人工项仍未代签。
  能力声明不变：**DWG 编排能力完成，真实转换能力未验收**。

## 544. 真并行分片回归：把「分片」变成「同时跑」+ 全量验收（10-9，本地）

- **根因**：`scripts/run_tests_sharded.py::run_shards()` 只在 `for` 循环里逐个阻塞
  `subprocess.run` —— 只切分片、不并发，所以 `--shards 8` 的墙钟 ≈ 串行，仍是 10 分钟级。
  批 2 的 Spec/红测只要求「分片 + 隔离 + 结论一致」，**从未要求并发**，属规格漏项。
- **修复**：`run_shards(..., jobs=)` 用 `concurrent.futures.ThreadPoolExecutor` 并发跑分片
  （分片本体是阻塞子进程，线程即可真并行）；新增 `--jobs`，默认取 **CPU 数的一半**（跑满核会
  超订）。分片隔离（`TMPDIR`/`CPQ_TEST_SHARD*`）、聚合口径、`shards` 升序、`jobs=1` 串行全部保留。
- Spec：`docs/specs/parallel-sharded-regression.md`；红测
  `tests/test_parallel_sharded_regression_red.py`（5 条：实现前 **4 红 1 绿**，现 5 绿）。
- **全量实测（436 模块）**：串行 628.79s → `--jobs 4`（默认）**292.30s，Ran=7067 failures=0
  errors=0 skipped=28 全绿**；`--jobs 8` 216~239s 更快，但会出 2 条**负载相关**红
  （`packaging_drawing_flow_red::e26_concurrent_reruns` 并发竞态、
  `dwg_conversion_quality_repair_red::e5_real_oda` 真实 ODA 转换超时）—— 二者单跑均绿，
  故默认取半核。3 分钟级目标未达标（约 4.9 分钟）。
- **顺带修掉的 2 条全量红**：`test_spec_status_truth_red` 的 c1/c2 —— 我自己新写的
  `parallel-sharded-regression` 声明「未实现」却无处说明原因、且红测已转绿；已按仓库口径改为
  「已实现」，元测试恢复绿。
- 回归：`test_release_assurance_batch2_red`、`test_spec_status_truth_red` 全绿；
  `git diff --check` 干净。
- 残留（登记不改）：`E26` 的并发竞态像是**真缺陷**（非仅抖动），建议单独开批定位；
  自带 ODA/xvfb/node 子进程的用例是尾片候选，可进一步压时间。
- 未改任何业务实现；未提交、未推送、未部署。

## 543. 可用性与服务边界（批次 5）实现：能力隔离 + 发布门禁 + 统一解析守卫（10-9，本地）

- 落地 `## 539` 之后的 Spec `docs/specs/capability-isolation-and-shared-parse-batch5.md`
  （状态改为已实现，补 §8 实现记录），红测 `tests/test_capability_isolation_and_shared_parse_batch5_red.py`
  由 `Ran 12 … FAILED (failures=8)` 转 **`Ran 12 … OK`**（T17 四条守卫本来就绿）。
- 新增 `tech_app/backend/services/capability_isolation.py`（纯函数、绝不抛异常）：
  `isolation_view`（两种能力形状都认；脏入参按 disabled；`documents` 恒 enabled 红线）、
  `release_verdict`（fail + manual_unacknowledged 阻断，生产环境 skip 也阻断；
  非法报告 → `no_go` + `gate_report_invalid`；只判发布、不判运行）。
- `scripts/create_release.py`：新增 `--gate-report` / `--force` 与 `require_release_gate()`，
  创建 Release 前必须过生产门禁，未过则拒绝并打印 blocking；`--force` 显式覆盖并告警。
  既有 tag 口径（`repository/tags`、`不得隐式创建 tag`、`vMAJOR.MINOR.PATCH`）逐字保留。
- 边界：不动 `cad_converter` 的 `available = 主 or 回退`、不装第二套 ODA、不在报价侧 import
  `ezdxf`、不在 CI 加部署；T17 只验收。
- 不回归：`test_repository_workflow_contract + test_dwg_file_capability_preflight_red` → `Ran 38 … OK`。
- 按 Spec §5 **未提交 / 未推送 / 未部署**；并行会话在途改动一行未碰。

## 542. 可用性与服务边界（批次 5）Spec 与红测（10-9，本地）

- Spec：`docs/specs/capability-isolation-and-shared-parse-batch5.md`，把「转换器坏了」与
  「系统坏了」分开：部署验收失败只阻止发布，运行时转换器故障只禁用 DWG 解析并告警，
  登录 / 历史报价 / 文字·Excel·PDF·图片 快速报价一律不受影响。
- `T8` 运行侧**只验收不重做**（10-9 实测已是能力隔离）：`cad_converter.capability()`
  `available = 主可用 or 回退可用` 且探测不抛；`unified_parse.capability()` 明确「能力查询绝不许
  500」；`file_preflight` 的缺转换器是单文件 415 业务错误。缺的是**统一投影**
  `capability_isolation.isolation_view()`（哪些能力可用 / 被禁用 / 为什么）与把「发布 / 运行」
  分开的 `release_verdict()`。
- `T8` 发布侧未接线：`scripts/create_release.py` 只校验 tag 存在，**不消费**生产门禁报告的
  `verdict`（`dwg_deploy_gate --env production` 的 `go`/`no_go`）；`deploy_34_bare.sh` 里那行门禁
  命令只是打印提示。本批要求 Release 前过 `release_verdict`（`--gate-report`，`--force` 才可覆盖）。
- `T17` **只验收加守卫**（10-9 实测已是共用同一服务）：`unified_parse.SERVICE_PATH /
  CAPABILITY_PATH` 是唯一事实源、`main.py` 用它登记免登录白名单、`cpq_quick_quote_file` 与之逐字
  一致且**不 import** `ezdxf`/ODA，转换只有 `cad_converter/adapters/local_cli.py` 一处。
- 红测：`tests/test_capability_isolation_and_shared_parse_batch5_red.py`，实现前实跑
  **12 条：8 红 4 绿**——8 红=新模块缺失（7）+ `create_release.py` 未接线（1）；
  4 绿=T17 守卫（地址唯一、报价侧不自解析、能力查询不抛、能力转述 provider）。
- 相关既有回归：`test_repository_workflow_contract` + `test_dwg_file_capability_preflight_red`
  合计 38 OK，零新增回归；`T17` 真机判定（34 `capability` 真探测）列为验收项。
- 边界：不改 `available = 主 or 回退` 口径与错误码闭集；不装第二套 ODA；不在 CI 里加部署
  （发布门禁放 Release 工具）；运行时故障不得升级成登录/历史报价/文字快速报价停机。
- 未改任何业务实现；未提交、未推送、未部署。五批（0~5）Spec + 红测已全部交付。

## 541. 真实数据与识别覆盖（批次 3）+ 体验与可观察性（批次 4）实现（10-9，本地）

- 落地两份 Spec，红测由红转绿：
  `tests.test_data_and_recognition_coverage_batch3_red` 改前 9 红 → **`Ran 11 … OK`**；
  `tests.test_observability_and_experience_batch4_red` 改前 23 红 → **`Ran 23 … OK`**。
- 批次 3（只新增三个只读工具，未改既有业务代码）：
  `tech_app/tools/packaging_box_type_range_audit.py`（分类 + issue 闭集 + no_lower_bound/inverted；
  `--strict` 才非零，审计永远退出 0）、`tech_app/tools/packaging_parts_accuracy.py`
  （漏识别/误识别/归属/尺寸/证据覆盖分开度量；`manual_fix_minutes` 原样透传；`summary` 无单一正确率标量）、
  `tech_app/tools/packaging_routing_diagnostic.py`（按编码/名称命中、孤儿步骤、草稿头、工时空头，
  verdict attention|ok；纯函数不改入参）。本仓种子 12 行审计 `issues==[]`、`adjustable==12`。
- 批次 4：新增 `tech_app/backend/services/packaging_observability.py`（P3/P4/P6/P7/P11/P1/P12/P13/P10
  九个纯函数投影：正式导出剩余步骤、成本版本+暂估+缺口分类、排模缺参、转换器 banner、表达式口径文案、
  业务零件优先、人工建档建议、草稿按操作细分、在等谁+已等多久）与 `kb_health.py`
  （`usable_for_current_route` 只由 `required_tables` 决定，无关表为空不判死）；
  `cpq_quick_quote_match` 新增 `explain_ranking` / `transfer_compare`；
  `project_access` 新增 `visible_scope_note`（只给有权数量，`hidden_count` 恒 `None`）；
  `cad_ir/parser` 曲线采样步长配置化（`DEFAULT_LIMITS.curve_sample_step`、
  `ENV_CURVE_SAMPLE_STEP`、3 处改用解析值、`parser.options` 落盘）。
- 前端接线（唯一口径来自后端，不在前端另算）：`cost-review.js` 新增 `crCostDisplayNote` 引用
  `cost_display`；`quick-quote-panel.js` 候选表新增「为什么不是更高分」列读 `why_not_higher`；
  `tech-task.js` 新增 `converterBannerNote` 消费 `converter_banner`。
- 实跑：批次 3 不回归 `test_packaging_match_undecidable_and_size_guard_red +
  test_packaging_parts_extraction_red` → `Ran 55 … OK`；批次 4 §6 不回归
  `test_dxf_cad_ir_red + test_tech_home_timeline_and_publish_closure_red` → `Ran 81 … OK (skipped=1)`；
  引用本批改动模块/文件的 71 个模块 → `Ran 1465 … OK (skipped=2)`；
  `test_doc_path_and_root_consistency_red + test_spec_status_truth_red` → `Ran 17 … OK`
  （批次 3 新建的 3 个工具路径补齐了 `test_a1` 的路径守卫、两份 Spec 状态行改为已实现后 `test_c1` 不再点名）。
- 边界与边界外的另一路：三份前端文件（`cost-review.js` / `quick-quote-panel.js` / `tech-task.js`）
  与本批改动互不重叠；按两份 Spec §5 **未提交 / 未推送 / 未部署**。
- 注：`docs/specs/packaging-business-part-process-by-authority-route.md`、
  `packaging-unified-route-cost`、`da-process-routing-live` 等并行会话的在途改动本批一行未碰。

## 540. 体验与可观察性（批次 4）Spec 与红测（10-9，本地）

- Spec：`docs/specs/observability-and-experience-batch4.md`，把「缺什么 / 哪一版 / 谁算的 /
  为什么排在后面」做成统一投影，并让 `P1`/`P2` 按「主界面只突出业务零件 + 只显示有权数量」降级落地。
- 覆盖项：`P3` 导出还差哪几步、`P4` 成本版本 + 暂估 + 缺口分类（闭集，未知归「其它」）、
  `P11` 排模点名缺什么、`P6` 转换主/回退 + 版本 + 许可、`P7` 表达式口径文案（恰为
  「变量映射后的表达式匹配」，禁「逐字」）、`P8` 知识库健康面板、`P9` 候选「为什么不是更高分」
  + 转精准对照、`P10` 在等谁 + 已等多久、`P12` 草稿角标按操作细分、`P1` 业务零件主、`P13`
  不建议自动识别建议人工建档、`P2` 可见范围说明（不泄露无权数量）、`T15` 曲线采样参数配置化写入 IR。
- 现状缺口（10-9 实测）：`packaging_observability.py` / `kb_health.py` 不存在；
  `converter_role` 只在前端之外的工具里出现；`cpq_quick_quote_match._rank_reason()` 只解释
  「为何排在这里」；`home_card._waiting_for()` 无「已等多久」；`cad_ir/parser.py` 的
  `sample_step: 0.01` 硬编码在 3 处、不进 `DEFAULT_LIMITS` 与 `parser.options`。
- 红测：`tests/test_observability_and_experience_batch4_red.py`，实现前实跑 **23 条全红**
  （23 failures / 0 errors），红点分三类：新模块不存在（12+2）、既有模块缺新函数（P9/P2）、
  前端未接线（3）与 T15 未配置化（3）。
- 相关既有回归：`test_dxf_cad_ir_red` + `test_tech_home_timeline_and_publish_closure_red`
  合计 81 OK (skipped=1)，零新增回归。
- 边界：不改成本公式/费率/匹配算法/缺口裁决源；缺口分类、是否暂估、相似度一律以后端为准，
  前端不另算；不做前端大规模重构；`P2` 范围说明不替代修 ACL。
- 未改任何业务实现；未提交、未推送、未部署。

## 539. 真实数据与识别覆盖（批次 3）Spec 与红测（10-9，本地）

- Spec：`docs/specs/data-and-recognition-coverage-batch3.md`，把「识别得准不准、区间数据对不对、
  工序编码能不能命中」从口头判断收敛为三份**可执行的只读报告**。
- T6 区间审计：匹配代码**已支持**逐轴下限（`packaging_match._dimension_size()`，`size_guard`
  红测 23 OK），本仓种子 `da_seed_packaging.BOX_TYPES` 12 行边界齐全、零倒挂；缺的是对生产
  `kb_packaging_box_type` 跑同一套不变量的工具。新增只读
  `tech_app/tools/packaging_box_type_range_audit.py`（`classify_sizes` 分固定规格/可调尺寸/参考；
  `audit_box_types` 出 `range_missing`/`range_inverted`/`range_not_positive`）。
- T14 + O6 识别度量：全仓无 accuracy/precision/recall/留出图实现（10-9 grep 为空），只有在位性
  门禁。新增只读 `tech_app/tools/packaging_parts_accuracy.py`（`load_holdout` / `score_parts`）：
  漏识别、误识别、归属、尺寸、证据覆盖、人工修正耗时**六个维度分开计**，人工耗时只透传不推算，
  `summary` 不得出现任何单一正确率标量。
- T16 路线诊断：只有只读桥 `da_process_routing.py`，无离线诊断。新增只读
  `tech_app/tools/packaging_routing_diagnostic.py`（`diagnose_routing` 出按编码命中、仅按名称命中、
  未命中、孤儿步骤、草稿头、空工时头、`verdict`），不改桥、不自动采纳模糊匹配。
- T13 只验收不重做（`import_da_kb_to_pg.py` 默认 dry-run、`kb_deploy_preflight` 含
  `box_type_missing_fit_clearance`），本批只放两条守卫。
- 红测：`tests/test_data_and_recognition_coverage_batch3_red.py`，实现前实跑 **11 条：9 红 2 绿**
  （9 failures 全部是三个工具模块不存在，按 T6/T14/T16 各 3 条；2 绿为 T13 守卫，符合预期）。
- 相关既有回归：`packaging_match_undecidable_and_size_guard` + `packaging_parts_extraction`
  合计 55 OK，零新增回归。
- 边界：不改 `packaging_match` 算法与权重、不给所有盒型统一补下限、不按样本件数或金标尺寸调参、
  不把答案键喂进提取链路（只在**对答案**阶段用）、不动 `da_process_routing.py` 与前端。
- 未改任何业务实现；未提交、未推送、未部署。

## 538. 发布保障（批次 2）实现：分片回归 + 共享测试件 + CI 全量语法门禁（10-9，本地）

- 落地 `## 537` 的 Spec `docs/specs/release-assurance-batch2.md`（状态改为已实现，补 §8 实现记录），
  红测 `tests/test_release_assurance_batch2_red.py` 由 **13 全红** 转 **13 全绿**。
- 新增 `scripts/run_tests_sharded.py`：`discover_modules` / `plan_shards` / `shard_env` 三个纯函数 +
  `--list` / `--shards N --dry-run` / `--shards N [--json]` 三段 CLI。分片按排序后轮转分配，
  确定性、两两不相交、并集等于全集；每个分片一个独立进程 + **独立 TMPDIR 根**（跑前建根），
  避免 `tests/_tmp_guard.py`「一次运行一个根」在多进程下互删临时目录。
- 新增 `scripts/ci_checks.py`：`compile_all(roots)` / `whitespace(base, cwd)` 两个 API；
  `--compile-all` 默认覆盖 `cpq_*.py` / `tech_app/` / `scripts/` / `tests/` 四个根，
  `--whitespace` 等价 `git diff --check`（`--base` 为空则退化为工作区差异）。
- 新增 `tests/support/{__init__,js_source,frozen,stage_table}.py`：唯一 JS 函数体抠取
  （字符串 / 注释 / 模板串 / `${...}` / 正则里的括号不算结尾，找不到函数名返回空串）、
  集合封闭断言（分开列出「新增」与「缺失」）、前后端编号表按 id 逐字段比对。
- 改 `.gitlab-ci.yml` 的 `python_contract`：`py_compile` 四个文件改为
  `python scripts/ci_checks.py --compile-all --whitespace --base "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"`，
  失败即拦合入（无 `allow_failure`）；CI 仍无 deploy / ssh / scp / rsync / systemctl / git pull。
- 实跑（`./open-claude/.venv/bin/python -W ignore -m unittest`）：
  `test_release_assurance_batch2_red` 改前 `Ran 13 … FAILED (failures=13)`、改后 `Ran 13 … OK`；
  `run_tests_sharded.py --list | wc -l` = 434 = `ls tests/test_*.py | wc -l`；
  `ci_checks.py --compile-all --json` → `{"ok": true, "checked": 663, "failures": []}`；
  `test_packaging_stage_order_red test_cpq_eval_ci_contract` → `Ran 31 … OK`（不回归）。
- 边界：只做本地测试编排与 CI 检查，未迁移既有 17 份 JS 抠取与 419 处计数断言（后续批次），
- 全量（冻结后）：`Ran 7050 … FAILED (failures=34, skipped=28)`；34 条**全部**落在另一路在途的
  两份新红测与它们的 Spec 守卫（`observability-and-experience-batch4_red` 23 条、
  `data-and-recognition-coverage-batch3_red` 9 条、`test_spec_status_truth_red::test_c1`、
  `test_doc_path_and_root_consistency_red::test_a1`）；本批新增/改动文件 **0 红**
  （改前基线 48 红 = 本批 13 条红测 + `test_repo_leftovers_red::test_c1` 误判 1 条）。
  误判已修：`run_tests_sharded.py` docstring 里的 `_tmp_guard.py` 字面量命中
  「入库脚本不许引用一次性脚本」正则（`tmp_[A-Za-z0-9_]*\.py`），改写为 `_tmp_guard` 后转绿。
  未动前端运行时与 `workflow_stages.py`；按 Spec §5 **未提交 / 未推送 / 未部署**。
- 注：`docs/specs/data-and-recognition-coverage-batch3.md`（另一路在途、未跟踪）的状态行
  「未实现」缺原因，会让 `test_spec_status_truth_red::test_c1` 保持一条红；本批未碰该文件。

## 537. 发布保障（批次 2）Spec 与红测（10-9，本地）

- Spec：`docs/specs/release-assurance-batch2.md`，把「全量回归从 10~11 分钟压到 3 分钟级」
  拆成五件可验收的事：分片运行器、唯一稳健的 JS 抠取、集合枚举断言件、前后端编号逐行比对、
  CI 语法检查补齐。
- 现状实测：`scripts/` 无分片/并行入口（432 模块 / 约 6970 项串行 10~11 分钟）；`tests/` 里
  至少 17 份各自实现的 JS 括号计数抠取（函数体出现 `}` 字面量即抠错、集体报红）；
  冻结清单一律用计数断言（实测 419 处 `assertEqual(len(...), <数字>)`）；
  `tech-workbench.js:19` 的 `const STAGES` 仍是手抄 9 行，没有任何测试与后端
  `workflow_stages.py` 逐行比对（10-9 只读比对：当前 0 漂移）；CI 的 `python_contract`
  只 `py_compile` 4 个文件（一方 Python 共 660 个），也没有空白检查。
- 红测：`tests/test_release_assurance_batch2_red.py`，实现前实跑 **13 条全红**
  （13 failures / 0 errors）：T1 分片 6 条、T3 JS 抠取 3 条、T4 集合断言 1 条、
  T5 逐行比对 1 条、T12 CI 门禁 3 条。
- 边界：分片运行器只做本地测试编排、每个分片独立 TMPDIR 根；CI 只加检查，保持
  「CI 不部署」；不迁移既有 17 份抠取与 419 处计数断言（后续批次分批做）；不动前端运行时
  与 `workflow_stages.py`。
- 相关既有回归：`packaging_stage_order 7`、`cpq_eval_ci_contract 24` 全绿，零新增回归。
- 未改任何业务实现；未提交、未推送、未部署。

## 536. 架构复盘事实校准 + 链路一致性（批次 1）Spec 与红测（10-9，本地）

- 架构复盘 12 份文档完成 10-9 事实复核：订正 8 条陈旧结论并加三态标注（本地已实现 /
  已上线待验收 / 仍缺实现）。案例库并非为零（导入脚本已在，本地 2 条案例全部达标、
  含价且已审核）；最小收费口径已裁决；配合间隙缺失已不再扣总分上限；CI 已跑全量；
  上线门禁实为 19 项、知识库实为 32 张表；同源路线在本地尚未上线；成本公式表述改为
  「登记源公式 + 变量映射后匹配」。产物：`docs/reports/architecture-review-20261009/`。
- 批次 1「链路一致性」Spec：`docs/specs/chain-consistency-batch1.md`，定位两个**可复现**的
  真实分歧：① 2.3 成本确认按布尔 `has_gaps` 判可用，与 readiness gate 的 severity 分层
  结论相反（实测 gate=formal、ready=False）；② 报价金额无唯一投影，展示单价 1.14 × 3 = 3.42
  而含税总额显示 3.41。另发现 `stale`（依据漂移）只在 2.3 生效、gate 不认。
- 红测：`tests/test_chain_consistency_batch1_red.py`，实现前实跑 **9 条中 8 条红**
  （5 failures / 3 errors，1 条前提校验绿）；相关既有回归
  `severity_layering 8 / panel 26 / input_version_pinning 7 / route_bom_version_pinning 14 /
  quote_close_loop 96` 全绿，零新增回归。
- 同批把「同源路线、回传版本、输入版本钉扎、缺口分层」列为**验收项**（已实现、不重做）。
- 金额尾差口径（单价权威 / 总额权威）留给业务拍板，本批只做唯一投影与如实披露。
- 未改任何业务实现；未提交、未推送、未部署。

## 535. 包装单件推荐—项目路线—公式成本同源（10-9，本地）

- 项目路线优先汇入已保存单件/组成工序；保留部件、组成、原序号、用量、公式及
  DA 依据。完整性检查不允许缺件或过期推荐混成完整路线；历史无单件结果路径兼容。
- 成本只消费确认路线，不再从 BOM 拼同名去重列表；机裱等加工公式不重复计人工。
  单件拼版/上机参数缺失给缺口，不用包围盒猜参数；模型分钟不冒充标准秒数。
- 保存/冻结来源指纹和参数，推荐或用量变化使路线过期并阻止成本继续算旧方案。
  前端路线表显示部件/组成/用量/公式，公式计费不误提示必须补标准工时。
- 修复整项目工艺/成本结果最多 20 条的截断，各部件各自保留最多 20 个版本。
- Spec：`packaging-unified-route-cost`；实现前 6 红（2 failures/4 errors）。
- 新增 15 条测试；相关回归合计 346 条通过，Python/JavaScript 语法及 diff 检查通过。
- 未新增排模算法、参数编辑 API 或 CLM 工序编码规则维护平台；未提交、推送、部署，
  未改线上数据，不能把隔离测试通过视为现场正式成本已核准。

## 534. DA CLM 工艺路线真实 PG 接入（10-9，本地）

- 只读核实线上 27 个头、124 行；26 个包装路线均为草稿，全部行的三类工时为空。
- 新增内部令牌保护的只读路线查询，技术侧通过 HTTP 读取；按产品编码或精确件名
  查询并关联工序，保留顺序、原文、来源、状态及空工时。各组成独立检索。
- 将真实依据加入模型工艺推荐输入并随结论保存；名称/草稿不视为批准标准，
  服务不可用明确披露，工序输出仍标记模型推荐。DA 表目前属于 pending 条目。
- Spec/red：`da-process-routing-live`；实现前 4 条红（2 errors/2 failures）。
- 实际适配器只读验证：左盖面纸名称/编码均命中 8 道工序；圆盘盒名称无匹配，
  头行和工序关联无孤儿。相关 77 条测试通过，语法与 diff 检查通过。
- 未写线上主数据、未提交、未推送、未部署；不构成标准工时或正式成本验收。

## 521. 本周 changelog 文件预建（10-8，Codex）

- 按“changelog 只按自然周维护、每个文件覆盖周一至周五”的规则，预建本周文件
  `changelog/changelog_10_5_9.md`（2026-10-05 至 2026-10-09），条目编号接续上周
  `changelog/changelog_9_28_10_2.md` 的 `## 520`，沿用同一套条目格式。
- 建文件时（2026-10-08 周四）当周尚无实际代码、配置、文档或测试变更，因此本次只建文件；
  待本周有实际改动后再按最终状态补入并合并整理。

## 522. 包装线上三组全流程复验与遗留问题记录（10-8，Codex）

- 在34服务器已部署的`62a1849`上，以SM1/PE1/FI1建立酒盒、圆盘上半/下半三组真实报价与工艺卡片，上传DWG、页面启动八步解析，业务件28/23/17；卡片全部保留。
- 包装BOM/路线/部分成本、FI1读写成本与原报价回传成功；通过带缺口内部草稿绕行继续销售3—6步，三组正式Word导出被拦。没有导入金标、补零件展开尺寸、代签缺口/正式审批、导出正式报价或写主数据。
- 核实行业传递、成本业务件口径、原报价回传产品行、毛利恢复及正方形CAD预览已生效；记录金额舍入/税口径冲突、整合丢已知需求、误登出、正常财务交接、完整零件与尺寸等15项遗留问题，明确绕行不等于正常流程验收通过。
- 补充实测智能补全：需求摘要读取顶层旧键而未读取包装data，导致已知220.5×90×90/225g被建议为260×210×80/250g；只留证据，没有保存或确认猜测值。全部项目最终也能找到三张卡片，但列表/历史加载存在明显延迟。
- 新增`docs/reports/online-packaging-e2e-20261008.md`及右盖实际预览截图；本轮仅验收证据/报告，不改业务代码或测试，不提交、推送或部署。

## 523. 包装曲线/零件尺寸与线上验收遗留问题本地修复（10-8，Codex）

- 新增 Spec `docs/specs/packaging-round-parts-and-e2e-fact-consistency.md`，逐项承接验收报告 15 项问题，明确已实现、部分实现和真实图纸/上线复验边界。
- 修复圆弧被当弦线、半弧/半椭圆被判闭合、椭圆与 bulge 曲线采样遗漏，以及零件预览漏画圆/弧。补充复合名称多轮廓与真实块实例的弱归属候选，不硬编码酒盒答案、不从 BOM 凑数或生成尺寸。
- 径向标注必须匹配真实圆、见证点及尺寸，可靠 CAD 标注尺寸可传入单件工艺和业务件 BOM；包围盒估算仍不解除正式门禁。零件行只显示两位小数的尺寸，移除用户指定的三条冗长说明，保留证据数据。
- 推荐/智能补全读取已保存包装需求，自动填充只保存确定性需求事实；补充克重/闭合方式同义抽取、矛盾克重隔离。成本区分报价批量与装配用量，数量不一致成为确认缺口。
- 新增内部草稿财务交接按钮及服务路径，传递同项目需求、业务件版本和路线，不代签参数/工艺、不开放正式发布。第五步保留引擎单价精度及原始税额，第六步使用保存报价生成确定性草稿并明确导出门禁。
- 修复能力探测鉴权、项目标题/真实轮次、请求超时/失败重试与并发合并、包装报告草稿结论、旧成本阶段和电池匹配用语，隐藏残缺 JSON 标记。
- 新增 8 模块 29 项红测并实跑失败后修复，最终相关 20 模块 300 项通过；JS/HTML 语法和 diff 检查通过。历史缓存实图只读诊断发现尺寸证据仍不齐，不能宣称全部零件正确或线上闭环通过；没有提交、推送、部署或改动生产数据。

## 524. 收口 `## 523`：全量回归暴露的 10 条红逐条定位并判因（10-8，Codex）

- 触发：`## 523` 只跑了 20 个相关模块（300 项），没跑全量。本轮跑
  `./open-claude/.venv/bin/python -W ignore -m unittest discover -s tests -p 'test_*.py'` →
  `Ran 6919 … FAILED (failures=10, skipped=28)`；把工作区改动 `git stash` 后同一组模块 164 项
  `OK`，证明这 10 条**全部由本批引入**，不是既有挂账。
- 判因（逐条实测，不是推测）：
  - 圆盘盒闭合数 255 → 262 的根因是 `_entity_chains()` 改吃解析器的 bulge 采样（`sampled_points`）。
    把 `sampled_points` 从 IR 行里摘掉再跑 → 精确回到 255/0.817；翻转的 7 件全是**单实体**
    「两顶点 + 两个 180° bulge」的整圆（采样后 289–386 点、首尾重合），此前被当成开口件。
    ELLIPSE 与 ARC 采样都不是原因（分别屏蔽后计数不变）。
  - `test_a5`「圆 0 段」与新 Spec §1「预览必须保留曲线形状」直接冲突，且 `## 523` 自己新增的
    `test_part_preview_preserves_round_and_bulged_segments` 明确要求圆/弧出段 —— 两条不可能同时成立，
    按新 Spec 判旧断言过期。
- 处置（**没有一条靠放宽判据**）：
  - 同步 7 处旧期望值并在测试内逐字留因：`cad_plan_polyline_segments::test_a5`（改名 + 改钉新契约：
    圆 1 段、首尾重合、空实体仍 0 段）、`dimension_frame_not_part_ring::test_c2`（255 → 262）、
    `business_parts_outline_bbox_link::CLOSED_RATIO`（0.817 → 0.840）、`2_1_part_row_size::test_e2`
    （`218.20 × 68.25 mm`）与 `::test_e4`（改名，`— × — mm`）、
    `tech_params_autofill_and_soft_gates::test_autofill_helper_reuses_existing_pipeline`
    （`aiParamsAutofill({ knownOnly: true })`）、`packaging_online_business_closure` 两条
    （`税金 '2080.00'`、`折后价格 '18'`，评测串补注入 `packagingQuoteDetailValues()`）。
  - 改**实现**而不是改测试的两处：`aiParamsAutofill()` 签名去掉默认对象字面量
    （`options = {}` 会让 `block_from("function aiParamsAutofill(")` 把 `{}` 当函数体，连带
    `test_b9` 也读不到函数体）；`packagingAuthorityDisclosureLines()` 的图纸推导清单句改为
    同时点名来源（图纸推导）与出路（CAD 可直看、无需导入对照表），使
    `packaging-part-thumbnail-absence-must-name-its-source.md` §2.4 与新 Spec §5 第 11 项同时成立。
  - 给 4 份旧 Spec 补附录 A 与 1 份新 Spec 补 §6.3，逐条写明「哪个 Spec 授权、判据没放宽」，
    并如实记下残留：几何分量行仍是 `展开 218.2×68.25 mm`（去尾零、无空格），与 §2 的
    「统一两位小数」不一致，另行裁决。
- 验证：全量 `unittest discover` 复跑 → `Ran 6919 … OK (skipped=28)`（零红）；`node --check`
  （`app.js` / `assembly-integration.js` / `cost-review.js` / `requirement-create.js` / `cpq_auth.js`）、
  改动 Python 文件 `py_compile`、`git diff --check` 全部通过。
- 入库：本批连同 `## 522`/`## 523` 的工作区成果一并提交（35 个跟踪文件改动 + 11 个新文件）；
  不入库的未跟踪项：`拆分程序/`（独立拆分程序与酒盒拆分方法说明，仓库运行路径无引用）、
  `.~南京锂能DA梳理.xlsx`（Excel 锁文件）。

## 527. 包装报价结构化展示、快速卡片路由及选中态（10-8，本地）

- 新增 Spec `packaging-quote-workspace-render-routing.md` 与 6 项测试，初始四项红测实测失败后实现；补实际执行的卡片/创建跳转、URL 编码、刷新与读取失败保护。
- 首页只选择精准/快速路线；提交创建快速实例后跳专属 URL，卡片回到同一实例页面，不在首页展开操作。复用已有命令/费率引擎；读失败不新建，提供重试，刷新恢复服务端工作区。
- 修正快速按钮永久选中样式，互斥选中态以实际模式为准。包装精准报价结构化展示完整快照及嵌套组成/尺寸/材料/用量，未填毛利率也可查看；定价表按真实列渲染，保留零值、false 与新增字段，不丢数据。
- 快速报价 489 项通过（跳过 3 项），包装报价 168 项通过；两页面内联 JS、两个组件语法及 diff 检查通过。旧“留在首页”测试按用户本轮路由要求更新。
- 未提交、未推送、未部署、未启动服务、未浏览器视觉验收；无后端协议/数据库更改。上一批多组成实现及用户未跟踪文件均保留。

## 526. 包装一个业务零件多组成部分闭环（10-8，本地）

- 用户要求直接完成 Spec、红测、实现；新增 `docs/specs/packaging-multipart-business-part-complete.md` 和 30 项红测，落实组成保存、独立尺寸/用量/材料、版本证据、整体及单部分 CAD 预览。
- 普通单个名称也可对应多个真实闭合图形；补单实体圆候选、排模副本隔离及负坐标平移测试。内部孔/工艺线按轮廓和角色归属；嵌套刀线存在组成歧义时不自动重复计材。
- 工艺、材料成本实际任务和 BOM 消费全部组成，保持父业务编码；缺组成/尺寸/费率不伪造完整总额。工艺合并保留组成归属并重编号依赖，成本按实际用量计入。
- 相关 22 个模块 323 项测试全部通过（53.979 秒）；前端模块语法、`git diff --check` 通过。四个旧接口/件数冻结测试按新 Spec 更新，保留 CAD 证据检查。
- 真实旧缓存只读诊断圆盘盒有 5 个多组成业务行，不等于所有件已拆准。无 BOM 输入、无生产库写入；本批未全仓测试、未提交、未推送、未部署，保留用户 Excel 锁文件与 `拆分程序/`。

## 525. `## 522`–`## 524` 的提交、双远端推送与 34 部署记录（10-8）

- 提交 `7b16541`（`## 522-524 包装线上验收记录、曲线/零件尺寸修复与全量回归收口`），入库 46 个
  文件：35 个跟踪文件改动 + 11 个新文件（Spec `packaging-round-parts-and-e2e-fact-consistency.md`、
  8 个新红测模块、`docs/reports/online-packaging-e2e-20261008.md` 与右盖实际预览截图）。
  不入库的未跟踪项（与本批能力无关，且仓库运行路径无引用）：`拆分程序/`（独立拆分程序 + 酒盒
  拆分方法说明）、`.~南京锂能DA梳理.xlsx`（Excel 锁文件）。
- 提交前复跑（复核口径）：全量 `unittest discover -s tests -p 'test_*.py'` → **`Ran 6919 … OK
  (skipped=28)`**；`node --check`（`app.js` / `assembly-integration.js` / `cost-review.js` /
  `requirement-create.js` / `cpq_auth.js`）、改动 Python 文件 `py_compile`、`git diff --check` 全过。
- 双远端：GitLab 与 GitHub 的 `ytbz` 均回读为 `7b165418e7cc1e61dae9e04d887abb0f9630874a`
  （`4b29e19..7b16541`），与本地 HEAD 三方一致。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz`，`62a1849 → 7b16541`（纯快进）；
  `build.commit=7b165418e7cc1e61dae9e04d887abb0f9630874a`、`branch=ytbz`、
  `deployed_at=2026-10-08T11:41:11+0800`，8010 pid=`2650356`，`/api/health` 与 `/suite/health`
  均 200，8012 `/api/health` 200；启动 PATH 含 `xvfb` 目录。
- 部署后核对（脚本第 5 步，两份真实样本）：ODA 27.1 主转换器直出、`converter_role=primary`、
  `fallback_used=false`（酒盒 6711 实体 / 8 层，圆盘盒 3457 实体 / 32 层），dxf + preview 齐全。
- 部署后核对（脚本第 6b 步，隔离端到端自检 `verdict=ok`）：酒盒八步 8/8、零件 263 件
  （`closed_ratio=0.510`）、可算 13 / 可挤出 13；圆盘盒八步 8/8、零件 312 件
  （**`closed_ratio=0.840`**，即本批 7 件 bulge 整圆首次判闭合）、可算 101 / 可挤出 262；
  两条权威实样路线（`YT-DWG-ROUND-10PC` 9 道 / `YT-DWG-WINE-700ML` 10 道）`confirm=confirmed`；
  隔离自检未写运行目录（`tech_app/tech_data` 78 → 78）。
- 部署后核对（只读）：8012 实际下发的 `app.js` 含「本版清单由图纸推导 / 无需导入对照表」，
  `assembly-integration.js` 含 `aiParamsAutofill(options)` —— 与本次提交逐字一致。
- 能力声明不变：**DWG 编排能力完成，真实转换能力未验收**；门禁两项人工项
  （`converter_license`、`real_samples_e2e_passed`）未代签。真实样图的整件边界 / 尺寸准确率与
  线上页面视觉仍须另行实图验收，本批不构成客户正式报价授权。

## 528. 收口 `## 526`/`## 527`：全量回归补齐 3 条漏改并落实「精确重指」（10-8，Codex）

- 触发：`## 526`（多组成闭环）与 `## 527`（报价结构化展示与快速卡片路由）各自只跑了相关模块
  （22 模块 323 项 / 489 + 168 项），都标了「未全仓测试」。本轮跑全量
  `unittest discover -s tests -p 'test_*.py'` → `Ran 6955 … FAILED (failures=3, skipped=28)`。
- 3 条红全部是同一类「路由计数冻结」与一处 DOM 桩缺口，**没有**业务口径回归：
  - `test_packaging_business_part_basis_in_panel_red::test_c6`、
    `test_packaging_authority_workbook_upload_red::test_d3`：`packaging-business-parts/` 8 → 9、
    `/size-confirm` 1 → 2 —— 与 `## 526` 已重指的另外三份逐字一致，只是当时漏了这两份；
  - `test_packaging_online_business_closure_red::test_cached_quote_repopulates_new_step_tables`：
    缓存命中分支现在也把结构化快照画进 `#packagingPackageContent`，评测串缺 `document` 与
    `renderPackage` 最小桩（`applied!==1` 的断言本身一个字没动）。
- 纠一处**判据放宽**：`## 526` 把
  `test_packaging_business_part_size_must_be_confirmed_by_dimension_red::test_e1` 的
  「derived / sized 件数**精确相等**」改成了 `assertGreaterEqual` 下限。下限不是重指，本轮改回
  `assertEqual` 并按新 Spec 精确填写实测值：圆盘盒 **36 → 46**（derived 与 sized 都是 46），
  酒盒 26 不变；新 Spec §1 允许的单实体闭合圆正是这 10 件。原有的
  `component_ids` 与 `geometry_region` 证据断言保留。
- Spec 留痕：给 `packaging-business-part-size-must-be-confirmed-by-dimension.md` 补附录 A
  （36 → 46 的授权与「仍是精确相等」），给 `packaging-multipart-business-part-complete.md` 补
  附录 A 汇总本批同步的**全部**旧冻结值（含本轮补的 3 份），逐条写明哪份 Spec 授权。
- 验证：全量复跑 → **`Ran 6955 … OK (skipped=28)`**（零红）；`git diff --check`、改动 JS
  `node --check`、改动/新增 Python `py_compile` 全过。
- 状态：本地提交 + 双远端推送 + 34 部署（详见 `## 529`）。

## 530. 零件首屏尺寸、候选来源图集与预览布局（10-8，本地）

- 新增 Spec `packaging-candidate-gallery-initial-size.md` 与 6 项测试；初始四项红测失败后实现。修复单组成尺寸显示遗漏，候选未重选也能显示既有尺寸/参考范围，保留两位小数；没有将估算写成确认尺寸。
- 候选下拉附真实证据来源，下面可展开全部候选 CAD 缩略图、尺寸、证据排序与来源；点图只预览，另有“选用当前候选”保存，预览不会被旧绑定覆盖，只读角色仍可看图。
- 提示/工具条移出正方形 SVG，包装主图按窗口高度约束居中适配，100% 明示为适应窗口的相对倍率；切换零件重置右栏滚动，包装 modelPanes 不再 flex 撑满挤压图形，其他行业不变。
- 相关 13 个模块 184 项通过；随后新增的复位按钮测试与新模块共 6 项复跑通过（有重叠，不相加）；模块语法及 diff 检查通过。
- 本地修改，未提交/推送/部署，未启动服务或做浏览器视觉验收；无后端/生产数据修改，保留用户未跟踪文件。

## 529. `## 526`–`## 528` 的提交、双远端推送与 34 部署记录（10-8）

- 提交 `2c5a386`（`## 526-528 一个业务件多组成部分闭环、报价工作区结构化展示与全量回归收口`），
  入库 26 个文件：2 份新 Spec、2 个新红测模块、新服务 `tech_app/backend/services/packaging_sections.py`、
  17 个改动文件与 changelog。不入库的未跟踪项不变：`拆分程序/`、`.~南京锂能DA梳理.xlsx`。
- 提交前复跑：全量 `unittest discover -s tests -p 'test_*.py'` → **`Ran 6955 … OK (skipped=28)`**；
  `git diff --check`、改动 JS `node --check`、改动/新增 Python `py_compile` 全过。
- 双远端：GitLab 与 GitHub 的 `ytbz` 均回读为 `2c5a3864596697ae7f10ce228d2a7fcf72e97ef5`
  （`634a6da..2c5a386`），与本地 HEAD 三方一致。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz`，`7b16541 → 2c5a386`（纯快进）；
  `build.commit=2c5a3864596697ae7f10ce228d2a7fcf72e97ef5`、`branch=ytbz`、
  `deployed_at=2026-10-08T15:12:26+0800`，8010 pid=`3456406`，`/api/health` 与 `/suite/health` 200。
- 部署后核对（第 5 步）：ODA 27.1 主转换器直出、`converter_role=primary`、`fallback_used=false`
  （酒盒 6711 实体 / 8 层；圆盘盒 3457 实体 / 32 层），dxf + preview 齐全。
- 部署后核对（第 6b 步，隔离端到端 `verdict=ok`）：酒盒八步 8/8、零件 263 件（`closed_ratio=0.510`）、
  可算 13 / 可挤出 13；圆盘盒八步 8/8、零件 312 件（`closed_ratio=0.840`）、可算 101 / 可挤出 262；
  两条权威实样路线 `confirm=confirmed`；隔离自检未写运行目录（78 → 78）。
- 部署后核对（只读）：8010 `/` 200；8012 实际下发的 `packaging-quote-panel.js` 含 `renderPackage`、
  `app.js` 含 **9** 处 `packaging-business-parts/`（与 `## 528` 重指后的护栏一致）。
- 能力声明不变：**DWG 编排能力完成，真实转换能力未验收**；门禁两项人工项未代签。多组成闭环在真实
  图纸上仍需逐件复验（本批只读诊断：圆盘盒 5 条多组成业务行），不构成客户正式报价授权。

## 531. 收口 `## 530`：全量回归暴露的 2 条红（10-8，Codex）

- `## 530` 只跑了「相关 13 个模块 184 项」，标了「未全仓测试」。本轮全量
  `unittest discover -s tests -p 'test_*.py'` → `Ran 6961 … FAILED (failures=2, skipped=28)`，
  两条都在本批改动的文件里，**都不是既有挂账**：
  - `test_global_brand_color_red::test_no_legacy_purple_or_competing_blue_brand_literals`：
    新增的候选图集样式把 `var(--color-primary, #2563eb)` / `var(--color-primary-light, #eff6ff)`
    当兜底值，`#2563eb` 是品牌守卫点名的竞争蓝。按本文件既有约定改为
    `var(--color-primary, #0060E6)` 与 `var(--color-primary-light, #E6F0FD)`。
  - `test_packaging_all_parts_visual_adjudication_red::test_frontend_marks_candidate_as_unconfirmed_and_exposes_manual_review`：
    候选提示改写时把「尺寸仍须单独确认」丢了。该句是「候选 ≠ 已确认」的真话护栏，**改实现补回**
    （`…；证据来源…。尺寸仍须单独确认。`），没有动那条断言。
- Spec 留痕：给 `packaging-2-1-right-pane-single-part-figure.md` 补附录 A，写明其
  「右栏同时至多一个 `svg`」的字面数量限制已被 `packaging-candidate-gallery-initial-size.md` 取代，
  而「只有一个可交互主图 / 主图出自纯函数 / 别件图元不进这张图 / 缩略图不共享拖拽绑定」照旧。
- 验证：全量复跑 → **`Ran 6961 … OK (skipped=28)`**；`git diff --check`、`node --check app.js` 通过。

## 532. `## 530`–`## 531` 的提交、双远端推送与 34 部署记录（10-8）

- 提交 `a3ed335`（`## 530-531 零件首屏尺寸、候选来源图集与预览布局；全量回归收口`），入库 6 个文件：
  新 Spec `packaging-candidate-gallery-initial-size.md` 与其 6 项测试、`app.js`、`drawing-flow.css`、
  `packaging-2-1-right-pane-single-part-figure.md` 附录与 changelog。不入库未跟踪项不变。
- 提交前复跑：全量 → **`Ran 6961 … OK (skipped=28)`**；`node --check app.js`、`git diff --check`、
  `py_compile` 通过。
- 双远端：GitLab 与 GitHub 的 `ytbz` 均回读为 `a3ed335c9898d2c37aa08e138a07a10a320e006b`
  （`903622c..a3ed335`），与本地 HEAD 三方一致。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz`，`2c5a386 → a3ed335`（纯快进）；
  `build.commit=a3ed335c9898d2c37aa08e138a07a10a320e006b`、`deployed_at=2026-10-08T15:59:53+0800`，
  8010 pid=`3648762`，`/api/health` 200。
- 部署后核对：ODA 27.1 主转换器直出、`fallback_used=false`（酒盒 6711 / 8 层，圆盘盒 3457 / 32 层）；
  隔离端到端自检 `verdict=ok`（酒盒 263 件 `closed_ratio=0.510`、可挤出 13；圆盘盒 312 件
  `closed_ratio=0.840`、可挤出 262；两条权威实样路线 confirmed；未写运行目录 78 → 78）。
- 部署后核对（只读）：8010 `/` 200；8012 下发的 `drawing-flow.css` 含候选图集样式且
  **`#2563eb` 归零**，`app.js` 含「尺寸仍须单独确认」——与本次提交逐字一致。
- 能力声明不变：**DWG 编排能力完成，真实转换能力未验收**；本批全部是前端展示与交互，
  未做浏览器视觉验收（真实页面观感仍须人工确认），不构成客户正式报价授权。
## 533. 首页项目轻量分页与工艺工作台渐进加载（10-8，本地）

- 新增 Spec `project-page-progressive-loading.md`，先实跑三项红测失败再实现，扩展新增测试到 9 项通过。
- `/api/projects?page=...&page_size=6&q=...` 返回权限筛选后的当前页元数据，不为卡片展示调用 build_card 或读取 IR/BOM/成本/会话明细；旧无页参数接口兼容。排序以更新时间和 ID 稳定分页，保留 ACL 必要依据。
- 首页我的/全部项目每次仅加载当前页，分页、查询、归档、重试接线，缓存按用户/范围/页/查询隔离；实际执行测试确认旧页迟到不覆盖新页。技术首页待办加载不拉报价卡片全集。
- 工艺卡片先跳工作台，再按需恢复该项目阶段。第 2 步已存零件和主体先显示，流程/角色映射/BOM/旧项目兼容补算不挡页面就绪；补算去重并增加跨项目迟到结果保护。第 3 步保存的整合结果不再等零件明细和会话回放。
- 相关回归 146 项通过，后续保护相关 70 项通过（有重叠），新增模块最新 9 项通过。
- 全量回归（`unittest discover -s tests -p 'test_*.py'`）先暴露 **2 条红**，都在本批改动的文件里：
  `test_tech_history_restore_real_stage_red::test_cpq_home_open_project_fetches_real_stage_data` 的
  `/workflow`、`/summary` 两条子用例 —— 它们钉的正是本批**取代**掉的旧契约「首页自己
  `Promise.all` 四个详情接口再判定」（首页 15s `homeBoundedRequest` 超时的直接来源）。
  按 Spec `project-page-progressive-loading.md` **精确重指、不是放宽**：首页 `openTechProject()` 里
  `/workflow`、`/summary`、`/cost-review` 一个都不许再出现，必须交代 `primary_action`（有阶段目标
  直接跳）与 `restore=1`（无目标交工作台恢复）两条出路；工作台那半条 R3 断言
  （`/workflow` + `/summary` + `techStageFromProject(…, …, …)`）**逐字保留**，真实取数只是从首页
  挪到工作台。函数内逐字写明旧断言为何过期、判据为何没放宽，并给
  `docs/specs/tech-history-restore-real-stage.md` 补附录 A（唯一被取代的是 R3 的首页半边）。
- 验证：全量复跑 → **`Ran 6970 … OK (skipped=28)`**（零红）；重指后该模块与新增模块 24 项通过；
  `报价首页.html` 内联脚本（2052 行）、`app.js` / `assembly-integration.js` / `tech-workbench.js`
  的 `node --check`、改动 Python 的 `py_compile`、`git diff --check` 全过。
- 未提交/推送/部署、未浏览器或线上耗时验收；旧历史消费者仍兼容使用原接口，不声称全部消费者已优化。用户未跟踪文件保留。

## 534. `## 533` 的提交、双远端推送与 34 部署记录（10-9）

- 提交 `5720a3b`（`## 533 首页项目轻量分页与工艺工作台渐进加载；全量回归收口`），入库 12 个文件：
  新 Spec `project-page-progressive-loading.md` 与其 9 项测试、`main.py`、`project_access.py`、
  `store.py`、`app.js`、`assembly-integration.js`、`tech-workbench.js`、`报价首页.html`、
  `tech-history-restore-real-stage.md` 附录 A、重指的 `test_tech_history_restore_real_stage_red.py`
  与 changelog。不入库未跟踪项不变：`拆分程序/`、`.~南京锂能DA梳理.xlsx`。
- 提交前复跑：全量 `unittest discover -s tests -p 'test_*.py'` → **`Ran 6970 … OK (skipped=28)`**；
  `报价首页.html` 内联脚本（2052 行）与 `app.js` / `assembly-integration.js` / `tech-workbench.js`
  的 `node --check`、改动 Python 的 `py_compile`、`git diff --check` 全过。
- 双远端：GitLab 与 GitHub 的 `ytbz` 均回读为 `5720a3b798db0de1fb576a6991d67497f7e52b94`
  （`ea59762..5720a3b`），与本地 HEAD 三方一致。
  · 本机当日**解析不到** `gitlab.boulderaitech.com`（`scutil --dns` 只有 8.8.8.8 / 1.1.1.1，
    `ssh` 报 `Could not resolve hostname`）：改问内网 DNS `172.16.99.114` 得到真实地址
    `172.16.5.150`，以 `HostKeyAlias=gitlab.boulderaitech.com` 直连该 IP 完成推送。
    未改仓库 remote 配置、未写 `/etc/hosts`、未改任何凭据；推送后按 sha 回读确认。
- 34 部署：`bash scripts/deploy_34_bare.sh ytbz`，`a3ed335 → 5720a3b`（纯快进）；
  `build.commit=5720a3b798db0de1fb576a6991d67497f7e52b94`、`branch=ytbz`、
  `deployed_at=2026-10-09T09:30:19+0800`，8010 pid=`3445930`；`/api/health`、`/`、8012 `/api/health` 全 200。
- 部署后核对（隔离端到端 `verdict=ok`）：ODA 27.1 主转换器直出、`converter_role=primary`、
  `fallback_used=false`；酒盒八步 8/8、零件 263 件（`closed_ratio=0.510`）、可算 13 / 可挤出 13；
  圆盘盒八步 8/8、零件 312 件（`closed_ratio=0.840`）、可算 101 / 可挤出 262；两条权威实样路线
  `confirm=confirmed`；隔离自检未写运行目录（78 → 78）。
- 部署后核对（**本批真实收益，只读**）：6 个改动文件在 34 与本机逐字节一致；HTTP 取回的
  `报价首页.html`（174,105 B）含 `loadTechProjectPage`×4、`page_size=`、`restore=1`、`primary_action`。
  在 34 上以 `DATA_DIR=tech_app/tech_data` 复测 PE1：旧口径 `visible_projects(mine)` 42 条
  **16.93s** → 新口径 `visible_projects_page(mine, page=1, page_size=6)` 6/42 条 **0.003s**；
  `all` 第 2 页 0.003s；搜索 `q=圆盘盒` 0.003s（命中 7 条）；轻量行不含 `has_ir`，
  `card=None`、`turns=None`、`list_detail_pending=True`（不再为列表挂卡片而整份解析大 JSON）。
- 未做：浏览器视觉/交互验收（本批改的是加载时序与分页）；未改任何生产数据、未重解析任何已有图纸；
  门禁两项人工项仍未代签。能力声明不变：**DWG 编排能力完成，真实转换能力未验收**。

- 2026-10-10 包装工艺/成本结果可见性修正（本地，未部署/推送）：4.1区分待测算、版本失效待重算与真实零元；逐件比较历史/当前输入，尺寸、材料、组成或归属变化仍失效，不直接改写版本。业务件选中后只读已保存工序及状态，工艺按钮统一业务编码，避免查询几何碎片；POC整单成本重算自动同步同版单件材料费，保持材料费投影口径。新增模块缺失红测已实际失败，修复后5项新增及相关回归共87项通过，JS语法/diff检查通过；未进行浏览器视觉验收或线上数据重算。
- 2026-10-10 手动CAD候选重叠选择（本地，未部署）：核对线上酒盒BP13候选2与BP05候选虽然ID不同但共享cmp:35/43/755，旧顺序占用规则拒绝保存且曾撤销BP13自动归属。人工选择改为允许重叠并记录重叠件，不清除其他件选择/尺寸/组成；前端候选可选、显示重叠提示。自动归属仍保留顺序占用检查，无效候选仍拒绝。新增红测实际复现拒选后修复，正确人工选择只用于评测，不写死候选编号或酒盒坐标。
- 2026-10-10 用户指定酒盒BP03保持当前、BP13候选2：通过线上业务API将BP03 region:DWG-P05保存为manual；BP13被线上旧占用代码400拒绝，未保存、不改BP05。识别改进本地加入有CAD证据的完整多组成排序、最高分失败后尝试后续候选、自动选择保护人工归属，前后端评分保持一致；新增两项红测实际失败后实现。不以指定编号/尺寸硬编码、不宣称跨图准确率；部署授权单独请求，未擅自部署。

- 本轮候选修复及识别排序修改获用户授权部署34；上线状态以最终健康及BP13持久化读回为准。

- 2026-10-10 包装一键工艺推荐真实执行修复（本地，未部署）：旧批量入口仅打开面板，未提交任务，导致无新工艺/无执行气泡。改为业务件POST+串行轮询，有效工艺保留、过期重算，逐件失败继续并汇总；单件/批量均提供prompt与runId，复用现有用户回声、Agent任务卡及看板桥。旧工艺可只读展开而非隐藏。新增红测实际失败后修复，另加Node模拟真实POST/轮询行为验证；未授权线上重算、未宣称浏览器验收完成。

- 用户明确授权将本轮一键工艺修复部署34，但禁止代运行；仅检查健康和静态资源，不提交任何工艺任务。

- 2026-10-10 线上读取性能修复（用户授权修改并部署）：文件JSON缓存按inode/mtime_ns/size失效，限制32条与128MB源文件字节预算，副本隔离；get_meta/get_doc/list_metas/list_audit不再持全后端写锁读文件，原子写和审计读改写锁保留。当前业务清单只复制最新版，历史不删除；单件工艺复用当前清单、空几何身份不读大几何文档。批量已有工艺读取立即显示运行中，30秒超时明确报错、不覆盖旧工艺。红测实际复现缓存缺失/全局锁阻塞后修复，部署结果随后读回；未自动重解析或提交推荐任务。此前上线为本地提交+直接服务器bundle部署，未远程push。
