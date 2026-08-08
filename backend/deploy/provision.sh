#!/usr/bin/env bash
# britney.ai trading agent — droplet provisioning script.
#
# Run ONCE on a fresh Ubuntu 22.04/24.04 LTS droplet, as root:
#
#   ssh root@<droplet-ip>
#   curl -fsSL https://raw.githubusercontent.com/aaravSmall/britney-ai/main/backend/deploy/provision.sh -o provision.sh
#   bash provision.sh
#
# Idempotent — safe to re-run (e.g. after `git pull` picks up a new
# dependency or a systemd unit change) instead of writing a separate
# update script for that.
#
# See docs/DEPLOYMENT.md for the full walkthrough, including droplet
# sizing/creation (this script only provisions software ON a droplet
# that already exists).
set -euo pipefail

APP_USER="britney"
APP_DIR="/opt/britney-ai"
REPO_URL="${REPO_URL:-https://github.com/aaravSmall/britney-ai.git}"
DB_NAME="britney"
DB_USER="britney"
LOG_DIR="/var/log/britney-agent"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root (e.g. via sudo)." >&2
  exit 1
fi

echo "==> Updating apt and installing system packages"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-contrib git openssl

echo "==> Ensuring the '${APP_USER}' system user exists"
id -u "$APP_USER" &>/dev/null || useradd --system --create-home --shell /bin/bash "$APP_USER"

echo "==> Enabling Postgres to start on boot (standard systemd unit, installed by apt above)"
systemctl enable --now postgresql

echo "==> Creating Postgres role '${DB_USER}' (idempotent)"
DB_PASSWORD=""
ROLE_EXISTS="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'")"
if [ "$ROLE_EXISTS" != "1" ]; then
  DB_PASSWORD="$(openssl rand -hex 16)"
  sudo -u postgres psql -c "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';"
  echo "    Generated Postgres password for role '${DB_USER}': ${DB_PASSWORD}"
  echo "    (save this now — it will only be echoed this once)"
else
  echo "    Role already exists, leaving its password as-is."
fi

echo "==> Creating database '${DB_NAME}' (idempotent)"
DB_EXISTS="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")"
if [ "$DB_EXISTS" != "1" ]; then
  sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
fi

echo "==> Cloning/updating the app code in ${APP_DIR}"
if [ -d "${APP_DIR}/.git" ]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" pull
else
  git clone "$REPO_URL" "$APP_DIR"
  chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
fi

echo "==> Creating/updating the Python virtualenv"
sudo -u "$APP_USER" python3 -m venv "${APP_DIR}/backend/.venv"
sudo -u "$APP_USER" "${APP_DIR}/backend/.venv/bin/pip" install --upgrade pip -q
sudo -u "$APP_USER" "${APP_DIR}/backend/.venv/bin/pip" install -q -r "${APP_DIR}/backend/requirements.txt"

ENV_FILE="${APP_DIR}/backend/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "==> No .env found — creating one from the template"
  sudo -u "$APP_USER" cp "${APP_DIR}/backend/deploy/agent.env.example" "$ENV_FILE"
  if [ -n "$DB_PASSWORD" ]; then
    sudo -u "$APP_USER" sed -i \
      "s#^DATABASE_URL=.*#DATABASE_URL=postgresql://${DB_USER}:${DB_PASSWORD}@localhost:5432/${DB_NAME}#" \
      "$ENV_FILE"
    echo "    Pre-filled DATABASE_URL with the generated Postgres password."
  fi
  echo "    IMPORTANT: edit ${ENV_FILE} to set FINNHUB_API_KEY / OPENAI_API_KEY"
  echo "    (the agent will run in offline mock mode for both until you do)."
else
  echo "==> ${ENV_FILE} already exists, leaving it alone."
fi

echo "==> Verifying ${ENV_FILE} has no unfilled CHANGE_ME placeholders"
if grep -q "CHANGE_ME" "$ENV_FILE"; then
  echo "ERROR: ${ENV_FILE} still contains a CHANGE_ME placeholder." >&2
  echo "       This happens when the '${DB_USER}' role already existed from a" >&2
  echo "       previous run, so this script left DATABASE_URL untouched instead" >&2
  echo "       of filling in a real password. Edit DATABASE_URL in ${ENV_FILE}" >&2
  echo "       by hand (or ALTER ROLE ${DB_USER} WITH PASSWORD '<new-password>'" >&2
  echo "       and put that password in the URL), then re-run this script." >&2
  exit 1
fi

echo "==> Creating log directory (used only if AGENT_LOG_FILE is set in .env)"
mkdir -p "$LOG_DIR"
chown "${APP_USER}:${APP_USER}" "$LOG_DIR"

echo "==> Creating/updating database schema (idempotent — adds missing tables and columns)"
sudo -u "$APP_USER" bash -c "cd '${APP_DIR}/backend' && '${APP_DIR}/backend/.venv/bin/python' -c '
from app.database import bootstrap_schema
import app.models  # noqa: F401 — registers all tables on Base.metadata
bootstrap_schema()
print(\"    Schema OK.\")
'"

echo "==> Installing the systemd service"
cp "${APP_DIR}/backend/deploy/britney-agent.service" /etc/systemd/system/britney-agent.service
systemctl daemon-reload
systemctl enable britney-agent

echo ""
echo "==> Done."
echo "    1. Review/edit ${ENV_FILE}"
echo "    2. Start the agent:  systemctl start britney-agent"
echo "    3. Watch it run:     journalctl -u britney-agent -f"
