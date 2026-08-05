#!/usr/bin/env bash
# britney.ai trading agent — deploy a code update to an already-provisioned
# droplet (see provision.sh for first-time setup). Pulls the latest commit
# on the branch the droplet already has checked out, reinstalls
# dependencies (harmless no-op if requirements.txt didn't change), and
# restarts the agent so the new code takes effect.
#
# Usage (run from your local machine, not the droplet):
#   DROPLET_HOST=root@<droplet-ip> backend/deploy/deploy.sh
set -euo pipefail

DROPLET_HOST="${DROPLET_HOST:?Set DROPLET_HOST, e.g. DROPLET_HOST=root@1.2.3.4}"
APP_DIR="${APP_DIR:-/opt/britney-ai}"

# shellcheck disable=SC2087
ssh "$DROPLET_HOST" bash -s <<EOF
set -euo pipefail
cd "${APP_DIR}"
sudo -u britney git pull
sudo -u britney "${APP_DIR}/backend/.venv/bin/pip" install -q -r "${APP_DIR}/backend/requirements.txt"
systemctl restart britney-agent
sleep 1
systemctl --no-pager --lines=5 status britney-agent
EOF

echo "==> Deployed. Tail logs with: ssh ${DROPLET_HOST} journalctl -u britney-agent -f"
