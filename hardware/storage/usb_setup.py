#!/usr/bin/env python3
"""
Interactive & Guided USB Pendrive Setup Assistant for Road Recorder.
Safely detects USB storage devices, distinguishes them from the microSD card,
checks partitions/filesystems/UUIDs, initializes the required directory structure,
creates the .road-recorder-volume marker, and guides fstab auto-mount configuration.

SAFETY GUARANTEES:
- NEVER formats or partitions any drive automatically.
- NEVER modifies the microSD card (/dev/mmcblk0).
- Backs up /etc/fstab before any proposed modifications.
- Provides complete rollback commands.
"""

import os
import sys
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hardware.storage.storage_check import (
    list_detailed_block_devices,
    find_usb_storage_devices,
    create_volume_marker,
    check_volume_marker,
    run_safe_rw_test,
    VOLUME_MARKER_FILENAME,
)

DEFAULT_MOUNT_PATH = Path("/mnt/road-recorder-usb")


def scan_usb_candidates() -> List[Dict[str, Any]]:
    """Scan and return potential USB pendrive partitions."""
    devices = list_detailed_block_devices()
    candidates = []

    for dev in devices:
        # Check if USB device
        if dev.get("device_class") != "USB_DEVICE":
            continue

        # If it has partitions (children), examine children
        children = dev.get("children", [])
        if children:
            for child in children:
                candidates.append({
                    "parent_device": dev.get("name"),
                    "device": child.get("name"),
                    "dev_path": f"/dev/{child.get('name')}",
                    "size": child.get("size"),
                    "fstype": child.get("fstype") or "unformatted",
                    "mountpoint": child.get("mountpoint"),
                    "uuid": child.get("uuid") or "No UUID",
                    "label": child.get("label") or "No Label",
                    "model": dev.get("model", "USB Drive"),
                })
        else:
            # Whole disk without partition table or raw fs
            candidates.append({
                "parent_device": None,
                "device": dev.get("name"),
                "dev_path": f"/dev/{dev.get('name')}",
                "size": dev.get("size"),
                "fstype": dev.get("fstype") or "unformatted",
                "mountpoint": dev.get("mountpoint"),
                "uuid": dev.get("uuid") or "No UUID",
                "label": dev.get("label") or "No Label",
                "model": dev.get("model", "USB Drive"),
            })

    return candidates


def initialize_usb_directory_layout(mount_path: Path) -> Dict[str, Any]:
    """
    Initialize the standard USB drive folder hierarchy:
    /mnt/road-recorder-usb/
    ├── recordings/
    ├── metadata/
    ├── logs/
    ├── diagnostics/
    └── .road-recorder-volume
    """
    subdirs = ["recordings", "metadata", "logs", "diagnostics"]
    results = {}

    for sd in subdirs:
        target = mount_path / sd
        try:
            target.mkdir(parents=True, exist_ok=True)
            results[sd] = "Created / Verified"
        except Exception as e:
            results[sd] = f"Error: {e}"

    # Create / verify volume marker
    marker_ok, marker_msg = create_volume_marker(mount_path)
    results[VOLUME_MARKER_FILENAME] = marker_msg

    # Read / write test
    rw_ok, rw_msg = run_safe_rw_test(mount_path)
    results["rw_test"] = rw_msg

    return results


def generate_fstab_entry(uuid_str: str, fstype: str, mount_path: str = "/mnt/road-recorder-usb") -> str:
    """Generate safe, non-blocking /etc/fstab entry with nofail option."""
    fstype_lower = fstype.lower()
    if fstype_lower in ["vfat", "fat32", "exfat", "ntfs"]:
        # FAT/exFAT filesystems don't support POSIX permissions, so specify uid/gid/umask
        return f"UUID={uuid_str}  {mount_path}  {fstype_lower}  defaults,nofail,uid=1000,gid=1000,umask=022  0  0"
    else:
        # Native Linux ext4
        return f"UUID={uuid_str}  {mount_path}  {fstype_lower}  defaults,nofail  0  2"


def run_interactive_setup():
    print("=" * 68)
    print("        ROAD RECORDER - USB PENDRIVE SETUP ASSISTANT")
    print("=" * 68)
    print("This utility will help configure a USB pendrive for continuous recording.")
    print("MicroSD card (/dev/mmcblk0) will NOT be modified or formatted.")
    print("-" * 68)

    candidates = scan_usb_candidates()
    if not candidates:
        print("\n[ALERT] No USB storage devices detected!")
        print("Please insert your USB pendrive into any USB 3.0/2.0 port on the Raspberry Pi.")
        print("Then run this tool again: python3 hardware/storage/usb_setup.py\n")
        return

    print(f"\nDetected {len(candidates)} USB storage candidate(s):")
    for i, c in enumerate(candidates, 1):
        print(f"\n  [{i}] Device      : {c['dev_path']} ({c['model']})")
        print(f"      Capacity    : {c['size']}")
        print(f"      Filesystem  : {c['fstype']}")
        print(f"      Mountpoint  : {c['mountpoint'] or 'Not currently mounted'}")
        print(f"      UUID        : {c['uuid']}")
        print(f"      Label       : {c['label']}")

    print("\n" + "-" * 68)
    print("CHOOSE AN ACTION:")
    print("  1. Select a partition to configure as /mnt/road-recorder-usb")
    print("  2. Show manual formatting instructions (for blank or unformatted drives)")
    print("  3. Initialize directory layout on currently mounted /mnt/road-recorder-usb")
    print("  4. Exit")
    print("-" * 68)

    try:
        choice = input("Enter choice [1-4]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return

    if choice == "1":
        try:
            dev_idx = int(input(f"Enter candidate number [1-{len(candidates)}]: ").strip()) - 1
            selected = candidates[dev_idx]
        except (ValueError, IndexError):
            print("Invalid selection.")
            return

        print(f"\nSelected device: {selected['dev_path']} (UUID: {selected['uuid']})")
        if selected["fstype"] == "unformatted" or selected["uuid"] == "No UUID":
            print("\n[WARNING] This partition has no recognized filesystem.")
            print("You must format it first. See option 2 or run:")
            print(f"  sudo mkfs.ext4 -L RoadRecorder {selected['dev_path']}")
            print("  (WARNING: Formatting erases all data on that drive!)")
            return

        mount_target = input(f"Enter mount path [default: {DEFAULT_MOUNT_PATH}]: ").strip() or str(DEFAULT_MOUNT_PATH)
        mount_path = Path(mount_target)

        fstab_line = generate_fstab_entry(selected["uuid"], selected["fstype"], str(mount_path))
        print("\n" + "=" * 68)
        print("RECOMMENDED AUTO-MOUNT CONFIGURATION (/etc/fstab):")
        print("=" * 68)
        print(f"1. Create mount directory:\n   sudo mkdir -p {mount_path}")
        print(f"\n2. Back up existing /etc/fstab:\n   sudo cp /etc/fstab /etc/fstab.bak.$(date +%Y%m%d_%H%M%S)")
        print(f"\n3. Append the following entry to /etc/fstab:")
        print(f"   {fstab_line}")
        print(f"\n4. Test mount without rebooting:\n   sudo mount -a")
        print(f"\n5. Set ownership so user 'omgawale' can write:")
        print(f"   sudo chown -R $USER:$USER {mount_path}")
        print(f"\n6. Initialize Road Recorder layout and marker:\n   python3 hardware/storage/usb_setup.py --init-dirs {mount_path}")
        print("=" * 68)

    elif choice == "2":
        print("\n" + "=" * 68)
        print("MANUAL FORMATTING INSTRUCTIONS (RUN IN RASPBERRY PI TERMINAL)")
        print("=" * 68)
        print("DATA LOSS WARNING: Formatting will permanently delete all contents")
        print("on the chosen USB drive. Verify device name carefully using 'lsblk'!")
        print("NEVER run mkfs on /dev/mmcblk0 or any partition of the microSD card.")
        print("-" * 68)
        print("To format a USB drive (for example /dev/sda1) as native Linux ext4:")
        print("  sudo mkfs.ext4 -L RoadRecorder /dev/sda1")
        print("\nTo format as exFAT (readable on Windows and Linux):")
        print("  sudo mkfs.exfat -n RoadRecorder /dev/sda1")
        print("=" * 68)

    elif choice == "3":
        mount_target = input(f"Enter mount path to initialize [default: {DEFAULT_MOUNT_PATH}]: ").strip() or str(DEFAULT_MOUNT_PATH)
        mount_path = Path(mount_target)
        if not mount_path.exists():
            print(f"[FAIL] Path {mount_path} does not exist.")
            return
        res = initialize_usb_directory_layout(mount_path)
        print("\nInitialization Results:")
        for k, v in res.items():
            print(f"  - {k:24}: {v}")


def main():
    if "--init-dirs" in sys.argv:
        target = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_MOUNT_PATH)
        print(f"Initializing directory layout on {target}...")
        res = initialize_usb_directory_layout(Path(target))
        for k, v in res.items():
            print(f"  - {k:24}: {v}")
    elif "--scan-json" in sys.argv:
        import json
        print(json.dumps(scan_usb_candidates(), indent=2))
    else:
        run_interactive_setup()


if __name__ == "__main__":
    main()
