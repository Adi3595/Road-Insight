#!/usr/bin/env python3
"""
Road Recorder — Remote Consumer Client Example.
Demonstrates the complete Workflow 1 (Record & Download) lifecycle
for external computer vision teams connecting to the Raspberry Pi API.

Usage:
    python3 client_consumer.py --host 192.168.1.100 --api-key YOUR_API_KEY

Requirements:
    pip install requests
"""

import os
import sys
import time
import argparse
import hashlib
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library is required.  pip install requests")
    sys.exit(1)


class RoadRecorderClient:
    """Authenticated client for the Road Recorder Video Data API."""

    def __init__(self, host: str, port: int, api_key: str, verify_ssl: bool = False):
        self.base_url = f"http://{host}:{port}"
        self.headers = {"X-API-Key": api_key}
        self.verify = verify_ssl
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    # ── Health & Device ──────────────────────────────────────────────

    def health_check(self) -> dict:
        """Check if the API and USB storage are healthy."""
        r = self.session.get(f"{self.base_url}/api/v1/health", timeout=10)
        return r.json()

    def device_status(self) -> dict:
        """Get full device telemetry: camera, storage, recording state."""
        r = self.session.get(f"{self.base_url}/api/v1/device/status", timeout=10)
        r.raise_for_status()
        return r.json()

    # ── Recording Lifecycle ──────────────────────────────────────────

    def start_recording(self, duration_seconds: int = None) -> dict:
        """
        Request a bounded recording session.
        
        Args:
            duration_seconds: Recording duration (5–3600s). None for continuous.
        """
        payload = {}
        if duration_seconds is not None:
            payload["duration_seconds"] = duration_seconds
        r = self.session.post(
            f"{self.base_url}/api/v1/recordings/start",
            json=payload, timeout=15
        )
        r.raise_for_status()
        return r.json()

    def stop_recording(self) -> dict:
        """Stop the currently active recording session."""
        r = self.session.post(f"{self.base_url}/api/v1/recordings/stop", timeout=15)
        r.raise_for_status()
        return r.json()

    def get_session(self, session_id: str) -> dict:
        """Poll a recording session's current status."""
        r = self.session.get(
            f"{self.base_url}/api/v1/recordings/{session_id}", timeout=10
        )
        r.raise_for_status()
        return r.json()

    def poll_session(self, session_id: str, poll_interval: float = 2.0, timeout: float = 120.0):
        """
        Poll a recording session until it completes or times out.
        Yields status dictionaries for each poll.
        """
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_session(session_id)
            yield status
            if status.get("status") in ["completed", "stopped", "failed"]:
                return
            time.sleep(poll_interval)
        raise TimeoutError(f"Session {session_id} did not complete within {timeout}s")

    # ── Recordings List & Metadata ───────────────────────────────────

    def list_recordings(self, page: int = 1, per_page: int = 20, date: str = None) -> dict:
        """
        Get paginated list of finalized recordings.
        
        Args:
            page: Page number (1-indexed).
            per_page: Items per page.
            date: Optional YYYY-MM-DD filter.
        """
        params = {"page": page, "per_page": per_page}
        if date:
            params["date"] = date
        r = self.session.get(
            f"{self.base_url}/api/v1/recordings",
            params=params, timeout=10
        )
        r.raise_for_status()
        return r.json()

    def get_metadata(self, recording_id: str) -> dict:
        """Get metadata for a specific recording segment."""
        r = self.session.get(
            f"{self.base_url}/api/v1/recordings/{recording_id}/metadata",
            timeout=10
        )
        r.raise_for_status()
        return r.json()

    # ── Video Download ───────────────────────────────────────────────

    def download_video(self, recording_id: str, output_dir: str = ".") -> Path:
        """
        Download an MP4 recording with progress bar.
        Supports resumable downloads via HTTP Range requests.
        
        Returns:
            Path to the downloaded file.
        """
        output_path = Path(output_dir) / f"{recording_id}.mp4"
        temp_path = output_path.with_suffix(".mp4.partial")

        # Check if partial download exists for resume
        resume_byte = 0
        if temp_path.exists():
            resume_byte = temp_path.stat().st_size
            print(f"  Resuming download from byte {resume_byte:,}...")

        headers = dict(self.headers)
        if resume_byte > 0:
            headers["Range"] = f"bytes={resume_byte}-"

        r = self.session.get(
            f"{self.base_url}/api/v1/recordings/{recording_id}/video",
            headers=headers, stream=True, timeout=30
        )

        if r.status_code == 416:
            # Range not satisfiable — file already complete
            if temp_path.exists():
                temp_path.rename(output_path)
            print(f"  ✓ Already downloaded: {output_path}")
            return output_path

        r.raise_for_status()

        # Determine total size
        total_size = None
        if "Content-Range" in r.headers:
            # e.g. "bytes 1024-9999/10000"
            total_size = int(r.headers["Content-Range"].split("/")[-1])
        elif "Content-Length" in r.headers:
            total_size = resume_byte + int(r.headers["Content-Length"])

        # Download with progress
        mode = "ab" if resume_byte > 0 else "wb"
        downloaded = resume_byte
        hasher = hashlib.md5()

        with open(temp_path, mode) as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if total_size:
                        pct = (downloaded / total_size) * 100
                        bar = "█" * int(pct // 2) + "░" * (50 - int(pct // 2))
                        print(f"\r  [{bar}] {pct:5.1f}%  {downloaded:,}/{total_size:,} bytes", end="", flush=True)
                    else:
                        print(f"\r  Downloaded {downloaded:,} bytes...", end="", flush=True)

        print()  # newline after progress bar

        # Rename to final filename
        temp_path.rename(output_path)
        print(f"  ✓ Saved: {output_path} ({downloaded:,} bytes, MD5: {hasher.hexdigest()[:12]}...)")
        return output_path

    def verify_mp4(self, filepath: Path) -> bool:
        """Basic MP4 file verification — checks for ftyp box header."""
        try:
            with open(filepath, "rb") as f:
                header = f.read(12)
            if b"ftyp" in header:
                print(f"  ✓ MP4 container verified: {filepath.name}")
                return True
            else:
                print(f"  ✗ WARNING: {filepath.name} does not appear to be a valid MP4 file.")
                return False
        except Exception as e:
            print(f"  ✗ Verification error: {e}")
            return False


# ─── Demo Workflow ───────────────────────────────────────────────────

def demo_workflow(client: RoadRecorderClient, duration: int, output_dir: str):
    """
    Complete demonstration of Workflow 1: Record → Poll → Download → Verify.
    """
    print("\n" + "=" * 68)
    print("  ROAD RECORDER — REMOTE CONSUMER WORKFLOW DEMO")
    print("=" * 68)

    # Step 1: Health check
    print("\n── Step 1: Health Check ──")
    health = client.health_check()
    print(f"  Status: {health.get('status', 'unknown')}")
    if health.get("status") != "healthy":
        print(f"  ⚠ Device is not healthy: {health}")
        print("  Aborting workflow.")
        return

    # Step 2: Device status
    print("\n── Step 2: Device Status ──")
    status = client.device_status()
    print(f"  API Version : {status.get('api_version')}")
    print(f"  Camera      : {status.get('camera', {}).get('sensor', 'N/A')}")
    print(f"  Recording   : {status.get('recording', {}).get('state', 'N/A')}")
    storage = status.get("storage", {})
    print(f"  USB Free    : {storage.get('free_space_mb', 'N/A')} MB")

    # Step 3: Start recording
    print(f"\n── Step 3: Start Recording ({duration}s) ──")
    start_resp = client.start_recording(duration_seconds=duration)
    session_id = start_resp["session_id"]
    print(f"  Session ID  : {session_id}")
    print(f"  Status      : {start_resp['status']}")

    # Step 4: Poll until complete
    print(f"\n── Step 4: Polling Session ──")
    for poll in client.poll_session(session_id, poll_interval=3.0, timeout=duration + 60):
        elapsed = poll.get("elapsed_seconds", 0)
        state = poll.get("status", "unknown")
        print(f"  [{elapsed:6.1f}s] {state}", end="")
        if state == "recording":
            print(f"  (segments: {poll.get('segments_completed', 0)})", end="")
        print()
        if state in ["completed", "stopped"]:
            break

    # Step 5: List available recordings
    print(f"\n── Step 5: List Recordings ──")
    recordings = client.list_recordings(per_page=5)
    print(f"  Total available: {recordings.get('total_recordings', 0)}")
    for item in recordings.get("items", [])[:5]:
        print(f"    • {item['recording_id']}  ({item.get('duration_sec', '?')}s, {item.get('file_size_bytes', 0):,} bytes)")

    # Step 6: Download the latest recording
    if recordings.get("items"):
        latest = recordings["items"][0]
        rec_id = latest["recording_id"]
        print(f"\n── Step 6: Download Recording {rec_id} ──")
        filepath = client.download_video(rec_id, output_dir=output_dir)

        # Step 7: Verify
        print(f"\n── Step 7: Verify MP4 ──")
        client.verify_mp4(filepath)
    else:
        print("\n  No recordings available to download.")

    print("\n" + "=" * 68)
    print("  WORKFLOW COMPLETE")
    print("=" * 68 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Road Recorder Remote Consumer Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick 30-second recording and download
  python3 client_consumer.py --host 192.168.1.100 --api-key abc123 --duration 30

  # Check device health only
  python3 client_consumer.py --host 192.168.1.100 --api-key abc123 --health-only

  # List and download existing recordings
  python3 client_consumer.py --host 192.168.1.100 --api-key abc123 --list-only
        """
    )
    parser.add_argument("--host", required=True, help="Raspberry Pi IP address")
    parser.add_argument("--port", type=int, default=8000, help="API port (default: 8000)")
    parser.add_argument("--api-key", required=True, help="X-API-Key secret")
    parser.add_argument("--duration", type=int, default=30, help="Recording duration in seconds (default: 30)")
    parser.add_argument("--output-dir", default="./downloaded_recordings", help="Directory for downloaded MP4 files")
    parser.add_argument("--health-only", action="store_true", help="Only run health check")
    parser.add_argument("--list-only", action="store_true", help="Only list available recordings")
    args = parser.parse_args()

    client = RoadRecorderClient(args.host, args.port, args.api_key)
    os.makedirs(args.output_dir, exist_ok=True)

    if args.health_only:
        health = client.health_check()
        print(f"Health: {health}")
        device = client.device_status()
        print(f"Device: {device}")
        return

    if args.list_only:
        recordings = client.list_recordings(per_page=50)
        print(f"Total recordings: {recordings.get('total_recordings', 0)}")
        for item in recordings.get("items", []):
            print(f"  {item['recording_id']}  |  {item.get('duration_sec', '?')}s  |  {item.get('file_size_bytes', 0):,} bytes")
        return

    demo_workflow(client, args.duration, args.output_dir)


if __name__ == "__main__":
    main()
