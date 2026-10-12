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
