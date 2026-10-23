from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, get_session

router = APIRouter(prefix="", tags=["heatmap"])


class ZoneHeat(BaseModel):
    zone_id: str
    value: int
    dwell_ms: int
    event_count: int


class HeatmapResponse(BaseModel):
    trace_id: str
    store_id: str
    data_confidence: bool
    zones: list[ZoneHeat]
    timestamp: datetime


@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    request: Request,
    store_id: str,
    session: AsyncSession = Depends(get_session),
) -> HeatmapResponse:
    trace_id = request.state.trace_id

    query = select(
        EventRecord.zone_id,
        func.coalesce(func.sum(EventRecord.dwell_ms), 0),
        func.count(),
        func.coalesce(func.avg(EventRecord.confidence), 0),
    ).where(
        EventRecord.store_id == store_id,
        EventRecord.is_staff.is_(False),
        EventRecord.zone_id.isnot(None),
    ).group_by(EventRecord.zone_id)

    rows = await session.execute(query)
    rows = rows.all()

    if not rows:
        return HeatmapResponse(
            trace_id=trace_id,
            store_id=store_id,
            data_confidence=False,
            zones=[],
            timestamp=datetime.now(timezone.utc),
        )

    max_dwell = max(int(row[1]) for row in rows) or 1
    zone_list: list[ZoneHeat] = []
    for zone_id, dwell_ms, count, avg_confidence in rows:
        normalized = int((int(dwell_ms) / max_dwell) * 100)
        zone_list.append(
            ZoneHeat(
                zone_id=zone_id,
                value=normalized,
                dwell_ms=int(dwell_ms),
                event_count=int(count),
            )
        )

    avg_confidence = float(
        sum(float(row[3]) for row in rows) / len(rows)
    )
    data_confidence = avg_confidence >= 0.5 and len(rows) >= 1

    return HeatmapResponse(
        trace_id=trace_id,
        store_id=store_id,
        data_confidence=data_confidence,
        zones=zone_list,
        timestamp=datetime.now(timezone.utc),
    )
