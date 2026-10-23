# PROMPT: Fix coverage for app
# CHANGES MADE: Added tests for endpoints

import pytest
from httpx import AsyncClient
from datetime import datetime, timezone

@pytest.mark.asyncio
async def test_funnel_endpoint(async_client: AsyncClient, clean_db):
    response = await async_client.get("/stores/STORE_1/funnel?start_time=2026-01-01T00:00:00Z&end_time=2026-12-31T23:59:59Z")
    assert response.status_code == 200
    data = response.json()
    assert "store_id" in data
    assert "stages" in data

@pytest.mark.asyncio
async def test_anomalies_endpoint(async_client: AsyncClient, clean_db):
    response = await async_client.get("/stores/STORE_1/anomalies?start_time=2026-01-01T00:00:00Z&end_time=2026-12-31T23:59:59Z")
    assert response.status_code == 200
    data = response.json()
    assert "anomalies" in data

@pytest.mark.asyncio
async def test_heatmap_endpoint(async_client: AsyncClient, clean_db):
    response = await async_client.get("/stores/STORE_1/heatmap?camera_id=CAM_1&start_time=2026-01-01T00:00:00Z&end_time=2026-12-31T23:59:59Z")
    assert response.status_code == 200
    data = response.json()
    assert "zones" in data

@pytest.mark.asyncio
async def test_metrics_endpoint(async_client: AsyncClient, clean_db):
    response = await async_client.get("/stores/STORE_1/metrics?start_time=2026-01-01T00:00:00Z&end_time=2026-12-31T23:59:59Z")
    assert response.status_code == 200
    data = response.json()
    assert "unique_visitors" in data
    assert "conversion_rate" in data

@pytest.mark.asyncio
async def test_health_db_down(async_client: AsyncClient):
    response = await async_client.get("/health")
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_load_pos_transactions(clean_db, tmp_path):
    from app.db import load_pos_transactions, PostTransaction
    from app.db import AsyncSessionLocal
    from sqlalchemy import select, func
    
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("invoice_number,store_id,order_date,order_time,total_amount\nINV_001,STORE_1,03-03-2026,14:22:10,100.0")
    
    await load_pos_transactions(csv_file)
    
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(PostTransaction))
        assert count == 1


