#!/usr/bin/env python3
"""Train a lightweight local classifier for line-audio anomaly segments.

The detector still uses rules to generate candidate segments. This script trains
a small distance-based model that can confirm candidates using local samples.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server import line_audio as la  # noqa: E402


SAMPLE_ROOT = ROOT_DIR / "models" / "line_audio_noise_samples"
MODEL_PATH = ROOT_DIR / "models" / "line_audio_noise_classifier.json"
DB_PATH = ROOT_DIR / "data" / "novels.db"


def convert_to_wav(audio_path: Path) -> Path | None:
    if not audio_path.exists() or not audio_path.is_file():
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
        )
        return tmp_path
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return None


def feature_vector_for_clip(audio_path: Path) -> list[float] | None:
    wav_path = convert_to_wav(audio_path)
    if wav_path is None:
        return None
    try:
        _, frames = la._read_wav_rms_frames(wav_path)
        if not frames:
            return None
        features = la._line_audio_noise_feature_dict(frames)
        return la._line_audio_noise_feature_vector(features)
    finally:
        try:
            wav_path.unlink()
        except OSError:
            pass


def list_sample_clips(label: str) -> list[Path]:
    sample_dir = SAMPLE_ROOT / label
    if not sample_dir.exists():
        return []
    return sorted(
        path
        for path in sample_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".flac", ".wav", ".mp3", ".m4a", ".ogg"}
    )


def extract_normal_samples_from_db(count: int, *, seconds: float = 2.0) -> int:
    if count <= 0 or not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, downloaded_file_path, duration_seconds
            FROM line_audio_tasks
            WHERE status='completed'
              AND downloaded_file_path IS NOT NULL
              AND downloaded_file_path != ''
              AND duration_seconds >= ?
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (max(2.5, seconds + 0.5), int(count * 3)),
        ).fetchall()
    finally:
        conn.close()

    output_dir = SAMPLE_ROOT / "normal"
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for row in rows:
        if written >= count:
            break
        source_path = (ROOT_DIR / str(row["downloaded_file_path"] or "")).resolve()
        if not source_path.exists() or not source_path.is_file():
            continue
        duration = float(row["duration_seconds"] or 0.0)
        if duration < seconds + 0.3:
            continue
        start = random.uniform(0.0, max(0.0, duration - seconds))
        target_path = output_dir / f"task-{int(row['id'])}-normal-{int(time.time() * 1000)}-{written:03d}.flac"
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{start:.3f}",
                    "-t",
                    f"{seconds:.3f}",
                    "-i",
                    str(source_path),
                    "-c:a",
                    "flac",
                    str(target_path),
                ],
                check=True,
                capture_output=True,
            )
            if target_path.exists() and target_path.stat().st_size > 0:
                written += 1
        except Exception:
            try:
                if target_path.exists():
                    target_path.unlink()
            except OSError:
                pass
    return written


def collect_vectors(label: str) -> list[list[float]]:
    vectors: list[list[float]] = []
    for path in list_sample_clips(label):
        vector = feature_vector_for_clip(path)
        if vector is not None:
            vectors.append(vector)
    return vectors


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def class_stats(vectors: list[list[float]]) -> dict:
    columns = list(zip(*vectors)) if vectors else []
    means = [mean(list(column)) for column in columns]
    stds = []
    for column, column_mean in zip(columns, means):
        variance = sum((value - column_mean) ** 2 for value in column) / max(1, len(column))
        stds.append(max(1e-6, variance ** 0.5))
    return {"count": len(vectors), "mean": means, "std": stds}


def main() -> int:
    parser = argparse.ArgumentParser(description="Train line-audio anomaly classifier")
    parser.add_argument("--normal-from-db", type=int, default=0, help="extract this many random normal clips from novels.db")
    parser.add_argument("--normal-seconds", type=float, default=2.0, help="normal sample clip length")
    parser.add_argument("--threshold", type=float, default=0.58, help="abnormal probability threshold")
    parser.add_argument(
        "--include-manual-abnormal",
        action="store_true",
        help="merge manually deleted unknown-type clips into the abnormal class",
    )
    args = parser.parse_args()

    extracted = extract_normal_samples_from_db(args.normal_from_db, seconds=args.normal_seconds)
    if extracted:
        print(f"extracted normal samples: {extracted}")

    normal_vectors = collect_vectors("normal")
    false_positive_vectors = collect_vectors("false_positive_normal")
    normal_vectors.extend(false_positive_vectors)
    if false_positive_vectors:
        print(f"included false_positive_normal samples: {len(false_positive_vectors)}")
    abnormal_vectors = collect_vectors("abnormal")
    if args.include_manual_abnormal:
        manual_vectors = collect_vectors("manual_abnormal")
        abnormal_vectors.extend(manual_vectors)
        print(f"included manual_abnormal samples: {len(manual_vectors)}")
    if len(normal_vectors) < 20 or len(abnormal_vectors) < 10:
        print(
            "not enough samples: "
            f"normal={len(normal_vectors)} abnormal={len(abnormal_vectors)}; "
            "need at least normal>=20 and abnormal>=10",
            file=sys.stderr,
        )
        return 1

    model = {
        "version": f"line-audio-noise-v{int(time.time())}",
        "type": "distance_centroid_v1",
        "featureKeys": la.LINE_AUDIO_NOISE_FEATURE_KEYS,
        "threshold": max(0.05, min(0.95, float(args.threshold))),
        "normal": class_stats(normal_vectors),
        "abnormal": class_stats(abnormal_vectors),
        "createdAt": int(time.time()),
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {MODEL_PATH}")
    print(f"normal={len(normal_vectors)} abnormal={len(abnormal_vectors)} threshold={model['threshold']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
