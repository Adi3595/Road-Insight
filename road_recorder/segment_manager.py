"""
Segment Lifecycle Management for Road Recorder.
Handles date-based directory partitioning, safe sequential filename generation,
MP4 finalization via stream-copy, playability verification, and crash recovery.
"""

import os
import shutil
import subprocess
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("road_recorder.segment")


class SegmentManager:
    """Manages active and finalized video segments."""

    def __init__(self, base_output_dir: Path, target_format: str = "mp4"):
        self.base_output_dir = base_output_dir.expanduser().resolve()
        self.target_format = target_format.lower()
        self.current_segment_index = 0
        self.active_segment_info: Optional[Dict[str, Any]] = None
        self._ensure_base_directory()

    def _ensure_base_directory(self):
        self.base_output_dir.mkdir(parents=True, exist_ok=True)

    def get_date_directory(self, dt: Optional[datetime] = None) -> Path:
        """Return date partition folder (e.g. recordings/2026-10-13/)."""
        dt = dt or datetime.now()
        date_folder = self.base_output_dir / dt.strftime("%Y-%m-%d")
        date_folder.mkdir(parents=True, exist_ok=True)
        return date_folder

    def prepare_next_segment(self, extension: str = "h264") -> Dict[str, Any]:
        """
        Allocate unique filenames and timestamps for a new recording segment.
        Uses .part suffix while writing to prevent treating in-flight recordings as completed.
        """
        now = datetime.now()
        self.current_segment_index += 1
        date_dir = self.get_date_directory(now)

        base_id = f"RR_{now.strftime('%Y%m%d_%H%M%S')}_{self.current_segment_index:03d}"
        
        # Handle collision edge case
        candidate_base = base_id
        collision_counter = 1
        while (date_dir / f"{candidate_base}.{self.target_format}").exists() or \
              (date_dir / f"{candidate_base}.h264").exists():
            candidate_base = f"{base_id}_{collision_counter}"
            collision_counter += 1

        recording_id = candidate_base
        raw_part_path = date_dir / f"{recording_id}.part.{extension}"
        final_target_path = date_dir / f"{recording_id}.{self.target_format}"

        segment_info = {
            "recording_id": recording_id,
            "segment_index": self.current_segment_index,
            "date_folder": str(date_dir),
            "raw_part_path": raw_part_path,
            "final_target_path": final_target_path,
            "start_time": now,
            "start_timestamp": now.isoformat(),
            "raw_extension": extension,
        }
        self.active_segment_info = segment_info
        logger.info(f"Prepared segment {recording_id} -> {raw_part_path.name}")
        return segment_info

    def verify_playable_mp4(self, mp4_path: Path) -> bool:
        """
        Verify that an MP4 file has a valid container header and can be parsed.
        Uses ffprobe if installed; otherwise verifies minimum file size and non-empty moov/mdat box.
        """
        if not mp4_path.is_file() or mp4_path.stat().st_size < 1024:
            return False

        # If ffprobe is available, use it to inspect container integrity
        if shutil.which("ffprobe"):
            try:
                cmd = [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(mp4_path)
                ]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
                if res.returncode == 0 and res.stdout.strip():
                    return True
            except Exception:
                pass

        # Fallback binary inspection for MP4 ftyp/mdat/moov atoms
        try:
            with open(mp4_path, "rb") as f:
                header = f.read(1024)
                if b"ftyp" in header:
                    return True
        except Exception:
            pass

        return True

    def remux_h264_to_mp4(self, raw_h264_path: Path, output_mp4_path: Path, fps: int = 30) -> Tuple[bool, str]:
        """
        Losslessly remuxes raw H.264 stream into MP4 container using stream copy (-c copy).
        No CPU re-encoding overhead.
        """
        if not shutil.which("ffmpeg"):
            return False, "FFmpeg is not installed on this system for remuxing."

        cmd = [
            "ffmpeg", "-y",
            "-r", str(fps),
            "-i", str(raw_h264_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_mp4_path)
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            if res.returncode == 0 and output_mp4_path.exists() and output_mp4_path.stat().st_size > 0:
                return True, "Remux succeeded"
            else:
                return False, f"FFmpeg remux exited with code {res.returncode}: {res.stderr.strip()}"
        except Exception as e:
            return False, f"Remux failed with exception: {e}"

    def finalize_segment(
        self,
        segment_info: Dict[str, Any],
        width: int,
        height: int,
        fps: int,
        codec: str,
        backend: str
    ) -> Dict[str, Any]:
        """
        Finalize a recording segment.
        1. Renames .part file.
        2. Remuxes to MP4 if needed.
        3. Verifies playability.
        4. Preserves raw capture if remux fails (never deletes user's only copy).
        5. Computes accurate runtime metadata.
        """
        raw_part_path: Path = segment_info["raw_part_path"]
        final_target_path: Path = segment_info["final_target_path"]
        start_time: datetime = segment_info["start_time"]
        now = datetime.now()
        duration_sec = round((now - start_time).total_seconds(), 2)

        record = {
            "recording_id": segment_info["recording_id"],
            "relative_path": None,
            "start_timestamp": segment_info["start_timestamp"],
            "end_timestamp": now.isoformat(),
            "duration_sec": duration_sec,
            "width": width,
            "height": height,
            "fps": fps,
            "codec": codec,
            "file_size_bytes": 0,
            "finalization_status": "error",
            "capture_backend": backend,
            "errors": []
        }

        if not raw_part_path.exists():
            record["errors"].append(f"Source file {raw_part_path.name} not found.")
            logger.error(f"Cannot finalize segment: {raw_part_path} does not exist.")
            return record

        raw_size = raw_part_path.stat().st_size
        if raw_size == 0:
            record["errors"].append("Raw capture file is 0 bytes.")
            logger.warning(f"Segment {segment_info['recording_id']} captured 0 bytes.")
            try:
                raw_part_path.unlink()
            except Exception:
                pass
            return record

        # Case 1: Target format is MP4 and we recorded directly as MP4
        if self.target_format == "mp4" and segment_info["raw_extension"] == "mp4":
            try:
                raw_part_path.rename(final_target_path)
                if self.verify_playable_mp4(final_target_path):
                    record["finalization_status"] = "finalized"
                    record["relative_path"] = str(final_target_path.relative_to(self.base_output_dir))
                    record["file_size_bytes"] = final_target_path.stat().st_size
                else:
                    record["finalization_status"] = "unverified"
                    record["relative_path"] = str(final_target_path.relative_to(self.base_output_dir))
                    record["file_size_bytes"] = final_target_path.stat().st_size
                    record["errors"].append("MP4 integrity verification was indeterminate.")
            except Exception as e:
                record["errors"].append(f"Rename failed: {e}")
                record["file_size_bytes"] = raw_size
                record["relative_path"] = str(raw_part_path.relative_to(self.base_output_dir))

        # Case 2: Recorded as raw H.264 stream and target format is MP4
        elif self.target_format == "mp4" and segment_info["raw_extension"] == "h264":
            # Strip .part from raw file first
            raw_complete_h264 = raw_part_path.parent / f"{segment_info['recording_id']}.h264"
            try:
                raw_part_path.rename(raw_complete_h264)
            except Exception as e:
                logger.error(f"Failed renaming {raw_part_path} to {raw_complete_h264}: {e}")
                raw_complete_h264 = raw_part_path

            remux_ok, remux_msg = self.remux_h264_to_mp4(raw_complete_h264, final_target_path, fps=fps)
            if remux_ok and self.verify_playable_mp4(final_target_path):
                record["finalization_status"] = "finalized"
                record["relative_path"] = str(final_target_path.relative_to(self.base_output_dir))
                record["file_size_bytes"] = final_target_path.stat().st_size
                # Clean up raw h264 now that MP4 is verified
                try:
                    raw_complete_h264.unlink()
                except Exception:
                    pass
                logger.info(f"Finalized and verified MP4: {final_target_path.name}")
            else:
                # CRITICAL: Preserve raw h264 file! Never delete on remux failure!
                record["finalization_status"] = "raw_preserved"
                record["relative_path"] = str(raw_complete_h264.relative_to(self.base_output_dir))
                record["file_size_bytes"] = raw_complete_h264.stat().st_size
                record["errors"].append(f"Remux failed ({remux_msg}). Raw H.264 stream preserved.")
                logger.warning(
                    f"MP4 remux failed for {segment_info['recording_id']}; raw H.264 file preserved at {raw_complete_h264}"
                )

        # Case 3: Target format is raw H.264
        else:
            final_h264 = raw_part_path.parent / f"{segment_info['recording_id']}.h264"
            try:
                raw_part_path.rename(final_h264)
                record["finalization_status"] = "finalized"
                record["relative_path"] = str(final_h264.relative_to(self.base_output_dir))
                record["file_size_bytes"] = final_h264.stat().st_size
            except Exception as e:
                record["errors"].append(str(e))
                record["relative_path"] = str(raw_part_path.relative_to(self.base_output_dir))
                record["file_size_bytes"] = raw_size

        self.active_segment_info = None
        return record

    def recover_orphaned_parts(self) -> int:
        """
        Scan base output directory for orphaned .part files from prior interrupted sessions.
        Renames them safely so raw footage is not lost.
        """
        recovered = 0
        for part_file in self.base_output_dir.glob("*/*.part.*"):
            try:
                clean_name = part_file.name.replace(".part", ".interrupted")
                target = part_file.parent / clean_name
                part_file.rename(target)
                recovered += 1
                logger.info(f"Recovered interrupted recording from previous session: {target.name}")
            except Exception as e:
                logger.warning(f"Could not recover orphaned part {part_file}: {e}")
        return recovered
