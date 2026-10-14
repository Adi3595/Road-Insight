"""
FastAPI Dependency Injection Providers for Road Recorder API.
Ensures single-instance ownership of the RoadRecorder engine and request-time auth evaluation.
"""

from typing import Optional
from fastapi import Header, Depends
from road_recorder.config import AppConfig, load_config
from road_recorder.recorder import RoadRecorder
from road_recorder.api.auth import APIKeyAuth

_global_config: Optional[AppConfig] = None
_global_recorder: Optional[RoadRecorder] = None
_global_auth: Optional[APIKeyAuth] = None


def set_recorder(recorder: Optional[RoadRecorder]):
    """Set the active global RoadRecorder singleton."""
    global _global_recorder
    _global_recorder = recorder


def set_config(config: AppConfig):
    """Set the active global AppConfig singleton and refresh auth."""
    global _global_config, _global_auth
    _global_config = config
    _global_auth = APIKeyAuth(config)


def init_dependencies(config: Optional[AppConfig] = None, recorder: Optional[RoadRecorder] = None):
    """
    Initialize global shared singletons safely.
    Does NOT instantiate RoadRecorder if one is not provided, preventing premature disk writes.
    """
    global _global_config, _global_recorder, _global_auth
    if config is not None:
        _global_config = config
        _global_auth = APIKeyAuth(config)
    elif _global_config is None:
        _global_config = load_config("config.yaml")
        _global_auth = APIKeyAuth(_global_config)

    if recorder is not None:
        _global_recorder = recorder


def get_config() -> AppConfig:
    global _global_config
    if _global_config is None:
        init_dependencies()
    return _global_config


def get_recorder() -> RoadRecorder:
    global _global_recorder
    if _global_recorder is None:
        raise RuntimeError(
            "RoadRecorder is not initialized. Storage gate must verify USB drive before constructing the recorder."
        )
    return _global_recorder


def get_optional_recorder() -> Optional[RoadRecorder]:
    """Retrieve active recorder without raising if not yet initialized."""
    global _global_recorder
    return _global_recorder


def get_auth() -> APIKeyAuth:
    global _global_auth
    if _global_auth is None:
        init_dependencies()
    return _global_auth


def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    auth: APIKeyAuth = Depends(get_auth)
) -> str:
    """Validate API key per request."""
    return auth(x_api_key)
