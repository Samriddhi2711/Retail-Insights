from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base

# ---------------------------------------------------------------------------
# Dual-mode database configuration
#
# DOCKER MODE  : DATABASE_URL env var is set to postgresql+asyncpg://...@db:...
#                by docker-compose.yml — used as-is.
# LOCAL MODE   : DATABASE_URL not set, or is the docker-default with host "db".
#                Fall back to SQLite so `uvicorn app.main:app --reload` works
#                on a developer machine without a running Postgres container.
# ---------------------------------------------------------------------------

_RAW_DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "",
)

_SQLITE_FALLBACK: str = "sqlite+aiosqlite:///./store_intelligence.db"

# Detect the "unset / docker-only" case: if the URL is empty or uses the
# Docker-internal hostname "db" (which is unreachable on the host), swap to
# SQLite automatically.
def _resolve_database_url(raw: str) -> str:
    """Return the effective database URL for this environment.

    Rules:
    - Empty string → SQLite fallback (env var was not set).
    - URL contains ``@db:`` (Docker-internal host) AND we are *not* inside a
      container (``/proc/1/cgroup`` check) → SQLite fallback.
    - Otherwise use the provided URL verbatim.
    """
    if not raw:
        return _SQLITE_FALLBACK

    # If the URL targets the Docker-internal hostname "db", check whether we
    # are actually running inside Docker.  If not, fall back to SQLite.
    if "@db:" in raw or "@db/" in raw:
        # Heuristic: inside Docker the file /proc/1/cgroup exists and is readable.
        in_docker = Path("/proc/1/cgroup").exists()
        if not in_docker:
            return _SQLITE_FALLBACK

    return raw


DATABASE_URL: str = _resolve_database_url(_RAW_DATABASE_URL)

POS_TRANSACTIONS_CSV = os.getenv(
    "POS_TRANSACTIONS_CSV",
    "Brigade_Bangalore_10_April_26 (1)bc6219c.csv",
)

# SQLite requires check_same_thread=False via connect_args
_engine_kwargs: dict = {"future": True, "echo": False}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class EventRecord(Base):
    __tablename__ = "events"

    event_id = Column(String(36), primary_key=True)
    store_id = Column(String, nullable=False, index=True)
    camera_id = Column(String, nullable=False)
    visitor_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    zone_id = Column(String, nullable=True, index=True)
    dwell_ms = Column(Integer, nullable=True)
    is_staff = Column(Boolean, nullable=False, index=True)
    confidence = Column(Float, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class PostTransaction(Base):
    __tablename__ = "pos_transactions"
    __table_args__ = (UniqueConstraint("invoice_number", "store_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String, nullable=False)
    store_id = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    total_amount = Column(Float, nullable=True)
    sku = Column(String, nullable=True)
    brand_name = Column(String, nullable=True)
    product_name = Column(String, nullable=True)
    salesperson_name = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


async def init_db() -> None:
    """Create database schema and optionally seed POS transactions."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    if POS_TRANSACTIONS_CSV:
        csv_path = Path(POS_TRANSACTIONS_CSV)
        if csv_path.exists():
            await load_pos_transactions(csv_path)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async SQLAlchemy session."""
    async with AsyncSessionLocal() as session:
        yield session


async def load_pos_transactions(path: Path) -> None:
    """Load POS transactions from the configured CSV into the database."""
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        existing_count = await session.scalar(
            select(func.count()).select_from(PostTransaction)
        )
        if existing_count and existing_count > 0:
            return

        rows_to_insert = []
        seen_invoices: set[tuple[str, str]] = set()

        with path.open("r", encoding="utf-8", errors="ignore") as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                order_date = row.get("order_date")
                order_time = row.get("order_time")

                if not order_date or not order_time:
                    continue

                try:
                    timestamp = datetime.strptime(
                        f"{order_date} {order_time}",
                        "%d-%m-%Y %H:%M:%S",
                    )
                except ValueError:
                    continue

                try:
                    total_amount = float(
                        row.get("total_amount", "0") or 0
                    )
                except ValueError:
                    total_amount = 0.0

                invoice_number = (
                    row.get("invoice_number", "") or ""
                ).strip()

                store_id = (
                    row.get("store_id", "") or ""
                ).strip()
                if store_id == "ST1008":
                    store_id = "STORE_BLR_002"

                # Skip invalid rows
                if not invoice_number or not store_id:
                    continue

                # Deduplicate CSV rows
                key = (invoice_number, store_id)

                if key in seen_invoices:
                    continue

                seen_invoices.add(key)

                rows_to_insert.append(
                    PostTransaction(
                        invoice_number=invoice_number,
                        store_id=store_id,
                        timestamp=timestamp,
                        total_amount=total_amount,
                        sku=row.get("sku"),
                        brand_name=row.get("brand_name"),
                        product_name=row.get("product_name"),
                        salesperson_name=row.get("salesperson_name"),
                    )
                )

        if rows_to_insert:
            session.add_all(rows_to_insert)
            await session.commit()