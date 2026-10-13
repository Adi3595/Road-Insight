#!/usr/bin/env python3
"""
Road Recorder - Standalone Raspberry Pi Continuous Video Recording Application.
Interactive beginner-friendly terminal interface with USB storage safeguards.
"""

import sys
import os
import time
import signal
import shutil
import subprocess
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from road_recorder.config import load_config
from road_recorder.recorder import RoadRecorder
from road_recorder.logging_setup import close_logger_handlers


def clear_screen():
    """Clear terminal screen cleanly across OS."""
    os.system("cls" if os.name == "nt" else "clear")


def print_banner():
    print("=" * 70)
    print("        ROAD RECORDER - RASPBERRY PI DASHCAM (USB STORAGE)")
    print("=" * 70)


def format_duration(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    s = int(seconds)
    hrs = s // 3600
    mins = (s % 3600) // 60
    secs = s % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def display_dashboard(recorder: RoadRecorder):
    """Render real-time status summary including USB mount telemetry."""
    status = recorder.get_status_summary()
    print_banner()
    state_str = status["state"]
    state_color = f"[{state_str}]"
    print(f"System State       : {state_color:16} Backend: {status['backend'] or 'Detecting...'}")
    
    if state_str == "RECORDING":
        print(f"Recording Time     : {format_duration(status['elapsed_sec'])}")
        print(f"Current Segment    : {status['active_file']} ({status['active_size_mb']} MB)")
    else:
        print(f"Recording Time     : Inactive")
        print(f"Current Segment    : None")

    latest = status.get("latest_completed")
    if latest:
        size_mb = round((latest.get("file_size_bytes", 0)) / (1024 * 1024), 2)
        print(f"Latest Completed   : {latest.get('recording_id')} ({size_mb} MB)")
    else:
        print(f"Latest Completed   : None yet")

    print("-" * 70)
    mount_label = "MOUNTED (USB)" if status["is_mount_point"] else "NOT MOUNTED / BLOCKED"
    dev_label = f"{status.get('device') or 'N/A'} ({status.get('fstype') or 'unknown fs'})"
    print(f"USB Mount Point    : {status['mount_path']} [{mount_label}]")
    print(f"USB Device Details : {dev_label}")
    print(f"Volume Marker      : {'[OK] Valid' if status['volume_marker_valid'] else '[FAIL] Missing / Unverified'}")
    print(f"Storage Capacity   : {status['storage_free_gb']} GB Free ({status['storage_used_pct']}% Used)")
    print(f"Estimated Time Left: ~{status['est_remaining_min']} mins until critical threshold")
    print(f"Storage Health     : {status['storage_status']}")
    print(f"Total Recorded     : {status['total_segments']} segments ({status['total_duration_min']} mins total)")

    if status.get("last_error"):
        print(f"\n[ALERT / REJECTION]: {status['last_error']}")
    print("=" * 70)


def menu_start_recording(recorder: RoadRecorder):
    clear_screen()
    print_banner()
    print("Initiating continuous recording to USB pendrive...")
    ok, msg = recorder.start()
    if ok:
        print(f"[SUCCESS] {msg}")
    else:
        print(f"\n[BLOCKED] {msg}")
        print("Note: To prevent wearing out or filling the microSD card,")
        print("Road Recorder will not start until the USB drive is properly mounted.")
    time.sleep(2.0)


def menu_stop_recording(recorder: RoadRecorder):
    clear_screen()
    print_banner()
    print("Stopping recording and finalizing current segment safely...")
    ok, msg = recorder.stop()
    if ok:
        print(f"[SUCCESS] {msg}")
    else:
        print(f"[FAILED]  {msg}")
    time.sleep(1.5)


def menu_list_recordings(recorder: RoadRecorder):
    clear_screen()
    print_banner()
    records = recorder.index_mgr.list_records(limit=15)
    print(f"RECENT RECORDINGS ON USB (Showing last {len(records)} segments):")
    print("-" * 70)
    if not records:
        print("  No recordings found in index yet.")
    else:
        print(f"{'Segment ID':<26} {'Duration':<10} {'Size':<10} {'Status':<12}")
        print("-" * 70)
        for r in records:
            dur = f"{round(r.get('duration_sec', 0))}s"
            size_mb = f"{round((r.get('file_size_bytes', 0))/(1024*1024), 1)} MB"
            status = r.get("finalization_status", "unknown")
            print(f"{r.get('recording_id', 'N/A'):<26} {dur:<10} {size_mb:<10} {status:<12}")

    print("-" * 70)
    summary = recorder.index_mgr.get_summary()
    print(f"Total: {summary['total_segments']} segments | {summary['total_duration_min']} mins | {summary['total_size_mb']} MB")
    print(f"Metadata File: {summary['index_path']}")
    input("\nPress Enter to return to main menu...")


def menu_view_storage_diagnostics(recorder: RoadRecorder):
    clear_screen()
    print_banner()
    from hardware.storage.storage_check import print_storage_diagnostic_report
    print_storage_diagnostic_report(str(recorder.config.mount_path))

    print("\nTo explore recordings in Raspberry Pi file manager:")
    print(f"  Command: pcmanfm \"{recorder.config.recordings_path}\"")
    print(f"       or: xdg-open \"{recorder.config.recordings_path}\"")

    open_choice = input("\nAttempt to open directory in file manager now? [y/N]: ").strip().lower()
    if open_choice in ["y", "yes"]:
        opened = False
        for opener in ["xdg-open", "pcmanfm", "explorer"]:
            if shutil.which(opener):
                try:
                    subprocess.Popen([opener, str(recorder.config.recordings_path)])
                    print(f"Opened with {opener}.")
                    opened = True
                    break
                except Exception:
                    pass
        if not opened:
            print("No supported graphical file manager found in current session.")
        time.sleep(1.5)


def menu_run_usb_setup():
    clear_screen()
    from hardware.storage.usb_setup import run_interactive_setup
    run_interactive_setup()
    input("\nPress Enter to return to main menu...")


def interactive_loop(recorder: RoadRecorder):
    """Main user interactive menu."""
    while True:
        clear_screen()
        display_dashboard(recorder)
        print("\nMAIN MENU:")
        print("  1. Start Recording")
        print("  2. Stop Recording")
        print("  3. View Status & Refresh")
        print("  4. List Recordings on USB")
        print("  5. View USB Storage Diagnostics")
        print("  6. Run USB Setup Assistant (/etc/fstab helper)")
        print("  7. Exit Application")
        print("-" * 70)

        try:
            choice = input("Enter option [1-7] (or press Enter to refresh): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down safely...")
            break

        if choice == "1":
            menu_start_recording(recorder)
        elif choice == "2":
            menu_stop_recording(recorder)
        elif choice == "3" or choice == "":
            pass
        elif choice == "4":
            menu_list_recordings(recorder)
        elif choice == "5":
            menu_view_storage_diagnostics(recorder)
        elif choice == "6":
            menu_run_usb_setup()
        elif choice == "7":
            print("\nExiting Road Recorder...")
            break
        else:
            print(f"Invalid option '{choice}'. Please select between 1 and 7.")
            time.sleep(1.0)


def main():
    config = load_config("config.yaml")
    recorder = RoadRecorder(config)

    # Setup signal handler for clean graceful shutdown on Ctrl+C (SIGINT)
    def sig_handler(sig, frame):
        print("\n\n[SHUTDOWN] Signal received. Halting recorder safely...")
        recorder.stop()
        close_logger_handlers()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, sig_handler)

    if config.auto_start or "--auto-start" in sys.argv:
        print("[INFO] Auto-start configured. Starting recording immediately...")
        recorder.start()

    try:
        interactive_loop(recorder)
    finally:
        recorder.stop()
        close_logger_handlers()


if __name__ == "__main__":
    main()
