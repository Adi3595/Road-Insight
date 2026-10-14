"""
Recording Lifecycle, Query, and Video Streaming Endpoints for Road Recorder API.
Handles starting/stopping sessions, listing indexed videos, metadata inspection,
and chunked streaming with HTTP Range (206) support.
"""

import os
import re
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query, Header, Request
from fastapi.responses import StreamingResponse

from road_recorder.config import AppConfig
from road_recorder.recorder import RoadRecorder
from road_recorder.api.dependencies import get_config, get_recorder, verify_api_key
from road_recorder.api.schemas import (
    RecordingStartRequest,
    RecordingStartResponse,
    RecordingStopResponse,
    SessionStatusResponse,
    RecordingsListResponse,
    RecordingItem,
)

router = APIRouter(prefix="/api/v1/recordings", tags=["Recordings"])

SAFE_ID_REGEX = re.compile(r"^[A-Za-z0-9_\-]+$")


def validate_safe_identifier(val: str, name: str = "identifier") -> str:
    """Strictly prevent path traversal attacks."""
    if not val or not SAFE_ID_REGEX.match(val):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {name}: contains disallowed characters or path traversal elements."
        )
    return val


def create_range_streaming_response(
    file_path: Path,
    range_header: Optional[str] = None,
    content_type: str = "video/mp4",
    chunk_size: int = 65536
) -> StreamingResponse:
    """
    Stream a video file with RFC 7233 HTTP Range (206 Partial Content) support.
    Streams directly from disk in small 64 KB chunks; never loads entire file into RAM.
    Does NOT emit Content-Range header for normal 200 OK full responses.
    """
    file_size = file_path.stat().st_size

    # Case 1: Normal full file response (200 OK) - MUST NOT include Content-Range
    if not range_header or not range_header.strip():
        def full_generator():
            with open(file_path, "rb") as f:
                while True:
                    data = f.read(chunk_size)
                    if not data:
                        break
                    yield data

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": content_type,
            "Content-Disposition": f'inline; filename="{file_path.name}"'
        }
        return StreamingResponse(full_generator(), status_code=status.HTTP_200_OK, headers=headers)

    # Case 2: Range request processing
    raw_range = range_header.strip()
    if not raw_range.startswith("bytes="):
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Unsupported range unit.",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    range_spec = raw_range[len("bytes="):].strip()

    # Reject multiple ranges (not supported)
    if "," in range_spec:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Multiple byte ranges are not supported.",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    # Match range pattern: "<start>-<end>", "<start>-", or "-<suffix>"
    match = re.match(r"^(\d*)-(\d*)$", range_spec)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Malformed Range header syntax.",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    raw_start, raw_end = match.groups()
    if not raw_start and not raw_end:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Invalid empty range specifier.",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    if not raw_start:
        # Suffix range: -<suffix>
        suffix_len = int(raw_end)
        if suffix_len <= 0 or file_size == 0:
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                detail="Suffix length out of bounds.",
                headers={"Content-Range": f"bytes */{file_size}"}
            )
        start = max(0, file_size - suffix_len)
        end = file_size - 1
    elif not raw_end:
        # Open-ended range: <start>-
        start = int(raw_start)
        end = file_size - 1
    else:
        # Closed range: <start>-<end>
        start = int(raw_start)
        end = int(raw_end)

    # Bounds validation
    if file_size == 0 or start >= file_size or start > end:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Requested range not satisfiable.",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    end = min(file_size - 1, end)
    content_length = end - start + 1

    def chunk_generator():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                to_read = min(chunk_size, remaining)
                data = f.read(to_read)
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": content_type,
        "Content-Disposition": f'inline; filename="{file_path.name}"'
    }
    return StreamingResponse(chunk_generator(), status_code=status.HTTP_206_PARTIAL_CONTENT, headers=headers)


@router.post("/start", response_model=RecordingStartResponse, summary="Start a bounded recording session")
def start_recording(
    req: RecordingStartRequest,
    recorder: RoadRecorder = Depends(get_recorder),
    config: AppConfig = Depends(get_config),
    _auth: str = Depends(verify_api_key)
):
    """
    Start a recording session with an explicit bounded duration in seconds.
    Rejects missing, non-integer, below-minimum (<5s), or above-maximum durations.
    Rejects concurrent sessions with 409 Conflict.
    Rejects unmounted or unverified USB storage with 503.
    """
    # 1. Check if already recording
    if recorder.state in ["RECORDING", "STARTING", "ROTATING"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A recording session is already active (Session: {recorder.current_session_id})."
        )

    # 2. Verify USB storage pre-flight
    metrics = recorder.storage_mgr.get_storage_metrics()
    if not metrics.get("safe_to_record", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=metrics.get("rejection_message") or "USB recording drive not available. Recording has not started."
        )

    # 3. Validate explicit bounded duration
    duration = req.duration_seconds
    if duration is None or not isinstance(duration, int):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Explicit integer duration_seconds is required."
        )

    if duration < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Requested duration {duration}s is below minimum allowed limit of 5s."
        )

    if duration > config.max_duration_seconds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Requested duration {duration}s exceeds maximum limit of {config.max_duration_seconds}s."
        )

    started, msg, session_id = recorder.start_session(duration_seconds=duration)
    if not started:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate recording session: {msg}"
        )

    return RecordingStartResponse(
        session_id=session_id,
        status="recording",
        requested_duration_seconds=duration
    )


@router.post("/stop", response_model=RecordingStopResponse, summary="Stop active recording session")
def stop_recording(
    recorder: RoadRecorder = Depends(get_recorder),
    _auth: str = Depends(verify_api_key)
):
    """
    Stop the current active recording session safely and finalize the in-flight MP4 segment.
    """
    if recorder.state == "STOPPED":
        return RecordingStopResponse(
            session_id=None,
            status="idle",
            recording_ids=[],
            finalized_files=[],
            elapsed_seconds=0.0
        )

    active_id = recorder.current_session_id
    ok, msg, summary = recorder.stop_session()

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error stopping recording: {msg}"
        )

    summary = summary or {}
    return RecordingStopResponse(
        session_id=active_id or summary.get("session_id"),
        status=summary.get("status", "stopped"),
        recording_ids=summary.get("recording_ids", []),
        finalized_files=summary.get("finalized_files", []),
        elapsed_seconds=summary.get("elapsed_seconds", 0.0)
    )


@router.get("/{session_id}", response_model=SessionStatusResponse, summary="Get session status by ID")
def get_session_status(
    session_id: str,
    recorder: RoadRecorder = Depends(get_recorder),
    _auth: str = Depends(verify_api_key)
):
    """
    Check the status, timestamps, and output files of a specific recording session.
    """
    validate_safe_identifier(session_id, "session_id")
    sess = recorder.get_session(session_id)
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording session '{session_id}' not found."
        )

    return SessionStatusResponse(**sess)


@router.get("", response_model=RecordingsListResponse, summary="List all indexed recordings")
def list_recordings(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    recorder: RoadRecorder = Depends(get_recorder),
    _auth: str = Depends(verify_api_key)
):
    """
    Retrieve paginated list of recordings stored on the USB pendrive and indexed in metadata.
    """
    records = recorder.index_mgr.list_records()

    # Optional date filtering (strictly YYYY-MM-DD)
    if date is not None:
        date_clean = date.strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_clean):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date filter format. Must be strictly YYYY-MM-DD (e.g. 2026-10-15)."
            )
        records = [r for r in records if str(r.get("start_timestamp", "")).startswith(date_clean)]

    # Newest recordings first
    records = list(reversed(records))
    total_count = len(records)

    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paged = records[start_idx:end_idx]

    items = []
    for r in paged:
        rec_id = r.get("recording_id", "")
        rel_path = r.get("relative_path") or ""
        file_name = Path(rel_path).name if rel_path else f"{rec_id}.mp4"
        file_bytes = int(r.get("file_size_bytes", 0) or 0)
        items.append(
            RecordingItem(
                recording_id=rec_id,
                relative_path=rel_path,
                filename=file_name,
                start_timestamp=str(r.get("start_timestamp", "")),
                end_timestamp=r.get("end_timestamp"),
                duration_seconds=float(r.get("duration_sec", 0.0) or 0.0),
                resolution=f"{r.get('width', 1280)}x{r.get('height', 720)}",
                fps=int(r.get("fps", 30) or 30),
                codec=str(r.get("codec", "h264")),
                file_size_bytes=file_bytes,
                file_size_mb=round(file_bytes / (1024 * 1024), 2),
                finalization_status=str(r.get("finalization_status", "unknown")),
                download_url=f"/api/v1/recordings/{rec_id}/video",
                metadata_url=f"/api/v1/recordings/{rec_id}/metadata"
            )
        )

    return RecordingsListResponse(
        total_recordings=total_count,
        page=page,
        page_size=page_size,
        items=items
    )


@router.get("/{recording_id}/video", summary="Stream or download finalized MP4 recording")
def download_recording_video(
    recording_id: str,
    range: Optional[str] = Header(None, alias="Range"),
    recorder: RoadRecorder = Depends(get_recorder),
    config: AppConfig = Depends(get_config),
    _auth: str = Depends(verify_api_key)
):
    """
    Stream finalized MP4 video file with HTTP Range (206) support.
    Streams directly from USB drive without memory buffering.
    Strictly prevents unfinalized downloads, path traversal, and symlink escapes.
    """
    validate_safe_identifier(recording_id, "recording_id")

    # 1. Look up in index
    records = recorder.index_mgr.list_records()
    matched = next((r for r in records if r.get("recording_id") == recording_id), None)
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording '{recording_id}' not found in recording index."
        )

    # 2. Enforce finalization status
    final_status = str(matched.get("finalization_status", "")).lower()
    if final_status != "finalized":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Recording '{recording_id}' is not finalized (status: '{final_status}'). Only finalized recordings may be downloaded."
        )

    rel_path = matched.get("relative_path")
    if not rel_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording '{recording_id}' has no associated output file."
        )

    # 3. Strict path traversal and symlink escape verification
    base_dir = config.recordings_path.resolve()
    target_file = (base_dir / rel_path).resolve()

    try:
        target_file.relative_to(base_dir)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Access denied: path traversal attempt detected."
        )

    real_base = Path(os.path.realpath(str(base_dir)))
    real_target = Path(os.path.realpath(str(target_file)))
    try:
        real_target.relative_to(real_base)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Access denied: symlink escape detected."
        )

    if not target_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Physical video file for recording '{recording_id}' is missing from USB storage."
        )

    # Content type
    ext = target_file.suffix.lower()
    content_type = "video/mp4" if ext == ".mp4" else ("video/x-h264" if ext == ".h264" else "application/octet-stream")

    return create_range_streaming_response(target_file, range_header=range, content_type=content_type)


@router.get("/{recording_id}/metadata", summary="Get recording segment metadata")
def get_recording_metadata(
    recording_id: str,
    recorder: RoadRecorder = Depends(get_recorder),
    _auth: str = Depends(verify_api_key)
):
    """
    Retrieve full technical metadata for a completed recording segment from the index.
    """
    validate_safe_identifier(recording_id, "recording_id")
    records = recorder.index_mgr.list_records()
    matched = next((r for r in records if r.get("recording_id") == recording_id), None)
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Metadata for recording '{recording_id}' not found."
        )
    return matched
