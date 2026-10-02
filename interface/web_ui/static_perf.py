"""Static-serving speed layer (Cycle 4): cached pre-compression, explicit Cache-Control, selective gzip.

Kept in its own module (server.py imports the whole dispatcher and its boot threads) so the behaviour
has a fast, offline regression test: tests/test_static_perf.py.
"""
import zlib
from mimetypes import guess_type

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

# .woff2/.woff are not in Python's mimetypes table on Windows: without this, font files are served as
# application/octet-stream and strict browsers refuse to apply them.
import mimetypes as _mt

_mt.add_type("font/woff2", ".woff2")
_mt.add_type("font/woff", ".woff")


class CachedStaticFiles(StaticFiles):
    """StaticFiles with an explicit Cache-Control plus a pre-compression cache for text assets.

    The HUD's bundle is ~1 MB of JS. Compressing it per request costs ~25-35 ms of CPU (measured), which
    on loopback is pure overhead - but a *cached* compressed copy is both smaller on the wire (1.02 MB
    -> ~290 KB) and faster than reading the file again. Names are content-hashed and versioned, so one
    compressed copy per process is enough; repeat loads are served with a 1-year immutable cache."""

    TEXT_EXT = (".js", ".mjs", ".css", ".svg", ".json", ".html", ".txt", ".map", ".webmanifest")
    _gz_cache = {}  # (path, mtime_ns) -> (gzip bytes | None, etag)

    def __init__(self, *args, max_age: int = 0, immutable: bool = False, min_gzip: int = 2048, **kwargs):
        super().__init__(*args, **kwargs)
        self._cache = f"public, max-age={max_age}" + (", immutable" if immutable else "")
        self._min_gzip = min_gzip

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        path = str(full_path)
        req = Headers(scope=scope)
        if (self._min_gzip and stat_result.st_size >= self._min_gzip and path.endswith(self.TEXT_EXT)
                and "gzip" in req.get("accept-encoding", "") and not req.get("range")):  # never break byte ranges
            key = (path, stat_result.st_mtime_ns)
            hit = self._gz_cache.get(key)
            if hit is None:
                blob = None
                try:
                    with open(path, "rb") as f:  # gzip wrapper (not zlib): Content-Encoding: gzip means RFC 1952
                        gz = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
                        blob = gz.compress(f.read()) + gz.flush()
                except OSError:
                    pass
                hit = (blob, f'"{stat_result.st_mtime_ns:x}-{stat_result.st_size:x}-gz"')
                if blob is not None:
                    if len(self._gz_cache) > 32:  # a handful of builds at most; never grow unbounded
                        self._gz_cache.clear()
                    self._gz_cache[key] = hit
            blob, etag = hit
            if blob is not None:
                return Response(content=blob, status_code=status_code,
                                media_type=guess_type(path)[0] or "application/octet-stream",
                                headers={"Cache-Control": self._cache, "ETag": etag,
                                         "Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = self._cache
        return response


class SelectiveGZipMiddleware:
    """GZipMiddleware, except on the token streams, media and pre-compressed assets: /api/execute_stream
    carries the model's reply one line at a time (compressing that path would trade real-time streaming
    for bytes gzip can't shrink), audio/video/images are already compressed, and CachedStaticFiles
    serves its own cached gzip copy in one shot."""

    SKIP = ("/api/execute_stream", "/api/voice", "/api/stt", "/api/media", "/api/manage/owner_voice/preview",
            "/assets", "/fonts", "/ws")

    def __init__(self, app, minimum_size: int = 700):
        self.raw = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size, compresslevel=6)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or any(scope["path"].startswith(p) for p in self.SKIP):
            return await self.raw(scope, receive, send)
        return await self.gzip(scope, receive, send)
