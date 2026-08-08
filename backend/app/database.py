"""SQLAlchemy engine and session factory."""

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

logger = logging.getLogger("app.database")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def bootstrap_schema() -> None:
    """Create any missing tables, then patch any missing columns onto
    tables that already exist.

    There's no Alembic (or any migration tool) in this project yet —
    schema changes have only ever been applied via
    `Base.metadata.create_all()`, which creates whole tables that don't
    exist but silently does nothing when an *existing* table's model
    gains a new column. The 2026-08-04 migration that added
    Portfolio.owner_type/risk_tolerance hit exactly that gap: every
    database created before that commit (local dev.db included) kept its
    old `portfolios` schema, so the agent's first post-migration query
    for owner_type="agent" rows found nothing, silently created brand
    new portfolios at the default $10k cash balance, and orphaned the
    old rows (with real trade history) with no code path left pointing
    at them. That's indistinguishable from "the agent's balance and
    trades just vanished" from the outside.

    This adds each missing column as ALTER TABLE ... ADD COLUMN, so
    existing databases (and their trade history) survive future model
    changes instead of needing to be dropped and recreated. NOT NULL
    columns get their model-level scalar default inlined into the ALTER
    (both SQLite and Postgres require a default to backfill existing
    rows for a NOT NULL column); columns with no usable scalar default
    are added as nullable rather than failing the whole bootstrap.
    """
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue

                ddl_type = column.type.compile(dialect=engine.dialect)
                clause = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'

                if not column.nullable:
                    default = None
                    if column.default is not None and column.default.is_scalar:
                        default = column.default.arg
                    if default is not None:
                        literal = f"'{default}'" if isinstance(default, str) else str(default)
                        clause += f" NOT NULL DEFAULT {literal}"
                        logger.warning(
                            "Adding missing column %s.%s (backfilled with default %r)",
                            table.name,
                            column.name,
                            default,
                        )
                    else:
                        logger.warning(
                            "Adding missing NOT NULL column %s.%s with no usable default — "
                            "adding as nullable instead",
                            table.name,
                            column.name,
                        )
                else:
                    logger.warning("Adding missing column %s.%s", table.name, column.name)

                conn.execute(text(clause))
