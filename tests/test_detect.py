# PROMPT: Fix coverage for pipeline/detect.py
# CHANGES MADE: Added tests for PersonDetector, mocking YOLO and cv2

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import numpy as np
import pytest

from pipeline.detect import PersonDetector, DetectionResult
from pipeline.config import PipelineConfig, CameraConfig, DetectionConfig

@pytest.fixture
def mock_config():
    config = MagicMock(spec=PipelineConfig)
    config.detection = DetectionConfig(model="yolov8n.pt", person_class_id=0, frame_skip=1, confidence_threshold=0.0)
    return config

@pytest.fixture
def mock_camera_config():
    return CameraConfig(
        camera_id="CAM_TEST",
        source="test.mp4",
        clip_start_time=datetime(2026, 3, 3, 14, 0, 0, tzinfo=timezone.utc),
        fps_override=30.0,
        zones=[]
    )

@patch("pipeline.detect.YOLO")
def test_person_detector_init(mock_yolo, mock_config):
    detector = PersonDetector(mock_config)
    mock_yolo.assert_called_once_with("yolov8n.pt")
    assert detector._person_class_id == 0

@patch("pipeline.detect.YOLO")
def test_detect_frame(mock_yolo, mock_config):
    # Mock YOLO result
    mock_result = MagicMock()
    
    box1 = MagicMock()
    box1.xyxy = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: [10.0, 20.0, 30.0, 40.0]))]
    box1.conf = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: 0.9))]
    box1.cls = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: 0))]
    
    mock_result.boxes = [box1]
    
    mock_yolo_instance = MagicMock()
    mock_yolo_instance.return_value = [mock_result]
    mock_yolo.return_value = mock_yolo_instance

    detector = PersonDetector(mock_config)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    
    detections = detector.detect_frame(frame)
    
    assert len(detections) == 1
    assert detections[0].bbox == (10.0, 20.0, 30.0, 40.0)
    assert detections[0].confidence == 0.9
    assert detections[0].class_id == 0

@patch("pipeline.detect.cv2.VideoCapture")
@patch("pipeline.detect.YOLO")
@patch("pipeline.detect.Path.exists", return_value=True)
def test_process_video(mock_exists, mock_yolo, mock_videocapture, mock_config, mock_camera_config):
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.get.side_effect = lambda prop: 30.0 if prop == 5 else 2.0 # cv2.CAP_PROP_FPS=5, cv2.CAP_PROP_FRAME_COUNT=7
    
    # Return 2 frames, then stop
    frame_array = np.zeros((100, 100, 3), dtype=np.uint8)
    mock_cap.read.side_effect = [(True, frame_array), (True, frame_array), (False, None)]
    
    mock_videocapture.return_value = mock_cap
    
    # Mock detection result
    mock_yolo_instance = MagicMock()
    mock_result = MagicMock()
    mock_result.boxes = [] # Empty boxes
    mock_yolo_instance.return_value = [mock_result]
    mock_yolo.return_value = mock_yolo_instance
    
    detector = PersonDetector(mock_config)
    frames = list(detector.process_video("test.mp4", mock_camera_config))
    
    assert len(frames) == 2
    assert frames[0].frame_index == 0
    assert frames[1].frame_index == 1
    assert frames[0].camera_id == "CAM_TEST"

@patch("pipeline.detect.Path.exists", return_value=False)
@patch("pipeline.detect.YOLO")
def test_process_video_file_not_found(mock_yolo, mock_exists, mock_config, mock_camera_config):
    detector = PersonDetector(mock_config)
    with pytest.raises(FileNotFoundError):
        list(detector.process_video("missing.mp4", mock_camera_config))

@patch("pipeline.detect.cv2.VideoCapture")
@patch("pipeline.detect.Path.exists", return_value=True)
@patch("pipeline.detect.YOLO")
def test_process_video_cannot_open(mock_yolo, mock_exists, mock_videocapture, mock_config, mock_camera_config):
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    mock_videocapture.return_value = mock_cap
    
    detector = PersonDetector(mock_config)
    with pytest.raises(RuntimeError):
        list(detector.process_video("corrupt.mp4", mock_camera_config))
