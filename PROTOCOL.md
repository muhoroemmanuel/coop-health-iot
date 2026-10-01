# Newcastle Disease (ND) Early Warning IoT Protocol Specification

This document defines the MQTT communication protocol between the **ESP32-S3 Coop Health Nodes**, the **Local AI Gateway (`gateway.py`)**, and the **Operator Dashboard (`dashboard.html`)**.

The protocol is streamlined for the **early detection of Newcastle Disease** in poultry flocks using four non-invasive physiological and behavioral indicators:
1. **Acoustic respiratory symptoms** (chicken coughing/sneezing count via I2S microphone)
2. **Flock fever** (infrared non-contact flock temperature via MLX90614)
3. **Lethargy and depression** (activity rate via PIR motion sensor)
4. **Ambient microclimate** (temperature and humidity context via SHT31)

---

## 1. Topic Hierarchy

All topics use the base namespace: `iot/devices/<device_id>/...`

| Topic Pattern | Direction | QoS | Retained | Purpose |
|---|---|---|---|---|
| `iot/devices/<device_id>/telemetry` | Node → Broker | 1 | No | Periodic flock health sensor data (every 10s) |
| `iot/devices/<device_id>/status` | Node → Broker | 1 | **Yes** | Device lifecycle & heartbeat (LWT offline / online heartbeat) |
| `iot/devices/<device_id>/commands` | Dashboard/GW → Node | 1 | No | Remote commands sent to node (e.g. trigger alarm/buzzer) |
| `iot/devices/<device_id>/commands/ack` | Node → Dashboard/GW | 1 | No | Node acknowledgement of command execution |
| `iot/devices/<device_id>/ai/request` | Dashboard → Gateway | 1 | No | Manual trigger for Qwen AI diagnostic analysis |
| `iot/devices/<device_id>/ai/insights` | Gateway → Dashboard | 1 | **Yes** | Diagnostic assessment, ND threat level & recommendations |

`<device_id>` is an alphanumeric string identifying the physical node (e.g., `coop-health-01`).

---

## 2. Telemetry Payload (`.../telemetry`)

Published by the ESP32-S3 node every 10 seconds.

### JSON Schema
```json
{
  "device_id": "coop-health-01",
  "timestamp": "2026-09-25T12:00:10Z",
  "uptime_s": 3600,
  "readings": {
    "cough_count_10m": 0,
    "cough_confidence": 0.0,
    "sound_db": 48.2,
    "object_temperature_c": 39.4,
    "air_temperature_c": 22.8,
    "humidity_percent": 61.4,
    "motion_events_10m": 42
  },
  "battery_percent": 95,
  "rssi_dbm": -58
}
```

### Telemetry Field Definitions

| Field | Type | Unit | Range | Sensor | Description |
|---|---|---|---|---|---|
| `readings.cough_count_10m` | integer | count | 0–500 | INMP441 (I2S) | Rolling count of detected cough/sneeze events over the last 10 minutes |
| `readings.cough_confidence` | float | % | 0.0–100.0 | INMP441 / TinyML | Classifier confidence score for the detected respiratory sounds |
| `readings.sound_db` | float | dB SPL | 30.0–110.0 | INMP441 (I2S) | Ambient sound level in the coop (RMS dB) |
| `readings.object_temperature_c` | float | °C | 15.0–50.0 | MLX90614 (I2C) | Surface temperature of birds on perch/roost (fever indicator) |
| `readings.air_temperature_c` | float | °C | -10.0–60.0 | SHT31 (I2C) | Ambient coop air temperature |
| `readings.humidity_percent` | float | % | 0.0–100.0 | SHT31 (I2C) | Relative humidity |
| `readings.motion_events_10m` | integer | count | 0–1000 | HC-SR501 PIR | Accumulated flock movement pulses over the last 10 minutes |
| `battery_percent` | integer | % | 0–100 | ADC Pin (GPIO 1) | Node supply battery charge (100% if mains powered) |
| `rssi_dbm` | integer | dBm | -100–0 | ESP32 Wi-Fi | Wi-Fi received signal strength |
| `uptime_s` | integer | seconds | >= 0 | ESP32 System | Node uptime since last reboot |

---

## 3. Node Status & LWT (`.../status`)

Published with **retained flag = true**.

### Online Status (Heartbeat every 60 seconds and on boot)
```json
{
  "device_id": "coop-health-01",
  "state": "online",
  "firmware": "1.0.0",
  "ip": "192.168.1.145",
  "rssi_dbm": -58,
  "battery_percent": 95,
  "uptime_s": 3600,
  "timestamp": "2026-09-25T12:00:00Z"
}
```

### Offline Status (MQTT Last Will and Testament - LWT)
Configured during MQTT connection:
```json
{
  "device_id": "coop-health-01",
  "state": "offline",
  "timestamp": "2026-09-25T12:01:15Z"
}
```

---

## 4. Remote Node Commands (`.../commands`)

Sent by the Dashboard or Gateway to trigger hardware feedback on the node.

### Payload Schema
```json
{
  "command": "notify_operator",
  "duration_ms": 5000
}
```

Supported commands:
- `notify_operator`: Activates the on-board alarm LED/buzzer for `duration_ms` (default 3000ms).
- `identify`: Flashes LED 5 times quickly.
- `reset_baseline`: Clears 10-minute accumulator buffers.

### Acknowledgement Payload (`.../commands/ack`)
Published by the node immediately upon receipt and action:
```json
{
  "device_id": "coop-health-01",
  "command": "notify_operator",
  "status": "success",
  "timestamp": "2026-09-25T12:00:15Z"
}
```

---

## 5. AI Diagnostic Assessment (`.../ai/insights`)

Published by `gateway.py` either automatically when telemetry anomalies are detected or on demand via `.../ai/request`.

```json
{
  "schema_version": 1,
  "request_id": "req-98af3b1c",
  "device_id": "coop-health-01",
  "timestamp": "2026-09-25T12:00:12Z",
  "threat_level": "CRITICAL",
  "nd_risk_score": 85,
  "message": {
    "title": "High Risk: Respiratory Outbreak & Fever Detected",
    "body": "Cough frequency has jumped to 14 events/10m while roost temperature is elevated at 42.6°C (+2.2°C above flock baseline). Severe risk of Newcastle Disease."
  },
  "evidence": [
    "Cough count (14 in 10m) is 4.6x above 1-hour rolling baseline",
    "Flock surface temperature (42.6°C) indicates hyperthermia/fever",
    "Flock activity declined by 65% over the past 30 minutes"
  ],
  "recommended_actions": [
    {
      "label": "Sound Coop Audible Alarm",
      "command": "notify_operator",
      "payload": {"duration_ms": 10000}
    }
  ],
  "model": "qwen2.5:3b"
}
```

### Threat Levels:
- `NORMAL`: All physiological parameters within baseline ranges.
- `WARNING`: One primary marker elevated (e.g., mild cough spike OR thermal rise OR activity drop).
- `CRITICAL`: Two or more primary markers confirmed (e.g., cough cluster + fever, or coughing + severe lethargy). Immediate flock inspection required.

