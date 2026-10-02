"""
ISHTI Isolated Browser Automation Agent
Hard-isolated to <ISHTI root>/data/chrome_profile.
Strictly prohibits reading, enumerating, or accessing any other Chrome profile or user directory.
Provides web research, page scraping, screenshot capture, and consequential action gating.
"""

import os
import re
import time
import base64
import json
from typing import Dict, Any, Optional, List
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "orchestrator", "config.json")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
ISOLATED_PROFILE_DIR = os.path.join(DATA_DIR, "chrome_profile")
CHROME_EXECUTABLE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def should_use_visible_browser(user_prompt: str) -> bool:
    """Detect explicit user requests for visible browser operation."""
    prompt_lower = user_prompt.lower()
    visible_triggers = [
        "show browser", "visible browser", "non-headless", "non headless",
        "let me watch", "open browser", "open the browser", "show me the browser", "watch what you",
        "use browser", "browser window", "see the browser"
    ]
    return any(trigger in prompt_lower for trigger in visible_triggers)


class BrowserAgent:
    def __init__(self, headless: bool = True):
        self.profile_dir = ISOLATED_PROFILE_DIR
        self.chrome_exe = CHROME_EXECUTABLE
        self.headless = headless
        self._ensure_profile_isolation()

    def _ensure_profile_isolation(self) -> None:
        """
        Hard constraint verification: the profile directory must be inside ISHTI's own data folder
        (a real path-component check, so a sibling like "<root>-evil" or a ".." escape cannot pass)
        and never inside AppData or another user's profile.
        """
        normalized = os.path.normcase(os.path.realpath(self.profile_dir))
        data_root = os.path.normcase(os.path.realpath(DATA_DIR))
        try:
            inside = os.path.commonpath([normalized, data_root]) == data_root
        except ValueError:  # different drives
            inside = False
        if not inside:
            raise PermissionError(
                f"[SECURITY ALERT] Profile directory '{self.profile_dir}' violates isolation boundary! "
                f"Must be inside {DATA_DIR}."
            )
        os.makedirs(self.profile_dir, exist_ok=True)

    # Search engines change their markup; one hardcoded selector is a single point of failure.
    # Engines are tried in order and each one declares its own selectors.
    SEARCH_ENGINES = [
        {
            "name": "bing",
            "url": "https://www.bing.com/search?q={q}&setlang=en&cc=US",
            "row": "li.b_algo",
            "title": "h2 a",
            "snippet": ".b_caption p",
        },
        {
            "name": "duckduckgo_html",
            "url": "https://html.duckduckgo.com/html/?q={q}",
            "row": ".result__body",
            "title": ".result__title a",
            "snippet": ".result__snippet",
        },
    ]

    def search(self, query: str, max_results: int = 4, headless: Optional[bool] = None) -> List[Dict[str, str]]:
        """
        Web search inside the isolated profile, trying each engine until one yields results.
        Returns [] only when every engine failed - callers must treat that as a real failure,
        never as "I cannot search".
        """
        effective_headless = headless if headless is not None else self.headless
        print(f"[BrowserAgent] Searching for: '{query}' (headless={effective_headless})")

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_dir,
                    executable_path=self.chrome_exe,
                    headless=effective_headless,
                    args=[
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                page = context.new_page()
                page.set_default_timeout(15000)

                for engine in self.SEARCH_ENGINES:
                    try:
                        page.goto(engine["url"].format(q=requests_quote(query)), wait_until="domcontentloaded")
                        # Wait for the rows themselves: reading the DOM too early returns the
                        # previous page's results.
                        page.wait_for_selector(engine["row"], timeout=10000)
                        found = self._scrape_rows(page, engine, max_results)
                        if found:
                            print(f"[BrowserAgent] {engine['name']} returned {len(found)} results.")
                            context.close()
                            return found
                        print(f"[BrowserAgent] {engine['name']} returned 0 rows, trying next engine.")
                    except PlaywrightTimeoutError:
                        print(f"[BrowserAgent] {engine['name']} timed out, trying next engine.")
                    except Exception as exc:
                        print(f"[BrowserAgent] {engine['name']} failed: {exc}")

                context.close()
        except Exception as e:
            print(f"[BrowserAgent] Browser could not be launched: {e}")

        return []

    @staticmethod
    def _scrape_rows(page, engine: Dict[str, str], max_results: int) -> List[Dict[str, str]]:
        out = []
        for row in page.locator(engine["row"]).all()[: max_results * 2]:
            try:
                link = row.locator(engine["title"]).first
                if link.count() == 0:
                    continue
                title = link.inner_text().strip()
                url = BrowserAgent._decode_redirect(link.get_attribute("href") or "")
                snip_el = row.locator(engine["snippet"]).first
                snippet = snip_el.inner_text().strip() if snip_el.count() > 0 else ""
                if title and url.startswith("http"):
                    out.append({"title": title, "url": url, "snippet": snippet})
                if len(out) >= max_results:
                    break
            except Exception:
                continue
        return out

    @staticmethod
    def _decode_redirect(href: str) -> str:
        """Bing wraps result URLs in a base64 u=a1 redirect."""
        if "u=a1" in href:
            try:
                part = href.split("u=a1")[1].split("&")[0]
                part += "=" * (-len(part) % 4)
                decoded = base64.b64decode(part).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception as _exc:
                swallowed('browser/browser_agent.py:BrowserAgent._decode_redirect', _exc)
        return href

    def search_duckduckgo(self, query: str, max_results: int = 4, headless: Optional[bool] = None) -> List[Dict[str, str]]:
        """Kept for existing callers - delegates to the multi-engine search."""
        return self.search(query, max_results=max_results, headless=headless)

    def fetch_page_text(self, url: str, max_chars: int = 4000, headless: Optional[bool] = None) -> Dict[str, Any]:
        """
        Fetches webpage content inside isolated profile and extracts clean readable text.
        """
        effective_headless = headless if headless is not None else self.headless
        print(f"[BrowserAgent] Fetching page: {url} (headless={effective_headless})")
        content_data = {"url": url, "title": "", "text": "", "status": "success"}

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_dir,
                    executable_path=self.chrome_exe,
                    headless=effective_headless,
                    args=["--no-first-run", "--no-default-browser-check"],
                )
                page = context.new_page()
                page.set_default_timeout(18000)
                response = page.goto(url, wait_until="domcontentloaded")

                content_data["title"] = page.title()
                # Clean up ads, scripts, nav, footer
                page.evaluate("""
                    () => {
                        const tags = ['script', 'style', 'nav', 'footer', 'iframe', 'header', 'aside'];
                        tags.forEach(t => {
                            document.querySelectorAll(t).forEach(el => el.remove());
                        });
                    }
                """)
                body_text = page.locator("body").inner_text()
                # Remove excessive whitespace
                cleaned = re.sub(r"\n{3,}", "\n\n", body_text).strip()
                content_data["text"] = cleaned[:max_chars]
                context.close()
        except Exception as e:
            content_data["status"] = "error"
            content_data["error"] = str(e)
            print(f"[BrowserAgent] Error fetching page {url}: {e}")

        return content_data

    def capture_screenshot(self, url: str, output_filename: str = "screenshot.png", headless: Optional[bool] = None) -> str:
        """Captures page screenshot saved into ISHTI's data folder."""
        effective_headless = headless if headless is not None else self.headless
        out_dir = DATA_DIR
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, output_filename)

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                executable_path=self.chrome_exe,
                headless=effective_headless,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            page = context.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            page.goto(url, wait_until="networkidle")
            page.screenshot(path=out_path)
            context.close()

        return out_path

    def fill_and_submit_form(
        self,
        url: str,
        fields: Dict[str, Any],
        submit_selector: Optional[str] = None,
        headless: Optional[bool] = None,
        wait_seconds: float = 2.0,
    ) -> Dict[str, Any]:
        """
        Navigates to URL, locates and fills form fields, and submits the form.
        Supports text inputs, password, email, textarea, selects, checkboxes, and radio buttons.
        """
        effective_headless = headless if headless is not None else self.headless
        print(f"[BrowserAgent] Form fill on: {url} (fields={list(fields.keys())}, headless={effective_headless})")
        result = {"url": url, "fields_filled": [], "fields_failed": [], "submitted": False, "status": "success"}

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_dir,
                    executable_path=self.chrome_exe,
                    headless=effective_headless,
                    args=["--no-first-run", "--no-default-browser-check"],
                )
                page = context.new_page()
                page.set_default_timeout(20000)
                page.goto(url, wait_until="domcontentloaded")
                time.sleep(1.0)

                for field_key, field_value in fields.items():
                    filled = False
                    selectors_to_try = [
                        field_key,
                        f"input[name='{field_key}']",
                        f"textarea[name='{field_key}']",
                        f"select[name='{field_key}']",
                        f"#{field_key}",
                        f"input[placeholder*='{field_key}' i]",
                        f"textarea[placeholder*='{field_key}' i]",
                        f"input[aria-label*='{field_key}' i]",
                    ]

                    for sel in selectors_to_try:
                        try:
                            loc = page.locator(sel).first
                            if loc.count() > 0 and loc.is_visible():
                                tag_name = loc.evaluate("el => el.tagName.toLowerCase()")
                                input_type = loc.evaluate("el => (el.type || '').toLowerCase()")

                                if tag_name == "select":
                                    try:
                                        loc.select_option(label=str(field_value))
                                    except Exception:
                                        loc.select_option(value=str(field_value))
                                elif input_type in ["checkbox", "radio"]:
                                    if bool(field_value):
                                        loc.check()
                                    else:
                                        loc.uncheck()
                                else:
                                    loc.fill(str(field_value))

                                filled = True
                                result["fields_filled"].append({"field": field_key, "selector": sel, "value": str(field_value)})
                                break
                        except Exception:
                            continue

                    if not filled:
                        result["fields_failed"].append(field_key)

                # Submit if requested or auto-detect
                if submit_selector:
                    try:
                        btn = page.locator(submit_selector).first
                        if btn.count() > 0:
                            btn.click()
                            result["submitted"] = True
                    except Exception as e:
                        result["submit_error"] = str(e)
                elif fields:
                    submit_candidates = [
                        "button[type='submit']",
                        "input[type='submit']",
                        "button:has-text('Submit')",
                        "button:has-text('Log in')",
                        "button:has-text('Sign in')",
                        "button:has-text('Search')",
                    ]
                    for s_sel in submit_candidates:
                        try:
                            s_btn = page.locator(s_sel).first
                            if s_btn.count() > 0 and s_btn.is_visible():
                                s_btn.click()
                                result["submitted"] = True
                                break
                        except Exception:
                            continue

                time.sleep(wait_seconds)
                result["final_url"] = page.url
                result["page_title"] = page.title()

                # Capture verification screenshot
                out_path = os.path.join(DATA_DIR, f"form_result_{int(time.time())}.png")
                page.screenshot(path=out_path)
                result["screenshot"] = out_path

                context.close()
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

        return result

    def open_url(self, url: str) -> bool:
        """Launches visible Chrome with the isolated profile pointing to url."""
        try:
            import subprocess
            subprocess.Popen([
                self.chrome_exe,
                f"--user-data-dir={self.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                url,
            ])
            return True
        except Exception as e:
            print(f"[BrowserAgent] Failed to open URL: {e}")
            return False


def requests_quote(string: str) -> str:
    import urllib.parse
    return urllib.parse.quote_plus(string)


if __name__ == "__main__":
    agent = BrowserAgent(headless=True)
    res = agent.search("RTX 5070 Ti drivers", max_results=3)
    assert res, "browser search returned nothing - every engine failed"
    assert all(r["url"].startswith("http") for r in res), res
    for r in res:
        print("->", r["title"][:60], "|", r["url"][:70])
    print("[self-check] browser search OK -", len(res), "results with real URLs")
