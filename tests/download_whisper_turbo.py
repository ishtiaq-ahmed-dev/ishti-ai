"""Fetch the CTranslate2 build of whisper large-v3-turbo (~1.6 GB) into D:\\ISHTI\\data\\models\\whisper-large-v3-turbo.

Why this exact repo: faster-whisper only downloads models by *name* from its own list, and the model it maps
"large-v3-turbo" to (mobiuslabsgmbh/faster-whisper-large-v3-turbo) is a different checkpoint. deepdml's repo is
the same 4-decoder-layer turbo checkpoint converted to CTranslate2, which is what WhisperModel() loads directly
from a local directory. Idempotent: already-complete files are not re-downloaded.

Run:  D:\\ISHTI\\venv\\Scripts\\python.exe D:\\ISHTI\\tests\\download_whisper_turbo.py
"""
import os
import sys

MODEL_DIR = r"D:\ISHTI\data\models\whisper-large-v3-turbo"
REPO = "deepdml/faster-whisper-large-v3-turbo-ct2"
# model.bin is the weights; the rest is the tokenizer/preprocessor the CT2 runtime needs to build a Whisper object.
FILES = ["model.bin", "config.json", "tokenizer.json", "vocabulary.json", "vocabulary.txt", "preprocessor_config.json"]
EXPECTED_MB = {"model.bin": (1500, 1750)}  # sanity band: a truncated download must not look "done"


def main() -> int:
    from huggingface_hub import snapshot_download
    os.makedirs(MODEL_DIR, exist_ok=True)
    print(f"[turbo] downloading {REPO} -> {MODEL_DIR}")
    snapshot_download(REPO, local_dir=MODEL_DIR, allow_patterns=FILES)
    ok = True
    for name in FILES:
        path = os.path.join(MODEL_DIR, name)
        if not os.path.isfile(path):
            print(f"[turbo] MISSING {name}")
            ok = False
            continue
        mb = os.path.getsize(path) / 1e6
        low, high = EXPECTED_MB.get(name, (0, 1e9))
        flag = "ok" if low <= mb <= high else "UNEXPECTED SIZE"
        if flag != "ok":
            ok = False
        print(f"[turbo] {name:24s} {mb:10.2f} MB  {flag}")
    total = sum(os.path.getsize(os.path.join(MODEL_DIR, f)) for f in FILES if os.path.isfile(os.path.join(MODEL_DIR, f)))
    print(f"[turbo] total {total / 1e6:.1f} MB in {MODEL_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
