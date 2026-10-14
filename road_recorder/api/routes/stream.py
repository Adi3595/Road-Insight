"""
Live Video Streaming Session Lifecycle and Hardware Architecture Assessment.
Provides explicit session operations for live video delivery (Workflow 2),
documents CSI hardware concurrency constraints, and prevents fake stream endpoints.
"""

import time
import uuid
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from road_recorder.recorder import RoadRecorder
from road_recorder.config import AppConfig
from road_recorder.api.dependencies import get_config, get_recorder, verify_api_key
from road_recorder.api.schemas import StreamSessionResponse

logger = logging.getLogger("road_recorder.api.stream")

router = APIRouter(prefix="/api/v1/stream", tags=["Live Streaming"])

# In-memory stream session state
_active_stream: Optional[Dict[str, Any]] = None


class StreamStartRequest(BaseModel):
    protocol: str = Field("rtsp", description="Desired streaming protocol: 'rtsp' or 'mjpeg'")
    duration_seconds: Optional[int] = Field(300, ge=10, le=1800, description="Session timeout in seconds")


@router.get("/status", summary="Get live stream capability and hardware architecture report")
def get_stream_status(
    recorder: RoadRecorder = Depends(get_recorder),
    _auth: str = Depends(verify_api_key)
):
    """
    Inspect live stream capability and hardware concurrency constraints.
    Reports streaming as unsupported on the single-pipeline camera stack.
    """
    is_recording = recorder.state in ["RECORDING", "STARTING", "ROTATING"]

    analysis = {
        "camera_sensor": "Sony IMX219 (CSI-2 2-lane)",
        "driver_stack": "Raspberry Pi Unicam (Single-Process Hardware Exclusive Lock)",
        "concurrency_limitation": (
            "The Linux unicam CSI driver on Raspberry Pi 4 Model B grants exclusive access "
            "to one process at a time. When 'rpicam-vid' records to an MP4/H.264 file on the USB drive, "
            "it holds the CSI buffer lock. Running a second independent camera capture process will fail."
        ),
        "recommended_production_workflow": (
            "Workflow 1 (Recorded Video Delivery): Consumer requests bounded recording via "
            "POST /api/v1/recordings/start, polls status, and downloads finalized MP4 via "
            "GET /api/v1/recordings/{id}/video. This avoids network packet drop and guarantees 100% video integrity."
        ),
        "future_dual_output_pathway": (
            "Pipe 'rpicam-vid' standard output into a multiplexer (e.g. MediaMTX RTSP server + local file writer) "
            "or migrate to Picamera2 multi-stream encoder."
        )
    }

    return {
        "stream_supported": False,
        "stream_active": False,
        "stream_id": None,
        "camera_occupied_by_recording": is_recording,
        "message": (
            "Live streaming is not implemented on the single-pipeline camera stack. "
            "The camera hardware is dedicated to MP4 file recording on the USB drive. "
            "Use Workflow 1 (POST /api/v1/recordings/start and GET /api/v1/recordings/{id}/video)."
        ),
        "architecture_analysis": analysis
    }


@router.post("/start", summary="Start live stream session (unimplemented)")
def start_live_stream(
    req: StreamStartRequest,
    _auth: str = Depends(verify_api_key)
):
    """
    Returns 501 Not Implemented. Real-time streaming is not implemented on the single-pipeline camera stack.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Live video streaming is not implemented. The Sony IMX219 CSI camera operates under "
            "exclusive single-process hardware ownership dedicated to high-fidelity MP4 recording. "
            "Use Workflow 1: request a bounded recording session via POST /api/v1/recordings/start "
            "and download the finalized MP4 via GET /api/v1/recordings/{id}/video."
        )
    )


@router.post("/stop", summary="Stop live stream session (unimplemented)")
def stop_live_stream(
    _auth: str = Depends(verify_api_key)
):
    """
    Returns 501 Not Implemented. Real-time streaming is not implemented.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Live video streaming is not implemented."
    )
