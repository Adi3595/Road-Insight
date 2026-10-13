#!/usr/bin/env python3
"""
USB Pre-Flight Gate for Road Recorder.
Guarantees that the Road Recorder API and recording engine will NOT start
unless the required external USB pendrive (UUID: 70AA-2D80) is verified, mounted,
and writable at /mnt/road-recorder-usb. Strictly prevents running or writing to microSD.
"""

import sys
import os
from pathlib import Path
from typing import Dict, Any, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from road_recorder.config import load_config, AppConfig
from hardware.storage.storage_check import (
    verify_usb_mount_safety,
    check_volume_marker,
    VOLUME_MARKER_FILENAME,
)


def verify_usb_gate(config: AppConfig) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Perform rigorous, multi-point verification of the USB pendrive.
    Returns (is_valid, message, details).
    """
    mount_path = config.mount_path
    expected_uuid = config.volume_uuid

    # Run core hardware storage check
    verif = verify_usb_mount_safety(
        mount_path_str=str(mount_path),
        expected_uuid=expected_uuid if expected_uuid else None,
        require_verified_usb=config.require_verified_usb,
        allow_fallback_to_sd=config.allow_fallback_to_sd,
    )

    if not verif["safe_to_start"]:
        reasons = "; ".join(verif.get("reasons_blocked", []))
        msg = f"USB storage verification failed: {reasons}"
        return False, msg, verif

    # Check project directory and volume marker
    marker_info = check_volume_marker(mount_path)
    if not marker_info["exists"] or not marker_info["valid"]:
        msg = (
            f"Volume marker '{VOLUME_MARKER_FILENAME}' is missing or invalid on {mount_path}. "
            "Drive does not appear to be an initialized Road Recorder volume."
        )
        return False, msg, verif

    return True, "USB volume (70AA-2D80) verified, mounted, and writable.", verif


def main():
    """CLI pre-flight check for systemd ExecStartPre."""
    config = load_config("config.yaml")
    is_valid, msg, details = verify_usb_gate(config)

    if is_valid:
        print(f"[OK] {msg}")
        sys.exit(0)
    else:
        print(f"[BLOCKED] {msg}", file=sys.stderr)
        print("Road Recorder API startup halted: USB pendrive is required.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
