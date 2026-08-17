"""Single source of truth for constants shared across the "non-obvious
stock discovery" feature family — the general-news discovery pipeline
built here, plus the obvious/non-obvious classification and per-tier
split-ratio work staged in later prompts. See docs/DISCOVERY_DESIGN.md
for the full design investigation this implements.

Deliberately a separate module from app/config.py's Settings (env-var-
backed request/deployment config) — everything here is a plain code
constant, not a secret or per-environment value, so it doesn't need
pydantic-settings/`.env` plumbing. Future prompts in this feature family
should extend this file rather than creating a second config module, so
there's one place to look for "why is the cadence/threshold/ratio X."
"""

from __future__ import annotations

# --- Discovery pipeline (agent/discovery.py, agent/run_discovery.py) ---

# How often the general-news discovery cycle runs, in minutes. Decoupled
# from agent/run_agent.py's 3-minute fixed-ticker poll on purpose:
# market-wide headlines don't need that freshness the way a ticker-
# specific reaction does, and running discovery this much slower keeps
# its added Finnhub/Yahoo call volume a small fraction of what the
# existing ticker poll already generates. See docs/DISCOVERY_DESIGN.md
# §4 for the cost/reliability reasoning behind this number.
DISCOVERY_POLL_MINUTES = 60

# Articles per GPT-4o-mini extraction call — same batching convention as
# agent/sentiment.py's DEFAULT_BATCH_SIZE (keeps each request's prompt
# manageable and bounds how much of a batch is lost/retried if one
# call's response fails to parse).
DISCOVERY_EXTRACTION_BATCH_SIZE = 10

# --- Obvious/non-obvious classification (agent/classification.py) ---
# See docs/DISCOVERY_DESIGN.md §3 for the reasoning behind each gate.
# "Obvious" = Gate A AND Gate B AND Gate C, all three.

# Gate A — price growth (consistency, not just direction). Trailing
# window in weeks, and the minimum fraction of week-over-week deltas
# that must be non-negative across that window.
GROWTH_LOOKBACK_WEEKS = 13
GROWTH_MIN_POSITIVE_WEEK_PCT = 0.60

# Real Yahoo history can return fewer candles than requested (recent
# IPO, a data gap, a holiday-heavy quarter) — this is a data-sufficiency
# floor, not a design choice from the investigation itself: below this
# many weekly candles, Gate A/C fail closed ("insufficient price
# history") rather than compute a ratio off too little data to mean
# anything.
GROWTH_MIN_WEEKLY_CANDLES = 8

# Gate B — article volume as a brand-recognition proxy: minimum
# Finnhub /company-news article count in the trailing window below.
ARTICLE_VOLUME_MIN = 15
ARTICLE_VOLUME_LOOKBACK_DAYS = 30

# Gate C — volatility, mandatory and independent of A/B (a heavily-
# covered, consistently-up stock can still be too volatile to call
# "safe" — see docs/DISCOVERY_DESIGN.md §3's Tesla/Nvidia example).
# VOLATILITY_MAX_VS_VOO is a ratio against VOO's OWN trailing
# volatility, recomputed fresh every classification run (see
# agent/classification.py) rather than hardcoded, since VOO's
# volatility drifts over time too.
VOLATILITY_MAX_VS_VOO = 1.5
VOLATILITY_MAX_WEEKLY_MOVE = 0.15

# --- Per-tier obvious/non-obvious split (agent/classification.py,
# app/services/rebalance_service.py) ---
# Of each tier's INVESTED (non-cash) portion — cash targets are
# unchanged (see rebalance_service.TIER_CASH_WEIGHT). KNOWN PROVISIONAL:
# these ratios were assigned by design judgment (docs/DISCOVERY_DESIGN.md
# §5: conservative gets the smallest satellite slice, aggressive the
# largest, matching each tier's existing risk posture) rather than
# backtested — expect these to be revisited once there's real
# performance history for the non-obvious buckets to judge them against.
SPLIT_RATIOS: dict[str, dict[str, float]] = {
    "conservative": {"obvious": 0.90, "non_obvious": 0.10},
    "moderate": {"obvious": 0.75, "non_obvious": 0.25},
    "aggressive": {"obvious": 0.55, "non_obvious": 0.45},
}

# Max concurrent non-obvious positions held at once per tier — caps
# how many routed candidates share a tier's non-obvious envelope
# (equal-weighted below the cap; extra candidates beyond it, ranked by
# discovery confidence, simply wait for a future cycle rather than
# further diluting position size). Deliberately NOT the same number for
# all three tiers:
#   - conservative=2: this tier's whole point is to stay simple and
#     low-risk; a couple of well-vetted satellite names (whatever
#     survives the tightest volatility band, see
#     NON_OBVIOUS_VOL_BAND_CONSERVATIVE_MAX) is more in keeping with
#     that posture than diffusing its already-small 10% slice across a
#     wide basket.
#   - moderate=3: a middle ground — enough to diversify a single
#     speculative pick's idiosyncratic risk without turning this tier
#     into a basket strategy.
#   - aggressive=5: this tier already carries this app's only crypto
#     exposure and the widest (unbounded-above) volatility band, so it's
#     the one tier where running a larger, more diversified basket of
#     speculative names is consistent with its existing risk budget
#     rather than a departure from it.
MAX_CONCURRENT_NON_OBVIOUS: dict[str, int] = {
    "conservative": 2,
    "moderate": 3,
    "aggressive": 5,
}

# --- Non-obvious tier routing bands (agent/classification.py) ---
# A non-obvious candidate's OWN trailing volatility, expressed as a
# ratio against VOO's trailing volatility (the same ratio Gate C already
# computes — reused here, not recomputed), determines which single tier
# it's routed into. Contiguous, non-overlapping partition (see
# agent/classification.py's route_non_obvious() docstring for why
# exactly one tier rather than multiple): conservative only accepts
# names calm enough that they'd have cleared Gate C's own 1.5x-VOO
# volatility bar (i.e. they're non-obvious only because they failed
# Gate A's growth-consistency or Gate B's article-volume bar, not
# because they're wild); moderate is genuine growth/small-cap
# volatility territory; aggressive is unbounded above, matching it
# already being this app's highest-risk-budget tier.
NON_OBVIOUS_VOL_BAND_CONSERVATIVE_MAX = 1.5  # ratio <= this -> conservative
NON_OBVIOUS_VOL_BAND_MODERATE_MAX = 3.0  # this < ratio <= this -> moderate; above -> aggressive
