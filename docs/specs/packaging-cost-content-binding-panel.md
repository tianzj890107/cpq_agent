# 规格：成本面板上必须看得见「包材绑定数据源缺失」——否则「没有阻断缺口」会被当成「没有缺口」

依赖：`docs/specs/packaging-cost-content-binding-source-disclosure.md` §2.2 / §2.3 / §2.4
（后端已把 `content_binding` 透出到读接口与结果体，并把那句
`包材绑定数据源缺失：N 条包材缺口只披露不阻断` 放进 `readiness.reasons`；该 Spec §6 的
「已记录的边界」明写**界面落点另提**）、`docs/specs/packaging-cost-readiness-panel.md`
（裁决条与缺口分层的界面落点）、`docs/specs/packaging-silent-degradation-disclosure.md`
（"失败 / 空 / 没有"三者不许同形 —— 这条同样适用于"我不知道这一单用了哪几项包材"）。

状态：Spec + 红测（已实现）（原状：后端每一份成本返回体（`compute_project()` 结果体与读侧
`_rehydrate()`）都挂着 `content_binding` —— `{"source": "none", "bound_total": 0,
"unbound_total": N, "bound_codes": [...], "unbound_codes": ["PKG-CT-DIVIDER", …]}`，
就绪门也带 `content_binding_source`（`packaging_cost.py:2031`），
而 `tech_app/frontend/requirement-confirm.js` 里 `content_binding` / `unbound_codes` /
`bound_total` **全部 0 处引用**（`grep -c` 实测）：
① 页面上只看到 `bound_gaps = []` 的样子 —— 「这一单确实一项都不缺」与「我们认不出这一单
用了哪几项包材，所以包材缺口只披露不阻断」长得**一模一样**；
② §2.4 要求"指名道姓"列出的 `unbound_codes` 在界面上不存在，看到缺口的人点不到具体包材项；
③ `source` 这一栏（唯一的可信度信号）在页面上没有落点，用户没有依据判断"这些包材缺口
为什么没进阻断"）
红测：`tests/test_packaging_cost_content_binding_panel_red.py`
行号基线：HEAD `ab949b7`

## 0. 一句话目标

成本面板上多一条「包材绑定数据源」：**说明白**这份成本是拿权威绑定算的，还是
"认不出这一单用了哪几项包材、包材缺口只披露不阻断"，并把被降级披露的包材项**点名列出来**。

## 1. 现状缺口（代码级）

1. `pcPanel()`（`requirement-confirm.js:1238`）读了 `cost.built` / `cost.gaps` /
   `cost.readiness`（上一批），**没有**读 `cost.content_binding`；
2. `content_binding.unbound_codes`（后端按内容码去重升序、指名道姓）在页面上 0 处；
3. `content_binding.source == "none"` 与 `"authoritative"` 在页面上同形 ——
   "我不知道" 被显示成 "没有缺口"。

## 2. 契约

### C1 三个纯函数（`requirement-confirm.js` 的包装成本面板 IIFE 内；体内无 DOM / `fetch(` / `storage`）

- `pcContentBinding(cost)` → `{source, state, headline, boundTotal, unboundTotal, unboundCodes}`：
  - `source`：`String(cost.content_binding.source)` trim 后落在闭集 `"none"` / `"authoritative"`
    才认，其余（缺失 / 空 / 别的字符串，含大小写不同）→ `""`（**不假装**权威，也不假装缺失）；
  - `state`：`source` 原样，空则 `"unknown"`（所以闭集是 `none` / `authoritative` / `unknown`）；
  - `headline`（逐字，三态）：
    - `none` → `包材绑定数据源缺失：认不出这一单用了哪几项包材，包材缺口（content_formula_error）只披露、不进阻断。`
    - `authoritative` → `包材绑定数据源：权威来源，这一单用哪几项包材是从权威数据里读的。`
    - `unknown` → `后端没给包材绑定来源（content_binding.source），「包材没有缺口」这句话不成立。`
  - `boundTotal` / `unboundTotal`：逐项 `Number(...)`，非有限数或负数 → `0`（**不许** `null` / `undefined`）；
  - `unboundCodes`：`content_binding.unbound_codes` 逐项 trim、丢空、保序（**不许**改写 / 截断 / 加序号）；
    非数组 → `[]`；
  - `cost` 不是对象 / `content_binding` 不是对象 → `state: "unknown"` + 全 `0` + `[]`，**不抛错**。
- `pcContentBindingBlock(cost)` → 这一段 HTML（**落点就是这里**，`pcPanel()` 只负责把它拼进模板）：
  `<div class="pc-hint" data-pc-content-binding="<state>">` + `headline` + ` · 绑定 N · 未绑 M`
  （N/M 取自 `pcContentBinding()` 的 `boundTotal` / `unboundTotal`）+ `</div>`，
  再在**有码时**接一节 `<div class="pc-hint" data-pc-content-binding-codes="<条数>">` +
  `pcContentBindingCodesText(cost)` + `</div>`（没有码时**这一节整个不存在**）；
  所有插值过 `pcEsc()`；非对象输入也必须返回**完整的**那条（`state: "unknown"`），**不抛错**。
- `pcContentBindingCodesText(cost)` → `""` 或一句：
  - 没有任何码（非对象 / 没有 `content_binding` / `unbound_codes` 为空或全是空白）→ `""`
   （没话说就别说，**不许**留「被降级披露的包材项：」这种空壳）；
  - 否则 `被降级披露的包材项：` + 码用「、」连（同 `pcContentBinding()` 的取码口径）。

### C2 面板渲染（`pcPanel()`）

- `pcPanel()` 里新增 `${pcContentBindingBlock(cost)}`，插在 `${readinessBlock}` 之后、
  `${head}` 之前（`pcContentBindingBlock()` 体内出现 `data-pc-content-binding=` 与
  `data-pc-content-binding-codes=`，**面板体里只出现这一处调用**）；
- **没测算过（`built=false`）也要显示**：读接口在没测算时同样带 `content_binding`，
  隐藏它就等于把"不知道"当成"没有"；
- 既有 banner（过期 / BOM 读不到 / 路线版本读不到 / 规则快照 / 回传漂移）、上一批的裁决条
  （`data-pc-readiness`）与 `pcAuditBlock()` **一字不动**。

### C3 冻结面

- 这一条**无条件渲染**（`built=false` 时读接口同样带 `content_binding`，不许按 `built` 隐藏，
  也不许按 `gaps` 是否为空隐藏）；
- `source` **只有一个来源**：后端 `content_binding.source`（前端不许按 `unbound_codes` 是否为空
  反推来源，也不许用 `readiness.content_binding_source` 之外的推断）；
- 不改后端、不改 `bound_content_codes_detail()` / `compute_project()` / `_content_binding_of()` /
  `packaging_cost_readiness_gate()` 的任何字段与取值；
- 不加接口、不加依赖；`pcPanel()` 里不新增请求；不改 `index.html`；不改 `tests/` 下任何文件。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：C1 三个纯函数 + `pcPanel()` 的一处调用；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许在 `source == "none"` 时说「包材没有缺口」/「包材缺口已清零」这类与其冲突的话；
- 不许把 `unknown` 归到 `none` 或 `authoritative` 任一一档（说不出来就说"后端没给"）；
- 不许只给数目不给名字（§2.4：报告要能指名道姓）；
- 不许把 `unbound_codes` 拿去当缺口明细渲染（它是**被降级披露**的包材项，不是缺口行）；
- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不连 PG / 34、不写生产数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_content_binding_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_content_binding_source_disclosure_red \
  tests.test_packaging_cost_readiness_panel_red \
  tests.test_packaging_cost_readiness_severity_layering_red \
  tests.test_packaging_cost_gaps_scoped_to_order_contents_red \
  tests.test_packaging_cost_engine_red tests.test_packaging_cost_gaps_red \
  tests.test_packaging_cost_red_closure_red tests.test_packaging_bom_disclosure_panel_red
```

## 6. 已记录的边界

1. 只**显示**来源与点名清单，不做绑定（接权威数据源是以后的事：后端今天老实报 `"none"`）；
2. 读侧的 `unbound_*` 仍是 0（`gaps_unbound_to_order` 还没有落库列，
   `packaging-cost-content-binding-source-disclosure.md` §6 已记录）—— 本批保证的是
   **来源**与**本次能点到的名字**在页面上可见，读侧 `unbound_*` 可回放仍需要另一批（加列）；
3. 不上 `bound_codes` / `bound_total` 之外的明细（绑上的项没有金额可展示）；
4. 缺口条目仍由上一批的阻断 / 提示两组承担，本批**不**把 `unbound_codes` 混进那两组；
5. 其它行业的成本面板仍只在包装需求单上挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

**实现前红基**（`git stash push -- tech_app/frontend/requirement-confirm.js` 后的原文）：

```
Ran 22 tests ... FAILED (failures=18, errors=1)
```

19 红 / 3 绿 —— 绿的三条是守卫（D4 既有 banner 与上一批裁决条一字不动、E3 `index.html`
未动、E4 `node --check` 通过）；19 红 = A1–A6、B1–B3、C1–C5（三个纯函数根本不存在）
+ D1、D2、D4 之外的接线（D1/D2 找不到调用、D3 的插入位置断言拿不到锚点 → ERROR）
+ E1、E2。

**实现后**：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_content_binding_panel_red -v
Ran 22 tests ... OK
node --check tech_app/frontend/requirement-confirm.js   # 退出码 0
```

落点（只改了 `tech_app/frontend/requirement-confirm.js` 一个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 | `pcContentBinding()` / `pcContentBindingCodesText()` / `pcContentBindingBlock()`（包装成本面板 IIFE 内，`pcCategoryRows()` 之前） | 三个纯函数，体内无 DOM / `fetch(` / `storage`；`source` 闭集只有 `none` / `authoritative`（其余含大小写不同 → `""` → `state: "unknown"`，不假装权威也不假装缺失）；`boundTotal` / `unboundTotal` 走 `Number()` + 非有限 / 负数 → `0`；`unboundCodes` 逐项 trim、丢空、保序；渲这一段 HTML 的落点**就是** `pcContentBindingBlock()`（`data-pc-content-binding` / `data-pc-content-binding-codes` 都在它体内），插值全部过 `pcEsc()` |
| §C2 | `pcPanel()`：`${readinessBlock}` 之后、`${head}` 之前插入 `${pcContentBindingBlock(cost)}` | 只**一处**调用、**无条件**渲染（不看 `built` / `gaps`）；有码时多一节点名清单（`data-pc-content-binding-codes="<条数>"`），没码时这一节整个不存在；既有 banner、上一批裁决条与 `pcAuditBlock()` 一字未动 |
| §C3 | 无 | `source` 只读 `cost.content_binding.source`（`pcContentBinding()` 体内出现 `content_binding`，**不出现** `readiness` / `gaps`）；未改后端、未加接口 / 依赖、`pcPanel()` 里无 `fetch(`、未改 `index.html` |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_content_binding_source_disclosure_red \
  tests.test_packaging_cost_readiness_panel_red \
  tests.test_packaging_cost_readiness_severity_layering_red \
  tests.test_packaging_cost_gaps_scoped_to_order_contents_red \
  tests.test_packaging_cost_engine_red tests.test_packaging_cost_gaps_red \
  tests.test_packaging_cost_red_closure_red tests.test_packaging_bom_disclosure_panel_red
Ran 183 tests ... OK
```

边界（与 §6 一致，实现如约未越）：读侧 `unbound_*` 仍是 0（`gaps_unbound_to_order` 没有
落库列，`packaging-cost-content-binding-source-disclosure.md` §6 已记录），所以页面上今天
能看见的是**来源**（`none` / 未知档）与本次能点到的名字；`unbound_codes` 不进阻断 / 提示两组
缺口；接权威数据源、读侧缺口回放都仍是以后的事。
