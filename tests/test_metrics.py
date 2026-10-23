# PROMPT: Verify metrics endpoint behavior for empty and ingested event scenarios.
# CHANGES MADE: Added API tests covering zero-safe metrics and idempotent ingestion.

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal, EventRecord


@pytest.mark.asyncio
async def test_metrics_returns_zero_for_empty_store(async_client: AsyncClient, clean_db) -> None:
    response = await async_client.get("/stores/STORE_BLR_002/metrics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["unique_visitors"] == 0
    assert payload["total_entries"] == 0
    assert payload["total_purchases"] == 0
    assert payload["conversion_rate"] == 0.0
    assert payload["average_dwell_ms"] == 0
    assert payload["queue_joins"] == 0
    assert payload["queue_abandons"] == 0


@pytest.mark.asyncio
async def test_ingestion_is_idempotent(async_client: AsyncClient, clean_db) -> None:
    event = {
        "event_id": "11111111-2222-4333-8444-555555555555",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_BILLING_01",
        "visitor_id": "VIS_a1b2c3",
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": "2026-04-10T20:15:00Z",
        "zone_id": "BILLING",
        "dwell_ms": 2000,
        "is_staff": False,
        "confidence": 0.85,
        "metadata": {"queue_depth": 1, "sku_zone": "ACCESSORIES", "session_seq": 1},
    }

    response1 = await async_client.post("/events/ingest", json=[event])
    assert response1.status_code == 200
    payload1 = response1.json()
    assert payload1["accepted"] == 1
    assert payload1["duplicates"] == 0

    response2 = await async_client.post("/events/ingest", json=[event])
    assert response2.status_code == 200
    payload2 = response2.json()
    assert payload2["accepted"] == 0
    assert payload2["duplicates"] == 1

    from sqlalchemy import func, select

    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count()).where(EventRecord.event_id == event["event_id"])
        )
    assert count == 1
