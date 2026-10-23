from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, PostTransaction, get_session

router = APIRouter(prefix="", tags=["anomalies"])


class AnomalyRecord(BaseModel):
    anomaly_type: str
    description: str
    timestamp: datetime


class AnomaliesResponse(BaseModel):
    trace_id: str
    store_id: str
    anomalies: list[AnomalyRecord]
    timestamp: datetime


@router.get("/stores/{store_id}/anomalies", response_model=AnomaliesResponse)
async def get_anomalies(
    request: Request,
    store_id: str,
    session: AsyncSession = Depends(get_session),
) -> AnomaliesResponse:
    trace_id = request.state.trace_id
    now = datetime.now(timezone.utc)

    queue_joins_count = await session.scalar(
        select(func.count()).where(
            EventRecord.store_id == store_id,
            EventRecord.is_staff.is_(False),
            EventRecord.event_type == "BILLING_QUEUE_JOIN",
            EventRecord.timestamp >= now - timedelta(minutes=15),
        )
    )

    anomalies: list[AnomalyRecord] = []

    if queue_joins_count and queue_joins_count >= 5:
        anomalies.append(
            AnomalyRecord(
                anomaly_type="QUEUE_SPIKE",
                description=(
                    "High billing queue activity detected in the last 15 minutes."
                ),
                timestamp=now,
            )
        )

    total_billing = await session.scalar(
        select(func.count()).where(
            EventRecord.store_id == store_id,
            EventRecord.is_staff.is_(False),
            EventRecord.event_type == "BILLING_QUEUE_JOIN",
        )
    )
    if total_billing and total_billing >= 5:
        billing_events = await session.execute(
            select(EventRecord.visitor_id, EventRecord.timestamp)
            .where(
                EventRecord.store_id == store_id,
                EventRecord.is_staff.is_(False),
                EventRecord.event_type == "BILLING_QUEUE_JOIN",
            )
        )
        pos_events = await session.execute(
            select(PostTransaction.timestamp)
            .where(PostTransaction.store_id == store_id)
        )
        pos_timestamps = [row.timestamp for row in pos_events]

        purchased = 0
        for _, billing_ts in billing_events:
            if any(0 <= (pos_ts - billing_ts).total_seconds() <= 300 for pos_ts in pos_timestamps):
                purchased += 1

        conversion_rate = float(purchased / total_billing) if total_billing else 0.0
        if conversion_rate < 0.2:
            anomalies.append(
                AnomalyRecord(
                    anomaly_type="CONVERSION_DROP",
                    description=(
                        "Conversion rate is low compared to billing queue activity."
                    ),
                    timestamp=now,
                )
            )

    zone_counts = await session.execute(
        select(EventRecord.zone_id, func.count()).where(
            EventRecord.store_id == store_id,
            EventRecord.is_staff.is_(False),
            EventRecord.zone_id.isnot(None),
        ).group_by(EventRecord.zone_id)
    )
    zone_map = {row[0]: row[1] for row in zone_counts}
    dead_zones = [zone for zone, count in zone_map.items() if count < 2]
    for zone_id in dead_zones:
        anomalies.append(
            AnomalyRecord(
                anomaly_type="DEAD_ZONE",
                description=f"Zone {zone_id} has very low activity.",
                timestamp=now,
            )
        )

    return AnomaliesResponse(
        trace_id=trace_id,
        store_id=store_id,
        anomalies=anomalies,
        timestamp=now,
    )
