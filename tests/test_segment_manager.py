"""Tests for SegmentManager directory structure, naming, remuxing, and recovery."""

from pathlib import Path
from unittest.mock import patch

from road_recorder.segment_manager import SegmentManager


def test_segment_naming_and_part_lifecycle(tmp_path: Path):
    mgr = SegmentManager(base_output_dir=tmp_path, target_format="mp4")
    seg1 = mgr.prepare_next_segment(extension="h264")
    
    assert seg1["segment_index"] == 1
    assert "RR_" in seg1["recording_id"]
    assert seg1["raw_part_path"].name.endswith(".part.h264")
    assert seg1["final_target_path"].suffix == ".mp4"

    # Simulate recorded bytes
    seg1["raw_part_path"].write_bytes(b"\x00\x00\x00\x01\x67" + b"\x00" * 2048)

    # Test finalization with simulated remux
    with patch.object(mgr, "remux_h264_to_mp4", return_value=(True, "OK")):
        with patch.object(mgr, "verify_playable_mp4", return_value=True):
            # Write dummy mp4 to simulate successful remux output
            seg1["final_target_path"].write_bytes(b"ftypmp42" + b"\x00" * 4096)
            
            record = mgr.finalize_segment(
                segment_info=seg1,
                width=1280,
                height=720,
                fps=30,
                codec="h264",
                backend="rpicam-vid"
            )
            assert record["finalization_status"] == "finalized"
            assert record["width"] == 1280
            assert record["fps"] == 30
            assert record["file_size_bytes"] > 0
            assert not seg1["raw_part_path"].exists()


def test_remux_failure_preserves_raw_file(tmp_path: Path):
    """Verify CRITICAL REQUIREMENT: Never delete raw file if MP4 remux fails."""
    mgr = SegmentManager(base_output_dir=tmp_path, target_format="mp4")
    seg = mgr.prepare_next_segment(extension="h264")
    raw_content = b"\x00\x00\x00\x01\x67" + b"\xAA" * 1024
    seg["raw_part_path"].write_bytes(raw_content)

    # Force remux failure
    with patch.object(mgr, "remux_h264_to_mp4", return_value=(False, "Remux error")):
        record = mgr.finalize_segment(
            segment_info=seg,
            width=1280,
            height=720,
            fps=30,
            codec="h264",
            backend="rpicam-vid"
        )
        assert record["finalization_status"] == "raw_preserved"
        # The preserved h264 file must still exist and contain the footage!
        raw_preserved_path = tmp_path / record["relative_path"]
        assert raw_preserved_path.exists()
        assert raw_preserved_path.read_bytes() == raw_content


def test_orphaned_part_recovery(tmp_path: Path):
    mgr = SegmentManager(base_output_dir=tmp_path, target_format="mp4")
    date_dir = mgr.get_date_directory()
    orphaned_file = date_dir / "RR_20261013_200000_001.part.h264"
    orphaned_file.write_bytes(b"prior_crash_data")

    recovered_count = mgr.recover_orphaned_parts()
    assert recovered_count == 1
    assert not orphaned_file.exists()
    assert (date_dir / "RR_20261013_200000_001.interrupted.h264").exists()
