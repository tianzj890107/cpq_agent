# 包装业务零件的多组成部分闭环

日期：2026-10-08。用户本轮明确要求 Spec、红测及直接实现；仅本地修改，不包含部署。

## 1. 问题与目标

当前 component_ids 多选只是归属，declared_sections 只是文本声明；统一 bbox 长宽和第一个闭合件入口会丢失组成，或把排版空白计入成本。一个业务零件可由多个独立物理部分组成，每部分又可有外轮廓、孔、压线等多个 CAD 分量。

目标是 `业务零件 → sections[] → component_ids/entity_ids/尺寸/用量/证据`。一个名字不要求对应一条闭合线，不按金标件数、固定名称或固定坐标生成结果。

## 2. 数据及识别

- sections 中保存稳定 section_id、name、component_ids、entity_ids、bbox（仅定位）、estimated_size、confirmed_size、quantity、source。保留父业务编码，实体必须来自当前项目完整证据。
- 文本复合名称声明多个部分时，未找到的部分必须显式 pending，不能因选择一个形状而称全部完成。普通单一名称也允许多个物理部分，不强行要求顿号名称。
- 多轮廓候选由名称归属、真实块实例、同排版格和几何证据产生；模型只选择已存在候选，不生成坐标/尺寸。多组成候选的数据不能在候选表/保存阶段被丢弃。
- 包含于同一完整外轮廓的孔/内部线不是新增物理部分。不能仅凭外接矩形包含就判孔，必须核对轮廓点；无法证明时保持独立/待核对。排模副本及其他名称的图形不得自动加入。
- 既有人工归属优先。默认最高证据候选确认归属，但尺寸独立核对；无法证明完整性时留缺口。人工可在零件详情新增、移除、重分组部分，保存显式完整集合。

## 3. 确认、兼容及版本

- 新增组成保存 API，只修改当前业务零件。拒绝未知分量、实体不属于所选分量、重复使用实体、占用他件实体、空部分、非法用量、跨项目引用；整体失败不得部分写入。
- 每部分尺寸确认要求操作者、证据说明、有限正数长宽。CAD 标注只能空间验证到该部分才确认；bbox 不等于尺寸。复合件不接受旧的整件单组长宽确认接口。
- 修改归属撤销受影响部分尺寸；未改部分保留。文档哈希包含组成、尺寸和用量，旧 BOM/工艺/成本随版本变动判过期。旧单部分数据和非图纸对照表保持兼容，无删除/迁移历史。

## 4. UI

- 零件仍为一个父条目；详情显示组成清单，每部分名称、两位小数 `长 × 宽 mm`、用量及编辑入口。
- 唯一正方形 CAD 预览支持“全部组成”与单个部分，按明确实体画，不按 union bbox 收入邻件。所有部分保留原始 CAD 坐标及图层颜色。
- 原有紧凑按钮样式，不增加 3D 挤出。多部分的工艺/成本走业务零件入口，禁止只操作首个几何件；缺某部分尺寸时具体显示哪个部分缺什么。

## 5. 下游

- BOM 按物理部分生成唯一 item_key，保留父编码、section_id、名称、尺寸、实际用量与版本证据；父业务件数不因展开成 BOM 行增加，不同时算父行和子行。
- 单件材料费逐部分使用既有公式引擎计算后乘该部分用量，再合计。未知尺寸、材料、费率或某部分公式失败不能跳过并给完整金额；可报告逐部分缺口，但完整总额应为空。
- 工艺推荐收到全部组成的材料、尺寸、实体引用、用量；逐部分生成并保留归属，禁止只对第一部分推荐；汇总仍挂在原父业务零件。
- 整单成本消费逐部分 BOM 行，与单件组成口径一致，数量为“每套用量 × 每部分用量”，不重复乘报价批量。

## 6. 红测与验收

合成测试覆盖两异尺寸圆形/平移、普通单件名多部分、孔与分散组成、同图邻件/排模副本、缺失声明部分、未知/重复/他件实体、独立尺寸、版本失效、首件截断保护、逐部分工艺与成本及持久化读回。测试不联网、不读生产库、不使用 BOM 作为识别输入。

功能闭环完成不等于所有未知 DWG 可 100% 自动识别。仍有歧义时界面必须支持修正且不伪报完整；真实上下方案验收与部署单独授权。

## 7. 实现及验证记录

- 已实现 `packaging_sections.py`、组成与逐部分尺寸保存 API、前端组成编辑/整件及单部分 CAD 预览，以及实际工艺/材料成本任务、BOM 的逐部分消费。每部分可指定材料，空值继承父件；不按候选顺序擅自分配“内/外”等语义名称。
- 同一区域的嵌套刀线轮廓不能直接当作两块材料；标记组成歧义并阻断完整成本，需显式调整组成。孔和内部工艺线通过真实轮廓包含及图层角色归属，不只看包围框。
- 新模块 `tests/test_packaging_multipart_sections_complete_red.py` 共 30 项。先观察未实现断言失败，再实现；覆盖真实解析器、真实任务闭包、持久化读回与执行前端纯函数。模型/费率外部调用使用隔离替身，不连接生产数据库。
- 相关 22 个模块组合回归：`Ran 323 tests in 53.979s / OK`。前端模块语法与 `git diff --check` 通过。本轮未跑全仓测试，未启动服务、未提交、未推送、未部署。
- 四个旧测试中的接口数量冻结与旧派生件数量冻结已按本 Spec 更新：新增独立组成尺寸 API、单实体闭合圆进入候选是本次明确变更。保留真实 CAD 引用与尺寸证据断言，不用固定答案件数验收。
- 旧 LibreDWG 缓存只读诊断：酒盒 28 个业务行、0 个多组成行；圆盘盒 55 个业务行、5 个多组成行。模型调用 0，未将 BOM 输入解析。这些数字仅证明真实输入可走新结构，不证明清单完整或组成正确，也不代表当前服务器 ODA 结果。

## 附录 A：本批同步的既有冻结值（2026-10-08）

本 Spec 允许的变化（单实体闭合圆、一名多形、共用 sections 写入、逐部分尺寸确认）会动到四份旧红测的
**精确计数**，全部按「精确重指 + 逐字留因」处理，没有一条改成下限或跳过：

| 旧红测 | 旧 → 新 | 说明 |
| --- | --- | --- |
| `test_packaging_business_part_size_must_be_confirmed_by_dimension_red` | `DERIVED_TOTAL` / `SIZED_TOTAL` 圆盘盒 36 → 46；`assertEqual` 保留 | 识别数量按本 Spec §1 精确重指 |
| `test_packaging_business_part_cost_by_authority_size_red` | `packaging-business-parts/` 8 → 9、`/size-confirm` 1 → 2 | 新增 `sections` 写入与逐部分确认两个入口，另加 `assertIn('/sections${suffix}')` |
| `test_packaging_business_part_process_entry_red` | 同上 | 同上 |
| `test_packaging_business_part_process_by_authority_route_red` | 同上 | 同上 |
| `test_packaging_business_part_basis_in_panel_red` | 同上 | 同上（全量回归补记，`## 528`） |
| `test_packaging_authority_workbook_upload_red` | 同上 | 同上（全量回归补记，`## 528`） |
| `test_quick_quote_entry_routing_packaging_isolation_red` | `test_quick_submission_stays_on_homepage` → `test_quick_submission_enters_instance_workspace` | 「留在首页」被 `packaging-quote-workspace-render-routing.md` §2 取代（用户本轮路由要求） |
| `test_packaging_online_business_closure_red::test_cached_quote_repopulates_new_step_tables` | 只补 `document` / `renderPackage` 最小桩 | 缓存命中分支现在也画结构化快照（`packaging-quote-workspace-render-routing.md` §3）；`applied!==1` 断言未动 |

未改动：几何绑定入口 `/geometry-binding`（仍 1）、`/auto-bind-candidates`（仍 1）、
两笔账与原因码闭集、尺寸确认的证据要求。
