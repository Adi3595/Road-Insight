"""Tests for StorageManager USB mount verification and capacity threshold safeguards."""

import shutil
from pathlib import Path
from unittest.mock import patch
from collections import namedtuple

from road_recorder.storage_manager import StorageManager
from hardware.storage.storage_check import create_volume_marker

UsageMock = namedtuple("usage", ["total", "used", "free"])


def test_refuse_recording_when_unmounted(tmp_path: Path):
    """Critical safeguard: Refuses to record when mount directory is ordinary directory on SD card."""
    fake_mount = tmp_path / "mnt_usb"
    fake_mount.mkdir()

    mgr = StorageManager(
        mount_path=fake_mount,
        require_verified_usb=True,
        allow_fallback_to_sd=False
    )
    can_rec, msg = mgr.can_start_recording()
    assert can_rec is False
    assert "USB recording drive not available. Recording has not started." in msg


def test_refuse_recording_when_path_missing(tmp_path: Path):
    """Safeguard: Refuses to record if USB mount path does not exist at all."""
    missing_path = tmp_path / "non_existent_mount"
    mgr = StorageManager(
        mount_path=missing_path,
        require_verified_usb=True,
        allow_fallback_to_sd=False
    )
    can_rec, msg = mgr.can_start_recording()
    assert can_rec is False
    assert "USB recording drive not available. Recording has not started." in msg


def test_refuse_recording_to_microsd(tmp_path: Path):
    """Safeguard: Detects and rejects microSD card device even if allow_fallback_to_sd is False."""
    fake_mount = tmp_path / "mnt_usb"
    fake_mount.mkdir()

    # Mock mount verification resolving to mmcblk0 (microSD)
    mock_verif = {
        "safe_to_start": False,
        "mount_path": str(fake_mount),
        "mount_info": {"is_mount_point": True, "is_microsd": True, "is_usb": False, "device": "/dev/mmcblk0p2"},
        "marker_info": {"exists": True, "valid": True},
        "rw_test": {"success": True, "message": "OK"},
        "usage": {"free_mb": 10000, "free_gb": 10, "used_pct": 20},
        "reasons_blocked": ["Resolves to microSD card."],
        "rejection_message": "USB recording drive not available. Recording has not started."
    }

    with patch("road_recorder.storage_manager.verify_usb_mount_safety", return_value=mock_verif):
        mgr = StorageManager(mount_path=fake_mount, require_verified_usb=True, allow_fallback_to_sd=False)
        can_rec, msg = mgr.can_start_recording()
        assert can_rec is False
        assert "USB recording drive not available. Recording has not started." in msg


def test_storage_critical_threshold_halts_recording(tmp_path: Path):
    """Safeguard: Halts recording when free space falls below critical threshold (e.g. 1024 MB)."""
    fake_mount = tmp_path / "mnt_usb"
    fake_mount.mkdir()

    mock_verif = {
        "safe_to_start": True,
        "mount_path": str(fake_mount),
        "mount_info": {"is_mount_point": True, "is_microsd": False, "is_usb": True, "device": "/dev/sda1"},
        "marker_info": {"exists": True, "valid": True},
        "rw_test": {"success": True, "message": "OK"},
        "usage": {"free_mb": 500, "free_gb": 0.5, "used_pct": 98},
        "reasons_blocked": [],
        "rejection_message": None
    }

    # Free space 500 MB (below critical limit 1024 MB)
    mock_usage = UsageMock(total=32 * 1024**3, used=31500 * 1024**2, free=500 * 1024**2)

    with patch("road_recorder.storage_manager.verify_usb_mount_safety", return_value=mock_verif):
        with patch("shutil.disk_usage", return_value=mock_usage):
            mgr = StorageManager(mount_path=fake_mount, critical_free_space_mb=1024)
            can_rec, msg = mgr.can_start_recording()
            assert can_rec is False
            assert "Critical storage threshold reached" in msg
            assert mgr.is_critical() is True


def test_verified_usb_healthy_allows_recording(tmp_path: Path):
    """Happy path: Verified USB mount with marker and healthy space allows recording."""
    fake_mount = tmp_path / "mnt_usb"
    fake_mount.mkdir()

    mock_verif = {
        "safe_to_start": True,
        "mount_path": str(fake_mount),
        "mount_info": {"is_mount_point": True, "is_microsd": False, "is_usb": True, "device": "/dev/sda1"},
        "marker_info": {"exists": True, "valid": True},
        "rw_test": {"success": True, "message": "OK"},
        "usage": {"free_mb": 15000, "free_gb": 15.0, "used_pct": 30},
        "reasons_blocked": [],
        "rejection_message": None
    }
    mock_usage = UsageMock(total=32 * 1024**3, used=17 * 1024**3, free=15 * 1024**3)

    with patch("road_recorder.storage_manager.verify_usb_mount_safety", return_value=mock_verif):
        with patch("shutil.disk_usage", return_value=mock_usage):
            mgr = StorageManager(mount_path=fake_mount, critical_free_space_mb=1024, low_space_warning_percent=15)
            can_rec, msg = mgr.can_start_recording()
            assert can_rec is True
            assert "verified and healthy" in msg
            assert mgr.is_critical() is False
            metrics = mgr.get_storage_metrics()
            assert metrics["status"] == "HEALTHY"
