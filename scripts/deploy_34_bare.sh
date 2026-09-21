#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# 172.16.10.34 裸进程部署 —— DWG 转换器上线口径的**唯一可执行版本**。
#
# 用法（在 34 上、以部署账号 wugefei 运行；默认部署 ytbz 的最新提交）：
#
#     bash scripts/deploy_34_bare.sh [<ref>]
#
# 为什么要脚本：8010 的 DWG 转换能力分散在三处，**少任何一处都会「主转换器失败 → 静默回退
# LibreDWG」**，而页面上只表现为「不是位图，请传 PNG」：
#
#   ① 仓库外的 env 文件（`DWG_CONVERTER_*`，权限 0600，不写进仓库）——env 名从
#      `tech_app/backend/services/cad_converter/service.py` 的常量取，不手抄；
#   ② **启动命令行的 `PATH` 前缀** —— `xvfb-run` 是 shell 脚本、按名字去调同目录的 `Xvfb`；
#      写进 env 文件**对服务无效**（`load_dotenv(override=False)` 不覆盖已存在的 `PATH`，34 实测）；
#   ③ 重启顺序：先停 8012 子进程、再停 8010 父进程，轮询端口释放后按原命令行重启。
#
# 脚本末尾**真转两份真实样本**并核对 `converter_role="primary"`、`fallback_used=false`：
# 只有 `status=ok` 是不够的 —— 回退顶上时 `status` 一样是 ok。
#
# 只做部署：不连数据库、不改仓库内文件、不装依赖、不打印任何密钥（env 文件只打印变量名）。
set -uo pipefail

REPO="${CPQ_REPO_DIR:-/home/wugefei/CPQ/cpq_agent}"
ENVF="${CPQ_ENV_FILE_PATH:-/home/wugefei/CPQ/cpq_env.sh}"
XVFB_BIN_DIR="/home/data/cpq-tools/xvfb-user/root/usr/bin"
REF="${1:-ytbz}"
PY="$REPO/open-claude/.venv/bin/python"

fail() { echo "✗ $*" >&2; exit 1; }
step() { echo; echo "== $* =="; }

[ -d "$REPO" ] || fail "部署目录不存在：$REPO"
[ -x "$PY" ] || fail "找不到部署用的 python：$PY"
cd "$REPO" || fail "进不去 $REPO"

# --------------------------------------------------------------------------- #
step "0. 部署前状态"
OLD_HEAD="$(git -c safe.directory="$PWD" rev-parse --short HEAD)"
echo "HEAD=$OLD_HEAD  branch=$(git -c safe.directory="$PWD" rev-parse --abbrev-ref HEAD)"
DIRTY="$(git -c safe.directory="$PWD" status --porcelain --untracked-files=no)"
[ -z "$DIRTY" ] || fail "工作区有未提交的 tracked 改动，先处理再部署：$DIRTY"
printf '%s\n%s\n' "$(git -c safe.directory="$PWD" rev-parse HEAD)" \
                  "$(git -c safe.directory="$PWD" rev-parse --abbrev-ref HEAD)" \
  > /home/wugefei/CPQ/deploy_prev_before_rollout.txt 2>/dev/null || true

# --------------------------------------------------------------------------- #
step "1. 转换器 env 文件（幂等；只打印变量名，不打印值）"
cp -p "$ENVF" "$ENVF.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
"$PY" - "$ENVF" "$REPO" <<'PY' || fail "写 env 文件失败"
import pathlib
import sys

envf, repo = pathlib.Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, repo)
from tech_app.backend.services.cad_converter import service as cc   # noqa: E402

XVFB_BIN_DIR = "/home/data/cpq-tools/xvfb-user/root/usr/bin"
TOOLS = "/home/data/cpq-tools"
# 值里带空格的（`xvfb-run -a`）必须加引号：python-dotenv 会剥掉引号，bash `source` 也能取到整串。
VALUES = {
    cc.PROVIDER_ENV: "oda",
    cc.BINARY_ENV: TOOLS + "/oda-file-converter-27.1/squashfs-root/AppRun",
    cc.VERSION_ENV: "27.1",
    cc.WRAPPER_ENV: XVFB_BIN_DIR + "/xvfb-run -a",
    cc.PREVIEW_ENV: TOOLS + "/current/bin/dwg2SVG",
    cc.FALLBACK_PROVIDER_ENV: "libredwg",
    cc.FALLBACK_BINARY_ENV: TOOLS + "/current/bin/dwg2dxf",
    cc.FALLBACK_VERSION_ENV: "0.14",
    cc.FALLBACK_PREVIEW_ENV: TOOLS + "/current/bin/dwg2SVG",
    cc.FALLBACK_WRAPPER_ENV: "",
}
MANAGED = tuple(VALUES) + (cc.LEGACY_PROVIDER_ENV, "PATH")


def render(name: str, value: str) -> str:
    quoted = '"%s"' % value if (value == "" or " " in value) else value
    return "export %s=%s" % (name, quoted)


#: 本脚本写进 env 文件的分节注释；**重写前先删掉上一次写的**，否则每部署一次就多一行。
HEADER = "# --- DWG 转换器（scripts/deploy_34_bare.sh 生成；env 名取自 cad_converter.service 常量）---"

kept = []
for line in (envf.read_text(encoding="utf-8").splitlines() if envf.exists() else []):
    if line.strip() == HEADER:
        continue                                    # 上一次写的分节注释，改写时不保留
    body = line[len("export "):] if line.startswith("export ") else line
    if body.split("=", 1)[0] in MANAGED:
        continue                                    # 本脚本托管这几项，重写而不是追加
    kept.append(line)

out = [line for line in kept if line.strip()] + [
    "",
    HEADER,
]
out += [render(name, value) for name, value in VALUES.items()]
# env 里这份 PATH 只对「先 source 再跑」的命令行工具有用；**服务侧无效**，
# 8010 必须在启动命令行上显式前缀（见文件头 ②）。
out.append(render("PATH", XVFB_BIN_DIR + ":$PATH"))
envf.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
envf.chmod(0o600)
print("env 文件已更新：%s（0600）" % envf)
print("变量名：" + " ".join(sorted(VALUES)))
PY
sed -E 's/=.*/=<hidden>/' "$ENVF" | sed 's/^/    /'
[ -x "$XVFB_BIN_DIR/Xvfb" ] || fail "缺少 $XVFB_BIN_DIR/Xvfb（xvfb-run 要靠它）"
for t in "oda-file-converter-27.1/squashfs-root/AppRun" "current/bin/dwg2dxf" "current/bin/dwg2SVG"; do
  [ -x "/home/data/cpq-tools/$t" ] || fail "转换器/预览器不可执行：/home/data/cpq-tools/$t"
done
echo "转换器与预览器在位"

# --------------------------------------------------------------------------- #
step "2. 取代码（纯快进；不产生 merge 提交）"
git -c safe.directory="$PWD" fetch --prune gitlab "$REF" || fail "fetch gitlab $REF 失败"
git -c safe.directory="$PWD" merge --ff-only FETCH_HEAD \
  || fail "不是快进合并，已中止（本脚本不做 merge commit、不改写历史）"
NEW_HEAD="$(git -c safe.directory="$PWD" rev-parse --short HEAD)"
echo "HEAD $OLD_HEAD → $NEW_HEAD"

# --------------------------------------------------------------------------- #
step "2b. 落版本 stamp（部署版本身份，Spec deploy-build-identity §2.3）"
# 目的：让「这台机器上跑的是哪一版代码」变成一条命令能读出来的事实（`/api/health` 的
# `build` 段）。stamp 落在**部署目录之外**，不脏工作区；写不进去就拒绝继续部署。
STAMP_PATH="${CPQ_BUILD_STAMP:-$(dirname "$REPO")/cpq_build.json}"
FULL_HEAD="$(git -c safe.directory="$PWD" rev-parse HEAD)"
BRANCH_NOW="$(git -c safe.directory="$PWD" rev-parse --abbrev-ref HEAD)"
DEPLOYED_AT="$(date +%Y-%m-%dT%H:%M:%S%z)"
"$PY" - "$STAMP_PATH" "$FULL_HEAD" "$BRANCH_NOW" "$REF" "$DEPLOYED_AT" <<'STAMPEOF' || fail "写 stamp 失败"
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
commit, branch, ref, deployed_at = sys.argv[2:6]
path.parent.mkdir(parents=True, exist_ok=True)
payload = {"commit": commit, "branch": branch, "ref": ref, "deployed_at": deployed_at}
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("stamp 已写入：%s（commit=%s ref=%s）" % (path, commit[:12], ref))
STAMPEOF
BUILD_COMMIT="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("commit", ""))' "$STAMP_PATH")"
HEAD_COMMIT="$(git -c safe.directory="$PWD" rev-parse HEAD)"
[ -n "$BUILD_COMMIT" ] || fail "stamp 里读不到 commit：$STAMP_PATH"
[ "$BUILD_COMMIT" = "$HEAD_COMMIT" ] || fail "stamp 的 commit（$BUILD_COMMIT）与仓库 HEAD（$HEAD_COMMIT）不一致，拒绝继续"
echo "build.commit=${BUILD_COMMIT:0:12}（与 git HEAD 一致）"

# --------------------------------------------------------------------------- #
step "3. 重启 8010（先子后父；启动命令带 PATH 前缀）"
mv nohup.out "nohup.out.prev.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
pkill -f 'tech_app_launch.py --host 127.0.0.1 --port 8012' 2>/dev/null || true
sleep 1
pkill -f 'cpq_suite_server.py --host 0.0.0.0 --port 8010' 2>/dev/null || true
for _ in $(seq 1 30); do
  ss -ltn 2>/dev/null | grep -qE ':(8010|8012)\b' || break
  sleep 1
done
ss -ltn 2>/dev/null | grep -qE ':(8010|8012)\b' && fail "8010/8012 端口没释放，不重启（避免起两套）"
PATH="$XVFB_BIN_DIR:$PATH" CPQ_ENV_FILE="$ENVF" CPQ_BUILD_STAMP="$STAMP_PATH" setsid nohup \
  "$PY" cpq_suite_server.py --host 0.0.0.0 --port 8010 >> nohup.out 2>&1 < /dev/null &

# --------------------------------------------------------------------------- #
step "4. 健康检查与 PATH 核对"
HEALTH_OK=0
for _ in $(seq 1 40); do
  if "$PY" - >/dev/null 2>&1 <<'PY'
import json, sys, urllib.request
try:
    payload = json.load(urllib.request.urlopen("http://127.0.0.1:8010/api/health", timeout=4))
except Exception:
    sys.exit(1)
sys.exit(0 if payload.get("status") == "ok" else 1)
PY
  then HEALTH_OK=1; break; fi
  sleep 2
done
[ "$HEALTH_OK" = "1" ] || fail "/api/health 的 status 不是 ok；看 nohup.out"
echo "health：status=ok"

PID="$(pgrep -f 'cpq_suite_server.py --host 0.0.0.0 --port 8010' | head -1)"
[ -n "$PID" ] || fail "找不到新起的 8010 进程"
echo "8010 pid=$PID"
if tr '\0' '\n' < "/proc/$PID/environ" | grep '^PATH=' | grep -q "$XVFB_BIN_DIR"; then
  echo "PATH：含 $XVFB_BIN_DIR ✓"
else
  fail "8010 的 PATH 里没有 $XVFB_BIN_DIR —— ODA 会启动失败并静默回退（见 DEPLOYMENT.md「PATH 与运行用户权限」）"
fi

# --------------------------------------------------------------------------- #
step "5. 真转两份样本（核对 converter_role=primary）"
set -a
# shellcheck disable=SC1090
. "$ENVF"
set +a
install -d "$REPO/裕同包装项目-待开发" 2>/dev/null || true
"$PY" - <<'PY' || fail "真实转换未通过（见上）"
import json
import os
import sys

from tech_app.backend.services import cad_converter
from tech_app.backend.services import file_preflight

SAMPLES = "裕同包装项目-待开发"
bad = []
# 能力事实与部署自检同源（Spec docs/specs/dwg-capability-truth-and-audit.md §3 C6）：
# 应用侧报的"有没有转换器"必须就是这里探测出来的结论，否则会出现
# "部署说装好了、应用说没装"的两套事实。
probe = file_preflight.detect_converter_availability()
print(json.dumps({"converter_probe": probe}, ensure_ascii=False))
if not probe.get("available") or probe.get("role") != "primary":
    bad.append("能力探测：detect_converter_availability() = %r（应为 primary 可用）" % (probe,))
for name in ("酒盒.dwg", "圆盘盒.dwg"):
    path = os.path.join(SAMPLES, name)
    if not os.path.exists(path):
        print("· %s：样本不在 %s，跳过（把两份真实样本放进去再跑）" % (name, SAMPLES))
        bad.append(name + "：样本缺失")
        continue
    manifest = cad_converter.convert_drawing("deploy-selfcheck", name, open(path, "rb").read())
    quality = manifest.get("quality") or {}
    roles = [item.get("role") for item in (manifest.get("output_files") or [])]
    print(json.dumps({"file": name, "status": manifest.get("status"),
                      "converter_role": manifest.get("converter_role"),
                      "fallback_used": manifest.get("fallback_used"),
                      "primary_failure_code": manifest.get("primary_failure_code"),
                      "converter_version": manifest.get("converter_version"),
                      "output_version": quality.get("output_version"),
                      "audit_enabled": quality.get("audit_enabled"),
                      "verified": quality.get("verified"),
                      "entity_count": quality.get("entity_count"),
                      "layer_count": quality.get("layer_count"),
                      "output_roles": roles}, ensure_ascii=False))
    if manifest.get("fallback_used") or manifest.get("converter_role") != "primary":
        bad.append("%s：主转换器未生效（converter_role=%r、primary_failure_code=%r）"
                   % (name, manifest.get("converter_role"), manifest.get("primary_failure_code")))
    if str(manifest.get("converter_role") or "") != str(probe.get("role") or ""):
        bad.append("%s：能力探测与真转的角色不一致（probe.role=%r、manifest.converter_role=%r）"
                   % (name, probe.get("role"), manifest.get("converter_role")))
    if str(manifest.get("status")) not in ("ok", "success_with_warnings"):
        bad.append("%s：status=%r" % (name, manifest.get("status")))
    if not quality.get("verified"):
        bad.append("%s：quality.verified 为假" % name)
    if "dxf" not in roles or "preview" not in roles:
        bad.append("%s：产物缺 role（dxf=%s、preview=%s）" % (name, "dxf" in roles, "preview" in roles))
if bad:
    print("\n未通过：", file=sys.stderr)
    for reason in bad:
        print("  · " + reason, file=sys.stderr)
    sys.exit(1)
print("两份样本均由主转换器完成，dxf + preview 齐全")
PY

# --------------------------------------------------------------------------- #
step "6. 下游连通自检（包装图纸零件：零件文档 → 单件详情 → 闭合件试挤出）"
# 样本项目 id **必须由用户提供**（CPQ_PARTS_PROJECT_ID）：不许脚本自己猜项目，也不许拿
# 生产项目当试验田。未提供 → skip 并打印原因（Spec packaging-parts-downstream-acceptance §6）。
PARTS_PID="${CPQ_PARTS_PROJECT_ID:-}"
BASE="${CPQ_BASE_URL:-http://127.0.0.1:8010}"
if [ -z "$PARTS_PID" ]; then
  echo "skip：未提供样本项目 id，跳过下游连通自检"
  echo "      要跑这一步：CPQ_PARTS_PROJECT_ID=<项目id> bash scripts/deploy_34_bare.sh $REF"
else
  "$PY" - "$PARTS_PID" "$BASE" <<'PY' || fail "下游连通自检未通过（见上）"
import json
import sys
import urllib.error
import urllib.request

pid, base = sys.argv[1], sys.argv[2]


def call(path, method="GET"):
    request = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:                                     # noqa: BLE001
            payload = {}
        return exc.code, payload


parts_path = "/api/projects/%s/requirement/packaging-parts" % pid
status, doc = call(parts_path)
if status in (401, 403):
    print("skip：本地 HTTP 需要登录态（HTTP %d），无法在部署脚本里跑下游自检" % status)
    print("     请登录后在浏览器里打开 %s 复核，或带上 ?token=<会话令牌> 再跑本步" % parts_path)
    sys.exit(0)
if status != 200:
    print("✗ 零件文档读不到：HTTP %d %s" % (status, doc), file=sys.stderr)
    sys.exit(1)
stats = doc.get("stats") or {}
print("· 零件文档：built=%s part_total=%s closed_ratio=%s"
      % (bool(doc.get("parts")), stats.get("part_total"), stats.get("closed_ratio")))
if not doc.get("parts"):
    print("✗ 零件文档是空的：先在这个项目上跑一次「一键解析图纸」", file=sys.stderr)
    sys.exit(1)

first = str((doc.get("parts") or [{}])[0].get("part_code") or "")
status, detail = call("%s/%s" % (parts_path, first))
if status != 200 or not detail.get("found"):
    print("✗ 单件详情读不到：%s HTTP %d %s" % (first, status, detail), file=sys.stderr)
    sys.exit(1)
outline = detail.get("outline") or {}
print("· 单件详情：%s outline.status=%s 点数=%s"
      % (first, outline.get("status"), len(outline.get("points") or [])))

if str(outline.get("status") or "") == "closed":
    status, solid = call("%s/%s/solid" % (parts_path, first), method="POST")
    if status != 200 or str(solid.get("status") or "") not in ("ok", "unsupported"):
        print("✗ 挤出结论异常：HTTP %d %s" % (status, solid), file=sys.stderr)
        sys.exit(1)
    print("· 3D 挤出：status=%s reason=%s（unsupported 也是结论，不算失败）"
          % (solid.get("status"), solid.get("reason") or "-"))
else:
    print("· 首件不是闭合轮廓，跳过 3D 挤出（unsupported 分支由单件详情已证）")
print("下游连通自检通过（零件文档 → 单件详情 → 挤出结论）")
PY
fi

# --------------------------------------------------------------------------- #
step "6b. 下游连通自检（隔离端到端：不需要项目 id，也不写任何项目数据）"
# 第 6 步要一个**真实项目** id，线上还没人点过"一键解析"时它只能 skip —— 那一步证明不了
# "这台机器上这条链路真的能跑通"。这一步补上不需要项目的那条：把 store 的数据根目录指到临时
# 目录（`DATA_DIR`，见 `tech_app/backend/config.py`），在隔离目录里建项目 → 建需求草稿 → 跑完整条
# 八步 flow → 读零件文档 → 单件详情 → 挤出，跑完删掉临时目录。**生产数据目录一个字节不写。**
# 口径见 Spec `packaging-parts-downstream-acceptance.md` §6.1。
META_BEFORE="$(ls -1 tech_app/data/*/meta.json 2>/dev/null | wc -l | tr -d ' ')"
SELFCHECK_DIR="${TMPDIR:-/tmp}/cpq-parts-selfcheck.$$"
# 知识库走"服务间内部令牌 + HTTP 快照"（技术工艺不直连 Postgres），令牌由 8010 启动时生成并
# 只传给它的 8012 子进程 —— 外部脚本要从**正在跑的 8010 进程 environ**里取同一个值，
# 否则权威实样那条自检只能跳过（"自检没跑"正是要防的失效模式）。
# 令牌由 8010 进程自己生成（cpq_suite_server 导入期 `secrets.token_urlsafe` 后 putenv），
# **只以环境变量形式传给它的 8012 子进程**：`/proc/8010/environ` 看不到 putenv 之后的改动，
# 而子进程是 execve 继承的，能读到 —— 所以先找 8012，再兜底 8010（env 里显式给了令牌时）。
SELFCHECK_TOKEN=""
for _p in $(pgrep -f 'tech_app_launch.py --host 127.0.0.1 --port 8012' 2>/dev/null) "$PID"; do
  SELFCHECK_TOKEN="$(tr '\0' '\n' < "/proc/$_p/environ" 2>/dev/null | sed -n 's/^CPQ_INTERNAL_TOKEN=//p' | head -1)"
  [ -n "$SELFCHECK_TOKEN" ] && break
done
if [ -n "$SELFCHECK_TOKEN" ]; then
  echo "· 已从运行中的服务进程取到服务间内部令牌，知识库自检可以真跑"
else
  echo "· 取不到服务间内部令牌，知识库自检会打印原因跳过"
fi
mkdir -p "$SELFCHECK_DIR"
cat > "$SELFCHECK_DIR/selfcheck.py" <<'PY'
import json
import os
import pathlib
import sys
import traceback

sys.path.insert(0, os.getcwd())
from tech_app.backend.storage import store
from tech_app.backend.models.workflow import RequirementDoc
from tech_app.backend.services import (packaging_drawing_flow, packaging_part_solids,
                                       packaging_parts, requirement_service)


def mix_text(mix):
    """账 → `CODE×n`（空账 → 空串，调用方据此决定打不打印这一行，Spec §3）。"""
    if not isinstance(mix, dict) or not mix:
        return ""
    return "、".join("%s×%d" % (key, int(value)) for key, value in mix.items())


SAMPLES = ("酒盒.dwg", "圆盘盒.dwg")
bad = []
for name in SAMPLES:
    source = pathlib.Path("裕同包装项目-待开发") / name
    if not source.is_file():
        bad.append("%s：样本缺失" % name)
        continue
    # 每个样本**当场**输出（Spec §4.10）：否则"卡在第一份"和"卡在第二份"在日志里分不出来。
    print("· %s：开始跑隔离链路…" % name, flush=True)
    try:
        pid = store.create_project(name, source.read_bytes(),
                                   note="部署自检（隔离数据目录）", owner="deploy-selfcheck",
                                   owner_display_name="deploy-selfcheck")
        requirement_service.save_requirement_draft(
            pid, RequirementDoc(project_id=pid, requirement_no="",
                                title="部署自检 " + name, data={"customer_credit": "A"}),
            user={"username": "deploy-selfcheck", "role": "admin"})
        flow = packaging_drawing_flow.run_flow(pid, prompt="", actor="deploy-selfcheck")
    except Exception:                                     # noqa: BLE001 - 检查失败如实报
        traceback.print_exc()
        bad.append("%s：跑不动（见上面的栈）" % name)
        continue
    steps = {str(row.get("step_id")): str(row.get("status"))
             for row in (flow.get("steps") or [])}
    not_done = {key: value for key, value in steps.items() if value != "completed"}
    doc = packaging_parts.load_parts(pid) or {}
    rows = doc.get("parts") or []
    # 整份零件文档一次算完（Spec `packaging-parts-solid-coverage.md` §2.2）——覆盖率与
    # "不可挤出原因"都从这一份结论来，不在脚本里另算一套。
    batch = packaging_part_solids.extrude_all(rows)
    summary = packaging_parts.summarize(doc, solids={"parts": batch.get("parts") or []})
    ready = [row for row in rows if packaging_parts.processability(row).get("ok")]
    solids = [item for item in (batch.get("parts") or []) if item.get("status") == "ok"]
    print("· %s：八步 %d/%d completed；零件 %d 件（closed_ratio=%.3f）；可算 %d / 可挤出 %d"
          % (name, len(steps) - len(not_done), len(steps), len(rows),
             float(summary["closed_ratio"]), len(ready), len(solids)), flush=True)
    # 失败时必须一眼看出断在哪一环（Spec `packaging-parts-selfcheck-diagnostics.md` §3）：
    # 打的是 summarize() 的**同一份账**，不在这里重算。
    unprocessable = mix_text(summary.get("unprocessable_reason_mix"))
    if unprocessable:
        print("   · 不可算原因：%s" % unprocessable, flush=True)
    solid_mix = mix_text(summary.get("solid_reason_mix"))
    if solid_mix:
        print("   · 不可挤出原因：%s" % solid_mix, flush=True)
    if not_done:
        bad.append("%s：有步骤没跑完 %r" % (name, not_done))
    if not rows:
        bad.append("%s：零件文档是空的" % name)
    if not ready:
        bad.append("%s：没有一件能跑工艺" % name)
    if not solids:
        bad.append("%s：没有一件能挤出 3D" % name)
    if rows:
        row = (ready or rows)[0]
        out = packaging_part_solids.extrude(row)
        print("   · 代表件 %s：outline_status=%s size_source=%s 挤出=%s"
              % (row.get("part_code"), row.get("outline_status"),
                 row.get("size_source"), out.get("status")), flush=True)
# 权威实样盒型的工艺路线必须能确认（Spec `packaging-route-template-closure.md` §3.4）：
# 模板工序名在 build 时归一化到 19 条闭集内；闭集外又没映射的名字在入库时就被点名拒绝。
# 少了这一条，盒型会以"永远 confirm 不了"（409 route_not_confirmable）的状态入库，直到
# 零件下游全部卡死才发现。样本项目照旧建在隔离数据目录里，**不读也不写生产项目**。
try:
    from tech_app.backend.storage import da_db, da_repo, kb_repo
    from tech_app.backend.services import packaging_bom, packaging_route

    authoritative = [row for row in kb_repo.packaging_box_types()
                     if str(row.get("business_status") or "").strip() == "权威实样"]
except Exception as exc:                              # noqa: BLE001 - 读不到知识库如实报
    print("· 权威实样路线自检：读不到知识库（%s），跳过" % exc)
    authoritative = None
if authoritative is not None:
    if not authoritative:
        print("· 权威实样路线自检：知识库里没有 business_status='权威实样' 的盒型，跳过")
    for item in authoritative:
        code = str(item.get("box_type_code") or "")
        req_no = "REQ-ROUTE-SELFCHECK"
        # BOM 展开需要内尺寸；自检不是需求单经办，用**盒型自己登记的可生产区间上限**
        # 填三个内尺寸（拿不到就跳过这个盒型并打印原因，不编数、不改业务口径）。
        dims = {key: item.get(source_key)
                for key, source_key in (("inner_length", "size_l_max"),
                                        ("inner_width", "size_w_max"),
                                        ("inner_height", "size_h_max"))}
        if any(dims[key] in (None, "") for key in dims):
            print("· 权威实样 %s：盒型行没有尺寸区间，跳过路线自检" % code)
            continue
        try:
            pid = store.create_project("route-selfcheck-%s.dwg" % code, b"selfcheck",
                                       note="部署自检（隔离数据目录）", owner="deploy-selfcheck",
                                       owner_display_name="deploy-selfcheck")
            requirement_service.save_requirement_draft(
                pid, RequirementDoc(project_id=pid, requirement_no=req_no,
                                    title="路线自检 " + code,
                                    data={"industry": "packaging", "box_type": code,
                                          "packaging_product_name": code,
                                          "quote_quantity": 1000, "lamination": "覆光膜",
                                          **dims}),
                user={"username": "deploy-selfcheck", "role": "admin"})
            da_repo.save_box_match({"project_id": pid, "requirement_no": req_no,
                                    "industry": "packaging",
                                    "engine_version": "packaging_match_v1",
                                    "inputs": {}, "candidates": [], "missing_inputs": [],
                                    "suggested_box_type": code})
            da_repo.update_box_match_decision(pid, req_no, decision="confirmed",
                                              confirmed_box_type=code,
                                              confirmed_by="deploy-selfcheck",
                                              confirmed_at=da_db.now())
            packaging_bom.build_bom(pid, req_no)
            built = packaging_route.build_route(pid, req_no)
            confirmed = packaging_route.confirm_route(
                pid, req_no, actor={"username": "deploy-selfcheck"})
            print("· 权威实样 %s：路线 %d 道，confirm=%s"
                  % (code, len(built.get("steps") or []), confirmed.get("status")))
            if str(confirmed.get("status")) != "confirmed":
                bad.append("权威实样 %s：confirm 结果不是 confirmed（%s）"
                           % (code, confirmed.get("status")))
        except Exception as exc:                      # noqa: BLE001 - 检查失败如实报
            traceback.print_exc()
            bad.append("权威实样 %s：工艺路线自检失败（%s）" % (code, exc))

print(json.dumps({"isolated_downstream_selfcheck": "ok" if not bad else "failed",
                  "problems": bad}, ensure_ascii=False))
if bad:
    print("\n隔离端到端自检未通过：", file=sys.stderr)
    for reason in bad:
        print("  · " + reason, file=sys.stderr)
    sys.exit(1)
print("隔离端到端自检通过（建项目 → 需求草稿 → 八步 flow → 零件文档 → 单件详情 → 挤出）")
PY
# 第 6b 步必须有**内部超时**（Spec `packaging-parts-pipeline-time-budget.md` §4.8/§4.9）：
# 今天没有超时，卡住只能被外部的 expect 杀掉，而"在跑"和"卡死"在日志里长得一模一样。
if command -v timeout >/dev/null 2>&1; then
  DATA_DIR="$SELFCHECK_DIR/data" CPQ_INTERNAL_TOKEN="$SELFCHECK_TOKEN" \
    timeout 900 "$PY" "$SELFCHECK_DIR/selfcheck.py"
  SELFCHECK_RC=$?
else
  # `timeout` 不在 PATH（受限 shell / 精简系统）也不许无限等：SECONDS 看门狗，上限同为 900s。
  SECONDS=0
  DATA_DIR="$SELFCHECK_DIR/data" CPQ_INTERNAL_TOKEN="$SELFCHECK_TOKEN" \
    "$PY" "$SELFCHECK_DIR/selfcheck.py" &
  SELFCHECK_PID=$!
  SELFCHECK_RC=0
  while kill -0 "$SELFCHECK_PID" 2>/dev/null; do
    sleep 5
    if [ "$SECONDS" -ge 900 ]; then
      kill -TERM "$SELFCHECK_PID" 2>/dev/null
      SELFCHECK_RC=124
      break
    fi
  done
  if [ "$SELFCHECK_RC" = "0" ]; then
    wait "$SELFCHECK_PID"
    SELFCHECK_RC=$?
  else
    wait "$SELFCHECK_PID" 2>/dev/null || true
  fi
fi
if [ "$SELFCHECK_RC" = "124" ]; then
  fail "第 6b 步：隔离端到端自检超时（上限 900s），卡在哪个样本见上面最后一行输出"
fi
"$PY" - "$SELFCHECK_DIR" <<'PY'
import shutil
import sys

shutil.rmtree(sys.argv[1], ignore_errors=True)
print("· 已删除隔离目录 %s" % sys.argv[1])
PY
META_AFTER="$(ls -1 tech_app/data/*/meta.json 2>/dev/null | wc -l | tr -d ' ')"
echo "· 生产数据目录未被写入（meta.json 数量 $META_BEFORE → $META_AFTER）"
[ "$META_BEFORE" = "$META_AFTER" ] || fail "隔离自检动了生产数据目录（meta.json $META_BEFORE → $META_AFTER）"
[ "$SELFCHECK_RC" = "0" ] || fail "隔离端到端自检未通过（见上）"

# --------------------------------------------------------------------------- #
step "7. 结论"
echo "部署完成：$OLD_HEAD → $NEW_HEAD（ref=$REF）"
echo "build.commit=$FULL_HEAD（branch=$BRANCH_NOW；stamp=$STAMP_PATH）"
echo "8010 pid=$PID；env 文件=$ENVF；日志=nohup.out"
echo "能力声明口径（未通过 L4 之前只能这么说）：DWG 编排能力完成，真实转换能力未验收"
echo "门禁（**必须带 env 与 PATH**，否则门禁探不到转换器、会误报 converter_version_pinned fail）："
echo "  cd $PWD"
echo "  set -a; . $ENVF; set +a"
echo "  PATH=\"$XVFB_BIN_DIR:\$PATH\" $PY tech_app/tools/dwg_deploy_gate.py --env production"
