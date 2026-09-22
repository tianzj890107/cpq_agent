# 规格：图纸入口分发器的能力探测失败不许说成"可用"

状态：Spec + 红测（已实现）（原状：`dispatch_project_drawing_parse()` 在探测抛异常时把 `flow_available` 写成 `True`，而它调的 `detect_converter_availability()` 契约定的是"探测失败按不可用返回"—— 两处口径相反，且"探测挂了"与"确实没有转换器"在读回体上不可分）
红测：`tests/test_packaging_drawing_dispatch_probe_red.py`

血缘：承接 `dwg-file-capability-preflight.md` §3 C1（`detect_converter_availability()` 是能力事实的**唯一来源**：
"探测失败按'没有'返回（`available=False, role="none"`）…… 能力查询失败必须能被上层当作'不可用'处理，
而不是把 500 抛给用户"）、
`packaging-silent-degradation-disclosure.md`（同一条纪律：失败与"没有"必须能分开）、
`dwg-capability-truth`（本环境能力不许由常量说）。

## 0. 一句话目标

用户看到"能一键解析"就该真能解析。今天分发器在**探测失败**时也返回 `flow_available: true` ——
把"不知道"渲染成"可用"，点下去才发现不行（现场那条"一键解析出来就是图纸解析未完成"
的体验正是这一类）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 上层把"探测失败"写成"可用"，与被调方的契约相反

`tech_app/backend/main.py:7355-7362`（`dispatch_project_drawing_parse()`）：

```python
        available = True
        try:
            available = bool(file_preflight.detect_converter_availability().get("available"))
        except Exception:                       # noqa: BLE001 - 探测失败不挡分流
            available = True
        return {"route": "drawing_flow", "suffix": suffix,
                "reason": "DWG/DXF 走图纸解析链路（2.1 一键解析）",
                "flow_available": available}
```

而 `tech_app/backend/services/file_preflight.py:461-462` 写得很清楚：

> 探测失败按"没有"返回（`available=False, role="none"`），绝不抛裸异常：
> 能力查询失败必须能被上层当作"不可用"处理，而不是把 500 抛给用户。

也就是说：**被调方约定"失败 = 不可用"，调用方却在同一条失败上说"可用"**。当前只在
`detect_converter_availability()` 自己把异常吞成 `{}` 时两者才碰巧一致 —— 一旦探测函数本身
出问题（导入失败、`cad_converter.capability()` 抛出、被替换的实现抛错），分发器就把
"探测不到"当成"能解析"。

### 1.2 "探测挂了"与"确实没有转换器"在读回体上分不出来

两者都给 `flow_available: false`（修好上一条之后）却该说不同的话：前者"暂时探测不到，
请稍后重试 / 检查服务"，后者"本环境没有 DWG 转换器，请换格式或先装转换器"。
今天返回体里没有任何键能区分（`grep -rn "flow_available" tech_app/` 只有这一个函数在写）。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/main.py:dispatch_project_drawing_parse()`
   - 探测抛异常时 `flow_available` 必须给 `False`（与 `detect_converter_availability()` 的既有
     契约同口径）；**不许**再给 `True`；
   - 返回体**新增** `probe_unavailable`（键**必须存在**，所有四个分支都带）：
     - 探测抛异常 → `{"code": "converter_probe_unavailable", "reason": "<异常类名>"}`；
     - 探测成功（无论 `available` 是真是假）→ `{}`；
     - 非 DWG/DXF 分支（位图 / 三维 / 其他）→ `{}`；
   - 探测抛异常时 `reason` 要说"暂时探测不到 DWG 转换器，请稍后重试"，
     **不许**说成"本环境没有 DWG 转换器"（那是 `available=False` 的说法）；
   - 既有四条的 `route` / `suffix` / `flow_available` / `reason` 口径逐字不变（探测成功时）；
     后缀判据不受探测结果影响：`.dwg/.dxf` 永远是 `drawing_flow`，不许因为探测失败改判成
     位图 / 三维 / blocked（本批只改**同一分支内的可用性说法**）。
2. 前端（图纸入口 / 上传完成后的按钮态）：`flow_available === false && probe_unavailable` 非空时
   显示"暂时探测不到 DWG 转换器，请稍后重试"；`flow_available === false` 且 `probe_unavailable`
   为空时维持既有文案（本环境不支持 / 需转换器）。

## 3. 禁止事项

- 不许改 `file_preflight.detect_converter_availability()` 的契约与返回形状
  （`available` / `role` / `version` / `source` / `checked_at` 逐字不变）。
- 不许把探测失败改成 500 / 抛错：分发器照旧返回一份可判的分流结论。
- 不许改四条既有路由的后缀映射（`DRAWING_FLOW_SUFFIXES` / `VISION_SUFFIXES` /
  `THREE_D_SUFFIXES`）与它们的 `reason` 文案。
- 不许在读接口里现装转换器 / 现探测后写盘 / 调模型 / 联网（本批只改"怎么说"）。
- 不许改 `tests/` 下任何既有文件（含 `test_dwg_capability_truth_red.py`、
  `test_dwg_file_capability_preflight_red.py`、`test_e2e_packaging_dwg_continuity_red.py`）；
  本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测只打桩探测函数。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_dispatch_probe_red -v
# M 组（5 条）：
#   M1 探测抛异常 → flow_available=false、probe_unavailable.code=converter_probe_unavailable、
#      reason 说"暂时探测不到"（红）
#   M2 探测成功且可用 → flow_available=true、probe_unavailable 给 {}（红：现在没有这个键）
#   M3 探测成功但不可用 → flow_available=false、probe_unavailable 给 {}（红）
#      —— 三种状态必须两两可分
#   M4 位图 / 三维 / 其他后缀 → route/suffix/reason/flow_available 四项逐字不变（护栏）
#   M5 探测失败时 .dwg 仍判 drawing_flow（后缀判据不受探测影响）（护栏）
# 现状：M1 M2 M3 红（3 条），M4 M5 绿（2 条护栏）
# 不回归（能力事实与分发器的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_dwg_capability_truth_red
./open-claude/.venv/bin/python -m unittest tests.test_dwg_file_capability_preflight_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
```

真机复验（实现方做完、且部署后）：

```
GET /api/projects/{pid}/drawing/dispatch?filename=酒盒.dwg
# 正常：{"route": "drawing_flow", "flow_available": true, "probe_unavailable": {}}
# 探测服务异常时：{"route": "drawing_flow", "flow_available": false,
#                 "probe_unavailable": {"code": "converter_probe_unavailable", "reason": "..."}}
```

## 5. 落地状态

（已实现）（`packaging-drawing-dispatch-probe-truthfulness` 9-22 落地。`dispatch_project_drawing_parse()`
在探测抛异常时改报 `flow_available: false` + `probe_unavailable = {"code":
"converter_probe_unavailable", "reason": "<异常类名>"}`，并把 `reason` 说成"暂时探测不到 DWG 转换器，
请稍后重试"；四个分支都带上这个键（正常给 `{}`）；后缀判据一个字没动。M1 / M2 / M3 由红转绿，
M4 / M5 两条护栏仍绿。）

### 5.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 探测失败按不可用 | `tech_app/backend/main.py` `dispatch_project_drawing_parse()`：`except` 分支由 `available = True` 改为 `available = False`（与被调方 `detect_converter_availability()` 的既有契约同口径） |
| §2.1 新增 `probe_unavailable` | 探测抛异常 → `{"code": "converter_probe_unavailable", "reason": "<异常类名>"}`；探测成功（可用 / 不可用）→ `{}`；位图 / 三维 / 其他三个分支 → `{}`（键必须存在） |
| §2.1 文案分家 | 探测失败时 `reason` = 「DWG/DXF 走图纸解析链路（2.1 一键解析），但暂时探测不到 DWG 转换器，请稍后重试」；探测成功时 `reason` 逐字不变 |
| §2.1 后缀判据不动 | `DRAWING_FLOW_SUFFIXES` / `VISION_SUFFIXES` / `THREE_D_SUFFIXES` 与三条既有 `reason` 一个字没改；`.dwg` 在探测失败时仍是 `drawing_flow` |

### 5.2 复跑

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_dispatch_probe_red
# Ran 5 tests ... OK
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_dwg_capability_truth_red \
    tests.test_dwg_file_capability_preflight_red tests.test_packaging_drawing_flow_red \
    tests.test_e2e_packaging_dwg_continuity_red
# Ran 111 tests ... OK (skipped=1)
```

### 5.3 已记录的边界

- §2 第 2 条（前端在 `flow_available === false && probe_unavailable` 非空时显示"暂时探测不到 DWG
  转换器，请稍后重试"）**没有落点**：全仓 `tech_app/frontend/` 里目前没有任何代码消费
  `drawing/dispatch` 的返回体（`grep -rn 'drawing_parse' tech_app/frontend/` 为空；
  分发结果只出现在建项响应 `drawing_parse` 里）。等前端真的接这个字段时按 §2.2 显示即可 ——
  本批不在前端造一个没人读的分支。
- 只改"怎么说"：不装转换器、不重探测写盘、不调模型、不联网；`file_preflight` 契约与返回形状
  （`available` / `role` / `version` / `source` / `checked_at`）逐字未动。
