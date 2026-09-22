# 包材缺口必须分「本单用到的项」与「没绑上本单的项」：后者只披露，不许单独阻断

血缘：承接 `packaging-cost-gaps-closure.md`（§1.1 已判定那 7 条 `content_formula_error` 是
**源工作簿空格**、不是取数 bug）、`packaging-cost-engine.md`（§2.10 包材逐条计算）、
`packaging-bom-part-size-provenance.md`（"这一项绑没绑上本单"要能一眼看见）、
`packaging-cost-readiness-severity-layering.md`（严重度分层，本条补"适用性"这一维）。

状态：Spec + 红测（已实现）（`compute_packaging` 行级 `binding` + `bound_gaps`/`unbound_gaps` 分家；
`compute_project` 只把绑上的收进 `gaps`，没绑上的进 `gaps_unbound_to_order`；就绪门新增 `unbound_total`）
红测：`tests/test_packaging_cost_gaps_scoped_to_order_contents_red.py`

## 0. 一句话目标

一张报价单里**没用到的包材项**算不出金额，是"库存量数据不完整"，不是"这一单算不出来"：
它必须看得见（披露），但不许单独把这份成本打成 `provisional`、把 `quote_publish` 卡住。

## 1. 现状缺口（可复现，不依赖线上）

`tech_app/backend/services/packaging_cost.py:2060-2063`：

```python
packaging = compute_packaging(kb_repo.packaging_cost_contents(), tax_factor=..., ...)
for line in packaging["lines"]:
    if line.get("gap"):
        gaps.append(dict(line["gap"]))          # ← 整表：与 BOM 有没有关系，一个判据都没有
```

- `kb_repo.packaging_cost_contents()` 是**包材明细全量**（11 行：彩盒 / 平卡 / 隔卡 / 胶袋 /
  双胶纸 / 护角 / 标签 / 盖板 / …）；
- 其中隔卡 / 胶袋 / 双胶纸 等行的尺寸与用量在源工作簿里本来就是空格（种子注释逐行写着
  `0903 包装运输!J4/J5：工作簿缺尺寸与用量，按空单元格口径记 None`），于是**每一单**都会拿到
  同一批 `content_formula_error:PKG-P-*`；
- 这几条的 severity 是 `blocking`（`GAP_RESOLUTIONS`，`packaging_cost.py:1629`），
  于是 34 那次的 11 条阻断缺口里有 7 条来自**本单 BOM 根本没用到**的包材项，
  `readiness.verdict=provisional` → `gates.quote_publish=blocked / cost_gaps_unresolved`。

本机用真实种子复现（两条 BOM 用不到的项 + 两条用得到的项）：

```
PKG-CT-CARTON amount=2.11080741274   gap=None
PKG-CT-PAD    amount=0.284041019453  gap=None
PKG-CT-DIVIDER amount=None           gap=content_formula_error:PKG-P-DIVIDER
PKG-CT-BAG     amount=None           gap=content_formula_error:PKG-P-BAG
```

## 2. 契约

### 2.1 行级绑定事实（一处判据，调用方给）

`compute_packaging(rows, *, bound_content_codes=None, ...)`：每行结果新增

```jsonc
"binding": {"content_code": "PKG-CT-DIVIDER", "status": "bound" | "unbound"}
```

- `bound_content_codes` 给了集合：命中即 `bound`，否则 `unbound`；
- **`bound_content_codes is None`（今天所有调用点）：`status = "unknown"`，行为与今天逐字相同** ——
  这条是"先立契约、后接数据"的退路，也保证既有冻结面不回归。

### 2.2 缺口分家（`bound_gaps` / `unbound_gaps`）

`compute_packaging()` 返回体新增两个键：

| 键 | 内容 |
| --- | --- |
| `bound_gaps` | `binding.status != "unbound"` 的行的缺口 **+** 任何 `binding.status == "unbound"` 但**不是** `content_formula_error:*` 的缺口 |
| `unbound_gaps` | `binding.status == "unbound"` **且** `gap.code` 以 `content_formula_error:` 开头的缺口 |

- **本批只收 `content_formula_error` 这一类**：`no_formula:*` / `invalid_units_per_pack` /
  `material_price_missing` 等无论绑没绑上本单，一律留在 `bound_gaps`（不放宽）；
- `lines[i]["gap"]` **一个字都不许删** —— 披露不许消失，只是不再单独参与结论。

### 2.3 调用方只把 `bound_gaps` 当缺口

`compute_project()`：

- 包材那一段只把 `packaging["bound_gaps"]` 收进 `gaps`（即 `verdict` 的输入）；
- `packaging["unbound_gaps"]` 收进结果体新键 `gaps_unbound_to_order`（每条必须带
  `content_code` 与 `binding_status="unbound"`），**不参与 `verdict`**；
- 绑定集合的口径只有一处（`bound_content_codes` 由调用方算好传入），算不出来就给**空集**
  （= 全部 unbound，只披露不阻断）——不许在成本引擎里另写一套"猜哪一项用到"的规则。

### 2.4 就绪门把"没绑上"单独报数

`packaging_cost_readiness_gate()` 新增 `unbound_total`
（= `len(cost.get("gaps_unbound_to_order") or [])`），**不许**进 `verdict`、不许进 `blocking_total`。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_cost.py`：`compute_packaging()`（加 `bound_content_codes`
   与 `binding` / `bound_gaps` / `unbound_gaps`）、`compute_project()`（只改包材那一段的取缺口
   与结果体新键）、`packaging_cost_readiness_gate()`（加 `unbound_total`）。
2. 调用方（若在其它文件里给绑定集合）：只允许新增"算这批 code"的纯函数，不许改既有映射语义。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件；
- 不许改 `GAP_RESOLUTIONS`（`content_formula_error` 仍是 `blocking`）、不许改
  `SILENT_ZERO_RESOLUTIONS`、不许改 `READINESS_VERSION`；
- 不许删/改 `lines[i]["gap"]`、不许让 `gaps` 少掉"本单用得到"的缺口；
- 不许把 `gaps_unbound_to_order` 只写进日志/审计而不进结果体（页面要读得到）；
- 不许改包材公式、费率、种子数据或 DDL；
- 不许 commit / push / tag / Release / 部署。

## 5. 验收

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_gaps_scoped_to_order_contents_red -v  # 红→绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_gaps_red -v                            # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red -v                          # 不回归（J6 存量红除外）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_readiness_severity_layering_red -v     # 同批 Spec，协同
```
