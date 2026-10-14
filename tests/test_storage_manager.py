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


def test_missing_usb_causes_no_writes_or_creation(tmp_path: Path):
    """Safeguard: When USB is missing, no directories, probe files, or markers are created."""
    target_path = tmp_path / "absent_usb"
    mgr = StorageManager(mount_path=target_path, require_verified_usb=True)

    can_rec, msg = mgr.can_start_recording()
    assert can_rec is False
    assert not target_path.exists(), "Underlying absent mount directory must not be created!"


def test_similarly_prefixed_directory_rejected(tmp_path: Path):
    """Safeguard: /mnt/road-recorder-usb-other cannot masquerade as /mnt/road-recorder-usb."""
    from hardware.storage.storage_check import _is_path_covered_by_mount

    legit_mount = Path("/mnt/road-recorder-usb")
    impostor = Path("/mnt/road-recorder-usb-other")
    child = Path("/mnt/road-recorder-usb/recordings")

    assert not _is_path_covered_by_mount(impostor, legit_mount)
    assert _is_path_covered_by_mount(child, legit_mount)
    assert _is_path_covered_by_mount(legit_mount, legit_mount)


def test_wrong_uuid_rejected(tmp_path: Path):
    """Safeguard: USB partition with mismatched UUID is rejected."""
    fake_mount = tmp_path / "mnt_usb"
    fake_mount.mkdir()

    from hardware.storage.storage_check import verify_usb_mount_safety, create_volume_marker

    # Create marker with correct UUID
    create_volume_marker(fake_mount, volume_id="70AA-2D80")

    mock_mount_info = {
        "is_mount_point": True,
        "is_microsd": False,
        "is_root_filesystem": False,
        "is_usb": True,
        "device": "/dev/sdb1",
        "fstype": "exfat",
        "options": ["rw"],
        "uuid": "WRONG-UUID-9999"
    }

    with patch("hardware.storage.storage_check.get_mount_info_for_path", return_value=mock_mount_info):
        verif = verify_usb_mount_safety(
            mount_path_str=str(fake_mount),
            expected_uuid="70AA-2D80",
            require_verified_usb=True,
            run_write_probe=True
        )
        assert verif["safe_to_start"] is False
        assert any("does not match required USB volume UUID" in r for r in verif["reasons_blocked"])


def test_mismatched_marker_uuid_rejected(tmp_path: Path):
    """Safeguard: Drive with marker copied from another volume (mismatched volume_id) is rejected."""
    fake_mount = tmp_path / "mnt_usb"
    fake_mount.mkdir()

    from hardware.storage.storage_check import verify_usb_mount_safety, create_volume_marker

    # Marker created with DIFFERENT uuid
    create_volume_marker(fake_mount, volume_id="COPIED-FROM-ANOTHER-DRIVE")

    mock_mount_info = {
        "is_mount_point": True,
        "is_microsd": False,
        "is_root_filesystem": False,
        "is_usb": True,
        "device": "/dev/sda1",
        "fstype": "exfat",
        "options": ["rw"],
        "uuid": "70AA-2D80"
    }

    with patch("hardware.storage.storage_check.get_mount_info_for_path", return_value=mock_mount_info):
        verif = verify_usb_mount_safety(
            mount_path_str=str(fake_mount),
            expected_uuid="70AA-2D80",
            require_verified_usb=True
        )
        assert verif["safe_to_start"] is False
        assert any("does not match expected volume identity" in r for r in verif["reasons_blocked"])


def test_readonly_filesystem_rejected(tmp_path: Path):
    """Safeguard: Read-only mount ('ro') is rejected immediately."""
    fake_mount = tmp_path / "mnt_usb"
    fake_mount.mkdir()

    from hardware.storage.storage_check import verify_usb_mount_safety, create_volume_marker
    create_volume_marker(fake_mount, volume_id="70AA-2D80")

    mock_mount_info = {
        "is_mount_point": True,
        "is_microsd": False,
        "is_root_filesystem": False,
        "is_usb": True,
        "device": "/dev/sda1",
        "fstype": "exfat",
        "options": ["ro"],  # Read-only!
        "uuid": "70AA-2D80"
    }

    with patch("hardware.storage.storage_check.get_mount_info_for_path", return_value=mock_mount_info):
        verif = verify_usb_mount_safety(
            mount_path_str=str(fake_mount),
            expected_uuid="70AA-2D80",
            require_verified_usb=True
        )
        assert verif["safe_to_start"] is False
        assert any("read-only" in r for r in verif["reasons_blocked"])


def test_probe_never_attempted_if_preconditions_fail(tmp_path: Path):
    """Safeguard: Write probe is NEVER called if UUID, marker, or mount checks fail."""
    fake_mount = tmp_path / "mnt_usb"
    fake_mount.mkdir()

    from hardware.storage.storage_check import verify_usb_mount_safety

    mock_mount_info = {
        "is_mount_point": False,  # Not mounted!
        "is_microsd": False,
        "is_root_filesystem": False,
        "is_usb": False,
        "device": None,
        "fstype": None,
        "options": [],
        "uuid": None
    }

    with patch("hardware.storage.storage_check.get_mount_info_for_path", return_value=mock_mount_info):
        with patch("hardware.storage.storage_check.run_safe_rw_test") as mock_probe:
            verif = verify_usb_mount_safety(
                mount_path_str=str(fake_mount),
                expected_uuid="70AA-2D80",
                require_verified_usb=True,
                run_write_probe=True
            )
            assert verif["safe_to_start"] is False
            # PROBE MUST NEVER BE CALLED!
            mock_probe.assert_not_called()
