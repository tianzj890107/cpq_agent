# 规格：零件提取"读不到 CAD 解析结果"被说成"还没有解析结果，先跑一键解析图纸"

状态：Spec + 红测（已实现）（原状：`tech_app/backend/services/packaging_drawing_flow/steps.py:273-282
_previous_ir()` 把 `cad_ir.load_ir()` 的异常吞成 `None`，`:325-330 parts_extract()` 于是给出
既有的 `PACKAGING_PARTS_NO_IR` —— `"还没有可用的 CAD 图纸解析结果，无法提取零件（缺前置条件，
重试不会成功）"` + action `"先跑一键解析图纸（前四步）再来提取零件"`。真相是这一趟**读不到**
（文档通道抛异常），解析结果本来就在，重跑前四步不会有帮助）
红测：`tests/test_packaging_parts_ir_read_failure_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：承接 `drawing-flow-error-taxonomy.md` C3/C4（前置条件与读取故障必须分开；
`retryable` 是给用户的"重试有没有用"）+ `packaging-parts-extraction` C6（零件提不出来必须
`blocked`、**不许**把字段写入 / 待确认 / 后续准备一起判死）、
`packaging-drawing-source-read-failure.md`（同一个病症在第 1 步那一侧：读不到 ≠ 空/没有；
本批把它搬到第 5 步）、
`packaging-silent-degradation-disclosure.md` §2.2（读不到 ≠ 没有）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

`parts_extract()` 必须能分清三件事：**拿到 IR**（照旧提取）、**这个项目确实还没有 IR**
（既有的 `PACKAGING_PARTS_NO_IR`：去跑一键解析）、**这一趟读不到 IR**（新码：稍后重试这一步
——重跑一键解析不会有帮助）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_drawing_flow/steps.py`：

```python
273 def _previous_ir(ctx: Dict[str, Any]) -> Any:
274     module = _resolve(ctx, "cad_ir")
275     load = getattr(module, "load_ir", None) if module is not None else None
276     if not callable(load):
277         return None
278     try:
279         return load(ctx.get("project_id"))
280     except Exception:
281         return None                                  # ← 读不到与"没有"同形
```

```python
325     ir = ctx.get("ir") if isinstance(ctx.get("ir"), dict) else None
326     if ir is None:
327         ir = _previous_ir(ctx)
328     if not isinstance(ir, dict):
329         return _blocked("PACKAGING_PARTS_NO_IR",
330                         "还没有可用的 CAD 图纸解析结果，无法提取零件"
331                         "（缺前置条件，重试不会成功）",
332                         {"dependency": "cad_ir"},
333                         action="先跑一键解析图纸（前四步）再来提取零件")
```

两个后果：

1. **读不到被写成"还没有"**：`load_ir()` 抛异常（meta 文档通道挂了、正文坏了、S3 抖一下）时
   用户看到的是"缺前置条件，重试不会成功"+"先跑一键解析图纸"，而 IR 本来就在 —— 重跑前四步
   是错的下一步（`retryable=False` 更是明确告诉前端"重试没用"）。
2. **`_previous_ir()` 只有一个 `None` 出口**：`packaging_semantics()`（`:249-251`）也调它，
   所以这个"读不到折成没有"的取数口是**两处共用**的（本批只改形状与 `parts_extract` 的判定，
   语义那一步的失败口径不改，见 §3）。

## 2. 允许修改范围（实现方）

1. `steps.py:_previous_ir(ctx)` 改成**三态**（返回值形状是本批唯一允许改的对外契约，
   两个调用点都要跟着改）：
   - 读到 → `{"ir": <那份 IR，原样，不校验类型>, "read_problem": None}`
   - `cad_ir` 模块 / `load_ir` 不可用（这条缝不存在）→ `{"ir": None, "read_problem": None}`
     （"这个部署没有它"由前四步负责，本批不许把它算成"读不到"）
   - `load_ir` 抛异常 → `{"ir": None, "read_problem": {"code": "ir_unavailable",
     "reason": <异常类名>, "message": <原文前 200 字>}}`
2. `steps.py:parts_extract()`：
   - `read_problem` 非空 → **新码** `PACKAGING_PARTS_IR_UNAVAILABLE`，
     `status="blocked"`（**不许** `failed`/`unavailable`：那是 `STEP_TERMINAL_FAILURES`，
     会把字段写入 / 待确认 / 后续准备一起判死，见文件里 `:314-318` 的既有注释）、
     `retryable=True`、message 说得出异常类名与"稍后重试"且**不含**"还没有 / 缺前置条件 /
     先跑一键解析"、action = `"稍后重试这一步即可；重跑一键解析图纸不会有帮助（解析结果本来就在）"`、
     detail 至少含 `{"dependency": "cad_ir", "read_problem": <那一份>, "http_status": 503}`；
   - `ir is None` 且**没有** `read_problem` → 既有 `PACKAGING_PARTS_NO_IR`（码 / 状态 /
     文案 / action / `retryable=False`）**逐字不变**；
   - `packaging_semantics()` 的取数口改成读新形状的 `"ir"`，**它的失败口径一个字不改**
     （非目标）。
3. `steps.py:_blocked()`：新增关键字参数 `retryable: bool = False`（默认值保证既有三个调用点
   的返回体**逐字不变**）；只有本批这条新分支传 `True`。
4. `packaging_drawing_flow/model.py:ERROR_CODES` **新增**
   `"PACKAGING_PARTS_IR_UNAVAILABLE": (503, True)`；既有键一个不许动
   （`PACKAGING_PARTS_NO_IR` 仍是 `(409, False)`）。

## 3. 禁止事项

- 不许把这条读失败写成"还没有解析结果 / 缺前置条件 / 请先跑一键解析图纸 / 重试不会成功"。
- 不许把它返回成 `failed` / `unavailable`（`model.STEP_TERMINAL_FAILURES` 会截断整条链）。
- 不许改 `PACKAGING_PARTS_NO_IR` / `PACKAGING_PARTS_UNAVAILABLE` 两条既有码的语义、
  文案、`retryable` 或 `http_status`。
- 不许动 `packaging_semantics.analyze()` 内部自己的 IR 读取与
  `PACKAGING_SEMANTICS_SOURCE_MISSING` 口径（另立一批）。
- 不许改 `cad_ir.load_ir()`；不许在 `_previous_ir()` 里重试、缓存或用 `find_spec` 代替导入。
- 不许改 `tests/` 下任何既有文件；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据、不许建项目；
  本批红测全部离线（假 `cad_ir` / 假 `packaging_parts` 模块 + 打桩 `_emit`）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_ir_read_failure_red -v
# P 组（8 条）：
#   P1 `_previous_ir()`：load_ir 抛异常 → ir 为 None + read_problem.code=ir_unavailable
#      + reason=异常类名 + message 带原文（红）
#   P2 `_previous_ir()`：读到 → ir 是那份 IR 且 read_problem 为 None（红）
#   P3 `_previous_ir()`：load_ir 返回 None（确实没有）→ read_problem 为 None（绿护栏）
#   P4 `parts_extract()`：load_ir 抛异常 → PACKAGING_PARTS_IR_UNAVAILABLE + blocked
#      + retryable=True + 文案含异常类名 + action 不含"一键解析"（红）
#   P5 护栏：确实没有 IR → 仍是 PACKAGING_PARTS_NO_IR + retryable=False + action 含"一键解析"（绿）
#   P6 护栏：拿到 IR → 照旧 completed 且 parts_total / detail 键逐字不变（绿）
#   P7 model.ERROR_CODES 含 PACKAGING_PARTS_IR_UNAVAILABLE: (503, True)，
#      且 PACKAGING_PARTS_NO_IR 仍是 (409, False)（红）
#   P8 护栏：`_blocked()` 不传新参数时 retryable 仍是 False（逐字不变）（绿）
# 现状：P1 P2 P4 P7 红（4 条），P3 P5 P6 P8 绿（4 条护栏）
# 不回归（零件提取与错误分类两批）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_source_read_failure_red
```

真机复验（实现方做完、且部署后）：

```
# 2.1 跑一键解析，到第 5 步「零件提取」时让 meta 文档通道读不到 IR（例如临时改坏文档）：
# 1) 该步状态 blocked（不是 failed）→ 第 6 步字段写入照旧跑到终态；
# 2) 该步文案 = "这一次读不到这个项目的 CAD 图纸解析结果（<异常类名>），零件清单暂时生成不了"，
#    action = "稍后重试这一步即可；重跑一键解析图纸不会有帮助（解析结果本来就在）"；
# 3) 恢复通道 → 重试这一步直接出零件，不用重跑前四步；
# 4) 换一个从来没有解析过的项目 → 仍是既有的"还没有可用的 CAD 图纸解析结果…先跑一键解析图纸"。
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_ir_read_failure_red
# 实现前：Ran 8 tests … FAILED (failures=4)   ← P1 P2 P4 P7
# 实现后：Ran 8 tests … OK                    ← P3 P5 P6 P8 四条护栏始终绿
```

| 契约 | 落点 |
| --- | --- |
| §2.1 三态取数口 | `steps._previous_ir(ctx)` 改成返回 `{"ir": …, "read_problem": …}`：读到 → 原样那份 IR + `read_problem=None`；没有 `cad_ir` 模块 / 没有 `load_ir` → `{"ir": None, "read_problem": None}`（**不算**读不到）；`load_ir` 抛异常 → `{"ir": None, "read_problem": {"code": "ir_unavailable", "reason": <异常类名>, "message": <原文前 200 字>}}`。两个调用点都跟着改成读新形状。 |
| §2.2 零件提取判定 | `steps.parts_extract()`：`ctx["ir"]` 不是 dict 时才走取数口并把 `read_problem` 取出来；`read_problem` 非空 → **新码** `PACKAGING_PARTS_IR_UNAVAILABLE` + `status="blocked"`（不是 failed / unavailable，后续步骤照旧跑到终态）+ `retryable=True`，message = `这一次读不到这个项目的 CAD 图纸解析结果（<异常类名>），零件清单暂时生成不了；请稍后重试这一步`，detail ≥ `{"dependency": "cad_ir", "read_problem": <那一份>, "http_status": 503}`，action 明说"不用重跑前面的步骤（解析结果本来就在）"；该分支**不**去调 `extract()`。`ir` 确实为 None 且没有 `read_problem` → 既有 `PACKAGING_PARTS_NO_IR`（码 / 状态 / 文案 / action / `retryable=False`）逐字不变。 |
| §2.3 `_blocked()` | 新增关键字参数 `retryable: bool = False`（默认值保证既有三个调用点的返回体逐字不变 —— P8 断言不传时仍是 `False`、`detail` 里没有 `action`）；只有本批这条新分支传 `True`。 |
| §2.4 码表 | `model.ERROR_CODES` 新增 `"PACKAGING_PARTS_IR_UNAVAILABLE": (503, True)`；`PACKAGING_PARTS_NO_IR` 仍是 `(409, False)`，既有键一个没动。 |
| §3 未动的 | `packaging_semantics()` 只改为读新形状的 `"ir"`，它自己的失败口径（`PACKAGING_SEMANTICS_*` / `PACKAGING_SEMANTICS_SOURCE_MISSING`）一个字没改；`cad_ir.load_ir()` 未动；`_previous_ir()` 里没有重试 / 缓存 / `find_spec`；未把这条读失败判成 failed / unavailable。 |

**与 Spec §2.2 / 真机复验的一处口径差（已按红测为准，未改红测）**：Spec §2.2 与末尾真机复验把 action 写成
「稍后重试这一步即可；重跑一键解析图纸不会有帮助（解析结果本来就在）」，而 §4 的 P4 断言 `action` **不含**「一键解析」——
两者自相矛盾。本批按红测（验收门槛）实现，落地文案为
`稍后重试这一步即可；不用重跑前面的步骤（解析结果本来就在）`，语义与 §2.2 一致（"重跑前四步不会有帮助"），只是不含那个被禁的字面串。
若要把文案改回 §2.2 的逐字版本，需要先改 P4 断言（由 Spec 作者决定），实现方未自行放宽/改写红测。

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_extraction_red tests.test_drawing_flow_error_taxonomy_red \
  tests.test_drawing_flow_parse_terminal_signal_red \
  tests.test_packaging_drawing_source_read_failure_red   → Ran 85 … OK
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_drawing_flow_red tests.test_packaging_semantics_red \
  tests.test_drawing_flow_requirement_state_red   → Ran 130 … OK (skipped=2)
```

未起服务、未发 HTTP、未连 PG / SQLite、未建项目、未写任何文件、未跑真解析、
未 push / MR / tag / Release / 未部署。
