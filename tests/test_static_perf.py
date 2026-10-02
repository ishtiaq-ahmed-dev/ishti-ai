"""Static-serving & poll-path performance guard (Cycle 4 - fast loading, fast access).

Locks in the contract without booting the dispatcher (fast, offline, no GPU):
  * CachedStaticFiles: hashed/versioned files are pre-compressed once (cached gzip) and served with an
    immutable Cache-Control; byte ranges are never broken by the compression path.
  * SelectiveGZipMiddleware: compresses ordinary responses but never the token stream / media paths.
  * The React entry point uses the LOCAL fonts (no fonts.googleapis.com on load) and the font files are
    on disk where the server mounts them.
  * get_system_telemetry() - the HUD's 3-second poll and the "how much ram am I using" skill - is cheap
    when warm; the old path slept 100 ms and spawned nvidia-smi per call (measured 150 ms).
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

APP_DIR = os.path.join(ROOT, "interface", "app")
PAYLOAD = b"/* js */\n" * 700  # ~7 KB of compressible text


def _app(tmp_path):
    """The two layers the HUD load path uses: CachedStaticFiles (immutable, pre-compressed) under
    /assets and SelectiveGZipMiddleware over everything."""
    from interface.web_ui.static_perf import CachedStaticFiles, SelectiveGZipMiddleware
    (tmp_path / "index-abc123.js").write_bytes(PAYLOAD)
    routes = [
        Route("/plain", lambda request: PlainTextResponse("y" * 4000)),
        Route("/api/execute_stream", lambda request: PlainTextResponse('{"token":"x"}\n' * 200)),
        Mount("/assets", app=CachedStaticFiles(directory=str(tmp_path), max_age=31536000, immutable=True)),
    ]
    return TestClient(SelectiveGZipMiddleware(Starlette(routes=routes)))


def test_hashed_asset_is_precompressed_once_and_immutable(tmp_path):
    client = _app(tmp_path)
    r = client.get("/assets/index-abc123.js", headers={"accept-encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert "immutable" in r.headers["cache-control"] and "31536000" in r.headers["cache-control"]
    assert r.headers.get("vary") == "Accept-Encoding"
    assert r.content == PAYLOAD  # decodes back to the exact file
    assert int(r.headers["content-length"]) < len(PAYLOAD)  # actually smaller on the wire
    assert client.get("/assets/index-abc123.js", headers={"accept-encoding": "gzip"}).content == PAYLOAD  # cached copy


def test_asset_without_gzip_support_is_served_raw(tmp_path):
    r = _app(tmp_path).get("/assets/index-abc123.js", headers={"accept-encoding": "identity"})
    assert r.status_code == 200 and "content-encoding" not in r.headers
    assert r.content == PAYLOAD


def test_range_request_is_never_gzipped(tmp_path):
    r = _app(tmp_path).get("/assets/index-abc123.js", headers={"accept-encoding": "gzip", "range": "bytes=0-99"})
    assert "content-encoding" not in r.headers, "a byte range must not claim gzip"


def test_token_stream_is_never_gzipped_but_json_is(tmp_path):
    from interface.web_ui.static_perf import SelectiveGZipMiddleware
    assert "/api/execute_stream" in SelectiveGZipMiddleware.SKIP and "/ws" in SelectiveGZipMiddleware.SKIP
    client = _app(tmp_path)
    stream = client.get("/api/execute_stream", headers={"accept-encoding": "gzip"})
    assert "content-encoding" not in stream.headers, "compressing the token stream would delay every token"
    assert client.get("/plain", headers={"accept-encoding": "gzip"}).headers["content-encoding"] == "gzip"


def test_ui_fonts_are_local_not_google():
    for page in (os.path.join(APP_DIR, "index.html"), os.path.join(APP_DIR, "dist", "index.html")):
        if not os.path.exists(page):
            continue
        html = open(page, encoding="utf-8").read()
        assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html, \
            "the HUD must not fetch fonts from the internet (local-first + fast loading)"
        assert "/fonts/fonts.css" in html and "rel=\"preload\"" in html
    css = os.path.join(APP_DIR, "public", "fonts", "fonts.css")
    assert os.path.exists(css), "tools/fetch_ui_fonts.py output is missing"
    body = open(css, encoding="utf-8").read()
    assert "font-display: swap" in body and "/fonts/" in body and "@font-face" in body
    woff2 = [f for f in os.listdir(os.path.dirname(css)) if f.endswith(".woff2")]
    assert woff2, "no local font files"
    from interface.web_ui.static_perf import CachedStaticFiles  # noqa: F401  (registers font/woff2 MIME)
    from mimetypes import guess_type
    assert guess_type("a.woff2")[0] == "font/woff2", "fonts must not be served as octet-stream"


def test_telemetry_poll_path_is_cheap_when_warm():
    from desktop.computer_use import get_computer_use_agent
    agent = get_computer_use_agent()
    first = agent.get_system_telemetry()          # first call primed psutil + filled the caches
    t0 = time.perf_counter()
    second = agent.get_system_telemetry()
    elapsed = time.perf_counter() - t0
    assert set(first) == set(second) and second["cpu_count"] == first["cpu_count"]
    assert isinstance(second["gpu"], (dict, str)) and isinstance(second["disks"], dict)
    # Generous bound: the old implementation slept 100 ms and spawned nvidia-smi every call (measured
    # 150 ms). 80 ms can only fail if the 100 ms sleep or a per-call subprocess comes back.
    assert elapsed < 0.08, f"telemetry poll regressed to {elapsed * 1000:.0f} ms"
