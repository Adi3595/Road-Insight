#!/usr/bin/env python3
"""
Camera Test Capture Diagnostic for Raspberry Pi Road Recorder.
Captures a real short test clip (default 5 seconds, 1280x720, 30fps)
to verify sensor acquisition, hardware encoder, and file output.
Never overwrites existing files or ~/road-recordings/test.h264.
"""

import sys
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Any


def run_test_capture(duration_sec: int = 5, output_dir: str = None) -> Dict[str, Any]:
    """
    Execute a short test recording using rpicam-vid / libcamera-vid or picamera2.
    Returns status dict with path, size, and duration.
    """
    if output_dir is None:
        target_dir = Path.home() / "road-recorder-data" / "diagnostics"
    else:
        target_dir = Path(output_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_file = target_dir / f"test_clip_{timestamp}.h264"

    # Identify tool
    tool = None
    if shutil.which("rpicam-vid"):
        tool = "rpicam-vid"
    elif shutil.which("libcamera-vid"):
        tool = "libcamera-vid"

    if tool:
        ms = duration_sec * 1000
        cmd = [
            tool,
            "-t", str(ms),
            "--width", "1280",
            "--height", "720",
            "--framerate", "30",
            "--codec", "h264",
            "-n",  # No preview window during test
            "-o", str(out_file)
        ]
        start_t = time.time()
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=duration_sec + 10)
            elapsed = round(time.time() - start_t, 2)
            
            if out_file.exists() and out_file.stat().st_size > 0:
                size_bytes = out_file.stat().st_size
                size_mb = round(size_bytes / (1024 * 1024), 2)
                return {
                    "success": True,
                    "tool": tool,
                    "file_path": str(out_file),
                    "size_bytes": size_bytes,
                    "size_mb": size_mb,
                    "elapsed_sec": elapsed,
                    "cmd": " ".join(cmd),
                    "stderr": proc.stderr.strip()
                }
            else:
                return {
                    "success": False,
                    "tool": tool,
                    "file_path": str(out_file),
                    "error": "Capture command executed but output file was not created or has 0 bytes.",
                    "stderr": proc.stderr.strip(),
                    "cmd": " ".join(cmd)
                }
        except subprocess.TimeoutExpired:
            return {"success": False, "tool": tool, "error": f"Capture timed out after {duration_sec + 10} seconds."}
        except Exception as e:
            return {"success": False, "tool": tool, "error": str(e)}

    # Picamera2 fallback
    try:
        from picamera2 import Picamera2
        import time as pytime
        picam = Picamera2()
        video_config = picam.create_video_configuration(main={"size": (1280, 720), "format": "XBGR8888"})
        picam.configure(video_config)
        
        # Test file
        out_file_mp4 = target_dir / f"test_clip_{timestamp}.mp4"
        start_t = time.time()
        picam.start_recording(str(out_file_mp4))
        pytime.sleep(duration_sec)
        picam.stop_recording()
        picam.close()
        elapsed = round(time.time() - start_t, 2)

        if out_file_mp4.exists() and out_file_mp4.stat().st_size > 0:
            size_bytes = out_file_mp4.stat().st_size
            return {
                "success": True,
                "tool": "picamera2",
                "file_path": str(out_file_mp4),
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "elapsed_sec": elapsed
            }
        else:
            return {"success": False, "tool": "picamera2", "error": "Picamera2 recorded 0 bytes."}
    except Exception as e:
        return {
            "success": False,
            "tool": "none",
            "error": f"Neither rpicam-vid, libcamera-vid, nor Picamera2 could record: {e}"
        }


def print_test_report():
    print("=" * 65)
    print("       ROAD RECORDER - CAMERA HARDWARE RECORDING TEST")
    print("=" * 65)
    print("Initiating 5-second test recording (1280x720 @ 30 FPS, H.264)...")
    res = run_test_capture(duration_sec=5)

    if res["success"]:
        print("[SUCCESS] Camera captured live video stream successfully!")
        print(f"Backend tool used : {res['tool']}")
        print(f"Recorded file     : {res['file_path']}")
        print(f"File size         : {res['size_mb']} MB ({res['size_bytes']} bytes)")
        print(f"Capture duration  : {res['elapsed_sec']} seconds")
        print("-" * 65)
        print("Note: The pre-existing test recording (~/road-recordings/test.h264)")
        print("was NOT touched or overwritten.")
    else:
        print("[FAIL] Test recording failed.")
        print(f"Error details : {res.get('error')}")
        if res.get("stderr"):
            print(f"Command error output:\n{res['stderr']}")
    print("=" * 65)


if __name__ == "__main__":
    print_test_report()
