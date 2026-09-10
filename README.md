# britney.ai

[![CI](https://github.com/aaravSmall/britney-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/aaravSmall/britney-ai/actions/workflows/ci.yml)

An AI-powered investing assistant aimed at beginners: personalized portfolio
recommendations, a chat coach that explains what's happening in plain
language, and paper-trading execution to practice with — with an explicit
product stance of **no minimums, no paywalls**. It's for someone who wants to
learn to invest without a brokerage's jargon or a $500 account minimum
standing in the way.

> **Screenshots: TODO.** None exist yet — the frontend has never been
> deployed anywhere public to capture them from. Contributions welcome.

## Architecture

- **`frontend/`** — Flutter, web only in practice today (no `android`/`ios`
  folders generated). Dark, Robinhood-style UI with a light theme option.
  Firebase Google Sign-In, plus a no-Firebase-required demo mode for local
  testing (see [Local setup](#local-setup)).
- **`backend/app/`** — FastAPI + SQLAlchemy REST API. OpenAI (`gpt-4o-mini`)
  drives recommendations, chat, and news sentiment, with a deterministic
  offline mock fallback everywhere it's used when no API key is configured.
  Market data comes from Yahoo Finance's free public endpoints and
  CoinGecko, with Alpaca as a not-yet-wired-up optional paid upgrade path.
  SQLite for local dev, Postgres for production (see
  [Local setup](#local-setup) for why those aren't symmetric today).
- **`backend/agent/`** — **a separate, standalone autonomous trading agent
  process** (own entrypoints, imports nothing from `app/` and isn't
  reachable through the API). This is the part of the codebase actually
  running unattended in production, live since mid-August with real trade
  data across three risk-tiered model portfolios (conservative / moderate /
  aggressive) plus a crypto sleeve. What it does, continuously:
  - Scores real news sentiment (Finnhub + CryptoPanic, gpt-4o-mini) and
    trades on high-confidence signals, risk-tiered.
  - **Discovers** candidate stocks from general market news beyond a fixed
    ticker list, validates each one against a real quote before ever
    trusting it.
  - **Classifies** every candidate (fixed and discovered) against
    price-growth, news-coverage, and volatility gates to decide what's
    "obvious" enough to hold at each risk tier.
  - **Rebalances** each tier back toward its target allocation daily, and
    **sells** a position whose classification flips against it (debounced
    over multiple days, so one noisy day doesn't trigger a sale).
  - Runs an independent **stop-loss** sweep every few minutes on every held
    position, bypassing the debounce above entirely on a sharp drop.

  It deploys to a DigitalOcean droplet as several independent
  `systemd`-managed processes — see [Deploying the trading
  agent](#deploying-the-trading-agent) below and
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full picture.
- **`docker-compose.yml`** — Postgres 16, for production-parity testing
  (see [Local setup](#local-setup) — this isn't the everyday local path).

## Local setup

This is the path actually used day to day. It gets you the user-facing app
(onboarding, dashboard, recommendations, chat, trading) running against
SQLite with zero external accounts required.

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Local dev runs SQLite, not the Postgres URL .env.example defaults to —
# override it when you start the server (see below), or edit DATABASE_URL
# in .env directly. Either way, "sqlite:///./dev.db" (or any local path)
# works with zero setup.

DATABASE_URL=sqlite:///./dev.db AUTH_DISABLED=true \
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Health: `GET http://localhost:8000/health`
- Interactive API docs: `http://localhost:8000/docs`
- `AUTH_DISABLED=true` accepts any `Authorization: Bearer <token>` and maps
  it to a per-token dev user — this is what makes the frontend's **Try demo**
  button work with no Firebase project and no manual database seeding: a
  first-ever request for a token auto-creates that user's portfolio with a
  small seeded demo position (VOO/AAPL/BTC), so the dashboard is meaningful
  immediately.
- `OPENAI_API_KEY` / `FINNHUB_API_KEY` are optional — every caller that
  would use them falls back to a clearly-logged deterministic mock when
  they're unset, so recommendations and chat still return *something*
  without either key. Set `OPENAI_API_KEY` for real AI output.

### 2. Frontend

```bash
cd frontend
flutter pub get
flutter build web
```

Serve the static build with any file server, e.g.
`python3 -m http.server 8080 --directory build/web`, or use the
hot-reload dev loop instead: `flutter run -d chrome`. Either way it talks
to `http://localhost:8000` by default (override with
`--dart-define=API_BASE=...`, e.g. `http://10.0.2.2:8000` for an Android
emulator). On the login screen, tap **Try demo (local backend)** — no
Google Sign-In setup needed.

### 3. Seeing the AI trading agent locally

The steps above only start the user-facing web app — **they never start the
autonomous agent**, which is entirely separate scheduled processes:
`backend/agent/run_agent.py` (news-driven trading + stop-loss, every few
minutes), `run_rebalance.py` (daily rebalance + classification),
`run_discovery.py` (hourly candidate discovery), `run_auto_invest.py`
(recurring user auto-invest). To see any agent behavior locally you have to
run one of these yourself, e.g. `python3 -m agent.run_agent` from
`backend/` with the same `.env`. Two things won't look like production even
then:
- A local demo user's own portfolio **never accumulates performance-chart
  history** — that's written by `agent/snapshot.py` during the agent's own
  run cycles, which nothing in the plain web-app flow triggers. Only the
  three agent-owned model portfolios get it, and only once one of the agent
  scripts above has actually run.
- Real crypto news (CryptoPanic) and Alpaca live order routing have never
  been exercised with real credentials, even in production — both fall
  back to their mock/placeholder path regardless of environment.

### Alternative: Postgres via docker-compose (production-parity testing)

Not the everyday path (Docker isn't even assumed to be installed above),
but useful if you specifically want to test against Postgres before
deploying:

```bash
docker compose up -d
cd backend
# DATABASE_URL in .env.example already matches docker-compose's defaults
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Google Sign-In

**Does not work out of the box.** `frontend/lib/firebase_options.dart` is
committed with placeholder values (`apiKey: 'REPLACE_ME'`,
`projectId: 'britney-ai-placeholder'`) so the repo builds without a real
Firebase project attached. To enable it for real: create a Firebase
project, enable Google auth, run
`dart pub global activate flutterfire_cli` then `flutterfire configure`
inside `frontend/` to regenerate `firebase_options.dart` with real values,
and run the backend with `FIREBASE_CREDENTIALS_PATH` set to a service
account JSON and `AUTH_DISABLED=false`. Until then, use **Try demo (local
backend)** — that's the only auth path that currently works.

## Testing & CI

```bash
cd backend && pytest          # 190 tests across 17 files
cd frontend && flutter test   # 1 widget test
```

Two GitHub Actions jobs run on every push and pull request against `main`
([.github/workflows/ci.yml](.github/workflows/ci.yml)): backend `pytest`
(no secrets required — every external-service key defaults to a mocked
fallback, confirmed in `tests/conftest.py`) and frontend `flutter test` +
`flutter build web`, on a pinned Flutter version. `tests/test_stocks.py`
and `tests/test_favorites.py` deliberately hit real Yahoo Finance
endpoints rather than mocking them — a known, accepted source of
occasional CI flakiness, not a bug.

## API surface

32 endpoints across 11 routers. Full detail in the interactive docs at
`/docs` once running; summary below.

| Method | Path | Router |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/users/me` | Current user's profile |
| GET | `/onboarding/questions` | The risk-questionnaire question bank |
| GET | `/onboarding` | This user's onboarding state |
| POST | `/onboarding` | Submit questionnaire answers → scored risk tolerance |
| GET | `/dashboard` | Balance, holdings, day change (demo-seeds an empty portfolio) |
| GET | `/dashboard/performance`, `/dashboard/performance/{symbol}` | Portfolio / per-holding value history |
| GET | `/portfolios` | List this user's portfolio + the agent's model portfolios |
| GET | `/portfolios/{id}/summary` | Same shape as `/dashboard`, for any portfolio |
| GET | `/portfolios/{id}/performance`, `/portfolios/{id}/performance/{symbol}` | Value / per-holding history for any portfolio |
| GET | `/portfolios/{id}/trades` | Paginated trade history, filterable |
| GET | `/portfolios/{id}/trades/{trade_id}/decision` | AI trade rationale (news citations) for an agent-sourced trade |
| POST | `/portfolios/{id}/deposit`, `/portfolios/{id}/withdraw` | Simulated cash movement |
| POST | `/generate-recommendation` | AI portfolio suggestion, grounded in real news sentiment |
| POST | `/chat` | Context-aware assistant; can look up real trade/price history to answer performance questions |
| GET | `/trading/price` | Live quote for a symbol |
| POST | `/trading/trade` | Place a paper trade (queues instead of filling if placed off-hours) |
| GET | `/stocks/search`, `/stocks/{ticker}/quote`, `/stocks/{ticker}/history` | Public stock search/quote/history |
| GET, POST, DELETE | `/favorites`, `/favorites/{ticker}` | Per-user watchlist |
| GET, POST, PATCH, DELETE | `/auto-invest/schedules`, `/auto-invest/schedules/{id}` | Recurring auto-invest schedule CRUD |
| PATCH | `/settings/auto-invest`, `/settings/timezone` | User settings |

## Deploying the trading agent

`backend/agent/run_agent.py` and its sibling entrypoints deploy to a
single DigitalOcean droplet, with Postgres installed directly on it. Full
step-by-step in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md); the short
version once a droplet is provisioned (`backend/deploy/provision.sh`):

```bash
# Status / logs
ssh root@<droplet-ip> systemctl status britney-agent
ssh root@<droplet-ip> journalctl -u britney-agent -f

# Restart (a git pull alone does NOT restart the long-running agent/API
# processes — see docs/DEPLOYMENT.md)
ssh root@<droplet-ip> systemctl restart britney-agent

# Connect to the droplet's Postgres
ssh root@<droplet-ip> 'sudo -u postgres psql -d britney'

# Ship a code update
DROPLET_HOST=root@<droplet-ip> backend/deploy/deploy.sh
```

## Further reading

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — how the trading agent's
  droplet is provisioned and operated.
- [docs/TECH_STACK.md](docs/TECH_STACK.md) — what's free-tier-now vs.
  paid-at-scale for every external dependency.
- [docs/DISCOVERY_DESIGN.md](docs/DISCOVERY_DESIGN.md) — the design
  investigation behind the discovery/classification/rebalance feature
  family described above.

## Security notes

- **Never** put OpenAI, Alpaca, or Firebase **server** secrets in the
  Flutter app.
- Use `.env` on the server (never committed — see `.gitignore`) and
  restrict CORS via `CORS_ORIGINS` in production.

## License

**TBD.** Not yet decided — treat this repo as all-rights-reserved until a
license is added.
