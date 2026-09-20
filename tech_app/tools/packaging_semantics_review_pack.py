# -*- coding: utf-8 -*-
"""包装图纸语义人工审查包（DWG 第 4 批 Spec §12）。

对某个项目 / 某份 CAD IR 生成一份**可视化审查包**，给人看图纸事实是否被读对：

    tech_app/tech_data/<project_id>/review/packaging_semantics/<semantics_id>/
      report.md      # 九节人读报告
      summary.json   # 机器可读的同一份内容

审查人看完后手写 `tests/fixtures/real_baselines/<sample>.packaging.golden.json`；
**本工具绝不自动生成金标**，也不猜尺寸：没有证据的字段一律保持 missing / 未确认。

纪律：
  · 纯本地：不联网、不调模型（`use_model` 恒为 False）、不写需求看板、不建任务；
  · 只读输入（CAD IR / 规则文件），只写审查包目录；
  · 不把 DXF 原文或预览字节写进任何产物。

用法：
    python tech_app/tools/packaging_semantics_review_pack.py --help
    python tech_app/tools/packaging_semantics_review_pack.py \
        --ir tests/fixtures/cad_ir/cut_crease_layers.json --project-id review-demo
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

TOOLS_DIR = Path(__file__).resolve().parent
CPQ_DIR = TOOLS_DIR.parent.parent
if str(CPQ_DIR) not in sys.path:
    sys.path.insert(0, str(CPQ_DIR))

from tech_app.backend.services import packaging_semantics  # noqa: E402


def _load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("输入必须是 JSON 对象：%s" % path)
    return data


def _table(rows: List[List[Any]], header: List[str]) -> List[str]:
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return out


def build_report(semantics: Dict[str, Any], ir: Dict[str, Any]) -> str:
    source = semantics.get("source") or {}
    lines: List[str] = [
        "# 包装图纸语义审查包",
        "",
        "> 本报告只描述**图纸事实与候选**，不锁定盒型、不给结论性尺寸、不涉及价格。",
        "",
        "## ① 来源摘要",
        "",
    ]
    for key in ("project_id", "ir_id", "ir_hash", "ir_version", "drawing_version",
                "conversion_status", "warning_count", "error_count", "template",
                "rules_version", "rules_path_kind"):
        lines.append("- `%s`：%s" % (key, source.get(key)))
    lines.append("- `dxf_sha256`：%s" % (ir.get("source") or {}).get("dxf_sha256"))
    lines.append("- `source_sha256`：%s" % (ir.get("source") or {}).get("source_sha256"))

    lines += ["", "## ② 转换预览", "",
              "- 预览不属于本批产物：本包只记录语义结论，不搬运预览字节。", ""]

    lines += ["", "## ③ 图层清单", ""]
    lines += _table([[row.get("name"), row.get("color"), row.get("line_type"),
                      row.get("entity_count"), row.get("matched_rule_id"),
                      row.get("role"), row.get("role_source"),
                      row.get("role_confidence")]
                     for row in semantics.get("layers") or []],
                    ["图层", "颜色", "线型", "实体数", "命中规则", "角色", "来源", "置信度"])

    stats = ir.get("stats") or {}
    lines += ["", "## ④ 实体统计", ""]
    lines += _table([[key, stats.get(key)] for key in sorted(stats)], ["项", "数量"])

    lines += ["", "## ⑤ 尺寸文字与归一化（含标注冲突）", ""]
    texts = semantics.get("texts") or {}
    lines += _table([[row.get("field"), row.get("text"), row.get("evidence_level")]
                     for row in (texts.get("material_candidates") or [])
                     + (texts.get("process_candidates") or [])],
                    ["字段", "原文", "证据强度"])
    conflicts = (semantics.get("dimensions") or {}).get("conflicts") or []
    if conflicts:
        lines += ["", "**标注冲突（未裁决前不得当作已确认）**", ""]
        lines += _table([[row.get("field"), row.get("declared"), row.get("measured"),
                          row.get("delta"), row.get("tolerance")]
                         for row in conflicts],
                        ["字段", "标注", "几何实测", "偏差", "容差"])

    lines += ["", "## ⑥ 候选轮廓", ""]
    outline = semantics.get("outline") or {}
    lines += _table([[row.get("outline_id"), row.get("is_closed"), row.get("bbox"),
                      row.get("area"), row.get("layer"), row.get("role")]
                     for row in outline.get("boundary_candidates") or []],
                    ["候选", "闭合", "包围盒", "面积", "图层", "角色"])
    holes = outline.get("holes") or []
    if holes:
        lines += ["", "孔位候选：%d 个" % len(holes), ""]

    lines += ["", "## ⑦ 刀线与压痕线", ""]
    cut_layers = [row.get("name") for row in semantics.get("layers") or []
                  if row.get("role") == "cut"]
    crease_layers = [row.get("name") for row in semantics.get("layers") or []
                     if row.get("role") == "crease"]
    lines.append("- 刀线图层：%s" % (", ".join(cut_layers) or "（未识别）"))
    lines.append("- 压痕线图层：%s" % (", ".join(crease_layers) or "（未识别）"))

    lines += ["", "## ⑧ 字段候选及证据", ""]
    lines += _table([[key, row.get("origin"), row.get("status"), row.get("value"),
                      row.get("confidence"), ",".join(row.get("evidence_refs") or [])]
                     for key, row in sorted((semantics.get("fields") or {}).items())],
                    ["字段", "来源", "状态", "值", "置信度", "证据"])

    lines += ["", "## ⑨ 未确认项", ""]
    lines += _table([[row.get("field"), row.get("reason"), row.get("status")]
                     for row in semantics.get("unresolved") or []],
                    ["字段", "原因", "状态"])

    lines += ["", "## 附：本次不可当作结论的提示", ""]
    for warning in semantics.get("warnings") or []:
        lines.append("- `%s`：%s" % (warning.get("code"), warning.get("message")))
    lines.append("")
    lines.append("> 人工复核后请手写 golden（`tests/fixtures/real_baselines/`），"
                 "并逐项标注：已确认事实 / 可接受候选 / 不应出现的错误 / 尚无法确认的字段。")
    return "\n".join(lines) + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成包装图纸语义人工审查包（不调模型、不联网、不写需求看板）")
    parser.add_argument("--ir", required=False, help="CAD IR JSON 文件路径")
    parser.add_argument("--project-id", default="review-demo", help="审查包落盘用的项目 id")
    parser.add_argument("--out", default="", help="输出目录（默认落在项目 review 目录下）")
    parser.add_argument("--rules", default="", help="图层规则 JSON（默认用仓库内置规则）")
    parser.add_argument("--template", default="", help="规则模板名（默认取配置里的 default_template）")
    args = parser.parse_args(argv)

    if not args.ir:
        parser.print_help()
        return 0
    ir_path = Path(args.ir)
    if not ir_path.exists():
        raise SystemExit("CAD IR 文件不存在：%s" % ir_path)

    ir = _load_json(ir_path)
    rules = None
    if args.rules:
        from tech_app.backend.services.packaging_semantics import rules as rules_mod

        rules = rules_mod.read_rules_file(Path(args.rules))[0]
    semantics = packaging_semantics.analyze(
        ir, template=args.template or None, rules=rules, use_model=False)

    if args.out:
        target = Path(args.out)
    else:
        from tech_app.backend.storage import store

        target = (store.project_dir(args.project_id) / "review" / "packaging_semantics"
                  / str(semantics.get("semantics_id") or "unknown"))
    target.mkdir(parents=True, exist_ok=True)
    (target / "report.md").write_text(build_report(semantics, ir), encoding="utf-8")
    (target / "summary.json").write_text(
        json.dumps(packaging_semantics.summarize(semantics), ensure_ascii=False, indent=2,
                   sort_keys=True), encoding="utf-8")
    print("审查包已生成：%s" % target)
    print("语义版本：%s" % semantics.get("semantics_id"))
    print("模型调用：%s（本工具恒为 0）" % (semantics.get("model_assist") or {}).get("calls"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
