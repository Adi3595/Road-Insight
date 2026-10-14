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


def _decode_mount_field(field: str) -> str:
    """Decode octal escape sequences used in /proc/mounts (e.g. \\040 for space, \\011 for tab)."""
    import re
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), field)


def _is_path_covered_by_mount(target: Path, mount_pt: Path) -> bool:
    """Check if target path is equal to or within mount_pt using path-component boundaries."""
    try:
        target.relative_to(mount_pt)
        return True
    except ValueError:
        return False


def get_mount_info_for_path(target_path: Path, proc_mounts_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Determine the mount point, device, and filesystem type for a given path.
    Parses /proc/mounts safely with octal escape decoding and path-component boundary matching.
    """
    resolved = target_path.expanduser().resolve()
    info = {
        "target_path": str(resolved),
        "is_mount_point": False,
        "mount_point": None,
        "device": None,
        "fstype": None,
        "options": None,
        "is_root_filesystem": False,
        "is_microsd": False,
        "is_usb": False,
        "uuid": None,
    }

    # On Linux, inspect /proc/mounts
    proc_mounts = proc_mounts_path or Path("/proc/mounts")
    if proc_mounts.is_file():
        try:
            mounts = []
            for line in proc_mounts.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    raw_dev = _decode_mount_field(parts[0])
                    raw_mount = _decode_mount_field(parts[1])
                    mounts.append({
                        "device": raw_dev,
                        "mount_point": raw_mount,
                        "fstype": parts[2],
                        "options": parts[3].split(",")
                    })

            # Match mount paths using path-component boundaries (never naive string prefix)
            matching = [
                m for m in mounts
                if _is_path_covered_by_mount(resolved, Path(m["mount_point"]))
            ]
            if matching:
                # Sort by number of path components descending; longest matching mount point wins
                matching.sort(key=lambda m: len(Path(m["mount_point"]).parts), reverse=True)
                active = matching[0]
                active_mount_str = active["mount_point"]
                active_mount_path = Path(active_mount_str)

                info["mount_point"] = active_mount_str
                info["device"] = active["device"]
                info["fstype"] = active["fstype"]
                info["options"] = active["options"]
                info["is_root_filesystem"] = (active_mount_str == "/" or active["device"] in ("rootfs", "/dev/root"))

                # Check if target_path is an exact mount point (and not the root filesystem)
                is_exact_mount = (resolved == active_mount_path)
                has_ismount = hasattr(os.path, "ismount")
                os_ismount = os.path.ismount(str(resolved)) if has_ismount else True
                info["is_mount_point"] = is_exact_mount and os_ismount and not info["is_root_filesystem"]

                # Check device classification
                dev_str = str(active["device"])
                if "mmcblk" in dev_str:
                    info["is_microsd"] = True
                elif dev_str.startswith("/dev/sd") or "/dev/disk/by-" in dev_str or dev_str.startswith("/dev/ubd"):
                    info["is_usb"] = True

                info["uuid"] = get_device_uuid(dev_str, active["mount_point"])
        except Exception:
            pass
    else:
        # Fallback when /proc/mounts is not available (e.g. testing / Windows)
        if hasattr(os.path, "ismount") and os.path.ismount(str(resolved)):
            info["is_mount_point"] = True
            info["mount_point"] = str(resolved)

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


def check_volume_marker(mount_path: Path, expected_uuid: Optional[str] = None) -> Dict[str, Any]:
    """
    Verify presence and validity of the .road-recorder-volume marker file.
    Validates both the application identifier ('road-recorder') and the marker volume UUID.
    Never trusts a marker copied from another volume.
    """
    marker_path = mount_path / VOLUME_MARKER_FILENAME
    if not marker_path.exists() or not marker_path.is_file():
        return {
            "exists": False,
            "path": str(marker_path),
            "valid": False,
            "data": None,
            "error": "Volume marker file does not exist."
        }

    try:
        content = marker_path.read_text(encoding="utf-8").strip()
        data = json.loads(content)
        if not isinstance(data, dict):
            return {
                "exists": True,
                "path": str(marker_path),
                "valid": False,
                "data": None,
                "error": "Volume marker JSON is not an object."
            }

        if data.get("app") != "road-recorder":
            return {
                "exists": True,
                "path": str(marker_path),
                "valid": False,
                "data": data,
                "error": f"Invalid app identifier '{data.get('app')}'; expected 'road-recorder'."
            }

        if expected_uuid:
            marker_uuid = str(data.get("volume_id") or data.get("volume_uuid") or "").strip()
            if not marker_uuid or (marker_uuid.upper() != expected_uuid.strip().upper()):
                return {
                    "exists": True,
                    "path": str(marker_path),
                    "valid": False,
                    "data": data,
                    "error": (
                        f"Volume marker identity '{marker_uuid or 'EMPTY'}' does not match "
                        f"expected volume identity '{expected_uuid}'."
                    )
                }

        return {
            "exists": True,
            "path": str(marker_path),
            "valid": True,
            "data": data,
            "error": None
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
    marker = check_volume_marker(mount_path, expected_uuid=volume_id)
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
    Target directory must already exist (does NOT create directory if absent).
    Creates a unique test file, writes test bytes, reads them back, and removes the file.
    Failure to write, verify, or remove the test probe is treated as a safety failure.
    """
    if not target_dir.exists() or not target_dir.is_dir():
        return False, f"Target directory '{target_dir}' does not exist or is not a directory."

    test_id = uuid.uuid4().hex[:8]
    test_file = target_dir / f".rr_rw_probe_{test_id}.tmp"
    test_payload = b"ROAD_RECORDER_RW_TEST_PAYLOAD_OK"

    try:
        test_file.write_bytes(test_payload)
        read_back = test_file.read_bytes()
        try:
            test_file.unlink()
        except Exception as e:
            return False, f"Failed to remove test probe file during cleanup: {e}"

        if test_file.exists():
            return False, "Test probe file still present after unlink attempt."

        if read_back == test_payload:
            return True, "Read/write integrity test passed successfully."
        else:
            return False, "Data mismatch during read/write verification."
    except Exception as e:
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
    allow_fallback_to_sd: bool = False,
    run_write_probe: bool = True
) -> Dict[str, Any]:
    """
    Comprehensive verification to prevent accidental recording to the microSD card.
    Strict fail-closed ordering:
    1. Resolve intended mount path safely.
    2. Identify exact mount covering that path using path-component boundaries.
    3. Reject missing paths, unmounted paths, root filesystem, and microSD devices.
    4. Verify filesystem UUID against expected UUID.
    5. Check mount options (reject read-only filesystems).
    6. Validate volume marker (.road-recorder-volume) only on verified filesystem.
    7. Check storage capacity and free space.
    8. Execute read/write probe ONLY after all preceding safety checks pass.
    """
    mount_path = Path(mount_path_str).expanduser().resolve()
    reasons_blocked = []

    # Precondition 1: Check if mount path exists
    if not mount_path.exists():
        reasons_blocked.append(f"Mount path '{mount_path}' does not exist on the filesystem.")

    # Precondition 2: Mount point inspection
    mount_info = get_mount_info_for_path(mount_path)

    if require_verified_usb:
        # Check 1: Is it a mounted filesystem?
        if not mount_info["is_mount_point"]:
            reasons_blocked.append(
                f"Path '{mount_path}' is NOT a mounted filesystem. "
                "It appears to be an ordinary directory on the microSD card because the USB pendrive is not mounted."
            )

        # Check 2: Is it on microSD or root filesystem?
        if mount_info["is_microsd"] or mount_info["is_root_filesystem"]:
            reasons_blocked.append(
                f"Mount point resolves to root/microSD card device ({mount_info.get('device')}). "
                "Recording to microSD card is strictly prohibited."
            )

        # Check 3: Is it USB?
        if not mount_info["is_usb"] and mount_info.get("device"):
            reasons_blocked.append(
                f"Mounted device ({mount_info.get('device')}) could not be verified as a USB storage drive."
            )

        # Check 4: Expected UUID matching
        if expected_uuid:
            dev_uuid = mount_info.get("uuid") or get_device_uuid(mount_info.get("device"), str(mount_path))
            mount_info["uuid"] = dev_uuid
            if not dev_uuid or (dev_uuid.strip().upper() != expected_uuid.strip().upper()):
                reasons_blocked.append(
                    f"Mounted volume UUID '{dev_uuid or 'UNKNOWN'}' does not match required USB volume UUID '{expected_uuid}'."
                )

        # Check 5: Read-only mount options
        options = mount_info.get("options") or []
        if "ro" in options:
            reasons_blocked.append("Mounted filesystem is mounted as read-only ('ro' flag in mount options).")

    # Precondition 3: Volume marker check (only on existing path, not silently creating it)
    marker_info = {"exists": False, "valid": False, "path": str(mount_path / VOLUME_MARKER_FILENAME), "data": None}
    if mount_path.exists() and not mount_info["is_root_filesystem"]:
        marker_info = check_volume_marker(mount_path, expected_uuid=expected_uuid)
        if require_verified_usb and (not marker_info["exists"] or not marker_info["valid"]):
            err_msg = marker_info.get("error") or "missing or invalid"
            reasons_blocked.append(
                f"Volume marker ({VOLUME_MARKER_FILENAME}) on '{mount_path}' is invalid: {err_msg}."
            )

    # Precondition 4: Disk usage metrics
    usage_info = {"total_gb": 0.0, "free_gb": 0.0, "free_mb": 0.0, "used_pct": 0.0}
    if mount_path.exists():
        try:
            usage = shutil.disk_usage(str(mount_path))
            usage_info["total_gb"] = round(usage.total / (1024 ** 3), 2)
            usage_info["free_gb"] = round(usage.free / (1024 ** 3), 2)
            usage_info["free_mb"] = round(usage.free / (1024 ** 2), 2)
            usage_info["used_pct"] = round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0
        except Exception as e:
            if require_verified_usb:
                reasons_blocked.append(f"Could not query disk usage on '{mount_path}': {e}")

    # Precondition 5: Read/write probe - ONLY run after all preceding safety checks pass!
    rw_ok = False
    if len(reasons_blocked) == 0:
        if run_write_probe:
            rw_ok, rw_msg = run_safe_rw_test(mount_path)
            if not rw_ok:
                reasons_blocked.append(f"Storage write verification failed: {rw_msg}")
        else:
            # Non-invasive permission check when write probe is disabled
            is_writable = ("ro" not in (mount_info.get("options") or [])) and os.access(str(mount_path), os.W_OK)
            rw_ok = is_writable
            rw_msg = "Write permission verified via OS access (probe skipped)." if rw_ok else "Path is not writable."
            if not rw_ok:
                reasons_blocked.append(f"Storage write check failed: {rw_msg}")
    else:
        rw_msg = "Skipped write probe due to prior safety verification failures."

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
