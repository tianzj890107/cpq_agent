# 包装零件链路的**时间预算**：逐件诊断不许超线性、部署自检不许无限等（第 6 层）

血缘：承接 `packaging-parts-true-outline.md`（第 1 层真实轮廓）、`packaging-parts-outline-chaining.md`
（重复边折叠 + 外轮廓重判）、`packaging-parts-downstream-acceptance.md` §6.1（部署自检 6b）。

- 状态：Spec + 红测（已实现）
- 红测：`tests/test_packaging_parts_pipeline_time_budget_red.py`
- 依赖：`tech_app/backend/services/packaging_parts.py`、`packaging_part_solids.py`、
  `scripts/deploy_34_bare.sh` 第 6b 步

## 0. 一句话目标

零件链路的每一次"逐件计算"都要有**可验证的时间上界**，并且部署自检**有超时、有进度** ——
今天的问题不是"算得慢"，是**慢了以后没人知道卡在哪、也没有任何东西会停下来**。

## 1. 现状缺口（34 实测，可复现）

`## 264` 的重复边折叠/外轮廓重判上线后，34 上同一步出现两种结果：

```
# 34 部署 2b56e2d：scripts/deploy_34_bare.sh ytbz 全程 ~50s，第 6b 步（隔离端到端）~20s 打印：
· 酒盒.dwg：八步 8/8 completed；零件 64 件（closed_ratio=0.797）；可算 4 / 可挤出 1
· 圆盘盒.dwg：八步 8/8 completed；零件 9 件（closed_ratio=0.889）；可算 1 / 可挤出 7

# 34 部署 9f4fcfe：同一条命令，第 6b 步的 `$PY -` 进程
$ ps -o pid,etime,time,pcpu -p <pid>
    PID     ELAPSED     TIME %CPU
 521916       22:54 00:22:58  100      # 22 分 54 秒、纯 CPU、无任何输出
```

- 第 6b 步**没有内部超时**：它只能被外部（expect 的 1800s）杀掉，杀掉前既没有阶段输出，
  也无法判断卡在哪个样本、哪一件 —— "自检在跑"和"自检卡死"在日志里长得一模一样。
- 同一提交下 8010 **服务侧**跑同一份 `酒盒.dwg` 是正常的（并行会话实测八步 19.2s、64 件、
  `closed_ratio=0.938`）——所以这不是"图纸太难"，是**逐件诊断里存在一条超线性路径**，
  而且它只在自检那条路上被抓到：**两条路必须同一口径，慢必须慢得一样，快必须快得一样。**

### 1.1 定位到的最可疑热点（要由红测钉死，不许靠"看起来"）

`packaging_parts._outline_evidence()` 对**每一个分量**都要算 `_nearest_gap_mm()`（奇度顶点配对），
而 `_outline_evidence()` 在 `extract()` 里是**无条件**调用的（不管这件最后是不是 `open`）。
`9f4fcfe` 里 `_nearest_gap_mm()` 是"每次取最近的一对、移除后再重扫全部点对"，即

```
O(N) 个配对点 × 每轮 O(N^2) 点对扫描  ⇒  O(N^3)
```

真图一个分量有上千个奇度顶点（刀口开放链）时这一步就是分钟级到小时级。
本 Spec **不指定实现写法**，只钉"距离计算次数必须是线性的"这条可验证性质。

## 2. 诊断的复杂度上界（硬口径）

对**任意**分量，记 `N = len(odd_degree_vertices)`（奇度顶点数）：

1. `_nearest_gap_mm()` 的对点距离计算次数（`cad_geometry.distance()` 调用次数）
   必须 `<= 4 * N`（线性；常数 4 给实现留余量）；
2. 规模翻倍时调用次数不得翻两倍以上：`calls(2N) <= 2.2 * calls(N)`（禁止 `O(N^2)` 及以上）；
3. `N == 0` 时返回 `0.0` 且**一次距离计算都不做**；`N` 为奇数时多出的那个顶点必须被忽略
   （不许拿它去配一个不存在的点），返回的仍是已配对间隙的最大值。

## 3. 墙钟上界（可诊断，不许静默）

4. `packaging_parts.outline_diagnosis()` 的返回必须带 `elapsed_ms`（数值，毫秒）——
   预算是否被突破要**看得见**，而不是只能靠外部 kill；
5. 逐件诊断（`outline_diagnosis` / `extract` 里的同一段）单件预算 `TIME_BUDGET_MS = 2000`：
   超过时必须在 `outline_diagnosis` 里带 `budget_exceeded=True`，且 `outline_reason` 只能是
   闭集里的值（不许新增第 6 种），**不许抛异常、不许挂住**；
6. `packaging_parts.extract()` 对 `tests/fixtures/cad_ir/parts_panels.json` 这类夹具 IR
   必须在 **20 秒**内返回（本机、单进程、无网络的硬上界）；
7. `packaging_part_solids.extrude()` 单件必须 **<= 500 毫秒**（最坏形状：`MAX_POINTS` 个点的凸多边形）。

## 4. 部署自检第 6b 步：必须有超时、有进度

8. 第 6b 步的 python heredoc **必须被超时包裹**（`timeout <秒数>` 或等价的 `SECONDS` 判据），
   超时预算不得大于 **900 秒**；
9. 超时**必须以非零退出**，并把"卡在哪个样本"打进输出（`|| fail "<样本>：超时"` 等价物），
   不许把超时算成通过、也不许只打印一个无主的 `timeout`;
10. 每个样本处理完**立即输出**（`print(..., flush=True)`），不许把两个样本的输出憋到进程结束 ——
    否则"卡在第一份样本"和"卡在第二份样本"在日志里无法区分。

## 5. 两条路径同一口径

11. 同一份合成 IR，`extract()` 里那条路径得到的 `nearest_gap_mm` 与直接调
    `outline_diagnosis()` 必须**逐字一致**（同一份诊断不许有第二份算法）；
12. `summarize()` 输出的 `closed_ratio / processable_ratio / solid_ok_ratio` 与逐件行一致
    （自检读的就是它；指标口径不许两处）。

## 6. 红测

`tests/test_packaging_parts_pipeline_time_budget_red.py`（A 组复杂度用**调用计数**，不看钟表；
B/C 组用夹具与静态检查，都是本机可复现的）：

| 组 | 例子 | 现在为什么红 |
| --- | --- | --- |
| A | A1 `N=100` 奇度顶点：距离计算次数 `<= 400`；A2 `calls(200) <= 2.2 × calls(100)`；A3 `N∈{0,1}` 零计算 | `_nearest_gap_mm` 是 `O(N^3)`：N=100 时约 16.6 万次调用 |
| B | B1 单件预算常量 `TIME_BUDGET_MS=2000` 存在且为正；B2 `outline_diagnosis` 带 `elapsed_ms`；B3 夹具 IR `extract()` 20s 内返回；B4 最坏形状 `extrude()` <= 500ms | 现在没有 `TIME_BUDGET_MS`、没有 `elapsed_ms` |
| C | C1 6b 被 `timeout <= 900` 包裹；C2 超时非零退出且点名样本；C3 每样本 `flush=True` 立即输出 | 现在 6b 无超时、无逐样本 flush |
| D | D1 `extract()` 与 `outline_diagnosis()` 的 `nearest_gap_mm` 逐字一致；D2 `summarize()` 与逐件行一致 | D 组现在可能已绿（护栏，防回归） |

## 7. 禁止事项 / 不变面

- 不许改已经写在 `packaging-parts-outline-chaining.md` §2 的：闭合件结论逐字保持、
  `OUTLINE_OPEN_REASONS` 闭集、`OUTLINE_BBOX_COVER_RATIO = 0.95`；
- 不许在 `packaging_parts` 里调模型或联网；不许新增第二种"成本/工艺可算"判据；
- 不许为了让红测转绿改 `tests/`（含本文件对应红测）或改两份真实 DWG 样本；
- 深度优先的环搜索预算（`MAX_LOOP_STATES = 20000`、`MAX_LOOP_CYCLES = 256`）与
  `loop_budget_exhausted` 的口径不变。
