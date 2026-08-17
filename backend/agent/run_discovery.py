"""
Scheduler entrypoint for the trading agent's general-news discovery
pipeline — a separate, independent process from agent/run_agent.py
(fixed-ticker news + trading), agent/run_rebalance.py (daily target-
allocation top-up), and agent/run_auto_invest.py (recurring user
$-amount buys). Ties together agent/discovery.py's fetch -> extract ->
validate steps and persists validated candidates via
app/services/discovery_service.py.

Deliberately does NOT trade, score sentiment, or touch
REBALANCE_TARGET_WEIGHTS — this pipeline's only output is rows in the
discovered_candidates table (app/models/discovered_candidate.py). Later
prompts read from that table; see docs/DISCOVERY_DESIGN.md for the full
design this and future prompts implement.

Runs on its own cadence (agent/agent_config.py's DISCOVERY_POLL_MINUTES,
default 60) rather than run_agent.py's 3-min ticker poll — market-wide
headlines don't need that freshness (see docs/DISCOVERY_DESIGN.md §4).

Shaped like agent/run_rebalance.py rather than run_agent.py/
run_auto_invest.py's Restart=always services: on the droplet,
backend/deploy/britney-discovery.timer is what actually fires this
(systemd's own hourly calendar scheduling, `--once` each time) — the
same reasoning run_rebalance.py's module docstring gives for its own
timer: a timer survives a droplet reboot/deploy restart more simply
than a process that has to stay resident just to wake once an hour.
run_forever() (no args) still works standalone for local dev/testing
without systemd.

    cd backend && python -m agent.run_discovery             # run forever, hourly
    cd backend && python -m agent.run_discovery --once       # one cycle now, then exit
"""

from __future__ import annotations

import asyncio
import logging
import sys

from agent import agent_config
from agent.discovery import (
    DISCOVERY_CURSOR_DB_PATH,
    DISCOVERY_SEEN_DB_PATH,
    MinIdCursorStore,
    extract_companies_batch,
    fetch_general_news,
    validate_candidate,
)
from agent.news_ingestion import SeenArticleStore
from app.database import SessionLocal, bootstrap_schema
from app.services import discovery_service

logger = logging.getLogger("agent.run_discovery")


def _configure_logging() -> None:
    """Stdout logging only — same reasoning as run_auto_invest.py's/
    run_rebalance.py's own _configure_logging: under systemd that's
    captured by journald for free, no AGENT_LOG_FILE-style rotation
    needed for an hourly job."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def _run_cycle() -> None:
    """One full discovery cycle: fetch unseen general news, extract
    company mentions, hard-validate each, and persist validated
    candidates. Never lets one bad article crash the whole cycle — each
    article is handled in its own try/except, same discipline
    run_agent.py/run_auto_invest.py already apply per-portfolio/
    per-schedule.

    Marks an article seen (agent/discovery.py's SeenArticleStore) only
    once it has been fully handled — extracted-with-no-company,
    ticker-dedup-skipped, rejected, or validated all count as "fully
    handled"; an unexpected exception does not, so that article is
    retried next cycle instead of silently lost. The minId cursor
    advances only if EVERY article in this cycle's batch was fully
    handled — see MinIdCursorStore's docstring for why a partial batch
    must not advance it.
    """
    db = SessionLocal()
    cursor_store = MinIdCursorStore(db_path=DISCOVERY_CURSOR_DB_PATH)
    seen_store = SeenArticleStore(db_path=DISCOVERY_SEEN_DB_PATH)
    try:
        min_id = cursor_store.get()
        articles, max_id_seen = await fetch_general_news(min_id, store=seen_store)

        if not articles:
            logger.info("No new general-news articles since min_id=%d.", min_id)
            return

        try:
            extractions = await extract_companies_batch(articles)
        except Exception:
            logger.exception(
                "Extraction crashed for this batch of %d article(s) — cursor not "
                "advanced, all will be re-fetched and retried next cycle.",
                len(articles),
            )
            return

        validated = rejected = skipped_no_company = skipped_dedup = 0
        all_processed = True

        for article, extraction in zip(articles, extractions):
            try:
                if not extraction.ticker_guess:
                    logger.info(
                        "no company extracted for article_id=%s headline=%r",
                        article.article_id, article.headline,
                    )
                    skipped_no_company += 1
                    seen_store.mark_seen(article.article_id)
                    continue

                ticker = extraction.ticker_guess.strip().upper()

                if discovery_service.was_ticker_discovered_today(db, ticker):
                    logger.info(
                        "ticker-level dedup: %s already discovered today — "
                        "skipping validation for article_id=%s.",
                        ticker, article.article_id,
                    )
                    skipped_dedup += 1
                    seen_store.mark_seen(article.article_id)
                    continue

                result = await validate_candidate(ticker, extraction.company_name)
                if not result.passed:
                    rejected += 1
                    seen_store.mark_seen(article.article_id)
                    continue

                candidate = discovery_service.create_discovered_candidate(
                    db,
                    ticker=result.ticker,
                    company_name=(
                        extraction.company_name or result.quote_company_name or result.ticker
                    ),
                    source_article_id=article.article_id,
                    source_headline=article.headline,
                    confidence=extraction.confidence,
                )
                logger.info(
                    "VALIDATED candidate id=%s ticker=%s company=%r confidence=%.2f "
                    "source=%s (%r)",
                    candidate.id, candidate.ticker, candidate.company_name,
                    candidate.confidence, article.article_id, article.headline,
                )
                validated += 1
                seen_store.mark_seen(article.article_id)
            except Exception:
                logger.exception(
                    "Unexpected failure processing article_id=%s — left unmarked, "
                    "will retry next cycle.",
                    article.article_id,
                )
                all_processed = False
                continue

        if all_processed:
            cursor_store.set(max_id_seen)
        else:
            logger.warning(
                "Not all articles in this cycle processed cleanly — cursor stays at "
                "%d (not advanced to %d) so the failed article(s) are retried next "
                "cycle.",
                min_id, max_id_seen,
            )

        logger.info(
            "cycle complete: %d article(s) considered — %d validated, %d rejected, "
            "%d no-company, %d ticker-dedup-skipped.",
            len(articles), validated, rejected, skipped_no_company, skipped_dedup,
        )
    finally:
        db.close()
        cursor_store.close()
        seen_store.close()


async def run_forever() -> None:
    while True:
        try:
            await _run_cycle()
        except Exception:
            logger.exception("Discovery cycle crashed unexpectedly")

        planned_sleep = agent_config.DISCOVERY_POLL_MINUTES * 60
        logger.info("Sleeping %d min", agent_config.DISCOVERY_POLL_MINUTES)
        await asyncio.sleep(planned_sleep)


def _main() -> None:
    _configure_logging()
    logger.info("Ensuring database schema exists (create_all + column sync)")
    bootstrap_schema()

    once = "--once" in sys.argv[1:]
    if once:
        asyncio.run(_run_cycle())
    else:
        asyncio.run(run_forever())


if __name__ == "__main__":
    _main()
