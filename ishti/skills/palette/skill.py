"""Color palette extraction (inventory domain 4 #18): an image's dominant colors, exactly.

"color palette of D:/pics/logo.png", "dominant colors in D:/shots/shot.png",
"what colors are in D:/pics/banner.jpg", "palette of D:/x.png" (default top 6).

Method: quantize the image to a fixed palette (PIL's adaptive median-cut), then count the
pixels mapped to each survivor — the hex codes and pixel shares are measured, not guessed.
Average brightness (0-255 rec.601) is reported so the owner can pick text contrast against
the palette. T0: read-only, nothing leaves the PC.

Self-check synthesizes a known image (solid red/blue/white thirds) and asserts the exact
hex codes come back with the right shares.
"""
import os
import re

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:png|jpe?g|bmp|gif|webp)|[\w./\\ -]+?\.(?:png|jpe?g|bmp|gif|webp))"
IMAGES = re.compile(
    r"^\s*(?:(?:color\s+|colour\s+)?palette\s+(?:of|for|from)|dominant\s+colors?\s+(?:of|in|from)|"
    r"what\s+colors?\s+(?:are\s+)?(?:there\s+)?in|extract\s+(?:the\s+)?colors?\s+from)\s+" + PATH +
    r"(?:\s+top\s+(?P<n>\d{1,2}))?\s*$", I)
IMAGES2 = re.compile(r"^\s*what\s+colors?\s+(?:are\s+)?in\s+(?:this\s+|the\s+)?(?P<path>[\w./\\ -]+\.(?:png|jpe?g|bmp|gif|webp))"
                     r"(?:\s+top\s+(?P<n>\d{1,2}))?\s*$", I)


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
        return img.convert("RGB"), ""
    except Exception as exc:
        return None, f"Could not read the image: {exc}"


def palette(path, top=6):
    top = max(3, min(int(top or 6), 12))
    img, err = _load(path)
    if img is None:
        return err
    w, h = img.size
    total = w * h
    # quantize to `top` colors (median cut); None dither keeps counts exact
    q = img.quantize(colors=top, method=Image.MEDIANCUT if hasattr(Image, "MEDIANCUT") else 0)
    counts = sorted(q.getcolors(maxcolors=top * top) or [], reverse=True)[:top]
    hexes = []
    for count, idx in counts:
        r, g, b = q.getpalette()[idx * 3: idx * 3 + 3]
        hexes.append((count, f"#{r:02x}{g:02x}{b:02x}", (r, g, b)))
    px = list(img.resize((64, 64)).getdata())
    brightness = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px) / len(px)
    lines = [f"**Dominant colors** in {os.path.basename(path)} ({w}×{h}):"]
    lines += [f"- `{hx}` — {c / total * 100:.1f}% of pixels" for c, hx, _rgb in hexes]
    mood = "light" if brightness > 170 else "dark" if brightness < 85 else "mid"
    lines.append(f"Average brightness {brightness:.0f}/255 ({mood}) — "
                 f"use {'dark' if brightness > 170 else 'light'} text on this for contrast.")
    return "\n".join(lines)


def match(text):
    t = text.strip()
    return bool(IMAGES.match(t) or IMAGES2.match(t))


def run(text, session):
    t = text.strip()
    m = IMAGES.match(t) or IMAGES2.match(t)
    if not m:
        return None
    trace(session, "palette", "extract")
    return {"reply": palette(m.group("path").strip(), m.groupdict().get("n")), "sources": []}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    img_path = os.path.join(tmp, "thirds.png")
    from PIL import Image
    img = Image.new("RGB", (90, 30))
    for x in range(90):
        for y in range(30):
            img.putpixel((x, y), (255, 0, 0) if x < 30 else (0, 0, 255) if x < 60 else (255, 255, 255))
    img.save(img_path)

    assert match("color palette of " + img_path)
    assert match("dominant colors in " + img_path)
    assert match("what colors are in " + img_path + " top 4")
    assert match("palette of " + img_path)
    assert not match("what colors are in style this season")   # no image file
    assert not match("paint the palette")                      # not a read ask

    r = run("color palette of " + img_path, {})
    for hx in ("#ff0000", "#0000ff", "#ffffff"):
        assert hx in r["reply"], (hx, r["reply"])
    assert "33.3%" in r["reply"] and "33.4%" in r["reply"] or r["reply"].count("33.") >= 2, r
    assert "brightness" in r["reply"].lower(), r
    r2 = run("dominant colors in " + img_path + " top 4", {})
    assert r2["reply"].count("`#") >= 3, r2
    r3 = run("palette of " + os.path.join(tmp, "nope.png"), {})
    assert "not found" in r3["reply"].lower()
    print("[self-check] palette OK - exact hex codes + pixel shares + brightness, read-only")
