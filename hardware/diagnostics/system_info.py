#!/usr/bin/env python3
"""
System Information Diagnostics for Raspberry Pi Road Recorder.
Inspects hardware model, OS version, Python version, camera tools,
desktop/display status, storage mount points, and existing recordings.
Zero external dependencies (uses standard Python 3 libraries only).
"""

import os
import sys
import shutil
import platform
import subprocess
import getpass
from pathlib import Path


def get_rpi_model() -> str:
    """Read Raspberry Pi hardware model safely from device tree or cpuinfo."""
    model_paths = [
        Path("/proc/device-tree/model"),
        Path("/sys/firmware/devicetree/base/model"),
    ]
    for p in model_paths:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace").strip("\x00\n ")
        except Exception:
            pass

    # Fallback to /proc/cpuinfo
    try:
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.is_file():
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Model"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass

    return f"Generic / Non-Pi ({platform.machine()} - {platform.system()})"


def get_os_info() -> dict:
    """Parse OS details from /etc/os-release and platform."""
    info = {
        "os_name": platform.system(),
        "kernel_release": platform.release(),
        "arch": platform.machine(),
        "pretty_name": "Unknown Linux",
        "debian_version": "Unknown",
    }
    os_release = Path("/etc/os-release")
    if os_release.is_file():
        try:
            for line in os_release.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line:
                    key, val = line.split("=", 1)
                    val = val.strip("\"'")
                    if key == "PRETTY_NAME":
                        info["pretty_name"] = val
                    elif key == "VERSION_CODENAME":
                        info["debian_version"] = val
        except Exception:
            pass
    return info


def check_tool(tool_name: str) -> dict:
    """Check if a CLI executable exists and return its path and version if possible."""
    path = shutil.which(tool_name)
    result = {"available": path is not None, "path": path or "Not found", "version": None}
    if path:
        try:
            # Common flags to query version
            flag = "--version" if tool_name != "v4l2-ctl" else "--version"
            res = subprocess.run([tool_name, flag], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
            first_line = (res.stdout or res.stderr).splitlines()
            if first_line:
                result["version"] = first_line[0].strip()
        except Exception:
            pass
    return result


def check_python_module(module_name: str) -> bool:
    """Check if a Python module is importable without loading heavy dependencies."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def get_memory_info() -> dict:
    """Return total and available RAM in MB."""
    mem_info = {"total_mb": None, "available_mb": None}
    meminfo_file = Path("/proc/meminfo")
    if meminfo_file.is_file():
        try:
            for line in meminfo_file.read_text().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    if parts[0] == "MemTotal:":
                        mem_info["total_mb"] = int(parts[1]) // 1024
                    elif parts[0] == "MemAvailable:":
                        mem_info["available_mb"] = int(parts[1]) // 1024
        except Exception:
            pass
    return mem_info


def inspect_existing_test_recording() -> dict:
    """Check the pre-existing test recording at ~/road-recordings/test.h264 without modifying it."""
    home = Path.home()
    candidate = home / "road-recordings" / "test.h264"
    if candidate.exists():
        try:
            stat = candidate.stat()
            size_mb = round(stat.st_size / (1024 * 1024), 2)
            return {
                "exists": True,
                "path": str(candidate),
                "size_mb": size_mb,
                "size_bytes": stat.st_size,
                "readable": os.access(candidate, os.R_OK),
            }
        except Exception as e:
            return {"exists": True, "path": str(candidate), "error": str(e)}
    return {"exists": False, "path": str(candidate)}


def get_system_info() -> dict:
    """Aggregate complete hardware and system diagnostics."""
    os_info = get_os_info()
    mem_info = get_memory_info()
    username = getpass.getuser()
    home_dir = str(Path.home())

    display = os.environ.get("DISPLAY")
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    desktop_session = os.environ.get("XDG_CURRENT_DESKTOP")

    # Camera CLI tools
    tools_to_check = [
        "rpicam-hello",
        "rpicam-vid",
        "rpicam-still",
        "libcamera-hello",
        "libcamera-vid",
        "ffmpeg",
        "ffprobe",
        "v4l2-ctl",
    ]
    tools_status = {t: check_tool(t) for t in tools_to_check}

    # Python packages
    python_modules = {
        "picamera2": check_python_module("picamera2"),
        "yaml": check_python_module("yaml"),
        "cv2": check_python_module("cv2"),
    }

    # Recommended camera stack detection
    recommended_backend = "none"
    if tools_status["rpicam-vid"]["available"]:
        recommended_backend = "rpicam-vid"
    elif tools_status["libcamera-vid"]["available"]:
        recommended_backend = "libcamera-vid"
    elif python_modules["picamera2"]:
        recommended_backend = "picamera2"

    return {
        "rpi_model": get_rpi_model(),
        "os": os_info,
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "user": username,
        "home": home_dir,
        "memory": mem_info,
        "display": {
            "has_display": bool(display or wayland_display),
            "display": display,
            "wayland_display": wayland_display,
            "desktop_env": desktop_session,
        },
        "tools": tools_status,
        "python_modules": python_modules,
        "recommended_backend": recommended_backend,
        "existing_test_recording": inspect_existing_test_recording(),
    }


def print_system_info_report():
    """Format and print an executive terminal report."""
    info = get_system_info()
    os_info = info["os"]
    mem = info["memory"]
    disp = info["display"]

    print("=" * 65)
    print("       ROAD RECORDER - HARDWARE & SYSTEM DIAGNOSTICS")
    print("=" * 65)
    print(f"Hardware Model   : {info['rpi_model']}")
    print(f"Operating System : {os_info['pretty_name']} ({os_info['debian_version']})")
    print(f"Kernel & Arch    : {os_info['kernel_release']} ({os_info['arch']})")
    print(f"Active User      : {info['user']}")
    print(f"Home Directory   : {info['home']}")
    print(f"Python Runtime   : {info['python_version']} ({info['python_executable']})")
    if mem['total_mb']:
        print(f"System RAM       : {mem['total_mb']} MB total, {mem['available_mb']} MB available")
    print(f"Desktop/Display  : {'Available (' + (disp['desktop_env'] or 'Active GUI') + ')' if disp['has_display'] else 'Headless / No Display'}")
    print("-" * 65)
    print("CAMERA TOOLS CHECK:")
    for tool_name, data in info["tools"].items():
        symbol = "[OK]" if data["available"] else "[--]"
        ver = f" ({data['version']})" if data["version"] else ""
        print(f"  {symbol} {tool_name:16}: {data['path']}{ver}")

    print("-" * 65)
    print("PYTHON MODULES:")
    for mod, ok in info["python_modules"].items():
        symbol = "[OK]" if ok else "[--]"
        print(f"  {symbol} {mod:16}: {'Installed' if ok else 'Not installed'}")

    print("-" * 65)
    print(f"RECOMMENDED BACKEND: {info['recommended_backend']}")

    print("-" * 65)
    rec = info["existing_test_recording"]
    print("PRE-EXISTING RECORDING CHECK:")
    if rec["exists"]:
        print(f"  Found test recording: {rec['path']} ({rec.get('size_mb', 0)} MB)")
        print(f"  Readable: {rec.get('readable', False)} | Status: PRESERVED (NOT TOUCHED)")
    else:
        print(f"  No file found at {rec['path']}")
    print("=" * 65)


if __name__ == "__main__":
    print_system_info_report()
