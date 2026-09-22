# 规格：非标路径治理（判定 / 只读定制要求 / 允许继续 / 回写待确认 / 流程状态只读）

状态：Spec + 红测（已实现）
红测：`tests/test_quote_nonstandard_path_red.py`

相关规格：
`docs/specs/quote-tech-handoff-button.md`（按钮区「转技术工艺」按钮）、
`docs/specs/quote-markup-gate-advice.md`（第 3/4 步提示）、
`docs/specs/quote-agent-industry-alignment.md`（需求门禁按行业）、
`docs/specs/packaging-box-type-matching.md`（工艺侧盒型匹配口径）、
`docs/specs/quote-packaging-box-library-selection.md`（同批：包装选品接盒型库）。

本批的五条口径由业务拍板（原话）：

| # | 拍板 |
| --- | --- |
| 1 | 非标判定**按建议**：加一条不看总分的维度下限（用途/场景太差就算接不住） |
| 2 | 定制技术要求：**要人工填就不做**；不需要给人加多余步骤才做 → 本批做成**自动生成的只读汇总** |
| 3 | 非标单在工艺出方案前**可以继续往下走**（POC 阶段无所谓）→ 不得新增门禁 |
| 4 | 工艺出新成品编码后，价格**提示销售确认**，不自动替换 |
| 6 | 流程状态（如「测算状态」）**只让系统改，AI 只读** |

---

## 1. 现场问题（实测）

现场会话：包装询盘（数码天地盒 100*90*40 / 铜版纸亮膜+哑膜 / 灰板 2.5mm / 首批 1500 /
常温），用户原话「想走技术工艺流程但是走不过去」。

1. **判不出来**：`cpq_match.py:293` 唯一的非标信号是 `below = best < RECOMMEND_THRESHOLD`（70）。
   本次「用途/场景契合」只有 30 分，但总分 86 ≥ 70 → `below_threshold=False`，
   维度塌陷被其余五项满分掩盖，系统不认为这单接不住。
2. **没有承载位**：第 2 步「工艺确认」的契约只有「沿用第 1 步产品信息/技术参数」
   （`确认需求解析结果.html:3492` `CARRY_MAP`、`:3508` `carryProducts` 的
   `if (!rows.length) return;`）——非标没有标品，两张表必然 `共 0 条`，
   工艺经理接手时看不到要确认什么。
3. **归因错误**：`:3781` `runMarkupStep()` 取不到产品行时只报
   「前面步骤还没有产品信息。」，且不给动作（A 档 `quote-markup-gate-advice` 已单独出方案）。
4. **回写无标记**：`cpq_tech_bridge.py:962` 的 `payload` 带 `"tech_result": result`，
   但没有任何「这是待确认结果、不是可直接用的价格」的标记。
5. **状态可被 AI 改写**：`_enforce_fixed_template()`（`:361`）只做字段裁剪，不区分
   业务值与流程状态；`s1_basic` 的 45 个字段里包含「测算状态」（DA 本体 `calc_status`），
   AI 可以照填。

---

## 2. 契约

### 2.1 非标判定（拍板 1）

`cpq_match.py` 新增模块级配置（**唯一事实源，HTML 里不得再写一份魔法数**）：

```python
#: 关键维度分下限：任一维度低于它即判「无适用标品」，不看总分（Spec §2.1）。
NONSTANDARD_DIM_FLOORS = {"scope": 60.0}
```

`match()` 返回值**新增** `nonstandard`（其余键原样保留）：

```python
"nonstandard": {
    "triggered": bool,
    "reasons": [{"key": "scope", "label": "用途/场景契合", "actual": 30.0,
                 "threshold": 60.0, "reason": "场景不匹配（…）"}],
    "suggested_task_kind": "tech_new_product" 或 "",
    "best_code": str, "best_total": float,
}
```

判定规则（任一成立即 `triggered=True`）：

1. `best_total < RECOMMEND_THRESHOLD`（既有规则，reason key = `total`）；
2. Top3 中任一行的 `detail[key].score < NONSTANDARD_DIM_FLOORS[key]`（reason key = 维度 key）。

`below_threshold` / `threshold` / `advice` / `products` / `weights` / `source` / `all_count`
逐字不变（`tests/test_quote_agent_industry_alignment_red.py` 不得回归）。
`suggested_task_kind` 为 `cpq_wf.TASK_KIND_TECH_NEW`，`triggered` 为假时是空串。

服务端 `_handle_match_products` 的 `chat_candidates` 事件必须带 `nonstandard`；
`/api/step1/match` 匹配分支返回体也必须带。非标时工具返回文本要给出动作指引
（含「新增工艺」），**不得编造产品**（既有禁令保留）。

### 2.2 只读的定制技术要求汇总（拍板 2）

**不加任何人要填的东西。** 第 2 步在非标路径下多一块**只读**汇总，内容全部从第 1 步
已有信息自动生成：

- 服务端 `_BI_SECTIONS` 新增
  `"s2_custom_spec": ("form", "定制技术要求（非标）", ("价格测算单", "定制技术要求"), False)`；
  第 4 个元素（可编辑）必须是 `False`；
- `_FALLBACK_FIELDS` 必须给出兜底字段（DA 本体没有该逻辑实体时表不能是空的），
  至少覆盖：`尺寸(mm)`、`纸张与膜系`、`板材与厚度`、`数量`、`交期`、`随附图纸`、`判定原因`；
- 前端 `carryProducts()` 在**非标路径**下必须渲染 `s2_custom_spec`（来源为空时不再静默
  `return`），内容来自：
  - `s1_basic` 的「质量专控要求」与需求文本里的尺寸/材质/数量；
  - `WF.nonstandard.reasons`（为什么判非标）—— 让工艺经理一眼看到原因；
- **只读**：字段不带 `editable`，页面不得给该分区渲染输入框、不得对它做必填校验、
  不得新增「确认」动作或任何额外步骤；
- 标品路径下该分区不渲染（非标专属）。

### 2.3 允许继续，不新增门禁（拍板 3）

- `confirmStep()` **不得**因为缺产品行而早退：它体内不得出现 `s1_products` /
  `extractSectionRows('s1_products')` 之类的产品行检查；
- `btnNext` 的 `disabled` 只允许由 `busy` / 登录态决定，不得新增「没有产品行就禁用」；
- 第 3/4 步拿不到产品行时，提示必须说明**可以先继续**（POC 阶段接受本步算不出），
  并给「转技术工艺」出口 —— 该文案由 A 档的 `markupGateAdvice(step, column)` 承载，
  `text` 必须同时含「可以继续」与「重算」；
- 仍然 `return false`：本步算不出就是算不出，不许伪造产品行、不许算出假价格。

### 2.4 回写待确认（拍板 4）

- `cpq_tech_bridge.send_to_quote()` 落库的 payload 必须显式标记这是**待确认结果**：
  新增 `"needs_confirmation": True`（与 `"tech_result"` 同级）；
- 回写路径**不得**直接写报价单的产品行与价格：`cpq_tech_bridge.py` 全文不得出现
  `s1_products` / `s1_techparams`（现状已满足，作为护栏）；
- 报价工作台收到带 `tech_result` 且 `needs_confirmation` 的任务时，必须渲染一块只读的
  「工艺回传结果（待确认）」+ 一个「确认写入」动作；
- **只有**该确认动作可以写 `s1_products` / `s1_techparams`（写入集中在这个函数里），
  写入后价格列注明来源为工艺回传；
- 目标行已有值时不得静默替换：确认前先提示两组值，由销售决定。

### 2.5 流程状态只读（拍板 6）

- `cpq_agent_server.py` 新增模块级只读字段清单
  `READONLY_FIELDS = ("测算状态",)`（业务中文名，与 DA 本体字段名对齐）；
- `_enforce_fixed_template()` 对只读字段必须：`value` 置空（**丢弃模型给的值**）、
  附 `"readonly": True`；前端对只读字段保留页面已有值、不覆盖；
- 系统写路径（工作流/后端流程）不受影响，本批只关掉「AI 直接改状态」这一条；
- 提示词必须引用同一份清单（不得再抄一份中文）：系统提示里要出现 `READONLY_FIELDS`，
  并写明这些字段 AI 只读。

---

## 3. 禁止事项

- 不为解锁按钮塞假产品行 / 假成品编码；不伪造价格。
- 不改 `RECOMMEND_THRESHOLD`（70）、不改六维权重与打分函数。
- 不改半导体/电池/电器的三项门禁与 `industry_templates.required_keys('packaging')`。
- 不给「定制技术要求」加任何人工填写或额外确认步骤。
- 不新增第三方依赖；不改 `cpq_wf` 的任务类型与默认角色。
- 不删除或改写历史会话、卡片、任务、证据。

---

## 4. 红测

`tests/test_quote_nonstandard_path_red.py`，全部离线：

| 组 | 覆盖 | 条数 |
| --- | --- | --- |
| A 非标判定（含 86 分真实复现） | §2.1 | 6 |
| B 只读定制要求汇总 | §2.2 | 5 |
| C 允许继续、不新增门禁 | §2.3 | 3 |
| D 回写待确认 | §2.4 | 5 |
| E 流程状态只读 | §2.5 | 3 |
| F 不回归护栏 | §3 | 3 |

共 25 条；实现前实测 `Ran 25 tests  FAILED (failures=14, errors=3)`（红：A5/B5/D4/E3）。

运行：

```bash
./open-claude/.venv/bin/python -m unittest tests.test_quote_nonstandard_path_red -v
```

---

## 5. 本批不做

1. 包装选品接盒型库 —— 同批另一份 Spec：`docs/specs/quote-packaging-box-library-selection.md`。
2. 智能体写「（推荐）」值的边界 —— 业务已拍板：什么都可以推荐，不设限制。
3. 第 3/4 步提示的**文案与动作**本体 —— 属 A 档 `docs/specs/quote-markup-gate-advice.md`，
   本批只追加「可以继续 / 重算」这一句要求。
