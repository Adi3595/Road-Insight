# Road Recorder: Raspberry Pi 4 Road-Facing Dashcam

A standalone, production-minded Python application designed for continuous road-facing video recording on a Raspberry Pi 4 Model B.

The application records segmented H.264 video streams directly to an external USB pendrive (`/mnt/road-recorder-usb`), strictly safeguarding the host microSD card from being filled or worn out.

---

## 1. Project Directory Structure

```text
Road-Recorder/
├── hardware/                                # Standalone hardware diagnostics (Zero external dependencies)
│   ├── camera/
│   │   ├── camera_detect.py                 # Detects physical CSI camera (IMX219, IMX708, etc.)
│   │   ├── camera_test.py                   # Performs 5-second test capture (checks sensor & encoder)
│   │   └── camera_preview.py                # Displays live preview window (if desktop display is connected)
│   ├── storage/
│   │   ├── storage_check.py                 # Inspects block devices, mount points, and permissions
│   │   └── usb_setup.py                     # Guided USB pendrive setup and /etc/fstab configuration
│   ├── diagnostics/
│   │   └── system_info.py                   # Reports RPi 4 model, OS version, RAM, and camera tools
│   ├── __init__.py
│   └── README.md
├── road_recorder/                           # Core continuous recording engine
│   ├── __init__.py
│   ├── config.py                            # Loads and validates config.yaml with full fallback support
│   ├── camera.py                            # Supervises rpicam-vid / libcamera-vid capture processes
│   ├── recorder.py                          # Central recording state machine and segment coordinator
│   ├── segment_manager.py                   # Manages segment rotation, filenames, and MP4 stream remuxing
│   ├── storage_manager.py                   # Monitors USB space and strictly prevents microSD fallback
│   ├── recording_index.py                   # Append-only JSON Lines index (metadata/recording_index.jsonl)
│   └── logging_setup.py                     # Immediate-flush file and console logging
├── tests/                                   # Unit and regression test suite
│   ├── __init__.py
│   ├── test_config.py                       # Tests configuration parsing and threshold validation
│   ├── test_storage_manager.py              # Tests USB verification, SD card refusal, and thresholds
│   ├── test_segment_manager.py              # Tests naming conventions, MP4 finalization, and crash recovery
│   ├── test_recording_index.py              # Tests append-only integrity and corrupted line resilience
│   └── test_recorder_lifecycle.py           # Tests orchestrator start/stop and single-instance locks
├── config.yaml                              # Production configuration file
├── main.py                                  # Interactive beginner-friendly terminal interface
├── inspect_environment.py                   # One-shot unified environment & hardware inspector
├── run_tests.py                             # Zero-dependency test runner
├── requirements.txt                         # Python requirements (pyyaml, pytest)
├── .gitignore
└── README.md
```

### Dedicated USB Storage Layout (`/mnt/road-recorder-usb`)

When mounted, all recording-related data is stored exclusively on the USB pendrive:

```text
/mnt/road-recorder-usb/
├── recordings/
│   └── YYYY-MM-DD/
│       ├── RR_YYYYMMDD_HHMMSS_001.mp4
│       └── RR_YYYYMMDD_HHMMSS_002.mp4
├── metadata/
│   └── recording_index.jsonl
├── logs/
│   └── recording.log
├── diagnostics/
└── .road-recorder-volume
```

---

## 2. Operating Environment: Windows vs. Raspberry Pi

> [!IMPORTANT]
> **Windows vs Raspberry Pi Terminal:**
> - If you are editing files on a Windows laptop, run git and editing commands on Windows.
> - **All hardware commands, camera tools, and recording executions must be run directly inside a terminal on the Raspberry Pi.**

---

## 3. Quick Start on Raspberry Pi OS

### Step 1: Open Terminal on Raspberry Pi
Open the Raspberry Pi OS terminal (via desktop menu or shortcut `Ctrl+Alt+T`).

### Step 2: Navigate to Project Folder
```bash
cd ~/road-recorder   # or the path where you placed the project
```

### Step 3: Install System Prerequisites
Ensure modern camera tools, FFmpeg (for stream copying), and Python packages are installed:
```bash
sudo apt update
sudo apt install -y rpicam-apps ffmpeg python3-yaml python3-pip
```

### Step 4: Run Unified Environment & Hardware Check
Run the zero-dependency environment inspection:
```bash
python3 inspect_environment.py
```
This tests:
1. Raspberry Pi model and operating system.
2. Camera detection (`rpicam-hello`).
3. USB pendrive detection vs microSD card (`mmcblk0`).

---

## 4. Configuring the USB Pendrive Storage

Road Recorder strictly requires recording to an external USB pendrive (`/mnt/road-recorder-usb`) to protect your 32 GB boot microSD card from wear and space exhaustion.

### Option A: Guided Setup Assistant (Recommended)
Run the guided interactive setup tool:
```bash
python3 hardware/storage/usb_setup.py
```
This tool:
1. Identifies the USB drive and its filesystem UUID (without touching the microSD card).
2. Generates the exact `/etc/fstab` configuration line with `nofail`.
3. Creates the `/mnt/road-recorder-usb` directory and sets user write permissions.
4. Initializes the folder structure and creates the `.road-recorder-volume` marker file.

### Option B: Manual USB Mount Configuration
1. **Identify the USB drive partition and UUID:**
   ```bash
   lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,UUID,MODEL,TRAN
   ```
   Look for the USB device (e.g. `sda1`). Note its `UUID` and filesystem (e.g. `ext4` or `exfat`).

2. **Create mount directory:**
   ```bash
   sudo mkdir -p /mnt/road-recorder-usb
   ```

3. **Back up `/etc/fstab`:**
   ```bash
   sudo cp /etc/fstab /etc/fstab.bak.$(date +%Y%m%d_%H%M%S)
   ```

4. **Add entry to `/etc/fstab`:**
   Open `/etc/fstab` in a text editor:
   ```bash
   sudo nano /etc/fstab
   ```
   Append one of the following lines (replace with your actual UUID):
   - **For ext4 drives:**
     ```text
     UUID=YOUR-UUID-HERE  /mnt/road-recorder-usb  ext4  defaults,nofail  0  2
     ```
   - **For exFAT or FAT32 drives:**
     ```text
     UUID=YOUR-UUID-HERE  /mnt/road-recorder-usb  exfat  defaults,nofail,uid=1000,gid=1000,umask=022  0  0
     ```

5. **Mount and set ownership:**
   ```bash
   sudo mount -a
   sudo chown -R $USER:$USER /mnt/road-recorder-usb
   ```

6. **Initialize folders and volume marker:**
   ```bash
   python3 hardware/storage/usb_setup.py --init-dirs /mnt/road-recorder-usb
   ```

---

## 5. Running the Application

Launch the interactive console menu:
```bash
python3 main.py
```

### Main Menu Controls
- **1. Start Recording:** Begins continuous video capture in 5-minute segments.
- **2. Stop Recording:** Safely halts capture, finalizes the active segment, and updates the index.
- **3. View Status & Refresh:** Displays live elapsed time, active file size, and storage metrics.
- **4. List Recordings on USB:** Displays recent segments with duration and file sizes.
- **5. View USB Storage Diagnostics:** Checks mount status, write health, and block devices.
- **6. Run USB Setup Assistant:** Reruns the interactive `/etc/fstab` configuration helper.
- **7. Exit Application:** Safely exits the program.

### Safe Graceful Shutdown
You can safely press `Ctrl+C` at any time. The application will:
1. Stop the camera capture process.
2. Finalize and verify the in-progress video segment.
3. Append segment metadata to `recording_index.jsonl`.
4. Release camera and file locks.
5. Exit cleanly without orphaned background processes.

---

## 6. Locating and Playing Saved Videos

- **Command Line:**
  ```bash
  ls -lh /mnt/road-recorder-usb/recordings/$(date +%Y-%m-%d)/
  ```
- **Raspberry Pi File Manager:**
  ```bash
  pcmanfm /mnt/road-recorder-usb/recordings
  ```
- **Play Video (using VLC):**
  ```bash
  vlc /mnt/road-recorder-usb/recordings/$(date +%Y-%m-%d)/*.mp4
  ```

---

## 7. Running Unit Tests

Run the zero-dependency test runner on any machine:
```bash
python run_tests.py
```
Or with pytest:
```bash
pytest -v
```

---

## 8. Troubleshooting

| Symptom | Cause | Solution |
|---|---|---|
| `"USB recording drive not available. Recording has not started."` | The USB pendrive is unplugged or `/mnt/road-recorder-usb` is not mounted. | Plug in USB drive, run `sudo mount -a`, and verify with `python3 hardware/storage/storage_check.py`. |
| `"No physical camera detected"` | Loose CSI ribbon cable or camera disabled. | Check ribbon cable (blue tape faces Ethernet/USB ports on RPi 4). Run `rpicam-hello --list-cameras`. |
| `"Another instance of Road Recorder is currently active"` | A prior instance was killed unexpectedly. | Remove stale lockfile: `rm ~/.road_recorder.lock`. |
| `"Critical storage threshold reached"` | Free space on USB pendrive is below 1024 MB. | Offload older recordings from `/mnt/road-recorder-usb/recordings/` to free space. |
