# 空特征零件回退到「plate / box / cylinder 空模板」：Spec / 红测口径（第二批，范围已按用户口径收窄）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_base_geometry_fallback_red.py`

## 0. 范围变更（以此为准，覆盖此前报告里的第二批 + 第三批）

原来的第二批（基础几何初始化 + `initialize_base_feature` + Agent 询问尺寸 + 上传 STEP +
「不需要 CAD」）和第三批（零件分类 `cad_requirement` / `make_or_buy`、标准件 / 外购件 /
电气件 / 柔性件、`not_required` 正常跳过、按分类的 工艺 / 成本 / BOM 完整性规则）
**按用户口径收窄为一条**：

> 没有 `feature` 的零件，自动回退成「plate / box / cylinder 的空模板」：用户只能在这三种
> 类型里选一个、只填这个类型固定那几个尺寸字段（默认空）。填完就保存、单件重生成；
> 没填就留空，等人工或 Agent 补。

不做的（明确写进本 Spec，代码里出现即视为越界）：

| 不做的东西 | 原因（用户口径） |
| --- | --- |
| `cad_requirement` / `make_or_buy` / `geometry_representation` | 物理表/数据库维护成本高，不加分类字段 |
| `not_required` / 「该零件不需要 CAD」 | 不需要这个功能 |
| 标准件 / 外购件 / 电气件 / 柔性件的区分 | 不需要 |
| 「P005 这类线缆组件默认待确认是否需要 CAD」 | 不要这个状态，直接就是「缺字段 → 空着 → 待补」 |
| 工艺 / 成本 / BOM 按零件分类使用不同完整性规则 | 不需要 |
| 上传供应商 STEP 替代简化几何 | 本批不做（既有 3D 导入链路不变） |
| 用户自由新增特征 / 自由新增字段 | 自由度越低越好；只允许三种基体模板的固定字段 |

## 1. 问题

`P-005 输出线缆组件`（型号 XT30）的 `features = []`：

- 参数编辑器（`tech_app/frontend/app.js:1841`）只遍历 `(part.features || []).forEach(...)`，
  features 为空 ⇒ 右侧只剩「名称 / 数量 / 材料 / 保存零件参数 / 重新生成零件」，
  **没有任何基体类型选择与尺寸输入框**，用户点了保存/重生成也永远修不好这个零件；
- 后端编辑服务（`tech_app/backend/services/part_edit.py:66`）只允许改**已有**特征的**已有**数值
  字段，`features == []` 时任何 `feature_index` 都会得到「特征序号 1 不存在」；
- Agent 的 `UpdatePartParameters` 复用同一份 `part_edit`，因此 Agent 同样修不了（也不会问用户，
  因为工具 schema 里根本没有「补基体」这个动作）。

再加上第 1 批（`docs/specs/tech-cad-batch-partial-generation.md`）之后，这个零件会稳定显示为
「待补」而不是「整批失败」—— 但**待补必须真的能补**，这一批就是把「补」的路打通。

## 2. 术语

- **基体特征**：`features[0]`，只允许 `plate`（length / width / thickness）、
  `box`（length / width / height）、`cylinder`（diameter / height）。
- **空模板**：类型已选、尺寸输入框为空 —— 不是「造一个 0 尺寸的实体」，也不是「猜一个尺寸」。
- **待补**：字段为空即待补；由第 1 批的结构化 code 表达（`BASE_FEATURE_MISSING` /
  `BASE_FEATURE_DIMENSION_MISSING` / `BASE_FEATURE_INVALID_TYPE`）。

## 3. 契约

### C1 —— 前端：features 为空时渲染固定模板，而不是空白参数区

在 2.1 参数编辑器（`currentIsImg` 分支）里，`!part.features.length` 时渲染一个受控的
**基体编辑器**，且必须满足：

- 顶层常量 `const BASE_FEATURE_TYPES = ["plate", "box", "cylinder"];`（**唯一**的基体白名单，
  与后端 `part_edit.BASE_FEATURE_FIELDS` 的键集一致；不得放别的特征类型）；
- 容器 `class="parameter-base-feature"`；
- 类型选择 `data-base-type`，选项只能来自 `BASE_FEATURE_TYPES`（plate / box / cylinder）；
  不得出现 `hole` / `hole_pattern` / `fillet` / `chamfer`；
- 尺寸输入 `data-base-dim="<字段名>"`，字段只用该类型固定模板：
  `plate → length,width,thickness`；`box → length,width,height`；`cylinder → diameter,height`；
  切换类型时按模板重渲染，默认全空；
- 一句说明，含「没有可建模特征」与「留空即待补」的意思，并给出三种类型的中文标签
  （板件 / 长方体 / 圆柱体）；
- 不出现任何「添加特征」「自定义字段」入口（禁止自由加字段与特征）。

已有的 `保存零件参数` / `重新生成零件` 按钮沿用，不新增按钮。

### C2 —— 前端：保存把模板写进 `features[0]`

`savePartEdits()` 在遇到基体编辑器时，读出 `[data-base-type]` 与各 `[data-base-dim]`，
组装 `{type, ...尺寸}` 写入 `part.features[0]`（`features` 为空时先补成 `[基体]`），
仍走既有的 `PUT /api/projects/{id}/ir` 保存；`重新生成零件` 仍只调
`POST /api/projects/{id}/parts/{part_id}/regenerate`。不新增接口。

留空语义：留空的尺寸保持空（`null`，即待补），**不得**填 0、不得填默认值、不得自动补全。

### C3 —— 后端服务：受控的基体初始化 / 替换（UI 与 Agent 共用同一份）

`tech_app/backend/services/part_edit.py` 新增两个能力（函数名可自定，语义必须一致），
两个入口（老 `workbench-chat`、2.1 页 Agent）都只能走这一份实现：

```python
BASE_FEATURE_FIELDS = {
    "plate": ("length", "width", "thickness"),
    "box": ("length", "width", "height"),
    "cylinder": ("diameter", "height"),
}

def initialize_base_feature(part, *, feature_type, dimensions) -> tuple[list[dict], bool]
def replace_base_feature(part, *, feature_type, dimensions) -> tuple[list[dict], bool]
```

规则（服务端强制，不依赖模型自觉）：

1. `initialize_base_feature` **仅当 `part.features == []`**；已有特征时抛 `PartEditError`，
   提示改用替换。
2. `replace_base_feature` **仅当 `features[0].type` 不属于三种基体**（例如首个特征是 `hole`）；
   首个特征已经是合法基体时抛 `PartEditError`（不许把好基体随便换掉）。
3. `feature_type` 只接受 `plate` / `box` / `cylinder`（大小写与空格归一），其余一律拒绝，
   错误信息里必须列出允许的三种。
4. `dimensions` **必须恰好是该类型的必填字段**：缺一项就拒绝；多给模板外的字段
   （例如 box 里塞 `diameter`）也拒绝 —— 不允许自由加字段。
5. 每个尺寸必须是**正数**（缺失 / 0 / 负数 / 非数值字符串全部拒绝）。
6. 替换只动 `features[0]`，`features[1:]` 原样保留。
7. 变更清单写 `features[0]` 的 before/after；返回的 `geometry_changed` 为 `True`。
8. 不落盘、不审计（与 `apply_edit` 一致，由调用方负责保存版本与审计）。

### C4 —— 空值不再当错误（留空 = 待补）

`apply_edit()` 的 `feature_updates`：某项的 `value` 为 `None` 或空串时**跳过该项**
（既不报错也不写入），其余校验不变：非数值字符串仍然拒绝、`≤0` 仍然拒绝、越界
`feature_index` 仍然拒绝、白名单外的 `field` 仍然拒绝。

理由：三选一模板默认就是空的，用户只填他手上有的尺寸；「还有几格没填」不能变成一个
保存错误，它应该是「待补」状态。

### C5 —— 单件重生成：只影响这一个零件

`POST /api/projects/{pid}/parts/{part_id}/regenerate` 的行为保持不变：
只生成该零件的 3D / 2D 并 `_upsert_part` 进既有结果文档；其他零件在 `geometry` /
`drawings` 文档里的条目**一个字节都不动**；不触发整批、不影响其他零件的成败。

### C6 —— Agent：同一份服务 + 不许编造尺寸

- `UpdatePartParameters` 增加封闭的 `base_feature` 入参：
  `{"type": "plate|box|cylinder", length?, width?, thickness?, height?, diameter?}`，
  类型用 `enum` 固定三选一，**没有任何自由字段名**；
- 只在「用户明确给出简化外形的类型与全部尺寸」或「用户明确要求把非法基体换成某类型」时调用；
  用户没给数值时**必须先把要哪些尺寸问清楚**，不得按常识补全、不得填默认值；
- 工具实现复用 C3 的同一份 `part_edit` 能力（禁止在 `oc_agent.py` 里再写一套校验）；
- 缺尺寸 / 非法类型 / 非法字段名 → 工具返回 `{"error": …}`、`applied: false`，IR 不得被改；
- 成功 → 走既有 `save_ir(stage="agent_edited")` + `store.audit("agent_part_edit")`，
  并沿用既有的 `requires_regeneration` 回执，由界面重生成该零件。

### C7 —— 与第 1 批的状态码对齐

补齐尺寸后，`BASE_FEATURE_MISSING` / `BASE_FEATURE_DIMENSION_MISSING` /
`BASE_FEATURE_INVALID_TYPE` 这些结构化问题自然消失；没补的仍然按原样显示为待补。
本批**不新增**状态码，也不改第 1 批的 code / repair 词表。

### C8 —— 不做分类（明确禁止）

不得新增 `cad_requirement`、`make_or_buy`、`geometry_representation`、
`cad_not_required` 等字段；不得引入「不需要 CAD」的跳过语义；不得按零件分类改变工艺 /
成本 / BOM 的完整性规则。「缺字段」永远只是「缺字段」。

## 4. 允许修改范围

- `tech_app/frontend/app.js`（参数编辑器的基体模板 + `savePartEdits` 写回 + 静态资源版本号）；
- `tech_app/frontend/index.html`（`app.js?v=` 版本号）；
- `tech_app/backend/services/part_edit.py`（C3 + C4）；
- `tech_app/backend/services/oc_agent.py`（`UpdatePartParameters` 的 schema 与实现，C6）；
- 必要时 `tech_app/backend/main.py` 里与 `part_edit` 对接的两处（`workbench_chat` 的
  `WorkbenchPartEdit` / `_apply_workbench_chat_edit`），并同步其请求模型；
- 既有注释与文案可在本批内微调。

## 5. 禁止事项

- 不得放开「随意新增特征 / 随意新增字段」；只允许三种基体模板的固定字段。
- 不得在 `features` 非空且首个特征是合法基体时替换基体。
- 不得删除或弱化 `blocks_feature_edit()`（导入 STEP/STP 项目仍然禁止改 IR）。
- 不得让留空变成 0 / 默认尺寸 / 自动猜尺寸。
- 不得新增分类字段或「不需要 CAD」语义（见 C8）。
- 不得为通过测试而放宽 `tests/test_tech_cad_batch_partial_generation_red.py`（第 1 批）与
  `tests/test_tech_backend_capability_preservation_red.py` 的路由集合。
- 不得改 `regenerate` 的语义与其他零件的结果条目。

## 6. 验收（红测清单）

红测文件：`tests/test_tech_base_geometry_fallback_red.py`

1. `features == []` 时 `initialize_base_feature` 能建立 box（长度/宽度/高度写入、变更清单、`geometry_changed=True`）。
2. `features` 非空时 `initialize_base_feature` 被拒绝（提示改用替换）。
3. 类型只允许三种：`hole` / 空 / 未知类型一律拒绝，错误信息列出允许的类型。
4. 模板外的额外字段被拒绝（box + `diameter`）。
5. 必填尺寸缺失 / 0 / 负数 / 非数值字符串全部拒绝。
6. `cylinder` 只需要 `diameter` + `height` 即可建立。
7. `replace_base_feature`：首个特征为 `hole` 时可换成 `box`，`features[1:]` 保留。
8. 首个特征已是合法基体时 `replace_base_feature` 被拒绝。
9. `apply_edit` 的 `value` 为 `None` / 空串时跳过且不报错；非数值与 `≤0` 仍然拒绝。
10. Agent 路径：`_update_part` 带完整 `base_feature` 能落库（IR 变化 + 版本快照 + 审计）。
11. Agent 路径：`base_feature` 缺尺寸 → 返回 error、`applied: false`、IR 未被修改。
12. Agent 路径：`base_feature.type = hole` → 同样被拒绝。
13. `UpdatePartParameters` 的 schema：`base_feature.type` 是 `enum` 三选一，描述里含
    「不得编造尺寸 / 缺参数先问用户」的要求。
14. 前端：`features` 为空时渲染 `parameter-base-feature` + `data-base-type` + `data-base-dim`，
    且选项只有 plate / box / cylinder（不含 hole / fillet / chamfer / hole_pattern）。
15. 前端：`savePartEdits()` 读模板并写 `part.features[0]`；留空保持空。
16. 前端：`app.js` 的静态资源版本号已 bump（`index.html` 不再是旧 `?v=`）。
17. 单件重生成只动该零件：其他零件在几何 / 2D 文档里的条目原样保留。
18. 边界：导入 STEP 项目仍禁止改 IR；本批未引入任何分类字段。

## 7. 测试命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_tech_base_geometry_fallback_red -v
node --check tech_app/frontend/app.js
python3 -m unittest discover -s tests -p 'test_*.py'
```
