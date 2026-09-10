#!/usr/bin/env python3
"""Safely push HEAD to GitLab and GitHub 20260909 without rewriting history."""
import argparse
import subprocess
import sys

BRANCH = "20260909"
REMOTE = "gitlab"
GITHUB_REMOTE = "origin"
EXPECTED_PUSH_URLS = {
    REMOTE: ["git@gitlab.boulderaitech.com:ai-team/cpq_agent.git"],
    GITHUB_REMOTE: ["git@github.com:tianzj890107/cpq_agent.git"],
}

def git(*args, check=True):
    proc = subprocess.run(["git", *args], text=True, capture_output=True)
    if check and proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc

def push_urls(remote):
    proc = git("remote", "get-url", "--push", "--all", remote)
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

def remote_sha(remote):
    proc = git("ls-remote", remote, f"refs/heads/{BRANCH}", check=False)
    if proc.returncode:
        raise RuntimeError(f"无法读取 {remote}: {(proc.stderr or '').strip()}")
    return proc.stdout.split()[0] if proc.stdout.strip() else None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只预检，不推送")
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(EXPECTED_PUSH_URLS),
        default=[],
        help="只补推指定远端；默认双推 GitLab 与 GitHub",
    )
    args = parser.parse_args()
    targets = args.only or list(EXPECTED_PUSH_URLS)
    if git("branch", "--show-current").stdout.strip() != BRANCH:
        sys.exit(f"错误：只允许从 {BRANCH} 推送。")
    if git("status", "--porcelain").stdout.strip():
        sys.exit("错误：工作区不干净，请先提交本任务修改。")
    head = git("rev-parse", "HEAD").stdout.strip()
    for remote, expected in EXPECTED_PUSH_URLS.items():
        actual = push_urls(remote)
        if actual != expected:
            sys.exit(
                f"错误：{remote} 推送地址为 {actual}，预期 {expected}。\n"
                f"请先规范化：git remote set-url --push {remote} {expected[0]}"
            )
    for remote in targets:
        sha = remote_sha(remote)
        if sha and sha != head and git("merge-base", "--is-ancestor", sha, head, check=False).returncode:
            sys.exit(f"错误：{remote}/{BRANCH} 含本地未知提交，拒绝自动合并或 force push。")
        print(f"{remote}/{BRANCH}: {sha or '(缺失，将新建分支)'}")
    print(f"HEAD: {head}")
    if args.check:
        print(f"--check：未执行 push（目标远端：{', '.join(targets)}）。")
        return
    results = {}
    for remote in targets:
        proc = git("push", remote, f"{head}:refs/heads/{BRANCH}", check=False)
        if proc.returncode:
            results[remote] = (False, (proc.stderr or proc.stdout).strip())
        elif remote_sha(remote) != head:
            results[remote] = (False, "push 后 SHA 不一致")
        else:
            results[remote] = (True, "")
    for remote, (ok, detail) in results.items():
        print(f"{remote}/{BRANCH} 推送{'并回读成功' if ok else '失败'}。")
        if detail:
            print(detail)
    failed = [remote for remote, (ok, _) in results.items() if not ok]
    if failed:
        retry = " ".join(f"--only {remote}" for remote in failed)
        print(f"成功远端：{[r for r, (ok, _) in results.items() if ok] or '无'}；"
              f"失败远端：{failed}。修复后以同一 HEAD 补推：python3 scripts/push_remotes.py {retry}")
        sys.exit(1)
    print(f"双远端推送并回读成功：{', '.join(targets)}。")

if __name__ == "__main__":
    main()
