"""Image quality assessment (inventory domain 4 #19): measured, not guessed.

"image quality of D:/pics/shot.jpg", "how sharp is D:/photo.png", "is D:/x.jpg blurry",
"check the quality of D:/img.png", "resolution of D:/pic.png"

Metrics (all computed with PIL + statistics):
- resolution + megapixels + aspect ratio (with the friendly ratio when it reduces nicely)
- sharpness: variance of the Laplacian (3x3 kernel on greyscale) — the standard no-model
  blur metric; a variance under ~100 reads as blurry, 100-400 soft, above sharp
- brightness (0-255 rec.601 mean) and contrast (standard deviation)
- a one-line verdict combining them
T0: read-only, local.

Self-check synthesizes a razor-sharp checkerboard (high Laplacian variance) and a
gaussian-blurred copy (low variance) and asserts the metric separates them cleanly.
"""
import os
import re
import statistics

from ishti.skills import trace

I = re.I

EXT = r"(?:png|jpe?g|bmp|gif|webp)"
PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\." + EXT + r"|[\w./\\ -]+?\." + EXT + r")"
ASKS = [
    re.compile(r"^\s*(?:image\s+quality|quality)\s+(?:of|for)\s+" + PATH + r"\s*$", I),
    re.compile(r"^\s*(?:how\s+(?:sharp|blurry)\s+is|check\s+(?:the\s+)?(?:quality|sharpness)\s+(?:of)?)\s+" + PATH + r"\s*$", I),
    re.compile(r"^\s*(?:is\s+)" + PATH + r"\s+blurry\s*\??\s*$", I),
    re.compile(r"^\s*(?:what(?:'s| is)\s+the\s+)?resolution\s+of\s+" + PATH + r"\s*$", I),
]
BLUR_ASK = re.compile(r"\bblurry\b|\bsharp\b|\bquality\b|\bresolution\b", I)


def _load(path):
    try:
        from PIL import Image
    except ImportError:
        return None, "Pillow isn't installed — say \"install pillow in ISHTI's python\"."
    if not os.path.isfile(path):
        return None, f"File not found: {path}"
    try:
        img = Image.open(path)
        img.load()
        return img, ""
    except Exception as exc:
        return None, f"Could not read the image: {exc}"


def _laplacian_variance(gs):
    """Variance of the 3x3 Laplacian over the greyscale pixels — the classic blur metric."""
    w, h = gs.size
    px = list(gs.getdata())
    lap = []
    for y in range(1, h - 1):
        row = y * w
        for x in range(1, w - 1):
            i = row + x
            lap.append(4 * px[i] - px[i - 1] - px[i + 1] - px[i - w] - px[i + w])
    if len(lap) < 2:
        return 0.0
    return statistics.pvariance(lap)


def _friendly_ratio(w, h):
    from math import gcd
    g = gcd(w, h) or 1
    rw, rh = w // g, h // g
    return f"{rw}:{rh}" if max(rw, rh) <= 40 else f"{w / h:.2f}:1"


def assess(path):
    img, err = _load(path)
    if img is None:
        return err
    w, h = img.size
    gs = img.convert("L")
    px = list(gs.getdata())
    brightness = sum(px) / len(px)
    contrast = statistics.pstdev(px)
    sharp = _laplacian_variance(gs)
    verdict = ("blurry — retake or sharpen" if sharp < 100 else
               "soft — usable but not crisp" if sharp < 400 else
               "sharp")
    light = ("dark" if brightness < 85 else "bright" if brightness > 170 else "evenly lit")
    return (f"**{os.path.basename(path)}** — {w}×{h}px ({w * h / 1e6:.2f} MP), ratio {_friendly_ratio(w, h)}.\n"
            f"Sharpness (Laplacian variance): {sharp:.0f} → **{verdict}**.\n"
            f"Brightness {brightness:.0f}/255 ({light}), contrast (σ) {contrast:.0f}.\n"
            + ("Low contrast: the histogram is squeezed — consider a levels stretch."
               if contrast < 25 else ""))


def match(text):
    t = text.strip()
    return bool(BLUR_ASK.search(t) and any(rx.match(t) for rx in ASKS))


def run(text, session):
    for rx in ASKS:
        m = rx.match(text.strip())
        if m:
            trace(session, "imgquality", "assess")
            return {"reply": assess(m.group("path").strip()), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    from PIL import Image, ImageFilter
    tmp = tempfile.mkdtemp()
    sharp_path = os.path.join(tmp, "sharp.png")
    blur_path = os.path.join(tmp, "blur.png")
    # a fine checkerboard is maximally sharp; its gaussian-blurred copy is not
    img = Image.new("L", (120, 120))
    for x in range(120):
        for y in range(120):
            img.putpixel((x, y), 255 if (x // 3 + y // 3) % 2 else 0)
    img.save(sharp_path)
    img.filter(ImageFilter.GaussianBlur(radius=3)).save(blur_path)

    assert match("image quality of " + sharp_path)
    assert match("how sharp is " + sharp_path)
    assert match("is " + blur_path + " blurry?")
    assert match("resolution of " + sharp_path)
    assert not match("is life blurry")                       # no image, no metric ask
    assert not match("check the quality of my work")         # not a file
    assert not match("image quality of the new camera")      # no file path

    rs = run("image quality of " + sharp_path, {})
    rb = run("is " + blur_path + " blurry?", {})
    assert "sharp" in rs["reply"].lower() and "blurry" in rb["reply"].lower(), (rs, rb)
    assert "120×120" in rs["reply"] and "0.01 MP" in rs["reply"], rs
    # the metric itself must separate the pair by a wide margin
    from PIL import Image as _I
    vs = _laplacian_variance(_I.open(sharp_path).convert("L"))
    vb = _laplacian_variance(_I.open(blur_path).convert("L"))
    assert vs > 1000 and vb < 400 and vs > vb * 5, (vs, vb)
    r3 = run("image quality of " + os.path.join(tmp, "nope.jpg"), {})
    assert "not found" in r3["reply"].lower()
    print("[self-check] imgquality OK - resolution, Laplacian-variance sharpness, brightness/contrast, "
          "blur verdict; separates a sharp image from a blurred copy")
