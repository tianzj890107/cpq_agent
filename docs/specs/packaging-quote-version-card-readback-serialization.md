# 规格：报价版本读回必须可 JSON 序列化（卡片第 5 步读历史不得 500）

状态：Spec + 红测（已实现）（`## 311` 在读回路径（`cpq_packaging_quote._fetch_versions()`）加了一层
类型归一：`datetime`/`date` → ISO 字符串、`Decimal` → `int`/`float`；`cpq_wf.quote_version_state()`
与 `cpq_suite_server` 的 `packaging_quote_versions` / `latest_quote_version` 一个字没改，
也没有给 `_send_json()` 加 `default=`。复跑：本批红测 `Ran 8 OK`、`test_packaging_quote_version_persistence_red` + `test_packaging_semantics_red` `Ran 67 OK (skipped=1)`；
34 上的真机复验（`GET /wf/card/step-data?...&step_no=5` → 200）待部署后重放）
红测：`tests/test_packaging_quote_version_readback_red.py`

血缘：承接 `packaging-quote-version-persistence.md`（`## 275` 落了版本表与第 5 步读回键）、
`packaging-quote-draft-and-card-visibility.md`（第 5 步要看得见历史版本）、
`e2e-packaging-dwg-quote-tech-continuity.md`（报价 → 技术工艺 → 回传 → 回报价的整条闭环）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

把"做完报价、回传成功、再点开卡片第 5 步"这条路上的**最后一个 500** 去掉：
`quote_version_state()` 的返回值必须是**能直接 `json.dumps`** 的普通 JSON 数据
（时间 → 字符串、`numeric` → 数字），**不许**靠改 `_send_json` 加 `default=` 蒙过去。

## 1. 现状缺口（2026-09-22 34 上真跑实测，逐条可复现）

### 1.1 有版本可读时，卡片第 5 步直接 500（P0）

整条闭环我在 34 上跑通了（证据见 §1.3），落版本这一步是好的：

```
POST /wf/card/step-done  {"session_id":"e2e00a1b2c3d","step_no":5,"snapshot":{…packaging_quote…}}
→ {"ok": true, "quote_version": {"version_no": 1, "already_saved": false,
                                 "quote_version_id": 3991596585107592505,
                                 "quote_fingerprint": "e67271aba05d11e48a33ff39b9f2768f",
                                 "quote_session_id": "e2e00a1b2c3d",
                                 "business_case_id": "bc_973aae43bb43"}}
```

紧接着读回历史版本，就变成了 500：

```
GET /wf/card/step-data?session_id=e2e00a1b2c3d&step_no=5
· 落版本之前：{"ok": true, "data": null, "packaging_quote_versions": [], "latest_quote_version": {}}
· 落版本之后：{"ok": false, "error": "服务异常，请稍后重试"}          ← 500
```

同一张卡片的 `step_no=1/3` 照旧 200 —— 坏的**只有**"有版本可读"这一条路径，
而它恰好就是用户要的"到最后再回去看报价"。

### 1.2 根因：`Decimal` / `datetime` 直接进了 `json.dumps`

```
cpq_wf.quote_version_state()            # 直接把 versions() / latest() 的行原样返回
      → cpq_packaging_quote._fetch_versions()
            rows = [dict(zip(_VERSION_COLS, row))]        # ← PG 原始行，没有做过类型转换
      → cpq_suite_server.Handler._send_json()
            body = json.dumps(obj, ensure_ascii=False)    # ← 裸 dumps，没有 default=
```

`cpq_wf_quote_version` 的列类型（`cpq_wf.py` 的 DDL）决定了回来的类型：
`created_at timestamp` → `datetime`；`cost_total` / `untaxed_unit_price` / `untaxed_total` /
`taxed_total` / … `numeric(18,6)` → `Decimal`。本机用同形状假行复现到的就是：

```
TypeError: Object of type Decimal is not JSON serializable
```

### 1.3 这次真跑留下的上下文（可复查）

| 项 | 值 |
| --- | --- |
| 34 服务 | `http://172.16.10.34:8010`，`/api/health` 200，ODA 27.1 可用 |
| 报价卡片会话 | `e2e00a1b2c3d`（卡片 id `3991594732206691606`，实例号 `bc_973aae43bb43`） |
| 技术工艺项目 | `0f080b24c65d`，需求单 `REQ-E2E-JIUHE-001`，`entry_origin=quote` |
| 图纸 | `酒盒.dwg`，sha256 `0991c8b0…f3e0`，ODA 27.1 → DXF，6569 实体 / 8 图层 / 单位 confirmed |
| 零件 | 一次解析出 64 件可读零件（真展开尺寸，如 `440.123 × 482.92`） |
| 回传 | `PKG → 报价` 成功，`quote_session_id` 回到同一张卡片，卡片 1→2 步自动 done |
| 报价版本 | 第 5 步落版本 1（`quote_version_id 3991596585107592505`）→ 随后第 5 步读回 500 |

### 1.4 既有红测为什么没钉住它

`tests/test_packaging_quote_version_persistence_red.py` 只断言 `packaging_quote_versions`
这个**键**在源码 / 页面里出现过（`bool("packaging_quote_versions" in branch)`），
从来没让这份返回体真的过一遍 `json.dumps`，所以 `Decimal` 这一路一直没人拦。

## 2. 允许修改范围（实现方）

1. `cpq_packaging_quote.py`
   - **只加读取侧的类型归一**：`versions()` / `latest()`（或它们共用的 `_fetch_versions()`）
     返回前把行里的值转成 JSON 原生类型 —— `datetime` / `date` → ISO 字符串
     （`isoformat(sep=" ")` 或 `isoformat()`，本批不规定到秒级格式，但**必须稳定、可回读**），
     `Decimal` → `int` / `float`（金额不许丢精度到科学计数法以外，`float` 即可），
     其余值原样透传；
   - 新增的归一函数只服务读取，**不改** `save_version()` 的签名、SQL 与写入值；
   - 不许在 `versions()` / `latest()` 里新写 SQL、不许改 `_VERSION_COLS` 的顺序与集合。
2. `cpq_wf.py`
   - `quote_version_state()` 保持"只读 + 复用 `versions()` / `latest()`"的现状
     （§2.6 已冻结），**不许**把它改成自己写 SQL，也**不许**在它里面吞掉异常改成空列表 ——
     读不到就是读不到，不许把 500 换成"看起来没有历史版本"。
3. `cpq_suite_server.py`
   - `/wf/card/step-data` 的 `packaging_quote_versions` / `latest_quote_version` 直接吃归一后的
     返回值，键名与结构不变（前端 `确认需求解析结果.html` 已按这两个键渲染）。

## 3. 禁止事项

- **不许**在 `_send_json()` 里加 `default=str` / `default=_json_default` 之类的兜底把 500 压掉：
  那会让所有接口的时间与金额字段"看运气"变形，也把这条 P0 藏进序列化层；
  契约在**读回路径**这一侧（§2.1）。
- 不许改版本表的 DDL、不许动已落的版本行（`cpq_wf_quote_version` 只增不改）。
- 不许改 `/wf/card/step-done` 的落版本语义（`_quote_like` 的 `cost_total` + `quote_quantity` 双键判据）。
- 不许改 `tests/` 下任何既有文件（含本批红测与 `test_packaging_quote_version_persistence_red.py`）。
- 不许连线上 PG 跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_version_readback_red -v
# 8 条：A 组 4 条（返回体可 dump / 时间转字符串 / numeric 转数字 / latest 同口径）
#       B 组 1 条（versions 与 latest 各自单独也能 dump）
#       C 组 3 条（空版本仍给 {"versions": [], "latest": {}} / 空会话号不连库 / 读回只读且归还连接）
# 现状：Ran 8, failures=5 —— A 组 4 条 + B 组 1 条红，C 组 3 条绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_version_persistence_red
# 不回归（旧口径不许被本批改掉）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_red
```

真机复验（实现方做完后，在 34 上按 §1.3 的卡片重放一次即可）：

```
GET /wf/card/step-data?session_id=e2e00a1b2c3d&step_no=5
→ 200，packaging_quote_versions 至少 1 条、latest_quote_version.version_no == 1
```
