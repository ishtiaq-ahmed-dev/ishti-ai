"""Local reader worker. Input and output are data, never executable instructions."""
import base64
import io
import json
import socket
import sys
import warnings


def _no_network(*_args, **_kwargs):
    raise RuntimeError("Media readers cannot access the network")


def read_bytes(kind, data):
    if not data or len(data) > 50 * 1024 * 1024:
        raise ValueError("Empty or oversized media")
    from ishti import ocr
    from PIL import Image, ImageOps
    result = {"text": "", "alternatives": {}, "readers": [], "uncertainty": [], "truncated": False}
    def image_text(image):
        primary = ""
        try:
            primary = ocr.windows_ocr(image)
            if primary.strip(): result["readers"].append("windows_ocr")
        except Exception:
            result["uncertainty"].append("Windows OCR unavailable")
        secondary = ""
        try:
            secondary = ocr.rapid_ocr(image)
            if secondary.strip(): result["readers"].append("rapidocr")
        except Exception:
            result["uncertainty"].append("Independent OCR unavailable")
        if primary.strip() and secondary.strip() and " ".join(primary.split()) != " ".join(secondary.split()):
            result["alternatives"]["rapidocr"] = secondary[:12000]
            result["uncertainty"].append("OCR engines disagree; identifiers, amounts and dates require checking against both readings")
        return primary or secondary
    if kind == "image":
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as opened:
            if opened.format not in {"PNG", "JPEG", "WEBP", "GIF"} or opened.width * opened.height > 25000000:
                raise ValueError("Unsupported or oversized image")
            opened.verify()
        with Image.open(io.BytesIO(data)) as opened:
            result["text"] = image_text(ImageOps.exif_transpose(opened).convert("RGB"))
    elif kind == "document":
        if not data.startswith(b"%PDF-"):
            raise ValueError("Only PDF documents are supported by this reader")
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(data)
        try:
            result["truncated"] = len(document) > 20
            pages = []
            for index in range(min(20, len(document))):
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try: text = textpage.get_text_range()
                    finally: textpage.close()
                    if not text.strip() and index < 5:
                        bitmap = page.render(scale=1.5)
                        try: text = image_text(bitmap.to_pil())
                        finally: bitmap.close()
                    elif not text.strip():
                        result["uncertainty"].append(f"Scanned page {index + 1} not OCR processed (five-page OCR limit)")
                    pages.append(f"[Page {index + 1}]\n{text}")
                finally: page.close()
            result["text"] = "\n\n".join(pages)
            result["readers"].append("pdfium_text")
        finally: document.close()
    else:
        raise ValueError("Only images and PDFs are supported by this reader")
    if len(result["text"]) > 12000:
        result["text"] = result["text"][:12000]
        result["truncated"] = True
    result["readers"] = sorted(set(result["readers"]))
    return result


if __name__ == "__main__":
    # Prevent reader libraries from downloading models or fetching embedded content.
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    def local_connect(self, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(self, address)
        return _no_network()
    def local_connect_ex(self, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect_ex(self, address)
        return _no_network()
    socket.socket.connect = local_connect
    socket.socket.connect_ex = local_connect_ex
    socket.create_connection = _no_network
    try:
        request = json.loads(sys.stdin.buffer.read(70 * 1024 * 1024))
        data = base64.b64decode(request["bytes"], validate=True)
        result = read_bytes(request["kind"], data)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
    except Exception:
        sys.stdout.write(json.dumps({"error": "Local media extraction failed; no content was executed"}))
        sys.exit(1)
