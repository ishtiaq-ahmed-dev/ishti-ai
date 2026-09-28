"""Tests for browser-skill tab reuse (issue 6): tabs() parsing of real browser_tabs output, find_tab() matching
(exact/host/domain), open_url() reusing an already-open tab, and the browser_navigate interception. No browser
and no MCP server: the MCP layer is faked. Run:
    $env:PYTHONIOENCODING='utf-8'; D:\\ISHTI\\venv\\Scripts\\python.exe D:\\ISHTI\\tests\\test_browser_tabs.py"""
import sys

sys.path.insert(0, r"D:\ISHTI")

from ishti.skills.browser import skill

# what browser_tabs {"action": "list"} really returned from @playwright/mcp 0.0.82 (live probe 2026-09-27;
# the extension's token value is redacted here, the line shape is verbatim).
LIST = (
    "### Result\n"
    "- 0: (current) [Welcome](chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/connect.html?mcpRelayUrl="
    "ws%3A%2F%2F%5B%3A%3A1%5D%3A51803%2Fextension%2F31b4aaf2-3bb9-4c49-a63b-abcd27b23c54&client=%7B%22name%22"
    "%3A%22mcp%22%7D&protocolVersion=2&token=REDACTED)\n"
    "- 1: [WhatsApp](https://web.whatsapp.com/)\n"
    "- 2: [Gmail](https://mail.google.com/mail/u/0/#inbox)\n"
    "- 3: [Python (programming language) - Wikipedia](https://en.wikipedia.org/wiki/Python_(programming_language))\n"
    "- 4: [Google Gemini](https://gemini.google.com/app)\n"
    "- 5: [](about:blank)\n"
    "- 6: [Gone](https://broken.example/gone) [crashed]\n")
URLS = ["chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/connect.html?mcpRelayUrl=ws%3A%2F%2F%5B%3A%3A1%5D"
        "%3A51803%2Fextension%2F31b4aaf2-3bb9-4c49-a63b-abcd27b23c54&client=%7B%22name%22%3A%22mcp%22%7D"
        "&protocolVersion=2&token=REDACTED",
        "https://web.whatsapp.com/", "https://mail.google.com/mail/u/0/#inbox",
        "https://en.wikipedia.org/wiki/Python_(programming_language)", "https://gemini.google.com/app",
        "about:blank", "https://broken.example/gone"]
SNAP = '- Page URL: https://web.whatsapp.com/\n- generic "Chat list" [ref=e12]'
NAV = '- Page URL: https://example.com/\n- heading "Example Domain" [ref=e3]'


class FakeMCP:
    """Stands in for the module's MCP layer: skill._call records tool calls, get_hub().call answers the tab list."""
    def __init__(self, tab_list, fail_tools=(), fail_list=False):
        self.tab_list, self.fail_tools, self.fail_list, self.calls = tab_list, set(fail_tools), fail_list, []

    def call_tool(self, name, args):  # this is what the monkeypatched skill._call does
        self.calls.append((name, dict(args)))
        if name in self.fail_tools:
            raise RuntimeError(f"{name} is down")
        return NAV if name == "browser_navigate" else SNAP

    def call(self, server, name, args=None, timeout=90):  # MCPHub.call: what _plain_call() and tabs() use
        self.calls.append((name, dict(args or {})))
        if self.fail_list and name == "browser_tabs" and (args or {}).get("action") == "list":
            raise RuntimeError("no browser session")
        if name == "browser_tabs":
            return self.tab_list
        if name == "browser_snapshot":
            return SNAP
        if name == "browser_navigate":
            return NAV
        raise AssertionError(f"hub got {name}")


real_call, real_hub = skill._call, skill.get_hub


def use(fake, patch_call=True):
    skill._call = fake.call_tool if patch_call else real_call
    skill.get_hub = (lambda: fake)


def nav_calls(fake):
    """Only the browser_navigate calls the fake MCP layer saw (tab-list calls are noise here)."""
    return [c for c in fake.calls if c[0] == "browser_navigate"]


# --- tabs(): parse the real list output; extension tabs, about:blank and crashed tabs are tolerated --------
use(FakeMCP(LIST))
assert skill.tabs() == URLS, skill.tabs()
use(FakeMCP("No open tabs. Navigate to a URL to create one.\n"))
assert skill.tabs() == [] and skill.tabs(None) == []
use(FakeMCP(LIST, fail_list=True))
assert skill.tabs() == []  # a dead session is "no tabs", never an exception
# --- find_tab(): pure matching, exact page first, then host, then registrable domain ----------------------
assert skill.find_tab("https://web.whatsapp.com/", URLS) == 1
assert skill.find_tab("http://web.whatsapp.com", URLS) == 1              # scheme and trailing slash don't matter
assert skill.find_tab("https://web.whatsapp.com/?lang=en", URLS) == 1    # same host+path: query doesn't matter
assert skill.find_tab("https://en.wikipedia.org/wiki/Python_(programming_language)", URLS) == 3  # () in the URL
assert skill.find_tab("https://gemini.google.com/app", URLS) == 4
assert skill.find_tab("https://mail.google.com/mail/u/0/#inbox", URLS) == 2      # Gmail's own URL exactly
assert skill.find_tab("https://mail.google.com/mail/u/0/?x=1#inbox", URLS) == 2  # same host+path, state differs
assert skill.find_tab("https://google.com/app", URLS) == 4               # google.com <-> gemini.google.com
assert skill.find_tab("https://www.google.com/app", URLS) == 4
assert skill.find_tab("https://example.com/", URLS) is None              # nothing open for it
assert skill.find_tab("https://google.com/", URLS) is None               # same domain but a different page
assert skill.find_tab("https://gemini.google.com/", URLS) == 4   # Issue 6: reuses open Gemini tab
assert skill.find_tab("https://mail.google.com/", URLS) == 2     # Issue 6: reuses open Gmail tab
assert skill.find_tab("https://example.com/", ["about:blank", "chrome-extension://abc/x.html"]) is None
assert skill.find_tab("https://shop.example.com.pk/page", ["https://www.example.com.pk/page"]) == 0  # 2-part suffix
two = ["https://web.whatsapp.com/?lang=en", "https://web.whatsapp.com/"]
assert skill.find_tab("https://web.whatsapp.com/", two) == 1             # the exact page beats same-host

# --- open_url(): reuses the open tab (select + fresh snapshot), never calls browser_navigate --------------
fake = FakeMCP(LIST)
use(fake)
out = skill.open_url("https://web.whatsapp.com/")
assert ("browser_tabs", {"action": "select", "index": 1}) in fake.calls, fake.calls
assert not any(name == "browser_navigate" for name, _ in fake.calls), fake.calls
assert out.startswith("reused the open web.whatsapp.com tab") and "Chat list" in out, out

# no match: navigate as before
fake = FakeMCP("- 0: [Example](https://example.com/)\n")
use(fake)
out = skill.open_url("https://web.whatsapp.com/")
assert nav_calls(fake) == [("browser_navigate", {"url": "https://web.whatsapp.com/"})], fake.calls
assert out == NAV

# new_tab=True: the caller wants a fresh tab, so navigate even though a matching tab is open
fake = FakeMCP(LIST)
use(fake)
skill.open_url("https://web.whatsapp.com/", new_tab=True)
assert nav_calls(fake) == [("browser_navigate", {"url": "https://web.whatsapp.com/"})], fake.calls

# the tab call dies: fall back to navigate, return its output
fake = FakeMCP(LIST, fail_tools=("browser_tabs",))
use(fake)
out = skill.open_url("https://web.whatsapp.com/")
assert fake.calls[-1] == ("browser_navigate", {"url": "https://web.whatsapp.com/"}), fake.calls
assert out == NAV

# listing the tabs dies: same fallback
fake = FakeMCP(LIST, fail_list=True)
use(fake)
out = skill.open_url("https://web.whatsapp.com/")
assert nav_calls(fake) == [("browser_navigate", {"url": "https://web.whatsapp.com/"})] and out == NAV, fake.calls

# --- the generic path: every browser_navigate reuses a matching tab too -----------------------------------
fake = FakeMCP(LIST)
use(fake, patch_call=False)  # exercise the real skill._call, only the MCP layer is fake
out = skill._call("browser_navigate", {"url": "https://web.whatsapp.com"})
assert out.startswith("reused the open web.whatsapp.com tab"), out
assert not any(name == "browser_navigate" for name, _ in fake.calls), fake.calls
assert ("browser_tabs", {"action": "select", "index": 1}) in fake.calls, fake.calls

# helper keys (new_tab/tab) say "open it fresh" and must never reach the MCP tool (its schema takes only url)
fake = FakeMCP(LIST)
use(fake, patch_call=False)
skill._call("browser_navigate", {"url": "https://web.whatsapp.com", "new_tab": True})
assert fake.calls[-1] == ("browser_navigate", {"url": "https://web.whatsapp.com"}), fake.calls

# a different query is a different page: a search must load, not "reuse" a tab with stale results
fake = FakeMCP("- 0: (current) [cats](https://www.google.com/search?q=cats)\n")
use(fake, patch_call=False)
skill._call("browser_navigate", {"url": "https://www.google.com/search?q=dogs"})
assert fake.calls[-1][0] == "browser_navigate", fake.calls
# ... but the very same query may be reused
fake = FakeMCP("- 0: (current) [cats](https://www.google.com/search?q=cats)\n")
use(fake, patch_call=False)
skill._call("browser_navigate", {"url": "https://www.google.com/search?q=cats"})
assert not any(name == "browser_navigate" for name, _ in fake.calls), fake.calls

# tab listing dies on the generic path too: navigate as before
fake = FakeMCP(LIST, fail_list=True)
use(fake, patch_call=False)
skill._call("browser_navigate", {"url": "https://web.whatsapp.com"})
assert fake.calls[-1][0] == "browser_navigate", fake.calls

# the SYSTEM prompt tells the model loop to reuse, never duplicate, an already-open site
assert "already open in a tab must be reused" in skill.SYSTEM and "never open a duplicate tab" in skill.SYSTEM

skill._call, skill.get_hub = real_call, real_hub  # leave the module as imported
print("[self-check] browser tab reuse OK - parse, exact/host/domain match, reuse over navigate, safe fallbacks")
