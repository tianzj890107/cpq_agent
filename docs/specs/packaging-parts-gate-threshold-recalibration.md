# 规格：零件门禁的真样本门槛按「绝对分子地板」重标定（门禁与它自己的红测不能再给相反的裁决）

血缘：`docs/specs/packaging-parts-downstream-acceptance.md` §3（**门槛值写进那一份 Spec，改门槛必须改那份文件**）
与 §4（六项门禁清单）、`docs/specs/packaging-parts-list-visibility-and-kinds.md` §6（**分母口径变了以后的裁决**：
「**修法只有一条判据**：把『按比值标定』的门槛换成**绝对分子地板**。比值分母本来就是『文档里有几件』（§2.1），
它会随分组口径变；绝对分子才是『能力有没有退步』」—— 四条测试已按此落地，本批**沿用同一条**判据）、
`docs/specs/packaging-parts-outline-chaining.md` / `packaging-parts-component-chaining.md`（kept 件从 64 → 263 / 312，
分母就是从这里变的）。

状态：Spec + 红测（已实现）（原状：`tech_app/tools/packaging_parts_gate.py --env local` 实测
`verdict=no_go`、`summary={ok:4, fail:1, manual:1}`，唯一失败项 `parts_outline_real_sample` 的原因逐字是
「圆盘盒.dwg：role_known_ratio=0.029 < 门槛 0.10」；而同一台机上同一批件的**绝对分子**是
`role_known_total=9`（旧分母 64 时是 8 —— `packaging-parts-list-visibility-and-kinds.md` §6 的表里逐字记着
`0.125（8/64）→ 0.029（9/312）`）：能力没有退步，退的是分母。同一条判据在**测试侧**已经改成绝对分子地板
（`tests/test_packaging_parts_downstream_gate_red.py::FRealSampleThresholds::test_f2`），**门禁脚本没跟着改**，
于是同一个输入上工具说 fail、它自己的红测说 pass，`DEPLOYMENT.md` 还写着「L2 成立」—— 三处互相打架）
红测：`tests/test_packaging_parts_gate_threshold_red.py`
行号基线：HEAD `c9a203c`

## 0. 一句话目标

门禁的**真样本门槛**换成不受分母影响的**绝对分子地板**（与 `## 308` §6 已裁决的那条判据同一把尺子），
读数行同时打出「比值 + 绝对分子 + 分母」，让「圆盘盒 role_known 9 件（旧 8 件，没退步）」这种事实
既不被分母稀释成 fail，也不被比值掩盖成 ok。

## 1. 现状缺口（实测，不是推断）

```bash
./open-claude/.venv/bin/python tech_app/tools/packaging_parts_gate.py --env local
# summary: {ok: 4, fail: 1, manual: 1, skip: 0} / verdict: no_go / reasons: [parts_outline_real_sample]
#   parts_outline_real_sample: "圆盘盒.dwg：role_known_ratio=0.029 < 门槛 0.10"
#   metrics: "酒盒.dwg closed_ratio=0.510 role_known_ratio=0.000 可算 13 可挤出 13（应用内转换器）"
#            "圆盘盒.dwg closed_ratio=0.817 role_known_ratio=0.029 可算 92 可挤出 255（应用内转换器）"
```

同一天、同一份零件文档的**绝对读数**（探针实测，`part_total` 来自 `summarize()`）：

| 样本 | `part_total` | `closed_total` | `role_known_total` | `processable_total` | `solid_total` |
| --- | --- | --- | --- | --- | --- |
| `酒盒.dwg` | 263 | 134 | 0 | 13 | 13 |
| `圆盘盒.dwg` | 312 | 255 | **9**（旧分母下是 8） | 92 | 255 |

三条缺口：

1. **门槛的刻度错了**：`THRESHOLDS`（`packaging_parts_gate.py:53`）写的是
   `{"酒盒.dwg": {"closed_ratio": 0.10}, "圆盘盒.dwg": {"closed_ratio": 0.50, "role_known_ratio": 0.10}}` ——
   两份样本都按**比值**判。分母（文档里有几件）`## 308` 之后从 64 涨到 263 / 312，比值跟着掉，
   **分子一分没掉**。`role_known_ratio` 因此把「9 件有角色」判成不合格；
2. **读数行只说比值**：`:252` 的 metrics 行只打 `closed_ratio=` / `role_known_ratio=`，
   看得见 0.029、看不见「9 件」—— 用户无法判断是能力退步还是分母变大；
3. **三处互相打架**：门禁说 `no_go`，测试侧同判据（`round(ratio × part_total) >= 8`）说 pass，
   `DEPLOYMENT.md` L2 那一行写「成立（本机实测 0.797 / 0.889）」（那是更早一版分母下的读数）。

## 2. 契约

### C1 门槛形态：只允许「绝对分子地板」

`THRESHOLDS` 的值**只能是**计数键（`*_total`），**不许**出现任何 `*_ratio` 键：

| 样本 | 地板（逐字） |
| --- | --- |
| `酒盒.dwg` | `closed_total >= 7` |
| `圆盘盒.dwg` | `closed_total >= 32` 且 `role_known_total >= 8` |

- 数字的来源是**同一把尺子**：`ceil(原比值 × 原分母 64)` —— 酒盒 `0.10 × 64 = 6.4 → 7`、
  圆盘盒 `0.50 × 64 = 32`；`role_known_total` 取 `## 308` §6 已裁决的 **8**（旧分母下的实测分子，
  比 `ceil(0.10 × 64) = 7` 还严一档）。**没有一个是新定的数**；
- **不许双通道**：`ratio >= x or total >= y` 这种写法会让「分母被压小」重新变成通过路径，
  本批一律禁止（判据只有一处：`*_total` 与地板比较）；
- `closed_total` / `role_known_total` **由 `summarize()` 已有的分母与比值还原**（`role_known_total =
  round(role_known_ratio × part_total)`；`summarize()` 没有 `role_known_total` 键，本批**不新增引擎键**，
  口径与 `## 308` 的测试侧一致）。

### C2 读数行：比值 + 绝对分子 + 分母

`_sample_metrics()` 的返回体补 `role_known_total`（见 C1）；`_check_outline_real_sample()` 的
`metrics` 行**必须**同时给出三样，缺一不可：

```
酒盒.dwg closed_ratio=0.510 closed_total=134/263 part_total=263 role_known_total=0 可算 13 可挤出 13（应用内转换器）
```

（顺序不强制，但四类读数 `closed_ratio` / `closed_total` / `part_total` / `role_known_total` 都必须在。）

### C3 裁决：纯函数 `sample_verdict(name, metrics, floors=None)`

- 顶层纯函数（可被 `import` 直接调、**不读文件、不转换、不联网**），返回失败原因清单（`[]` = 过）；
- 判据**只有两段**：① 逐个地板比 `*_total`（缺失按 0 算）；② 既有两条不变式
  （`processable_total >= 1`、`solid_total >= 1`，文案逐字不变）；
- 失败文案用「地板」并把依据点出来：`<样本>：<键>=<实测> < 地板 <地板值>（Spec packaging-parts-downstream-acceptance.md §3）`；
  **不许**再写「门槛 0.10」这类比值话术；
- `_check_outline_real_sample()` 改为调用它（`floors` 缺省取 `THRESHOLDS[name]`），
  `fail`/`ok`/`skip` 与 `metrics` 的形状逐字不变。

### C4 三处口径同步（本批必做，缺一不可）

1. `docs/specs/packaging-parts-downstream-acceptance.md` §3 的表按 C1 改写（同值、同形状），
   并把依据写成 `## 308` §6 那条裁决；
2. `tech_app/tools/packaging_parts_gate.py` 的 `THRESHOLDS` 与 C1 逐字一致；
3. `DEPLOYMENT.md` 的 L2 行改成按绝对地板读数（写出本轮实测的 `closed_total` / `part_total` /
   `role_known_total`），并把旧的比值读数删掉。

### C5 冻结面

- 六项 `GATE_ITEMS` 的 id 与顺序、`GATE_STATUSES`、`GATE_VERSION`、输出形状
  （`gate_version` / `env` / `items` / `summary{ok,fail,manual,skip,acknowledged}` / `verdict` / `reasons`）、
  退出码（go → 0 / no_go → 1 / 未知 `--ack` → 2）、`manual` 项**绝不**自动 `ok`、
  `production` 下 `skip` 记 `reasons`（`local` 下不记）—— 逐字不变；
- 门禁**只读**纪律不变（不连库、不写文件、不调模型、不联网；转换产物仍只落临时目录）；
- 不改零件引擎：`summarize()` 的键**不增不减**、`closed_ratio` / `role_known_ratio` 的定义与值一个数不动；
- 不改前四层口径与红测、不改 `tests/` 下任何文件（含本批红测）。

## 3. 允许修改范围

1. `tech_app/tools/packaging_parts_gate.py`（`THRESHOLDS`、`_sample_metrics()`、新增 `sample_verdict()`、
   `_check_outline_real_sample()` 的裁决与 metrics 行）；
2. `docs/specs/packaging-parts-downstream-acceptance.md` §3（门槛表按 C1 改写 + 依据一行）；
3. `DEPLOYMENT.md` 的 L2 行；
4. 本 Spec 与它的红测；`changelog/changelog_9_21_25.md` 追加一条。

## 4. 禁止事项

- 不许为了让门禁变绿去动分子（不改零件引擎、不改角色识别、不扩大整盒兜底、不删件）；
- 不许双通道判据，不许把地板换成比值，也不许把地板调到实测值以下（地板只能 ≥ C1 表里的数）；
- 不许把 `manual` 项自动置 `ok`、不许把 `production` 的 `skip` 当通过；
- 不许改六项 id、输出形状、退出码；不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署；
- 不许改 `tests/` 下任何文件。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_gate_threshold_red -v   # 12 条全绿
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_downstream_gate_red      # 不回退
./open-claude/.venv/bin/python tech_app/tools/packaging_parts_gate.py --env local
#   期望：parts_outline_real_sample = ok；summary.fail = 0；verdict = go（parts_demo_script 仍是 manual）
```

## 6. 已记录的边界

- 本批只换刻度与读数，**不改能力**：圆盘盒 `role_known_total = 9` 这个绝对水平仍然很低
  （被识别角色的件只占 2.9%）；要让 L2 的「角色可识别」更硬，得另开一批提升角色识别率
  （图层规则 / 分量聚合），那批会**同时**抬高这里的分子与比值；
- 地板按「旧分母下实测到的分子」定，属**不许往下调**的下限；以后分母再变，门槛不再跟着变；
- `--env production` 的行为（`skip` 记 `reasons`、`manual` 待签字）本批未动。

## 7. 落地状态（2026-09-23，Codex 实现）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 地板 | `tech_app/tools/packaging_parts_gate.py` 的 `THRESHOLDS` | `{"酒盒.dwg": {"closed_total": 7}, "圆盘盒.dwg": {"closed_total": 32, "role_known_total": 8}}` —— 比值键清零，数字来源与依据写在常量上方的注释里 |
| §C1 分子 | 同文件 `_sample_metrics()` | 补 `role_known_total = round(role_known_ratio × part_total)`（由 `summarize()` 自己的分母/比值还原，**不新增引擎键**） |
| §C2 读数 | 同文件 `_check_outline_real_sample()` 的 metrics 行 | 改为 `closed_ratio=… closed_total=134/263 part_total=263 role_known_total=0 …`（比值 + 绝对分子 + 分母） |
| §C3 裁决 | 新增顶层纯函数 `sample_verdict(name, metrics, floors=None)` | 只比 `*_total` 与地板（缺失按 0），另加既有两条不变式（`processable_total >= 1` / `solid_total >= 1`）；文案用「地板」并点名依据 Spec；`_check_outline_real_sample()` 改调它 |
| §C4 同步 | `docs/specs/packaging-parts-downstream-acceptance.md` §3 + `DEPLOYMENT.md` L2 行 | 门槛表换成同三个绝对地板并写明依据（`## 308` §6）；L2 行改成按绝对地板读数（134/263、255/312、圆盘盒角色 9 件） |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_gate_threshold_red -v
Ran 12 tests ... OK        （红基：Ran 12 ... FAILED (failures=7)，5 条绿为 B3 + C1–C4 冻结守卫）

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_downstream_gate_red tests.test_deploy_isolation_root_red
Ran 25 tests ... OK

./open-claude/.venv/bin/python tech_app/tools/packaging_parts_gate.py --env local
# verdict=go / summary={ok:5, fail:0, manual:1, skip:0} / reasons=[] / exit 0
#   parts_outline_real_sample: "两份样本过门槛：酒盒.dwg closed_ratio=0.510 closed_total=134/263
#     part_total=263 role_known_total=0 可算 13 可挤出 13（应用内转换器）；圆盘盒.dwg
#     closed_ratio=0.817 closed_total=255/312 part_total=312 role_known_total=9 可算 92 可挤出 255（应用内转换器）"
```

修前原文（本批红基）：`verdict=no_go`、`summary={ok:4, fail:1, manual:1}`、`reasons=[parts_outline_real_sample]`、
失败原因逐字 `圆盘盒.dwg：role_known_ratio=0.029 < 门槛 0.10`。

边界（与 §6 一致）：只换刻度与读数，**没动能力** —— 圆盘盒 `role_known_total = 9` 这个绝对水平仍然很低
（2.9%），要更硬得另开一批把角色识别率抬上去（那批会同时抬高分子与比值）；`parts_demo_script` 仍是
`manual_unacknowledged`（本批未代签、未改 `manual` 语义）；`--env production` 的 `skip → reasons` 与退出码
逐字未动；未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 部署。
