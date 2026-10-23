from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, PostTransaction, get_session

router = APIRouter(prefix="", tags=["metrics"])


class MetricsResponse(BaseModel):
    trace_id: str
    store_id: str
    unique_visitors: int
    total_entries: int
    total_events: int
    total_purchases: int
    conversion_rate: float
    average_dwell_ms: int
    queue_joins: int
    queue_abandons: int
    timestamp: datetime


@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
async def get_metrics(
    request: Request,
    store_id: str,
    session: AsyncSession = Depends(get_session),
) -> MetricsResponse:
    trace_id = request.state.trace_id

    visitor_query = select(func.count(func.distinct(EventRecord.visitor_id))).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
    )
    total_entries_query = select(func.count()).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
        EventRecord.event_type.in_(["ENTRY", "REENTRY"]),
    )
    total_events_query = select(func.count()).where(
        EventRecord.store_id == store_id,
    )
    avg_dwell_query = select(func.coalesce(func.avg(EventRecord.dwell_ms), 0)).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
        EventRecord.event_type == "ZONE_DWELL",
    )
    queue_joins_query = select(func.count()).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
        EventRecord.event_type == "BILLING_QUEUE_JOIN",
    )
    queue_abandons_query = select(func.count()).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
        EventRecord.event_type == "BILLING_QUEUE_ABANDON",
    )

    visitor_count = (await session.scalar(visitor_query)) or 0
    total_entries = (await session.scalar(total_entries_query)) or 0
    total_events = (await session.scalar(total_events_query)) or 0
    average_dwell_ms = int((await session.scalar(avg_dwell_query)) or 0)
    queue_joins = (await session.scalar(queue_joins_query)) or 0
    queue_abandons = (await session.scalar(queue_abandons_query)) or 0

    # POS correlation: count unique visitors with a billing join within 5 minutes before any POS transaction.
    billing_events = await session.execute(
        select(EventRecord.visitor_id, EventRecord.timestamp)
        .where(
            EventRecord.store_id == store_id,
            EventRecord.is_staff.is_(False),
            EventRecord.zone_id == "BILLING",
        )
    )
    billing_rows = billing_events.all()

    pos_rows = await session.execute(
        select(PostTransaction.timestamp)
        .where(PostTransaction.store_id == store_id)
    )
    pos_timestamps = [row.timestamp for row in pos_rows]

    purchased_visitors: set[str] = set()
    for visitor_id, billing_ts in billing_rows:
        # Normalize to UTC-aware (SQLite returns naive datetimes)
        if billing_ts.tzinfo is None:
            billing_ts = billing_ts.replace(tzinfo=timezone.utc)
        for pos_ts in pos_timestamps:
            if pos_ts.tzinfo is None:
                pos_ts = pos_ts.replace(tzinfo=timezone.utc)
            if 0 <= (pos_ts - billing_ts).total_seconds() <= 300:
                purchased_visitors.add(visitor_id)
                break

    total_purchases = len(purchased_visitors)
    conversion_rate = float(total_purchases / visitor_count) if visitor_count else 0.0

    return MetricsResponse(
        trace_id=trace_id,
        store_id=store_id,
        unique_visitors=visitor_count,
        total_entries=total_entries,
        total_events=total_events,
        total_purchases=total_purchases,
        conversion_rate=conversion_rate,
        average_dwell_ms=average_dwell_ms,
        queue_joins=queue_joins,
        queue_abandons=queue_abandons,
        timestamp=datetime.now(timezone.utc),
    )
