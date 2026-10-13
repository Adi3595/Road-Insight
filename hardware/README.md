# Hardware Diagnostics Package

This directory contains standalone, non-destructive hardware diagnostic utilities for the Raspberry Pi Road Recorder.

All modules in this folder use **only the Python 3 standard library** (`os`, `sys`, `shutil`, `subprocess`, `json`, `pathlib`) so they run immediately on Raspberry Pi OS without installing third-party Python packages.

## Safety Guarantees

1. **Non-destructive**: Never partitions, formats, deletes, or writes to external storage devices without explicit instructions.
2. **Preserves Existing Data**: Strictly respects and does not modify `~/road-recordings/test.h264` or any pre-existing recordings.
3. **No Phantom Writes**: Disk storage checks only verify non-critical temporary file creation (`.storage_test_perm.tmp`) and remove it immediately.

## Directory Structure

```text
hardware/
├── __init__.py
├── camera/
│   ├── __init__.py
│   ├── camera_detect.py    # Detects CSI camera via rpicam-hello, libcamera-hello, or Picamera2
│   ├── camera_test.py      # Captures a short 5-second test clip to verify encoder & sensor
│   └── camera_preview.py   # Opens a live preview window if a GUI display is connected
├── storage/
│   ├── __init__.py
│   └── storage_check.py    # Checks free disk space, mount points, and 1 TB external drive status
├── diagnostics/
│   ├── __init__.py
│   └── system_info.py      # Reports Raspberry Pi model, OS release, tools, RAM, user, and home dir
└── README.md
```

## How to Run on the Raspberry Pi

From your terminal on the Raspberry Pi inside the `road-recorder` directory:

### 1. Run Complete System & Hardware Inspection
```bash
python3 inspect_environment.py
```
Or generate a machine-readable JSON report:
```bash
python3 inspect_environment.py --json
```

### 2. Run System & OS Diagnostics
```bash
python3 hardware/diagnostics/system_info.py
```
Checks RPi 4 model, Raspberry Pi OS version, active user (`omgawale`), installed tools (`rpicam-vid`, `ffmpeg`), and RAM.

### 3. Run Camera Detection
```bash
python3 hardware/camera/camera_detect.py
```
Scans for connected camera modules (IMX219, IMX477, IMX708, OV5647).

### 4. Run Live Camera Preview (Requires Connected Monitor / Desktop)
```bash
python3 hardware/camera/camera_preview.py
```
Opens a 5-second camera preview. If running headless (e.g. over SSH without X11/Wayland), it will safely report that headless mode is active.

### 5. Run 5-Second Test Recording
```bash
python3 hardware/camera/camera_test.py
```
Records a 5-second test video at 1280x720, 30 FPS to verify hardware encoding.

### 6. Run Storage & Mount Point Verification
```bash
python3 hardware/storage/storage_check.py
```
Reports total and available free disk space on the boot drive and detects whether the 1 TB external drive is plugged into USB.
