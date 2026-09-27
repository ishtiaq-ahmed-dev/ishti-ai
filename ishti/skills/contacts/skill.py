"""Duplicate contacts, merged with Google Contacts' own "Merge and fix" (it already matches names, numbers and
emails well - no need to re-implement that). Read-only until the owner says yes; then "Merge all" (or each
suggestion's Merge button) is clicked in the owner's Google-signed-in Chrome profile."""
import re
import time

from ishti.skills import ask_first, trace
from ishti.skills.browser import skill as browser

I = re.I
URL = "https://contacts.google.com/suggestions"
TRIGGER = re.compile(r"\b(?:duplicate|duplicated|dupe)\s+contacts?\b|\b(?:merge|dedupe|de-duplicate|clean\s+up|fix)\s+(?:my\s+)?contacts\b|"
                     r"\bcontacts?\s+(?:duplicates|dedupe)\b", I)


def match(text):
    return bool(TRIGGER.search(text))


def state(page_text, snapshot):
    """('none', 0) | ('merge_all', ref) | ('each', [refs]) from the Merge and fix page."""
    if re.search(r"no new suggestions|no suggestions|no duplicates", page_text, I):
        return "none", 0
    all_btn = re.search(r'button "Merge all"[^\n]*\[ref=([^\]]+)\]', snapshot, I)
    if all_btn:
        return "merge_all", all_btn.group(1)
    each = re.findall(r'button "Merge"[^\n]*\[ref=([^\]]+)\]', snapshot, I)
    return ("each", each) if each else ("none", 0)


def count_of(page_text):
    m = re.search(r"(\d+)\s+(?:duplicates?|suggestions?)", page_text, I)
    return int(m.group(1)) if m else None


def run(text, session):
    wanted = browser.profile_for("contacts")
    with browser.LOCK:
        try:
            browser.use_server(wanted, session)
        except RuntimeError as exc:
            return {"reply": str(exc), "sources": []}
        trace(session, "contacts", "opening Google Contacts > Merge and fix", status="start")
        browser._call("browser_navigate", {"url": URL})
        time.sleep(4)
        page = browser._call("browser_evaluate", {"function": "() => (document.querySelector('[role=main]') || document.body).innerText"})
        snap = browser._call("browser_snapshot", {})
        wanted = browser.SERVER  # Profile 10 if Profile 9 hit a login page: the merge clicks go where the page is
    kind, what = state(page, snap)
    trace(session, "contacts", {"none": "no duplicates", "merge_all": "duplicates found", "each": f"{len(what) if isinstance(what, list) else 0} to merge"}[kind],
          status="done")
    if kind == "none":
        return {"reply": "No duplicate contacts - Google Contacts has no merge suggestions right now.", "sources": []}
    n = count_of(page) or (len(what) if isinstance(what, list) else None)

    def merge():
        with browser.LOCK:
            browser.use_server(wanted, session)
            if kind == "merge_all":
                browser._call("browser_click", {"element": "Merge all", "ref": what})
            else:
                for ref in what:
                    browser._call("browser_click", {"element": "Merge", "ref": ref})
                    time.sleep(1)
        return f"Merged {n or 'the'} duplicate contact{'s' if n != 1 else ''} in Google Contacts."
    return ask_first(session, "contacts", f"Google Contacts suggests merging {n or 'some'} duplicate contact(s) - merge them", merge)


if __name__ == "__main__":
    assert match("find duplicate contacts") and match("merge my contacts") and match("dedupe contacts")
    assert not match("send a message to my contacts") and not match("contact Ali")
    assert state("Good work. No new suggestions.", "") == ("none", 0)
    assert state("2 duplicates", '- button "Merge all" [ref=e40]') == ("merge_all", "e40")
    assert state("Duplicates", '- button "Merge" [ref=e1]\n- button "Merge" [ref=e2]') == ("each", ["e1", "e2"])
    assert count_of("You have 3 duplicates") == 3
    print("[self-check] contacts OK - routing, merge page states (none / merge all / each), count")
