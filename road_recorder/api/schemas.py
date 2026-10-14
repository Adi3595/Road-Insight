"""
Pydantic Request and Response Schemas for Road Recorder Video Data API.
Defines strict type models, parameter validations, and documentation examples.
"""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class DeviceStatusResponse(BaseModel):
    status: str = Field(..., description="Overall device status (e.g. ready, recording, storage_blocked, error)")
    api_version: str = Field(..., description="API software version")
    camera: Dict[str, Any] = Field(..., description="Camera hardware and encoder status")
    storage: Dict[str, Any] = Field(..., description="USB storage health, mount status, and capacity")
    recording: Dict[str, Any] = Field(..., description="Current recording state and session metrics")


class RecordingStartRequest(BaseModel):
    duration_seconds: int = Field(
        ...,
        ge=5,
        description="Explicit duration of recording in seconds. Bounded between 5s and configured maximum limit."
    )


class RecordingStartResponse(BaseModel):
    session_id: str = Field(..., description="Unique recording session identifier")
    status: str = Field(..., description="Status of the session (e.g. recording)")
    requested_duration_seconds: int = Field(..., description="Requested bounded duration in seconds")


class RecordingStopResponse(BaseModel):
    session_id: Optional[str] = Field(None, description="Session ID that was stopped")
    status: str = Field(..., description="Final session status (e.g. stopped)")
    recording_ids: List[str] = Field(default_factory=list, description="IDs of segments recorded in this session")
    finalized_files: List[str] = Field(default_factory=list, description="Finalized file paths")
    elapsed_seconds: float = Field(..., description="Total elapsed recording seconds")


class SessionStatusResponse(BaseModel):
    session_id: str
    status: str = Field(..., description="recording, completed, stopped, or error")
    start_time: str
    end_time: Optional[str] = None
    requested_duration_seconds: Optional[int] = None
    elapsed_seconds: float = 0.0
    recording_ids: List[str] = Field(default_factory=list)
    finalized_files: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class RecordingItem(BaseModel):
    recording_id: str
    relative_path: str
    filename: str
    start_timestamp: str
    end_timestamp: Optional[str] = None
    duration_seconds: float
    resolution: str
    fps: int
    codec: str
    file_size_bytes: int
    file_size_mb: float
    finalization_status: str
    download_url: str
    metadata_url: str


class RecordingsListResponse(BaseModel):
    total_recordings: int
    page: int
    page_size: int
    items: List[RecordingItem]


class StreamSessionResponse(BaseModel):
    stream_id: str
    status: str
    supported_protocols: List[str]
    message: str
    investigation_details: Dict[str, Any]
