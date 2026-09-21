# 人工录入/确认的需求字段必须能让图纸链路门禁转绿

血缘：承接 `packaging-drawing-semantics.md`（§5 字段来源与看板）、`packaging-downstream-blockers-close-loop.md`
（§1.3 人工来源但值为空）、`drawing-flow-error-taxonomy.md`。

- 状态：Spec + 红测（已实现）
- 红测：`tests/test_packaging_manual_field_confirmation_red.py`
- 依赖：包装语义第 4 批（`packaging_semantics/provenance.py`）、图纸链路第 5 批（`packaging_drawing_flow/gates.py`）

本批解决**「人正常填进去的需求值，永远开不了门禁」**这一处缝。有 34 线上实测证据，不是推断（§1）。

## 0. 一句话目标

让人工录入 / 人工确认的字段成为**能与图纸证据并列的确认依据**：门禁判据认它，字段看板也如实写它；
同时**不放宽**任何既有拒绝口径（值为空、冲突证据、单位未确认一律照旧挡住）。

## 1. 现状缺口（34 实测，可复现）

项目 `f1417060ae9d`（酒盒.dwg），需求单由工艺经理在 1.1 录入 `inner_length=200 / inner_width=150 /
inner_height=80 / closure_type=天地盖`，`field_sources` 记为 `manual`，然后跑一键解析图纸。
写完 `field_write` 之后的真实状态：

```
requirement.data.inner_length            = "200"          # 人在 1.1 填的值，还在
requirement.data.field_sources           = {"inner_length": "manual", ...}
requirement.data.field_provenance.inner_length
        = {"origin": "missing", "status": "missing", "value": null, "confidence": 0.0, ...}

GET /api/projects/f1417060ae9d/drawing-flow
  gates.stages.box_match.status = "blocked"
  blocking = [{"code": "field_unconfirmed", "field": "inner_length",
               "message": "内长尚未确认，确认后才能进行该步骤", "source": "field_provenance"}, ...]
```

`bom / route / cost / quote_publish` 依次全部 blocked：**值明明在，门禁却说"尚未确认"，且没有任何界面动作能改它。**

### 1.1 代码事实（两处，缺一不可）

1. `packaging_semantics/provenance.py:118-133`（人工确认分支）用的是
   `entry = dict(previous) or _entry_snapshot(candidate)` + `entry.setdefault("origin", "user_confirmed")`
   + `entry.setdefault("status", "confirmed")`。而 `_entry_snapshot(candidate)` **已经带了**
   `status="missing"` / `origin="inferred_from_geometry"`，`setdefault` 抢不到 → 人工确认的字段
   **永远升不到 `confirmed`**，看板上也永远不是人工口径。
2. `packaging_drawing_flow/gates.py:42-51 _is_confirmed()` 要求
   `status == "confirmed"` **并且**（`field_sources == "manual"` 或 `origin == "user_confirmed"`）。
   这是两条**互相独立**的证据路径被写成"与"：图纸证据那条能自己满足 `status`，人工那条永远不行。

### 1.2 反方向同样不可达：图纸已确认的字段也开不了门禁

`_is_confirmed()` 的实现把两条**互相独立**的证据路径写成了「与」：

```python
if status != "confirmed":
    return False
return source == "manual" or origin == "user_confirmed"
```

于是：

- 人工填的字段：`source == "manual"` 成立，但几何看板给的是 `status="missing"` → 卡在第 1 行；
- 图纸已确认的字段：`status == "confirmed"` 成立，但来源是 `attachment`、`origin="confirmed_from_cad"`
  → 卡在第 2 行。

**两条路各自都走不通**，唯一能满足的是「人工来源 + 图纸把值确认下来」这个窄组合
（`provenance.py:141-149` 的补值分支）。红测 B5 把这条钉住：四个字段全为图纸已确认时，
`box_match` 仍必须是 `open`。

### 1.3 为什么「点一下确认」也救不了

全仓 `packaging_semantics.apply_to_requirement(accept=...)` 的**唯一业务调用点**是
`packaging_drawing_flow/steps.py:378`，而它恒传 `accept=()`（`steps.py:405`）。也就是说
「人工把某个候选字段确认下来」这条通道在业务代码里**根本不存在**，
用户没有任何合法动作能让一个字段变成 `status="confirmed"`。

## 2. 口径（本批要定的三条）

### 2.1 门禁判据（唯一口径）

`_is_confirmed(field)` 为真，当且仅当**二者之一**成立：

1. **图纸/模型证据已确认**：`provenance[field].status == "confirmed"`；或
2. **人工录入/确认且有值**：`data[field]` 有值（`_has_value` 口径，`0` / `False` 算有值）
   且（`field_sources[field] == "manual"` 或 `provenance[field].origin == "user_confirmed"`）。

必须保持不变的拒绝口径：

- `data[field]` 为空（`None` / 空白串 / 空容器）→ 仍然 `field_missing`，**不许**因为写了 `manual` 就放行；
- `status == "conflict"` 或看板 `board == "conflict"` → 仍然 `field_conflict`，人工来源也不例外；
- 单位未确认时图纸侧绝对尺寸不许 `confirmed`（第 4 批冻结口径，本批不动）。

### 2.2 字段看板（与门禁同一口径）

人工录入/确认的字段，`field_provenance[field]` 必须落成 `origin="user_confirmed"`、
`status="confirmed"`、`value` 为该字段当前值，并把本次图纸候选追加进 `alternatives`。
**不许**出现"看板说 missing、门禁说 unconfirmed、值又是人工填的"这种三处不一致。

### 2.3 人工确认通道（可写）

必须存在一条**业务可调用**的确认路径，把选定字段写成 2.1 第 2 条的形状；
`accept` 不能永远是空集。第一版允许的最小形态：`apply_to_requirement(accept=(...))`
被 `field_write` 或一条专用路由以真实字段集调用。写法由实现方定，但必须能被红测看见。

## 3. 验收（红测逐条对应）

- A 组：`provenance` 分支行为（升级 / 不覆盖值 / 非人工不许升级 / `status=confirmed` 的既有路径不变）；
- B 组：真实门禁行为（人工口径 → `box_match` open；空值 → `field_missing`；冲突 → `field_conflict`）；
- B5：图纸已确认（`status=confirmed` 且来源不是人工）也必须开门禁 —— 今天同样红；
- C 组：源码契约（`provenance.py` 的人工分支必须显式写死 `status`/`origin`；
  `accept` 必须有一条非空调用路径）；
- D 组：回归护栏（第 4 批 `packaging_semantics`、第 5 批 `packaging_drawing_flow` 红测全绿不变）。

### 2.4 实现记录：与既有红测 `drawing_flow_red::C8` 的一处**真冲突**（实现方按本 Spec 落地，已上报测试侧）

本 Spec §2.1 的第 1 条（图纸/模型证据已确认 → 门禁开放）与 `tests/test_packaging_drawing_flow_red.py`
的 `CGates::test_c8_user_confirmation_opens_the_blocked_stages` **在同一份输入上互相矛盾**：

| | `C8` 的 before 状态（实测打印） | 本 Spec B5 的夹具 |
| --- | --- | --- |
| `data[field]` | `70.0 / 40.0 / 120.0 / "tuck"` | `200.0 / 150.0 / 80.0 / "天地盖"` |
| `field_sources` | 四个字段全 `attachment` | 四个字段全 `attachment` |
| `field_provenance[field]` | `status="confirmed"`、`origin="confirmed_from_cad"`、`evidence_level="STRONG"` | 同左（只差 `confidence` 与 `evidence_refs` 的具体值） |
| 期望 | **blocked**（"字段还没人工确认时不许开放盒型匹配") | **open**（"图纸已确认这条路径不许被本批改坏"） |

两边的 `provenance` 形态逐键一致，且 C8 那份还带着**更多**证据（flow 看板 `board="written"`、
`ir_id/ir_hash`、真实 anchor）—— 换句话说，不存在任何"更可信才放行"的判据能把两者分开。
本批按 Spec §2.1 落地（**§1.2 明写这处"与"逻辑是缺陷、B5 把反方向钉住**），因此 C8 的 before
断言由绿转红。测试侧的**一行修法**（本批不动 tests/）：把 C8 的 before 夹具改成"字段已写入但
`status != "confirmed"`"（例如 provenance 用 `origin="inferred_from_geometry"` / `status="missing"`），
这样 before=blocked（field_unconfirmed）、after=open 的故事仍然成立，两套红测即可同时全绿。

## 4. 命令与期望

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_manual_field_confirmation_red -v   # 先红后绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_red                      # 59 OK (skipped=1)
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red                   # 53 OK / 1 FAILED（C8，见 §2.4）
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_requirement_state_red           # 17 OK
```

## 5. 本批不做

- 不改「已提交/已审批需求不可静默改写」的纪律（那是 `drawing-flow-non-editable-requirement.md` 的范围）；
- 不改 `GATE_REQUIRES` 的字段清单；
- 不引入"字段级审批人"这类新概念；`origin` / `status` 仍用既有闭集。
