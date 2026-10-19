# 2026-10-19 包装 2.1「确认工艺」按钮修复

- 根因：`drawingParsed()` 只认技术 IR。包装项目的业务部件在 `currentPackagingBusinessParts`，`currentIR` 为空，导致「确认工艺」的 `visible` 恒为 false，批量按钮也永远当不了主按钮。
- 修复：包装业务零件清单参与“已解析”的同步判定；按钮文案、确认闸门、确认后按人发送与批量实现均不变。同步更新 `app.js` 缓存版本号，避免浏览器沿用旧文件。
- 新增 `tests/test_packaging_confirm_button_visibility_red.py`，覆盖包装业务零件判定、确认按钮可见性、批量主按钮与缓存版本；不改后端接口与既有确认口径。
