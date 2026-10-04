"""Local OCR without the vision model: exact text from images, the screen and scanned PDFs.
  1. Windows' own OCR engine (Windows.Media.Ocr, built into Windows 11): ~0.1 s a screen, keeps spaces
  2. RapidOCR (PaddleOCR models on ONNX Runtime, CPU) when Windows OCR is missing or reads nothing
The vision model stays for "what's happening on my screen"."""
import asyncio
import threading

try:  # must load before the WinRT modules: importing RapidOCR (OpenCV) after them segfaults the process
    import rapidocr_onnxruntime  # noqa: F401
except ImportError:
    pass

_rapid, _lock = None, threading.Lock()


def _pil(image):
    from PIL import Image
    if hasattr(image, "convert"):
        return image
    if isinstance(image, (bytes, bytearray)):
        import io
        return Image.open(io.BytesIO(image))
    if hasattr(image, "shape"):  # numpy
        return Image.fromarray(image)
    return Image.open(image)


def windows_ocr(img):
    """Text via Windows.Media.Ocr, one line per text line ('' if the engine isn't available)."""
    try:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError:
        return ""
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""
    img = img.convert("RGBA")
    limit = OcrEngine.max_image_dimension
    if max(img.size) > limit:
        img.thumbnail((limit, limit))
    writer = DataWriter()
    writer.write_bytes(img.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8, *img.size)

    async def recognize():
        return await engine.recognize_async(bmp)
    result = asyncio.run(recognize()) if not _loop_running() else _in_thread(recognize)
    return "\n".join(line.text for line in result.lines)


def windows_ocr_boxes(img):
    """Read-only OCR word boxes in image-local pixels for grounded desktop clicks."""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return []
    image = img.convert("RGBA")
    if max(image.size) > OcrEngine.max_image_dimension:
        raise ValueError("Screen is too large to locate text without changing coordinates")
    writer = DataWriter()
    writer.write_bytes(image.tobytes())
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8, *image.size)
    async def recognize():
        return await engine.recognize_async(bitmap)
    result = asyncio.run(recognize()) if not _loop_running() else _in_thread(recognize)
    return [[(word.text, int(word.bounding_rect.x), int(word.bounding_rect.y),
              int(word.bounding_rect.width), int(word.bounding_rect.height)) for word in line.words]
            for line in result.lines]


def _loop_running():
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _in_thread(coro_fn):  # called from inside an event loop (the web server): run the WinRT call on its own loop
    out = {}
    t = threading.Thread(target=lambda: out.update(r=asyncio.run(coro_fn())))
    t.start()
    t.join()
    return out["r"]


def rapid_ocr(img):
    global _rapid
    import numpy as np
    with _lock:
        if _rapid is None:
            from rapidocr_onnxruntime import RapidOCR
            _rapid = RapidOCR()
    result, _ = _rapid(np.array(img.convert("RGB")))
    if not result:
        return ""
    boxes = sorted(result, key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])))
    lines, last_y = [], None
    for box, text, _conf in boxes:  # words on the same row join with a space, new rows start a new line
        y = min(p[1] for p in box)
        h = max(p[1] for p in box) - y
        if lines and last_y is not None and abs(y - last_y) < h * 0.6:
            lines[-1] += " " + text
        else:
            lines.append(text)
            last_y = y
    return "\n".join(lines)


def ocr(image):
    """Text of an image (path, bytes, PIL image or numpy array)."""
    img = _pil(image)
    try:
        found = windows_ocr(img)
    except Exception:
        found = ""
    return found if found.strip() else rapid_ocr(img)


def ocr_pdf(path, pages=30, scale=2.0):
    """Scanned PDF -> text, page by page (pages separated by a form feed)."""
    import pypdfium2 as pdfium
    from ishti import PDFIUM_LOCK
    with PDFIUM_LOCK:  # render the pages under the lock, OCR them after
        doc = pdfium.PdfDocument(path)
        images = [doc[i].render(scale=scale).to_pil() for i in range(min(len(doc), pages))]
    return "\f".join(ocr(im) for im in images)


def ocr_screen():
    """(text on every monitor, monitor by monitor; screenshot path of all of them)."""
    import mss
    from PIL import Image
    from ishti.skills.desktop.skill import _shot
    texts = []
    try:
        with getattr(mss, "MSS", mss.mss)() as s:
            for i, mon in enumerate(s.monitors[1:], 1):
                try:
                    g = s.grab(mon)
                    text = ocr(Image.frombytes("RGB", g.size, g.rgb))
                    if text.strip():
                        texts.append((f"[Screen {i}]\n" if len(s.monitors) > 2 else "") + text)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        path = _shot()
    except Exception:
        from ishti.automation.multi_screen_agent import get_multi_screen_agent
        _, _, path = get_multi_screen_agent().capture_engine.capture_stitched()
    return "\n\n".join(texts), path


if __name__ == "__main__":
    import os
    import tempfile
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (900, 220), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype("arial.ttf", 40)
    d.text((20, 30), "Invoice 4471 total 12,500 PKR", fill="black", font=font)
    d.text((20, 120), "Due date 30 September 2026", fill="black", font=font)
    got = ocr(im)
    assert got == "Invoice 4471 total 12,500 PKR\nDue date 30 September 2026", got  # Windows OCR: exact, spaces kept
    rapid = rapid_ocr(im)
    assert "4471" in rapid and rapid.count("\n") == 1, rapid  # the fallback reads it too
    pdf = os.path.join(tempfile.mkdtemp(), "scan.pdf")
    im.save(pdf)  # an image-only PDF, like a scan
    assert "Due date 30 September 2026" in ocr_pdf(pdf)
    print("[self-check] ocr OK - Windows OCR exact, RapidOCR fallback, scanned PDF")
