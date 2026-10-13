"""Tests for AppConfig loading and validation with USB storage schema."""

from pathlib import Path
from road_recorder.config import AppConfig, load_config, _simple_yaml_parser


def test_default_config_validity():
    cfg = load_config("non_existent_config.yaml")
    assert cfg.width == 1280
    assert cfg.height == 720
    assert cfg.fps == 30
    assert cfg.segment_duration_seconds == 300
    assert cfg.container_format == "mp4"
    assert cfg.require_verified_usb is True
    assert cfg.allow_fallback_to_sd is False
    assert cfg.critical_free_space_mb == 1024
    assert cfg.low_space_warning_percent == 15.0
    assert cfg.recordings_path.name == "recordings"
    assert cfg.metadata_path.name == "metadata"
    assert cfg.logs_path.name == "logs"


def test_invalid_resolution_fails():
    caught = False
    try:
        AppConfig({
            "camera": {"width": -1, "height": 720},
            "recording": {},
            "storage": {},
            "logging": {}
        })
    except ValueError as e:
        caught = True
        assert "Invalid resolution" in str(e)
    assert caught, "Expected ValueError for negative width"


def test_invalid_fps_fails():
    caught = False
    try:
        AppConfig({
            "camera": {"fps": 0},
            "recording": {},
            "storage": {},
            "logging": {}
        })
    except ValueError as e:
        caught = True
        assert "Invalid frame rate" in str(e)
    assert caught, "Expected ValueError for 0 fps"


def test_invalid_storage_thresholds_fails():
    caught = False
    try:
        AppConfig({
            "camera": {},
            "recording": {},
            "storage": {
                "critical_free_space_mb": 0,
            },
            "logging": {}
        })
    except ValueError as e:
        caught = True
        assert "Critical storage threshold" in str(e)
    assert caught, "Expected ValueError for 0 MB critical threshold"


def test_simple_yaml_parser_usb_schema():
    sample = """
    camera:
      backend: auto
      width: 1920
      height: 1080
      fps: 30
      preview: false
    storage:
      mount_path: /mnt/custom-usb
      require_verified_usb: true
      allow_fallback_to_sd: false
      critical_free_space_mb: 2048
      low_space_warning_percent: 20
    """
    parsed = _simple_yaml_parser(sample)
    assert parsed["camera"]["width"] == 1920
    assert parsed["storage"]["mount_path"] == "/mnt/custom-usb"
    assert parsed["storage"]["require_verified_usb"] is True
    assert parsed["storage"]["allow_fallback_to_sd"] is False
    assert parsed["storage"]["critical_free_space_mb"] == 2048
    assert parsed["storage"]["low_space_warning_percent"] == 20
