"""
Configuration Management for Road Recorder.
Loads, validates, and defaults settings from config.yaml with full fallback support.
Supports dedicated USB storage layout, threshold safeguards, and backward compatibility.
"""

import os
from pathlib import Path
from typing import Dict, Any


DEFAULT_CONFIG: Dict[str, Any] = {
    "camera": {
        "backend": "auto",
        "width": 1280,
        "height": 720,
        "fps": 30,
        "codec": "h264",
        "bitrate": 5000000,
        "preview": False,
    },
    "storage": {
        "mount_path": "/mnt/road-recorder-usb",
        "recordings_directory": "recordings",
        "metadata_directory": "metadata",
        "logs_directory": "logs",
        "diagnostics_directory": "diagnostics",
        "require_verified_usb": True,
        "allow_fallback_to_sd": False,
        "volume_uuid": "",
        "low_space_warning_percent": 15,
        "critical_free_space_mb": 1024,
    },
    "recording": {
        "segment_duration_seconds": 300,
        "container_format": "mp4",
        "auto_start": False,
    },
    "logging": {
        "level": "INFO",
    },
}


def _simple_yaml_parser(content: str) -> Dict[str, Any]:
    """
    Lightweight fallback parser for nested key-value yaml structures.
    Used when PyYAML is not installed.
    """
    result: Dict[str, Any] = {}
    current_section = None

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            parts = line.split(":", 1)
            key = parts[0].strip()
            val = parts[1].split("#")[0].strip()

            if val == "":
                current_section = key
                result[current_section] = {}
            else:
                parsed_val: Any = val
                if val.lower() in ("true", "yes"):
                    parsed_val = True
                elif val.lower() in ("false", "no"):
                    parsed_val = False
                elif val.startswith('"') and val.endswith('"'):
                    parsed_val = val[1:-1]
                elif val.startswith("'") and val.endswith("'"):
                    parsed_val = val[1:-1]
                else:
                    try:
                        if "." in val:
                            parsed_val = float(val)
                        else:
                            parsed_val = int(val)
                    except ValueError:
                        parsed_val = val

                if current_section:
                    result[current_section][key] = parsed_val
                else:
                    result[key] = parsed_val

    return result


class AppConfig:
    """Validated Application Configuration object."""

    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.camera = raw.get("camera", {})
        self.storage = raw.get("storage", {})
        self.recording = raw.get("recording", {})
        self.logging = raw.get("logging", {})

        # Camera settings
        self.camera_backend = str(self.camera.get("backend", "auto")).lower()
        self.width = int(self.camera.get("width", 1280))
        self.height = int(self.camera.get("height", 720))
        self.fps = int(self.camera.get("fps", 30))
        self.codec = str(self.camera.get("codec", "h264")).lower()
        self.bitrate = int(self.camera.get("bitrate", 5000000))
        self.preview = bool(self.camera.get("preview", False))

        # Storage settings
        raw_mount = str(self.storage.get("mount_path", "/mnt/road-recorder-usb"))
        self.mount_path = Path(os.path.expanduser(raw_mount)).resolve()
        self.recordings_directory = str(self.storage.get("recordings_directory", "recordings")).strip("/\\")
        self.metadata_directory = str(self.storage.get("metadata_directory", "metadata")).strip("/\\")
        self.logs_directory = str(self.storage.get("logs_directory", "logs")).strip("/\\")
        self.diagnostics_directory = str(self.storage.get("diagnostics_directory", "diagnostics")).strip("/\\")

        self.require_verified_usb = bool(self.storage.get("require_verified_usb", True))
        self.allow_fallback_to_sd = bool(self.storage.get("allow_fallback_to_sd", False))
        self.volume_uuid = str(self.storage.get("volume_uuid", "")).strip()

        self.low_space_warning_percent = float(self.storage.get("low_space_warning_percent", 15))
        self.critical_free_space_mb = int(self.storage.get("critical_free_space_mb", 1024))

        # Absolute paths on USB pendrive
        self.recordings_path = (self.mount_path / self.recordings_directory).resolve()
        self.metadata_path = (self.mount_path / self.metadata_directory).resolve()
        self.logs_path = (self.mount_path / self.logs_directory).resolve()
        self.diagnostics_path = (self.mount_path / self.diagnostics_directory).resolve()

        # Recording settings (support both new and legacy key names)
        seg_duration = self.recording.get("segment_duration_seconds", self.recording.get("segment_duration_sec", 300))
        self.segment_duration_seconds = int(seg_duration)
        self.container_format = str(self.recording.get("container_format", "mp4")).lower()
        auto_start = self.recording.get("auto_start", self.recording.get("auto_start_on_launch", False))
        self.auto_start = bool(auto_start)

        # Logging settings
        self.log_level = str(self.logging.get("level", "INFO")).upper()

        self.validate()

    def validate(self):
        """Ensure settings are sane and within safe operating parameters."""
        valid_backends = ["auto", "rpicam-vid", "libcamera-vid", "picamera2", "mock"]
        if self.camera_backend not in valid_backends:
            raise ValueError(f"Invalid camera backend '{self.camera_backend}'. Must be one of: {valid_backends}")

        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Invalid resolution: {self.width}x{self.height}. Dimensions must be positive.")

        if self.fps <= 0 or self.fps > 120:
            raise ValueError(f"Invalid frame rate: {self.fps}. Must be between 1 and 120 FPS.")

        if self.segment_duration_seconds < 5:
            raise ValueError(f"Segment duration {self.segment_duration_seconds}s is too short (minimum 5s).")

        if self.critical_free_space_mb <= 0:
            raise ValueError("Critical storage threshold must be greater than 0 MB.")

        if self.low_space_warning_percent <= 0 or self.low_space_warning_percent >= 100:
            raise ValueError("low_space_warning_percent must be between 1 and 99.")

        if self.container_format not in ["mp4", "h264", "mkv"]:
            raise ValueError(f"Unsupported container format '{self.container_format}'. Supported: mp4, h264, mkv")


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """Load configuration from YAML file or return safe defaults."""
    cfg_file = Path(config_path).expanduser().resolve()
    loaded_data: Dict[str, Any] = {}

    if cfg_file.is_file():
        try:
            content = cfg_file.read_text(encoding="utf-8")
            try:
                import yaml
                loaded_data = yaml.safe_load(content) or {}
            except ImportError:
                loaded_data = _simple_yaml_parser(content)
        except Exception as e:
            print(f"[WARN] Failed reading config file at {cfg_file}: {e}. Falling back to defaults.")

    # Deep merge with defaults
    merged: Dict[str, Any] = {}
    for section, sec_defaults in DEFAULT_CONFIG.items():
        merged[section] = {}
        sec_loaded = loaded_data.get(section, {}) if isinstance(loaded_data, dict) else {}
        for k, def_v in sec_defaults.items():
            merged[section][k] = sec_loaded.get(k, def_v)

    return AppConfig(merged)
