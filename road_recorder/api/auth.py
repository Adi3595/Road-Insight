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


def generate_and_save_api_key(key_file_path: Path, config: Optional[AppConfig] = None) -> str:
    """
    Generate a cryptographically secure 256-bit API key and save it to file with restricted permissions.
    Fails closed if the USB drive is not verified, path is not within USB storage, or persistence fails.
    Never returns an unsaved key.
    """
    if config is not None:
        from hardware.storage.storage_check import verify_usb_mount_safety
        verif = verify_usb_mount_safety(
            mount_path_str=str(config.mount_path),
            expected_uuid=config.volume_uuid or None,
            require_verified_usb=config.require_verified_usb,
            allow_fallback_to_sd=config.allow_fallback_to_sd,
            run_write_probe=False
        )
        if not verif["safe_to_start"]:
            reasons = "; ".join(verif.get("reasons_blocked", []))
            raise RuntimeError(f"Cannot generate/save API key: USB storage is not verified or mounted. {reasons}")

        try:
            key_file_path.resolve().relative_to(config.mount_path.resolve())
        except ValueError:
            raise RuntimeError(
                f"Configured api_key_file '{key_file_path}' must reside within the verified USB storage '{config.mount_path}'."
            )

    new_key = secrets.token_urlsafe(32)

    try:
        key_file_path.parent.mkdir(parents=True, exist_ok=True)
        # Write with restrictive permissions (0600 on POSIX)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(str(key_file_path), flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_key + "\n")

        try:
            os.chmod(str(key_file_path), 0o600)
        except Exception:
            pass

        # Verify persistence immediately
        read_back = key_file_path.read_text(encoding="utf-8").strip()
        if read_back != new_key:
            raise RuntimeError("Persistence verification failed: read-back key did not match generated key.")

        logger.info(f"Generated new API key saved to {key_file_path}")
        return new_key
    except Exception as e:
        logger.error(f"Failed to persist API key to file {key_file_path}: {e}")
        # Clean up incomplete key file if present
        if key_file_path.exists():
            try:
                key_file_path.unlink()
            except Exception:
                pass
        raise RuntimeError(f"Could not persist API key to {key_file_path}: {e}") from e


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
