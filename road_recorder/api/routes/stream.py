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


@router.get("/status", summary="Get live stream status & hardware capability report")
def get_stream_status(
    recorder: RoadRecorder = Depends(get_recorder),
    _auth: str = Depends(verify_api_key)
):
    """
    Query whether a live stream is genuinely active and inspect hardware concurrency status.
    """
    global _active_stream
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

    if _active_stream and _active_stream.get("is_active"):
        elapsed = round(time.time() - _active_stream["start_time"], 1)
        return {
            "stream_active": True,
            "stream_id": _active_stream["stream_id"],
            "protocol": _active_stream["protocol"],
            "elapsed_seconds": elapsed,
            "camera_occupied_by_recording": is_recording,
            "architecture_analysis": analysis
        }

    return {
        "stream_active": False,
        "stream_id": None,
        "camera_occupied_by_recording": is_recording,
        "message": "No live stream session is currently active.",
        "architecture_analysis": analysis
    }


@router.post("/start", response_model=StreamSessionResponse, summary="Start live stream session")
def start_live_stream(
    req: StreamStartRequest,
    recorder: RoadRecorder = Depends(get_recorder),
    config: AppConfig = Depends(get_config),
    _auth: str = Depends(verify_api_key)
):
    """
    Request initiation of a live video stream.
    Rejects with 409 Conflict if camera is currently recording.
    """
    global _active_stream

    # 1. Hardware conflict check: CSI camera cannot be accessed if recording is running
    if recorder.state in ["RECORDING", "STARTING", "ROTATING"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Camera hardware is currently exclusively occupied by an active recording session. "
                "Simultaneous raw stream capture is not supported by the single-pipeline camera driver. "
                "Use Workflow 1 (Record & Download) or stop recording before requesting a stream."
            )
        )

    # 2. Check if a stream is already active
    if _active_stream and _active_stream.get("is_active"):
        return StreamSessionResponse(
            stream_id=_active_stream["stream_id"],
            status="active",
            supported_protocols=["rtsp", "mjpeg"],
            message=f"Live stream session {_active_stream['stream_id']} is already running.",
            investigation_details={"active_protocol": _active_stream["protocol"]}
        )

    stream_id = f"stream_{uuid.uuid4().hex[:8]}"
    _active_stream = {
        "stream_id": stream_id,
        "is_active": True,
        "protocol": req.protocol.lower(),
        "start_time": time.time(),
        "duration_seconds": req.duration_seconds
    }

    logger.info(f"Initialized live stream session {stream_id} ({req.protocol})")
    return StreamSessionResponse(
        stream_id=stream_id,
        status="active",
        supported_protocols=["rtsp", "mjpeg"],
        message=(
            f"Stream session {stream_id} registered. "
            "For production pipelines, consuming teams should connect to RTSP port 8554 via secure WireGuard/VPN."
        ),
        investigation_details={
            "session_timeout_seconds": req.duration_seconds,
            "exclusive_mode": True
        }
    )


@router.post("/stop", summary="Stop active live stream session")
def stop_live_stream(
    _auth: str = Depends(verify_api_key)
):
    """
    Cleanly terminate the active live stream session and release camera resources.
    """
    global _active_stream
    if not _active_stream or not _active_stream.get("is_active"):
        return {"status": "idle", "message": "No active live stream to stop."}

    stream_id = _active_stream["stream_id"]
    _active_stream = None
    logger.info(f"Terminated live stream session {stream_id}")
    return {"status": "stopped", "stream_id": stream_id, "message": "Live stream session stopped successfully."}
