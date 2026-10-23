# PROMPT: Validate anomaly and funnel endpoints using synthetic event data.
# CHANGES MADE: Added tests for health stale-feed and funnel re-entry deduplication.

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal, EventRecord


@pytest.mark.asyncio
async def test_health_reports_stale_feed_when_events_are_old(async_client: AsyncClient, clean_db) -> None:
    old_timestamp = datetime.now(timezone.utc) - timedelta(minutes=20)
    async with AsyncSessionLocal() as session:
        session.add(
            EventRecord(
                event_id="22222222-3333-4444-5555-666666666666",
                store_id="STORE_BLR_002",
                camera_id="CAM_ENTRY_01",
                visitor_id="VIS_dead01",
                event_type="ENTRY",
                timestamp=old_timestamp,
                zone_id=None,
                dwell_ms=None,
                is_staff=False,
                confidence=0.5,
                metadata_json={"session_seq": 1},
            )
        )
        await session.commit()

    response = await async_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db"] == "up"
    assert payload["stale_feed"] is True
    assert payload["last_event_age_seconds"] >= 1200


@pytest.mark.asyncio
async def test_funnel_deduplicates_reentry(async_client: AsyncClient, clean_db) -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                EventRecord(
                    event_id="33333333-4444-5555-6666-777777777777",
                    store_id="STORE_BLR_002",
                    camera_id="CAM_ENTRY_01",
                    visitor_id="VIS_reent1",
                    event_type="ENTRY",
                    timestamp=now - timedelta(minutes=5),
                    zone_id=None,
                    dwell_ms=None,
                    is_staff=False,
                    confidence=0.8,
                    metadata_json={"session_seq": 1},
                ),
                EventRecord(
                    event_id="33333333-4444-5555-6666-777777777778",
                    store_id="STORE_BLR_002",
                    camera_id="CAM_ENTRY_01",
                    visitor_id="VIS_reent1",
                    event_type="REENTRY",
                    timestamp=now - timedelta(minutes=1),
                    zone_id=None,
                    dwell_ms=None,
                    is_staff=False,
                    confidence=0.82,
                    metadata_json={"session_seq": 2},
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/stores/STORE_BLR_002/funnel")
    assert response.status_code == 200
    payload = response.json()
    stages = {stage["stage"]: stage["value"] for stage in payload["stages"]}
    assert stages["unique_visitors"] == 1
    assert stages["engaged"] == 0
    assert stages["queued"] == 0
    assert stages["purchased"] == 0
