#!/usr/bin/env python3
"""Broker probe: subscribe to a topic and print every message.

Use to split "publisher problem" from "dashboard problem":
  keep tools/sim_node.py running, then run this in another terminal.
  GOT lines here + empty dashboard  => dashboard-side (WSS/CDN).
  Silence here                       => publisher-side (sim/device down).

Run: .\\.venv\\Scripts\\python.exe tools\\watch_topic.py
"""
import argparse
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print('[err] paho-mqtt missing in venv. Run:')
    print('  .\\.venv\\Scripts\\python.exe -m pip install paho-mqtt')
    raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description='MQTT topic probe')
    ap.add_argument('--broker', default='broker.hivemq.com')
    ap.add_argument('--port', type=int, default=1883)
    ap.add_argument('--topic', default='iot/devices/+/telemetry')
    ap.add_argument('--seconds', type=float, default=120.0)
    ap.add_argument('--no-tls', action='store_true')
    a = ap.parse_args()
    n = {'count': 0}
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                      client_id=f'watch-{int(time.time()) % 9999}')

    def on_conn(c, u, f, reason, props=None):
        print(f'[watch] connected rc={reason}, subscribing {a.topic}')
        c.subscribe(a.topic, qos=1)

    def on_msg(c, u, msg):
        n['count'] += 1
        body = msg.payload.decode('utf-8', 'replace')[:120]
        print(f'GOT #{n["count"]} {msg.topic} :: {body}')

    cli.on_connect = on_conn
    cli.on_message = on_msg
    if not a.no_tls and a.port == 8883:
        cli.tls_set()
    try:
        cli.connect(a.broker, a.port, 60)
    except OSError as exc:
        print(f'[err] cannot reach {a.broker}:{a.port} ({exc})')
        print('[hint] check the broker host/port or your network/firewall.')
        return
    print(f'[watch] listening {a.seconds:.0f}s — Ctrl+C to stop early.')
    cli.loop_start()
    try:
        time.sleep(a.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        cli.loop_stop()
        cli.disconnect()
    print(f'[watch] done. messages seen: {n["count"]}')
    if n['count'] == 0:
        print('[hint] silence => nothing is publishing. Start '
              'tools/sim_node.py (or the real node) and retry.')


if __name__ == '__main__':
    main()
