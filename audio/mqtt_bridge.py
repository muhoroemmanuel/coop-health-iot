#!/usr/bin/env python3
"""Publish laptop-mic cough telemetry in the exact PROTOCOL.md node schema.

gateway.py consumes it UNCHANGED (device coop-audio-01, same 10 s cadence,
same cough_count_10m / cough_confidence / sound_db fields).

Run: .\\.venv\\Scripts\\python.exe audio\\mqtt_bridge.py --no-tls
"""
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mic_monitor import MicSource, CoughDetector, RollingCounter, SR_DEFAULT

TELEMETRY_S = 10.0   # matches firmware TELEMETRY_INTERVAL_MS
HEARTBEAT_S = 60.0   # matches firmware HEARTBEAT_INTERVAL_MS


def telemetry(device_id, boot, det, counter):
    info = det.last
    return {'device_id': device_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'uptime_s': int(time.monotonic() - boot),
            'readings': {'cough_count_10m': counter.total(),
                         'cough_confidence': round(float(info.get(
                             'confidence', 0.0)), 2),
                         'sound_db': round(float(info.get('db', -90.0)) + 90.0,
                                           1),
                         'object_temperature_c': None,
                         'air_temperature_c': None,
                         'humidity_percent': None}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--device', default=None, help='mic device index/name')
    ap.add_argument('--broker', default='broker.hivemq.com')
    ap.add_argument('--port', type=int, default=1883)
    ap.add_argument('--device-id', default='coop-audio-01')
    ap.add_argument('--no-tls', action='store_true')
    a = ap.parse_args()
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print('[err] paho-mqtt missing: pip install paho-mqtt')
        return
    boot = time.monotonic()
    det = CoughDetector(sr=SR_DEFAULT, thr_db=12.0)
    det.calibrate(3.0)
    counter = RollingCounter()
    src = MicSource(SR_DEFAULT, a.device)
    try:
        src.start()
    except RuntimeError as exc:
        print(f'[err] {exc}')
        return
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                      client_id=f'{a.device_id}-{int(time.time()) % 9999}')
    if not a.no_tls and a.port == 8883:
        cli.tls_set()
    cli.connect(a.broker, a.port, 60)
    cli.loop_start()
    tele = f'iot/devices/{a.device_id}/telemetry'
    stat = f'iot/devices/{a.device_id}/status'
    print(f'[mqtt] telemetry -> {tele} every {TELEMETRY_S:.0f}s. Ctrl+C stop.')
    nxt_tele, nxt_hb, conf = 0.0, 0.0, 0.0
    try:
        while True:
            block = src.read()
            if block is None:
                time.sleep(0.01)
            else:
                info = det.process(block)
                if info['event']:
                    counter.hit()
                    conf = info['confidence']
                    print(f'[evt] conf={conf:.2f} count_10m={counter.total()}')
            now = time.monotonic()
            if now >= nxt_tele:
                nxt_tele = now + TELEMETRY_S
                payload = telemetry(a.device_id, boot, det, counter)
                cli.publish(tele, json.dumps(payload), qos=1)
                print(f'[mqtt] count_10m={payload["readings"]["cough_count_10m"]} '
                      f'db={payload["readings"]["sound_db"]}')
            if now >= nxt_hb:
                nxt_hb = now + HEARTBEAT_S
                cli.publish(stat, json.dumps(
                    {'device_id': a.device_id, 'state': 'online',
                     'firmware': 'audio-bridge-1.0.0',
                     'uptime_s': int(now - boot),
                     'timestamp': datetime.now(timezone.utc).isoformat()}),
                    qos=1, retain=True)
    except KeyboardInterrupt:
        pass
    finally:
        cli.publish(stat, json.dumps(
            {'device_id': a.device_id, 'state': 'offline',
             'timestamp': datetime.now(timezone.utc).isoformat()}),
            qos=1, retain=True)
        src.stop()
        cli.loop_stop()
        cli.disconnect()
        print('[done] offline status published')


if __name__ == '__main__':
    main()
