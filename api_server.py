#!/usr/bin/env python3
"""
Road Recorder Video Data API Server Entrypoint.
FastAPI application with lifespan validation, CORS, graceful shutdown,
and single-instance hardware ownership.
"""

import sys
import os
import uvicorn
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from road_recorder.config import load_config, AppConfig
from road_recorder.recorder import RoadRecorder
from road_recorder.usb_gate import verify_usb_gate
from road_recorder.logging_setup import close_logger_handlers
from road_recorder.api.dependencies import init_dependencies, get_recorder, get_optional_recorder, set_recorder, get_config
from road_recorder.api.auth import get_configured_api_key, generate_and_save_api_key
from road_recorder.api.routes import device, recordings, stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Enforces strict startup ordering:
    1. Validates configuration.
    2. Runs USB pre-flight gate. ABORTS startup immediately if USB is missing or unverified.
    3. Confirms or generates API key on verified USB storage.
    4. Constructs RoadRecorder and persistent file logging only after USB gate passes.
    5. Safely tears down resources on shutdown.
    """
    config = get_config()

    # 1. Pre-flight USB gate check BEFORE constructing recorder or opening log files
    is_valid, msg, details = verify_usb_gate(config)
    if not is_valid:
        print(f"[FATAL STARTUP ERROR] USB gate verification failed: {msg}", file=sys.stderr)
        raise RuntimeError(f"Road Recorder API startup aborted: {msg}")

    print(f"[STARTUP] USB storage verified: {msg}")

    # 2. Ensure API Key exists on verified USB drive
    api_key = get_configured_api_key(config)
    if not api_key:
        new_key = generate_and_save_api_key(config.api_key_file, config=config)
        print(f"[API KEY] Initialized new API key at {config.api_key_file}")
    else:
        print("[API KEY] Verified active API key configuration.")

    # 3. Construct RoadRecorder singleton only after USB gate passes (if not injected via tests)
    recorder = get_optional_recorder()
    if recorder is None:
        recorder = RoadRecorder(config)
        set_recorder(recorder)

    recorder.logger.info("[STARTUP] Road Recorder API initialized and ready.")

    yield

    # Shutdown sequence
    active_rec = get_optional_recorder()
    if active_rec is not None:
        active_rec.logger.info("[SHUTDOWN] Terminating Road Recorder API...")
        active_rec.stop()
    close_logger_handlers()


def create_app(config: Optional[AppConfig] = None, recorder: Optional[RoadRecorder] = None) -> FastAPI:
    """FastAPI Application Factory."""
    init_dependencies(config=config, recorder=recorder)
    cfg = get_config()

    app = FastAPI(
        title="Road Recorder Video Data API",
        version="1.0.0",
        description=(
            "Authenticated Video Data Provider for Raspberry Pi 4 Road Inspection Dashcam. "
            "Delivers segmented MP4 video files, metadata, and hardware telemetry for external computer vision pipelines. "
            "Intended for trusted LAN/VPN environments only."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # CORS configuration - default to no cross-origin; never combine wildcard with credentials
    origins = cfg.cors_origins if hasattr(cfg, "cors_origins") and cfg.cors_origins else []
    if origins:
        has_wildcard = "*" in origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=not has_wildcard,  # Wildcard cannot combine with credentials per CORS standard
            allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
            allow_headers=["*"],
        )

    # Mount API routes
    app.include_router(device.router)
    app.include_router(recordings.router)
    app.include_router(stream.router)

    return app


# Module-level app instance for Uvicorn
app = create_app()


def main():
    """Run Uvicorn dev/production server directly."""
    config = get_config()
    host = config.api_host
    port = config.api_port

    print("=" * 68)
    print("       ROAD RECORDER - VIDEO DATA API SERVER")
    print("=" * 68)
    print(f"Server starting on : http://{host}:{port}")
    print(f"Interactive Docs   : http://{host}:{port}/docs")
    print(f"USB Mount Target   : {config.mount_path}")
    print(f"Required Volume    : {config.volume_uuid}")
    print("=" * 68)

    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=False,
        log_level=config.log_level.lower()
    )


if __name__ == "__main__":
    main()
