"""YOLOv8 person detection module.

Processes video frames using YOLOv8 and returns bounding boxes with
raw confidence scores. Never suppresses low-confidence detections
(Hard Rule #4).

Technology: YOLOv8 (ultralytics) — AGENTS.md § Technology Stack
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import cv2
import numpy as np
import structlog
from ultralytics import YOLO

from pipeline.config import CameraConfig, DetectionConfig, PipelineConfig

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DetectionResult:
    """A single person detection in one video frame.

    Attributes:
        bbox: Bounding box as (x1, y1, x2, y2) in pixel coordinates.
        confidence: Raw model confidence score in [0.0, 1.0].
                    Never rounded up (Hard Rule #4, Detection Rule).
        class_id: COCO class ID (0 = person).
    """

    bbox: tuple[float, float, float, float]
    confidence: float
    class_id: int = 0


@dataclass
class FrameData:
    """Container for a processed video frame and its detections.

    Attributes:
        frame_index: Zero-based index of the frame in the video.
        frame: The raw video frame as a numpy array (BGR).
        detections: List of person detections found in this frame.
        timestamp: Computed timestamp = clip_start_time + (frame_index / fps).
        camera_id: Camera identifier from config.
    """

    frame_index: int
    frame: np.ndarray
    detections: list[DetectionResult]
    timestamp: datetime
    camera_id: str


# ---------------------------------------------------------------------------
# Person Detector
# ---------------------------------------------------------------------------

class PersonDetector:
    """YOLOv8-based person detector.

    Loads a YOLOv8 model and detects persons in video frames.
    Confidence threshold is set to 0.0 by design — we emit ALL
    detections and filter at query time (Hard Rule #4, Detection Rule).

    Args:
        config: Pipeline configuration containing detection parameters.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self._detection_config: DetectionConfig = config.detection
        self._model: YOLO = YOLO(self._detection_config.model)
        self._person_class_id: int = self._detection_config.person_class_id
        self._frame_skip: int = self._detection_config.frame_skip

        logger.info(
            "person_detector_initialized",
            model=self._detection_config.model,
            frame_skip=self._frame_skip,
            confidence_threshold=self._detection_config.confidence_threshold,
        )

    def detect_frame(self, frame: np.ndarray) -> list[DetectionResult]:
        """Run person detection on a single frame.

        Args:
            frame: Video frame as numpy array (BGR format).

        Returns:
            List of DetectionResult for each person detected.
            Includes ALL confidence levels — never suppresses (Hard Rule #4).
        """
        results = self._model(
            frame,
            conf=self._detection_config.confidence_threshold,
            classes=[self._person_class_id],
            verbose=False,
        )

        detections: list[DetectionResult] = []
        for result in results:
            if result.boxes is None or len(result.boxes) == 0:
                continue

            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0].cpu().numpy())
                cls_id = int(box.cls[0].cpu().numpy())

                detections.append(
                    DetectionResult(
                        bbox=(float(x1), float(y1), float(x2), float(y2)),
                        confidence=conf,  # Raw value — no rounding (Detection Rule)
                        class_id=cls_id,
                    )
                )

        return detections

    def process_video(
        self,
        video_path: str,
        camera_config: CameraConfig,
    ) -> Generator[FrameData, None, None]:
        """Process a video file and yield frame data with detections.

        Timestamps are calculated as:
            clip_start_time + (frame_index / fps)
        per the Detection Rule in project-rules.md.

        Args:
            video_path: Path to the video file.
            camera_config: Camera configuration with clip_start_time and camera_id.

        Yields:
            FrameData for each processed frame (respecting frame_skip).

        Raises:
            FileNotFoundError: If the video file does not exist.
            RuntimeError: If the video cannot be opened.
        """
        path = Path(video_path)
        if not path.exists():
            msg = f"Video file not found: {video_path}"
            raise FileNotFoundError(msg)

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            msg = f"Cannot open video: {video_path}"
            raise RuntimeError(msg)

        fps = camera_config.fps_override or cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        clip_start = camera_config.clip_start_time

        # Ensure clip_start_time is timezone-aware (UTC)
        if clip_start.tzinfo is None:
            clip_start = clip_start.replace(tzinfo=timezone.utc)

        logger.info(
            "processing_video",
            camera_id=camera_config.camera_id,
            video_path=video_path,
            fps=fps,
            total_frames=total_frames,
            frame_skip=self._frame_skip,
            frames_to_process=total_frames // self._frame_skip,
        )

        frame_index = 0
        processed_count = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # Apply frame skip
                if frame_index % self._frame_skip != 0:
                    frame_index += 1
                    continue

                # Detect persons in frame
                detections = self.detect_frame(frame)

                # Calculate timestamp: clip_start_time + (frame_index / fps)
                # Detection Rule: ISO-8601 UTC
                timestamp = clip_start + timedelta(seconds=frame_index / fps)

                yield FrameData(
                    frame_index=frame_index,
                    frame=frame,
                    detections=detections,
                    timestamp=timestamp,
                    camera_id=camera_config.camera_id,
                )

                processed_count += 1
                frame_index += 1

                # Progress logging every 500 processed frames
                progress_log_interval = 500
                if processed_count % progress_log_interval == 0:
                    pct = (frame_index / total_frames * 100) if total_frames > 0 else 0
                    logger.info(
                        "detection_progress",
                        camera_id=camera_config.camera_id,
                        frame=frame_index,
                        total=total_frames,
                        percent=f"{pct:.1f}%",
                        detections_this_frame=len(detections),
                    )

        finally:
            cap.release()
            logger.info(
                "video_processing_complete",
                camera_id=camera_config.camera_id,
                frames_processed=processed_count,
                total_frames=total_frames,
            )
