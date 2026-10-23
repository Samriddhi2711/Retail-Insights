from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import EventRecord, get_session
from app.models import EventIn

router = APIRouter(prefix="", tags=["ingestion"])


# class IngestEventError(BaseModel):
#     event_index: int
#     errors: Any

class IngestEventError(BaseModel):
    event_index: int
    errors: list[dict[str, Any]]


class IngestResponse(BaseModel):
    trace_id: str
    accepted: int
    rejected: int
    duplicates: int
    errors: list[IngestEventError]
    timestamp: datetime


@router.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(
    request: Request,
    payload: list[dict[str, Any]],
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    trace_id = request.state.trace_id
    if len(payload) > 500:
        raise HTTPException(status_code=400, detail="Maximum batch size is 500 events.")

    event_ids = [str(item.get("event_id")) for item in payload if item.get("event_id") is not None]
    existing_ids = set(
        row[0]
        for row in await session.execute(
            select(EventRecord.event_id).where(EventRecord.event_id.in_(event_ids))
        )
    )

    accepted = 0
    rejected = 0
    duplicates = 0
    errors: list[IngestEventError] = []
    records: list[EventRecord] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(payload):
        try:
            validated = EventIn.model_validate(item)

        except ValidationError as exc:
            rejected += 1

            cleaned_errors = []
            for err in exc.errors():
                cleaned = dict(err)

                if "ctx" in cleaned:
                    cleaned["ctx"] = {
                        k: str(v)
                        for k, v in cleaned["ctx"].items()
                    }

                cleaned_errors.append(cleaned)

            errors.append(
                IngestEventError(
                    event_index=index,
                    errors=cleaned_errors,
                )
            )
            continue

        event_id = str(validated.event_id)

        if event_id in existing_ids or event_id in seen_ids:
            duplicates += 1
            continue

        seen_ids.add(event_id)

        records.append(
            EventRecord(
                event_id=event_id,
                store_id=validated.store_id,
                camera_id=validated.camera_id,
                visitor_id=validated.visitor_id,
                event_type=validated.event_type.value,
                timestamp=validated.timestamp,
                zone_id=validated.zone_id,
                dwell_ms=validated.dwell_ms,
                is_staff=validated.is_staff,
                confidence=validated.confidence,
                metadata_json=validated.metadata.model_dump()
                if validated.metadata is not None
                else None,
            )
        )

        accepted += 1

    if records:
        session.add_all(records)
        await session.commit()

    from datetime import timezone
    return IngestResponse(
        trace_id=trace_id,
        accepted=accepted,
        rejected=rejected,
        duplicates=duplicates,
        errors=errors,
        timestamp=datetime.now(timezone.utc),
    )