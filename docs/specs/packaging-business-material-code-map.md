# 规格：材料原文 → 材料码的**唯一映射表**，以及"该补哪一条"的可执行缺口

依赖：`docs/specs/packaging-bom-business-material-rows.md`（§7 边界 2/3 明写"材料行只报
**能不能解析到材料码**"、`material_unresolved` 是业务数据待办；本批就是那条待办的通路）、
`docs/specs/packaging-drawing-semantics.md`（同一套"配置只有一个来源、读不到不许静默回落"
的口径：`packaging_semantics/rules.py` 的 `PACKAGING_LAYER_RULES_INVALID`）、`docs/specs/packaging-silent-degradation-disclosure.md`。

状态：Spec + 红测（已实现）（原状：材料码只有一处解析口径 `packaging_bom._resolve_material_code()`
（`:377-386`）—— **取 `material` 第一个空白分词、在 `kb_material.name` 里唯一包含才算命中**；
客户原文（真样本如 `350G玖龙粉灰`、`EVA`、`磁铁`）大多整串无空白、或分词落在多个候选上，
于是解析不到就只剩一句 `material_unresolved`（`:1192` / `:1330`）——
**"怎么补"没有任何一处可维护的落点**，业务/采购就算想给映射也没地方写（`agent_knowledge/rules/`
下没有材料映射文件，`entries` 在前端与接口上都不存在）；
`gaps.material_unresolved` 只给 `item_key` 清单，不区分"没有候选"与"多个候选"，
也不给动作 —— 看到"还有 12 行没映射"的人不知道该改哪里）
红测：`tests/test_packaging_material_code_map_red.py`
行号基线：HEAD `67a4a58`

## 0. 一句话目标

材料码的映射**有一个可维护、可审计、可回查的地方**（客户原文 → 材料码），
解析优先级是"映射表优先、既有规则兜底"；解析不到的每一行在缺口里都带**可执行动作**
（补哪一条映射 / 核对哪一条），且映射表读不到时**如实说读不到**（不许假装"没有映射"）。

## 1. 现状缺口（代码级）

1. `_resolve_material_code()` 只有一个"分词包含"规则：没有别的候选来源，业务给不出映射；
2. `agent_knowledge/rules/packaging_material_code_map.json` **不存在** → 映射无处可写；
3. `gaps.material_unresolved`（`:1330`）只是 `item_key` 列表：不分"没有候选 / 多个候选"，
   也没有 `action`；`business_material_rows` 那把账（`:554-565`）只说"几行解析到了"，
   不说"其中几行是映射给的"；
4. 没有任何地方披露"映射表读不到"（真读不到时，界面上会与"业务还没给映射"长得一模一样）。

## 2. 契约

### C1 唯一映射来源（新文件 + 新模块）

- 新增 `tech_app/agent_knowledge/rules/packaging_material_code_map.json`：
  `{"rule_set": "packaging_material_code_map_v1", "review_status": "draft", "note": "...",
    "entries": []}` —— `entries` 每项 `{"text": "<客户原文>", "material_code": "<材料码>", "note": ""}`；
  **本批不改动业务数据**：`entries` 保持空表（映射由业务/采购签字后再填）。
- 新增 `tech_app/backend/services/packaging_material_map.py`：
  - 常量：`ENGINE_VERSION = "packaging-material-map/1"`、`MATERIAL_MAP_FILENAME`、
    `RULE_SET = "packaging_material_code_map_v1"`、`ENV_MATERIAL_MAP_PATH`（env 覆盖）、
    `MATERIAL_MAP_ERROR_CODE = "PACKAGING_MATERIAL_CODE_MAP_INVALID"`、
    `LOOKUP_STATUSES = ("hit", "missing", "ambiguous")`；
  - `MaterialMapError(message)`：带 `.code` 与 `.stable_error_code`（两个名字都给，
    与既有 `LayerRulesError` 同形）；
  - `map_path() -> (Path, source)`（`default` = 仓库内置 / `override` = env）；
  - `read_material_map(path=None) -> (entries, source, fingerprint)`：**缺失 / 非法 JSON /
    `rule_set` 不对 / `entries` 不是数组 / 条目缺 `text` 或 `material_code`** 一律抛
    `MaterialMapError`（**不许静默回落成空表**，与 `rules.py` 同一条纪律）；`fingerprint` =
    文件字节的 sha256 前 12 位；
  - `normalize_material_text(text)`：去掉**所有**空白（含全角空格 `\u3000`）、ASCII 转小写、
    trim —— 映射的键一律过它（"350G 玖龙粉灰" 与 "350g玖龙粉灰" 是同一个键）；
  - `lookup_material_code(text, entries) -> (code, status)`：`hit` / `missing` /
    `ambiguous`（同一个规范化键出现**两个不同码**时 `ambiguous`，**不许猜**）。

### C2 解析优先级：映射优先、既有规则兜底（口径只加不改）

- `packaging_bom.resolve_material_code(text, rows, *, map_entries=None) -> str`（新函数，
  与私有的 `_resolve_material_code()` 并存）：
  1. `lookup_material_code()` 命中 → 该码**当且仅当**它出现在本次材料清单 `rows` 里（按
     `material_code` 比）才用；命中但码不在清单里 → 视为**没解出来**（落 C3 的缺口，不许用）；
      `ambiguous` 同样不给码；
  2. 映射没给 → 逐字调用既有 `_resolve_material_code()`（**一行都不改**）。
- 写路径（`_assemble()` → `business_material_rows()`）改用 `resolve_material_code(...,
  map_entries=...)`；`materials` 为空（知识库读不到）时行为与今天逐字相同（材料码仍留空串）。
- `_resolve_material_code()` 本体、`material_unresolved` 的项与顺序、成本侧材料口径**一字不动**。

### C3 账与缺口：说清"哪几行是映射给的"、每行"怎么补"

- 读路径新增纯函数 `packaging_bom.classify_material_resolution(item, *, map_entries=None,
  map_available=True) -> str`，**只报能从行 + 映射表推出的事实**（闭集）：
  | 情形 | 档 |
  | --- | --- |
  | 行有码，且映射表里这条原文正好给出这个码 | `map_hit` |
  | 行有码，但映射表没给（既有分词规则解的） | `legacy_hit` |
  | 行没码，映射表里也没有这条原文的键 | `map_key_missing` |
  | 行没码，但映射表里有这条原文的键（写了没生效：码不在材料清单，或这版 BOM 早于映射） | `map_entry_not_applied` |
  | 映射表读不到（`map_available=False`） | `map_unknown` |
- `_business_material_scope(items, *, map_entries=None, map_available=True)`：既有四键
  （`row_total` / `resolved_total` / `unresolved_total` / `keys`）**一字不动**，新增
  `reason_counts`（只放非零档）、`map_hit_total`、`map_unavailable`（读不到时 `{code, message}`，
  否则 `{}`）。
- `load_bom()` 的 `gaps` 新增 `material_unresolved_detail`（**加法**，`material_unresolved`
  不动）：逐条 `{"item_key", "reason", "action"}`，顺序与 `material_unresolved` 一致；
  `action` 是**可执行动作**：`map_key_missing` → 在 `packaging_material_code_map.json` 的
  `entries` 里补"这条原文 → 材料清单里的码"；`map_entry_not_applied` → 核对映射表里的码是否
  真在材料清单里，然后重算 BOM / 成本；`map_unknown` → 先修映射表（读不到），再重算。
- 映射表读不到时：`entries = []`、`map_available = False`、`map_unavailable` 带稳定码与
  message；**解析仍按既有规则进行**（生成 BOM 不许因为"映射表坏了"整体失败），
  但账上如实写 `map_unknown`。

### C4 冻结面

- `_resolve_material_code()` 一行不改；`entries` 为空时 `resolve_material_code()` 的返回值与
  今天逐字相同；
- `material_unresolved`（项 / 顺序 / 计数）与 `_stats()` 的口径不变；
- 成本侧（`packaging_cost.py`）与材料价口径不变；`main.py` 不需要改（`load_bom()` 的返回体
  原样透传）；
- 不改 `tests/` 下任何文件；不新增第三方依赖、不联网、不调模型。

## 3. 允许修改范围

1. 新增 `tech_app/agent_knowledge/rules/packaging_material_code_map.json`（空 `entries`）；
2. 新增 `tech_app/backend/services/packaging_material_map.py`；
3. `tech_app/backend/services/packaging_bom.py`：`resolve_material_code()` /
   `classify_material_resolution()` / `business_material_rows()` 的 `map_entries` /
   `_assemble()` 读映射 / `_business_material_scope()` 三键 / `load_bom()` 的
   `gaps.material_unresolved_detail`；
4. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许在代码里硬编码任何"原文 → 材料码"的对照（映射只能来自那个 JSON，业务签字后才填）；
- 不许让映射表**跳过**材料清单校验（映射写了个不存在的码就说"解析成功"）；
- 不许静默回落：映射表缺失 / 非法 / `rule_set` 不对一律 `MaterialMapError`；
- 不许改既有分词规则的语义、不许把 `material_unresolved` 并进 `needs_input`；
- 不许为了转绿改任何 `tests/` 文件；不连 PG / 34、不写业务数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_code_map_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_material_rows_red tests.test_packaging_bom_business_parts_rows_red \
  tests.test_packaging_parametric_bom_red tests.test_packaging_semantics_red \
  tests.test_packaging_cost_engine_red
```

## 6. 已记录的边界

1. 本批只做**通路**：`entries` 是空表，真映射由业务/采购签字后填（填完不需要改代码）；
2. 材料码**不为空**不等于"可计价"：价格与计价单位仍是 `packaging-cost-gaps-closure.md` §1.1
   登记的待办；
3. 读路径只报"行 + 映射表"能推出的事实；"为什么没解出来（0 候选 / 多候选）"由写路径的
   `resolve_material_code()` 负责，本批不把它持久化（那要动 BOM 表结构）；
4. 前端不改：缺口与账都已在 `GET .../requirement/packaging-bom` 里，界面接入是下一批；
5. 一份映射表服务全部项目（不按项目分表）；多行业的材料清单仍由知识库按行业给。

## 7. 落地状态（2026-09-22，Codex 实现）

红基（实现前：`git stash push -- tech_app/backend/services/packaging_bom.py` +
把新模块与映射表临时移走）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_code_map_red
Ran 26 tests … FAILED (failures=9, errors=15)          # 24 红 / 2 绿
```

- failures=9：`A1`–`A4`（映射表文件与模块常量都不存在）、`B1`–`B5`（读取 / 规范化 / 三级查表都没有）；
- errors=15：`C1`–`C6`、`D1`–`D6`、`E3`、`E4` —— 全是 `AttributeError` / 文件不存在
  （`resolve_material_code` / `classify_material_resolution` / `material_unresolved_detail`
  / `map_entries` 参数一个都没有）；
- 2 绿全是护栏：`E1`（既有分词规则源码逐字未动）、`E2`（既有四键形状未变）。

实现后：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_code_map_red
Ran 26 tests in 0.005s
OK
```

| 契约 | 落点 |
| --- | --- |
| §C1 唯一映射表 | 新增 `tech_app/agent_knowledge/rules/packaging_material_code_map.json`（`rule_set=packaging_material_code_map_v1`、`review_status=draft`、**`entries` 空表**——不替业务填数）；新增 `tech_app/backend/services/packaging_material_map.py`：`ENGINE_VERSION` / `MATERIAL_MAP_FILENAME` / `RULE_SET` / `ENV_MATERIAL_MAP_PATH` / `MATERIAL_MAP_ERROR_CODE` / `LOOKUP_STATUSES` / `MaterialMapError`（`.code` 与 `.stable_error_code` 都给）/ `map_path()`（`default` / `override`）/ `read_material_map()`（缺失·非法 JSON·`rule_set` 不对·条目缺键一律抛）/ `normalize_material_text()`（去**所有**空白含全角、ASCII 转小写）/ `lookup_material_code()`（`hit` / `missing` / `ambiguous`）/ `map_facts()`（读不到**不抛**但如实带 `unavailable`） |
| §C2 解析优先级 | `packaging_bom.resolve_material_code(text, rows, *, map_entries=None)`：映射命中且码在本次材料清单里才用、命中但码不在清单里 → 不给码、`ambiguous` → 走兜底；映射没给 → 逐字 `_resolve_material_code()`（源码未动，`E1` 守）。写入两条分支同口径：`business_material_rows(..., map_entries=...)`（权威清单）与 `_assemble()` 的模板展开兜底分支 |
| §C3 账与缺口 | `classify_material_resolution(item, *, map_entries, map_available)`（五档闭集 `MATERIAL_RESOLVE_REASONS`）；`material_unresolved_detail(items, ...)`（逐条 `{item_key, reason, action}`，动作点名 `packaging_material_code_map.json`）；`_business_material_scope(..., map_entries, map_available, map_unavailable, map_source, map_fingerprint)`（既有四键不动，新增 `reason_counts` / `map_hit_total` / `map_source` / `map_fingerprint` / `map_unavailable`）；`material_map_facts()`；`load_bom()` 开头读一次映射事实（读不到不抛）、`gaps` 新增 `material_unresolved_detail`（`material_unresolved` 与计数一字不动） |
| §C4 冻结面 | `_resolve_material_code()` 一行未改；`entries` 为空时 `resolve_material_code()` 与今天逐字相同（`C6` 逐条比）；`main.py` 未改（`load_bom()` 的返回体原样透传）；成本侧未动；未改 `tests/` 下任何文件；未加依赖 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_material_rows_red tests.test_packaging_bom_business_parts_rows_red \
  tests.test_packaging_parametric_bom_red tests.test_packaging_semantics_red \
  tests.test_packaging_cost_engine_red tests.test_packaging_material_code_map_red
Ran 261 tests … OK (skipped=1)

# packaging 全域：
Ran 2158 tests … FAILED (failures=5, skipped=8)
# 正是既有 5 条挂账（part_role_mapping_reaches_card::A2、bom_part_size_provenance::B3、
# parse_to_downstream_seams::B4、quote_send_recovery::C1、route_bom_version_pinning::F2）
```

边界（与 §6 一致）：`entries` 空表，真映射由业务/采购签字后填（填完不用改代码）；
映射跳过不了材料清单校验；读不到映射表时 BOM 照算、账上写 `map_unknown` 与稳定码；
未连 PG / 34、未写业务数据、未 push / MR / tag / Release / 部署。
