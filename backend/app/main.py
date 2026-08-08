"""
britney.ai FastAPI application entrypoint.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import bootstrap_schema
from app.routes import (
    chat,
    dashboard,
    favorites,
    health,
    onboarding,
    portfolios,
    recommendations,
    settings as settings_routes,
    stocks,
    trading,
    users,
)

# Without this, nothing configures the root logger in this process, so
# every logger.info/warning/error call anywhere in the app (including
# agent/sentiment.py and agent/news_ingestion.py's own "real vs mock"
# logging, now also exercised here via the recommendation engine) is
# silently dropped — those modules' loud logging has only ever actually
# been visible when run through agent/run_agent.py's own
# _configure_logging(), a separate process from this one. uvicorn's own
# LOGGING_CONFIG has disable_existing_loggers=False, so this doesn't
# conflict with uvicorn's access/error log setup applied after import.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.auth_disabled and not settings.firebase_credentials_path:
        logging.getLogger(__name__).warning(
            "Production auth: set FIREBASE_CREDENTIALS_PATH or AUTH_DISABLED=true for dev."
        )
    bootstrap_schema()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(users.router)
    app.include_router(onboarding.router)
    app.include_router(dashboard.router)
    app.include_router(portfolios.router)
    app.include_router(recommendations.router)
    app.include_router(chat.router)
    app.include_router(trading.router)
    app.include_router(settings_routes.router)
    app.include_router(stocks.router)
    app.include_router(favorites.router)
    return app


app = create_app()
