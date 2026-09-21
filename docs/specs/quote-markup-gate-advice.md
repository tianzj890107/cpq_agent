# 规格：第 3/4 步拿不到产品行时的可执行提示（不再单一归因）

状态：Spec + 红测（**未实现**）
红测：`tests/test_quote_markup_gate_advice_red.py`

相关规格：`docs/specs/quote-tech-handoff-button.md`（A 档同批：按钮区「转技术工艺」按钮）。

---

## 1. 现场问题（实测）

现场会话：包装询盘（数码天地盒 100*90*40 / 铜版纸亮膜+哑膜 / 灰板 2.5mm / 首批 1500 /
常温）走到第 2 步「工艺确认」后卡住。往下走时，`确认需求解析结果.html:3749` 的空产品分支只输出：

```js
addErrorBubble('第 ' + step + ' 步无法计算' + cfg.column + '：前面步骤还没有产品信息。');
return false;
```

这句话对非标定制是**错误归因**：产品行为空有两种完全不同的原因，

1. 标品路径：前面确实还没选定产品（用户漏操作）；
2. 非标路径：产品库里没有适配的标品，需要转技术工艺新增产品再回来。

原文案只说了第 1 种，用户按它去「补产品信息」是补不出来的（库里就没有），
而且这条错误气泡**不带任何可执行动作**——这正是用户说「走不过去」的直接体感。

同一分支同时服务第 3 步（定价-利润加成）与第 4 步（报价-其他加价项），
见 `确认需求解析结果.html:3721` `MARKUP_CFG = {3: …, 4: …}`。

### 1.1 本批边界

**只改这一句提示与其动作**，不碰：

- 非标判定（总分阈值/维度阈值）—— 属 B 档，未拍板；
- 第 2 步 `s2_custom_spec` 承载位 —— 属 B 档，未拍板；
- `carryProducts` / `CARRY_MAP` / 门禁本身（仍然 `return false`，不许静默继续算价）。

也就是说：**本批不做「怎么判断是非标」，只做「拿不到产品行时，话说清楚、给出路」。**
两种原因都摆出来，用户自己知道该走哪条——这是不依赖任何未拍板判定的最小改法。

---

## 2. 目标

把 `runMarkupStep()` 的空产品分支换成调用一个新的提示构造器 `markupGateAdvice(step, column)`：
文案同时说明两种原因，并挂上既有的「转技术工艺」出口
（`wfOpenSend(false, 'tech_new_product')`，与 A 档按钮同一个弹窗、同一条任务）。

---

## 3. 接口契约

### 3.1 `markupGateAdvice(step, column)`

在 `确认需求解析结果.html` 的 `runMarkupStep` 之前新增一个纯函数（只拼字符串，不发请求、
不改 DOM、不读全局状态）：

```js
    /* 第 3/4 步拿不到产品行时的可执行提示。
       原来只有一种归因（「前面步骤还没有产品信息。」），对非标定制是错的：产品行为空
       可能是①前面还没选定产品，也可能是②产品库里没有适配标品、需要转技术工艺新增产品。
       两种都说清，并给出同一个出口 ── 转技术工艺（与按钮区那个按钮同一条任务）。 */
    function markupGateAdvice(step, column) {
      return {
        text: '第 ' + step + ' 步无法计算「' + column + '」：本单还没有可用的产品行。' +
              '可能是①前面还没有选定产品，也可能是②产品库里没有适配的标品、' +
              '需要转技术工艺新增产品后再回到本步。',
        actionLabel: '转技术工艺',
        action: "wfOpenSend(false, 'tech_new_product')",
      };
    }
```

要求：

1. 返回值恰好三个键：`text` / `actionLabel` / `action`（便于测试与后续复用）。
2. `text` 必须同时出现两种原因的关键词：**「还没有选定产品」**与**「新增产品」**，
   且必须带上 `step` 与 `column`（用户要知道卡在哪一步、哪一列）。
3. `action` 必须逐字等于 `wfOpenSend(false, 'tech_new_product')`。
4. 不引用 `RECOMMEND_THRESHOLD`、`below_threshold`、`nonstandard`、`WF.matchResult`
   —— 本批不引入任何非标判定。

### 3.2 `runMarkupStep()` 的空产品分支

```js
      if (!products.length) {
        const advice = markupGateAdvice(step, cfg.column);
        addErrorBubble(advice.text);
        addGateActionBubble(advice.actionLabel, advice.action);   // 气泡里的可点出口
        return false;
      }
```

要求：

1. 仍然 `return false`（不许静默继续算价，不许伪造产品行）。
2. 仍然调用 `addErrorBubble`（错误不能吞掉、不能降级成普通提示）。
3. 必须把 `step` 与 `cfg.column` 传进 `markupGateAdvice`。
4. 旧的 `'：前面步骤还没有产品信息。'` 文案必须从该分支移除（否则等于还是单一归因）。
5. **不要**在引擎里硬编码 `product_para_value` 之外的任何新数据来源，本批不查库。

### 3.3 动作气泡

`addGateActionBubble(label, action)` 是新增的轻量渲染函数：一条 AI 气泡 + 一个按钮，
点击执行 `action`（这里是打开转交弹窗并预选「新增工艺」）。要求：

- 按钮用系统主色（`var(--color-primary)`），与既有建议气泡一致，不用警告橙色；
- 不弹 `alert`、不弹 `confirm`；
- 不自动发送任务——发送仍由用户在弹窗里确认收件人后点「发送」。

### 3.4 第 4 步同样生效

第 4 步（报价-其他加价项）走同一个 `runMarkupStep`，`MARKUP_CFG[4].column = '其他加价'`。
不需要为第 4 步另写文案。

---

## 4. 禁止事项

- 不改非标判定、不改 `cpq_match`、不改第 2 步分区、不改 `carryProducts`/`CARRY_MAP`。
- 不改门禁行为：产品行缺失时依然不许算价、不许推进。
- 不新增第三方依赖；不新增后端接口；不改 `cpq_agent_server.py`。
- 不写入 `s1_products` / `s1_techparams`，不生成占位成品编码。
- 不改既有 `addErrorBubble` / `addAiBubble` 的签名与既有调用点行为。

---

## 5. 红测

`tests/test_quote_markup_gate_advice_red.py`，全部离线、只读源码。

| 组 | 覆盖 | 条数 |
| --- | --- | --- |
| A 提示构造器存在且内容正确 | §3.1 | 7 |
| B 空产品分支改用它 | §3.2 | 5 |
| C 动作气泡与主色 | §3.3 | 2 |
| D 不回归护栏 | §4 | 4 |

运行：

```bash
./open-claude/.venv/bin/python -m unittest tests.test_quote_markup_gate_advice_red -v
```

---

## 6. 本批不做（同现场、已分析、留待拍板）

1. 非标判定（`cpq_match.py:293` 只看总分阈值；现场 86 分但用途维度 30 分 → 判不出来）。
2. 第 2 步「工艺确认」的非标承载位（`s2_custom_spec`）。
3. 包装行业选品仍查电池库（`_handle_match_products` → `cpq_match.match()` 写死
   `product_para_value`），以及 ④ 表头与行数据不同源。
4. 智能体批量写「（推荐）」业务值的边界。

以上都属 B/C 档，未拍板前不动。
