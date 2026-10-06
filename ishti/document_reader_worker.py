"""Parser worker. Receives only bytes; never opens an owner-supplied file path."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import zipfile

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 60000
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_MEMORY_BYTES = 1536 * 1024 * 1024
_job_handle = None


def _limit_resources():
    """Hard parser process limits; these do not restrict filesystem access."""
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
        return
    import ctypes
    from ctypes import wintypes
    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                    ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]
    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IOCounters),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.basic.flags = 0x100 | 0x2 | 0x8 | 0x2000  # memory, CPU, one process, kill-on-close
    limits.basic.process_time = 40 * 10_000_000
    limits.basic.active_processes = 1
    limits.process_memory = MAX_MEMORY_BYTES
    if not handle:
        raise OSError("Reader resource limits unavailable")
    if (not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())):
        kernel.CloseHandle(handle)
        raise OSError("Reader resource limits unavailable")
    global _job_handle
    _job_handle = handle  # Keep until worker exit; closing it terminates its job.


def _deny_network(*_args, **_kwargs):
    raise RuntimeError("Document extraction cannot use the network")


def _check_archive(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(item.file_size for item in entries) > MAX_ARCHIVE_BYTES:
            raise ValueError("Office archive exceeds reader bounds")
        if any(item.flag_bits & 1 for item in entries):
            raise ValueError("Encrypted Office archive")


def _join(parts, limit):
    result, length = [], 0
    for part in parts:
        chunk = str(part)[:max(0, limit - length)]
        result.append(chunk)
        length += len(chunk) + 1
        if length >= limit:
            break
    return "\n".join(result)[:limit]


def _pdf(data, limit):
    import pypdfium2 as pdfium
    parts = []
    length = 0
    with pdfium.PdfDocument(data) as document:
        for index in range(min(60, len(document))):
            with contextlib.closing(document[index]) as page:
                with contextlib.closing(page.get_textpage()) as textpage:
                    text = textpage.get_text_range()
                if len(text.strip()) < 40 and index < 30:
                    # Only local OCR models. Limit raster size before native allocation.
                    width, height = page.get_size()
                    if width <= 0 or height <= 0 or width * height > 25_000_000:
                        raise ValueError("PDF page exceeds raster bounds")
                    scale = min(2.0, (25_000_000 / (width * height)) ** 0.5)
                    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                    from ishti.ocr import ocr
                    with contextlib.closing(page.render(scale=scale)) as bitmap:
                        with bitmap.to_pil() as image:
                            scanned = ocr(image)
                    text = scanned or text
                parts.append(text[:max(0, limit - length)])
                length += len(parts[-1]) + 1
                if length >= limit:
                    break
    return "\n".join(parts)[:limit]


def extract(extension, data, limit):
    if len(data) > MAX_FILE_BYTES or not 0 <= limit <= MAX_TEXT_CHARS:
        raise ValueError("Reader input exceeds bounds")
    if extension in {".docx", ".pptx", ".xlsx"}:
        _check_archive(data)
    if extension == ".pdf":
        return _pdf(data, limit)
    if extension == ".docx":
        import docx
        document = docx.Document(io.BytesIO(data))
        def parts():
            yield from (paragraph.text for paragraph in document.paragraphs)
            yield from (" | ".join(cell.text for cell in row.cells)
                        for table in document.tables for row in table.rows)
        return _join(parts(), limit)
    if extension == ".pptx":
        from pptx import Presentation
        return _join((shape.text_frame.text for slide in Presentation(io.BytesIO(data)).slides
                      for shape in slide.shapes if shape.has_text_frame), limit)
    if extension in {".xlsx", ".xls", ".csv"}:
        import pandas as pd
        source = io.BytesIO(data)
        frame = pd.read_csv(source, nrows=200) if extension == ".csv" else pd.read_excel(source, nrows=200)
        return frame.to_csv(index=False)[:limit]
    if extension in {".txt", ".md", ".doc"} or extension in _CODE_EXT:
        return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")[:limit]
    raise ValueError("Unsupported format")


_CODE_EXT = {".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log", ".sql", ".sh", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".php", ".rb", ".vue", ".scss"}


def main():
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.socket.sendto = _deny_network
    socket.create_connection = _deny_network
    socket.getaddrinfo = _deny_network
    socket.gethostbyname = _deny_network
    socket.gethostbyaddr = _deny_network
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    os.environ.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    # Parser output is discarded; only a bounded JSON response crosses the pipe.
    try:
        _limit_resources()
        request = json.loads(sys.stdin.buffer.read(70 * 1024 * 1024))
        data = base64.b64decode(request["bytes"], validate=True)
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
            text = extract(request["extension"], data, int(request["limit"]))
        sys.stdout.write(json.dumps({"text": text}, ensure_ascii=True))
        return 0
    except Exception:
        sys.stdout.write('{"error":"Document extraction failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
