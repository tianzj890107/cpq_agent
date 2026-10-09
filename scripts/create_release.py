#!/usr/bin/env python3
"""Idempotently create a GitLab Release for an existing immutable tag."""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = "ai-team/cpq_agent"

def load_token():
    value = os.getenv("GITLAB_TOKEN")
    path = Path.home() / ".config/codex/gitlab_token"
    if not value and path.exists(): value = path.read_text().strip()
    if not value: sys.exit("错误：缺少 GitLab token。")
    return value

def api(method, path, payload=None):
    request = urllib.request.Request("http://gitlab.boulderaitech.com/api/v4/" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"PRIVATE-TOKEN": load_token(), "Content-Type":"application/json"}, method=method)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as response:
            body = response.read(); return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404: return None
        raise

def require_release_gate(gate_report, force):
    """创建 Release 前必须过生产门禁（Spec `capability-isolation-and-shared-parse-batch5.md` §2.3）。

    门禁未过 → 拒绝创建并打印 blocking；`--force` 可显式覆盖并打印告警。
    只判**发布**，不据此对运行期做任何禁用。
    """
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path: sys.path.insert(0, str(root))
    from tech_app.backend.services.capability_isolation import release_verdict
    report = None
    if gate_report is not None:
        try:
            report = json.loads(Path(gate_report).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"警告：生产门禁报告读不到或不是 JSON（{exc}）。")
            report = None
    verdict = release_verdict(report)
    if verdict["release_ok"]:
        print("生产门禁：go")
        return
    print("生产门禁：no_go，blocking=" + "、".join(verdict["blocking"] or ["(无)"]))
    if force:
        print("警告：--force 已显式覆盖生产门禁；本次 Release 未过门禁，请留档说明。")
        return
    sys.exit("错误：生产门禁未过；拒绝创建 Release。如确需创建，请显式加 --force。")


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--tag", required=True)
    parser.add_argument("--name", required=True); parser.add_argument("--description-file", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--gate-report", type=Path, default=None,
                        help="生产门禁报告 JSON（dwg_deploy_gate --env production 的输出）")
    parser.add_argument("--force", action="store_true",
                        help="门禁未过时仍创建 Release（打印告警；默认拒绝）")
    args = parser.parse_args()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag): sys.exit("错误：tag 必须符合 vMAJOR.MINOR.PATCH。")
    if not args.check:
        require_release_gate(args.gate_report, args.force)
    project = api("GET", "projects/" + urllib.parse.quote(PROJECT, safe="")); pid = project["id"]
    encoded = urllib.parse.quote(args.tag, safe=""); tag = api("GET", f"projects/{pid}/repository/tags/{encoded}")
    if not tag: sys.exit("错误：GitLab 上不存在该 tag；Release 不得隐式创建 tag。")
    existing = api("GET", f"projects/{pid}/releases/{encoded}")
    print(f"tag {args.tag}: {tag['commit']['id']}\nrelease: {'已存在' if existing else '缺失'}")
    if args.check: print("--check：未创建 Release。"); return
    payload = {"name":args.name, "description":args.description_file.read_text(encoding="utf-8")}
    if existing: result = api("PUT", f"projects/{pid}/releases/{encoded}", payload)
    else: result = api("POST", f"projects/{pid}/releases", {"tag_name":args.tag, **payload})
    print(f"Release 已创建/更新：{result['_links']['self']}")

if __name__ == "__main__": main()
