from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["health"])


class HealthResponse(BaseModel):
    trace_id: str
    status: str
    db: str
    last_event_age_seconds: int | None
    stale_feed: bool
    timestamp: datetime


@router.get("/health", response_model=HealthResponse)
async def health_check(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HealthResponse:
    trace_id = request.state.trace_id
    now = datetime.now(timezone.utc)
    db_status = "up"
    last_event_age_seconds: int | None = None
    stale_feed = False

    try:
        last_event = await session.execute(
            select(func.max(EventRecord.timestamp)).select_from(EventRecord)
        )
        last_timestamp = last_event.scalar_one_or_none()
        if last_timestamp is not None:
            # Handle timezone-naive datetimes from SQLite
            if last_timestamp.tzinfo is None:
                last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)
            last_event_age_seconds = int((now - last_timestamp).total_seconds())
            stale_feed = last_event_age_seconds > 600
    except Exception as e:
        logger.exception(f"Health check DB error: {e}", exc_info=e)
        db_status = "down"
        last_event_age_seconds = None
        stale_feed = True

    return HealthResponse(
        trace_id=trace_id,
        status="ok",
        db=db_status,
        last_event_age_seconds=last_event_age_seconds,
        stale_feed=stale_feed,
        timestamp=now,
    )
