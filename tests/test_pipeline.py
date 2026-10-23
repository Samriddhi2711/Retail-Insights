# PROMPT: Validate pipeline event emission and schema compliance.
# CHANGES MADE: Added pipeline unit tests for EventIn validation and EventEmitter JSONL output.

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import EventIn
from pipeline.config import load_config
from pipeline.emit import EventEmitter


def test_event_schema_validates_sample_event() -> None:
    event = {
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

    validated = EventIn.model_validate(event)
    assert validated.event_type.value == "ZONE_DWELL"
    assert validated.visitor_id.startswith("VIS_")
    assert validated.confidence == 0.91


def test_event_emitter_writes_jsonl(tmp_path) -> None:
    config = load_config("pipeline/pipeline_config.yaml")
    config.output.output_dir = str(tmp_path)
    config.output.events_file = "events.jsonl"

    emitter = EventEmitter(config)
    event = SimpleNamespace(
        camera_id="CAM_ENTRY_01",
        visitor_id="VIS_abcd12",
        event_type="ENTRY",
        timestamp=datetime.now(timezone.utc),
        zone_id=None,
        dwell_ms=None,
        is_staff=False,
        confidence=0.42,
        metadata={"queue_depth": None},
    )

    emitted = emitter.emit(event)
    emitter.close()

    assert emitted is True
    contents = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    record = json.loads(contents.strip())
    assert record["event_type"] == "ENTRY"
    assert record["store_id"] == "STORE_BLR_002"
    assert record["metadata"]["session_seq"] == 1
    assert record["confidence"] == 0.42
