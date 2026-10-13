"""
Authentication and API Key Security Module for Road Recorder API.
Enforces request validation via the 'X-API-Key' header using constant-time comparison.
"""

import os
import secrets
import logging
from pathlib import Path
from typing import Optional
from fastapi import Header, HTTPException, status, Depends

from road_recorder.config import AppConfig

logger = logging.getLogger("road_recorder.api.auth")

API_KEY_HEADER_NAME = "X-API-Key"


def get_configured_api_key(config: AppConfig) -> Optional[str]:
    """
    Retrieve configured API key from environment variable or protected key file.
    Never returns hardcoded keys.
    """
    # 1. Environment variable priority
    env_key = os.environ.get(config.api_key_env_var)
    if env_key and env_key.strip():
        return env_key.strip()

    # 2. Key file on USB drive
    key_file = config.api_key_file
    if key_file and key_file.is_file():
        try:
            content = key_file.read_text(encoding="utf-8").strip()
            if content:
                return content
        except Exception as e:
            logger.error(f"Failed reading API key from {key_file}: {e}")

    return None


def generate_and_save_api_key(key_file_path: Path) -> str:
    """
    Generate a cryptographically secure 256-bit API key and save it to file with restricted permissions.
    """
    new_key = secrets.token_urlsafe(32)
    try:
        key_file_path.parent.mkdir(parents=True, exist_ok=True)
        key_file_path.write_text(new_key + "\n", encoding="utf-8")
        # Restrict permissions on POSIX systems to owner read/write only (600)
        try:
            os.chmod(str(key_file_path), 0o600)
        except Exception:
            pass
        logger.info(f"Generated new API key saved to {key_file_path}")
    except Exception as e:
        logger.warning(f"Could not persist API key to file {key_file_path}: {e}")
    return new_key


class APIKeyAuth:
    """Dependency callable to validate X-API-Key header."""

    def __init__(self, config: AppConfig):
        self.config = config

    def __call__(self, x_api_key: Optional[str] = Header(None, alias=API_KEY_HEADER_NAME)) -> str:
        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Missing required authentication header '{API_KEY_HEADER_NAME}'.",
                headers={"WWW-Authenticate": API_KEY_HEADER_NAME}
            )

        expected_key = get_configured_api_key(self.config)
        if not expected_key:
            logger.error("API Key authentication requested but no API key is configured!")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="API authentication is unconfigured on the device. Set ROAD_RECORDER_API_KEY."
            )

        # Constant-time comparison prevents timing attacks
        if not secrets.compare_digest(x_api_key, expected_key):
            logger.warning("Rejected request with invalid API key.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key provided.",
                headers={"WWW-Authenticate": API_KEY_HEADER_NAME}
            )

        return x_api_key
