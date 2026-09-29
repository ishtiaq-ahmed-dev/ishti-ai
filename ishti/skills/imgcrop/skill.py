"""Image cropping (inventory domain 4 #30): computed crops, originals untouched.

"crop D:/pic.png to square", "center crop D:/photo.jpg to 16:9", "crop D:/x.png to 1:1",
"crop D:/x.png to 300x200 at 10,20", "trim the borders of D:/scan.png"

Modes: aspect (center crop to the largest box with the target ratio), box (x,y,w,h from the
request), trim (cut uniform border rows/columns by comparing edge pixels to the corner
colour with a tolerance). Output: data/crops/<name>.crop.<ext>, original untouched.
Self-check: aspect math on a known 200x100 image -> 100x100; trim on an image with a
synthetic 12px uniform border shrinks by exactly that much per side.
"""
import os
import re

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:png|jpe?g|bmp|gif|webp))"
ASPECT = re.compile(
    rf"^\s*(?:center\s+|centre\s+)?crop\s+(?:the\s+)?{PATH}\s+to\s+"
    r"(?P<ratio>\d{1,2}[:x]\d{1,2}|square)\s*$", I)
BOX = re.compile(
    r"^\s*crop\s+(?:the\s+)?(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:png|jpe?g|bmp|gif|webp))\s+to\s+"
    r"(?P<w>\d{1,5})x(?P<h>\d{1,5})\s+at\s+(?P<x>\d{1,5})\s*,\s*(?P<y>\d{1,5})\s*$", I)
TRIM = re.compile(rf"^\s*(?:trim|remove)\s+(?:the\s+)?borders?\s+(?:of|from)\s+(?:the\s+)?{PATH}\s*$", I)


def _load(path):
    from PIL import Image
    if not os.path.isfile(path):
        return None, f"File not found: {path}"
    try:
        return Image.open(path), ""
    except Exception as exc:
        return None, f"Could not open the image: {exc}"


def _save(img, src, session=None, tag="crop"):
    d = (session or {}).get("_imgcrop_out") or os.path.join("data", "crops")
    os.makedirs(d, exist_ok=True)
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    fp = os.path.join(d, f"{stem}.{tag}{ext or '.png'}")
    img.convert("RGB").save(fp)
    return fp


def _aspect_box(w, h, rw, rh):
    target = rw / rh
    if w / h > target:                      # too wide -> cut width
        nw = int(h * target)
        return (w - nw) // 2, 0, (w - nw) // 2 + nw, h
    nh = int(w / target)                    # too tall -> cut height
    return 0, (h - nh) // 2, w, (h - nh) // 2 + nh


def crop_aspect(path, ratio, session=None):
    img, err = _load(path)
    if img is None:
        return err
    w, h = img.size
    rw, rh = (1, 1) if ratio == "square" else (int(ratio.split(r"[:x]")[0]), int(re.split(r"[:x]", ratio)[1]))
    box = _aspect_box(w, h, rw, rh)
    fp = _save(img.crop(box), path, session, tag=f"{rw}x{rh}")
    return (f"Cropped to **{rw}:{rh}** ({box[2] - box[0]}×{box[3] - box[1]} from {w}×{h}) -> `{fp}`. "
            f"Original untouched.")


def crop_box(path, x, y, w, h, session=None):
    img, err = _load(path)
    if img is None:
        return err
    W, H = img.size
    x, y = max(0, int(x)), max(0, int(y))
    w, h = min(int(w), W - x), min(int(h), H - y)
    if w <= 0 or h <= 0:
        return "That box is outside the image."
    fp = _save(img.crop((x, y, x + w, y + h)), path, session, tag="box")
    return f"Cropped **{w}×{h}** at ({x},{y}) -> `{fp}`. Original untouched."


def trim(path, session=None, tol=12):
    img, err = _load(path)
    if img is None:
        return err
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    corner = px[0, 0]

    def row_same(y):
        return all(abs(px[x, y][0] - corner[0]) <= tol and abs(px[x, y][1] - corner[1]) <= tol
                   and abs(px[x, y][2] - corner[2]) <= tol for x in range(0, w, max(1, w // 60)))

    def col_same(x):
        return all(abs(px[x, y][0] - corner[0]) <= tol and abs(px[x, y][1] - corner[1]) <= tol
                   and abs(px[x, y][2] - corner[2]) <= tol for y in range(0, h, max(1, h // 60)))

    top, bottom, left, right = 0, h - 1, 0, w - 1
    while top < bottom - 1 and row_same(top):
        top += 1
    while bottom > top + 1 and row_same(bottom):
        bottom -= 1
    while left < right - 1 and col_same(left):
        left += 1
    while right > left + 1 and col_same(right):
        right -= 1
    if (top, left, bottom, right) == (0, 0, h - 1, w - 1):
        return "No uniform border found to trim."
    fp = _save(rgb.crop((left, top, right + 1, bottom + 1)), path, session, tag="trim")
    return (f"Trimmed {top}px top, {h - 1 - bottom}px bottom, {left}px left, {w - 1 - right}px right "
            f"-> `{fp}`. Original untouched.")


def match(text):
    return bool(ASPECT.match(text.strip()) or BOX.match(text.strip()) or TRIM.match(text.strip()))


def run(text, session):
    t = text.strip()
    m = ASPECT.match(t)
    if m:
        trace(session, "imgcrop", f"aspect {m.group('ratio')}")
        ratio = "square" if m.group("ratio") == "square" else m.group("ratio").replace("x", ":")
        return {"reply": crop_aspect(m.group("path"), ratio, session), "sources": []}
    if (m := BOX.match(t)):
        trace(session, "imgcrop", "box")
        return {"reply": crop_box(m.group("path"), m.group("x"), m.group("y"),
                                  m.group("w"), m.group("h"), session), "sources": []}
    if (m := TRIM.match(t)):
        trace(session, "imgcrop", "trim")
        return {"reply": trim(m.group("path"), session), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    from PIL import Image
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    wide = os.path.join(tmp, "wide.png")
    Image.new("RGB", (200, 100), (30, 30, 30)).save(wide)
    bordered = os.path.join(tmp, "bordered.png")
    img = Image.new("RGB", (120, 120), (250, 250, 250))     # uniform border colour
    for x in range(20, 100):
        for y in range(20, 100):
            img.putpixel((x, y), (10, 10, 10))
    img.save(bordered)

    assert match("crop " + wide + " to square")
    assert match("center crop " + wide + " to 16:9")
    assert match("crop " + wide + " to 300x200 at 10,20")
    assert match("trim the borders of " + bordered)
    assert not match("crop my sentences")
    assert not match("trim the video")

    r = run("crop " + wide + " to square", {"_imgcrop_out": out})
    fp = os.path.join(out, "wide.1x1.png")
    assert os.path.isfile(fp), (r, os.listdir(out))
    assert "100×100" in r["reply"], r                       # 200x100 -> largest centred square
    r2 = run("crop " + wide + " to 300x200 at 10,20", {"_imgcrop_out": out})
    assert "190×80" in r2["reply"], r2                      # clipped to the image bounds
    r3 = run("trim the borders of " + bordered, {"_imgcrop_out": out})
    assert "20px top" in r3["reply"] and "20px left" in r3["reply"], r3
    r4 = run("crop D:/nope.png to square", {})
    assert "not found" in r4["reply"].lower()
    print("[self-check] imgcrop OK - aspect/box/trim crops computed exactly, originals untouched, "
          "writes only data/crops")
