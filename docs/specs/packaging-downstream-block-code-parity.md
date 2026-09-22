# 规格：包装下游"做不下去"的原因必须机器可分（错误码同构 + 前置缺口分得开）

状态：Spec + 红测（已实现）（`BoxMatchError` 已与另外四个错误类同构；缺草稿 = `requirement_draft_missing`(409)、非本行业 = `industry_missing`(400)；五条包装链路出口都带 `code`）
红测：`tests/test_packaging_downstream_block_code_red.py` `tests/test_packaging_downstream_block_code_http_red.py`

血缘：承接 `packaging-downstream-blockers-close-loop.md`（下游三处缝）、
`packaging-parse-to-downstream-seams.md`（门禁不认解析结论）、
`packaging-dwg-parts-extraction.md`（零件只是新增前置事实）、
`packaging-parametric-bom.md` / `packaging-route-and-cost.md`（BOM / 路线 / 成本的错误码口径）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

让"零件出来了、下游却做不下去"这件事**能被人和前端一眼判出该补什么**：
包装下游的业务错误统一带稳定 `code`；盒型匹配的"没有需求草稿"与"不是包装行业"两件事不许共用一句话。

## 1. 现状缺口（2026-09-22 34 上真跑实测，逐条可复现）

### 1.1 四句话四个样，没有一句能判分支

在 34 的真实项目 `0f080b24c65d`（需求单 `REQ-E2E-JIUHE-001`）上一键解析跑完之后：

| 下游入口 | 真实返回 |
| --- | --- |
| `POST …/requirement/box-match` | **400** `盒型匹配只对包装行业的需求单生效` |
| `POST …/requirement/packaging-cost` | **409** `工艺路线尚未确认，人工费无从取工时（Spec §2.13）` |
| 补完路线再跑 `packaging-cost` | **409** `报价数量缺失或 ≤ 0，无法测算包装成本（Spec §2.13）` |
| 链路里的 `downstream_prepare` | `blocked`，理由在 `blocking[].code = field_missing` |

前三条是**裸中文**（HTTP body 只有 `detail` 一句话），第四条才有 `code`。前端因此没法统一做
"缺什么 → 给哪个补录入口"。

### 1.2 一半错误有码、一半没有

```
packaging_bom.BomError        (message, status_code, code)
packaging_route.RouteError    (message, status_code, code)
packaging_cost.CostError      (message, status_code, code)
packaging_handoff.HandoffError(message, status_code, code)
packaging_match.BoxMatchError (message, status_code)        ← 只有它没有 code
```

同一个业务面里两种错误形状，调用方只能对盒型匹配特殊处理。

### 1.3 最刺眼的一处：缺草稿被错报成"行业不对"

需求单**完全不存在**（没有草稿）时，`_require_packaging()` 走到的是同一句

```
raise BoxMatchError("盒型匹配只对包装行业的需求单生效", 400)
```

用户照这句话去改行业，**永远改不好** —— 真正该做的是先把需求草稿建出来。
（本次真跑第一次 `field_write` blocked 的 `REQUIREMENT_DRAFT_MISSING` 与第 5 步的
`图纸解析未完成` 是同一类问题的两种表现。）

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_match.py`
   - `BoxMatchError.__init__(self, message, status_code=409, code="")`，与 `BomError` /
     `RouteError` / `CostError` / `HandoffError` **逐字同构**（参数名、顺序、缺省值一致），
     保留 `self.message` / `self.status_code` 两个既有属性；
   - `_require_packaging()` 拆成两条判据，各带自己的码：
     1. 需求单不存在（`store.load_requirement(project_id)` 空）→
        `BoxMatchError("还没有需求草稿，请先建需求单再匹配盒型", 409, "requirement_draft_missing")`；
     2. 需求单存在但行业不是包装 → `code="industry_missing"`，**文案要指出可处置动作**
        （去需求单选「包装」行业），不再是一句"只对包装行业生效"；
   - 既有拒绝点的码逐个补上，且**语义不许改**：`box_type_not_confirmable`、
     `box_type_not_confirmed`（沿用现网文案对应的既有字符串），其余新码加在本批的
     `_DECISION_STATES` / 匹配前置分支上。
2. `tech_app/backend/main.py`
   - `_box_match_flow()` 捕获 `BoxMatchError` 时，把 `exc.code` 放进 HTTP 返回体
     （`HTTPException(status, detail={"message": str(exc), "code": exc.code})` 或等价结构），
     与包装 BOM / 路线 / 成本三条路由的既有出口**同构**；
   - 不许改路由路径、权限门禁（`BOX_MATCH_DECIDE_ROLES`）与行业闸门 `assert_industry_scoped_candidates`。
3. `tech_app/frontend/app.js`
   - 盒型匹配面板按 `code` 给处置（`requirement_draft_missing` → 跳需求草稿；
     `industry_missing` → 跳行业选择；`box_type_not_confirmed` → 留在匹配页）。

## 3. 禁止事项

- 不许为了"有码"去改 `BoxMatchError` 的**已有** `status_code`（400 / 403 / 409 的既有分布保持），
  也不许把 `message` 改成空串或英文码。
- 不许改 `BomError` / `RouteError` / `CostError` / `HandoffError` 的既有码（`box_type_not_confirmed`、
  `route_not_confirmed`、`quantity_missing`、`bom_not_built`、`not_packaging_industry` …）。
- 不许放宽任何门禁：本批只让"拒绝得更清楚"，不许把拒绝改成放行。
- 不许改 `tests/` 下任何既有文件（含本批红测）。
- 不许连线上 PG 跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线（假 store）。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_downstream_block_code_red -v
# 7 条：A 组 2 条（五个错误同构 / code 缺省空串）
#       B 组 3 条（缺草稿 → requirement_draft_missing / 非本行业 → industry_missing
#                 / 两条前置不许共用同一句文案）
#       C 组 2 条（未确认盒型的 box_type_not_confirmed 不许被本批改掉 / HandoffError 形状保持）
# 现状：Ran 7, failures=5 —— A 组 2 条 + B 组 3 条红，C 组 2 条绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
# 不回归（零件提取仍是"新增前置事实"，不许被本批改口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
```

真机复验（实现方做完后）：

```
POST /api/projects/{pid}/requirement/box-match      # 需求单不存在 → 409 + code=requirement_draft_missing
POST /api/projects/{pid}/requirement/box-match      # industry=battery → 400 + code=industry_missing
```

## 5. 实现记录与已记录的偏差（2026-09-22，实现方落地后补写）

### 5.1 落地内容

| 位置 | 改了什么 |
| --- | --- |
| `packaging_match.BoxMatchError` | 构造签名补第 3 个位置参数 `code: str = ""`，与另外四个错误类逐字同构 |
| `packaging_match._require_packaging()` | 拆两条判据：缺草稿 → `requirement_draft_missing`(409)；非本行业 → `industry_missing`(400)，文案改成可处置动作 |
| `packaging_match.decide_box_match()` | 五个既有拒绝点补码：`forbidden_role` / `unknown_decision` / `box_match_not_found` / `box_type_not_confirmable`×2（状态码与文案一字未改） |
| `main._packaging_error_detail()` | 新增共用出口：`{"message": str(exc), "code": exc.code}` |
| `main` 五条包装链路 | `_box_match_flow` / `_packaging_bom_flow` / `_packaging_route_flow` / `_packaging_cost_flow` / `_packaging_handoff_flow` 全部改走共用出口 |
| `main.assert_industry_scoped_candidates()` | "候选全部跨行业"这条拒绝补码 `no_industry_candidate`（状态码仍 409） |
| `workflow.js apiError()` | 结构化 detail `{code, message}` 取 `detail.message`（否则界面只剩"请求失败 (409)"） |
| `requirement-confirm.js` | 盒型匹配面板按 `code` 给出口（`BM_BLOCK_ACTIONS`）；`bm/pb/pr/pc` 四个 `*Api()` 兜底也从对象 detail 取人话 |
| `requirement-confirm.html` | `.box-match-block` 样式（红底 + 跳转按钮） |

### 5.2 已记录的偏差：Spec §2.2 对"三条既有出口"的描述与源码不符

Spec §2.2 原文写"与包装 BOM / 路线 / 成本三条路由的既有出口**同构**"，并据此只要求改
`_box_match_flow()`。落地时核对源码：**五条**链路的出口当时**全部**只有
`HTTPException(exc.status_code, str(exc))`，一条都没带 `code`（也就是说不存在一个"既有
同构出口"可以照抄）。按本批标题「包装下游的业务错误统一带稳定 `code`」的意图，五条一起
改成同构出口；不改任何 `status_code`、不改任何既有 `message`、不放宽任何门禁。

### 5.3 已记录的偏差：§2.3 指的 `app.js` 里没有盒型匹配面板

Spec §2.3 要求改 `tech_app/frontend/app.js`。源码事实：`app.js` 里
`盒型匹配`/`box-match` 出现 0 次，面板实际住在 `tech_app/frontend/requirement-confirm.js`
（`#boxMatchPanel`，第 4 批引入）。因此 §2.3 的意图落在 `requirement-confirm.js`：
`requirement_draft_missing` / `industry_missing` 给跳转出口（指向该需求单页面），
`box_type_not_confirmed` 明确"留在本页继续确认"、不给跳转。`app.js` 一行未改。

### 5.4 出口侧护栏

本批 7 条红测只覆盖到服务层。"业务层有码、HTTP 出口或前端把它吃掉"是同型复发的入口，
因此另加 `tests/test_packaging_downstream_block_code_http_red.py`（10 条护栏：五条链路
出口同构 / `apiError` 读结构化 detail / 面板按码给出口 / `403-400-409` 分布冻结 /
两个前置各自的 `status_code + code`）。该文件在实现之后写成，落地即绿，属回归护栏。
