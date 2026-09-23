# 规格：三步 catch 把"重试没用"的失败一律报成可重试 —— 界面上写着"（可重试）"，而码表说不可重试

状态：Spec + 红测（已实现）（原状：`tech_app/backend/services/packaging_drawing_flow/steps.py` 里
`dwg_convert()`（`:157-161`）、`cad_ir_parse()`（`:207-210`）、`packaging_semantics()`（`:259-262`）
三处 catch 都写成 `_failed(code, message, {"reason": type(exc).__name__})` —— `_failed()` 的
`retryable` 默认值是 `True`（`:75-79`），于是**异常自己带的可重试性被丢掉**）
红测：`tests/test_packaging_flow_step_reports_retryable_honestly_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：承接 `dwg-file-capability-preflight.md` §3（`STABLE_ERROR_CODES` 是**唯一权威闭集**，
每条都带 `http_status` + `retryable`；`FileCapabilityError` 会把它读进实例：
`file_preflight.py:266-272`）、
`drawing-flow-error-taxonomy.md` C1/C2（"能不能重试"必须与真因一致；`steps.py:516-535`
的字段写入那一步已经这么做：`model.error_meta(code)` → `_failed(..., retryable=retryable)`）、
`packaging-silent-degradation-disclosure.md` §2.2（失败与可重试性都不许说反）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

三步（转换 / 矢量解析 / 语义识别）catch 到带 `retryable` 的异常时，**照它说的报**：
`STABLE_ERROR_CODES` 里写着不可重试的（"请拆分图纸" / "请检查转换器安装与配置" /
"请联系系统管理员"），界面与终态信号就**不许**说"（可重试）"。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_drawing_flow/steps.py`：

```python
75 def _failed(code: str, message: str, detail: Optional[dict] = None,
76             retryable: bool = True) -> Dict[str, Any]:        # ← 默认 True
77     return {"status": "failed", "error_code": str(code),
78             "error_message": str(message), "retryable": bool(retryable), ...}
```

```python
157     except Exception as exc:                              # dwg_convert()
158         code = str(getattr(exc, "stable_error_code", "") or "DWG_CONVERSION_FAILED")
161         return _failed(code, message, {"reason": type(exc).__name__})   # ← retryable 丢了
...
207     except Exception as exc:                              # cad_ir_parse()
208         code = str(getattr(exc, "stable_error_code", "") or "CAD_IR_SOURCE_MISSING")
209         return _failed(code, str(exc) or "CAD 矢量解析失败，请重试",
210                        {"reason": type(exc).__name__})        # ← retryable 丢了
...
259     except Exception as exc:                              # packaging_semantics()
260         code = str(getattr(exc, "stable_error_code", "") or "PACKAGING_SEMANTICS_FAILED")
261         return _failed(code, str(exc) or "包装语义识别失败，请重试",
262                        {"reason": type(exc).__name__})        # ← retryable 丢了
```

异常这一侧是**带答案**的（`file_preflight.py:266-272`：`FileCapabilityError.__init__` 把
`STABLE_ERROR_CODES[code]` 的 `retryable` 存进 `self.retryable`），可 `stable_error_code` 取了、
`retryable` 却没用。三个具体后果：

| 真因 | 码表口径 | 今天的结果 |
| --- | --- | --- |
| 图层规则配置坏了 | `PACKAGING_LAYER_RULES_INVALID`(500, **False**)「请联系系统管理员」 | 报成 `retryable=True` |
| 画布实体超上限 | `CAD_IR_ENTITY_LIMIT_EXCEEDED`(413, **False**)「请拆分图纸」 | 报成 `retryable=True` |
| 转换器二进制不可用 | `DWG_CONVERTER_BINARY_UNUSABLE`(500, **False**)「请检查转换器安装与配置」 | 报成 `retryable=True` |

前端就是按这个字段给话术的（`app.js:5248` 的终态信号、`quick-quote-panel.js:902-903`
的 `（可重试）/（最终失败）`）。说反了，用户会一直点重试，而真因（去改配置 / 拆图）永远没被说出来。
反方向同样要防：`DRAWING_SOURCE_UNAVAILABLE`(503, **True**) 这类可重试的，不能被"顺手改成一律 False"。

## 2. 允许修改范围（实现方）

`tech_app/backend/services/packaging_drawing_flow/steps.py`：上面三处 catch，各自在调 `_failed()` 前
取一次异常自带的可重试性，并**只**改 `retryable` 这一个入参：

```python
        retryable = getattr(exc, "retryable", None)
        return _failed(code, message, {"reason": type(exc).__name__},
                       **({"retryable": bool(retryable)} if isinstance(retryable, bool) else {}))
```

- 取不到（普通异常没有这个属性 / 不是 bool）→ **保持既有默认 `True`**，返回体逐字不变；
- `code` 的取法（`stable_error_code` 优先）、兜底码、message 取法、detail（`{"reason": 类名}`）、
  `status="failed"` **全部逐字不变**；
- `_failed()` 的函数签名与默认值（`retryable=True`）**不许改**；
- `dwg_convert()` 里 `manifest.status` 不成功 / 质量门槛那两条 `_failed(...)`（`:173-179`）
  **也逐字不变**（那是"转换跑完了但结果不合格"，不是异常路径，另立一批）。

## 3. 禁止事项

- 不许把 `retryable` 一律改成 `False`（可重试的读取故障必须仍可说可重试）。
- 不许改 `STABLE_ERROR_CODES` 任何一条的 `retryable` / `http_status` / 文案。
- 不许改 `steps.py:516-535`（字段写入那一步）已经正确的口径，也不许动
  `model.error_meta()` 的默认值 `(500, False)`。
- 不许在本批里顺手改 `status`（仍是 `failed`）或把这三步改成 `blocked`。
- 不许改 `tests/` 下任何既有文件；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据、不许建项目、不跑真 DWG；
  本批红测全部离线（假 `cad_converter` / `cad_ir` / `packaging_semantics` 模块 + 真
  `FileCapabilityError` 构造）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_flow_step_reports_retryable_honestly_red -v
# R 组（8 条）：
#   R1 packaging_semantics()：抛 PACKAGING_LAYER_RULES_INVALID(500, False) → retryable 必须是 False（红）
#   R2 dwg_convert()：抛 DWG_CONVERTER_BINARY_UNUSABLE(500, False) → retryable 必须是 False（红）
#   R3 cad_ir_parse()：抛 CAD_IR_ENTITY_LIMIT_EXCEEDED(413, False) → retryable 必须是 False（红）
#   R4 护栏：三步抛普通 RuntimeError（无 retryable 属性）→ 仍是 True（既有默认逐字不变）（绿）
#   R5 护栏：三步抛 DRAWING_SOURCE_UNAVAILABLE(503, True) → 仍是 True（不许一律改成 False）（绿）
#   R6 源码守卫：三处 catch 都取了 getattr(exc, "retryable") 并把它交给 _failed(retryable=…)（红）
#   R7 护栏：三处 catch 的兜底码（DWG_CONVERSION_FAILED / CAD_IR_SOURCE_MISSING /
#      PACKAGING_SEMANTICS_FAILED）与 detail 形状（{"reason": 类名}）逐字不变（绿）
#   R8 护栏：_failed() 的默认 retryable 仍是 True、status 仍是 "failed"（绿）
# 现状：R1 R2 R3 R6 红（4 条），R4 R5 R7 R8 绿（4 条护栏）
# 不回归（错误分类、快照与第 4 步三批）
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_red
./open-claude/.venv/bin/python -m unittest tests.test_dwg_file_capability_preflight_red
```

真机复验（实现方做完、且部署后）：

```
# 把图层规则文件临时改坏（PACKAGING_LAYER_RULES_PATH 指向不存在的文件）后跑一键解析：
# 1) 第 4 步的终态信号 error_code = PACKAGING_LAYER_RULES_INVALID、retryable = false
#    （界面按"最终失败"话术，不是"（可重试）"）；
# 2) 换回正常规则、但让 IR 通道读不到 → 仍是 retryable = true；
# 3) 抛普通异常（不是 FileCapabilityError）→ 仍与今天一致（retryable = true）。
```

## 5. 落地状态（2026-09-23，Codex 实现）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2 三处 catch 照异常报 | `steps.py` 新增顶层纯函数 `_retryable_from_exception(code, value)`，`dwg_convert()` / `cad_ir_parse()` / `packaging_semantics()` 三处 catch 的 `_failed(...)` 各加 `retryable=_retryable_from_exception(code, getattr(exc, "retryable", None))` | `code` 取法、兜底码、`message`、`{"reason": 类名}`、`status="failed"` 逐字未动 |
| §2 取不到 → 既有默认 | 同函数 | 非 bool / 没有该属性 → 返回 `True`（即 `_failed()` 的既有默认，函数签名与默认值未动） |
| §3 不一律 False | 同函数 | 可重试的码（`DRAWING_SOURCE_UNAVAILABLE`、`DWG_CONVERSION_FAILED`…）仍报可重试 |

**口径说明（红测 R5 与 §1 表格的一处出入，实现按红测走）**：Spec §1 表格把
`DRAWING_SOURCE_UNAVAILABLE` 记成「(503, True) 的权威闭集成员」，实测它**不在**
`file_preflight.STABLE_ERROR_CODES` 里（它在**流层** `model.ERROR_CODES`）——
`FileCapabilityError("DRAWING_SOURCE_UNAVAILABLE").retryable` 因此是构造默认值 `False`。
若照 §2 的示例片段无条件 `bool(exc.retryable)`，这次读取故障会被说成"最终失败"，
正好踩中该 Spec 自己的 R5 护栏。所以实现取的是同一条判据的更紧形式：**只有当这个码确实属于
权威闭集 `file_preflight.STABLE_ERROR_CODES` 时**才照异常报；码不在闭集里（含只在流层表里的码）
-> 保持既有默认 `True`。R1–R8 全部满足，未改任何测试、未改任何码表数值。

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_flow_step_reports_retryable_honestly_red
Ran 8 tests ... OK        （红基：Ran 8 ... FAILED (failures=4) —— R1/R2/R3/R6 红，R4/R5/R7/R8 绿护栏）

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_drawing_flow_error_taxonomy_red tests.test_packaging_semantics_red \
  tests.test_dwg_file_capability_preflight_red
Ran 102 tests ... OK (skipped=1)
```
