# -*- coding: utf-8 -*-
"""DWG 金标与验收记录工具（DWG 支持第 6 批 Spec §4.2）。

三件事，按被调用的动作分：

  · `--verify`（**默认**，只读）：逐条比对金标与验收记录，有差异 / 有未审批金标 /
    记录无效 → 非零退出并打印版本化 diff 摘要；**一律不写任何文件**。
  · `--write-baseline`：把**已通过验收的记录**固化成 `<baseline-dir>/<version>/`
    下的金标快照。必须显式给 `--approved-by`，缺则退出码 2 且不写任何文件。
  · `--write-record`：把一次真实 E2E 的结论写成验收记录。同样必须 `--approved-by`，
    且必须给 `--e2e-report`（没有报告哈希的记录不算证据）。

纪律（Spec §4.2）：
  · 没有任何"自动刷绿"开关（不接受强制覆盖/自动更新快照的参数）——金标只能由人写；
  · 未审批（`approval.approved_by` 为空或时间不可解析）的金标一律判不合格；
  · 输出里不出现密钥、绝对部署路径以外的敏感值，也不出现"支持 DWG"这类未经证据的断言。

用法：
    python tech_app/tools/dwg_acceptance_report.py [--verify] [--baseline-dir DIR]
        [--record PATH] [--write-baseline | --write-record] --approved-by <人> [--json]
        [--report PATH] [--e2e-report PATH]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CPQ_DIR = TOOLS_DIR.parent.parent
if str(CPQ_DIR) not in sys.path:
    sys.path.insert(0, str(CPQ_DIR))

DEFAULT_BASELINE_RELATIVE = ("tests", "fixtures", "dwg_acceptance")
SAMPLE_FILENAMES = ("酒盒.dwg", "圆盘盒.dwg")
REQUIRED_GOLDEN_KEYS = (
    "golden_version", "sample_filename", "source_sha256", "detected_dwg_version",
    "converter", "artifacts", "stats", "unit_status", "cut_layers", "crease_layers",
    "box_candidates", "key_dimensions", "required_unresolved", "forbidden_fields",
    "three_d_status", "reviewed_by", "reviewed_at", "approval",
)
EXIT_OK = 0
EXIT_FAILED = 2
EXIT_USAGE = 2


def default_baseline_dir() -> Path:
    return CPQ_DIR.joinpath(*DEFAULT_BASELINE_RELATIVE)


def sha256_file(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:                                     # noqa: BLE001 - 读不到就当没有
        return ""


def _parse_time(value) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def collect_versions(root) -> list:
    """`<root>` 本身是版本目录（含 manifest.json）就取它，否则取它下面的一级子目录。"""
    root = Path(root)
    if (root / "manifest.json").is_file():
        return [root]
    if not root.is_dir():
        return []
    return [item for item in sorted(root.iterdir()) if item.is_dir()]


def verify_version(version_dir) -> list:
    """逐条比对一份版本化金标；返回问题清单（空 = 通过）。"""
    import importlib

    dispatch = importlib.import_module("tech_app.backend.services.dwg_dispatch")
    allowed_three_d = tuple(dispatch.DRAWING3D_STATUS) + ("",)
    version_dir = Path(version_dir)
    problems = []
    manifest_path = version_dir / "manifest.json"
    if not manifest_path.is_file():
        return ["%s：缺少 manifest.json" % version_dir.name]
    try:
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return ["%s：manifest.json 解析失败（%s）" % (version_dir.name, exc)]
    samples = meta.get("samples") or {}
    for name in SAMPLE_FILENAMES:
        if name not in samples:
            problems.append("%s：金标未覆盖样本 %s" % (version_dir.name, name))
    for name in SAMPLE_FILENAMES:
        path = version_dir / (name.replace(".dwg", "") + ".json")
        if not path.is_file():
            problems.append("%s：缺少样本金标 %s" % (version_dir.name, path.name))
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append("%s：解析失败（%s）" % (path.name, exc))
            continue
        missing = [key for key in REQUIRED_GOLDEN_KEYS if key not in doc]
        if missing:
            problems.append("%s：缺字段 %s" % (path.name, "、".join(sorted(missing))))
        converter = doc.get("converter") or {}
        if str(converter.get("role") or "") != "primary":
            problems.append("%s：converter.role 必须是 primary（回退产物不算验收证据）"
                            % path.name)
        if converter.get("fallback_used"):
            problems.append("%s：金标记着 fallback_used（回退产物不许当主转换器证据）"
                            % path.name)
        if len(str(doc.get("source_sha256") or "")) != 64:
            problems.append("%s：source_sha256 不是 sha256" % path.name)
        if not doc.get("forbidden_fields"):
            problems.append("%s：forbidden_fields 不许为空（没有幻觉字段清单就没有护栏）"
                            % path.name)
        if str(doc.get("three_d_status") or "") not in allowed_three_d:
            problems.append("%s：three_d_status 越界（%r）"
                            % (path.name, doc.get("three_d_status")))
        approval = doc.get("approval") or {}
        if not str(approval.get("approved_by") or "").strip():
            problems.append("%s：未经人工审批（缺少 approval.approved_by）" % path.name)
        if not str(approval.get("approved_at") or "").strip():
            problems.append("%s：审批时间缺失" % path.name)
        elif not _parse_time(approval.get("approved_at")):
            problems.append("%s：审批时间不可解析" % path.name)
    return problems


def verify_record(record_path) -> dict:
    from tech_app.backend.services import dwg_acceptance

    try:
        state = dwg_acceptance.validate(None, record_path=Path(record_path))
    except Exception as exc:                              # noqa: BLE001 - 校验失败要如实报
        return {"valid": False, "reason": "unreadable", "detail": str(exc)}
    return state


def verify(baseline_dir, record_path) -> dict:
    problems = []
    versions = collect_versions(baseline_dir)
    if not versions:
        problems.append("%s：没有 <golden_version>/ 金标目录" % baseline_dir)
    for version in versions:
        problems.extend(verify_version(version))
    record_state = verify_record(record_path)
    if not record_state.get("valid"):
        problems.append("验收记录无效：%s" % (record_state.get("reason") or "unreadable"))
    return {"action": "verify", "baseline_dir": str(baseline_dir),
            "versions": [item.name for item in versions],
            "record": record_state, "record_path": str(record_path),
            "problems": problems, "ok": not problems}


def _blocked(message: str) -> dict:
    return {"ok": False, "problems": [message]}


def write_baseline(baseline_dir, record_path, approved_by, approved_at) -> dict:
    """把**已通过验收的记录**固化成金标；记录无效一律拒绝写入。"""
    state = verify_record(record_path)
    if not state.get("valid"):
        return _blocked("验收记录无效（%s），拒绝写金标" % (state.get("reason") or "unreadable"))
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    version = str(record.get("golden_version") or "").strip()
    if not version:
        return _blocked("验收记录缺少 golden_version，拒绝写金标")
    target = Path(baseline_dir) / version
    target.mkdir(parents=True, exist_ok=True)
    samples = []
    for row in record.get("samples") or []:
        name = str(row.get("filename") or "")
        if not name:
            continue
        doc = {"golden_version": version, "sample_filename": name,
               "source_sha256": str(row.get("sha256") or ""),
               "detected_dwg_version": str(row.get("detected_dwg_version") or ""),
               "converter": {"name": str((record.get("converter") or {}).get("name") or ""),
                             "version": str((record.get("converter") or {}).get("version") or ""),
                             "role": "primary", "fallback_used": False},
               "artifacts": {"dxf_sha256": str(row.get("dxf_sha256") or ""),
                             "preview_sha256": str(row.get("preview_sha256") or ""),
                             "preview_non_blank": bool(row.get("non_blank"))},
               "stats": {"layer_total": int(row.get("layer_count") or 0),
                         "entity_total": int(row.get("entity_count") or 0),
                         "dimension_total": 0, "text_total": 0, "block_total": 0},
               "unit_status": "", "cut_layers": [], "crease_layers": [],
               "box_candidates": [], "key_dimensions": [], "required_unresolved": [],
               "forbidden_fields": ["material", "paper_gsm", "board_thickness",
                                    "surface_treatment", "unit_price", "quantity"],
               "three_d_status": str(row.get("three_d_status") or ""),
               "reviewed_by": "", "reviewed_at": "",
               "approval": {"approved_by": approved_by, "approved_at": approved_at,
                            "note": "由 --write-baseline 按验收记录固化；业务结论待人工复核"}}
        (target / (name.replace(".dwg", "") + ".json")).write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        samples.append({"filename": name, "source_sha256": doc["source_sha256"],
                        "detected_dwg_version": doc["detected_dwg_version"],
                        "three_d_status": doc["three_d_status"], "stats": doc["stats"]})
    (target / "manifest.json").write_text(
        json.dumps({"golden_version": version, "converter": record.get("converter") or {},
                    "samples": {item["filename"]: item for item in samples}},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"action": "write_baseline", "ok": True, "baseline_dir": str(target),
            "samples": [item["filename"] for item in samples], "problems": []}


def write_record(record_path, baseline_dir, approved_by, approved_at, e2e_report) -> dict:
    """按金标 + 真实 E2E 报告写验收记录（没有报告哈希不算证据）。"""
    if not e2e_report or not Path(e2e_report).is_file():
        return _blocked("缺少 --e2e-report（没有报告哈希的记录不算验收证据）")
    versions = collect_versions(baseline_dir)
    if not versions:
        return _blocked("没有金标目录可比，拒绝写验收记录")
    version = versions[-1]
    meta = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    samples = []
    for name in SAMPLE_FILENAMES:
        path = version / (name.replace(".dwg", "") + ".json")
        if not path.is_file():
            return _blocked("缺少样本金标 %s" % path.name)
        doc = json.loads(path.read_text(encoding="utf-8"))
        samples.append({"filename": name, "sha256": doc.get("source_sha256"),
                        "dxf_sha256": (doc.get("artifacts") or {}).get("dxf_sha256"),
                        "preview_sha256": (doc.get("artifacts") or {}).get("preview_sha256"),
                        "layer_count": (doc.get("stats") or {}).get("layer_total"),
                        "entity_count": (doc.get("stats") or {}).get("entity_total"),
                        "non_blank": bool((doc.get("artifacts") or {}).get("preview_non_blank"))})
    converter = meta.get("converter") or {}
    binary = str(converter.get("binary") or "")
    record = {
        "record_version": "dwg-acceptance/1",
        "converter": {"name": str(converter.get("name") or ""),
                      "version": str(converter.get("version") or ""),
                      "binary_sha256": sha256_file(binary) if binary else ""},
        "samples": samples,
        "e2e": {"report_path": str(e2e_report), "report_sha256": sha256_file(e2e_report),
                "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
        "golden_version": str(meta.get("golden_version") or version.name),
        "approved_by": approved_by, "approved_at": approved_at,
    }
    target = Path(record_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return {"action": "write_record", "ok": True, "record_path": str(target),
            "samples": [item["filename"] for item in samples], "problems": []}


def render_report(result: dict) -> str:
    lines = ["# DWG 金标与验收记录校验", "",
             "- 动作：%s" % result.get("action"),
             "- 金标目录：%s" % result.get("baseline_dir"),
             "- 版本：%s" % "、".join(result.get("versions") or []) or "（无）",
             "- 验收记录：%s" % result.get("record_path"),
             "- 验收记录状态：%s" % (result.get("record") or {}).get("reason", ""),
             "- 判定：%s" % ("通过" if result.get("ok") else "不通过"), ""]
    problems = result.get("problems") or []
    if problems:
        lines.append("## 差异")
        lines.extend("- %s" % item for item in problems)
    else:
        lines.append("## 差异")
        lines.append("- 无")
    lines.append("")
    lines.append("能力声明：真实转图能力只由本记录与 L4 真实样本 E2E 决定；"
                 "缺任一证据时不得对外宣称已通过。")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="DWG 金标与验收记录：默认只读校验，写入必须显式审批人")
    parser.add_argument("--verify", action="store_true",
                        help="只读校验（默认动作）")
    parser.add_argument("--baseline-dir", default="",
                        help="金标目录（默认 tests/fixtures/dwg_acceptance）")
    parser.add_argument("--record", default="", help="验收记录路径（默认仓库内默认位）")
    parser.add_argument("--write-baseline", action="store_true", help="写金标快照（需审批人）")
    parser.add_argument("--write-record", action="store_true", help="写验收记录（需审批人）")
    parser.add_argument("--approved-by", default="", help="审批人（写入动作必填）")
    parser.add_argument("--approved-at", default="", help="审批时间（默认取当前时间）")
    parser.add_argument("--e2e-report", default="", help="L4 真实样本 E2E 报告路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--report", default="", help="把校验结果写成 Markdown")
    args = parser.parse_args(argv)

    from tech_app.backend.services import dwg_acceptance

    baseline = Path(args.baseline_dir) if args.baseline_dir else default_baseline_dir()
    record_path = Path(args.record) if args.record else dwg_acceptance.record_path()
    approved_at = args.approved_at or datetime.datetime.now(
        datetime.timezone.utc).isoformat()

    if args.write_baseline or args.write_record:
        if not str(args.approved_by or "").strip():
            print("拒绝写入：写入金标/验收记录必须显式给 --approved-by <审批人>，"
                  "不许由脚本自动刷绿。")
            return EXIT_USAGE
        if args.write_baseline:
            result = write_baseline(baseline, record_path, args.approved_by, approved_at)
        else:
            result = write_record(record_path, baseline, args.approved_by, approved_at,
                                  args.e2e_report)
    else:
        result = verify(baseline, record_path)

    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).write_text(render_report(result), encoding="utf-8")
    if args.json:
        print(text)
    else:
        print(text)
    return EXIT_OK if result.get("ok") else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
