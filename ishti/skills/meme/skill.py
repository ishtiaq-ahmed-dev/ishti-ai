"""Meme captions (inventory domain 4 #31): text on an image, classic style, computed layout.

"meme D:/cat.png: top text | bottom text"
"caption D:/x.png with when the build passes | everyone is surprised"
"make a meme: one does not simply | skip the self-check"    (grey canvas base)

Text is wrapped to the image width, font size shrinks until the longest line fits, and both
the white fill and the black outline are drawn 8-way offset (the classic look without the
Impact font — DejaVu/Liberation via ImageFont.truetype with graceful fallback). Output:
data/memes/<name>.meme.png. The base image is never modified. T1, local write only.
"""
import os
import re

from ishti.skills import trace

I = re.I

FILE = re.compile(
    r"^\s*(?:meme\s+(?P<path>[A-Za-z]:[\\\\/][^\n|]+?\.(?:png|jpe?g|bmp|gif|webp))\s*[:\-]?:?|"
    r"caption\s+(?P<path2>[A-Za-z]:[\\\\/][^\n|]+?\.(?:png|jpe?g|bmp|gif|webp))\s+with)\s*"
    r"(?P<text>.+)$", I)
CANVAS = re.compile(r"^\s*make\s+an?\s+meme\s*[:\-]\s*(?P<text>.+)$", I)
SPLIT = re.compile(r"\s*\|\s*|\n")


def _out_dir(session=None):
    d = (session or {}).get("_meme_out") or os.path.join("data", "memes")
    os.makedirs(d, exist_ok=True)
    return d


def _font(size):
    from PIL import ImageFont
    for name in ("impact.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(draw, text, width):
    """Largest font size whose wrapped lines all fit the width, plus the wrapped lines."""
    for size in range(min(72, max(16, width // 6)), 11, -2):
        font = _font(size)
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=font) <= width - 16 or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if all(draw.textlength(l, font=font) <= width - 16 for l in lines):
            return font, lines
    return _font(12), [text]


def _draw_block(draw, lines, font, width, y, anchor_bottom=False):
    line_h = (font.size if hasattr(font, "size") else 14) + 6
    total = line_h * len(lines)
    yy = (y - total) if anchor_bottom else y
    for line in lines:
        w = draw.textlength(line, font=font)
        x = max(8, (width - w) // 2)
        for dx in (-2, 0, 2):
            for dy in (-2, 0, 2):
                if dx or dy:
                    draw.text((x + dx, yy + dy), line, font=font, fill=(0, 0, 0))
        draw.text((x, yy), line, font=font, fill=(255, 255, 255))
        yy += line_h


def make(path, text, session=None):
    from PIL import Image, ImageDraw
    top, bottom = (SPLIT.split(text.strip(), 1) + [""])[:2]
    top, bottom = top.strip().upper(), bottom.strip().upper()
    if path:
        if not os.path.isfile(path):
            return f"File not found: {path}"
        try:
            base = Image.open(path).convert("RGB")
        except Exception as exc:
            return f"Could not open the image: {exc}"
        stem = os.path.splitext(os.path.basename(path))[0]
    else:
        base = Image.new("RGB", (800, 500), (96, 96, 96))
        stem = "canvas"
    W, H = base.size
    draw = ImageDraw.Draw(base)
    if top:
        font, lines = _fit(draw, top, W)
        _draw_block(draw, lines, font, W, 10)
    if bottom:
        font, lines = _fit(draw, bottom, W)
        _draw_block(draw, lines, font, W, H - 14, anchor_bottom=True)
    out_dir = _out_dir(session)
    fp = os.path.join(out_dir, f"{stem}.meme.png")
    base.save(fp)
    return (f"Meme saved to `{fp}` — top: “{top or '—'}”, bottom: “{bottom or '—'}”. "
            f"Base image untouched.")


def match(text):
    return bool(FILE.match(text.strip()) or CANVAS.match(text.strip()))


def run(text, session):
    t = text.strip()
    m = FILE.match(t) or CANVAS.match(t)
    if not m:
        return None
    trace(session, "meme", "caption")
    path = (m.groupdict().get("path") or m.groupdict().get("path2") or "")
    return {"reply": make(path, m.group("text"), session), "sources": [path] if path and os.path.isfile(path) else []}


if __name__ == "__main__":
    import tempfile
    from PIL import Image
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    cat = os.path.join(tmp, "cat.png")
    Image.new("RGB", (640, 480), (120, 90, 60)).save(cat)

    assert match("meme " + cat + ": top text | bottom text")
    assert match("caption " + cat + " with when the build passes | everyone is surprised")
    assert match("make a meme: one does not simply | skip the self-check")
    assert not match("meme of the year")               # no file, no pipes
    assert not match("caption this")

    r = run("meme " + cat + ": when the tests pass | first try", {"_meme_out": out})
    fp = os.path.join(out, "cat.meme.png")
    assert os.path.isfile(fp), (r, os.listdir(out))
    assert "top: “WHEN THE TESTS PASS”" in r["reply"], r
    img = Image.open(fp)
    assert img.size == (640, 480), img.size
    r2 = run("make a meme: one does not simply | skip the self-check", {"_meme_out": out})
    assert os.path.isfile(os.path.join(out, "canvas.meme.png")), r2
    r3 = run("meme D:/nope.png: a | b", {})
    assert "not found" in r3["reply"].lower()
    print("[self-check] meme OK - top/bottom wrapped outlined captions, auto font fit, canvas "
          "fallback, writes only data/memes, base untouched")
