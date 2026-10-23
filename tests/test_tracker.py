# PROMPT: Fix coverage for pipeline/tracker.py
# CHANGES MADE: Cleaned up tests for tracker

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
import numpy as np

from pipeline.tracker import MultiCameraTracker, ReIDGallery, StaffClassifier, ZoneDetector, TrackEvent
from pipeline.config import PipelineConfig, CameraConfig, ZoneConfig, DetectionConfig, TrackingConfig, ReIDConfig, StaffConfig, ZoneBehaviorConfig, EdgeCaseConfig, HSVRange
from pipeline.detect import DetectionResult

@pytest.fixture
def mock_config():
    config = MagicMock(spec=PipelineConfig)
    
    zone = ZoneConfig(
        zone_id="SKINCARE",
        type="dwell",
        polygon=[[0, 0], [100, 0], [100, 100], [0, 100]],
        sku_zone="MOISTURISER"
    )
    
    cam = CameraConfig(
        camera_id="CAM_1",
        source="test.mp4",
        clip_start_time=datetime(2026, 3, 3, 14, 0, 0, tzinfo=timezone.utc),
        zones=[zone]
    )
    config.cameras = [cam]
    
    config.reid = ReIDConfig(model_name="test", similarity_threshold=0.7, gallery_update_interval=10)
    config.staff = StaffConfig(method="hsv_uniform", hsv_ranges=[], min_uniform_ratio=0.5)
    config.zones = ZoneBehaviorConfig(dwell_threshold_ms=1000)
    config.edge_cases = EdgeCaseConfig()
    
    return config

@patch("pipeline.tracker.ReIDGallery")
@patch("pipeline.tracker.StaffClassifier")
def test_tracker_update_new_track(mock_staff, mock_reid, mock_config):
    mock_reid_instance = MagicMock()
    mock_reid_instance.extract_embedding.return_value = np.zeros(512)
    mock_reid_instance.match_or_register.return_value = ("VIS_123456", False)
    mock_reid.return_value = mock_reid_instance
    
    mock_staff_instance = MagicMock()
    mock_staff_instance.classify.return_value = False
    mock_staff.return_value = mock_staff_instance
    
    tracker = MultiCameraTracker(mock_config)
    
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    detections = [
        DetectionResult(bbox=(10.0, 10.0, 20.0, 20.0), confidence=0.9, class_id=0)
    ]
    timestamp = datetime(2026, 3, 3, 14, 0, 1, tzinfo=timezone.utc)
    
    events = tracker.update("CAM_1", 0, frame, detections, timestamp, [1])
    
    assert len(events) >= 1
    assert events[0].event_type == "ENTRY"
    assert events[0].visitor_id == "VIS_123456"

@patch("pipeline.tracker.ReIDGallery")
@patch("pipeline.tracker.StaffClassifier")
def test_tracker_zone_transition_and_finalize(mock_staff, mock_reid, mock_config):
    mock_reid_instance = MagicMock()
    mock_reid_instance.extract_embedding.return_value = np.zeros(512)
    mock_reid_instance.match_or_register.return_value = ("VIS_123456", False)
    mock_reid.return_value = mock_reid_instance
    
    mock_staff_instance = MagicMock()
    mock_staff_instance.classify.return_value = False
    mock_staff.return_value = mock_staff_instance
    
    tracker = MultiCameraTracker(mock_config)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    
    # Inside the zone [0,0,100,100]
    det_in_zone = DetectionResult(bbox=(10.0, 10.0, 20.0, 20.0), confidence=0.9, class_id=0)
    ts1 = datetime(2026, 3, 3, 14, 0, 1, tzinfo=timezone.utc)
    events = tracker.update("CAM_1", 0, frame, [det_in_zone], ts1, [1])
    
    # Stay in zone for > dwell_threshold_ms
    ts2 = ts1 + timedelta(seconds=2)
    events2 = tracker.update("CAM_1", 1, frame, [det_in_zone], ts2, [1])
    
    # Move out of zone
    # Mock pointPolygonTest to return -1 (outside) by modifying cv2_mock in conftest?
    # Actually, we can just use another mock or assume it always returns 1.0 (inside) based on conftest
    # Let's override it in this test explicitly
    import cv2
    original_return = cv2.pointPolygonTest.return_value
    cv2.pointPolygonTest.side_effect = lambda poly, pt, measure: 1.0 if (0 <= pt[0] <= 100 and 0 <= pt[1] <= 100) else -1.0
    
    det_out_zone = DetectionResult(bbox=(150.0, 150.0, 160.0, 160.0), confidence=0.9, class_id=0)
    ts3 = ts2 + timedelta(seconds=1)
    events3 = tracker.update("CAM_1", 2, frame, [det_out_zone], ts3, [1])
    
    assert any(e.event_type == "ZONE_DWELL" for e in events3)
    assert any(e.event_type == "ZONE_EXIT" for e in events3)
    
    final_events = tracker.finalize("CAM_1", ts3 + timedelta(seconds=1))
    assert any(e.event_type == "EXIT" for e in final_events)
    
    # restore
    cv2.pointPolygonTest.side_effect = None
    cv2.pointPolygonTest.return_value = original_return

def test_zone_detector(mock_config):
    import cv2
    original_return = cv2.pointPolygonTest.return_value
    cv2.pointPolygonTest.side_effect = lambda poly, pt, measure: 1.0 if (0 <= pt[0] <= 100 and 0 <= pt[1] <= 100) else -1.0
    
    detector = ZoneDetector(mock_config.cameras[0].zones)
    
    zone_id = detector.detect_zone((10, 10, 20, 20))
    assert zone_id == "SKINCARE"
    
    zone_id2 = detector.detect_zone((150, 150, 160, 160))
    assert zone_id2 is None
    
    cv2.pointPolygonTest.side_effect = None
    cv2.pointPolygonTest.return_value = original_return

def test_reid_gallery():
    # Mock the lazy import
    import sys
    sys.modules['torchreid.utils'] = MagicMock()
    mock_extractor_instance = MagicMock()
    mock_extractor_instance.return_value = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: np.ones(512)))]
    sys.modules['torchreid.utils'].FeatureExtractor = MagicMock(return_value=mock_extractor_instance)
    
    config = ReIDConfig()
    gallery = ReIDGallery(config)
    
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    emb = gallery.extract_embedding(frame, (10, 10, 50, 50))
    
    vid, is_reentry = gallery.match_or_register(emb)
    assert vid.startswith("VIS_")
    assert not is_reentry
    
    vid2, is_reentry2 = gallery.match_or_register(emb)
    assert vid2 == vid
    assert not is_reentry2
    
    gallery.mark_exited(vid)
    vid3, is_reentry3 = gallery.match_or_register(emb)
    assert vid3 == vid
    assert is_reentry3

def test_staff_classifier():
    config = StaffConfig(method="hsv_uniform", hsv_ranges=[HSVRange(lower=[0,0,0], upper=[10,10,10])], min_uniform_ratio=0.5)
    classifier = StaffClassifier(config)
    
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    is_staff = classifier.classify(frame, (10, 10, 50, 50))
    assert not is_staff
