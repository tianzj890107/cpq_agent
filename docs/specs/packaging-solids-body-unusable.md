# Spec：批量 3D 挤出的正文读不出来，被渲染成"3D 覆盖率 0%（0/0 件可挤出）"

状态：Spec + 红测（已实现）（原状：本批只修两处"`res.ok` 但正文不可用"的出口：`app.js:2142-2165`
批量挤出、`:1450-1490` 单件预览；覆盖率判据、`PACKAGING_SOLID_COPY` 与失败路径的服务端文案一字不改）
红测：`tests/test_packaging_solids_body_unusable_red.py`
血缘：`packaging-silent-degradation-disclosure.md` §2.2（"读不到"≠"事实是空的"）、
`packaging-role-map-unreadable-body.md`（`res.ok` 只说明 HTTP 成功，**不是**"正文可用"）、
`packaging-bom-role-unbound-note-read-failure.md` §2.1（`bom_body_unexpected` 那条同形出口）、
`packaging-parts-solid-coverage.md`（`stats.solid_ok_ratio` 的口径）。
本批 changelog 条目号：`## 442`。

## 0. 一句话目标

两次 3D 请求**HTTP 成功但正文不可用**时（网关返回 200 的 HTML / 空对象 / 形状不对），界面必须说
"这一次读不到挤出结果"，而**不是**替这一件/这一批下结论 ——
"覆盖率 0%（0/0 件可挤出）"、"这一件暂时挤不出来（unknown）" 这两句话今天都会被这种正文触发。

## 1. 现状缺口（代码级，逐条实测）

`tech_app/frontend/app.js:2145-2164`（批量）：

```js
payload = await res.json().catch(() => ({}));        // 200 的 HTML / 空正文 → {}
if (!res.ok) { … throw … }
…
await refreshPackagingParts();
const stats = (payload && payload.stats) || {};      // ← 正文不可用时是 {}
status(`3D 覆盖率 ${Math.round((Number(stats.solid_ok_ratio) || 0) * 100)}%`
  + `（${stats.ok_total || 0}/${stats.part_total || 0} 件可挤出）`);   // ← 渲染成 0%（0/0）
return { ok: true, result: payload };                // ← 还回报成功
```

`tech_app/frontend/app.js:1463-1485`（单件）：

```js
payload = await res.json().catch(() => ({}));        // 同上
if (!res.ok) { … }
if (String(payload.status || "") !== "ok") {         // ← 正文不可用时 status 是空串
  const message = packagingPartSolidReason(payload.reason)
    || `这一件暂时挤不出来（${payload.reason || "unknown"}）`;       // ← 渲染成"挤不出来"
```

两种现实今天同形（本机静态核对，判据都是"缺就是 0 / 缺就是 unknown"）：

| 现实 | 今天的界面 |
| --- | --- |
| 真的算完了、64 件里 0 件可挤出 | `3D 覆盖率 0%（0/64 件可挤出）` ✅ |
| 200 但正文不可用（HTML / `{}` / 网关改包） | `3D 覆盖率 0%（0/0 件可挤出）` + `ok: true` ❌ |
| 真的这一件 `unsupported`（已知 reason） | `这一件暂时挤不出来（…既有理由…）` ✅ |
| 200 但正文不可用 | `这一件暂时挤不出来（unknown）` ❌ |

为什么危险：这两个入口是"这一批/这一件到底能不能出 3D"的唯一结论来源，而 `0%（0/0）`
看起来**完全像一个合法结论**（分母是 0 反而更不容易被察觉），用户会据此认为整份图纸都挤不出来、
或者以为这一件永远挤不出来；而真因（这一次没读到）永远不会被说出来。
`## 388`（角色映射正文）与 `bom_body_unexpected`（BOM 正文）已经为同一类情况立了先例：
`res.ok` 只说明 HTTP 成功，正文可用性要单独判。

## 2. 契约

### 2.1 新纯函数 `packagingSolidsBatchFacts(payload)`

返回 `{available, ok_total, part_total, ratio, unsupported_total, headline, message}`：

- **可用**（`payload` 是对象 且 `payload.stats` 是对象 且 `payload.stats.part_total` 是可解析的
  非负数字）：`available: true`、`message: ""`，`ok_total` / `part_total` / `ratio` /
  `unsupported_total` 逐字取自 `stats`（缺的按 `0`），`headline` 逐字等于既有文案
  `` `3D 覆盖率 ${Math.round(ratio * 100)}%（${ok_total}/${part_total} 件可挤出）` ``
  （即 25% / 16 / 64 → `3D 覆盖率 25%（16/64 件可挤出）`）；
- **不可用**：`available: false`、`headline: ""`，`message` 逐字为
  `这一次读不到批量挤出结果（正文里没有覆盖率），请稍后重试；这不代表一件都挤不出来。`
- 纯函数纪律：函数体内不得出现 `document.` / `window.` / `fetch(` / `localStorage` / `alert(`。

### 2.2 新纯函数 `packagingSolidBodyProblemText(payload)`

- `payload` 不是对象，或 `status` 不是非空字符串 → 逐字返回
  `这一次读不到这一件的挤出结果（正文里没有结论），请稍后重试；这不代表这一件挤不出来。`；
- `status` 是非空字符串（`"ok"` / `"unsupported"` / 别的结论）→ 返回 `""`
  （结论路径仍由既有 `packagingPartSolidReason()` + 既有兜底文案渲染，一个字不改）。

### 2.3 两处调用点

- `packagingPartsSolidBatch()`：`facts.available` 为假 → `status(facts.message)`，返回
  `{ok: false, error: {code: "solids_body_unexpected", message: facts.message}}`；**不许**再渲染
  `3D 覆盖率 …%`、**不许**返回 `ok: true`。可用 → `status(facts.headline)`、`await
  refreshPackagingParts()`、`return {ok: true, result: payload}` 逐字不变；
- `packagingPartSolidPreview()`：`packagingSolidBodyProblemText(payload)` 非空 →
  `notePackagingPartSolid(text)` 并返回 `{ok: false, error: {code: "solid_body_unexpected",
  message: text}}`；**不许**再渲染 `这一件暂时挤不出来（unknown）`。`status === "ok"` 与已知
  `reason` 两条路径逐字不变（`正在计算 3D 挤出体…` / `已挤出：…` / `loadSTL(…)` 都不动）；
- 源码守卫：这两处函数体内不许再出现 `Number(stats.solid_ok_ratio) || 0` 这种"缺就是 0"的直接渲染。

## 3. 非目标

- 不许改后端（`packaging_part_solids` / `PACKAGING_PARTS_SOLIDS_PATH` 的返回体与状态码都不动）；
- 不许改 `3D 覆盖率 …` 这句文案本身（可用时必须逐字不变），不许改 `PACKAGING_SOLID_COPY` /
  `packagingPartSolidReason()` / `notePackagingPartSolid()`；
- 不许把 `unsupported` 当失败（它是结论），不许改失败路径的文案取法（服务端 `detail.message`
  仍然优先，只在没有 message 时用既有 `HTTP <status>` 兜底）；
- 不许改覆盖率口径（`stats.solid_ok_ratio` 仍是唯一来源），不许在前端重算覆盖率。

## 4. 红测映射（`tests/test_packaging_solids_body_unusable_red.py`，8 条）

| 用例 | 夹具 | 期望 | 现状 |
| --- | --- | --- | --- |
| V1 | `packagingSolidsBatchFacts({})` / `null` / 字符串 | `available:false` + 逐字 message | 红（函数不存在） |
| V2 | `stats` 不是对象；`stats.part_total` 是 `"N/A"` / 缺失 | 一律 `available:false`（缺就是 0 的写法不许留） | 红 |
| V3 | `stats = {solid_ok_ratio: 0.25, ok_total: 16, part_total: 64}` | `available:true`、`headline === "3D 覆盖率 25%（16/64 件可挤出）"` | 红（函数不存在） |
| V4 | 纯函数守卫 | 函数体内无 `document.` / `window.` / `fetch(` / `localStorage` / `alert(` | 红（函数不存在） |
| V5 | `packagingSolidBodyProblemText({})`、`{status: 0}`、`null` | 逐字"读不到这一件的挤出结果"那句 | 红（函数不存在） |
| V6 | `{status:"ok", …}`、`{status:"unsupported", reason:"outline_open"}` | 返回 `""`（结论路径不受影响） | 红（函数不存在） |
| V7 | 源码守卫 | 批函数仍渲染 `3D 覆盖率`、仍 `await refreshPackagingParts()`、仍 `return { ok: true, result: payload }`；单件函数仍用 `packagingPartSolidReason(` / `正在计算 3D 挤出体…` / `loadSTL(` | 护栏（今天绿） |
| V8 | 源码守卫 | 两处函数体各引用新函数；批函数体内**不再**出现 `Number(stats.solid_ok_ratio)` | 红 |

## 5. 实测（本机，HEAD 工作副本）

```text
tests.test_packaging_solids_body_unusable_red   Ran 8 … FAILED (failures=7)
  V1 批函数不可用正文（{} / null / 字符串）→ 今天渲染 0%（0/0）+ ok:true   （红，纯函数不存在）
  V2 stats 形状不对（缺 part_total / "N/A" / -1）→ 同样被折成 0 件      （红）
  V3 可用正文 → 新纯函数给逐字 headline（今天没有该函数）                （红）
  V4 纯函数纪律（无 DOM / fetch）                                       （红）
  V5 单件 status 缺失 → 今天说「这一件暂时挤不出来（unknown）」          （红）
  V6 有结论（ok / unsupported）→ 读不到提示必须为空                      （红，纯函数不存在）
  V8 两处调用点接线 + 批函数体内不许再有 Number(stats.solid_ok_ratio)    （红）
  V7 既有渲染路径（3D 覆盖率 / refreshPackagingParts / ok:true / reason 表 /
     loadSTL）逐字仍在                                                  （护栏绿）

不回归（3D 两批 + 仓库级状态护栏，全部离线；`node --check app.js` 退出码 0）：
tests.test_packaging_parts_solid_coverage_red        Ran 23 OK
tests.test_packaging_solids_parts_version_binding_red Ran 6 OK
```

## 6. 交付边界

本批只写 Spec + 红测（业务实现不在本批）；未改业务实现、未改既有测试、未放宽任何断言、
未起服务、未发 HTTP、未连 PG / 34、未写业务数据、未跑真 DWG；未 push / MR / tag / Release / 未部署。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 441`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 `packagingSolidsBatchFacts(payload)` | `tech_app/frontend/app.js`（`packagingPartsSolidBatch()` 之前，顶层纯函数） | `available` 只认「`stats` 是对象 **且** `part_total` 是可解析的非负数字」（`null` / 缺键 / `"N/A"` / 负数都算不可用）；可用时 `headline` 逐字 `` `3D 覆盖率 ${Math.round(ratio*100)}%（${ok}/${part_total} 件可挤出）` ``；不可用时 `headline: ""` + 逐字那句「这一次读不到批量挤出结果…」 |
| §2.2 `packagingSolidBodyProblemText(payload)` | 同文件（`packagingPartSolidPreview()` 之前） | `status` 不是非空字符串 → 逐字那句「这一次读不到这一件的挤出结果…」；有结论（`ok` / `unsupported` / 别的）→ `""` |
| §2.3 批量调用点 | `packagingPartsSolidBatch()` | 不可用 → `status(facts.message)` + `{ok:false, error:{code:"solids_body_unexpected", …}}`；可用 → `await refreshPackagingParts()` + `status(facts.headline)` + `return { ok: true, result: payload }`；`Number(stats.solid_ok_ratio) || 0` 那种「缺就是 0」的渲染已删 |
| §2.3 单件调用点 | `packagingPartSolidPreview()` | 新增 `packagingSolidBodyProblemText(payload)` 非空分支（`code:"solid_body_unexpected"`）；`packagingPartSolidReason()` / 「正在计算 3D 挤出体…」/ `loadSTL(…)` 三条既有路径一字未动 |
| §3 非目标 | 未动 | 后端、`PACKAGING_SOLID_COPY`、`packagingPartSolidReason()`、`notePackagingPartSolid()`、覆盖率口径、失败路径文案取法全部未改；`node --check app.js` 通过 |

### 7.1 红测自身的两处缺陷（实现无法绕过，需测试侧改 2 行）

红测 `tests/test_packaging_solids_body_unusable_red.py` 的 8 条里，**5 条（V1/V2/V3/V5/V6）在
"纯函数还没写"之外还有各自的结构性缺陷**，实现方不许改 `tests/`，因此它们仍红：

1. **`call_pure()` 送的是"取值表"，而 `EXTRACT_JS` 要的是"实参表"**：JS 侧是
   `for (const args of cases) { name + "(" + args.map(…).join(", ") + ")" }`，每个 case 必须是**参数数组**；
   而 V1/V2/V3/V5/V6 传的是 `[{}, None, "nope", [], 0]` 这种**裸取值** ——
   `{}.map` / `null.map` 直接抛 `TypeError`（`args.map is not a function`，抛在 `try` 之外），
   于是这 5 条**永远**只会得到「node 执行失败」，与实现无关。
   修法：`call_pure()` 里 `cases = [[c] for c in cases]`（或调用点写成实参表）。
2. **V1 的断言消息是非法格式串**：`"不可用时不许产出「3D 覆盖率 …%」这句：%r" % value` ——
   中文引号紧跟在 `%` 后面，Python 会在构造消息时抛
   `ValueError: unsupported format character '?' (0x300d)`（**先于**断言求值）。
   修法：把那个 `%` 写成 `%%`。

**证据（未改仓库、只在 `/tmp` 放了一份临时副本）**：把 `call_pure()` 按第 1 条包一层后，
本批实现下 **7/8 绿**，唯一剩下的就是第 2 条 V1 的 `ValueError`；未改的仓库里这 5 条仍是
`node 执行失败`。也就是说：**实现侧已经按 Spec 全部落地**，这 5 条要转绿只需测试侧改这 2 行。

复跑命令与结果：

```
node --check tech_app/frontend/app.js            # 通过

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_solid_coverage_red tests.test_packaging_solids_parts_version_binding_red
Ran 29 tests ... OK

./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_solids_body_unusable_red -v
V1 FAIL / V2 FAIL / V3 FAIL / V4 ok / V5 FAIL / V6 FAIL / V7 ok / V8 ok
  → 红基同样的 8 条也是 7 红 1 绿（V7），所以本批**没有把任何一条已绿的改红**；
    剩下 5 条的红=§7.1 的两处测试侧缺陷，不是实现缺口。
```
