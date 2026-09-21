# 零件自检要能**指着原因说话**：不可算/不可挤出必须按第一原因汇总，两份样本对等达标

血缘：承接 `packaging-parts-downstream-acceptance.md` §2/§3（指标与样本门槛）、§6.1（部署自检 6b）、
`packaging-parts-material-attribution.md`（材料/厚度归属）、`packaging-parts-solid-coverage.md`（挤出覆盖率）。

- 状态：Spec + 红测（未实现）
- 红测：`tests/test_packaging_parts_selfcheck_diagnostics_red.py`
- 依赖：`tech_app/backend/services/packaging_parts.py`（`summarize`）、`scripts/deploy_34_bare.sh` 第 6b 步

## 0. 一句话目标

自检失败时**必须一眼看出断在哪一环**，而不是只有一句"没有一件能跑工艺"；
并且**两份样本是对等的验收对象** —— 一份好、一份全灭，就是没通过。

## 1. 现状缺口（34 实测，`0d8884d` 部署自检输出）

```
· 酒盒.dwg：八步 8/8 completed；零件 64 件（closed_ratio=0.938）；可算 9 / 可挤出 6
· 圆盘盒.dwg：八步 8/8 completed；零件 9 件（closed_ratio=0.889）；可算 0 / 可挤出 0
   · 代表件 DWG-P01：outline_status=closed size_source=closed_outline 挤出=unsupported
{"isolated_downstream_selfcheck": "failed",
 "problems": ["圆盘盒.dwg：没有一件能跑工艺", "圆盘盒.dwg：没有一件能挤出 3D"]}
```

- `圆盘盒.dwg` 的 9 件**轮廓是闭合的**（`closed_ratio=0.889`），可算却是 0 —— 说明断在
  `processability()` 的另外两个条件（材料 / 厚度），但自检**一个字都没说**是哪一个，
  只能靠人再去逐件翻 `GET /requirement/packaging-parts`；
- `挤出=unsupported` 同样只有一个词，`reason`（`thickness_unknown` / `concave_polygon` /
  `too_many_points` …）没有汇总，无法直接判断是"归属断了"还是"几何不支持"；
- 这不是"某一份样本不重要"：自检的**意义**就是两份样本都跑，一份全灭必须拦住发车
  （这一点当前门禁做到了：`"isolated_downstream_selfcheck": "failed"` → 脚本非零退出）。

## 2. 指标（`packaging_parts.summarize()` 新增两把账）

`summarize(doc)` 必须返回（与现有 `open_reason_mix` / `attribution_kind_mix` 同风格）：

| 键 | 定义 |
| --- | --- |
| `unprocessable_reason_mix` | `{code: 件数}`：对**每一件**取 `processability(row)["code"]`，只统计 `ok == False` 的件；`ok` 的件不进这个账 |
| `solid_reason_mix` | `{reason: 件数}`：对每一件取挤出结论的 `reason`（`status == "ok"` 的件按 `""`/`ok` 计，且必须能对上） |

两条不变：
1. `sum(unprocessable_reason_mix.values()) == part_total - processable_total`
   （分母口径与 `processable_ratio` 完全一致，不许两处各算一套）；
2. `part_total == 0` 时两个 mix 都是 `{}`（不是 `null`、不抛错）。

## 3. 部署自检必须打印这两把账

第 6b 步每个样本处理完，必须**当场**打印（`flush=True`）：

```
· 圆盘盒.dwg：八步 8/8 completed；零件 9 件（closed_ratio=0.889）；可算 0 / 可挤出 0
   · 不可算原因：PACKAGING_PART_MATERIAL_UNKNOWN×9
   · 不可挤出原因：thickness_unknown×9
```

要求：
- 原因按**件数降序**、同数按 `code` 字典序（同一份数据两次跑逐字相同）；
- `unprocessable_reason_mix` 为空时不打印这一行（不许打印空行或 `None`）；
- 打印的是 `summarize()` 的同一份账（不许在脚本里重算一遍）。

## 4. 两份样本对等（门禁口径不变，写清楚）

- 每份样本都必须 `closed_ratio >= 0.10`、`可算 >= 1`、`可挤 >= 1`（`packaging-parts-downstream-acceptance.md` §3）；
- 任一份为 0 → 自检 `failed`、脚本**非零退出**（现状已如此，本条是护栏，防"为了发车放宽门槛"）；
- 样本缺失/跑不动（异常）与"跑完但没件"必须是**两种不同的失败文案**，不许都叫"没通过"。

## 5. 红测

`tests/test_packaging_parts_selfcheck_diagnostics_red.py`：

| 组 | 例子 | 现在为什么红 |
| --- | --- | --- |
| A | A1 `summarize()` 有 `unprocessable_reason_mix`；A2 与 `processable_ratio` 分母一致；A3 空文档给 `{}` | 现在没有这两个键 |
| B | B1 每件的 `processability().code` 在闭集内；B2 `solid_reason` 闭集；B3 mix 排序确定（跑两次逐字相同） | B1/B2 现在可能已绿（护栏）；B3 依赖 A |
| C | C1 6b 步打印 `不可算原因`；C2 打印 `不可挤出原因`；C3 用 `flush=True`；C4 两个样本的打印必须在同一份账上（脚本里出现 `unprocessable_reason_mix`） | 现在脚本里没有这两行 |
| D | D1 两份样本门槛仍是 `可算 >= 1` / `可挤 >= 1`（静态钉住 + 非零退出） | 现在已绿（护栏，防放宽） |

## 6. 禁止事项 / 不变面

- 不许改 `processability()` 的判据（闭合 + 材料 + 厚度）与 `PACKAGING_PART_*` 错误码字面；
- 不许改 `packaging-parts-material-attribution.md` 的归属链，也不许为了让 `圆盘盒` 有可算件
  而在自检里兜底造材料/厚度；
- 不许改 `packaging-parts-downstream-acceptance.md` §3 的样本门槛（只许更严，不许放宽）；
- 不许改 `tests/`（含本文件对应红测）或两份真实 DWG 样本。
