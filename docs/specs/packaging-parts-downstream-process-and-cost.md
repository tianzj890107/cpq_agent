# 包装图纸零件：跑通工艺推荐与成本测算（第 3 层）

血缘：承接第 1 层（真实轮廓）与第 2 层（可选中 + 右栏面板）。
本层解决**"点得动，但算不了：一律报『还没有零件』"**。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_parts_downstream_red.py`

## 0. 一句话目标

图纸零件能真正跑「生成工艺推荐」和「成本测算」：**复用既有算法**（`process.outline_process`、
`packaging_cost`），只补 id 映射与最低输入前提；前提不满足时**拒绝并说清缺什么**，绝不用默认值硬算。

## 1. 现状缺口（代码事实）

- `POST /api/projects/{project_id}/parts/{part_id}/process`（`main.py:2894`）第一句就是
  `store.load_ir(project_id)`：图纸链路把 CAD IR 写进**自己那份文档**，不回写这个 store →
  **404「请先解析(parse)得到 IR」**；即便有 IR，它按 `ir.parts` 找 `part_id`，而图纸零件只有
  `part_code`（`DWG-P01`）→ 第二道 **404「零件 DWG-P01 不存在」**。
- 工艺推荐入口判的是 `currentIR.parts`（`app.js:2860`）→ 图纸项目下恒为 `"还没有零件，请先完成图纸解析。"`，
  与左栏 64 行自相矛盾。
- 图纸零件缺**材料/厚度**，而 `process.outline_process(part, overall, geom, ...)` 需要 `Part` 与几何；
  今天直接把包围盒尺寸接上去等于拿错误输入算工艺。

## 2. id 映射（唯一口径）

- 零件文档每件新增 `part_id`，值 **= `part_code`**（例 `DWG-P01`）；**不改** `part_code`。
- 文档顶层新增 `part_id_namespace = "packaging_parts/1"`，并把 `part_id` 一起写进 `size_source` 同级的
  留痕处，便于后续跨层核对。
- 本版**不建**独立映射表（`part_id = part_code` 已是一一对应）；若后续要做"一个零件多次出现 ↔ 多个零件号"
  的正式对应表，另行一批，且必须业务确认。

## 3. 新增纯函数（`packaging_parts`）

```python
PART_ID_NAMESPACE = "packaging_parts/1"
PROCESS_REJECT_CODES = ("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                        "PACKAGING_PART_NOT_FOUND")

def as_ir_part(row: dict) -> Part        # 转成 tech_app.backend.models.ir.Part
def processability(row: dict, *, options: dict | None = None) -> dict
```

`as_ir_part(row)`：

- `part_id` = `row["part_id"]`；`name` = `row["name"]`；`quantity` = `1`；
  `role` = 第 1 层的 `role`；
- `material`：只有在能确定时才填（来自需求/属性；**不许猜**）；厚度字段名固定为 `thickness_mm`
  （浮点、单位 mm，来自需求/属性，**不许给默认值**）；
- `features`：**仅当 `outline_status == "closed"` 且厚度已知**时给
  `[plate(length=unfolded_length_mm, width=unfolded_width_mm, thickness=<已知厚度>)]`；否则 `[]`；
- `confidence`：`closed` 且有材料 → `>= 0.6`；`open` 或缺料 → `<= 0.4`；
- `provenance`：带 `entity_ids` / `outline_status` / `size_source`（可放 `doc_ref` 文本里）。

`processability(row)`（**路由必须先调它**）：

| 条件 | `ok` | `code` | 备注 |
| --- | --- | --- | --- |
| `outline_status != "closed"` | `False` | `PACKAGING_PART_NOT_CLOSED` | `missing_variables` 含 `outline` |
| 闭合但缺材料/厚度 | `False` | `PACKAGING_PART_MATERIAL_UNKNOWN` | `missing_variables` 列出具体字段（`material`、`thickness_mm` 里的缺失项） |
| 都满足 | `True` | `""` | 返回可直接交给 `outline_process` 的 `part` |

## 4. 路由（不碰既有技术路由）

```python
PACKAGING_PART_PROCESS_PATH = "/api/projects/{pid}/requirement/packaging-parts/{part_code}/process"
PACKAGING_PART_COST_PATH    = "/api/projects/{pid}/requirement/packaging-parts/{part_code}/cost"
```

- 写权限**直接引用** `packaging_match.BOX_MATCH_DECIDE_ROLES`（不另抄一份）。
- `process`：`part = as_ir_part(row)`；`processability` 不过 → 按表给 **409**（`retryable=false`）+
  `missing_variables`；否则 `process.outline_process(part, overall=None, geom=None, ...)`，
  返回与既有工艺路由**同形状**（`task_id` + 进度上报），**不复用**既有的
  `/parts/{part_id}/process`（那条绑定技术 IR）。
- `cost`：第一版只做**单件成本**；入口复用 `packaging_cost` 的既有能力（不得新写费率/公式），
  参数取自需求 + 零件文档；缺料同样按 `processability` 拒绝。

## 5. 前端

1. 面板（第 2 层新增的 `#packagingPartPanel`）里加两个按钮：「生成工艺推荐」「成本测算」。
2. 可点条件 = `outline_status == "closed"` 且 `processability.ok`；不满足必须**置灰 + 写明原因**
   （灰按钮必须自己说明为什么灰：`未找到闭合轮廓` / `缺材料或厚度：<字段>`）。
3. 点击后复用既有 `CadInlineAnalysis` 渲染到右栏分析区（`setRightPane("analysis", title)`），
   **不新建第二套工艺/成本渲染**。
4. 板级「一键生成全部工艺推荐」在图纸项目下必须**按零件文档计数**（不是 `currentIR.parts`），
   并对每件分别走 `processability`：不可算的件计入 `skipped` 并逐条说明，不再整批报"没有零件"。

## 6. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：`PART_ID_NAMESPACE`、`part_id` 字段、
   `as_ir_part`、`processability`、`PROCESS_REJECT_CODES`。
2. `tech_app/backend/main.py`：两个新路由（含常量与 409 码）。
3. `tech_app/frontend/app.js`：面板按钮 + 板级批量入口按零件文档计数。
4. 允许在 `packaging_cost` 增**只读入口**（若既有入口拿不到零件级输入），但**不得**改公式/费率/口径。

## 7. 禁止事项

- 不许改 `POST /parts/{part_id}/process` 的既有行为（视觉链路与既有项目不许受影响）。
- 不许把图纸零件写进 `store.save_ir()`（技术 IR 只由技术链路写）。
- 不许在缺料时用默认厚度/默认材料；不许用包围盒面积冒充零件面积喂成本。
- 不许新写第二套工艺/成本算法。
- 不许改 `tests/` 下任何既有文件；不许改语义层与门禁判据。

## 8. 红测

`tests/test_packaging_parts_downstream_red.py`（实现前必须失败）：

- A id 映射：每件有 `part_id == part_code`；文档顶层 `part_id_namespace == "packaging_parts/1"`。
- B `as_ir_part`：闭合 + 有材料 → `Part` 校验通过、`features[0]["type"] == "plate"`、`confidence >= 0.6`；
  缺厚度 → `features == []` 且 `confidence <= 0.4`。
- C `processability`：未闭合 → `NOT_CLOSED`；缺料 → `MATERIAL_UNKNOWN` + `missing_variables` 非空；
  齐备 → `ok`。
- D 路由：两个新路由已注册；写权限 `is` `packaging_match.BOX_MATCH_DECIDE_ROLES`；
  源码里路由**先调用** `processability`。
- E 既有技术路由不受影响：`/api/projects/{project_id}/parts/{part_id}/process` 仍在 routes 里，
  且该函数源码块不出现 `packaging_parts`。
- F 前端：面板两按钮存在；灰按钮原因文案含 `闭合轮廓` 与 `缺材料`；点击走 `CadInlineAnalysis`；
  板级入口按零件文档计数（不出现 `currentIR.parts` 作为图纸项目的唯一判据）。

## 9. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_red -v   # 全绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_process_route_red          # 不回退
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red            # 81 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_red_closure_red       # 14 OK(1 skip)
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red       # 32 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_red          # 第 1 层不能回退
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_panel_red            # 第 2 层不能回退
```

## 10. 实现期回写（2026-09-21）

本节只记录"实现期才暴露出来、Spec 原文没写死"的口径；上面 §2–§5 的契约一条未改。

1. **材料与厚度从哪来（§3 那句"来自需求/属性、不许猜"的落地规则）**：图纸零件不在技术 IR 里，
   没有"属性"可继承，所以取**图纸自己写着的标注**（`ir["texts"]` 里的「包装材料说明」与引出标注），
   两档、都留痕到 `row["material_source"] / row["thickness_source"]`：
   - 件级：距该件包围盒 `≤ max(25% 对角线, 50mm)` 的最近一条标注（`kind="part_note"`，
     记 `evidence_ref` 与 `distance_mm`）；
   - 图级兜底：**只在这张图上该字段只有一个候选值**时才用（`kind="drawing_note"`）——
     两个值以上就是有歧义，宁可不填。
   实测：`酒盒.dwg` 64 件里 4 件、`圆盘盒.dwg` 9 件里 1 件因此满足 `processability.ok`。
   取不到的件照 §3 拒绝（`PACKAGING_PART_MATERIAL_UNKNOWN`），绝不给默认料厚。
2. **成本入口复用既有契约**：`packaging_cost.compute_line("material", variables)` 算数字，
   结果映射进平台既有的 `CostAnalysis`（`models/cost.py`），`summary` 由既有 `cost.compute()` 生成 ——
   前端因此不需要第二套成本渲染。变量只取"能确定的两处"：零件文档（展开尺寸、标注里的克重）
   + 需求里已填的 `face_paper_gsm / ton_price / imposition_count / proof_base`；
   缺的一律不给默认值，由 `compute_line()` 自己回 `gap=missing_variable:<名>`。
3. **`inline-analysis.js` 增加 `context.endpointBase`（可选）**：默认仍是技术侧
   `/parts/{part_id}`，图纸零件传一个函数指向 `/requirement/packaging-parts/{part_code}`，
   渲染与任务轮询不改一个字。§5.3 的"复用既有 CadInlineAnalysis"因此是**真复用**，不是复制。
4. **工艺 / 成本结论第一版不落技术侧 store**（`store.save_process` / `store.save_cost` 都不写），
   免得图纸零件与技术 IR 零件在同一个文档里互相覆盖：两个 `GET` 如实回 `plan: null` /
   `analysis: null`（前端会说"尚未生成"）。这一点在交付回执里作为"未完成能力"声明。
