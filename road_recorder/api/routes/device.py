"""
Device Readiness and Hardware Status Endpoints for Road Recorder API.
Provides structured device, camera, storage, and recording telemetry.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from road_recorder.config import AppConfig
from road_recorder.recorder import RoadRecorder
from road_recorder.api.dependencies import get_config, get_recorder, verify_api_key
from road_recorder.api.schemas import DeviceStatusResponse

router = APIRouter(prefix="/api/v1", tags=["Device & Health"])


@router.get("/health", summary="Lightweight health check")
def health_check(
    recorder: RoadRecorder = Depends(get_recorder),
    config: AppConfig = Depends(get_config)
):
    """
    Lightweight health check for monitoring and load balancers.
    Fails safely with HTTP 503 if the required USB drive is missing.
    """
    metrics = recorder.storage_mgr.get_storage_metrics()
    is_usb_ready = metrics.get("safe_to_record", False)

    if not is_usb_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "degraded",
                "usb_ready": False,
                "message": metrics.get("rejection_message") or "USB drive is unavailable.",
                "reasons": metrics.get("reasons_blocked", [])
            }
        )

    return {
        "status": "healthy",
        "usb_ready": True,
        "recording_state": recorder.state,
        "mount_path": str(config.mount_path)
    }


@router.get("/device/status", response_model=DeviceStatusResponse, summary="Comprehensive device readiness")
def get_device_status(
    recorder: RoadRecorder = Depends(get_recorder),
    config: AppConfig = Depends(get_config),
    _auth: str = Depends(verify_api_key)
):
    """
    Retrieve full device, camera, storage, and recording state.
    Requires authentication via 'X-API-Key'.
    """
    summary = recorder.get_status_summary()
    cam_available, cam_msg = recorder.camera.is_camera_available()

    # Determine top-level status
    if summary["state"] == "RECORDING":
        device_status = "recording"
    elif not summary.get("is_verified_usb", False):
        device_status = "storage_blocked"
    elif not cam_available:
        device_status = "camera_unavailable"
    elif summary["storage_status"] == "CRITICAL_STORAGE":
        device_status = "critical_storage"
    else:
        device_status = "ready"

    return DeviceStatusResponse(
        status=device_status,
        api_version="1.0.0",
        camera={
            "available": cam_available,
            "backend": summary.get("backend"),
            "resolution": f"{config.width}x{config.height}",
            "fps": config.fps,
            "codec": config.codec,
            "message": cam_msg
        },
        storage={
            "mount_path": summary.get("mount_path"),
            "is_mounted": summary.get("is_mount_point", False),
            "is_verified_usb": summary.get("is_verified_usb", False),
            "volume_uuid": config.volume_uuid or "unspecified",
            "device": summary.get("device"),
            "fstype": summary.get("fstype"),
            "free_gb": summary.get("storage_free_gb", 0.0),
            "free_mb": summary.get("storage_free_mb", 0.0),
            "used_percent": summary.get("storage_used_pct", 0.0),
            "health": summary.get("storage_status"),
            "estimated_minutes_left": summary.get("est_remaining_min", 0.0),
            "writable": (summary.get("storage_status") not in ["READ_ONLY", "NOT_MOUNTED"])
        },
        recording={
            "state": summary.get("state"),
            "active_session_id": summary.get("active_session_id"),
            "active_segment_id": summary.get("active_segment_id"),
            "active_file": summary.get("active_file"),
            "active_size_mb": summary.get("active_size_mb", 0.0),
            "elapsed_seconds": summary.get("elapsed_sec", 0.0),
            "total_segments_indexed": summary.get("total_segments", 0),
            "total_duration_minutes": summary.get("total_duration_min", 0.0),
            "last_error": summary.get("last_error")
        }
    )
