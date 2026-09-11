# Spec: 报价「已转交·待领取」流程提示条去倒角（直角化）

状态：待实现（红测已落地）
适用分支：`20260909`

## 1. 需求

`确认需求解析结果.html` 顶部的流程状态提示条 `#wfBar` 不做倒角（圆角），改为直角。
该提示条承载的是"这张卡片现在卡在谁那里"，例如：

> 已转交·待领取：销售经理·salesm1 发起「新增工艺」 · 请到技术工艺新增产品 · 指派给指定人员。对方领取后即可继续第 1 步。

## 2. 现状

`确认需求解析结果.html:2703` 的 `wfRenderBar()` 里，提示条是动态创建的，内联样式写死圆角：

```js
bar.style.cssText = 'display:none;margin:0 0 10px;padding:8px 12px;border-radius:8px;' +
  'font-size:12px;line-height:1.5;border:1px solid var(--border-color);';
```

文案来源：`cpq_wf.py:731` 组装 `source_label`，`确认需求解析结果.html:2731` 的
`WF.pendingTask` 分支渲染（`:2734` 的 `已转交·待领取`）。

## 3. 要求

- R1 `#wfBar` 的圆角必须去掉（`border-radius` 不得为正数；删掉或写 `0` 均可）。
- R2 提示条的留白与描边不得一起删掉：`padding`、`border:1px solid`、`font-size`、
  `line-height` 保持不变，保证直角化之后仍是一条可读的状态条。
- R3 只改这一条：页面其它圆角元素（聊天气泡 `14px`、胶囊 `9999px`、圆点 `50%` 等）
  必须保留。
- R4 提示条的全部状态分支与文案不变：`未登录` / `待转交` / `已转交·待领取` /
  可由我完成 / `只能查看`（含"对方领取后即可继续第 N 步"）。
- R5 不影响后端：不新增/删除/修改任何接口与 `cpq_wf.py` 的字段。

## 4. 验收标准

- 红测 `tests/test_quote_transfer_bar_square_corners_red.py` 全绿：
  - `#wfBar` 创建分支内不得出现非 0 的 `border-radius`；
  - 提示条仍保留 `padding` / `border:1px solid` / `font-size` / `line-height`；
  - 五种状态文案仍在；
  - 其它圆角 token 未消失。
- 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 无新增失败。

## 5. 不在本次范围

- 调整提示条的配色、文案、位置或显示时机。
- 其它页面（`报价首页.html`、技术工艺统一工作台）的圆角体系。
