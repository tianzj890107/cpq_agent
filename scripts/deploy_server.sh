#!/usr/bin/env bash
set -euo pipefail

deploy_root="${CPQ_DEPLOY_ROOT:-/home/data/zhangzhen_home/zhangzhen/cpq_agent}"
deploy_ref="${CPQ_DEPLOY_REF:?必须显式设置 CPQ_DEPLOY_REF 为已授权的 tag 或 commit}"
deploy_port="${CPQ_DEPLOY_PORT:-8010}"

cd "$deploy_root"
test "$(git remote get-url origin)" = "git@gitlab.boulderaitech.com:ai-team/cpq_agent.git" || {
  echo "部署目录 origin 不是 CPQ GitLab，拒绝部署。" >&2
  exit 1
}
test -z "$(git status --porcelain)" || { echo "服务器工作区不干净，拒绝部署。" >&2; exit 1; }
git fetch --prune origin master --tags
target="$(git rev-parse --verify "${deploy_ref}^{commit}")"
git merge-base --is-ancestor "$target" origin/master || { echo "目标不在 GitLab master 历史中，拒绝部署。" >&2; exit 1; }
git checkout --detach "$target"
docker info >/dev/null
test -f cpq_settings.json || { echo "缺少持久化 cpq_settings.json，拒绝重建。" >&2; exit 1; }
for path in cpq_history xbom_history rule_history; do
  test -d "$path" || { echo "缺少持久化目录 $path，拒绝重建。" >&2; exit 1; }
done
docker compose up -d --build cpq-suite
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error "http://127.0.0.1:${deploy_port}/" >/dev/null; then
    echo "部署成功 commit=$target port=$deploy_port"
    exit 0
  fi
  sleep 1
done
docker compose logs --tail=100 cpq-suite >&2 || true
echo "服务健康检查失败。" >&2
exit 1
