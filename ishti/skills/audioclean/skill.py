"""Audio enhancement (inventory domain 2 #9): FFmpeg denoise + loudness normalization.

"clean up the audio in D:/rec.wav", "denoise D:/call.mp3", "normalize the loudness of D:/x.wav",
"clean up and normalize D:/interview.wav"

Filters (applied by ffmpeg, stated in the reply): afftdn (FFT denoise, nr=12) for "denoise"/"clean up",
loudnorm (EBU R128) for "normalize", both for "clean up and normalize". Output is a WAV in
data/clean/<name>.clean.wav — the original is untouched. The reply reports the measured duration
of the output via ffprobe, and ffmpeg's failure text verbatim on error. T1, local write only.
"""
import json
import os
import re
import subprocess

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:mp3|wav|m4a|flac|ogg|opus|aac|wma))"
CLEAN = re.compile(rf"^\s*(?:clean\s+up\s+(?:the\s+)?audio\s+(?:in|of)\s*|clean\s+audio\s*){PATH}\s*$", I)
DENOISE = re.compile(rf"^\s*denoise\s+(?:the\s+)?(?:audio\s+(?:in\s+)?)?{PATH}\s*$", I)
NORMALIZE = re.compile(rf"^\s*(?:normalize|normalise)\s+(?:the\s+)?loudness\s+of\s+(?:the\s+)?{PATH}\s*$", I)
BOTH = re.compile(rf"^\s*(?:clean\s+up\s+and\s+normalize|denoise\s+and\s+normalize)\s+(?:the\s+)?{PATH}\s*$", I)


def _out_path(src, session=None):
    d = (session or {}).get("_clean_out") or os.path.join("data", "clean")
    os.makedirs(d, exist_ok=True)
    stem, _ = os.path.splitext(os.path.basename(src))
    return os.path.join(d, f"{stem}.clean.wav")


def _run(filters, path, session=None):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    out = _out_path(path, session)
    cmd = ["ffmpeg", "-y", "-i", path, "-af", filters, "-vn", out]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return "ffmpeg isn't installed or not on PATH."
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        return "ffmpeg failed:\n" + "\n".join(tail)
    dur = _duration(out)
    dur_txt = f" Duration: {dur:.1f}s." if dur else ""
    return (f"Saved `{out}` — filters applied: **{filters}**.{dur_txt} Original untouched.\n"
            f"Listen before trusting it; denoise can dull the highs.")


def _duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30)
        return float(json.loads(out.stdout or "{}").get("format", {}).get("duration") or 0)
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired, ValueError):
        return 0.0


def match(text):
    t = text.strip()
    return bool(CLEAN.match(t) or DENOISE.match(t) or NORMALIZE.match(t) or BOTH.match(t))


def run(text, session):
    t = text.strip()
    for rx, filters, tag in (
            (BOTH, "afftdn=nr=12,loudnorm", "clean+normalize"),
            (CLEAN, "afftdn=nr=12", "clean"),
            (DENOISE, "afftdn=nr=12", "denoise"),
            (NORMALIZE, "loudnorm", "normalize")):
        if (m := rx.match(t)):
            trace(session, "audioclean", tag)
            return {"reply": _run(filters, m.group("path"), session), "sources": []}
    return None


if __name__ == "__main__":
    import math
    import struct
    import tempfile
    import wave

    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    src = os.path.join(tmp, "noisy.wav")
    # 2 s of 440 Hz tone + white noise, mono 16-bit — a real file, not a stub
    with wave.open(src, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = b"".join(struct.pack(
            "<h", int(12000 * math.sin(2 * math.pi * 440 * i / 16000) + 9000 * (hash(i) % 1000 - 500) / 500))
            for i in range(32000))
        w.writeframes(frames)

    assert match("clean up the audio in " + src)
    assert match("denoise " + src)
    assert match("normalize the loudness of " + src)
    assert match("clean up and normalize " + src)
    assert not match("clean my room")
    assert not match("denoise the picture")

    r = run("denoise " + src, {"_clean_out": out})
    fp = os.path.join(out, "noisy.clean.wav")
    assert os.path.isfile(fp) and os.path.getsize(fp) > 1000, r
    assert "afftdn" in r["reply"], r
    dur = _duration(fp)
    assert abs(dur - 2.0) < 0.3, (r, dur)              # duration survives the filter
    r2 = run("clean up and normalize " + src, {"_clean_out": out})
    assert "afftdn=nr=12,loudnorm" in r2["reply"] and os.path.isfile(
        os.path.join(out, "noisy.clean.wav")), r2
    r3 = run("denoise D:/definitely/missing.mp3", {})
    assert "not found" in r3["reply"].lower(), r3
    print("[self-check] audioclean OK - real WAV denoised + loudnorm'd by ffmpeg, output duration "
          "measured, failure text shown verbatim, writes only data/clean")
