# -*- coding: utf-8 -*-
"""CAD IR 人工审查包：一份 DXF / 一个项目的 CAD IR → 可读报告（Spec `dxf-cad-ir.md` §10）。

它**不下结论**：只把解析出来的事实按七节摊开，供人工核对并写真实样本金标。
因此这里不允许出现第二套几何口径 —— 所有数字都来自 `cad_ir.parse_dxf()` 的 IR，
本工具只做排版与截图级的定位信息。

七节（Spec §10）：
  ① 来源摘要（sha256 / 图纸版本 / 转换器）② 图层清单（颜色/线型/实体数）
  ③ 实体统计 ④ 尺寸文字与归一化结果 ⑤ 候选闭合轮廓 ⑥ 单位判定与候选 ⑦ 警告

用法：
    python tech_app/tools/dxf_ir_review_pack.py --dxf 某图纸.dxf [--project-id demo]
    python tech_app/tools/dxf_ir_review_pack.py --project-id <项目> [--ir-id <ir_id>]
    python tech_app/tools/dxf_ir_review_pack.py --dxf 某图纸.dxf --out /tmp/审查包
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import cad_ir  # noqa: E402

#: 报告里最多列几行明细（审查包给人看，不是给机器读）
MAX_ROWS = 60


def _rows(lines, items, render, limit=MAX_ROWS):
    for index, item in enumerate(items or []):
        if index >= limit:
            lines.append("- …（还有 %d 条，见 summary.json）" % (len(items) - limit))
            break
        lines.append(render(item))


def build_report(ir: dict) -> str:
    source = ir.get("source") or {}
    units = ir.get("units") or {}
    document = ir.get("document") or {}
    stats = ir.get("stats") or {}
    lines = ["# CAD IR 审查包（%s）" % str(ir.get("ir_id") or ""), ""]

    lines += ["## ① 来源摘要", "",
              "- ir_version：`%s`" % ir.get("ir_version"),
              "- ir_hash：`%s`" % ir.get("ir_hash"),
              "- 图纸来源：`%s`" % source.get("kind"),
              "- 附件名：`%s`" % (source.get("attachment_name") or "—"),
              "- DWG 版本：`%s`" % (source.get("detected_dwg_version") or "—"),
              "- 源文件 sha256：`%s`" % (source.get("source_sha256") or "—"),
              "- DXF 产物：`%s` sha256=`%s`" % (source.get("dxf_artifact") or "—",
                                              (source.get("dxf_sha256") or "—")[:16]),
              "- 转换器：`%s %s`（角色 %s，回退 %s；状态 %s，告警 %s / 错误 %s）" % (
                  source.get("converter_name") or "—", source.get("converter_version") or "",
                  source.get("converter_role") or "—", bool(source.get("fallback_used")),
                  source.get("conversion_status") or "—",
                  source.get("warning_count"), source.get("error_count")),
              "- 交叉核对：`%s` %s" % ((source.get("crosscheck") or {}).get("match"),
                                      (source.get("crosscheck") or {}).get("deltas") or {}),
              ""]

    lines += ["## ② 图层清单", "", "| 图层 | 颜色 | 线型 | 实体数 | 可见 | 冻结 |",
              "| --- | --- | --- | --- | --- | --- |"]
    _rows(lines, ir.get("layers"), lambda row: "| %s | %s | %s | %s | %s | %s |" % (
        row.get("name"), row.get("color"), row.get("line_type"), row.get("entity_count"),
        row.get("visible"), row.get("frozen")))
    lines.append("")

    lines += ["## ③ 实体统计", "", "```json", json.dumps(stats, ensure_ascii=False, indent=2), "```", ""]

    lines += ["## ④ 尺寸文字与归一化结果", ""]
    _rows(lines, ir.get("texts"), lambda row: "- `%s`（%s，图层 %s）→ %s" % (
        row.get("entity_id"), row.get("type"), row.get("layer"),
        row.get("normalized_text")))
    lines += ["", "### 标注（declared 与 measured 分开列，禁止互相覆盖）", ""]
    _rows(lines, ir.get("dimensions"), lambda row: "- `%s` %s：declared=%s measured=%s delta=%s" % (
        row.get("entity_id"), row.get("dim_type"), row.get("declared_value"),
        row.get("measured_value"), row.get("delta")))
    lines.append("")

    lines += ["## ⑤ 候选闭合轮廓", "", "- 闭合轮廓 %d / 开放回路 %d / 孔位 %d（tolerance=%s）" % (
        stats.get("closed_outline_total"), stats.get("open_outline_total"),
        len((ir.get("geometry") or {}).get("holes") or []),
        (ir.get("geometry") or {}).get("tolerance")), ""]
    _rows(lines, (ir.get("geometry") or {}).get("closed_outlines"),
          lambda row: "- `%s` 面积=%s bbox=%s 证据 `%s`" % (
              row.get("outline_id"), row.get("area"), row.get("bbox"), row.get("evidence_ref")))
    lines.append("")

    lines += ["## ⑥ 单位判定与候选", "",
              "- drawing_units=`%s` unit_status=`%s` scale_to_mm=%s confidence=%s" % (
                  units.get("drawing_units"), units.get("unit_status"),
                  units.get("scale_to_mm"), units.get("unit_confidence"))]
    _rows(lines, units.get("candidates"),
          lambda row: "  - 候选 %s（confidence=%s）：%s" % (
              row.get("unit"), row.get("confidence"), row.get("reason")))
    if units.get("unit_status") != "confirmed":
        lines.append("- **单位待人工确认**：所有 `length_mm` / `area_mm2` 为空，绝对尺寸不得直接使用")
    lines += ["", "- 图幅 extents=%s 模型空间实体 %s" % (
        document.get("extents"), (document.get("model_space") or {}).get("entity_count")), ""]

    lines += ["## ⑦ 警告与不支持", ""]
    _rows(lines, ir.get("warnings"), lambda row: "- `%s`：%s" % (row.get("code"), row.get("message")))
    _rows(lines, ir.get("unsupported"), lambda row: "- 不支持实体 %s ×%s（句柄 %s）" % (
        row.get("type"), row.get("count"), ",".join(row.get("handles") or [])[:80]))
    lines.append("")
    return "\n".join(lines)


def build_summary(ir: dict) -> dict:
    return {
        "ir_id": ir.get("ir_id"), "ir_hash": ir.get("ir_hash"),
        "ir_version": ir.get("ir_version"),
        "source": ir.get("source") or {}, "units": ir.get("units") or {},
        "stats": ir.get("stats") or {},
        "warnings": [row.get("code") for row in ir.get("warnings") or []],
        "unsupported": [{"type": row.get("type"), "count": row.get("count")}
                        for row in ir.get("unsupported") or []],
        "layer_total": len(ir.get("layers") or []),
        "closed_outlines": (ir.get("geometry") or {}).get("closed_outlines") or [],
        "summarize": cad_ir.summarize(ir),
    }


def default_out_dir(project_id: str, ir_id: str) -> pathlib.Path:
    return ROOT / "tech_app" / "tech_data" / str(project_id) / "review" / "cad_ir" / str(ir_id)


def write_pack(ir: dict, out_dir: pathlib.Path) -> dict:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(ir)
    summary = build_summary(ir)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"out_dir": str(out_dir), "report_bytes": len(report.encode("utf-8")),
            "summary_bytes": len(json.dumps(summary, ensure_ascii=False).encode("utf-8"))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CAD IR 人工审查包（Spec dxf-cad-ir.md §10）")
    parser.add_argument("--dxf", help="直接解析一份 DXF（只读，不入库）")
    parser.add_argument("--project-id", default="cad-ir-review", help="项目 id（落盘目录与来源标注）")
    parser.add_argument("--ir-id", default="", help="项目里已有的 IR id（不给取最新一版）")
    parser.add_argument("--out", default="", help="输出目录（默认 tech_app/tech_data/<项目>/review/cad_ir/<ir_id>）")
    args = parser.parse_args(argv)

    if args.dxf:
        path = pathlib.Path(args.dxf)
        if not path.exists():
            print("找不到 DXF：%s" % path)
            return 2
        content = path.read_bytes()
        ir = cad_ir.parse_dxf(content, filename=path.name, source={
            "kind": "dxf_2d", "project_id": args.project_id,
            "attachment_name": path.name,
            "dxf_artifact": path.name,
            "dxf_sha256": hashlib.sha256(content).hexdigest(),
        })
    else:
        ir = cad_ir.load_ir(args.project_id, args.ir_id or None)
        if not ir:
            print("项目 %s 没有可审查的 CAD IR（先跑第 3 批解析）" % args.project_id)
            return 2

    out_dir = pathlib.Path(args.out) if args.out else default_out_dir(args.project_id, ir.get("ir_id"))
    info = write_pack(ir, out_dir)
    print("审查包已生成：%s" % info["out_dir"])
    print("  report.md %d B / summary.json %d B" % (info["report_bytes"], info["summary_bytes"]))
    print("  实体 %s / 图层 %s / 闭合轮廓 %s / 警告 %s" % (
        (ir.get("stats") or {}).get("entity_total"), (ir.get("stats") or {}).get("layer_total"),
        (ir.get("stats") or {}).get("closed_outline_total"), len(ir.get("warnings") or [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
