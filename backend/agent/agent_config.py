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

# --- Obvious/non-obvious classification & split ratios ---
# Added in a later prompt. Nothing lives here yet — this section is a
# placeholder so that prompt extends this file (Gate A/B/C thresholds
# from docs/DISCOVERY_DESIGN.md §3, and the per-tier obvious/non-obvious
# ratios from §5) instead of creating a second config module for the
# same feature family.
