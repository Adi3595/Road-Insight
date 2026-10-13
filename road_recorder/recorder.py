"""
Central Road Recorder Orchestrator.
Coordinates camera acquisition, continuous segmentation, USB storage safeguards,
atomic metadata indexing, process lifecycle, clean shutdown handling,
and API session management.
"""

import os
import sys
import time
import uuid
import signal
import threading
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

from road_recorder.config import AppConfig
from road_recorder.camera import CameraController
from road_recorder.segment_manager import SegmentManager
from road_recorder.storage_manager import StorageManager
from road_recorder.recording_index import RecordingIndex
from road_recorder.logging_setup import setup_logger, close_logger_handlers

logger = logging.getLogger("road_recorder.engine")


class RoadRecorder:
    """Production-grade continuous video recorder state machine with USB enforcement and API session support."""

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

        # Session tracking for API consumers
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.current_session_id: Optional[str] = None
        self.requested_duration_seconds: Optional[int] = None
        self.session_start_time: Optional[datetime] = None

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

    def start_session(self, duration_seconds: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:
        """
        Start a recording session (either bounded duration or continuous).
        Returns (success, message, session_id).
        """
        if self.state in ["RECORDING", "STARTING", "ROTATING"]:
            return False, "A recording session is already active.", self.current_session_id

        # Verify instance lock
        if not self._acquire_instance_lock():
            return False, "Another instance of Road Recorder is currently active.", None

        # Verify USB storage and prevent microSD recording
        can_rec, storage_msg = self.storage_mgr.can_start_recording()
        if not can_rec:
            self._release_instance_lock()
            self.state = "ERROR"
            self.last_error = storage_msg
            return False, storage_msg, None

        # Verify camera availability
        cam_ok, cam_msg = self.camera.is_camera_available()
        if not cam_ok:
            self._release_instance_lock()
            self.state = "ERROR"
            self.last_error = cam_msg
            return False, cam_msg, None

        # Generate unique session ID
        now = datetime.now()
        session_id = f"session_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.current_session_id = session_id
        self.requested_duration_seconds = duration_seconds
        self.session_start_time = now

        # Initialize session tracking dictionary
        self.sessions[session_id] = {
            "session_id": session_id,
            "status": "recording",
            "start_time": now.isoformat(),
            "end_time": None,
            "requested_duration_seconds": duration_seconds,
            "elapsed_seconds": 0.0,
            "recording_ids": [],
            "finalized_files": [],
            "error": None
        }

        self.stop_requested.clear()
        self.last_error = None
        self.state = "STARTING"
        self.worker_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self.worker_thread.start()
        logger.info(f"Session {session_id} started (requested duration: {duration_seconds}s).")
        return True, "Recording session started successfully.", session_id

    def start(self) -> Tuple[bool, str]:
        """Backward-compatible continuous start."""
        ok, msg, _ = self.start_session(duration_seconds=None)
        return ok, msg

    def stop_session(self) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Stop active recording session safely, finalize in-flight segment, and return session summary.
        """
        if self.state == "STOPPED":
            if self.current_session_id and self.current_session_id in self.sessions:
                return True, "Session already stopped.", self.sessions[self.current_session_id]
            return True, "Recorder is already stopped.", None

        logger.info(f"Stop requested for session {self.current_session_id}. Finalizing...")
        self.state = "STOPPING"
        self.stop_requested.set()

        # Stop camera subprocess
        self.camera.stop_capture(timeout_sec=5.0)

        # Wait for worker thread to finish finalization
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=10.0)

        self._release_instance_lock()
        self.state = "STOPPED"

        session_summary = None
        if self.current_session_id and self.current_session_id in self.sessions:
            sess = self.sessions[self.current_session_id]
            sess["status"] = "stopped"
            sess["end_time"] = datetime.now().isoformat()
            session_summary = sess

        self.current_session_id = None
        self.requested_duration_seconds = None
        logger.info("Recorder shut down completely and cleanly.")
        return True, "Recording stopped cleanly.", session_summary

    def stop(self) -> Tuple[bool, str]:
        """Backward-compatible stop."""
        ok, msg, _ = self.stop_session()
        return ok, msg

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session status and metadata by ID."""
        sess = self.sessions.get(session_id)
        if not sess:
            return None
        # If active, update live elapsed
        if sess["status"] == "recording" and self.session_start_time:
            sess["elapsed_seconds"] = round((datetime.now() - self.session_start_time).total_seconds(), 1)
        return sess

    def _recording_loop(self):
        """Main loop: manages segment lifecycle, rotations, and capacity monitors."""
        active_sess_id = self.current_session_id
        try:
            total_session_elapsed = 0.0

            while not self.stop_requested.is_set():
                # 1. Verify USB mount is still connected and healthy
                if not self.storage_mgr.is_usb_connected_and_mounted():
                    err_msg = "USB recording drive was disconnected or became unmounted! Halting recording."
                    logger.critical(err_msg)
                    self.last_error = err_msg
                    if active_sess_id and active_sess_id in self.sessions:
                        self.sessions[active_sess_id]["error"] = err_msg
                        self.sessions[active_sess_id]["status"] = "error"
                    break

                # 2. Check storage thresholds
                metrics = self.storage_mgr.get_storage_metrics()
                if metrics["status"] == "CRITICAL_STORAGE":
                    err_msg = f"Halted: Critical storage threshold reached ({metrics['free_mb']} MB free)."
                    logger.critical(err_msg)
                    self.last_error = err_msg
                    if active_sess_id and active_sess_id in self.sessions:
                        self.sessions[active_sess_id]["error"] = err_msg
                        self.sessions[active_sess_id]["status"] = "error"
                    break

                # 3. Determine segment target duration
                configured_seg_sec = self.config.segment_duration_seconds
                if self.requested_duration_seconds is not None:
                    remaining_in_session = self.requested_duration_seconds - total_session_elapsed
                    if remaining_in_session <= 0:
                        logger.info(f"Requested duration {self.requested_duration_seconds}s reached. Ending session.")
                        break
                    segment_target_sec = min(configured_seg_sec, int(remaining_in_session))
                else:
                    segment_target_sec = configured_seg_sec

                # 4. Prepare next segment on USB drive
                raw_ext = "h264" if self.config.camera_backend != "picamera2" else "mp4"
                self.active_segment_info = self.segment_mgr.prepare_next_segment(extension=raw_ext)
                part_path = self.active_segment_info["raw_part_path"]

                # 5. Launch camera capture
                started, msg = self.camera.start_segment_capture(
                    output_path=part_path,
                    duration_sec=segment_target_sec
                )
                if not started:
                    logger.error(f"Failed to start camera capture: {msg}")
                    self.last_error = f"Camera start failure: {msg}"
                    if active_sess_id and active_sess_id in self.sessions:
                        self.sessions[active_sess_id]["error"] = self.last_error
                        self.sessions[active_sess_id]["status"] = "error"
                    break

                self.state = "RECORDING"
                seg_id = self.active_segment_info["recording_id"]
                if active_sess_id and active_sess_id in self.sessions:
                    self.sessions[active_sess_id]["recording_ids"].append(seg_id)
                logger.info(f"Segment recording started: {seg_id}")

                # 6. Monitor loop for current segment
                seg_start_monotonic = time.monotonic()
                while not self.stop_requested.is_set():
                    time.sleep(0.5)
                    elapsed = round(time.monotonic() - seg_start_monotonic, 1)
                    current_total_elapsed = total_session_elapsed + elapsed

                    # Update live session elapsed
                    if active_sess_id and active_sess_id in self.sessions:
                        self.sessions[active_sess_id]["elapsed_seconds"] = current_total_elapsed

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
                        logger.info(f"Segment target duration {segment_target_sec}s reached.")
                        self.camera.stop_capture(timeout_sec=3.0)
                        break

                # 7. Finalize completed or interrupted segment
                total_session_elapsed += self.camera.get_elapsed_seconds()
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
                    if active_sess_id and active_sess_id in self.sessions:
                        if final_rec.get("relative_path"):
                            self.sessions[active_sess_id]["finalized_files"].append(final_rec["relative_path"])
                    self.active_segment_info = None

                # Check if bounded session is complete
                if self.requested_duration_seconds is not None and total_session_elapsed >= self.requested_duration_seconds:
                    logger.info(f"Session {active_sess_id} successfully completed total duration {total_session_elapsed}s.")
                    if active_sess_id and active_sess_id in self.sessions:
                        self.sessions[active_sess_id]["status"] = "completed"
                        self.sessions[active_sess_id]["end_time"] = datetime.now().isoformat()
                    break

        except Exception as e:
            logger.exception(f"Unexpected error in recording loop: {e}")
            self.last_error = str(e)
            self.state = "ERROR"
            if active_sess_id and active_sess_id in self.sessions:
                self.sessions[active_sess_id]["error"] = str(e)
                self.sessions[active_sess_id]["status"] = "error"
        finally:
            self._finalize_active_on_exit()
            self._release_instance_lock()
            self.state = "STOPPED"
            if active_sess_id and active_sess_id in self.sessions:
                if self.sessions[active_sess_id]["status"] == "recording":
                    self.sessions[active_sess_id]["status"] = "stopped"
                self.sessions[active_sess_id]["end_time"] = datetime.now().isoformat()
            self.current_session_id = None
            self.requested_duration_seconds = None

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
                if self.current_session_id and self.current_session_id in self.sessions:
                    if final_rec.get("relative_path"):
                        self.sessions[self.current_session_id]["finalized_files"].append(final_rec["relative_path"])
            except Exception as e:
                logger.error(f"Error finalizing segment on exit: {e}")
            finally:
                self.active_segment_info = None

    def get_status_summary(self) -> Dict[str, Any]:
        """Aggregate comprehensive system, storage, and recording telemetry for UI and API."""
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
            "active_session_id": self.current_session_id,
            "active_segment_id": self.active_segment_info["recording_id"] if self.active_segment_info else None,
            "active_file": cur_file,
            "active_size_mb": cur_size_mb,
            "latest_completed": self.latest_completed_segment,
            "total_segments": index_sum["total_segments"],
            "total_duration_min": index_sum["total_duration_min"],
            "mount_path": str(self.config.mount_path),
            "is_mount_point": storage.get("is_mount_point", False),
            "is_verified_usb": storage.get("is_usb", False) and storage.get("volume_marker_valid", False),
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
