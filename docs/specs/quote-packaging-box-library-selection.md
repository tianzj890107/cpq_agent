# 规格：包装行业选品接到盒型库

状态：Spec + 红测（已实现）
红测：`tests/test_quote_packaging_box_selection_red.py`

相关规格：
`docs/specs/packaging-box-type-matching.md`（工艺侧盒型五维匹配口径）、
`docs/specs/packaging-requirement-template.md`（包装需求模板 64 字段 / 10 必填）、
`docs/specs/kb-in-pg-http-snapshot.md`（知识库唯一事实源 `cpq_kb`）、
`docs/specs/quote-nonstandard-path.md`（同批：非标判定与只读定制要求）、
`docs/specs/quote-agent-industry-alignment.md`（④产品技术参数按行业换源）。

业务拍板：**包装行业的选品这一轮要真的接到盒型库**。

---

## 1. 现场问题（实测）

现场会话（包装询盘：数码天地盒 100*90*40）在第 1 步匹配出来的 Top3 是
`91000226 / 91000255 / 91000365` 三个**锂亚电池**，「用途/场景契合」只有 30 分。

原因：`cpq_agent_server.py:805` `_handle_match_products()` 不带行业，
直接调 `cpq_match.match()`，而 `cpq_match.py:25` 把 `PRODUCT_TABLE` 写死成
`product_para_value`（电池成品参数表）。`## 191` 只把 ④产品技术参数换了源
（`_PRODUCT_SOURCES['packaging'] = 'kb_packaging_box_type'`），**匹配源没换**。

同时工艺侧早就有一套成熟的盒型匹配，且是确定性纯函数：

- `tech_app/backend/services/packaging_match.py:339` `match_box_types(inputs)`：
  五维打分（尺寸 / 配合间隙 / 面纸克重 / 闭合方式 / V槽），权重与硬门槛**全部读表**
  `kb_packaging_match_weight`，不写死数字、不调模型、不落库；
- 输入键 `MATCH_INPUT_KEYS = ("inner_length", "inner_width", "inner_height",
  "closure_type", "v_groove", "face_paper_gsm", "fit_clearance")`；
- 盒型库 `kb_packaging_box_type` 由工艺侧经 HTTP 快照读取，唯一事实源是 8010 的
  `cpq_kb` schema（`cpq_kb.py`），而**报价侧本来就有这把库的直连**
  （`cpq_db` + `cpq_kb.SCHEMA`）。

结论：报价侧要做的不是"再造一个匹配"，而是**把同一套口径接过来**，并保证两侧
不漂移。

---

## 2. 契约

### 2.1 报价侧新增确定性盒型匹配（可注入数据）

新增 `cpq_packaging_match.py`（仓库根，与 `cpq_match.py` 同层）：

```python
ENGINE_VERSION = "packaging_match_v1"          # 与工艺侧同值
MATCH_INPUT_KEYS = ("inner_length", "inner_width", "inner_height",
                    "closure_type", "v_groove", "face_paper_gsm", "fit_clearance")

#: 盒型库读不到（PG/schema/表缺失）时抛这个；**绝不回落空表** —— 那会把「桥断了」
#: 伪装成「库里没有可用的盒型」。
class QuoteKbUnavailable(RuntimeError): ...

def _load_rows(boxes=None, weights=None) -> tuple[list, list]:
    """boxes / weights 传 None 时读报价侧自己的 PG `cpq_kb`
    （`kb_packaging_box_type` / `kb_packaging_match_weight`，走 `cpq_kb.snapshot()`）；
    显式传入时只用传入的行（离线测试与两侧口径比对走这条）。
    库读不到（`cpq_kb.KbUnavailable` 等）一律转成 `QuoteKbUnavailable` 抛出，
    **不得吞掉异常后返回空候选**。"""

def match_box_types(inputs: dict, boxes=None, weights=None) -> dict:
    """五维匹配候选盒型（确定性纯函数：不调模型、不落库、不改入参）。"""

def load_box_type(box_type_code: str, boxes=None) -> dict:
    """按盒型编码取整行（无此编码如实返回 `{}`，绝不编造）；
    库读不到同样抛 `QuoteKbUnavailable`。"""
```

三个名字（`ENGINE_VERSION` / `MATCH_INPUT_KEYS` / `match_box_types` / `load_box_type`）
与异常类 `QuoteKbUnavailable` 都是红测直接引用的**命名契约**，不得改名。

返回结构与工艺侧逐字对齐：

```python
{"engine_version": str, "dimensions": [{"dimension", "weight", "hard_gate"}, …],
 "inputs_complete": bool, "missing_inputs": [...],
 "candidates": [{... 与工艺侧候选同键 ...}],
 "suggested_box_type": str, "needs_new_tooling": bool, "new_tooling_reason": str}
```

要求：

1. 五维打分的**维度、权重、硬门槛一律来自权重行**，代码里不得出现数字权重常量；
2. 候选排序规则与工艺侧一致（`status` 命中在前、缺输入居中、淘汰最后；
   同档按盒型编码升序）；
3. `suggested_box_type` 取**可确认候选里总分最高**者，同分按编码升序（与工艺侧一致）；
4. 纯函数：不写库、不联网、不改入参（不修改传入的 boxes / weights 行）。

### 2.2 两侧口径必须同源（不可漂移）

同一份 `boxes` + `weights` + `inputs` 下，报价侧与工艺侧
（`tech_app.backend.services.packaging_match.match_box_types`）必须给出**完全相同**的：

- `dimensions`（维度名、权重、硬门槛，顺序一致）；
- 每个候选的 `status`、`can_confirm`、`total_score`、`dimension_scores`；
- `suggested_box_type`、`needs_new_tooling`。

红测会用同一份 fixture 同时驱动两侧做逐字段比对。**允许实现是两份代码，但不允许口径漂移。**

### 2.3 匹配入口按行业分流

`_handle_match_products()`（`cpq_agent_server.py:805`）：

- `industry == "packaging"` → 走盒型库匹配（`cpq_packaging_match.match_box_types`），
  **不再查** `product_para_value`；输入取 `tool_input` 里与 `MATCH_INPUT_KEYS`
  同名的键（包装模板的必填项名与之同名，`fit_clearance` 仍是选填）；
- 包装分支的门禁输入用**整份 `tool_input`**（键名与需求模板一致），
  这样 `step1_missing(req, industry='packaging')` 仍按 10 项必填判定 ——
  不得因为换了匹配源而放宽或绕开必填门禁；缺项时仍返回现有那句
  「❌ 需求缺少必备匹配参数：…」。
- 其余行业（半导体/电池/电器/默认）→ 保持现有 `cpq_match.match()` 行为逐字不变；
- `chat_candidates` 事件在包装下要带盒型库口径：`engine_version`、
  `dimensions`、`inputs_complete`、`missing_inputs`、`suggested_box_type`、
  `needs_new_tooling`、`new_tooling_reason`，以及 `candidates`（盒型候选，
  含编码/名称/状态/总分/五维分/淘汰原因/越界/是否可确认）。
  前端沿用既有候选表格渲染：`products[i]` 用 `code`（=盒型编码）、`name`、
  `total`（= 总分 × 100，保留 1 位小数，与六维界面的 0~100 口径一致）、
  `detail`（五维明细，键=维度、值含 `label`/`score`）、`warnings`
  （淘汰原因 / 越界 / 缺输入的中文说明）；
- 盒型库读不到时：如实报「盒型库读取失败，暂时无法推荐」，**不回落电池表**、
  不返回空候选冒充正常。

### 2.4 「选用」落到包装口径

- 包装下点「选用」写 `s1_products`（盒型编码 / 盒型名称等盒型库口径）与
  `s1_techparams`（`tech_param_row(industry='packaging', product={'box_type_code': …})`
  已支持，返回 20 列盒型参数）；
- ④ 表头与行数据必须**同源**：选用的行键与 `tech_param_columns('packaging')` 的键一致，
  不得再出现「表头是盒型列、行是电池列」而渲染成空的情况；
- 选用的盒型编码必须能被追溯（写进产品行的成品编码位置）。

### 2.5 接不住时给出口

`needs_new_tooling=True`（没有任何可确认候选）时：

- 工具返回文本里必须同时出现两处**固定措辞**：「没有适配」（如实说明盒型库里没有
  适配的盒型）与「转技术工艺」（可执行的出口说明），并带上 `new_tooling_reason`；
- 前端必须处理 `needs_new_tooling`（接不住时给出口，而不是照常画「选用」按钮），
  出口与按钮区同一个：`wfOpenSend(false, 'tech_new_product')`；
- 不得为了让流程继续而伪造盒型编码或尺寸。

---

## 3. 禁止事项

- 不写死任何权重/阈值数字；不改 `tech_app/backend/services/packaging_match.py` 的既有口径
  （本批只新增报价侧实现并比对，不改工艺侧）。
- 不改非包装行业的匹配来源与既有六维打分。
- 不把盒型库读失败伪装成「空库 / 无候选」。
- 不新增第三方依赖；不改 `industry_templates` 的模板与必填项。
- 不删除或改写历史会话、卡片、任务、证据。

---

## 4. 红测

`tests/test_quote_packaging_box_selection_red.py`（已落盘，**20 个用例：18 红 / 2 绿护栏**），
全部离线（工艺侧用 `kb_repo` 快照注入 fixture，报价侧用 `boxes` / `weights` 注入）：

| 组 | 覆盖 | 条数 |
| --- | --- | --- |
| A 报价侧匹配器存在且配置驱动 | §2.1 | 5 |
| B 两侧口径逐字段一致 | §2.2 | 4 |
| C 入口按行业分流 | §2.3 | 4 |
| D 选用与 ④ 同源 | §2.4 | 3 |
| E 接不住给出口 + 不回归 | §2.5 / §3 | 4 |

运行：

```bash
./open-claude/.venv/bin/python -m unittest tests.test_quote_packaging_box_selection_red -v
```

实现前的实测原文：

```
Ran 20 tests  FAILED (failures=22)
```

红点分布：A 组 5 全红、B 组 4 全红（含 6 个子用例）、C 组 4 全红、D 组 2（d1/d2）、
E 组 3（e1/e2/e3）。绿：D3（`tech_param_row` 与 ④ 表头同源，已实现）、
E4（非包装行业与工艺侧口径护栏）。

---

## 5. 本批不做

1. 工艺侧盒型匹配与确认流程（已实现，不动）。
2. 包装 BOM / 工艺路线 / 成本（另有批次）。
3. 非标判定与只读定制要求 —— 见 `docs/specs/quote-nonstandard-path.md`。
