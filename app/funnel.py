from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, PostTransaction, get_session

router = APIRouter(prefix="", tags=["funnel"])


class FunnelStage(BaseModel):
    stage: str
    value: int


class FunnelResponse(BaseModel):
    trace_id: str
    store_id: str
    stages: list[FunnelStage]
    timestamp: datetime


@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
async def get_funnel(
    request: Request,
    store_id: str,
    session: AsyncSession = Depends(get_session),
) -> FunnelResponse:
    trace_id = request.state.trace_id

    visitors_query = select(func.distinct(EventRecord.visitor_id)).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
    )
    visitor_rows = await session.execute(visitors_query)
    unique_visitors = {row[0] for row in visitor_rows}

    engaged_query = select(func.distinct(EventRecord.visitor_id)).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
        EventRecord.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
    )
    engaged_rows = await session.execute(engaged_query)
    engaged_visitors = {row[0] for row in engaged_rows}

    queued_query = select(func.distinct(EventRecord.visitor_id)).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
        EventRecord.event_type == "BILLING_QUEUE_JOIN",
    )
    queued_rows = await session.execute(queued_query)
    queued_visitors = {row[0] for row in queued_rows}

    billing_events = await session.execute(
        select(EventRecord.visitor_id, EventRecord.timestamp)
        .where(
            EventRecord.store_id == store_id,
            EventRecord.is_staff.is_(False),
            EventRecord.zone_id == "BILLING",
        )
    )
    pos_events = await session.execute(
        select(PostTransaction.timestamp)
        .where(PostTransaction.store_id == store_id)
    )

    pos_timestamps = [row.timestamp for row in pos_events]
    purchased_visitors: set[str] = set()
    for visitor_id, billing_ts in billing_events:
        if billing_ts.tzinfo is None:
            billing_ts = billing_ts.replace(tzinfo=timezone.utc)
        for pos_ts in pos_timestamps:
            if pos_ts.tzinfo is None:
                pos_ts = pos_ts.replace(tzinfo=timezone.utc)
            if 0 <= (pos_ts - billing_ts).total_seconds() <= 300:
                purchased_visitors.add(visitor_id)
                break

    stages = [
        FunnelStage(stage="unique_visitors", value=len(unique_visitors)),
        FunnelStage(stage="engaged", value=len(engaged_visitors)),
        FunnelStage(stage="queued", value=len(queued_visitors)),
        FunnelStage(stage="purchased", value=len(purchased_visitors)),
    ]

    return FunnelResponse(
        trace_id=trace_id,
        store_id=store_id,
        stages=stages,
        timestamp=datetime.now(timezone.utc),
    )
