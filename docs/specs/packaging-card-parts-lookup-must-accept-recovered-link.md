# 报价卡片第 6 步的零件反查认不出「认回（recover）过」的技术项目：写侧写 `quote_session_id`，读侧只读 `source_session_id`

血缘：`packaging-parts-in-card-and-material-fill.md` §2.1.4（卡片第 6 步按会话反查技术项目再看零件）、
`quote-first-project-entry.md` §6（历史项目一次性恢复 `/api/projects/{id}/quote-link/recover`）、
`packaging-quote-close-loop.md`（回传通道把 `quote_session_id` 当来源会话用）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测，卡片侧反查只认 `business_case.source_session_id`）
红测：`tests/test_packaging_card_parts_lookup_link_key_parity_red.py`
本批 changelog 条目号：`## 444`（缺口类，作者侧）；落地条目见 `## 446`。

## 0. 一句话目标

报价卡片第 6 步「图纸拆出来的零件」有一条兜底：快照里没带零件表时，页面自己按会话号
反查技术项目、再读零件端点渲染。反查只认 `business_case.source_session_id`；
而**认回（recover）**那条既有出口写的是 `business_case.quote_session_id`
（回传通道 `packaging_handoff` 读的时候还要把 `quote_session_id` 映射成 `source_session_id`，
说明这两个键说的是**同一件事**）。于是"先在需求创建页建项 / 后来才把报价会话认回来"的项目，
卡片永远反查不到它 —— 兜底读件落空，用户在第 6 步**看不到拆出来的零件**。

## 1. 现场证据（34，2026-09-23，Codex 真跑）

- 链路：SM1 报价会话 `1bef04f7dab3` → PE1 技术项目 `8131f6d29d99`（`酒盒.dwg`）→
  `POST /api/projects/8131f6d29d99/quote-link/recover {"quote_session_id": "1bef04f7dab3"}` → 200；
- 该项目的 `business_case`（`GET /api/projects?scope=all`，SM1 与 PE1 读到的都一样）：

```json
{"entry_origin": "quote", "internal_test": false, "clues": ["source_task_id"],
 "business_case_id": "", "source_task_id": "", "source_session_id": "",
 "quote_session_id": "1bef04f7dab3", "create_new": false,
 "recovered_from_project_id": "8131f6d29d99", "recovered_by": "PE1",
 "recovery_reason": "全流程复跑：技术项目关联到 SM1 的报价会话"}
```

- 卡片页 `确认需求解析结果.html` 的反查函数 `resolveCardTechProject()`：

```js
const sid = bc.source_session_id || (p && p.source_session_id) || '';
return String(sid) === String(currentSessionId || '');
```

`source_session_id` 是空串 → 反查落空 → `ensureCardPackagingParts()` 直接 `return false`
（"还没有技术项目：安静跳过"），零件表既不进快照也不上屏。

- 该项目其实有 263 件零件：`GET /api/projects/8131f6d29d99/requirement/packaging-parts`
  → `part_total=263`、`parts_id=parts:c2f1579db133d9ba`。
- 对照：`tech_app/frontend/tech-task.js:296` 建项时写的是 `source_session_id`（从报价任务带入），
  所以"从报价卡片推任务 → 建项"这条路今天是对的；**认回**这条路不是
  （`grep` 前端：没有任何页面调用 `/quote-link/recover`，恢复动作只能由接口/集成通道发起）。

## 2. 口径（可直接验收）

1. 卡片侧反查**必须同时认两个键**：`business_case.source_session_id` **或**
   `business_case.quote_session_id`（`p.source_session_id` 这个旧位置也照旧保留）。
   两个键取到同一个会话号时行为完全一致 —— 反查结果只影响"读哪一份零件文档"，不引入第二套身份。
2. 命中之后的行为逐字不变：读 `GET /api/projects/{project_id}/requirement/packaging-parts`、
   按 `part_columns` 渲染成第 6 步的只读表；读不到就照旧安静跳过（不报错、不阻塞其它步骤）。
3. 写侧（`/quote-link/recover`）的线索键集合、`entry_origin` 判定、只写技术侧事实
   （**不建报价卡片**）都不动。
4. 冻结面：`resolveCardTechProject()` 的返回形状（字符串 project_id）、
   `ensureCardPackagingParts()` 的快照/端点口径、`packaging_parts` 表的列定义
   （唯一来源仍是后端 `CARD_COLUMNS`）、`FORMS` 里其它节的行为 —— 一个字不动。

## 3. 允许修改范围

- `确认需求解析结果.html`：`resolveCardTechProject()` 的会话命中判据（加 `quote_session_id`）。
- 禁止：改 `/quote-link/recover` 的落点语义、改 `tech-task.js` 的写侧键、改零件端点、
  在服务端补第二个身份字段、把"找不到项目"改成报错弹窗。

## 4. 红测分组（`tests/test_packaging_card_parts_lookup_link_key_parity_red.py`）

- **A 组 反查认回的项目（今天都是红的）**
  - A1 卡片反查的会话键集合必须含 `quote_session_id`；
  - A2 用 34 那份真实形状的 `business_case`（`source_session_id=""` + `quote_session_id` 有值）
    跑一遍判据：必须命中 `8131f6d29d99`；
  - A3 两个键都有值时仍命中同一个项目（不许二选一改变身份）。
- **B 组 护栏（今天就是绿的，不许被改红）**
  - B1 仍然认 `source_session_id`（旧卡片/任务建项那条路不许坏）；
  - B2 `POST …/quote-link/recover` 的线索键集合仍是
    `business_case_id / quote_session_id / source_task_id / source_session_id`，且不建卡片；
  - B3 `packaging_handoff` 仍把 `quote_session_id` 映射成 `source_session_id`（两个键同义的事实）；
  - B4 反查命中后读的仍是 `/requirement/packaging-parts`，列定义仍取自后端。

## 5. 验收

1. 34 上打开 `确认需求解析结果.html?session_id=1bef04f7dab3` 的第 6 步：
   即使快照没带零件表，也能按 `quote_session_id` 反查到 `8131f6d29d99` 并渲染 263 件零件；
2. 从报价卡片推任务建出来的项目（写 `source_session_id`）行为不变；
3. 没有技术项目的会话仍然安静跳过（不报错、不阻塞）。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_card_parts_lookup_link_key_parity_red -v
```

## 6. 红基（2026-09-23 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_card_parts_lookup_link_key_parity_red
  → Ran 7 tests … FAILED (failures=3, errors=0)
```

红的 3 条 = A1（反查键集合里没有 `quote_session_id`）、A2（认回过的项目判据不命中）、
A3（同上，两键都有值也走不到）；绿的 4 条护栏 = B1–B4。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 446`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 反查同时认两个键 | `确认需求解析结果.html` `resolveCardTechProject()` | 命中判据改成 `bc.source_session_id \|\| bc.quote_session_id \|\| (p && (p.source_session_id \|\| p.quote_session_id))`（旧位置 `p.source_session_id` 照旧保留）；两个键都命中时返回同一个 `project_id` |
| §2.2 命中后行为逐字不变 | 同上（未动） | 仍读 `GET /api/projects/{project_id}/requirement/packaging-parts`、仍按 `part_columns` 渲染、读不到仍安静跳过 |
| §2.3/§2.4 冻结面 | 未动 | `/quote-link/recover` 的线索键集合、`tech-task.js` 写侧键、零件端点、`CARD_COLUMNS`、`refresh_link` 语义都没碰 |
| 函数头注释同步 | 同上 | 顶部两处口径注释补上 `quote_session_id`（说明为什么两个键同义），代码判据与注释一致 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_card_parts_lookup_link_key_parity_red
Ran 7 tests ... OK            # 红基 3 红 4 绿 → 7 全绿

./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_in_card_and_material_fill_red
Ran 13 tests ... OK
```

红测自身缺陷：无（A1–A3 一次落地全绿，B1–B4 护栏未被改红）。
