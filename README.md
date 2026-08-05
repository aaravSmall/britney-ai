# britney.ai

AI-powered investing assistant: recommendations, chat, and paper-trading hooks — **no minimums, no paywalls** in product intent. This repo is a production-oriented **MVP scaffold** (FastAPI + PostgreSQL + Flutter).

## Architecture

- **`backend/`** — FastAPI REST API, OpenAI recommendations/chat, CoinGecko + optional Alpaca quotes, abstracted `execute_trade` / `simulate_trade`.
- **`frontend/`** — Flutter (mobile + web), dark Robinhood-style UI, Firebase Google Sign-In with a **demo mode** for local API testing.
- **`docker-compose.yml`** — PostgreSQL 16 for local development.

## Prerequisites

- Python 3.11+
- Docker (for Postgres) *or* your own PostgreSQL URL
- Flutter SDK (for mobile/web UI)
- Optional: OpenAI API key, Firebase project + `flutterfire configure`, Alpaca paper keys

## Backend

### 1. Start PostgreSQL

```bash
cd britney.ai
docker compose up -d
```

### 2. Configure environment

```bash
cd backend
cp .env.example .env
# Set DATABASE_URL if not using docker-compose defaults
# For local dev without Firebase verification:
echo 'AUTH_DISABLED=true' >> .env
# Optional: OPENAI_API_KEY=sk-...
```

### 3. Install and run

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Health: `GET http://localhost:8000/health`
- Docs: `http://localhost:8000/docs`

### Auth modes

- **`AUTH_DISABLED=true`** — Accepts any `Authorization: Bearer <token>` and maps to a single dev user. Use with the Flutter **Try demo** button.
- **Production** — Set `FIREBASE_CREDENTIALS_PATH` to a Firebase service account JSON file and `AUTH_DISABLED=false`. Verify Google ID tokens from the app.

## Frontend

### 1. Generate platform folders (if missing)

If `android/` / `ios/` are not present:

```bash
cd frontend
flutter create . --project-name britney_ai --org com.britneyai --platforms=ios,android,web
```

### 2. Dependencies

```bash
cd frontend
flutter pub get
```

### 3. Firebase (optional for Google Sign-In)

1. Create a Firebase project, enable Google auth.
2. Run `dart pub global activate flutterfire_cli` then `flutterfire configure` inside `frontend/` to generate real `lib/firebase_options.dart`.
3. Until then, use **Try demo (local backend)** on the login screen with `AUTH_DISABLED=true`.

### 4. Run

Web (API on same machine):

```bash
cd frontend
flutter run -d chrome --dart-define=API_BASE=http://localhost:8000
```

Android emulator (host loopback):

```bash
flutter run --dart-define=API_BASE=http://10.0.2.2:8000
```

iOS simulator can use `http://localhost:8000` if the API runs on the Mac.

## API summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness |
| GET | `/users/me` | Profile |
| POST | `/onboarding` | Risk, goals, horizon |
| GET | `/dashboard` | Balance, holdings, performance (demo seed if empty) |
| POST | `/generate-recommendation` | AI portfolio suggestion |
| POST | `/chat` | Context-aware assistant |
| POST | `/trading/trade` | Simulated / future Alpaca execution |
| PATCH | `/settings/auto-invest` | Toggle paper auto-invest |

## Deploying the trading agent

`backend/agent/run_agent.py` (the autonomous agent) deploys to a single
DigitalOcean droplet with Postgres installed directly on it. Full
step-by-step in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md); the short
version once a droplet is provisioned (`backend/deploy/provision.sh`):

```bash
# Status / logs
ssh root@<droplet-ip> systemctl status britney-agent
ssh root@<droplet-ip> journalctl -u britney-agent -f

# Restart
ssh root@<droplet-ip> systemctl restart britney-agent

# Connect to the droplet's Postgres
ssh root@<droplet-ip> 'sudo -u postgres psql -d britney'

# Ship a code update
DROPLET_HOST=root@<droplet-ip> backend/deploy/deploy.sh
```

## Security notes

- **Never** put OpenAI, Alpaca, or Firebase **server** secrets in the Flutter app.
- Use `.env` on the server and restrict CORS via `CORS_ORIGINS` in production.

## License

MIT (or your choice) — adjust as needed for your startup.
