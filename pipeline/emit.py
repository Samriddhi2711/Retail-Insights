"""Event emission module — validates and writes events to JSONL.

Every event is validated against the Pydantic EventIn schema before
being written (Hard Rule #1). Events are written one-per-line to
events.jsonl as specified in build-pipeline.md § Step 3.

Reference: AGENTS.md § Event Schema, Hard Rule #1, #2, #4
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import structlog
from pydantic import ValidationError

from app.models import EventIn, EventMetadata
from pipeline.config import OutputConfig, PipelineConfig

if TYPE_CHECKING:
    from pipeline.tracker import TrackEvent

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Named Constants
# ---------------------------------------------------------------------------

FLUSH_INTERVAL: int = 100  # Flush file buffer every N events


# ---------------------------------------------------------------------------
# Event Emitter
# ---------------------------------------------------------------------------

class EventEmitter:
    """Validates and writes canonical events to JSONL output.

    Responsibilities:
    - Generate UUID v4 event_id for each event (Hard Rule #2)
    - Build the canonical event dict matching AGENTS.md schema exactly
    - Validate every event via Pydantic EventIn model (Hard Rule #1)
    - Write validated events to events.jsonl, one per line
    - Track session_seq per visitor_id
    - Never suppress or modify confidence values (Hard Rule #4)

    Args:
        config: Pipeline configuration.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self._store_id: str = config.store.store_id
        self._output_config: OutputConfig = config.output
        self._session_counters: dict[str, int] = defaultdict(int)
        self._emit_count: int = 0
        self._error_count: int = 0
        self._file_handle = None
        self._output_path: Optional[Path] = None

        # Create output directory
        output_dir = Path(self._output_config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_path = output_dir / self._output_config.events_file

        # Open output file
        self._file_handle = open(self._output_path, "w", encoding="utf-8")

        logger.info(
            "event_emitter_initialized",
            store_id=self._store_id,
            output_path=str(self._output_path),
        )

    def emit(self, track_event: TrackEvent) -> bool:
        """Validate and write a single event to JSONL output.

        Process:
        1. Generate event_id as UUID v4 (Hard Rule #2)
        2. Build canonical event dict
        3. Validate against Pydantic EventIn schema (Hard Rule #1)
        4. Write JSON line to events.jsonl
        5. Log emission via structlog

        Args:
            track_event: Intermediate event from the tracker.

        Returns:
            True if the event was successfully emitted, False if validation failed.
        """
        # Generate globally unique event_id (Hard Rule #2)
        event_id = uuid.uuid4()

        # Build session sequence
        session_seq = self._get_next_seq(track_event.visitor_id)

        # Build metadata
        metadata_dict = track_event.metadata or {}
        metadata_dict["session_seq"] = session_seq

        # Build the canonical event matching AGENTS.md schema exactly
        event_data = {
            "event_id": str(event_id),
            "store_id": self._store_id,
            "camera_id": track_event.camera_id,
            "visitor_id": track_event.visitor_id,
            "event_type": track_event.event_type,
            "timestamp": self._format_timestamp(track_event.timestamp),
            "zone_id": track_event.zone_id,
            "dwell_ms": track_event.dwell_ms,
            "is_staff": track_event.is_staff,       # Model-set (Hard Rule #3)
            "confidence": track_event.confidence,     # Raw value (Hard Rule #4)
            "metadata": {
                "queue_depth": metadata_dict.get("queue_depth"),
                "sku_zone": metadata_dict.get("sku_zone"),
                "session_seq": metadata_dict.get("session_seq"),
            },
        }

        # Validate against Pydantic schema (Hard Rule #1)
        try:
            validated_event = EventIn.model_validate(event_data)
        except ValidationError as exc:
            self._error_count += 1
            logger.error(
                "event_validation_failed",
                event_type=track_event.event_type,
                visitor_id=track_event.visitor_id,
                errors=exc.errors(),
            )
            return False

        # Write validated event as JSON line (build-pipeline.md: "Write to events.jsonl line by line")
        json_line = validated_event.model_dump_json()
        self._file_handle.write(json_line + "\n")

        self._emit_count += 1

        # Periodic flush
        if self._emit_count % FLUSH_INTERVAL == 0:
            self.flush()

        logger.debug(
            "event_emitted",
            event_id=str(event_id),
            event_type=track_event.event_type,
            visitor_id=track_event.visitor_id,
            camera_id=track_event.camera_id,
            confidence=track_event.confidence,
        )

        return True

    def emit_batch(self, events: list[TrackEvent]) -> int:
        """Emit multiple events, returning count of successfully emitted.

        Args:
            events: List of TrackEvent objects from the tracker.

        Returns:
            Number of events successfully validated and written.
        """
        success_count = 0
        for event in events:
            if self.emit(event):
                success_count += 1
        return success_count

    def flush(self) -> None:
        """Flush the output file buffer to disk."""
        if self._file_handle is not None and not self._file_handle.closed:
            self._file_handle.flush()

    def close(self) -> None:
        """Close the output file and log final statistics."""
        self.flush()
        if self._file_handle is not None and not self._file_handle.closed:
            self._file_handle.close()

        logger.info(
            "event_emitter_closed",
            total_emitted=self._emit_count,
            total_errors=self._error_count,
            output_path=str(self._output_path),
            unique_visitors=len(self._session_counters),
        )

    def get_stats(self) -> dict:
        """Return emission statistics.

        Returns:
            Dict with total_emitted, total_errors, unique_visitors.
        """
        return {
            "total_emitted": self._emit_count,
            "total_errors": self._error_count,
            "unique_visitors": len(self._session_counters),
        }

    def _get_next_seq(self, visitor_id: str) -> int:
        """Get and increment the session sequence counter for a visitor.

        Args:
            visitor_id: Visitor identifier (VIS_xxxxxx format).

        Returns:
            Next monotonic session_seq value for this visitor.
        """
        self._session_counters[visitor_id] += 1
        return self._session_counters[visitor_id]

    @staticmethod
    def _format_timestamp(ts: datetime) -> str:
        """Format a datetime as ISO-8601 UTC string.

        Detection Rule: Timestamps as ISO-8601 UTC.

        Args:
            ts: Datetime object (should be UTC).

        Returns:
            ISO-8601 formatted string ending in 'Z'.
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    def __enter__(self) -> "EventEmitter":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit — ensures file is closed."""
        self.close()
