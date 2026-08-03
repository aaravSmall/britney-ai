# Tech stack: free (MVP) vs. paid (production)

britney.ai is currently wired to run entirely on free tiers / no-signup
public endpoints so the MVP costs nothing to run and demo. This doc tracks
what's used today and what to swap in as the product needs real reliability,
scale, or compliance guarantees.

| Category | Free / current (MVP) | Paid / recommended at scale | Why upgrade |
|---|---|---|---|
| Stock quotes | [Yahoo Finance public chart endpoint](https://query1.finance.yahoo.com/v8/finance/chart/) (`market_data.py`) — unauthenticated, no signup, ~15-min-delayed data | [Alpaca Market Data](https://alpaca.markets/data) (already partially wired — set `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`), or [Polygon.io](https://polygon.io) / [IEX Cloud](https://www.iex.io/cloud) / [Twelve Data](https://twelvedata.com) | Yahoo's endpoint is undocumented and unofficial — no SLA, can rate-limit or change shape without notice. Paid providers give real-time (not delayed) prices, official support, and websocket streaming instead of polling. |
| Crypto quotes | [CoinGecko free public API](https://www.coingecko.com/en/api) — no key, ~10-30 calls/min | [CoinGecko Pro](https://www.coingecko.com/en/api/pricing) or [CoinMarketCap](https://coinmarketcap.com/api/) paid tier | Higher rate limits and uptime guarantees once traffic grows past the free tier's throttling. |
| Trade execution | [Alpaca](https://alpaca.markets) paper trading (free account, `trading/execution.py` simulates fills locally either way) | Alpaca live trading (real money, same API) — or a licensed broker-dealer partner if going beyond Alpaca's supported markets | Paper trading has no regulatory requirements; routing real money requires KYC/AML, a funded brokerage relationship, and compliance review. |
| AI recommendations & chat | [OpenAI `gpt-4o-mini`](https://platform.openai.com) if `OPENAI_API_KEY` is set, else a deterministic offline mock (`recommendation_engine.py`, `chat_engine.py`) | Same OpenAI API with a funded billing account (usage-based, not a free tier) — or a cheaper/self-hosted model (e.g. an open-weight model via a provider like Groq/Together) if per-request cost matters at volume | The mock exists only so the app runs with zero cost/signup; OpenAI itself is pay-per-token from the first real key you add. |
| Auth | [Firebase Authentication](https://firebase.google.com/pricing) free (Spark) tier, or `AUTH_DISABLED=true` demo mode with no auth provider at all | Firebase Blaze (pay-as-you-go) — same product, billing only kicks in at high volume | Spark tier's free quota (50k MAU for most sign-in methods) is generous enough that this rarely needs upgrading early. |
| Database | Local PostgreSQL via `docker-compose.yml` (free, self-hosted) | Managed Postgres: [Supabase](https://supabase.com/pricing), [Neon](https://neon.tech/pricing), or [RDS](https://aws.amazon.com/rds/postgresql/pricing/) | Local Docker Postgres has no backups, HA, or public reachability — needed once anyone but you is hitting the API. |
| Backend hosting | Not yet deployed — runs locally via `uvicorn` | [Render](https://render.com/pricing), [Fly.io](https://fly.io/docs/about/pricing/), or [Railway](https://railway.app/pricing) — all have a free/hobby tier that's enough for a small beta | Free tiers sleep on inactivity and cap resources; paid dynos remove cold-starts and give more CPU/RAM headroom. |
| Frontend hosting | Not yet deployed — runs via `flutter run -d chrome` locally, or `flutter build web` served statically | [Firebase Hosting](https://firebase.google.com/pricing) free tier for web; Apple App Store ($99/yr) + Google Play ($25 one-time) for mobile distribution | Store fees are unavoidable for native app distribution; Firebase Hosting's free tier covers the web build until traffic is meaningful. |

## Notes

- Everything in the "free" column requires **zero signup** except Firebase
  (needed for real Google Sign-In — `AUTH_DISABLED=true` sidesteps it for
  local dev) and Alpaca (needed for paper trading, though the trade
  endpoint fully simulates fills without it too).
- The Yahoo Finance and CoinGecko integrations are the two spots most
  likely to need attention first if this goes from demo to real users —
  neither is designed for production load.
