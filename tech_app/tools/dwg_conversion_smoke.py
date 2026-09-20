# -*- coding: utf-8 -*-
"""DWG 受控转换冒烟：A 层 / B 层两层验收（Spec `dwg-controlled-conversion-adapter.md` §8）。

它回答的是一个**是非题**：这台机器到底能不能真把 DWG 转出可用的中间格式。
- 没装真实转换器 → 只打印 SKIP 与「A 层已验收 / B 层未验收」，退出码 0（不是失败）；
- 装了 → 对两份真实样本各转一次，逐项校验 DXF 可打开性、实体/图层数、预览非空白，
  任一项不满足就打印「B 层未通过」并返回非零退出码。

口径纪律：
  · 样本只读（`裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg`），不入库、不修改；
  · 产物与 manifest 全部落在临时目录，不写真实 tech_data、不写审计；
  · fake 产物**永远**不能当 B 层证据：`is_simulated is True` 直接判不通过。
  · `ezdxf` 属于后续批次依赖，这里缺失就降级成「DXF 可打开性未验证」，不假装验过。

用法：
    python tech_app/tools/dwg_conversion_smoke.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CPQ_DIR = TOOLS_DIR.parent.parent
if str(CPQ_DIR) not in sys.path:
    sys.path.insert(0, str(CPQ_DIR))

SAMPLES_DIR = CPQ_DIR / "裕同包装项目-待开发"
SAMPLE_NAMES = ("酒盒.dwg", "圆盘盒.dwg")
#: 冒烟专用项目 id：只落在临时工作区，不会出现在任何真实项目列表里。
PROJECT_ID = "dwg-smoke"

SKIP_LINE = "SKIP（未安装真实转换器）：A 层已验收 / B 层未验收"
LAYER_A_ONLY = "A 层通过"
LAYER_A_AND_B = "A+B 通过"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _temp_workspace(root: Path):
    """把产物 / manifest / 审计落到临时目录，返回恢复函数。"""
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


def _preview_looks_blank(data: bytes) -> bool:
    """粗判预览是不是一张空白页：跳过文件头后只剩单一字节值就算空白。"""
    body = data[64:]
    if not body:
        return True
    return len(set(body)) <= 1


def _check_preview(path: Path):
    data = path.read_bytes()
    if not data:
        return "预览为空：%s" % path.name
    if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"%PDF-")
            or data[:4096].lstrip().startswith(b"<?xml")
            or b"<svg" in data[:4096].lower()):
        return "预览 magic 不合法：%s" % path.name
    if _preview_looks_blank(data):
        return "预览疑似空白页：%s" % path.name
    return ""


def _check_dxf(path: Path):
    """DXF 可打开性与内容规模；缺 ezdxf 时返回 (problems, verified)。"""
    try:
        import ezdxf
    except ImportError:
        return [], False
    try:
        document = ezdxf.readfile(str(path))
    except Exception as exc:  # noqa: BLE001 - 冒烟只做是非判定
        return ["DXF 无法打开：%s: %s" % (type(exc).__name__, exc)], True
    entities = len(list(document.modelspace()))
    layers = len(list(document.layers))
    problems = []
    if entities <= 0:
        problems.append("DXF 实体数为 0（空图）")
    if layers <= 0:
        problems.append("DXF 图层数为 0")
    return problems, True


def _summarize(manifest: dict) -> dict:
    return {
        "conversion_id": manifest.get("conversion_id"),
        "status": manifest.get("status"),
        "warning_count": manifest.get("warning_count"),
        "error_count": manifest.get("error_count"),
        "warning_codes_truncated": manifest.get("warning_codes_truncated"),
        "diagnostics_bytes": manifest.get("diagnostics_bytes"),
        "quality": manifest.get("quality"),
        "detected_dwg_version": manifest.get("detected_dwg_version"),
        "converter": "%s %s" % (manifest.get("converter_name"),
                                manifest.get("converter_version")),
        "is_simulated": manifest.get("is_simulated"),
        "acceptance_level": manifest.get("acceptance_level"),
        "source_sha256": str(manifest.get("source_sha256") or "")[:16],
        "outputs": [{"role": item.get("role"), "filename": item.get("filename"),
                     "bytes": item.get("bytes"),
                     "sha256": str(item.get("sha256") or "")[:16]}
                    for item in manifest.get("output_files") or []],
        "warnings": manifest.get("warnings") or [],
    }


def main() -> int:
    from tech_app.backend.services import cad_converter

    capability = cad_converter.capability()
    print("capability()：" + json.dumps(capability, ensure_ascii=False, indent=2, sort_keys=True))

    if not capability.get("available") or capability.get("simulated"):
        print("")
        print(SKIP_LINE)
        print("结论：%s（真实转换器未安装，B 层一律不宣称通过）" % LAYER_A_ONLY)
        return 0

    workspace = Path(tempfile.mkdtemp(prefix="dwg-conv-smoke-"))
    restore = _temp_workspace(workspace)
    problems = []
    dxf_verified = True
    try:
        for name in SAMPLE_NAMES:
            sample = SAMPLES_DIR / name
            if not sample.exists():
                problems.append("样本不存在（只读样本不入库）：%s" % name)
                continue
            content = sample.read_bytes()
            print("")
            print("=== %s（%d B）" % (name, len(content)))
            manifest = cad_converter.convert_drawing(PROJECT_ID, name, content)
            print(json.dumps(_summarize(manifest), ensure_ascii=False, indent=2, sort_keys=True))

            if manifest.get("is_simulated") or manifest.get("acceptance_level") != "real":
                problems.append("%s：产物不是真实转换（is_simulated/acceptance_level 不符）" % name)
                continue
            # 质量门槛（修复批 Spec §4）：退出码 0 不算数，结构/实体/图层都要过。
            status = str(manifest.get("status") or "")
            if status not in ("ok", "success_with_warnings"):
                problems.append("%s：转换状态不是 ok/success_with_warnings（%r）" % (name, status))
            quality = manifest.get("quality") or {}
            if not quality.get("verified"):
                problems.append("%s：quality.verified 为假（质量门槛未真验过）" % name)
            if int(quality.get("entity_count") or 0) <= 0:
                problems.append("%s：quality.entity_count 为 0" % name)
            if int(quality.get("layer_count") or 0) <= 0:
                problems.append("%s：quality.layer_count 为 0" % name)
            if status == "ok" and (manifest.get("warning_count") or manifest.get("error_count")):
                problems.append("%s：报 ok 却带着警告/错误计数（状态机口径不符）" % name)
            roles = {}
            for item in manifest.get("output_files") or []:
                roles.setdefault(item.get("role"), []).append(item)
            if not roles.get("dxf"):
                problems.append("%s：没有 DXF 产物" % name)
            if not roles.get("preview"):
                problems.append("%s：没有预览产物" % name)
            root = workspace / PROJECT_ID / str(manifest.get("conversion_id"))
            for item in roles.get("dxf") or []:
                found, verified = _check_dxf(root / str(item.get("filename")))
                problems.extend("%s：%s" % (name, reason) for reason in found)
                dxf_verified = dxf_verified and verified
            for item in roles.get("preview") or []:
                reason = _check_preview(root / str(item.get("filename")))
                if reason:
                    problems.append("%s：%s" % (name, reason))
    finally:
        restore()
        shutil.rmtree(workspace, ignore_errors=True)

    print("")
    if not dxf_verified:
        print("提示：本机没有 ezdxf，DXF 可打开性未验证（第 3 批正式依赖）")
    if problems:
        print("B 层未通过（含质量门槛）：")
        for reason in problems:
            print("  · " + reason)
        print("结论：%s" % LAYER_A_ONLY)
        return 1
    print("B 层通过：两份样本都转出了非空 DXF 与合法预览，且质量门槛（结构/实体/图层）全过。")
    print("结论：%s" % LAYER_A_AND_B)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
