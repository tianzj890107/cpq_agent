# 包装 DWG 拆件必须可泛化，下游只消费可信事实

状态：Spec + 红测（已实现）（原状：红基 `Ran 9 … FAILED (failures=1)`，唯一红是 §2.2 第 4 条的顺序不变性；`## 474` 已落地，落点与实测见 §8）

红测：`tests/test_packaging_dwg_generalization_and_downstream_trust_red.py`

修订：`packaging-wine-dwg-parts-and-downstream-truth.md` 的28件精确断言只定位为**已知演示样本回归**，
不得外推成任意包装DWG的通用规则，也不得用样本答案参与生产推理。

## 0. 裁定

28件《酒盒 报价资料》是测试侧金标，不是生产解析器输入，也不是所有包装图纸的通用规则。

正确目标是：

```text
生产：DWG证据 → 可观测件 / 有规则依据的推断件 / 待确认件 → BOM草稿 → 工艺与成本
测试：系统输出 ⇄ 样本金标，计算 precision / recall / 尺寸准确率 / 下游可信覆盖率
```

金标可以证明算法是否进步，但不得通过文件名、SHA、固定坐标、固定实体ID、28件名称表或金标尺寸参与
生产推理。酒盒演示允许作为已知样本回归，但不得要求DWG凭空生成图中不可观测的BOM行。

## 1. 输出事实分层

每个业务部件必须声明：

- `observed`：文字、尺寸、轮廓、引线、图块等DWG证据足以直接支持；
- `inferred`：由通用且已审核的盒型结构规则推断，必须带规则号及触发事实；
- `pending_confirmation`：存在候选但名称、尺寸或材料不足，等待人工确认。

不得把测试金标中的 `bom_only` 行伪装为 `observed`。例如磁铁若图中没有磁吸文字、几何或装配说明，
DWG解析器不得因为金标有磁铁就输出其15×5×2尺寸；只能由通用结构规则生成“磁性件待确认”，或在后续
BOM对账时报告缺项。

## 2. 防过拟合护栏

生产树必须满足：

1. 不包含酒盒样本SHA、conversion id、固定文件名分支或整套28件名称/尺寸指纹。
2. 不读取 `tests/fixtures/gold`、报价资料BOM或按样本哈希命中的答案快照。
3. 给解析器传入金标、知识库伪答案或不传，生产结果逐字相同。
4. CAD实体、文字、尺寸和证据字典仅改变遍历顺序时，语义结果不变；编码可重新编号，但名称、事实状态、
   尺寸及证据语义必须一致。
5. 消融某类真实证据后，相关结果必须降级、消失或改变证据声明；不得仍然神奇地得到完全相同答案。
6. 通用规则不得引用样本坐标、实体ID或金标行号；规则应能在至少两个不同样本或合成夹具上成立。

## 3. 评测而非凑数

酒盒金标仅在测试侧计算：

- 名称 precision / recall；
- 确认尺寸准确率；
- 材料准确率；
- 父组误当叶子件数量；
- 可观测件、推断件、待确认件分布；
- 工艺可用覆盖率和成本可信覆盖率。

当前阶段保留已有最低线：名称 recall ≥ 22/28、precision ≥ 0.85。不能把最低线改成生产代码里的
`count == 28`。演示报告可以展示“金标28、识别25、待对账3”，不能把25说成28，也不能为凑28造件。

确认尺寸必须可复核：按长宽可交换比较，已标 `confirmed/unfolded` 的尺寸与测试金标误差应≤1mm；
不准确就降级为 `unconfirmed`，不能只因存在两个数字而算成功。

## 4. 工艺推荐可信门禁

1. 工艺只能自动消费叶子件、确认尺寸和可信材料。
2. `bbox_only`、总图包围盒、标题框尺寸等只能用于候选定位，必须返回
   `PACKAGING_BUSINESS_PART_SIZE_UNCONFIRMED`，不得自动排工艺。
3. 缺材料继续返回 `PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`。
4. 批量工艺允许部分成功，并必须报告成功、待确认、失败及逐件原因；HTTP 200不等于全部完成。

## 5. 成本可信门禁

1. 成本只能消费确认尺寸和与部件角色相容的材料参数。
2. `face_paper_gsm`只允许兜底明确属于面纸/衬纸且材料类别为纸的行；不得兜底灰板、衬板、EVA、磁铁。
3. 缺尺寸、材料或费率时可以生成草稿及缺口，但 readiness 不得为 formal，不能发送报价或沉淀案例。
4. 每条成本行必须留存部件编码、尺寸来源、材料来源、规则/费率版本和缺口状态。

## 6. 多样本要求

酒盒之外，至少逐步建立圆盘盒、天地盖、书型盒、抽屉盒、折叠盒测试集；每类至少包含正常样本和一种
缺文字/镜像/外购件场景。未达到多样本覆盖前，不得把单样本精确命中宣传为“通用DWG拆件能力”。

## 7. 本批完成标准

- 金标仍只在测试侧，生产结果对金标注入零敏感。
- 顺序扰动不改变语义结果，文字证据消融能引起合理降级。
- 酒盒继续满足最低precision/recall，但不强制生产输出28。
- 被标成确认的尺寸全部经得住误差验收。
- bbox尺寸不能进入工艺；缺材料灰板不能使用225g面纸克重进入成本。
- 页面和API如实展示识别、推断、待确认及下游可用覆盖率。

## 8. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 474`）

改**一处**（`tech_app/backend/services/packaging_business_part_resolver.py`，只动"锚点以什么顺序交出去"，
不动任何判据）：

| 契约 | 落点 | 实现 |
| --- | --- | --- |
| §2.2 第 4 条（遍历顺序不改变语义） | `extract_text_anchors()` 的返回 | `return sorted(anchors, key=lambda item: (_text(item.get("entity_id")), _text(item.get("raw_text"))))` —— 锚点按图纸自己的稳定标识（实体句柄生成的 `entity_id`，`cad_ir` 解析文本时也按它排过）排一遍再交出去 |
| §2.2 第 1/2/3 条（生产不吃样本指纹 / 金标 / 传不传金标都不变） | 未动 | A1 / A2 红基即绿（生产侧无 SHA、无 conversion id、无 `tests/fixtures/gold`；`attachments` / `kb` / `seed_path` 一律不进推理，仍只记 `refused_sources`） |
| §2.2 第 5 条（消融证据必须降级） | 未动 | A4 红基即绿：抽掉文字后语义不再逐字相同 |
| §1 / §3 / §4 / §5（事实分层、评测口径、工艺与成本可信门禁） | 未动 | B1–B3 / C1–C2 红基即绿（`truth_state` 闭集、金标只做度量、确认尺寸不过误差线即降级、bbox 尺寸与面纸克重不得越权） |

### 8.1 根因（实测，不是推断）

红基 `Ran 9 … FAILED (failures=1)`，唯一红是 A3。把真样本的两套排版（原图 + 镜像）逐条对账后，
真因是「同一件名在图上出现两次时，`_derived_rows()` 取**第一条**」＝**谁先被遍历到算谁**：

| 件名 | 倒序后变成 | 真因 |
| --- | --- | --- |
| `内盒3衬纸` | 尺寸 119.9×81.0（`region:cmp:63`，带 `size_dimension`）→ 绑不上（只剩 `text_anchor`，`bbox_only`） | 代表锚点从 `ent:model:1204`（原件附近）换成 `ent:model:5AFA`（镜像，最近轮廓在 937.8mm 外且不同形） |
| `内盒3面纸` | 123.381×30.525 → 绑不上 | 同上（`1202` → `5C4E`） |
| `右盖外盒外层衬板` | 221.762×492.62 → 443.523×492.62 | 镜像锚点的最近轮廓是**跨了两件**的那块（`region:cmp:130`） |
| `左盖外盒外层衬板` | 221.762 → 222.062 | 同上（`region:cmp:801` → 另一块） |
| `右盖外盒里层灰板1/2` | 族名漂移成 `右盖外盒里层灰板` / `右盖盒背灰板` | 分组要求"每个成员轮廓互不相同"（`plan_family_groups()`），镜像那份没绑上轮廓 → 分组条件不成立 |

件名代表锚点一旦按顺序漂移，锚点位置就变，`_assign_outlines()` 的"最近轮廓"跟着变，尺寸、证据类目与
父子分组全部连坐 —— 所以这不是"测试太严"，而是**同一份图纸能给出两套答案**。

### 8.2 为什么不算放宽 + 反向对照

- **期望值与断言一字未改**（本批只改生产代码一处；`tests/` 下任何文件都没动，也没有新增 skip）。
- **plain 运行输出与改前逐字节相同**：对照改前/改后同一份 IR 的完整 `resolve_business_parts()` JSON
  （`sort_keys=True`）→ `diff` 为空。原因也说得清：`cad_ir` 解析文本时本来就按 `entity_id` 排，
  这一处只是把"排序"从调用方的隐含前提变成函数自己的契约。
- **反向对照（本机实测）**：把这一处还原成不排序的 `return anchors` ⇒
  `Ran 9 … FAILED (failures=1)`，只红 A3；加回即 `Ran 9 … OK`。

### 8.3 复跑命令与结果（本机 `./open-claude/.venv/bin/python -W ignore -m unittest`）

```text
tests.test_packaging_dwg_generalization_and_downstream_trust_red
    Ran 9 … OK                                        （红基 Ran 9 … FAILED (failures=1)）
tests.test_packaging_wine_dwg_parts_and_downstream_truth_red \
tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red \
tests.test_packaging_business_part_*_red（10 份）tests.test_packaging_business_parts_*_red（7 份）
    Ran 290 … OK                                      （金标 recall/precision、28 件名集、确认尺寸误差线都没被打回）
```

超出 A3 要求的加强验证（本机，一次性脚本）：

```text
把 geometry.components 也倒序            → 语义逐字节相同（EQUAL: True）
6 组随机 shuffle（entities / texts / dimensions / layers / evidence / geometry.components）
                                        → 6/6 语义逐字节相同（mismatches: []）
```

### 8.4 未做 / 边界

- 未动任何判据：轮廓指派半径、嵌套最外层规则、族分组三条件、`TRUTH_STATES` 闭集、工艺与成本门禁、
  结构规则表与规则号，全部逐字未改；
- A1 按字面扫的是 `tech_app/**/*.py`（不含 `tests/` 与 `docs/`），也就是"生产侧不得出现样本指纹"；
- 顺序不变性这一条，本批钉住的是 `entities` / `texts` / `dimensions` / `layers` 四个列表与 `evidence`
  字典（A3 倒序）+ `geometry.components`（§8.3 的加强验证）；几何零件文档（`regions_from_geometry_parts()`）
  与 `rects` 的输入顺序未逐项钉，它们只在"按 id 取值 / 任一条命中即确认"的语义下参与判定。
- **§7 最后一条（页面与 API 如实展示"识别 / 推断 / 待确认"三档 + 下游可用覆盖率）本批未做，
  也不在本红测覆盖内** —— 本机只读核对（不是推断）：
  · `truth_state` 目前只活在 `packaging_business_part_resolver` 的返回值里；
  · `packaging_parts.business_parts_document()` 的行形状是固定的（`business_part_code` / `name` /
    `authority` / `geometry_binding` / `thumbnail`），**不携带** `truth_state`，
    `packaging_drawing_flow/steps.py` 的 `detail` 复制清单也没有 `truth_state_counts`；
  · 前端（`tech_app/frontend/app.js`）全文没有 `truth_state` 渲染位；
  · 现成的读数在 `resolve_business_parts()["detail"]`：`truth_state_counts` / `inferred_total` /
    `pending_confirmation_total`（外加既有 `size_source_counts` / `unbound_total` 等）。
  改行形状属于「另起一批」（`packaging-wine-dwg-parts-and-downstream-truth.md` §7.4 已把它挂账），
  需要新的红测 + 落地，本批不擅自扩面。
