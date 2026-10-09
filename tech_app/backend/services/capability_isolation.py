# -*- coding: utf-8 -*-
"""能力隔离与发布判据（可用性与服务边界批次 5，Spec §2.1/§2.2）。

把「转换器坏了」与「系统坏了」分开：
  · **运行期**只认 `isolation_view`：DWG 转换器不可用时**只禁用 DWG 解析**并明确告警，
    登录 / 历史报价 / 文字·Excel·PDF·图片 快速报价一律不受影响（`documents` 恒可用）；
  · **发布期**只认 `release_verdict`：生产门禁失败只阻止**发布**，不据此对运行期做任何禁用。

纯函数、不连库、不写盘、不调模型；能力查询**绝不许抛异常**（Spec §5）。
"""
from __future__ import annotations

ISOLATION_VERSION = "capability-isolation/1"

#: DWG 解析被禁用、但上游没给具体原因时的默认码。
DEFAULT_DISABLED_CODE = "DWG_PARSER_DISABLED"

#: 门禁里「已确认」的状态（不阻断发布）。
ACKNOWLEDGED_STATUSES = ("ok", "acknowledged")


def isolation_view(capability) -> dict:
    """把两种能力形状翻成「哪些可用 / 哪些被禁用 / 为什么」（纯函数，绝不抛异常）。

    两种入参形状都认：
      · `cad_converter.capability()`：`available` / `preview_available` / `stable_error_code`
      · `unified_parse.capability()`：`dwg` / `preview` / `detail`

    红线（Spec §2.1）：本函数只描述「DWG 解析被禁用」，**不得**把 `documents` 置为 disabled。
    """
    cap = capability if isinstance(capability, dict) else {}
    available = bool(cap.get("available")) or bool(cap.get("dwg"))
    preview_ok = bool(cap.get("preview_available")) or bool(cap.get("preview"))
    if available:
        return {"version": ISOLATION_VERSION, "dwg_parse": "enabled", "documents": "enabled",
                "preview": "enabled" if preview_ok else "disabled",
                "disabled_reason": "", "warning": ""}
    reason = (str(cap.get("stable_error_code") or "").strip()
              or str(cap.get("error_code") or "").strip()
              or str(cap.get("detail") or "").strip()
              or DEFAULT_DISABLED_CODE)
    return {"version": ISOLATION_VERSION, "dwg_parse": "disabled", "documents": "enabled",
            "preview": "enabled" if preview_ok else "disabled",
            "disabled_reason": reason,
            "warning": "DWG 解析已禁用（%s）：转换器不可用只影响 DWG，登录 / 历史报价 / "
                       "文字·Excel·PDF·图片 快速报价不受影响。" % reason}


def release_verdict(gate_report) -> dict:
    """生产门禁报告 → 发布结论（纯函数）。**只产出发布结论，不做运行期禁用**（Spec §2.2）。"""
    invalid = {"version": ISOLATION_VERSION, "release_ok": False, "verdict": "no_go",
               "blocking": ["gate_report_invalid"], "manual": []}
    if not isinstance(gate_report, dict) or not isinstance(gate_report.get("items"), list):
        return invalid
    env = str(gate_report.get("env") or "")
    blocking, manual = [], []
    for item in gate_report.get("items") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        item_id = str(item.get("id") or "")
        if status == "manual_unacknowledged":
            manual.append(item_id)
            blocking.append(item_id)
        elif status == "fail":
            blocking.append(item_id)
        elif status == "skip" and env == "production":
            blocking.append(item_id)          # 生产不允许跳过
    verdict = "no_go" if str(gate_report.get("verdict") or "") == "no_go" else "go"
    release_ok = (not blocking) and verdict != "no_go"
    return {"version": ISOLATION_VERSION, "release_ok": release_ok, "verdict": verdict,
            "blocking": blocking, "manual": manual}
