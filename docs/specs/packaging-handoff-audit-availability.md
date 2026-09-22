# 规格：交接留痕的可用性必须自己说出来 —— 审计写不下去时调用方不许看不出来

依赖：`docs/specs/packaging-handoff-audit-trail.md`（§2.1 九键载荷与「留痕不是闸门」、
§5.3 把「审计可用性」**登记**为后续批次的话题）、
`docs/specs/packaging-quote-close-loop.md`（回传结果的既有键 `:507-524`）、
`docs/specs/packaging-silent-degradation-disclosure.md`（「悄悄降级」必须披露）。

状态：Spec + 红测（已实现）（原状：`_audit_handoff_sent()` 用 `try/except Exception: pass`
吞掉写审计的异常（`:351-353`），`send_to_quote()` 的返回里既没有 `audit` 键也没有稳定码 ——
审计没落下时，调用方与「写成功」逐字看不出区别）
红测：`tests/test_packaging_handoff_audit_availability_red.py`
行号基线：HEAD `96a7741`

## 0. 一句话目标

「把哪一版推给了报价侧」这条留痕有没有真的落下，必须能从回传结果里读出来：
写成功给 `ok=true`，写失败给**稳定码** + 原异常；而回传本身**照旧成功**（留痕不是闸门）。

## 1. 现状缺口（代码级）

### 1.1 吞异常的那处（`packaging_handoff.py:327`、`:351-353`）

```python
def _audit_handoff_sent(...) -> None:          # :327 —— 返回 None
    ...
    try:
        store.audit(project_id, AUDIT_SENT_ACTION, payload)   # :351
    except Exception:  # noqa: BLE001 — 留痕失败不许把回传判成失败   # :352
        pass                                                   # :353
```

### 1.2 两个调用点都把返回值丢掉了

`send_to_quote()`（`:402`）的**两条**路径都调它、都不看返回：

- **复用路径**（`:440-451`）：`outcome = _reuse_outcome(row, package)` → `_audit_handoff_sent(...)`
  → `return outcome`（`_reuse_outcome()` `:526` 产出的 12 键里没有 `audit`）；
- **首次回传路径**（`:503-506` → `:507-524`）：落库 → `_audit_handoff_sent(...)` →
  `return {...}`（那 12 键里也没有 `audit`）。

于是「审计后端不可用 / 权限不对 / 磁盘满 / 表不存在」与「审计写成功」在调用方
（路由 `main.py:7703` 把结果原样包成 `{"handoff": result}`、财务界面读它）看来**逐字相同**；
`packaging-handoff-audit-trail.md` §5.3 已把这条记为遗留：「代价是审计落不下去时调用方从
返回值上看不出来 —— 本批红测不覆盖这条，留作后续批次的『审计可用性』话题，不在此自增判据」。

### 1.3 本批不动「审计该不该是闸门」这条既有裁决

按 `packaging-handoff-audit-trail.md` §2.1，留痕失败**不许**把回传判成失败
（既有红测 `tests/test_packaging_handoff_audit_red.py` 的护栏还要求：被拒路径零审计、
成功路径恰好一条审计）。本批**只补「读得出来」**：不改判据、不改载荷、不改时机。

### 1.4 后果

34 上如果审计写不进去（例如存储后端异常），「这一版到底有没有留痕」只能靠翻服务端日志；
界面与接口都会给出一次**看起来完全成功**的回传 —— 事后对账时无法区分「没留痕」与「留痕了但查错地方」。

## 2. 契约

### C1 `_audit_handoff_sent()` 返回稳定披露体（不再返回 `None`）

```python
{"attempted": True, "ok": bool, "action": AUDIT_SENT_ACTION, "code": str, "message": str}
```

- 键集**固定这五个**：不许加售价 / 毛利 / 整份交接包 / 登录凭据（沿用 `_FORBIDDEN_COST_KEYS`
  与 §2.1 的载荷纪律）；
- `store.audit(...)` 正常返回 → `ok=True`、`code=""`、`message=""`；
- 抛**任何** `Exception` → `ok=False`、`code=AUDIT_UNAVAILABLE_CODE`、
  `message` **必须含异常类名**（如 `RuntimeError`）且含 `str(exc)` 原文；`str(exc)` 为空时
  只给类名，不许给空串；
- `attempted` 恒 `True`（这个函数存在的唯一意义就是写审计）；
- 行为纪律不变：**不抛异常**、不改调用时机、不改九键载荷、不改 `_FORBIDDEN_COST_KEYS` 的 pop。

### C2 `AUDIT_UNAVAILABLE_CODE` 是模块级稳定码

`tech_app/backend/services/packaging_handoff.py` 新增模块级常量
`AUDIT_UNAVAILABLE_CODE = "PACKAGING_HANDOFF_AUDIT_UNAVAILABLE"`，与 `AUDIT_SENT_ACTION`（`:324`）
并列在常量区，源码里逐字可见。它只描述「留痕没落下」，**不是** `HandoffError` 的码
（回传失败仍用既有码）。

### C3 `send_to_quote()` 两条路径的返回都带 `audit`

- **首次回传**（`:507-524`）返回体新增 `"audit": <C1 的披露体>`；
- **复用路径**（`:451` 的 `return outcome`）返回体也新增 `"audit": <C1 的披露体>`
  （复用同样是动作，留痕写的也是被复用那一行 —— §2.1 既有口径）；
- `audit` 是**新增键**：既有键（`handoff_no` / `handoff_id` / `version_no` / `already_sent` /
  `industry` / `handoff_kind` / `quote_session_id` / `business_case_id` / `package_fingerprint` /
  `package` / `handoff` / `bridge`）一个字不改、逐字不变；
- **审计失败时**：`audit.ok=False`，而 `already_sent` / `handoff_no` / `version_no` /
  落库记录（`save_packaging_handoff` 收到的 record）与「审计写成功」时**逐字相同**。

### C4 披露不参与任何判据

- `audit` 不进 `package_fingerprint()`、不进 `_reuse_outcome()` 的判重、不进 `_guard_gaps()`
  的放行判断；
- 审计失败**不改变**异常类型（该抛 `HandoffError` 的照旧抛）、不改变是否落库、不改变返回码。

### C5 冻结面

- `AUDIT_SENT_ACTION` 逐字不变；九键载荷（`requirement_no` / `scenario_code` / `handoff_no` /
  `version_no` / `already_sent` / `cost_result_version` / `has_gaps` / `quote_session_id` / `by`）不变；
- `handoff_package()` 仍然**不**写审计；通用行业的 `integration_send_to_quote` 与
  `cost_flow.py` 不碰；
- 不加接口 / 不改路由形状（`main.py` 的 `{"handoff": result}` 原样透传，不改一行）/
  不加依赖 / 不迁移 / 不回填历史回传记录。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_handoff.py`：新增 `AUDIT_UNAVAILABLE_CODE`；
   `_audit_handoff_sent()` 改为返回 C1 的披露体；`send_to_quote()` 的两处返回加 `audit` 键；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把留痕改成闸门（审计失败不许抛、不许改回传结果、不许回滚已落库记录、不许跳过落库）；
- 不许在 `audit` 披露体里塞售价 / 毛利 / 整份交接包 / 凭据；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_availability_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_handoff_audit_red \
  tests.test_packaging_handoff_input_drift_red \
  tests.test_packaging_quote_send_recovery_red \
  tests.test_packaging_quote_close_loop_red
```

## 6. 已记录的边界

1. 只做**披露**：审计没落下时本批不重试、不缓存、不补写、不告警（「补写 / 重试」是另一条话题）；
2. 只覆盖包装这条回传（`packaging_handoff.send_to_quote()`）；通用行业的回传桥与本模块的
   `handoff_package()` 不动；
3. 披露体是**回传事实**的一部分，不是审计记录本身：查「谁把哪一版推给了报价侧」仍然只能翻
   项目审计（`workflow:packaging_handoff_sent`）—— 本批只是让「那一条没写上」不再隐形。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_availability_red
# 实现前：Ran 15 tests … FAILED (failures=7, errors=2)   ← 9 红：A1 A2 A3 A4 A5 / B1 B2 B3 B4
# 实现后：Ran 15 tests … OK                              ← B5 与 C1–C5 六条护栏始终绿
```

| 契约 | 落点（`tech_app/backend/services/packaging_handoff.py`） |
| --- | --- |
| §C2 稳定码 | 常量区新增 `AUDIT_UNAVAILABLE_CODE = "PACKAGING_HANDOFF_AUDIT_UNAVAILABLE"`（与 `AUDIT_SENT_ACTION` 并列） |
| §C1 披露体 | `_audit_handoff_sent()` 的返回类型 `-> None` 改为 `-> Dict[str, Any]`；`try` 前置 `disclosure = {"attempted": True, "ok": True, "action": AUDIT_SENT_ACTION, "code": "", "message": ""}`，`except` 分支置 `ok=False` / 稳定码 / `"<类名>: <原文>"`（原文为空时只给类名），尾部 `return disclosure`；九键载荷与 `_FORBIDDEN_COST_KEYS` 的 pop 一个字未动、仍然不抛 |
| §C3 两条路径 | 复用路径 `audit = _audit_handoff_sent(...)` → `outcome["audit"] = audit` → `return outcome`；首次回传路径 `audit = _audit_handoff_sent(...)`，返回体新增 `"audit": audit`（既有 12 键逐字不变） |
| §C4 / §C5 冻结面 | `audit` 不进 `package_fingerprint()` / `_reuse_outcome()` / `_guard_gaps()`；`main.py` 的 `{"handoff": result}` 原样透传未改一行；`handoff_package()` 仍不写审计；`AUDIT_SENT_ACTION` 与九键不变 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_handoff_audit_red tests.test_packaging_handoff_input_drift_red \
  tests.test_packaging_quote_send_recovery_red tests.test_packaging_quote_close_loop_red
# Ran 122 … FAILED (failures=1) —— 唯一一条是既有挂账 tests/test_packaging_quote_send_recovery_red.py::C1
# （`docs/specs/packaging-quote-send-recovery.md` §2.5 已记为"夹具自遮挡"，本批未碰、不属本 Spec 范围）
```

未改 `tests/` 下任何文件、未改路由、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。

> 后续（2026-09-22）：本 Spec §C1 的「披露体固定五键」已由
> `docs/specs/packaging-handoff-audit-relay.md` §C1 **重指为七键**（原五键一个不少，
> 语义不变；新增 `attempts` / `pending`），本文件正文 §1–§6 不动；同批把「只披露、不重试、
> 不补写」升级为重试一次 + 待补写 + 两条接口。
