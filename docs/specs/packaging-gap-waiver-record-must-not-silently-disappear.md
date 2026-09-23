# Spec：有人签过的成本缺口放行留痕，读不出来时就**悄悄消失** —— 门禁分不出「没签过」和「签了但这份留痕不能用」

状态：Spec + 红测（已实现）（原状：实现提示词只在会话交付、业务实现不在本批；红基见 §4；
`## 463` 已落地，落点与实测见 §6）
红测：`tests/test_packaging_gap_waiver_record_disclosure_red.py`
血缘：`packaging-parse-to-downstream-seams.md` §2.3/§3.3（放行留痕的四条判据 + C1–C4：合法留痕必须被认出、
不合法不许当放行）、`packaging-gate-read-failure-disclosure.md` §2.1（**同一类缺口**：读不到与确实没做
在返回体与用户文案上逐字相同 —— 那一批只修了"上游读不到"，没修"留痕读不出来"）
本批 changelog 条目号：`## 463`。

## 0. 一句话目标

`gates._gap_waiver()` 的返回只有两种：`dict`（这条留痕成立）与 `None`（其余**全部**情况）。
于是"交接记录里根本没有留痕"和"留痕在、但读不出来 / 字段不全 / 码不覆盖"在门禁返回体、行上的键、
用户文案上**逐字相同** —— 签过字的人看不到自己那份留痕去哪了，只能看到"缺口清零后才能生成正式报价"。
本批要求：留痕存在但不可用时，门禁必须把**拒绝原因**说出来（两态可分），但不放宽任何判据。

## 1. 真实跑证据（本机，HEAD `8f8b011` 工作副本，打桩 `store` / `persistence` / 假依赖模块，不写盘）

同一条路径 `gates.build(PID, resolve=…)["stages"]["quote_publish"]`（成本 `has_gaps=true`、
唯一缺口码 `loss_rate_missing`、其余必需字段都已确认），只换 `handoff["gap_waiver_json"]`：

| 交接记录里的 `gap_waiver_json` | 行上 `waived` | `entry["waiver"]` | `entry["waiver_invalid"]` | 行上的键 | 与"无留痕"比 |
| --- | --- | --- | --- | --- | --- |
| 键缺席 | `None` | 无 | **无** | `[code, message, source]` | 基准 |
| `""`（空串） | `None` | 无 | **无** | 同上 | **逐字相同** |
| `"{not json"`（损坏 JSON） | `None` | 无 | **无** | 同上 | **逐字相同** |
| `"[1,2]"`（不是对象） | `None` | 无 | **无** | 同上 | **逐字相同** |
| `by`/`at`/`reason` 有一项为空 | `None` | 无 | **无** | 同上 | **逐字相同** |
| `codes: []` | `None` | 无 | **无** | 同上 | **逐字相同** |
| `codes: ["other_code"]`（不覆盖当前缺口） | `None` | 无 | **无** | 同上 | **逐字相同** |
| 合法留痕（四项齐全且覆盖） | `True` | 有 | 无 | `[…, waived, waiver]` | 只有这一行不同 |

- 六种"留痕其实存在但没被采用"的情形，和"PE1 从来没签过"**一模一样**（连 `blocking_message()`
  给出的用户文案都逐字相同：`成本仍存在缺口，缺口清零后才能生成正式报价`）。
- 根因在 `tech_app/backend/services/packaging_drawing_flow/gates.py:139 _gap_waiver()`：
  `except Exception: waiver = None`（`:157`）、`if not isinstance(waiver, dict): return None`（`:161`）、
  `if not (by and at and reason): return None`（`:166`）、`if not codes: return None`（`:170`）、
  `if gap_codes and not set(gap_codes) <= set(codes): return None`（`:173`）—— 五条拒绝理由全塌成同一个 `None`，
  调用点（`:241`）只拿得到"有 / 没有"这一个二值。
- 同文件 `:97 _READ_SEVERITY` / `:203 _finish()` 已经把"读不到"这件事单独披露过
  （`reads` / `reads_unavailable`）；留痕这条链**漏了同样的处理**。

## 2. 契约

### 2.1 拒绝原因闭集（模块常量）

`gates.py` 新增常量（名字与五个值冻结）：

```python
WAIVER_INVALID_REASONS = ("unreadable_json", "not_an_object", "missing_fields",
                          "empty_codes", "codes_not_covering")
```

判定**按此顺序**取第一个命中的（一条留痕可能同时踩两条，顺序不定就不可复核）：

1. `unreadable_json`：`json.loads()` 抛异常；
2. `not_an_object`：解析结果不是 dict（含 JSON 的 `null` / 数组 / 数字 / 字符串）；
3. `missing_fields`：是 dict 但 `by` / `at` / `reason` 至少一项为空；
4. `empty_codes`：`codes` 里没有有效项；
5. `codes_not_covering`：`codes` 非空，但**读得到的**当前缺口码里有没被覆盖的
   （`gap_codes` 为空 = 无从校验，仍按今天算成立，不许报这一条）。

### 2.2 披露形状（新增，只加不减）

- `entry["waiver_invalid"] = {"code": "cost_gap_waiver_unusable", "reason": <上面五选一>, "codes": [<留痕里读到的码，读不到给 []>]}`；
  仅当 ①`handoff["has_gaps"]` 为真、②`gap_waiver_json` **有内容**（非 `None`、非空白串）、
  ③该留痕不可用时出现；允许额外键（超集），`code` / `reason` / `codes` 三个必须逐字对上；
- `cost_gaps_unresolved` 行新增键 `"waiver_unusable": "<reason>"`（行级也要自证，
  否则只看 blocking 列表的人仍然看不出"这里本来有一份留痕"）；
- **冻结（一个字不改）**：`waived` 不为 `True`、`entry` 顶层不出现 `waiver`、
  `cost_gaps_unresolved` 仍在 `blocking` 里、`status == "blocked"`、行 `message` 与
  `blocking_message()` 文案、`has_gaps` 为假时的分支（含"没缺口却有留痕"）。

### 2.3 两态可分（本批的核心判据）

- **没有留痕**（键缺席 / `None` / `""`）→ `entry["waiver_invalid"]` 必须**缺席**（不许"读不到就报异常"造成噪音）；
- **有留痕但不可用** → 必须给 `waiver_invalid` + 行上 `waiver_unusable`；
- **合法留痕** → `waived=true` + `entry["waiver"]`，且 `waiver_invalid` 必须缺席。

### 2.4 非目标

- 不改四条判据本身（接受/拒绝结论一个字不变），不改 `_gap_codes()` 的取数口径；
- 不改前端文案与页面（本批只给码与 reason，人话另批）；
- 不改 `packaging_handoff.py` 写留痕那一段（`gap_waiver_json` 的写入口径是 `## 440` 那条 Spec 的事）；
- 不新增"把不合法留痕当放行"的宽恕路径（`packaging-parse-to-downstream-seams.md` §3.3 C3 冻结）。

## 3. 红测映射（`tests/test_packaging_gap_waiver_record_disclosure_red.py`，8 条）

| 用例 | 夹具 | 期望 | 现状 |
| --- | --- | --- | --- |
| R1 | 合法留痕 | `waived=True`、`entry["waiver"]` 四项与输入逐字一致、`waiver_invalid` 缺席 | 护栏（今天绿） |
| R2 | 损坏 JSON | `waiver_invalid.code="cost_gap_waiver_unusable"`、`reason="unreadable_json"`、行上 `waiver_unusable="unreadable_json"` | 红（键不存在） |
| R3 | 五种不可用形态 | reason 逐条映射（`unreadable_json` / `not_an_object` / `missing_fields` / `empty_codes` / `codes_not_covering`），且 `codes` 读到多少给多少 | 红 |
| R4 | 无留痕三态（缺席 / `None` / `""`） | `waiver_invalid` 必须缺席，且与 R3 的形态**机械可分** | 护栏（今天绿） |
| R5 | 公开入口 `gates.build()` | 披露能从公开入口读到（不只是 `_stage_entry`）；`blocking_message()` 文案逐字不变 | 红（键不存在） |
| R6 | `gates.py` 源码 | 常量 `WAIVER_INVALID_REASONS` 五值逐字；`waiver_invalid` / `waiver_unusable` 两个键出现在源码里 | 红 |
| R7 | 六种不可用形态 | 判据冻结：`waived` 都不为 `True`、`cost_gaps_unresolved` 仍在 `blocking`、`status="blocked"`、行 `message` 逐字 | 护栏（今天绿） |
| R8 | `_gap_waiver()` 纯函数 | 五种 reason 各自可被一个输入触发（闭集不空转） | 红（今天的返回只有 `dict` / `None`） |

## 4. 实测（本机，HEAD 工作副本）

```text
tests.test_packaging_gap_waiver_record_disclosure_red   Ran 8 in 0.001s  FAILED (failures=9)
  R2  {} is not true：留痕存在但读不出来时必须披露（键不存在）                                    （红）
  R3  None != 'unreadable_json' / 'not_an_object' / 'missing_fields' / 'empty_codes' /
      'codes_not_covering'（五条 subTest 逐条红）                                                  （红）
  R5  None is not true：披露必须能从公开入口 `gates.build()` 读到                                   （红）
  R6  unexpectedly None：`gates.py` 没有闭集常量 `WAIVER_INVALID_REASONS`                           （红）
  R8  None not found in ('unreadable_json', …,'codes_not_covering')：闭集空转                        （红）
  R1  合法留痕仍被认出 + 四项摘要逐字 + 不报「留痕不可用」                                          （护栏绿）
  R4  无留痕四态（键缺席 / None / 空串 / 空白串）一律静默                                           （护栏绿）
  R7  六种不可用形态下：waived 都不为 true、cost_gaps_unresolved 仍在 blocking、status=blocked、
      行上文案逐字不改                                                                              （护栏绿）
```

同一趟打桩跑出来的两态对照（本批 §1 的表就是它）：

```text
无留痕（键缺席 / ""）   行上键 [code, message, source]                  文案「成本仍存在缺口，缺口清零后才能生成正式报价」
损坏 JSON / 不是对象 /
缺字段 / codes 空 /
codes 不覆盖            行上键 [code, message, source]（**逐字相同**）   文案同上（**逐字相同**）
合法留痕                行上键 [code, message, source, waived, waiver]   文案同上
```

## 5. 交付边界

本批只写 Spec + 红测（业务实现不在本批）；未改业务实现、未改既有测试、未放宽任何断言、
未起服务、未连 34 / PG、未写盘（打桩 `store` / `persistence` / 假依赖模块）、未 push / MR / tag /
Release / 未部署。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 463`）

只改一个文件 `tech_app/backend/services/packaging_drawing_flow/gates.py`（留痕判定从"二值"改成"三态"）：

| 契约 | 落点 | 实现 |
| --- | --- | --- |
| §2.1 拒绝原因闭集 | 模块常量 | 新增 `WAIVER_INVALID_REASONS = ("unreadable_json", "not_an_object", "missing_fields", "empty_codes", "codes_not_covering")`（五值逐字冻结），判定按此顺序取第一个命中的 |
| §2.1/§2.3 三态 | `_waiver_verdict(handoff, gap_codes)` | `(摘要, None)` 成立 / `(None, 披露)` 存在但用不了 / `(None, None)` **根本没有留痕**（键缺席 / `None` / 空白串）—— 原来的 `_gap_waiver()` 保留成一行包装（`_waiver_verdict(...)[0]`），既有调用方口径逐字不变 |
| §2.2 披露形状 | 调用点 + `entry` | 新增 `WAIVER_UNUSABLE_CODE = "cost_gap_waiver_unusable"`；行上新增 `waiver_unusable=<reason>`，`entry["waiver_invalid"] = {"code", "reason", "codes"}`（`codes` = 留痕里读到的码，读不到给 `[]`），**只加不减、不进 `blocking`** |
| §2.2 冻结面 | 未动 | `waived` 不为 `True`、`entry` 顶层不出现 `waiver`、`cost_gaps_unresolved` 仍在 `blocking`、`status == "blocked"`、行 `message` 与 `blocking_message()` 文案、`has_gaps` 为假时的分支 —— 全部未动 |
| §2.4 非目标 | 未做 | 四条判据（接受/拒绝结论）、`_gap_codes()` 取数口径、前端文案与页面、`packaging_handoff.py` 写留痕那一段、`## 440` C3 的"不合法不许当放行" —— 一个都没变 |

### 6.1 复跑命令与结果（本机 `./open-claude/.venv/bin/python -m unittest`）

```text
tests.test_packaging_gap_waiver_record_disclosure_red
    # Ran 8 … OK                       （红基 Ran 8 in 0.002s … FAILED (failures=9)）

tests.test_packaging_downstream_blockers_red tests.test_packaging_drawing_source_read_failure_red \
tests.test_packaging_flow_dependency_probe_truth_red tests.test_packaging_flow_non_exception_retryable_red \
tests.test_packaging_gate_read_failure_disclosure_red tests.test_packaging_handoff_input_drift_red \
tests.test_packaging_manual_field_confirmation_red tests.test_packaging_parse_to_downstream_seams_red \
tests.test_packaging_parts_ir_read_failure_red tests.test_packaging_preconditions_requirement_read_failure_red \
tests.test_packaging_quote_close_loop_red tests.test_packaging_quote_send_button_entry_red \
tests.test_packaging_stage_chain_read_failure_red tests.test_spec_status_truth_red
    # Ran 223 in 3.445s … FAILED (failures=2) → 修完本 Spec 的状态行后剩 1 条
    #   唯一那条是**既有挂账**、与本批无关：`parse_to_downstream_seams_red` B4 的 `stats` 键集冻结
    #   （BOM 文档多出键，`## 462` 已记）。已用 `git stash` 去掉本批改动复跑确认：改前同样 FAIL。
```

两态对照（本批修的就是这两态原来逐字相同）：

```text
无留痕（键缺席 / None / "" / "   "）  waiver_invalid 必须缺席（静默）
损坏 JSON / 不是对象 / 缺字段 /
codes 空 / codes 不覆盖                entry["waiver_invalid"]={code, reason, codes} + 行上 waiver_unusable
合法留痕                              waived=true + entry["waiver"]，waiver_invalid 必须缺席
```

### 6.2 红测自身缺陷（如实记录）

- R6 是源码守卫（常量五值 + 两个键名出现在源码里），它不校验"五个 reason 与五种输入形态一一对应"
  —— 那条由 R3 / R8 的行为断言兜着。
- R8 的"闭集不空转"只覆盖五种形态各触发一次，**没有**覆盖"同一条留痕同时踩两条判据时按序取第一个"
  （例如 `codes` 空且 `by` 也空 → 必须报 `missing_fields` 而不是 `empty_codes`）。这是覆盖缺口，
  顺序本身已被 §2.1 与实现钉死。
- 本批红测不校验前端（Spec §2.4 明确"人话另批"）。

未 push / MR / tag / Release / 部署，未起服务、未连 PG / 34、未写盘（打桩 `store` / `persistence` /
假依赖模块）。

### 6.4 人工复核（红测覆盖缺口之外，2026-09-23）

§6.2 记的覆盖缺口（"一条留痕同时踩两条判据时按序取第一个"）直接调 `gates._waiver_verdict()` 复核：

```text
codes 空 + by 也空（同时踩 empty_codes 与 missing_fields） → missing_fields   ← 按 §2.1 的顺序
JSON 字符串 "just a string" / JSON null                    → not_an_object
codes: ["  ", "loss_rate_missing"]                         → 成立（空白项被剔掉，覆盖得住）
has_gaps 为假（有留痕也不成立）                              → waiver=无、invalid=无（静默）
键缺席                                                     → waiver=无、invalid=无（静默）
```
