# 规格：BOM 面板上的「解析不到材料码」要给出**原因与补齐办法**

依赖：`docs/specs/packaging-business-material-code-map.md`（§C3 已在接口上给出
`gaps.material_unresolved_detail` 的 `reason` + `action`，以及
`business_material_rows.reason_counts` / `map_hit_total` / `map_source` / `map_fingerprint` /
`map_unavailable`；§6 边界 4 明写"前端不改：缺口与账都已在 `GET .../requirement/packaging-bom` 里，
界面接入是下一批"——本批就是那一批）、`docs/specs/packaging-silent-degradation-disclosure.md`。

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/requirement-confirm.js:443-444` 的 BOM 面板
只把那批行名拼成一句 ——
`解析不到材料码（已在库外）：<item_key>、<item_key>…`，这句话有两处硬伤：
① **"已在库外"是猜的**（后端从没这么说过；`## 421` 的口径是"可能只是映射表里没写 / 写了没生效 /
映射表读不到"，三档互斥）；② **没有动作** —— 看到这句话的人不知道该改哪一处；
同一份响应里后端已经给了 `gaps.material_unresolved_detail`（逐条 `reason` + `action`）与
`business_material_rows` 那把账（映射命中几行、映射表读不到时的稳定码与 message），
前端**一个都没用**（`material_unresolved_detail` / `reason_counts` / `map_hit_total` /
`map_unavailable` 在前端源码里 0 处））
红测：`tests/test_packaging_material_unresolved_panel_red.py`
行号基线：HEAD `a97fa58`

## 0. 一句话目标

BOM 面板上"解析不到材料码"这件事：**说清几行、逐行给原因、逐行给补齐办法**；
映射表读不到时用后端的 message 显形；**不许再猜"已在库外"**。

## 1. 现状缺口（代码级）

1. `pbPanel()`（`:390`）里那一段（`:443-444`）：
   - 只读 `gaps.material_unresolved`（`item_key` 清单），丢掉 `gaps.material_unresolved_detail`；
   - 文案断言"已在库外"（后端没有这个说法，且三档原因互斥）；
   - 没有动作（该补映射表？该修映射表？该重算？）；
2. `record.business_material_rows` 那把账（`reason_counts` / `map_hit_total` / `map_source` /
   `map_fingerprint` / `map_unavailable`）在面板上没有任何落点：映射给了几行看不见，
   映射表读不到时界面上与"业务还没给映射"长得一模一样（静默降级）。

## 2. 契约

### C1 四个纯函数（`node -e` 可抽出真跑；体内无 DOM / `fetch(` / `storage`）

- `pbMaterialReasonLabel(reason)` → 人话标签，**闭集认三档，其余一律"原因未知"**：

  | 输入 | 返回 |
  | --- | --- |
  | `map_key_missing` | `映射表里还没有这条原文` |
  | `map_entry_not_applied` | `映射表里写了，但这一版没生效` |
  | `map_unknown` | `映射表读不到，这次没能判定` |
  | 其它 / 空 / 非字符串 | `原因未知（后端没有给出原因档）` |

  未知档**不许**被说成上面任一种（三档互斥是后端口径，前端不猜）。

- `pbMaterialUnresolvedRows(gaps)` → 数组 `[{key, label, action}]`：
  - 优先用 `gaps.material_unresolved_detail`（逐条 `item_key` / `reason` / `action`）：
    `key` 取 `item_key`（**空项跳过**，不许造无名行）、`label` = `pbMaterialReasonLabel(reason)`、
    `action` = 后端 `action` 逐字（trim）；
  - 没有 `detail`（旧 BOM）时退回 `gaps.material_unresolved`：逐项 `{key, label: 原因未知档,
    action: ""}`（**退回不等于编原因**）；
  - `gaps` 不是对象 / 两个键都不是数组 → `[]`；
  - 行数只由真数据决定（不许拿 `stats` 或计数键凑行）。
- `pbMaterialUnresolvedHint(gaps)` → `""`（没有行）或 `解析不到材料码 N 行：下面逐条给出原因与补齐办法。`
  （N = `pbMaterialUnresolvedRows(gaps).length`）——**全文不许出现"库外"**。
- `pbMaterialMapNote(scope)`（`scope` = `record.business_material_rows`）→ `""` 或一句：
  - `map_unavailable` 非空 → 用**后端 message 逐字**说读不到（并说明这次的材料码只按既有规则解析）；
  - 否则 `map_hit_total > 0` → `材料码映射表命中 N 行`；
  - 否则 `""`（没话说就别说）。

### C2 面板渲染（`pbPanel()`）

- 那一段（`:443-444`）替换为一整块：`data-pb-material-unresolved="<行数>"` 的块，
  逐行 `data-pb-material-unresolved-key="<item_key>"`，行内渲染 `key` + `label` + `action`
  （`action` 非空才渲染，逐字）；
- 另加 `data-pb-material-map="1"` 的备注块，内容 = `pbMaterialMapNote(record.business_material_rows)`
  （空则整块不渲染）；
- 既有 banner（混盒型 / 盒型未知 / 包围盒 / 过期 / 零件文档读不到）与 `pbRow()` **一字不动**。

### C3 冻结面

- `gaps.material_unresolved` 仍是"哪些行没解析出来"的**唯一**依据：前端不许自己按
  `material_code` 重算清单、不许把 `detail` 当成新的行来源去改 `pbRow()`；
- 不新增接口、不改后端、不改 `index.html`、不加依赖；不改 `tests/` 下任何文件。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：四个纯函数 + `pbPanel()` 那一段替换；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许再写"已在库外"（也不许换一个等价的猜测词，如"库里没有"）；
- 不许把未知 `reason` 归到已知三档；不许在 `action` 为空时自己编一句动作；
- 不许把 `map_unavailable` 的 message 重新措辞（逐字给用户）；
- 不许改后端与 `## 421` 的口径；不许为了转绿改任何 `tests/` 文件；
- 不连 PG / 34、不写业务数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_unresolved_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_material_code_map_red tests.test_packaging_bom_business_material_rows_red \
  tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_business_part_basis_in_panel_red \
  tests.test_packaging_parametric_bom_red
```

## 6. 已记录的边界

1. 面板只**显示**原因与动作，不提供"在这里填映射"的输入框（映射是仓库里的 JSON，改它要过
   Git 评审，不是页面上的字段）；
2. `map_fingerprint` / `map_source` 本批不上界面（面板放不下也不该放），留在接口上供回查；
3. 老 BOM（没有 `detail`）退回清单时**只**显示行名与"原因未知"，提示重新生成 BOM 后可见原因；
4. 与材料**价格**无关：缺价仍是 `packaging-cost-gaps-closure.md` §1.1 的待办；
5. 其它行业的 BOM 面板仍在 `industry === 'packaging'` 之外不挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

## 7. 落地状态（2026-09-22，Codex 实现）

**实现前红基**（`git stash push -- tech_app/frontend/requirement-confirm.js` 后的原文）：

```
Ran 31 tests ... FAILED (failures=27)
```

27 红 = A1–A4（四个纯函数根本不存在）、B1–B7、C1–C3、D1–D5、E1–E5（面板里 0 处
`data-pb-material-unresolved` / `data-pb-material-map`）、F2（源码里仍有那句猜测）、
F3 / F4；4 绿为守卫（E6 既有 banner 未被改动、E7 面板不发请求、F1 `pbRow()` 未变、
G1 `node --check` 通过）。

**实现后**：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_material_unresolved_panel_red -v
Ran 31 tests ... OK
node --check tech_app/frontend/requirement-confirm.js   # 退出码 0
```

落点（只改了 `tech_app/frontend/requirement-confirm.js` 一个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 | `pbMaterialReasonLabel()` / `pbMaterialUnresolvedRows()` / `pbMaterialUnresolvedHint()` / `pbMaterialMapNote()`（包装 BOM 面板 IIFE 内，`pbRow()` 之前） | 四个纯函数，体内无 DOM / `fetch(` / `storage`；三档原因闭集在函数体内就地声明（为了能被 `node` 单独抽出真跑，规则只写一次） |
| §C2 | `pbPanel()`：原 `:443-444` 那句 `解析不到材料码（已在库外）：…` 整段替换为 `data-pb-material-unresolved="<行数>"` 块（逐行 `data-pb-material-unresolved-key="<item_key>"` + `label` + 非空 `action`）+ `data-pb-material-map="1"` 备注块 | 行数 / 行名 / 原因 / 动作**全部来自后端载荷**；既有 banner 与 `pbRow()` 一字未动 |
| §C3 | 无 | 未新增接口、未改后端与 `index.html`、未加依赖；`gaps.material_unresolved` 仍是唯一清单依据（前端不按 `material_code` 重算） |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_material_code_map_red tests.test_packaging_bom_business_material_rows_red \
  tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_business_part_basis_in_panel_red \
  tests.test_packaging_parametric_bom_red
Ran 140 tests ... OK

# 同文件（`requirement-confirm.js`）的其它面板批次，逐个复跑
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_box_candidate_runnability_panel_red tests.test_packaging_cost_route_version_read_failure_red \
  tests.test_packaging_cost_rule_routing_red tests.test_packaging_downstream_block_code_http_red \
  tests.test_packaging_handoff_audit_pending_panel_red tests.test_packaging_process_route_red \
  tests.test_packaging_authority_workbook_upload_red tests.test_packaging_handoff_audit_relay_red
Ran 195 tests ... OK
```

边界（与 §6 一致，实现如约未越）：没有"在面板上填映射"的输入框；`map_source` /
`map_fingerprint` 未上界面；老 BOM 退回清单时只给行名 + 未知档；映射表读不到时
`map_unavailable.message` 逐字转达并说明本次仍按既有分词规则解析；材料价格仍不涉及。
