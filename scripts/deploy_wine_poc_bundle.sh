#!/usr/bin/env bash
# Explicitly authorized 34 wine POC deployment; no remote push and no data cleanup.
set -euo pipefail
cd /home/wugefei/CPQ/cpq_agent
test -z "$(git status --porcelain --untracked-files=no)"
BUNDLE="${1:-/tmp/cpq-wine-poc-20261010.bundle}"
TARGET="${2:-7c3f07cea539a4b3a90133cbaaf9eb0106b114ed}"
git bundle verify "$BUNDLE"
git fetch "$BUNDLE" HEAD
test "$(git rev-parse FETCH_HEAD)" = "$TARGET"
git merge --ff-only FETCH_HEAD
test "$(git rev-parse HEAD)" = "$TARGET"
cp -n /home/wugefei/CPQ/cpq_build.json /home/wugefei/CPQ/cpq_build.before_wine_poc_20261010.json
./open-claude/.venv/bin/python - <<'PY'
import json,datetime,subprocess
from pathlib import Path
Path('/home/wugefei/CPQ/cpq_build.json').write_text(json.dumps({
 'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'branch':'ytbz','ref':'wine-poc-local-bundle',
 'deployed_at':datetime.datetime.now().astimezone().isoformat()}))
PY
pkill -f 'tech_app_launch.py --host 127.0.0.1 --port 8012' || true
sleep 1
pkill -f 'cpq_suite_server.py --host 0.0.0.0 --port 8010' || true
for attempt in $(seq 1 30); do
  if ! ss -ltn | grep -qE ':(8010|8012)\b'; then break; fi
  sleep 1
done
if ss -ltn | grep -qE ':(8010|8012)\b'; then exit 1; fi
PATH="/home/data/cpq-tools/xvfb-user/root/usr/bin:$PATH" CPQ_ENV_FILE=/home/wugefei/CPQ/cpq_env.sh CPQ_BUILD_STAMP=/home/wugefei/CPQ/cpq_build.json \
 setsid nohup ./open-claude/.venv/bin/python cpq_suite_server.py --host 0.0.0.0 --port 8010 >> nohup.out 2>&1 < /dev/null &
echo 'Wine POC build launched; health must be checked before acceptance.'
