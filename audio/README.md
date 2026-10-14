# Coop Audio — Phase 1: Mic Capture for Cough Detection (laptop -> INMP441)

Capture, meter, and log coop-like sounds with your laptop mic today. Emits the
**same `PROTOCOL.md` telemetry fields** (`cough_count_10m`, `cough_confidence`,
`sound_db`) the ESP32-S3 node will later publish from its INMP441 I2S mic.

## 1. Install (Windows)

```powershell
cd 'c:\Users\User\Downloads\IoT'
.\.venv\Scripts\python.exe -m pip install -r audio\requirements-audio.txt
```

## 2. Live monitor (do this first)

```powershell
.\.venv\Scripts\python.exe audio\mic_monitor.py --calibrate
.\.venv\Scripts\python.exe audio\mic_monitor.py --no-show   # headless CSV
.\.venv\Scripts\python.exe -m sounddevice                   # list mic devices
.\.venv\Scripts\python.exe audio\mic_monitor.py --device 1  # pick another mic
```

Keys: `q` quit · `c` recalibrate floor · `s` save last 2 s WAV · `r` reset.

**Self-test:** stay quiet 3 s for calibration, then **clap once** 30 cm from
the mic — you should see `[evt] cough-like burst` and `cough_10m=1` with the
status flipping toward WARNING at 5 events / CRITICAL at 12 (same thresholds
as `gateway.py`, so it previews the real alarm path).

## 3. Build the Phase-2 dataset

```powershell
.\.venv\Scripts\python.exe audio\capture_dataset.py
```

Cough/clap **then** press `C` (saves last 2 s as `cough/`) or press `B` during
silence for `background/`. Aim 200+ clips per class, varied distance/room.

## 4. Stream to the existing gateway (no gateway changes)

```powershell
pip install paho-mqtt
.\.venv\Scripts\python.exe audio\mqtt_bridge.py --no-tls --broker 192.168.1.50
```

Publishes exact node telemetry as `coop-audio-01` every 10 s plus a retained
`.../status` heartbeat — `gateway.py` scores it with the live cough rules.

## 5. ESP32 transfer path

Capture/analysis is 16 kHz mono = `I2S_SAMPLE_RATE` in
`firmware/coop-health-node/include/config.h`. When Phase-2-trained (TFLite /
Edge Impulse on your labeled clips), the classifier runs on the ESP32-S3 and
fills the same `cough_count_10m` / `cough_confidence` fields — laptop becomes
the node, no protocol change.

**Honest limit:** this energy detector is a *candidate spotter* for labeling,
not a diagnosis. Real cough-vs-noise separation comes from your labeled data.
dB shown is dBFS (relative) — recalibrate per room with `c`.
