# 规格：包装 BOM 与零件文档的版本绑定（重解析后 BOM 不许静默指旧图）

状态：Spec + 红测（已实现）（绑定留痕与读接口都带零件文档版本并披露漂移；落地见 §5）
红测：`tests/test_packaging_bom_parts_version_binding_red.py`

血缘：承接 `packaging-dwg-parts-extraction.md`（C7 行级回填 / §6.1「BOM 必须带出它是基于哪一版算的」）、
`packaging-bom-part-size-provenance.md`（尺寸来源一路带到底）、
`packaging-part-role-manual-mapping.md`（留痕口径）。
范式参照：同一仓里 CAD 侧已有现成做法 —— `tech_app/backend/main.py:5880 _decorate_result()`
读时装饰逐件过期状态（`parts_stale` / `stale_reasons`，不写回存储）。包装 BOM 这一侧没有对应物。

## 0. 一句话目标

BOM 行上那些"来自 DWG 零件"的尺寸，必须能回答**它是照哪一版零件文档配的**，以及
**现在这一版是不是还是那一版**；重解析出新零件后，读接口不许再把这些行当成"已确认结果"照旧展示。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 绑定留痕里没有零件文档版本

`tech_app/backend/services/packaging_parts.py:2366` `bind_rows()`：

```python
        size_binding = {
            "component_id": _text(part.get("component_id")),
            "part_code": _text(part.get("part_code")),
            "rule_id": BINDING_RULE_ID,
            "fallback_paired": bool(fallback),
            "original_missing_variables": original_missing,
            "pairing_basis": basis,
            "material_match": material_match,
            "size_source": _text(part.get("size_source")),
            "outline_status": _text(part.get("outline_status")),
            "size_quality": size_quality_of(part.get("size_source")),
        }
```

`parts` 这个入参**就是**零件文档（`packaging_parts.py:2033 save_parts()` 落的整份 `record`），
顶层带着 `parts_id` / `parts_hash`；`bind_rows()` 也把这两样从零件行里读得到。
但留痕一个字都不写 —— 落库到 `wip_packaging_bom_item.size_source_json` 的那一份，
**没有任何地方说明这一对尺寸是照哪一版图纸配的**。

`packaging_bom.py` 全文对 `parts_id` / `parts_hash` 的引用数是 **0**：

```
grep -c "parts_hash\|parts_id" tech_app/backend/services/packaging_bom.py   # → 0
```

也就是说：版本锚点是**算出来就丢**的（`packaging_drawing_flow/steps.py:318` 只在链路 detail 里
报了一次，BOM 这一路一次都没有）。

### 1.2 读接口不比对版本：重解析后旧尺寸照旧当"已确认结果"

`packaging_bom.py:921 load_bom()` 的 `source_versions`（`:952`）只有盒型匹配的三项：

```python
        "source_versions": {
            "box_type_code": ...,
            "engine_version": ...,
            "confirmed_by": ...,
            "confirmed_at": ...,
        },
```

没有零件文档这一路。后果是一条**静默的错误链**：

1. 用 酒盒.dwg 解析一版零件 → `build_bom()` 回填，行上写死当时的尺寸与 `component_id`；
2. 图纸被重新解析（改一个尺寸、换一份 DWG）→ 零件文档换了 `parts_id` / `parts_hash`，
   `packaging-parts` 读接口上已经是**新**零件；
3. 用户没有再点一次"生成 BOM"（他只是重解析了图纸）—— `load_bom()` 照旧返回**旧**尺寸、
   旧的 `component_id`，`status="computed"`、`missing_variables=[]`、`source="dwg_parts"`；
4. 2.1 上的零件树与 BOM 行因此**互相打架**，而两边看上去都是"已经算好的结果"，
   没有任何地方说得出"这些行指着一份已经不存在的零件文档"。

点进零件明细还会顺着 `component_id` 找不到那一件（新文档里没有这个 id），
现场表现是"零件在、点不动"。

### 1.3 历史行（`dwg_binding` 存在但没有版本）同样无从判断

本批之前落库的行都属于这一类：`size_source_json.dwg_binding` 存在（说明"这一行是图纸绑的"），
但没有 `parts_hash`。读接口现在既不说它过期、也不说它无从判断 ——
与 §1.2 是同一个洞的另一格。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_parts.py`
   - `bind_rows()`（`:2295`）：
     - `size_binding`（`:2366`）新增 `parts_id` / `parts_hash`，取值**逐字来自入参 `parts` 文档**
       （`_text(parts.get("parts_id"))` / `_text(parts.get("parts_hash"))`）；文档没有这两样时给 `""`，
       **不许**用零件行、行号、时间或任何别的东西**编**一个版本；
     - 返回体（`:2406`）顶层新增 `parts_id` / `parts_hash`，同样取自入参文档；
     - 既有键一个都不许动：`component_id` / `part_code` / `rule_id` / `fallback_paired` /
       `original_missing_variables` / `pairing_basis` / `material_match` / `size_source` /
       `outline_status` / `size_quality` 以及 `role_value` / `role_rule` / `binding_evidence` /
       `binding_method` / `bound_by` 的位置与含义逐字不变（**新增项加在末尾**）。
2. `tech_app/backend/services/packaging_bom.py`
   - `load_bom()`（`:921`）：
     - 读一次当前零件文档（沿用 `_bind_parts()` 的延迟导入范式：`from . import packaging_parts`，
       避免 `bom ↔ parts` 互相 import），取它的 `parts_id` / `parts_hash`；
     - `source_versions` **新增** `parts_id` / `parts_hash`（当前零件文档的；读不到给 `""`）；
     - **新增** `parts_binding_stale` 键（键**必须存在**，没有问题时给 `[]`）：逐行比对
       行上 `size_source_json.dwg_binding.parts_hash` 与当前文档 `parts_hash`：
       - 两边都有值且不同 → `{"item_key", "bound_parts_hash", "current_parts_hash",
         "reason": "parts_reparsed"}`；
       - 行上有 `dwg_binding` 但**没有** `parts_hash` → `{"item_key", "bound_parts_hash": "",
         "current_parts_hash", "reason": "binding_without_version"}`；
       - 行上没有 `dwg_binding`（模板行 / 人工行）→ **不进列表**；
       - 排序按 `item_key` 升序（稳定输出）；
     - **新增** `parts_document_unavailable` 键（键**必须存在**，正常时给 `{}`）：当前零件文档
       读不到（抛异常 / 返回非 dict / 没有 `parts`）时给
       `{"code": "parts_document_unavailable", "reason": "<异常类名或空>"}`；
       - 这种情况**不许**逐行断言谁过期 —— `parts_binding_stale` 给 `[]`：
         "比较不了" ≠ "不一致"（与 `packaging-silent-degradation-disclosure.md` 同一套纪律）；
     - 既有键（`built` / `items` / `gaps` / `stats` / `pairing_review` / `role_unbound`
       / `source_versions` 的既有三项）逐字不变 —— 本批是**加法披露**，不改任何结论口径。
3. `tech_app/backend/main.py`
   - `GET /api/projects/{pid}/requirement/packaging-bom` 原样带出 `parts_binding_stale` /
     `parts_document_unavailable` / `source_versions.parts_hash`（键名不变、不改路由形状）。
4. 前端（`tech_app/frontend/app.js` 包装 BOM 面板）
   - `parts_binding_stale` 非空时按行显示"这一行的尺寸来自上一版图纸解析，请重新生成 BOM"，
     **不许**继续用"已确认/无缺口"的样式展示这些行；`parts_document_unavailable` 非空时显示
     "暂时读不到零件文档版本，请稍后重试"（与"没有过期行"区分开）。

## 3. 禁止事项

- 不许把"版本对不上"改成**改数**或**清值**：过期行照旧带着历史尺寸返回（用户要能对比），
  本批只要求**披露**，不许把 `length_mm` / `status` / `missing_variables` 改写或清空。
- 不许把 `load_bom()` 改成"版本对不上就抛错 / 整条链失败"。
- 不许在读接口里**顺手重绑**（例如读到过期就调 `bind_rows()` 覆盖）—— 重绑是
  `build_bom()` 的动作，读接口只报事实。
- 不许动 `bind_rows()` 的配对口径（行序 ↔ 面积降序、循环取件、`fallback_paired`、`locked` 不碰）。
- 不许改 `_identity()` / `save_parts()` 的幂等口径（同一份内容同一个 `parts_id`），
  也不许新增"每次解析都换 id"的写法。
- 不许把历史行（`reason="binding_without_version"`）当成"没过期"静默放过；
  也不许把它当成 `parts_reparsed` 混在同一个码里 —— 两者的处置话术不同。
- 不许改 `tests/` 下任何既有文件（含本批红测与
  `test_packaging_parts_extraction_red.py` / `test_packaging_parse_to_downstream_seams_red.py` /
  `test_packaging_parametric_bom_red.py`）。
- 不许连线上 PG 跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线（假后端 / 假零件模块）。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_parts_version_binding_red -v
# E 组（绑定留痕带版本，2 条）：绑出来的行上 size_source.dwg_binding 必须带文档那一版的
#                                parts_id / parts_hash，且返回体顶层同样带一份 /
#                                既有绑定口径（bound 计数、source=dwg_parts、缺变量清空）逐字不变
# F 组（读接口披露，5 条）：行绑旧版 + 当前新版 → parts_binding_stale 报 parts_reparsed，
#                          source_versions.parts_hash 是当前版 /
#                          行上有 binding 无版本 → reason=binding_without_version /
#                          版本一致 → 必须是 [] /
#                          当前零件文档读不到 → parts_document_unavailable 非空，
#                          且 parts_binding_stale 给 []（比较不了≠不一致），items/gaps 逐字不变 /
#                          没有 dwg_binding 的行不许被标过期
# 现状：E1 F1 F2 F4 红（4 条），E2 F3 F5 绿（3 条护栏）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
# 不回归（零件提取 / 配对口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parse_to_downstream_seams_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_silent_degradation_red
```

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-drawing-flow/... # 重解析一次图纸（换一版零件）
GET  /api/projects/{pid}/requirement/packaging-bom              # parts_binding_stale 必须报出被顶掉的行
```

## 5 落地状态（2026-09-22，实现方 Codex）

实跑：`./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_parts_version_binding_red`
→ **Ran 7 tests … OK**（E1/F1/F2/F4 四条红转绿，E2/F3/F5 三条护栏仍绿）。

- `packaging_parts.bind_rows()`：`size_binding` 新增 `parts_id` / `parts_hash`，逐字取入参
  **文档**顶层（`_text(parts.get("parts_id"))` / `("parts_hash")`，没有就给 `""`，绝不编一个版本）；
  返回体顶层也带一份 `parts_id` / `parts_hash`。既有键（`component_id` / `part_code` / `rule_id` /
  `fallback_paired` / `original_missing_variables` / `pairing_basis` / `material_match` /
  `size_source` / `outline_status` / `size_quality`）与配对口径一个字未动。
- `packaging_bom._parts_binding_scope(project_id, items)`：读当前零件文档（`packaging_parts.load_parts`），
  逐行比对 `size_source_json.dwg_binding.parts_hash`：
  - 有值且不同 → `"/reason": "parts_reparsed"`；
  - 有 `dwg_binding` 但没版本 → `"binding_without_version"`；
  - 没有 `dwg_binding` → 不进列表；清单按 `item_key` 升序；
  - 当前文档读不到（抛异常 / 非 dict / 没有 `parts`）→ `stale = []` +
    `parts_document_unavailable = {"code": "parts_document_unavailable", "reason": …, "message": …}`
    （比较不了 ≠ 不一致）。
- `packaging_bom.load_bom()`：新增 `parts_binding_stale` / `parts_document_unavailable` 两个**必存在**
  的键；`source_versions` 新增 `parts_id` / `parts_hash`（当前版）。既有键与结论口径（`built` /
  `items` / `gaps` / `stats`）逐字不变 —— 过期行照旧带着历史尺寸返回，不改数、不清值、不重绑。
- `main.py`：`GET …/requirement/packaging-bom` 原样透出（路由形状未变）。
- 前端 `requirement-confirm.js`（包装 BOM 面板实际所在文件）：过期行逐行带
  `data-pb-stale` 与"图纸已换版（待重新生成）"，不再用"已算出/无缺口"的样式；顶部
  `data-pb-parts-stale` 给总账、`data-pb-parts-unavailable` 把"读不到版本"与"没有过期行"分开说；
  标题栏顺带显示当前零件文档版本前 12 位。`node --check` 通过。

### 已记录的边界

- Spec §2 第 4 条写的是"`tech_app/frontend/app.js` 包装 BOM 面板"，但该面板实际在
  `tech_app/frontend/requirement-confirm.js`（`#packagingBomPanel` / `pbPanel()`）里，
  `app.js` 全文没有 BOM 面板渲染代码 —— 按意图改在真正承载面板的文件上。
- `parts_document_unavailable` 按 Spec §2.2 的字面口径判定：**没有零件文档**也算"比对不了"
  （不只是读失败），因此纯模板项目（还没跑过图纸解析）读 BOM 时该键也非空 —— 这是 Spec 写死的
  口径，未自行放宽。
