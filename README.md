# coop-health-iot — Newcastle Disease Early-Warning System (IoT)

A low-cost, offline-first IoT system for **early detection of Newcastle Disease (ND)** in poultry
flocks. Battery-friendly ESP32-S3 nodes watch four non-invasive physiological and behavioural
markers, publish them over MQTT, and a local AI gateway (`gateway.py`) turns those signals into an
actionable threat assessment for the operator — all without sending data to the cloud.

```
+----------------------+        MQTT (QoS 1)        +--------------------------+
|  ESP32-S3 Node       |  iot/devices/<id>/...      |  Local AI Gateway        |
|  coop-health-01      | -------------------------> |  gateway.py (Python 3)   |
|                      |                            |                          |
|  INMP441  (cough)    | <------------------------- |  rule-based + Qwen 2.5   |
|  MLX90614 (fever)    |      commands / ack        |  via Ollama (optional)   |
|  HC-SR501 (activity) |                            |  HTTP API :8787          |
|  SHT31    (climate)  |                            +------------+-------------+
+----------------------+                                         |
                                                                 | ai/insights
                                                                 v
                                                    +--------------------------+
                                                    |  Operator Dashboard      |
                                                    |  dashboard.html          |
                                                    |  (browser + MQTT over WS)|
                                                    +--------------------------+
```

## Repository Layout

| Path | Description |
|---|---|
| `PROTOCOL.md` | Full MQTT topic / payload specification (single source of truth) |
| `gateway.py` | Local MQTT-to-AI gateway: subscribes to telemetry, publishes ND risk assessments |
| `dashboard.html` | Single-file operator dashboard (MQTT over WebSockets + Chart.js) |
| `requirements.txt` | Python dependencies for the gateway (`paho-mqtt`) |
| `firmware/coop-health-node/` | PlatformIO project for the ESP32-S3 coop health node |
| `firmware/coop-health-node/include/config.h` | Pin map, sampling and timing constants |
| `firmware/coop-health-node/include/secrets.example.h` | Template for Wi-Fi / MQTT credentials (copy to `secrets.h`) |

## Detection Signals & Sensing Hardware

| Marker | Sensor | Bus / Pin | Key telemetry fields |
|---|---|---|---|
| Acoustic respiratory distress (coughing / sneezing) | INMP441 MEMS microphone | I2S — BCLK `GPIO14`, WS `GPIO15`, DIN `GPIO16` | `cough_count_10m`, `cough_confidence`, `sound_db` |
| Flock fever / hyperthermia (normal ≈ 39.0–40.5 °C) | MLX90614 IR thermometer | I2C — SDA `GPIO8`, SCL `GPIO9` | `object_temperature_c` |
| Lethargy & depression | HC-SR501 PIR | Digital `GPIO4` | `motion_events_10m` |
| Ambient microclimate context | SHT31 | I2C — SDA `GPIO8`, SCL `GPIO9` | `air_temperature_c`, `humidity_percent` |

Supporting hardware: local alert LED/buzzer on `GPIO2`, optional battery divider on `GPIO1`.
Node behaviour: telemetry every **10 s**, retained heartbeat every **60 s**, 10-minute rolling
accumulators (120 × 5 s buckets), MQTT Last Will & Testament for automatic offline detection.

## MQTT Topics (`iot/devices/<device_id>/...`)

| Topic | Direction | Retained | Purpose |
|---|---|---|---|
| `telemetry` | Node → Broker | No | Sensor payload every 10 s |
| `status` | Node → Broker | **Yes** | Online heartbeat / LWT offline notice |
| `commands` | Dashboard/Gateway → Node | No | `notify_operator`, `identify`, `reset_baseline` |
| `commands/ack` | Node → Dashboard | No | Execution acknowledgement |
| `ai/request` | Dashboard → Gateway | No | Ask for an on-demand AI diagnostic |
| `ai/insights` | Gateway → Dashboard | **Yes** | Threat level, evidence, recommended actions |

See [`PROTOCOL.md`](PROTOCOL.md) for the complete JSON schemas and field ranges.

## Threat Levels

| Level | Meaning |
|---|---|
| `NORMAL` | All monitored parameters within baseline |
| `WARNING` | One primary ND marker elevated (cough spike, thermal rise, or activity drop) |
| `CRITICAL` | Score ≥ 60 or two or more confirmed markers — inspect the flock immediately |

The gateway always produces a result: it falls back to deterministic rule-based scoring whenever the
local Qwen model endpoint is unavailable, so the alarm path never depends on the AI being online.

## Quick Start

### 1. Local AI Gateway (`gateway.py`)

```powershell
cd 'c:\Users\User\Downloads\IoT'
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python gateway.py
```

Defaults: MQTT `broker.hivemq.com:8883` (TLS), HTTP API `http://127.0.0.1:8787`,
model endpoint `http://127.0.0.1:11434/api/chat` (`qwen2.5:3b`).

| Flag | Purpose |
|---|---|
| `--broker`, `--port` | Point at your own Mosquitto broker |
| `--no-tls` | Plain MQTT (used with a local broker on `1883`) |
| `--api-host`, `--api-port` | Gateway HTTP API bind address |
| `--model-url`, `--model` | Any Ollama-compatible `/api/chat` endpoint |
| `--model-timeout` | Seconds to wait for the model before falling back to rules |

Local broker example:

```powershell
python gateway.py --broker 192.168.1.50 --port 1883 --no-tls
```

### 2. Optional — run the model locally with Ollama

```powershell
ollama pull qwen2.5:3b
ollama serve
```

The gateway degrades gracefully: if the model is unreachable it publishes rule-based insights and
records the error in `/status` (`model_last_error`).

### 3. Operator Dashboard

Open `dashboard.html` directly in a browser (no build step, no server required). Default values —
broker `wss://broker.hivemq.com:8884/mqtt`, topic filter `iot/devices/+/telemetry` — then press
**Connect MQTT**. The dashboard renders live sensor cards, activity charts, node status and the Qwen
AI copilot panel, and can publish commands back to the node.

### 4. Firmware (ESP32-S3 + PlatformIO)

```powershell
cd 'c:\Users\User\Downloads\IoT\firmware\coop-health-node'
Copy-Item include\secrets.example.h include\secrets.h   # then fill in real credentials
pio run -t upload
pio device monitor -b 115200
```

Or use the VS Code PlatformIO extension: open `firmware/coop-health-node`, then
**PlatformIO → Upload** (`esp32-s3-devkitc-1`, Arduino framework).

### Gateway HTTP API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness + MQTT connection state |
| `GET` | `/status` | Known devices, model errors, requests processed |
| `POST` | `/ai/analyze` | Body `{"device_id": "coop-health-01"}` — triggers an AI insight |

## Security Notes

- `include/secrets.h` (real Wi-Fi / MQTT credentials) is **git-ignored** — only the template
  `secrets.example.h` is committed. Never paste live credentials into a commit.
- The public HiveMQ broker has no authentication and no privacy; for real flocks run a local
  Mosquitto broker with credentials and point both the firmware and the gateway at it.
- The gateway HTTP API binds to `127.0.0.1` by default; expose it deliberately, not by accident.

## License

No license file has been added yet. Add one (for example MIT) before allowing reuse or
redistribution.


