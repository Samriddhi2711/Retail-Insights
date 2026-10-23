"""Pydantic v2 models for the canonical event schema.

This module is the single source of truth for event validation.
Both the detection pipeline (emit.py) and the FastAPI API share these models.
Every event must pass validation against EventIn before storage or emission.

Reference: AGENTS.md § Event Schema (canonical — never deviate)
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VISITOR_ID_PATTERN: re.Pattern[str] = re.compile(r"^VIS_[a-f0-9]{6}$")
"""Visitor ID format: VIS_ + first 6 hex chars of a UUID v4."""

CONFIDENCE_MIN: float = 0.0
CONFIDENCE_MAX: float = 1.0


# ---------------------------------------------------------------------------
# Event Type Enum — all 8 types from AGENTS.md § Event Types
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """All valid event types emitted by the detection pipeline.

    Reference: AGENTS.md § Event Types
    ENTRY · EXIT · ZONE_ENTER · ZONE_EXIT · ZONE_DWELL ·
    BILLING_QUEUE_JOIN · BILLING_QUEUE_ABANDON · REENTRY
    """

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


# ---------------------------------------------------------------------------
# Event Metadata
# ---------------------------------------------------------------------------

class EventMetadata(BaseModel):
    """Optional metadata attached to each event.

    Fields match the canonical schema in AGENTS.md exactly.
    """

    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None


# ---------------------------------------------------------------------------
# Core Event Models
# ---------------------------------------------------------------------------

class EventIn(BaseModel):
    """Canonical event schema — inbound from pipeline or POST /events/ingest.

    Hard Rules enforced:
      #1  Every event must be valid against this schema.
      #2  event_id must be UUID v4, globally unique.
      #3  is_staff must be set by the model — never hardcoded false.
      #4  confidence must reflect actual model output — no rounding up.

    Detection Rules enforced:
      - visitor_id format: VIS_ + first 6 chars of UUID.
      - confidence is a raw float in [0.0, 1.0].
    """

    event_id: UUID = Field(
        ...,
        description="UUID v4, globally unique (Hard Rule #2).",
    )
    store_id: str = Field(
        ...,
        min_length=1,
        description="Store identifier, e.g. STORE_BLR_002.",
    )
    camera_id: str = Field(
        ...,
        min_length=1,
        description="Camera identifier, e.g. CAM_ENTRY_01.",
    )
    visitor_id: str = Field(
        ...,
        description="Visitor ID in format VIS_ + 6 hex chars.",
    )
    event_type: EventType = Field(
        ...,
        description="One of the 8 canonical event types.",
    )
    timestamp: datetime = Field(
        ...,
        description="ISO-8601 UTC timestamp of the event.",
    )
    zone_id: Optional[str] = Field(
        default=None,
        description="Zone where the event occurred (e.g. SKINCARE).",
    )
    dwell_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Dwell time in milliseconds (ZONE_DWELL events).",
    )
    is_staff: bool = Field(
        ...,
        description="Set by staff classifier — NEVER hardcoded (Hard Rule #3).",
    )
    confidence: float = Field(
        ...,
        ge=CONFIDENCE_MIN,
        le=CONFIDENCE_MAX,
        description="Raw model confidence — no rounding up (Hard Rule #4).",
    )
    metadata: Optional[EventMetadata] = Field(
        default=None,
        description="Optional event metadata (queue_depth, sku_zone, session_seq).",
    )

    @field_validator("visitor_id")
    @classmethod
    def validate_visitor_id_format(cls, value: str) -> str:
        """Enforce VIS_ + 6 hex char format (Detection Rule)."""
        if not VISITOR_ID_PATTERN.match(value):
            msg = (
                f"visitor_id must match pattern VIS_<6 hex chars>, got: {value!r}"
            )
            raise ValueError(msg)
        return value

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "event_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
                    "store_id": "STORE_BLR_002",
                    "camera_id": "CAM_ENTRY_01",
                    "visitor_id": "VIS_c8a2f1",
                    "event_type": "ZONE_DWELL",
                    "timestamp": "2026-03-03T14:22:10Z",
                    "zone_id": "SKINCARE",
                    "dwell_ms": 8400,
                    "is_staff": False,
                    "confidence": 0.91,
                    "metadata": {
                        "queue_depth": None,
                        "sku_zone": "MOISTURISER",
                        "session_seq": 5,
                    },
                }
            ]
        }
    }
