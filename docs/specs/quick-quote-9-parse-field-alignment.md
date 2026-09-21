# 规格：逆向快速报价 第 9 批 —— 统一解析服务的字段形状 → 报价侧匹配输入（收口集成缝）

状态：Spec（**未实现**）
红测：`tests/test_quick_quote_parse_field_alignment_red.py`
依赖：批 5（报价侧客户端 `cpq_quick_quote_file.py`）、批 7（统一解析服务 `unified_parse.py`）。

## 0. 本批要解决的问题（实测，不是推断）

批 7 把解析服务补上后，`## 250` 在 34 上真跑客户端链路，拿到的是这个：

```
parse_file kind=drawing layers=8 dims=316 texts=127 v_groove=True     ← 解析本身是对的
to_match_inputs -> {"inner_width": 14362.15, "inner_height": 6151.80, "v_groove": true}
```

`14362×6152` 是**整张图纸的幅面**（`outline_size.source="document_extents"`），却被批 5 的
客户端当成了盒子的**内宽 / 内高**。两条口径各自都按自己的 Spec 实现，接起来才出问题：

| 侧 | 事实 |
| --- | --- |
| 服务（批 7 §2.4） | `outline_size` 取 `document.extents`，并按 Spec 标 `source="document_extents"` + warning `outline_from_extents`，明说「**不是成品内尺寸**」 |
| 服务（批 7 §2.4） | `annotated_dimensions` 是 `measured_value` 的**裸数字列表**（CAD IR 的 DIMENSION 行没有轴名） |
| 客户端（批 5 §2.4 第 2 条） | `outline_size` → `inner_length/inner_width/inner_height` 兜底；`annotated_dimensions` 只认带 `axis`+`value` 的 dict |
| 结果 | 尺寸类输入全错，且**看不出来**：`missing` 里没有 `inner_*`（客户端以为已经拿到了） |

今天不会因此算出错误报价，只因为案例库那 2 条本身 `needs_input`（排在可用案例之后）；**案例补齐
那天就会失真**。批 7 的提示词明令「不改批 5 客户端」，所以那一轮只登记不上手，本批专门收口。

## 1. 契约（`cpq_quick_quote_file.py`）

### 1.1 新增常量

```python
#: 图纸幅面类来源：这些 outline_size **不是成品内尺寸**，一律不得当内尺寸用。
SHEET_SIZE_SOURCES = ("document_extents",)
```

### 1.2 `_dimensions(fields, factor, warnings=None)`

在批 5 口径（标注内尺寸优先、外形兜底）之上加两条**收紧**，其余行为逐字不变：

1. **图纸幅面必须被拒**：`outline_size` 是 dict 且 `source` 命中 `SHEET_SIZE_SOURCES` →
   该 outline **一个尺寸都不用**，并往 `warnings` 记一条**含「图纸范围」**的说明
   （讲清它是整张图的幅面、不是内尺寸，要人工补）。
   `source` 缺失或为其它值 → 沿用批 5 行为（`length`/`width`/`height` 兜底），不得误伤。
2. **裸数字标注不猜轴**：`annotated_dimensions` 只认 `axis` + `value` 的 dict（批 5 口径）；
   元素是**裸数字**时不用它、也不猜是长还是宽，并往 `warnings` 记一条**含「轴」**的说明
   （讲清服务只回实测值、没有轴名）。

### 1.3 `to_match_inputs(parsed, *, fallback=None)`

- 出参形状**一个字不改**：`{"inputs", "missing", "sources", "warnings", "units_factor",
  "match_input_keys"}`；
- 尺寸一个都没拿到时，`inner_length` / `inner_width` / `inner_height` 必须如实出现在 `missing` 里
  （不得用幅面顶、不得静默）；
- 其它字段（`box_features` / `closure_type` / `v_groove` / 材料克重 / `fallback` / 冲突告警）行为不变。

### 1.4 不得做的事

- 不得把裸数字按顺序当成 长/宽/高（**那就是猜**）；
- 不得因为幅面不可用就把 `outline_size` 整键丢掉（那是服务的输出，客户端只决定**用不用**）；
- 不得改服务侧（`unified_parse.py`）的字段口径：它按 Spec 回 `source="document_extents"` 是对的，
  本批只改**消费方**。

## 2. 红测映射（`tests/test_quick_quote_parse_field_alignment_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：`SHEET_SIZE_SOURCES`、`_dimensions` 可调用、`to_match_inputs` 出参形状不变 |
| B | 幅面被拒：`inputs` 不含 `inner_*`、`missing` 含三者、`sources` 不含、warning 含「图纸范围」、幅面数值不出现在 `inputs` |
| C | 不误伤：无 `source` 的 outline 照旧（mm / cm）、非幅面来源照旧、带 axis 的标注仍优先 |
| D | 裸数字标注：不进 `inputs`、warning 含「轴」、带 axis 的 dict 不受影响 |
| E | 服务真实形状（批 7 §2.4 的 `outline_size` + 裸 `measured_value`）→ 不产出 `inner_*`，其它字段照旧 |
| F | 护栏：批 5 的 e3 / e4 / e5 / e10 等价行为逐条不回退 |

## 3. 非目标

- 不新增「按几何推内尺寸」的能力（那是技术工艺语义层的事）；
- 不改统一解析服务的字段清单与 `source` 取值；
- 不动差异价 / 案例库 / 费率权威化（批 3 / 6 / 8）。
