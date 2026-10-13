"""
Camera Controller and Subprocess Lifecycle for Road Recorder.
Wraps hardware-supported H.264 capture via rpicam-vid / libcamera-vid or Picamera2.
Provides clean process supervision, signal handling, and error reporting.
"""

import os
import sys
import shutil
import signal
import subprocess
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

# Import hardware detection
from hardware.camera.camera_detect import detect_camera

logger = logging.getLogger("road_recorder.camera")


class CameraController:
    """Manages video capture process execution and lifecycle."""

    def __init__(
        self,
        backend: str = "auto",
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        codec: str = "h264",
        bitrate: int = 5000000,
        preview: bool = False
    ):
        self.requested_backend = backend.lower()
        self.width = width
        self.height = height
        self.fps = fps
        self.codec = codec.lower()
        self.bitrate = bitrate
        self.preview = preview

        self.resolved_backend = None
        self.active_process: Optional[subprocess.Popen] = None
        self.active_output_file: Optional[Path] = None
        self.start_monotonic: Optional[float] = None

        self._resolve_backend()

    def _resolve_backend(self):
        """Determine actual capture backend to use."""
        if self.requested_backend == "auto":
            detection = detect_camera()
            detected_backend = detection.get("backend")
            if detected_backend in ["rpicam-vid", "libcamera-vid", "picamera2"]:
                self.resolved_backend = detected_backend
            elif shutil.which("rpicam-vid"):
                self.resolved_backend = "rpicam-vid"
            elif shutil.which("libcamera-vid"):
                self.resolved_backend = "libcamera-vid"
            else:
                self.resolved_backend = "mock"  # For local/testing without camera hardware
        else:
            self.resolved_backend = self.requested_backend

        logger.info(f"Resolved camera backend: {self.resolved_backend}")

    def is_camera_available(self) -> Tuple[bool, str]:
        """Check if camera hardware and tool are ready for capture."""
        if self.resolved_backend in ["rpicam-vid", "libcamera-vid"]:
            bin_name = self.resolved_backend
            if not shutil.which(bin_name):
                return False, f"Camera binary '{bin_name}' is not found in PATH."
            # Check detection
            det = detect_camera()
            if not det["detected"]:
                return False, f"No physical camera detected by {bin_name}."
            return True, f"Camera verified via {bin_name}"

        elif self.resolved_backend == "picamera2":
            try:
                import picamera2
                return True, "Picamera2 module available."
            except ImportError:
                return False, "picamera2 module is not installed."

        elif self.resolved_backend == "mock":
            return True, "Using mock backend (testing / offline mode)"

        return False, f"Unknown or unsupported camera backend '{self.resolved_backend}'"

    def start_segment_capture(self, output_path: Path, duration_sec: int) -> Tuple[bool, str]:
        """
        Launch the capture process for a single segment of specified duration.
        """
        if self.is_running():
            return False, "A capture process is already running."

        self.active_output_file = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration_ms = duration_sec * 1000

        if self.resolved_backend in ["rpicam-vid", "libcamera-vid"]:
            cmd = [
                self.resolved_backend,
                "-t", str(duration_ms),
                "--width", str(self.width),
                "--height", str(self.height),
                "--framerate", str(self.fps),
                "--codec", self.codec,
                "--bitrate", str(self.bitrate),
                "--inline",  # Include SPS/PPS headers in video stream for safe recovery
                "-o", str(self.active_output_file)
            ]
            if not self.preview:
                cmd.append("-n")  # Disable preview window

            logger.info(f"Executing: {' '.join(cmd)}")
            try:
                self.active_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                self.start_monotonic = time.monotonic()
                return True, f"Capture started with {self.resolved_backend}"
            except Exception as e:
                logger.error(f"Failed to start {self.resolved_backend}: {e}")
                self.active_process = None
                return False, str(e)

        elif self.resolved_backend == "mock":
            # For non-RPi testing: simulate capture by writing simulated frames or sleep
            posix_path = self.active_output_file.as_posix()
            logger.info(f"[MOCK] Simulating capture to {posix_path} for {duration_sec}s")
            cmd = [
                sys.executable,
                "-c",
                f'import time, pathlib; p=pathlib.Path("{posix_path}"); p.write_bytes(b"\\x00\\x00\\x00\\x01\\x67" + b"\\x00"*10240); time.sleep({duration_sec})'
            ]
            self.active_process = subprocess.Popen(cmd)
            self.start_monotonic = time.monotonic()
            return True, "Mock capture started"

        return False, f"Backend {self.resolved_backend} start routine not implemented."

    def is_running(self) -> bool:
        """Check if capture subprocess is currently alive."""
        if self.active_process is None:
            return False
        return self.active_process.poll() is None

    def poll_status(self) -> Tuple[Optional[int], Optional[str]]:
        """
        Poll active process. Returns (exit_code, stderr) if finished, or (None, None) if running.
        """
        if self.active_process is None:
            return None, None

        ret = self.active_process.poll()
        if ret is not None:
            stderr = ""
            if self.active_process.stderr:
                try:
                    stderr = self.active_process.stderr.read()
                except Exception:
                    pass
            return ret, stderr
        return None, None

    def get_elapsed_seconds(self) -> float:
        """Return elapsed recording time for active segment."""
        if not self.is_running() or self.start_monotonic is None:
            return 0.0
        return round(time.monotonic() - self.start_monotonic, 1)

    def stop_capture(self, timeout_sec: float = 5.0) -> Tuple[bool, str]:
        """
        Gracefully terminate capture subprocess.
        Sends SIGINT/SIGTERM to allow encoder to flush stream headers, then waits.
        Kills process if it fails to exit cleanly before timeout.
        """
        if not self.is_running():
            self.active_process = None
            return True, "No active process"

        proc = self.active_process
        logger.info(f"Stopping capture process PID {proc.pid} gracefully...")

        try:
            # On Linux/Pi, send SIGINT so rpicam-vid writes trailing headers and flushes file
            if sys.platform != "win32" and hasattr(signal, "SIGINT"):
                proc.send_signal(signal.SIGINT)
            else:
                proc.terminate()

            proc.wait(timeout=timeout_sec)
            logger.info("Capture process exited cleanly.")
            self.active_process = None
            return True, "Clean stop"
        except subprocess.TimeoutExpired:
            logger.warning("Capture process did not exit in time; sending SIGKILL.")
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass
            self.active_process = None
            return False, "Forced kill after timeout"
        except Exception as e:
            logger.error(f"Error stopping capture process: {e}")
            self.active_process = None
            return False, str(e)
