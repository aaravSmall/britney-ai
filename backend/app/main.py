"""
britney.ai FastAPI application entrypoint.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import Base, engine
from app.routes import (
    chat,
    dashboard,
    health,
    onboarding,
    recommendations,
    settings as settings_routes,
    trading,
    users,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.auth_disabled and not settings.firebase_credentials_path:
        import logging

        logging.getLogger(__name__).warning(
            "Production auth: set FIREBASE_CREDENTIALS_PATH or AUTH_DISABLED=true for dev."
        )
    Base.metadata.create_all(bind=engine)
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
    app.include_router(recommendations.router)
    app.include_router(chat.router)
    app.include_router(trading.router)
    app.include_router(settings_routes.router)
    return app


app = create_app()
