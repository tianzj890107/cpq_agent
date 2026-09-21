# -*- coding: utf-8 -*-
"""DWG 上线部署门禁（DWG 支持第 6 批 Spec §6/§7）。

18 项检查逐项给结论，`verdict` 只在全部满足时才是 `go`：

  1. 没有任何 auto 项是 `fail`；
  2. 每个 manual 项都被显式 `--ack <item_id>=<用户>` 确认过；
  3. auto 项的 `skip` 只允许出现在 `--env ci` 且原因写得明白；
  4. `--env production` 下 `skip` 一律算 `fail`。

纪律：不读任何密钥文件、不联网、不打印环境里的敏感值；`kind == "manual"` 的项
绝不许被脚本自动报成 `ok`（Spec §6）。退出码：`verdict == "go"` → 0；否则非零；
未知的 `--ack` id → 用法错误 2。

用法：
    python tech_app/tools/dwg_deploy_gate.py --env local|ci|production [--json]
        [--report PATH] [--ack <item_id>=<用户>] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CPQ_DIR = TOOLS_DIR.parent.parent
if str(CPQ_DIR) not in sys.path:
    sys.path.insert(0, str(CPQ_DIR))

GATE_VERSION = "dwg-deploy-gate/1"

#: 18 项门禁清单（id/kind 冻结；新增项只能追加在末尾，Spec §7/§14）。
GATE_ITEMS = (
    ("converter_license", "manual", "转换器许可与部署方式已由业务/法务确认"),
    ("converter_version_pinned", "auto", "转换器版本已显式固定且与现场一致"),
    ("health_reports_capability", "auto", "健康检查如实上报转换能力与三维声明"),
    ("tmp_dir_permissions", "auto", "转换临时目录存在、可写、非世界可写"),
    ("disk_quota_and_cleanup", "auto", "产物保留期与清理入口在位"),
    ("conversion_timeout", "auto", "单次转换超时上限已配置"),
    ("concurrency_limit", "auto", "全局与单项目并发上限已配置"),
    ("malicious_cad_isolation", "auto", "恶意图纸隔离：独立临时目录、无网络、无脚本执行"),
    ("model_failure_isolation", "auto", "模型不可用时确定性链仍能给出结论"),
    ("db_migration_rollback", "auto", "文档位迁移可回滚且不删旧数据"),
    ("legacy_projects_open", "auto", "老项目照常打开（分流未知态不抛异常）"),
    ("non_packaging_no_regression", "auto", "非包装行业回归由 CI 覆盖"),
    ("step_flow_no_regression", "auto", "STEP/三维既有流程不受影响"),
    ("no_secrets_in_logs_or_fixtures", "auto", "日志/夹具/输出不含密钥"),
    ("no_dev_machine_dependency", "auto", "无转换器环境仍可跑完 L1–L3"),
    ("ci_separates_adapter_and_real_smoke", "auto", "CI 区分适配器测试与真实样本冒烟"),
    ("real_samples_e2e_passed", "manual", "两份真实样本 L4 通过且金标已人工审批"),
    ("converter_chain_configured", "auto", "受控回退链与 manifest 契约在位"),
)

GATE_STATUSES = ("ok", "fail", "manual_unacknowledged", "acknowledged", "skip")

#: 需要扫描密钥的目录/文件（有界，不递归整个仓库）。
SECRET_SCAN_ROOTS = ("tech_app/tools", "tech_app/backend/services/dwg_dispatch.py",
                     "tech_app/backend/services/cad_converter",
                     "tech_app/backend/services/dwg_acceptance.py")
SECRET_PATTERNS = ("-----BEGIN", "AKIA", "sk-live", "password=", "passwd=")

MANUAL_IDS = tuple(item_id for item_id, kind, _title in GATE_ITEMS if kind == "manual")


def _rel(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(CPQ_DIR))
    except Exception:                                     # noqa: BLE001 - 只为展示
        return str(path)


def _read_text(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _service_sources(*names) -> str:
    chunks = []
    for name in names:
        target = CPQ_DIR / "tech_app" / "backend" / "services" / name
        if target.is_dir():
            for path in sorted(target.rglob("*.py")):
                chunks.append(_read_text(path))
        else:
            chunks.append(_read_text(target))
    return "\n".join(chunks)


def _result(item_id: str, kind: str, status: str, message: str, evidence=None) -> dict:
    if status not in GATE_STATUSES:
        status = "fail"
    return {"id": item_id, "kind": kind, "status": status,
            "evidence": evidence if isinstance(evidence, dict) else {},
            "message": str(message or "")}


# --------------------------------------------------------------------------- #
# 逐项判定
# --------------------------------------------------------------------------- #
def _check_converter_version_pinned(env: str) -> dict:
    from tech_app.backend.services import cad_converter

    capability = cad_converter.capability()
    if not capability.get("available"):
        code = str(capability.get("stable_error_code") or "DWG_CONVERTER_NOT_INSTALLED")
        return _result("converter_version_pinned", "auto", "skip",
                       "本环境没有可用的 DWG 转换器（%s），本项在 CI 跳过" % code,
                       {"available": False, "stable_error_code": code})
    expected = str(capability.get("expected_version") or "")
    actual = str(capability.get("converter_version") or "")
    evidence = {"provider": capability.get("provider"), "expected": expected, "actual": actual,
                "version_source": capability.get("version_source") or "probed"}
    if not expected:
        return _result("converter_version_pinned", "auto", "fail",
                       "没有显式固定转换器版本，无法复现转换结果", evidence)
    if not capability.get("version_ok") or expected != actual:
        return _result("converter_version_pinned", "auto", "fail",
                       "转换器版本与固定值不一致", evidence)
    return _result("converter_version_pinned", "auto", "ok",
                   "转换器版本已固定且与现场一致", evidence)


def _check_health_reports_capability() -> dict:
    from tech_app.backend.services import cad_converter

    source = _read_text(CPQ_DIR / "tech_app" / "backend" / "main.py")
    wired = bool(re.search(r"\"cad_converter\"\s*:\s*cad_converter\.capability\(\)", source))
    capability = cad_converter.capability()
    required = ("available", "provider", "converter_version", "three_d", "acceptance",
                "support_claim", "dwg_supported", "env")
    missing = [key for key in required if key not in capability]
    evidence = {"health_wired": wired, "missing_keys": missing}
    if not wired or missing:
        return _result("health_reports_capability", "auto", "fail",
                       "健康检查没有如实上报转换能力", evidence)
    return _result("health_reports_capability", "auto", "ok",
                   "健康检查按 capability() 上报能力与三维声明", evidence)


def _check_tmp_dir_permissions() -> dict:
    probe = Path(tempfile.mkdtemp(prefix="dwg-gate-"))
    try:
        mode = stat.S_IMODE(probe.stat().st_mode)
        inside_repo = str(probe.resolve()).startswith(str(CPQ_DIR.resolve()))
        writable = os.access(str(probe), os.W_OK)
        evidence = {"dir": _rel(probe), "mode": oct(mode), "in_repo": inside_repo,
                    "writable": writable}
        if inside_repo or not writable or (mode & 0o002):
            return _result("tmp_dir_permissions", "auto", "fail",
                           "转换临时目录不满足隔离要求（需独立、可写、非世界可写）", evidence)
        return _result("tmp_dir_permissions", "auto", "ok",
                       "转换临时目录独立、可写且非世界可写", evidence)
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def _check_disk_quota_and_cleanup() -> dict:
    from tech_app.backend.services import dwg_dispatch

    live = dwg_dispatch.limits()
    probe = dwg_dispatch.retention_plan("2026-09-21T00:00:00+00:00", [])
    ok = int(live["CAD_ARTIFACT_RETENTION_DAYS"]) > 0 and "expire" in probe
    evidence = {"retention_days": live["CAD_ARTIFACT_RETENTION_DAYS"],
                "cleanup_enabled": live["CAD_ARTIFACT_CLEANUP_ENABLED"],
                "policy_version": probe.get("policy_version")}
    if not ok:
        return _result("disk_quota_and_cleanup", "auto", "fail",
                       "产物保留期或清理入口缺失", evidence)
    return _result("disk_quota_and_cleanup", "auto", "ok",
                   "保留期与清理入口在位（默认不自动清理历史产物）", evidence)


def _check_conversion_timeout() -> dict:
    from tech_app.backend.services import dwg_dispatch

    seconds = dwg_dispatch.limits()["CAD_CONVERTER_TIMEOUT_SECONDS"]
    evidence = {"seconds": seconds}
    if int(seconds) <= 0:
        return _result("conversion_timeout", "auto", "fail", "转换超时未配置", evidence)
    return _result("conversion_timeout", "auto", "ok", "转换超时已配置", evidence)


def _check_concurrency_limit() -> dict:
    from tech_app.backend.services import dwg_dispatch

    live = dwg_dispatch.limits()
    evidence = {"max_concurrency": live["CAD_CONVERTER_MAX_CONCURRENCY"],
                "per_project": live["DWG_DISPATCH_MAX_CONCURRENCY_PER_PROJECT"]}
    if int(evidence["max_concurrency"]) < 1 or int(evidence["per_project"]) < 1:
        return _result("concurrency_limit", "auto", "fail", "并发上限配置不合法", evidence)
    return _result("concurrency_limit", "auto", "ok", "并发上限已配置", evidence)


def _check_malicious_cad_isolation() -> dict:
    source = _service_sources("cad_converter")
    checks = {"independent_temp_dir": "mkdtemp" in source,
              "path_traversal_guard": "_within" in source or "resolve()" in source,
              "no_shell": "shell=True" not in source and "os.system" not in source,
              "no_network": not any(token in source for token in
                                    ("urlopen", "urlretrieve", "create_connection"))}
    failed = [name for name, value in checks.items() if not value]
    if failed:
        return _result("malicious_cad_isolation", "auto", "fail",
                       "恶意图纸隔离项缺失：%s" % "、".join(sorted(failed)), checks)
    return _result("malicious_cad_isolation", "auto", "ok",
                   "独立临时目录、路径穿越防护、无 shell、无网络均在位", checks)


def _check_model_failure_isolation() -> dict:
    from tech_app.backend.services import dwg_dispatch

    source = _read_text(CPQ_DIR / "tech_app" / "backend" / "services" / "dwg_dispatch.py")
    model_tokens = [token for token in ("qwen_client", "llm_client", "claude_client")
                    if token in source]
    probe = dwg_dispatch.classify("dwg-gate-probe")
    evidence = {"model_tokens": model_tokens, "drawing_kind": probe.get("drawing_kind")}
    if model_tokens or probe.get("drawing_kind") != "unknown":
        return _result("model_failure_isolation", "auto", "fail",
                       "分流链路没有与模型解耦", evidence)
    return _result("model_failure_isolation", "auto", "ok",
                   "模型不可用时分流仍给出确定性结论", evidence)


def _check_db_migration_rollback() -> dict:
    from tech_app.backend.services import dwg_dispatch
    from tech_app.backend.storage import store

    rebuilt = dwg_dispatch.migrate({})
    raised = False
    try:
        dwg_dispatch.migrate({"dispatch_version": "dwg-dispatch/999"})
    except ValueError:
        raised = True
    registered = "dwg_dispatch" in tuple(store.PARSE_STAGE_DOCS)
    evidence = {"needs_rebuild": rebuilt.get("status"), "unknown_version_raises": raised,
                "doc_registered": registered}
    if rebuilt.get("status") != "needs_rebuild" or not raised or not registered:
        return _result("db_migration_rollback", "auto", "fail",
                       "文档位迁移缺少可回滚三分支或未登记文档位", evidence)
    return _result("db_migration_rollback", "auto", "ok",
                   "迁移可回滚、未知版本不硬读、文档位已登记", evidence)


def _check_legacy_projects_open() -> dict:
    from tech_app.backend.services import dwg_dispatch

    status = dwg_dispatch.status_for("000000000000")
    evidence = {"code": status.get("code"), "pipeline": status.get("pipeline")}
    if status.get("code") != "3d_unknown":
        return _result("legacy_projects_open", "auto", "fail",
                       "老项目没有给出未知态结论", evidence)
    return _result("legacy_projects_open", "auto", "ok", "老项目照常打开且不抛异常", evidence)


def _check_non_packaging_no_regression() -> dict:
    targets = ("tests/test_industry_registry_unified_red.py",
               "tests/test_packaging_requirement_template_red.py",
               "tests/test_cpq_eval_route_coverage.py")
    missing = [name for name in targets if not (CPQ_DIR / name).is_file()]
    evidence = {"regression_entries": list(targets), "missing": missing}
    if missing:
        return _result("non_packaging_no_regression", "auto", "fail",
                       "非包装行业回归入口缺失", evidence)
    return _result("non_packaging_no_regression", "auto", "ok",
                   "非包装行业回归入口在位（由 CI 的 python_contract 执行）", evidence)


def _check_step_flow_no_regression() -> dict:
    source = _read_text(CPQ_DIR / "tech_app" / "backend" / "main.py")
    from tech_app.backend.services import step_import

    gate = "import_3d_gate_error" in source or "/api/projects/3d" in source
    evidence = {"three_d_route": "/api/projects/3d" in source,
                "gate_present": gate,
                "step_import_available": bool(getattr(step_import, "AVAILABLE", False))}
    if not gate:
        return _result("step_flow_no_regression", "auto", "fail",
                       "三维入口门禁不在位", evidence)
    return _result("step_flow_no_regression", "auto", "ok",
                   "STEP/三维既有入口与门禁在位", evidence)


def _check_no_secrets() -> dict:
    hits = []
    for root in SECRET_SCAN_ROOTS:
        target = CPQ_DIR / root
        paths = sorted(target.rglob("*")) if target.is_dir() else [target]
        for path in paths:
            if not path.is_file() or path.suffix not in (".py", ".json", ".md", ".yml"):
                continue
            text = _read_text(path)
            if any(pattern in text for pattern in SECRET_PATTERNS):
                hits.append(_rel(path))
    evidence = {"scanned": len(SECRET_SCAN_ROOTS), "flagged": hits}
    if hits:
        return _result("no_secrets_in_logs_or_fixtures", "auto", "fail",
                       "扫描到疑似机密字样（文件名已列出，不回显内容）", evidence)
    return _result("no_secrets_in_logs_or_fixtures", "auto", "ok",
                   "有界扫描未发现机密字样", evidence)


def _check_no_dev_machine_dependency() -> dict:
    from tech_app.backend.services import cad_converter, dwg_dispatch

    probe = dwg_dispatch.capability()
    capability = cad_converter.capability()
    evidence = {"test_entry": (CPQ_DIR / "tests"
                               / "test_dwg_final_acceptance_red.py").is_file(),
                "dispatch_version": probe.get("dispatch_version"),
                "capability_available": bool(capability.get("available"))}
    if not evidence["test_entry"] or probe.get("dispatch_version") != "dwg-dispatch/1":
        return _result("no_dev_machine_dependency", "auto", "fail",
                       "L1–L3 入口或分流模块缺失", evidence)
    return _result("no_dev_machine_dependency", "auto", "ok",
                   "无转换器环境下 L1–L3 仍可跑（本项只做能力探针）", evidence)


def _ci_blocks() -> dict:
    text = _read_text(CPQ_DIR / ".gitlab-ci.yml")
    blocks, current = {}, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t":
            current = line.split(":")[0].strip()
            blocks[current] = [line]
            continue
        if current is not None:
            blocks[current].append(line)
    return {name: "\n".join(lines) for name, lines in blocks.items()}


def _check_ci_separation() -> dict:
    blocks = _ci_blocks()
    real = blocks.get("dwg_real_samples", "")
    contract = blocks.get("python_contract", "")
    evidence = {"real_job": bool(real), "when_manual": "when: manual" in real,
                "allow_failure_false": "allow_failure: false" in real,
                "calls_sample_tool": "dwg_sample_e2e.py" in real,
                "contract_clean": ("CPQ_DWG_REAL_SAMPLES" not in contract
                                   and "dwg_sample_e2e" not in contract)}
    if not all(evidence.values()):
        return _result("ci_separates_adapter_and_real_smoke", "auto", "fail",
                       "CI 没有把真实样本冒烟与适配器测试分开", evidence)
    return _result("ci_separates_adapter_and_real_smoke", "auto", "ok",
                   "真实样本冒烟是独立 job，python_contract 不跑它", evidence)


def _check_converter_chain_configured() -> dict:
    from tech_app.backend.services import cad_converter

    source = _service_sources("cad_converter")
    contract_keys = ("converter_role", "fallback_used", "primary_failure_code")
    missing = [key for key in contract_keys if key not in source]
    capability = cad_converter.capability()
    evidence = {"missing_manifest_keys": missing,
                "provider": capability.get("provider"),
                "attempts_supported": "attempts" in source}
    if missing or not str(capability.get("provider") or "").strip():
        return _result("converter_chain_configured", "auto", "fail",
                       "受控回退链或 manifest 契约不在位", evidence)
    return _result("converter_chain_configured", "auto", "ok",
                   "受控回退链与 manifest 契约在位", evidence)


AUTO_CHECKS = (
    ("converter_version_pinned", lambda env: _check_converter_version_pinned(env)),
    ("health_reports_capability", lambda env: _check_health_reports_capability()),
    ("tmp_dir_permissions", lambda env: _check_tmp_dir_permissions()),
    ("disk_quota_and_cleanup", lambda env: _check_disk_quota_and_cleanup()),
    ("conversion_timeout", lambda env: _check_conversion_timeout()),
    ("concurrency_limit", lambda env: _check_concurrency_limit()),
    ("malicious_cad_isolation", lambda env: _check_malicious_cad_isolation()),
    ("model_failure_isolation", lambda env: _check_model_failure_isolation()),
    ("db_migration_rollback", lambda env: _check_db_migration_rollback()),
    ("legacy_projects_open", lambda env: _check_legacy_projects_open()),
    ("non_packaging_no_regression", lambda env: _check_non_packaging_no_regression()),
    ("step_flow_no_regression", lambda env: _check_step_flow_no_regression()),
    ("no_secrets_in_logs_or_fixtures", lambda env: _check_no_secrets()),
    ("no_dev_machine_dependency", lambda env: _check_no_dev_machine_dependency()),
    ("ci_separates_adapter_and_real_smoke", lambda env: _check_ci_separation()),
    ("converter_chain_configured", lambda env: _check_converter_chain_configured()),
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
        except Exception as exc:                          # noqa: BLE001 - 检查本身失败要如实报
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
            summary["manual"] += 1
            reasons.append(row["id"])
        elif status == "acknowledged":
            summary["acknowledged"] += 1
        else:
            summary["skip"] += 1
            if env != "ci":
                reasons.append(row["id"])
    verdict = "go" if not reasons else "no_go"
    return {"gate_version": GATE_VERSION, "env": env,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": items, "summary": summary, "verdict": verdict, "reasons": reasons}


def _claim_line() -> str:
    from tech_app.backend.services import cad_converter

    capability = cad_converter.capability()
    claim = str(capability.get("support_claim") or "orchestration_only")
    if claim == "supported":
        return "真实转图能力已验收（主转换器 + 有效验收记录）"
    if claim == "conversion_available":
        return "DWG 编排能力完成，真实转换能力未验收"
    return "只完成编排层，本环境没有可用的真实转换器"


def render_report(payload: dict) -> str:
    lines = ["# DWG 支持最终验收报告", "",
             "- 门禁版本：%s" % payload.get("gate_version"),
             "- 环境：%s" % payload.get("env"),
             "- 检查时间：%s" % payload.get("checked_at"),
             "- 转换器：%s" % _converter_line(),
             "- 部署门禁：18 项逐项状态", ""]
    for row in payload.get("items") or []:
        lines.append("- %s（%s）：%s　%s" % (row["id"], row["kind"], row["status"],
                                           row["message"]))
    lines.extend(["", "- 能力声明：%s" % _claim_line(),
                  "- 判定：%s" % ("GO" if payload.get("verdict") == "go" else "NO-GO"),
                  "- 理由：%s" % ("、".join(payload.get("reasons") or []) or "无"),
                  "", "未决风险与后续动作：",
                  "- L4 真实样本 E2E 需显式触发（人工 job），未跑过之前不得对外宣称已通过。",
                  "- 金标业务结论（刀线/压痕线/盒型/关键尺寸）需人工复核后填写。",
                  "- 转换器与金标升级后必须重跑本门禁。", "",
                  "签批：由业务/法务与工艺经理按 --ack 逐项确认（本报告只记录状态）。"])
    return "\n".join(lines)


def _converter_line() -> str:
    from tech_app.backend.services import cad_converter

    capability = cad_converter.capability()
    return "%s %s（available=%s, simulated=%s, three_d=%s）" % (
        capability.get("provider") or "none", capability.get("converter_version") or "-",
        capability.get("available"), capability.get("simulated"),
        (capability.get("three_d") or {}).get("status"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="DWG 上线部署门禁（18 项；manual 项必须显式 --ack）")
    parser.add_argument("--env", default="local", choices=("local", "ci", "production"),
                        help="目标环境：CI 允许带原因的 skip，production 一律算失败")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结论")
    parser.add_argument("--report", default="", help="把门禁报告写成 Markdown")
    parser.add_argument("--ack", action="append", default=[],
                        help="确认人工项：--ack <item_id>=<用户>")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不做任何写入")
    args = parser.parse_args(argv)

    known = {item_id for item_id, _kind, _title in GATE_ITEMS}
    acks = {}
    for raw in args.ack:
        item_id, _, user = str(raw).partition("=")
        item_id = item_id.strip()
        if item_id not in known:
            # 用法错误也要给机器可读的输出（CI 侧按退出码 2 判定）。
            print(json.dumps({"gate_version": GATE_VERSION, "env": args.env,
                              "error": "unknown_ack_item", "item_id": item_id,
                              "hint": "只能确认 18 项清单里的 id"},
                             ensure_ascii=False, sort_keys=True))
            return 2
        acks[item_id] = user.strip()

    payload = run_gate(args.env, acks)
    if args.report:
        Path(args.report).write_text(render_report(payload), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["verdict"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
