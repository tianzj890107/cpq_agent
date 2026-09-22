# 规格：语义识别"读不到 CAD 解析结果"被说成"项目里没有解析结果，请先重跑图纸解析"

状态：Spec + 红测（已实现）（原状：`tech_app/backend/services/packaging_semantics/__init__.py:276-284
analyze_conversion()` 只有两个出口：`cad_ir.load_ir()` 的异常**原样往上抛**（于是
`steps.packaging_semantics()` 拿默认码 `PACKAGING_SEMANTICS_FAILED` 兜住，`audit` 一条都不写），
`load_ir` 返回非 dict 一律算 `PACKAGING_SEMANTICS_SOURCE_MISSING`
（"项目里没有可用的 CAD 图纸解析结果，请先重跑图纸解析" + `audit.reason="source_missing"`）——
"读不到"与"确实没有"同形）
红测：`tests/test_packaging_semantics_ir_read_failure_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：承接 `packaging-drawing-semantics.md` §9（`PACKAGING_SEMANTICS_SOURCE_MISSING` 是给
"项目里**没有**可用 CAD IR"的码，不是给"读不到"的）、
`packaging-parts-ir-read-failure.md`（同一个病症在第 5 步那一侧，本批补上它 §3 明确留白的第 4 步）、
`dwg-file-capability-preflight.md` §3（`STABLE_ERROR_CODES` 是**唯一权威闭集**：新码必须并进去，
否则 `FileCapabilityError` 会给 500 + `retryable=False` 的默认值）、
`packaging-silent-degradation-disclosure.md` §2.2（读不到 ≠ 没有；而且失败必须在 `audit` 里留下
一笔 —— 今天抛异常的路径**一条审计都不写**）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

`analyze_conversion()` 必须能分清：**读到 IR**（照旧分析）、**项目确实没有 IR**
（既有 `PACKAGING_SEMANTICS_SOURCE_MISSING`：去重跑图纸解析）、**这一趟读不到 IR**
（新码：稍后重试 —— 重跑图纸解析不会有帮助，而且必须先写一条 `audit`）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_semantics/__init__.py`：

```python
276     if ir is None:
277         from .. import cad_ir  # 延迟导入：只在真的要从项目里取 IR 时依赖第 3 批
278
279         ir = cad_ir.load_ir(project_id, ir_id) if ir_id else cad_ir.load_ir(project_id)
280     if not isinstance(ir, dict):                       # ← 异常的路径在上面就断了，不留痕
281         persistence_mod.audit(project_id, ACTION_FAILED,
282                               {"reason": "source_missing", "by": author})
283         raise FileCapabilityError("PACKAGING_SEMANTICS_SOURCE_MISSING",
284                                   detected={"project_id": str(project_id or ""),
285                                             "ir_id": str(ir_id or "")},
286                                   message="项目里没有可用的 CAD 图纸解析结果，请先重跑图纸解析")
```

三个后果：

1. **读不到时连审计都没有**：`load_ir()` 抛异常（meta 文档通道挂了 / 正文坏了 / S3 抖一下）
   直接越过 `:280-286`，`audit` 一条不写；上游 `steps.packaging_semantics()`
   （`steps.py:254-257`）只能拿 `getattr(exc, "stable_error_code", "")` 兜底，于是
   **`PACKAGING_SEMANTICS_FAILED`** 这个"这一步坏了"的通用码，把"读通道抖了一下"说成
   "语义识别失败"。
2. **`SOURCE_MISSING` 被当成兜底**：`:280` 的 `not isinstance(ir, dict)` 同时收下了
   "`load_ir` 返回 None（确实没有）"与"返回了一个坏形状"，文案是"项目里没有可用的 CAD 图纸
   解析结果，请先重跑图纸解析" —— 对读通道故障来说这是错的下一步。
3. **`detected` 里没有原因**：真出了这条码，排障看不到异常类名；`audit.reason` 也只有一个
   `source_missing`。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/file_preflight.py:STABLE_ERROR_CODES` **新增**
   `"PACKAGING_SEMANTICS_SOURCE_UNREADABLE"`：`{"http_status": 503, "retryable": True,
   "message": "暂时读不到这个项目的 CAD 图纸解析结果，请稍后重试；这不代表这个项目还没有解析结果"}`；
   既有九条（含 `PACKAGING_SEMANTICS_SOURCE_MISSING` 的 422 / True / 文案）**逐字不变**。
2. `packaging_semantics/__init__.py:analyze_conversion()`
   - 把 `cad_ir.load_ir(...)` 包进 `try/except Exception as exc`：先写审计
     `persistence_mod.audit(project_id, ACTION_FAILED, {"reason": "source_unreadable",
     "by": author})`，再
     `raise FileCapabilityError("PACKAGING_SEMANTICS_SOURCE_UNREADABLE",
     detected={"project_id": ..., "ir_id": ..., "reason": <异常类名>,
     "message": <原文前 200 字>}, message="暂时读不到这个项目的 CAD 图纸解析结果（<异常类名>），
     请稍后重试；这不代表这个项目还没有解析结果") from exc`；
   - **`load_ir` 返回非 dict**（含 `None`）→ 既有 `PACKAGING_SEMANTICS_SOURCE_MISSING` 分支
     **逐字不变**（码 / `audit.reason="source_missing"` / `detected` 两个键 / 文案）；
   - `cad_ir` 模块本身导入失败（`ModuleNotFoundError`）**不算**本批的读失败：仍按"这条缝不存在"
     走既有兜底（本批不改 `from .. import cad_ir` 这一层）。
3. 不许改 `analyze()` 的既有口径、不许改 `persistence_mod.audit()` 的签名、
   不许改 `steps.packaging_semantics()` 的 catch 分支（`retryable` 的传递另立一批）。

## 3. 禁止事项

- 不许把这条读失败写成"没有 / 不存在 / 请先重跑图纸解析 / 缺前置条件"。
- 不许复用 `PACKAGING_SEMANTICS_SOURCE_MISSING` 承载读失败（它答的是"有没有"，不是"读得到吗"）。
- 不许只改抛出点、不改权威码表（未注册的码会被 `FileCapabilityError` 兜成 500 + 不可重试）。
- 不许改 `PACKAGING_SEMANTICS_FAILED` / `PACKAGING_LAYER_RULES_INVALID` 的既有语义与文案。
- 不许在 `analyze_conversion()` 里重试、缓存或降级成"没有 IR"继续跑。
- 不许改 `tests/` 下任何既有文件（含 `tests/test_packaging_semantics_red.py` 里
  `NEW_ERROR_CODES` 那两条断言）；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据、不许建项目；
  本批红测全部离线（`mock.patch.object(cad_ir, "load_ir", …)` + 打桩 `persistence.audit` /
  `save_semantics` / `list_semantics`）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_ir_read_failure_red -v
# Q 组（8 条）：
#   Q1 load_ir 抛异常 → stable_error_code=PACKAGING_SEMANTICS_SOURCE_UNREADABLE、
#      http_status=503、retryable=True、文案含异常类名且不含"没有 / 重跑图纸解析"（红）
#   Q2 同一次失败必须写审计：reason="source_unreadable" 且 detected 带异常类名（红）
#   Q3 护栏：load_ir 返回 None → 仍是 PACKAGING_SEMANTICS_SOURCE_MISSING（422 / 文案 / 审计原因逐字）（绿）
#   Q4 护栏：load_ir 返回非 dict（list）→ 同上（绿）
#   Q5 STABLE_ERROR_CODES 含新码 (503, True, 文案)，且既有两条语义码逐字不变（红）
#   Q6 源码守卫：analyze_conversion() 里 load_ir 被 try/except 包住且出现新码（红）
#   Q7 护栏：显式给了 ir= 时一次也不许去调 cad_ir.load_ir()（绿）
#   Q8 护栏：新码走 FileCapabilityError 时 getattr(exc, "retryable") 为 True（红）
# 现状：Q1 Q2 Q5 Q6 Q8 红（5 条），Q3 Q4 Q7 绿（3 条护栏）
# 不回归（语义与第 4 步两批）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_red
./open-claude/.venv/bin/python -m unittest tests.test_dwg_file_capability_preflight_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_ir_read_failure_red
```

真机复验（实现方做完、且部署后）：

```
# 2.1 跑一键解析，第 4 步「包装语义识别」时让 meta 文档通道读不到 IR：
# 1) 该步 error_code = PACKAGING_SEMANTICS_SOURCE_UNREADABLE、文案 =
#    "暂时读不到这个项目的 CAD 图纸解析结果（<异常类名>），请稍后重试；这不代表这个项目还没有解析结果"，
#    审核记录里有一笔 reason=source_unreadable；
# 2) 恢复通道后重试该步直接过，不用重跑第 3 步；
# 3) 换一个真的没解析过的项目 → 仍是既有的"项目里没有可用的 CAD 图纸解析结果，请先重跑图纸解析"。
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_semantics_ir_read_failure_red
# 实现前：Ran 8 tests … FAILED (failures=5)   ← Q1 Q2 Q5 Q6 Q8
# 实现后：Ran 8 tests … OK                    ← Q3 Q4 Q7 三条护栏始终绿
```

| 契约 | 落点 |
| --- | --- |
| §2.1 权威码表 | `tech_app/backend/services/file_preflight.py:STABLE_ERROR_CODES` 新增 `"PACKAGING_SEMANTICS_SOURCE_UNREADABLE": {"http_status": 503, "retryable": True, "message": "暂时读不到这个项目的 CAD 图纸解析结果，请稍后重试；这不代表这个项目还没有解析结果"}`；既有九条（含 `PACKAGING_SEMANTICS_SOURCE_MISSING` 的 422 / True / 逐字文案、`PACKAGING_LAYER_RULES_INVALID` 的 500 / False）一个字没动。 |
| §2.2 读失败分支 | `packaging_semantics/__init__.py:analyze_conversion()`：`cad_ir.load_ir(...)` 包进 `try/except Exception as exc` —— 先 `persistence_mod.audit(project_id, ACTION_FAILED, {"reason": "source_unreadable", "by": author})`，再 `raise FileCapabilityError("PACKAGING_SEMANTICS_SOURCE_UNREADABLE", detected={"project_id", "ir_id", "reason": <异常类名>, "message": <原文前 200 字>}, message="暂时读不到这个项目的 CAD 图纸解析结果（<异常类名>），请稍后重试；这不代表这个项目还没有解析结果") from exc`。`from .. import cad_ir` 仍在 `try` 之外（模块本身缺 = 这条缝不存在，不算读失败）。 |
| §2.2 既有分支逐字不变 | `load_ir` 返回非 dict（含 `None` / list）→ 既有 `PACKAGING_SEMANTICS_SOURCE_MISSING`（码 / `audit.reason="source_missing"` / `detected` 两个键 / 文案）**逐字不变**（Q3 Q4）；`analyze()` 既有口径、`persistence_mod.audit()` 签名、`steps.packaging_semantics()` 的 catch 分支都没改。给 `ir=` 时一次也不调 `cad_ir.load_ir()`（Q7）。 |

**一处既有守卫的镜像同步（已记明）**：`tests/test_dwg_file_capability_preflight_red.py::C1` 用一张硬编码镜像表
断言 `set(STABLE_ERROR_CODES) == set(ERROR_CODES)`。§2.1 要求把新码并进权威闭集，就必须同步镜像（否则"码表多了 1 条"
会把这条守卫判红）。本批只在该测试的镜像表**加一行**（带 Spec 出处注释，样式与 `DWG_USE_DRAWING_FLOW` 那一行相同），
**没有改任何断言、没有放宽任何口径**；这与仓库既有做法一致（`a2292a0`「三处旧守卫按新口径更新」）。
本批自己的红测 `tests/test_packaging_semantics_ir_read_failure_red.py` 一字未改。

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_semantics_red tests.test_dwg_file_capability_preflight_red \
  tests.test_drawing_flow_error_taxonomy_red tests.test_packaging_parts_ir_read_failure_red \
  tests.test_packaging_semantics_ir_read_failure_red   → Ran 118 … OK (skipped=1)
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_dwg_conversion_quality_repair_red tests.test_dwg_capability_truth_red \
  tests.test_dwg_conversion_adapter_red tests.test_dxf_cad_ir_red \
  tests.test_packaging_semantics_red   → Ran 203 … OK (skipped=3)
```

未起服务、未发 HTTP、未连 PG / SQLite、未建项目、未写任何文件、未 push / MR / tag / Release / 未部署。
