# 规格：留痕没落下时的**重试 / 补写 / 告警**

依赖：`docs/specs/packaging-handoff-audit-availability.md`（§6 边界 1 明写"只做披露：不重试、不缓存、
不补写、不告警 ——「补写 / 重试」是另一条话题"；本批就是那条话题，并且**重指**它的"披露体固定五键"）、
`docs/specs/packaging-quote-send-recovery.md`（回传这条链的拒绝口径与留痕九键）、
`docs/specs/packaging-silent-degradation-disclosure.md`（说不出"有一条留痕没落下"就是静默降级）。

状态：Spec + 红测（已实现）（原状：`packaging_handoff._audit_handoff_sent()`（`:331`）**只试一次**，
失败就把 `ok=False` + `AUDIT_UNAVAILABLE_CODE` 放进披露体 —— 那条留痕**就此丢掉**：
项目审计里查不到"谁把哪一版推给了报价侧"，而披露体只活在这一次响应里，刷新一次就没了；
`main.py` 也没有任何"还有几条留痕没落下"的读接口，"告警"在界面上无处可取）
红测：`tests/test_packaging_handoff_audit_relay_red.py`
行号基线：HEAD `7e2de0b`

## 0. 一句话目标

留痕写不进去时：**当场再试一次**；还是不行就**记成待补写**（幂等、有上限），
并且说得出来"这条留痕现在处在什么状态"；另有两条接口 —— 一条**读**（还欠几条、都是哪几条）、
一条**补写**（把欠的逐条补上，成功的划掉、失败的留着）。

## 1. 现状缺口（代码级）

1. `_audit_handoff_sent()`（`:331`）`try/except` 只包一次 `store.audit()`：**没有重试**，
   失败即丢；
2. 没有任何**待补写**的落点 —— 披露体只在回传响应里活一次，刷新即无；
3. `main.py` 只有 `send` / 读 / versions / package 四条包装报价路由（`:7673-7676`），
   **没有**"还欠几条留痕"的读接口，也没有补写接口 —— 告警与补写都无处发起。

## 2. 契约

### C1 重试一次，并让披露体回答"试了几次、有没有待补写"

- 新增常量 `AUDIT_RETRY_LIMIT = 1`：`_audit_handoff_sent()` 最多尝试 `1 + AUDIT_RETRY_LIMIT = 2` 次
  （**同步、不 sleep、不递归**）；
- 披露体由五键扩为**七键**（`packaging-handoff-audit-availability.md` §C1 按本批**重指**：
  原五键一个不少、语义不变）：`{attempted, ok, action, code, message, attempts, pending}`；
  - `attempted` 恒 `True`；`action` 恒 `AUDIT_SENT_ACTION`；
  - `attempts` = **实际**尝试次数（写成功的那一次算在内；两次都失败 → `2`）；
  - 任一次成功 → `ok=True`、`code=""`、`message=""`、`pending=""`；
  - 两次都失败 → `ok=False`、`code=AUDIT_UNAVAILABLE_CODE`、`message` 与今天逐字同形
    （`"<类名>: <原文>"`，原文为空时只有类名），并按 C2 记成待补写：
    记上 → `pending="recorded"`；记不上 → `pending="unavailable"`；
- 九键载荷（`payload`）与 `_FORBIDDEN_COST_KEYS` 的 pop 一个字不动。

### C2 待补写文档（幂等、有上限、写不进去也不抛）

- 新增常量：`AUDIT_PENDING_DOC_KEY = "packaging_handoff_audit_pending"`、`AUDIT_PENDING_MAX = 20`、
  `AUDIT_PENDING_UNAVAILABLE_CODE = "PACKAGING_HANDOFF_AUDIT_PENDING_UNAVAILABLE"`；
- `record_pending_audit(project_id, payload, *, code="", message="", by="") -> dict`
  返回**四键** `{ok, code, pending_id, count}`：
  - `payload` 不是 dict → `ok=False` + `AUDIT_PENDING_UNAVAILABLE_CODE`（**不抛**）；
  - `pending_id` = 九键载荷的规范 JSON 的 sha256 前 16 位（**同一份留痕重复记 → 同一条**，
    `count` 不涨 —— 幂等）；
  - 新记录插到最前，最多留 `AUDIT_PENDING_MAX` 条；每条带
    `{pending_id, payload, code, message, recorded_at, by}`；
  - 落库抛异常 → `ok=False` + 那个码（**不抛**、不改调用方的结果）；
- `load_pending_audits(project_id) -> {"items": [...], "count": n}`：
  读不到 / 没记过 → `{"items": [], "count": 0}`（**不抛**）；
- `relay_pending_audits(project_id, *, by="") -> {attempted, relayed, remaining, code, message}`：
  - 逐条 `store.audit(project_id, AUDIT_SENT_ACTION, item["payload"])`；成功 → 从待办里删掉，
    失败 → **留着**（下次还能再补）；
  - `attempted` = 这次试了几条、`relayed` = 补上了几条、`remaining` = 补完之后**还欠几条**；
  - 有失败 → `code=AUDIT_UNAVAILABLE_CODE`、`message` 含第一条异常的类名（与 C1 同形）；
    全成功 → 空码空消息；
  - 跑过一次就写一条审计 `AUDIT_RELAY_ACTION = "workflow:packaging_handoff_audit_relayed"`，
    载荷 `{relayed, remaining, by}`（写不进去**不抛**、不改返回值）；
  - 一条都没有 → `attempted=0, relayed=0, remaining=0`、空码空消息（**不抛**）。

### C3 两条路由（`main.py`）

- `GET /api/projects/{pid}/requirement/packaging-quote/audit-pending`：`_workflow_project` + **纯读**
  → `{"project_id", "count", "items"}`（`items` 逐条原样带出 `pending_id` / `code` / `message` /
  `recorded_at` / `payload`）；
- `POST /api/projects/{pid}/requirement/packaging-quote/audit-pending/relay`：写权限**直接引用**
  `packaging_handoff.HANDOFF_WRITE_ROLES`（与回传那条同口径）+ `_workflow_project` →
  `packaging_handoff.relay_pending_audits(pid, by=<用户名>)`，返回那个五键披露体 +
  `project_id` + 补写后**再读一次**的 `count` / `items`。

### C4 冻结面

- 九键载荷、`AUDIT_SENT_ACTION`、`_FORBIDDEN_COST_KEYS` 一字不动；
- `send_to_quote()` 的回传结果键与既有行为不变（`audit` 仍是那个披露体，只是多了两键）；
  `package_fingerprint()` / `_reuse_outcome()` / `_guard_gaps()` / `handoff_package()` 一字不动；
- `main.py` 既有四条包装报价路由的路径与行为不变（只新增两条）；
- 不新增依赖、不联网；不改前端（面板展示是下一批）；
- 不改 `tests/` 下任何文件 —— 唯一例外：`packaging-handoff-audit-availability.md` 那条
  **五键守卫**按 C1 重指为七键（只改计数与注释里点名的那两键，**断言其余部分一字不动**）。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_handoff.py`：新增常量 + `_audit_handoff_sent()` 的重试与
   `pending` 两键 + `record_pending_audit()` / `load_pending_audits()` / `relay_pending_audits()`；
2. `tech_app/backend/main.py`：两条待补写路由（GET / POST）；
3. `tests/test_packaging_handoff_audit_availability_red.py`：**只**把那条五键计数与注释改成七键
   （Spec §C4 的重指流程，别的一条都不许动）；
4. `docs/specs/packaging-handoff-audit-availability.md`：§7 末尾追加一行指针（正文 §1–§6 不动）；
5. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许无界重试 / 循环重试 / `sleep` / 后台线程；重试次数必须由上界常量决定；
- 不许把重试成功也说成"留痕失败"（`ok` 只回答**最终**有没有落下）；
- 不许把待补写记成"已补写"；`relay` 失败的条必须留在待办里；
- 不许把待补写文档当成审计记录本身（那是两件事：待办是"欠条"，审计才是"已留痕"）；
- 不许给待补写编内容（`payload` 逐字来自那次失败的回传载荷）；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_relay_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_handoff_audit_availability_red tests.test_packaging_handoff_audit_red \
  tests.test_packaging_handoff_input_drift_red tests.test_packaging_quote_send_recovery_red \
  tests.test_packaging_quote_close_loop_red
# 既有挂账：tests/test_packaging_quote_send_recovery_red.py::C1（Spec §2.5「夹具自遮挡」，本批不碰）
```

## 6. 已记录的边界

1. 本批只做**留痕**这一条：待补写不含回传本身（回传该成功还是成功，留痕不是闸门）；
2. 重试只针对**异常**（后端抛错）；"写成功但没有记录"这种语义问题本批不判（那要后端给回执）；
3. 待补写文档同样是后端文档，跟着同一套存储 —— 存储整体不可用时它也会写不进去，
   这时 `pending="unavailable"`（**如实说**，不假装记上了）；
4. 面板上的告警（左栏 / 状态条那一句）是下一批；
5. `relay` 不做自动调度：由人（或下一批的定时入口）点名发起。

## 7. 落地状态（2026-09-22，Codex 实现）

红基（实现前，`git stash push -- tech_app/backend/services/packaging_handoff.py tech_app/backend/main.py` 后实跑）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_relay_red
Ran 28 tests … FAILED (failures=5, errors=16)          # 21 红 / 7 绿
```

- failures=5：`A1`（披露体五键 ≠ 七键）、`A2`（没有重试 → `attempts` 缺）、
  `A3`（两次失败不记待补写）、`A4`（`pending` 缺）、`C1`（两条路由不存在）；
- errors=16：`A5` + `B1`–`B11` + `C2`–`C4` + `D3` —— 全是 `AttributeError`
  （`AUDIT_RETRY_LIMIT` / `AUDIT_PENDING_DOC_KEY` / `AUDIT_PENDING_MAX` /
  `AUDIT_PENDING_UNAVAILABLE_CODE` / `AUDIT_RELAY_ACTION` /
  `record_pending_audit` / `load_pending_audits` / `relay_pending_audits` 一个都不存在）；
- 7 绿全是护栏：`A6`（重试不 sleep / 不 while / 无线程）、`A7`（九键载荷 + 禁售价字段）、
  `A8`（动作名与稳定码逐字）、`D1`（回传不许顺手补写）、`D2`（既有四条路由路径）、
  `D4`（`handoff_package()` 不写审计）、`D5`（不新增依赖）。

实现后：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_relay_red
Ran 28 tests in 0.011s
OK
```

| 契约 | 落点 |
| --- | --- |
| §C1 重试 + 七键 | `packaging_handoff.AUDIT_RETRY_LIMIT = 1`；`_audit_handoff_sent()` 改为 `for attempt in range(1 + AUDIT_RETRY_LIMIT)`（同步、不 sleep、不 while），披露体键集 `{attempted, ok, action, code, message, attempts, pending}`，两次都失败 → `recorded` / `unavailable`；九键载荷与 `_FORBIDDEN_COST_KEYS` 的 pop 一字未动 |
| §C2 欠条 | `AUDIT_PENDING_DOC_KEY` / `AUDIT_PENDING_MAX = 20` / `AUDIT_PENDING_UNAVAILABLE_CODE` / `AUDIT_RELAY_ACTION` + `_pending_id_of()` / `_pending_items()` / `record_pending_audit()` / `load_pending_audits()` / `relay_pending_audits()`；文档走 `get_backend().get_doc / put_doc`（与 `packaging_parts.py` 同一套版本化存储；`put_doc` 整份替换，`get`–改–`put` 与零件件级侧档同形） |
| §C3 两条路由 | `main.py` 新增 `PACKAGING_QUOTE_AUDIT_PENDING_PATH`（GET，纯读）与 `PACKAGING_QUOTE_AUDIT_RELAY_PATH`（POST，`_require(user, packaging_handoff.HANDOFF_WRITE_ROLES, …)`），紧跟既有一条 `@app.get(PACKAGING_QUOTE_PACKAGE_PATH)` 之后注册 |
| §C4 重指 | `tests/test_packaging_handoff_audit_availability_red.py` 的 `DISCLOSURE_KEYS` 五键 → 七键（`attempts` / `pending`）；两处 `assertEqual(set(DISCLOSURE_KEYS), set(out))` 的报文同步改写，**其它断言一字未动**（重指≠放宽：原五键仍在键集里、语义不变）；`docs/specs/packaging-handoff-audit-availability.md` §7 末尾追加一行指针，正文 §1–§6 未动 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest   tests.test_packaging_handoff_audit_availability_red tests.test_packaging_handoff_audit_red   tests.test_packaging_handoff_input_drift_red tests.test_packaging_quote_send_recovery_red   tests.test_packaging_quote_close_loop_red
Ran 137 tests … FAILED (failures=1)
# 唯一一条是**既有挂账** tests/test_packaging_quote_send_recovery_red.py::C1
# （`docs/specs/packaging-quote-send-recovery.md` §2.5 已记为「夹具自遮挡」，本批未碰）
```

边界（与 §6 一致）：重试只针对**异常**上界一次；待补写只是"欠条"、不冒充审计记录；
`relay` 不自动调度、不后台跑；待补写文档与审计记录同为后端文档，存储整体不可用时
`pending="unavailable"`（如实说）；未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 部署。
