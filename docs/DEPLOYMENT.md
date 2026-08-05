# Deploying the trading agent to a DigitalOcean droplet

This covers `backend/agent/run_agent.py` — the autonomous trading agent —
running continuously on a single droplet, with Postgres installed
directly on that same droplet (not a managed add-on; see the "Why
self-hosted Postgres for now" note below). It does **not** cover
deploying the FastAPI web app (`app/main.py`) or the Flutter frontend —
the agent never imports either.

Scripts referenced below live in `backend/deploy/`:
- `provision.sh` — idempotent setup script, run on the droplet
- `britney-agent.service` — the systemd unit
- `agent.env.example` — environment variable template
- `deploy.sh` — pushes a code update to an already-provisioned droplet

## 1. Create the droplet

Droplet creation itself needs a DigitalOcean account/API token, so it's a
manual (or `doctl`) step rather than something `provision.sh` can do for
you.

**Sizing:** the agent is a lightweight Python polling loop (a few HTTP
calls to Yahoo/CoinGecko/Finnhub/OpenAI every 15-60 min, no web traffic to
serve) plus a small local Postgres instance. The cheapest droplet tier
comfortably fits both:

- **Image:** Ubuntu 22.04 or 24.04 LTS
- **Plan:** Basic / Regular, 1 vCPU, 1 GB RAM (DigitalOcean's smallest
  "Basic" droplet — ~$6/mo at time of writing). Bump to 2 GB if Postgres
  feels tight once you have real data, but 1 GB is plenty for the mock
  three-portfolio setup this ships with.
- **Region:** whichever is closest to you for lowest-latency SSH/`psql`;
  doesn't otherwise matter (the agent's external calls are all
  internet-wide APIs, not region-pinned).

Via the web console: **Create → Droplets**, pick the image/plan above,
add your SSH key, create.

Via `doctl` (if you have it configured):

```bash
doctl compute droplet create britney-agent \
  --image ubuntu-24-04-x64 \
  --size s-1vcpu-1gb \
  --region nyc1 \
  --ssh-keys <your-ssh-key-fingerprint>
```

Note the droplet's public IP either way — you'll need it for every step
below.

## 2. Provision the droplet

SSH in as root and run the provisioning script:

```bash
ssh root@<droplet-ip>
curl -fsSL https://raw.githubusercontent.com/aaravSmall/britney-ai/main/backend/deploy/provision.sh -o provision.sh
bash provision.sh
```

This installs Python 3 + venv, Postgres, and git; creates a dedicated
`britney` system user (the agent doesn't run as root); enables Postgres
to start on boot (`systemctl enable postgresql` — Ubuntu already ships a
standard systemd unit for it, nothing custom needed); creates the
`britney` Postgres role + database with a random generated password;
clones this repo to `/opt/britney-ai`; creates a venv and installs
`requirements.txt`; copies `agent.env.example` to `backend/.env` (with
`DATABASE_URL` pre-filled using the generated password); and installs +
enables (but does not yet start) the `britney-agent` systemd service.

It's idempotent — re-run it after a `git pull` to pick up new
dependencies or a systemd unit change, without disturbing an
already-configured `.env` or an existing Postgres role's password.

## 3. Configure environment variables

The script creates `/opt/britney-ai/backend/.env` from
`backend/deploy/agent.env.example` on first run — **never committed to
git**, same as local dev. Edit it on the droplet:

```bash
nano /opt/britney-ai/backend/.env
```

| Variable | Required? | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | Pre-filled by `provision.sh` against the local Postgres it just created. Only change this if pointing at a different DB. |
| `FINNHUB_API_KEY` | No | Real news ingestion needs this (free tier at finnhub.io). Empty → offline mock articles, agent still runs. |
| `OPENAI_API_KEY` | No | Real LLM sentiment scoring needs this. Empty → offline keyword-heuristic mock scores, agent still runs. |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | No | Only used if you want Alpaca stock quotes instead of the free Yahoo Finance fallback `market_data.py` already uses. |
| `AGENT_LOG_FILE` | No | Set to also write logs to a file (with rotation) in addition to journald — see logging section below. |
| `FIREBASE_CREDENTIALS_PATH`, `AUTH_DISABLED`, `CORS_ORIGINS` | N/A | Not used by the agent at all — only relevant if this droplet later also runs the API server. |

The agent runs fine with only `DATABASE_URL` set — it just trades on mock
news/sentiment until you add real API keys.

## 4. Start the agent

```bash
systemctl start britney-agent
systemctl status britney-agent
```

`britney-agent.service` (`backend/deploy/britney-agent.service`) is
configured with `Restart=always` and is enabled via `systemctl enable`,
so it comes back automatically both on a crash and on droplet reboot —
same as Postgres's own unit.

## 5. Operations

**Check status:**
```bash
systemctl status britney-agent
```

**Tail logs** (stdout/stderr are captured by journald automatically —
zero extra setup needed):
```bash
journalctl -u britney-agent -f          # live tail
journalctl -u britney-agent --since today
```

If you also set `AGENT_LOG_FILE` in `.env`, the same output is written to
that file too (rotated at 10 MB, 5 backups kept) — useful if you'd rather
`tail -f` a plain file:
```bash
tail -f /var/log/britney-agent/agent.log
```

**Restart:**
```bash
systemctl restart britney-agent
```

**Stop / re-enable auto-start:**
```bash
systemctl stop britney-agent
systemctl disable britney-agent   # if you want it to NOT come back on reboot
```

**Connect to Postgres for debugging:**
```bash
sudo -u postgres psql -d britney
# or, as the app role:
psql "$(grep DATABASE_URL /opt/britney-ai/backend/.env | cut -d= -f2-)"
```
Useful queries once connected:
```sql
select id, owner_type, risk_tolerance, cash_balance from portfolios;
select portfolio_id, symbol, side, quantity, price, source, timestamp
  from trades order by timestamp desc limit 20;
select portfolio_id, decision, confidence, reasoning, timestamp
  from agent_decisions order by timestamp desc limit 20;
```

**Update the code after a change** (from your local machine):
```bash
DROPLET_HOST=root@<droplet-ip> backend/deploy/deploy.sh
```
This SSHes in, `git pull`s the repo, reinstalls dependencies if
`requirements.txt` changed, and restarts the service. Equivalent by hand,
if you're already SSHed in:
```bash
cd /opt/britney-ai && sudo -u britney git pull
sudo -u britney backend/.venv/bin/pip install -r backend/requirements.txt
systemctl restart britney-agent
```
Or just re-run `provision.sh` — it does the same thing plus picks up any
new system-level dependencies or a changed systemd unit file.

## Why self-hosted Postgres for now

Postgres runs directly on the droplet rather than as a DigitalOcean
Managed Database. That's free and plenty for the current three
agent-managed model portfolios (conservative/moderate/aggressive) — there's
no real user data on this droplet yet. See `docs/IDEAS.txt` for the note
flagging this as a promotion candidate once real users are involved: a
managed Postgres add-on (~$15/mo) buys automated backups, point-in-time
recovery, easier vertical/read-replica scaling, and decouples the
database's lifecycle from the agent process's — none of which matter yet
for a handful of model portfolios but all of which matter once real
money/accounts are on the line.
