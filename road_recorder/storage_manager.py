"""
Storage Management and Disk Safeguards for Road Recorder.
Guarantees recording data is stored exclusively on a verified USB pendrive.
Prevents accidental fallback to the microSD card.
Enforces absolute (MB) and percentage-based capacity safety thresholds.
"""

import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

from hardware.storage.storage_check import (
    verify_usb_mount_safety,
    run_safe_rw_test,
    check_volume_marker,
    VOLUME_MARKER_FILENAME
)

logger = logging.getLogger("road_recorder.storage")


class StorageManager:
    """Manages USB pendrive mount validation, storage health, and safeguards."""

    def __init__(
        self,
        mount_path: Path,
        require_verified_usb: bool = True,
        allow_fallback_to_sd: bool = False,
        volume_uuid: str = "",
        critical_free_space_mb: int = 1024,
        low_space_warning_percent: float = 15.0,
    ):
        self.mount_path = mount_path.expanduser().resolve()
        self.require_verified_usb = require_verified_usb
        self.allow_fallback_to_sd = allow_fallback_to_sd
        self.volume_uuid = volume_uuid
        self.critical_free_space_mb = critical_free_space_mb
        self.low_space_warning_percent = low_space_warning_percent

    def check_permissions(self) -> bool:
        """Verify write access non-destructively."""
        if not self.mount_path.exists():
            return False
        rw_ok, _ = run_safe_rw_test(self.mount_path)
        return rw_ok

    def get_storage_metrics(self) -> Dict[str, Any]:
        """
        Query storage status, mount safety, and free capacity metrics.
        """
        # Run verification check
        verif = verify_usb_mount_safety(
            mount_path_str=str(self.mount_path),
            expected_uuid=self.volume_uuid or None,
            require_verified_usb=self.require_verified_usb,
            allow_fallback_to_sd=self.allow_fallback_to_sd
        )

        mount_info = verif["mount_info"]
        marker_info = verif["marker_info"]
        rw_test = verif["rw_test"]

        # If mount path exists, compute usage
        total_gb = 0.0
        used_gb = 0.0
        free_gb = 0.0
        free_mb = 0.0
        used_pct = 0.0
        free_pct = 0.0

        if self.mount_path.exists():
            try:
                usage = shutil.disk_usage(str(self.mount_path))
                total_gb = round(usage.total / (1024 ** 3), 2)
                used_gb = round(usage.used / (1024 ** 3), 2)
                free_gb = round(usage.free / (1024 ** 3), 2)
                free_mb = round(usage.free / (1024 * 1024), 2)
                used_pct = round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0
                free_pct = round((usage.free / usage.total) * 100, 1) if usage.total > 0 else 0
            except Exception as e:
                logger.error(f"Failed to query disk metrics for '{self.mount_path}': {e}")

        # Determine state
        if not verif["safe_to_start"]:
            if not mount_info.get("is_mount_point", False) and self.require_verified_usb:
                status = "NOT_MOUNTED"
            elif mount_info.get("is_microsd", False):
                status = "BLOCKED_MICROSD"
            elif not rw_test.get("success", False):
                status = "READ_ONLY"
            else:
                status = "UNVERIFIED"
        elif free_mb <= self.critical_free_space_mb:
            status = "CRITICAL_STORAGE"
        elif free_pct <= self.low_space_warning_percent or free_mb <= (self.critical_free_space_mb * 2):
            status = "LOW_STORAGE"
        else:
            status = "HEALTHY"

        return {
            "status": status,
            "safe_to_record": verif["safe_to_start"] and (status not in ["CRITICAL_STORAGE", "READ_ONLY"]),
            "mount_path": str(self.mount_path),
            "is_mount_point": mount_info.get("is_mount_point", False),
            "is_usb": mount_info.get("is_usb", False),
            "device": mount_info.get("device"),
            "fstype": mount_info.get("fstype"),
            "volume_marker_valid": marker_info.get("valid", False),
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "free_mb": free_mb,
            "used_pct": used_pct,
            "free_pct": free_pct,
            "writable": rw_test.get("success", False),
            "reasons_blocked": verif.get("reasons_blocked", []),
            "rejection_message": verif.get("rejection_message"),
        }

    def can_start_recording(self) -> Tuple[bool, str]:
        """
        Validate whether a recording session can safely begin.
        Enforces USB requirement, prevents microSD fallback, and checks space.
        """
        metrics = self.get_storage_metrics()

        # 1. Require verified USB check
        if self.require_verified_usb and not metrics["is_mount_point"]:
            msg = "USB recording drive not available. Recording has not started."
            logger.error(f"{msg} Reason: '{self.mount_path}' is not a mounted filesystem.")
            return False, msg

        # 2. Block recording to microSD card
        if not self.allow_fallback_to_sd and (metrics["status"] == "BLOCKED_MICROSD"):
            msg = "USB recording drive not available. Recording has not started."
            logger.error(f"{msg} Reason: Path resolves to microSD card.")
            return False, msg

        # 3. Check write access
        if not metrics["writable"]:
            msg = f"USB drive '{self.mount_path}' is not writable. Check mount options and permissions."
            logger.error(msg)
            return False, msg

        # 4. Critical space check
        if metrics["free_mb"] <= self.critical_free_space_mb:
            msg = (
                f"Critical storage threshold reached ({metrics['free_mb']} MB free <= "
                f"{self.critical_free_space_mb} MB limit). Recording cannot start."
            )
            logger.error(msg)
            return False, msg

        # 5. Low space warning (logs warning but allows starting)
        if metrics["status"] == "LOW_STORAGE":
            logger.warning(
                f"Low storage warning: {metrics['free_mb']} MB free ({metrics['free_pct']}%). "
                f"Threshold: {self.low_space_warning_percent}%."
            )

        return True, "USB storage status verified and healthy."

    def is_critical(self) -> bool:
        """Check if storage capacity has reached the critical stop threshold."""
        metrics = self.get_storage_metrics()
        return metrics["free_mb"] <= self.critical_free_space_mb

    def is_usb_connected_and_mounted(self) -> bool:
        """Quick check during recording to verify USB drive has not been disconnected."""
        if not self.require_verified_usb:
            return True
        metrics = self.get_storage_metrics()
        return metrics["is_mount_point"] and metrics["writable"]

    def estimate_remaining_time_minutes(self, bitrate_bps: int = 5000000) -> float:
        """Estimate remaining recording minutes before reaching critical stop threshold."""
        metrics = self.get_storage_metrics()
        free_mb = metrics.get("free_mb", 0)
        usable_mb = max(0.0, free_mb - self.critical_free_space_mb)

        # Bytes per min = (bitrate / 8) * 60
        bytes_per_min = (bitrate_bps / 8.0) * 60.0
        mb_per_min = bytes_per_min / (1024 * 1024)

        if mb_per_min <= 0:
            return 0.0
        return round(usable_mb / mb_per_min, 1)
