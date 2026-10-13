# 包装业务部件可见编号采用稳定身份码

## 1. 背景

图纸解析产出**两套**各自形如 `DWG-BP%02d` 的编号：

- `display_code`：`order_business_parts_by_regions()`（`tech_app/backend/services/packaging_parts.py`）按**版面阅读顺序**（包围盒顶边降序、左边升序）重排后产生，只写 `display_code` / `sequence_no`。
- `business_part_code`：解析器按**发现顺序**产生（`tech_app/backend/services/packaging_business_part_resolver.py`），是点击、URL、落库、面板标题用的**稳定身份**。

左栏列表渲染取 `display_code`（`tech_app/frontend/app.js` 的业务件行），而工艺面板头取 `business_part_code`（`tech_app/frontend/inline-analysis.js` 的 `part_id · 件名`）。两处口径不同 → 同一件在列表里是 `DWG-BP20 贴牌`，点开的工艺面板头却是 `DWG-BP17 · 贴牌`，看起来"零件和工艺序号对不上"。

## 2. 契约

- **C1 可见编号取身份码**：业务部件列表行与 `openPackagingBusinessPart()` 面板标题的编号一律取 `business_part_code`；不再取 `display_code`。
- **C2 身份码不动**：`business_part_code` 的赋值、排序、落库、下游绑定**一律不改**；`order_business_parts_by_regions()` 保持"只改展示序号、不改稳定业务身份"，`display_code` 仍照旧产出并留在文档里（本批只是不再把它放进可见文案）。
- **C3 一件一个号**：同一件的列表号 == 工艺面板头号 == 请求 URL 里的号。
- **C4 列表顺序不变**：列表仍按文档数组顺序（版面阅读顺序）排列；本批只改行上显示的**编号文本**，不改排序。

## 3. 不做

- 不做"身份号改成版面顺序"（即 `business_part_code` 重编号）：那会改动稳定主键、使下游按码落库的工艺/成本/BOM/料厚记录失联，现行契约（`docs/specs/packaging-layout-first-auto-selection-and-size-consistency.md`）明确禁止。
- 不新增接口、不改后端、不改 `display_code` 生成逻辑。

## 4. 验收

- `tests/test_packaging_business_part_identity_code_red.py` 全绿；
- 既有前端接线护栏不回归：`tests/test_packaging_batch_real_execution_red.py`、`tests/test_drawing_board_two_column_parts_and_3d_red.py`、`tests/test_packaging_layout_first_auto_selection_red.py`。
