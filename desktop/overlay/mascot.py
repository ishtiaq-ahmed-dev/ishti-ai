"""ISHTI original mascot + behaviours (WP-COUCOU, matrix COU-10/11; coucou mochi/engine).

Everything here is ORIGINAL: character name "Pebble" (a river-stone), art generated as
SVG from our own shapes, sounds synthesized at runtime as PCM - coucou's Mochi art,
names and 28 WAVs are all-rights-reserved and are NOT imitated (behaviour patterns only).

Behaviours (COU-11):
  - launch greeting (mood "greeting" + synthesized chime)
  - eye tracking: look_at() clamps the gaze to [-1, 1] - pupils never leave the whites
  - squish / annoyed / dizzy / hover-hearts: timed moods that auto-revert to neutral
  - neutral persists; unknown moods are rejected, nothing ever raises

sprite_svg(mascot) renders the current mood+gaze; greeting_wav() synthesizes the chime.
Self-check: python desktop/overlay/mascot.py
"""
import math
import os
import struct
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MOODS = ("neutral", "greeting", "squished", "annoyed", "dizzy", "hearts")
DEFAULT_DURATIONS = {                 # seconds a triggered mood lasts before auto-revert
    "greeting": 2.0, "squished": 0.6, "annoyed": 3.0, "dizzy": 1.2, "hearts": 1.5,
}
BODY = "#2aa198"                      # original palette (teal stone) - ours
BODY_DARK = "#1c7a73"


class Mascot:
    def __init__(self, now=None):
        self._now = now or time.monotonic
        self.mood = "neutral"
        self.gaze = (0.0, 0.0)        # (x, y) in [-1, 1], 0 = looking straight ahead
        self._until = None
        self._hover = False
        self.greeted = False          # the launch greeting fires at most once

    # ---- eyes -------------------------------------------------------------
    def look_at(self, x, y, cx=0.0, cy=0.0) -> tuple:
        """Point the eyes at (x, y) relative to the face centre (cx, cy); clamped to [-1, 1].
        Junk coordinates keep the previous gaze - the overlay never crashes on bad input."""
        try:
            gx = float(x) - float(cx)
            gy = float(y) - float(cy)
            scale = max(abs(gx), abs(gy), 1.0)      # normalise big coordinates to the unit box
            self.gaze = (max(-1.0, min(1.0, gx / scale)),
                         max(-1.0, min(1.0, gy / scale)))
        except (TypeError, ValueError):
            pass
        return self.gaze

    # ---- moods ------------------------------------------------------------
    def set_mood(self, mood, duration_s=None) -> bool:
        if mood not in MOODS:
            return False                       # unknown mood: rejected, never raises
        dur = DEFAULT_DURATIONS.get(mood)
        if mood == "neutral":
            self.mood, self._until = "neutral", None
            return True
        if duration_s is None:
            duration_s = dur if dur is not None else 1.0
        self.mood = mood
        self._until = self._now() + float(duration_s)
        return True

    def tick(self) -> str:
        """Current mood, auto-reverting to neutral when its duration lapses."""
        if self._until is not None and self._now() >= self._until:
            self.mood, self._until = "neutral", None
        return self.mood

    def greet(self) -> bool:
        first = not self.greeted
        self.greeted = True
        return self.set_mood("greeting") and first

    def squish(self) -> bool:
        return self.set_mood("squished")

    def annoyed(self) -> bool:
        return self.set_mood("annoyed")

    def dizzy(self) -> bool:
        return self.set_mood("dizzy")

    def hover(self, on: bool = True) -> bool:
        """Hover-hearts: hearts while the pointer rests on the stone, neutral on leave."""
        self._hover = bool(on)
        if on:
            return self.set_mood("hearts", duration_s=10 ** 9)   # stays until unhover
        if self.mood == "hearts":
            return self.set_mood("neutral")
        return False


# --------------------------------------------------------------------- rendering

def sprite_svg(m: Mascot, size: int = 64) -> str:
    """Original Pebble art as pure SVG: a rounded stone, two eyes tracking the gaze,
    mood decorations. No external assets, nothing imitated."""
    mood = m.tick()
    gx, gy = m.gaze
    w = size
    h = size if mood != "squished" else int(size * 0.72)
    cy = int(size * (0.52 if mood != "squished" else 0.66))
    rx = int(size * 0.42)
    ry = int(size * (0.42 if mood != "squished" else 0.26))
    eye_dx = int(size * 0.15)
    eye_r = int(size * 0.11)
    pup_r = max(2, int(size * 0.05))
    pup_ox = int(pup_r * gx)
    pup_oy = int(pup_r * gy)
    ey = cy - int(size * 0.06)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" data-ishti-mascot="pebble-v1">',
        f'<ellipse cx="{w // 2}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{BODY}" '
        f'stroke="{BODY_DARK}" stroke-width="2"/>',
    ]
    if mood == "dizzy":
        for sx in (-1, 1):
            ex = w // 2 + sx * eye_dx
            spiral = " ".join(
                f"{ex + int(pup_r * math.cos(a))},{ey + int(pup_r * math.sin(a))}"
                for a in [i * 1.05 for i in range(7)])
            parts.append(f'<polyline points="{spiral}" fill="none" stroke="#123" '
                         f'stroke-width="2"/>')
    else:
        for sx in (-1, 1):
            ex = w // 2 + sx * eye_dx
            parts.append(f'<circle cx="{ex}" cy="{ey}" r="{eye_r}" fill="#fff"/>')
            parts.append(f'<circle cx="{ex + pup_ox}" cy="{ey + pup_oy}" r="{pup_r}" fill="#123"/>')
            if mood == "annoyed":
                parts.append(f'<line x1="{ex - eye_r}" y1="{ey - eye_r - 2}" '
                             f'x2="{ex + eye_r}" y2="{ey - eye_r + 3}" stroke="#123" '
                             f'stroke-width="3"/>')
    if mood in ("hearts", "greeting"):
        hx, hy = w // 2, max(4, cy - ry - 8)
        parts.append(f'<path d="M {hx} {hy + 6} C {hx - 8} {hy - 2}, {hx - 4} {hy - 8}, '
                     f'{hx} {hy - 3} C {hx + 4} {hy - 8}, {hx + 8} {hy - 2}, {hx} {hy + 6} Z" '
                     f'fill="#e74c3c"/>')
    # mouth: a small original smile, suppressed when squished/annoyed
    my = cy + int(size * 0.16)
    mw = int(size * 0.12)
    parts.append(f'<path d="M {w // 2 - mw} {my} Q {w // 2} {my + (6 if mood != "annoyed" else -2)} '
                 f'{w // 2 + mw} {my}" fill="none" stroke="#123" stroke-width="2"/>')
    parts.append("</svg>")
    return "".join(parts)


def greeting_wav(duration_s: float = 0.42, rate: int = 22050) -> bytes:
    """Synthesized original two-tone launch chime (no sampled/imitated audio).
    Returns RIFF/WAVE bytes; tiny by construction (< 40 KB)."""
    n = int(rate * duration_s)
    frames = bytearray()
    for i in range(n):
        t = i / rate
        freq = 523.25 if t < duration_s / 2 else 659.25      # C5 -> E5
        env = min(1.0, i / (rate * 0.01)) * max(0.0, 1.0 - t / duration_s)
        sample = int(12000 * env * math.sin(2 * math.pi * freq * t))
        frames += struct.pack("<h", sample)
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


if __name__ == "__main__":
    # self-check: gaze clamping, timed mood reverts, original-only art, wav shape
    clock = [0.0]
    m = Mascot(now=lambda: clock[0])
    assert m.look_at(9999, -9999) == (1.0, -1.0), "gaze clamps to the unit box"
    assert m.look_at(0, 0) == (0.0, 0.0)
    assert m.mood == "neutral"
    assert m.squish() and m.mood == "squished"
    clock[0] += 10
    assert m.tick() == "neutral", "squish auto-reverts"
    assert m.greet() and m.mood == "greeting"
    assert not m.greet(), "the launch greeting fires only once"
    assert m.hover(True) and m.mood == "hearts"
    clock[0] += 10 ** 6
    assert m.tick() == "hearts", "hearts persist while hovered"
    m.hover(False)
    assert m.mood == "neutral", "leaving the stone clears the hearts"
    assert m.set_mood("explode") is False and m.mood == "neutral", "unknown mood rejected"
    assert m.set_mood(None) is False
    svg = sprite_svg(m)
    assert svg.startswith("<svg") and 'data-ishti-mascot="pebble-v1"' in svg, "original art marker"
    low = svg.lower()
    for imitated in ("mochi", "coucou"):
        assert imitated not in low, "no imitation of the all-rights-reserved character"
    m.look_at(1, 0)
    assert sprite_svg(m) != svg, "the eyes must actually track the gaze"
    wav = greeting_wav()
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and len(wav) < 40_000, len(wav)
    print("[self-check] overlay mascot OK - clamped gaze, timed moods, original SVG (no imitation), "
          "synthesized chime")
