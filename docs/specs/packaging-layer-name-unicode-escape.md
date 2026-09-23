# Spec：图层名里的 `_U+XXXX` 转义从不还原 —— 转义过的中文刀线层判成 unknown，界面看不出是转义造成的

状态：Spec + 红测（已实现）（原状：本批只加"图层名归一化"这一层 —— `roles.py` 缺纯函数，
匹配/披露走的还是图上的转义原名；规则表、角色语义、`cad_ir` 的文字归一化仍然不改）
红测：`tests/test_packaging_layer_name_unicode_escape_red.py`
血缘：`packaging-product-outline-and-die-layer-roles.md` §1.1/§1.2（图层角色；`DESIGN`/`Defpoints`
不得被判成刀线压线）、`packaging-dwg-parts-extraction.md`（层角色是零件/成品的上游）、
`cad_ir` 的 `normalize_text()`（`\U+XXXX` 解码**已有先例，但只服务文字**，图层名从不经过它）。
本批 changelog 条目号：`## 449`（缺口类，作者侧）；落地条目见 `## 452`。

## 0. 一句话目标

转换器把中文图层名写成 `_U+XXXX…` 转义（LibreDWG 的写法）或 `\U+XXXX`（AutoCAD 写法）时，
语义层必须**先把名字还原再匹配规则**，并把还原结果如实披露；否则同一份 DWG 在不同转换器下
"哪个层是刀线/压线"会给出不同答案，而界面只会显示 `role=unknown`，看不出是转义造成的。

## 1. 真实跑证据（本机，HEAD 工作副本；`tech_app/tools/dwg_sample_e2e.py` + `cad_ir` + `packaging_semantics`）

本机转换器是 LibreDWG（`/opt/homebrew/bin/dwg2dxf`，ODA 不在本机），两份样本都真跑了一遍：

| 样本 | 真实层清单（转换产物的真实名字） |
| --- | --- |
| `酒盒.dwg`（8 层） | `0` / `CUTTER` / `DESIGN` / `Defpoints` / `SAMPLE` / **`_U+56FE_U+5C42 1`** / `图层 2` / `轮廓线` |
| `圆盘盒.dwg`（32 层） | 含 `全穿刀` / `压线 Crease` / `图框层` / `排图层`（今天角色分别 `cut` / `crease` / `frame` / `frame`），也含 **`_U+56FE_U+5C42 1`** |

`_U+56FE_U+5C42 1` 就是 `图层 1`（U+56FE=图、U+5C42=层）。**同一份 `酒盒.dwg` 里同时出现
`图层 2`（明文）与 `_U+56FE_U+5C42 1`（转义）** —— 说明这不是客户命名习惯，是转换器的转义行为。

今天 `_U+56FE_U+5C42 1` 的角色是 `unknown` / `role_source="none"`（规则表里当然没有这个名字）。
本次被转义的恰好不是规则层，所以还没出事故；但同一个转换器只要把 `全穿刀` 写成
`_U+5168_U+7A7F_U+5200`（或把 `压线 Crease` 写成 `_U+538B_U+7EBF Crease`），切/压线角色立刻
判成 `unknown`：**刀线与压痕线分不出 → 盒型候选 / 成品轮廓 / 零件链路一起退化**，而界面只显示
一个 unknown，排查方向完全指不到"名字被转义了"。

同一趟真实跑还暴露一条**判据问题**（不是产品缺陷，一并记进本 Spec §2.4）：
`tests/test_packaging_product_outline_red.py::test_d1_real_layer_roles_are_recognised` 对**酒盒**
断言存在图层 `Make2D$可见线$普通线`，实测 `酒盒.dwg` 只有上表那 8 层、**根本没有 Make2D 层**
（`Make2D$可见线$普通线` / `Make2D$注解` / `Make2D$隐藏线$普通线` 是 `圆盘盒.dwg` 才有的层）。
于是真样本 D 组在真实样本集上恒红：`AssertionError: 酒盒.dwg 里没有图层 Make2D$可见线$普通线`
—— "这一份图里没有这一层"被 `self.fail()` 报成"角色判错"，真样本验收信号因此不可用。

## 2. 契约

### 2.1 新纯函数 `packaging_semantics.roles.normalize_layer_name(text)`

- 还原两种转义：`_U+XXXX`（LibreDWG，**下划线前缀**、大小写不敏感、可连续多个、可带尾随文字）与
  `\U+XXXX`（AutoCAD 文字那套口径，与 `cad_ir.normalize_text()` 同一码位规则）；
  `_U+56FE_U+5C42 1` → `图层 1`；`_U+538B_U+7EBF Crease` → `压线 Crease`；
- 其余字符**原样**（空格、`$`、中文、ASCII 都不动），不做大小写/空白归一（那是 `_normalized()`
  的比较口径，本批不改）；
- 残缺转义（`_U+ZZZZ`、`_U+56F`、结尾孤立的 `_U+`、空串、`None`）**不许抛异常**：
  解不出的那一段逐字保留，其余照常；
- 纯函数：不读文件、不连库、不联网、不打日志。

### 2.2 匹配与披露（`roles.resolve_layer()` / `resolve_layer_roles()`）

- `_match_rule()` 用**还原后的名字**匹配规则（`names` / `name_prefix` / `name_contains` 三条口径
  一律不变），未出现转义时行为与今天逐字相同（`CUTTER` 仍靠 `name_prefix` 命中）；
- 每一行**新增键** `name_normalized`（键必须存在；没有转义时逐字等于 `name`）；
  `name` 仍是**原名**逐字不改（界面/证据要能对回图上的层名）；
- 证据引用仍是 `ev:L:<原名>`（**不许**换成还原名，否则 CAD IR 里回查不到）；
- 未命中清单（`unresolved.layers` 与 `roles_summary` 的 unknown 名单）仍用**原名**；
- `stats` 的既有键与数值口径不变（`layer_total` / `cut_layer_total` / `crease_layer_total` …）。

### 2.3 非目标

- 不许改 `packaging_layer_rules.json`（规则表仍写可读的中文层名，**不许**在表里加转义别名）；
- 不许改 `cad_ir` 的 `normalize_text()`（它服务文字实体），不许在 CAD IR 解析阶段改层名；
- 不许改角色闭集、置信度上界、`colors` / `line_types` 弱证据口径；
- 不许放宽 §1.2 的"不得误判"判据（`DESIGN` / `Defpoints` / `Make2D$…` 仍不许被判成 cut/crease）；
- 不许动后端接口形状（`layers[]` 只加 `name_normalized`，不删不改既有键）。

### 2.4 真样本验收判据（同期需要）

"某一层在不在图里"必须按**该份图自己真实存在的层**判：`酒盒.dwg` 的判据不许要求
`Make2D$可见线$普通线`（它属于 `圆盘盒.dwg`），也不许把"层不在图里"和"角色判错"混成同一句
`fail` —— 前者是"这份图没有这一层"（记录/前置条件不满足），后者才是角色识别缺陷。
本批只写 Spec 与红测；既有红测的改写由实现方按本 Spec 决定（测试文件本批不动）。

## 3. 红测映射（`tests/test_packaging_layer_name_unicode_escape_red.py`，8 条）

| 用例 | 夹具 | 期望 | 现状 |
| --- | --- | --- | --- |
| W1 | `_U+56FE_U+5C42 1` | `图层 1`（纯函数存在） | 红（函数不存在） |
| W2 | `_u+56fe_u+5c42 1`（小写）/ `\U+56FE\U+5C42`（反斜杠写法） | 都还原成 `图层 1` / `图层` | 红 |
| W3 | 残缺转义（`_U+ZZZZ` / `_U+56F` / 末尾 `_U+` / `None`）+ 普通名 | 不抛异常；普通名逐字不变 | 红 |
| W4 | 规则表里的中文层名换成转义写法（`_U+5168_U+7A7F_U+5200` / `_U+538B_U+7EBF Crease` / `_U+56FE_U+6846_U+5C42`） | 角色仍分别 `cut` / `crease` / `frame` | 红 |
| W5 | 一行转义层 | `name` 逐字原名 + `name_normalized` 键存在且已还原；`evidence_refs` 仍含 `ev:L:<原名>` | 红 |
| W6 | 两份真实样本（`CPQ_DWG_REAL_SAMPLES=1`） | 产物里存在 `_U+56FE_U+5C42 1`；`name_normalized` 里不再含 `_U+` / `\U+`；圆盘盒四条规则层角色不变 | 红（没有该键） |
| W7 | 两份真实样本的层清单事实钉 | `酒盒.dwg` **没有** `Make2D…` 层；`圆盘盒.dwg` 有 `Make2D$可见线$普通线` | 护栏（今天绿，钉住 §2.4 的事实） |
| W8 | `CUTTER` / `全穿刀` / `压线 Crease` / `DESIGN` / `Defpoints` | 角色与今天逐字相同（`cut` / `cut` / `crease` / 非 cut-crease） | 护栏（今天绿） |

## 4. 实测（本机，HEAD 工作副本）

```text
tests.test_packaging_layer_name_unicode_escape_red                        Ran 6 … FAILED (failures=5, skipped=1)
CPQ_DWG_REAL_SAMPLES=1 tests.test_packaging_layer_name_unicode_escape_red Ran 8 in 9.163s … FAILED (failures=6)
```

- 红的是 W1–W6：W1/W2/W3 `AssertionError: 缺少纯函数 …roles.normalize_layer_name()（Spec §2.1）`；
  W4 `'unknown' != 'cut'`（`_U+5168_U+7A7F_U+5200` 这种被转义的规则层判成 unknown）；
  W5/W6 `'name_normalized' not found in {…}`。绿的是 W7（真样本层清单事实钉）与 W8（既有角色不变）。
- 未设 `CPQ_DWG_REAL_SAMPLES=1` 时 `Ran 6 (skipped=1)`：真样本类整体跳过（1 条 skip 计在类上），
  所以跑的是 W1–W5 + W8。
- W6 的真跑读数（本机 LibreDWG，两份样本都真转换）：`酒盒.dwg` 的层行里
  `{'name': '0', 'role': 'unknown', 'role_source': 'none', 'entity_count': 3725, 'evidence_refs': ['ev:L:0']}`、
  转义层那行 `{'name': '_U+56FE_U+5C42 1', 'role': 'unknown', 'role_source': 'none', 'evidence_refs': ['ev:L:_U+56FE_U+5C42 1']}`
  —— 两行都没有 `name_normalized`，**转义层与"图上真有个怪名字的层"在产物里完全同形**。
- 不回归：`test_packaging_semantics_red` `Ran 59 OK (skipped=1)`、`test_packaging_parts_extraction_red`
  `Ran 32 OK`、`test_packaging_product_outline_red` `Ran 21 OK (skipped=1)`、`test_spec_status_truth_red`
  `Ran 7 OK`。
- §2.4 那条判据缺陷同期实测复现：`CPQ_DWG_REAL_SAMPLES=1 tests.test_packaging_product_outline_red`
  = `Ran 27 in 9.278s … FAILED (failures=1)`，唯一失败是
  `RealSampleProductOutline.test_d1_real_layer_roles_are_recognised`
  → `AssertionError: 酒盒.dwg 里没有图层 Make2D$可见线$普通线`（既有测试文件，本批未改一行）。

## 5. 交付边界

本批只写 Spec + 红测（业务实现不在本批）；未改业务实现、未改既有测试、未放宽任何断言、
未起服务、未连 34 / PG、样本只读（只在临时目录产出 DXF）、未 push / MR / tag / Release / 未部署。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 452`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 纯函数 | `packaging_semantics/roles.py:normalize_layer_name()` | 新常量 `_LAYER_ESCAPE = re.compile(r"[_\\][Uu]\+([0-9A-Fa-f]{4})")`：`_U+XXXX`（下划线前缀、**大小写不敏感**、可连续多个、可带尾随文字）与 `\U+XXXX`（AutoCAD 那套）一起还原；只认**完整 4 位**十六进制码位，残缺写法（`_U+ZZZZ` / `_U+56F` / 结尾孤立 `_U+`）与 `None` / 空串一律逐字返回、绝不抛；其余字符（空格 / `$` / 中文 / ASCII）一字不动 |
| §2.2 匹配走还原名 | `roles._match_rule()` | `name = _normalized(normalize_layer_name(layer.get("name")))` —— `names` / `name_prefix` / `name_contains` 三条口径一字未改，规则表里仍写可读中文层名；没有转义时行为与今天逐字相同（`CUTTER` 仍靠 `name_prefix` 命中） |
| §2.2 如实披露 | `roles.resolve_layer()` 四类行 | 每行新增键 `name_normalized`（规则命中 / 颜色命中 / 线型弱证据 / unknown 四支都有）；`name` 仍是**原名**逐字，`evidence_refs` 仍是 `ev:L:<原名>`，未命中名单与 `stats` 口径不变 |
| §2.3 非目标 | 未动 | `packaging_layer_rules.json` / `cad_ir.normalize_text()` / 角色闭集 / 置信度上界 / `colors`/`line_types` / `DESIGN`·`Defpoints` 的"不得误判"判据，一行未改 |
| §2.4 真样本判据 | `tests/test_packaging_product_outline_red.py::test_d1`（**重指**） | 按本 Spec 把"不得误判"那一半改成**按该份图真实存在的层**判：`Make2D$可见线$普通线` 是 `圆盘盒.dwg` 的层，`酒盒.dwg` 里没有它 —— "层不在图里"是前置条件不满足，不再 `self.fail()` 冒充"角色判错"；层只要在图里，角色仍逐个断言（重指≠放宽）。新增 `layers_of()` 只读辅助，`role()` 的 fail-fast 一字未改 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_layer_name_unicode_escape_red
Ran 6 tests ... OK (skipped=1)          # 红基 5 红（W1–W5）+ 1 skip；W6/W7 真样本组默认跳过

CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_layer_name_unicode_escape_red
Ran 8 tests in 9.4s ... OK             # 红基 6 红 → 8 全绿（两份真实样本本机 LibreDWG 真转换）

CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_product_outline_red
Ran 27 tests in 9.8s ... OK            # 红基 26 OK + 1 红（§2.4 那条恒红判据）→ 27 全绿

./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_semantics_red \
    tests.test_packaging_parts_extraction_red tests.test_packaging_layer_name_unicode_escape_red \
    tests.test_packaging_product_outline_red tests.test_packaging_drawing_flow_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red tests.test_spec_status_truth_red
Ran 193 tests ... OK (skipped=4)       # 状态行同步后 c2 不再报"声明未实现却已全绿"
```

红测自身缺陷：无。
