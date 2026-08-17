"""Coverage for obvious/non-obvious classification
(agent/classification.py) — see docs/DISCOVERY_DESIGN.md §3.

Three things this file exists to guarantee:
1. Gate A (growth consistency) and Gate C (volatility, independent of
   A/B) compute correctly on constructed weekly-close series, including
   the Tesla/Nvidia-shaped case Gate C exists specifically to catch:
   consistently-up AND heavily-covered but too volatile to be "obvious."
2. classify_ticker() combines all three gates with AND, not any
   individual gate alone.
3. route_non_obvious() routes into exactly one tier by volatility band,
   never routes a fixed-6 ticker, and never routes an obvious-classified
   ticker.

Network calls (stock_data.history, Finnhub) are monkeypatched — same
convention test_rebalance.py/test_pending_trades.py already use.
"""

import asyncio
from datetime import date, timedelta

import httpx
import pytest

import app.services.stock_data as stock_data
from agent import agent_config, news_ingestion
from agent.classification import (
    ClassificationResult,
    _annualized_volatility,
    _gate_a,
    _gate_c,
    _weekly_returns,
    classify_ticker,
    route_non_obvious,
    update_streaks,
)
from app.database import SessionLocal
from app.models import TickerStreakState


# ---------------------------------------------------------------------
# Gate A — price growth consistency
# ---------------------------------------------------------------------


def test_gate_a_passes_steady_climb():
    # 13 weeks, steadily up, every week non-negative.
    closes = [100.0 + i for i in range(13)]
    passed, detail = _gate_a(closes)
    assert passed is True
    assert "total_return=+12.00%" in detail


def test_gate_a_fails_net_negative_even_if_mostly_up_weeks():
    """Consistency alone isn't enough — total return must also be
    positive (catches "mostly flat/down but with many tiny up-ticks")."""
    closes = [100.0, 99.0, 98.0, 97.0, 96.5, 96.6, 96.7, 96.8, 96.9, 97.0, 97.1, 97.2, 97.3]
    passed, _ = _gate_a(closes)
    assert passed is False


def test_gate_a_fails_one_big_spike_then_flat_or_down():
    """The exact failure mode Gate A's consistency requirement exists to
    catch: a single earnings-pop spike followed by drift, net positive
    overall, but most individual weeks are down — not "steady growth.\""""
    closes = [100.0, 130.0, 129.0, 128.0, 127.0, 126.0, 125.0, 124.0, 123.0, 122.0, 121.0, 120.0, 119.0]
    total_return = (closes[-1] - closes[0]) / closes[0]
    assert total_return > 0  # net positive overall...
    passed, detail = _gate_a(closes)
    assert passed is False  # ...but still fails on consistency
    assert "positive_weeks=8%" in detail  # only week 1->2 was up, out of 12 deltas


# ---------------------------------------------------------------------
# Gate C — volatility, independent of A/B
# ---------------------------------------------------------------------


def test_gate_c_passes_calm_stock_similar_to_voo():
    closes = [100.0, 100.5, 101.0, 100.7, 101.3, 101.6, 101.4, 101.9, 102.1, 101.8, 102.3, 102.5, 102.8]
    voo_vol = _annualized_volatility(_weekly_returns(closes))  # judge it against itself: ratio ~1.0
    passed, detail, vol, ratio = _gate_c(closes, voo_vol)
    assert passed is True
    assert ratio == pytest.approx(1.0, abs=0.01)


def test_gate_c_fails_tesla_nvidia_shaped_stock_consistently_up_but_too_volatile():
    """The exact scenario docs/DISCOVERY_DESIGN.md §3 flags: a stock that
    would PASS Gate A (steady climb, 8 of 12 weeks up, net +90%) but
    swings 10-20%/week — far more volatile than VOO — so Gate C must
    independently reject it despite A passing."""
    closes = [
        100.0, 120.0, 140.0, 125.0, 145.0, 165.0, 148.0,
        168.0, 188.0, 170.0, 190.0, 210.0, 190.0,
    ]
    a_passed, a_detail = _gate_a(closes)
    voo_vol = 0.10  # a calm VOO-like trailing annualized volatility
    c_passed, detail, vol, ratio = _gate_c(closes, voo_vol)
    assert a_passed is True, a_detail  # net up, consistency requirement still met on this shape
    assert c_passed is False  # but Gate C independently rejects it
    assert ratio > agent_config.VOLATILITY_MAX_VS_VOO


def test_gate_c_fails_on_single_outsized_weekly_move_even_with_low_overall_volatility():
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 130.0, 131.0, 132.0, 133.0, 134.0]
    passed, detail, vol, ratio = _gate_c(closes, voo_vol=0.15)
    assert passed is False
    assert "max_weekly_move" in detail


def test_gate_c_fails_closed_when_voo_volatility_unavailable():
    closes = [100.0, 101.0, 102.0]
    passed, detail, vol, ratio = _gate_c(closes, voo_vol=None)
    assert passed is False
    assert ratio is None


# ---------------------------------------------------------------------
# classify_ticker() — combines all three gates with AND
# ---------------------------------------------------------------------


@pytest.fixture
def fake_history(monkeypatch):
    table: dict[str, list[dict]] = {}

    async def _fake(ticker: str, range_key: str) -> list[dict]:
        closes = table.get(ticker.upper(), [])
        return [{"close": c} for c in closes]

    monkeypatch.setattr(stock_data, "history", _fake)
    return table


@pytest.fixture
def fake_finnhub_count(monkeypatch):
    """Monkeypatches news_ingestion._fetch_ticker_news_finnhub (Gate B's
    underlying call) to return a fixed-length list without any network
    call — table maps ticker -> article count."""
    table: dict[str, int] = {}

    async def _fake(client, ticker, api_key, limiter, days_back):
        return [{}] * table.get(ticker.upper(), 0)

    monkeypatch.setattr(news_ingestion, "_fetch_ticker_news_finnhub", _fake)
    return table


def _steady_closes(n=13):
    return [100.0 + i * 0.5 for i in range(n)]


def test_classify_ticker_obvious_when_all_three_gates_pass(fake_history, fake_finnhub_count):
    fake_history["AAPL"] = _steady_closes()
    fake_finnhub_count["AAPL"] = 20  # >= ARTICLE_VOLUME_MIN

    async def run():
        async with httpx.AsyncClient() as client:
            limiter = news_ingestion.RateLimiter()
            return await classify_ticker(
                "AAPL", client=client, api_key="fake-key", limiter=limiter, voo_vol=0.05
            )

    result = asyncio.run(run())
    assert result.gate_a_passed is True
    assert result.gate_b_passed is True
    assert result.gate_c_passed is True
    assert result.is_obvious is True


def test_classify_ticker_not_obvious_when_only_article_volume_fails(fake_history, fake_finnhub_count):
    """A/C pass but B fails (low press coverage) -> NOT obvious, even
    though price behavior looks fine — obvious requires all three."""
    fake_history["QUIET"] = _steady_closes()
    fake_finnhub_count["QUIET"] = 2  # well under ARTICLE_VOLUME_MIN

    async def run():
        async with httpx.AsyncClient() as client:
            limiter = news_ingestion.RateLimiter()
            return await classify_ticker(
                "QUIET", client=client, api_key="fake-key", limiter=limiter, voo_vol=0.05
            )

    result = asyncio.run(run())
    assert result.gate_a_passed is True
    assert result.gate_b_passed is False
    assert result.is_obvious is False


def test_classify_ticker_insufficient_history_fails_closed(fake_history, fake_finnhub_count):
    fake_history["NEWLISTING"] = [100.0, 101.0]  # fewer than GROWTH_MIN_WEEKLY_CANDLES
    fake_finnhub_count["NEWLISTING"] = 50

    async def run():
        async with httpx.AsyncClient() as client:
            limiter = news_ingestion.RateLimiter()
            return await classify_ticker(
                "NEWLISTING", client=client, api_key="fake-key", limiter=limiter, voo_vol=0.05
            )

    result = asyncio.run(run())
    assert result.gate_a_passed is False
    assert result.gate_c_passed is False
    assert result.is_obvious is False
    assert result.vol_ratio is None


# ---------------------------------------------------------------------
# route_non_obvious() — exactly one tier, fixed-6 excluded, obvious excluded
# ---------------------------------------------------------------------


def _result(ticker, *, is_obvious, vol_ratio):
    return ClassificationResult(
        ticker=ticker, is_obvious=is_obvious,
        gate_a_passed=not is_obvious, gate_a_detail="",
        gate_b_passed=not is_obvious, gate_b_detail="",
        gate_c_passed=not is_obvious, gate_c_detail="",
        volatility=None, voo_volatility=None, vol_ratio=vol_ratio,
    )


def test_route_non_obvious_bands_by_volatility_ratio():
    classifications = {
        "CALM": _result("CALM", is_obvious=False, vol_ratio=1.2),   # -> conservative
        "MID": _result("MID", is_obvious=False, vol_ratio=2.5),     # -> moderate
        "WILD": _result("WILD", is_obvious=False, vol_ratio=8.0),   # -> aggressive
    }
    routed = route_non_obvious(
        classifications,
        discovered_tickers={"CALM", "MID", "WILD"},
        confidence_by_ticker={"CALM": 0.5, "MID": 0.5, "WILD": 0.5},
    )
    assert [c.ticker for c in routed["conservative"]] == ["CALM"]
    assert [c.ticker for c in routed["moderate"]] == ["MID"]
    assert [c.ticker for c in routed["aggressive"]] == ["WILD"]


def test_route_non_obvious_never_routes_fixed_six_tickers():
    """Even if a fixed-6 ticker classifies as non-obvious, it must never
    appear in any tier's routed list — only discovery-sourced tickers are
    ever routed (see route_non_obvious()'s docstring)."""
    classifications = {
        "BND": _result("BND", is_obvious=False, vol_ratio=1.0),  # fixed-6, fails classification
        "XYZ": _result("XYZ", is_obvious=False, vol_ratio=1.0),  # discovery-sourced
    }
    routed = route_non_obvious(
        classifications,
        discovered_tickers={"XYZ"},  # BND deliberately NOT in this set
        confidence_by_ticker={"XYZ": 0.7},
    )
    all_routed_tickers = {c.ticker for tier_list in routed.values() for c in tier_list}
    assert all_routed_tickers == {"XYZ"}
    assert "BND" not in all_routed_tickers


def test_route_non_obvious_never_routes_obvious_classified_tickers():
    classifications = {
        "XYZ": _result("XYZ", is_obvious=True, vol_ratio=1.0),
    }
    routed = route_non_obvious(
        classifications, discovered_tickers={"XYZ"}, confidence_by_ticker={"XYZ": 0.9},
    )
    all_routed_tickers = {c.ticker for tier_list in routed.values() for c in tier_list}
    assert all_routed_tickers == set()


def test_route_non_obvious_sorts_by_confidence_descending_within_tier():
    classifications = {
        "LOW": _result("LOW", is_obvious=False, vol_ratio=1.0),
        "HIGH": _result("HIGH", is_obvious=False, vol_ratio=1.0),
    }
    routed = route_non_obvious(
        classifications,
        discovered_tickers={"LOW", "HIGH"},
        confidence_by_ticker={"LOW": 0.2, "HIGH": 0.9},
    )
    assert [c.ticker for c in routed["conservative"]] == ["HIGH", "LOW"]


def test_route_non_obvious_skips_candidates_with_no_volatility_ratio():
    classifications = {
        "NODATA": _result("NODATA", is_obvious=False, vol_ratio=None),
    }
    routed = route_non_obvious(
        classifications, discovered_tickers={"NODATA"}, confidence_by_ticker={"NODATA": 0.9},
    )
    all_routed_tickers = {c.ticker for tier_list in routed.values() for c in tier_list}
    assert all_routed_tickers == set()


# ---------------------------------------------------------------------
# update_streaks() — the 3-day debounce (Part A of the sell mechanisms).
# CONSTRUCTED: real production data is at most a couple of days old for
# this feature, so these simulate multiple distinct days via update_
# streaks()'s injectable `today` parameter rather than waiting.
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_streak_state(client):
    """Schema bootstrap (via the unused `client` fixture, see
    test_rebalance.py's identical convention) plus a clean slate per
    test — TickerStreakState is a single upserted row per ticker, so
    tests must not see each other's leftover rows for the same ticker."""
    db = SessionLocal()
    try:
        db.query(TickerStreakState).delete()
        db.commit()
    finally:
        db.close()
    yield


def _classifications(**raw_by_ticker: bool) -> dict[str, ClassificationResult]:
    return {
        ticker: _result(ticker, is_obvious=raw, vol_ratio=1.0)
        for ticker, raw in raw_by_ticker.items()
    }


def test_update_streaks_bootstrap_never_flips_or_triggers_a_sell():
    """First-ever run for a ticker must never itself count as a
    'flip' — there is no prior effective status to have flipped away
    from. Matters concretely: several real fixed-6 tickers are CURRENTLY
    failing raw classification, and bootstrapping must not retroactively
    sell their existing positions the moment this feature is deployed."""
    db = SessionLocal()
    try:
        results = update_streaks(db, _classifications(BND=False, VOO=True), today=date(2026, 1, 1))
    finally:
        db.close()

    assert results["BND"].effective_status is False
    assert results["BND"].consecutive_days == 1
    assert results["BND"].flipped is False

    assert results["VOO"].effective_status is True
    assert results["VOO"].flipped is False


def test_update_streaks_flip_fires_on_day_three_not_one_or_two():
    """CONSTRUCTED: obvious ticker starts passing (day 0, bootstrap),
    then fails 3 consecutive days — the flip (and the sell it would
    trigger in agent/run_rebalance.py) must land on day 3, not day 1
    or day 2."""
    ticker = "XYZ"
    day0, day1, day2, day3 = (date(2026, 1, i) for i in (1, 2, 3, 4))
    db = SessionLocal()
    try:
        r0 = update_streaks(db, _classifications(**{ticker: True}), today=day0)[ticker]
        assert r0.effective_status is True and r0.flipped is False  # bootstrap, still obvious

        r1 = update_streaks(db, _classifications(**{ticker: False}), today=day1)[ticker]
        assert r1.consecutive_days == 1
        assert r1.effective_status is True  # still obvious — day 1 of 3
        assert r1.flipped is False

        r2 = update_streaks(db, _classifications(**{ticker: False}), today=day2)[ticker]
        assert r2.consecutive_days == 2
        assert r2.effective_status is True  # still obvious — day 2 of 3
        assert r2.flipped is False

        r3 = update_streaks(db, _classifications(**{ticker: False}), today=day3)[ticker]
        assert r3.consecutive_days == 3
        assert r3.effective_status is False  # NOW it flips — day 3
        assert r3.flipped is True
    finally:
        db.close()


def test_update_streaks_single_day_flip_back_resets_streak_without_selling():
    """CONSTRUCTED: 2 consecutive failing days (one short of the 3-day
    threshold), then a single passing day — the streak must reset to 1,
    and effective_status must have stayed obvious=True THE ENTIRE TIME
    (it never got close enough to flip), so no sell would ever fire."""
    ticker = "XYZ"
    days = [date(2026, 1, i) for i in range(1, 6)]
    db = SessionLocal()
    try:
        update_streaks(db, _classifications(**{ticker: True}), today=days[0])  # bootstrap
        update_streaks(db, _classifications(**{ticker: False}), today=days[1])  # fail day 1
        r2 = update_streaks(db, _classifications(**{ticker: False}), today=days[2])[ticker]  # fail day 2
        assert r2.consecutive_days == 2
        assert r2.effective_status is True  # not flipped yet

        r3 = update_streaks(db, _classifications(**{ticker: True}), today=days[3])[ticker]  # back to pass
        assert r3.raw_status is True
        assert r3.consecutive_days == 1  # reset, not 3
        assert r3.effective_status is True  # was never touched
        assert r3.flipped is False  # nothing to flip — it never left True
    finally:
        db.close()


def test_update_streaks_regaining_obvious_status_needs_three_days_too():
    """Symmetric to the losing-obvious-status case: a ticker that starts
    non-obvious (bootstrap) only regains effective obvious status after
    3 consecutive passing days."""
    ticker = "XYZ"
    days = [date(2026, 1, i) for i in range(1, 5)]
    db = SessionLocal()
    try:
        r0 = update_streaks(db, _classifications(**{ticker: False}), today=days[0])[ticker]
        assert r0.effective_status is False  # bootstrap non-obvious

        r1 = update_streaks(db, _classifications(**{ticker: True}), today=days[1])[ticker]
        assert r1.consecutive_days == 1 and r1.effective_status is False

        r2 = update_streaks(db, _classifications(**{ticker: True}), today=days[2])[ticker]
        assert r2.consecutive_days == 2 and r2.effective_status is False

        r3 = update_streaks(db, _classifications(**{ticker: True}), today=days[3])[ticker]
        assert r3.consecutive_days == 3
        assert r3.effective_status is True  # regained, on day 3
        assert r3.flipped is True
    finally:
        db.close()


def test_update_streaks_same_day_rerun_is_idempotent_not_double_counted():
    """A manual re-run (or a droplet catching up the same calendar day)
    must not advance the streak twice for one calendar day."""
    ticker = "XYZ"
    today = date(2026, 1, 1)
    db = SessionLocal()
    try:
        update_streaks(db, _classifications(**{ticker: True}), today=today)  # bootstrap
        r1 = update_streaks(db, _classifications(**{ticker: False}), today=today + timedelta(days=1))[ticker]
        assert r1.consecutive_days == 1

        r1_again = update_streaks(
            db, _classifications(**{ticker: False}), today=today + timedelta(days=1)
        )[ticker]
        assert r1_again.consecutive_days == 1  # NOT 2 — same calendar day, not double-counted
        assert r1_again.flipped is False
    finally:
        db.close()
