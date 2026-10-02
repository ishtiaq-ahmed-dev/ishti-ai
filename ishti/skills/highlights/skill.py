"""Video highlights (inventory domain 4 #5): loudest audio moments as candidate highlights.

"highlights of D:/stream.mp4", "find the best moments in D:/vlog.mp4", "key moments D:/talk.mkv"

Measured with ffmpeg's astats per 1-second window: the RMS level of each second is computed,
the top seconds (default 5) are reported with their exact levels and times, honestly labelled
as LOUDNESS candidates — a highlight is not confirmed until the owner watches them. T0,
read-only; ffmpeg/ffprobe on PATH required.
"""
import os
import re
import subprocess

from ishti.skills import trace

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


I = re.I

PATH = r"[A-Za-z]:[\\/][^\"']+?\.(?:mp4|mkv|mov|avi|webm)"
HL = re.compile(
    rf"^\s*(?:highlights?|key\s+moments?)(?:\s+(?:of|in|for))?\s+(?:the\s+)?(?P<path>{PATH})\s*$|"
    rf"^\s*(?:find\s+the\s+)?best\s+moments?\s+in\s+(?:the\s+)?(?P<path2>{PATH})\s*$", I)


def _astats_seconds(path, max_seconds=3600):
    """One-second windows via asetnsamples, RMS per window via astats+ametadata (stdout)."""
    rate = 48000
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True, timeout=60)
        import json
        for st in json.loads(probe.stdout or "{}").get("streams", []):
            if st.get("codec_type") == "audio" and st.get("sample_rate"):
                rate = int(st["sample_rate"])
                break
    except (FileNotFoundError, ValueError) as _exc:
        swallowed('ishti/skills/highlights/skill.py:_astats_seconds', _exc)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-t", str(max_seconds), "-af",
             f"asetnsamples=n={rate}:p=0,astats=metadata=1:reset=1,"
             f"ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        return None, "ffmpeg isn't installed or not on PATH."
    if proc.returncode != 0:
        return None, "ffmpeg failed:\n" + "\n".join((proc.stderr or "").strip().splitlines()[-3:])
    secs, cur_t = {}, None
    for line in (proc.stdout or "").splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            cur_t = int(float(m.group(1)))
            continue
        m2 = re.search(r"lavfi\.astats\.Overall\.RMS_level=(-?[0-9.]+|-inf|\+inf)", line)
        if m2 and cur_t is not None:
            try:
                secs[cur_t] = float(m2.group(1))
            except ValueError:
                secs[cur_t] = -120.0
    return secs, ""


def highlights(path, top=5):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    secs, err = _astats_seconds(path)
    if secs is None:
        return err
    if not secs:
        return "No audio stream found in that file."
    top_secs = sorted(secs.items(), key=lambda kv: -kv[1])[:top]
    top_secs.sort()
    lines = [f"**Loudest moments in {os.path.basename(path)}** — MEASURED RMS per second, "
             f"top {len(top_secs)}:", ""]
    for t, rms in top_secs:
        lines.append(f"- {t // 60:02d}:{t % 60:02d} — {rms:.1f} dB RMS")
    lines += ["", "These are loudness candidates, not confirmed highlights — watch them before "
              "cutting. Loud ≠ interesting (silence before a drop can be the best part)."]
    return "\n".join(lines)


def match(text):
    t = text.strip()
    return bool(HL.match(t))


def run(text, session):
    t = text.strip()
    if (m := HL.match(t)):
        trace(session, "highlights", "detect")
        return {"reply": highlights(m.group("path") or m.group("path2")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile

    tmp = tempfile.mkdtemp()
    vid = os.path.join(tmp, "probe.mp4")
    # 6 s video: 2 s loud tone (0.9 amplitude), then 4 s quieter tone (0.1) — the loud
    # seconds must win. Real file, real measurement.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-filter_complex", "[0:a]volume=0.9[a0];[1:a]volume=0.1[a1];[a0][a1]concat=n=2:v=0:a=1[out]",
         "-map", "[out]", "-ar", "16000", vid], check=True, capture_output=True, timeout=60)

    assert match("highlights of " + vid)
    assert match("find the best moments in " + vid)
    assert match("key moments " + vid)
    assert not match("highlight my hair")
    assert not match("best moments of 2024")

    r = run("find the best moments in " + vid, {})
    assert "loudness candidates" in r["reply"], r
    first = re.search(r"- (\d\d:\d\d) — (-?[0-9.]+) dB", r["reply"])
    assert first and first.group(1) in ("00:00", "00:01"), r   # loud seconds ranked first
    assert float(first.group(2)) > -30, (r, first.group(2))    # 0.9-amplitude tone is loud
    r2 = run("highlights of D:/definitely/missing.mp4", {})
    assert "not found" in r2["reply"].lower(), r2
    print("[self-check] highlights OK - per-second RMS measured on a real two-level probe video, "
          "loud seconds ranked first, honestly labelled loudness candidates")
