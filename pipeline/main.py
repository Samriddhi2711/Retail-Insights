"""Pipeline orchestrator — processes all cameras and emits events.

Entry point for the detection pipeline. Coordinates detect.py,
tracker.py, and emit.py to process CCTV footage and produce a
canonical event stream.

Usage:
    python -m pipeline.main --config pipeline/pipeline_config.yaml
    python -m pipeline.main --config pipeline/pipeline_config.yaml --camera CAM_ENTRY_01

Reference: AGENTS.md § Project Structure (pipeline/)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

from pipeline.config import PipelineConfig, load_config
from pipeline.detect import PersonDetector
from pipeline.emit import EventEmitter
from pipeline.tracker import MultiCameraTracker

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Named Constants
# ---------------------------------------------------------------------------

LOG_PROGRESS_INTERVAL: int = 100  # Log progress every N frames


# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    """Configure structlog for JSON output.

    Code Style Rule: No print() — use structlog logger.
    AGENTS.md: Logging: structlog JSON output.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# Pipeline Runner
# ---------------------------------------------------------------------------

def run_pipeline(
    config_path: str,
    camera_filter: Optional[str] = None,
) -> dict:
    """Main pipeline entry point.

    Processes all cameras (or a single camera if filtered) through the
    detect → track → emit pipeline.

    Args:
        config_path: Path to pipeline_config.yaml.
        camera_filter: If set, only process this camera_id.

    Returns:
        Dict with pipeline statistics (events emitted, visitors, timing).
    """
    start_time = time.monotonic()

    # 1. Load configuration
    config = load_config(config_path)
    logger.info(
        "pipeline_starting",
        store_id=config.store.store_id,
        num_cameras=len(config.cameras),
        camera_filter=camera_filter,
    )

    # 2. Initialize components
    detector = PersonDetector(config)
    tracker = MultiCameraTracker(config)
    emitter = EventEmitter(config)

    # 3. Filter cameras if requested
    cameras = config.cameras
    if camera_filter:
        cameras = [c for c in cameras if c.camera_id == camera_filter]
        if not cameras:
            logger.error(
                "camera_not_found",
                camera_id=camera_filter,
                available=[c.camera_id for c in config.cameras],
            )
            sys.exit(1)

    # 4. Process each camera
    total_events = 0
    for cam_config in cameras:
        logger.info(
            "processing_camera",
            camera_id=cam_config.camera_id,
            source=cam_config.source,
        )

        camera_events = _process_single_camera(
            detector=detector,
            tracker=tracker,
            emitter=emitter,
            cam_config=cam_config,
            config=config,
        )
        total_events += camera_events

        logger.info(
            "camera_complete",
            camera_id=cam_config.camera_id,
            events_emitted=camera_events,
        )

    # 5. Close emitter and get final stats
    emitter.close()
    elapsed = time.monotonic() - start_time
    stats = emitter.get_stats()
    stats["elapsed_seconds"] = round(elapsed, 2)

    logger.info(
        "pipeline_complete",
        **stats,
    )

    return stats


def _process_single_camera(
    detector: PersonDetector,
    tracker: MultiCameraTracker,
    emitter: EventEmitter,
    cam_config,
    config: PipelineConfig,
) -> int:
    """Process a single camera through the detect → track → emit pipeline.

    For each frame:
    1. detect.detect_frame() → person bounding boxes + confidence
    2. Run ByteTrack via YOLO model.track() for track ID assignment
    3. tracker.update() → Re-ID, staff classification, zone detection → events
    4. emitter.emit_batch() → validate + write to JSONL

    Args:
        detector: PersonDetector instance.
        tracker: MultiCameraTracker instance.
        emitter: EventEmitter instance.
        cam_config: Camera configuration.
        config: Full pipeline configuration.

    Returns:
        Number of events emitted for this camera.
    """
    from ultralytics import YOLO

    camera_events = 0
    frame_count = 0
    last_timestamp = None

    # Load a separate YOLO model for tracking (ByteTrack needs its own instance)
    track_model = YOLO(config.detection.model)

    for frame_data in detector.process_video(cam_config.source, cam_config):
        frame_count += 1
        last_timestamp = frame_data.timestamp

        # Run ByteTrack tracking to get track IDs
        # persist=True maintains IDs across frames (essential for tracking)
        track_results = track_model.track(
            frame_data.frame,
            conf=config.detection.confidence_threshold,
            classes=[config.detection.person_class_id],
            tracker=config.tracking.tracker_config,
            persist=config.tracking.persist,
            verbose=False,
        )

        # Extract track IDs and detections from ByteTrack results
        track_ids = []
        detections = []

        for result in track_results:
            if result.boxes is None or len(result.boxes) == 0:
                continue

            for i, box in enumerate(result.boxes):
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0].cpu().numpy())

                # Get track ID (ByteTrack assigns these)
                track_id = -1
                if box.id is not None:
                    track_id = int(box.id[0].cpu().numpy())
                else:
                    # Untracked detection — assign temporary negative ID
                    track_id = -(i + 1 + frame_count * 1000)

                from pipeline.detect import DetectionResult

                detections.append(DetectionResult(
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
                    confidence=conf,
                    class_id=config.detection.person_class_id,
                ))
                track_ids.append(track_id)

        # Update tracker — produces events
        events = tracker.update(
            camera_id=cam_config.camera_id,
            frame_index=frame_data.frame_index,
            frame=frame_data.frame,
            detections=detections,
            timestamp=frame_data.timestamp,
            track_ids=track_ids,
        )

        # Emit events
        if events:
            emitted = emitter.emit_batch(events)
            camera_events += emitted

        # Progress logging
        if frame_count % LOG_PROGRESS_INTERVAL == 0:
            logger.info(
                "pipeline_progress",
                camera_id=cam_config.camera_id,
                frames_processed=frame_count,
                events_so_far=camera_events,
            )

    # Finalize — flush remaining zone/exit events
    if last_timestamp is not None:
        final_events = tracker.finalize(cam_config.camera_id, last_timestamp)
        if final_events:
            emitted = emitter.emit_batch(final_events)
            camera_events += emitted

    return camera_events


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for the detection pipeline."""
    parser = argparse.ArgumentParser(
        description="Apex Retail Store Intelligence — CCTV Detection Pipeline",
        epilog="Reference: AGENTS.md",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="pipeline/pipeline_config.yaml",
        help="Path to pipeline configuration YAML file.",
    )
    parser.add_argument(
        "--camera",
        type=str,
        default=None,
        help="Process only this camera_id (optional, default: all cameras).",
    )

    args = parser.parse_args()

    configure_logging()

    logger.info(
        "pipeline_cli_start",
        config=args.config,
        camera=args.camera,
    )

    stats = run_pipeline(
        config_path=args.config,
        camera_filter=args.camera,
    )

    # Final summary
    logger.info("pipeline_summary", **stats)


if __name__ == "__main__":
    main()
