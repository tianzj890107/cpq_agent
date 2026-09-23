# Spec：件名里的 MTEXT 格式码必须被剥掉（圆盘盒 39/66 件名带 `\C1;`，镜像重复件因此被当成两件）

状态：Spec + 红测（已实现）（原状：`_strip_mtext_codes()` 只剥**带花括号**的 `{\C0;…}`，
真样本不带花括号的写法把格式码原样留进件名 —— 实测见 §1）
红测：`tests/test_packaging_part_name_mtext_codes_red.py`
血缘：`docs/specs/dxf-cad-ir.md`（IR 层的 `normalize_text()` **早就**剥了码，`normalized_text`
一条都不带码 —— 本批只是把业务件名这一层对齐）、
`docs/specs/packaging-parts-must-be-derived-from-the-drawing.md`（件名这一层的来源闭集）、
`docs/specs/packaging-business-part-size-must-be-confirmed-by-dimension.md`（**本批 supersede 它的
`DERIVED_TOTAL` / `SIZED_TOTAL` 的圆盘盒那一格：39 → 38**，理由见 §5）、
`docs/specs/packaging-business-parts-outline-bbox-broken-link.md`（同上，「修后 39/66」这个读数）
本批 changelog 条目号：`## 475`。

## 0. 一句话目标

`packaging_business_part_resolver._strip_mtext_codes()` 只认 `{\C0;旧款大货色位不够` 这种**带花括号**
的形式；真样本 `圆盘盒.dwg` 里 39/66 个件名是 `\C1;地盒内圈衬纸2` / `\fArial|b0|i0|c0|p34;\W1;402X50.5MM…`
这种**不带花括号**的写法，格式码就这样进了件名 —— 用户界面上看到的是 `\C1;盖内圈衬纸1`，
而且同一个零件的两次排版（原图 + 镜像，MTEXT run 切法不同）被当成两个不同的件。

## 1. 实测证据（本机，只读；两份真样本 DXF）

样本：`酒盒` `47c39dc1ab6738fc48c8`（sha `0991c8b0…93f3e0`）、
`圆盘盒` `a6140fbc4e9b8d2e9bee`（sha `4c70ce7b…4a531b`）；
路径 `tech_app/data/cad-ir-realsample/conversions/<cid>/converted.dxf`。
`resolve_business_parts("red-test", ir, parts_doc)` → `authority_rows`。

| 读数 | 酒盒（改前 = 改后） | 圆盘盒 改前（HEAD `0312614`） | 圆盘盒 改后（本批） |
| --- | --- | --- | --- |
| `authority_rows` 行数 | 28 | 66 | **65** |
| `status == "derived"` | 26 | 39 | **38** |
| 有长宽的件（`sized`） | 26 | 39 | **38** |
| **件名带 MTEXT 码的件数** | 0 | **39** | **0** |
| 件名里含反斜杠的件数 | 0 | **39** | **0** |
| `material_text` / `process_text` 带码 | 0 / 0 | 0 / 0 | 0 / 0 |
| 锚点 `kept` 数 / 其中件名带码 | 37 / 0 | 161 / **84** | 161 / **0** |

带码件名的原文与改后件名（各自逐字）：

| 锚点原文（截断） | 改前件名 | 改后件名 |
| --- | --- | --- |
| `{\fSimSun\|b0\|i0\|c134\|p2;\C1;地盒内圈衬纸2}` | `\C1;地盒内圈衬纸2` | `地盒内圈衬纸2` |
| `\fSimSun\|b1\|i0\|c134\|p2; BC坑` | `\fSimSun\|b1\|i0\|c134\|p2; BC坑` | `BC坑` |
| `\C1;10PC圆盒 内托面卡：350g单粉 …` | `\C1;10PC圆盒 内托面卡` | `10PC圆盒 内托面卡` |
| `\fArial\|b0\|i0\|c0\|p34;\W1;402X50.5MM\fAdobe…;高\fArial…;/\fAdobe…;厚度\fArial…;2MM` | 原样带码 | `402X50.5MM高/厚度2MM` |
| `\c2367469;1\C1;0PC圆盒 盖/地内外圈围边衬纸1/2：250g双铜 860*460mm 各排2模 2024-04-06` | 原样带码 | `10PC圆盒 盖/地内外圈围边衬纸1/2` |

**66 → 65 的那一行是"镜像重复件被合并"，不是丢件**（`## 474` 同一族问题）：

- `ent:model:8C665` 原文 `\C240;\c2367469;1\C1;0PC圆盒 盖/地内外圈围边衬纸1/2：250g双铜 860*460mm 各排2模 2024-04-06`
- `ent:model:8D60F` 原文 `\C240;\c2367469;\C1;10PC圆盒 盖/地内外圈围边衬纸1/2：250g双铜 860*460mm 各排2模 2024-04-06`

两条的**件名与材料逐字相同**（材料都是 `250g双铜 860*460mm 各排2模 2024-04-06`），只是 MTEXT run
被切在 `1` 与 `0PC` 之间；`_derived_rows()` 按**件名**分组，所以它们本来就是同一件。
改前它们"不同"纯粹是因为格式码残留；改后归并 —— 与图纸上其它镜像对（`地盒底贴面纸` ×2、
`外层倒扣内衬纸` ×2 …）的处理完全一致。

## 2. 契约

### 2.1 C1：件名不许带 MTEXT 控制序列

`_strip_mtext_codes()` 必须**同时**剥掉两种写法：

- 带花括号：`{\C0;旧款大货色位不够` → `旧款大货色位不够`（老口径，不许回退）；
- 不带花括号：`\C1;地盒内圈衬纸2` → `地盒内圈衬纸2`、
  `\fSimSun|b1|i0|c134|p2; BC坑` → `BC坑`、
  `\c2367469;1\C1;0PC圆盒 …` → `10PC圆盒 …`。

剥完剩下的件名里**不许**再出现 `\` + 字母 + `;` 这种控制序列（`\C…;` / `\c…;` / `\f…;` / `\W…;` / `\H…;`）。

### 2.2 C2：`\P` / `\p` 是**折行**，不是格式码

`\P` 是 MTEXT 的换行符，由 `_SEGMENT_SEEDS` / `LABEL_CUTS` 当分隔符用（真样本
`内托支撑围条灰板\P650G灰板\P正面图，啤面` → 件名 `内托支撑围条灰板`、材料 `650G灰板`、工艺 `啤面`）。
格式码正则必须**显式排除** `\P` / `\p`：

- 反例（不许回归）：`内托支撑折板\P250G白卡纸;860*500=40M` 里 `\P` 后面那半段里的**半角 `;`**
  不许被当成"格式码的结尾" —— 件名必须是 `内托支撑折板`、材料必须留着 `250G白卡纸`，
  不许变成 `内托支撑折板860*500=40M`。

### 2.3 C3：剥码必须在 `_width_normalized()` **之前**

两个调用点（`_label_head()` / `split_label_parts()`）的顺序必须是
`_width_normalized(_strip_mtext_codes(text))`，理由是**与 IR 层同一条口径**：`normalize_text()`
也是在**原样文本**上剥码，不做任何宽度归一化。`_width_normalized()` 会把全角 `；` 归一成半角 `;`，
先归一化再剥码就等于让"归一化"参与了"什么是格式码"的判断 —— 那正是 C2 反例里那条链。
配上 C2 的 `\P` 保护以后，这个顺序在**今天两份真样本上不产生可观测差异**（两份样本
`；` 出现 0 次、全角反斜杠 0 次），所以 §4 的反向对照 3 **不会**让任何一条转红 —— 如实记录，
不假装它是一条红基。

### 2.4 C4：口径与 IR 层一致

同一份原文，本函数剥完的结果不许与 `cad_ir.parser.normalize_text()` 的"去码"这一步相矛盾
（IR 层的 `normalized_text` 实测 153 条带码文本**一条都不带码** —— 业务件名这一层只是对齐）。

### 2.5 C5：其它层不被本批改变

- 件名的**截断**（`LABEL_CUTS`）、材料/工艺拆分（`split_label_parts()`）、别名表（`ALIASES`）、
  排除规则（`_exclusion_reason()`）、锚点排序（`## 474`）**一个字不改**；
- `material_text` / `process_text` 本批实测 0/66 带码，改前改后都是 0 —— 不许因为本批反而带码；
- `packaging_parts.extract()` / BOM / 成本 / 门禁的判据与公式不改。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_business_part_resolver.py`：`_strip_mtext_codes()` 的
   正则与两处调用顺序；新增模块级 `_MTEXT_CODES`。
2. `tests/test_packaging_business_part_size_must_be_confirmed_by_dimension_red.py`：
   **只允许**把 `DERIVED_TOTAL` / `SIZED_TOTAL` 的圆盘盒那一格 `39` 改成 `38`（§5）；
   断言结构、门槛（`MIN_CONFIRMED` / `MIN_UNCONFIRMED`）与其它组的期望值**一个字不改**。

禁止：改 `cad_ir`（IR 层没有这条缺口）；改任何排除/截断/别名口径；改 `packaging_parts.py`、
BOM、成本、门禁；改本批以外的任何测试；为了让某条红转绿而放宽断言。

## 4. 红测与反向对照

红测：`tests/test_packaging_part_name_mtext_codes_red.py`（A 真样本红基 / B 酒盒护栏 /
C `\P` 与全角 `；` / D 花括号老口径 / E 口径护栏）。

- 绿基线（本批落地后）：`Ran 22 tests … OK`；
- 红基（把 `_strip_mtext_codes()` 还原成 HEAD 口径）⇒ `Ran 22 … FAILED (failures=9)`，
  红的正好是 A1–A6（件名带码 39/66、行数 66、锚点 84 条带码）+ D1（`{\f…;\C1;…}` 只剥了第一段码）
  + E1（模块里还没有 `_MTEXT_CODES`）+ E4（剥完还留控制序列）；B/C 两组 13 条绿；
- 反向对照 2（只把 `(?![Pp])` 去掉，其余保持本批）⇒ C1 FAIL
  （件名变成 `内托支撑折板860*500=40M`、材料被吞）—— 这条证明 C2 的折行保护是**真在起作用**；
- 反向对照 3（把调用顺序倒回 `_strip_mtext_codes(_width_normalized(…))`）⇒ **22 条全绿，不转红**：
  两份真样本里 `；` 与全角反斜杠各出现 0 次，顺序差异今天没有观测量。如实记录（§2.3 的
  理由是同 IR 层的自洽，不是一条可判别行为）。

## 5. 本批 supersede 的口径：圆盘盒 `derived` / `sized` 39 → 38

`packaging-business-part-size-must-be-confirmed-by-dimension.md` §2 与它的红测 E1 写死了
「酒盒 derived 26、圆盘盒 39 不变」。那个 39 里的**一件是格式码造出来的假件**（§1 的
`ent:model:8C665` / `8D60F` 镜像对）。本批把假件归并回它的镜像，因此：

- `derived` 39 → **38**、`sized` 39 → **38**、行数 66 → **65**（酒盒 26 / 26 / 28 不变）；
- 确认/未确认两档（`MIN_CONFIRMED = 6`、`MIN_UNCONFIRMED = 25`）都还满足（实测 8 / 31 未变）；
- 绑定侧门槛 `MIN_DERIVED["圆盘盒"] = 31` 仍满足（38 ≥ 31）。

这是**口径变化**，按那份测试自己的话「禁止为了让红测转绿而修改本文件；**口径变化请改 Spec**」——
本节就是那次授权；红测只改这一个数字，并在行内注明指向本 Spec。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 475`）

| 契约 | 落点 | 复跑结果 |
| --- | --- | --- |
| C1 两种写法都剥 | `packaging_business_part_resolver.py` `_MTEXT_CODES` + `_strip_mtext_codes()` | 圆盘盒件名带码 39 → **0**、含反斜杠 39 → **0** |
| C2 `\P` 不当格式码 | `_MTEXT_CODES` 的 `(?![Pp])` | `内托支撑折板\P250G白卡纸;860*500=40M` → 件名 `内托支撑折板`、材料留着 `250G白卡纸` |
| C3 剥码先于归一化 | 两个调用点改成 `_width_normalized(_strip_mtext_codes(…))` | 全角 `；` 那条与半角 `;` 那条同结果 |
| C4 与 IR 一致 | 同一条正则口径 | 真样本剥完 0 条含控制序列 |
| C5 其它层不动 | 未改 | 酒盒 28 行 / 26 derived / 0 带码；材料、工艺 0/66 带码 |

复跑（本机 `./open-claude/.venv/bin/python -W ignore -m unittest`）：

- `tests.test_packaging_part_name_mtext_codes_red` → **22 OK**；
- `tests.test_packaging_business_part_size_must_be_confirmed_by_dimension_red` →
  OK（`DERIVED_TOTAL` / `SIZED_TOTAL` 圆盘盒改 `38`，其余组一字未动）；
- `tests.test_packaging_business_parts_outline_bbox_link_red`（`MIN_DERIVED` 门槛 31 ≤ 38）→ OK；
- `tests.test_packaging_wine_dwg_parts_and_downstream_truth_red`（28 件名集）→ OK；
- `tests.test_packaging_parts_must_come_from_the_drawing_red`、`tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red` → OK；
- 全部 `tests/test_packaging_*.py`（161 模块）→ `Ran 2693 … OK (skipped=12)`；把 §5 那一格的期望值
  留着 `39`、只上代码 ⇒ 唯一红的就是它；
- 护栏反向对照（HEAD 代码 + 本批的 `38`）：`EGuards.test_e1` FAIL（`38 != 39`）—— 那个数字确实
  由本批改动决定，不是随手改的；
- 全量（显式模块列表）：**同一份 379 模块列表**，
  HEAD 代码 `Ran 6319 … FAILED (failures=2, skipped=28)` → 本批 `+ 本批新红测 22 条 + 补进的
  tests.test_oc_agent_open_claude_dir_resolved_at_load_red 7 条` = `Ran 6348 … FAILED (failures=2, skipped=28)`：
  失败与跳过**逐条不变**（那 2 条是 `test_cpq_eval_ci_contract` 的既有环境/待裁决项，见 `## 472`），
  用例数的差正好是新加的两份模块。

未做 / 边界：

- 本批**不**动 `_exclusion_reason()`：`圆盘盒` 里 `402X50.5MM高/厚度2MM` / `8PCS/箱` / `30659003-10PC 装柜图纸`
  这类"规格/装箱说明"改后仍然留在件名里（改前也留，只是带码）。它们是否该算件名是
  `packaging-parts-material-attribution.md` §2.2 那一族的口径（那份 Spec 逐字引用过
  `402X50.5MM高/厚度2MM`），不在本批。
- 本批**不**碰 `\U+XXXX` 解码：那是 `cad_ir.parser.normalize_text()` 的活（真样本 `raw_text` 里没有
  这种转义；`layer=_U+56FE_U+5C42` 那种图层名走的是另一条路，不影响件名）。
