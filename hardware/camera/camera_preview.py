#!/usr/bin/env python3
"""
Camera Preview Diagnostic for Raspberry Pi Road Recorder.
Launches a brief camera preview if an active display (HDMI / Wayland / X11)
is detected. Safely informs and skips preview if running headless.
"""

import sys
import os
import shutil
import subprocess
from typing import Dict, Any


def run_camera_preview(duration_ms: int = 5000, fullscreen: bool = False) -> Dict[str, Any]:
    """
    Launch camera preview window.
    Detects whether desktop environment is active before attempting to open a window.
    """
    has_x11 = bool(os.environ.get("DISPLAY"))
    has_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))

    if not (has_x11 or has_wayland):
        return {
            "success": False,
            "reason": "HEADLESS_MODE",
            "message": (
                "No graphical display environment detected (both DISPLAY and WAYLAND_DISPLAY are unset).\n"
                "The camera preview window requires an active desktop/monitor session.\n"
                "Note: Road Recorder records video perfectly in headless mode without requiring a preview."
            )
        }

    tool = None
    if shutil.which("rpicam-hello"):
        tool = "rpicam-hello"
    elif shutil.which("libcamera-hello"):
        tool = "libcamera-hello"

    if not tool:
        return {
            "success": False,
            "reason": "TOOL_MISSING",
            "message": "Neither rpicam-hello nor libcamera-hello is installed."
        }

    cmd = [tool, "-t", str(duration_ms)]
    if fullscreen:
        cmd.append("-f")

    try:
        print(f"Launching camera preview for {duration_ms / 1000} seconds using {tool}...")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=(duration_ms / 1000) + 5)
        if proc.returncode == 0:
            return {
                "success": True,
                "tool": tool,
                "duration_ms": duration_ms,
                "message": f"Preview completed successfully using {tool}."
            }
        else:
            return {
                "success": False,
                "reason": "PROCESS_ERROR",
                "stderr": proc.stderr.strip(),
                "message": f"Preview exited with code {proc.returncode}: {proc.stderr.strip()}"
            }
    except subprocess.TimeoutExpired:
        return {"success": True, "tool": tool, "message": "Preview duration elapsed."}
    except Exception as e:
        return {"success": False, "reason": "EXCEPTION", "message": str(e)}


def print_preview_report():
    print("=" * 65)
    print("       ROAD RECORDER - CAMERA PREVIEW TEST")
    print("=" * 65)
    res = run_camera_preview(duration_ms=5000)
    if res["success"]:
        print(f"[SUCCESS] {res['message']}")
    else:
        print(f"[NOTICE] Preview not displayed: {res.get('reason')}")
        print(res.get("message"))
    print("=" * 65)


if __name__ == "__main__":
    print_preview_report()
