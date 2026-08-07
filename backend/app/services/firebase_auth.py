"""Verify Firebase ID tokens; optional dev bypass when AUTH_DISABLED."""

import logging
from typing import Any

import firebase_admin
from firebase_admin import auth, credentials
from fastapi import HTTPException, status

from app.config import get_settings

logger = logging.getLogger(__name__)
_initialized = False


def _ensure_firebase() -> None:
    global _initialized
    if _initialized:
        return
    settings = get_settings()
    if settings.auth_disabled:
        _initialized = True
        return
    if not settings.firebase_credentials_path:
        logger.warning(
            "FIREBASE_CREDENTIALS_PATH not set; set AUTH_DISABLED=true for local dev"
        )
        return
    cred = credentials.Certificate(settings.firebase_credentials_path)
    firebase_admin.initialize_app(cred)
    _initialized = True


def verify_id_token(id_token: str) -> dict[str, Any]:
    """Returns decoded token claims including 'uid', 'email'."""
    settings = get_settings()
    if settings.auth_disabled:
        # Dev-only: trust client-sent mock (still require Authorization
        # header format). uid is derived from the token value itself
        # (not real verification) rather than a single fixed "dev-user",
        # so AUTH_DISABLED mode can still distinguish different callers —
        # needed to test/demo the owner-only 403 path locally without real
        # Firebase. The actual frontend demo mode always sends the same
        # fixed token (see auth_controller.dart's `demoToken`), so its
        # user identity is unaffected and stays stable across requests.
        _ensure_firebase()
        return {"uid": f"dev-{id_token}", "email": f"dev-{id_token}@britney.ai.local"}
    if not settings.firebase_credentials_path:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server auth not configured (FIREBASE_CREDENTIALS_PATH)",
        )
    _ensure_firebase()
    try:
        return auth.verify_id_token(id_token)
    except Exception as e:
        logger.exception("Firebase token verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e!s}",
        ) from e
