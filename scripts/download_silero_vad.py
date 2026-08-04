#!/usr/bin/env python3
"""Download the Silero VAD ONNX model used by optional audio quality checks."""

from __future__ import annotations

import urllib.request
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MODEL_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
MODEL_PATH = ROOT_DIR / "models" / "silero_vad.onnx"


def main() -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = MODEL_PATH.with_suffix(".onnx.tmp")
    print(f"Downloading {MODEL_URL}")
    urllib.request.urlretrieve(MODEL_URL, tmp_path)
    tmp_path.replace(MODEL_PATH)
    print(f"Saved {MODEL_PATH}")


if __name__ == "__main__":
    main()
