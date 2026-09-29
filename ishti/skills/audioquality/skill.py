"""Audio quality report (inventory domain 2 #25): ffprobe facts, plain-language verdict.

"audio quality of D:/rec.mp3", "check this audio D:/voice.wav",
"what are the specs of D:/song.flac", "ffprobe D:/x.m4a"

Everything measured from the container with ffprobe: codec, sample rate, bit rate, channels,
duration. The verdict bands are stated openly (lossless / podcast-grade / voice-note-grade /
very low). T0, read-only; ffprobe must be on PATH (it is on this PC).
Self-check: generates a real WAV with Python's wave module and asserts the measured numbers.
"""
import json
import os
import re
import subprocess
import wave

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:mp3|wav|m4a|flac|ogg|opus|aac|wma)|[\w./\\ -]+?\.(?:mp3|wav|m4a|flac|ogg|opus|aac|wma))"
ASKS = re.compile(
    rf"^\s*(?:audio\s+quality\s+(?:of|for)|check\s+(?:this\s+)?audio\s+(?:file\s+)?|"
    rf"what\s+are\s+the\s+specs\s+of\s*|ffprobe\s*)\s*{PATH}\s*$", I)


def _ffprobe(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout or "{}")
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        return {}


def verdict(rate, bitrate, codec):
    if codec in ("flac", "alac", "pcm_s16le", "pcm_f32le"):
        return "lossless — full fidelity"
    if rate >= 44100 and bitrate >= 192000:
        return "podcast/music-grade"
    if rate >= 22050 and bitrate >= 64000:
        return "voice-note-grade — fine for speech"
    return "very low — expect audible artifacts"


def report(path):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    data = _ffprobe(path)
    if data is None:
        return "ffprobe isn't installed or not on PATH."
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        return "No audio stream found in that file."
    s = streams[0]
    fmt = data.get("format", {})
    rate = int(s.get("sample_rate") or 0)
    br = int(s.get("bit_rate") or fmt.get("bit_rate") or 0)
    dur = float(s.get("duration") or fmt.get("duration") or 0)
    ch = int(s.get("channels") or 1)
    codec = (s.get("codec_name") or "?").lower()
    ch_name = {1: "mono", 2: "stereo"}.get(ch, f"{ch} channels")
    return (f"**{os.path.basename(path)}** — {codec}, {rate:,} Hz, {br // 1000 if br else '?'} kbps, "
            f"{ch_name}, {dur:.1f}s.\nVerdict: **{verdict(rate, br, codec)}** (bands: lossless / "
            f"podcast ≥192k / voice-note ≥64k / below).")


def match(text):
    return bool(ASKS.match(text.strip()))


def run(text, session):
    m = ASKS.match(text.strip())
    if not m:
        return None
    trace(session, "audioquality", "ffprobe")
    return {"reply": report(m.group("path").strip()), "sources": []}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    wav_path = os.path.join(tmp, "tone.wav")
    with wave.open(wav_path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 44100)     # one second of silence

    assert match("audio quality of " + wav_path)
    assert match("check this audio " + wav_path)
    assert match("ffprobe " + wav_path)
    assert not match("audio quality of my singing")
    assert not match("check this audio cable")

    r = run("audio quality of " + wav_path, {})
    assert "44,100 Hz" in r["reply"], r
    assert "mono" in r["reply"] and "1.0s" in r["reply"], r
    assert "lossless" in r["reply"].lower(), r          # pcm wav
    r2 = run("audio quality of D:/nope.mp3", {})
    assert "not found" in r2["reply"].lower()
    print("[self-check] audioquality OK - ffprobe codec/rate/bitrate/channels/duration measured, "
          "verdict bands stated")
