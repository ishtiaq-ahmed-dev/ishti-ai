"""API surface sweep for the ISHTI scratch server (tools/qa/ui_api_sweep.py).

What it does
  1. GET /openapi.json and enumerate every declared route (method + path).
  2. Call every safe read route (GET) and record status, latency and body size.
  3. Fuzz every non-destructive write route with a valid body and several invalid bodies,
     recording status + latency.
  4. Watch the server log for new tracebacks and attribute them to the sweep.
  5. Write docs/audit/qa_api_sweep.json and append defects to docs/audit/qa_defects.json.

Safety (higher priority than literal coverage):
  Routes that would control the PC, the app lifecycle, remote channels, external connectors,
  model/config state or delete data are NEVER called, even with an invalid body. They are recorded
  with status "skipped_safety" and the reason, so the report stays honest without violating the
  ISHTI safety invariants.

Self-check: python tools/qa/ui_api_sweep.py --help
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _qa_common as qa  # noqa: E402

# The app logs to %LOCALAPPDATA%\ISHTI\logs\ishti.log (read-only for this harness).
DEFAULT_SERVER_LOG = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                                  "ISHTI", "logs", "ishti.log")
SLOW_MS = 10_000.0
# best-effort source guess for a 5xx (checked as a substring of the path)
FILE_GUESS = [
    ("/api/about/instructions", "ishti/dispatcher.py"),
    ("/api/sessions", "interface/web_ui/server.py"),
    ("/api/connectors", "ishti/connectors.py"),
    ("/api/learning", "ishti/learning/dashboard.py"),
]

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# ---- safety denylist: matched as substrings of the full path -------------------------------
# Each entry: (pattern, reason). A write route matching any pattern is skipped.
DENY_WRITE = [
    ("/api/kill", "app kill / control (T3)"),
    ("/api/shutdown", "app lifecycle (T2)"),
    ("/api/remote", "remote channels / pairing / tokens"),
    ("/api/connectors", "external account credentials / connect"),
    ("/api/inputs/wa", "starts the WhatsApp bridge"),
    ("/api/mic/", "PC microphone control"),
    ("/api/manage", "model / role / voice / file management"),
    ("/api/self_heal", "changes self-heal behaviour"),
    ("/api/rebenchmark", "heavy model benchmark"),
    ("/api/arch", "model backend / memory mutation"),
    ("/api/ollama/", "model settings mutation"),
    ("/api/mcp/reload", "restarts MCP clients"),
    ("/api/vault", "vault purge / cleanup (destructive)"),
    ("/api/memory/backup", "writes a backup outside scratch"),
    ("/api/memory/export", "writes export files"),
    ("/api/memory/people", "forget/confirm people"),
    ("/api/memory/conflicts", "resolves memory conflicts"),
    ("/api/memory/vault", "memory vault write"),
    ("/api/crm", "CRM mutation / delete"),
    ("/api/leads/export", "writes an export file"),
    ("/api/sessions/export", "writes an export file"),
    ("/api/sessions/delete", "deletes sessions"),
    ("/api/sessions/undo", "restores sessions"),
    ("/api/sessions/{session_id}/feedback", "writes feedback"),
    ("/api/sessions/{session_id}/activate", "session state change"),
    ("/api/sessions/{session_id}/rename", "renames a session"),
    ("/api/learning/export", "writes dataset export"),
    ("/api/learning/dataset", "dataset mutation"),
    ("/api/learning/family", "family memory mutation"),
    ("/api/learning/lessons", "lesson mutation"),
    ("/api/learning/settings", "learning config write"),
    ("/api/learning/mode", "learning mode write"),
    ("/api/learning/reflections", "reflection mining"),
    ("/api/learning/workflows", "workflow mutation"),
    ("/api/skills/{name}/enabled", "enable/disable a skill"),
    ("/api/skills/proposals", "proposal resolve"),
    ("/api/tasks/", "task mutation"),
    ("/api/agents/events", "agent bus injection"),
    ("/api/prefs", "prefs config write"),
    ("/api/privacy", "privacy config write"),
    ("/api/settings/languages", "languages config write"),
    ("/api/prompt/layers", "prompt layer write"),
    ("/api/notifications/read", "safe but low value"),
    ("/api/errors/clear", "erases diagnostics"),
    ("/api/reveal", "opens files in Explorer"),
    ("/api/focus", "focus-mode control"),
    ("/api/execute", "runs the orchestrator (covered by chat flow)"),
    ("/api/execute_stream", "runs the orchestrator (covered by chat flow)"),
    ("/api/chat", "runs the orchestrator (covered by chat flow)"),
    ("/api/stt", "audio upload"),
    ("/api/voice", "audio upload"),
    ("/api/upload", "file upload"),
    ("/api/markets", "market watchlist mutation"),
    ("/api/chrome_profiles", "browser profile write"),
    ("/api/feedback", "writes feedback"),
    ("/api/reminders", "writes reminders"),
    ("/api/facts", "writes owner facts"),
    ("/api/memory/", "memory mutation"),
    ("/api/arch/memory", "memory mutation"),
    ("/api/arch/switch", "backend switch"),
    ("/api/vram/", "VRAM admission control"),
    ("/api/eval/run", "runs the eval harness"),
    ("/api/flowise", "flowise control"),
    ("/api/health", "n/a"),
]

# GET routes that still have side effects or leak/trigger work -> skipped.
DENY_READ = [
    ("/api/inputs/wa-qr", "may start the WhatsApp bridge"),
    ("/api/inputs/screen", "captures the live screen"),
    ("/api/manage/preview", "opens/streams a local file"),
    ("/api/manage/owner_voice/preview", "streams a local file"),
]

# A valid-ish body per path prefix (first match wins). Missing -> {} sent as JSON.
VALID_BODY = {
    "/api/profile": {"active": "personal"},
    "/api/chats": {"profile": "personal"},
    "/api/chats/{chat_id}/messages": {"text": "qa sweep ping", "client_msg_id": "qa-sweep"},
    "/api/chats/{chat_id}/live-turns": {"user": "u", "reply": "r"},
    "/api/learning/dataset": {"example": {}},
    "/api/learning/mode": {"mode": "propose"},
    "/api/memory/training-dry-run": {},
    "/api/chrome_profiles": {},
    "/api/coonnectors": {},
}

INVALID_BODIES = [
    ("empty", b""),
    ("non_json", b"not json at all"),
    ("json_array", b"[1,2,3]"),
    ("wrong_types", b'{"text": 123, "profile": ["x"], "active": null}'),
    ("unexpected_keys", b'{"__qa_no_such_key__": {"deep": [1, 2, 3]}}'),
]


def load_routes(base: str) -> list[tuple[str, str]]:
    resp = qa.http(base, "GET", "/openapi.json", timeout=15)
    spec = resp.json()
    if not isinstance(spec, dict) or not spec.get("paths"):
        raise SystemExit(f"could not read /openapi.json (status={resp.status})")
    routes: list[tuple[str, str]] = []
    for path, ops in spec["paths"].items():
        for method in ops:
            if method.lower() in HTTP_METHODS:
                routes.append((method.upper(), path))
    routes.sort(key=lambda r: (r[1], r[0]))
    return routes


def deny_reason(method: str, path: str) -> str:
    table = DENY_READ if method == "GET" else DENY_WRITE
    for pattern, reason in table:
        if pattern in path:
            return reason
    return ""


def file_guess(path: str) -> str:
    for pattern, src in FILE_GUESS:
        if pattern in path:
            return src
    return "interface/web_ui/server.py"


def fill_params(path: str) -> str:
    """Substitute every {param} with a deterministic non-existent id."""
    out = path
    for name in ("chat_id", "run_id", "session_id", "candidate_id", "example_id",
                 "lesson_id", "workflow_id", "conflict_id", "cid", "service", "pid",
                 "task_id", "action", "name", "attr", "rid", "aid"):
        out = out.replace("{" + name + "}", f"zzz-no-such-{name}")
    for name in ("{name}", "{attr}", "{action}"):
        out = out.replace(name, "")
    return out


def valid_body_for(path: str):
    for prefix, body in VALID_BODY.items():
        if path.startswith(prefix):
            return body
    return {}


def read_new_log(path: str, offset: int) -> tuple[str, int]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            text = fh.read()
            return text, fh.tell()
    except OSError:
        return "", offset


def extract_tracebacks(text: str) -> list[str]:
    out, current = [], []
    for line in text.splitlines():
        if line.startswith("Traceback (most recent call last)"):
            if current:
                out.append("\n".join(current))
            current = [line]
        elif current:
            if line and not line.startswith((" ", "\t")):
                out.append("\n".join(current))
                current = []
            else:
                current.append(line)
    if current:
        out.append("\n".join(current))
    return out


def main() -> int:
    args = parse_args()
    qa.require_test_mode()
    base = qa.safe_base_url(args.url)
    output = qa.repo_output(args.output)
    defects = qa.DefectStore()

    result = {
        "schema": 1, "started_at": qa.iso_now(), "base_url": base,
        "server_log": args.server_log, "routes": [], "tracebacks": [],
        "counts": {}, "errors": [],
    }
    qa.dump_json(output, result)

    routes = load_routes(base)
    log_offset = 0
    try:
        log_offset = pathlib.Path(args.server_log).stat().st_size
    except OSError:
        log_offset = 0
    result["server_log"] = args.server_log

    counts = {"total": len(routes), "get_ok": 0, "get_5xx": 0, "write_ok": 0,
              "write_5xx": 0, "skipped_safety": 0, "transport_error": 0,
              "unexpected_5xx": 0, "slow": 0}
    defects_before = len(defects.rows)
    seen_route_defects: set[str] = set()

    for method, path in routes:
        record = {"method": method, "path": path}
        if method == "GET":
            reason = deny_reason(method, path)
            if reason:
                record.update(status=None, outcome="skipped_safety", reason=reason)
                counts["skipped_safety"] += 1
                result["routes"].append(record)
                continue
            resp = qa.http(base, "GET", fill_params(path), timeout=args.timeout)
            record.update(status=resp.status, latency_ms=resp.latency_ms,
                          bytes=len(resp.body or ""), error=resp.error)
            if resp.status == 0:
                record["outcome"] = "transport_error"
                counts["transport_error"] += 1
            elif resp.status >= 500:
                record["outcome"] = "server_error"
                counts["get_5xx"] += 1
                counts["unexpected_5xx"] += 1
                note_get_500(defects, result, record, resp)
            elif resp.status in (401, 403, 404, 405, 422):
                record["outcome"] = "handled"  # expected auth/validation rejection
                counts["get_ok"] += 1
            else:
                record["outcome"] = "ok"
                counts["get_ok"] += 1
            if resp.status and resp.latency_ms > SLOW_MS:
                record["outcome"] = "slow"
                counts["slow"] += 1
                note_slow(defects, result, record, resp.latency_ms, seen_route_defects)
            result["routes"].append(record)
            qa.dump_json(output, result)
            continue

        # write methods
        reason = deny_reason(method, path)
        if reason:
            record.update(status=None, outcome="skipped_safety", reason=reason)
            counts["skipped_safety"] += 1
            result["routes"].append(record)
            continue
        plain = fill_params(path)
        # valid body
        resp = qa.http(base, method, plain, body=valid_body_for(path), timeout=args.timeout)
        record["valid"] = {"status": resp.status, "latency_ms": resp.latency_ms,
                           "error": resp.error, "bytes": len(resp.body or "")}
        bad_variants = []
        if resp.status >= 500:
            counts["write_5xx"] += 1
            counts["unexpected_5xx"] += 1
            bad_variants.append("valid")
        # invalid bodies
        record["invalid"] = []
        for label, raw in INVALID_BODIES:
            r2 = qa.http(base, method, plain, raw_body=raw, timeout=args.timeout,
                         headers={"content-type": "application/json"})
            entry = {"variant": label, "status": r2.status, "latency_ms": r2.latency_ms,
                     "error": r2.error}
            record["invalid"].append(entry)
            if r2.status >= 500:
                counts["write_5xx"] += 1
                counts["unexpected_5xx"] += 1
                bad_variants.append(label)
        if bad_variants:
            detail = ("variants " + ",".join(bad_variants)
                      + f" -> 500 on {method} {path}")
            note_write_500(defects, result, record, detail)
        ok = resp.status not in (0,) and resp.status < 500
        record["outcome"] = "ok" if ok else "failing"
        if ok:
            counts["write_ok"] += 1
        result["routes"].append(record)
        qa.dump_json(output, result)

    # traceback attribution
    new_log, _ = read_new_log(args.server_log, log_offset)
    tbs = extract_tracebacks(new_log)
    result["tracebacks"] = tbs[-20:]
    if tbs:
        for i, tb in enumerate(tbs, 1):
            last = next((ln for ln in reversed(tb.splitlines()) if ln.strip()), "")
            defects.add(f"SWEEP-TB-{i:02d}", "api_sweep",
                        f"sweep against {base}",
                        "no unhandled traceback in the server log during the sweep",
                        f"server logged traceback: {last[:300]}",
                        "interface/web_ui/server.py")
    result["counts"] = counts
    result["new_defects"] = len(defects.rows) - defects_before
    result["finished_at"] = qa.iso_now()
    qa.dump_json(output, result)
    defects.save()

    print(f"API sweep: {counts['total']} routes, {counts['unexpected_5xx']} unexpected 5xx, "
          f"{counts['skipped_safety']} skipped for safety, {len(tbs)} tracebacks; report={output}")
    return 1 if counts["unexpected_5xx"] or tbs or counts["transport_error"] else 0


def note_get_500(defects, result, record, resp) -> None:
    detail = f"GET {record['path']} -> {resp.status}: {(resp.body or '')[:200]}"
    result["errors"].append({"at": qa.iso_now(), "method": "GET", "path": record["path"],
                             "status": resp.status, "detail": detail})
    key = f"SWEEP-500-GET-{record['path']}"
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)[:80]
    defects.add(safe_id, "api_sweep", f"curl -X GET {record['path']}",
                "a read route returns a handled status (<500)", detail, file_guess(record["path"]))


def note_write_500(defects, result, record, detail) -> None:
    result["errors"].append({"at": qa.iso_now(), "method": record["method"],
                             "path": record["path"], "status": 500, "detail": detail})
    key = f"SWEEP-500-{record['method']}-{record['path']}"
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)[:80]
    defects.add(safe_id, "api_sweep",
                f"curl -X {record['method']} {record['path']} with an empty / non-JSON / array body",
                "an invalid body is rejected with 400/422, never an unhandled 500",
                detail,
                file_guess(record["path"]))


def note_slow(defects, result, record, latency_ms, seen) -> None:
    key = f"SWEEP-SLOW-{record['method']}-{record['path']}"
    if key in seen:
        return
    seen.add(key)
    result["errors"].append({"at": qa.iso_now(), "method": record["method"],
                             "path": record["path"], "status": record.get("status"),
                             "detail": f"latency {latency_ms:.0f} ms"})
    defects.add(key[:80], "api_sweep",
                f"curl -X GET {record['path']}",
                f"a read route responds in under {SLOW_MS / 1000:.0f}s",
                f"latency {latency_ms:.0f} ms (> {SLOW_MS / 1000:.0f}s)",
                file_guess(record["path"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ISHTI scratch-server API surface sweep.")
    parser.add_argument("--url", default="http://127.0.0.1:8899")
    parser.add_argument("--output", default=str(qa.AUDIT / "qa_api_sweep.json"))
    parser.add_argument("--server-log", default=DEFAULT_SERVER_LOG)
    parser.add_argument("--timeout", type=float, default=45.0)
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
