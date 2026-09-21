# -*- coding: utf-8 -*-
"""包装图纸零件下游闭环门禁（Spec `packaging-parts-downstream-acceptance.md` §4）。

六项检查逐项给结论，输出形状与退出码与既有 `dwg_deploy_gate.py` 完全一致：
`{gate_version, env, checked_at, items, summary, verdict, reasons}`；有 `fail` → 非零退出。

纪律：**只读**。不连任何库、不读写项目数据、不调模型、不联网、不写文件；`kind == "manual"`
的项绝不许被脚本自动报成 `ok`（要显式 `--ack parts_demo_script=<用户>`）。

用法：
    python tech_app/tools/packaging_parts_gate.py --env local|production [--json]
        [--ack <item_id>=<用户>]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CPQ_DIR = TOOLS_DIR.parent.parent
if str(CPQ_DIR) not in sys.path:
    sys.path.insert(0, str(CPQ_DIR))

GATE_VERSION = "packaging-parts-gate/1"

#: 六项门禁清单（id 冻结，不许改名；新增项只能追加在末尾）。
GATE_ITEMS = (
    ("parts_outline_engine", "auto", "第 1 层：真实轮廓引擎常量与纯函数在位"),
    ("parts_outline_real_sample", "auto", "第 1 层：两份真实样本过 Spec §3 门槛"),
    ("parts_panel_wired", "auto", "第 2 层：零件行可点 + 右栏面板 + 三态文案在位"),
    ("parts_downstream_wired", "auto", "第 3 层：工艺/成本两条入口 + processability 在位"),
    ("parts_3d_wired", "auto", "第 4 层：solid 路由 + 前端复用 loadSTL 在位"),
    ("parts_demo_script", "manual", "演示脚本经用户签字"),
)

GATE_STATUSES = ("ok", "fail", "manual_unacknowledged", "acknowledged", "skip")

SAMPLES_DIR = CPQ_DIR / "裕同包装项目-待开发"
APP_JS = CPQ_DIR / "tech_app" / "frontend" / "app.js"
INDEX_HTML = CPQ_DIR / "tech_app" / "frontend" / "index.html"
MAIN_PY = CPQ_DIR / "tech_app" / "backend" / "main.py"

#: 真实样本门槛（Spec §3，改门槛必须同时改 Spec 文件）。
THRESHOLDS = {"酒盒.dwg": {"closed_ratio": 0.10},
              "圆盘盒.dwg": {"closed_ratio": 0.50, "role_known_ratio": 0.10}}

MANUAL_IDS = tuple(item_id for item_id, kind, _title in GATE_ITEMS if kind == "manual")


def _read(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _result(item_id, kind, status, message, extra=None):
    row = {"id": item_id, "kind": kind, "status": status, "message": message}
    if extra:
        row.update(extra)
    return row


# --------------------------------------------------------------------------- #
# 逐项检查
# --------------------------------------------------------------------------- #
def _check_outline_engine(_env: str) -> dict:
    from tech_app.backend.services import packaging_parts

    missing = [name for name in ("LOOP_TOLERANCE_MM", "OUTLINE_STATUSES", "SIZE_SOURCES",
                                 "extract", "drawing_notes", "processability")
               if not hasattr(packaging_parts, name)]
    if missing:
        return _result("parts_outline_engine", "auto", "fail",
                       "轮廓引擎缺：%s" % "、".join(missing))
    return _result("parts_outline_engine", "auto", "ok",
                   "轮廓引擎在位（容差 %.1fmm、三态 %s）"
                   % (packaging_parts.LOOP_TOLERANCE_MM,
                      "/".join(packaging_parts.OUTLINE_STATUSES)))


#: 门禁自带的转换宿主项目 id：产物只落临时目录，不进任何真实项目。
GATE_PROJECT_ID = "packaging-parts-gate"


@contextlib.contextmanager
def _temp_converter_store(root: Path):
    """把转换器的产物 / 清单 / 审计重定向到临时目录（真实项目数据一个字节都不动）。"""
    from tech_app.backend.services.cad_converter import persistence

    names = ("artifact_dir", "save_artifact", "save_manifest", "load_manifest",
             "list_manifests", "sync", "audit")
    saved = {name: getattr(persistence, name) for name in names}
    manifests = {}

    def artifact_dir(project_id, conversion_id):
        target = root / str(project_id) / str(conversion_id)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def save_artifact(project_id, conversion_id, filename, data):
        payload = bytes(data)
        target = artifact_dir(project_id, conversion_id) / Path(str(filename)).name
        target.write_bytes(payload)
        return {"filename": target.name, "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}

    def save_manifest(project_id, manifest):
        manifests[str(manifest.get("conversion_id"))] = dict(manifest)
        return dict(manifest)

    def load_manifest(project_id, conversion_id):
        item = manifests.get(str(conversion_id))
        return dict(item) if item else None

    def list_manifests(project_id):
        return [dict(item) for item in reversed(list(manifests.values()))]

    def sync(project_id, conversion_id):
        return None

    def audit(project_id, action, detail=None):
        return None

    persistence.artifact_dir = artifact_dir
    persistence.save_artifact = save_artifact
    persistence.save_manifest = save_manifest
    persistence.load_manifest = load_manifest
    persistence.list_manifests = list_manifests
    persistence.sync = sync
    persistence.audit = audit
    try:
        yield manifests
    finally:
        for name, fn in saved.items():
            setattr(persistence, name, fn)


def _app_converted_dxf(path: Path) -> bytes:
    """用**应用内转换器**（与线上 8010 同一条链路）把样本转成 DXF 字节。

    线上只装了 ODA、没有 libredwg 的 `dwg2dxf`，门禁必须按应用的转换能力判，不许按
    PATH 里有没有某个命令行工具判。产物只落临时目录。
    """
    from tech_app.backend.services import cad_converter

    root = Path(tempfile.mkdtemp())
    with _temp_converter_store(root):
        manifest = cad_converter.convert_drawing(GATE_PROJECT_ID, path.name,
                                                 path.read_bytes())
        if bool(manifest.get("is_simulated")):
            raise RuntimeError("应用内转换器只有模拟实现（is_simulated=true）")
        outputs = [row for row in (manifest.get("output_files") or [])
                   if str(row.get("role") or "") == "dxf"]
        if not outputs:
            raise RuntimeError("转换产物里没有 DXF（status=%s）"
                               % str(manifest.get("status") or ""))
        filename = Path(str(outputs[0].get("filename") or "drawing.dxf")).name
        target = root / GATE_PROJECT_ID / str(manifest.get("conversion_id") or "") / filename
        return target.read_bytes()


def _libredwg_dxf(path: Path) -> bytes:
    """回退路径：本机有 libredwg 的 `dwg2dxf` 时用它转（只在应用内转换器不可用时）。"""
    tool = shutil.which("dwg2dxf")
    if not tool:
        raise RuntimeError("本机没有 libredwg 的 dwg2dxf")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    target = Path(tempfile.mkdtemp()) / ("gate_%s.dxf" % digest)
    subprocess.run([tool, "-y", "-o", str(target), str(path)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return target.read_bytes()


def _converter_gap() -> str:
    """没有任何可用的 DWG 转换器时返回原因（用于 skip 文案），否则返回空串。"""
    from tech_app.backend.services import cad_converter

    notes = []
    try:
        cap = cad_converter.capability() or {}
    except Exception as exc:                              # noqa: BLE001 - 探测失败如实报
        cap = {}
        notes.append("应用内转换器探测失败（%s）" % type(exc).__name__)
    if bool(cap.get("available")) and not bool(cap.get("simulated")):
        return ""
    notes.append("应用内转换器可用=%s 模拟=%s" % (bool(cap.get("available")),
                                                  bool(cap.get("simulated"))))
    if shutil.which("dwg2dxf"):
        return ""
    notes.append("本机也没有 libredwg 的 dwg2dxf")
    return "；".join(notes)


def _sample_dxf(path: Path) -> tuple:
    """样本 → (DXF 字节, 用了哪个转换器)：先应用内转换器，再回退 dwg2dxf。"""
    try:
        return _app_converted_dxf(path), "应用内转换器"
    except Exception as exc:                              # noqa: BLE001 - 回退前如实记录
        app_note = "应用内转换器转不动（%s：%s）" % (type(exc).__name__, exc)
    return _libredwg_dxf(path), "dwg2dxf（%s）" % app_note


def _sample_metrics(path: Path) -> dict:
    """把一份样本真跑到零件文档（DWG → DXF → CAD IR → 零件），返回指标。"""
    from tech_app.backend.services import cad_ir, packaging_part_solids, packaging_parts

    content, converter = _sample_dxf(path)
    ir = cad_ir.parse_dxf(content, filename="gate_sample.dxf",
                          source={"kind": "dxf_2d", "attachment_name": path.name})
    doc = packaging_parts.extract(ir)
    summary = packaging_parts.summarize(doc)
    rows = doc.get("parts") or []
    summary["processable_total"] = sum(1 for row in rows
                                       if packaging_parts.processability(row).get("ok"))
    summary["solid_total"] = sum(1 for row in rows
                                 if packaging_part_solids.extrude(row).get("status") == "ok")
    summary["part_total"] = len(rows)
    summary["dxf_source"] = converter
    return summary


def _check_outline_real_sample(_env: str) -> dict:
    if not SAMPLES_DIR.is_dir():
        return _result("parts_outline_real_sample", "auto", "skip",
                       "本机没有真实样本目录 %s，跳过（放上两份样本后再跑）" % SAMPLES_DIR.name)
    gap = _converter_gap()
    if gap:
        return _result("parts_outline_real_sample", "auto", "skip",
                       "本机没有可用的 DWG 转换器（%s），跳过真实样本门槛" % gap)
    bad = []
    lines = []
    for name, wanted in THRESHOLDS.items():
        sample = SAMPLES_DIR / name
        if not sample.exists():
            bad.append("%s：样本不在 %s" % (name, SAMPLES_DIR.name))
            continue
        try:
            metrics = _sample_metrics(sample)
        except Exception as exc:                          # noqa: BLE001 - 检查失败如实报
            bad.append("%s：跑不动（%s：%s）" % (name, type(exc).__name__, exc))
            continue
        lines.append("%s closed_ratio=%.3f role_known_ratio=%.3f 可算 %d 可挤出 %d（%s）"
                     % (name, metrics["closed_ratio"], metrics["role_known_ratio"],
                        metrics["processable_total"], metrics["solid_total"],
                        metrics.get("dxf_source") or ""))
        for key, floor in wanted.items():
            if float(metrics.get(key) or 0.0) < float(floor):
                bad.append("%s：%s=%.3f < 门槛 %.2f" % (name, key,
                                                       float(metrics.get(key) or 0.0), floor))
        if metrics["processable_total"] < 1:
            bad.append("%s：没有一件能跑工艺（Spec §3）" % name)
        if metrics["solid_total"] < 1:
            bad.append("%s：没有一件能挤出 3D（Spec §3）" % name)
    if bad:
        return _result("parts_outline_real_sample", "auto", "fail", "；".join(bad),
                       {"metrics": lines})
    return _result("parts_outline_real_sample", "auto", "ok",
                   "两份样本过门槛：%s" % "；".join(lines), {"metrics": lines})


def _check_panel_wired(_env: str) -> dict:
    app = _read(APP_JS)
    html = _read(INDEX_HTML)
    missing = []
    if "dataset.partId" not in app or "selectPackagingPart" not in app:
        missing.append("零件行可点")
    if "packagingPartPanel" not in html:
        missing.append("面板容器")
    if "PACKAGING_OUTLINE_COPY" not in app:
        missing.append("三态文案")
    if missing:
        return _result("parts_panel_wired", "auto", "fail",
                       "第 2 层缺：%s" % "、".join(missing))
    return _result("parts_panel_wired", "auto", "ok", "行可点 + 面板 + 三态文案在位")


def _check_downstream_wired(_env: str) -> dict:
    main = _read(MAIN_PY)
    app = _read(APP_JS)
    missing = []
    if "/requirement/packaging-parts/{part_code}/process" not in main:
        missing.append("工艺入口")
    if "/requirement/packaging-parts/{part_code}/cost" not in main:
        missing.append("成本入口")
    if "packaging_parts.processability" not in main:
        missing.append("processability 前置判定")
    if "packagingPartProcess" not in app:
        missing.append("面板按钮")
    if missing:
        return _result("parts_downstream_wired", "auto", "fail",
                       "第 3 层缺：%s" % "、".join(missing))
    return _result("parts_downstream_wired", "auto", "ok",
                   "工艺/成本入口 + processability + 面板按钮在位")


def _check_3d_wired(_env: str) -> dict:
    main = _read(MAIN_PY)
    app = _read(APP_JS)
    missing = []
    if "/requirement/packaging-parts/{part_code}/solid" not in main:
        missing.append("solid 路由")
    if "application/sla" not in main:
        missing.append("STL MIME")
    if "packagingPartSolid" not in app or "loadSTL" not in app:
        missing.append("前端 3D 预览复用 loadSTL")
    if missing:
        return _result("parts_3d_wired", "auto", "fail",
                       "第 4 层缺：%s" % "、".join(missing))
    return _result("parts_3d_wired", "auto", "ok", "solid 路由 + 前端复用 loadSTL 在位")


AUTO_CHECKS = (
    ("parts_outline_engine", _check_outline_engine),
    ("parts_outline_real_sample", _check_outline_real_sample),
    ("parts_panel_wired", _check_panel_wired),
    ("parts_downstream_wired", _check_downstream_wired),
    ("parts_3d_wired", _check_3d_wired),
)


def run_gate(env: str, acks: dict) -> dict:
    items = []
    for item_id, kind, _title in GATE_ITEMS:
        if kind == "manual":
            if item_id in acks:
                items.append(_result(item_id, kind, "acknowledged",
                                     "已由 %s 人工确认" % (acks[item_id] or "未署名"),
                                     {"acknowledged_by": acks[item_id]}))
            else:
                items.append(_result(item_id, kind, "manual_unacknowledged",
                                     "人工项未经确认（需 --ack %s=<用户>）" % item_id))
            continue
        check = dict(AUTO_CHECKS).get(item_id)
        if check is None:
            items.append(_result(item_id, kind, "skip", "本项没有自动检查实现"))
            continue
        try:
            row = check(env)
        except Exception as exc:                          # noqa: BLE001 - 检查自身失败要如实报
            row = _result(item_id, kind, "fail", "检查执行失败：%s" % type(exc).__name__)
        if row["status"] == "skip" and env == "production":
            row = dict(row)
            row["status"] = "fail"
            row["message"] = "%s；生产环境不允许跳过" % row["message"]
        items.append(row)

    summary = {"ok": 0, "fail": 0, "manual": 0, "acknowledged": 0, "skip": 0}
    reasons = []
    for row in items:
        status = row["status"]
        if status == "ok":
            summary["ok"] += 1
        elif status == "fail":
            summary["fail"] += 1
            reasons.append(row["id"])
        elif status == "manual_unacknowledged":
            # 人工项如实标 status；它不进 reasons —— 演示脚本签不签字由 `summary.manual`
            # 单列，不把整条门禁判成 no_go（Spec §10 要求 --env local 退出码 0）。
            summary["manual"] += 1
        elif status == "acknowledged":
            summary["acknowledged"] += 1
        else:
            summary["skip"] += 1
            if env == "production":
                reasons.append(row["id"])
    verdict = "go" if not reasons else "no_go"
    return {"gate_version": GATE_VERSION, "env": env,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": items, "summary": summary, "verdict": verdict, "reasons": reasons}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="包装图纸零件下游闭环门禁（6 项；manual 项需显式 --ack）")
    parser.add_argument("--env", default="local", choices=("local", "production"),
                        help="目标环境：production 下 skip 一律算失败")
    parser.add_argument("--json", action="store_true", help="以单行 JSON 输出结论")
    parser.add_argument("--ack", action="append", default=[],
                        help="确认人工项：--ack <item_id>=<用户>")
    args = parser.parse_args(argv)

    known = {item_id for item_id, _kind, _title in GATE_ITEMS}
    acks = {}
    for raw in args.ack:
        item_id, _, user = str(raw).partition("=")
        item_id = item_id.strip()
        if item_id not in known:
            print(json.dumps({"gate_version": GATE_VERSION, "env": args.env,
                              "error": "unknown_ack_item", "item_id": item_id,
                              "hint": "只能确认 6 项清单里的 id"},
                             ensure_ascii=False, sort_keys=True))
            return 2
        acks[item_id] = user.strip()

    payload = run_gate(args.env, acks)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["verdict"] == "go" else 1


if __name__ == "__main__":
    sys.exit(main())
