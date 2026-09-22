# 图纸零件的业务角色：未映射清单读得回来 + 人工映射有生产入口（第 6 层）

血缘：`docs/specs/e2e-packaging-dwg-quote-tech-continuity.md` §4.4（`role=unknown` 的 DWG 零件
**不得**按行号/面积顺序自动贴 BOM 角色）落地之后暴露出来的第一道**人工**缺口。

状态：**已实现**（红测 `tests/test_packaging_part_role_manual_mapping_red.py` 21 条全绿）

## 0. 一句话目标

`role=unknown` 时 BOM 行的业务角色保持 `unbound` 是**对的**；但今天
「哪些行还没映射」读不回来、「人工映射」没有任何生产入口 —— 于是 §4.4 要求的
"必须先完成人工映射"**永远做不完**，业务角色这一栏永远是空的。
本层只补这两件事：**看得见** + **做得动**，并且**绝不**把它做成"猜一个角色填上去"。

## 1. 线上证据（34，2026-09-22，项目 `648d09d57f6f` / 需求单 `REQ-648D09D57F6F`）

| 读法 | 真实结果 |
| --- | --- |
| `GET /api/projects/648d09d57f6f/requirement/packaging-parts`（SM1） | 64 件；`summary.role_known_ratio = 0.0`；`stats.by_role = {"unknown": 64}` |
| 零件行 | `DWG-P01` … `DWG-P64`；`layers = ["0","DESIGN"]`（真实客户图，图层名无语义） |
| `GET /api/projects/648d09d57f6f/requirement/packaging-bom`（SM1） | 32 行，其中 `box_part` 11 行；行上 `size_source.dwg_binding` 只有 `pairing_basis`/`size_source`/`size_quality`，**没有** `role_value`/`binding_method`/`bound_by`；读接口里没有 `role_unbound`/`role_unbound_total` |

代码级事实（本机 HEAD）：

- `packaging_parts.bind_rows()` **已经**返回 `role_unbound` / `role_unbound_total`
  （`packaging_parts.py`），但 `packaging_bom._bind_parts()` 只取 `items` 与 `pairing_review`
  两项，**把未映射清单丢掉了** —— 与当年 `pairing_review` 被丢掉是同一个缺陷形状；
  因此 `load_bom()` 的输出里也从来没有这两个键。
- `packaging_bom.BINDING_METHODS` 里的 `manual_mapping` 至今**没有任何触发点**：
  `grep -rn "role-map\|manual_mapping\|role_unbound" tech_app/backend/main.py tech_app/frontend/app.js`
  0 命中 —— 和 `save_version()` 当年"有函数没入口"一样。
- 卡片第 6 步的快照（`GET /wf/card/step-data?session_id=71c5a1c26619&step_no=6`，2026-09-22 实测）
  里那张「图纸拆出来的零件（64 件）」只有 8 列：`零件号`/`名称`/`材料`/`厚度(mm)`/`尺寸来源`/
  `轮廓状态`/`展开长(mm)`/`展开宽(mm)` —— **根本没有「角色」这一列**，也就是说
  "这一件的角色还没映射"在用户看得见的那张卡片上**完全不可见**。
- 结论：§4.4 只关掉了"自动贴"这条错路，**没有给对的路**，也没让"还没映射"这件事被看见。
  对的路必须是人工映射。

## 2. 数据口径（逐条）

1. **未映射行**：`bom_category ∈ ("box_part","optional_part")`，且行上 `part_role ∈ ("", "unbound", "unknown")`，
   且这一行确实绑到了零件（有 `part_code`，或 `size_source.dwg_binding` 非空）。
   `material` / `process` / `finished` / `packaging` / `tooling` 行**不进**清单。
2. **候选角色只来自确认盒型的部件模板**：`kb_repo.packaging_part_templates(box_type_code)` 每行的
   `component`，按模板顺序去重、丢掉空值。**不许**来自模型、自由文本、`item_name` 拆词。
3. **映射只改角色与留痕**：`part_role` 与 `size_source_json.dwg_binding.*`；
   `length_mm` / `width_mm` / `height_mm` / `material` / `material_code` / `status` / `locked` /
   `item_name` / `quantity` 一个都不许动。
4. `binding_method` 只能取 `packaging_bom.BINDING_METHODS` 闭集里的值；人工映射 = `manual_mapping`。
5. **幂等**：同一行 + 同一角色重复提交 = 无变化（`changed=false`，`mapped_at` 不变，不重复写审计）。
6. **改绑**：换一个角色时**必须**保留旧角色与旧时间（`binding_evidence.superseded_role` /
   `superseded_at` / `superseded_by`），审计里 `superseded=true`；不许静默覆盖。
7. **红线不变**（`e2e-packaging-dwg-quote-tech-continuity.md` §4.4）：除了人工映射这一条路，
   **任何**路径都不许把 `part_role` 从 `unbound` 变成具体角色名 —— `reject_unknown_role_autobind()`
   继续生效，`bind_rows()` 的角色判定逐字不改。
8. **重建不丢映射**：`build_bom()` 必须重放已保存的映射（`apply_saved_role_map()`），
   重算 BOM 之后人工映射仍然在行上（否则"映射完再算一次 BOM 就没了"，等于没做）。

## 3. 纯函数契约（红测直接驱动这些，不许连库）

```python
ROLE_MAP_DOC_KEY = "packaging_bom_role_map"       # meta 文档通道，与 PAIRING_DOC_KEY 同范式

def role_candidates(part_templates: Any) -> list[str]
def role_map_status(items: Any, *, box_type_code: str = "", part_templates: Any = None,
                    role_map: Any = None) -> dict
def apply_role_mapping(items: Any, *, item_key: str, part_code: str, role: str,
                       candidates: Any = None, actor: Any = None, note: str = "",
                       mapped_at: str = "", history: Any = None) -> dict
def role_map_doc(project_id: str) -> dict                    # 读不到给 {"by_requirement": {}}
def save_role_mapping(project_id: str, requirement_no: str, row: dict) -> None
def role_candidates_for(project_id: str, requirement_no: str = "") -> dict
def apply_saved_role_map(project_id: str, requirement_no: str, items: list) -> list
```

`role_map_status()` 返回（键必须齐）：

```json
{"engine_version": "packaging_bom_role_map_v1",
 "candidates_source": "confirmed_box_type",
 "box_type_code": "YT-DWG-WINE-700ML",
 "role_candidates": ["面纸", "灰板"],
 "unbound_total": 11,
 "mapped_total": 0,
 "items": [{"item_key": "WINE-P01", "item_name": "左盖面纸", "bom_category": "box_part",
            "part_code": "DWG-P01", "part_role": "unbound",
            "reason": "role_unknown:DWG-P01", "role_candidates": ["面纸", "灰板"],
            "size_source": "component_bbox", "outline_status": "open",
            "mapped": false, "mapped_by": "", "mapped_at": "", "note": ""}]}
```

`apply_role_mapping()` 返回（键必须齐）：

```json
{"items": ["<新的 items 列表；不改入参>"],
 "changed": true,
 "record": {"item_key": "WINE-P01", "part_code": "DWG-P01", "role": "面纸",
            "previous_role": "unbound", "superseded_role": "", "mapped_by": "PE1",
            "mapped_at": "2026-09-22 08:30:00", "note": "",
            "binding_method": "manual_mapping"},
 "audit": {"action": "workflow:packaging_bom_role_mapped", "item_key": "WINE-P01",
           "part_code": "DWG-P01", "role": "面纸", "previous_role": "unbound",
           "by": "PE1", "superseded": false}}
```

失败口径（`packaging_bom.BomError`，`status_code` / `code` 逐字固定）：

| 条件 | status_code | code |
| --- | --- | --- |
| `role` 空 / `unknown` / `unbound` | 400 | `role_required` |
| 给了候选、但 `role` 不在候选里 | 400 | `role_not_in_candidates` |
| `item_key` 不在 `items` 里 | 404 | `item_not_found` |
| `part_code` 与行上的零件不一致 | 409 | `part_mismatch` |

## 4. 接口契约

1. `GET /api/projects/{pid}/requirement/packaging-bom/role-map`
   —— 读；路径参数写 `{pid}`（**只能一个路径参数**，不顶掉批次 7 的"需求相关读路由"基线）；
   返回 `{"role_map": <role_map_status() 的输出>}`；纯读，不判写权限。
2. `POST /api/projects/{project_id}/requirement/packaging-bom/role-map`
   —— 写；`_require(user, packaging_bom.BOM_WRITE_ROLES, "需要工艺经理、工艺技术总监或管理员权限")`；
   body `{"requirement_no": "", "item_key": "...", "part_code": "...", "role": "...", "note": ""}`；
   成功后：`save_role_mapping()` 落盘 + `store.audit(project_id, "workflow:packaging_bom_role_mapped", ...)`
   + 返回 `{"role_map": <status>, "bom": <load_bom()>}`；重复提交同一角色 → 200 且 `changed=false`。
3. `load_bom()` 输出**新增**两个键：`role_unbound`（清单，没有时 `[]`）与 `role_unbound_total`（整数）；
   `_bind_parts()` 不再丢 `role_unbound`。
4. `build_bom()` 在 `_bind_parts()` 之后、落库之前调用 `apply_saved_role_map()`。
5. 前端（`tech_app/frontend/app.js`）：BOM/零件面板必须显示「角色未映射 n 行」并能逐行选候选角色提交；
   提交成功后刷新未映射计数与 BOM 行。

## 5. 允许修改范围

1. `tech_app/backend/services/packaging_bom.py`（**主要落点**）
2. `tech_app/backend/main.py`（两个路由 + 请求模型）
3. `tech_app/frontend/app.js`（未映射计数 + 逐行候选提交）
4. `tech_app/backend/services/packaging_parts.py`：**只允许**把已经算出来的
   `role_unbound` / `role_unbound_total` 透传补齐，不许改角色判定
5. `changelog/changelog_9_21_25.md`（按周记录）

## 6. 禁止事项

- 不许自动推断、猜、或用模型补角色；不许把 `item_name` 拆词当角色；不许放宽
  `reject_unknown_role_autobind()` 的任何一条判据。
- 不许改 `BINDING_METHODS` 的既有取值（只能新增，不能替换）；不许把 `manual_mapping` 之外的
  写法当留痕。
- 不许动数据库 schema、不许加表；映射走 meta 文档通道（`get_doc`/`put_doc`）。
- 不许改尺寸 / 材料 / 状态 / 锁定；不许在映射里写生产数据。
- 红测只读源码 + 驱动纯函数，不连 PG、不发 HTTP、不写任何文件。

## 7. 红测

`tests/test_packaging_part_role_manual_mapping_red.py`（实现前必须失败；只读源码 + 纯函数）：

- **A 看得见**：A1 `role_candidates()` 去重/保序/丢空；A2 `role_map_status()` 只列未映射的部件行、
  带 `reason` 与候选、`material`/`process` 行不进清单、已映射行只进 `mapped_total`；
  A3 `packaging_bom.py` 里 `role_unbound` 不再被丢（源码级）；A4 `load_bom()` 输出含两个键。
- **B 写得进**：B1 合法映射 → `changed=true`、`part_role` = 候选名、`binding_method=manual_mapping`、
  `bound_by` = 操作者、证据带 `note`；B2 尺寸/材料/状态/锁定逐字不变、入参不被改；
  B3 同角色二次提交 `changed=false` 且 `mapped_at` 不变；B4 改绑保留旧角色。
- **C 拒绝口径**：C1 `role_not_in_candidates`(400)；C2 `role_required`(400，空/`unknown`/`unbound`)；
  C3 `item_not_found`(404)；C4 `part_mismatch`(409)。
- **D 生产入口**：D1 两个路径常量与路由在 `main.py` 逐字出现（读路由只有一个路径参数）；
  D2 写路由带 `BOM_WRITE_ROLES` 门禁；D3 `main.py` 里真的调用了 `role_map_status(` 与
  `apply_role_mapping(`；D4 审计动作名 `workflow:packaging_bom_role_mapped` 在 `packaging_bom.py`。
- **E 重建不丢**：E1 `build_bom()` 体内出现 `role_map` / `apply_saved_role_map`（源码级）。
- **F 面板**：F1 `app.js` 出现 `role-map` 与「未映射」文案。
- **G 红线不破**（**这一组今天就是绿的**，它是护栏不是缺口：实现前后都必须绿，实现时不许为了
  让它绿而放宽 §4.4）：G1 `packaging_parts.py` 里 `reject_unknown_role_autobind(` 调用点仍在；
  G2 `UNBOUND_ROLE` / `BINDING_METHODS` / `manual_mapping` 常量仍在且 `apply_role_mapping`
  不接受 `unknown`/`unbound`/空角色。

实测红基（2026-09-22，`./open-claude/.venv/bin/python -m unittest
tests.test_packaging_part_role_manual_mapping_red -v`）：`Ran 21`，A/B/C/D/E/F 六组 **19 条全红**
（`role_map_status`/`apply_role_mapping` 不存在、两个路由 0 处、`load_bom` 无两个键、
`build_bom` 无重放、面板无入口），G 组 2 条**本来就是绿的**（红线护栏）。

## 8. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_manual_mapping_red -v
./open-claude/.venv/bin/python -m unittest \
  tests.test_e2e_packaging_dwg_continuity_red \
  tests.test_packaging_parametric_bom_red \
  tests.test_packaging_parts_extraction_red \
  tests.test_packaging_parse_to_downstream_seams_red
```

## 9. 本层不做的（边界）

- 不解决"64 件里 4 件未闭合所以不可算"（`odd_endpoints`，另批）；
- 不做"一件对多行/多件同角色"的正式对应表（要业务签字，另批）；
- 不动 34 的部署、推送与版本（那些是另一条独立指令）。

## 10. 实现记录

### 10.1 纯函数（`packaging_bom.py`，Spec §3 逐键照做）

- `ROLE_MAP_DOC_KEY` / `ROLE_MAP_ENGINE_VERSION="packaging_bom_role_map_v1"` /
  `ROLE_MAP_ACTION="workflow:packaging_bom_role_mapped"` / `ROLE_UNBOUND_VALUES`；
- `_role_binding()` / `_row_role()` / `_row_part_code()`：角色的读口径收在一处
  （`dwg_binding.role_value` 优先，其次行上的 `part_role`；零件号取 `dwg_binding.part_code`）；
- `role_candidates()`：只取模板 `component`，按模板顺序去重、丢空值（不猜、不拆词）；
- `role_map_status()`：只列 `box_part`/`optional_part` 且角色仍是空/`unknown`/`unbound`
  且**确实绑到了零件**的行；`mapped_total` 把已映射行算在清单之外；`reason=role_unknown:<零件号>`；
- `apply_role_mapping()`：失败口径 `role_required`(400) / `role_not_in_candidates`(400) /
  `item_not_found`(404) / `part_mismatch`(409)；只改 `part_role` +
  `size_source_json.dwg_binding.*`（`role_value`/`role_candidates`/`binding_method=manual_mapping`/
  `bound_by`/`mapped_at`/`binding_evidence.note`），**不改入参**（`copy.deepcopy`）；
  同角色二次提交 `changed=false` 且 `mapped_at` 不变；换角色写
  `binding_evidence.superseded_role/superseded_at/superseded_by` 且审计 `superseded=true`。

### 10.2 读得回来（Spec §4.3）

- `_bind_parts()` 不再丢 `role_unbound`：返回三元组 `(items, pairing_review, role_unbound)`，
  `build_bom()` 把它落进 `ROLE_MAP_DOC_KEY` 文档（`unbound` 段）；
- `load_bom()` 新增 `role_unbound` / `role_unbound_total`，按**当前行现算**
  （`_role_scope()`），与清单永远一致，没有时给 `[]` / `0`；
- `build_bom()` 在落库前调 `apply_saved_role_map()` 重放人工映射（Spec §2.8）——
  重算不会把映射算没；配对换了零件时不套旧映射（`part_mismatch` 直接跳过）。

### 10.3 生产入口（Spec §4.1/§4.2）

- `GET /api/projects/{pid}/requirement/packaging-bom/role-map`（纯读，一个路径参数）；
- `POST /api/projects/{project_id}/requirement/packaging-bom/role-map`
  （`packaging_bom.BOM_WRITE_ROLES` 门禁；成功 → `save_role_mapping()` 落 BOM 行 + 文档 +
  `store.audit("workflow:packaging_bom_role_mapped")` + 返回 `{role_map, changed, record, bom}`；
  重复提交同角色 → 200 且 `changed=false`，不重复写审计）。

### 10.4 面板（Spec §4.5）

- `tech_app/frontend/index.html`：#packagingPartPanel 里新增 `#packagingRoleMap` 区；
- `tech_app/frontend/app.js`：`loadPackagingRoleMap()` / `renderPackagingRoleMap()` /
  `submitPackagingRoleMap()`，挂进 `refreshPackagingParts()`；显示「角色未映射 n 行」，
  逐行 `<select>` 候选 + 提交，提交后刷新计数与 BOM；候选**只来自后端**，前端不拼第二份清单。

### 10.5 实跑

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_manual_mapping_red
  → Ran 21 OK（实现前 19 红 / 2 护栏绿）
./open-claude/.venv/bin/python -m unittest \
  tests.test_e2e_packaging_dwg_continuity_red tests.test_packaging_parametric_bom_red \
  tests.test_packaging_parts_extraction_red tests.test_packaging_parse_to_downstream_seams_red
  → 全绿
node --check tech_app/frontend/app.js → OK
```

### 10.6 边界

未改角色判定（`reject_unknown_role_autobind()` 逐字未动）；未改 `BINDING_METHODS` 既有取值；
未动数据库 schema（映射走 `size_source_json` + meta 文档通道）；未改任何 `tests/`。
