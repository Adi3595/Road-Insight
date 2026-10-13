"""
Central Road Recorder Orchestrator.
Coordinates camera acquisition, continuous segmentation, USB storage safeguards,
atomic metadata indexing, process lifecycle, and clean shutdown handling.
"""

import os
import sys
import time
import signal
import threading
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from road_recorder.config import AppConfig
from road_recorder.camera import CameraController
from road_recorder.segment_manager import SegmentManager
from road_recorder.storage_manager import StorageManager
from road_recorder.recording_index import RecordingIndex
from road_recorder.logging_setup import setup_logger, close_logger_handlers

logger = logging.getLogger("road_recorder.engine")


class RoadRecorder:
    """Production-grade continuous video recorder state machine with USB enforcement."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = setup_logger(config.logs_path, config.log_level)

        # Core components directed to dedicated USB storage
        self.storage_mgr = StorageManager(
            mount_path=config.mount_path,
            require_verified_usb=config.require_verified_usb,
            allow_fallback_to_sd=config.allow_fallback_to_sd,
            volume_uuid=config.volume_uuid,
            critical_free_space_mb=config.critical_free_space_mb,
            low_space_warning_percent=config.low_space_warning_percent,
        )
        self.segment_mgr = SegmentManager(
            base_output_dir=config.recordings_path,
            target_format=config.container_format,
        )
        self.index_mgr = RecordingIndex(
            index_file=config.metadata_path / "recording_index.jsonl"
        )
        self.camera = CameraController(
            backend=config.camera_backend,
            width=config.width,
            height=config.height,
            fps=config.fps,
            codec=config.codec,
            bitrate=config.bitrate,
            preview=config.preview,
        )

        # State tracking
        self.state = "STOPPED"  # STOPPED, STARTING, RECORDING, ROTATING, STOPPING, ERROR
        self.active_segment_info: Optional[Dict[str, Any]] = None
        self.latest_completed_segment: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.stop_requested = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        
        # Local lockfile to avoid creating unmounted files on target path
        self.lock_file = Path.home() / ".road_recorder.lock"

        # Recover prior interrupted segments if any
        try:
            recovered_count = self.segment_mgr.recover_orphaned_parts()
            if recovered_count > 0:
                logger.info(f"Crash recovery: found and preserved {recovered_count} prior session fragments.")
        except Exception:
            pass

    def _acquire_instance_lock(self) -> bool:
        """Ensure only one recorder process is accessing the hardware."""
        if self.lock_file.exists():
            try:
                pid_str = self.lock_file.read_text().strip()
                if pid_str:
                    old_pid = int(pid_str)
                    # Check if old_pid is still running
                    try:
                        os.kill(old_pid, 0)
                        logger.error(f"Another RoadRecorder instance is already running with PID {old_pid}.")
                        return False
                    except OSError:
                        logger.warning(f"Removing stale lock file from defunct PID {old_pid}.")
            except Exception:
                pass

        try:
            self.lock_file.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"Failed to create lock file at {self.lock_file}: {e}")
            return False

    def _release_instance_lock(self):
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass

    def start(self) -> Tuple[bool, str]:
        """Start the continuous recording engine in a managed background thread."""
        if self.state in ["RECORDING", "STARTING", "ROTATING"]:
            return False, "Recorder is already running."

        # Verify instance lock
        if not self._acquire_instance_lock():
            return False, "Another instance of Road Recorder is currently active."

        # Verify USB storage and prevent microSD recording
        can_rec, storage_msg = self.storage_mgr.can_start_recording()
        if not can_rec:
            self._release_instance_lock()
            self.state = "ERROR"
            self.last_error = storage_msg
            return False, storage_msg

        # Verify camera
        cam_ok, cam_msg = self.camera.is_camera_available()
        if not cam_ok:
            self._release_instance_lock()
            self.state = "ERROR"
            self.last_error = cam_msg
            return False, cam_msg

        self.stop_requested.clear()
        self.last_error = None
        self.state = "STARTING"
        self.worker_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self.worker_thread.start()
        return True, "Recording initiated successfully."

    def stop(self) -> Tuple[bool, str]:
        """Gracefully halt recording and finalize in-flight segment."""
        if self.state == "STOPPED":
            return True, "Recorder is already stopped."

        logger.info("Stop requested. Commencing clean shutdown sequence...")
        self.state = "STOPPING"
        self.stop_requested.set()

        # Stop camera subprocess
        self.camera.stop_capture(timeout_sec=5.0)

        # Wait for worker thread to finish finalization
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=10.0)

        self._release_instance_lock()
        self.state = "STOPPED"
        logger.info("Recorder shut down completely and cleanly.")
        return True, "Recorder stopped cleanly."

    def _recording_loop(self):
        """Main loop: manages segment lifecycle, rotations, and capacity monitors."""
        try:
            while not self.stop_requested.is_set():
                # 1. Verify USB mount is still connected and healthy
                if not self.storage_mgr.is_usb_connected_and_mounted():
                    err_msg = "USB recording drive was disconnected or became unmounted! Halting recording."
                    logger.critical(err_msg)
                    self.last_error = err_msg
                    break

                # 2. Check storage thresholds
                metrics = self.storage_mgr.get_storage_metrics()
                if metrics["status"] == "CRITICAL_STORAGE":
                    logger.critical(
                        f"CRITICAL STORAGE LIMIT REACHED ({metrics['free_mb']} MB free). Halting recording."
                    )
                    self.last_error = f"Halted: Critical storage threshold reached ({metrics['free_mb']} MB free)."
                    break

                # 3. Prepare next segment on USB drive
                raw_ext = "h264" if self.config.camera_backend != "picamera2" else "mp4"
                self.active_segment_info = self.segment_mgr.prepare_next_segment(extension=raw_ext)
                part_path = self.active_segment_info["raw_part_path"]

                # 4. Launch camera capture
                started, msg = self.camera.start_segment_capture(
                    output_path=part_path,
                    duration_sec=self.config.segment_duration_seconds
                )
                if not started:
                    logger.error(f"Failed to start camera capture: {msg}")
                    self.last_error = f"Camera start failure: {msg}"
                    break

                self.state = "RECORDING"
                logger.info(f"Segment recording started: {self.active_segment_info['recording_id']}")

                # 5. Monitor loop for current segment
                segment_target_sec = self.config.segment_duration_seconds
                while not self.stop_requested.is_set():
                    time.sleep(1.0)
                    elapsed = self.camera.get_elapsed_seconds()

                    # Periodic USB mount & space check while recording
                    if int(elapsed) % 5 == 0:
                        if not self.storage_mgr.is_usb_connected_and_mounted():
                            logger.critical("USB drive disconnected mid-segment! Initiating emergency stop.")
                            self.last_error = "USB drive disconnected mid-segment."
                            self.stop_requested.set()
                            break

                        cur_metrics = self.storage_mgr.get_storage_metrics()
                        if cur_metrics["status"] == "CRITICAL_STORAGE":
                            logger.critical("Critical storage reached mid-segment. Initiating emergency stop.")
                            self.stop_requested.set()
                            break

                    # Check if camera process exited or completed duration
                    exit_code, stderr = self.camera.poll_status()
                    if exit_code is not None:
                        logger.info(f"Capture process completed segment (exit code {exit_code}).")
                        break

                    if elapsed >= segment_target_sec:
                        logger.info(f"Segment duration {segment_target_sec}s reached. Rotating to next segment.")
                        self.camera.stop_capture(timeout_sec=3.0)
                        break

                # 6. Finalize completed or interrupted segment
                self.state = "ROTATING" if not self.stop_requested.is_set() else "STOPPING"
                if self.active_segment_info:
                    final_rec = self.segment_mgr.finalize_segment(
                        segment_info=self.active_segment_info,
                        width=self.config.width,
                        height=self.config.height,
                        fps=self.config.fps,
                        codec=self.config.codec,
                        backend=self.camera.resolved_backend or "unknown"
                    )
                    self.index_mgr.append_segment(final_rec)
                    self.latest_completed_segment = final_rec
                    self.active_segment_info = None

        except Exception as e:
            logger.exception(f"Unexpected error in recording loop: {e}")
            self.last_error = str(e)
            self.state = "ERROR"
        finally:
            self._finalize_active_on_exit()
            self._release_instance_lock()
            self.state = "STOPPED"

    def _finalize_active_on_exit(self):
        """Ensure any partially recorded active segment is finalized and not discarded."""
        if self.camera.is_running():
            self.camera.stop_capture(timeout_sec=3.0)

        if self.active_segment_info:
            try:
                final_rec = self.segment_mgr.finalize_segment(
                    segment_info=self.active_segment_info,
                    width=self.config.width,
                    height=self.config.height,
                    fps=self.config.fps,
                    codec=self.config.codec,
                    backend=self.camera.resolved_backend or "unknown"
                )
                self.index_mgr.append_segment(final_rec)
                self.latest_completed_segment = final_rec
            except Exception as e:
                logger.error(f"Error finalizing segment on exit: {e}")
            finally:
                self.active_segment_info = None

    def get_status_summary(self) -> Dict[str, Any]:
        """Aggregate comprehensive system, storage, and recording telemetry for UI."""
        storage = self.storage_mgr.get_storage_metrics()
        index_sum = self.index_mgr.get_summary()

        # Active segment metrics
        cur_file = None
        cur_size_mb = 0.0
        elapsed_sec = 0.0
        if self.active_segment_info:
            cur_file = self.active_segment_info["raw_part_path"].name
            part_path = self.active_segment_info["raw_part_path"]
            if part_path.exists():
                cur_size_mb = round(part_path.stat().st_size / (1024 * 1024), 2)
            elapsed_sec = self.camera.get_elapsed_seconds()

        return {
            "state": self.state,
            "backend": self.camera.resolved_backend,
            "elapsed_sec": elapsed_sec,
            "active_segment_id": self.active_segment_info["recording_id"] if self.active_segment_info else None,
            "active_file": cur_file,
            "active_size_mb": cur_size_mb,
            "latest_completed": self.latest_completed_segment,
            "total_segments": index_sum["total_segments"],
            "total_duration_min": index_sum["total_duration_min"],
            "mount_path": str(self.config.mount_path),
            "is_mount_point": storage.get("is_mount_point", False),
            "device": storage.get("device"),
            "fstype": storage.get("fstype"),
            "storage_status": storage.get("status"),
            "storage_free_gb": storage.get("free_gb", 0),
            "storage_free_mb": storage.get("free_mb", 0),
            "storage_used_pct": storage.get("used_pct", 0),
            "volume_marker_valid": storage.get("volume_marker_valid", False),
            "est_remaining_min": self.storage_mgr.estimate_remaining_time_minutes(self.config.bitrate),
            "last_error": self.last_error
        }
