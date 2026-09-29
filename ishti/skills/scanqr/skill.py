"""QR codes (inventory domain 3 #9): generate and read, entirely local.

"qr code for https://ishti.local", "generate a qr for wifi: SSID/pass",
"scan the qr in D:/shots/ticket.png", "read the qr D:/imgs/code.png", "decode qr D:/x.png"

Encoding: cv2.QRCodeEncoder (OpenCV 5), matrix upscaled 8 px/module with a 32 px quiet zone
so phones can read it; the generated file is re-decoded before claiming success.
Decoding: zxing-cpp — this machine's cv2 5.0 wheel QR *decoder* returns None even on its own
freshly-encoded output (encoder works fine), so the decoder is zxingcpp.read_barcodes, which
round-trips both polarities. If zxing-cpp is absent the reply says exactly that instead of
pretending to scan. Output: data/qr/qr_<slug>.png. T1, local write only.
"""
import os
import re
import unicodedata

from ishti.skills import trace

I = re.I

GEN = re.compile(
    r"^\s*(?:generate|make|create)\s+(?:an?\s+)?qr\s+code\s+(?:for|of|with)?\s*:?\s*(?P<text>.+)$|"
    r"^\s*qr\s+code\s+(?:for|of)\s*:?\s*(?P<text2>.+)$", I)
SCAN = re.compile(
    r"^\s*(?:scan|read|decode)\s+(?:the\s+)?qr\s+(?:code\s+)?(?:in\s+)?(?P<path>[A-Za-z]:[\\/].+?\.(?:png|jpe?g|bmp|webp|gif|tiff?))\s*$|"
    r"^\s*(?:scan|read)\s+(?:the\s+)?qr(?:\s+code)?\s+(?P<path2>[\w./\\ -]+?\.(?:png|jpe?g|bmp|webp|gif|tiff?))\s*$", I)


def _slug(text):
    text = unicodedata.normalize("NFKD", text.strip())[:40]
    text = re.sub(r"[^\w\s-]", "", text, flags=re.I)
    return re.sub(r"[\s_]+", "-", text.strip().lower()) or "qr"


def _read_barcodes(img):
    """Decode via zxing-cpp (cv2 5.0's own QR decoder is broken on this build)."""
    try:
        import zxingcpp
    except ImportError:
        return None
    return [(r.format.name, r.text) for r in zxingcpp.read_barcodes(img)]


def generate(text, session=None):
    import cv2
    import numpy as np
    text = text.strip()
    try:
        matrix = cv2.QRCodeEncoder.create().encode(text)
    except cv2.error as exc:
        return f"Could not encode that text (too long or invalid for a QR): {exc}"
    big = np.kron((matrix == 0).astype(np.uint8) * 255, np.ones((8, 8), dtype=np.uint8))
    big = np.pad(big, 32, constant_values=255)          # 4 modules × 8 px quiet zone
    out = (session or {}).get("_qr_out") or os.path.join("data", "qr")
    os.makedirs(out, exist_ok=True)
    fp = os.path.join(out, f"qr_{_slug(text)}.png")
    cv2.imwrite(fp, big)
    parts = [f"QR code for **{text}** saved to `{fp}` (8 px/module, quiet zone included)."]
    found = _read_barcodes(cv2.imread(fp))
    if found is None:
        parts.append("Not verified — install zxing-cpp and I can self-check reads.")
    elif found and found[0][1] == text:
        parts.append("Verified: decoded back to exactly the same text.")
    else:
        parts.append(f"**Warning:** self-read came back {found or 'empty'} — check the file before using it.")
    return "\n".join(parts)


def scan(path, session=None):
    import cv2
    if not os.path.isfile(path):
        return f"File not found: {path}"
    img = cv2.imread(path)
    if img is None:
        return f"Could not read the image: {path}"
    found = _read_barcodes(img)
    if found is None:
        return ("No QR decoder available — `pip install zxing-cpp` (this build's cv2 QR decoder "
                "is broken, so the standalone reader does the scanning).")
    if not found:
        return "No QR code or barcode found in that image."
    lines = [f"**{len(found)} code(s) in {os.path.basename(path)}:**"]
    for fmt, text in found:
        lines.append(f"- [{fmt}] {text}" + ("" if text else " (empty payload)"))
    return "\n".join(lines)


def match(text):
    t = text.strip()
    return bool(GEN.match(t) or SCAN.match(t))


def run(text, session):
    t = text.strip()
    if (m := GEN.match(t)):
        trace(session, "scanqr", "generate")
        return {"reply": generate(m.group("text") or m.group("text2"), session), "sources": []}
    if (m := SCAN.match(t)):
        trace(session, "scanqr", "scan")
        return {"reply": scan(m.group("path") or m.group("path2"), session), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    payload = "ISHTI-SMOKE-123"

    assert match("qr code for https://ishti.local")
    assert match("generate a qr code for wifi: net/pass")
    assert match("scan the qr in D:/shots/ticket.png")
    assert match("read the qr D:/imgs/code.png")
    assert match("decode qr D:/x.png")
    assert not match("scan my computer for viruses")
    assert not match("qr code standards history")      # needs 'for' + payload

    r = generate(payload, {"_qr_out": out})
    fp = os.path.join(out, f"qr_{_slug(payload)}.png")
    assert os.path.isfile(fp), r
    assert "Verified" in r, r                          # self-read must agree
    r2 = scan(fp, {})
    assert payload in r2 and "QRCode" in r2, r2
    r3 = scan("D:/definitely/missing.png", {})
    assert "not found" in r3.lower(), r3
    print("[self-check] scanqr OK - cv2-encoded QR re-decoded via zxing-cpp to the exact payload, "
          "missing-file + no-decoder paths honest, writes only data/qr")
