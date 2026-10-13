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
from road_recorder.api.dependencies import init_dependencies, get_recorder, get_config
from road_recorder.api.auth import get_configured_api_key, generate_and_save_api_key
from road_recorder.api.routes import device, recordings, stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Validates USB storage before serving traffic and performs graceful shutdown on exit.
    """
    config = get_config()
    recorder = get_recorder()

    # Pre-flight USB gate check
    is_valid, msg, details = verify_usb_gate(config)
    if not is_valid:
        recorder.logger.warning(
            f"[STARTUP NOTICE] USB pre-flight gate check: {msg}. "
            "Recording endpoints will reject requests until verified USB drive is mounted."
        )
    else:
        recorder.logger.info(f"[STARTUP] {msg}")

    # Ensure API Key exists or log instructions
    api_key = get_configured_api_key(config)
    if not api_key:
        new_key = generate_and_save_api_key(config.api_key_file)
        recorder.logger.info(f"[API KEY] Initialized new API key at {config.api_key_file}")
    else:
        recorder.logger.info("[API KEY] Verified active API key configuration.")

    yield

    # Shutdown sequence
    recorder.logger.info("[SHUTDOWN] Terminating Road Recorder API...")
    recorder.stop()
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
            "Delivers segmented MP4 video files, metadata, and hardware telemetry for external computer vision pipelines."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # CORS configuration
    origins = cfg.cors_origins if hasattr(cfg, "cors_origins") else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
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
