"""UI checks for the Chat page (agent GLM-FLASH, branch audit/glm-20261007).

Asserts the three behaviours fixed in this audit:
  1. chat text can be selected (drag in a user bubble, double-click in an answer)
  2. the thinking panel toggles (finished runs are collapsed one-liners, click expands)
  3. the activity panel opens with the plain step list

Run against a LIVE UI (the backend plus either the built app served at :8888 or a dev server):

    set ISHTI_TEST_MODE=1 && set ISHTI_SCRATCH=1 && start.bat server 8889
    set ISHTI_UI_URL=http://localhost:5199
    venv\\Scripts\\python.exe interface\\app\\tests\\ui_check.py

Environment:
  ISHTI_UI_URL  base URL of the running UI (default http://localhost:5199)
  ISHTI_CHROME  path to a Chrome/Chromium binary (default: Playwright's own, then a local install)
Exit code 0 = all checks passed, 1 = at least one failed.
"""
from __future__ import annotations

import asyncio
import glob
import os
import re
import sys

from playwright.async_api import async_playwright

URL = os.environ.get("ISHTI_UI_URL", "http://localhost:5199")
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def chrome_path() -> str | None:
    if os.environ.get("ISHTI_CHROME"):
        return os.environ["ISHTI_CHROME"]
    local = os.environ.get("LOCALAPPDATA", "")
    for pat in ("ms-playwright/chromium-*/chrome-win64/chrome.exe", "ms-playwright/chromium-*/chrome-win/chrome.exe"):
        hits = sorted(glob.glob(os.path.join(local, pat)))
        if hits:
            return hits[-1]
    return None


async def dismiss_gate(page) -> None:
    gate = page.locator('[data-testid="profile-gate"]')
    if await gate.count():
        await page.locator('[data-testid="gate-personal"]').click()
        await gate.wait_for(state="detached", timeout=10000)


async def main() -> int:
    exe = chrome_path()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
        page = await (await browser.new_context(viewport={"width": 1280, "height": 800})).new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await dismiss_gate(page)

        # --- send one message so the chat has a user bubble, an answer, a thinking block and a run
        await page.get_by_role("button", name="New chat", exact=True).click()
        await page.wait_for_timeout(500)
        box = page.get_by_placeholder(re.compile("Ask or tell"))
        await box.click()
        await box.fill("UI check: short greeting please.")
        await box.press("Enter")

        try:
            await page.wait_for_function(
                "() => document.querySelectorAll('[data-testid=copy-message]').length >= 2", timeout=120000)
        except Exception as exc:  # noqa: BLE001
            check("run produced a reply", False, repr(exc))
        await page.wait_for_timeout(1500)

        # --- 1. selection
        bubble = page.locator(".ishti-selectable.bg-accent").last
        try:
            await bubble.scroll_into_view_if_needed()
            bb = await bubble.bounding_box()
            y = bb["y"] + 16
            x0, x1 = bb["x"] + 22, bb["x"] + bb["width"] - 22
            await page.mouse.move(x0, y)
            await page.mouse.down()
            for i in range(1, 11):
                await page.mouse.move(x0 + (x1 - x0) * i / 10, y)
                await page.wait_for_timeout(30)
            await page.mouse.up()
            sel = await page.evaluate("() => window.getSelection().toString()")
            check("selection: drag in a user bubble", bool(sel and sel.strip()), repr(sel[:40]))
        except Exception as exc:  # noqa: BLE001
            check("selection: drag in a user bubble", False, repr(exc))

        try:
            para = page.locator(".ishti-selectable:has(p) p").last
            await para.scroll_into_view_if_needed()
            pb = await para.bounding_box()
            await page.mouse.dblclick(pb["x"] + 10, pb["y"] + pb["height"] / 2)
            await page.wait_for_timeout(200)
            sel = await page.evaluate("() => window.getSelection().toString()")
            check("selection: double-click in an answer", bool(sel and sel.strip()), repr(sel[:40]))
        except Exception as exc:  # noqa: BLE001
            check("selection: double-click in an answer", False, repr(exc))

        # --- 2. thinking toggle
        try:
            panel = page.get_by_test_id("persisted-thinking").last
            await panel.wait_for(state="visible", timeout=20000)
            btn = panel.get_by_test_id("thinking-toggle")
            collapsed = (await btn.get_attribute("aria-expanded")) == "false"
            check("thinking: finished run is one collapsed line", collapsed, "")
            await btn.click()
            await page.wait_for_timeout(300)
            check("thinking: click expands the steps",
                  (await btn.get_attribute("aria-expanded")) == "true",
                  f"steps={await panel.locator('li').count()}")
        except Exception as exc:  # noqa: BLE001
            check("thinking toggles", False, repr(exc))

        # --- 3. activity list
        try:
            await page.get_by_role("button", name="Activity", exact=True).click()
            await page.get_by_test_id("activity-panel").wait_for(state="visible", timeout=10000)
            list_tab = page.get_by_test_id("activity-view-list")
            check("activity: list view is the default tab",
                  (await list_tab.get_attribute("aria-pressed")) == "true", "")
            await page.wait_for_function(
                "() => document.querySelectorAll('[data-testid=activity-step]').length >= 1", timeout=30000)
            row = page.get_by_test_id("activity-step").first
            await row.click()
            await page.wait_for_timeout(250)
            check("activity: step row expands its detail",
                  (await row.get_attribute("aria-expanded")) == "true", "")
        except Exception as exc:  # noqa: BLE001
            check("activity list opens with expandable steps", False, repr(exc))

        real_errors = [e for e in errors if "favicon" not in e]
        check("no console/page errors", not real_errors, f"count={len(real_errors)}")
        for e in real_errors[:5]:
            print("   ERR:", e[:160])

        await browser.close()

    failed = [r for r in RESULTS if not r[1]]
    print(f"=== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
