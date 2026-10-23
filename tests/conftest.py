import sys
from unittest.mock import MagicMock
import numpy as np

cv2_mock = MagicMock()
cv2_mock.pointPolygonTest.side_effect = lambda *args, **kwargs: 1.0
cv2_mock.inRange.side_effect = lambda *args, **kwargs: np.zeros((20, 20), dtype=np.uint8)
cv2_mock.cvtColor.side_effect = lambda *args, **kwargs: np.zeros((20, 20, 3), dtype=np.uint8)
sys.modules['cv2'] = cv2_mock

sys.modules['ultralytics'] = MagicMock()
sys.modules['torchreid'] = MagicMock()
sys.modules['torchreid.utils'] = MagicMock()
sys.modules['torch'] = MagicMock()
sys.modules['torch.nn'] = MagicMock()
sys.modules['torch.nn.functional'] = MagicMock()
sys.modules['torchvision'] = MagicMock()
sys.modules['torchvision'] = MagicMock()

pytest_plugins = ["pytest_asyncio"]

import asyncio
import os

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_store_intelligence.db")
os.environ.setdefault("POS_TRANSACTIONS_CSV", "")

from app.main import app
from app.db import Base, engine, init_db, AsyncSessionLocal


async def _prepare_database() -> None:
    """Create all database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _cleanup_database() -> None:
    """Drop all database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="session", autouse=True)
def setup_session() -> None:
    """Set up session: create tables once at session start."""
    asyncio.run(_prepare_database())
    yield
    # Cleanup at end
    asyncio.run(_cleanup_database())
    asyncio.run(engine.dispose())


@pytest.fixture
async def clean_db() -> None:
    """Clean database before each test."""
    # Clear all data
    async with AsyncSessionLocal() as session:
        from sqlalchemy import text
        await session.execute(text("DELETE FROM events"))
        await session.execute(text("DELETE FROM pos_transactions"))
        await session.commit()
    yield


@pytest.fixture
async def async_client():
    """Async HTTP client for testing FastAPI endpoints via ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
