"""
AI recommendation: OpenAI when configured; structured mock fallback for dev.

Rationale is grounded in real news sentiment reused directly from the
trading agent's own infrastructure (agent/news_ingestion.py,
agent/sentiment.py) via _gather_market_signal() below, rather than a
second, parallel news/LLM pipeline. Both of those modules already accept
arbitrary ticker/article lists and aren't agent-specific in any way that
blocks reuse here, with one caveat: SeenArticleStore's default dedupe-cache
path is derived from news_ingestion.py's own file location (not the
caller's), so calling fetch_news() without an explicit store would
silently share the trading agent's persistent "already seen" cache —
articles the agent already processed would come back empty for
recommendations too. _gather_market_signal() sidesteps that with a
scoped, in-memory SeenArticleStore per call instead of touching
news_ingestion.py itself.

Deliberately does NOT import from agent/decision_loop.py (out of scope
for this feature) — RISK_TIER_BY_TOLERANCE below is a small, independent
copy of the same fixed low/medium/high vocabulary mapping, not shared
business logic.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from agent import news_ingestion, sentiment
from agent.news_ingestion import NewsArticle, SeenArticleStore
from agent.sentiment import ScoreResult
from app.config import get_settings
from app.schemas.recommendation import (
    RecommendationResponse,
    RecommendedAsset,
)

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"

# User.risk_tolerance ("low"|"medium"|"high", set during onboarding) onto
# agent/news_ingestion.py's TARGET_PORTFOLIOS buckets — the same three
# risk-tier candidate-symbol sets the trading agent fetches news for.
RISK_TIER_BY_TOLERANCE: dict[str, str] = {
    "low": "conservative",
    "medium": "moderate",
    "high": "aggressive",
}
DEFAULT_TIER = "moderate"

# Per-ticker cap on how many fetched articles get sentiment-scored for a
# single recommendation request — see the comment in
# _gather_market_signal() for why this differs from the agent's own
# uncapped background cadence.
MAX_ARTICLES_PER_TICKER = 5

# Deterministic mock allocations per tier, in the same order as
# TARGET_PORTFOLIOS[tier] (2 stock/ETF entries, then 1 crypto entry).
MOCK_ALLOCATIONS_BY_TIER: dict[str, list[float]] = {
    "conservative": [55.0, 30.0, 15.0],
    "moderate": [50.0, 35.0, 15.0],
    "aggressive": [40.0, 35.0, 25.0],
}

TONE_GUIDANCE: dict[str, str] = {
    "conservative": (
        "This user has LOW risk tolerance. Use a calm, reassuring tone that "
        "emphasizes capital preservation and steady progress. Be explicit "
        "that this approach trades higher potential growth for more "
        "stability, and explain why that trade fits their profile."
    ),
    "moderate": (
        "This user has MEDIUM risk tolerance. Use a balanced, confident "
        "tone. Explain the mix of stability and growth, and why a middle "
        "path fits someone who wants progress without extreme swings."
    ),
    "aggressive": (
        "This user has HIGH risk tolerance. Use an energetic but still "
        "honest tone that leans into growth potential, while plainly "
        "explaining that this portfolio can swing significantly in value "
        "and that's the tradeoff for higher potential returns."
    ),
}

TIER_RISK_EXPLANATION: dict[str, str] = {
    "conservative": (
        "You told us you're comfortable with lower risk, so this plan leans "
        "toward stability: most of your money sits in broad, well-established "
        "holdings, with only a small slice in anything that moves a lot day "
        "to day. Expect smaller ups and downs than a typical stock portfolio."
    ),
    "moderate": (
        "You told us you're comfortable with a moderate amount of risk, so "
        "this plan splits the difference: solid, established holdings for "
        "stability, plus a real (but not huge) slice aimed at growth. Expect "
        "noticeable ups and downs, but not extreme ones."
    ),
    "aggressive": (
        "You told us you're comfortable with higher risk, so this plan "
        "leans into growth: a bigger share goes toward holdings that can "
        "swing significantly in value in exchange for higher long-term "
        "growth potential. Expect real ups and downs — sometimes sharp ones."
    ),
}

TIER_PLAIN_ENGLISH: dict[str, str] = {
    "conservative": (
        "Because preserving what you have matters most right now, we kept "
        "things simple and steady rather than chasing bigger gains. "
        "Investing a little regularly, even in a cautious mix like this "
        "one, adds up more than trying to time the market."
    ),
    "moderate": (
        "This mix aims to grow your money over time without betting too "
        "much on any single outcome. Investing regularly and letting time "
        "do the work usually matters more than picking the 'perfect' "
        "portfolio."
    ),
    "aggressive": (
        "Since you're comfortable with more risk, this mix aims higher — "
        "but higher potential returns come with real potential for bigger "
        "drops too. Only invest what you can leave alone through the "
        "swings."
    ),
}

TIER_SUMMARY_TEMPLATE: dict[str, str] = {
    "conservative": "A steady, lower-risk mix aimed at {goals}, built for a {horizon} timeline.",
    "moderate": "A balanced mix aimed at {goals}, built for a {horizon} timeline.",
    "aggressive": "A growth-focused mix aimed at {goals}, built for a {horizon} timeline.",
}

TIER_ASSET_FLAVOR: dict[str, str] = {
    "conservative": "a plan built around steady, lower-drama growth",
    "moderate": "a plan that balances steady growth with some upside",
    "aggressive": "a plan willing to accept bigger swings for bigger potential upside",
}

FUNDAMENTALS_ROLE_TEXT: dict[str, str] = {
    "stock": (
        "a broad, established holding chosen to anchor the portfolio with "
        "steady, well-understood exposure rather than a single risky bet"
    ),
    "crypto": (
        "a smaller, higher-swing slice included to add some extra upside "
        "potential without staking too much of the portfolio on it"
    ),
}

# Flagged (not blocked) in generated rationale/summary text — loudly logged
# so drift toward jargon-heavy copy doesn't go unnoticed. Not exhaustive;
# a cheap regression signal, not a hard content gate.
JARGON_TERMS = (
    "beta", "alpha", "sharpe ratio", "p/e ratio", "standard deviation",
    "volatility-adjusted", "drawdown", "correlation coefficient",
)

# Phrases that imply the rationale is citing news coverage — used to catch
# a symbol whose rationale claims a news-based reason despite no real
# article being available for it (see _check_consistency).
_NEWS_CLAIM_PHRASES = ("recent news", "according to news", "the news", "reports show", "headlines")

_PCT_PATTERN = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")

SYSTEM = """You are britney.ai, a friendly investing coach for people who have never invested before.

Respond ONLY with valid JSON matching this schema:
{
  "summary": "string",
  "risk_explanation": "string",
  "plain_english_reasoning": "string",
  "assets": [
    {"symbol": "string", "asset_type": "stock|crypto|option", "allocation_pct": number, "rationale": "string"}
  ]
}

Rules:
- Recommend a portfolio using ONLY the candidate symbols given in the user message — do not invent tickers, and include every candidate symbol given.
- Allocations must sum to ~100.
- Each asset's rationale must be grounded in the market signal given for that symbol when one is present. If a symbol's signal says there's no recent news, its rationale must rely on fundamentals/diversification/risk-fit reasoning instead — never invent a news-based reason for it.
- Never use unexplained financial jargon (e.g. "beta", "alpha", "Sharpe ratio", "P/E ratio", "volatility-adjusted"). If a technical term is genuinely necessary, define it briefly in the same sentence, in plain language.
- Match tone and content to the tone guidance given in the user message — conservative/moderate/aggressive profiles should read noticeably differently in wording and emphasis, not just have different numbers.
- Naturally weave the user's stated goals and time horizon into the summary and reasoning.
- Every asset's rationale must mention that asset's own ticker symbol, and any percentage stated in the rationale text must match its allocation_pct.
"""


@dataclass
class AssetSignal:
    symbol: str
    asset_type: str
    sentiment: str | None  # bullish | bearish | neutral, or None if no news
    confidence: float | None
    reasoning: str | None


@dataclass
class MarketSignal:
    tier: str
    assets: list[AssetSignal]
    used_real_news: bool
    used_real_sentiment: bool

    def block(self) -> str:
        """Plain-text block for the LLM/mock template: one line per
        candidate asset, either its real sentiment finding or an explicit
        "no recent news" flag so the model doesn't fabricate one."""
        lines = []
        for a in self.assets:
            if a.sentiment is None:
                lines.append(
                    f"- {a.symbol} ({a.asset_type}): no recent news available — "
                    "reason about this one from fundamentals/allocation fit "
                    "only, not news."
                )
            else:
                lines.append(
                    f"- {a.symbol} ({a.asset_type}): recent news reads "
                    f"{a.sentiment} (confidence {a.confidence:.2f}) — {a.reasoning}"
                )
        return "\n".join(lines)


def _tier_for(risk_tolerance: str) -> str:
    return RISK_TIER_BY_TOLERANCE.get((risk_tolerance or "").lower(), DEFAULT_TIER)


async def _gather_market_signal(risk_tolerance: str) -> MarketSignal:
    """Fetch real (or mock, per news_ingestion's own fallback) news for this
    tier's stock/ETF candidates and score it (real or mock, per sentiment's
    own fallback) — reusing both modules exactly as the trading agent does,
    just scoped to an in-memory dedupe store so this never reads/writes the
    agent's own persistent .news_seen.sqlite3 cache."""
    settings = get_settings()
    tier = _tier_for(risk_tolerance)
    candidates = news_ingestion.TARGET_PORTFOLIOS[tier]
    stock_tickers = [sym for sym, atype in candidates if atype == "stock"]

    if not stock_tickers:
        return MarketSignal(
            tier=tier,
            assets=[AssetSignal(sym, atype, None, None, None) for sym, atype in candidates],
            used_real_news=False,
            used_real_sentiment=False,
        )

    store = SeenArticleStore(db_path=":memory:")
    try:
        articles = await news_ingestion.fetch_news(tickers=stock_tickers, store=store)
    finally:
        store.close()

    # A widely-covered ticker (e.g. SPY) can have hundreds of articles in
    # a 2-day window — fetch_news()/score_batch() don't cap that (fine for
    # the agent's own background cadence, where it runs unattended), but
    # scoring all of them here would mean dozens of OpenAI calls and
    # tens of seconds of added latency on every live "generate
    # recommendation" request. Cap to the most recent few per ticker
    # before scoring; recency matters more than volume for this signal.
    if len(articles) > MAX_ARTICLES_PER_TICKER * len(stock_tickers):
        by_ticker: dict[str, list[NewsArticle]] = {}
        for article in articles:
            by_ticker.setdefault(article.ticker, []).append(article)
        articles = [
            a
            for ticker_articles in by_ticker.values()
            for a in sorted(ticker_articles, key=lambda a: a.published_at, reverse=True)[
                :MAX_ARTICLES_PER_TICKER
            ]
        ]

    scores_by_ticker: dict[str, ScoreResult] = {}
    if articles:
        scores = await sentiment.score_batch(articles)
        for article, score in zip(articles, scores):
            best = scores_by_ticker.get(article.ticker)
            if best is None or score.confidence > best.confidence:
                scores_by_ticker[article.ticker] = score

    assets = []
    for symbol, asset_type in candidates:
        score = scores_by_ticker.get(symbol)
        if score is None:
            assets.append(AssetSignal(symbol, asset_type, None, None, None))
        else:
            assets.append(
                AssetSignal(symbol, asset_type, score.sentiment, score.confidence, score.reasoning)
            )

    return MarketSignal(
        tier=tier,
        assets=assets,
        used_real_news=bool(settings.finnhub_api_key),
        used_real_sentiment=bool(settings.openai_api_key) and bool(articles),
    )


def _asset_rationale(a: AssetSignal, tier: str, pct: float) -> str:
    role = FUNDAMENTALS_ROLE_TEXT.get(a.asset_type, "a supporting holding")
    flavor = TIER_ASSET_FLAVOR.get(tier, TIER_ASSET_FLAVOR["moderate"])
    if a.sentiment is not None and a.sentiment != "neutral":
        return (
            f"Recent news on {a.symbol} reads {a.sentiment} ({a.reasoning}) "
            f"We're putting {pct:.0f}% here as {role}, which fits {flavor}."
        )
    return (
        f"There's no strong recent news signal for {a.symbol} right now, so "
        f"this {pct:.0f}% allocation is based on fundamentals: it's {role}, "
        f"which fits {flavor}."
    )


def _mock_recommendation(
    risk_tolerance: str,
    goals: str | None,
    horizon: str | None,
    signal: MarketSignal,
) -> RecommendationResponse:
    """Deterministic fallback when OpenAI is unavailable or the LLM's
    output failed the consistency check. Rationale is built from the SAME
    real (or mock, per news_ingestion/sentiment's own fallbacks) market
    signal the LLM path uses, so grounding survives fully-offline/degraded
    runs too — not just a static template with no signal at all."""
    tier = signal.tier
    pcts = MOCK_ALLOCATIONS_BY_TIER.get(tier, MOCK_ALLOCATIONS_BY_TIER["moderate"])

    assets = [
        RecommendedAsset(
            symbol=a.symbol,
            asset_type=a.asset_type,
            allocation_pct=pct,
            rationale=_asset_rationale(a, tier, pct),
        )
        for a, pct in zip(signal.assets, pcts)
    ]

    g = (goals or "general wealth building").strip()[:120]
    h = (horizon or "a timeline you haven't set yet").strip()

    return RecommendationResponse(
        summary=TIER_SUMMARY_TEMPLATE.get(tier, TIER_SUMMARY_TEMPLATE["moderate"]).format(
            goals=g, horizon=h
        ),
        risk_explanation=TIER_RISK_EXPLANATION.get(tier, TIER_RISK_EXPLANATION["moderate"]),
        assets=assets,
        plain_english_reasoning=TIER_PLAIN_ENGLISH.get(tier, TIER_PLAIN_ENGLISH["moderate"]),
    )


def _parse_json_loose(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        text = m.group(0)
    return json.loads(text)


def _check_consistency(rec: RecommendationResponse, signal: MarketSignal) -> list[str]:
    """Automated drift check between the numeric allocation and what the
    rationale text claims, plus a check that no asset's rationale cites
    news it was never actually given. Guards against drift within a single
    generation and would equally catch drift if this pipeline is later
    split into separate allocation/rationale calls."""
    issues: list[str] = []
    candidate_symbols = {a.symbol for a in signal.assets}
    signal_by_symbol = {a.symbol: a for a in signal.assets}

    total = sum(a.allocation_pct for a in rec.assets)
    if not (90.0 <= total <= 110.0):
        issues.append(f"allocations sum to {total:.1f}%, expected ~100%")

    for asset in rec.assets:
        if asset.symbol not in candidate_symbols:
            issues.append(f"{asset.symbol}: not in the candidate set given to the model")
            continue

        if asset.symbol.lower() not in asset.rationale.lower():
            issues.append(f"{asset.symbol}: rationale doesn't mention its own symbol")

        for pct in (float(m) for m in _PCT_PATTERN.findall(asset.rationale)):
            if abs(pct - asset.allocation_pct) > 2.0:
                issues.append(
                    f"{asset.symbol}: rationale states {pct:.0f}% but "
                    f"allocation_pct is {asset.allocation_pct:.1f}%"
                )

        sig = signal_by_symbol.get(asset.symbol)
        if sig is not None and sig.sentiment is None:
            low = asset.rationale.lower()
            if any(p in low for p in _NEWS_CLAIM_PHRASES):
                issues.append(
                    f"{asset.symbol}: rationale appears to cite news, but no "
                    "real news was available for it (possible fabrication)"
                )

    return issues


def _log_consistency_issues(issues: list[str], *, source: str) -> None:
    if not issues:
        logger.info("consistency check passed (%s path).", source)
        return
    for issue in issues:
        logger.warning("rationale/allocation consistency issue (%s path): %s", source, issue)


def _check_jargon(rec: RecommendationResponse) -> None:
    text = " ".join(
        [rec.summary, rec.risk_explanation, rec.plain_english_reasoning]
        + [a.rationale for a in rec.assets]
    ).lower()
    hits = [term for term in JARGON_TERMS if term in text]
    if hits:
        logger.warning(
            "rationale may contain unexplained jargon: %s — review for beginner readability.",
            ", ".join(hits),
        )


async def generate_recommendation(
    risk_tolerance: str,
    investment_goals: str | None,
    time_horizon: str | None,
    market_context: str,
) -> RecommendationResponse:
    settings = get_settings()
    signal = await _gather_market_signal(risk_tolerance)
    news_with_signal = sum(1 for a in signal.assets if a.sentiment is not None)
    logger.info(
        "market signal gathered for tier=%s: news=%s sentiment=%s (%d/%d candidates have news)",
        signal.tier,
        "real" if signal.used_real_news else "mock",
        "real" if signal.used_real_sentiment else "mock",
        news_with_signal, len(signal.assets),
    )

    if not settings.openai_api_key:
        logger.warning(
            "mock fallback (no OPENAI_API_KEY) — building recommendation rationale from "
            "template + %s news signal.",
            "real" if signal.used_real_news else "mock",
        )
        rec = _mock_recommendation(risk_tolerance, investment_goals, time_horizon, signal)
        _log_consistency_issues(_check_consistency(rec, signal), source="mock")
        _check_jargon(rec)
        return rec

    candidate_list = ", ".join(f"{a.symbol} ({a.asset_type})" for a in signal.assets)
    tone = TONE_GUIDANCE.get(signal.tier, TONE_GUIDANCE["moderate"])
    user_prompt = f"""User profile:
- Risk tolerance: {risk_tolerance}
- Investment goals: {investment_goals or 'not specified'}
- Time horizon: {time_horizon or 'not specified'}

Tone guidance: {tone}

Candidate symbols for this risk tier (recommend a portfolio using ONLY these): {candidate_list}

Recent market signal for each candidate:
{signal.block()}

General market context: {market_context}

Build a diversified portfolio from the candidate symbols above (allocations summing to ~100%). Write the summary and reasoning so they clearly reflect this user's stated goals and time horizon."""

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
        raw = resp.choices[0].message.content or "{}"
        data = _parse_json_loose(raw)
        assets = [RecommendedAsset(**a) for a in data.get("assets", [])]
        if not assets:
            raise ValueError("LLM returned no assets")

        rec = RecommendationResponse(
            summary=data.get("summary", ""),
            risk_explanation=data.get("risk_explanation", ""),
            plain_english_reasoning=data.get("plain_english_reasoning", ""),
            assets=assets,
        )
        logger.info(
            "real LLM success — recommendation generated for tier=%s via %s (news=%s, sentiment=%s).",
            signal.tier, MODEL,
            "real" if signal.used_real_news else "mock",
            "real" if signal.used_real_sentiment else "mock",
        )

        issues = _check_consistency(rec, signal)
        if issues:
            _log_consistency_issues(issues, source="LLM")
            logger.warning(
                "falling back to templated recommendation for tier=%s — LLM output "
                "failed the rationale/allocation consistency check.",
                signal.tier,
            )
            rec = _mock_recommendation(risk_tolerance, investment_goals, time_horizon, signal)
        else:
            _log_consistency_issues(issues, source="LLM")

        _check_jargon(rec)
        return rec
    except Exception as exc:
        logger.error(
            "mock fallback (API error: %s: %s) — recommendation LLM call failed for "
            "tier=%s; using template + %s news signal instead.",
            type(exc).__name__, exc, signal.tier,
            "real" if signal.used_real_news else "mock",
        )
        rec = _mock_recommendation(risk_tolerance, investment_goals, time_horizon, signal)
        _log_consistency_issues(_check_consistency(rec, signal), source="mock (LLM error)")
        _check_jargon(rec)
        return rec
