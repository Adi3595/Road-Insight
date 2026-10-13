#!/usr/bin/env python3
"""
Road Recorder - Unified Environment & Hardware Inspector.
Designed to run on the Raspberry Pi with Python 3 standard library.
Checks model, OS, user, camera detection, video tools, USB storage, and pre-existing files.
"""

import sys
import os
import json
from pathlib import Path

# Add repo root to sys.path so hardware package can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hardware.diagnostics.system_info import get_system_info, print_system_info_report
from hardware.camera.camera_detect import detect_camera, print_detection_report
from hardware.storage.storage_check import (
    verify_usb_mount_safety,
    print_storage_diagnostic_report,
    list_detailed_block_devices,
)


def main():
    print("\n" + "#" * 68)
    print("      ROAD RECORDER -- ENVIRONMENT & HARDWARE VERIFICATION")
    print("#" * 68 + "\n")

    print_system_info_report()
    print("\n")
    print_detection_report()
    print("\n")
    print_storage_diagnostic_report("/mnt/road-recorder-usb")
    print("\n")

    # If --json is passed, output pure JSON to a file or stdout
    if "--json" in sys.argv:
        data = {
            "system": get_system_info(),
            "camera": detect_camera(),
            "usb_storage": verify_usb_mount_safety("/mnt/road-recorder-usb"),
            "block_devices": list_detailed_block_devices()
        }
        json_path = Path("environment_report.json")
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[INFO] Detailed JSON report written to: {json_path.resolve()}")


if __name__ == "__main__":
    main()
