"""Green-screen removal (inventory domain 4 #15): ffmpeg chromakey/colorkey.

"remove the green screen from D:/take1.mp4", "chroma key D:/shot.mp4 with 00ff00",
"remove the green screen from D:/a.png", "key out the background of D:/x.jpg"

Uses colorkey for images (PNG with alpha out) and chromakey for video (WebM with alpha —
VP9 supports it; the reply states the output container). The similarity blend (0.20) and the
exact filter string are shown so the result is reproducible; the original is untouched.
Outputs to data/keyed/. T1, local write only.
"""
import os
import re
import subprocess

from ishti.skills import trace

I = re.I

IMG = re.compile(
    r"^\s*(?:key\s+out\s+the\s+background\s+of|remove\s+the\s+green\s+screen\s+from|chroma\s*key)\s+"
    r"(?:the\s+)?(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:png|jpe?g|bmp|webp))\s*"
    r"(?:\s+with\s+(?P<color>#[0-9a-f]{6}))?\s*$", I)
VID = re.compile(
    r"^\s*(?:remove\s+the\s+green\s+screen\s+from|chroma\s*key)\s+"
    r"(?:the\s+)?(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:mp4|mkv|mov|avi|webm))\s*"
    r"(?:\s+with\s+(?P<color>#[0-9a-f]{6}))?\s*$", I)


def _out(path, session=None):
    d = (session or {}).get("_key_out") or os.path.join("data", "keyed")
    os.makedirs(d, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    return os.path.join(d, f"{stem}.keyed{'.png' if ext.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.webp') else '.webm'}")


def key_out(path, color, is_image, session=None):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    color = (color or "#00ff00").lstrip("#")
    filt = (f"colorkey=0x{color}:0.20:0.10" if is_image
            else f"chromakey=0x{color}:0.20:0.10")
    out = _out(path, session)
    cmd = (["ffmpeg", "-y", "-i", path, "-vf", filt, out] if is_image
           else ["ffmpeg", "-y", "-i", path, "-vf", filt,
                 "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", out])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        return "ffmpeg isn't installed or not on PATH."
    if proc.returncode != 0 or not os.path.isfile(out):
        return "ffmpeg failed:\n" + "\n".join((proc.stderr or "").strip().splitlines()[-3:])
    kind = "image" if is_image else "video (WebM/VP9, alpha kept)"
    return (f"Keyed `{os.path.basename(path)}` → `{out}`.\n"
            f"Filter: **{filt}** (similarity 0.20, blend 0.10) — {kind}. Original untouched. "
            f"Check the edges; spill on hair/glass may need a higher blend.")


def match(text):
    t = text.strip()
    return bool(IMG.match(t) or VID.match(t))


def run(text, session):
    t = text.strip()
    if (m := IMG.match(t)):
        trace(session, "chromakey", "image")
        return {"reply": key_out(m.group("path"), m.group("color"), True, session), "sources": []}
    if (m := VID.match(t)):
        trace(session, "chromakey", "video")
        return {"reply": key_out(m.group("path"), m.group("color"), False, session), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    from PIL import Image

    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    img = os.path.join(tmp, "shot.png")
    # left half green (the "background"), right half red (the subject) — real pixels
    im = Image.new("RGB", (64, 64), (0, 255, 0))
    for x in range(32, 64):
        for y in range(64):
            im.putpixel((x, y), (200, 0, 0))
    im.save(img)

    assert match("remove the green screen from " + img)
    assert match("chroma key " + img + " with #00ff00")
    assert match("key out the background of " + img)
    assert not match("remove the green paint from the wall list")

    r = run("remove the green screen from " + img, {"_key_out": out})
    fp = os.path.join(out, "shot.keyed.png")
    assert os.path.isfile(fp), r
    keyed = Image.open(fp).convert("RGBA")
    # green corner must now be transparent, red corner must stay opaque
    assert keyed.getpixel((2, 2))[3] < 128, keyed.getpixel((2, 2))
    assert keyed.getpixel((40, 32))[3] > 200, keyed.getpixel((40, 32))
    assert "colorkey=0x00ff00" in r["reply"], r
    r2 = run("remove the green screen from D:/definitely/missing.mp4", {})
    assert "not found" in r2["reply"].lower(), r2
    print("[self-check] chromakey OK - real green/red image keyed: green → transparent, "
          "subject → opaque, filter string stated, writes only data/keyed")
