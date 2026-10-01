#!/usr/bin/env python3
"""Local MQTT-to-Qwen gateway for coop telemetry.

The gateway keeps model credentials off the browser and can run on a PC first,
then move to a Raspberry Pi. Qwen is optional: rule-based analysis is used
when the local model endpoint is unavailable.
"""
import argparse
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

DEFAULT_BROKER = "broker.hivemq.com"
DEFAULT_PORT = 8883
DEFAULT_MODEL_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:3b"
TELEMETRY_TOPIC = "iot/devices/+/telemetry"
REQUEST_TOPIC = "iot/devices/+/ai/request"
LEGACY_REQUEST_TOPIC = "poultry/+/llm_request"
MAX_HISTORY = 30


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_device_id(value):
    return isinstance(value, str) and 1 <= len(value) <= 64 and all(
        character.isalnum() or character in "-_" for character in value
    )


def parse_json(raw):
    try:
        value = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def rule_based_analysis(device_id, reading):
    readings = reading.get("readings", {})
    warnings = []
    actions = []
    nd_risk_score = 10

    cough_count = readings.get("cough_count_10m", 0)
    object_temp = readings.get("object_temperature_c")
    motion_events = readings.get("motion_events_10m")
    sound_db = readings.get("sound_db")

    # Primary ND Marker 1: Acoustic respiratory distress (coughing/sneezing)
    if cough_count >= 12:
        warnings.append(f"Cough frequency is critically high ({cough_count} events/10m)")
        nd_risk_score += 40
    elif cough_count >= 5:
        warnings.append(f"Cough frequency is elevated ({cough_count} events/10m)")
        nd_risk_score += 20

    # Primary ND Marker 2: Flock fever / roosting bird surface hyperthermia (normal ~39-40.5C)
    if object_temp is not None:
        if object_temp >= 42.0:
            warnings.append(f"Flock surface temperature indicates high fever ({object_temp:.1f}°C)")
            nd_risk_score += 35
        elif object_temp >= 41.2:
            warnings.append(f"Flock surface temperature is elevated ({object_temp:.1f}°C)")
            nd_risk_score += 20

    # Primary ND Marker 3: Lethargy and depression (drop in activity count over 10m)
    if motion_events is not None and motion_events < 15:
        warnings.append(f"Flock activity is abnormally depressed ({motion_events} pulses/10m)")
        nd_risk_score += 20

    # Environmental / microclimate context
    if readings.get("ammonia_ppm", 0) > 20:
        warnings.append("Ammonia concentration exceeds 20 ppm safe limit")
        nd_risk_score += 10
    if readings.get("water_level_percent", 100) < 15 or readings.get("water_low") is True:
        warnings.append("Water supply is low")
    if readings.get("co2_ppm", 0) > 2500:
        warnings.append("Carbon dioxide is elevated")

    nd_risk_score = min(nd_risk_score, 100)

    # Classify overall threat level
    if nd_risk_score >= 60 or len(warnings) >= 2 and (cough_count >= 5 or (object_temp and object_temp >= 41.5)):
        threat = "CRITICAL"
        title = "High Risk: Newcastle Disease Respiratory & Fever Outbreak"
        body = (
            f"Detected severe anomalies: {'; '.join(warnings)}. "
            f"Newcastle Disease threat index is {nd_risk_score}/100. Immediate veterinary inspection recommended."
        )
        actions.append({
            "label": "Sound Coop Audible Alarm",
            "command": "notify_operator",
            "payload": {"duration_ms": 10000}
        })
    elif nd_risk_score >= 30 or warnings:
        threat = "WARNING"
        title = "Warning: Abnormal Flock Physiological Indicators"
        body = f"Anomalies detected: {'; '.join(warnings)}. Monitor flock closely for progression."
        actions.append({
            "label": "Alert Operator (Short Buzzer)",
            "command": "notify_operator",
            "payload": {"duration_ms": 3000}
        })
    else:
        threat = "NORMAL"
        nd_risk_score = 5
        title = "Flock Health Nominal"
        body = "All respiratory acoustic, body temperature, and behavioral activity indicators within safe flock baseline."

    return {
        "threat_level": threat,
        "nd_risk_score": nd_risk_score,
        "message": {"title": title, "body": body},
        "evidence": warnings,
        "recommended_actions": actions,
        "model": "local-nd-rules-engine",
        "device_id": device_id,
    }


class GatewayState:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.history = {}
        self.last_insight = {}
        self.mqtt_connected = False
        self.model_last_error = None
        self.request_count = 0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"qwen-gateway-{secrets.token_hex(3)}")
        if not args.no_tls:
            self.client.tls_set()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def start(self):
        self.client.connect(self.args.broker, self.args.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        self.mqtt_connected = reason_code == 0
        if self.mqtt_connected:
            client.subscribe([(TELEMETRY_TOPIC, 1), (REQUEST_TOPIC, 1), (LEGACY_REQUEST_TOPIC, 1)])
            print("Gateway connected and subscribed to telemetry and AI request topics.")
        else:
            print(f"MQTT connection failed: {reason_code}")

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.mqtt_connected = False

    def on_message(self, client, userdata, message):
        data = parse_json(message.payload)
        if data is None:
            print(f"Ignored invalid JSON on {message.topic}")
            return
        if message.topic.endswith("/telemetry"):
            self.store_telemetry(message.topic, data)
        elif "/ai/request" in message.topic or message.topic.endswith("/llm_request"):
            self.handle_request(message.topic, data)

    def store_telemetry(self, topic, data):
        device_id = data.get("device_id") or topic.split("/")[2]
        if not safe_device_id(device_id) or not isinstance(data.get("readings", {}), dict):
            print(f"Ignored invalid telemetry from {device_id!r}")
            return
        with self.lock:
            readings = self.history.setdefault(device_id, [])
            readings.append(data)
            del readings[:-MAX_HISTORY]

        # Auto-evaluate telemetry anomalies for early outbreak detection
        quick_check = rule_based_analysis(device_id, data)
        if quick_check["threat_level"] in {"WARNING", "CRITICAL"}:
            threading.Thread(
                target=self.analyze,
                args=(device_id, {"question": "Automatic anomaly trigger: evaluate possible ND outbreak.", "request_id": f"auto-{secrets.token_hex(4)}"}),
                daemon=True,
            ).start()

    def latest(self, device_id):
        with self.lock:
            values = self.history.get(device_id, [])
            return values[-1] if values else None

    def handle_request(self, topic, request):
        device_id = request.get("device_id") or topic.split("/")[2]
        if not safe_device_id(device_id):
            print(f"Rejected AI request for invalid device: {device_id!r}")
            return
        self.analyze(device_id, request)

    def analyze(self, device_id, request):
        reading = self.latest(device_id)
        if reading is None:
            return self.publish_insight(device_id, request, {
                "threat_level": "WARNING",
                "message": {"title": "No telemetry yet", "body": "Waiting for a valid reading from this device."},
                "evidence": ["no recent telemetry"],
                "recommended_actions": [],
                "model": "gateway-validation",
                "device_id": device_id,
            })

        result = self.ask_model(device_id, reading, request.get("question", "Analyze the latest coop telemetry."))
        self.publish_insight(device_id, request, result)

    def ask_model(self, device_id, reading, question):
        fallback = rule_based_analysis(device_id, reading)
        prompt = (
            "You are an expert avian bioacoustics and flock veterinary diagnostic system specializing in Newcastle Disease (ND) early detection. "
            "Evaluate telemetry including cough_count_10m (I2S acoustic cough bursts), object_temperature_c (roosting birds infrared surface temp, fever is >41.5°C), "
            "motion_events_10m (PIR activity, lethargy is <15), air_temperature_c, and humidity_percent.\n"
            "Return JSON only with exact keys: threat_level (NORMAL, WARNING, or CRITICAL), nd_risk_score (0-100 integer), "
            "message {title, body}, evidence (array of strings), and recommended_actions (array of {label, command, payload}). "
            "Allowed commands in recommended_actions: 'notify_operator'. Do not create arbitrary commands. "
            "Analyze this coop telemetry:\n" + json.dumps({"question": question, "reading": reading})
        )
        body = json.dumps({
            "model": self.args.model,
            "stream": False,
            "format": "json",
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        request = urllib.request.Request(self.args.model_url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.args.model_timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            content = response_data.get("message", {}).get("content", "")
            model_result = parse_json(content)
            if not model_result or model_result.get("threat_level") not in {"NORMAL", "WARNING", "CRITICAL"}:
                raise ValueError("model returned an invalid insight")
            if "nd_risk_score" not in model_result:
                model_result["nd_risk_score"] = fallback.get("nd_risk_score", 10)
            model_result["model"] = self.args.model
            model_result["device_id"] = device_id
            self.model_last_error = None
            return model_result
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.model_last_error = str(error)
            return fallback

    def publish_insight(self, device_id, request, result):
        request_id = request.get("request_id") or secrets.token_hex(8)
        result.update({"schema_version": 1, "request_id": request_id, "timestamp": utc_now()})
        topic = f"iot/devices/{device_id}/ai/insights"
        self.client.publish(topic, json.dumps(result), qos=1)
        with self.lock:
            self.last_insight[device_id] = result
            self.request_count += 1
        print(f"Published AI insight for {device_id}: {result['threat_level']} ({result['model']})")

    def status(self):
        with self.lock:
            return {
                "mqtt_connected": self.mqtt_connected,
                "model_configured": bool(self.args.model_url),
                "model_last_error": self.model_last_error,
                "devices": list(self.history),
                "requests_processed": self.request_count,
            }


class ApiHandler(BaseHTTPRequestHandler):
    gateway = None

    def send_json(self, status, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"ok": self.gateway.mqtt_connected, **self.gateway.status()})
        elif self.path == "/status":
            self.send_json(200, self.gateway.status())
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/ai/analyze":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = parse_json(self.rfile.read(length))
            device_id = request.get("device_id") if request else None
            if not safe_device_id(device_id):
                self.send_json(400, {"error": "device_id is required and must be safe"})
                return
            threading.Thread(target=self.gateway.analyze, args=(device_id, request), daemon=True).start()
            self.send_json(202, {"accepted": True, "device_id": device_id})
        except (ValueError, TypeError):
            self.send_json(400, {"error": "invalid JSON request"})

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="Run the local coop Qwen MQTT gateway.")
    parser.add_argument("--broker", default=DEFAULT_BROKER)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-tls", action="store_true", help="Disable TLS for a local MQTT broker")
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8787)
    parser.add_argument("--model-url", default=DEFAULT_MODEL_URL, help="Ollama-compatible /api/chat endpoint")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-timeout", type=float, default=45)
    args = parser.parse_args()
    gateway = GatewayState(args)
    ApiHandler.gateway = gateway
    try:
        gateway.start()
        server = ThreadingHTTPServer((args.api_host, args.api_port), ApiHandler)
        print(f"Gateway API listening at http://{args.api_host}:{args.api_port}")
        print(f"Model endpoint: {args.model_url} ({args.model})")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping gateway...")
    finally:
        if gateway.mqtt_connected:
            gateway.stop()


if __name__ == "__main__":
    main()
