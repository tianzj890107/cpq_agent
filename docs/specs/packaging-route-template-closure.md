# 规格：权威实样盒型的工艺模板必须落在工序闭集内（工序名归一化 + 入库自检）

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_route_template_closure_red.py`（`Ran 15 OK`）
实现：由实现方在同批落地（`PROCESS_ALIASES` / `normalize_step_name` /
`da_seed_packaging.assert_step_names_mappable` / 部署脚本第 6b 步权威实样自检），
Spec 与红测字面常量未随之改动；收尾复跑见 `changelog/changelog_9_21_25.md` `## 258`。

血缘：承接 `packaging-process-route.md`（第 6 批：19 条工序闭集与 `validate_order`）、
`packaging-dwg-parts-extraction.md`（第 5/8 批：BOM 与零件）、
`packaging-product-outline-and-die-layer-roles.md`（第 4b 批：DWG 实样语义）、
`packaging-downstream-blockers-close-loop.md`（批 12：下游三处缝）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

让**从真实 DWG 实样导入的盒型**能像标准盒型一样把工艺路线确认下去：模板里的工序名在**构建路线时**
按一张显式的别名表归一化到 19 条 `PROCESS_CATALOG` 闭集内；闭集外又**没有**映射的名字在**入库时**
就被点名拒绝，绝不落成一个"永远不能确认"的盒型。

## 1. 现状缺口（实测证据，逐条可复现）

### 1.1 34 上真跑：权威实样盒型的路线永远 confirm 不了（P0）

项目 `325effd296a5`（酒盒.dwg 全流程演示，需求 `REQ-325EFFD296A5`）：

| 步骤 | 结果 |
| --- | --- |
| `POST /requirement/box-match` | `YT-DWG-WINE-700ML`（权威实样）`total_score=1.000`、`status=matched`、`can_confirm=true` |
| `POST /requirement/box-match/decision` | `confirmed`，`confirmed_box_type=YT-DWG-WINE-700ML` |
| `POST /requirement/packaging-bom` | 建成（`box_type_code=YT-DWG-WINE-700ML`） |
| `POST /requirement/packaging-route` | **200**，路线落库成 `draft` |
| `POST /requirement/packaging-route/confirm` | **409 `route_not_confirmable`**，`order_violations` 里 8 条 `unknown_process:*` |

后果链（真实发生）：路线不 `confirmed` → `packaging_cost.compute_project()` 报
`route_not_confirmed`（`packaging_cost.py:1484`）→ 成本算不出 → 报价侧拿不到包 →
**零件下游的每一件事都做不了**。为了把全流程跑完，只能把盒型**改确认成标准书型盒
`YT-RB-02001-A`**（该 note 逐字留在项目 `325effd296a5` 的 `box_match.note` 里）——
也就是说：图纸与盒型对得上，业务上正确的那条路是**走不通**的。

### 1.2 本机复现（同一份样本模板名，不依赖 34）

把样本导入脚本写进知识库的 8 道工序（`scripts/tmp_import_dwg_cases.py` 的 `PROCESS`
原文：`材料开料 / 面纸印刷与覆膜 / 模切/半穿 / V槽 / 裱贴包面 / 内盒成型 / EVA与托件制作 /
总装与检验`）放进 `kb_packaging_process_template`，跑 `build_route` + `confirm_route`：

```
工序: ['面纸印刷', '覆膜', 'EVA与托件制作', 'V槽', '内盒成型', '总装与检验',
       '材料开料', '模切/半穿', '裱贴包面', '面纸印刷与覆膜']
order_violations: ['unknown_process:EVA与托件制作', 'unknown_process:V槽',
                   'unknown_process:内盒成型', 'unknown_process:总装与检验',
                   'unknown_process:材料开料', 'unknown_process:模切/半穿',
                   'unknown_process:裱贴包面', 'unknown_process:面纸印刷与覆膜']
confirm → RouteError code=route_not_confirmable status=409
```

（`面纸印刷` / `覆膜` 两条来自需求表面字段展开，属于闭集内，所以它们没有进违规清单。）

### 1.3 根因

- 第 6 批的闭集（19 条）是**规格**；DWG 实样导入的模板是**现场文字**（"材料开料"这种车间说法），
  两者之间**没有任何对齐层**；
- 闭集外名字的唯一出口是 `validate_order` → `confirm_route` 409，于是盒型入库那一刻就注定了
  "永远不能确认"；入库路径上**没有自检**，谁也不知道这个盒型是废的，直到零件下游全部卡死；
- 复合名（`面纸印刷与覆膜`、`总装与检验`、`装配检验包装`）在闭集里对应**多道**工序，
  1:1 的映射装不下，必须有 1:N 的落法。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_route.py`
   - 新增模块级常量 `PROCESS_ALIASES: Dict[str, Tuple[str, ...]]`：闭集外模板名 → 闭集名列表
     （1:1 用单元素 tuple，1:N 用多元素 tuple），**只允许出现在这个常量里**；
   - 新增纯函数 `normalize_step_name(name: str) -> Tuple[str, ...]`：闭集内 → `(name,)`；
     别名表内 → 映射值；其它 → `()`（不猜、不兜底）；
   - `build_route_steps()` 在落库/返回**之前**把模板工序名逐个归一化：路线里的
     `step_name` 只能是闭集名，`step_no` 按 `PROCESS_CATALOG` 位次升序、去重；
   - `validate_order()` 的闭集判定与 `confirm_route()` 的 409 码/文案**一个字不改**。
2. `tech_app/backend/storage/da_seed_packaging.py`
   - 新增 `assert_step_names_mappable(rows: Iterable[dict]) -> None`：逐行取 `step_name`，
     `PROCESS_CATALOG` 与 `PROCESS_ALIASES` 都不认时抛
     `ValueError("unknown_process:<工序名>")`（点名到具体名字，不是"有不合法工序"）；
   - 样本/DWG 导入路径（`PROCESS_TEMPLATES` 的落库入口）必须先过这道自检。
3. `scripts/deploy_34_bare.sh`
   - 第 6b 步自检追加一条：对知识库里每个 `business_status='权威实样'` 的盒型跑
     `build_route` + `confirm_route`，任一失败非零退出（命令里必须出现 `权威实样` 字面量）。

## 3. 口径（逐条验收）

### 3.1 别名表

- 键**逐字**来自真实样本模板（本 Spec 登记 16 个：§1.2 的 8 个 + 圆盘盒的
  `纸张印刷覆膜 / 灰板与面纸模切 / 纸管成型切管 / V槽围边 / 围边裱贴 / 内托复合 /
  天地盖组装 / 装配检验包装`）；
- 值必须非空，且**每个元素都在 `PROCESS_CATALOG` 里**（不在 → 这条规格没实现完）；
- 映射目标是**业务口径**：实现方只许照抄已签字的映射表，不许为了让红测变绿随便指一个闭集名
  （红测只校验"覆盖 + 合法 + 可 confirm"，不校验具体映射到哪一道）。

### 3.2 构建后的路线

- 路线里出现的每个 `step_name` ∈ `PROCESS_CATALOG`；
- `order_violations == []`；同名工序不重复；`step_no` 严格递增；
- 归一化**只在构建时**发生：知识库、模板表、样本数据里的原始文字**不要求**改写
  （改写样本会掩盖问题，禁止）。

### 3.3 1:N 映射的工时

一条模板行映射出 N 道工序时：**第一道**沿用模板行的 `standard_seconds`，其余 N-1 道记 `None`
并进 `gaps.needs_standard_time`（不编工时、不摊分、不复制）。工时缺口照旧由既有缺口闭集披露。

### 3.4 权威实样自检

- 每个 `business_status='权威实样'` 的盒型：`build_route` 后 `order_violations` 为空且
  `confirm_route` 成功；
- 这条自检必须进部署脚本（§2.3），失败即非零退出 —— 不许等用户点到"确认路线"才发现。

## 4. 禁止事项

- 不许放宽闭集（不许把样本工序名加进 `PROCESS_CATALOG`）、不许在 `validate_order` /
  `confirm_route` 里加"白名单豁免"；
- 不许改写样本 / 知识库里的原始工序文字来绕过（问题在缺映射层，不在样本）；
- 不许给映射出的工序编工时、不许按 0 顶替；
- 不许改任何既有红测（含 `test_packaging_process_route_red.py` 的字面常量）；
- 不许改前端、不许改成本/报价口径、不许改数据库 schema；
- 不许 commit / push / tag / Release / 部署 / 重启服务 / 写生产库。

## 5. 验收

```bash
# 本批红测（实现前必须真的红）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_route_template_closure_red -v

# 不回归（冻结面）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_process_route_red      # 冻结口径
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_downstream_blockers_red
```

真样本（本机有转换器时；gated 用例）不变：
`CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -m unittest tests.test_packaging_product_outline_red -v`。

## 6. 本批不做（写在这里，别当成已通）

- 19 条闭集本身是否够用（例如纸管成型、复合/贴装、EVA 开料是否要成为正式工序）要业务签字，
  本批只要求"闭集外必须显式映射或拒绝入库"；
- 标准工时（`kb_packaging_process_template.standard_seconds`）的真值来源不在本批；
- 圆盘盒的 `纸管成型切管` 这类没有闭集对应的工序，映射由业务给（本批只要求形状与验收）。
