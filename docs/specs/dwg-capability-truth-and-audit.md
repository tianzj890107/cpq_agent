# DWG 能力事实：能力矩阵、错误码与审计不得写死

Spec 版本：1（状态行见下）

状态：Spec + 红测（已实现）
红测：`tests/test_dwg_capability_truth_red.py` `tests/test_dwg_conversion_quality_repair_red.py` `tests/test_dwg_file_capability_preflight_red.py`

## 1. 背景（实测）

34 上 ODA 27.1 主转换器已装好并用真实 `酒盒.dwg` 实测出 DXF（`fallback_used=false`），
但代码里对"有没有转换器"这件事的表述仍停留在"没有转换器"的旧批次结论：

| 位置 | 现状 | 与 34 事实的关系 |
| --- | --- | --- |
| `file_preflight.py:204-225` | 能力矩阵 `"converter_available": False` 逐行写死（注释：第 1 批不装转换器） | 与事实相反 |
| `file_preflight.py:244` | `_GATE_ERROR_CODE["dwg"] = "DWG_CONVERTER_NOT_INSTALLED"` 硬编码 | 码名与事实相反 |
| `file_preflight.py:46-48` | 该码 message 写死「当前环境尚未安装 CAD 转换服务，暂时无法解析」 | 用户看到假话 |
| `file_preflight.py:474+` | `vision_gate_error()` 对 DWG 一律返回上述码 | 拒答方向指向运维，真因是入口错 |
| `audit_entry()` | 审计里的 `converter_available` 恒 False | **审计跟着说假话** |

业务后果：用户/客服从 2.1 传 DWG 得到"环境没装转换器"，排查方向整体跑偏；同一份 DWG 在
两个入口得到两套互相矛盾的拒答文案（"没装转换器" vs "不是位图"）。

## 2. 目标

"有没有转换器"只有一个来源：运行时探测。错误码、用户文案、审计字段都从它派生。闸门仍然在
（DWG 在 34 上确实已可转换，但技术工艺侧的匹配/语义链路已改走 drawing-flow），
只是**拒答理由改成事实**。

## 3. 契约

### C1 单一探测入口（运行时）

新增：

```python
file_preflight.detect_converter_availability() -> {
    "available": bool, "role": "primary" | "fallback" | "none",
    "version": str, "source": str, "checked_at": str,
}
```

- 判定必须来自部署自检同源的转换器事实（主转换器可用性），不得读常量；
- 纯函数纪律的例外只此一处：它允许探测，但**不得**读写库、不得联网、不得改文件；
- 探测失败按 `available=False, role="none"` 返回，不得抛裸异常。

### C2 `capabilities_of` 接受注入

```python
capabilities_of(detected, *, converter=None) -> dict
```

- 传 `converter` 时按传入结果计算（纯函数，测试可直接喂）；
- 不传时调用 C1；
- 矩阵里 `"converter_available": False` 的字面量必须清零，改为由 `converter` 决定。

### C3 DWG 在视觉入口的拒答改为"走对路"

- `converter["available"] is True` 时，`vision_gate_error()` 对 DWG 返回新稳定码
  **`DWG_USE_DRAWING_FLOW`**：`http_status=409`、`retryable=False`，
  message 指向"该项目请使用图纸解析链路（drawing-flow）"；
- `converter["available"] is False` 时保持现有 `DWG_CONVERTER_NOT_INSTALLED`（语义此时为真）；
- DXF / 3D / 未知格式的码不变（`FILE_FORMAT_UNSUPPORTED` / `DWG_NOT_A_3D_MODEL`）。

### C4 稳定码与文案

- `STABLE_ERROR_CODES` 增加 `DWG_USE_DRAWING_FLOW`，闭集之外不得新增；
- `DWG_CONVERTER_NOT_INSTALLED` 的默认 message 去掉"既成事实"口气，改为
  「未检测到可用的 CAD 转换服务」（因为默认文案只在该判定为真时才用得上）。

### C5 审计与事实一致

`audit_entry(detected, ..., converter=...)` 输出：

- `converter_available` = 探测结果（不再恒 False）；
- 新增 `converter_role`、`converter_version`（用于事后区分"环境故障"与"入口错误"）。

### C6 部署自检与能力矩阵同源

`scripts/deploy_34_bare.sh` 的转换器自检（真转两份样本、核对 `converter_role`）
与 C1 必须使用同一判据函数/同一字段名，避免"部署说好、应用说没装"。

## 4. 不在本批范围

- 不改 `qwen_client._media_type_for()` 的兜底（DWG 永远不会走到它，靠 C3 挡住）；
- 不装/不升级转换器，不改部署脚本的安装步骤；
- 不改 drawing-flow 服务端实现。

## 5. 验收标准

1. `tests/test_dwg_capability_truth_red.py` 全绿；
2. 34 上 `GET /api/meta`（或等价只读端点）暴露的能力事实里 DWG 为可用，
   且与部署自检的 `converter_role` 一致；
3. 传 DWG 到视觉入口得到的错误码是 `DWG_USE_DRAWING_FLOW`，文案不含"尚未安装"。
