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


kept = []
for line in (envf.read_text(encoding="utf-8").splitlines() if envf.exists() else []):
    body = line[len("export "):] if line.startswith("export ") else line
    if body.split("=", 1)[0] in MANAGED:
        continue                                    # 本脚本托管这几项，重写而不是追加
    kept.append(line)

out = [line for line in kept if line.strip()] + [
    "",
    "# --- DWG 转换器（scripts/deploy_34_bare.sh 生成；env 名取自 cad_converter.service 常量）---",
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
PATH="$XVFB_BIN_DIR:$PATH" CPQ_ENV_FILE="$ENVF" setsid nohup \
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
step "6. 结论"
echo "部署完成：$OLD_HEAD → $NEW_HEAD（ref=$REF）"
echo "8010 pid=$PID；env 文件=$ENVF；日志=nohup.out"
echo "能力声明口径（未通过 L4 之前只能这么说）：DWG 编排能力完成，真实转换能力未验收"
echo "门禁：$PY tech_app/tools/dwg_deploy_gate.py --env production"
