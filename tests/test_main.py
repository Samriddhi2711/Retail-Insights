# PROMPT: Fix coverage for pipeline/main.py
# CHANGES MADE: Added tests for pipeline orchestrator

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import numpy as np

from pipeline.main import run_pipeline, _process_single_camera, main
from pipeline.config import PipelineConfig, CameraConfig
from pipeline.detect import FrameData, DetectionResult

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.store.store_id = "TEST_STORE"
    
    cam = CameraConfig(
        camera_id="CAM_1",
        source="test.mp4",
        clip_start_time=datetime(2026, 3, 3, 14, 0, 0, tzinfo=timezone.utc),
        zones=[]
    )
    config.cameras = [cam]
    config.detection.model = "yolov8n.pt"
    config.detection.confidence_threshold = 0.5
    config.detection.person_class_id = 0
    config.tracking.tracker_config = "bytetrack.yaml"
    config.tracking.persist = True
    return config

@patch("pipeline.main.EventEmitter")
@patch("pipeline.main.MultiCameraTracker")
@patch("pipeline.main.PersonDetector")
@patch("pipeline.main.load_config")
@patch("pipeline.main._process_single_camera", return_value=5)
def test_run_pipeline(mock_process, mock_load, mock_detector, mock_tracker, mock_emitter, mock_config):
    mock_load.return_value = mock_config
    mock_emitter_instance = MagicMock()
    mock_emitter_instance.get_stats.return_value = {"events_emitted": 5}
    mock_emitter.return_value = mock_emitter_instance
    
    stats = run_pipeline("dummy.yaml")
    
    assert stats["events_emitted"] == 5
    assert "elapsed_seconds" in stats
    mock_process.assert_called_once()
    mock_emitter_instance.close.assert_called_once()

@patch("pipeline.main.EventEmitter")
@patch("pipeline.main.MultiCameraTracker")
@patch("pipeline.main.PersonDetector")
@patch("pipeline.main.load_config")
def test_run_pipeline_camera_filter_not_found(mock_load, mock_detector, mock_tracker, mock_emitter, mock_config):
    mock_load.return_value = mock_config
    with pytest.raises(SystemExit):
        run_pipeline("dummy.yaml", camera_filter="CAM_NOT_FOUND")

@patch("ultralytics.YOLO")
def test_process_single_camera(mock_yolo, mock_config):
    mock_detector = MagicMock()
    mock_tracker = MagicMock()
    mock_emitter = MagicMock()
    
    # Mock FrameData
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    fd = FrameData(
        frame_index=0,
        frame=frame,
        detections=[],
        timestamp=datetime(2026, 3, 3, 14, 0, 0, tzinfo=timezone.utc),
        camera_id="CAM_1"
    )
    mock_detector.process_video.return_value = [fd]
    
    # Mock YOLO track results
    mock_yolo_instance = MagicMock()
    mock_result = MagicMock()
    box1 = MagicMock()
    box1.xyxy = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: [10.0, 20.0, 30.0, 40.0]))]
    box1.conf = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: 0.9))]
    box1.id = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: 1))]
    mock_result.boxes = [box1]
    mock_yolo_instance.track.return_value = [mock_result]
    mock_yolo.return_value = mock_yolo_instance
    
    # Mock tracker update
    mock_event = MagicMock()
    mock_tracker.update.return_value = [mock_event]
    mock_tracker.finalize.return_value = [mock_event]
    
    mock_emitter.emit_batch.return_value = 1
    
    events_count = _process_single_camera(
        detector=mock_detector,
        tracker=mock_tracker,
        emitter=mock_emitter,
        cam_config=mock_config.cameras[0],
        config=mock_config
    )
    
    assert events_count == 2 # 1 from update, 1 from finalize
    mock_emitter.emit_batch.assert_called()

@patch("pipeline.main.run_pipeline")
@patch("pipeline.main.argparse.ArgumentParser.parse_args")
def test_main_cli(mock_parse_args, mock_run_pipeline):
    mock_parse_args.return_value = MagicMock(config="dummy.yaml", camera=None)
    mock_run_pipeline.return_value = {"events": 0}
    main()
    mock_run_pipeline.assert_called_once_with(config_path="dummy.yaml", camera_filter=None)
