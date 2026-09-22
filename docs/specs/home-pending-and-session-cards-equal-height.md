# 首页卡片等高：待办卡与会话卡统一高度 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_home_cards_equal_height_red.py`

## 背景与目标

`报价首页.html` 的卡片网格 `.card-grid` 是 `repeat(3, 1fr)`，但没有显式声明等高策略；
待办卡 `taskCardHtml()`（`:1603`）比会话卡 `cardHtml()`（`:1741`）多一行
「来自 <人>（<角色>）· 第 N 步 <步骤>」，而且备注为空时还硬塞了一整行占位：

```
'<div class="request-info request-note">' + (t.note ? '“' + escH(t.note) + '”' : '&nbsp;') + '</div>' +
```

后果：同一行里卡片高度不一致，多出来的空白把页脚顶开，视觉上「大小不一」。
上一轮的 CSS 注释还把这种占位当成正确做法（「备注那一行没内容时也占位，四张卡的页脚才对得齐」），
需要一并纠正。

目标：同一行的卡片高度一致，待办卡保留「来自 … 第 N 步」这一行，但**不再有任何空占位行**，
会话卡以同样高度对齐（页脚贴底）。

## 产品契约

1. `.card-grid` 显式声明 `align-items: stretch`，同一行卡片（含待办卡与会话卡）等高。
2. `.request-card` 加 `height: 100%`，把网格单元高度吃满；不得出现 `align-items: start`
   / `align-self: start` 这类会让卡片回到自然高度的写法。
3. 待办卡备注行只在有备注时渲染：`t.note` 为空时**不输出**该行，不得再用 `&nbsp;` 占位。
4. 待办卡保留「来自 … 第 N 步 …」这一行与 `.request-card.wf-task` 的左侧蓝边。
5. 会话卡不许靠「补一行占位内容」去凑高度：`cardHtml()` 的 `.request-info` 仍是 1 行，
   高度靠网格拉伸 + `.request-footer { margin-top: auto }` 对齐。
6. 三列网格、单行截断（`white-space: nowrap` + `text-overflow: ellipsis`）、
   页脚贴底规则保持不变。

## 范围

允许修改：

- `报价首页.html`：`.card-grid` / `.request-card` 相关样式、`taskCardHtml()` 的备注行，
  以及描述占位做法的过时注释；
- 当周 changelog。

## 禁止事项

- 不改 `cardHtml()` 的信息行数量与内容，不加假占位行凑高度；
- 不改待办卡「来自 … 第 N 步」这一行与左侧蓝边；
- 不改卡片点击、领取、删除、搜索、分页与数据接口；
- 不动 `报价首页.html` 以外的页面样式（含 `规则首页.html`、`配置首页.html`、工作台与看板）；
- 不引入固定像素高度（如 `height: 180px`）来「拉齐」，避免长内容被裁切；
- 不为迁就实现修改本 Spec 或 Red 测试；
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 验收标准

1. `.card-grid` 含 `align-items: stretch`，`.request-card` 含 `height: 100%`。
2. 文件里不存在作用在卡片网格/卡片上的 `align-items: start` / `align-self: start`。
3. `taskCardHtml()` 体内不再出现 `&nbsp;`，备注行由 `t.note` 条件渲染。
4. 「来自 … 第 N 步」这一行仍在；`.request-card.wf-task` 左侧蓝边仍在。
5. `cardHtml()` 的信息行数量未增加；`.request-footer` 仍是 `margin-top: auto`。
6. 三列网格与单行截断规则未被削弱。
7. 新增 Red 测试转绿，且既有首页相关测试不新增失败。

## 对应测试

`tests/test_home_cards_equal_height_red.py`
