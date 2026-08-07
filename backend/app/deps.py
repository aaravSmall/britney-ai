"""FastAPI dependencies: DB session and current user."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.firebase_auth import verify_id_token


def resolve_current_user(db: Session, authorization: str | None) -> User:
    """Core of get_current_user, factored out so routes that only need
    auth conditionally (e.g. a resource that's public for some rows and
    owner-only for others) can call it directly instead of forcing auth
    on every request via a Depends()."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.removeprefix("Bearer ").strip()
    claims = verify_id_token(token)
    uid = claims.get("uid")
    email = claims.get("email") or "unknown@local"
    name = claims.get("name")

    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        user = User(firebase_uid=uid, email=email, name=name)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    return resolve_current_user(db, authorization)


def get_optional_current_user(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Same as get_current_user, but returns None instead of raising when
    no Authorization header is present at all (a bad/expired header still
    raises 401 — only *absence* of one is treated as "anonymous")."""
    if authorization is None:
        return None
    return resolve_current_user(db, authorization)


DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]
