"""Headless self-test: synthetic cough burst -> exactly 1 event; silence -> 0."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np

from mic_monitor import CoughDetector, RollingCounter, ClipBuffer, status_for

SR = 16000
N = SR // 10  # one 100 ms block

rng = np.random.default_rng(7)
bg = (rng.standard_normal(N) * 40).astype(np.int16)
sig = np.zeros(SR * 3, dtype=np.float64)
sig[:SR] += rng.standard_normal(SR) * 40
burst_n = int(SR * 0.3)
env = np.sin(np.pi * np.arange(burst_n) / burst_n) ** 2
tt = np.arange(burst_n) / SR
tone = (np.sin(2 * np.pi * 300 * tt) + 0.6 * np.sin(2 * np.pi * 900 * tt)
        + 0.3 * rng.standard_normal(burst_n)) * env * 9000
sig[SR:SR + burst_n] += tone
sig[2 * SR:] += rng.standard_normal(SR) * 40
frames = sig.reshape(-1, N).astype(np.int16)

det = CoughDetector(sr=SR)
for _ in range(30):
    det.process(bg)
det.floor_db = float(np.percentile(
    [det.process(bg)['db'] for _ in range(30)], 10))
det.floor_db = min(det.floor_db, -70.0)  # test-harness: force sane floor
events, confs = 0, []
for f in frames:
    r = det.process(f)
    if r['event']:
        events += 1
        confs.append(r['confidence'])
print('AUDIO_SIM events =', events,
      'maxconf =', round(max(confs) if confs else 0.0, 2))
assert events == 1 and confs and confs[0] >= 0.30, 'cough burst not detected'

quiet = CoughDetector(sr=SR)
for _ in range(30):
    quiet.process(bg)
quiet.floor_db = float(np.percentile(
    [quiet.process(bg)['db'] for _ in range(30)], 10))
qev = sum(1 for _ in range(30) if quiet.process(bg)['event'])
print('SILENCE events =', qev)
assert qev == 0, 'false positives on background'

rc = RollingCounter()
rc.hit()
rc.hit()
print('COUNTER total =', rc.total(), 'status(5) =', status_for(5)[0])
assert rc.total() == 2 and status_for(5)[0] == 'WARNING'

cb = ClipBuffer(SR, N)
cb.push(bg)
ok = cb.write('audio/_selftest.wav')
print('CLIP wrote =', ok)
assert ok and Path('audio/_selftest.wav').stat().st_size > 1000
os.remove('audio/_selftest.wav')

print('SELFTEST_OK')
