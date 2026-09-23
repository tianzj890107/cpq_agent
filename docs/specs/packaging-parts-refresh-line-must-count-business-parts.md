# Spec：左栏那句「零件清单已刷新：N 件」必须按**业务部件**口径报数

状态：Spec + 红测（已实现）（2026-09-23 由并行会话落地：`app.js` 新增纯函数
`packagingPartsRefreshLine()`，`refreshPackagingPartsAfterDrawingFlow()` 改按**业务账优先**报数
（有业务部件就报业务部件，没有才退几何账并自称"几何区域"）；本机实测 `Ran 10 … OK`）
红测：`tests/test_packaging_parts_refresh_line_business_count_red.py`
血缘：`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md` §2.7（几何分量只进折叠诊断区、
不作结果呈现）、`docs/specs/packaging-two-ledgers-reconciliation.md`（两笔账同屏对账：几何账与业务账
不许混成一个数字）、`docs/specs/packaging-parts-entry-readback.md`（重进 2.1 必须把已落库的零件读回来，读回来的口径就是这个数）。
本批 changelog 条目号：`## 484`（落地条目同为 484，见 changelog）。

## 0. 用户原话（2026-09-23）

> 而且不应该显示这个 零件清单已刷新：263 件（点一行可在右栏看这一件） 因为下面是 业务部件 28 件
> （从图纸推导（待人工确认））… 那就应该显示 28 件

左栏实际同屏出现的内容（用户贴的是真实现场）：

```text
零件清单已刷新：263 件（点一行可在右栏看这一件）      ← 与下面自相矛盾
业务部件 28 件（从图纸推导（待人工确认））
几何区域 263 个 → 业务部件 28 件（已定位 26 件）
识别 25 · 推断 1 · 待确认 2
部件图：28 件都没配到部件图（这一版清单的图没有归属）。
```

## 1. 实测证据（HEAD `872898b` 工作副本只读）

| 读数 | 实测 |
| --- | --- |
| `app.js:3848-3856` `refreshPackagingPartsAfterDrawingFlow()` | `const rows = Array.isArray(doc && doc.parts) ? doc.parts.length : 0;` → `status(\`零件清单已刷新：${rows} 件（点一行可在右栏看这一件）\`)` —— 这里的 `doc` 是**几何零件文档**（`/requirement/packaging-parts`，`doc.parts` 是 263 个几何分量） |
| 下面那句「业务部件 28 件」 | 走 `renderPackagingBusinessTree()` → `packagingBusinessPartRows(currentPackagingBusinessParts)`，来源是**业务部件文档**（28 行） |
| 为什么口径不同 | 一句话取自几何账、一句话取自业务账；`## 480` 已经规定"几何分量只进折叠诊断区、不作结果呈现"，但这句刷新提示**没跟上**，于是同一屏出现 263 与 28 |
| 真样本读数（与线上一致） | 酒盒：几何分量 **263**、业务部件 **28**（已定位 26）；`识别 25 · 推断 1 · 待确认 2` 是业务账的三档分子 |
| 影响面 | 只在前端一句文案：不涉及接口、后端、数据；但它是**用户看到的第一句话**，直接决定"到底解析出几件" |

## 2. 契约

### 2.1 C1：新增纯函数 `packagingPartsRefreshLine(geometryDoc, businessDoc)`（只出文案）

按**业务账优先**的顺序取数：

1. `packagingBusinessPartRows(businessDoc).length > 0` →
   `零件清单已刷新：业务部件 {n} 件（点一行可在右栏看这一件）`
   —— 这个数就是左栏结果区的行数，**同一个数**；此时文案里**不许出现几何分量数**。
2. 否则（业务账为空）几何分量数 `geometryDoc.parts.length > 0` →
   `零件清单已刷新：几何区域 {m} 个（还没推导出业务部件，点一行可查看）`
   —— 几何账必须**自称几何区域**，**不许**再写成"零件清单 …件"。
3. 两个都是 0 → 返回 `""`，由调用方保留既有空态文案
   （`零件文档还没有内容，详见上方步骤表与前置条件。`）。

非对象入参一律当 0 处理；函数体内不许出现 `document.` / `window.` / `fetch(` / `localStorage`。

### 2.2 C2：`refreshPackagingPartsAfterDrawingFlow()` 必须走它

- 该函数不再自己用 `doc.parts.length` 决定那句文案，改为
  `const line = packagingPartsRefreshLine(doc, currentPackagingBusinessParts); if (line) status(line); else status(空态文案);`
- 业务部件文档在这一步必须是**新鲜**的（`## 480` 的就地补推导已经保证：跑完链路后业务部件文档已落库）；
  业务账暂时读不到时**不许**硬编 28 —— 走 §2.1 第 2 条（几何区域口径），并保持"还没推导出业务部件"的说法；
- 三档句（`识别 a · 推断 b · 待确认 c`）与对账句（`几何区域 m 个 → 业务部件 n 件`）一个字不动。

### 2.3 C3：反向判据（防"数字碰巧对上"）

- 业务账非空时，刷新句里的数字**只允许**等于业务部件行数；把几何账数字塞进同一句要能测出来；
- 几何账非空、业务账为空时，句子里必须同时出现"几何区域"与那个几何数字（不许沉默、也不许把它叫零件）；
- 两个账都读不到时不许编数字（返回 `""`）。

## 3. 本批不做

- 不改左栏结果区的表头（`业务部件 N 件（来源）`）、不改三档句与对账句；
- 不改接口与后端：`/requirement/packaging-parts` 与 `/requirement/packaging-business-parts` 各管各的；
- 不改 `refreshPackagingParts()`（它照旧刷几何文档）；
- 不把 263 / 28 这类数字写死在前端任何地方（数字必须来自文档）。

## 4. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_refresh_line_business_count_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_2_1_result_parts_and_shape_only_red -v   # 27 OK 不回退
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_frontend_wiring_red -v               # 12 OK 不回退
```

## 5. 红测清单（`tests/test_packaging_parts_refresh_line_business_count_red.py`）

- A 组（纯函数真跑）：A1 业务 28 + 几何 263 → 报 28 且**不含 263**；A2 只有几何 → 自称几何区域；
  A3 两空 → `""`；A4 业务优先（几何为 0 也走业务口径）；A5 入参非对象不崩；
  A6 纯函数纪律（不碰 DOM / 网络）；
- B 组（接线守卫）：B1 `refreshPackagingPartsAfterDrawingFlow()` 走 `packagingPartsRefreshLine(`；
  B2 该函数不许再单独用 `doc.parts.length` 拼那句文案；B3 空态文案逐字保留；
  B4 纯函数体里 `packagingBusinessPartRows(` 出现在 `.parts` 之前（业务账优先的顺序是判据）。

纪律：只读源码 / CSS + `node -e` 抽具名函数真跑；不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。

## 6. 落地状态（2026-09-23，Codex 实现）

| 项 | 实测 |
| --- | --- |
| 本批红测（`Ran 10`） | **OK**（红基：HEAD 工作副本 + 本红测 = `Ran 10 … FAILED (failures=8)`，另 2 条为护栏） |
| 反向对照 | 关掉 `refreshPackagingPartsAfterDrawingFlow()` 里的 `packagingPartsRefreshLine(` 调用 ⇒ `Ran 10 … FAILED (failures=1)`（只红 B1：接线守卫；A 组纯函数仍在，说明本批的判据确实落在"接线"上） |
| 改动文件 | `tech_app/frontend/app.js`：新增纯函数 `packagingPartsRefreshLine(geometryDoc, businessDoc)`（业务账优先 → `零件清单已刷新：业务部件 N 件…`；否则几何账 → `零件清单已刷新：几何区域 M 个…`；都 0 → `""`），`refreshPackagingPartsAfterDrawingFlow()` 改走它；空态文案逐字保留 |
| 保护网 | 同一批三份红测 + 4 个既有模块：`Ran 32 … OK`、`Ran 60 … OK` |
| 未动 | 业务账 / 几何账各自的取数与行渲染（本批只改那一句刷新文案的来源） |
