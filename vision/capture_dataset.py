#!/usr/bin/env python3
"""Capture healthy/sick training crops from live tracks for Phase 2.
Press H = save focused track as healthy, S = sick, Q = quit. Uses camera_tracker core.
"""
import argparse, time, sys
from pathlib import Path
import cv2
sys.path.insert(0, str(Path(__file__).resolve().parent))
from camera_tracker import CentroidTracker, build_detectors, detect, draw

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default='0'); ap.add_argument('--detector', default='auto')
    ap.add_argument('--target', default='all'); ap.add_argument('--conf', type=float, default=0.35)
    ap.add_argument('--out', default='vision/dataset')
    a = ap.parse_args()
    try: src = int(a.source)
    except ValueError: src = a.source
    face, hog, yolo = build_detectors(a.detector)
    tr = CentroidTracker()
    cap = cv2.VideoCapture(src)
    out = Path(a.out); (out/'healthy').mkdir(parents=True, exist_ok=True); (out/'sick').mkdir(parents=True, exist_ok=True)
    print('[cap] H=healthy S=sick Q=quit. Aim for 200+ per class, varied light/angle.')
    counts = {'healthy': len(list((out/'healthy').glob('*.jpg'))), 'sick': len(list((out/'sick').glob('*.jpg')))}
    while True:
        ok, frame = cap.read()
        if not ok: break
        tracks = tr.update(detect(frame, face, hog, yolo, a.detector, a.conf, a.target))
        disp = draw(frame, tracks, True, 0, f"CAPTURE H:{counts['healthy']} S:{counts['sick']}")
        cv2.imshow('Capture - H/S/Q', disp)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord('q'), 27): break
        if k in (ord('h'), ord('s')) and tracks:
            label = 'healthy' if k == ord('h') else 'sick'
            t = max(tracks, key=lambda t: t.w*t.h)  # biggest = closest subject
            x1,y1=max(0,int(t.cx-t.w/2)),max(0,int(t.cy-t.h/2))
            x2,y2=min(frame.shape[1],int(t.cx+t.w/2)),min(frame.shape[0],int(t.cy+t.h/2))
            p = out/label/f'{label}_{int(time.time()*1000)}_id{t.id}.jpg'
            cv2.imwrite(str(p), frame[y1:y2, x1:x2]); counts[label]+=1
            print(f'[cap] {label} -> {p} ({counts[label]})')
    cap.release(); cv2.destroyAllWindows()
    print(f'[done] healthy={counts["healthy"]} sick={counts["sick"]} in {a.out}/')

if __name__ == '__main__':
    main()
