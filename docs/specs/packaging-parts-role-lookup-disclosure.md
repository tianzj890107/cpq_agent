# 规格：零件角色全是 `unknown` 时，「语义层没算出来」不许与「图纸图层名不认识」同形

状态：Spec + 红测（已实现）（原状：`packaging_parts._layer_roles()` 只回 `图层名 → 角色` 一张表，
语义层 `analyze()` 抛异常时被 `except Exception: doc = None` 吞掉、退回 IR 图层兜底，
于是**两种完全不同的原因**在零件表上都是 `by_role = {"unknown": N}`，读接口一个字段都分不出）
红测：`tests/test_packaging_parts_role_lookup_disclosure_red.py`

血缘：承接 `packaging-silent-degradation-disclosure.md`（同一病症：失败 / 空 / 没有不许同形；
它覆盖 BOM 与盒型匹配侧，本批是**零件侧的角色来源**）、
`packaging-drawing-semantics.md` §2.2（语义文档**层内**已经有逐层 `role_source` ∈
`rule / color_rule / line_type_weak / none` —— 信息本来就有，是 `packaging_parts` 在取角色时把它扔了）、
`packaging-product-outline-and-die-layer-roles.md`（§2 `role_source="rule"` 的落点）、
`packaging-part-role-manual-mapping.md`（`role=unknown` 不许自动贴业务角色，本批不动这条纪律）、
`packaging-parts-downstream-acceptance.md`（`role_known_ratio` 这条下游门禁的读数来源）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

零件树里 64 件全是「未知角色」时，用户必须能分辨这是**哪一种**没查出来：

1. 语义层这一次没跑成（可重试）；
2. 语义层跑成了，但图纸上的图层名（`DESIGN` / `SAMPLE` / `0` / `轮廓线` …）不在角色规则里（要么补规则、要么人工指定）；
3. 图纸确实没给任何可用图层名。

今天这三种在返回体上**逐字相同**（`stats.by_role = {"unknown": N}`），而它直接决定下游能不能自动
绑定 BOM 行（`reject_unknown_role_autobind()` 会拒掉全部自动绑定 → 材料费 0 → 工艺推荐"没有零件"）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_parts.py:440 _layer_roles()`：

```python
def _layer_roles(ir: Dict[str, Any], semantics: Any) -> Dict[str, str]:
    """图层名（大写）→ 角色。有语义文档就用它，否则现算一份（同一套规则）。"""
    doc = semantics if isinstance(semantics, dict) else None
    if doc is None:
        try:                                              # 现算：只读规则文件，不落盘
            from . import packaging_semantics
            doc = packaging_semantics.analyze(ir)
        except Exception:                                 # noqa: BLE001 - 算不出来也要出零件
            doc = None
    roles: Dict[str, str] = {}
    if isinstance(doc, dict):
        for row in doc.get("layers") or []:
            ...
            roles[key] = _text(row.get("role")) or "unknown"
    if roles:
        return roles
    # 语义层不可用时的兜底：IR 里已经带角色就用它，否则 unknown（绝不猜）。
    for row in ir.get("layers") or []:
        ...
        roles[key] = _text(row.get("role") or row.get("inferred_role")) or "unknown"
    return roles
```

三个问题：

1. **失败被吞**：`analyze()` 抛任何异常都变成 `doc = None`，返回体上没有任何痕迹 ——
   与「图层名不在规则里」产出同一张 `{图层名: "unknown"}` 表（`:449-456` 与 `:459-466` 两条路径
   在"全 unknown"时完全同形）。
2. **信息被扔**：语义文档每一层本来就带 `role_source`（`packaging_semantics/roles.py` 的
   `rule / color_rule / line_type_weak / none`，见 `packaging-drawing-semantics.md` §2.2），
   `_layer_roles()` 只取 `role` 字段，把 `"none"`（= 规则里没有这个图层名）这条**唯一能解释
   原因**的证据丢了。
3. **读接口没有出处**：`extract()`（`:1461`）的 `stats.by_role`（`:1744-1746`）只有计数、
   `summarize()`（`:1841`）照旧带出，用户/看板/下游门禁（`role_known_ratio`）都看不出
   "这一次角色是怎么查出来的"，更看不出"根本没查成"。

实测（离线，本机 fixture `tests/fixtures/cad_ir/parts_panels.json`）：
`extract(ir, None)` → `by_role = {"cut": 3, "unknown": 1}`、`_layer_roles(ir, None)` →
`{'0': 'unknown', 'CREASE': 'crease', 'CUT': 'cut', 'INSERT': 'unknown', 'TEXT': 'unknown'}`
—— 注意 `'0' / 'INSERT' / 'TEXT'` 这三个"认不出"的图层名与"语义层没跑成"在返回体上是**同一张表**。

## 2. 契约

### 2.1 新增 `role_lookup_state(ir, semantics=None) -> dict`（唯一一处查角色）

```jsonc
{"source": "semantics" | "ir_layers" | "unavailable",
 "reason": "",                       // 只有语义层失败时给异常类名
 "unknown_layers": ["DESIGN", "SAMPLE", "0"],   // 大写、去重、升序；没有就给 []
 "message": ""}                      // source != "semantics" 或 unknown_layers 非空时必须非空（人话）
```

判据（闭集，按顺序）：

1. `semantics` 是 dict → 直接用它（**不许**再调 `analyze()`）；
   否则调 `packaging_semantics.analyze(ir)`：
   - 成功 → `source = "semantics"`、`reason = ""`；
   - 抛异常 → `reason = <异常类名>`，继续第 2 步；
2. 语义文档不可用（`semantics` 不是 dict **且** 现算失败）时看 IR 图层自带角色
   （`ir["layers"][].role` / `.inferred_role`）：
   - 至少一个非 `"unknown"` → `source = "ir_layers"`；
   - 一个都没有 → `source = "unavailable"`（**这就是"没查成"的那一态**）；
3. `unknown_layers`：语义文档可用时取"语义文档里 `role` 为空 / `unknown`，或
   `role_source == "none"`"的图层名；`unavailable` 时取 IR 里的全部图层名（大写去重升序）。

`_layer_roles()` 的**返回口径逐字不变**（仍是 `{图层名: 角色}`，仍是"语义文档非空就不回落 IR"），
但它与 `role_lookup_state()` 必须走**同一趟查找**（新增私有 `_resolve_layer_roles(ir, semantics)
-> (roles, state)`，两个公开入口都从它派生）—— 不许在模块里写第二套"猜角色"的逻辑。

### 2.2 `extract()` 的 `stats` 必须带出处

`extract()` 结果 `stats` **新增键（键必须存在）**：`role_lookup` = `role_lookup_state()` 的返回体
（逐字一致）。既有 `stats.by_role` 的计数口径**一个字不改**（本批只加出处，不改角色判定、
不改过滤、不改任何件数）。

### 2.3 `summarize()` 必须原样带出

`summarize()` 结果**新增键（键必须存在）**：`role_lookup`：

- 文档里有 `stats.role_lookup` → 逐字带出；
- 老文档没有这个键（`packaging_parts` 本批之前落库的）→
  `{"source": "unavailable", "reason": "role_lookup_missing", "unknown_layers": [], "message": "<人话>"}`
  —— **不许**编成 `"semantics"`（"这份文档没带出处" ≠ "语义层这次可用"）。

### 2.4 前端必须按 source 分家说人话

零件树 / 2.1 左栏：

- `role_lookup.source == "unavailable"` → 显示 `message`（"图层角色这一次没算出来，请稍后重试；
  零件尺寸不受影响"），**不许**显示成"图纸上没有可识别图层 / 图纸没写角色"；
- `role_lookup.source == "semantics"` 且 `unknown_layers` 非空 → 显示
  "这些图层名认不出角色：…（可补规则或人工指定）"，并列出 `unknown_layers`；
- 两者都要带稳定测试钩子（`data-parts-role-lookup-unavailable="1"` /
  `data-parts-role-lookup-unknown-layers="1"`）。

## 3. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_parts.py`
   - 新增 `ROLE_LOOKUPS = ("semantics", "ir_layers", "unavailable")`、
     `ROLE_LOOKUP_MESSAGES`（人话文案）与 `role_lookup_state()`；
   - 抽出 `_resolve_layer_roles(ir, semantics) -> (roles, state)`，`_layer_roles()` 与
     `role_lookup_state()` 都由它派生（`_layer_roles()` 的返回口径逐字不变）；
   - `extract()` 的 `stats` 加 `role_lookup`；`summarize()` 加 `role_lookup`（含老文档兜底）。
2. `tech_app/backend/main.py`
   - `GET /api/projects/{pid}/requirement/packaging-parts` 原样带出 `stats.role_lookup` /
     `summary.role_lookup`（键名不变、不改路由形状）。
3. 前端（`tech_app/frontend/app.js` 零件树与 2.1 左栏）
   - §2.4 的两条文案分家 + 两个 `data-` 钩子。
4. 不要求改 `packaging_semantics`：逐层 `role_source` 已经存在，本批只是**别把它扔掉**。

## 4. 禁止事项

- 不许改角色判定本身：`reject_unknown_role_autobind()` 的判据、`ROLE_PRIORITY`、
  `packaging_layer_rules.json` 的规则与 `rule_set` / `review_status` 一个字都不许动
  （本批只让"角色是怎么来的"可见，**不许**顺手把 `unknown` 变具体角色 —— 那会绕开人工映射纪律）。
- 不许改任何件数口径：`part_total` / `filtered_total` / `by_role` / `closed_ratio` /
  `role_known_ratio` / `kind_total` 等的值与算法逐字不变（新增键是**加法**）。
- 不许把 `role_lookup_state()` 的 `source` 写成开集或自由文本；不许给"认不出来"编具体角色名。
- 不许让 `role_lookup_state()` 抛异常（与 `analyze()` 失败同样要能给出 `unavailable`），
  也不许在 `source == "semantics"` 时再调一次 `analyze()`。
- 不许把"语义层失败"折成 `ir_layers`：只有 IR 图层**真的**给出了至少一个已知角色才配叫 `ir_layers`。
- 不许改 `tests/` 下任何既有文件（含本批红测与
  `test_packaging_parts_extraction_red.py` / `test_packaging_parts_downstream_gate_red.py` /
  `test_packaging_part_role_manual_mapping_red.py` / `test_packaging_semantics_red.py`）。
- 不许连线上 PG / 生产库跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线
  （本机 fixture + `mock.patch.object`，不连 34、不跑真 DWG 转换）。

## 5. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_role_lookup_disclosure_red -v
# A 组（纯函数 role_lookup_state，4 条）：给了 semantics 不许再调 analyze / 现算成功给 semantics
#                                    / analyze 挂了但 IR 图层自带角色 → ir_layers + 异常类名
#                                    / analyze 挂了且 IR 也没有 → unavailable + 异常类名 + 人话
# B 组（extract，2 条）：stats.role_lookup 键必须存在且与 role_lookup_state 逐字一致
#                        / 语义层挂掉时 by_role 照旧 {unknown: N}（结论不变，只留痕）
# C 组（summarize，2 条）：role_lookup 原样带出 / 老文档给 role_lookup_missing，不许编 semantics
# D 组（前端，1 条）：零件树/左栏必须有 data-parts-role-lookup-unavailable 钩子
# E 组（护栏，3 条，今天必须绿）：_layer_roles 返回口径逐字不变 / by_role 与零件角色集合一致
#                                 / summarize 既有键不动
# 现状：A1 A2 A3 A4 B1 B2 C1 C2 D1 红（9 条），E1 E2 E3 绿（3 条护栏）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
# 不回归（零件提取既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_gate_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_manual_mapping_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_red
```

真机复验（实现方做完、且部署后）：

```
GET  /api/projects/{pid}/requirement/packaging-parts
     # stats.role_lookup.source / unknown_layers / reason 可读；
     # 语义层挂掉时 source=unavailable（不是"图纸上没有可识别图层"）
     # 语义层可用时 unknown_layers 列出 DESIGN / SAMPLE / 0 这类认不出的图层名
```

## 6. 落地状态

（已实现）（`packaging-parts-role-lookup-disclosure` 9-22 落地。`_layer_roles()` 的返回口径逐字不变，
出处由**同一趟**查找（`_resolve_layer_roles()`）派生；`stats.role_lookup` 与 `summarize().role_lookup`
把「语义层没跑成（可重试）」/「语义层跑成但图层名不认识」/「图纸没给可用图层名」/「老文档没记出处」
四态分开。既有 `by_role` 计数口径一个字不改，本批是**加法**。）

### 6.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 三态闭集与人话 | `tech_app/backend/services/packaging_parts.py`：`ROLE_LOOKUPS = ("semantics","ir_layers","unavailable")`、`ROLE_LOOKUP_MESSAGES`（`unavailable` / `ir_layers` / `unknown_layers` / `missing` 四句）、`role_lookup_state(ir, semantics=None)`（不抛异常、`semantics` 命中时不再调 `analyze()`） |
| §2.1 唯一一趟查找 | `_resolve_layer_roles(ir, semantics) -> (roles, state)`（`_layer_roles()` 改为从它派生，返回值逐字不变）；`_ir_layer_roles(ir)` 只读 IR 自带 `role`/`inferred_role`，没有给 `unknown`；`_role_lookup_message(source, reason, unknown_layers)` 拼人话 |
| §1 第 2 条证据 | 语义文档逐层 `role_source == "none"` 与 `role == "unknown"` 一起进 `unknown_layers`（大写去重升序）——以前只取 `role`，把这条证据扔了 |
| §2.2 `extract()` 留痕 | `stats["role_lookup"] = role_lookup`；`by_role` / 件数口径不动 |
| §2.3 `summarize()` | `_summary_role_lookup(stats)`：文档里记了逐字带出；老文档（本批之前落库）给 `{"source":"unavailable","reason":"role_lookup_missing",...}`，不猜 |
| §2.4 前端 | `tech_app/frontend/app.js`：`packagingRoleLookupNote(doc)` —— `unavailable` / `ir_layers` / `unknown_layers` 三态钩子（`setAttribute("data-parts-role-lookup-unavailable"/"-ir-layers"/"-unknown-layers","1")`），文案优先取后端 `message`；`renderTree()` 的 `drawing_flow` 分支在覆盖率行之后挂上去 |

### 6.2 判定口径（与红测逐条对齐）

- `semantics`（传入 dict 或现算成功）→ `reason=""`；`unknown_layers` = 语义文档里 `role` 空 / `unknown`
  **或** `role_source == "none"` 的图层名（大写去重升序）；
- 语义层失败 → `reason = <异常类名>`；IR 图层**至少一个非 `unknown` 角色** → `source="ir_layers"`
  （`unknown_layers` = IR 里的 `unknown` 项）；一个都没有 → `source="unavailable"`
  （`unknown_layers` = IR 全部图层名）；
- 模块内**没有**第二套「猜角色」逻辑；`reject_unknown_role_autobind()` / `ROLE_PRIORITY` /
  `packaging_layer_rules.json` 一个字没动。

### 6.3 复跑

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_role_lookup_disclosure_red
# Ran 12 tests ... OK（A1-A4 / B1-B2 / C1-C2 / D1 全绿；E1-E3 三条护栏仍绿）
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_extraction_red \
    tests.test_packaging_parts_downstream_gate_red tests.test_packaging_part_role_manual_mapping_red \
    tests.test_packaging_parts_outline_red tests.test_packaging_semantics_red
# Ran 149 tests ... OK (skipped=1)
node --check tech_app/frontend/app.js    # OK
```

### 6.4 已记录的边界

- 红测 D 组只查 `data-parts-role-lookup-unavailable` 这一个钩子（`unavailable` 态）；`ir_layers` /
  `unknown_layers` 两态由 `packagingRoleLookupNote()` 一并渲染，但没有独立断言的钩子测试。
- 本批**不**改 `role=unknown` 不许自动贴业务角色的纪律（`packaging-part-role-manual-mapping.md`），
  `role_known_ratio` 门禁读数因此不变；本批只让"为什么全是 unknown"可分辨。
