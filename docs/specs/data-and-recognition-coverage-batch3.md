# 真实数据与识别覆盖（批次 3）：区间审计 + 识别质量度量 + 路线编码诊断

血缘：承接 `packaging-box-type-matching.md`（尺寸区间与硬门槛）、
`packaging-dwg-parts-extraction.md`（零件提取：只吃 CAD IR，不读 BOM/金标）、
`packaging-cost-minimum-charge.md`（「数据不对先查数据、不要改代码」的既有先例）、
`kb-deploy-preflight`（`tech_app/tools/kb_deploy_preflight.py`，缺表/空表/demo 的 no-go 口径）、
`da-process-routing-live.md`（本地未提交的只读路线桥，本批只消费其**快照**，不改它）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测；业务实现由 Codex 在本地落地，按本文 §5 未提交/未推送/未部署，红测 11 项已全绿）
红测：`tests/test_data_and_recognition_coverage_batch3_red.py`
复核基线：2026-10-09 实测（§1 的每个数字都可复现）

## 0. 一句话目标

把「识别得准不准、区间数据对不对、工序编码能不能命中」从**口头判断**变成**可执行的只读报告**：
数据问题就报数据问题（不许拍脑袋给所有盒型补下限），识别问题就按维度拆开度量
（更不许按样本件数调参）。

## 1. 现状缺口（代码事实 + 10-9 实测）

### 1.1 T6：小盒匹配大盒已堵住，但「区间数据本身好不好」没人查

- 代码侧**已支持下限**：`packaging_match._dimension_size()` 逐轴比 `size_l_min/size_w_min/size_h_min`，
  越界即 `out_of_range`（硬门槛）；`tests/test_packaging_match_undecidable_and_size_guard_red` **23 OK**。
- 数据侧本仓种子是干净的：`da_seed_packaging.BOX_TYPES` 12 行，六项边界**齐全且 min < max**
  （10-9 实测：缺项 0、倒挂 0）。但**没有任何工具**能对**生产** `cpq_kb.kb_packaging_box_type`
  跑同一套不变量，也分不出「固定规格标准盒 / 可调尺寸盒型 / 只能当参考的案例」。
- 结论：本批要的是**只读审计**，不是再改匹配代码。

### 1.2 T14 + O6：识别质量没有任何度量工具

- 现有工具里**没有**任何 accuracy / precision / recall / 留出图相关实现（10-9 `grep` 实测为空）。
- 已有的是**门槛**不是**度量**：`tech_app/tools/packaging_parts_gate.py`（6 项在位性门禁）、
  `tests/fixtures/dwg_acceptance/*`（转换层金标：实体/图层）。
- 口径必须先立住：**「有尺寸证据的比例」不是「识别正确率」**；漏识别、误识别、归属、尺寸、
  证据覆盖、人工修正耗时是**六个不同指标**，不能揉成一个数。
- O6 是数据投入（留出图的业务答案键只能由人给）；本批交付**读答案键并对答案**的工具，
  答案键本身由你指定人标注。

### 1.3 T16：工序路线只有桥，没有诊断

- `tech_app/backend/services/da_process_routing.py`（本地未提交）只做「按产品编码或精确件名查询」，
  命中不到就如实 `unavailable`；`## 534` 已记录线上 27 个头、124 行、26 个包装路线全草稿、三类工时全空。
- **没有**任何只读诊断回答三件事：名称/产品编码/工序编码各自命中多少、有多少步骤是**孤儿**
  （有步骤没有头）、有多少头是草稿或工时为空。
- 结论：本批交付**离线诊断**（吃快照、出报告），不动 `da_process_routing`，更不许自动采纳模糊匹配结果。

### 1.4 T13：已经实现，本批只做验收（不重做）

- `scripts/import_da_kb_to_pg.py`：默认 dry-run（`main()` 里 `dry_run = not args.confirm`，
  实测 `parse_args([]).dry_run is False`、归一化发生在 `main`）、`--confirm` 才写、
  `--json`、源库 sha256 前后比对（导入器必须以只读方式打开源库）。
- `tech_app/tools/kb_deploy_preflight.py`：纯函数 `preflight(tables, env, kb_version)`，
  no-go 码含 `missing_tables` / `empty_required_table` / `kb_version_missing` / `demo_only` /
  `authority_missing` / `unclassified_rows` / **`box_type_missing_fit_clearance`** / `below_min_rows`。

## 2. 契约

### 2.1 T6：`tech_app/tools/packaging_box_type_range_audit.py`

纯函数 + 只读 CLI，不连库（吃 JSON/快照），不写任何东西。

- `AUDIT_VERSION = "packaging-box-type-range-audit/1"`
- `classify_sizes(row) -> "fixed_spec" | "adjustable" | "reference"`：
  - `fixed_spec`：三轴 `min == max` 且 > 0（固定规格标准盒）；
  - `reference`：任一轴缺 `min` 或 `max`（只能当参考案例）；
  - `adjustable`：其余（区间完整且 `min < max`）。
- `audit_box_types(rows) -> dict`：
  ```python
  {"version": AUDIT_VERSION, "total": int,
   "classes": {"fixed_spec": [code, ...], "adjustable": [...], "reference": [...]},
   "issues": [{"box_type_code": ..., "code": ISSUE_RANGE_MISSING... , "detail": str}, ...],
   "no_lower_bound": [code, ...],          # 任一轴 min 缺失
   "inverted": [code, ...]}                # 任一轴 min > max
  ```
- issue 码闭集：`range_missing`（缺 min 或 max）、`range_inverted`、`range_not_positive`（min 或 max ≤ 0）。
- CLI：`--source <json> [--json] [--strict]`；**审计永远退出 0**，只有 `--strict` 且 `issues` 非空才非零。
- 判据红线：**不许**因为「小盒匹配大盒」就去改 `packaging_match`；本工具只输出数据结论。

### 2.2 T14：`tech_app/tools/packaging_parts_accuracy.py`

纯函数 + 只读，**结构上不接受 BOM 作为提取输入**（只接受两个已算好的集合：`predicted` 与 `truth`）。

- `ACCURACY_VERSION = "packaging-parts-accuracy/1"`、`DEFAULT_SIZE_TOLERANCE_MM = 1.0`
- `load_holdout(folder) -> {"version": ..., "drawings": [{"drawing": name, "truth": [...]}, ...]}`
  读 `*.json` 答案键（业务侧标注）；文件缺失/空目录 → `drawings == []`，不抛异常、不编造。
- `score_parts(predicted, truth, *, size_tolerance_mm=..., manual_fix_minutes=None) -> dict`：
  - 匹配键：两边都有 `part_code` 时按 `part_code`，否则按 `name`；
  - `missed`（truth 未识别）/ `spurious`（识别出但 truth 没有）/ `matched_total`；
  - `attribution_ok` / `attribution_total`（已匹配件里 `role`/`group` 正确的数）；
  - `size_ok` / `size_checked`（`length_mm`、`width_mm` 都在 ±容差内）；
  - `evidence_covered` / `evidence_total`（**证据覆盖率**，与正确率分开）；
  - `manual_fix_minutes`：**原样透传**（可为 `None`），**绝不由程序推算**；
  - `summary`：只放计数；**不得**出现单一 `accuracy` / `precision` / `recall` / `正确率` 标量。

### 2.3 T16：`tech_app/tools/packaging_routing_diagnostic.py`

纯函数 `diagnose_routing(headers, steps, *, time_keys=("standard_seconds","labor_seconds","equipment_seconds")) -> dict`：

```python
{"version": "packaging-routing-diagnostic/1",
 "headers_total": int,
 "matched_by_code": [header_key, ...],       # 按 product_item_code 命中步骤
 "matched_by_name": [header_key, ...],       # 只能靠名称命中（口径未对齐的信号）
 "unmatched": [header_key, ...],             # 完全命中不到步骤
 "orphan_steps": [{"product_item_code": ..., "seq": ...}, ...],   # 有步骤没有头
 "draft_headers": [header_key, ...],
 "empty_time_headers": [header_key, ...],    # time_keys 全是空/0
 "verdict": "ok" | "attention"}
```

规则：不改入参（纯函数）、不连库、不联网、**不自动采纳**任何模糊匹配结果；
`unmatched`/`orphan_steps`/`draft_headers` 任一非空 → `verdict == "attention"`。

### 2.4 T13：验收（不新增代码）

红测里只放两条**守卫**（今天应为绿），把既有口径钉住：
1. `scripts/import_da_kb_to_pg.py`：不给 `--confirm` 时 `cpq_kb.import_from_sqlite(confirm=False)`；
2. `kb_deploy_preflight.preflight()`：生产环境 + 权威盒型缺 `fit_clearance` → 产出
   `box_type_missing_fit_clearance`。

## 3. 不做什么

- 不改 `packaging_match` 的匹配算法与权重（小盒问题已由 `size_guard` 覆盖）；
- 不给所有盒型统一补下限、不按样本件数或金标尺寸调参；
- 不把 BOM/答案键喂进提取链路（只在**对答案**阶段使用）；
- 不改 `da_process_routing.py`（并行会话产物）与 `workflow_stages.py`；
- 不做前端界面（本批全部是只读工具与报告）。

## 4. 允许修改范围

1. 新增 `tech_app/tools/packaging_box_type_range_audit.py`；
2. 新增 `tech_app/tools/packaging_parts_accuracy.py`；
3. 新增 `tech_app/tools/packaging_routing_diagnostic.py`。
（T13 只验收，不新增文件。）

## 5. 禁止事项

- 不许改 `tests/` 下任何既有文件（含本批红测）；
- 不许引入任何联网、写库、写生产目录的行为；
- 三个工具必须能从 JSON/内存快照驱动（红测不连 PG）；
- 不许把 `evidence_coverage` 当成「识别正确率」展示；
- 不许 commit / push / tag / Release / 部署。

## 6. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_data_and_recognition_coverage_batch3_red -v
./open-claude/.venv/bin/python -m unittest \
    tests.test_packaging_match_undecidable_and_size_guard_red \
    tests.test_packaging_parts_extraction_red -v        # 不回归（提取侧不得被答案键污染）
python tech_app/tools/packaging_box_type_range_audit.py --source <box_types.json> --json
python tech_app/tools/packaging_routing_diagnostic.py --source <routes.json> --json
```

## 7. 验收清单（本批不做、只登记）

- [ ] O6 留出图（5~10 张）由指定工程师标注，答案键含「名称 + 尺寸 + 图纸归属」；
- [ ] 是否把 `package_parts_accuracy` 接进 `packaging_parts_gate`（成为第 7 项门禁）——本批不动门禁；
- [ ] 路线诊断的报告口径是否需要同步给主数据维护方（组织动作）。

## 8. 实现记录（2026-10-09，Codex）

按 §2 契约逐条落地，只新增 §4 允许的三个只读工具，未改任何既有业务代码：

- `tech_app/tools/packaging_box_type_range_audit.py`：`classify_sizes`（fixed_spec / adjustable / reference）、
  `audit_box_types`（classes / issues / no_lower_bound / inverted，issue 码闭集
  `range_missing` / `range_inverted` / `range_not_positive`）；CLI `--source/--json/--strict`，
  审计永远退出 0，仅 `--strict` 且有 issue 才非零。吃 JSON，不连库。
- `tech_app/tools/packaging_parts_accuracy.py`：`load_holdout`（缺失/空目录 → `drawings==[]`）、
  `score_parts`（漏识别 / 误识别 / 归属 / 尺寸 / 证据覆盖分开度量；`manual_fix_minutes` 原样透传；
  `summary` 无任何单一正确率标量）。结构上只吃 `predicted` 与 `truth`，不接受 BOM。
- `tech_app/tools/packaging_routing_diagnostic.py`：`diagnose_routing`（按编码命中 / 只按名称命中 /
  完全命中不到 / 孤儿步骤 / 草稿头 / 工时空头，`verdict` attention|ok），纯函数、不改入参、不自动采纳模糊匹配。

实跑（2026-10-09，`./open-claude/.venv/bin/python -W ignore -m unittest`）：

- `tests.test_data_and_recognition_coverage_batch3_red`：改前 9 红（3×T6 / 3×T14 / 3×T16），改后 **`Ran 11 … OK`**（含 2 条 T13 守卫）；
- 不回归：`test_packaging_match_undecidable_and_size_guard_red + test_packaging_parts_extraction_red` → `Ran 55 … OK`；
- 本仓种子 `da_seed_packaging.BOX_TYPES` 12 行审计 → `issues==[]`、`adjustable==12`（与 §1.1 实测一致）。

§7 的三项（留出图人工标注、是否接进 7 项门禁、报告口径同步主数据方）仍留给后续批次/人工。
