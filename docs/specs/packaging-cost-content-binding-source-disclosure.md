# 「包材绑定数据源缺失」必须说出来：不许把"我没数据"显示成"没有缺口"

血缘：承接 `packaging-cost-gaps-scoped-to-order-contents.md`（§2.3 的退路：绑定集合算不出来就给空集
= 全部 unbound = 只披露不阻断）、`packaging-cost-readiness-severity-layering.md`（提示缺口怎么披露）、
`packaging-silent-degradation-disclosure.md`（"失败 / 空 / 没有"三者不许在返回体上同形，本批补的是同一
病症在成本侧的**第四处**）。

状态：Spec + 红测（已实现）（`bound_content_codes_detail()` 带来源闭集 + 结果体 `content_binding`
逐条列出降级披露的包材项 + 就绪门 `content_binding_source` 与那句"绑定数据源缺失"；落地见 §6）
红测：`tests/test_packaging_cost_content_binding_source_disclosure_red.py`

## 0. 一句话目标

`packaging-cost-gaps-scoped-to-order-contents.md` 的退路（绑定算不出来 → 全部只披露）必须**自报家门**：
读接口、就绪门、报告都要能回答"这些包材缺口为什么没进阻断"——否则用户看到的是"没有阻断缺口"，
而真相是"我们不知道这一单用了哪几项包材"。

## 1. 现状缺口（实现自查，逐条可指到行）

`tech_app/backend/services/packaging_cost.py`：

```python
def bound_content_codes(data: Any, *, rows=None) -> set:
    """本单「绑上的包材项」集合（Spec §2.3）—— **唯一** 一处算它的地方。
    这一版故意只给**空集**：仓库里还没有「这一单到底用哪几项包材」的权威数据源 …
    """
    return set()
```

`compute_project()`（`:2193-2204`）用它算 `bound_codes` → 每一行都是 `unbound` → `bound_gaps = []`。
后果（本批要修的**披露**问题，不是公式问题）：

1. **所有** `content_formula_error:*` 都变成"只披露"——包括本单**真的用得上**的包材项，
   它的金额缺失不再阻断（成本可能**少算**，见 §2.2 的账）；
2. 读接口拿到 `bound_gaps = []`，与"这一单确实一项都不缺"**同形**，页面/报告看不出差别；
3. 就绪门已经有 `unbound_total`（上一批加的），但那只是**条数**，没有回答"为什么"。

## 2. 契约

### 2.1 绑定集合要带来源（一处，可判）

新增 `bound_content_codes_detail(data, *, rows=None) -> dict`：

```jsonc
{"codes": ["..."], "source": "authoritative" | "none"}
```

- `source` 是**闭集**；今天没有权威数据源 → 必须老实报 `"none"`，**不许**假装 `"authoritative"`；
- `bound_content_codes()` 保留为兼容包装（只回 `codes`），既有调用点不许改行为；
- 算这件事的地方**只有这一处**（不许在成本引擎别处再写一套"猜哪一项用到"）。

### 2.2 成本结果体必须自报家门

`compute_project()` 结果体新增：

```jsonc
"content_binding": {"source": "none", "bound_total": 0, "unbound_total": 7,
                    "bound_codes": [], "unbound_codes": ["PKG-CT-DIVIDER", "..."]}
```

- `unbound_total` / `unbound_codes` 逐字来自这次算出的 `unbound_gaps`（不许重算一份）；
- **`source == "none"` 时必须能被读接口/报告读到**（`load_cost()` 原样透出，不许吞掉）。

### 2.3 就绪门要把"为什么"说出来

`packaging_cost_readiness_gate()` 新增 `content_binding_source`（键**必须存在**，取不到给 `""`），并且：

| 条件 | `reasons` 必须有 |
| --- | --- |
| `content_binding_source == "none"` 且 `unbound_total > 0` | 一句"包材绑定数据源缺失：N 条包材缺口只披露不阻断"（N = `unbound_total`） |
| `content_binding_source == "authoritative"` | **不许**出现上面那句 |

`verdict` 口径**一个字不改**（`"none"` 不把成本打成 `provisional`——那是上一批定下的严重度分层）；
本批只加"说出来"这一件事。

### 2.4 不许用"少算的钱"换安静

`source == "none"` 时，`unbound_codes` 里**必须**逐条列出被降级披露的包材项，
不许只给一个总数（报告要能指名道姓）。

## 3. 允许修改范围

只改 `tech_app/backend/services/packaging_cost.py`：`bound_content_codes_detail()`（新增）、
`bound_content_codes()`（改为包装它）、`compute_project()`（结果体加 `content_binding`）、
`packaging_cost_readiness_gate()`（加 `content_binding_source` 与那句 reason）。
`load_cost()` 若已在透出结果体则不改；若白名单式取键，**只许加这一个键**。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件；
- 不许改 `verdict` / `blocking_total` / `unbound_total` 的口径，不许让 `"none"` 进阻断；
- 不许真的去猜绑定（本批**没有**权威数据源，`source` 就是 `"none"`；接数据是以后的事）；
- 不许改公式、费率、种子数据、DDL；
- 不许 commit / push / tag / Release / 部署。

## 5. 验收

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_content_binding_source_disclosure_red -v  # 红→绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_gaps_scoped_to_order_contents_red -v     # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_readiness_severity_layering_red -v       # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red -v                            # 不回归
```

## 6 落地状态（2026-09-22，实现方 Codex）

实跑：`./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_content_binding_source_disclosure_red`
→ **Ran 7 tests … OK**（A1/A3/B1–B4 六条红转绿，A2 兼容包装护栏仍绿）。

- `packaging_cost.bound_content_codes_detail(data, *, rows=None)`（新）：返回
  `{"codes": [], "source": "none"}`；来源闭集 `CONTENT_BINDING_SOURCES = ("authoritative", "none")`，
  今天**老实报 `none`**（没有权威数据源就不假装）。
- `packaging_cost.bound_content_codes()` 改为**兼容包装**（只回 `codes`，既有调用点行为不变）。
- `compute_project()`：`bound_detail = bound_content_codes_detail(data)` 取来源，结果体新增
  `content_binding`：`source` / `bound_total` / `unbound_total` / `bound_codes` / `unbound_codes`
  —— 后两项**逐字来自这一趟算出的 `gaps_unbound_to_order`**（不重算一份），
  `unbound_codes` 按内容码去重升序（指名道姓，不只给总数）。
- `packaging_cost_readiness_gate()`：新增 `content_binding_source`（键**总是存在**，取不到 `""`）
  与那句 `包材绑定数据源缺失：N 条包材缺口只披露不阻断`（仅当 `source == "none"` 且
  `unbound_total > 0`）；**`verdict` 口径一个字未改**（`"none"` 不把成本打成 `provisional`）。
- 读侧 `_rehydrate()` 也带同一个键（`_content_binding_of()`，与 `compute_project()` 同一形状，
  来源也走那一个函数）—— 读接口不吞来源。

### 已记录的边界

- 读侧的 `unbound_*` 取自落库回来的那一份缺口；`gaps_unbound_to_order` 目前**没有落库列**
  （本批不许改 DDL 之外的东西，且 Spec §3 只说"只许加这一个键"），所以读侧 `unbound_total`
  仍是 0 —— 与这一批之前读侧的现状一致；本批保证的是**来源**（`source: "none"`）在读接口上
  可见。让读侧的 `unbound_*` 也可回放需要另一批（给成本表加这一列），本批不越界。

未改任何测试、未放宽任何断言、未连 34、未写生产数据、未 push / MR / tag / Release / 未部署。
