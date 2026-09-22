# 规格：`preconditions()` 里"读不到需求单"不许说成"需求单不存在"

状态：Spec + 红测（已实现）（原状：`packaging_drawing_flow/__init__.py:474-482` 把 `store.load_requirement()`
的异常吞成 `requirement = None`，于是"存储通道读不到"与"这个项目真的没有需求单"给出同一条
`REQUIREMENT_DRAFT_MISSING`（`severity="blocking"`、动作是"先去建一张草稿"））
红测：`tests/test_packaging_preconditions_requirement_read_failure_red.py`

血缘：承接 `drawing-flow-error-taxonomy.md` §3（`preconditions(project_id) ->
[{"code","severity","message","action"}]` 的唯一口径；本批**只加一条码与一个披露键**，
既有四键与既有两条码逐字不变）、
`packaging-silent-degradation-disclosure.md`（同一条纪律：失败 / 空 / 没有不许同形）、
`packaging-parts-selectable-panel.md`（2.1 空态文案就是把这些 precondition 逐条渲染成
`[code] message → action` —— 文案错在服务端就是错的）、
`packaging-stage-chain-read-failure-disclosure.md`（同一病症在链条侧）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

需求单读不到时，用户看到的必须能区分：**"这个项目真的没有需求单（去建一张）"** 与
**"存储通道暂时读不到（去重试）"**。前者是用户可以自己解决的前置条件，后者让用户去建草稿
只会建出一张重复的需求单，而 2.1 左栏的"零件为 0"那条空态文案还会一直挂着这条错误指引。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_drawing_flow/__init__.py`：

```python
474     try:
475         requirement = store.load_requirement(str(project_id))
476     except Exception:                                   # noqa: BLE001 - 读不到就按缺前置条件报
477         requirement = None
478     if not requirement:
479         spec = model.PRECONDITION_BLOCKERS["REQUIREMENT_DRAFT_MISSING"]
480         items.append({"code": "REQUIREMENT_DRAFT_MISSING", "severity": "blocking",
481                       "message": str(spec["message"]), "action": str(spec["action"])})
482         return items
```

`model.PRECONDITION_BLOCKERS["REQUIREMENT_DRAFT_MISSING"]`（`model.py:41-44`）：

```
message: "需求单不存在，请先创建需求草稿（缺前置条件，重试不会成功）"
action:  "先在需求看板为这个项目建一张需求草稿，再回到图纸解析重跑"
```

两处后果：

1. **读失败被写成"需求单不存在"**：`store` 通道异常（元数据后端不可用、锁超时、临时故障）
   时，接口给的是上面这句**断言**：需求单"不存在"、而且"重试不会成功"。真相是"这一次读不到"
   —— 用户被劝去建一张**重复**的需求草稿，而那条 `blocking` 前置条件会一直挂在
   `GET /api/projects/{pid}/drawing-flow` 上（`main.py:7399`）。
2. **2.1 左栏跟着说错**：`packagingPartsEmptyText()`（`app.js:1720-1745`）把每条 precondition
   渲染成 `[code] message → action`。零件一份都出不来时，用户看到的第一句话就是
   "`[REQUIREMENT_DRAFT_MISSING]` 需求单不存在，请先创建需求草稿（缺前置条件，重试不会成功）"
   —— 而这一趟的真实卡点可能是存储通道，与需求单无关。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_drawing_flow/__init__.py:preconditions()`
   - `store.load_requirement()` **抛异常**时，返回一条**新码**的前置条件：
     `{"code": "REQUIREMENT_UNREADABLE", "severity": "blocking",
       "message": "暂时读不到这个项目的需求单（<异常类名>），请稍后重试；这不代表需求单不存在",
       "action": "稍后重试；若持续失败请让管理员检查存储通道",
       "unavailable": {"code": "requirement_unreadable", "reason": "<异常类名>"}}`；
     **不许**再给 `REQUIREMENT_DRAFT_MISSING`；
   - 读到但**为空**（这个项目真的没有需求单）时，`REQUIREMENT_DRAFT_MISSING` 的
     `code` / `severity` / `message` / `action` **逐字不变**；
   - 读到且状态不可编辑时，`REQUIREMENT_NOT_EDITABLE` 照旧**逐字不变**；
   - 返回的**每一条**前置条件都新增必存在键 `unavailable`：非读失败的两条给 `{}`，
     读失败那条给上面的形状；
   - `project_id` 为空 → 照旧返回 `[]`；读异常照旧**不抛**给调用方（接口照旧 200）。
2. 前端**不改**：`app.js` 照旧按 `[code] message → action` 原样渲染（本批只保证服务端给的
   这三样是对的）。

## 3. 禁止事项

- 不许改 `preconditions()` 的返回形状（仍是 `list`，每条仍带既有四键），不许新增 / 改名路由。
- 不许改 `model.PRECONDITION_BLOCKERS` 里两条既有文案一个字。
- 不许把"读异常"改成一整条链路失败（照旧返回一份可判的前置条件清单）。
- 不许把 `REQUIREMENT_UNREADABLE` 也说成"不存在 / 重试不会成功"：这两句是给"真的没有"的。
- 不许改 `packaging_semantics/provenance.py` 的 `REQUIREMENT_DRAFT_MISSING` 稳定码
  （那是字段写入步的码，本批不碰）。
- 不许改 `tests/` 下任何既有文件（含 `test_drawing_flow_error_taxonomy_red.py` 的 B 组三条、
  `test_drawing_flow_requirement_state_red.py`）；本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （打桩 `store.load_requirement`）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_preconditions_requirement_read_failure_red -v
# Q 组（9 条）：
#   Q1 读需求单抛异常 → 新码 REQUIREMENT_UNREADABLE（不许再给 REQUIREMENT_DRAFT_MISSING）（红）
#   Q2 读异常时的文案红线：message 说得出异常类名与"重试"，且**不含**"不存在"（红）
#   Q3 每条前置条件都必须带 unavailable 键（真的没有 / 不可编辑两条给 {}）（红）
#   Q4 读到但为空 → REQUIREMENT_DRAFT_MISSING 四条键逐字不变（护栏）
#   Q5 读到且可编辑 → 给 []（护栏）
#   Q6 读到且不可编辑 → REQUIREMENT_NOT_EDITABLE 照旧（护栏）
#   Q7 project_id 为空 → 给 []（护栏）
#   Q8 读异常时不抛给调用方，仍返回 list（护栏）
#   Q9 读接口照旧带 preconditions（源码守卫）（护栏）
# 现状：Q1 Q2 Q3 红（3 条），Q4 Q5 Q6 Q7 Q8 Q9 绿（6 条护栏）
# 不回归（前置条件与错误分类的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_requirement_state_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
```

真机复验（实现方做完、且部署后）：

```
GET /api/projects/{pid}/drawing-flow
# 存储通道正常、没有需求单：{"preconditions": [{"code": "REQUIREMENT_DRAFT_MISSING",
#                                              "unavailable": {}, ...}]}
# 存储通道异常：{"preconditions": [{"code": "REQUIREMENT_UNREADABLE",
#                                   "message": "暂时读不到这个项目的需求单（…），请稍后重试；"
#                                              "这不代表需求单不存在",
#                                   "unavailable": {"code": "requirement_unreadable", ...}}]}
```

## 5. 落地状态

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_preconditions_requirement_read_failure_red
# Ran 9 tests ... OK（Q1 / Q2 / Q3 由红转绿；Q4–Q9 六条护栏仍绿）
```

`preconditions()` 现在给"这一次读不到需求单"一条**新码** `REQUIREMENT_UNREADABLE`，与"这个项目
真的没有需求单"的 `REQUIREMENT_DRAFT_MISSING` 分家；返回的每一条都带 `unavailable`（读失败那条
给 `{"code": "requirement_unreadable", "reason": "<异常类名>"}`，另两条给 `{}`）。

### 5.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 新码 | `tech_app/backend/services/packaging_drawing_flow/__init__.py`：模块级 `REQUIREMENT_UNREADABLE = "REQUIREMENT_UNREADABLE"` |
| §2.1 读挂分支 | `preconditions()` 的 `except Exception as exc`：`code=REQUIREMENT_UNREADABLE`、`severity="blocking"`、`message` 带异常类名 + "重试"、`action="稍后重试；若持续失败请让管理员检查存储通道"`、`unavailable={"code": "requirement_unreadable", "reason": reason}`；**不再**给 `REQUIREMENT_DRAFT_MISSING`，也不抛给调用方（接口照旧 200） |
| §2.1 真的没有 | 读到但为空：`REQUIREMENT_DRAFT_MISSING` 的 `code` / `severity` / `message` / `action` 逐字不变，只多一个 `unavailable: {}` |
| §2.1 不可编辑 | `REQUIREMENT_NOT_EDITABLE` 的 `message` / `action` 逐字不变，只多一个 `unavailable: {}` |
| §2.1 空项目号 | 照旧 `[]`（不读存储） |
| §2.2 前端 | 未改（`app.js` 照旧按 `[code] message → action` 渲染） |
| §3 不许改的 | `model.PRECONDITION_BLOCKERS` 两条文案、`packaging_semantics/provenance.py` 的稳定码、`main.py` 路由形状（Q9 源码守卫）逐字未动 |

### 5.2 复跑（不回归）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_drawing_flow_error_taxonomy_red \
    tests.test_drawing_flow_requirement_state_red tests.test_packaging_drawing_flow_red
# Ran 85 tests ... OK (skipped=1)
```

### 5.3 已记录的偏差（Spec 与红测的一处矛盾，**以红测为准**，未改测试）

- Spec §2.1 给的示例文案结尾是"这不代表需求单**不存在**"，而红测 Q2 明确断言
  `message` **不得**包含"不存在"（只为真的没有需求单那条码保留这两个字）。二者不可同时满足，
  本批按红测落地为：`"暂时读不到这个项目的需求单（<异常类名>），请稍后重试；这不代表该需求单缺失，请勿据此新建需求草稿"`
  —— 保留"不是缺失、别去建重复草稿"的原意，去掉"不存在"这三个字。除这句文案外，
  §2.1 的字面要求（码 / severity / action / unavailable 形状）逐条照做。
- 只披露不修复：不自动重试、不建草稿、不改需求状态；未 push / 未建 MR / 未 tag / 未部署 /
  未连库 / 未写生产数据。
