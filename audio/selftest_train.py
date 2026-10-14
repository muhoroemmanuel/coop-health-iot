"""Smoke-validate the training pipeline headlessly on SYNTHETIC clips.

Generates cough-like (300/900 Hz bursts) + background (colored noise) WAVs
in audio/_synth/, trains with --augment 1, asserts artifacts + sane metrics.
Run: .\\.venv\\Scripts\\python.exe audio\\selftest_train.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
SR = 16000


def write_wav(path, y):
    import wave
    y16 = np.clip(y, -1.0, 1.0)
    y16 = (y16 * 32767).astype(np.int16)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(y16.tobytes())


def main():
    synth = ROOT / '_synth'
    if synth.exists():
        shutil.rmtree(synth)
    (synth / 'cough').mkdir(parents=True)
    (synth / 'background').mkdir(parents=True)
    rng = np.random.default_rng(11)
    for s in range(4):                      # 4 sessions per class
        for i in range(4):
            n = SR * 2
            tt = np.arange(n) / SR
            if True:
                sig = rng.standard_normal(n) * 0.02
                start = rng.integers(SR // 2, SR)
                bl = rng.integers(int(SR * 0.2), int(SR * 0.5))
                env = np.sin(np.pi * np.arange(bl) / bl) ** 2
                f0 = rng.uniform(250, 450)
                tone = (np.sin(2 * np.pi * f0 * tt[:bl])
                        + 0.6 * np.sin(2 * np.pi * 3 * f0 * tt[:bl])) \
                    * env * 0.5
                sig[start:start + bl] += tone
                write_wav(synth / 'cough' /
                          f'cough_20261013-120{s}0{i}-000000.wav', sig)
                bg = np.cumsum(rng.standard_normal(n)) * 0.0005 \
                    + rng.standard_normal(n) * 0.015
                write_wav(synth / 'background' /
                          f'background_20261013-121{s}0{i}-000000.wav', bg)
    print('[selftest] synthetic clips written')
    cmd = [sys.executable, str(ROOT / 'train_audio_baseline.py'),
           '--dataset', str(synth), '--augment', '1',
           '--out', str(ROOT / '_synth_model'), '--folds', '2']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print(r.stdout[-1500:])
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise SystemExit('train script failed')
    import json
    m = json.loads((ROOT / '_synth_model' / 'metrics.json').read_text())
    print('[selftest] recall =', m['recall_mean'],
          'clips =', m['n_clips'])
    assert m['recall_mean'] >= 0.85, 'synthetic baseline below target'
    assert (ROOT / '_synth_model' / 'model.pkl').exists()
    shutil.rmtree(synth)
    shutil.rmtree(ROOT / '_synth_model')
    print('TRAIN_SELFTEST_OK')


if __name__ == '__main__':
    main()
