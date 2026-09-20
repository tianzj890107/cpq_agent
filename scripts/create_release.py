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

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--tag", required=True)
    parser.add_argument("--name", required=True); parser.add_argument("--description-file", required=True, type=Path)
    parser.add_argument("--check", action="store_true"); args = parser.parse_args()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag): sys.exit("错误：tag 必须符合 vMAJOR.MINOR.PATCH。")
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
