# 几何 / 2D 结果的「逐件版本粒度」：Spec / 红测口径（第四批）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_per_part_result_staleness_red.py`

## 0. 一句话结论

现在的结果过期判定只有**一整份 IR 哈希**：

- `tech_app/backend/main.py:5019`（`GET /api/projects/{id}`）用
  `geometry_result.source_ir_hash != _digest_value(当前 IR)` 直接判定整份 3D / 2D **过期并把整份文档
  置成 `None`**；
- `tech_app/backend/main.py:2200`（`_geom_for_part`，工艺 / 成本取几何属性的入口）用同一个整份哈希，
  于是**别的零件被改一下，这个零件的几何属性就取不到了**；
- `tech_app/backend/storage/store.py:375` 的 `save_ir()` 每次保存 IR 都无条件
  `derived_results_stale = true`。

后果：改 P-005 的一个尺寸 / 数量 / 名称，`P-001 ~ P-004` 已生成的 STEP / STL / 视图会一起被判过期、
整份 3D 视图清空，用户被迫全量重跑。这与第 1 批（逐件容错）、第 2 批（逐件补录）的方向一致，
但粒度停在「整份」这一步。

本批把过期判定改成**逐件指纹**，并明确「哪类字段变了、失效什么」。

## 1. 现状证据（只读检查）

| 位置 | 现状 |
| --- | --- |
| `main.py:5019-5038` | `geometry_stale` / `drawings_stale` 由整份 `source_ir_hash` 决定；真则 `geometry: None` / `drawings: None` |
| `main.py:2200-2206` | `_geom_for_part` 先比整份哈希，不等就返回 `None`（工艺 / 成本的几何属性来源） |
| `store.py:369-386` | `save_ir()` 无条件把 `derived_results_stale` 置真 + 撤销 12 个阶段的确认 |
| `main.py:1646/1701` | 批量 `/generate` `/drawings` 只写顶层 `source_ir_hash`，逐件条目没有任何来源指纹 |
| `main.py:2180-2193` | 单件重生成只 `_upsert_part` 覆盖该零件条目，不刷新任何来源信息（顶层哈希于是长期「不可信」） |

## 2. 术语

- **形状指纹 `part_fingerprint(part)`**：只由「决定实体形状」的内容决定 —— `part_id` + `features`
  （顺序、类型、数值）。改名称 / 数量 / 材料 / 公差 / 型号 / 备注都不影响它。
- **属性指纹 `part_attribute_fingerprint(part)`**：本批只覆盖 `material.spec` 与 `material.density`
  —— 它决定条目里的 `mass_g`（体积 × 密度）。这是本批唯一「形状没变、派生数值会变」的字段。
- **逐件过期**：结果条目自带的指纹与当前 IR 里该零件的指纹不一致。
- **整份过期**：整份结果都无法逐件判断（零件增删 / 结构变化 / 来源资料替换 / legacy 无指纹），
  只能整批重生成。

## 3. 契约

### C1 零件指纹与逐件来源

新增一个纯函数模块（建议 `tech_app/backend/services/part_versions.py`，函数名可自定）：

```python
def part_fingerprint(part: dict) -> str            # 形状指纹
def part_attribute_fingerprint(part: dict) -> str  # 属性指纹（本批 = material）
```

要求：
1. 规范化后再哈希（字段顺序不影响、数值统一按数值比较）；同一份内容必须得到同一个串，非空。
2. 改 `features` 里任何尺寸 / 类型 / 顺序 / 数量 → 形状指纹变。
3. 改 `name` / `quantity` / `model_no` / `tolerance_general` / `role` / `parent_id` / 备注 →
   形状指纹**不变**。
4. 改 `material.spec` / `material.density` → 属性指纹变、形状指纹不变。

`/generate`、`/drawings` 落库的每个 part 条目新增：
`source_part_hash`、`source_attr_hash`、`generated_at`（ISO 时间串）。

### C2 读时装饰：逐件过期标记

`GET /api/projects/{id}` 返回的 `geometry` / `drawings` 文档里，每个 part 条目**新增（读时计算，
不写回存储）**：

```json
{
  "part_id": "P-005", "ok": true, "step_url": "…", "stl_url": "…",
  "source_part_hash": "…", "source_attr_hash": "…", "generated_at": "…",
  "stale": true, "stale_reason": "geometry",
  "stale_attributes": []
}
```

- `stale_reason`：`""` / `"geometry"` / `"material"` / `"legacy"` / `"ir_replaced"`；
- 材料变化：`stale` 保持 `false`（3D / 2D 文件仍然有效），但 `stale_attributes: ["mass_g"]`；
- 过期条目**不得清空** `step_url` / `stl_url` / `views` / `dxf` —— 文件还在，用户要能下载与对比。

`artifact_status` 新增（既有 `geometry_stale` / `drawings_stale` 必须保留）：

```json
{
  "geometry_stale": false, "drawings_stale": false,
  "geometry_parts_stale": ["P-005"], "drawings_parts_stale": ["P-005"],
  "stale_attributes": {"P-005": ["mass_g"]},
  "legacy_fingerprints": false,
  "stale_reason": "" 
}
```

### C3 整份隐藏收窄（本批最关键的一条）

`geometry_stale` / `drawings_stale` 为真（即整份 `geometry` / `drawings` 返回 `null`）**只剩三种情况**：

1. 来源资料被替换（`meta.input_revision` 与结果生成时不一致 / 解析被重置）；
2. IR 的零件集合或结构发生变化（零件新增 / 删除 / `part_id` 变化 / 总成层级变化）——
   逐件对不上号，必须整批重生成；
3. legacy 结果文档没有任何逐件指纹，且顶层 `source_ir_hash` 与当前 IR 不一致（无法逐件判断）。

**单纯的字段修改（尺寸 / 数量 / 名称 / 材料 / 公差 / 型号）一律不隐藏整份文档**，
只把相关零件标 `stale` / `stale_attributes`。

### C4 字段依赖表（本批落地的口径）

| 变化字段 | 形状指纹 | 属性指纹 | 该零件结果条目 | 其他零件 | 整份 |
| --- | --- | --- | --- | --- | --- |
| `features`（尺寸 / 类型 / 顺序 / 个数） | 变 | 不变 | `stale=true` / `stale_reason="geometry"` | 不受影响 | 不隐藏 |
| `name` | 不变 | 不变 | 不标 stale | 不受影响 | 不隐藏 |
| `quantity` | 不变 | 不变 | 不标 stale | 不受影响 | 不隐藏 |
| `material.spec` / `material.density` | 不变 | 变 | 不标 stale，`stale_attributes=["mass_g"]` | 不受影响 | 不隐藏 |
| `tolerance_general` / `purpose` / `role` / 备注 | 不变 | 不变 | 不标 stale | 不受影响 | 不隐藏 |
| `model_no` | 不变 | 不变 | 不标 stale | 不受影响 | 不隐藏 |
| 零件新增 / 删除 / `part_id` 变化 | — | — | — | — | `*_stale=true` |

> 数量 / 材料对「BOM、成本、工艺」的影响不在本批（见第 6 节），本批只保证它们**不连坐几何 / 2D**。

### C5 `_geom_for_part` 逐件判断

`main._geom_for_part(project_id, part_id)`（工艺、成本读取几何属性的入口）改为只比较**该零件**的形状指纹：

- 别的零件被改 → 本零件仍然拿得到 `bbox / volume_mm3 / mass_g`；
- 本零件自己的尺寸变了 → 返回 `None`（沿用既有语义，让上游提示先重生成）；
- 材料变了 → 仍然返回，但 `mass_g` 与当前材料不再对应（本批在 C2 里以 `stale_attributes` 暴露）。

### C6 单件重生成只刷新该件

`POST /api/projects/{id}/parts/{part_id}/regenerate`：

- 只把该零件条目的 `source_part_hash` / `source_attr_hash` / `generated_at` 更新为当前值；
- 其他零件条目的指纹、URL、数值**一字不动**；
- 顶层 `source_ir_hash` **不再被单件重生成刷新**（保持原值），避免「整份哈希不可信」——
  逐件指纹才是判据。

### C7 迁移兜底（legacy）

- 老结果文档（只有顶层 `source_ir_hash`、没有逐件指纹）：`artifact_status.legacy_fingerprints = true`；
  - 顶层哈希与当前 IR 相同 → 判为有效（不隐藏），并保持可读提示「下次生成后升级为逐件指纹」；
  - 顶层哈希不同 → 整份 stale（沿用既有行为），但 `stale_reason` 必须能说明是 `legacy` 判断不出来。
- 不做批量改写历史数据：只在**新生成 / 单件重生成**时补写逐件指纹。

### C8 前端

- `openProject()` 之后，零件清单每行按 `artifact_status` 显示过期标记：
  「结果已过期」/「质量待重算」；过期零件仍可点开 3D / 2D（文件在），只是带标记；
- **某个零件过期不得清空整份 3D 视图**（第 1 批已让成功件立即可看，这里再保证「过期件不连坐」）；
- `app.js` 的 `?v=` 在同一批里继续 bump。

### C9 不放松任何既有契约

- 既有字段一个不删：`source_ir_hash`、`artifact_status.geometry_stale` / `drawings_stale`、
  `parts[].ok / step_url / stl_url / volume_mm3 / mass_g / bbox / warnings / error / views / dxf`；
- 权限、`_assert_ir_unchanged` 并发保护、审计、`sync_geometry`、路由集合一律不变；
- 不得为了让测试变绿而放宽第 1 / 2 批红测与 `test_tech_backend_capability_preservation_red.py`。

## 4. 允许修改范围

- `tech_app/backend/services/`（新增零件指纹模块，或用既有模块承载同名能力）；
- `tech_app/backend/main.py`（`/generate`、`/drawings` 的 payload、`regenerate_part`、
  `_geom_for_part`、`GET /api/projects/{id}` 的 `artifact_status`）；
- `tech_app/backend/storage/store.py`（`save_ir()` 的 `derived_results_stale` 语义收窄；
  `save_geometry_result` / `save_drawings_result` 如需补写指纹）；
- `tech_app/frontend/app.js` + `index.html`（`?v=`）。

## 5. 本批不做（留给下一批，红测也不覆盖）

- 数量 / 材料 变化对 **BOM / 工艺 / 成本** 的逐件失效与重算提示；
- `invalidate_confirmations()` 从「整批 12 个阶段」收敛到「按字段子集」；
- 报告版本（3.1 / 3.2）与成本快照的逐件版本；
- 成本 / 工艺批量 partial 语义与「仅重试失败项」。

## 6. 验收（红测清单）

红测文件：`tests/test_tech_per_part_result_staleness_red.py`
（真实 CadQuery + `TestClient` + 临时 `DATA_DIR`，不联网、不碰线上数据）

1. 结果条目带 `source_part_hash` / `source_attr_hash` / `generated_at`，且非空。
2. 改 P-005 尺寸：`geometry_parts_stale == ["P-005"]`，P-001 ~ P-004 不在其中；
   整份 `geometry` 仍返回（`geometry_stale == false`），P-005 条目 `stale_reason="geometry"`。
3. 同一场景 2D 同理（`drawings_parts_stale == ["P-005"]`，整份仍返回）。
4. 改 P-005 数量：两个 `*_parts_stale` 都为空，且 P-005 条目不带 stale。
5. 改 P-005 名称：同上（不失效几何 / 2D）。
6. 改 P-005 材料：3D / 2D 不标 stale，但 `stale_attributes["P-005"] == ["mass_g"]`。
7. 改 P-005 公差：不失效任何东西。
8. 形状指纹对尺寸敏感：改尺寸前后 P-005 的 `source_part_hash` 必须不同。
9. 逐件判断：改 P-005 尺寸后 `main._geom_for_part(pid, "P-001")` 仍返回几何属性，
   `main._geom_for_part(pid, "P-005")` 返回 `None`。
10. 单件重生成只刷新该件：P-001 条目的指纹与 URL 一字不动，P-005 的指纹刷新到当前值，
    顶层 `source_ir_hash` 不被刷新。
11. 零件新增 / 删除：整份 `*_stale == true`（必须整批重生成），而不是只标一个零件。
12. legacy 文档：顶层哈希与当前 IR 相同 → 不隐藏（`geometry` 非 null）、`legacy_fingerprints == true`；
    哈希不同 → 整份 stale 且带可读原因。
13. 既有契约保留：`artifact_status.geometry_stale / drawings_stale` 仍在；`/generate`、`/drawings`、
    `/parts/{part_id}/regenerate` 路由仍在；`source_ir_hash` 字段仍在。
14. 前端：零件行显示过期标记、`?v=` 已 bump。

## 7. 测试命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_tech_per_part_result_staleness_red -v
node --check tech_app/frontend/app.js
python3 -m unittest discover -s tests -p 'test_*.py'
```
