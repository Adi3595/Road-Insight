"""Tests for RoadRecorder orchestrator state machine and single-instance locks."""

import time
from pathlib import Path
from unittest.mock import patch

from road_recorder.config import AppConfig
from road_recorder.recorder import RoadRecorder
from road_recorder.logging_setup import close_logger_handlers


def mock_config(tmp_path: Path, require_verified_usb: bool = False):
    mount_dir = tmp_path / "mnt_usb"
    mount_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig({
        "camera": {
            "backend": "mock",
            "width": 1280,
            "height": 720,
            "fps": 30,
            "preview": False
        },
        "storage": {
            "mount_path": str(mount_dir),
            "require_verified_usb": require_verified_usb,
            "allow_fallback_to_sd": True,
            "critical_free_space_mb": 20,
            "low_space_warning_percent": 10
        },
        "recording": {
            "segment_duration_seconds": 5,
            "container_format": "mp4",
            "auto_start": False
        },
        "logging": {
            "level": "DEBUG",
        }
    })


def test_refuse_start_when_usb_unverified(tmp_path: Path):
    """Ensure recorder refuses to start if require_verified_usb is True and mount is not verified."""
    cfg = mock_config(tmp_path, require_verified_usb=True)
    rec = RoadRecorder(cfg)
    try:
        started, msg = rec.start()
        assert started is False
        assert "USB recording drive not available. Recording has not started." in msg
        assert rec.state == "ERROR"
    finally:
        rec.stop()
        close_logger_handlers()


def test_single_instance_lock(tmp_path: Path):
    cfg1 = mock_config(tmp_path, require_verified_usb=False)
    cfg2 = mock_config(tmp_path, require_verified_usb=False)
    rec1 = RoadRecorder(cfg1)
    rec2 = RoadRecorder(cfg2)

    try:
        # First instance starts
        started, msg = rec1.start()
        assert started is True
        assert rec1.state in ["STARTING", "RECORDING"]

        # Second instance must be rejected
        started2, msg2 = rec2.start()
        assert started2 is False
        assert "Another instance" in msg2

        # Stop first instance
        rec1.stop()
        assert rec1.state == "STOPPED"

        # Now second instance can start
        started2_after, _ = rec2.start()
        assert started2_after is True
        rec2.stop()
    finally:
        rec1.stop()
        rec2.stop()
        close_logger_handlers()


def test_recorder_graceful_stop_finalizes_segment(tmp_path: Path):
    cfg = mock_config(tmp_path, require_verified_usb=False)
    rec = RoadRecorder(cfg)

    try:
        started, msg = rec.start()
        assert started is True
        time.sleep(1.0)

        # Active segment should be recording
        status_mid = rec.get_status_summary()
        assert status_mid["state"] == "RECORDING"
        assert status_mid["active_file"] is not None

        # Stop gracefully
        rec.stop()
        assert rec.state == "STOPPED"

        # Verify index was written in metadata/ directory on USB
        records = rec.index_mgr.list_records()
        assert len(records) >= 1
        assert records[0]["finalization_status"] in ["finalized", "raw_preserved"]
    finally:
        rec.stop()
        close_logger_handlers()
