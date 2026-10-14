#!/usr/bin/env python3
"""Coop camera tracker — Phase 1: detection + stable ID (laptop first, ESP32-CAM later).

Tested with your head as chicken proxy. No training needed yet.

Run:
  pip install -r vision/requirements-vision.txt
  python vision/camera_tracker.py --source 0 --detector auto --show-trails
  python vision/camera_tracker.py --source http://192.168.1.100:81/stream  (ESP32-CAM later)

Keys: [q] quit  [t] toggle trails  [s] snapshot track crops  [r] reset IDs
"""
import argparse, csv, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import cv2
import numpy as np

COLORS = [(135,201,107),(231,184,92),(232,117,97),(124,185,215),(186,135,215)]

class Track:
    def __init__(self, tid, cx, cy, w, h, label):
        self.id = tid; self.cx=cx; self.cy=cy; self.w=w; self.h=h
        self.label=label; self.age=1; self.lost=0
        self.trail=deque(maxlen=60); self.trail.append((int(cx),int(cy)))
        self.vx=0.0; self.vy=0.0; self.dwell_s=0.0
        self.first_seen=time.time(); self.conf=0.0

class CentroidTracker:
    """Lightweight ByteTrack-style linker: IoU + distance, keeps lost tracks."""
    def __init__(self, max_lost=30, max_dist=120):
        self.tracks={}; self.next_id=1; self.max_lost=max_lost; self.max_dist=max_dist
    def update(self, dets):
        # dets: list of (x1,y1,x2,y2,label,conf)
        used=set(); tid_used=set()
        for d in sorted(dets,key=lambda d:-d[5]):
            x1,y1,x2,y2,label,conf=d
            cx,cy=(x1+x2)/2,(y1+y2)/2
            best=None; best_d=self.max_dist
            for tid,t in self.tracks.items():
                if tid in tid_used: continue
                dist=np.hypot(cx-t.cx,cy-t.cy)
                if dist<best_d: best_d=dist; best=tid
            if best is None:
                t=Track(self.next_id,cx,cy,x2-x1,y2-y1,label); t.conf=conf
                self.tracks[self.next_id]=t; self.next_id+=1
            else:
                t=self.tracks[best]; tid_used.add(best)
                t.vx=cx-t.cx; t.vy=cy-t.cy
                t.cx,t.cy=cx,cy; t.w=x2-x1; t.h=y2-y1
                t.label=label; t.conf=conf; t.age+=1; t.lost=0
                t.trail.append((int(cx),int(cy))); t.dwell_s=time.time()-t.first_seen
            used.add(id(d))
        for tid in list(self.tracks):
            if tid not in tid_used:
                self.tracks[tid].lost+=1
                if self.tracks[tid].lost>self.max_lost: del self.tracks[tid]
        return list(self.tracks.values())

def build_detectors(mode):
    face = cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
    hog = cv2.HOGDescriptor(); hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    yolo = None
    if mode in ('auto','yolo'):
        try:
            from ultralytics import YOLO
            yolo = YOLO('yolov8n.pt')  # auto-downloads ~6MB on first run
            print('[det] YOLOv8n loaded.')
        except Exception as e:
            print(f'[det] YOLO not available ({e}), falling back to face+HOG.')
            if mode=='yolo': print('[det] tip: pip install ultralytics')
    return face, hog, yolo

def detect(frame, face, hog, yolo, mode, conf_thr, target):
    H,W = frame.shape[:2]; out=[]
    if yolo is not None and mode in ('auto','yolo'):
        try:
            r = yolo.predict(frame, verbose=False, conf=conf_thr)[0]
            for b in r.boxes:
                cls = r.names[int(b.cls[0])]; c=float(b.conf[0])
                if target!='all' and cls!=target: continue
                x1,y1,x2,y2=map(int,b.xyxy[0].tolist())
                out.append((x1,y1,x2,y2,cls,c))
            if out: return out
        except Exception as e: print('[det] yolo fail:',e)
    if mode in ('auto','face','head') or target in ('head','person','all'):
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        for (x,y,w,h) in face.detectMultiScale(gray,1.2,5,minSize=(60,60)):
            out.append((x,y,x+w,y+h,'head',0.85))
        if target in ('person','all'):
            try:
                rects,_ = hog.detectMultiScale(frame,winStride=(8,8),padding=(8,8),scale=1.05)
                for (x,y,w,h) in rects: out.append((x,y,x+w,y+h,'person',0.6))
            except Exception: pass
    return out

def draw(frame, tracks, show_trails, fps, detector_name):
    for t in tracks:
        x1,y1=int(t.cx-t.w/2),int(t.cy-t.h/2); x2,y2=int(t.cx+t.w/2),int(t.cy+t.h/2)
        col=COLORS[t.id%len(COLORS)]
        speed=np.hypot(t.vx,t.vy)
        # abnormality placeholder for Phase 2: stillness + aspect ratio
        still = 'STILL' if speed<2.0 and t.age>60 else 'MOVING'
        cv2.rectangle(frame,(x1,y1),(x2,y2),col,2)
        cv2.putText(frame,f'ID{t.id} {t.label} {t.conf:.2f}',(x1,y1-8),
                    cv2.FONT_HERSHEY_SIMPLEX,0.6,col,2)
        cv2.putText(frame,f'{still} v={speed:.1f}px/f dwell={t.dwell_s:.0f}s',(x1,y2+18),
                    cv2.FONT_HERSHEY_SIMPLEX,0.5,(237,243,237),1)
        if show_trails and len(t.trail)>1:
            pts=[(int(x),int(y)) for (x,y) in t.trail]
            for i in range(1,len(pts)): cv2.line(frame,pts[i-1],pts[i],col,2)
    cv2.putText(frame,f'{detector_name} | {len(tracks)} IDs | {fps:.1f} FPS  [q]uit [t]rails [s]nap [r]eset',
                (10,28),cv2.FONT_HERSHEY_SIMPLEX,0.6,(237,243,237),2)
    return frame


def main():
    ap = argparse.ArgumentParser(description='Coop camera tracker Phase 1')
    ap.add_argument('--source', default='0', help='0 for webcam or ESP32-CAM http://IP:81/stream or video file')
    ap.add_argument('--detector', default='auto', choices=['auto','yolo','face','head'])
    ap.add_argument('--target', default='all', help='yolo class filter: person, bird, cat, dog, all (use person for head test)')
    ap.add_argument('--conf', type=float, default=0.35)
    ap.add_argument('--max-lost', type=int, default=30, help='frames to keep ID after occlusion/disappear')
    ap.add_argument('--max-dist', type=float, default=120, help='px linking distance')
    ap.add_argument('--no-show', action='store_true')
    ap.add_argument('--save-csv', default='vision/track_logs.csv')
    ap.add_argument('--snapshot-dir', default='vision/snapshots')
    ap.add_argument('--show-trails', action='store_true')
    a = ap.parse_args()
    try: src = int(a.source)
    except ValueError: src = a.source
    face, hog, yolo = build_detectors(a.detector)
    det_name = 'YOLOv8n+ByteTrack-lite' if yolo else 'Face+HOG+Centroid'
    tracker = CentroidTracker(a.max_lost, a.max_dist)
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(f'[err] cannot open source {a.source}. Try --source 0 or 1.'); return
    Path(a.snapshot_dir).mkdir(parents=True, exist_ok=True)
    csv_f = open(a.save_csv,'w',newline=''); wr = csv.writer(csv_f)
    wr.writerow(['timestamp','track_id','label','conf','cx','cy','w','h','speed_px_f','dwell_s'])
    show_trails = a.show_trails; fps=0; t0=time.time(); frames=0; id_switches=0; prev_ids=set()
    print(f'[run] {det_name} | source={a.source} | target={a.target}')
    print('[run] HEAD TEST: move L/R, cover face 1s, leave+re-enter. ID should persist ~1s (max-lost).')
    while True:
        ok, frame = cap.read()
        if not ok: print('[err] frame grab failed'); break
        dets = detect(frame, face, hog, yolo, a.detector, a.conf, a.target)
        tracks = tracker.update(dets)
        ids = {t.id for t in tracks}
        if prev_ids and ids and len(ids - prev_ids) > 0 and len(prev_ids - ids) > 0: id_switches += 1
        prev_ids = ids
        frames += 1
        if time.time()-t0 >= 1.0: fps = frames/(time.time()-t0); frames=0; t0=time.time()
        ts = datetime.now(timezone.utc).isoformat()
        for t in tracks:
            wr.writerow([ts,t.id,t.label,f'{t.conf:.2f}',int(t.cx),int(t.cy),int(t.w),int(t.h),
                         f'{np.hypot(t.vx,t.vy):.1f}',f'{t.dwell_s:.1f}'])
        if not a.no_show:
            out = draw(frame, tracks, show_trails, fps, det_name)
            cv2.imshow('Coop Tracker P1 - q/t/s/r', out)
            k = cv2.waitKey(1) & 0xFF
            if k == ord('q'): break
            elif k == ord('t'): show_trails = not show_trails
            elif k == ord('r'): tracker = CentroidTracker(a.max_lost, a.max_dist); print('[run] IDs reset')
            elif k == ord('s'):
                for t in tracks:
                    x1,y1=max(0,int(t.cx-t.w/2)),max(0,int(t.cy-t.h/2))
                    x2,y2=min(frame.shape[1],int(t.cx+t.w/2)),min(frame.shape[0],int(t.cy+t.h/2))
                    cv2.imwrite(f'{a.snapshot_dir}/id{t.id}_{int(time.time())}.jpg', frame[y1:y2,x1:x2])
                print(f'[run] saved {len(tracks)} crops -> {a.snapshot_dir}/')
    cap.release(); cv2.destroyAllWindows(); csv_f.close()
    print(f'[done] log -> {a.save_csv} | approx ID-switch events: {id_switches}')

if __name__ == '__main__':
    main()
