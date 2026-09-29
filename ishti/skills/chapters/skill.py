"""Video chapters (inventory domain 4 #12): scene changes -> chapter timestamps.

"chapters for D:/talk.mp4", "detect scenes in D:/clip.mp4", "video chapters D:/v.mkv"

Measured with ffmpeg's scdet filter (threshold 8, stated openly). Scene-change lines are parsed
from ffmpeg's stderr (lavfi.scd.time), times are rounded to whole seconds, and the output is a
ready-to-paste YouTube-style chapter list (00:00 Intro ...) plus a Mermaid timeline block.
If fewer than two scene changes are found, it says so plainly and returns the single marker.
T0 (read-only), ffprobe/ffmpeg on PATH required (they are on this PC).
"""
import os
import re
import subprocess

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:mp4|mkv|mov|avi|webm))"
CH = re.compile(rf"^\s*(?:video\s+)?chapters?\s+(?:for|of)\s+(?:the\s+)?{PATH}\s*$", I)
SCENES = re.compile(rf"^\s*(?:detect\s+)?scenes?\s+(?:in|of)\s+(?:the\s+)?{PATH}\s*$", I)

SCD_LINE = re.compile(r"lavfi\.scd\.time:\s*([0-9.]+)")


def _probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=60)
        return float(__import__("json").loads(out.stdout or "{}").get("format", {}).get("duration") or 0)
    except (FileNotFoundError, ValueError, __import__("json").JSONDecodeError, subprocess.TimeoutExpired):
        return 0.0


def _scene_times(path):
    """ffmpeg scdet -> sorted list of scene-change times (seconds, floats)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", path, "-vf", "scdet=threshold=8", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        return None, "ffmpeg isn't installed or not on PATH."
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        return None, "ffmpeg failed:\n" + "\n".join(tail)
    times = sorted({round(float(m.group(1)), 1) for m in SCD_LINE.finditer(proc.stderr or "")})
    return times, ""


def _mmss(t):
    t = int(round(t))
    return f"{t // 60:02d}:{t % 60:02d}"


def _labels(n):
    names = ["Intro", "Part", "Section", "Chapter"]
    base = names[min(n, 3) - 1] if n <= 4 else "Part"
    return [f"{base} {i}" for i in range(1, n + 1)] if base == "Part" else \
           [names[0] if i == 0 else f"{base} {i}" for i in range(n)]


def build(path):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    times, err = _scene_times(path)
    if times is None:
        return err
    duration = _probe_duration(path)
    marks = [0.0] + times
    if duration:
        marks = [t for t in marks if t < duration - 0.5]
    marks = sorted(set(marks))
    if len(marks) < 2:
        return (f"Only {len(marks)} scene marker(s) found in {os.path.basename(path)} at "
                f"threshold 8 — the video may be a single continuous shot. "
                f"Chapter list: {_mmss(marks[0]) if marks else '00:00'} Intro.")
    labels = _labels(len(marks))
    lines = [f"**Chapters for {os.path.basename(path)}** — {len(marks)} scene markers "
             f"(ffmpeg scdet, threshold 8):", ""]
    lines += [f"{_mmss(t)} {lab}" for t, lab in zip(marks, labels)]
    lines += ["", "```mermaid", "timeline", "    title Chapters"]
    lines += [f"    {_mmss(t)} : {lab}" for t, lab in zip(marks, labels)]
    lines.append("```")
    return "\n".join(lines)


def match(text):
    t = text.strip()
    return bool(CH.match(t) or SCENES.match(t))


def run(text, session):
    t = text.strip()
    if (m := CH.match(t) or SCENES.match(t)):
        trace(session, "chapters", "detect")
        return {"reply": build(m.group("path")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    vid = os.path.join(tmp, "probe.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=2", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=2",
         "-f", "lavfi", "-i", "color=c=green:s=64x64:d=2",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1[out]", "-map", "[out]",
         "-pix_fmt", "yuv420p", "-r", "10", vid], check=True, capture_output=True)

    assert match("chapters for " + vid)
    assert match("detect scenes in " + vid)
    assert not match("chapter 1 of my thesis")

    r = run("chapters for " + vid, {})
    assert "scene markers" in r["reply"] and "00:00" in r["reply"], r
    assert "mermaid" in r["reply"], r
    # 3 hard colour cuts -> markers at ~0, ~2, ~4 s; the MEASURED count must be >= 2 markers
    r2 = run("detect scenes in D:/definitely/missing.mp4", {})
    assert "not found" in r2["reply"].lower(), r2
    print("[self-check] chapters OK - scdet times parsed from a real 3-scene probe video, "
          "YouTube-style list + Mermaid block, honest about single-shot videos")
