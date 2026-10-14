#!/usr/bin/env python3
"""Collect labeled cough/background clips for Phase 2 audio training.

Cough or clap near the mic and press C to save the last 2 s as a cough-like
clip; press B at quiet moments for background. Target 200+ per class.

Run: .\\.venv\\Scripts\\python.exe audio\\capture_dataset.py
Keys: [c] save COUGH clip  [b] save BACKGROUND clip  [q] quit
"""
import argparse, sys
from datetime import datetime, timezone
from pathlib import Path
from collections import deque
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mic_monitor import MicSource, ClipBuffer, band_hist, status_for

SR = 16000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--device', default=None)
    ap.add_argument('--out', default='audio/dataset')
    ap.add_argument('--clip-s', type=float, default=2.0)
    a = ap.parse_args()
    out = Path(a.out)
    (out / 'cough').mkdir(parents=True, exist_ok=True)
    (out / 'background').mkdir(parents=True, exist_ok=True)
    src = MicSource(SR, a.device)
    try:
        src.start()
    except RuntimeError as exc:
        print(f'[err] {exc}')
        return
    clips = ClipBuffer(SR, 1600, keep_s=5.0)
    counts = {'cough': len(list((out / 'cough').glob('*.wav'))),
              'background': len(list((out / 'background').glob('*.wav')))}
    print(f'[cap] C=cough B=background Q=quit. Have {counts}.')
    print('[cap] e.g. cough into the mic -> C; room silence -> B.')
    try:
        import cv2
        hist = deque(maxlen=30)
        while True:
            block = src.read()
            if block is not None:
                clips.push(block)
                hist.append(band_hist(block, SR))
            view = np.zeros((200, 800, 3), dtype=np.uint8)
            cv2.putText(view, f'COUGH {counts["cough"]}   '
                              f'BACKGROUND {counts["background"]}',
                        (14, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (135, 201, 107), 2)
            cv2.putText(view, '[c]ough [b]ackground [q]uit',
                        (14, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (150, 160, 150), 1)
            cv2.putText(view, 'tip: cough/clap THEN press key within 2 s',
                        (14, 165), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (150, 160, 150), 1)
            cv2.imshow('Audio capture', view)
            k = cv2.waitKey(10) & 0xFF
            if k in (ord('q'), 27):
                break
            if k in (ord('c'), ord('b')):
                label = 'cough' if k == ord('c') else 'background'
                stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
                p = out / label / f'{label}_{stamp}.wav'
                if clips.write(p, tail_s=a.clip_s):
                    counts[label] += 1
                    print(f'[cap] {label} -> {p} ({counts[label]})')
                else:
                    print('[cap] buffer empty, wait 1 s and retry')
    except KeyboardInterrupt:
        pass
    finally:
        src.stop()
        try:
            import cv2
            cv2.destroyAllWindows()
        except Exception:
            pass
    print(f'[done] cough={counts["cough"]} background={counts["background"]}')


if __name__ == '__main__':
    main()
