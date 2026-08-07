"""Coverage for GET /stocks/search, /stocks/{ticker}/quote, and
/stocks/{ticker}/history (app/routes/stocks.py). Public/no-auth, same as
the agent's own model-portfolio reads, so no Authorization header is
used anywhere here.

These call the real Yahoo Finance / yfinance data path (no mocking
layer exists elsewhere in this test suite to hook into — see
test_portfolio_auth.py, which relies on real DB state rather than
mocks) — assertions are kept to structure/shape/sanity rather than
exact live-market values, so they don't flake on real price movement.
"""

from app.services import stock_data

KNOWN_TICKER = "AAPL"


def test_search_with_results(client):
    resp = client.get("/stocks/search?q=apple")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert len(body) <= 10
    tickers = [r["ticker"] for r in body]
    assert KNOWN_TICKER in tickers
    for r in body:
        assert set(r.keys()) == {"ticker", "name", "exchange"}


def test_search_with_no_results_for_nonsense_query(client):
    resp = client.get("/stocks/search?q=zzzznonexistentcompanyxyz123")
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_empty_query_returns_empty_list_not_error(client):
    resp = client.get("/stocks/search?q=")
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_short_query_returns_empty_list(client):
    resp = client.get("/stocks/search?q=a")
    assert resp.status_code == 200
    assert resp.json() == []


def test_valid_ticker_quote(client):
    resp = client.get(f"/stocks/{KNOWN_TICKER}/quote")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == KNOWN_TICKER
    assert isinstance(body["company_name"], str) and body["company_name"]
    assert isinstance(body["price"], (int, float)) and body["price"] > 0
    # Fields that can legitimately be null (e.g. for an ETF) still need
    # to be present in the response shape.
    for field in (
        "change", "change_pct", "day_high", "day_low",
        "fifty_two_week_high", "fifty_two_week_low", "volume",
        "average_volume", "market_cap", "pe_ratio_trailing",
        "dividend_yield", "sector", "industry", "description",
    ):
        assert field in body


def test_invalid_ticker_quote_is_404_not_500(client):
    resp = client.get("/stocks/ZZZZINVALIDTICKER/quote")
    assert resp.status_code == 404


def test_history_returns_well_formed_ohlc_for_every_range(client):
    for range_key in stock_data.RANGE_TO_YAHOO:
        resp = client.get(f"/stocks/{KNOWN_TICKER}/history?range={range_key}")
        assert resp.status_code == 200, f"range={range_key}"
        body = resp.json()
        assert body["ticker"] == KNOWN_TICKER
        assert body["range"] == range_key
        candles = body["candles"]
        assert isinstance(candles, list)
        assert len(candles) > 0, f"range={range_key} returned no candles"
        for c in candles[:3]:
            assert set(c.keys()) == {"timestamp", "open", "high", "low", "close", "volume"}
            assert isinstance(c["timestamp"], str)
            assert c["low"] <= c["open"] <= c["high"]
            assert c["low"] <= c["close"] <= c["high"]


def test_history_invalid_range_is_422(client):
    resp = client.get(f"/stocks/{KNOWN_TICKER}/history?range=1M")
    assert resp.status_code == 422


def test_history_invalid_ticker_returns_empty_candles_not_error(client):
    # Not a 404 by design: an unknown ticker's history degrades to an
    # empty list (see stock_data.history()'s docstring) rather than
    # hard-failing, matching the codebase's mock/offline fallback
    # convention for external data sources.
    resp = client.get("/stocks/ZZZZINVALIDTICKER/history?range=30D")
    assert resp.status_code == 200
    assert resp.json()["candles"] == []
