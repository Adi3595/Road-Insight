"""
Automated Integration and Unit Tests for Road Recorder Video Data API.
Covers authentication, storage readiness, session start/stop, conflict handling,
range streaming, path traversal prevention, and metadata queries.
"""

import os
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from road_recorder.config import AppConfig
from road_recorder.recorder import RoadRecorder
from road_recorder.logging_setup import close_logger_handlers
from road_recorder.api.dependencies import init_dependencies
from api_server import create_app

TEST_API_KEY = "test_secret_key_road_recorder_12345"


def create_test_setup(tmp_path: Path, require_verified_usb: bool = False):
    """Build isolated test environment with mock USB storage."""
    mount_dir = tmp_path / "mnt_usb"
    mount_dir.mkdir(parents=True, exist_ok=True)
    (mount_dir / "recordings").mkdir(parents=True, exist_ok=True)
    (mount_dir / "metadata").mkdir(parents=True, exist_ok=True)
    (mount_dir / "logs").mkdir(parents=True, exist_ok=True)

    config = AppConfig({
        "camera": {
            "backend": "mock",
            "width": 1280,
            "height": 720,
            "fps": 30,
            "preview": False
        },
        "storage": {
            "mount_path": str(mount_dir),
            "recordings_directory": "recordings",
            "metadata_directory": "metadata",
            "logs_directory": "logs",
            "require_verified_usb": require_verified_usb,
            "allow_fallback_to_sd": True,
            "volume_uuid": "70AA-2D80",
            "critical_free_space_mb": 50,
            "low_space_warning_percent": 10
        },
        "recording": {
            "segment_duration_seconds": 5,
            "container_format": "mp4",
            "auto_start": False
        },
        "api": {
            "host": "127.0.0.1",
            "port": 8000,
            "api_key_env_var": "ROAD_RECORDER_TEST_KEY",
            "api_key_file": str(mount_dir / ".road-recorder-api.key"),
            "max_duration_seconds": 3600
        },
        "logging": {
            "level": "DEBUG",
        }
    })

    # Set test API key in environment
    os.environ["ROAD_RECORDER_TEST_KEY"] = TEST_API_KEY

    recorder = RoadRecorder(config)
    app = create_app(config=config, recorder=recorder)
    client = TestClient(app)

    return client, recorder, config, mount_dir


def test_missing_api_key_returns_401(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        res = client.get("/api/v1/device/status")
        assert res.status_code == 401
        assert "Missing required authentication header" in res.json()["detail"]
    finally:
        recorder.stop()
        close_logger_handlers()


def test_invalid_api_key_returns_401(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        res = client.get("/api/v1/device/status", headers={"X-API-Key": "invalid_wrong_key"})
        assert res.status_code == 401
        assert "Invalid API key" in res.json()["detail"]
    finally:
        recorder.stop()
        close_logger_handlers()


def test_valid_api_key_device_readiness(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        res = client.get("/api/v1/device/status", headers={"X-API-Key": TEST_API_KEY})
        assert res.status_code == 200
        data = res.json()
        assert data["api_version"] == "1.0.0"
        assert "camera" in data
        assert "storage" in data
        assert "recording" in data
    finally:
        recorder.stop()
        close_logger_handlers()


def test_health_check_unauthenticated(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        # Mock storage ready
        mock_metrics = {"safe_to_record": True}
        with patch.object(recorder.storage_mgr, "get_storage_metrics", return_value=mock_metrics):
            res = client.get("/api/v1/health")
            assert res.status_code == 200
            assert res.json()["status"] == "healthy"
    finally:
        recorder.stop()
        close_logger_handlers()


def test_health_check_degraded_when_usb_unmounted(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path, require_verified_usb=True)
    try:
        mock_metrics = {
            "safe_to_record": False,
            "rejection_message": "USB recording drive not available. Recording has not started.",
            "reasons_blocked": ["Not a mounted filesystem."]
        }
        with patch.object(recorder.storage_mgr, "get_storage_metrics", return_value=mock_metrics):
            res = client.get("/api/v1/health")
            assert res.status_code == 503
            assert res.json()["detail"]["status"] == "degraded"
    finally:
        recorder.stop()
        close_logger_handlers()


def test_refuse_recording_without_verified_usb(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path, require_verified_usb=True)
    try:
        mock_metrics = {
            "safe_to_record": False,
            "rejection_message": "USB recording drive not available. Recording has not started."
        }
        with patch.object(recorder.storage_mgr, "get_storage_metrics", return_value=mock_metrics):
            res = client.post(
                "/api/v1/recordings/start",
                headers={"X-API-Key": TEST_API_KEY},
                json={"duration_seconds": 60}
            )
            assert res.status_code == 503
            assert "USB recording drive not available" in res.json()["detail"]
    finally:
        recorder.stop()
        close_logger_handlers()


def test_recording_start_and_stop_lifecycle(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        # Start recording session
        res_start = client.post(
            "/api/v1/recordings/start",
            headers={"X-API-Key": TEST_API_KEY},
            json={"duration_seconds": 10}
        )
        assert res_start.status_code == 200
        start_data = res_start.json()
        session_id = start_data["session_id"]
        assert start_data["status"] == "recording"
        assert session_id.startswith("session_")

        # Check session status
        res_sess = client.get(f"/api/v1/recordings/{session_id}", headers={"X-API-Key": TEST_API_KEY})
        assert res_sess.status_code == 200
        assert res_sess.json()["session_id"] == session_id

        time.sleep(1.0)

        # Stop recording session
        res_stop = client.post("/api/v1/recordings/stop", headers={"X-API-Key": TEST_API_KEY})
        assert res_stop.status_code == 200
        stop_data = res_stop.json()
        assert stop_data["status"] in ["stopped", "completed"]
        assert stop_data["session_id"] == session_id
    finally:
        recorder.stop()
        close_logger_handlers()


def test_duplicate_recording_request_conflict(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        res1 = client.post(
            "/api/v1/recordings/start",
            headers={"X-API-Key": TEST_API_KEY},
            json={"duration_seconds": 30}
        )
        assert res1.status_code == 200

        # Second start request must return 409 Conflict
        res2 = client.post(
            "/api/v1/recordings/start",
            headers={"X-API-Key": TEST_API_KEY},
            json={"duration_seconds": 30}
        )
        assert res2.status_code == 409
        assert "already active" in res2.json()["detail"]
    finally:
        recorder.stop()
        close_logger_handlers()


def test_invalid_recording_durations_rejected(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        # Less than 5s minimum (422 validation error)
        res_too_short = client.post(
            "/api/v1/recordings/start",
            headers={"X-API-Key": TEST_API_KEY},
            json={"duration_seconds": 2}
        )
        assert res_too_short.status_code == 422

        # Greater than max_duration_seconds 3600 (400 validation error)
        res_too_long = client.post(
            "/api/v1/recordings/start",
            headers={"X-API-Key": TEST_API_KEY},
            json={"duration_seconds": 99999}
        )
        assert res_too_long.status_code == 400
    finally:
        recorder.stop()
        close_logger_handlers()


def test_list_recordings_and_metadata(tmp_path: Path):
    client, recorder, config, mount_dir = create_test_setup(tmp_path)
    try:
        # Append mock records to recording index
        rec1 = {
            "recording_id": "RR_20261013_210000_001",
            "relative_path": "2026-10-13/RR_20261013_210000_001.mp4",
            "start_timestamp": "2026-10-13T21:00:00",
            "end_timestamp": "2026-10-13T21:05:00",
            "duration_sec": 300.0,
            "width": 1280,
            "height": 720,
            "fps": 30,
            "codec": "h264",
            "file_size_bytes": 12000000,
            "finalization_status": "finalized",
            "capture_backend": "rpicam-vid"
        }
        recorder.index_mgr.append_segment(rec1)

        # Query listing
        res = client.get("/api/v1/recordings", headers={"X-API-Key": TEST_API_KEY})
        assert res.status_code == 200
        data = res.json()
        assert data["total_recordings"] >= 1
        assert len(data["items"]) >= 1
        item = data["items"][0]
        assert item["recording_id"] == "RR_20261013_210000_001"
        assert item["download_url"] == "/api/v1/recordings/RR_20261013_210000_001/video"

        # Query metadata endpoint
        res_meta = client.get(
            "/api/v1/recordings/RR_20261013_210000_001/metadata",
            headers={"X-API-Key": TEST_API_KEY}
        )
        assert res_meta.status_code == 200
        assert res_meta.json()["recording_id"] == "RR_20261013_210000_001"
    finally:
        recorder.stop()
        close_logger_handlers()


def test_download_video_and_range_requests(tmp_path: Path):
    client, recorder, config, mount_dir = create_test_setup(tmp_path)
    try:
        # Create dummy MP4 file in recordings folder
        date_dir = config.recordings_path / "2026-10-13"
        date_dir.mkdir(parents=True, exist_ok=True)
        video_file = date_dir / "RR_20261013_220000_001.mp4"
        dummy_content = b"ftypisom" + b"\x00" * 10240
        video_file.write_bytes(dummy_content)

        recorder.index_mgr.append_segment({
            "recording_id": "RR_20261013_220000_001",
            "relative_path": "2026-10-13/RR_20261013_220000_001.mp4",
            "start_timestamp": "2026-10-13T22:00:00",
            "file_size_bytes": len(dummy_content),
            "finalization_status": "finalized"
        })

        # Full download (HTTP 200)
        res_full = client.get(
            "/api/v1/recordings/RR_20261013_220000_001/video",
            headers={"X-API-Key": TEST_API_KEY}
        )
        assert res_full.status_code == 200
        assert res_full.content == dummy_content
        assert res_full.headers["Content-Type"] == "video/mp4"

        # Range request (HTTP 206)
        res_range = client.get(
            "/api/v1/recordings/RR_20261013_220000_001/video",
            headers={"X-API-Key": TEST_API_KEY, "Range": "bytes=0-7"}
        )
        assert res_range.status_code == 206
        assert res_range.content == b"ftypisom"
        assert "Content-Range" in res_range.headers
    finally:
        recorder.stop()
        close_logger_handlers()


def test_download_nonexistent_and_path_traversal(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        # Nonexistent recording ID
        res_404 = client.get(
            "/api/v1/recordings/nonexistent_id/video",
            headers={"X-API-Key": TEST_API_KEY}
        )
        assert res_404.status_code == 404

        # Path traversal attack in recording ID
        res_attack = client.get(
            "/api/v1/recordings/..%2F..%2Fetc%2Fpasswd/video",
            headers={"X-API-Key": TEST_API_KEY}
        )
        assert res_attack.status_code in [400, 404]
    finally:
        recorder.stop()
        close_logger_handlers()


def test_live_stream_session_endpoints(tmp_path: Path):
    client, recorder, _, _ = create_test_setup(tmp_path)
    try:
        # Stream status
        res_stat = client.get("/api/v1/stream/status", headers={"X-API-Key": TEST_API_KEY})
        assert res_stat.status_code == 200
        assert res_stat.json()["stream_active"] is False

        # Start stream session
        res_start = client.post(
            "/api/v1/stream/start",
            headers={"X-API-Key": TEST_API_KEY},
            json={"protocol": "rtsp", "duration_seconds": 60}
        )
        assert res_start.status_code == 200
        data = res_start.json()
        assert data["status"] == "active"
        stream_id = data["stream_id"]

        # Stop stream session
        res_stop = client.post("/api/v1/stream/stop", headers={"X-API-Key": TEST_API_KEY})
        assert res_stop.status_code == 200
        assert res_stop.json()["status"] == "stopped"
    finally:
        recorder.stop()
        close_logger_handlers()
