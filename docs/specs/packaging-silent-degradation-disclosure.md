# 规格：包装链路上的"静默降级"必须留痕（零件回填 / 人工角色映射 / 角色候选）

状态：Spec + 红测（已实现）（五处都已改成留痕降级；落地见 §7）
红测：`tests/test_packaging_silent_degradation_red.py`

血缘：承接 `packaging-dwg-parts-extraction.md`（C7 零件回填）、
`packaging-parse-to-downstream-seams.md` §3.2（配对复核要能从读接口读到 —— 本批用的是同一套落点范式）、
`packaging-part-role-manual-mapping.md` §2.8（重算不许把人工映射算没了）、
`packaging-parts-downstream-acceptance.md`（"这条链路做到了哪一步"要看得见）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

把包装链路上四处 `except Exception` 的**静默降级**改成**留痕降级**：
失败可以不影响既有结论，但**不许**让读接口把"我读不到 / 我算挂了"显示成
"本来就没有" —— 用户照着"没有"去做决定，永远查不出真因。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

五处的形状完全一样：**失败和"空"在返回体上是同一个值**。

### 1.1 零件文档在、回填却挂了 → BOM 静默退回"没有尺寸"

`tech_app/backend/services/packaging_bom.py:1039` `_bind_parts()`：

```python
    try:
        from . import packaging_parts
        doc = packaging_parts.load_parts(project_id)
        if not isinstance(doc, dict) or not doc.get("parts"):
            return items, [], []
        result = packaging_parts.bind_rows(items, doc)
        return (list(result.get("items") or items), [...pairing_review...], [...role_unbound...])
    except Exception:                                   # noqa: BLE001 - 回填失败不改既有结论
        return items, [], []
```

`bind_rows()` 抛任何异常（零件文档形状不对、component_id 缺失、材料分类炸了……）都被吞掉，
返回值与"这个项目根本没有零件文档"**逐字相同**：`(原样 items, [], [])`。
后果：BOM 行照旧停在 `needs_input`、材料费 0、`pairing_review` / `role_unbound` 都是空清单，
而 `packaging-parts` 读接口上明明躺着 263 件零件 —— 现场只能看到"零件有、BOM 没数"，
没有任何地方说得出"回填这一步挂了"。同一批的 `pairing_review` / `role_unbound` 就是
为了消灭这类"算出来但看不见"而加的，本处是同一个洞的第三格。

### 1.2 人工角色映射的读/写失败 → 静默丢映射 / 静默没保存

`packaging_bom.py:755` `_role_doc()`：

```python
    try:
        doc = get_backend().get_doc(project_id, ROLE_MAP_DOC_KEY) or {}
    except Exception:                                   # noqa: BLE001 - 读不出来按"没有"
        return {"by_requirement": {}}
```

`apply_saved_role_map()`（`:852`，`build_bom()` 每次都调）拿到的就是这份"空映射"，
于是 **Spec §2.8「重算不许把人工映射算没了」在最需要它的那一刻失效**：
文档通道只要抖一下，重算就按"没人映射过"处理，人工确认过的角色在 BOM 上被清掉，
而且没有任何留痕。

`packaging_bom.py:832` `save_role_mapping()` 的写侧是同一个形状：

```python
    except Exception:                                   # noqa: BLE001 - 留痕失败不改 BOM 结论
        return
```

行上的 `size_source_json.dwg_binding` 已经写成功了，文档那一份没写进去；接口却回 200。
下一次重算时 `apply_saved_role_map()` 读到的是**上一次**的映射（或空），
"我映射了、它没了"没有任何地方能追。

### 1.3 角色候选读不到 KB → 显示成"这个盒型没有部件模板"

`packaging_bom.py:836` `role_candidates_for()`：

```python
        try:
            templates = list(kb_repo.packaging_part_templates(box_code) or [])
        except Exception:                               # noqa: BLE001 - 读不到知识库不挡人工映射
            templates = []
    return {"box_type_code": box_code, "requirement_no": req_no,
            "part_templates": templates, "role_candidates": role_candidates(templates)}
```

这个返回体直接进 `GET /api/projects/{pid}/requirement/packaging-bom/role-map`
（`main.py:6856`），`part_templates: []` 同时也是"这个盒型确实没有部件模板"的正常值。
用户看到空下拉，会去改盒型或以为模板没入库 —— 真因是知识库这一趟没读到。

### 1.4 同一条缝在盒型匹配侧：读不到模板 → 候选被判成"选了它走不下去"

`packaging_match.py:419` `_part_template_count()`（`## 324` 刚落地的能力）：

```python
    try:
        return len(kb_repo.packaging_part_templates(code) or [])
    except Exception:                  # noqa: BLE001 - 见 docstring：读不到按没有模板
        return 0
```

两个下游都把它当成**事实**用：

- `:499` `"part_template_available": bool(_part_template_count(...))` → 知识库抖一下，
  这个候选就被标成"选了它往下走 BOM 会 409"（`False`），用户于是**主动避开一个完全可用的盒型**；
- `:433` `_template_warnings()` → `_part_template_count()` 为 0 时给出
  `{"code": "box_type_without_part_template", "detail": "该盒型在部件模板表里没有模板，下一步 BOM
  会以 no_part_template 失败"}` —— 这是一句**断言的错话**，把"我查不到"说成了"它没有"。

与 §1.3 是同一个洞的两端（一个在读接口、一个在匹配候选），本批一并收。

### 1.5 配对复核文档读不到 → 报告里“这次没有不一致项”

`packaging_bom.py:1011` `_pairing_doc()`：

```python
    try:
        doc = get_backend().get_doc(project_id, PAIRING_DOC_KEY) or {}
    except Exception:                                   # noqa: BLE001 - 读不出来按"没有"
        return {"by_requirement": {}}
```

`_load_pairing_review()`（`:1022`）拿到的就是这份空壳，`load_bom()` 的 `pairing_review`
于是给 `[]` —— 与“这次配对确实没有任何不一致项”**逐字相同**。
配对复核是 `bind_rows()` 唯一一处把“配对后材料明显不同类”喊出来的地方
（Spec `packaging-parse-to-downstream-seams.md` §3.2）：读不到按“没有”处理，
等于让一次可疑配对在报告里凭空消失 —— 零件真被配错了也当成没事，而且没有任何地方能追。

与 §1.2 是同一个洞的两端（一个管人工角色映射、一个管配对复核），本批一并收。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_bom.py`
   - `_bind_parts()` 改成返回 **4 元组**：`(items, pairing_review, role_unbound, binding_error)`；
     `binding_error` 是 `{}` 或 `{"code": "part_binding_failed", "reason": "<异常类名>",
     "message": "<第一行文案>"}`。四元组的新增项**必须**是最后一个，前三个位置与语义逐字不变；
   - `build_bom()` 在 `_bind_parts()` 之后调用新落点 `_save_binding_error(project_id, req_no, err)`
     （文档通道，键 `packaging_bom_binding_error`，形状与 `pairing_review` / `role_unbound` 同构：
     `{"by_requirement": {需求单: {...}}}`），并像那两份一样在写失败时**不挡 BOM 结论**（只留日志）；
   - `load_bom()` 输出**必须**新增 `binding_error` 键（没有时给 `{}`，与 `pairing_review` 同一口径：
     键存在、值可空，不省略）；
   - `_role_doc()` 的读失败**不许**再返回空映射：抛 `BomError("人工角色映射读不到，请稍后重试",
     503, "role_map_unavailable")`（`BomError` 已有 `code`，路由会原样映射）；
     **正常读到空文档仍然给 `{"by_requirement": {}}`** —— 本批只区分"没有"与"读不到"；
   - `save_role_mapping()` 的文档写失败**必须抛给调用方**（`BomError(..., 503,
     "role_map_save_failed")`），不许 `return` 假装成功；行上已经写好的 `size_source_json`
     不回滚（那不是本批要动的），但调用方必须知道"留痕没落盘"；
   - `role_candidates_for()` 增加 `templates_unavailable` 键：KB 读失败时给
     `{"code": "template_lookup_failed", "reason": "<异常类名>"}`，读成功时给 `{}`；
   - `_pairing_doc()`（`:1011`）的读失败**不许**再返回空壳：同样只区分“没有”与“读不到” ——
     读到空文档仍给 `{"by_requirement": {}}`；读失败时 `load_bom()` 输出**必须**带
     `pairing_review_unavailable`（`{"code": "pairing_review_unavailable", "reason": "<异常类名>"}`），
     而 `pairing_review` 键的存在性、类型与“读得到时逐字带出”的口径**逐字不变**。
2. `tech_app/backend/services/packaging_match.py`
   - `_part_template_count()` 不许再把读失败折成 `0`：改成"读不到给 `None`"（新增
     `_part_template_state(code) -> (available: Optional[bool], total: int, error: dict)` 或等价形状，
     由它统一供上面两个下游使用）；
   - `_candidate()` 的 `part_template_available`：读到 → `True/False`（既有口径逐字不变）；
     **读不到 → `None`**（未知）；`part_template_total` 读不到时给 `0` 并在候选上带
     `part_template_unavailable`（`{"code": "template_lookup_failed", "reason": "<异常类名>"}`）；
   - `_template_warnings()` 在"读不到"时**不许**给 `box_type_without_part_template`，
     改给 `{"code": "box_type_template_lookup_failed", ...}`，文案说明"暂时查不到，请稍后重试"。
3. `tech_app/backend/main.py`
   - `GET …/packaging-bom/role-map` 原样把 `templates_unavailable` 带出去（键名不变）。
4. 前端（`tech_app/frontend/app.js` 角色映射面板）
   - `templates_unavailable` 非空时显示"模板暂时读不到，请稍后重试"，**不许**显示成"该盒型没有部件模板"。

## 3. 禁止事项

- 不许把这五处的降级改成"整条链失败"：`_bind_parts` / `_pairing_doc` 失败照旧不让
  `build_bom` / `load_bom` 整体失败 —— 本批只要求**留痕**，不改既有"读不到也不挡"的结论口径
  （`load_bom()` 的 `items` / `gaps` / `stats` 在读不到披露文档时必须逐字不变）。
- 不许改 `bind_rows()` / `apply_role_mapping()` / `role_map_status()` 的签名与语义。
- 不许改 `pairing_review` / `role_unbound` 的键名、位置与"键必须存在"的口径
  （新增的 `pairing_review_unavailable` / `binding_error` 是**加法**，不许拿它们替换既有键）。
- 不许把 `_role_doc()` 的"读不到"改成"按空映射继续"的任何等价写法（例如 catch 后塞 `{}`
  再往下走）—— 那正是本批要消灭的静默降级。
- 不许把 `_part_template_count()` 的"读不到"再折成任何"看起来像事实"的值（`0` / `False` / 空清单）：
  `bool(None)` 已经是 `False`，但这只是显示层的巧合，**不许**靠它蒙过去 —— 契约要求候选上
  带的是 `None` + 显式 `part_template_unavailable`。
- 不许改 `tests/` 下任何既有文件（含本批红测与
  `test_packaging_parse_to_downstream_seams_red.py` / `test_packaging_part_role_manual_mapping_red.py` /
  `test_packaging_box_candidate_rank_and_runnability_red.py`）。
- 不许连线上 PG 跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线（假 store / 假后端）。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_silent_degradation_red -v
# A 组（零件回填，3 条）：失败必须给 part_binding_failed 留痕 / load_bom 必须带 binding_error 键 /
#                          没有零件文档时 binding_error 必须是 {}（不许把"没有"当失败）
# B 组（人工映射，3 条）：读不到必须抛 role_map_unavailable / 写失败必须抛 role_map_save_failed /
#                          正常读写路径逐字不变
# C 组（角色候选 + 模板可用性，4 条）：KB 读失败必须给 templates_unavailable / 读成功时必须是 {}
#                                  / 候选的 part_template_available 读不到必须是 None（未知）
#                                  / 读不到时不许给 box_type_without_part_template
# D 组（配对复核，3 条）：读不到必须披露 pairing_review_unavailable（不许看起来"干净"）
#                          / 读得到时标记必须是 {} 且清单逐字带出 / 结论键（items·gaps·stats）不许因
#                          读失败而变（只留痕，不改结论）
# 现状：A1 A2 B1 B2 C1 C3 C4 D1 红（8 条），A3 B3 C2 D2 D3 绿（5 条护栏）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parse_to_downstream_seams_red
# 不回归（配对复核 / 未映射清单的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_manual_mapping_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_box_candidate_rank_and_runnability_red
```

真机复验（实现方做完、且部署后）：

```
GET  /api/projects/{pid}/requirement/packaging-bom          # 回填挂了时能读到 binding_error；
                                                             #   配对复核读不到时能读到
                                                             #   pairing_review_unavailable
GET  /api/projects/{pid}/requirement/packaging-bom/role-map  # KB 读不到时 templates_unavailable 非空
```

## 7 落地状态（2026-09-22，实现方 Codex）

实跑：`./open-claude/.venv/bin/python -m unittest tests.test_packaging_silent_degradation_red`
→ **Ran 13 tests … OK**（A1/A2/B1/B2/C1/C3/C4/D1 八条红转绿，A3/B3/C2/D2/D3 五条护栏仍绿）。

五处都在 `tech_app/backend/services/packaging_bom.py`（+ `packaging_match.py`、`main.py`、`app.js`）：

1. **零件回填失败**（§2.1）：`_bind_parts()` 改 4 元组，失败路径返回
   `{"code": "part_binding_failed", "reason": "<异常类名>: <首行>", "message": …}`；
   `build_bom()` 用新 `_save_bind_error()` 落一份 `packaging_bom_bind_error` 文档
   （这次没失败就**清掉**上一次的，不许留过期告警）；`load_bom()` 新增 `binding_error` 键
   （没有失败给 `{}`；连披露文档都读不到时给 `code="binding_error_unavailable"`）。
2. **人工角色映射读/写**（§2.2）：`_role_doc()` 读失败 → 抛
   `BomError(code="role_map_unavailable", status_code=503)`（不再返回空壳）；
   `save_role_mapping()` 的 meta 写失败 → 抛 `role_map_save_failed`（以前静默 `return`，
   接口还回 200）。`load_bom()` 侧新增 `role_unbound_unavailable`：清单给空但标记非空，
   界面据此说"暂时读不到"，**绝不**按"没人映射过"渲染。
3. **角色候选 / KB**（§2.3）：`role_candidates_for()` 读失败时给
   `part_templates: []` **+** `templates_unavailable: {"code": "template_lookup_failed", …}`；
   读成功给 `{}`。`main.py` 的 `GET …/role-map` 原样带出（`role_map.templates_unavailable`
   与顶层 `templates_unavailable` 同名同值），并在映射文档读不到时按 `BomError.status_code`
   回 `{"code", "message"}`，不再 500。
4. **盒型候选的模板可用性**（§2.4）：`packaging_match._part_template_state()` 返回
   `(available, total, unavailable)`，`available is None` 表示**未知**；`_candidate()`
   的 `part_template_available` 读不到给 `None`（不是 `False`）并带
   `part_template_unavailable`；`_template_warnings()` 读不到给
   `box_type_template_lookup_failed`（"暂时查不到，请稍后重试"），不再给
   `box_type_without_part_template` 那句错话。
5. **配对复核**（§1.5/§2.5）：`_pairing_doc()` 读失败 → 抛 `pairing_review_unavailable`；
   新增 `_pairing_scope()` 供 `load_bom()` 用：清单照旧 `[]`、标记非空，既有结论键
   （`built`/`items`/`gaps`/`stats`）一个字不改。
6. **前端**（§4）：`renderPackagingRoleMap()` 顶部渲染
   `data-role-map-templates-unavailable` 警告并替换"没有候选角色（模板为空）"那句；
   `loadPackagingRoleMap()` 对 503 / 网络错误渲染 `data-role-map-unavailable`
   真因，不再静默 `return null`。`node --check` 通过。

### 已记录的边界

- `_save_role_unbound()` / `_save_pairing_review()` 的**写**失败仍然只吞不抛：它们写的是
  披露快照（读侧每次都现算），写不进去不影响结论；真正影响"人工映射不被算没"的那条写路径
  （`save_role_mapping`）已改成显式失败。
- `cpq_packaging_match.py`（报价侧的同类第二份实现）本批未动：Spec §2 的允许范围只列了
  `tech_app/` 侧四处文件。
