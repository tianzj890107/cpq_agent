#!/usr/bin/env python3
"""Safely push HEAD to GitHub and GitLab 20260909 without rewriting history."""
import argparse
import subprocess
import sys

BRANCH = "20260909"
REMOTES = {"origin": "git@github.com:tianzj890107/cpq_agent.git", "gitlab": "git@gitlab.boulderaitech.com:ai-team/cpq_agent.git"}

def git(*args, check=True):
    proc = subprocess.run(["git", *args], text=True, capture_output=True)
    if check and proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc

def remote_sha(remote):
    proc = git("ls-remote", remote, f"refs/heads/{BRANCH}", check=False)
    if proc.returncode:
        raise RuntimeError(f"无法读取 {remote}: {(proc.stderr or '').strip()}")
    return proc.stdout.split()[0] if proc.stdout.strip() else None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if git("branch", "--show-current").stdout.strip() != BRANCH:
        sys.exit(f"错误：只允许从 {BRANCH} 推送。")
    if git("status", "--porcelain").stdout.strip():
        sys.exit("错误：工作区不干净，请先提交本任务修改。")
    head = git("rev-parse", "HEAD").stdout.strip()
    for remote, expected in REMOTES.items():
        actual = git("remote", "get-url", remote).stdout.strip()
        if actual != expected:
            sys.exit(f"错误：{remote} URL 为 {actual!r}，预期 {expected!r}。")
        sha = remote_sha(remote)
        if sha and sha != head and git("merge-base", "--is-ancestor", sha, head, check=False).returncode:
            sys.exit(f"错误：{remote}/{BRANCH} 含本地未知提交，拒绝自动合并或 force push。")
        print(f"{remote}/{BRANCH}: {sha or '(缺失)'}")
    print(f"HEAD: {head}")
    if args.check:
        print("--check：未执行 push。")
        return
    failures = []
    for remote in REMOTES:
        proc = git("push", remote, f"{head}:refs/heads/{BRANCH}", check=False)
        if proc.returncode:
            failures.append(f"{remote}: {(proc.stderr or proc.stdout).strip()}")
        elif remote_sha(remote) != head:
            failures.append(f"{remote}: push 后 SHA 不一致")
        else:
            print(f"{remote}/{BRANCH} 推送并回读成功。")
    if failures:
        sys.exit("部分远端推送失败；保留成功结果并以同一 HEAD 重试：\n" + "\n".join(failures))

if __name__ == "__main__":
    main()
