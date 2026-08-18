"""Obvious/non-obvious classification for the trading agent's candidate
universe — see docs/DISCOVERY_DESIGN.md §3. Run once daily as part of
agent/run_rebalance.py's existing 10am ET cycle (not a new standalone
process/timer) so classification always reflects the same morning's
settled cash/holdings state that cycle already establishes.

Candidate pool = the 6 fixed TARGET_PORTFOLIOS stock/ETF tickers
(news_ingestion.target_stock_tickers()) UNION every distinct ticker ever
validated by agent/discovery.py into discovered_candidates
(app.services.discovery_service.distinct_discovered_tickers()). Nothing
outside that pool is ever classified — a ticker nobody discovered and
that isn't one of the fixed 6 was never eligible for anything in the
first place (see agent/discovery.py's hard validation gates a/b/c for
why "eligible" already means "real, tradable, and either fixed or
discovery-validated" before classification ever runs).

Re-classifies EVERY pool member on EVERY run (not just tickers unclassified
"today") rather than incrementally: the whole point of running this daily
is to let classification drift as trailing-window data changes, and gate
inputs (13-week price history, 30-day article counts) are different every
day regardless of whether a given ticker was classified yesterday. The
pool is small (single/low-double digits of tickers), so re-classifying
everyone daily is cheap — a handful of cached-friendly stock_data.history()
calls plus one Finnhub company-news call per ticker — and avoids a
"discovered on day 1, silently never reclassified again" staleness bug an
incremental approach would risk.

A discovered ticker that CONFIRMS obvious status (3 consecutive passing
days, same debounce as every other status transition — see
update_streaks()) stops being a route_non_obvious() candidate and
instead joins global_obvious_pool() — a ticker set folded into EVERY
tier's obvious bucket (not volatility-routed to one tier like an
ordinary non-obvious candidate), since clearing Gate C's strict
volatility bar already means it's calm enough for any risk level. See
app.services.rebalance_service.effective_target_weights() for how that
pool actually changes each tier's target weights.

Does NOT touch agent/discovery.py's validation gates (a/b/c) or
decision_loop.py's news-driven trading — those are explicitly out of
scope for this module.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from sqlalchemy.orm import Session

from agent import agent_config, news_ingestion
from app.config import get_settings
from app.models import TickerClassification, TickerStreakState
from app.services import discovery_service, stock_data

logger = logging.getLogger(__name__)

TIERS = ("conservative", "moderate", "aggressive")


@dataclass
class ClassificationResult:
    ticker: str
    is_obvious: bool
    gate_a_passed: bool
    gate_a_detail: str
    gate_b_passed: bool
    gate_b_detail: str
    gate_c_passed: bool
    gate_c_detail: str
    volatility: float | None
    voo_volatility: float | None
    vol_ratio: float | None  # volatility / voo_volatility, None if either is unavailable


@dataclass
class RoutedCandidate:
    ticker: str
    tier: str
    vol_ratio: float
    confidence: float


@dataclass
class StreakResult:
    ticker: str
    raw_status: bool
    consecutive_days: int
    effective_status: bool
    previous_effective_status: bool
    flipped: bool  # effective_status != previous_effective_status, as of THIS run


async def _weekly_closes(ticker: str) -> tuple[list[float] | None, str]:
    """Trailing ~GROWTH_LOOKBACK_WEEKS weekly closes via stock_data.history()
    (reused as-is, not reinvented — see stock_data.RANGE_TO_YAHOO's "13W"
    entry). Returns (None, reason) if Yahoo returned too few candles to
    compute anything meaningful from (recent IPO, data gap, live outage)."""
    candles = await stock_data.history(ticker, "13W")
    closes = [c["close"] for c in candles]
    if len(closes) < agent_config.GROWTH_MIN_WEEKLY_CANDLES:
        return None, (
            f"only {len(closes)} weekly candle(s) available, need >= "
            f"{agent_config.GROWTH_MIN_WEEKLY_CANDLES}"
        )
    return closes, ""


def _weekly_returns(closes: list[float]) -> list[float]:
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]


def _annualized_volatility(returns: list[float]) -> float:
    """Sample stdev of weekly returns, annualized via sqrt(52) — standard
    return-series volatility estimation. 0.0 for fewer than 2 returns
    (can't compute a sample stdev), which naturally fails Gate C for
    anything with that little data (see _gate_c's docstring)."""
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns) * math.sqrt(52)


def _gate_a(closes: list[float]) -> tuple[bool, str]:
    total_return = (closes[-1] - closes[0]) / closes[0] if closes[0] else 0.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    positive_frac = sum(1 for d in deltas if d >= 0) / len(deltas) if deltas else 0.0
    passed = total_return > 0 and positive_frac >= agent_config.GROWTH_MIN_POSITIVE_WEEK_PCT
    detail = (
        f"{len(closes)}wk total_return={total_return:+.2%} "
        f"positive_weeks={positive_frac:.0%} "
        f"(need >0% and >={agent_config.GROWTH_MIN_POSITIVE_WEEK_PCT:.0%})"
    )
    return passed, detail


async def _gate_b(
    ticker: str,
    *,
    client: httpx.AsyncClient,
    api_key: str,
    limiter: news_ingestion.RateLimiter,
) -> tuple[bool, str, int]:
    """Article-volume proxy — reuses news_ingestion's real Finnhub call
    (_fetch_ticker_news_finnhub) directly rather than going through
    fetch_news()'s full NewsArticle-normalization/SeenArticleStore-dedup
    path, since classification only needs a COUNT, not article bodies it
    would just discard (per this prompt's explicit instruction). Same
    offline mock-mode convention as the rest of the agent when no
    FINNHUB_API_KEY is set: news_ingestion._mock_articles() always
    returns 2, which is honest (no live data = no evidence of coverage),
    not a synthetic pass."""
    if not api_key:
        count = len(news_ingestion._mock_articles(ticker))
    else:
        raw = await news_ingestion._fetch_ticker_news_finnhub(
            client, ticker, api_key, limiter, agent_config.ARTICLE_VOLUME_LOOKBACK_DAYS
        )
        count = len(raw)
    passed = count >= agent_config.ARTICLE_VOLUME_MIN
    detail = (
        f"{count} article(s) in trailing {agent_config.ARTICLE_VOLUME_LOOKBACK_DAYS}d "
        f"(need >={agent_config.ARTICLE_VOLUME_MIN})"
    )
    return passed, detail, count


def _gate_c(closes: list[float], voo_vol: float | None) -> tuple[bool, str, float, float | None]:
    """Volatility gate — independent of A/B on purpose (see module
    docstring's Tesla/Nvidia framing in docs/DISCOVERY_DESIGN.md §3): a
    heavily-covered, consistently-up stock can still be too volatile to
    call "safe." voo_vol is passed in (computed once per classify_all()
    run, not per-candidate) so every candidate this run is judged against
    the SAME freshly-computed VOO baseline."""
    returns = _weekly_returns(closes)
    volatility = _annualized_volatility(returns)
    max_weekly_move = max((abs(r) for r in returns), default=0.0)

    if voo_vol is None or voo_vol <= 0:
        return False, "VOO's own volatility unavailable this run — cannot compute ratio", volatility, None

    vol_ratio = volatility / voo_vol
    vol_ok = vol_ratio <= agent_config.VOLATILITY_MAX_VS_VOO
    move_ok = max_weekly_move <= agent_config.VOLATILITY_MAX_WEEKLY_MOVE
    passed = vol_ok and move_ok
    detail = (
        f"volatility={volatility:.2%} voo_volatility={voo_vol:.2%} "
        f"ratio={vol_ratio:.2f}x (need <={agent_config.VOLATILITY_MAX_VS_VOO}x) "
        f"max_weekly_move={max_weekly_move:.2%} "
        f"(need <={agent_config.VOLATILITY_MAX_WEEKLY_MOVE:.0%})"
    )
    return passed, detail, volatility, vol_ratio


async def classify_ticker(
    ticker: str,
    *,
    client: httpx.AsyncClient,
    api_key: str,
    limiter: news_ingestion.RateLimiter,
    voo_vol: float | None,
) -> ClassificationResult:
    closes, insufficient_reason = await _weekly_closes(ticker)

    if closes is None:
        # Fails closed: can't compute Gate A or C without price history,
        # so this ticker isn't "obvious" this run — but Gate B can still
        # run independently (it doesn't need price data), for a fuller
        # picture in the persisted row even though the overall verdict
        # is already decided.
        gate_a_passed, gate_a_detail = False, insufficient_reason
        gate_c_passed, gate_c_detail, volatility, vol_ratio = False, insufficient_reason, None, None
    else:
        gate_a_passed, gate_a_detail = _gate_a(closes)
        gate_c_passed, gate_c_detail, volatility, vol_ratio = _gate_c(closes, voo_vol)

    gate_b_passed, gate_b_detail, _count = await _gate_b(
        ticker, client=client, api_key=api_key, limiter=limiter
    )

    is_obvious = gate_a_passed and gate_b_passed and gate_c_passed

    return ClassificationResult(
        ticker=ticker,
        is_obvious=is_obvious,
        gate_a_passed=gate_a_passed,
        gate_a_detail=gate_a_detail,
        gate_b_passed=gate_b_passed,
        gate_b_detail=gate_b_detail,
        gate_c_passed=gate_c_passed,
        gate_c_detail=gate_c_detail,
        volatility=volatility,
        voo_volatility=voo_vol,
        vol_ratio=vol_ratio,
    )


async def classify_all(db: Session) -> dict[str, ClassificationResult]:
    """Classifies every pool member (see module docstring) and persists
    one TickerClassification row per ticker for this run. Returns the
    in-memory results too, so agent/run_rebalance.py's cycle can use them
    immediately without a second DB read racing "which run is 'today's'"
    if this is invoked more than once in a day (e.g. manual verification).

    Computes VOO's own volatility ONCE up front and reuses it for every
    candidate's Gate C (including VOO's own — its ratio against itself is
    trivially ~1.0x unless VOO itself had an outsized single-week move,
    which would be a genuinely notable finding, not a bug, if it ever
    happens). If VOO's own history is unavailable this run (Yahoo outage),
    every candidate's Gate C fails closed rather than raising and losing
    the whole cycle — see _gate_c().
    """
    settings = get_settings()
    fixed_six = set(news_ingestion.target_stock_tickers())
    discovered = discovery_service.distinct_discovered_tickers(db)
    pool = sorted(fixed_six | discovered)

    async with httpx.AsyncClient() as client:
        limiter = news_ingestion.RateLimiter(news_ingestion.DEFAULT_CALLS_PER_MINUTE)

        voo_closes, voo_reason = await _weekly_closes("VOO")
        if voo_closes is None:
            logger.error(
                "VOO's own price history unavailable this run (%s) — every "
                "candidate's Gate C will fail closed this cycle.",
                voo_reason,
            )
            voo_vol: float | None = None
        else:
            voo_vol = _annualized_volatility(_weekly_returns(voo_closes))
            logger.info("VOO trailing-13wk annualized volatility this run: %.2f%%", voo_vol * 100)

        results: dict[str, ClassificationResult] = {}
        for ticker in pool:
            result = await classify_ticker(
                ticker, client=client, api_key=settings.finnhub_api_key,
                limiter=limiter, voo_vol=voo_vol,
            )
            results[ticker] = result
            logger.info(
                "classified %s: obvious=%s | A(%s): %s | B(%s): %s | C(%s): %s",
                ticker, result.is_obvious,
                result.gate_a_passed, result.gate_a_detail,
                result.gate_b_passed, result.gate_b_detail,
                result.gate_c_passed, result.gate_c_detail,
            )
            db.add(
                TickerClassification(
                    ticker=result.ticker,
                    is_obvious=result.is_obvious,
                    gate_a_passed=result.gate_a_passed,
                    gate_a_detail=result.gate_a_detail,
                    gate_b_passed=result.gate_b_passed,
                    gate_b_detail=result.gate_b_detail,
                    gate_c_passed=result.gate_c_passed,
                    gate_c_detail=result.gate_c_detail,
                    volatility=result.volatility,
                    voo_volatility=result.voo_volatility,
                    vol_ratio=result.vol_ratio,
                )
            )
        db.commit()

    return results


def _tier_band_for_vol_ratio(vol_ratio: float) -> str:
    if vol_ratio <= agent_config.NON_OBVIOUS_VOL_BAND_CONSERVATIVE_MAX:
        return "conservative"
    if vol_ratio <= agent_config.NON_OBVIOUS_VOL_BAND_MODERATE_MAX:
        return "moderate"
    return "aggressive"


def route_non_obvious(
    classifications: dict[str, ClassificationResult],
    streaks: dict[str, StreakResult],
    *,
    discovered_tickers: set[str],
    confidence_by_ticker: dict[str, float],
) -> dict[str, list[RoutedCandidate]]:
    """Routes every non-obvious, discovery-sourced ticker into EXACTLY ONE
    tier's candidate pool, by its own volatility relative to VOO's (see
    agent_config.NON_OBVIOUS_VOL_BAND_* for the band cutoffs and their
    reasoning).

    Three scoping decisions, all deliberate:

    1. Fixed-6 tickers are NEVER routed here, even if they classify as
       non-obvious. Classification is computed for them (useful history/
       drift-tracking — see TickerClassification), but a fixed ticker
       failing a gate simply stops receiving new "obvious" bucket buys
       (buy-only, no forced sell — see rebalance_service.py); it does not
       get redirected into a non-obvious bucket, since it was never
       "discovered" in the first place. `discovered_tickers` is what
       enforces this scoping.

    2. Checks the DEBOUNCED effective status (via `streaks`), not the raw
       daily is_obvious — a discovered ticker that just started passing
       today (raw=True, effective still False, mid-debounce toward
       global-obvious-pool entry) stays routable here, since it hasn't
       actually left the non-obvious pool yet as far as allocation is
       concerned. Only once a ticker is CONFIRMED obvious (3 consecutive
       days — see update_streaks()) does it stop being routed here at
       all: agent/run_rebalance.py's cycle then folds it into the global
       obvious pool (rebalance_service.effective_target_weights()'s
       `global_obvious_discovered`) instead, which — being cross-tier,
       not routed to one tier's cap — automatically frees whatever
       non-obvious slot it used to occupy in this function's output.

    3. Exactly one tier, not overlapping adjacent tiers: a ticker's
       volatility ratio is a single point-in-time number, and routing it
       into multiple tiers at once would mean two INDEPENDENT agent
       portfolios (each tier is its own Portfolio row, no shared
       position) could simultaneously try to buy the same speculative
       name in the same cycle with no coordination between them — adding
       complexity (contended-look UX: "why do both my conservative and
       moderate model portfolios suddenly hold the same microcap?") for
       no clear benefit. A clean partition keeps each tier's non-obvious
       bucket interpretable as "names at THIS tier's risk level," not a
       blurry shared set. (The global obvious pool, by contrast, IS
       deliberately shared across every tier — see effective_target_
       weights()'s docstring for why that's the right call there:
       clearing Gate C's strict volatility bar already means "calm enough
       for any risk level," unlike an ordinary non-obvious candidate.)

    Sorted by discovery confidence, descending, within each tier — the
    ranking agent/run_rebalance.py's cycle uses to pick which candidates
    fill that tier's MAX_CONCURRENT_NON_OBVIOUS cap when more are routed
    than the cap allows.
    """
    routed: dict[str, list[RoutedCandidate]] = {tier: [] for tier in TIERS}

    for ticker, result in classifications.items():
        if ticker not in discovered_tickers:
            continue  # fixed-6 tickers are never routed — see docstring
        streak = streaks.get(ticker)
        if streak is not None and streak.effective_status:
            continue  # CONFIRMED obvious -> global obvious pool, not routed here
        if result.vol_ratio is None:
            logger.info(
                "skipping non-obvious routing for %s: no volatility ratio "
                "available this run (insufficient price history)",
                ticker,
            )
            continue

        tier = _tier_band_for_vol_ratio(result.vol_ratio)
        confidence = confidence_by_ticker.get(ticker, 0.0)
        routed[tier].append(
            RoutedCandidate(ticker=ticker, tier=tier, vol_ratio=result.vol_ratio, confidence=confidence)
        )
        logger.info(
            "routed %s -> %s tier (vol_ratio=%.2fx, confidence=%.2f)",
            ticker, tier, result.vol_ratio, confidence,
        )

    for tier_list in routed.values():
        tier_list.sort(key=lambda c: c.confidence, reverse=True)

    return routed


def update_streaks(
    db: Session,
    classifications: dict[str, ClassificationResult],
    *,
    today: date | None = None,
) -> dict[str, StreakResult]:
    """Debounces each ticker's raw daily is_obvious result into an
    EFFECTIVE status that only changes after
    agent_config.CLASSIFICATION_DEBOUNCE_DAYS consecutive days of a new
    raw value — symmetric for losing OR regaining obvious status. See
    app/models/ticker_streak_state.py's docstring for why this is a
    separate upserted-state table, not more columns on the append-only
    TickerClassification history.

    Algorithm per ticker, per day:
      - today's raw == yesterday's stored raw -> consecutive_days += 1
      - today's raw != yesterday's stored raw -> raw resets, consecutive_days = 1
      - if consecutive_days >= DEBOUNCE_DAYS and raw != effective_status
        -> effective_status flips to raw

    Idempotent per calendar day (guards on TickerStreakState.updated_at's
    date): calling this more than once on the same day — a manual
    verification re-run, or a droplet restart re-firing the same day's
    cycle — must NOT double-count that day as two consecutive days. Only
    the first call in a given calendar day advances the streak; later
    calls the same day just re-report the already-current state.

    First-ever run for a ticker (no existing TickerStreakState row)
    bootstraps effective_status = today's raw immediately, with
    flipped=False by definition — there is no prior effective status to
    have "flipped" away from, so a brand-new tracker's first reading must
    never itself trigger a sell. This matters concretely today: several
    of the fixed 6 are currently failing raw classification (see
    docs/DISCOVERY_DESIGN.md's real verification runs) — bootstrapping
    this way means they correctly start "already non-obvious" (no change
    from their current buy-only-excluded behavior) WITHOUT retroactively
    selling an existing position the very first time this feature runs.

    `today` is injectable (defaults to real UTC today) purely so tests
    can simulate multiple distinct days without waiting — see
    tests/test_classification.py. The persisted updated_at is anchored to
    this same `today` (combined with the real time-of-day, for a
    readable timestamp) rather than always stamping the real wall-clock
    date, so injecting different `today` values across calls fully
    controls the idempotency check too, not just the streak math.
    """
    today = today or datetime.utcnow().date()
    now = datetime.combine(today, datetime.utcnow().time())
    results: dict[str, StreakResult] = {}

    for ticker, result in classifications.items():
        raw = result.is_obvious
        row = db.query(TickerStreakState).filter(TickerStreakState.ticker == ticker).one_or_none()

        if row is None:
            row = TickerStreakState(
                ticker=ticker, raw_status=raw, consecutive_days=1, effective_status=raw,
                updated_at=now,
            )
            db.add(row)
            previous_effective = raw  # bootstrap: nothing to have flipped from
            db.flush()  # so the "already updated today" branch below reads it correctly if re-hit
            results[ticker] = StreakResult(
                ticker=ticker, raw_status=raw, consecutive_days=1, effective_status=raw,
                previous_effective_status=previous_effective, flipped=False,
            )
            logger.info(
                "streak %s: first-ever run (bootstrap) — raw=%s consecutive_days=1 effective=%s",
                ticker, raw, raw,
            )
            continue
        elif row.updated_at.date() == today:
            # Already updated today — idempotent re-run, don't double-
            # count this calendar day as an extra consecutive day.
            results[ticker] = StreakResult(
                ticker=ticker, raw_status=row.raw_status, consecutive_days=row.consecutive_days,
                effective_status=row.effective_status,
                previous_effective_status=row.effective_status, flipped=False,
            )
            logger.info(
                "streak %s: already updated today (idempotent re-run) — raw=%s "
                "consecutive_days=%d effective=%s",
                ticker, row.raw_status, row.consecutive_days, row.effective_status,
            )
            continue
        else:
            previous_effective = row.effective_status
            if raw == row.raw_status:
                row.consecutive_days += 1
            else:
                row.raw_status = raw
                row.consecutive_days = 1
            if (
                row.consecutive_days >= agent_config.CLASSIFICATION_DEBOUNCE_DAYS
                and raw != row.effective_status
            ):
                row.effective_status = raw

        row.updated_at = now
        flipped = row.effective_status != previous_effective
        results[ticker] = StreakResult(
            ticker=ticker, raw_status=row.raw_status, consecutive_days=row.consecutive_days,
            effective_status=row.effective_status,
            previous_effective_status=previous_effective, flipped=flipped,
        )
        logger.info(
            "streak %s: raw=%s consecutive_days=%d/%d effective=%s%s",
            ticker, row.raw_status, row.consecutive_days, agent_config.CLASSIFICATION_DEBOUNCE_DAYS,
            row.effective_status, " <<< FLIPPED" if flipped else "",
        )

    db.commit()
    return results


def global_obvious_pool(
    streaks: dict[str, StreakResult], *, discovered_tickers: set[str]
) -> set[str]:
    """Discovered tickers CONFIRMED obvious (debounced effective_status
    True via update_streaks() — one good raw day is never enough, same
    3-day requirement as every other status transition) — the set
    rebalance_service.effective_target_weights() folds into EVERY tier's
    obvious bucket, not just one (see that function's docstring for why
    "shared across every tier" is the right call for a ticker that's
    cleared Gate C's strict volatility bar: it's calm enough for any risk
    level, unlike an ordinary non-obvious candidate, which stays
    volatility-routed to a single tier via route_non_obvious()).

    Fixed-6 tickers are excluded via `discovered_tickers` the same way
    route_non_obvious() excludes them — this pool is specifically "which
    DISCOVERED tickers have earned their way into the obvious bucket,"
    not a general "everyone currently obvious" set (the fixed 6's own
    obvious/non-obvious status is read directly off `streaks` by
    agent/run_rebalance.py, unchanged from before this pool existed).
    """
    # This set comprehension has non-deterministic iteration order (it
    # iterates `discovered_tickers`, itself a set). That's currently safe
    # ONLY because this function's sole caller,
    # rebalance_service.effective_target_weights(), re-sorts this return
    # value before using it (`sorted(pool - set(obvious_base))`) rather
    # than iterating it directly. Any future caller that consumes this
    # return value directly — e.g. to decide which pool member gets
    # clamped first against a shared cash budget — would need its own
    # deterministic ordering, or would risk reintroducing the exact
    # unstable-iteration-order bug class already found and fixed once in
    # this module's redistribution logic (2026-08-17/18 incident).
    return {
        ticker
        for ticker in discovered_tickers
        if streaks.get(ticker) is not None and streaks[ticker].effective_status
    }
