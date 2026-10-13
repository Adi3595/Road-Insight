"""
Logging Setup for Road Recorder.
Configures timestamped console output and persistent file logging with immediate flushing.
Logs directly to the dedicated USB drive (/mnt/road-recorder-usb/logs/recording.log).
"""

import logging
import sys
from pathlib import Path


class FlushingFileHandler(logging.FileHandler):
    """FileHandler that flushes after every emit to prevent lost logs on power loss."""
    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_logger(log_dir: Path, level_str: str = "INFO") -> logging.Logger:
    """Initialize root application logger directed to USB storage."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    log_file = log_dir / "recording.log"

    level = getattr(logging, level_str.upper(), logging.INFO)
    logger = logging.getLogger("road_recorder")
    logger.setLevel(level)

    # Avoid duplicate handlers if already initialized
    if logger.hasHandlers():
        return logger

    # Formatting
    file_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File Handler
    try:
        file_handler = FlushingFileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.warning(f"Failed to open log file at {log_file}: {e}")

    logger.propagate = False
    return logger


def close_logger_handlers():
    """Close and detach all logger handlers (ensures file locks are freed on shutdown)."""
    logger = logging.getLogger("road_recorder")
    for handler in list(logger.handlers):
        try:
            handler.close()
            logger.removeHandler(handler)
        except Exception:
            pass
