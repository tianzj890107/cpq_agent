# 损耗率取数必须接库里已有的两处权威源（材料行标准损耗率 / 因子表作用域）

血缘：承接 `packaging-cost-engine.md`（第 7 批成本引擎）、`packaging-cost-gaps-closure.md`
（把「引擎自己能算却算不出来」的那一类缺口收掉，本条是同一条纪律的下一例）、
`packaging-cost-rule-routing.md`（规则取数口径）。

状态：Spec + 红测（已实现）（`default_loss_rate()` 现在四级取数：材料行 `standard_loss_rate` →
文字兜底 → 因子表按作用域 → None；0 只当「未登记」，不留痕不猜）
红测：`tests/test_packaging_cost_loss_rate_sources_red.py`

## 0. 一句话目标

材料行上写着的标准损耗率、因子表里带作用域的损耗率，**必须被成本引擎用到**；取不到就是取不到，
照旧出缺口，但**不许**因为"只认灰板/纸两种文字"而白丢一条缺口。

## 1. 现状缺口（代码事实 + 数据事实，逐条可复现）

### 1.1 引擎只按文字认三种材料（`tech_app/backend/services/packaging_cost.py`）

```python
def default_loss_rate(material_text: Any, *, rows=None) -> Optional[float]:
    text = _text(material_text)
    source = rows if rows is not None else kb_repo._table("kb_cost_factor")
    factors = {row.get("factor_code"): row for row in (source or [])
               if row.get("factor_type") == "scrap"}
    if "灰板" in text:
        hit = factors.get("F-PKG-LOSS-GREYBOARD")
        return _num(hit.get("value")) if hit else None
    for keyword in ("面纸", "衬纸", "特种纸", "铜版", "纸"):
        if keyword in text:
            hit = factors.get("F-PKG-LOSS-PAPER")
            return _num(hit.get("value")) if hit else None
    return None
```

- 调用点 `loss_rate_for()`（同文件 1838-1841）只把因子表 `rows` 传进去，**材料行没传**；
  而材料行在同一个循环里已经取到了（`material = _resolve_material(row, materials)`，1860）。
- `applicable_scope` 这个列在 `kb_repo.effective_factor()`（`kb_repo.py:621`）里已经有"专用作用域
  压过通用兜底"的现成口径，成本引擎没走那条路。

### 1.2 库里两处权威源都有值

| 出处 | 事实 |
| --- | --- |
| `kb_material.standard_loss_rate` | 真列（`da_schema.sql:296`，`NOT NULL DEFAULT 0`）；包装 5 条材料全有值：灰板 `0.08`、铜版纸面纸 `0.06`、内衬纸 `0.05`、特种纸 `0.09`、EVA 片材 `0.10`（`da_seed_packaging.py:434-466`） |
| `kb_cost_factor.applicable_scope` | 真列（`da_schema.sql:405`）；`F-PKG-LOSS-GREYBOARD` / `F-PKG-LOSS-PAPER` 都写着 `applicable_scope='包材'`（`da_seed_packaging.py:504-507`） |

### 1.3 后果

- `EVA 片材` 材料行明明写着 `standard_loss_rate = 0.10`，引擎仍报 `loss_rate_missing`；
- 那一行金额照出（损耗按 0 计），于是按 `reject_silent_zero_fallback()` 被判"静默按 0"
  → 整份成本 `verdict=provisional`（34 全流程那次跑：`loss_rate_missing` 9 条、
  影响金额 5.4831 元/件，占成本约 31%）；
- 换句话说：**这条缺口不是"业务没给数"，是引擎没去看已经给了的数**。

## 2. 契约

### 2.1 取数顺序（四级，逐级只有命中才停止）

`default_loss_rate(material_text, *, rows=None, material=None) -> Optional[float]`：

| 序 | 来源 | 条件 | 命中后 `loss_rate_source` |
| --- | --- | --- | --- |
| 1 | 材料行 `standard_loss_rate` | `material` 给了且该值 `> 0` | `"material.standard_loss_rate"` |
| 2 | 文字兜底（既有行为，不许删） | 文案含 `灰板` → `F-PKG-LOSS-GREYBOARD`；含 `面纸/衬纸/特种纸/铜版/纸` → `F-PKG-LOSS-PAPER` | `"kb_cost_factor:<factor_code>"` |
| 3 | 因子表按作用域 | `material.category`（或调用方显式给的 scope）非空时，取 `factor_type="scrap"` 的因子：**同作用域压过无作用域**，同级取 `effective_from` 最新 | `"kb_cost_factor:<factor_code>"` |
| 4 | 取不到 | —— | `None` |

### 2.2 `standard_loss_rate == 0` 是「未登记」，不是「损耗 0」

该列 DDL 是 `NOT NULL DEFAULT 0`，所以 0 只说明没人填 —— 必须继续往下找（第 2/3 级），
**不许**把 0 当成一个可用的损耗率返回。

### 2.3 没有材料行就不猜

工序行 / 人工行没有材料行：**不做**第 3 级的"随便捡一条 scrap 因子"。作用域要由材料行（或调用方
显式参数）给出；给不出就只能是 `None` + 缺口。这条是防止"用一条通用兜底把 9 条缺口全部糊绿"。

### 2.4 留痕

材料行的 `inputs_json` 必须带 `loss_rate_source`（闭集同 §2.1）；工序行照旧不带。

### 2.5 缺口口径一个字不放宽

取不到仍然 `gaps += {"code": "loss_rate_missing", ...}`，`severity=advisory`、
"损耗按 0 计（金额保留）"的文案与 `reject_silent_zero_fallback` 判据都不动。

## 3. 允许修改范围

只改 `tech_app/backend/services/packaging_cost.py`：

1. `default_loss_rate()`：加 `material=None` 关键字参数，按 §2.1 四级取数、按 §2.2 把 0 当未登记；
2. `loss_rate_for()`：内部把当前材料行传进去（材料行循环处再加一个只传材料的形参即可，
   工序/人工三处调用点**一个字不改**）；
3. 材料行写 `loss_rate_source`（§2.4）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件；
- 不许改 `GAP_RESOLUTIONS` 的 severity、不许改 `reject_silent_zero_fallback` 的判据；
- 不许给任何材料一个"默认损耗率"（包括把 0.08 当通用值铺给所有非纸材料）；
- 不许改 `kb_material` / `kb_cost_factor` 的 DDL 与种子数据；
- 不许 commit / push / tag / Release / 部署。

## 5. 验收

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_loss_rate_sources_red -v   # 红→绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_gaps_red -v                # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red -v              # 不回归（J6 存量红除外）
```
