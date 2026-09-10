# 组装与整合“不可操作”与“正在处理”状态区分 Spec

## 问题

参数推荐或组装工艺尚未生成时，“确认参数推荐”“确认组装工艺”等按钮会因缺少前置结果而 disabled，但当前 `.ai-actions .inline-action:disabled` 统一使用 `cursor: wait`，鼠标悬浮看起来像任务正在转圈。此时实际含义是“暂时不能做”，应与“确认工艺并发送财务”的禁用态一致，而不是伪装成正在运行。

## 状态定义

- 不可操作：缺少 `has_params`、缺少 `has_process`、前置步骤不满足或其他业务闸门导致 disabled。使用 `cursor: not-allowed` 和既有禁用透明度，不响应 hover 深色样式。
- 正在处理：确有异步生成请求正在执行，且控件显式携带 `aria-busy="true"`（或等价的明确 busy class/data 属性）。只有这种状态可以使用 `cursor: wait` 并显示 spinner。
- `disabled` 本身不等于 busy，禁止再用通用 `:disabled { cursor: wait; }` 表达两种不同语义。

## 具体要求

1. `.ai-actions .inline-action:disabled` 默认改为 `cursor: not-allowed`，视觉与 `.ai-op-btn:disabled` 一致。
2. `#aiParamsConfirm` 在没有参数推荐时、`#aiProcessConfirm` 在没有组装工艺时显示不可操作光标，不能显示等待/转圈光标。
3. `#aiGenerate` 真实生成期间应显式设置 `aria-busy="true"`，继续显示现有 spinner 和“生成中…”文案，并使用 `cursor: wait`。
4. 非 busy 的 disabled 按钮不得出现 spinner；busy 完成、失败或 finally 结束后，重新渲染出的按钮必须不再保留 `aria-busy="true"`。
5. 不修改 `has_params/has_process`、确认条件、请求流程、按钮点击事件及后端逻辑。

## 验收

- 空白初始状态下，两个确认按钮悬浮为禁止操作，不再转圈。
- 正在生成参数或工艺时，生成按钮仍明确呈现等待状态和 spinner。
- 生成成功或失败后等待态消失；disabled、hover 和业务闸门行为保持正确。

