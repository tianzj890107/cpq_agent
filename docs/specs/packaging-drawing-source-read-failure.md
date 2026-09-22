# 规格：图纸源文件"读不到"不许折成"空文件"——更不许把空内容的哈希当成这一版图纸

状态：Spec + 红测（已实现）（红测当前全绿 —— 该切片已随并行实现批次落地，状态行随事实更新，
2026-09-22 复核；原始缺口见 §1：`packaging_drawing_flow/__init__.py _source_bytes()` 把异常吞成
`b""`、`sha256(b"")` 被当成这一版图纸的锚点）
红测：`tests/test_packaging_drawing_source_read_failure_red.py`

血缘：承接 `dwg-semantics-agent-flow.md` §6.1/§6.2（`source_sha256` 是 run_id 复用判定、
锚点与下游 stale（`source_sha256_changed`）的共同依据；本批不动它的算法，只禁止把"读不到"
算成"空内容那一版"）、
`drawing-flow-error-taxonomy.md` §3 C1/C2（错误分类与文案必须来自真实原因；本批给第 1 步补一个码，
既有码与文案逐字不变）、
`packaging-silent-degradation-disclosure.md`（失败 / 空 / 没有不许同形）、
`packaging-stage-chain-read-failure-disclosure.md`（同一病症在链条侧）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

`b""` 今天有三个来源：**附件内容真的是空的** / **这个项目还没有源附件** / **blob 通道读不到**。
前两者该照旧，第三者必须说出来 —— 因为它的哈希是 `sha256("")` 这个**看起来完全合法**的常量，
一旦写进锚点，之后所有 stale 比对都会说"源文件没变"。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_drawing_flow/__init__.py`：

```python
252 def _source_bytes(project_id: str, meta: Dict[str, Any]) -> bytes:
253     name = str((meta or {}).get("source_path") or "")
254     if not name:
255         return b""                       # ← 这个项目没有源附件
256     try:
257         data = store._blob().get_bytes("%s/%s" % (project_id, name))
258     except Exception:
259         return b""                       # ← blob 读不到 —— 与上一处同形
260     return bytes(data) if isinstance(data, (bytes, bytearray)) else b""
```

三个后果：

1. **`source_sha256` 把"读不到"写成一版内容**：`start()`（`:300-301`）无条件
   `hashlib.sha256(content).hexdigest()`，`b""` 给的是 `sha256("")` =
   `e3b0c442…b855` —— 一个**格式完全合法**的哈希。它会被写进
   `inputs.source_sha256`（`:319`）、锚点（`:329`）并参与：
   - `run_id_for()` 的 run 判定（`:310`，"两次都读不到"会算成同一份输入）；
   - `_reusable()` 的复用判定（`:268`，"读不到"与"上次读不到"会被当成同一份输入）；
   - 下游 stale 的 `source_sha256_changed`（`:288` 附近）——
     **图纸真的换了、但 blob 这一次读不到**时，锚点里存的还是那个空内容哈希，
     与上一版相同 → 结论是"源文件没变"，下游照旧"没过期"。
2. **第 1 步拿空字节去预检，直接判死且判成"文件是空的"**：`_context()`（`:373`）把
   `content` 给第 1 步；`steps.file_preflight()`（`steps.py:92-101`）调
   `detect_file_format(filename, b"")`。实测（离线纯函数，`b""`）：

   ```
   {'detected_format': 'unsupported', 'file_size': 0, 'is_empty': True,
    'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}
   ```

   于是 `steps.py:108-109` 走 `FILE_EMPTY` 分支：
   `_failed("FILE_EMPTY", "上传的图纸是空文件，请重新上传", detail, False)` ——
   **`retryable=False`**、文案把用户指向"重传"，而真相是 blob 这一次读不到；
   返回体里的 `detail.sha256` 就是那个空内容哈希。
3. **三态在返回体上不可分**：`grep -rn "_source_bytes" tech_app/` 命中处没有任何字段能回答
   "这一趟是读不到源文件，还是这个项目没有源文件"。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_drawing_flow/__init__.py`
   - `_source_bytes()` 的三态必须可分（实现形状不限：例如另加
     `_source_bytes_detail(project_id, meta) -> {"content": bytes, "source": …, "reason": …}`，
     既有 `_source_bytes()` 的返回类型仍是 `bytes`）：
     - `"blob"`：读到（含内容真的是空的）；
     - `"none"`：`meta` 里没有 `source_path`（这个项目确实还没有源附件）；
     - `"unavailable"`：`store._blob().get_bytes()` 抛异常（`reason` = 异常类名）。
   - `start()`：`inputs` **新增两个必存在键**：
     - `source_content` ∈ `{"blob", "none", "unavailable"}`；
     - `source_content_unavailable`：`{}` / `{"code": "drawing_source_unavailable",
       "reason": "<异常类名>"}`（仅 `"unavailable"` 非空）；
     并且 `source_sha256` **只有在 `source_content == "blob"` 时**才允许是真哈希；
     其余两态给 `""`（**不许**给 `sha256(b"")`）—— 与 `packaging_cost.bom_input_hash()`
     同一条纪律："没有内容"不是一版内容。`drawing_version` / `snapshot` /
     `run_id_for()` / `_reusable()` 的算法与调用顺序逐字不变；`start()` 照旧不抛。
   - `_context()`：**新增两个必存在键** `content_source` 与 `content_unavailable`
     （形状同上）；`content` 仍是 `bytes`（该给 `b""` 仍给 `b""`，本批只补披露）。
2. `tech_app/backend/services/packaging_drawing_flow/steps.py:file_preflight()`
   - `ctx.get("content_source") == "unavailable"` 时**在调 `detect_file_format()` 之前**返回：
     `{"status": "failed", "error_code": "DRAWING_SOURCE_UNAVAILABLE",
       "error_message": "暂时读不到这个项目上传的图纸文件（<reason>），请稍后重试；"
                        "这不代表图纸没有上传",
       "retryable": True,
       "detail": {"reason": "<异常类名>", "content_source": "unavailable"}}`；
     **不许**是 `blocked`（这是可重试的读取故障，不是缺前置条件），
     文案里**不许**出现"重新上传"（重传救不了它）；
   - `content_source` 为 `"none"` / `"blob"` / 键不存在时，`file_preflight()` 的行为与
     返回体**逐字不变**（键不存在 = 旧 run 与单测的向后兼容路径）。
3. `tech_app/backend/services/packaging_drawing_flow/model.py`
   - `ERROR_CODES` **新增** `"DRAWING_SOURCE_UNAVAILABLE": (503, True)`（其余键不动）。

## 3. 禁止事项

- 不许改 `source_sha256` 的算法与 `run_id_for()` / `_reusable()` / `_stale_reasons()` 的判据
  （`"blob"` 那一路必须逐字不变）；不许把 `inputs` 里既有四个键改名或删掉。
- 不许把 `start()` / `_context()` 的异常抛给调用方，也不许在读接口里重传 / 重试 / 调模型 / 联网。
- 不许改 `file_preflight` 的既有码（`FILE_PREFLIGHT_FAILED` / `PACKAGING_FLOW_DEPENDENCY_MISSING`）
  与它们的文案、`_unavailable()` / `_blocked()` 的形状；不许动 `dwg_convert` 及后续步骤的码。
- 不许改 `model.ERROR_CODES` 里既有键、不许改 `STEP_IDS` / `STEP_TITLES` / `_DEPENDS_ON`。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_drawing_flow_red.py`、
  `test_drawing_flow_error_taxonomy_red.py`、`test_drawing_flow_parse_terminal_signal_red.py`）；
  本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （打桩 `store.load_meta` / `store._blob` / `persistence`，不建项目、不落盘）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_source_read_failure_red -v
# R 组（9 条）：
#   R1 blob 读抛异常 → _context() 的 content_source="unavailable"、
#      content_unavailable.code=drawing_source_unavailable、content 仍是 b""（红）
#   R2 blob 读得到 → content_source="blob"、content_unavailable 给 {}（键必须存在）（红）
#   R3 meta 没有 source_path → content_source="none"（"确实没有" ≠ "读不到"）（红）
#   R4 start() 在 blob 读不到时 source_sha256 必须是 ""，**不许**是 sha256(b"")，
#      且 inputs 带 source_content / source_content_unavailable（红）
#   R5 start() 读得到时 source_sha256 仍等于 sha256(真内容)（护栏）
#   R6 file_preflight 在 content_source="unavailable" 时给 DRAWING_SOURCE_UNAVAILABLE、
#      status="failed"、retryable=True、文案不含"重新上传"、也不许给 FILE_EMPTY，
#      且**没有调用** detect_file_format()（红）
#   R7 content_source="none" 时 file_preflight 照旧调 detect、返回体逐字不变（护栏）
#   R8 ctx 里没有 content_source 键（旧 run / 单测）时 file_preflight 照旧（护栏）
#   R9 model.ERROR_CODES 含 DRAWING_SOURCE_UNAVAILABLE: (503, True)（红）
# 现状：R1 R2 R3 R4 R6 R9 红（6 条），R5 R7 R8 绿（3 条护栏）
# 不回归（链路与错误分类的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
```

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/drawing-flow/run        # blob 通道异常时
# 第 1 步：{"status": "failed", "error_code": "DRAWING_SOURCE_UNAVAILABLE",
#           "error_message": "暂时读不到这个项目上传的图纸文件（…），请稍后重试；"
#                            "这不代表图纸没有上传", "retryable": true}
GET  /api/projects/{pid}/drawing-flow
# {"flow": {"inputs": {"source_sha256": "", "source_content": "unavailable",
#                      "source_content_unavailable": {"code": "drawing_source_unavailable", …}}}}
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_source_read_failure_red
# 实现前：Ran 9 tests … FAILED (failures=6)   ← R1 R2 R3 R4 R6 R9
# 实现后：Ran 9 tests … OK                    ← R5 R7 R8 三条护栏始终绿
```

| 契约 | 落点 |
| --- | --- |
| §2.1 三态可分 | `tech_app/backend/services/packaging_drawing_flow/__init__.py`：新增 `SOURCE_CONTENT_STATES = ("blob", "none", "unavailable")` 与 `_source_bytes_detail(project_id, meta) -> {"content", "source", "reason"}`（`reason` = 异常类名）；`_source_bytes()` **返回类型仍是 `bytes`**，退化成一行 `return _source_bytes_detail(...)["content"]`（既有调用方逐字不变）；`_source_disclosure(detail)` 给 `{}` / `{"code": "drawing_source_unavailable", "reason": …}` |
| §2.1 `start()` 两个必存在键 | `inputs` 新增 `source_content`（三态之一）与 `source_content_unavailable`（读不到时非空）；`source_sha256` 改成**只有 `blob` 才**算真哈希，`none` / `unavailable` 一律 `""`。`drawing_version` / `snapshot` / `run_id_for()` / `_reusable()` / `_stale_reasons()` 的算法与调用顺序**逐字未动**（R5 守 R5 那一路）；`start()` 照旧不抛 |
| §2.1 `_context()` 两个必存在键 | 新增 `content_source` / `content_unavailable`；`content` 仍是 `bytes`（该给 `b""` 仍给 `b""`，本批只补披露） |
| §2.2 第 1 步 | `steps.file_preflight()`：`content_source == "unavailable"` 时**在调 `detect_file_format()` 之前**返回 `_failed("DRAWING_SOURCE_UNAVAILABLE", "暂时读不到这个项目上传的图纸文件（<异常类名>），请稍后重试；这不代表图纸没有上传", {"reason", "content_source"}, True)` —— `status="failed"`、`retryable=True`、文案不含"重新上传"；`none` / `blob` / 键不存在三路逐字不变（R7 / R8） |
| §2.3 码表 | `model.ERROR_CODES` 新增 `"DRAWING_SOURCE_UNAVAILABLE": (503, True)`；既有键一个未动（R9） |
| §3 未动的 | `source_sha256` 算法、`run_id_for()` / `_reusable()` / `_stale_reasons()` 判据、`inputs` 既有四个键名、`FILE_PREFLIGHT_FAILED` / `PACKAGING_FLOW_DEPENDENCY_MISSING` 的码与文案、`_unavailable()` / `_blocked()` 形状、`STEP_IDS` / `STEP_TITLES` / `_DEPENDS_ON` 全部逐字未动 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_flow_red \
    tests.test_drawing_flow_error_taxonomy_red tests.test_drawing_flow_parse_terminal_signal_red
# Ran 98 tests … OK (skipped=1)
```
