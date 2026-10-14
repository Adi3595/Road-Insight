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
        """Verify write access non-destructively without writing to unverified paths."""
        if not self.mount_path.exists():
            return False
        verif = verify_usb_mount_safety(
            mount_path_str=str(self.mount_path),
            expected_uuid=self.volume_uuid or None,
            require_verified_usb=self.require_verified_usb,
            allow_fallback_to_sd=self.allow_fallback_to_sd,
            run_write_probe=False
        )
        return verif["safe_to_start"] and verif["rw_test"].get("success", False)

    def get_storage_metrics(self) -> Dict[str, Any]:
        """
        Query storage status, mount safety, and free capacity metrics.
        Read-only telemetry: does not perform disk writes or probe file creation.
        """
        verif = verify_usb_mount_safety(
            mount_path_str=str(self.mount_path),
            expected_uuid=self.volume_uuid or None,
            require_verified_usb=self.require_verified_usb,
            allow_fallback_to_sd=self.allow_fallback_to_sd,
            run_write_probe=False
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
            elif mount_info.get("is_microsd", False) or mount_info.get("is_root_filesystem", False):
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
            "safe_to_record": verif["safe_to_start"] and (status not in ["CRITICAL_STORAGE", "READ_ONLY", "NOT_MOUNTED", "BLOCKED_MICROSD", "UNVERIFIED"]),
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
        Enforces full USB safety verification (device identity, UUID, marker, writability probe)
        and checks capacity thresholds. Fail closed on any failure.
        """
        # Run full verification including non-destructive read/write probe
        verif = verify_usb_mount_safety(
            mount_path_str=str(self.mount_path),
            expected_uuid=self.volume_uuid or None,
            require_verified_usb=self.require_verified_usb,
            allow_fallback_to_sd=self.allow_fallback_to_sd,
            run_write_probe=True
        )

        if not verif["safe_to_start"]:
            reasons = "; ".join(verif.get("reasons_blocked", []))
            rejection = verif.get("rejection_message") or "USB recording drive not available. Recording has not started."
            logger.error(f"{rejection} Reason: {reasons}")
            return False, rejection

        # Capacity check
        free_mb = verif["usage"].get("free_mb", 0.0)
        if free_mb <= self.critical_free_space_mb:
            msg = (
                f"Critical storage threshold reached ({free_mb} MB free <= "
                f"{self.critical_free_space_mb} MB limit). Recording cannot start."
            )
            logger.error(msg)
            return False, msg

        # Low space warning
        if free_mb <= (self.critical_free_space_mb * 2):
            logger.warning(
                f"Low storage warning: {free_mb} MB free. Threshold: {self.critical_free_space_mb * 2} MB."
            )

        return True, "USB storage status verified and healthy."

    def is_critical(self) -> bool:
        """Check if storage capacity has reached the critical stop threshold."""
        metrics = self.get_storage_metrics()
        return metrics["free_mb"] <= self.critical_free_space_mb

    def is_usb_connected_and_mounted(self) -> bool:
        """Verify USB drive remains verified, mounted, and healthy during active recording."""
        if not self.require_verified_usb:
            return True
        if not self.mount_path.exists():
            return False
        verif = verify_usb_mount_safety(
            mount_path_str=str(self.mount_path),
            expected_uuid=self.volume_uuid or None,
            require_verified_usb=True,
            allow_fallback_to_sd=self.allow_fallback_to_sd,
            run_write_probe=False
        )
        return bool(verif["safe_to_start"])

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
