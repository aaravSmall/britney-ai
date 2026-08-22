# britney.ai — overview

*Last updated 2026-08-22, written to be dropped into a fresh Claude chat
as planning context for what comes next.*

## What it is

An AI-powered investing assistant aimed at beginners: personalized
portfolio recommendations, a chat coach, and paper-trading execution —
with an explicit product stance of **no minimums, no paywalls**. It's a
Flutter (mobile + web) frontend on a FastAPI backend, currently built and
run as an MVP scaffold. Nothing is hosted for end users yet — the only
thing actually deployed anywhere is the autonomous trading agent, running
unattended on a DigitalOcean droplet (see "Sprint 0" below and
`docs/DEPLOYMENT.md`). The web app + Flutter frontend are still local-only.

The agent itself has grown well past "read news, trade on sentiment"
since the last full rewrite of this doc: it now also runs a daily
target-allocation rebalance job with an AI-discovered "non-obvious" stock
sleeve alongside its fixed "obvious" holdings, a 3-consecutive-day
debounced sell when a holding's obvious/non-obvious classification
flips, and an independent emergency stop-loss that bypasses that debounce
entirely on a sharp drop. See "Sprint 5" below — this is the single
largest addition since Sprint 0 and spans several new modules.

**As of this pass, Sprint 5's mechanisms have moved from "deployed but
unexercised" to actively shaping real portfolios** — see "Sprint 6" and
especially "AI model performance report" below for real numbers: the
discovery/classification pipeline is now routing real non-obvious buys
(NOK, HIMS bought live on 2026-08-21), several of the *fixed* 6 tickers
(MSFT, AAPL, QQQ, BND) have themselves flipped to non-obvious/debounced
at various points, and the aggressive tier hit a real "53.9% of target
sitting in cash, nothing obvious to buy" cycle. There's also a new,
uncommitted chunk of work on top of Sprint 5 — a chat tool that grounds
"why did I lose money" answers in real trade/price data, plus a
user-facing display-timezone setting — both still sitting as local
changes, not yet committed (see "Sprint 6").

## Architecture

- **`backend/app/`** — FastAPI + SQLAlchemy + PostgreSQL (SQLite works
  fine for local dev too). OpenAI (`gpt-4o-mini`) drives recommendations,
  chat, news sentiment, discovery extraction, and offline mock fallbacks
  everywhere it's used when no API key is set (each caller logs clearly
  which mode it's in). Market data comes from Yahoo Finance's free public
  endpoints for stocks (both the unofficial `v8/finance/chart`/`v1/finance/search`
  JSON endpoints via plain `httpx`, and `yfinance` specifically for
  quote/profile fields that need Yahoo's crumb/cookie handshake) and
  CoinGecko for crypto, with Alpaca as an optional paid upgrade path for
  both quotes and (eventually) live order routing. Company/market news
  comes from Finnhub (both the per-ticker `/company-news` endpoint and,
  since Sprint 5, the general-market `/news?category=general` endpoint)
  and CryptoPanic for crypto (never verified against a real key — see
  Known gaps).
  - `routes/` — `dashboard.py`, `portfolios.py`, `trading.py`,
    `recommendations.py`, `chat.py`, `onboarding.py`, `settings.py`,
    `users.py`, `health.py`, `stocks.py` (public search/quote/history),
    `favorites.py` (per-user watchlist), `auto_invest.py` (recurring
    schedule CRUD, Sprint 3), `cash.py` (deposit/withdraw, Sprint 3).
  - `services/` — `portfolio_service.py` (holdings valuation, dashboard
    assembly, per-symbol cost-basis/P&L, pending-trade queue/fill —
    Sprint 3), `portfolio_snapshot_service.py` (performance chart data),
    `agent_decision_service.py`, `market_data.py`, `stock_data.py`
    (search/quote/history against Yahoo), `firebase_auth.py`,
    `cash_ledger.py` (Sprint 3 — see below), `auto_invest_service.py`
    (Sprint 3), `risk_questionnaire.py` (Sprint 3 — scoring only, no
    routes/ file of its own since onboarding.py already owned the route),
    `rebalance_service.py` (Sprint 4/5 — target-allocation math, the
    obvious/non-obvious split, and both deliberate sell paths; see
    Sprint 5), `discovery_service.py` (Sprint 5 — persistence/dedup for
    discovered candidates).
  - `ai/recommendation_engine.py` — OpenAI + mock recommendation logic.
  - `database.py` — `bootstrap_schema()`: runs `create_all()` and then
    diffs each model's columns against the live table, `ALTER TABLE ADD
    COLUMN`-ing anything missing (backfilling NOT NULL columns with their
    model-level default). Runs on every startup (`app/main.py`'s
    lifespan, every `agent/run_*.py` script's startup, `provision.sh`'s
    droplet schema step).
- **`backend/agent/`** — the standalone autonomous trading agent process
  family (imports nothing from `app/` and vice versa except where
  `recommendation_engine.py` deliberately reuses `agent/news_ingestion.py`
  + `agent/sentiment.py`). As of Sprint 5 this is no longer one script —
  it's five independent scheduled entrypoints sharing library modules:
  - `run_agent.py` — the original 3-min news-driven decision loop
    (`decision_loop.py`), now ALSO runs `stop_loss.py`'s emergency sweep
    every cycle (Sprint 5).
  - `run_rebalance.py` — daily 10am ET target-allocation rebalance
    (Sprint 4), now also running classification, the debounced sell, and
    the stop-loss sweep as part of the same cycle (Sprint 5).
  - `run_auto_invest.py` — 5-min recurring user auto-invest poller
    (Sprint 3).
  - `run_discovery.py` — hourly general-news discovery pipeline
    (Sprint 5).
  - `decision_loop.py`, `news_ingestion.py`, `sentiment.py`,
    `market_hours.py`, `snapshot.py` — library modules, mostly unchanged
    since Sprint 0/3 (see below for what did change).
  - `agent_config.py` (Sprint 5) — single source of truth for every
    discovery/classification/rebalance-split/stop-loss constant added
    since Sprint 5 started; see that section for what lives here.
  - `classification.py`, `discovery.py`, `stop_loss.py` (all Sprint 5) —
    see below.
- **`backend/deploy/`** — droplet provisioning/deploy scripts and systemd
  units: `provision.sh`, `deploy.sh`, `britney-agent.service`,
  `britney-api.service`, `britney-rebalance.service` + `.timer`,
  `britney-discovery.service` + `.timer`. **Gotcha (found the hard way,
  Sprint 5):** `britney-agent.service` and `britney-api.service` are
  long-running (`Type=simple`/`Restart=always`) — a `git pull` on the
  droplet updates files on disk but does **not** make an already-running
  process pick up the new code. `deploy.sh` already does `systemctl
  restart britney-agent` after every pull; any manual/ad-hoc SSH deploy
  must do the same explicitly, and it's worth verifying via
  `journalctl -u britney-agent -n 20` that new behavior actually shows up
  in the live log, not just trusting that the pull succeeded. The
  `Type=oneshot` services (`britney-rebalance`, `britney-discovery`) are
  unaffected — each timer fire starts a brand-new process, so they always
  run current disk code with no restart needed.
- **`backend/tests/`** — pytest suite, **183 tests** across 17 files (up
  from 173/14 at the last full doc pass — verified passing locally
  2026-08-22, `183 passed` in ~5s): `test_agent_dedup.py`,
  `test_auto_invest.py`, `test_cash_ledger.py`, `test_chat_activity_tool.py`
  (Sprint 6, uncommitted — see below), `test_classification.py`
  (Sprint 5), `test_decision_loop.py`, `test_discovery.py` (Sprint 5),
  `test_favorites.py`, `test_onboarding.py`, `test_pending_trades.py`,
  `test_portfolio_auth.py`, `test_rebalance.py` (Sprint 4/5),
  `test_run_agent_schedule.py`, `test_sizing.py`, `test_stocks.py`,
  `test_stop_loss.py` (Sprint 5), `test_trading_route.py`.
  No CI wired up yet — see Known gaps.
- **`frontend/`** — Flutter, dark Robinhood-style UI with a light theme
  option, Firebase Google Sign-In plus a no-Firebase-required demo mode
  (`AUTH_DISABLED=true` on the backend) for local testing. Bottom-nav
  tabs (in order): Portfolio (dashboard), Favorites, AI picks
  (recommendations), Chat, Account — kept alive across switches via
  `StatefulShellRoute.indexedStack`. `/trade-history/:portfolioId` and
  `/stock/:ticker` are top-level routes outside that shell (path params,
  not GoRouter `extra`, specifically so a Flutter-web page refresh/deep
  link still works).
  - `screens/` — `dashboard_screen.dart`, `favorites_screen.dart`,
    `recommendations_screen.dart`, `chat_screen.dart`, `account_screen.dart`,
    `onboarding_screen.dart` (rebuilt as a 12-question wizard, Sprint 3),
    `login_screen.dart`, `stock_detail_screen.dart`,
    `trade_history_screen.dart` (Queued Orders section added, Sprint 3).
  - `widgets/` — `price_chart.dart`, `buy_sell_bottom_sheet.dart`,
    `trade_rationale_sheet.dart`, `cash_amount_sheet.dart` (deposit/
    withdraw, Sprint 3).
- **`docker-compose.yml`** — local Postgres for dev (SQLite is what's
  actually used day-to-day per current local setup, see below).
- **Local dev note:** no Docker/Postgres running locally right now — a
  `.env` exists but `DATABASE_URL` still gets overridden to SQLite for
  local work; frontend gets tested as a static `flutter build web` output
  rather than `flutter run`.

## What's been built — user-facing app

**Auth & onboarding** — Google Sign-In via Firebase, or a one-tap demo
login for local dev. As of Sprint 3, onboarding is a scored 12-question
wizard (see below) rather than a single free-text risk field.

**Portfolio dashboard** (`dashboard_screen.dart`) —
- Total value, cash balance, holdings list. Holdings are tappable into a
  merged position/cost-basis/P&L detail view.
- Performance chart wired to real `PortfolioSnapshot` data (`GET
  /dashboard/performance` / `GET /portfolios/{id}/performance`, `range`/
  `since`/`until` filters). Below 3 points in the selected range it falls
  back to a plain current-value state instead of a misleading line
  through 1-2 dots; 0 points shows an explicit empty-history placeholder.
- Line, candlestick, and waterfall chart modes (shared with the stock
  detail page via `widgets/price_chart.dart`); range toggles
  (1D/1W/30D/YTD/5Y); drag-to-scrub tooltip.
- Light/dark theme and 12h/24h time format, both persisted.
- **Multi-portfolio switcher** — a dropdown in the AppBar title lets the
  user view their own portfolio *or* any of the agent's three risk-tier
  model portfolios (Conservative/Moderate/Aggressive) from the same
  screen; cash, holdings, performance chart, and trade history all
  re-scope to whichever portfolio is selected.
- **Inline slide-out search** — a search icon in the AppBar animates a
  search field in place, debounced `GET /stocks/search` results render
  as a floating dropdown; selecting a result pushes `/stock/:ticker`.
- **Queued Orders section** (Sprint 3, `trade_history_screen.dart`) —
  shown only when a portfolio has pending (off-hours-queued) orders:
  symbol, side, quantity, and a current indicative price (`GET
  /stocks/{ticker}/quote`). See "Off-hours order queueing" under Sprint 3
  for the backend mechanics this surfaces.
- **Trade History** row below Holdings links to the trade history screen.

**Trade History / order log** (`trade_history_screen.dart`) — every fill
is queryable: infinite-scroll pagination (`GET /portfolios/{id}/trades`,
filterable by `symbol`), a source filter (user/agent/auto_invest/
rebalance, widened in Sprint 3/4 as those sources were added) via
`SegmentedButton`, a status filter (Sprint 3, so pending orders can be
isolated from filled ones), distinct empty state, graceful 404/malformed-
id/network-error handling. AI-sourced trades carry a "More info" icon
opening the trade rationale sheet.

**Stock detail / holdings drill-down** (`stock_detail_screen.dart`) — a
standalone `/stock/:ticker` page usable from any entry point (search,
favorites, a dashboard holding tile) that shows a Robinhood-style header
(price, colored day change), a heart-icon favorite toggle, the shared
`price_chart.dart` view with kind/range toggles, a key-stats section
(market cap, P/E, day/52-week range, volume, dividend yield), an About
section, and Buy/Sell buttons wired to the shared bottom sheet. When
reached from a dashboard holding tile it also shows a **"Your position"**
section: cost basis and unrealized P&L computed from the holding's
running weighted-average cost, plus that ticker's recent trades. Crypto
holdings get a simpler scaffold (current price + per-symbol price chart).

**Favorites** (`favorites_screen.dart` + `favorites.py`) — a per-user
watchlist: `Favorite` model, `GET`/`POST`/`DELETE /favorites`, list
responses enriched with a live quote, dedicated bottom-nav tab.

**Buy/Sell** — unified into one reusable flow
(`widgets/buy_sell_bottom_sheet.dart`), called from recommendations, the
stock detail page, search results, and favorites alike. Supports entering
either a **dollar amount or a share quantity**, a live preview while
typing, and an explicit **confirm step** before submitting. Submits to
`POST /trading/trade`; as of Sprint 3, a stock order placed outside NYSE
hours now queues (`status="pending"`) the same way the agent's own
off-hours decisions already did, instead of filling at a stale
after-hours quote — see "Off-hours order queueing" below. A cross-tab
notifier (`PortfolioBus`) keeps the dashboard in sync after a trade fires
from elsewhere.

**AI recommendations** (`recommendations_screen.dart` +
`ai/recommendation_engine.py`) — generates a suggested allocation from
the user's risk profile, grounded in real news sentiment: candidate
symbols come from `agent/news_ingestion.TARGET_PORTFOLIOS` per risk tier,
then `fetch_news` + `sentiment.score_batch` run against a fresh
in-memory `SeenArticleStore` scoped to the request. An automated
consistency check swaps to a templated fallback if the LLM output fails
it.

**"More info" trade rationale view** (`widgets/trade_rationale_sheet.dart`) —
AI-badged trades (`source == "agent"`) expose *why* the agent traded.
`GET /portfolios/{id}/trades/{trade_id}/decision` serves the persisted
`AgentDecision.articles` citations (404 if the trade has no linked
decision — true for any user/auto_invest/rebalance/stop_loss-sourced
trade, not just user ones, since only decision_loop.py's news-driven
trades ever create an `AgentDecision` row).

**Chat** — a context-aware assistant that knows the user's profile and
current holdings, same OpenAI/mock split as recommendations. As of
Sprint 6 (uncommitted), it can also answer "why did I lose money on
&lt;date&gt;"-style questions by calling a real OpenAI tool
(`get_portfolio_activity`) that looks up actual trades, cash movements,
snapshot history, and per-holding price moves for the window in
question, rather than guessing from the static holdings summary (which
carries no date information at all). See "Sprint 6" and "AI model
performance report" below.

**Live market data** — stock and crypto prices shown across the app are
real, fetched from free public sources (no signup required for either).

**Account / settings** — auto-invest toggle + schedule management
(Sprint 3), deposit/withdraw (Sprint 3), theme, time format, and (Sprint
6, uncommitted) a display timezone picker — see "Sprint 6" below.

## Sprint 0 — autonomous trading agent (deployed)

**Schema** — `AgentDecision` and `PortfolioSnapshot` models.
`Portfolio`/`Trade` absorbed the old `User.cash_balance`/holding flow:
`Portfolio.user_id` is nullable with `owner_type` of `"user"` or
`"agent"` (agent portfolios are one-per-risk-tier, carry their own
`risk_tolerance`), and `Trade.source` started as `"user"`/`"agent"` and
has since grown to `"auto_invest"`/`"rebalance"`/`"stop_loss"` (Sprint
3-5).

**Agent pipeline** (`backend/agent/`) —
- `news_ingestion.py` — real stock/ETF news from Finnhub, plus real
  crypto news from CryptoPanic (`fetch_crypto_news()`) as a symmetric
  sibling. Real-API verification against CryptoPanic itself has still
  never been done — see Known gaps (unchanged since Sprint 0/2).
- `sentiment.py` — scores it with real OpenAI `gpt-4o-mini` (batched, 429
  backoff/retry, explicit real-vs-mock logging); the blocking OpenAI call
  runs in a threadpool so it can't stall the shared event loop.
- `decision_loop.py` + `run_agent.py` — apply risk-tiered decision
  thresholds for conservative/moderate/aggressive tiers. As of Sprint 3
  the poll cadence is a flat 3 minutes 24/7 (was 15-min-market-hours/
  60-min-after-hours); as of Sprint 5, `run_agent.py`'s loop also runs
  `stop_loss.py`'s emergency sweep every cycle (see Sprint 5). Crypto
  trades through the same decision path as stocks, but has never actually
  fired in real production, because `CRYPTOPANIC_API_KEY` has never been
  configured — real crypto news ingestion returns deterministic offline
  mock articles, so crypto sentiment never has a real signal behind it in
  practice, even though the code path itself was verified once with a
  forced BTC buy+sell against a real agent portfolio.
- `snapshot.py` — captures `PortfolioSnapshot` rows, decoupled from the
  decision loop's trading logic. Its dedupe window was shrunk from 10 to
  2 minutes in Sprint 3 to match the new 3-min poll cadence (the old
  10-min window was silently swallowing most legitimate snapshots once
  polling got faster than it).

**Deployment** — live and running unattended on a DigitalOcean droplet
via `provision.sh` + systemd units (see Architecture above for the full
current list — it's grown from one service to two long-running services
plus two timer-driven oneshot pairs since Sprint 0). Postgres runs on the
droplet itself (not managed) — see `docs/DEPLOYMENT.md`.

## Sprint 2 — hardening, search/favorites, and bug fixes (2026-08-06 → 2026-08-08)

A batch of backend hardening plus the full stock-search/favorites/detail
feature. In rough order: owner auth scoping on portfolio reads (401/403
instead of any user reading any portfolio by guessing an id), crypto news
+ crypto trading, an event-loop-blocking fix (sync OpenAI calls wrapped
in `run_in_threadpool`, verified 12.25s serialized → 4.20s concurrent for
3 requests), the stock search/quote/history + favorites + detail page
feature (6-step build), a crypto quantity display precision fix (was
silently rounding toward `0.0000`), the buy/sell confirm step + $/shares
toggle, holdings tappable into a merged detail view, trade rationale
citations + "More info", and the `bootstrap_schema()` fix for
`create_all()` never `ALTER`ing existing tables on model changes (see
the `project_schema_no_migrations_incident` memory for the fuller
incident writeup — a real, if brief, "agent's balance and trades
appeared to vanish" scare from before this fix existed).

## Sprint 3 — cash ledger, auto-invest, risk questionnaire, off-hours order queueing (2026-08-08 → 2026-08-14)

**Cash ledger audit trail** — `Portfolio.cash_balance` is no longer
mutated directly anywhere; `app/services/cash_ledger.py`'s
`apply_cash_delta(db, portfolio, amount, entry_type, trade_id=None,
note=None)` is the single choke point, pairing every balance change with
a `CashLedgerEntry` row (entry_type: trade/deposit/withdrawal, signed
amount, `balance_after`, optional `trade_id`/`note`). `record_trade_fill`
was refactored to call it instead of `cash_balance +=`/`-=` directly.
`POST /portfolios/{id}/deposit` and `/withdraw` let a user top up/pull
down their own simulated cash; agent portfolios 403 on both.

**Recurring auto-invest schedules** — `AutoInvestSchedule` lets a user
buy a fixed $ amount of one ticker on a daily/weekly/monthly cadence.
CRUD at `/auto-invest/schedules`. Execution is a separate standalone
process, `agent/run_auto_invest.py` (own `asyncio.run()`, own
`SessionLocal`, polls every 5 min, no market-hours gating), with
due-check/execution logic in `app/services/auto_invest_service.py`
reusing the same `execute_trade`/`record_trade_fill` path every other
trade source uses. Fills are tagged `Trade.source="auto_invest"`.
Explicitly unrelated to the pre-existing `User.auto_invest_enabled` bare
bool (that only gates simulated-vs-live execution on manually-submitted
trades — deliberately untouched). Accepted limitation: no catch-up/
backfill for missed intervals if the poller isn't running when a
schedule comes due.

**Risk questionnaire rebuild** — onboarding is now a 4-section, 12-
question scored wizard (`frontend/lib/screens/onboarding_screen.dart`)
instead of a single-page low/medium/high radio. Every question is worth
1-5 points; the question bank + scoring (`QUESTIONS`, `bucket_for_score()`)
is entirely server-side in `app/services/risk_questionnaire.py` — the
client only ever POSTs which option it picked, never a point value, so
scoring can't be gamed from the frontend. Total score (12-60) buckets
into the same `"low"`/`"medium"`/`"high"` vocabulary `User.risk_tolerance`
already used, so `recommendation_engine.py`/`decision_loop.py`/
`chat_engine.py` needed zero changes. Individual answers persist to a new
`RiskQuestionnaireResponse` table as an audit trail only. Verified
end-to-end: a "high"-scored questionnaire → `risk_tolerance="high"` →
a real `/generate-recommendation` call returned exactly
`TARGET_PORTFOLIOS["aggressive"]`'s tickers with zero pipeline changes.

**Off-hours order queueing** — a stock decision made outside NYSE hours
used to fill instantly at Yahoo's stale after-hours `regularMarketPrice`,
indistinguishable from a during-hours fill. Off-hours stock buy/sell
decisions now queue as `status="pending"` `Trade` rows (no cash/holdings/
ledger effect until settled) and fill at the real market-open quote via a
precise 9:30am ET wake in `run_agent.py`'s loop, independent of the
regular poll cadence — `agent/market_hours.py` grew `next_market_open()`
for this. Crypto is unaffected (24/7, always fills immediately). This
was later (same sprint) extended from agent-only to user-submitted
orders too (`POST /trading/trade` now queues the same way for an
off-hours stock order), and the dashboard grew a Queued Orders section to
surface pending rows (see "What's been built" above). The cash-ledger
invariant (`cash_balance == starting balance + sum of ledger entries`)
was verified to hold with pending trades in the mix
(`tests/test_pending_trades.py`).

**Poll cadence flattened to 3 min 24/7** — replaced the old
15-min-market-hours/60-min-after-hours split. The investigation found
real Finnhub call volume (6 calls/sweep across 3 portfolios) is over an
order of magnitude under the free-tier ~60/min ceiling even at this
cadence, so rate limiting was never the binding constraint. The precise
9:30am ET pending-fill wake composes safely with the faster cadence
without any code change (self-limiting to once per day by construction —
verified via simulation in `tests/test_run_agent_schedule.py`). Two
follow-up fixes landed right after, both caught live rather than in
review: the snapshot dedupe window (see Sprint 0's `snapshot.py` note
above) and a trade-history `?source=` filter that 422'd on the newer
`auto_invest`/`rebalance` values because its regex was never widened past
the original `user`/`agent`.

**`britney-api.service`** — the FastAPI web server's systemd unit,
already running on the droplet but never checked into the repo until
this sprint (so a fresh `provision.sh` run now sets it up too, matching
the agent's and rebalance's own units).

## Sprint 4 — target-allocation rebalance job (2026-08-15 → 2026-08-16)

`decision_loop.py` only trades when a confidence-gated news signal fires,
which real production history showed could leave a tier sitting on a
large idle cash pile indefinitely (conservative's 0.75 confidence
threshold had ~87% idle cash in practice). `agent/run_rebalance.py` +
`app/services/rebalance_service.py` added a separate, independent daily
job (10:00am ET, after the 9:30 queued-fill wake has already settled that
morning's pending trades) that tops each of the 3 agent tiers back up
toward a fixed target weight — `REBALANCE_TARGET_WEIGHTS`, originally a
flat `{tier: {ticker: weight}}` (conservative VOO 60%/BND 35%, moderate
SPY/MSFT 48.5% each, aggressive QQQ/AAPL 49% each, remainder cash) — on a
schedule, buy-only (never sells an overweight position down; this rule
was later deliberately reversed in two specific cases — see Sprint 5).
Reuses the exact same `execute_trade()`/`record_trade_fill()` path every
other trade source already uses, tagged `Trade.source="rebalance"`.
Crypto is deliberately absent from every tier's target weights (never
actually traded — see Sprint 0). `britney-rebalance.timer` fires this
daily on the droplet (`Type=oneshot`, survives reboots more simply than a
resident process that only needs to wake once a day).

## Sprint 5 — non-obvious stock discovery, classification, and sell mechanisms (2026-08-17 → 2026-08-18)

By far the largest addition since Sprint 0 — four sequential pieces of
work, each building on the last, adding 3 new agent library modules
(`discovery.py`, `classification.py`, `stop_loss.py`), 2 new scheduled
processes (`run_discovery.py`, plus classification/sell logic folded into
the existing `run_rebalance.py`), 3 new models (`DiscoveredCandidate`,
`TickerClassification`, `TickerStreakState`), and a new shared constants
module (`agent_config.py`). Full design investigation lives in
`docs/DISCOVERY_DESIGN.md`.

### 5a. General-news discovery + validation pipeline

`agent/discovery.py` + `agent/run_discovery.py` scan Finnhub's
general-market news (`/news?category=general` — distinct from the
per-ticker `/company-news` `news_ingestion.py` already used; confirmed
live that its `related` field is unreliably empty for general-category
stories, so extraction can never shortcut off it), extract company
mentions via GPT-4o-mini (reusing `sentiment.py`'s exact batching/retry/
mock-fallback pattern, not a new AI pipeline), and hard-validate every
extraction through three gates before it's ever persisted:

- **(a)** the ticker resolves to a real, live `stock_data.quote()` —
  never trust an LLM-guessed ticker string directly.
- **(b)** the quote's real company name fuzzy-matches the LLM's claimed
  company name (a normalized token-overlap check, no new dependency
  added) — catches the LLM naming a real company but guessing the wrong
  ticker for it (a real example hit live: `RR` was guessed for
  "Rolls-Royce Holdings plc" but `RR`'s actual quote is "Richtech
  Robotics Inc." — correctly rejected).
- **(c)** the ticker isn't already one of the fixed 6 target tickers.

Validated candidates land in `discovered_candidates` (ticker, company
name, confidence, source article traceability). Ticker-level dedup
(same-day duplicate mentions across multiple articles) is backed by the
`discovered_candidates` table itself, not a separate cache. Runs hourly
(`britney-discovery.timer`), deliberately decoupled from the 3-min agent
poll — market-wide headlines don't need that freshness, and Yahoo/Finnhub
call volume stays a small fraction of the existing ticker-specific poll's
volume. A real, previously-unknown production bug was caught and fixed
during this work: a shared-session `InsufficientFundsError` from a float-
rounding edge case (see 5c below) could leave an orphaned `Trade` row
with no actual cash/holdings effect — this specific pipeline's own buy
path doesn't hit that bug, but the fix in `rebalance_service.py`
(flooring instead of rounding trade quantities, plus a `db.rollback()`
on the exception) protects every buy path in the app.

### 5b. Obvious/non-obvious classification

`agent/classification.py`, run once daily as part of `run_rebalance.py`'s
existing cycle (not a new process), classifies every candidate — the
fixed 6 tickers plus every distinct ticker discovery has ever validated —
against three gates, all of which must pass for "obvious":

- **Gate A (price growth):** trailing 13-week total return > 0% AND at
  least 60% of week-over-week deltas non-negative — total return alone
  would pass a one-time spike that drifted down afterward; consistency
  alone would pass noisy-flat. Needs both.
- **Gate B (article volume, a brand-recognition proxy):** ≥15 Finnhub
  `/company-news` articles in the trailing 30 days.
- **Gate C (volatility, independent and mandatory):** trailing-13-week
  annualized volatility ≤1.5× VOO's own trailing volatility (recomputed
  fresh every run, not hardcoded), AND no single week moved ±15%. Exists
  specifically so a heavily-covered-but-volatile name (the investigation
  used Tesla/Nvidia as the canonical example) doesn't get miscategorized
  as "safe" just because it clears A and B.

Non-obvious discovered tickers route to exactly one tier's non-obvious
candidate pool by their own volatility ratio relative to VOO
(conservative only accepts the calmest ratios, aggressive accepts the
widest/unbounded range), capped at a per-tier max concurrent count
(conservative 2, moderate 3, aggressive 5 — deliberately different, not
copy-pasted, reasoning in `agent_config.py`), equal-weighted within that
cap. A real, live-caught surprise from this gate set worth remembering:
**BND (the fixed conservative bond ETF) currently fails classification**
— not on volatility or coverage, but on Gate A alone (a real negative
trailing-13-week bond return). Only VOO and SPY currently pass all three
gates among the fixed 6; QQQ, AAPL, MSFT, BND all currently fail.

### 5c. Split wired into rebalance + a real production bug found and fixed

`REBALANCE_TARGET_WEIGHTS` restructured from a flat `{tier: {ticker:
weight}}` into a nested `{tier: {"obvious": {...}, "non_obvious": {}}}`
— the `"obvious"` sub-dict holds each tier's original fixed-ticker
weights, rescaled to fit a smaller envelope; `"non_obvious"` is a static
empty placeholder (populated per-cycle, since membership changes daily).
New `agent_config.SPLIT_RATIOS` set each tier's obvious/non-obvious split
of its invested (non-cash) portion: conservative 90/10, moderate 75/25,
aggressive 55/45 — smallest satellite slice for the most risk-averse
tier, largest for the tier that already carries the app's only crypto
exposure. `effective_target_weights()` builds the real per-cycle flat
targets: a fixed ticker that fails classification is excluded (its
target weight redistributes to the tier's remaining passing tickers,
not left idle); a tier with zero qualifying non-obvious candidates (or,
per 5d below, zero qualifying obvious candidates) leaves that slice of
cash simply un-invested rather than falling back to buying more of the
other bucket.

**Real bug found and fixed during production verification:**
`plan_rebalance()` can clamp a buy to exactly a tier's remaining cash
(routine once a non-obvious buy is often the last/only item competing
for 100% of what's left); the old `round()`-based quantity calculation
could round up a fraction of a cent past that exact cash figure, tripping
`InsufficientFundsError` on a fully-funded buy — and because
`record_trade_fill()` had already flushed the `Trade` insert before that
exception, and `run_rebalance.py` shares one DB session across all 3
portfolios per cycle, the uncaught exception left a `status="filled"`
`Trade` row with zero actual cash/holdings/ledger effect once a later
portfolio's real commit swept it in. Caught live on the droplet (a real
orphaned row, since cleaned up). Fixed by flooring instead of rounding
trade quantities (guarantees cost never exceeds the intended dollar
amount, for every buy) and adding `db.rollback()` on the caught
exception, with two regression tests reproducing the exact real numbers.

### 5d. Debounced classification-based sell + emergency stop-loss

Two mechanisms that **deliberately reverse** the original buy-only rule
— confirmed, intentional overrides, not inconsistencies:

**Debounced sell** — a new `TickerStreakState` table (separate from the
append-only `TickerClassification` history — this one is upserted
current state, not history) tracks each ticker's raw daily pass/fail and
how many consecutive days it's held. A ticker's EFFECTIVE status (the one
that actually drives allocation and selling) only flips after
`agent_config.CLASSIFICATION_DEBOUNCE_DAYS` (3) consecutive days of a new
raw signal — symmetric for losing or regaining obvious status. A
confirmed obvious→non-obvious flip sells the full position via a new
shared `rebalance_service.sell_full_position()` helper, tagged
`source="rebalance"`. Idempotent per calendar day (a manual re-run or a
droplet catch-up run the same day doesn't double-count) and bootstrap-
safe (a ticker's first-ever classification run never itself counts as a
"flip" — it adopts today's raw status immediately with no sell
triggered, so deploying this feature didn't retroactively sell BND/QQQ/
AAPL/MSFT's existing positions just because they were already failing).

**Emergency stop-loss** (`agent/stop_loss.py`) — an independent, faster
check that bypasses the 3-day debounce entirely: checks EVERY currently-
held position (obvious, non-obvious, AND crypto) each cycle. Stocks
trigger on a -15% single-day move OR a -20% cumulative drop from the
trailing 10-day high, whichever hits first; crypto uses wider -30%/-35%
bands so routine crypto volatility doesn't false-trigger a stock-
calibrated threshold. Runs from BOTH `run_rebalance.py`'s daily cycle AND
every 3 minutes from `run_agent.py` — an "emergency" mechanism that only
checked once a day would defeat its own purpose (a position cratering at
11am shouldn't wait until tomorrow's 10am cycle). Cheap to add to the
3-min loop despite the cadence: `stock_data.history()`'s existing 5-min
cache means the real Yahoo network call happens at most once per ~5
minutes per ticker regardless of check frequency. Triggered sells are
tagged `source="stop_loss"` (distinct from a classification sell's
`source="rebalance"`), and freed cash deliberately does NOT get
reinvested the same cycle — a stop-loss is a "get out now" signal, not a
"here's where to reinvest" one; the next normal rebalance cycle decides
where it goes. Every check is logged even when nothing triggers, so
there's a real audit trail proving the sweep ran. As of this writing, no
real stop-loss has ever fired in production (nothing has crashed) — the
mechanism is verified via 12 constructed tests plus a real, live-restart-
confirmed audit trail of checks that correctly found no trigger.

**Deploy gotcha found while verifying this specific piece:** after
pushing the code that adds the stop-loss sweep to `run_agent.py`'s 3-min
loop, a `git pull` on the droplet alone was not enough —
`britney-agent.service` had been running continuously since before the
deploy and kept executing the old in-memory code until explicitly
restarted. See the Architecture section's deploy gotcha above.

### 5e. Global obvious pool

The final piece: a discovered ticker that itself earns confirmed obvious
status (same 3-day debounce as everything else) is no longer wasted —
it's folded into a GLOBAL obvious pool shared across **all 3 tiers**
(unlike an ordinary non-obvious candidate, which stays volatility-routed
to exactly one tier), on the reasoning that clearing Gate C's strict
volatility bar already means "calm enough for any risk level."
`route_non_obvious()` was changed to check the debounced effective status
(not the raw daily signal) so a ticker mid-debounce toward obvious status
doesn't briefly vanish from non-obvious routing before it's actually
confirmed — which also means the moment a ticker IS confirmed, its old
non-obvious cap slot for whatever tier it used to occupy is automatically
freed for the next-best candidate, with no special-casing needed.
`effective_target_weights()` gained a `global_obvious_discovered`
parameter: each tier's full obvious pool becomes (its own passing fixed
tickers) ∪ (the global pool), redistributed proportionally by a nominal
weight — a fixed ticker keeps its real static weight, a newcomer gets
that tier's average fixed weight as a fair, non-arbitrary starting point
(this was a deliberate design call specifically to make the change
exactly backward-compatible when nothing has qualified yet, which is
today's real state — verified live, zero discovered tickers currently
qualify). **A real ordering bug was caught by the test suite before
deploy here too:** an early version of the redistribution logic iterated
a Python `set` to build target weights, which has non-deterministic
insertion order — occasionally clamping the wrong ticker's buy first when
cash was tight. Fixed by preserving a stable, deterministic order.

As of this writing, real production state: **zero discovered tickers
currently qualify for the global obvious pool.** The closest is UNP
(Union Pacific), passing Gate A (+12.80% return, 77% positive weeks) and
Gate B (116 articles) but failing Gate C alone (volatility ratio 1.94×
vs. the 1.5× ceiling).

**Update from real production logs pulled 2026-08-22 (see "AI model
performance report" below for the full picture):** the above two
paragraphs describe the state *as documented on 2026-08-18*. Four days of
real droplet activity later, the picture has moved on non-trivially —
the discovered-candidate universe has grown from single digits to at
least 20 distinct tickers now classified daily alongside the fixed 6,
non-obvious slots are actively filled (moderate 11 candidates routed,
aggressive 9, as of the 2026-08-21 10am ET cycle), and real non-obvious
buys have executed (NOK, HIMS on 2026-08-21). UNP itself still has not
crossed into the global obvious pool. This doc's Sprint 5 narrative above
is left as originally written (it's an accurate record of what shipped
and why); treat the numbers in it as a snapshot, not current state — the
performance report below is the current snapshot.

## Sprint 6 — chat activity grounding + display timezone (in progress, uncommitted as of 2026-08-22)

Two independent, unrelated features, both currently sitting as local
changes only (`git status` shows them modified/untracked, no commit yet)
— grouped here as "Sprint 6" for narrative continuity, not because they
share any code.

### 6a. Chat can now answer "why did I lose money" with real data

**Problem:** `chat_engine.py`'s system prompt only ever gave the model a
static holdings summary — no dates, no trade history, no price moves. A
user asking "why did I lose money on Aug 19" had no real data behind the
answer; the model could only produce a plausible-sounding guess.

**Fix:** `app/services/performance_service.py` (new) —
`portfolio_activity_report(db, portfolio, start_date=None, end_date=None)`
builds a text report for a date window (defaults to the trailing 14
days): every `Trade` filled, every non-trade `CashLedgerEntry` (deposits/
withdrawals), the portfolio's recorded `PortfolioSnapshot` value history
plus its biggest single recorded drop in the window (explicitly
distinguishing "there's a trade at that time" from "pure market move, no
trade"), and for every currently-held symbol, its daily open/close price
move over the window from the same Yahoo daily-candle source
`agent/stop_loss.py` already trusts — so a pure mark-to-market swing with
no trade behind it is still explainable, not just trade-driven ones. If a
portfolio has no snapshot history (true for most user portfolios today —
snapshot capture predates most user accounts), the report says so
explicitly rather than silently omitting that section.

`chat_engine.chat_reply()` is now a real OpenAI tool-calling loop (up to
3 rounds, `gpt-4o-mini`): the system prompt instructs the model to call a
new `get_portfolio_activity` function tool before answering any
performance/date/gain-or-loss question, feeds the tool's text result back
as a `role: tool` message, and lets the model produce its final answer
grounded in that. If the model skips the tool (a plain "what's a good
ETF" question), the first response returns directly with zero extra
round-trips — verified by test, see below. `chat_reply()`'s signature
grew two new required params (`db`, `portfolio`) to support this;
`routes/chat.py` was updated to call `get_or_create_portfolio()` and pass
both through.

**Test coverage** (`tests/test_chat_activity_tool.py`, new, 4 tests, all
passing): two hit `portfolio_activity_report()` directly against a real
DB session (mocked `stock_data.history()` only, no live network) —
confirms trades/price-move sections render correctly, and confirms the
"no snapshot history" and "no trades" empty-state sentences appear
verbatim when there's nothing to report. Two more exercise
`chat_reply()`'s orchestration with a fully mocked OpenAI client (a
scripted 2-response conversation: tool-call, then final answer) —
confirm the tool is actually invoked with the args the model asked for,
its result is fed back as a `tool` message, and the tool is *not* called
at all for a non-performance question (a `pytest.fail()` inside the mock
would catch an unwanted call). None of this exercises real OpenAI
behavior (same reasoning as `test_agent_dedup.py` — a real call would be
billed) — see "AI model performance report" below for why real-traffic
data on this feature doesn't exist yet.

### 6b. User-facing display timezone

**Problem:** every timestamp in the app (trade times, auto-invest last-
run, chart x-axis, article publish dates) was hardcoded to
`DateTime.toLocal()` — the device's own zone, no user choice, and no way
to see "what time did this happen in ET" (the only zone that actually
matters for whether a trade was during/after NYSE hours) without doing
the math by hand.

**Fix:** `User.timezone` (new column, default `"device"` — a sentinel
meaning "render in whatever zone the client device is in," not a real
IANA name) + `PATCH /settings/timezone` (validates against
`zoneinfo.available_timezones()` plus the `"device"` sentinel).
Frontend: `TimezoneController` (new, `frontend/lib/services/`) mirrors
the same "local state first, best-effort PATCH to server" pattern
`ThemeController`/`TimeFormatController`/auto-invest's toggle already
use — persisted locally via `SharedPreferences` (instant, offline-safe),
seeded from `GET /users/me`'s `timezone` field on login so a zone chosen
on one device follows the user to another. New `timezone` package
dependency (`^0.9.4`) for IANA zone conversion; `format.dart` grew
`toDisplayZone()` (UTC instant → chosen zone or device-local) and
`formatEasternSuffix()` (always-ET annotation appended next to a
timestamp when the user's chosen display zone isn't already ET) — wired
into the dashboard chart, stock detail chart, trade history, the
dashboard's queued-orders tile, and the trade rationale sheet's article
timestamps. Account screen grew a `_TimezoneSelector` (a picker bottom
sheet, 9 preset zones with Eastern Time listed right under "Device"
specifically because it's the NYSE's own clock, not for alphabetical/
geographic reasons).

**Not yet covered by an automated test** — this landed as a UI/formatting
feature with no backend logic beyond the validated PATCH endpoint; worth
a quick manual pass (switch zones in Account, confirm trade history/
dashboard/stock-detail timestamps all shift together and the "(... ET)"
suffix appears/disappears correctly) before considering it done, since it
touches five different screens' worth of call sites.

### 6c. Small cleanup alongside

`.gitignore` grew an entry for `backend/deploy/.env.deploy` (a local-only
deploy target file — never meant to be committed, since it would carry
the droplet's real IP; see the `reference_production_droplet` memory).
`docs/IDEAS.txt` had its DATA & BACKEND / TRADING / AGENT sections
pruned of items that shipped in Sprints 2-5 (real performance history,
trade history screen, crypto news wiring, the event-loop-blocking fix) —
those are now changelog entries at the bottom of the file instead of open
items, and the CI note was corrected to reflect that a real (if
unwired-to-CI) test suite now exists.

## AI model performance report (as of 2026-08-22, from real droplet logs)

Everything below comes from `journalctl` on the production droplet
(`britney-agent`, `britney-rebalance`, `britney-discovery` services),
pulled fresh for this doc pass — not from test output or code reading.
The FastAPI web app and Flutter frontend are still not deployed anywhere
(see Known gaps), so **chat and AI recommendations have zero real user
traffic** — `britney-api`'s logs show no `/chat` or
`/generate-recommendation` requests in the last 30 days. Everything
real-world below is the autonomous agent talking to `gpt-4o-mini`
unattended.

**Reliability — good.** Every sampled OpenAI call in the last 48 hours
(sentiment scoring, discovery's company extraction) returned
`HTTP/1.1 200 OK` and logged `real LLM success`; no 429s, no timeouts, no
fallback-to-mock triggers in that window. Sentiment batches ran 1-10
articles at a time depending on real news volume that cycle.

**News-driven sentiment (`decision_loop.py`, every 3 min)** — working as
designed, but real news volume per cycle is thin: most 3-minute cycles
score 0-1 fresh article, occasionally 5-10 when a burst of coverage
lands. No qualitative issue observed; this is a volume/cadence
characteristic, not an accuracy one — the confidence-gated thresholds
that already motivated Sprint 4's rebalance job (idle cash from a
demanding confidence bar) are the more consequential effect.

**Discovery extraction (`discovery.py`, hourly, general-market news) —
mixed, with one concrete accuracy gap worth fixing.** Sampled ~90 hourly
cycles (2026-08-18 through 2026-08-22): the overwhelming majority of
cycles extract "no company" from a 1-3 headline sample (expected — most
general-market headlines aren't about a single public company). Of the
cycles that *did* extract a candidate:
- **6 validated** and persisted cleanly across the sample window.
- **Several correct rejections** — the validation gates caught real LLM
  mistakes exactly as designed: `PARA` claimed for "Paramount Global" but
  `PARA`'s real quote resolves to "Banzai International, Inc." (wrong-
  ticker guess, correctly caught by the company-name fuzzy match);
  `ADANIGREEN`, `HYMTF`, `LHA`, `CITA` all failed gate (a) — no live
  quote at all, so not a real/tradable ticker; `AAPL` was correctly
  rejected by gate (c) for already being a fixed target ticker, not a
  discovery candidate.
- **A real, previously-undocumented false-rejection pattern**: `JPM`
  rejected twice (2026-08-21, 2026-08-22) because the LLM said "JPMorgan
  Chase & Co." but Yahoo's quote calls it "JP Morgan Chase & Co." (a
  spacing difference only); `XOM` rejected once for "Exxon Mobil
  Corporation" vs. Yahoo's "ExxonMobil Holdings Corporation"; `SIEGY`
  rejected for "Siemens AG" vs. Yahoo's "Siemens Aktiengesellschaft" (AG
  *is* the standard abbreviation for Aktiengesellschaft — this one's the
  LLM being *more* correct than the string match). These are all real,
  liquid, correctly-identified companies that the fuzzy-match gate (b)
  is currently rejecting on trivial legal-name-formatting differences,
  not on any actual ticker-guessing error. **This is a real, live
  accuracy gap** in gate (b)'s normalized token-overlap check — worth
  tightening (e.g. stripping/expanding common corporate suffixes) before
  it silently costs the pipeline legitimate large-cap candidates like
  JPMorgan and Exxon indefinitely. Not fixed as part of this doc pass —
  flagged here for the next sprint.
- `2222.SR` (Saudi Aramco) was rejected on the same gate — arguably a
  correct outcome in practice (Yahoo's `.SR` listing isn't going to be
  tradable through this app's pipeline regardless), but the same
  underlying strictness issue.

**Classification (`classification.py`, daily 10am ET, real gate math) —
working, and now materially shaping real portfolios.** The 2026-08-21
10am ET run classified 26 tickers (fixed 6 + 20 currently-tracked
discovered candidates: AAL, BA, BABA, BC, DELL, DIS, GS, HIMS, INTC, LHX,
LLY, MRNA, MU, NOK, NVDA, NVO, SNDK, TGT, UNP, XOM). Real gate outcomes
worth knowing:
- Only **VOO and SPY** passed all three gates that day. Every other
  ticker examined — including 4 of the app's own fixed 6 (**MSFT, AAPL,
  QQQ, BND**) — failed at least one gate, mostly Gate C (volatility):
  e.g. NVDA at 2.84× VOO's volatility, QQQ at 1.70× (just over the 1.5×
  ceiling), MSFT failing Gate A outright (43% positive weeks, needs 60%).
- **Real operational consequence**: the aggressive tier's own fixed
  tickers, AAPL and QQQ, were *both* mid-debounce toward non-obvious that
  day (3-day-consecutive-fail streak not yet confirmed either way), which
  froze new buys into either — logged live as `WARNING portfolio=3
  tier=aggressive: ZERO obvious candidates pass this cycle — 53.9% of
  invested target is unbuyable and will sit as cash this cycle`. This is
  exactly the "confirmed obvious/non-obvious status can starve a tier of
  buyable targets" scenario the debounce mechanism was designed to
  handle gracefully (cash sits idle rather than a bad forced buy) — and
  it's now been observed for real, not just in a constructed test.
- Non-obvious routing is genuinely populated now: 11 candidates routed to
  moderate, 9 to aggressive, 0 to conservative (matches conservative's
  narrower volatility-ratio acceptance band by design). Real buys landed
  the same cycle: conservative bought more VOO, moderate bought more SPY,
  **aggressive bought real non-obvious positions — 27.95 NOK @ $10.30 and
  13.51 HIMS @ $32.79** — the discovery→classification→rebalance pipeline
  closing the loop end-to-end in production, not just in tests.
- The global obvious pool remains empty in every sampled run — UNP is
  still the closest miss (passes Gates A/B, fails C at ~1.9× vs. 1.5×).

**Emergency stop-loss (`stop_loss.py`, every 3 min + daily) — no real
trigger yet, but real near-misses being correctly evaluated.** BABA
(a real aggressive-tier holding, presumably picked up via the
non-obvious pipeline before this doc's last full pass) logged a -8.57%
single-day move and a -9.98%-from-10-day-high move on 2026-08-22 —
meaningfully volatile, and correctly left untouched since both are still
short of the -15%/-20% thresholds. This is the mechanism actually doing
real work (evaluating a real live drawdown against real thresholds and
correctly holding), not merely idling — worth watching BABA specifically
if this doc gets revisited again.

**Bottom line:** the AI-driven pieces of the trading agent (sentiment,
discovery extraction, classification, routing, stop-loss evaluation) are
all functioning reliably in production with no availability problems.
The one concrete quality issue found — discovery's company-name
fuzzy-match gate being too strict on legal-name formatting, silently
costing legitimate large-cap candidates (JPM, XOM, SIEGY-style names) —
was fixed the same doc pass (2026-08-22, uncommitted, see below) —
everything else observed (extraction volume, classification gate
outcomes, routing, stop-loss evaluation) is behaving as designed,
including in edge cases (the aggressive-tier cash-starvation cycle) that
hadn't previously been observed outside of constructed tests. Chat and
recommendations remain entirely unvalidated against real usage since
neither is deployed to real users yet.

**Fixed same-day (2026-08-22, uncommitted):** `agent/discovery.py`'s
`company_name_matches()` now also checks the two names with every token
squashed together (no spaces) before falling back to token-set overlap —
catches a compound name written as one word by one source and multiple
words by the other, exactly the JPM ("JPMorgan" vs. "JP Morgan") and XOM
("ExxonMobil" vs. "Exxon Mobil") cases above. Guarded with a 4-character
minimum on the substring-containment branch so two short, unrelated
squashed names can't coincidentally match. Two new parametrized cases
added to `tests/test_discovery.py::test_company_name_matches` reproducing
the real JPM/XOM pairs; full suite re-run clean (185 passed). The SIEGY
case ("Siemens AG" vs. "Siemens Aktiengesellschaft") is **not** fixed by
this — that's a corporate-abbreviation-equivalence problem (AG ==
Aktiengesellschaft), a different and much narrower class of gap than
word-splitting, not addressed here.

## Known gaps / not yet built

*(Closed since the last full doc pass: cash as a single float [now a
real ledger], no recurring/auto-invest execution, buy-only-forever with
no forced-sell mechanism at all [two deliberate, narrowly-scoped
exceptions now exist — see Sprint 5], stock/ETF discovery limited to a
fixed 6-ticker list.)*

- ~~Discovery's company-name fuzzy-match gate (gate b) is too strict~~ —
  found live 2026-08-22, **fixed the same day** (uncommitted): real,
  correctly-identified large-cap companies (JPMorgan Chase, ExxonMobil)
  were being rejected purely on word-splitting differences between the
  LLM's phrasing and Yahoo's exact quote name ("JPMorgan" vs. "JP
  Morgan", "ExxonMobil" vs. "Exxon Mobil"). `company_name_matches()` now
  also compares both names with all tokens squashed together (no spaces)
  before falling back to token overlap — see "AI model performance
  report" above for the fix details and test coverage. **Still open:**
  the SIEGY case ("Siemens AG" vs. "Siemens Aktiengesellschaft") is a
  different problem — corporate-abbreviation equivalence, not word-
  splitting — and remains unaddressed.
- **CryptoPanic real-API verification** — crypto news ingestion is built
  and verified in mock mode only; still never run against a real
  `CRYPTOPANIC_API_KEY`. In practice this also means crypto has never
  organically traded in production — only a forced manual test has ever
  exercised that code path.
- **Sprint 6 (chat activity grounding + display timezone) is uncommitted**
  — both features are implemented and backend-tested (chat) or manually
  reviewable (timezone), sitting as local working-tree changes only as of
  this doc pass. The timezone feature specifically has no automated test
  yet — see Sprint 6b above for the manual pass worth doing before commit.
- **The global obvious pool and the emergency stop-loss are both real,
  deployed, and tested — but neither has yet been exercised by a real
  production trigger.** Zero discovered tickers currently pass all 3
  classification gates (closest is UNP, failing only on volatility); no
  stock/crypto position has dropped sharply enough to fire the stop-loss
  yet. Both are verified via constructed tests plus a real, live audit
  trail showing the checks genuinely run — worth revisiting real
  behavior once either actually fires for the first time.
- **No cap on the global obvious pool's size** — deliberate (not
  implemented, not forgotten), since nothing has qualified yet to judge
  whether dilution across a large pool is a real concern in practice.
- **Discovered candidate universe is still small** (single digits of
  tickers as of this writing) — the per-tier `MAX_CONCURRENT_NON_OBVIOUS`
  caps (2/3/5) have real routing pressure but haven't yet had a real
  cycle where more candidates were routed to a tier than its cap allows.
- **No stock options support** — never existed.
- **No Alpaca live/paper order routing yet** — `execution.py` has a
  placeholder; everything is simulated locally today.
- **A never-loaded portfolio can still be traded against without demo
  seed holdings** — `record_trade_fill` doesn't call the same
  seed/initialize path `build_dashboard` does. Still open.
- **No Alembic migrations** — `bootstrap_schema()` covers new/added
  columns automatically but is additive-only (no renames/type changes/
  drops).
- **No CI, no lint pipeline** — the 173-test pytest suite passes locally
  and on the droplet but nothing runs it automatically on push/PR.
- **FastAPI web app + Flutter frontend are not deployed anywhere** — only
  the trading agent (now 4 scheduled processes) is.
- **Mobile builds** — android/ios folders aren't generated yet, flutter
  web only in practice.
- See `docs/IDEAS.txt` for the fuller running list, `docs/TECH_STACK.md`
  for free-tier-now vs. paid-at-scale on every external dependency, and
  `docs/DISCOVERY_DESIGN.md` for the full design investigation behind
  everything in Sprint 5.

## Where to look next

- `docs/IDEAS.txt` — the fullest, most current backlog; organized by
  DATA & BACKEND / TRADING / FRONTEND-UX / AI / INFRA / MONETIZATION.
- `docs/TECH_STACK.md` — free-tier-now vs. paid-at-scale for every
  external dependency.
- `docs/DEPLOYMENT.md` — how the trading agent's droplet is provisioned
  and operated.
- `docs/DISCOVERY_DESIGN.md` — the design investigation behind the
  entire non-obvious discovery/classification/sell-mechanism feature
  family (Sprint 5).
