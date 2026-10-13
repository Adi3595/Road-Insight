#!/usr/bin/env python3
"""
Storage Capacity, Health, and USB Volume Verification Diagnostic for Road Recorder.
Inspects block devices, distinguishes the microSD card (mmcblk0) from USB pendrives,
verifies mount points, filesystem UUIDs, and .road-recorder-volume markers.
Includes non-destructive read/write verification.
Zero external dependencies (uses standard Python 3 libraries only).
"""

import os
import sys
import shutil
import subprocess
import json
import uuid
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple


VOLUME_MARKER_FILENAME = ".road-recorder-volume"


def get_mount_info_for_path(target_path: Path) -> Dict[str, Any]:
    """
    Determine the mount point, device, and filesystem type for a given path.
    Parses /proc/mounts (or 'findmnt' / 'df') on Linux.
    """
    resolved = target_path.expanduser().resolve()
    info = {
        "target_path": str(resolved),
        "is_mount_point": os.path.ismount(str(resolved)) if hasattr(os.path, "ismount") else False,
        "mount_point": None,
        "device": None,
        "fstype": None,
        "options": None,
        "is_root_filesystem": False,
        "is_microsd": False,
        "is_usb": False,
    }

    # On Linux, inspect /proc/mounts
    proc_mounts = Path("/proc/mounts")
    if proc_mounts.is_file():
        try:
            mounts = []
            for line in proc_mounts.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    mounts.append({
                        "device": parts[0],
                        "mount_point": parts[1],
                        "fstype": parts[2],
                        "options": parts[3].split(",")
                    })

            # Find longest matching mount point prefix for resolved path
            matching = [m for m in mounts if str(resolved).startswith(m["mount_point"])]
            if matching:
                # Longest mount path is the actual mount
                matching.sort(key=lambda m: len(m["mount_point"]), reverse=True)
                active = matching[0]
                info["mount_point"] = active["mount_point"]
                info["device"] = active["device"]
                info["fstype"] = active["fstype"]
                info["options"] = active["options"]
                info["is_root_filesystem"] = (active["mount_point"] == "/")
                
                # Check device type
                dev_str = active["device"]
                if "mmcblk" in dev_str:
                    info["is_microsd"] = True
                elif dev_str.startswith("/dev/sd") or "/dev/disk/by-" in dev_str:
                    info["is_usb"] = True
                
                info["uuid"] = get_device_uuid(dev_str, active["mount_point"])
        except Exception:
            pass

    return info


def get_device_uuid(device_path: Optional[str] = None, mount_point: Optional[str] = None) -> Optional[str]:
    """Retrieve filesystem UUID safely via findmnt, lsblk, or blkid."""
    if mount_point and shutil.which("findmnt"):
        try:
            res = subprocess.run(
                ["findmnt", "-n", "-o", "UUID", mount_point],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2
            )
            val = res.stdout.strip()
            if val:
                return val
        except Exception:
            pass

    if device_path and shutil.which("lsblk"):
        try:
            res = subprocess.run(
                ["lsblk", "-no", "UUID", device_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2
            )
            val = res.stdout.strip()
            if val:
                return val
        except Exception:
            pass

    if device_path and shutil.which("blkid"):
        try:
            res = subprocess.run(
                ["blkid", "-s", "UUID", "-o", "value", device_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2
            )
            val = res.stdout.strip()
            if val:
                return val
        except Exception:
            pass

    return None


def list_detailed_block_devices() -> List[Dict[str, Any]]:
    """
    List all block devices with size, fstype, mountpoint, model, transport, UUID, and PARTUUID.
    Distinguishes microSD card from USB drives safely.
    """
    if not shutil.which("lsblk"):
        return []

    try:
        # Use lsblk with JSON output
        cmd = [
            "lsblk", "-J",
            "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,LABEL,UUID,PARTUUID,MODEL,TRAN,ROTA,RM"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=4)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            devices = data.get("blockdevices", [])
            # Annotate device classification
            for dev in devices:
                _annotate_device(dev)
            return devices
    except Exception:
        pass

    return []


def _annotate_device(dev: Dict[str, Any]):
    """Recursively tag devices as microSD, USB, or other."""
    name = str(dev.get("name", ""))
    tran = str(dev.get("tran", "")).lower()
    rm = str(dev.get("rm", ""))  # removable flag

    if "mmcblk" in name:
        dev["device_class"] = "MICROSD_CARD"
    elif tran == "usb" or name.startswith("sd"):
        dev["device_class"] = "USB_DEVICE"
    else:
        dev["device_class"] = "UNKNOWN"

    for child in dev.get("children", []):
        child["device_class"] = dev["device_class"]
        _annotate_device(child)


def find_usb_storage_devices() -> List[Dict[str, Any]]:
    """Scan and return all USB block devices (disks and partitions)."""
    devices = list_detailed_block_devices()
    usb_drives = []

    for dev in devices:
        if dev.get("device_class") == "USB_DEVICE":
            usb_drives.append(dev)

    return usb_drives


def check_volume_marker(mount_path: Path) -> Dict[str, Any]:
    """
    Verify presence and validity of the .road-recorder-volume marker file.
    """
    marker_path = mount_path / VOLUME_MARKER_FILENAME
    if not marker_path.exists():
        return {
            "exists": False,
            "path": str(marker_path),
            "valid": False,
            "data": None
        }

    try:
        content = marker_path.read_text(encoding="utf-8").strip()
        data = json.loads(content)
        is_valid = data.get("app") == "road-recorder"
        return {
            "exists": True,
            "path": str(marker_path),
            "valid": is_valid,
            "data": data
        }
    except Exception as e:
        return {
            "exists": True,
            "path": str(marker_path),
            "valid": False,
            "error": str(e),
            "data": None
        }


def create_volume_marker(mount_path: Path, volume_id: Optional[str] = None) -> Tuple[bool, str]:
    """
    Safely initialize a .road-recorder-volume marker file on the target mount.
    Never overwrites if already valid.
    """
    marker = check_volume_marker(mount_path)
    if marker["exists"] and marker["valid"]:
        return True, "Volume marker already present and valid."

    marker_path = mount_path / VOLUME_MARKER_FILENAME
    vol_uuid = volume_id or str(uuid.uuid4())
    data = {
        "app": "road-recorder",
        "volume_id": vol_uuid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "description": "Road Recorder Dedicated USB Volume"
    }

    try:
        marker_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True, f"Created volume marker with ID {vol_uuid}"
    except Exception as e:
        return False, f"Failed to create volume marker: {e}"


def run_safe_rw_test(target_dir: Path) -> Tuple[bool, str]:
    """
    Execute a non-destructive read/write test inside target directory.
    Creates a unique test file, writes test bytes, reads them back, and removes the file.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    test_id = uuid.uuid4().hex[:8]
    test_file = target_dir / f".rr_rw_probe_{test_id}.tmp"
    test_payload = b"ROAD_RECORDER_RW_TEST_PAYLOAD_OK"

    try:
        test_file.write_bytes(test_payload)
        read_back = test_file.read_bytes()
        test_file.unlink()

        if read_back == test_payload:
            return True, "Read/write integrity test passed successfully."
        else:
            return False, "Data mismatch during read/write verification."
    except Exception as e:
        # Attempt cleanup if file exists
        if test_file.exists():
            try:
                test_file.unlink()
            except Exception:
                pass
        return False, f"Read/write test failed: {e}"


def verify_usb_mount_safety(
    mount_path_str: str = "/mnt/road-recorder-usb",
    expected_uuid: Optional[str] = None,
    require_verified_usb: bool = True,
    allow_fallback_to_sd: bool = False
) -> Dict[str, Any]:
    """
    Comprehensive verification to prevent accidental recording to the microSD card.
    1. Checks if mount_path exists and is a mount point.
    2. Verifies the mounted device is a USB drive and NOT mmcblk0 (microSD).
    3. Checks volume marker.
    4. Runs read/write test.
    5. Returns pass/fail with actionable diagnostics.
    """
    mount_path = Path(mount_path_str).expanduser().resolve()
    mount_info = get_mount_info_for_path(mount_path)
    marker_info = check_volume_marker(mount_path)

    # Disk usage
    usage_info = {"total_gb": 0, "free_gb": 0, "free_mb": 0, "used_pct": 0}
    try:
        usage = shutil.disk_usage(str(mount_path))
        usage_info["total_gb"] = round(usage.total / (1024 ** 3), 2)
        usage_info["free_gb"] = round(usage.free / (1024 ** 3), 2)
        usage_info["free_mb"] = round(usage.free / (1024 ** 2), 2)
        usage_info["used_pct"] = round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0
    except Exception:
        pass

    # Read/Write verification
    rw_ok, rw_msg = run_safe_rw_test(mount_path) if mount_path.exists() else (False, "Mount path does not exist.")

    # Safety checks
    reasons_blocked = []

    if not mount_path.exists():
        reasons_blocked.append(f"Mount path '{mount_path}' does not exist on the filesystem.")
    
    if require_verified_usb:
        # Check 1: Is it a mount point?
        if not mount_info["is_mount_point"]:
            reasons_blocked.append(
                f"Path '{mount_path}' is NOT a mounted filesystem. "
                "It appears to be an ordinary directory on the microSD card because the USB pendrive is not mounted."
            )

        # Check 2: Is it on microSD?
        if mount_info["is_microsd"] or mount_info["is_root_filesystem"]:
            reasons_blocked.append(
                f"Mount point resolves to microSD card device ({mount_info.get('device')}). "
                "Recording to microSD card is strictly prohibited."
            )

        # Check 3: Is it USB?
        if not mount_info["is_usb"] and mount_info.get("device"):
            reasons_blocked.append(
                f"Mounted device ({mount_info.get('device')}) could not be verified as a USB storage drive."
            )

        # Check 4: Volume marker
        if not marker_info["exists"] or not marker_info["valid"]:
            reasons_blocked.append(
                f"Volume marker ({VOLUME_MARKER_FILENAME}) is missing or invalid on '{mount_path}'."
            )

        # Check 5: Expected UUID matching
        if expected_uuid:
            dev_uuid = mount_info.get("uuid") or get_device_uuid(mount_info.get("device"), str(mount_path))
            mount_info["uuid"] = dev_uuid
            if not dev_uuid or (dev_uuid.strip().upper() != expected_uuid.strip().upper()):
                reasons_blocked.append(
                    f"Mounted volume UUID '{dev_uuid or 'UNKNOWN'}' does not match required USB volume UUID '{expected_uuid}'."
                )

    if not rw_ok:
        reasons_blocked.append(f"Storage write verification failed: {rw_msg}")

    # Safe to start decision
    safe_to_start = (len(reasons_blocked) == 0)

    return {
        "safe_to_start": safe_to_start,
        "mount_path": str(mount_path),
        "mount_info": mount_info,
        "marker_info": marker_info,
        "usage": usage_info,
        "rw_test": {"success": rw_ok, "message": rw_msg},
        "reasons_blocked": reasons_blocked,
        "rejection_message": "USB recording drive not available. Recording has not started." if not safe_to_start else None
    }


def print_storage_diagnostic_report(mount_path_str: str = "/mnt/road-recorder-usb"):
    """Format and print an executive terminal report."""
    print("=" * 68)
    print("       ROAD RECORDER - USB PENDRIVE STORAGE DIAGNOSTICS")
    print("=" * 68)

    verification = verify_usb_mount_safety(mount_path_str)
    mount_info = verification["mount_info"]
    marker = verification["marker_info"]
    usage = verification["usage"]

    print(f"Target USB Mount Path    : {verification['mount_path']}")
    print(f"Is Mount Point?          : {'YES' if mount_info['is_mount_point'] else 'NO (Ordinary directory on root/SD)'}")
    print(f"Mounted Device           : {mount_info.get('device') or 'None'}")
    print(f"Filesystem Type          : {mount_info.get('fstype') or 'Unknown'}")
    print(f"Volume Marker Present?   : {'YES (Valid)' if marker['valid'] else ('YES (Invalid)' if marker['exists'] else 'NO')}")
    print(f"Storage Free Space       : {usage['free_gb']} GB ({usage['free_mb']} MB) - {usage['used_pct']}% Used")
    print(f"Read/Write Test          : {'[PASSED]' if verification['rw_test']['success'] else '[FAILED] ' + verification['rw_test']['message']}")
    print("-" * 68)

    print("BLOCK DEVICE HIERARCHY (MICROSD vs USB):")
    devices = list_detailed_block_devices()
    if not devices:
        print("  lsblk not available or no devices detected.")
    else:
        for d in devices:
            d_class = d.get("device_class", "UNKNOWN")
            d_name = d.get("name", "")
            d_size = d.get("size", "")
            d_model = d.get("model", "")
            print(f"  [{d_class:<12}] {d_name} ({d_size}) {d_model}")
            for c in d.get("children", []):
                c_name = c.get("name", "")
                c_size = c.get("size", "")
                c_fs = c.get("fstype", "") or "raw"
                c_mount = c.get("mountpoint") or "not mounted"
                c_uuid = c.get("uuid", "")
                print(f"    └─ {c_name} ({c_size}, {c_fs}) -> {c_mount} [UUID: {c_uuid}]")

    print("-" * 68)
    if verification["safe_to_start"]:
        print("[SUCCESS] USB pendrive is verified, mounted, and READY for recording!")
    else:
        print("[BLOCKED] USB recording drive is NOT available:")
        for r in verification["reasons_blocked"]:
            print(f"  * {r}")
        print(f"\nResult: {verification['rejection_message']}")
        print("\nNext step: Run 'python3 hardware/storage/usb_setup.py' to configure the USB drive.")
    print("=" * 68)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "/mnt/road-recorder-usb"
    print_storage_diagnostic_report(target)
