"""Shared pytest fixtures.

Uses a throwaway SQLite file as DATABASE_URL (same override local dev
already relies on, per docs/OVERVIEW.md) and AUTH_DISABLED=true so the
test client can simulate distinct users just by sending distinct bearer
tokens — see app/services/firebase_auth.py's per-token dev uid under
AUTH_DISABLED. Env vars must be set before app.main is imported, since
app/config.py's get_settings() is lru_cache'd on first call and
app/database.py builds its engine from settings at import time.
"""

import os
import tempfile

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["AUTH_DISABLED"] = "true"
os.environ.setdefault("CORS_ORIGINS", "http://localhost")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """One TestClient (and one backing SQLite file) for the whole test
    session — tests are written to only assert on objects they create
    themselves, not on the DB being empty."""
    with TestClient(app) as c:
        yield c
    os.remove(_db_path)
