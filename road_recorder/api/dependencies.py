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


def init_dependencies(config: Optional[AppConfig] = None, recorder: Optional[RoadRecorder] = None):
    """Initialize global shared singletons."""
    global _global_config, _global_recorder, _global_auth
    _global_config = config or load_config("config.yaml")
    _global_recorder = recorder or RoadRecorder(_global_config)
    _global_auth = APIKeyAuth(_global_config)


def get_config() -> AppConfig:
    global _global_config
    if _global_config is None:
        init_dependencies()
    return _global_config


def get_recorder() -> RoadRecorder:
    global _global_recorder
    if _global_recorder is None:
        init_dependencies()
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
