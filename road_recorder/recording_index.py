"""
Recording Metadata Index Management for Road Recorder.
Maintains an append-only JSON Lines index of all recorded segments,
guaranteeing data integrity and crash-resilient reading.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("road_recorder.index")


class RecordingIndex:
    """Manages the append-only JSON Lines index."""

    def __init__(self, index_file: Path):
        self.index_file = index_file.resolve()
        self._ensure_parent()

    def _ensure_parent(self):
        try:
            self.index_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory for index file {self.index_file}: {e}")

    def append_segment(self, record: Dict[str, Any]) -> bool:
        """
        Safely append a completed recording segment record to the index.
        Writes JSON line and immediately flushes to disk.
        """
        self._ensure_parent()
        
        # Ensure mandatory fields
        required_keys = [
            "recording_id", "relative_path", "start_timestamp",
            "end_timestamp", "duration_sec", "width", "height",
            "fps", "codec", "file_size_bytes", "finalization_status",
            "capture_backend"
        ]
        for key in required_keys:
            if key not in record:
                logger.warning(f"Metadata record missing field '{key}', defaulting to None.")
                record.setdefault(key, None)

        if "recorded_at" not in record:
            record["recorded_at"] = datetime.now().isoformat()

        try:
            line = json.dumps(record, ensure_ascii=False) + "\n"
            with open(self.index_file, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
            logger.info(f"Indexed segment {record['recording_id']} in {self.index_file.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to append record to {self.index_file}: {e}")
            return False

    def list_records(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Read all valid records from the append-only file.
        Gracefully skips corrupt or incomplete lines (e.g. from power loss during write).
        """
        if not self.index_file.is_file():
            return []

        records = []
        try:
            with open(self.index_file, "r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        records.append(record)
                    except json.JSONDecodeError as err:
                        logger.warning(f"Corrupt line {line_num} in {self.index_file} skipped: {err}")
        except Exception as e:
            logger.error(f"Error reading index file {self.index_file}: {e}")

        if limit and limit > 0:
            return records[-limit:]
        return records

    def get_latest_record(self) -> Optional[Dict[str, Any]]:
        """Return the most recently indexed segment or None."""
        records = self.list_records()
        return records[-1] if records else None

    def get_summary(self) -> Dict[str, Any]:
        """Aggregate total count, duration, and file size across all recorded segments."""
        records = self.list_records()
        total_count = len(records)
        total_duration_sec = sum(float(r.get("duration_sec", 0) or 0) for r in records)
        total_size_bytes = sum(int(r.get("file_size_bytes", 0) or 0) for r in records)
        total_size_mb = round(total_size_bytes / (1024 * 1024), 2)
        total_size_gb = round(total_size_bytes / (1024 ** 3), 2)

        return {
            "total_segments": total_count,
            "total_duration_sec": round(total_duration_sec, 1),
            "total_duration_min": round(total_duration_sec / 60.0, 1),
            "total_size_bytes": total_size_bytes,
            "total_size_mb": total_size_mb,
            "total_size_gb": total_size_gb,
            "index_path": str(self.index_file),
        }
