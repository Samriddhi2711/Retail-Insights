"""Multi-object tracking with Re-ID and staff classification.

Handles all 7 edge cases from AGENTS.md:
  #1 Group entry    — multiple entries within time window
  #2 Staff          — HSV uniform color classification
  #3 Re-entry       — OSNet Re-ID matches exited visitors
  #4 Occlusion      — ByteTrack Kalman filter prediction
  #5 Billing queue  — zone-based queue join/abandon tracking
  #6 Empty periods  — gap detection in event stream
  #7 Camera overlap — shared Re-ID gallery across cameras

Technology: ByteTrack (ultralytics) + OSNet Re-ID (torchreid)
Reference: AGENTS.md § Hard Rules, build-pipeline.md § Step 2
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import cv2
import numpy as np
import structlog
import torch
import torch.nn as nn

from pipeline.config import (
    CameraConfig,
    EdgeCaseConfig,
    HSVRange,
    PipelineConfig,
    ReIDConfig,
    StaffConfig,
    ZoneBehaviorConfig,
    ZoneConfig,
)
from pipeline.detect import DetectionResult

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Named Constants
# ---------------------------------------------------------------------------

VISITOR_ID_PREFIX: str = "VIS_"
VISITOR_ID_HEX_LENGTH: int = 6
TORSO_TOP_RATIO: float = 0.2     # Top 20% of bbox is head — skip
TORSO_BOTTOM_RATIO: float = 0.8  # Bottom 20% is legs — skip
REID_EMBEDDING_DIM: int = 512
BBOX_HISTORY_MAXLEN: int = 30
GROUP_MIN_SIZE: int = 2


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class TrackEvent:
    """Intermediate event from tracker, before Pydantic validation.

    Passed to emit.py for schema validation and JSONL output.
    """

    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: Optional[int] = None
    is_staff: bool = False
    confidence: float = 0.0
    metadata: Optional[dict] = None


@dataclass
class TrackedPerson:
    """State for a single tracked person across frames.

    Maintains all information needed for zone detection,
    event emission, and Re-ID matching.
    """

    track_id: int
    visitor_id: str
    reid_embedding: Optional[np.ndarray] = None
    is_staff: bool = False                    # Hard Rule #3: set by model
    confidence: float = 0.0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    current_zone: Optional[str] = None
    zone_enter_time: Optional[datetime] = None
    session_seq: int = 0
    bbox_history: deque = field(default_factory=lambda: deque(maxlen=BBOX_HISTORY_MAXLEN))
    is_reentry: bool = False
    has_exited: bool = False
    entry_emitted: bool = False
    last_bbox: Optional[tuple[float, float, float, float]] = None
    staff_votes: list[bool] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Re-ID Gallery — Cross-Camera Person Matching (Edge Cases #3, #7)
# ---------------------------------------------------------------------------

class ReIDGallery:
    """Cross-camera Re-ID feature gallery using OSNet embeddings.

    Maintains a gallery of visitor embeddings. When a new detection
    is observed, it is compared against the gallery via cosine similarity.
    If similarity > threshold (0.7 from build-pipeline.md), the existing
    visitor_id is reused.

    Edge case #3 (Re-entry): Matches visitors who previously exited.
    Edge case #7 (Camera overlap): Shared gallery across all cameras.
    """

    def __init__(self, config: ReIDConfig) -> None:
        self._similarity_threshold: float = config.similarity_threshold
        self._gallery_update_interval: int = config.gallery_update_interval
        self._device: str = "cuda" if torch.cuda.is_available() else "cpu"
        self._cosine_sim = nn.CosineSimilarity(dim=1, eps=1e-6)

        # Gallery: visitor_id → embedding tensor
        self._gallery: dict[str, torch.Tensor] = {}
        # Track which visitors have exited (for re-entry detection)
        self._exited_visitors: set[str] = set()

        # Lazy-load OSNet extractor (heavy import)
        self._extractor = None
        self._model_name: str = config.model_name

        logger.info(
            "reid_gallery_initialized",
            model=config.model_name,
            threshold=self._similarity_threshold,
            device=self._device,
        )

    def _ensure_extractor(self) -> None:
        """Lazy-load the OSNet feature extractor."""
        if self._extractor is None:
            from torchreid.reid.utils import FeatureExtractor

            self._extractor = FeatureExtractor(
                model_name=self._model_name,
                model_path=None,  # Auto-download pretrained weights
                device=self._device,
            )
            logger.info("osnet_extractor_loaded", model=self._model_name)

    def extract_embedding(
        self,
        frame: np.ndarray,
        bbox: tuple[float, float, float, float],
    ) -> np.ndarray:
        """Extract a Re-ID embedding from a person crop.

        Args:
            frame: Full video frame (BGR).
            bbox: Person bounding box (x1, y1, x2, y2).

        Returns:
            512-dimensional embedding vector as numpy array.
        """
        self._ensure_extractor()

        x1, y1, x2, y2 = map(int, bbox)
        h, w = frame.shape[:2]

        # Clamp coordinates to frame bounds
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)

        # Crop and resize person image
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return np.zeros(REID_EMBEDDING_DIM, dtype=np.float32)

        # Convert BGR to RGB for torchreid
        crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

        # Extract features
        features = self._extractor([crop_rgb])
        embedding = features[0].cpu().numpy()

        return embedding

    def match_or_register(
        self,
        embedding: np.ndarray,
    ) -> tuple[str, bool]:
        """Match an embedding against the gallery or register a new visitor.

        Args:
            embedding: 512-d Re-ID embedding vector.

        Returns:
            Tuple of (visitor_id, is_reentry).
            is_reentry is True if the visitor previously exited (Edge Case #3).
        """
        if len(self._gallery) == 0:
            visitor_id = self._generate_visitor_id()
            self._gallery[visitor_id] = torch.tensor(
                embedding, dtype=torch.float32
            )
            return visitor_id, False

        # Compare against all gallery entries
        query = torch.tensor(embedding, dtype=torch.float32).unsqueeze(0)
        best_match: Optional[str] = None
        best_similarity: float = -1.0

        for vid, gallery_emb in self._gallery.items():
            similarity = self._cosine_sim(query, gallery_emb.unsqueeze(0))
            sim_val = float(similarity.item())

            if sim_val > best_similarity:
                best_similarity = sim_val
                best_match = vid

        if best_match is not None and best_similarity > self._similarity_threshold:
            is_reentry = best_match in self._exited_visitors
            if is_reentry:
                self._exited_visitors.discard(best_match)
            logger.debug(
                "reid_match",
                visitor_id=best_match,
                similarity=f"{best_similarity:.3f}",
                is_reentry=is_reentry,
            )
            return best_match, is_reentry

        # No match — register new visitor
        visitor_id = self._generate_visitor_id()
        self._gallery[visitor_id] = torch.tensor(
            embedding, dtype=torch.float32
        )
        return visitor_id, False

    def mark_exited(self, visitor_id: str) -> None:
        """Mark a visitor as exited for re-entry detection."""
        self._exited_visitors.add(visitor_id)

    def update_embedding(self, visitor_id: str, embedding: np.ndarray) -> None:
        """Update the gallery embedding for a visitor (periodic refresh)."""
        if visitor_id in self._gallery:
            self._gallery[visitor_id] = torch.tensor(
                embedding, dtype=torch.float32
            )

    @staticmethod
    def _generate_visitor_id() -> str:
        """Generate a visitor ID in format VIS_ + first 6 hex chars of UUID.

        Detection Rule: visitor_id format: VIS_ + first 6 chars of UUID.
        """
        raw_uuid = uuid.uuid4().hex[:VISITOR_ID_HEX_LENGTH]
        return f"{VISITOR_ID_PREFIX}{raw_uuid}"


# ---------------------------------------------------------------------------
# Staff Classifier — HSV Uniform Detection (Edge Case #2)
# ---------------------------------------------------------------------------

class StaffClassifier:
    """Classifies persons as staff based on HSV uniform color analysis.

    Hard Rule #3: is_staff must be set by the model — NEVER hardcoded false.
    The classifier analyzes the torso region of each detection for uniform colors.

    Reference: build-pipeline.md § Step 2
    """

    def __init__(self, config: StaffConfig) -> None:
        self._hsv_ranges: list[HSVRange] = config.hsv_ranges
        self._min_ratio: float = config.min_uniform_ratio

        logger.info(
            "staff_classifier_initialized",
            method=config.method,
            num_hsv_ranges=len(self._hsv_ranges),
            min_ratio=self._min_ratio,
        )

    def classify(
        self,
        frame: np.ndarray,
        bbox: tuple[float, float, float, float],
    ) -> bool:
        """Classify a person as staff or visitor based on uniform color.

        Extracts the torso region (middle 60% of bbox height),
        converts to HSV, and checks if enough pixels match the
        configured uniform color ranges.

        Args:
            frame: Full video frame (BGR).
            bbox: Person bounding box (x1, y1, x2, y2).

        Returns:
            True if the person is classified as staff.
            This value is SET BY THE MODEL — never hardcoded (Hard Rule #3).
        """
        x1, y1, x2, y2 = map(int, bbox)
        h, w = frame.shape[:2]

        # Clamp to frame bounds
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)

        bbox_height = y2 - y1
        if bbox_height <= 0 or (x2 - x1) <= 0:
            return False

        # Extract torso region (skip head and legs)
        torso_y1 = y1 + int(bbox_height * TORSO_TOP_RATIO)
        torso_y2 = y1 + int(bbox_height * TORSO_BOTTOM_RATIO)
        torso = frame[torso_y1:torso_y2, x1:x2]

        if torso.size == 0:
            return False

        # Convert to HSV color space
        hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)

        # Check each configured HSV range
        total_pixels = hsv.shape[0] * hsv.shape[1]
        if total_pixels == 0:
            return False

        uniform_pixels = 0
        for hsv_range in self._hsv_ranges:
            lower = np.array(hsv_range.lower, dtype=np.uint8)
            upper = np.array(hsv_range.upper, dtype=np.uint8)
            mask = cv2.inRange(hsv, lower, upper)
            uniform_pixels += int(np.count_nonzero(mask))

        ratio = uniform_pixels / total_pixels
        is_staff = ratio >= self._min_ratio

        return is_staff


# ---------------------------------------------------------------------------
# Zone Detector — Point-in-Polygon (Edge Case #5: Billing Queue)
# ---------------------------------------------------------------------------

class ZoneDetector:
    """Detects which zone a tracked person is in via point-in-polygon tests.

    Uses the ray-casting algorithm to test if the center of a person's
    bounding box falls within any configured zone polygon.
    """

    def __init__(self, zones: list[ZoneConfig]) -> None:
        self._zones: list[ZoneConfig] = zones
        self._zone_polygons: dict[str, np.ndarray] = {
            z.zone_id: np.array(z.polygon, dtype=np.int32)
            for z in zones
        }
        logger.info(
            "zone_detector_initialized",
            num_zones=len(zones),
            zone_ids=[z.zone_id for z in zones],
        )

    def detect_zone(
        self,
        bbox: tuple[float, float, float, float],
    ) -> Optional[str]:
        """Determine which zone the person is in based on bbox center.

        Args:
            bbox: Person bounding box (x1, y1, x2, y2).

        Returns:
            Zone ID if the bbox center is inside a zone polygon,
            None otherwise.
        """
        # Use bottom-center of bbox as the person's position
        # (more stable than center, especially for standing persons)
        x_center = (bbox[0] + bbox[2]) / 2
        y_bottom = bbox[3]  # Bottom of bounding box
        point = (int(x_center), int(y_bottom))

        for zone_id, polygon in self._zone_polygons.items():
            if self._point_in_polygon(point, polygon):
                return zone_id

        return None

    def get_zone_config(self, zone_id: str) -> Optional[ZoneConfig]:
        """Get the ZoneConfig for a given zone_id."""
        for zone in self._zones:
            if zone.zone_id == zone_id:
                return zone
        return None

    @staticmethod
    def _point_in_polygon(point: tuple[int, int], polygon: np.ndarray) -> bool:
        """Test if a point is inside a polygon using OpenCV.

        Args:
            point: (x, y) coordinate to test.
            polygon: Polygon vertices as numpy array.

        Returns:
            True if the point is inside the polygon.
        """
        result = cv2.pointPolygonTest(polygon.astype(np.float32), point, False)
        return result >= 0


# ---------------------------------------------------------------------------
# Multi-Camera Tracker — Orchestrates All Tracking Logic
# ---------------------------------------------------------------------------

class MultiCameraTracker:
    """Orchestrates ByteTrack tracking, Re-ID, staff classification, and zone detection.

    This is the central tracking component that ties together:
    - ByteTrack for frame-to-frame multi-object tracking
    - OSNet Re-ID for cross-camera person matching
    - HSV staff classifier
    - Zone polygon detection
    - All 7 edge case handlers

    The Re-ID gallery is SHARED across all cameras (Edge Case #7: camera overlap).
    """

    def __init__(self, config: PipelineConfig) -> None:
        self._config: PipelineConfig = config
        self._reid_gallery = ReIDGallery(config.reid)
        self._staff_classifier = StaffClassifier(config.staff)
        self._zone_behavior: ZoneBehaviorConfig = config.zones
        self._edge_cases: EdgeCaseConfig = config.edge_cases

        # Per-camera zone detectors
        self._zone_detectors: dict[str, ZoneDetector] = {}
        for cam in config.cameras:
            self._zone_detectors[cam.camera_id] = ZoneDetector(cam.zones)

        # Track state: track_id → TrackedPerson
        self._tracks: dict[int, TrackedPerson] = {}

        # Billing queue depth per zone
        self._queue_depth: dict[str, int] = defaultdict(int)

        # Recent entries per camera for group detection (Edge Case #1)
        self._recent_entries: dict[str, list[tuple[datetime, str]]] = defaultdict(list)

        # Last detection time per camera for empty period detection (Edge Case #6)
        self._last_detection_time: dict[str, datetime] = {}

        # Frame counter per track for Re-ID gallery updates
        self._track_frame_count: dict[int, int] = defaultdict(int)

        logger.info(
            "multi_camera_tracker_initialized",
            num_cameras=len(config.cameras),
            reid_threshold=config.reid.similarity_threshold,
            staff_method=config.staff.method,
        )

    def update(
        self,
        camera_id: str,
        frame_index: int,
        frame: np.ndarray,
        detections: list[DetectionResult],
        timestamp: datetime,
        track_ids: list[int],
    ) -> list[TrackEvent]:
        """Process detections for a single frame and generate events.

        This is the main tracking loop called once per processed frame.
        It handles track assignment, Re-ID matching, staff classification,
        zone transitions, and all edge cases.

        Args:
            camera_id: Camera identifier.
            frame_index: Frame index in the video.
            frame: Video frame (BGR numpy array).
            detections: Person detections from YOLOv8.
            timestamp: Computed timestamp for this frame.
            track_ids: ByteTrack track IDs corresponding to detections.

        Returns:
            List of TrackEvent objects for emission.
        """
        events: list[TrackEvent] = []

        # Update last detection time for empty period tracking (Edge Case #6)
        if len(detections) > 0:
            self._last_detection_time[camera_id] = timestamp
        else:
            events.extend(self._check_empty_period(camera_id, timestamp))

        # Process each detection with its track ID
        new_entries: list[tuple[datetime, str]] = []

        for detection, track_id in zip(detections, track_ids):
            bbox = detection.bbox
            confidence = detection.confidence

            # Get or create tracked person
            if track_id not in self._tracks:
                # New track — perform Re-ID matching
                embedding = self._reid_gallery.extract_embedding(frame, bbox)
                visitor_id, is_reentry = self._reid_gallery.match_or_register(embedding)

                # Classify staff (Edge Case #2, Hard Rule #3)
                is_staff = self._staff_classifier.classify(frame, bbox)

                person = TrackedPerson(
                    track_id=track_id,
                    visitor_id=visitor_id,
                    reid_embedding=embedding,
                    is_staff=is_staff,
                    confidence=confidence,
                    first_seen=timestamp,
                    last_seen=timestamp,
                    is_reentry=is_reentry,
                    last_bbox=bbox,
                )
                person.staff_votes.append(is_staff)
                self._tracks[track_id] = person

                # Emit ENTRY or REENTRY event
                if is_reentry:
                    events.append(TrackEvent(
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="REENTRY",
                        timestamp=timestamp,
                        is_staff=is_staff,
                        confidence=confidence,
                        metadata={"session_seq": person.session_seq},
                    ))
                    logger.info(
                        "reentry_detected",
                        visitor_id=visitor_id,
                        camera_id=camera_id,
                    )
                else:
                    events.append(TrackEvent(
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="ENTRY",
                        timestamp=timestamp,
                        is_staff=is_staff,
                        confidence=confidence,
                        metadata={"session_seq": person.session_seq},
                    ))
                    person.entry_emitted = True
                    new_entries.append((timestamp, visitor_id))

            else:
                person = self._tracks[track_id]
                person.last_seen = timestamp
                person.confidence = confidence
                person.last_bbox = bbox
                person.bbox_history.append(bbox)

                # Periodic staff vote update
                is_staff_vote = self._staff_classifier.classify(frame, bbox)
                person.staff_votes.append(is_staff_vote)
                # Use majority vote for staff classification
                staff_true = sum(person.staff_votes)
                staff_false = len(person.staff_votes) - staff_true
                person.is_staff = staff_true > staff_false

                # Periodic Re-ID embedding update
                self._track_frame_count[track_id] += 1
                if (self._track_frame_count[track_id]
                        % self._config.reid.gallery_update_interval == 0):
                    embedding = self._reid_gallery.extract_embedding(frame, bbox)
                    self._reid_gallery.update_embedding(person.visitor_id, embedding)
                    person.reid_embedding = embedding

            # Zone transition detection (Edge Cases #5)
            zone_events = self._detect_zone_transition(
                person, camera_id, bbox, timestamp
            )
            events.extend(zone_events)

        # Group entry detection (Edge Case #1)
        if len(new_entries) >= GROUP_MIN_SIZE:
            self._record_group_entries(camera_id, new_entries, events)

        # Clean up stale entries list
        self._clean_recent_entries(camera_id, timestamp)

        return events

    def finalize(self, camera_id: str, timestamp: datetime) -> list[TrackEvent]:
        """Flush remaining events when video processing is complete.

        Emits EXIT events for all still-active tracks and ZONE_DWELL
        events for persons still inside zones.

        Args:
            camera_id: Camera being finalized.
            timestamp: Final timestamp of the video.

        Returns:
            List of remaining TrackEvent objects.
        """
        events: list[TrackEvent] = []

        for track_id, person in list(self._tracks.items()):
            # Emit pending zone dwell
            if person.current_zone is not None and person.zone_enter_time is not None:
                dwell_ms = int(
                    (timestamp - person.zone_enter_time).total_seconds() * 1000
                )
                if dwell_ms >= self._zone_behavior.dwell_threshold_ms:
                    zone_config = self._zone_detectors.get(camera_id)
                    sku_zone = None
                    if zone_config:
                        zc = zone_config.get_zone_config(person.current_zone)
                        if zc:
                            sku_zone = zc.sku_zone

                    person.session_seq += 1
                    events.append(TrackEvent(
                        camera_id=camera_id,
                        visitor_id=person.visitor_id,
                        event_type="ZONE_DWELL",
                        timestamp=timestamp,
                        zone_id=person.current_zone,
                        dwell_ms=dwell_ms,
                        is_staff=person.is_staff,
                        confidence=person.confidence,
                        metadata={
                            "sku_zone": sku_zone,
                            "session_seq": person.session_seq,
                        },
                    ))

                # Emit ZONE_EXIT
                person.session_seq += 1
                events.append(TrackEvent(
                    camera_id=camera_id,
                    visitor_id=person.visitor_id,
                    event_type="ZONE_EXIT",
                    timestamp=timestamp,
                    zone_id=person.current_zone,
                    is_staff=person.is_staff,
                    confidence=person.confidence,
                    metadata={"session_seq": person.session_seq},
                ))

            # Emit EXIT
            if not person.has_exited:
                person.session_seq += 1
                events.append(TrackEvent(
                    camera_id=camera_id,
                    visitor_id=person.visitor_id,
                    event_type="EXIT",
                    timestamp=timestamp,
                    is_staff=person.is_staff,
                    confidence=person.confidence,
                    metadata={"session_seq": person.session_seq},
                ))
                self._reid_gallery.mark_exited(person.visitor_id)
                person.has_exited = True

        logger.info(
            "tracker_finalized",
            camera_id=camera_id,
            active_tracks=len(self._tracks),
            events_emitted=len(events),
        )

        return events

    # -----------------------------------------------------------------------
    # Zone Transition Detection
    # -----------------------------------------------------------------------

    def _detect_zone_transition(
        self,
        person: TrackedPerson,
        camera_id: str,
        bbox: tuple[float, float, float, float],
        timestamp: datetime,
    ) -> list[TrackEvent]:
        """Detect zone enter/exit/dwell transitions for a tracked person.

        Handles Edge Case #5 (billing queue) by tracking queue depth
        and emitting BILLING_QUEUE_JOIN / BILLING_QUEUE_ABANDON events.
        """
        events: list[TrackEvent] = []
        zone_detector = self._zone_detectors.get(camera_id)
        if zone_detector is None:
            return events

        current_zone = zone_detector.detect_zone(bbox)
        previous_zone = person.current_zone

        # No zone change
        if current_zone == previous_zone:
            return events

        # --- Exit previous zone ---
        if previous_zone is not None and person.zone_enter_time is not None:
            dwell_ms = int(
                (timestamp - person.zone_enter_time).total_seconds() * 1000
            )

            zone_cfg = zone_detector.get_zone_config(previous_zone)

            # Check if this was a billing zone (Edge Case #5)
            if zone_cfg and zone_cfg.type == "billing":
                self._queue_depth[previous_zone] = max(
                    0, self._queue_depth[previous_zone] - 1
                )
                # Billing queue abandon if dwell < threshold
                if dwell_ms < self._zone_behavior.dwell_threshold_ms:
                    person.session_seq += 1
                    events.append(TrackEvent(
                        camera_id=camera_id,
                        visitor_id=person.visitor_id,
                        event_type="BILLING_QUEUE_ABANDON",
                        timestamp=timestamp,
                        zone_id=previous_zone,
                        dwell_ms=dwell_ms,
                        is_staff=person.is_staff,
                        confidence=person.confidence,
                        metadata={
                            "queue_depth": self._queue_depth[previous_zone],
                            "session_seq": person.session_seq,
                        },
                    ))

            # Emit ZONE_DWELL if dwell exceeds threshold
            if dwell_ms >= self._zone_behavior.dwell_threshold_ms:
                sku_zone = zone_cfg.sku_zone if zone_cfg else None
                person.session_seq += 1
                events.append(TrackEvent(
                    camera_id=camera_id,
                    visitor_id=person.visitor_id,
                    event_type="ZONE_DWELL",
                    timestamp=timestamp,
                    zone_id=previous_zone,
                    dwell_ms=dwell_ms,
                    is_staff=person.is_staff,
                    confidence=person.confidence,
                    metadata={
                        "sku_zone": sku_zone,
                        "session_seq": person.session_seq,
                    },
                ))

            # Emit ZONE_EXIT
            person.session_seq += 1
            events.append(TrackEvent(
                camera_id=camera_id,
                visitor_id=person.visitor_id,
                event_type="ZONE_EXIT",
                timestamp=timestamp,
                zone_id=previous_zone,
                is_staff=person.is_staff,
                confidence=person.confidence,
                metadata={"session_seq": person.session_seq},
            ))

        # --- Enter new zone ---
        if current_zone is not None:
            person.current_zone = current_zone
            person.zone_enter_time = timestamp

            zone_cfg = zone_detector.get_zone_config(current_zone)

            # Check if entering billing zone (Edge Case #5)
            if zone_cfg and zone_cfg.type == "billing":
                self._queue_depth[current_zone] += 1
                person.session_seq += 1
                events.append(TrackEvent(
                    camera_id=camera_id,
                    visitor_id=person.visitor_id,
                    event_type="BILLING_QUEUE_JOIN",
                    timestamp=timestamp,
                    zone_id=current_zone,
                    is_staff=person.is_staff,
                    confidence=person.confidence,
                    metadata={
                        "queue_depth": self._queue_depth[current_zone],
                        "session_seq": person.session_seq,
                    },
                ))
            else:
                # Regular zone enter
                person.session_seq += 1
                events.append(TrackEvent(
                    camera_id=camera_id,
                    visitor_id=person.visitor_id,
                    event_type="ZONE_ENTER",
                    timestamp=timestamp,
                    zone_id=current_zone,
                    is_staff=person.is_staff,
                    confidence=person.confidence,
                    metadata={"session_seq": person.session_seq},
                ))
        else:
            person.current_zone = None
            person.zone_enter_time = None

        return events

    # -----------------------------------------------------------------------
    # Edge Case #1: Group Entry Detection
    # -----------------------------------------------------------------------

    def _record_group_entries(
        self,
        camera_id: str,
        new_entries: list[tuple[datetime, str]],
        events: list[TrackEvent],
    ) -> None:
        """Detect and tag group entries.

        Edge Case #1: Multiple ENTRY events within group_entry_time_window_s
        from the same camera are tagged as a group in event metadata.
        Each person retains a distinct visitor_id.
        """
        self._recent_entries[camera_id].extend(new_entries)

        window = timedelta(seconds=self._edge_cases.group_entry_time_window_s)

        if len(new_entries) < GROUP_MIN_SIZE:
            return

        # Check if all new entries are within the time window
        timestamps = [ts for ts, _ in new_entries]
        time_span = max(timestamps) - min(timestamps)

        if time_span <= window:
            group_size = len(new_entries)
            group_visitor_ids = [vid for _, vid in new_entries]

            logger.info(
                "group_entry_detected",
                camera_id=camera_id,
                group_size=group_size,
                visitor_ids=group_visitor_ids,
            )

            # Update metadata on the ENTRY events in this batch
            for event in events:
                if (event.event_type == "ENTRY"
                        and event.visitor_id in group_visitor_ids):
                    if event.metadata is None:
                        event.metadata = {}
                    event.metadata["group_size"] = group_size
                    event.metadata["group_visitor_ids"] = group_visitor_ids

    def _clean_recent_entries(self, camera_id: str, now: datetime) -> None:
        """Remove old entries outside the group detection window."""
        window = timedelta(seconds=self._edge_cases.group_entry_time_window_s)
        cutoff = now - window
        self._recent_entries[camera_id] = [
            (ts, vid) for ts, vid in self._recent_entries[camera_id]
            if ts > cutoff
        ]

    # -----------------------------------------------------------------------
    # Edge Case #6: Empty Period Detection
    # -----------------------------------------------------------------------

    def _check_empty_period(
        self,
        camera_id: str,
        timestamp: datetime,
    ) -> list[TrackEvent]:
        """Detect empty periods — no detections for > threshold.

        Edge Case #6: Logs an anomaly if no person is detected
        for longer than empty_period_threshold_s.
        """
        events: list[TrackEvent] = []
        last_time = self._last_detection_time.get(camera_id)

        if last_time is not None:
            gap = (timestamp - last_time).total_seconds()
            if gap >= self._edge_cases.empty_period_threshold_s:
                logger.warning(
                    "empty_period_detected",
                    camera_id=camera_id,
                    gap_seconds=gap,
                    last_detection=last_time.isoformat(),
                    current_time=timestamp.isoformat(),
                )

        return events
