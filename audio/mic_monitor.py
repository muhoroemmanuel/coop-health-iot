#!/usr/bin/env python3
"""Laptop mic monitor — Phase 1 sound capture for cough/sneeze detection.

Test with your own claps/coughs now -> same schema feeds INMP441 on ESP32-S3 later.

Run:
  .\\.venv\\Scripts\\python.exe -m pip install -r audio\\requirements-audio.txt
  .\\.venv\\Scripts\\python.exe audio\\mic_monitor.py --calibrate
  .\\.venv\\Scripts\\python.exe audio\\mic_monitor.py --no-show   (headless CSV logging)

Keys: [q] quit  [c] recalibrate noise floor  [s] save last 2s clip  [r] reset counters
Emit schema matches PROTOCOL.md telemetry: cough_count_10m, cough_confidence, sound_db
"""
import argparse, csv, threading, time, wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

SR_DEFAULT = 16000          # matches firmware I2S_SAMPLE_RATE in config.h
BLOCK_MS = 100              # analysis frame
GREEN, YELLOW, RED = (107, 201, 135), (92, 184, 231), (97, 117, 232)


class RollingCounter:
    """Mirrors firmware: 120 five-second buckets = rolling 10-minute window."""
    def __init__(self, bucket_s=5, bucket_count=120):
        self.bucket_s = bucket_s
        self.buckets = deque([0] * bucket_count, maxlen=bucket_count)
        self._cur = 0
        self._cur_start = time.monotonic()

    def hit(self):
        self._roll()
        self._cur += 1

    def _roll(self):
        now = time.monotonic()
        while now - self._cur_start >= self.bucket_s:
            self.buckets.append(self._cur)
            self._cur = 0
            self._cur_start += self.bucket_s

    def total(self):
        self._roll()
        return self._cur + sum(self.buckets)

    def reset(self):
        self.buckets = deque([0] * len(self.buckets), maxlen=len(self.buckets))
        self._cur = 0
        self._cur_start = time.monotonic()


class ClipBuffer:
    """Keeps trailing audio so events can be written to WAV after they end."""
    def __init__(self, sr, block_n, keep_s=3.0):
        self.sr = sr
        self.buf = deque(maxlen=max(1, int(keep_s * sr / block_n)))

    def push(self, block):
        self.buf.append(np.asarray(block, dtype=np.int16))

    def write(self, path, tail_s=2.0):
        import math
        n = min(len(self.buf), max(1, math.ceil(tail_s * self.sr / 1600)))
        frames = list(self.buf)[-n:]
        if not frames:
            return False
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            w.writeframes(np.concatenate(frames).tobytes())
        return True


class CoughDetector:
    """Energy + spectral-shape candidate detector. Tunable; labels train Phase 2.

    Emits an event when a burst rises clearly above the adaptive noise floor,
    lasts 0.08-1.5 s, and carries mid-band (300-2000 Hz) energy typical of
    poultry coughs/sneezes. Confidence = burst prominence above floor.
    """
    def __init__(self, sr=SR_DEFAULT, thr_db=12.0, min_dur_s=0.08,
                 max_dur_s=1.5, refractory_s=0.6, floor_alpha=0.01):
        self.sr = sr
        self.thr = thr_db
        self.min_b = max(1, int(min_dur_s * 1000 / BLOCK_MS))
        self.max_b = max(1, int(max_dur_s * 1000 / BLOCK_MS))
        self.refr_b = max(1, int(refractory_s * 1000 / BLOCK_MS))
        self.alpha = floor_alpha
        self.floor_db = None
        self._calib = []
        self._active = 0
        self._peak = -120.0
        self._quiet = 0
        self._cooldown = 0
        self._win = np.hanning(int(sr * BLOCK_MS / 1000))
        self._burst_mid = 0.0
        self._burst_cent = 0.0
        self.last = {'db': -120.0, 'floor': -120.0, 'above': 0.0,
                     'centroid': 0.0, 'mid_ratio': 0.0,
                     'event': False, 'confidence': 0.0}

    def calibrate(self, seconds=3.0):
        self._calib = [None] * max(1, int(seconds * 1000 / BLOCK_MS))

    def _db(self, block):
        rms = float(np.sqrt(np.mean(np.asarray(block, dtype=np.float64) ** 2)))
        return 20.0 * np.log10(max(rms, 1.0) / 32767.0)

    def _spectrum(self, block):
        x = np.asarray(block, dtype=np.float64) * self._win
        mag = np.abs(np.fft.rfft(x))
        freqs = np.fft.rfftfreq(len(x), 1.0 / self.sr)
        tot = float(mag.sum()) + 1e-9
        cent = float((mag * freqs).sum() / tot)
        mid = float(mag[(freqs >= 300) & (freqs <= 2000)].sum() / tot)
        return cent, mid

    def process(self, block):
        db = self._db(block)
        if isinstance(self._calib, list) and self._calib and \
                self._calib[0] is None:
            self._calib = [db]
        elif isinstance(self._calib, list):
            self._calib.append(db)
            need = max(1, int(3.0 * 1000 / BLOCK_MS))
            if len(self._calib) >= need:
                self.floor_db = float(np.percentile(self._calib, 10))
                self._calib = True
        if self.floor_db is None:
            self.floor_db = db
        elif db <= self.floor_db + 2.0:
            self.floor_db += self.alpha * (db - self.floor_db)
        above = db - self.floor_db
        cent, mid = self._spectrum(block)
        event, conf = False, 0.0
        if self._cooldown > 0:
            self._cooldown -= 1
        if above >= self.thr and self._cooldown == 0:
            if self._active == 0:
                self._peak = db
                self._quiet = 0
                self._burst_mid = mid
                self._burst_cent = cent
            self._active += 1
            self._peak = max(self._peak, db)
            self._burst_mid = max(self._burst_mid, mid)
        elif self._active > 0:
            self._quiet += 1
            if self._quiet >= 2:
                dur_ok = self.min_b <= self._active <= self.max_b
                shape_ok = 200.0 <= self._burst_cent <= 6000.0 and \
                    self._burst_mid >= 0.30
                loud_ok = (self._peak - self.floor_db) >= self.thr + 3.0
                if dur_ok and shape_ok and loud_ok:
                    event = True
                    conf = float(min(0.99, max(0.30,
                               (self._peak - self.floor_db) / 30.0)))
                    self._cooldown = self.refr_b
                self._active = 0
        info = {'db': db, 'floor': self.floor_db, 'above': above,
                'centroid': cent, 'mid_ratio': mid,
                'event': event, 'confidence': conf}
        self.last = info
        return info


def status_for(cough_count):
    if cough_count >= 12:      # matches gateway.py CRITICAL cough rule
        return 'CRITICAL', RED
    if cough_count >= 5:       # matches gateway.py WARNING cough rule
        return 'WARNING', YELLOW
    return 'NORMAL', GREEN


def render_view(wave_hist, spec_hist, info, count, conf, fps):
    import cv2
    W, H = 800, 480
    img = np.zeros((H, W, 3), dtype=np.uint8)
    status, col = status_for(count)
    db = info['db']
    meter_col = GREEN if db < -35 else (YELLOW if db < -22 else RED)
    cv2.putText(img, 'COOP MIC  16kHz mono', (14, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 190, 180), 1)
    cv2.putText(img, f'{db:5.1f} dBFS', (14, 66),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, meter_col, 2)
    cv2.putText(img, f'{status}  cough_10m={count}  conf={conf:.2f}',
                (14, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    # waveform strip (last 3 s)
    x0, y0, wdt, hgt = 14, 130, W - 28, 120
    cv2.rectangle(img, (x0, y0), (x0 + wdt, y0 + hgt), (60, 70, 60), 1)
    if wave_hist:
        data = np.concatenate(list(wave_hist)[-30:]).astype(np.float32)
        data = data / 32768.0
        step = max(1, len(data) // wdt)
        pts = []
        for i in range(0, len(data), step):
            x = x0 + int(i / max(1, len(data)) * wdt)
            y = int(y0 + hgt / 2 - data[i] * (hgt / 2 - 4))
            pts.append((x, max(y0, min(y0 + hgt, y))))
        if len(pts) > 1:
            cv2.polylines(img, [np.array(pts, np.int32)], False, (135, 201, 107), 1)
    # spectrum bars: 16 bins of band energy
    x0, y0, wdt, hgt = 14, 270, W - 28, 130
    cv2.rectangle(img, (x0, y0), (x0 + wdt, y0 + hgt), (60, 70, 60), 1)
    if spec_hist:
        mag = spec_hist[-1]
        n = len(mag)
        bw = wdt / n
        for i, v in enumerate(mag):
            bh = int(min(1.0, v) * hgt)
            c = RED if v > 0.75 else (YELLOW if v > 0.45 else GREEN)
            bx = int(x0 + i * bw)
            cv2.rectangle(img, (bx, y0 + hgt - bh),
                          (int(bx + bw - 1), y0 + hgt), c, -1)
    cv2.putText(img, f'cent={info["centroid"]:.0f}Hz mid={info["mid_ratio"]:.2f} '
                     f'floor={info["floor"]:.1f}dB fps~{fps:.0f}  '
                     '[q]uit [c]al [s]ave [r]eset',
                (14, 430), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 160, 150), 1)
    return img


class MicSource:
    """sounddevice reader thread feeding 100 ms int16 mono blocks via queue."""
    def __init__(self, sr, device):
        self.sr = sr
        self.device = device
        self.blocks = deque()
        self.lock = threading.Lock()
        self.running = False

    def start(self):
        import sounddevice as sd
        try:
            info = sd.query_devices(self.device, 'input')
        except Exception as exc:
            raise RuntimeError(f'No input device {self.device!r}: {exc}')
        print(f'[mic] {info["name"]} @ {self.sr} Hz, {info["max_input_channels"]} ch')
        self.stream = sd.InputStream(samplerate=self.sr, channels=1,
                                     dtype='int16', device=self.device,
                                     blocksize=int(self.sr * BLOCK_MS / 1000),
                                     callback=self._cb)
        self.running = True
        self.stream.start()

    def _cb(self, indata, frames, tinfo, status):
        if status:
            print('[mic]', status)
        with self.lock:
            self.blocks.append(np.array(indata[:, 0], dtype=np.int16))

    def read(self):
        with self.lock:
            return self.blocks.popleft() if self.blocks else None

    def stop(self):
        self.running = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


def band_hist(block, sr=SR_DEFAULT, bins=16):
    mag = np.abs(np.fft.rfft(np.asarray(block, dtype=np.float64)))
    edges = np.linspace(0, len(mag), bins + 1, dtype=int)
    out = np.array([mag[edges[i]:edges[i + 1]].mean() for i in range(bins)])
    m = out.max()
    return (out / m) if m > 0 else out


def main():
    ap = argparse.ArgumentParser(description='Coop mic monitor Phase 1')
    ap.add_argument('--device', default=None, help='sounddevice index/name')
    ap.add_argument('--sr', type=int, default=SR_DEFAULT)
    ap.add_argument('--thr', type=float, default=12.0, help='dB above floor')
    ap.add_argument('--calibrate', action='store_true', help='3 s ambient cal')
    ap.add_argument('--no-show', action='store_true')
    ap.add_argument('--save-csv', default='audio/event_logs.csv')
    ap.add_argument('--snapshot-dir', default='audio/snapshots')
    a = ap.parse_args()
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        print('[err] sounddevice missing. Run:')
        print('  .\\.venv\\Scripts\\python.exe -m pip install -r audio\\requirements-audio.txt')
        return
    det = CoughDetector(sr=a.sr, thr_db=a.thr)
    if a.calibrate or True:
        det.calibrate(3.0)
        print('[run] calibrating noise floor 3 s — stay quiet...')
    counter = RollingCounter()
    clips = ClipBuffer(a.sr, int(a.sr * BLOCK_MS / 1000))
    csv_f = open(a.save_csv, 'a', newline='')
    wr = csv.writer(csv_f)
    if Path(a.save_csv).stat().st_size == 0:
        wr.writerow(['ts', 'db', 'floor_db', 'above_db', 'centroid_hz',
                     'mid_ratio', 'event', 'conf', 'cough_count_10m'])
    src = MicSource(a.sr, a.device)
    try:
        src.start()
    except RuntimeError as exc:
        print(f'[err] {exc}')
        print('[err] list mics: .\\.venv\\Scripts\\python.exe -m sounddevice')
        return
    wave_hist, spec_hist = deque(maxlen=60), deque(maxlen=60)
    conf, fps, frames, t0 = 0.0, 0.0, 0, time.time()
    snap_n = 0
    print('[run] live. Clap or cough -> event + CSV row. Keys q/c/s/r.')
    try:
        while True:
            block = src.read()
            if block is None:
                time.sleep(0.01)
            else:
                clips.push(block)
                info = det.process(block)
                if info['event']:
                    counter.hit()
                    conf = info['confidence']
                    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
                    clips.write(f'{a.snapshot_dir}/event_{stamp}_{conf:.2f}.wav')
                    print(f'[evt] cough-like burst conf={conf:.2f} '
                          f'count_10m={counter.total()}')
                wave_hist.append(block)
                spec_hist.append(band_hist(block, a.sr))
                frames += 1
                if time.time() - t0 >= 1.0:
                    fps = frames / (time.time() - t0)
                    frames, t0 = 0, time.time()
                wr.writerow([datetime.now(timezone.utc).isoformat(),
                             f'{info["db"]:.1f}', f'{info["floor"]:.1f}',
                             f'{info["above"]:.1f}',
                             f'{info["centroid"]:.0f}',
                             f'{info["mid_ratio"]:.2f}',
                             int(info['event']), f'{info["confidence"]:.2f}',
                             counter.total()])
                if frames % 10 == 0:
                    csv_f.flush()
            if not a.no_show:
                import cv2
                out = render_view(wave_hist, spec_hist, det.last,
                                  counter.total(), conf, fps)
                cv2.imshow('Coop Mic P1 - q/c/s/r', out)
                k = cv2.waitKey(10) & 0xFF
                if k == ord('q'):
                    break
                elif k == ord('c'):
                    det.calibrate(3.0)
                    print('[run] recalibrating 3 s — stay quiet...')
                elif k == ord('r'):
                    counter.reset()
                    print('[run] counters reset')
                elif k == ord('s'):
                    snap_n += 1
                    p = f'{a.snapshot_dir}/manual_{snap_n}.wav'
                    clips.write(p)
                    print(f'[run] saved {p}')
    except KeyboardInterrupt:
        pass
    finally:
        src.stop()
        csv_f.close()
        try:
            import cv2
            cv2.destroyAllWindows()
        except Exception:
            pass
    print(f'[done] log -> {a.save_csv} | snapshots -> {a.snapshot_dir}/')


if __name__ == '__main__':
    main()




