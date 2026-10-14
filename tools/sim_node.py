#!/usr/bin/env python3
"""Scenario simulator: fake ESP32-S3 flock days on MQTT, no hardware needed.

Replays time-compressed coop days in exact PROTOCOL.md telemetry schema so
gateway.py + dashboard.html can be tested end-to-end (alarm path, charts,
Qwen insights) before the sensors arrive.

Scenarios (day compressed to --minutes):
  healthy    steady baseline, no markers elevated
  outbreak   ND-like progression: fever spike -> cough cluster -> activity drop
  falsealarm fan noise + feeder clatter, no real markers

Run:
  .\\.venv\\Scripts\\python.exe tools\\sim_node.py --scenario outbreak --minutes 3
  .\\.venv\\Scripts\\python.exe tools\\sim_node.py --scenario healthy --minutes 1 --device-id coop-health-01 --no-tls
"""
import argparse, json, math, random, sys, time
from datetime import datetime, timezone

INTERVAL_S = 10.0  # same 10 s cadence as firmware telemetry


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def scenario_readings(name, frac, rng):
    """frac: 0.0 start of day .. 1.0 end. Returns readings dict."""
    if name == 'healthy':
        return {'cough_count_10m': rng.randint(0, 2),
                'cough_confidence': round(rng.uniform(0.0, 0.3), 2),
                'sound_db': round(rng.uniform(44.0, 50.0), 1),
                'object_temperature_c': round(rng.uniform(39.0, 40.3), 1),
                'air_temperature_c': round(rng.uniform(21.0, 24.0), 1),
                'humidity_percent': round(rng.uniform(55.0, 65.0), 1),
                'motion_events_10m': rng.randint(30, 60)}
    if name == 'falsealarm':
        return {'cough_count_10m': rng.randint(0, 2),
                'cough_confidence': round(rng.uniform(0.0, 0.4), 2),
                'sound_db': round(rng.uniform(62.0, 72.0), 1),
                'object_temperature_c': round(rng.uniform(39.0, 40.4), 1),
                'air_temperature_c': round(rng.uniform(24.0, 27.0), 1),
                'humidity_percent': round(rng.uniform(55.0, 65.0), 1),
                'motion_events_10m': rng.randint(30, 70)}
    # outbreak: ND-like staged progression across the compressed day
    # fever ramps from frac~0.15, peaks ~0.45 (42.4C), decays slowly
    fever = 39.6 + 2.8 * math.exp(-((frac - 0.45) ** 2) / 0.05)
    cough_peak = math.exp(-((frac - 0.60) ** 2) / 0.008)
    cough = int(clamp(round(rng.gauss(14 * cough_peak, 2.5)), 0, 40))
    motion = int(clamp(rng.gauss(45 - 38 * max(0.0, (frac - 0.5)) * 2, 6),
                       0, 80))
    return {'cough_count_10m': cough,
            'cough_confidence': round(clamp(0.35 + 0.6 * cough_peak
                                           + rng.uniform(-0.1, 0.1), 0, 0.99),
                                      2),
            'sound_db': round(rng.uniform(46.0, 52.0)
                              + 8 * cough_peak, 1),
            'object_temperature_c': round(fever + rng.uniform(-0.2, 0.2), 1),
            'air_temperature_c': round(rng.uniform(22.0, 26.0), 1),
            'humidity_percent': round(rng.uniform(55.0, 68.0), 1),
            'motion_events_10m': motion}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', default='outbreak',
                    choices=['healthy', 'outbreak', 'falsealarm'])
    ap.add_argument('--minutes', type=float, default=15.0,
                    help='compressed day length; telemetry is NOT retained, '
                         'so keep this longer than your dashboard session')
    ap.add_argument('--device-id', default='coop-health-01')
    ap.add_argument('--broker', default='broker.hivemq.com')
    ap.add_argument('--port', type=int, default=1883)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--no-tls', action='store_true')
    a = ap.parse_args()
    rng = random.Random(a.seed)
    total = max(1, int(a.minutes * 60 / INTERVAL_S))

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print('[err] paho-mqtt missing in venv. Run:')
        print('  .\\.venv\\Scripts\\python.exe -m pip install paho-mqtt')
        return
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                      client_id=f'sim-{a.scenario}-{int(time.time()) % 9999}')
    if not a.no_tls and a.port == 8883:
        cli.tls_set()
    cli.connect(a.broker, a.port, 60)
    cli.loop_start()
    tele = f'iot/devices/{a.device_id}/telemetry'
    stat = f'iot/devices/{a.device_id}/status'
    boot = time.monotonic()
    base = datetime.now(timezone.utc)
    print(f'[sim] {a.scenario}: {total} packets over {a.minutes} min '
          f'-> {tele}')
    try:
        for i in range(total):
            frac = i / max(1, total - 1)
            payload = {'device_id': a.device_id,
                       'timestamp': base.isoformat(),
                       'uptime_s': int(time.monotonic() - boot),
                       'readings': scenario_readings(a.scenario, frac, rng),
                       'battery_percent': 95,
                       'rssi_dbm': -58}
            cli.publish(tele, json.dumps(payload), qos=1)
            r = payload['readings']
            print(f'[sim] {i + 1:3d}/{total} cough={r["cough_count_10m"]:2d} '
                  f'objT={r["object_temperature_c"]:.1f}C '
                  f'motion={r["motion_events_10m"]:2d} '
                  f'sound={r["sound_db"]:.0f}dB')
            time.sleep(INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        cli.publish(stat, json.dumps(
            {'device_id': a.device_id, 'state': 'offline',
             'timestamp': datetime.now(timezone.utc).isoformat()}),
            qos=1, retain=True)
        cli.loop_stop()
        cli.disconnect()
        print('[sim] done')


if __name__ == '__main__':
    main()
