#!/usr/bin/env python3
"""Create or reuse the fixed GitLab MR 20260909 -> master; never merge it."""
import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = "ai-team/cpq_agent"
SOURCE = "20260909"
TARGET = "master"
REVIEWER = "tianzijing"
ASSIGNEE = "zhangzhen"

def token():
    value = os.getenv("GITLAB_TOKEN")
    path = Path.home() / ".config/codex/gitlab_token"
    if not value and path.exists(): value = path.read_text().strip()
    if not value: sys.exit("错误：缺少 GitLab token。")
    return value

def api(method, path, payload=None):
    request = urllib.request.Request("http://gitlab.boulderaitech.com/api/v4/" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"PRIVATE-TOKEN": token(), "Content-Type": "application/json"}, method=method)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as response:
        body = response.read(); return json.loads(body) if body else None

def user_id(username):
    users = api("GET", "users?username=" + urllib.parse.quote(username))
    if not users: sys.exit(f"错误：GitLab 用户 {username} 不存在。")
    return users[0]["id"]

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--check", action="store_true")
    parser.add_argument("--title"); parser.add_argument("--description-file", type=Path); args = parser.parse_args()
    project = api("GET", "projects/" + urllib.parse.quote(PROJECT, safe=""))
    query = urllib.parse.urlencode({"state":"opened", "source_branch":SOURCE, "target_branch":TARGET})
    existing = api("GET", f"projects/{project['id']}/merge_requests?{query}") or []
    subprocess.run(["git", "fetch", "gitlab", TARGET], capture_output=True)
    commits = subprocess.run(["git", "log", "--no-merges", "--format=%h %s", f"gitlab/{TARGET}..{SOURCE}"], text=True, capture_output=True).stdout.strip()
    print(f"MR: {SOURCE} -> {TARGET}\n待合入提交：\n{commits or '(无法比较；请先确认 master 已建立)'}")
    if args.check:
        print(f"opened MR: {existing[0].get('web_url') if existing else '(无)'}\n--check：未创建或修改 MR。"); return
    if not args.title or not args.description_file: sys.exit("错误：创建 MR 必须提供 --title 和 --description-file。")
    payload = {"source_branch":SOURCE, "target_branch":TARGET, "title":args.title,
        "description":args.description_file.read_text(encoding="utf-8"),
        "reviewer_ids":[user_id(REVIEWER)], "assignee_ids":[user_id(ASSIGNEE)], "remove_source_branch":False}
    if existing: mr = api("PUT", f"projects/{project['id']}/merge_requests/{existing[0]['iid']}", payload)
    else: mr = api("POST", f"projects/{project['id']}/merge_requests", payload)
    print(f"MR 已创建/更新但未合并：{mr['web_url']}")

if __name__ == "__main__": main()
