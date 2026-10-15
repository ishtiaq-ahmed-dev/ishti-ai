"""Shared helpers for the ISHTI QA harness (tools/qa/*).

Safety rules enforced here, not in each script:
  * the client must be running with ISHTI_TEST_MODE=1 and ISHTI_SCRATCH=1
  * targets must be loopback http(s) only
  * every artifact is written atomically inside reports/qa/qa_*.json

Artifacts live under `reports/`, which is gitignored, and not under the retired `docs/audit/`: that
directory no longer exists, so writing there silently recreated it and put QA output back into
`git status`. The parts of a sweep worth publishing belong in a tracked file, the way
`tests/fixtures/tool_select_eval.json` carries the routing numbers.

This module never calls product code; it only speaks HTTP to the running scratch server.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
AUDIT = ROOT / "reports" / "qa"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def require_test_mode() -> None:
    if os.environ.get("ISHTI_TEST_MODE") != "1" or os.environ.get("ISHTI_SCRATCH") != "1":
        raise SystemExit("set ISHTI_TEST_MODE=1 and ISHTI_SCRATCH=1 for the QA client")


def safe_base_url(raw: str) -> str:
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in LOOPBACK:
        raise SystemExit("refusing non-loopback/non-http target")
    return raw.rstrip("/")


def repo_output(raw: str) -> pathlib.Path:
    """Only reports/qa/qa_*.json may be written by this role."""
    output = pathlib.Path(raw).resolve()
    try:
        output.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit("output must stay inside the ISHTI repository") from exc
    if output.parent != AUDIT or not output.name.startswith("qa_"):
        raise SystemExit("QA artifacts must be reports/qa/qa_*.json")
    return output


def dump_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Windows: a scanner can briefly hold the destination, so retry the atomic replace.
    last = None
    for attempt in range(10):
        try:
            os.replace(temp, path)
            return
        except OSError as exc:  # noqa: PERF203 - bounded retry
            last = exc
            time.sleep(0.05 * (attempt + 1))
    raise last


class Response:
    __slots__ = ("status", "body", "headers", "latency_ms", "error")

    def __init__(self, status, body, headers, latency_ms, error=""):
        self.status = status
        self.body = body
        self.headers = headers
        self.latency_ms = latency_ms
        self.error = error

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, TypeError):
            return None


def http(base: str, method: str, path: str, *, body=None, raw_body: bytes | None = None,
         headers: dict | None = None, timeout: float = 20.0) -> Response:
    """One HTTP request. Never raises; transport failures become status=0."""
    url = base + path
    data = raw_body
    hdrs = {"accept": "application/json", **(headers or {})}
    if body is not None and raw_body is None:
        data = json.dumps(body).encode("utf-8")
        hdrs["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return Response(resp.status, payload.decode("utf-8", "replace"),
                            dict(resp.headers), round((time.perf_counter() - t0) * 1000, 1))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        return Response(exc.code, payload.decode("utf-8", "replace"),
                        dict(exc.headers or {}), round((time.perf_counter() - t0) * 1000, 1))
    except Exception as exc:  # noqa: BLE001 - timeouts/refusals are data
        return Response(0, "", {}, round((time.perf_counter() - t0) * 1000, 1),
                        f"{type(exc).__name__}: {exc}")


class DefectStore:
    """reports/qa/qa_defects.json {id, area, steps, expected, actual, file_guess}."""

    def __init__(self, path: pathlib.Path | None = None):
        self.path = path or (AUDIT / "qa_defects.json")
        if self.path.exists():
            try:
                self.rows = json.loads(self.path.read_text(encoding="utf-8")).get("defects", [])
            except (ValueError, TypeError):
                self.rows = []
        else:
            self.rows = []
        self._seen = {r.get("id") for r in self.rows}

    def add(self, id: str, area: str, steps: str, expected: str, actual: str,
            file_guess: str) -> bool:
        if id in self._seen:
            return False
        self.rows.append({"id": id, "area": area, "steps": steps, "expected": expected,
                          "actual": actual, "file_guess": file_guess})
        self._seen.add(id)
        self.save()
        return True

    def save(self) -> None:
        dump_json(self.path, {"schema": 1, "updated_at": iso_now(), "defects": self.rows})


def new_defect_id(prefix: str, rows) -> str:
    return f"{prefix}-{len(rows) + 1:03d}"
