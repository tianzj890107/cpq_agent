# 业务部件的「尺寸」必须分得开：有独立证据的量测，vs 只有几何包络的猜测

状态：Spec + 红测（已实现）（原状：实现提示词只在会话交付、业务实现不在本批；红基见 §6；
`## 462` 已落地，落点与实测见 §7）
红测：`tests/test_packaging_business_part_size_must_be_confirmed_by_dimension_red.py`
承接：`packaging-business-parts-must-come-from-all-drawing-evidence.md`（`## 459` 算出 `size_confirmed`）、
`packaging-business-parts-outline-bbox-broken-link.md`（`## 461` 先把断链接上）、
`packaging-bom-part-size-provenance.md`（BOM 行上的 `size_quality` 两档）
本批 changelog 条目号：`## 462`

## 0. 本轮现场结论（真实跑，2026-09-23 本机，隔离 `DATA_DIR`）

夹具与口径：把 `## 461` 的断链先摆平（真零件文档的 `outline.bbox` 提到行顶层 `bbox`，
让绑定先跑通），两份真样本各跑一次 `resolve_business_parts()`：

```text
酒盒  ：26 行全部 derived，26 件有尺寸 —— 其中确认 12 / **猜测 14**
        行上 reasons 全空、`evidence.size_quality` 不存在；
        detail 里只有一笔 parts_with_size_total = 26
圆盘盒：39 件有尺寸 —— 确认 8 / **猜测 31**
        （27 行没绑上图的另算，原因是 no_outline_evidence）
```

也就是说：`evidence.size_confirmed` 这个布尔**已经算出来了**（`## 459`），但没有任何读得出来的出口：

- 行上**没有**质量档 —— 页面 / BOM / 成本只能看到"这件有尺寸"；
- `reasons` 里**没有**"尺寸未确认"这个码 —— 未确认的 14 / 31 件在明细里与确认件同形；
- 详情里**只有一笔** `parts_with_size_total`（26 / 39），把"量出来的 12"与"估出来的 14"合成一个数，
  审阅者拿它核不出任何东西。

**为什么这值得单独一批**：`## 459` 的成绩单写的是"带尺寸 26/26"，
但真正有标注证据的只有 12 件 —— 这个差别今天在产物里**一处都看不出来**。
尺寸进了 BOM / 成本就是钱：一个包络猜测被当成量测值，错了没人知道是猜的。

## 1. 需求

1. **行级质量档**：每一件业务部件行必须在 `evidence.size_quality` 给出机器可读的质量档，
   取值必须落在 `packaging_parts.SIZE_QUALITIES`（**导入**那个闭集，不许在这里再造一套字面量）：
   - `evidence.size_confirmed is True` → **`unfolded`**（有独立尺寸证据：同形尺寸标注 / 闭合轮廓）；
   - 其余（只有分量或环的包络、来源缺失、没尺寸）→ **`bbox_only`**。
   - 实现方式二选一（断言只看输出）：(a) 行级按 `size_confirmed` 直接给；
     (b) 让 `packaging_parts.size_quality_of()` 认得解析器这一套来源词 —— 但那必须同时保证
     `size_dimension` 映射到 `unfolded`，且**闭集不扩**。
2. **两笔账必须分开**：`detail["size_confirmed_total"]` 与 `detail["size_unconfirmed_total"]`
   （= 有尺寸但 `size_confirmed is not True` 的行数）必须给出，且
   **不变量**：`size_confirmed_total + size_unconfirmed_total == parts_with_size_total`
   （没尺寸的行不进这两笔）。
3. **未确认的行必须自己说出来**：新模块常量
   `REASON_SIZE_UNCONFIRMED = "outline_size_unconfirmed"`，只有未确认的行进 `reasons`，
   已确认的行**不许**带它；`detail.reasons_breakdown` 必须数得到它，件数与第 2 条的
   `size_unconfirmed_total` 一致。
4. **真样本规模门槛**（本机实测，留余量；这既是门槛也是护栏 —— 不许把确认检测改坏）：
   - 酒盒：确认 ≥ **10**（实测 12）、未确认 ≥ **10**（实测 14）
   - 圆盘盒：确认 ≥ **6**（实测 8）、未确认 ≥ **25**（实测 31）
5. **冻结面**：
   - `derived` / `partial` / `unbound` 的语义**一个字不改**（酒盒 derived 26、圆盘盒 39 不变），
     `parts_with_size_total` 不变 —— 本批只做**披露**；
   - `_region_is_size_confirmed()` 的容差、`_sizes_match()`、配对算法、`dimension_rects()` 都不改；
   - `packaging_parts.size_quality_of()` 的既有映射与 `SIZE_QUALITIES` / `UNFOLDED_SIZE_SOURCES`
     闭集不扩（护栏）；
   - BOM / 成本 / 工艺路线的公式与门禁不改；接口形状不改（只加键）；
   - 前端文案不在本批（现有"已定位"那句的人话口径另批）。

## 2. 需要签字的口径选择（不在本批，列出以免被当成漏项）

把**未确认尺寸**的行从 `derived` 降级成 `partial`（行仍给尺寸、仍留痕，只是不再叫"已定位"），
是更严的一种做法。它要同时修订三处已绿的期望值：

- `## 459` 的成绩单口径（`packaging-business-parts-must-come-from-all-drawing-evidence.md` §"带尺寸 26/26"）；
- `tests/test_packaging_business_parts_must_come_from_all_drawing_evidence_red.py` 的 `sized/total ≥ 0.5`；
- `## 461` 红测的真样本门槛（`derived ≥ 20 / 31`）。

所以这属于**产品口径决策**，需要签字后另立一批；本批先把差别**披露出来**，不改判定。

## 3. 非目标

- 不改进"哪条轮廓该配给哪一件"的判定质量（配错件的概率）。
- 不新增尺寸来源、不改标注解析（`dimension_rects`）。
- 不把 `unfolded` 这个档名换掉（它是下游既有闭集里的字；换名要动 BOM / 成本 / 面板一片）。

## 4. 验收命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_part_size_must_be_confirmed_by_dimension_red -v
    # 现状 Ran 15 … FAILED (failures=8)；实现后 Ran 15 … OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red \
    tests.test_packaging_parts_outline_red tests.test_packaging_parts_components_red \
    tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_drawing_flow_red
    # 现状 Ran 142 OK（不回归；工作区 skipped=2 / 隔离副本 skipped=7）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_must_come_from_the_drawing_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red \
    tests.test_packaging_business_parts_binding_size_source_red \
    tests.test_packaging_business_parts_must_come_from_all_drawing_evidence_red \
    tests.test_packaging_bom_part_size_provenance_red tests.test_packaging_bom_size_quality_accounting_red
    # 现状 103 条里 1 条是**已挂账**的旧护栏字面（`bom_part_size_provenance_red::B3` 的 stats 键集，
    # 见 `packaging-bom-size-quality-accounting.md` §"已记录的偏差（不改测试）"，属测试侧动作），
    # 其余全绿 —— 与本批无关
```

真样本：`tech_app/data/cad-ir-realsample/conversions/<cid>/converted.dxf`
（酒盒 `47c39dc1ab6738fc48c8`、圆盘盒 `a6140fbc4e9b8d2e9bee`；缺样本时红测自 `skip`）。

## 5. 现场数字

| 项 | 酒盒 | 圆盘盒 |
| --- | ---: | ---: |
| 业务部件行 | 26 | 66 |
| `derived`（= 绑上且长宽齐全） | 26 | 39 |
| 有尺寸的件 | 26 | 39 |
| 尺寸有标注证据（`size_confirmed=true`） | **12** | **8** |
| 尺寸只是几何包络（`size_confirmed=false`） | **14** | **31** |
| 行上 `evidence.size_quality`（现状） | 不存在 | 不存在 |
| 未确认件在 `reasons` 里说了什么（现状） | 什么都没说 | 什么都没说 |
| detail 的尺寸账（现状） | 只有 `parts_with_size_total=26` | 只有 `parts_with_size_total=39` |
| 尺寸标注矩形 `dimension_rects(ir)` | 107 | — |

## 6. 红基（本批实测，2026-09-23 本机隔离 `DATA_DIR`）

```text
tests.test_packaging_business_part_size_must_be_confirmed_by_dimension_red   Ran 15 … FAILED (failures=8)

红（8）：A1 行上没有 `evidence.size_quality`
        A2/A3 确认件与猜测件没有分开的两档（没有尺寸的行也没有档）
        B1/B2 `size_confirmed_total` / `size_unconfirmed_total` 不存在（两笔账合在一起）
        C1 `REASON_SIZE_UNCONFIRMED` 常量不存在
        C2 未确认的 14 / 31 件在 `reasons` 里什么都没说
        C3 `reasons_breakdown` 里数不到 `outline_size_unconfirmed`（0 ≠ 14 / 31）
护栏绿（7）：D1/D2 两档规模（酒盒 12/14、圆盘盒 8/31）；
            E1 derived 26 / 39 与有尺寸件数不变；E2 `packaging_parts` 侧质量档映射与闭集不变；
            E3 确认件必有 `size_dimension` 证据、未确认件必无；E4 确定性；E5 不改入参
```

口径说明：红基与不回归都在 **`HEAD 8f8b011` 的隔离副本**上量（`git archive 8f8b011` 解到临时目录、
把工作区里的真样本 `tech_app/data/cad-ir-realsample/` 复制进去、只放进本批的 Spec / 红测）——
工作区当时有并行会话正在改 `packaging_parts.py` / `packaging_business_part_resolver.py`，
直接在原工作区量会把别人的半成品算进来。

不回归（本批未改业务实现，逐条复跑）：

```text
tests.test_packaging_parts_extraction_red + packaging_parts_outline_red + packaging_parts_components_red
  + packaging_bom_business_parts_rows_red + packaging_drawing_flow_red          Ran 142 OK (skipped=7)
tests.test_packaging_parts_must_come_from_the_drawing_red
  + packaging_business_parts_and_cad_plan_view_red + packaging_business_parts_binding_size_source_red
  + packaging_business_parts_must_come_from_all_drawing_evidence_red
  + packaging_bom_part_size_provenance_red + packaging_bom_size_quality_accounting_red
      Ran 103 … 1 failure（`bom_part_size_provenance_red::B3` 旧护栏字面，**已挂账**、与本批无关）
      + 1 error（隔离副本缺客户 `酒盒 报价资料.xlsx`；原工作区不出现这个错）
```

本批只写 Spec + 红测：未改任何业务实现、未改既有测试断言、未起服务、未发 HTTP、未连 PG / 34、
未写业务数据、未 push / MR / tag / Release、未部署。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 462`）

只改了一个文件：`tech_app/backend/services/packaging_business_part_resolver.py`（本批只做**披露**）。

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §1.1 行级质量档 | `_size_quality_for()` / `evidence.size_quality` | 档名与闭集都取自 `packaging_parts`（`SIZE_QUALITY_UNFOLDED` / `SIZE_QUALITY_BBOX`），本模块不造第二套字面量；`size_confirmed is True` → `unfolded`，其余（含没尺寸）→ `bbox_only` |
| §1.2 两笔账 | `detail.size_confirmed_total` / `detail.size_unconfirmed_total` | 不变量 `confirm + unconfirm == parts_with_size_total`（没尺寸的行不进这两笔） |
| §1.3 未确认自证 | `REASON_SIZE_UNCONFIRMED` / 行 `reasons` / `reasons_breakdown` | 只有"有尺寸且未确认"的行背 `outline_size_unconfirmed`；已确认的行不许带；`reasons_breakdown` 里这一项 == `size_unconfirmed_total` |
| §1.4 真样本门槛 | 见下 | 酒盒 确认 12 / 未确认 14（门槛 10 / 10）；圆盘盒 确认 8 / 未确认 31（门槛 6 / 25） |
| §1.5 冻结面 | 未动 | `derived`/`partial`/`unbound` 语义、`parts_with_size_total`、`_region_is_size_confirmed()`/`_sizes_match()`/`dimension_rects()`、`packaging_parts.size_quality_of()` 的既有映射与闭集、BOM/成本/工艺公式 |
| §2 需要签字的选项 | 未做 | 未把"未确认尺寸"的行从 `derived` 降级成 `partial` —— 那要动三处已绿期望值，需业务签字后另立一批 |

真样本实测（真零件文档 + 把 `outline.bbox` 提到行顶层，与红测夹具同口径）：

```text
酒盒  ：derived 26 / 有尺寸 26；确认 12 / 未确认 14（门槛 10 / 10）
圆盘盒：derived 39 / 有尺寸 39；确认  8 / 未确认 31（门槛  6 / 25）
两笔账都满足 confirm + unconfirm == parts_with_size_total；reasons_breakdown 里
outline_size_unconfirmed 的件数与 size_unconfirmed_total 相等
```

复跑命令与结果（本机 `./open-claude/.venv/bin/python`）：

```bash
python -m unittest tests.test_packaging_business_part_size_must_be_confirmed_by_dimension_red
    # Ran 15 … OK（红基 Ran 15 … FAILED (failures=8)）
python -m unittest tests.test_packaging_parts_extraction_red \
    tests.test_packaging_parts_outline_red tests.test_packaging_parts_components_red \
    tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_drawing_flow_red \
    tests.test_packaging_parts_must_come_from_the_drawing_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red \
    tests.test_packaging_business_parts_binding_size_source_red \
    tests.test_packaging_business_parts_must_come_from_all_drawing_evidence_red \
    tests.test_packaging_bom_part_size_provenance_red tests.test_packaging_bom_size_quality_accounting_red
    # Ran 263 … FAILED (failures=1, skipped=2)
    #   唯一那条是**既有挂账**、与本批无关：`bom_part_size_provenance_red` B3 的 `stats` 键集冻结
    #   （多出 `size_quality`）。已用 `git stash` 去掉本批改动复跑确认：改前同样 FAIL（failures=1）。
    #   Spec §4 写的"现状全绿"与事实不符，以本行为准。
```

红测自身缺陷（如实记录）：本批红测钉的是"两档分得开 + 两笔账 + 原因码 + 真样本规模"，
**没有**校验"未确认的尺寸数值准不准"（配错件的概率），也没要求把未确认件降级 —— 那是 §2 待签字的选项。

未 push / MR / tag / Release / 部署，未连 PG、未起服务、未写业务数据。
