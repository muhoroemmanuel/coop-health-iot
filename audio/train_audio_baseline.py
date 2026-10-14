#!/usr/bin/env python3
"""Track A baseline: MFCC + RandomForest cough-vs-background classifier.

Trains on audio/dataset/{cough,background}/*.wav (16 kHz). Splits by SESSION
(one recording session per fold) to block augmentation-leakage optimism.
Exports model + scaler + metrics to audio/models/mfcc_rf_v1/.

Run:
  .\\.venv\\Scripts\\python.exe audio\\train_audio_baseline.py
  .\\.venv\\Scripts\\python.exe audio\\train_audio_baseline.py --dataset my_clips --augment 3
"""
import argparse, json, sys, warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

warnings.filterwarnings('ignore', category=UserWarning)
try:
    import librosa
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import confusion_matrix, recall_score, precision_score
except ImportError as exc:
    print(f'[err] training deps missing ({exc}). Run:')
    print('  .\\.venv\\Scripts\\python.exe -m pip install '
          '-r audio\\requirements-train.txt')
    sys.exit(1)

SR = 16000
CLIP_S = 2.0
N_MFCC = 20


def session_id(path):
    """Group clips by the minute they were captured (one clap-session)."""
    stem = Path(path).stem          # e.g. cough_20261013-121503-123456
    parts = stem.split('_')
    return parts[-1][:13] if len(parts) > 1 else stem


def augment(y, sr, seed):
    rng = np.random.default_rng(seed)
    out = y.copy()
    shift = rng.integers(0, len(y) // 4)
    out = np.roll(out, shift)
    out = out * rng.uniform(0.7, 1.2)
    noise = rng.standard_normal(len(y)) * rng.uniform(0.0, 0.01)
    return (out + noise).astype(np.float32)


def features(y, sr):
    y = librosa.util.fix_length(y, size=int(SR * CLIP_S))
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC)
    d1 = librosa.feature.delta(mfcc)
    stat = np.concatenate([mfcc.mean(1), mfcc.std(1),
                           d1.mean(1), d1.std(1),
                           [float(np.abs(y).mean()),
                            float((y ** 2).mean())]])
    return stat.astype(np.float64)


def load_clips(root, augment_n):
    files, groups, labels = [], [], []
    for label, name in ((1, 'cough'), (0, 'background')):
        for p in sorted((root / name).glob('*.wav')):
            y, sr = librosa.load(str(p), sr=SR, mono=True)
            files.append((y, label, session_id(p)))
            for k in range(augment_n):
                files.append((augment(y, sr, 1000 * label + k),
                              label, session_id(p)))
    return files



def main():
    ap = argparse.ArgumentParser(description='MFCC+RF cough baseline')
    ap.add_argument('--dataset', default='audio/dataset')
    ap.add_argument('--augment', type=int, default=3,
                    help='extra augmented copies per clip')
    ap.add_argument('--out', default='audio/models/mfcc_rf_v1')
    ap.add_argument('--folds', type=int, default=3)
    a = ap.parse_args()
    root = Path(a.dataset)
    clips = load_clips(root, a.augment)
    n_cough = sum(1 for _, lab, _ in clips if lab == 1)
    n_bg = len(clips) - n_cough
    print(f'[train] clips={len(clips)} (cough={n_cough} bg={n_bg}) '
          f'augment x{a.augment}')
    if n_cough < 4 or n_bg < 4:
        print('[err] need >=4 clips per class. Capture with '
              'audio/capture_dataset.py first.')
        return
    print('[train] extracting MFCC features...')
    X = np.stack([features(y, SR) for y, _, _ in clips])
    y = np.array([lab for _, lab, _ in clips])
    groups = np.array([g for _, _, g in clips])
    n_sessions = len(set(groups))
    print(f'[train] feature dim={X.shape[1]} sessions={n_sessions}')
    if n_sessions < a.folds:
        print(f'[warn] only {n_sessions} sessions < {a.folds} folds; '
              'collect more sessions for honest splits.')

    recalls, precs, cms = [], [], np.zeros((2, 2), dtype=int)
    gkf = GroupKFold(n_splits=min(a.folds, n_sessions))
    for fold, (tr, te) in enumerate(gkf.split(X, y, groups)):
        scaler = StandardScaler().fit(X[tr])
        clf = RandomForestClassifier(
            n_estimators=300, min_samples_leaf=2,
            class_weight='balanced', random_state=7, n_jobs=-1)
        clf.fit(scaler.transform(X[tr]), y[tr])
        pred = clf.predict(scaler.transform(X[te]))
        rec = recall_score(y[te], pred, zero_division=0)
        pre = precision_score(y[te], pred, zero_division=0)
        recalls.append(rec)
        precs.append(pre)
        cms += confusion_matrix(y[te], pred, labels=[0, 1])
        print(f'[train] fold {fold + 1}: n_test={len(te)} '
              f'recall={rec:.2f} precision={pre:.2f}')

    rec_m, pre_m = float(np.mean(recalls)), float(np.mean(precs))
    print(f'[train] MEAN recall={rec_m:.2f} (target>=0.85) '
          f'precision={pre_m:.2f}')
    print('[train] confusion (rows=true bg/cough, cols=pred):')
    print(cms)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    scaler = StandardScaler().fit(X)
    clf = RandomForestClassifier(
        n_estimators=300, min_samples_leaf=2, class_weight='balanced',
        random_state=7, n_jobs=-1)
    clf.fit(scaler.transform(X), y)
    import pickle
    with open(out / 'scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)
    with open(out / 'model.pkl', 'wb') as f:
        pickle.dump(clf, f)
    with open(out / 'metrics.json', 'w') as f:
        json.dump({'timestamp': datetime.now(timezone.utc).isoformat(),
                   'n_clips': len(clips), 'n_cough': n_cough, 'n_bg': n_bg,
                   'augment': a.augment, 'feature_dim': X.shape[1],
                   'recall_mean': rec_m, 'precision_mean': pre_m,
                   'confusion': cms.tolist()}, f, indent=2)
    print(f'[done] model+scaler+metrics -> {a.out}/')
    if rec_m < 0.85:
        print('[hint] recall below 0.85: add sessions (esp. hard '
              'backgrounds: fan, feeder), raise --augment, retune --thr.')


if __name__ == '__main__':
    main()
