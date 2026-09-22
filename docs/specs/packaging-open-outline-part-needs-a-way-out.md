# 轮廓未闭合的件必须有一条出路：现在它是死路，而且文案不告诉人能做什么（第 11 层）

血缘：`packaging-parts-outline-and-projection.md`（轮廓判定与开线原因闭集）、
`packaging-parts-downstream-process-and-cost.md` §3（`processability()` 与 409 出口）、
`packaging-parts-in-card-and-material-fill.md` §2.3（缺材料/缺料厚的件级出路范式）、
`packaging-parts-thickness-facts.md` §2.5（件级「补料厚」范式）。

状态：Spec + 红测（已实现）（本层只补**出路与文案**，不改几何判据；红基见 §6，落地见 §7）
红测：`tests/test_packaging_open_outline_part_needs_a_way_out_red.py`
本批 changelog 条目号：`## 339`。

## 0. 一句话目标

64 件里那 4 件未闭合的件（34 实测 `a42e5e60a720`：`closed 60/64`、
`unprocessable_reason_mix.PACKAGING_PART_NOT_CLOSED = 4`，代表件 `DWG-P01` / `DWG-P02`）
在**整条链上是死路**：缺材料/缺料厚的 51 件都有件级出路（「补材料」/「补料厚」），
唯独这 4 件既没有入口、也没有一句话告诉用户该干什么 ——
文案只说"不能拿包围盒尺寸去排工艺"，用户既不知道该改图、还是该重跑、还是可以签字放行。

## 1. 根因（代码事实，逐条可复现；不是推断）

1. **出口文案不给可执行下一步**：`packaging_parts.processability()`
   （`packaging_parts.py:1985`）的 `PACKAGING_PART_NOT_CLOSED` 分支返回
   `"这一件没有可信的闭合轮廓（%s），不能拿包围盒尺寸去排工艺"` —— 只有现象 + 原因码；
   对比同一个函数里缺材料/料厚那条分支（`packaging_parts.py:2012-2022`）会逐条给
   `"缺材料 → 点这一行「补材料」补上"` / `"缺料厚 → 点这一行「补料厚」补上"`。
2. **没有任何件级轮廓出路**：`main.py` 的零件级写路由只有
   `…/packaging-parts/{part_code}/thickness`（`PACKAGING_PART_THICKNESS_PATH`）、
   `…/material`（`PACKAGING_PART_MATERIAL_PATH`）、`…/process`、`…/cost`、
   `…/solid`（+ 两条 `-lookup`）—— **没有** `…/outline` 一类；
   服务层也没有 `set_manual_outline` / `save_part_outline` / `recompute_outline` 任何同名物
   （`packaging_parts.py` 里与轮廓相关的都是**判定**函数：`outline_diagnosis()` /
   `_open_outline_reason()` / `_rescue_outline()`）。
3. **前端只有"画出来"，没有"点下去"**：`app.js:1115-1127` 有一整张
   `OUTLINE_STATUS_TEXT` / `OUTLINE_OPEN_REASON_TEXT` 文案表（未闭合件画虚线 + 包围盒矩形），
   但零件树里只有 `part-material-fix` / `part-thickness-fix` 两个控件
   （`app.js:2739-2761`），**没有**任何轮廓相关的按钮。
4. **"重跑一次"也不会变**：未闭合原因由几何决定且**确定性** ——
   `_open_outline_reason()`（`packaging_parts.py:1250`）按 `no_curve_entity` →
   `loop_budget_exhausted` → `odd_endpoints` → `loop_too_small` 的顺序判，
   `loop_budget_exhausted` 来自"折叠边的搜索额度"（`_outline_evidence()` 的
   `exhausted_collapsed` / `budget_exhausted`）而不是墙钟；`outline_diagnosis()` 的 docstring
   明确要求"零件文档里的逐件诊断必须两次跑逐字相同"。同一份 IR 重抽 = 同一结论。
5. **唯一的"重抽"接口前端也没人调**：`PACKAGING_PARTS_EXTRACT_PATH`
   （`…/packaging-parts/extract`，`main.py:7097`）在全仓前端**0 命中**
   （`grep -rn 'packaging-parts/extract' tech_app/frontend *.html` 无结果）——
   它是八步解析内部用的，不是给用户按的。
6. **闭环后果**：`DWG-P01` / `DWG-P02` 这类件在任何页面上都无法推进到工艺 / 成本：
   入口是 409（`main.py:7969` 的 `packaging_part_process()` → `_packaging_part_reject()`），
   卡片第 6 步的「不可算原因」也只是那句不带动作的话。用户被卡住却拿不到下一步。

## 2. 口径（逐条，可直接验收）

1. **未闭合件的出口必须给可执行下一步**：`processability()` 对
   `PACKAGING_PART_NOT_CLOSED` 返回的 `message` 必须包含**动作指引**（像缺材料/缺料厚那样
   点名"点哪儿做什么"），不许只有"不能拿包围盒尺寸去排工艺"。
2. **文案必须按"可重试"与"要人处理"分家**（原因码来自既有闭集，不新增码）：
   - `loop_budget_exhausted`（这一件**没算完**）→ 指向"这一件重算轮廓"这个动作；
   - `odd_endpoints` / `no_closed_loop` / `loop_too_small` / `no_curve_entity`
     / `unit_unconfirmed`（图纸**真的**没有闭合轮廓 / 单位没定）→ 指向"改图重传"或
     "人工按包围盒**签字确认**"这两个动作之一。
3. **必须存在一条件级人工出路**（与材料/料厚同范式，二选一或都给）：
   （a）`POST …/packaging-parts/{part_code}/outline/confirm` —— 人工签字"这一件按包围盒估算"，
   留痕（人 / 理由 / 时间）并让这一件可算；
   （b）`POST …/packaging-parts/{part_code}/outline/recompute` —— 带更大搜索额度的单件重算，
   落一版新结论（额度用完仍 `loop_budget_exhausted`，诚实照旧）。
4. **`closed` 仍然只由几何判定给出**：人工出路**绝不**允许把 `outline_status` 写成 `"closed"`；
   签字确认必须走一个**新的、可分辨的**状态或标记（例如 `outline_status` 保持 `open` +
   `outline_confirmation.kind` 一类留痕），卡片上要能看出"这是人签的字，不是几何给的"。
5. **判据与闭集不变**：`OUTLINE_OPEN_REASONS` 闭集、`_open_outline_reason()` 的判定顺序、
   `_packaging_part_reject()` 的 409 形状（`code` / `message` / `missing_variables` /
   `retryable`）逐字保持；本层只补出路与文案，不改几何算法。
6. **不许连坐**：人工出路只影响这一件；其它未闭合件仍照旧 409，
   摘要里 `unprocessable_reason_mix.PACKAGING_PART_NOT_CLOSED` 只对这一件 −1。

## 3. 允许修改范围

- `tech_app/backend/services/packaging_parts.py`：加"这一条为什么卡着 + 下一步做什么"的
  纯函数（文案表按 `OUTLINE_OPEN_REASONS` 分家）+（若走 (a)/(b)）件级签字或重算的落库函数；
- `tech_app/backend/main.py`：注册件级轮廓路由（写 `{project_id}` / 读 `{pid}`，沿用既有约定）、
  写权限直接引用既有 `packaging_match.BOX_MATCH_DECIDE_ROLES`、按 `_packaging_part_reject()` 的形状 409；
- `tech_app/frontend/app.js`：零件树里给未闭合件加可点控件（`part-outline-fix` 一类），
  与「补材料」/「补料厚」同一渲染循环。
- 禁止：改 `_open_outline_reason()` / `outline_diagnosis()` / `_rescue_outline()` 的判定；
  新增 `OUTLINE_OPEN_REASONS` 之外的码；把 `outline_status` 置成 `"closed"`；
  改卡片 10 列定义；改 409 的 `code` / `retryable` 语义。

## 4. 红测分组（`tests/test_packaging_open_outline_part_needs_a_way_out_red.py`）

- **A 组 出路（今天都是红的）**
  - A1（红）`processability()` 对未闭合件返回的 `message` 必须含**动作指引**
    （点哪儿 / 做什么），且 `loop_budget_exhausted` 与 `odd_endpoints` 两种原因给的指引
    **必须不同**（一个指向重算、一个指向改图或签字）；
  - A2（红）必须存在**件级轮廓出路**：`main.py` 里出现 `…/packaging-parts/{part_code}/outline`
    一类的写路由常量 + 处理器，且 `packaging_parts.py` 里有对应的落库函数；
  - A3（红）件级轮廓出口的**路由 + 落库函数**都在（`main.py` 的常量/处理器 + `packaging_parts.py`
    的落库或签字函数）；
  - A4（红）前端零件树必须给未闭合件一个可点控件（`part-outline-fix` 一类），
    与 `part-material-fix` / `part-thickness-fix` 同一渲染循环。
- **B 组 护栏（今天就是绿的，不许被改红）**
  - B1 `OUTLINE_OPEN_REASONS` 闭集逐字不变、`_open_outline_reason()` 的判定顺序不变；
  - B2 `processability()` 对未闭合件仍 `ok=False`、`missing_variables == ["outline"]`、
    `code == "PACKAGING_PART_NOT_CLOSED"`；
  - B3 没有任何地方把 `outline_status` 写成 `"closed"`（`closed` 只由几何判定给出）；
  - B4 卡片 `CARD_COLUMNS` 仍 10 列、`card_row()` 的可算性仍只取自 `processability()`。

## 5. 验收

1. 34 上对 `a42e5e60a720` 的 `DWG-P01`（未闭合）点这一件的出路控件；
2. `loop_budget_exhausted` 的件 → 重算后要么闭合、要么仍诚实报 `loop_budget_exhausted`；
   `odd_endpoints` 的件 → 签字确认后这一件可算，卡片上能看出"人工签字"而不是"几何闭合"；
3. 这一件的「工艺推荐」/「成本测算」→ `200 succeeded`；
4. 其它 3 件仍照旧 409，摘要里 `PACKAGING_PART_NOT_CLOSED` 只减 1。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_open_outline_part_needs_a_way_out_red \
  tests.test_packaging_part_manual_fill_persists_red -v
```

## 6. 红基（2026-09-22 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_open_outline_part_needs_a_way_out_red
  → Ran 8 tests … FAILED (failures=4, errors=0)
```

红的 4 条 = A1（未闭合件的出口文案没有动作指引）、A2（`loop_budget_exhausted` 与 `odd_endpoints`
给的还是同一句话，除了原因码一字不差）、A3（没有任何件级轮廓写路由与落库函数）、
A4（前端零件树没有轮廓控件）；
绿的 4 条护栏 = B1（`OUTLINE_OPEN_REASONS` 与判定顺序不变）、
B2（未闭合仍 409 `PACKAGING_PART_NOT_CLOSED` + `missing_variables=["outline"]`）、
B3（没人把 `outline_status` 写死成 `"closed"`）、B4（卡片 10 列与 `card_row()` 取数不变）。

## 7 落地状态（2026-09-22，实现方 Codex）

实跑：`./open-claude/.venv/bin/python -m unittest tests.test_packaging_open_outline_part_needs_a_way_out_red`
→ **Ran 8 tests … OK**（A1–A4 四条红转绿，B1–B4 四条护栏仍绿）。

落地方案：**两条出路都做了**（Spec §2.3 的 (a) 与 (b)），并且文案按 §2.2 分家。

- `tech_app/backend/services/packaging_parts.py`
  - `outline_advice(reason)`：未闭合件的「为什么卡着 + 下一步做什么」纯函数。按
    `OUTLINE_RETRYABLE_REASONS` / `OUTLINE_MANUAL_REASONS` 分家（闭集不扩）：
    `loop_budget_exhausted` → 指向「重算轮廓」；其余 → 指向「改图重传 / 按包围盒签字确认」。
  - `processability()` 的 `PACKAGING_PART_NOT_CLOSED` 分支改成
    `…不能拿包围盒尺寸去排工艺 —— <动作指引>`；**人签过字**（`outline_confirmation`，
    且签名人非空）的件放行 —— 放行的是人，几何状态仍是 `open`。
  - `set_manual_outline()` / `save_part_outline()` / `load_part_outline()`：人工签字
    「这一件按包围盒估算」，写侧档 `packaging_part_outline`（`kind=manual_bbox`），
    留痕 `bound_by` / `reason` / `confirmed_at` / 当时那条 `outline_reason`；
    匿名（`bound_by` 为空）→ `ValueError`，绝不放行。
  - `recompute_outline(row, ir, *, scale)` / `save_part_outline_recompute()`：**单件**重算，
    只对这一件的分量按 `_outline_evidence(..., max_states=MAX_LOOP_STATES × scale)` 再跑
    一次找环；判据、`OUTLINE_OPEN_REASONS` 闭集与 `_open_outline_reason()` 的次序一律沿用。
    真闭合了才改几何结论（`set_recomputed_outline()` 里由**这次几何判定**给出 `closed`）；
    没算出来就诚实照旧（仍是 `open` + 原原因，另留 `outline_recompute` 痕）。额度夹在
    `[1, RECOMPUTE_BUDGET_SCALE_MAX]`。
  - `_manual_fill_overlay()` 现在把**三份**侧档（材料 / 料厚 / 轮廓出路）按 `part_code`
    合回零件行；顺手修掉上一版 overlay 里"同一件只取第一个键"的漏合并（材料与料厚以前
    三选一，现在能同时合）。`card_row()` 的「轮廓状态」列对人工签字的件带上
    `（人工签字·按包围盒估算）`，卡片上一眼能分出"人放的"与"几何闭合的"。
- `tech_app/backend/main.py`：新增两条写路由 + 一条读路由（写权限直接引用
  `packaging_match.BOX_MATCH_DECIDE_ROLES`；匿名/无效 → 400；IR 缺失 → 409）：
  `GET/POST /api/projects/{pid}/requirement/packaging-parts/{part_code}/outline/confirm`、
  `POST …/outline/recompute`；审计动作 `workflow:packaging_part_outline_confirmed` /
  `workflow:packaging_part_outline_recomputed`。新增 `_now_iso()`（UTC 秒级时间戳）。
- `tech_app/frontend/app.js`：零件树里未闭合件多出与「补材料」/「补料厚」同渲染循环的
  `part-outline-fix` 控件（按 `outline_reason` 决定走重算还是签字确认，与后端两条出口一一对应）；
  已签字的件显示 `part-outline-signoff`（人工签字·按包围盒）；面板状态文案（`pkgPartStatusText()`）
  同时说清"人工签字"与"已重算（额度 ×N，仍/已闭合）"。`node --check` 通过。

### 已知边界（不改测试、不影响契约）

1. 重算是**单件**行为：只更新这一行的几何结论，不重算跨件的 `kind_key` / `kind_index`
   指纹（这一件闭合后在"按种类折叠"里仍归在原来那一组）。原因：种类指纹是跨全量件的口径，
   单件重算时重排它会连带改动其它件的 `kind_index`，违反 §2.6「不许连坐」。
2. 本层不动几何判据：`closed` 仍然只由几何判定给出；人工出路只加"人签的字"这一笔留痕，
   `outline_status` 一个字不改（B3 护栏）。
