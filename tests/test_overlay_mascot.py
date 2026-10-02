"""WP-COUCOU acceptance: originals-only mascot (matrix rows COU-10, COU-11).

T-C10/T-C11, injected clock (no real time, no assets on disk):
  - look_at() clamps the gaze to [-1, 1] and normalises big coordinates; the SVG pupils
    actually move with the gaze
  - timed moods (squished/annoyed/dizzy/ratelimit-free) auto-revert to neutral; hover
    hearts persist while the pointer rests on the stone and clear on leave
  - the launch greeting fires at most once; unknown moods are rejected, never raise
  - sprite_svg() is ORIGINAL, pure inline SVG: pebble-v1 marker, no scripts/links, and no
    imitation of coucou's all-rights-reserved character (name or assets)
  - greeting_wav() is a synthesised, valid RIFF/WAVE chime (mono, 22.05 kHz, < 40 KB)
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_overlay_mascot.py -q
"""
import io
import os
import struct
import subprocess
import sys
import wave

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop.overlay import mascot  # noqa: E402


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _mascot():
    c = Clock()
    return mascot.Mascot(now=c), c


# ------------------------------------------------------------------ eyes

def test_gaze_clamps_to_the_unit_box():
    m, _ = _mascot()
    assert m.look_at(9999, -9999) == (1.0, -1.0), "extreme coordinates clamp"
    assert m.look_at(0, 0) == (0.0, 0.0)
    assert m.look_at(2, 1) == (1.0, 0.5), "big coordinates normalise into the box"
    assert m.look_at(-2, -1) == (-1.0, -0.5)
    # centre offset: relative to the face, not the screen
    assert m.look_at(100, 100, cx=100, cy=60) == (0.0, 1.0)
    assert m.look_at("bad", object()) == m.gaze, "junk coordinates never raise"


def test_pupils_actually_track_the_gaze():
    m, _ = _mascot()
    left = mascot.sprite_svg(m, size=64)
    m.look_at(1, 0)
    right = mascot.sprite_svg(m, size=64)
    m.look_at(0, 1)
    down = mascot.sprite_svg(m, size=64)
    assert left != right != down, "each gaze direction renders differently"


# ------------------------------------------------------------------ moods

def test_timed_moods_auto_revert_to_neutral():
    m, c = _mascot()
    for mood, duration in (("greeting", 2.0), ("squished", 0.6), ("annoyed", 3.0), ("dizzy", 1.2)):
        assert m.set_mood(mood) and m.tick() == mood
        c.t += duration - 0.01
        assert m.tick() == mood, f"{mood} still live just before its duration"
        c.t += 0.02
        assert m.tick() == "neutral", f"{mood} auto-reverts"
    assert m.set_mood("neutral") and m.tick() == "neutral"


def test_hover_hearts_persist_until_unhover():
    m, c = _mascot()
    assert m.hover(True) and m.tick() == "hearts"
    c.t += 10 ** 6
    assert m.tick() == "hearts", "hearts stay while hovered (no timer)"
    assert m.hover(False) is True and m.tick() == "neutral"
    assert m.hover(False) is False, "unhover when not hearted is a no-op"
    # a timed mood interrupted by hover goes to hearts and stays
    m.squish()
    m.hover(True)
    c.t += 60
    assert m.tick() == "hearts"


def test_greeting_fires_at_most_once():
    m, _ = _mascot()
    assert m.greet() is True and m.greeted and m.mood == "greeting"
    assert m.greet() is False, "the launch greeting never repeats"


def test_unknown_moods_are_rejected_never_raise():
    m, _ = _mascot()
    for bad in ("explode", None, 42, "", "neutral "):
        assert m.set_mood(bad) is False, repr(bad)
    assert m.mood == "neutral"


# ------------------------------------------------------------------ original art

def test_sprite_is_original_pure_svg_with_marker():
    m, _ = _mascot()
    for mood in mascot.MOODS:
        m.set_mood(mood)
        svg = mascot.sprite_svg(m, size=64)
        assert svg.startswith("<svg") and svg.endswith("</svg>"), mood
        assert 'data-ishti-mascot="pebble-v1"' in svg, mood
        low = svg.lower()
        for forbidden in ("mochi", "coucou", "<script", "href=", "<image"):
            assert forbidden not in low, (mood, forbidden)
    m.set_mood("squished")
    assert 'height="46"' in mascot.sprite_svg(m, size=64), "squished is visibly shorter"


def test_chime_is_a_valid_synthesised_wav():
    wav = mascot.greeting_wav()
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert len(wav) < 40_000, len(wav)
    with wave.open(io.BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1 and wf.getsampwidth() == 2
        assert wf.getframerate() == 22050
        frames = wf.readframes(wf.getnframes())
    samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
    assert any(s != 0 for s in samples), "the chime is silence"
    assert max(abs(s) for s in samples) <= 12000 + 1, "headroom kept"


# ------------------------------------------------------------------ self-check

def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "desktop", "overlay", "mascot.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=120, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] overlay mascot OK" in r.stdout
