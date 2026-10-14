# Coop Vision — Phase 1: Detection + Stable Tracking (laptop → ESP32-CAM)

Live boxes with persistent IDs. Test with your head as chicken proxy today,
swap `--source` to ESP32-CAM MJPEG stream later. No training needed in Phase 1.

## 1. Install (Windows)

```powershell
cd 'c:\Users\User\Downloads\IoT'
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r vision/requirements-vision.txt
# Optional full quality: pip install ultralytics supervision
# Optional MQTT: pip install paho-mqtt
```

## 2. Head test (do this first)

```powershell
# Fast fallback (no download, works offline):
python vision/camera_tracker.py --source 0 --detector face --show-trails
# Best quality (auto-downloads yolov8n.pt ~6MB once):
python vision/camera_tracker.py --source 0 --detector auto --target person --show-trails
```

Keys: `q` quit · `t` trails · `s` snapshot crops · `r` reset IDs.

**Pass criteria:**
1. Walk left/right — `ID1` box follows, no flicker.
2. Cover face with hand 1s — ID persists (max-lost=30 frames ≈ 1s).
3. Leave frame 2–3s, re-enter — same ID if <1s, new ID if longer (expected).
4. Check `vision/track_logs.csv` — one row per ID per frame with speed/dwell.

Tune: `--max-lost 60` (keep ID 2s) · `--max-dist 160` (fast movement) · `--conf 0.5` (fewer false boxes).

## 3. Build Phase-2 dataset while testing

```powershell
python vision/capture_dataset.py --source 0 --detector auto --target person
```

Press `H` for healthy-acting, `S` for sick-acting (droopy/still/head-tilt).
Aim 200+ per class in `vision/dataset/healthy| sick/`, varied light + angles.
Phase 2 will train `healthy vs sick` head on these crops without touching tracking.

## 4. ESP32-CAM transfer (no model porting)

Classic ESP32-CAM cannot run YOLO — use it as streamer, keep inference on laptop/RPi:

1. Flash `firmware/coop-cam-streamer/` (AI-Thinker, CameraWebServer @ 81/stream).
2. Same script, new source:
```powershell
python vision/camera_tracker.py --source http://192.168.1.100:81/stream --detector auto --target bird
```
3. Optional MQTT to existing gateway:
```powershell
python vision/mqtt_bridge.py --source http://192.168.1.100:81/stream --broker 192.168.1.50 --port 1883 --no-tls --device-id coop-cam-01
```
New topic `iot/devices/coop-cam-01/vision` — gateway/dashboard fuse later, `PROTOCOL.md` telemetry untouched.

## 5. How stable ID works + honest limits

Detector finds boxes → Centroid/ByteTrack linker joins by distance + keeps lost
tracks `max-lost` frames. Trails + `speed/dwell` already computed per ID so Phase 2
health classifier plugs straight on. Limit: >2s disappearance or dense crossing
will eventually new-ID — solved in Phase 2 with feather-pattern ReID.
