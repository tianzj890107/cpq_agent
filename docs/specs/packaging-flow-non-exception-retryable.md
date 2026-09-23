# 规格：转换"跑完了但结果不合格"的 `retryable` 也不许说反 —— 非异常失败路径绕过了码表

状态：Spec + 红测（已实现）（原状：`tech_app/backend/services/packaging_drawing_flow/steps.py` 的
`dwg_convert()` 有两条**非异常**失败路径（`:173-174` manifest 不成功、`:177-179` 质量门槛未过），
它们都调 `_failed(code, message, detail)` —— `_failed()` 的 `retryable` 默认值是 `True`
（`:75-79`），于是转换器报回来的 `error_code`（可能是
`DWG_CONVERTER_UNSAFE_PATH`(500, **False**) / `FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION`(500, **False**)
/ `DWG_CONVERTER_BINARY_UNUSABLE`(500, **False**)）一律被说成可重试；
同时 `model.error_meta()`（`model.py:287-288`）只认流层 `ERROR_CODES`，**第三批以前的权威闭集
`file_preflight.STABLE_ERROR_CODES` 一个都不认**
红测：`tests/test_packaging_flow_non_exception_retryable_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：承接 `packaging-flow-step-reports-retryable-honestly.md`（`## 437`：异常路径已经要照
异常自带的 `retryable` 报；本批补**非异常**那两条，并把取值口收成一个）、
`dwg-file-capability-preflight.md` §3（`STABLE_ERROR_CODES` 是**唯一权威闭集**：
每条都带 `http_status` + `retryable`；`dwg-conversion-service.md` 的 `CONVERSION_ERROR_CODES`
必须是它的子集且数值逐条一致）、
`drawing-flow-error-taxonomy.md` C1/C2（"能不能重试"必须与真因一致）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

任何一步报失败时，"能不能重试"都必须来自**码表**（或异常自己带的答案），不许来自
`_failed()` 的默认值 —— 转换器说"输出路径不安全 / 假转换器不许上生产 / 转换器装坏了"
（都是 `retryable=False`）时，界面不许说"（可重试）"。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_drawing_flow/steps.py`：

```python
172     if status not in ("ok", "success_with_warnings"):
173         return _failed(str(manifest.get("error_code") or "DWG_CONVERSION_FAILED"),
174                        "图纸转换未成功，请重试或联系管理员", detail)      # ← retryable 默认 True
175     quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
176     if quality and not quality.get("verified"):
177         return _failed("DWG_CONVERTER_OUTPUT_INVALID",
178                        "转换产物未通过质量门槛，请重试或联系管理员", detail)  # ← 同上
```

`tech_app/backend/services/file_preflight.py`（权威闭集，三条不合一）：

```python
83     "DWG_CONVERTER_OUTPUT_INVALID": {"http_status": 502, "retryable": True, ...},
96     "DWG_CONVERTER_UNSAFE_PATH":    {"http_status": 500, "retryable": False, ...},
99     "FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION": {"http_status": 500, "retryable": False, ...},
112    "DWG_CONVERTER_BINARY_UNUSABLE": {"http_status": 500, "retryable": False, ...}
```

`tech_app/backend/services/packaging_drawing_flow/model.py`：

```python
287 def error_meta(code: str) -> Tuple[int, bool]:
288     return ERROR_CODES.get(str(code), (500, False))     # ← 只认流层表，权威闭集不认识
```

两个后果：

1. **非异常路径没有码表可依**：manifest 里的 `error_code` 是转换器给的，完全可能是上面三条
   `retryable=False` 之一；今天 `_failed()` 一律按 `True` 报，用户会一直点重试，而真因
   （换转换器 / 关掉假转换器 / 改输出路径策略）永远没被说出来。
2. **取值口认不全**：`model.error_meta()` 拿 `DWG_CONVERSION_TIMEOUT`(504, **True**) 这种
   "只登记在权威闭集里"的码时给 `(500, False)` —— 方向也能说反（可重试的说成最终失败）。
   目前已有两处按它取值：`_unavailable()`（`PACKAGING_FLOW_DEPENDENCY_MISSING`，在流层表里，
   不受影响）与 `field_write` 的 catch（`:531-535`，流层码，不受影响）。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_drawing_flow/model.py:error_meta()`
   - 查找顺序：**流层 `ERROR_CODES` 优先**（既有键的数值逐字不变）→ 其次
     `file_preflight.STABLE_ERROR_CODES`（按 `http_status` / `retryable` 两个键取值）→
     都没有才回落 `(500, False)`；
   - 返回类型仍是 `Tuple[int, bool]`；**允许延迟导入**（函数内 `from . import …` 之外的那条路
     也行）以避免模块级循环导入；
   - 不许改 `ERROR_CODES` 里任何一个既有键的数值。
2. `steps.py:dwg_convert()` 的两条非异常路径：
   - `code = str(manifest.get("error_code") or "DWG_CONVERSION_FAILED")` **取法逐字不变**，
     只是 `_failed(..., retryable=model.error_meta(code)[1])`；
   - 质量门槛那条同理（code 仍是 `DWG_CONVERTER_OUTPUT_INVALID`，`retryable` 取表值）；
   - `message`、`detail`（`conversion_id` / `status` / `quality` / `warning_count` / `error_count` /
     `converter_name` / `converter_version` / `drawing_version`）逐字不变。
3. `steps.py` 里其它 `_failed(...)` 调用点**不许动**（`## 437` 管异常路径那三处）。

## 3. 禁止事项

- 不许把这两条一律改成 `retryable=False` 或一律 `True`：必须按码取值。
- 不许改 `file_preflight.STABLE_ERROR_CODES` 任何一条的数值与文案，也不许在 `model.py` 里
  再抄一份表。
- 不许改 `_failed()` 的签名与默认值；不许改 `_blocked()` 的默认值。
- 不许顺手动 `field_write` 的 catch 分支行为（它已经在用 `model.error_meta()`，取值口变准之后
  对它是向好，但本批不许改它的判据）。
- 不许改 `tests/` 下任何既有文件；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据、不许建项目、不跑真 DWG；
  本批红测全部离线（假 `cad_converter` 模块 + 直接调 `model.error_meta()`）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_flow_non_exception_retryable_red -v
# S 组（8 条）：
#   S1 model.error_meta()：DWG_CONVERSION_TIMEOUT → (504, True)；DWG_CONVERTER_UNSAFE_PATH
#      → (500, False)（权威闭集的值，今天给的是默认 (500, False)）（红）
#   S2 护栏：流层既有键逐字不变（REQUIREMENT_NOT_EDITABLE(409,False) /
#      DRAWING_SOURCE_UNAVAILABLE(503,True) / PACKAGING_PARTS_NO_IR(409,False)）+ 未知码 (500,False)（绿）
#   S3 dwg_convert()：manifest.status=failed + error_code=DWG_CONVERTER_UNSAFE_PATH
#      → retryable 必须是 False（红）
#   S4 护栏：manifest.error_code=DWG_CONVERSION_TIMEOUT(504, True) → retryable 仍是 True（绿）
#   S5 护栏：manifest 不给 error_code → 仍回落 DWG_CONVERSION_FAILED 且 retryable=True（绿）
#   S6 护栏：质量门槛未过 → DWG_CONVERTER_OUTPUT_INVALID + retryable=True（表值）（绿）
#   S7 源码守卫：两条非异常 _failed(...) 都带 retryable=（红）
#   S8 护栏：code 取法与 detail 八键逐字不变（绿）
# 现状：S1 S3 S7 红（3 条），S2 S4 S5 S6 S8 绿（5 条护栏）
# 不回归（错误分类、链路与码表三批）
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
./open-claude/.venv/bin/python -m unittest tests.test_dwg_file_capability_preflight_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_flow_step_reports_retryable_honestly_red
```

真机复验（实现方做完、且部署后）：

```
# 1) 让转换器 manifest 回报 error_code=DWG_CONVERTER_UNSAFE_PATH 后跑一键解析：
#    第 2 步终态信号 retryable=false（界面按"最终失败"话术）；
# 2) 让它回 DWG_CONVERSION_TIMEOUT → retryable=true；
# 3) 让它不报 error_code（只给 status=failed）→ 仍回落 DWG_CONVERSION_FAILED + retryable=true；
# 4) 转换产物 quality.verified=false → DWG_CONVERTER_OUTPUT_INVALID + retryable=true。
```

## 5. 落地状态（2026-09-23，Codex 实现）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 取值口认全 | `packaging_drawing_flow/model.py:error_meta()` | 查找顺序：流层 `ERROR_CODES` → `file_preflight.STABLE_ERROR_CODES`（取 `http_status` / `retryable` 两键）→ 默认 `(500, False)`；`file_preflight` 延迟导入（该模块只依赖 stdlib，无循环）；`ERROR_CODES` 既有键一个没动 |
| §2.2 manifest 不成功 | `steps.py:dwg_convert()` | 取 `code = str(manifest.get("error_code") or "DWG_CONVERSION_FAILED")`（取法逐字不变），`_failed(..., retryable=model.error_meta(code)[1])` |
| §2.2 质量门槛未过 | 同函数 | 码仍是 `DWG_CONVERTER_OUTPUT_INVALID`，`retryable` 取表值（(502, True)）；`message` 与 detail 八键逐字不变 |
| §2.3 其它调用点不动 | — | `## 437` 管的三处异常路径、`field_write` 的 catch、`_failed()` / `_blocked()` 的签名与默认值均未动 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_flow_non_exception_retryable_red
Ran 8 tests ... OK        （红基：Ran 8 ... FAILED (failures=3) —— S1/S3/S7 红，S2/S4/S5/S6/S8 绿护栏）

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_drawing_flow_error_taxonomy_red tests.test_packaging_drawing_flow_red \
  tests.test_dwg_file_capability_preflight_red tests.test_packaging_flow_step_reports_retryable_honestly_red
Ran 105 tests ... OK (skipped=1)
```
