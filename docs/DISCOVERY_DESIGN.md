# Non-Obvious Stock Discovery — Design Investigation

Status: **investigation only** — no trading/execution code changed in this pass.
Date: 2026-08-17

Goal under investigation: let the trading agent discover "non-obvious" stocks from
general news (not just the six fixed tickers in `TARGET_PORTFOLIOS`), split each
risk tier's holdings between a fixed **obvious** bucket and a dynamically-discovered
**non-obvious** bucket, with the split ratio varying by tier.

## Go / no-go

**Conditional go**, on a *bounded* version of the idea, not literally "scan all news
everywhere."

- **Cost is not the constraint.** The added OpenAI (gpt-4o-mini) spend for
  extraction + sentiment on discovered candidates comes out to roughly **$0.02–0.05/day
  (~$0.60–1.50/month)** — see §4. That's noise against the existing $20/mo cap.
- **The real constraints are reliability, not budget**: (a) an LLM extracting a
  ticker from free text can hallucinate or mismatch, and (b) the free path for
  validating a ticker (`stock_data.py`'s Yahoo-based `quote()`) is an unofficial,
  undocumented endpoint the codebase's own comments already flag as able to
  rate-limit or block without notice.
- Recommendation: build a **bounded candidate pipeline** — Finnhub's general-news
  category (already in budget, zero new vendor) → GPT-4o-mini extraction (reusing
  `sentiment.py`'s pattern) → **hard** Yahoo-quote validation gate before anything
  is trade-eligible → daily obvious/non-obvious reclassification riding on the
  existing rebalance job. This is "go." True unbounded, provider-agnostic discovery
  ("all news everywhere") is a "not yet" — it's a scaling exercise once this
  pipeline's false-positive/failure rate is measured against live traffic, not a
  starting point.

---

## 1. General news source

Finnhub does have a general/market-wide news endpoint, separate from
`/company-news` (which `fetch_news()` already calls per-ticker):

```
GET https://finnhub.io/api/v1/news?category=general&token=${FINNHUB_API_KEY}
```

- `category` ∈ `general | forex | crypto | merger` (`general` is the default).
- `minId` supports pagination / "only give me articles newer than X."
- Response is an array of objects: `category`, `datetime` (unix), `headline`,
  `id`, `image`, `related`, `source`, `summary`, `url` — the same shape family
  `_normalize()` in `news_ingestion.py` already parses (`headline`/`summary`/
  `source`/`url`/`datetime`), so it slots into the existing `NewsArticle`
  dataclass with no new parsing logic.
- It's headline + short summary, **not full article bodies** — same as
  `/company-news` today, so no change to how much text sentiment.py/an
  extraction step gets to work with.
- **Caveat worth flagging**: for `/company-news`, the `related` field reliably
  echoes back the ticker you queried. For `/news?category=general`, `related` is
  reported as populated when a story is genuinely tied to specific tickers, but
  general macro/market stories often carry it empty — meaning Finnhub does **not**
  reliably hand us pre-tagged tickers for general news. This needs a five-minute
  smoke test against a live key before implementation starts (the docs page
  itself is a JS app that didn't yield a fetchable example during this
  investigation) — but plan on doing our own extraction rather than trusting
  `related` to already contain the answer.
- Free tier: 60 calls/min (same tier we already use for `/company-news`), so this
  costs **zero new signup, zero new key, zero new budget line** — just more calls
  against the same `FINNHUB_API_KEY`.

**Next-best alternative, if `category=general` proves too thin or too
mega-cap-skewed once tested live:** Alpha Vantage's `NEWS_SENTIMENT` (free tier
is 25 requests/day — too low for anything but a once-daily batch, would force a
much slower discovery cadence) or NewsAPI.org (free "developer" tier delays
articles ~24h, bad for a near-real-time pipeline). Neither is a drop-in win over
Finnhub's own general category, so the recommendation is: **start with Finnhub,
re-evaluate only if live testing shows it's insufficient.**

---

## 2. Company/ticker extraction from free text

Reuse `agent/sentiment.py`'s pattern wholesale rather than a new pipeline: same
`gpt-4o-mini` model, same `OpenAI(max_retries=0)` + `run_in_threadpool` + 429
backoff + mock-fallback shape, same `DEFAULT_BATCH_SIZE=10` batching. Concretely,
a new `agent/discovery.py` module with an `extract_companies_batch()` function
shaped exactly like `score_batch()`, prompting for, per article:

```
[{"ticker_guess": "AAPL" | null, "company_name": "Apple Inc." | null, "confidence": 0.0-1.0}, ...]
```

### Hard requirement: validate before eligible

This is a hard requirement, not a nice-to-have: **`ticker_guess` is never trusted
directly.** Before a candidate is eligible for anything downstream (sentiment
scoring, bucket placement, `execute_trade()`), it must pass:

1. `stock_data.quote(ticker_guess)` (the Yahoo-backed quote function
   `app/services/stock_data.py` already exposes to `app/routes/stocks.py`) returns
   a non-`None` result. `quote()` already distinguishes "confirmed not found" from
   "fetch failed" in its logging — reuse that distinction so a transient Yahoo
   outage doesn't get misread as "not a real ticker."
2. The `company_name` on that quote result fuzzy-matches the LLM's claimed
   `company_name` (e.g. token-overlap or a cheap string-similarity check). This
   catches the sharper failure mode: the LLM naming a real company but guessing
   the *wrong* ticker for it (ticker-class confusion, a defunct/delisted symbol
   being reused, or confusing two similarly-named companies) — a case where
   `quote()` alone would happily return a valid quote for the *wrong* company.
3. The resolved ticker isn't already one of the six fixed `TARGET_PORTFOLIOS`
   tickers — a "discovery" that rediscovers VOO isn't discovery.

Only a candidate that clears all three becomes eligible for the non-obvious
candidate pool. An LLM hallucinating or mismatching a ticker that reaches
`execute_trade()` unvalidated would be a real correctness bug in a trading
system, not cosmetic noise — this gate is the fix for that, and it should be
treated as non-negotiable in any implementation of this design.

---

## 3. "Obvious" classification — a concrete, computable definition

### Reusable inputs

`app/services/stock_data.py`'s `history()` already pulls OHLCV series from
Yahoo's chart endpoint for the dashboard/stock-detail chart — it's reusable
as-is for a growth check, with one addition: `RANGE_TO_YAHOO` currently only
maps `1D/1W/30D/YTD/5Y` to Yahoo range/interval pairs; a trailing-3-month
check needs a new internal-only entry (e.g. `"3M": ("3mo", "1wk")`) — Yahoo's
chart endpoint already accepts `range=3mo` natively, this is just wiring, not
a new integration.

### Gate A — price growth (consistency, not just direction)

Using ~13 weekly closes over the trailing 3 months:

- **Total return > 0%** over the window, **and**
- **At least 60% of week-over-week deltas are non-negative** (≥8 of 13 weeks
  flat-or-up).

Reasoning: total return alone would pass a stock that spiked once on an
earnings surprise and drifted down the rest of the quarter — not what
"consistent growth" means. The weekly-positive-fraction alone would pass a
stock that's essentially flat with noise near zero. Requiring both catches
"actually, steadily growing" and rejects "spiky" or "flat."

### Gate B — article volume as a brand-recognition proxy

Finnhub has no dedicated "article count" endpoint; the proxy is: count of
`/company-news` articles for that ticker over a trailing 30-day window (an
extended `days_back` call to the existing `fetch_news()`, not a new provider
call type). Proposed threshold: **≥15 articles in 30 days** (roughly one
every two days). This needs calibration against real mega-caps once the
general-news pipeline is live — AAPL/MSFT should clear it comfortably; a
genuinely obscure micro-cap should produce close to zero.

### Gate C — volatility (mandatory, separate from A and B)

**Flag, explicitly:** article volume measures "how much is written about this
stock," not "how safe it is." Tesla and Nvidia are exactly the counter-example —
heavily covered *and* highly volatile. Gates A and B alone would let a
heavily-covered, volatile stock get miscategorized as "obvious/safe." A third,
independent gate is required:

- Reject from "obvious" if trailing-3-month realized volatility (e.g. stdev of
  weekly returns, annualized) exceeds **1.5× VOO's own trailing volatility**
  over the same window, **or** any single week's move exceeds **±15%**.

"Obvious" = Gate A **and** Gate B **and** Gate C, all three. Failing any one
disqualifies a ticker from "obvious" — it either lands in "non-obvious" (if it
also came through the discovery/validation path in §2) or is excluded from both
buckets entirely.

### Where and how often this runs

Recompute once daily, riding on the existing `agent/run_rebalance.py` job
(fixed 10:00am ET, `agent/market_hours.py`'s `next_rebalance_time()`) rather
than a new standalone process. A trailing-3-month signal doesn't meaningfully
change inside a 3-minute poll window, so there's no reason to compute it on
`run_agent.py`'s cadence — reusing the already-scheduled daily job avoids
standing up a fourth scheduled process alongside `run_agent`, `run_rebalance`,
and `run_auto_invest`.

---

## 4. Cost / reliability estimate

Baseline today (per `run_agent.py`'s own comments): 6 unique Finnhub
`/company-news` calls per full 3-portfolio sweep, at the 3-minute cadence —
~2,880 calls/day, well under Finnhub's 60/min free-tier ceiling.

**Added load from general-news discovery:**

| Step | Estimate | Basis |
|---|---|---|
| Finnhub `/news?category=general` calls | ~96/day | Proposed on its **own** slower cadence (hourly-ish, see below), not the 3-min ticker poll |
| New (unseen) general articles/day | ~150–400 | US financial-media headline volume aggregated by Finnhub; needs live calibration |
| Extraction calls (gpt-4o-mini, batch of 10) | ~15–40/day | 150–400 articles ÷ 10 per batch |
| Yahoo `quote()` validation calls | ~50–150/day | Free, no OpenAI cost; already has a 60s TTL cache |
| Extra sentiment-scoring calls for validated candidates | ~2–5/day | Small batches of newly-validated tickers only |

**OpenAI cost** (gpt-4o-mini: $0.15/1M input tokens, $0.60/1M output tokens):
~40 extraction calls/day × ~1,200 input + ~400 output tokens ≈ 48k input +
16k output tokens/day ≈ **$0.017/day (~$0.51/month)**. Add sentiment scoring
for new candidates and the total lands around **$0.02–0.05/day, ~$0.60–1.50/month**
— even at a generous 5–10x miscalibration this stays a rounding error against
the existing $20/mo cap. **OpenAI cost is not the thing to worry about here.**

**What actually needs care:** Yahoo's unofficial quote endpoint. It already
carries no documented rate limit and the codebase's own comments flag it as
able to change or block without notice. Adding ~50–150 validation calls/day is
roughly a 10–15% bump on top of existing Yahoo call volume from ticker pricing
— not huge, but it's the more fragile dependency, not OpenAI billing.

**On poll cadence:** no need to slow the existing 3-minute ticker-specific
poll — that one isn't the source of new load. Instead, run the *new*
general-news discovery sweep on its own decoupled, slower cadence (e.g.
hourly, or piggybacked onto the once-daily rebalance cycle) since market-wide
headlines don't need 3-minute freshness the way a ticker-specific reaction
does. This caps the added Finnhub/Yahoo/OpenAI load naturally without
touching the cadence that already works.

---

## 5. Split mechanism

`app/services/rebalance_service.py`'s `REBALANCE_TARGET_WEIGHTS` is currently a
flat `{tier: {ticker: weight}}` dict, with the leftover being cash
(conservative 5%, moderate 3%, aggressive 2%). Proposed restructuring nests
inside the existing shape rather than replacing it:

```python
REBALANCE_TARGET_WEIGHTS = {
    "conservative": {
        "obvious": {"VOO": 0.54, "BND": 0.315},      # 90% of the 95% invested envelope
        "non_obvious": {...daily-discovered...},      # 10% of the 95% invested envelope
    },
    ...
}
```

Cash targets are unchanged (5% / 3% / 2%); what used to be one flat "invested"
bucket per tier now subdivides into obvious/non-obvious *within* that same
invested envelope. `plan_rebalance()`'s shortfall/tolerance/`MIN_TRADE_DOLLARS`
logic applies per-bucket, unchanged in spirit.

### Recommended ratios (of the invested, non-cash portion)

| Tier | Obvious | Non-obvious | Reasoning |
|---|---|---|---|
| Conservative | 90% | 10% | Already the lowest-risk tier (60/35/5 cash-heavy target); a discovered small/micro-cap should only ever be a small satellite position here, matching its existing posture rather than fighting it. |
| Moderate | 75% | 25% | A meaningful but clearly secondary discovery sleeve — enough to matter, not enough to define the portfolio's risk character. |
| Aggressive | 55% | 45% | Closest to "half the portfolio is a research bet," consistent with this tier already being the one carrying crypto exposure (SOL) — it's already the tier explicitly positioned for higher-variance bets. |

Within the non-obvious sub-bucket itself: propose **equal-weighting across
that day's validated candidates, capped at some max concurrent non-obvious
positions per tier (e.g. 3)**, rather than picking a single winner — this
diversifies discovery risk instead of concentrating a tier's "bet" on
whichever single headline the LLM happened to extract most confidently that
day.

---

## 6. Risk / edge cases to flag

**Zero qualifying non-obvious stocks on a thin news day.** Recommend: that
bucket's target cash simply stays un-invested for the cycle — same
buy-only/tolerance-gated philosophy `plan_rebalance()` already applies to the
obvious bucket via `MIN_TRADE_DOLLARS`. Do **not** silently fall back to
buying more of the obvious bucket with that cash — that would quietly erode
the tier's stated obvious/non-obvious split without it being a deliberate
rebalance decision. Worth a UI/reporting note too: showing "0% non-obvious
today, holding cash" is more honest than a chart that silently drifts back
toward the obvious portfolio over time.

**Duplicate discovery.** `SeenArticleStore` dedupes at the *article* level
(`article_id` → seen), not the *company* level — five different articles
about the same company on the same day would currently each independently
extract and re-validate that company. This needs a second, ticker-level dedup
layer: track "already discovered this cycle" per ticker (a new small table or
an extension of `SeenArticleStore`'s schema), separate from "already seen"
per article, so a heavily-covered same-day story doesn't get evaluated — or
worse, bought — multiple times in one cycle.

**Regulatory/compliance note (flagging only, not resolving).** Trading
LLM-"discovered" small/micro-cap names, even in paper trading, is worth a
compliance/legal look given this is a pitch-facing product — surfacing this
explicitly per the investigation's scope, not attempting to resolve it here.

---

## Summary of open engineering questions for a follow-up implementation pass

1. Live-verify Finnhub's `/news?category=general` `related`-field behavior
   and realistic daily article volume against a real key before committing to
   the cost estimates in §4.
2. Decide the ticker-level dedup mechanism's exact storage shape (extend
   `SeenArticleStore` vs. a new table).
3. Decide the exact fuzzy-match method/threshold for §2's company-name
   cross-check.
4. Calibrate Gate B's 15-articles/30-days threshold and Gate C's 1.5x-VOO
   volatility threshold against real historical data before trusting them.
5. Decide the non-obvious sub-bucket's max-concurrent-positions cap per tier.
