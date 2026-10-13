"""Tests for RecordingIndex append-only integrity and safe reading."""

import json
from pathlib import Path
from road_recorder.recording_index import RecordingIndex


def test_index_append_and_retrieve(tmp_path: Path):
    index_file = tmp_path / "recording_index.jsonl"
    idx = RecordingIndex(index_file)

    rec1 = {
        "recording_id": "RR_20261013_210000_001",
        "relative_path": "2026-10-13/RR_20261013_210000_001.mp4",
        "start_timestamp": "2026-10-13T21:00:00",
        "end_timestamp": "2026-10-13T21:05:00",
        "duration_sec": 300.0,
        "width": 1280,
        "height": 720,
        "fps": 30,
        "codec": "h264",
        "file_size_bytes": 15000000,
        "finalization_status": "finalized",
        "capture_backend": "rpicam-vid"
    }
    assert idx.append_segment(rec1) is True

    records = idx.list_records()
    assert len(records) == 1
    assert records[0]["recording_id"] == "RR_20261013_210000_001"

    summary = idx.get_summary()
    assert summary["total_segments"] == 1
    assert summary["total_duration_sec"] == 300.0
    assert summary["total_size_bytes"] == 15000000


def test_index_skips_corrupted_lines(tmp_path: Path):
    """Ensure partially written lines from sudden power cut don't crash parser."""
    index_file = tmp_path / "recording_index.jsonl"
    idx = RecordingIndex(index_file)

    # Valid record
    rec1 = {"recording_id": "RR_1", "duration_sec": 60.0, "file_size_bytes": 1000}
    idx.append_segment(rec1)

    # Simulate abrupt power interruption while writing 2nd record
    with open(index_file, "a", encoding="utf-8") as f:
        f.write('{"recording_id": "RR_2", "duration_sec": 120.0, "file_size_b\n')

    # Valid 3rd record
    rec3 = {"recording_id": "RR_3", "duration_sec": 180.0, "file_size_bytes": 2000}
    idx.append_segment(rec3)

    # list_records should parse 2 valid records and skip the corrupted middle line
    records = idx.list_records()
    assert len(records) == 2
    assert records[0]["recording_id"] == "RR_1"
    assert records[1]["recording_id"] == "RR_3"
