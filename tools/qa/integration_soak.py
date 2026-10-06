"""Safe mixed-traffic soak test for an already-running ISHTI scratch server.

The server MUST have been started with ISHTI_TEST_MODE=1 and ISHTI_SCRATCH=1.
This client enforces the same environment flags and a loopback URL.  It never enables
the microphone, connects an account, or invokes a world-changing connector action.

Example (PowerShell):
    $env:ISHTI_TEST_MODE='1'; $env:ISHTI_SCRATCH='1'
    venvScripts/python.exe tools/qa/integration_soak.py --url http://127.0.0.1:8888
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "audit" / "integration_soak.json"
TERMINAL_RUN_STATES = {"done", "failed", "cancelled"}


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def require_safe_target(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("refusing non-loopback on-http target; the soak is for a local scratch server only")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("target URL must be a plain loopback origin, without credentials/query/fragment")
    if os.environ.get("ISHTI_TEST_MODE") != "1" or os.environ.get("ISHTI_SCRATCH") != "1":
        raise SystemExit(
            "set ISHTI_TEST_MODE=1 and ISHTI_SCRATCH=1 before running this harness, "
            "and start the target server with the same flags"
        )
    return url.rstrip("/")


def require_repo_output(raw: str) -> pathlib.Path:
    output = pathlib.Path(raw).resolve()
    try:
        output.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit("output must stay inside the ISHTI repository") from exc
    return output


class Soak:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.base = require_safe_target(args.url)
        self.output = require_repo_output(args.output)
        self.report: dict[str, Any] = {
            "schema": 1,
            "started_at": iso_now(),
            "finished_at": None,
            "base_url": self.base,
            "requested_duration_s": args.duration,
            "safe_mode": {"test_mode": True, "scratch": True, "loopback": True, "mic_policy": "off-only"},
            "counts": {"requests": 0, "iterations": 0, "chats": 0, "uploads": 0},
            "checks": [],
            "errors": [],
        }
        self.original_profile: str | None = None
        self.profile_chats: dict[str, set[str]] = {"personal": set(), "business": set()}

    def save(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temp = self.output.with_suffix(self.output.suffix + ".tmp")
        temp.write_text(json.dumps(self.report, indent=2, ensure_ascii=False) + chr(10), encoding="utf-8")
        os.replace(temp, self.output)

    def error(self, kind: str, operation: str, detail: Any, **extra: Any) -> None:
        row = {"at": iso_now(), "kind": kind, "operation": operation, "detail": str(detail)[:2000], **extra}
        self.report["errors"].append(row)
        print(f"[ERROR] {operation}: {row['detail']}", flush=True)
        self.save()

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        row = {"at": iso_now(), "name": name, "ok": bool(ok), "detail": detail[:800]}
        self.report["checks"].append(row)
        if len(self.report["checks"]) > 500:
            self.report["checks"] = self.report["checks"][-500:]
        if not ok:
            self.error("assertion", name, detail or "check failed")
        return ok

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        expected: set[int] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, Any] | None:
        expected = expected or set(range(200, 300))
        headers = {"Accept": "application/json", "Origin": self.base}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        self.report["counts"]["requests"] += 1
        try:
            with urllib.request.urlopen(req, timeout=self.args.request_timeout) as response:
                status = response.status
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - every transport exception belongs in the artifact
            self.error("exception", f"{method} {path}", f"{type(exc).__name__}: {exc}")
            return None
        try:
            decoded: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            decoded = {"raw": raw[:2000]}
        if status not in expected:
            self.error("http", f"{method} {path}", decoded, status=status, expected=sorted(expected))
        return status, decoded

    def set_profile(self, profile: str) -> bool:
        out = self.request("PUT", "/api/profile", {"active": profile})
        return bool(out and out[0] == 200 and isinstance(out[1], dict) and out[1].get("active") == profile)

    def chats(self) -> list[dict[str, Any]]:
        out = self.request("GET", "/api/chats")
        if not out or out[0] != 200 or not isinstance(out[1], dict):
            return []
        return out[1].get("chats") or []

    def check_profile_isolation(self, active: str) -> None:
        rows = self.chats()
        wrong = [row for row in rows if row.get("profile") != active]
        leaked_ids = self.profile_chats["business" if active == "personal" else "personal"] & {
            str(row.get("chat_id")) for row in rows
        }
        self.check(
            f"{active} chat list is profile-scoped",
            not wrong and not leaked_ids,
            f"wrong_profiles={wrong[:3]} leaked_ids={sorted(leaked_ids)}",
        )

    def connector_reads(self) -> None:
        out = self.request("GET", "/api/connectors")
        if not out or out[0] != 200 or not isinstance(out[1], dict):
            return
        rows = out[1].get("connectors") or []
        if rows:
            row = random.choice(rows)
            connector_id = urllib.parse.quote(str(row.get("id") or ""), safe="")
            if connector_id:
                self.request("GET", f"/api/connectors/{connector_id}/status")

    def mic_off_check(self) -> None:
        # OFF is idempotent and is the only microphone mutation this harness is permitted to make.
        self.request("POST", "/api/mic/off", {})
        out = self.request("GET", "/api/mic/status")
        if out and out[0] == 200 and isinstance(out[1], dict):
            state = out[1]
            self.check(
                "microphone remains off",
                not state.get("enabled") and not state.get("listener_running")
                and not state.get("live_session_running"),
                json.dumps(state, default=str)[:800],
            )

    def upload_probe(self) -> None:
        boundary = f"----ishti-soak-{uuid.uuid4().hex}"
        filename = f"soak-probe-{uuid.uuid4().hex[:8]}.html"
        content = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/html\r\n"
            "\r\n"
            "<!doctype html><title>synthetic soak probe</title><p>No user data.</p>\r\n"
            f"--{boundary}--\r\n"
        )
        body = content.encode("ascii")
        out = self.request(
            "POST",
            "/api/upload",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        if out and out[0] == 200 and isinstance(out[1], dict):
            data = out[1]
            self.check(
                "upload describes intended handling",
                bool(data.get("action") or data.get("mention")),
                json.dumps(data, default=str)[:800],
            )
            self.report["counts"]["uploads"] += 1

    def wait_for_run(self, run_id: str, chat_id: str, marker: str) -> None:
        deadline = time.monotonic() + self.args.run_timeout
        run: dict[str, Any] = {}
        while time.monotonic() < deadline:
            out = self.request("GET", f"/api/runs/{urllib.parse.quote(run_id, safe='')}")
            if out and out[0] == 200 and isinstance(out[1], dict):
                run = out[1]
                if run.get("state") in TERMINAL_RUN_STATES:
                    break
            time.sleep(1.0)
        self.check(
            "run reached terminal state",
            run.get("state") in TERMINAL_RUN_STATES,
            f"run_id={run_id} state={run.get('state', 'unavailable')}",
        )
        out = self.request("GET", f"/api/chats/{urllib.parse.quote(chat_id, safe='')}/messages")
        messages = out[1].get("messages", []) if out and out[0] == 200 and isinstance(out[1], dict) else []
        sent_present = any(
            m.get("role") == "user" and marker in str(m.get("text") or "") for m in messages
        )
        reply_present = any(m.get("role") == "assistant" and m.get("run_id") == run_id for m in messages)
        self.check("mid-run switch preserved sent message", sent_present, f"chat_id={chat_id}")
        if run.get("state") == "done":
            self.check("mid-run switch preserved assistant reply", reply_present, f"chat_id={chat_id} run_id={run_id}")

    def chat_round(self) -> None:
        self.set_profile("personal")
        created = self.request("POST", "/api/chats", {"profile": "personal"})
        if not created or created[0] != 200 or not isinstance(created[1], dict):
            return
        chat_id = str(created[1].get("chat_id") or "")
        if not chat_id:
            self.error("contract", "POST /api/chats", "response omitted chat_id")
            return
        self.profile_chats["personal"].add(chat_id)
        marker = "SOAK_" + uuid.uuid4().hex[:10]
        sent = self.request("POST", f"/api/chats/{urllib.parse.quote(chat_id, safe='')}/messages", {
            "client_msg_id": str(uuid.uuid4()),
            "text": f"Reply briefly with this marker: {marker}",
            "attachments": [],
        })
        if not sent or sent[0] != 200 or not isinstance(sent[1], dict) or not sent[1].get("run_id"):
            return
        run_id = str(sent[1]["run_id"])

        # Change profile while the executor owns the run. The foreign chat/run must be invisible.
        self.set_profile("business")
        business_rows = self.chats()
        self.check(
            "personal chat hidden during business switch",
            chat_id not in {str(row.get("chat_id")) for row in business_rows},
            chat_id,
        )
        self.request(
            "GET",
            f"/api/chats/{urllib.parse.quote(chat_id, safe='')}/messages",
            expected={404},
        )
        self.request(
            "GET",
            f"/api/runs/{urllib.parse.quote(run_id, safe='')}",
            expected={404},
        )
        business = self.request("POST", "/api/chats", {"profile": "business"})
        if business and business[0] == 200 and isinstance(business[1], dict):
            business_id = str(business[1].get("chat_id") or "")
            if business_id:
                self.profile_chats["business"].add(business_id)
        self.check_profile_isolation("business")

        self.set_profile("personal")
        self.check_profile_isolation("personal")
        self.wait_for_run(run_id, chat_id, marker)
        self.report["counts"]["chats"] += 1

    def run(self) -> int:
        profile = self.request("GET", "/api/profile")
        if profile and profile[0] == 200 and isinstance(profile[1], dict):
            self.original_profile = profile[1].get("active")
        self.request("GET", "/api/health")
        self.mic_off_check()
        started = time.monotonic()
        next_chat = started
        next_upload = started
        try:
            while time.monotonic() - started < self.args.duration:
                self.report["counts"]["iterations"] += 1
                self.request("GET", "/api/health")
                self.mic_off_check()
                self.connector_reads()
                active = "personal" if self.report["counts"]["iterations"] % 2 else "business"
                self.set_profile(active)
                self.check_profile_isolation(active)
                now = time.monotonic()
                if now >= next_upload:
                    self.upload_probe()
                    next_upload = now + self.args.upload_every
                if now >= next_chat:
                    self.chat_round()
                    next_chat = time.monotonic() + self.args.chat_every
                self.save()
                remaining = self.args.duration - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(min(self.args.interval, remaining))
        except KeyboardInterrupt:
            self.report["interrupted"] = True
        except Exception as exc:  # noqa: BLE001 - preserve a diagnostic artifact on harness failure
            self.error("harness_exception", "main loop", f"{type(exc).__name__}: {exc}")
        finally:
            if self.original_profile in {"personal", "business"}:
                self.set_profile(self.original_profile)
            self.mic_off_check()
            self.report["finished_at"] = iso_now()
            self.report["elapsed_s"] = round(time.monotonic() - started, 3)
            self.save()
        print(
            f"soak complete: {len(self.report['errors'])} errors; report={self.output}",
            flush=True,
        )
        return 1 if self.report["errors"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run safe mixed traffic against an ISHTI scratch server.")
    parser.add_argument("--url", default="http://127.0.0.1:8888", help="loopback server origin")
    parser.add_argument("--duration", type=float, default=30 * 60, help="seconds to run (default: 1800)")
    parser.add_argument("--interval", type=float, default=5, help="seconds between mixed-traffic rounds")
    parser.add_argument("--chat-every", type=float, default=60, help="minimum seconds between chat runs")
    parser.add_argument("--upload-every", type=float, default=300, help="minimum seconds between safe uploads")
    parser.add_argument("--request-timeout", type=float, default=20, help="HTTP timeout in seconds")
    parser.add_argument("--run-timeout", type=float, default=180, help="seconds to await each chat run")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON evidence path")
    args = parser.parse_args()
    for name in ("duration", "interval", "chat_every", "upload_every", "request_timeout", "run_timeout"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


if __name__ == "__main__":
    sys.exit(Soak(parse_args()).run())
