# -*- coding: utf-8 -*-
"""真实样本 DWG 端到端转换（DWG 支持第 6 批 Spec §5 契约 E / §10）。

它做一件很窄的事：**拿一份真实 DWG，跑一次受控转换，把产物与统计如实打出来**。

  · 样本**只读**：读进来就算，不改、不入库、不上传、不联网、不读 .env；
  · 产物**只写** `--out`：正式 store 的产物目录/审计/清单全部重定向到调用方给的目录，
    绝不写真实 `tech_data`；
  · **不写金标**：金标只由 `tech_app/tools/dwg_acceptance_report.py` 写（Spec §4.2）；
  · `three_d_status` 由**分流状态机**（第 6 批 §1）从转换产物 + CAD IR 证据推出，
    不靠文件名、不靠预览图、不猜。

用法：
    python tech_app/tools/dwg_sample_e2e.py --sample <样本.dwg> --out <目录> [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CPQ_DIR = TOOLS_DIR.parent.parent
if str(CPQ_DIR) not in sys.path:
    sys.path.insert(0, str(CPQ_DIR))

#: E2E 专用项目 id：产物只落临时工作区，不出现在任何真实项目列表里。
PROJECT_ID = "dwg-sample-e2e"

STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "failed"

_OUTPUT_KEYS = ("path", "role", "sha256", "bytes")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _temp_workspace(root: Path):
    """把产物 / manifest / 审计重定向到临时目录，返回恢复函数（样本只读）。"""
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
        return {"filename": target.name, "sha256": _digest(payload), "bytes": len(payload)}

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

    def restore():
        for name, fn in saved.items():
            setattr(persistence, name, fn)

    return restore


def _copy_outputs(workspace: Path, out_dir: Path, manifest: dict):
    """把产物从临时工作区搬到 `--out`，返回带真实路径的 output_files。"""
    root = workspace / PROJECT_ID / str(manifest.get("conversion_id"))
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in manifest.get("output_files") or []:
        source = root / str(item.get("filename"))
        if not source.is_file():
            continue
        target = out_dir / source.name
        shutil.copyfile(source, target)
        payload = target.read_bytes()
        rows.append({"path": str(target), "role": str(item.get("role") or ""),
                     "sha256": _digest(payload), "bytes": len(payload)})
    return rows


def _three_d_status(manifest: dict, dxf_path: Path) -> str:
    """用第 6 批的分流状态机推出三维状态（只吃 manifest + CAD IR 证据）。"""
    from tech_app.backend.services import cad_ir, dwg_dispatch

    ir = None
    if dxf_path.is_file():
        try:
            ir = cad_ir.parse_dxf(dxf_path.read_bytes(), filename=dxf_path.name)
        except Exception:                                 # noqa: BLE001 - 解析不了就没有 IR 证据
            ir = None
    manifest_doc = {key: manifest.get(key) for key in
                    ("status", "converter_name", "converter_version", "converter_role",
                     "fallback_used", "primary_failure_code", "conversion_id",
                     "output_files")}
    for item in manifest_doc.get("output_files") or []:
        item = dict(item)
        item.setdefault("role", item.get("role"))
    if isinstance(manifest_doc.get("output_files"), list):
        root = Path(str(manifest.get("artifact_root") or ""))
        rows = []
        for item in manifest.get("output_files") or []:
            row = dict(item)
            if root and item.get("filename"):
                row["path"] = str(root / str(item["filename"]))
            rows.append(row)
        manifest_doc["output_files"] = rows
    previous = os.environ.get("DWG_DISPATCH_ENABLED")
    os.environ["DWG_DISPATCH_ENABLED"] = "true"
    try:
        classification = dwg_dispatch.classify(PROJECT_ID, manifest=manifest_doc, ir=ir,
                                              semantics=None)
        routed = dwg_dispatch.route(PROJECT_ID, classification=classification)
    finally:
        if previous is None:
            os.environ.pop("DWG_DISPATCH_ENABLED", None)
        else:
            os.environ["DWG_DISPATCH_ENABLED"] = previous
    return str(routed.get("status") or "3d_unknown")


def _stats(manifest: dict) -> dict:
    quality = manifest.get("quality") or {}
    return {
        "layer_total": int(quality.get("layer_count") or 0),
        "entity_total": int(quality.get("entity_count") or 0),
        "dimension_total": int(quality.get("dimension_count") or 0),
        "text_total": int(quality.get("text_count") or 0),
        "block_total": int(quality.get("block_ref_count") or 0),
    }


def _empty_payload(reason: str, *, status: str, returncode: int) -> dict:
    return {"status": status, "returncode": returncode, "reason": reason,
            "dxf_path": "", "preview_paths": [], "output_files": [],
            "detected_dwg_version": "", "three_d_status": "", "stats": {}}


def run(sample: Path, out_dir: Path) -> tuple:
    from tech_app.backend.services import cad_converter, file_preflight

    if not sample.is_file():
        return _empty_payload("样本不在本机：%s" % sample, status=STATUS_FAILED,
                              returncode=2), 2
    content = sample.read_bytes()
    detected = file_preflight.detect_file_format(sample.name, content)
    version = str(detected.get("dwg_version") or "")
    capability = cad_converter.capability()
    if not capability.get("available") or capability.get("simulated"):
        return _empty_payload("本机没有可用的真实 DWG 转换器（真实转换能力未验收）",
                              status=STATUS_UNAVAILABLE, returncode=3), 3

    workspace = Path(tempfile.mkdtemp(prefix="dwg-sample-e2e-"))
    restore = _temp_workspace(workspace)
    try:
        manifest = cad_converter.convert_drawing(PROJECT_ID, sample.name, content)
        workspace_root = workspace / PROJECT_ID / str(manifest.get("conversion_id"))
        enriched = dict(manifest)
        enriched["artifact_root"] = str(workspace_root)
        outputs = _copy_outputs(workspace, out_dir, manifest)
        dxf_path = next((Path(row["path"]) for row in outputs
                         if row["role"] in ("dxf", "dxf_2d")), None)
        previews = [row["path"] for row in outputs
                    if row["role"] in ("preview", "svg", "png", "pdf")]
        status = str(manifest.get("status") or "")
        payload = {
            "status": status,
            "returncode": 0 if status in ("ok", "success_with_warnings") else 1,
            "dxf_path": str(dxf_path) if dxf_path else "",
            "preview_paths": previews,
            "output_files": [{key: row[key] for key in _OUTPUT_KEYS} for row in outputs],
            "detected_dwg_version": version or str(manifest.get("detected_dwg_version") or ""),
            "three_d_status": _three_d_status(enriched, dxf_path) if dxf_path else "",
            "stats": _stats(manifest),
        }
        return payload, payload["returncode"]
    finally:
        restore()
        shutil.rmtree(workspace, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="真实样本 DWG 端到端转换（只读样本、只写 --out、不写金标）")
    parser.add_argument("--sample", required=True, help="样本 .dwg 路径（只读）")
    parser.add_argument("--out", required=True, help="产物输出目录（唯一写入位置）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（供 L4 红测解析）")
    args = parser.parse_args(argv)

    payload, code = run(Path(args.sample), Path(args.out))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
