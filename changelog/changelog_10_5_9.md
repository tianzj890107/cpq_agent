# 变更日志（10-5 ~ 10-9）

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
