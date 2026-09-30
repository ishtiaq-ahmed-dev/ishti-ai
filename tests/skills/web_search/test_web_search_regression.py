"""FIX-3 + FIX-4 regressions (QA prompt 2, web_search batch).

FIX-3: tests/run_skill.py decoded the pytest child's output with the Windows default
codec (cp1252), so any suite printing non-ASCII (web_search's Urdu/Arabic rows) crashed
the HARNESS with UnicodeDecodeError and was recorded red although the suite was green.
The harness must decode child output as UTF-8 with errors="replace".

FIX-4: MultiProviderSearch.search walked the full provider cascade (searxng -> bing ->
wikipedia -> Playwright CHROME LAUNCH -> github) with no overall time budget; with
SearXNG down that measured 40-60 s per attempt, and deep_research repeats it up to 3x -
a chat turn could hang for minutes on a dead search backend. budget_s now bounds the
cascade, and deep_research shares ONE deadline across all its attempts.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_harness_decodes_child_output_as_utf8():
    """FIX-3: the harness must launch its pytest child with an explicit UTF-8 decode, so
    non-ASCII suite output can never crash the harness itself (both call sites)."""
    src = open(os.path.join(ROOT, "tests", "run_skill.py"), encoding="utf-8").read()
    assert 'encoding="utf-8", errors="replace"' in src, \
        "run_skill.py must decode child output as UTF-8 (FIX-3 regression)"
    # the stdout wrapper + the collect-only dry run + the real pytest run
    assert src.count('encoding="utf-8", errors="replace"') >= 2, \
        "both subprocess.run call sites in run_skill.py need the explicit decode"


def _fake_provider(name, calls, ret=None):
    def search(query, max_results=5):
        calls.append(name)
        return [] if ret is None else ret
    return type("P", (), {"search": staticmethod(search)})()


def test_cascade_skips_everything_when_budget_already_spent():
    """FIX-4: search() with an exhausted budget returns [] without calling ANY provider -
    in particular without launching the Playwright browser."""
    from tools.web.search import MultiProviderSearch

    m = MultiProviderSearch.__new__(MultiProviderSearch)   # no real providers built
    calls = []
    for name in ("searxng", "bing", "wikipedia", "playwright", "github", "gh_release"):
        setattr(m, name, _fake_provider(name, calls))

    out = m.search("qwerty layout history", max_results=6,
                   budget_s=-1.0)                          # deadline already in the past
    assert out == []
    assert calls == [], f"providers were called past the budget: {calls}"


def test_cascade_without_budget_reaches_browser_fallback():
    """The budget is opt-in (default None): callers that pass none keep the legacy
    fall-through behaviour - Playwright is still reached when earlier providers return
    nothing (the FIX-4 change must not silently degrade the healthy path)."""
    from tools.web.search import MultiProviderSearch

    m = MultiProviderSearch.__new__(MultiProviderSearch)
    calls = []
    for name in ("searxng", "bing", "wikipedia", "github", "gh_release"):
        setattr(m, name, _fake_provider(name, calls))
    sentinel = object()
    m.playwright = _fake_provider("playwright", calls, ret=[sentinel])

    out = m.search("qwerty layout history")                # no budget_s -> legacy path
    assert calls[-4:] == ["searxng", "bing", "wikipedia", "playwright"], calls
    assert "gh_release" in calls                           # Route 0 still probed first
    assert out == [sentinel]
