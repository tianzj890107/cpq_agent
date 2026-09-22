# 规格：角色候选「读不到」的披露，不许在自己下一层调用点被丢掉

状态：Spec + 红测（已实现）（原状：`role_candidates_for()` 已经会算 `templates_unavailable`，
但 `_load_role_scope()` 只取 `part_templates`、把标记扔了，自己又留了一处 `except Exception:
templates = []`；`load_bom()` 的未映射清单于是只有"空候选"，没有"为什么空"）
红测：`tests/test_packaging_bom_role_unbound_template_disclosure_red.py`

血缘：承接 `packaging-silent-degradation-disclosure.md` §2.3–§2.5（`role_candidates_for()` 的
`templates_unavailable` 与 `role_unbound_unavailable` 就是那一批刚落地的披露，
本批补的是**同一个披露在 `_load_role_scope()` 这一层的丢失**）、
`packaging-part-role-manual-mapping.md` §4.3（未映射清单与候选角色的唯一口径）、
`packaging-box-candidate-rank-and-runnability.md`（`part_template_available` 的"读不到 = 未知"纪律）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

同一件事（"部件模板这一趟没读到"）在两个读接口上必须说同一句话：
角色映射面板（`GET …/packaging-bom/role-map`）已经会说，**BOM 未映射清单
（`GET …/packaging-bom` 的 `role_unbound`）今天不会说** —— 它给的是"候选角色：空"，
用户看到空下拉框，只会去改盒型或以为这个盒型没有候选角色。

## 1. 现状缺口（代码级，逐条可指到行；离线实测）

`tech_app/backend/services/packaging_bom.py:1100 _load_role_scope()`：

```python
    templates: list = []
    try:
        templates = list(role_candidates_for(project_id, requirement_no).get("part_templates") or [])
    except Exception:                                   # noqa: BLE001 - 读不到 KB 不挡披露
        templates = []
```

两个问题：

1. **披露被丢**：`role_candidates_for()`（`:860`）在 KB 读不到时返回
   `part_templates: []` **并且** `templates_unavailable: {"code": "template_lookup_failed",
   "reason": "<异常类名>", "message": "部件模板暂时读不到…这不代表该盒型没有部件模板"}`
   （`packaging-silent-degradation-disclosure.md` §2.3 的产物）。`_load_role_scope()` 只读
   `part_templates`，**这个标记在返回体里不存在**（实测：`sorted(scope) ==
   ['box_type_code', 'candidates_source', 'engine_version', 'items', 'mapped_total',
   'role_candidates', 'unavailable', 'unbound_total']`，`scope.get("templates_unavailable")`
   → `None`）。
2. **自己又吞一次**：`role_candidates_for()` 万一抛异常（不是它内部处理的那条路），
   `except Exception: templates = []` 同样不留痕 —— 清单照出、每行 `role_candidates: []`，
   与"模板读了、这个盒型确实一个候选都没有"逐字相同。

后果（实测，离线打桩）：`unbound_total = 1`、该行 `role_candidates = []`、
`role_unbound_unavailable = {}` → BOM 面板上"还有 1 行没映射角色 + 候选下拉是空的"，
而 `GET …/packaging-bom/role-map` 同一时刻会显示"模板暂时读不到，请稍后重试"
（`main.py:6885` 原样带出）—— **两个面板对同一件事说法不一致**。

## 2. 契约

### 2.1 `_load_role_scope()` 必须原样透出披露

- 返回体**新增键（键必须存在）** `templates_unavailable`：
  - `role_candidates_for()` 返回里有该键 → **逐字**带出（不许重算一份、不许只抄 `code`）；
  - `role_candidates_for()` **抛异常** → 给
    `{"code": "template_lookup_failed", "reason": "<异常类名>", "message": "<人话>"}`，
    形状与 `role_candidates_for()` 内部那处**逐字一致**；清单照旧出（`unbound_total` 不变）。
- 既有键 `engine_version` / `candidates_source` / `role_candidates` / `items` /
  `mapped_total` / `unbound_total` / `unavailable` 的口径与位置**一个字不改**。

### 2.2 `load_bom()` 必须把它挂到未映射清单旁边

`load_bom()` 结果**新增键（键必须存在）** `role_unbound_templates_unavailable`：

- 逐字等于 `_role_scope(...)["templates_unavailable"]`（同一份，不许第二次查知识库）；
- 正常时 `{}`；
- 既有 `role_unbound` / `role_unbound_total` / `role_unbound_unavailable` 不变。

### 2.3 前端必须说同一句话

BOM 面板的未映射清单（以及角色映射面板）在 `role_unbound_templates_unavailable` /
`templates_unavailable` 非空时，显示"候选角色暂时读不到（<code>），请稍后重试；
这不代表该盒型没有候选角色"，并带稳定钩子
（`data-role-unbound-templates-unavailable="1"`）；**不许**让下拉框空着不解释。

## 3. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_bom.py`
   - `_load_role_scope()`：去掉那处 `except Exception: templates = []`，改成一次调用
     `role_candidates_for()` 并同时取 `part_templates` 与 `templates_unavailable`
     （失败按 §2.1 留痕）；返回体加 `templates_unavailable`；
   - `load_bom()`：加 `role_unbound_templates_unavailable`。
2. `tech_app/backend/main.py`
   - `GET /api/projects/{pid}/requirement/packaging-bom` 原样带出该键（不改路由形状）。
3. 前端（BOM 面板未映射清单）
   - §2.3 的文案与钩子。

## 4. 禁止事项

- 不许改 `role_candidates_for()` 的既有返回形状与 `templates_unavailable` 的 code / message
  （它已经被 `test_packaging_silent_degradation_red.py` 的 C1/C2 钉住）。
- 不许改 `role_map_status()` 的清单口径（谁进清单、`role_candidates` 怎么填），
  也不许因为"候选读不到"就**不报**未映射行 —— 本批只加一句解释，清单照出。
- 不许把 `templates_unavailable` 折成布尔、空串或 `None`；不许在 `_load_role_scope()` 里
  重新查一次知识库（口径只有一处）。
- 不许在 `load_bom()` 里顺手把披露写进 `role_unbound_unavailable`（那是"人工映射文档读不到"
  的位置，两件事不许混）。
- 不许改 `tests/` 下任何既有文件（含本批红测与 `test_packaging_silent_degradation_red.py` /
  `test_packaging_part_role_manual_mapping_red.py` / `test_packaging_parametric_bom_red.py`）。
- 不许连线上 PG / 生产库跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线（打桩 / 假后端）。

## 5. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_role_unbound_template_disclosure_red -v
# A 组（3 条）：role_candidates_for 的标记必须原样出现在 _load_role_scope 里
#              / 它抛异常时必须留痕 template_lookup_failed + 异常类名，且清单照出
#              / 读得到时标记必须是 {}（不许常驻非空，否则等于没披露）
# B 组（1 条）：load_bom()[role_unbound_templates_unavailable] 逐字等于那一份
# D 组（1 条）：前端未映射清单必须有 data-role-unbound-templates-unavailable 钩子
# E 组（3 条护栏，今天必须绿）：_load_role_scope 既有键全在 / role_map_status 形状不变
#                              / load_bom 既有 role_unbound* 键全在
# 现状：A1 A2 A3 B1 D1 红（5 条），E1 E2 E3 绿（3 条护栏）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_silent_degradation_red
# 不回归（本披露的产出方）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_manual_mapping_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
```

真机复验（实现方做完、且部署后）：

```
GET  /api/projects/{pid}/requirement/packaging-bom
     # 知识库读不到时 role_unbound_templates_unavailable 非空（不是空下拉框无解释）
GET  /api/projects/{pid}/requirement/packaging-bom/role-map
     # 同一时刻 templates_unavailable 有同一个 code（两个面板同一句话）
```

## 6. 落地状态

（已实现）（`packaging-bom-role-unbound-template-disclosure` 9-22 落地。`_load_role_scope()` 一次调用
`role_candidates_for()` 并**原样透出** `templates_unavailable`（它抛异常时按同一形状留
`template_lookup_failed` + 异常类名 + 同一句人话，清单照出）；`load_bom()` 新增
`role_unbound_templates_unavailable` 挂在未映射清单旁边；前端读这个键并把空下拉框解释掉。
A1 / A2 / A3 / B1 / D1 由红转绿，E1 / E2 / E3 三条护栏仍绿。）

### 6.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 原样透出 | `tech_app/backend/services/packaging_bom.py` `_load_role_scope()`：一次 `role_candidates_for()` 同时取 `part_templates` 与 `templates_unavailable`（`dict(flag)` 逐字带出）；它抛异常 → `{"code": "template_lookup_failed", "reason": "<异常类名>", "message": "部件模板暂时读不到（知识库读失败：<类名>），请稍后重试；这不代表该盒型没有部件模板"}`（与 `role_candidates_for()` 内部那处逐字一致）；`role_map_doc()` 不可读那条早退路径也带上这个键（空） |
| §2.1 既有键不动 | `role_map_status()` 的 `engine_version` / `candidates_source` / `role_candidates` / `items` / `mapped_total` / `unbound_total` / `unavailable` 口径与位置一个字没改；候选读不到**不**让未映射行消失 |
| §2.2 挂到清单旁 | `load_bom()` 新增 `role_unbound_templates_unavailable`（逐字等于那一份；正常 `{}`），与 `role_unbound_unavailable`（人工映射文档读不到）分开两件事 |
| §2.3 前端 | `tech_app/frontend/app.js`：新增 `refreshPackagingBomRoleUnboundNote()` —— 读 `GET …/requirement/packaging-bom` 的 `role_unbound_templates_unavailable`，非空时在角色映射面板尾部挂 `data-role-unbound-templates-unavailable="1"` 的说明（「候选角色暂时读不到（<code>），请稍后重试；这不代表该盒型没有候选角色」），由 `refreshPackagingParts()` 顺带调用；`{}` 时不加任何东西 |

`main.py` 的 `GET …/requirement/packaging-bom` 是 `return {"bom": load_bom(...)}`，新键自动带出，
路由形状未改（§3.2 无需改动）。

### 6.2 复跑

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_role_unbound_template_disclosure_red
# Ran 8 tests ... OK
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_silent_degradation_red \
    tests.test_packaging_part_role_manual_mapping_red tests.test_packaging_parametric_bom_red
# Ran 91 tests ... OK
node --check tech_app/frontend/app.js    # OK
```

### 6.3 已记录的边界

- 红测 D1 按**字面量**在 `tech_app/frontend/app.js` 里找钩子与键名（不是跑前端）。已落地的读点
  是"顺带刷新"：`refreshPackagingParts()` → `refreshPackagingBomRoleUnboundNote()`；BOM 面板本身
  目前在 `requirement-confirm.js`（`pbPanel`），两处说的是同一句话，本批只在 app.js 补上这个键的读点
  （D1 钉的文件）。
- 只加键、只加说明：候选为空照旧进未映射清单；两处 `unavailable` 不合并。
