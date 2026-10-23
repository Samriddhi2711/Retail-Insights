"""Pipeline configuration models and YAML loader.

Provides strongly-typed Pydantic v2 models for all pipeline configuration.
All magic numbers live in pipeline_config.yaml — never in code (Code Style Rule).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import structlog
import yaml
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Named Constants (Code Style Rule: no magic numbers)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH: str = "pipeline/pipeline_config.yaml"
DEFAULT_FRAME_SKIP: int = 2
DEFAULT_PERSON_CLASS_ID: int = 0
DEFAULT_REID_THRESHOLD: float = 0.7
DEFAULT_GALLERY_UPDATE_INTERVAL: int = 30
DEFAULT_UNIFORM_RATIO: float = 0.4
DEFAULT_DWELL_THRESHOLD_MS: int = 3000
DEFAULT_GROUP_ENTRY_WINDOW_S: float = 2.0
DEFAULT_REENTRY_COOLDOWN_S: float = 300.0
DEFAULT_EMPTY_PERIOD_THRESHOLD_S: float = 1800.0
DEFAULT_CAMERA_OFFLINE_THRESHOLD_S: float = 600.0


# ---------------------------------------------------------------------------
# Zone Configuration
# ---------------------------------------------------------------------------

class ZoneConfig(BaseModel):
    """Definition of a detection zone within a camera view.

    Zones are polygons drawn on the camera frame. The pipeline uses
    point-in-polygon tests to determine which zone a tracked person
    is currently in.
    """

    zone_id: str = Field(..., description="Zone identifier, e.g. SKINCARE")
    type: Literal["entry_exit", "dwell", "billing", "staff_only"] = Field(
        ...,
        description="Zone type: entry_exit for gates, dwell for shopping areas, billing for checkout, or staff-only areas.",
    )
    polygon: list[list[int]] = Field(
        ...,
        min_length=3,
        description="Polygon vertices as [[x1,y1], [x2,y2], ...]. Minimum 3 points.",
    )
    sku_zone: Optional[str] = Field(
        default=None,
        description="SKU zone label for metadata, e.g. MOISTURISER.",
    )


# ---------------------------------------------------------------------------
# Camera Configuration
# ---------------------------------------------------------------------------

class CameraConfig(BaseModel):
    """Configuration for a single CCTV camera source."""

    camera_id: str = Field(..., description="Camera identifier, e.g. CAM_ENTRY_01")
    source: str = Field(..., description="Path to video file or RTSP stream URL")
    clip_start_time: datetime = Field(
        ...,
        description="ISO-8601 UTC start time of the clip. Timestamps = clip_start_time + (frame_index / fps).",
    )
    fps_override: float | None = Field(
        default=None,
        description="Optional fps override for video source timestamp calculation.",
    )
    zones: list[ZoneConfig] = Field(
        default_factory=list,
        description="Zone definitions for this camera view.",
    )


# ---------------------------------------------------------------------------
# Detection Configuration
# ---------------------------------------------------------------------------

class DetectionConfig(BaseModel):
    """YOLOv8 detection parameters."""

    model: str = Field(
        default="yolov8n.pt",
        description="YOLOv8 model name or path. Options: yolov8n/s/m/l/x.",
    )
    person_class_id: int = Field(
        default=DEFAULT_PERSON_CLASS_ID,
        description="COCO class ID for 'person'. Always 0.",
    )
    frame_skip: int = Field(
        default=DEFAULT_FRAME_SKIP,
        ge=1,
        description="Process every Nth frame. Higher = faster but less accurate.",
    )
    confidence_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Min confidence for detection. Set to 0.0 — never suppress (Hard Rule #4).",
    )


# ---------------------------------------------------------------------------
# Tracking Configuration
# ---------------------------------------------------------------------------

class TrackingConfig(BaseModel):
    """ByteTrack multi-object tracking parameters."""

    tracker_config: str = Field(
        default="bytetrack.yaml",
        description="Tracker YAML config file (bundled with ultralytics).",
    )
    persist: bool = Field(
        default=True,
        description="Persist track state across frames (required for consistent IDs).",
    )


# ---------------------------------------------------------------------------
# Re-ID Configuration
# ---------------------------------------------------------------------------

class ReIDConfig(BaseModel):
    """OSNet Re-ID feature extraction parameters."""

    model_name: str = Field(
        default="osnet_x1_0",
        description="OSNet model variant: osnet_x1_0, osnet_x0_5, osnet_ain_x1_0.",
    )
    similarity_threshold: float = Field(
        default=DEFAULT_REID_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Cosine similarity threshold. > this = same person.",
    )
    gallery_update_interval: int = Field(
        default=DEFAULT_GALLERY_UPDATE_INTERVAL,
        ge=1,
        description="Update Re-ID gallery embedding every N frames per track.",
    )


# ---------------------------------------------------------------------------
# Staff Classification Configuration
# ---------------------------------------------------------------------------

class HSVRange(BaseModel):
    """HSV color range for uniform detection."""

    lower: list[int] = Field(..., min_length=3, max_length=3)
    upper: list[int] = Field(..., min_length=3, max_length=3)


class StaffConfig(BaseModel):
    """Staff detection via HSV uniform color classification."""

    method: str = Field(
        default="hsv_uniform",
        description="Staff detection method. Currently: hsv_uniform.",
    )
    hsv_ranges: list[HSVRange] = Field(
        default_factory=lambda: [
            HSVRange(lower=[0, 50, 50], upper=[20, 255, 200]),
            HSVRange(lower=[100, 50, 50], upper=[130, 255, 200]),
        ],
        description="HSV ranges matching staff uniform colors.",
    )
    min_uniform_ratio: float = Field(
        default=DEFAULT_UNIFORM_RATIO,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of torso pixels matching uniform color.",
    )


# ---------------------------------------------------------------------------
# Zone Behavior Configuration
# ---------------------------------------------------------------------------

class ZoneBehaviorConfig(BaseModel):
    """Global zone behavior thresholds."""

    dwell_threshold_ms: int = Field(
        default=DEFAULT_DWELL_THRESHOLD_MS,
        ge=0,
        description="Minimum dwell time (ms) to emit a ZONE_DWELL event.",
    )
    entry_exit_direction: str = Field(
        default="bottom",
        description="Direction from which visitors enter: top, bottom, left, right.",
    )


# ---------------------------------------------------------------------------
# Edge Case Configuration
# ---------------------------------------------------------------------------

class EdgeCaseConfig(BaseModel):
    """Thresholds for handling edge cases from AGENTS.md."""

    group_entry_time_window_s: float = Field(
        default=DEFAULT_GROUP_ENTRY_WINDOW_S,
        description="Edge case #1: entries within this window = group.",
    )
    reentry_cooldown_s: float = Field(
        default=DEFAULT_REENTRY_COOLDOWN_S,
        description="Edge case #3: same Re-ID within this period = re-entry.",
    )
    empty_period_threshold_s: float = Field(
        default=DEFAULT_EMPTY_PERIOD_THRESHOLD_S,
        description="Edge case #6: no detections for this long = anomaly.",
    )
    camera_offline_threshold_s: float = Field(
        default=DEFAULT_CAMERA_OFFLINE_THRESHOLD_S,
        description="Edge case #7: camera inactive for this long = offline.",
    )


# ---------------------------------------------------------------------------
# Output Configuration
# ---------------------------------------------------------------------------

class OutputConfig(BaseModel):
    """Pipeline output settings."""

    output_dir: str = Field(default="output", description="Output directory path.")
    events_file: str = Field(
        default="events.jsonl",
        description="JSONL output filename.",
    )
    emit_to_api: bool = Field(
        default=False,
        description="If True, POST events to the API in addition to file output.",
    )
    api_url: str = Field(
        default="http://localhost:8000/events/ingest",
        description="API endpoint for event ingestion.",
    )


# ---------------------------------------------------------------------------
# Root Pipeline Configuration
# ---------------------------------------------------------------------------

class StoreConfig(BaseModel):
    """Store identification."""

    store_id: str = Field(..., description="Store identifier, e.g. STORE_BLR_002.")


class PipelineConfig(BaseModel):
    """Root configuration for the detection pipeline.

    Loaded from pipeline_config.yaml. All thresholds and parameters
    are centralized here — no magic numbers in code.
    """

    store: StoreConfig
    cameras: list[CameraConfig] = Field(
        ..., min_length=1, description="At least one camera must be configured."
    )
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)
    reid: ReIDConfig = Field(default_factory=ReIDConfig)
    staff: StaffConfig = Field(default_factory=StaffConfig)
    zones: ZoneBehaviorConfig = Field(default_factory=ZoneBehaviorConfig)
    edge_cases: EdgeCaseConfig = Field(default_factory=EdgeCaseConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @field_validator("cameras")
    @classmethod
    def validate_unique_camera_ids(cls, cameras: list[CameraConfig]) -> list[CameraConfig]:
        """Ensure all camera_ids are unique."""
        ids = [c.camera_id for c in cameras]
        duplicates = [cid for cid in ids if ids.count(cid) > 1]
        if duplicates:
            msg = f"Duplicate camera_ids found: {set(duplicates)}"
            raise ValueError(msg)
        return cameras


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> PipelineConfig:
    """Load and validate pipeline configuration from a YAML file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Validated PipelineConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValidationError: If the config does not match the schema.
    """
    path = Path(config_path)
    if not path.exists():
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)

    logger.info("loading_pipeline_config", path=str(path))

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    config = PipelineConfig.model_validate(raw)
    logger.info(
        "pipeline_config_loaded",
        store_id=config.store.store_id,
        num_cameras=len(config.cameras),
        model=config.detection.model,
    )
    return config
