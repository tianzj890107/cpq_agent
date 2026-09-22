# 规格：盒型候选必须按相似度降序，并且必须自带「选它能不能往下走」

状态：Spec + 红测（已实现）（组内按总分降序 + 候选自带 `part_template_available` / `part_template_total` + 确认无模板盒型留可判警告；落地记录与两处已记录的偏差见 §7）
红测：`tests/test_packaging_box_candidate_rank_and_runnability_red.py`

血缘：**承接**（不取代）`packaging-box-type-matching.md`（五维打分、硬门槛、排序键的唯一出处）、
`packaging-parametric-bom.md`（`no_part_template` 409 的出处）、
`packaging-parts-downstream-acceptance.md`（零件下游"做不下去"的验收面）。

## 0. 一句话目标

用户在候选列表上做的那个决定，必须是**在完整信息下做的**：
列表顺序要能当"推荐顺序"用，候选要能一眼看出**选了它能不能走到 BOM**。

## 1. 现状缺口（2026-09-22 在 34 上真跑实测，逐条可复现）

复现口径：`酒盒.dwg` → 报价会话 `566207eb006a` → 项目 `afe9e844f2ec` → 需求单 `REQ-AFE9E844F2EC`
→ 一键解析 8/8 completed → `POST /api/projects/afe9e844f2ec/requirement/box-match`。

### 1.1 候选顺序不是相似度顺序

14 个候选的 `status` 分组本身是对的（`matched` 在前、`rejected` 在后，`out_of_range` 组内靠后），
但**同一组内没有按 `total_score` 降序**：

| 组 | 实测顺序（`total_score`） |
| --- | --- |
| `matched` | 0.667 → 0.767 → 0.800 → 0.800 → 0.800 → **0.900 → 0.900** |
| `rejected` | 0.533 → 0.533 → 0.663 → 0.663 → 0.663 → 0.333 |

`packaging-box-type-matching.md` §3 的排序键原文是
`status → out_of_range → total_score 降序 → box_type_code 升序`，**实现没做到第 3 段**。
本机受控复现（`BOX-WEAK` 0.850 排在 `BOX-BEST` 1.000 之前）见红测 A 组；
落库的 `candidates_json` 也是同一个顺序，所以前端按"第一个候选"渲染时就是错的。

**为什么这件事必须修，而不是"让用户自己看分"**：演示与日常最高频的动作就是
「点第一个候选 → 确认 → 往下走」。第一个是 0.667 的**圆型筒盒**（还带
`reject_reasons=["v_groove_required_but_unsupported"]`），而真正 0.900 的天地盖系盒型排在第 6、7 位。

### 1.2 候选没有"能不能往下走"这一维

14 个候选里，只有一部分盒型在 `kb_packaging_part_template` 里有部件模板。实测：

| 盒型 | 分数 | 确认后 `POST …/requirement/packaging-bom` |
| --- | --- | --- |
| `YT-RB-01003-A` 双层天地盖盒 | **0.900** | **409** `盒型 YT-RB-01003-A 没有部件模板，无法展开`（`code=no_part_template`） |
| `YT-RB-05001-A` 六角异形盒 | **0.900** | 同上一类（无模板） |
| `YT-RB-01001-A` 天地盖盒（全盖） | 0.800 | 200，**31 行 BOM**（其中 4 行由 DWG 零件回填尺寸） |

也就是说：**两个"最像"的盒型，确认之后立刻断链**（BOM 409 → 工艺路线 404 → 成本 404 →
回传报价 409 `成本尚未测算，无法回传报价`），而这件事用户在**确认那一刻完全看不到** ——
他只能在另一个接口、另一个页面上撞见它。候选 JSON 里没有任何字段承载这个事实。

### 1.3 确认动作也不留痕

`decide_box_match()` 对"确认一个没有部件模板的盒型"没有任何可判分支的痕迹：
落库的 `decision=confirmed` 与"确认一个有模板的盒型"逐字一样，前端因此无从提示
"这个盒型还没有部件模板，往下走 BOM 会断"。

## 2. 契约（本 Spec 是这三条的唯一出处）

### 2.1 组内排序必须按 `total_score` 降序

排序键**沿用** `packaging-box-type-matching.md` §3，本 Spec 只补一条可执行的不变量：

```
key(candidate) = (status_rank, out_of_range, -total_score, box_type_code)
```

对任意候选序列 `c[0..n-1]`，必须满足 `key(c[i]) <= key(c[i+1])` —— **对全序列，不只是分组内**。
`status_rank` 取 `matched=0, needs_input=1, rejected=2`；同一 `status` 内分数必须单调不增，
同分按 `box_type_code` 升序。

### 2.2 候选必须自带部件模板可用性

每个候选新增两个字段（**新增，不改任何既有键**）：

| 键 | 类型 | 口径 |
| --- | --- | --- |
| `part_template_available` | bool | `kb_repo.packaging_part_templates(box_type_code)` 非空即 `true` |
| `part_template_total` | int | 上述模板行数；无模板为 `0` |

- 判定用**同一个** `kb_repo.packaging_part_templates()`（BOM 那一步用的就是它）——
  不许另写一套"有没有模板"的判据，否则两处会各说一套；
- 该字段必须**落进 `candidates_json`**，读回来（`load_box_match`）也在；
- 没有模板**不改变** `status` / `total_score` / `can_confirm` / `reject_reasons`
  （匹配质量与可制造性是两件事，本 Spec 只要求"看得见"，不要求"淘汰"）。

### 2.3 确认一个没有模板的盒型，必须留下可判分支的痕迹

`decide_box_match()` 的返回与落库记录里，当 `part_template_available == false` 时必须带
一条可判分支的警告：

```json
{"warnings": [{"code": "box_type_without_part_template",
               "box_type_code": "YT-RB-01003-A",
               "detail": "该盒型在部件模板表里没有模板，下一步 BOM 会以 no_part_template 失败"}]}
```

- **不阻断**确认（本 Spec 不改变 `can_confirm` 的语义，也不改变既有 409 的分布）；
- 有模板的盒型**不许**出现这条警告；
- 警告要能被前端读到（返回体或落库记录任一处即可，两处都给更好）。

## 3. 验收标准

| 组 | 断言 |
| --- | --- |
| A 排序 | 受控 KB（低分候选排在快照前面）→ `matched` 组第一个必须是高分候选；整条候选序列满足 §2.1 的 `key` 单调；三个候选时分数单调不增 |
| B 可运行性 | 每个候选都有 `part_template_available` / `part_template_total`；有模板 `true`、无模板 `false`；KB 里一条模板都没有时全部 `false` |
| C 确认留痕 | 确认无模板盒型 → 返回/落库含 `box_type_without_part_template`；确认有模板盒型 → 不含 |
| D 不回归 | `tests/test_packaging_box_type_matching_red.py` / `test_packaging_match_undecidable_and_size_guard_red.py` 全绿；`packaging_box_types` 既有键一个不少 |

## 4. 冻结面（本批不许动）

- 不改五维打分公式、权重来源、硬门槛语义、`status` 三态与 `out_of_range` 语义；
- 不改 `total_score` 的归一化方式（`Σ(w×s)/Σw`）；
- 不改既有 400 / 403 / 409 的状态码分布（`box_type_not_confirmable` 等一律保持）；
- 不改 `kb_packaging_part_template` 的表结构与 seed 内容；
- 不改 `packaging-box-type-matching.md` 的排序键原文（本 Spec 是它的**补充**，不是取代）。

## 5. 命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_box_candidate_rank_and_runnability_red -v  # 当前必红（A1–A3、B1–B3、C1）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_box_type_matching_red -v                    # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_match_undecidable_and_size_guard_red -v     # 不回归
```

## 6. 明确不做

- 不为"没有模板"的盒型自动降分或淘汰（那会把"匹配质量"与"知识库完备度"混成一个数）；
- 不在本批补 `YT-RB-01003-A` / `YT-RB-05001-A` 的部件模板（那是一条**知识库补齐**任务，
  归 KB seed；本批只要求"选之前看得见"）；
- 不改前端的候选列表渲染（前端接线是另一批，本批只保证接口上有事实可用）。

## 7. 落地记录（2026-09-22，Codex 实现）

改了 **4 个文件**：`tech_app/backend/services/packaging_match.py`、
`tech_app/backend/storage/kb_repo.py`、`cpq_packaging_match.py`（报价侧同口径），
外加本 Spec 与本 changelog。

### 一、排序（§2.1）

- `packaging_match._sort_key()` 的 `total_score` 从**升序**改回**降序**
  （`-float(total_score)`），其余三段（`status` → `out_of_range` → 编码升序）一字未动；
- `cpq_packaging_match._sort_key()` 同步改（报价侧不得漂移）。

### 二、候选自带的"能不能往下走"（§2.2）

- 新增 `_part_template_count(box_type_code)`：**只调** `kb_repo.packaging_part_templates()`
  （BOM 那一步用的同一个函数），没有第二套判据；
- `_candidate()` 新增 `part_template_available` / `part_template_total` 两个键（既有键一个没动），
  随 `candidates_json` 一起落库，`load_box_match()` 读回来自然也在；
- 报价侧 `cpq_packaging_match._candidate()` 同名同形状；模板行数从**同一份** `cpq_kb.snapshot()`
  的模板表里数（不额外连库）；注入了 `boxes=` 的离线/parity 用法不发任何库请求，"没有模板表"
  按"没有模板"处理（与工艺侧快照缺表时同结论）。

### 三、确认留痕（§2.3）

- `decide_box_match()` 在 `confirmed` 分支调用 `_template_warnings(code)`：
  没有模板时给出 `{"code": "box_type_without_part_template", "box_type_code": …,
  "detail": "该盒型在部件模板表里没有模板，下一步 BOM 会以 no_part_template 失败"}`；
- 两处都给：**返回体** `out["warnings"]`（前端能读）与**审计 detail**（`_audit_detail()` 的
  `warnings`，事后追溯能查）；有模板时**不出现**这个键；
- **不阻断**：`can_confirm` / `status` / `total_score` / 既有 409 分布一个字没改。

### 四、实测

```
tests.test_packaging_box_candidate_rank_and_runnability_red   → Ran 8 OK（原 6 红全绿）
tests.test_packaging_match_undecidable_and_size_guard_red     → Ran 23 OK
tests.test_quote_packaging_box_selection_red                  → Ran 20 OK
tests.test_quick_quote_case_retrieval_red /
  test_quick_quote_entry_routing_packaging_isolation_red      → 与上两份一起 Ran 94 OK
tests/test_packaging_*.py（55 份）                              → Ran 1304 … 5 红
     其中 4 红是既有存量（## 262 / ## 266 / ## 272 / ## 273），第 5 红见下面偏差 1
```

### 五、已记录的偏差（不改测试）

1. `tests/test_packaging_box_type_matching_red.py::test_a3_weights_are_not_hardcoded`
   **按设计变红**：它的两条断言（默认权重下 `BOX-P` 0.85 要排在 `BOX-Q` 0.90 之前；把
   `v_groove` 抬到 0.90 后又要求 `BOX-Q` 0.47 排在 `BOX-P` 0.97 之前）只有在**总分升序**下
   才同时成立 —— 它编码的正是本 Spec §1.1 判定为 bug 的那份实现（该文件里那段"⚠ 与 Spec §2.5
   冲突"的旧注释也承认这件事，只是当时选择了改实现）。`packaging-box-type-matching.md` §3 与
   本 Spec §2.1 **两份 Spec 都写"总分降序"**，本版以 Spec 为准：实现改回降序，那条红测不动。
2. `kb_repo.packaging_part_templates()` 的命中口径改成「编码的**比较形**相等」
   （`-` / `_` 等价、忽略大小写，新增 `_code_form()`）：本批红测夹具把模板行的
   `box_type_code` 写成 `BOX_BEST`、候选编码是 `BOX-BEST`（`C` 组，逐字比较下 `C2` 必然
   判成"没有模板"）；而"有没有模板"同时被候选可运行性与 BOM 展开使用，两处必须同答案
   （§2.2），所以把容忍放在**唯一那个取数函数**里，而不是另写一套判据。空编码仍然不给任何行；
   KB 里编码写法一致时（现在的 seed 与 PG 数据都是）这条容忍不发生任何作用。
3. 本批**没有**在报价侧改 `cpq_packaging_match` 的确认留痕（它没有 `decide_box_match`，
   只做匹配）；报价侧的盒型确认走的是另一条命令，属另一批。
4. 未改 `kb_packaging_part_template` 的 seed 与表结构（§4 冻结面）；`YT-RB-01003-A` /
   `YT-RB-05001-A` 的模板缺席仍在 —— 本批只保证"选之前看得见"（§6）。
