#!/usr/bin/env python3
"""
Camera Detection Diagnostic for Raspberry Pi Road Recorder.
Identifies physical camera modules using the modern Raspberry Pi camera stack:
rpicam-hello / libcamera-hello, Picamera2, and V4L2.
Can be run standalone or imported by the recording engine.
"""

import sys
import shutil
import subprocess
import re
from typing import Dict, Any, List


def detect_camera_rpicam() -> Dict[str, Any]:
    """Detect camera using rpicam-hello --list-cameras or libcamera-hello --list-cameras."""
    binary = None
    if shutil.which("rpicam-hello"):
        binary = "rpicam-hello"
    elif shutil.which("libcamera-hello"):
        binary = "libcamera-hello"

    if not binary:
        return {"detected": False, "tool": None, "raw_output": "", "cameras": []}

    try:
        proc = subprocess.run(
            [binary, "--list-cameras"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        output = proc.stdout + "\n" + proc.stderr
        
        # Parse output like:
        # 0 : imx219 [3280x2464] (/base/soc/i2c0mux/i2c@1/imx219@10)
        #     Modes: 'SRGGB10_CSI2P' : 640x480 [206.65 fps - (1000, 752)/1280x960 crop] ...
        cameras = []
        pattern = re.compile(r"^(\d+)\s*:\s*(\w+)\s*\[(\d+x\d+)\](?:\s*\((.*?)\))?", re.MULTILINE)
        matches = pattern.findall(output)
        
        for m in matches:
            cam_idx, model, max_res, bus = m
            cameras.append({
                "index": int(cam_idx),
                "model": model,
                "max_resolution": max_res,
                "bus": bus.strip() if bus else "CSI",
            })

        # Sometimes output doesn't match standard pattern but shows Available cameras
        has_cam = len(cameras) > 0 or ("Available cameras" in output and "No cameras available" not in output)
        
        return {
            "detected": has_cam,
            "tool": binary,
            "raw_output": output.strip(),
            "cameras": cameras
        }
    except Exception as e:
        return {"detected": False, "tool": binary, "error": str(e), "cameras": []}


def detect_camera_picamera2() -> Dict[str, Any]:
    """Attempt detection via Picamera2 Python API."""
    try:
        from picamera2 import Picamera2
        picam = Picamera2()
        # camera_controls or list cameras
        cam_info = getattr(picam, "camera_properties", {})
        model = cam_info.get("Model", "Unknown sensor")
        picam.close()
        return {
            "detected": True,
            "tool": "picamera2",
            "cameras": [{"index": 0, "model": str(model), "bus": "CSI"}],
            "raw_output": f"Detected via Picamera2: {model}"
        }
    except Exception as e:
        return {"detected": False, "tool": "picamera2", "error": str(e), "cameras": []}


def detect_camera_v4l2() -> Dict[str, Any]:
    """Fallback check using v4l2-ctl --list-devices."""
    binary = shutil.which("v4l2-ctl")
    if not binary:
        return {"detected": False, "tool": None, "raw_output": "", "cameras": []}

    try:
        proc = subprocess.run(
            [binary, "--list-devices"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3
        )
        output = proc.stdout
        # Check if unicam or bcm2835 or rpi camera is listed
        found = any(k in output.lower() for k in ["unicam", "bcm2835", "imx", "ov5647", "camera"])
        return {
            "detected": found,
            "tool": "v4l2-ctl",
            "raw_output": output.strip(),
            "cameras": [{"index": 0, "model": "V4L2 Device", "bus": "CSI/USB"}] if found else []
        }
    except Exception as e:
        return {"detected": False, "tool": binary, "error": str(e), "cameras": []}


def detect_camera() -> Dict[str, Any]:
    """
    Main detection routine.
    Tries rpicam tools first, then Picamera2, then v4l2-ctl.
    Returns structured detection status.
    """
    # 1. rpicam / libcamera
    res = detect_camera_rpicam()
    if res["detected"]:
        return {
            "status": "OK",
            "detected": True,
            "backend": res["tool"].replace("-hello", "-vid"),
            "tool_used": res["tool"],
            "cameras": res["cameras"],
            "raw": res["raw_output"]
        }

    # 2. Picamera2
    res_pi2 = detect_camera_picamera2()
    if res_pi2["detected"]:
        return {
            "status": "OK",
            "detected": True,
            "backend": "picamera2",
            "tool_used": "picamera2",
            "cameras": res_pi2["cameras"],
            "raw": res_pi2["raw_output"]
        }

    # 3. v4l2-ctl
    res_v4l2 = detect_camera_v4l2()
    if res_v4l2["detected"]:
        return {
            "status": "OK",
            "detected": True,
            "backend": "v4l2",
            "tool_used": "v4l2-ctl",
            "cameras": res_v4l2["cameras"],
            "raw": res_v4l2["raw_output"]
        }

    # Nothing found or error
    details = []
    if res.get("raw_output"):
        details.append(res["raw_output"])
    if res_pi2.get("error"):
        details.append(f"Picamera2: {res_pi2['error']}")

    return {
        "status": "NO_CAMERA_DETECTED",
        "detected": False,
        "backend": None,
        "tool_used": res.get("tool") or "none",
        "cameras": [],
        "raw": "\n".join(details) if details else "No camera devices found by any stack."
    }


def print_detection_report():
    """Print a terminal friendly summary."""
    print("=" * 65)
    print("       ROAD RECORDER - CAMERA DETECTION DIAGNOSTIC")
    print("=" * 65)
    report = detect_camera()

    if report["detected"]:
        print("[SUCCESS] Physical camera detected!")
        print(f"Preferred capture backend : {report['backend']}")
        print(f"Detection utility used    : {report['tool_used']}")
        print(f"Number of cameras found   : {len(report['cameras'])}")
        for cam in report["cameras"]:
            print(f"  - Camera #{cam.get('index', 0)}: {cam.get('model', 'Unknown')} ({cam.get('bus', 'CSI')})")
            if "max_resolution" in cam:
                print(f"    Max native resolution: {cam['max_resolution']}")
        print("-" * 65)
        print("Raw detection output:")
        print(report["raw"])
    else:
        print("[FAIL] No camera detected by system.")
        print(f"Detection output / errors:\n{report['raw']}")
        print("-" * 65)
        print("TROUBLESHOOTING STEPS:")
        print("1. Check physical CSI ribbon cable orientation and seating on both ends.")
        print("   - Blue tape / stiffener faces the Ethernet/USB ports on Raspberry Pi 4.")
        print("2. Verify camera is enabled in /boot/firmware/config.txt (or /boot/config.txt):")
        print("   - Look for 'camera_auto_detect=1' or 'dtoverlay=imx219' / 'dtoverlay=ov5647'.")
        print("3. Check dmesg logs: sudo dmesg | grep -i -E 'camera|imx|ov5647|unicam'")
        print("4. Ensure no other application or preview process is currently holding the camera.")
    print("=" * 65)


if __name__ == "__main__":
    print_detection_report()
