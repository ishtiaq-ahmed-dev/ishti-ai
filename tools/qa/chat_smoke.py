"""Headless browser smoke for the durable, profile-isolated chat UI.

The target server must already be running in test+scratch mode.  This script exercises
real UI controls for rename, delete/undo, switching profile during a run, and the persisted
collapsed thought panel. It records page, console, transport, HTTP, and assertion failures.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
import uuid
import urllib.parse


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "audit" / "chat_smoke.json"


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("refusing non-loopback/non-http target")
    if os.environ.get("ISHTI_TEST_MODE") != "1" or os.environ.get("ISHTI_SCRATCH") != "1":
        raise SystemExit("set ISHTI_TEST_MODE=1 and ISHTI_SCRATCH=1 for both server and smoke client")
    return url.rstrip("/") + "/"


def repo_output(raw: str) -> pathlib.Path:
    output = pathlib.Path(raw).resolve()
    try:
        output.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit("output must stay inside the ISHTI repository") from exc
    return output


def main() -> int:
    args = parse_args()
    url = safe_url(args.url)
    output = repo_output(args.output)
    result = {"schema": 1, "started_at": iso_now(), "base_url": url, "checks": [], "errors": []}

    def save() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + ".tmp")
        temp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temp, output)

    def record(name: str, ok: bool, detail: str = "") -> None:
        result["checks"].append({"at": iso_now(), "name": name, "ok": bool(ok), "detail": detail[:1000]})
        if not ok:
            result["errors"].append({"at": iso_now(), "kind": "assertion", "operation": name,
                                     "detail": detail[:2000] or "check failed"})
        save()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        result["errors"].append({"at": iso_now(), "kind": "dependency", "operation": "import playwright",
                                 "detail": str(exc)})
        save()
        print(f"Playwright is not installed; report={output}")
        return 2

    marker = "UI_SOAK_" + uuid.uuid4().hex[:10]
    title = "Smoke " + uuid.uuid4().hex[:8]
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=not args.headed, channel=args.channel or None)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_init_script("sessionStorage.setItem('ishti-profile-chosen','1')")
            page = context.new_page()

            def page_error(exc) -> None:
                result["errors"].append({"at": iso_now(), "kind": "pageerror", "operation": "browser",
                                         "detail": str(exc)[:2000]})
                save()

            def console(msg) -> None:
                if msg.type == "error":
                    result["errors"].append({"at": iso_now(), "kind": "console", "operation": "browser",
                                             "detail": msg.text[:2000]})
                    save()

            def response(resp) -> None:
                if "/api/" in resp.url and resp.status >= 400:
                    result["errors"].append({"at": iso_now(), "kind": "http", "operation": resp.request.method,
                                             "status": resp.status, "detail": resp.url[:1000]})
                    save()

            page.on("pageerror", page_error)
            page.on("console", console)
            page.on("response", response)
            page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
            page.locator('textarea[placeholder*="Ask or tell ISHTI"], textarea[placeholder*="Assign a task"]').wait_for(
                state="visible", timeout=args.timeout * 1000
            )
            page.get_by_test_id("profile-personal").click(timeout=args.timeout * 1000)
            page.get_by_role("button", name="New chat", exact=True).click(timeout=args.timeout * 1000)

            # The newest row is first. Its hover-only controls are still deterministic DOM controls.
            page.locator('button[title="Rename chat"]').first.click(force=True)
            # The only visible autofocus text input at this point is the chat title editor.
            rename = page.locator("input[autofocus]")
            rename.fill(title)
            rename.press("Enter")
            record("rename persists in sidebar", page.get_by_text(title, exact=True).count() > 0, title)

            composer = page.locator('textarea[placeholder*="Ask or tell ISHTI"], textarea[placeholder*="Assign a task"]')
            composer.fill(f"Reply briefly with this marker: {marker}")
            composer.press("Enter")
            page.get_by_test_id("pending-run").wait_for(state="visible", timeout=args.timeout * 1000)

            # A pending run intentionally produces the switch confirmation dialog.
            page.get_by_test_id("profile-business").click()
            confirm = page.get_by_test_id("profile-confirm-yes")
            if confirm.count():
                confirm.click()
            page.wait_for_timeout(1200)
            record("personal message hidden in business profile", marker not in page.locator("body").inner_text(), marker)

            page.get_by_test_id("profile-personal").click()
            confirm = page.get_by_test_id("profile-confirm-yes")
            if confirm.count():
                confirm.click()
            page.get_by_text(marker, exact=False).first.wait_for(state="visible", timeout=args.timeout * 1000)
            page.get_by_test_id("persisted-thinking").first.wait_for(state="visible", timeout=args.run_timeout * 1000)
            record("mid-run switch preserved sent message", page.get_by_text(marker, exact=False).count() > 0, marker)
            thought = page.get_by_test_id("persisted-thinking").first
            record("thought panel is collapsed after answer", thought.locator('button[aria-expanded="false"]').count() == 1,
                   thought.inner_text())

            page.reload(wait_until="domcontentloaded")
            page.get_by_test_id("persisted-thinking").first.wait_for(state="visible", timeout=args.timeout * 1000)
            record("thought panel persists after reload", page.get_by_test_id("persisted-thinking").count() > 0)

            # Target the row by its unique title, then use its own delete control.
            row = page.get_by_text(title, exact=True).locator("xpath=ancestor::div[.//button[@title='Delete chat']][1]")
            row.locator('button[title="Delete chat"]').click(force=True)
            page.get_by_role("button", name="Yes, delete", exact=True).click()
            page.get_by_role("button", name="Undo", exact=True).click()
            page.get_by_text(title, exact=True).wait_for(state="visible", timeout=args.timeout * 1000)
            record("delete undo restores chat", page.get_by_text(title, exact=True).count() > 0, title)

            # The harness never uses a voice control; independently prove global state stayed OFF.
            mic = page.request.get(url.rstrip("/") + "/api/mic/status")
            mic_data = mic.json() if mic.ok else {}
            record("microphone remained off", mic.ok and not mic_data.get("enabled")
                   and not mic_data.get("listener_running") and not mic_data.get("live_session_running"),
                   json.dumps(mic_data, default=str))
            browser.close()
    except Exception as exc:  # noqa: BLE001 - the artifact must contain browser/setup timeouts too
        result["errors"].append({"at": iso_now(), "kind": "exception", "operation": "chat smoke",
                                 "detail": f"{type(exc).__name__}: {exc}"[:4000]})
    result["finished_at"] = iso_now()
    save()
    print(f"chat smoke complete: {len(result['errors'])} errors; report={output}")
    return 1 if result["errors"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless UI smoke for ISHTI chat lifecycle and isolation.")
    parser.add_argument("--url", default="http://127.0.0.1:8888", help="loopback app URL")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON evidence path")
    parser.add_argument("--timeout", type=int, default=20, help="ordinary UI timeout in seconds")
    parser.add_argument("--run-timeout", type=int, default=180, help="assistant-run timeout in seconds")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--channel", default="", help="Playwright browser channel, e.g. chrome")
    args = parser.parse_args()
    if args.timeout <= 0 or args.run_timeout <= 0:
        parser.error("timeouts must be positive")
    return args


if __name__ == "__main__":
    sys.exit(main())
