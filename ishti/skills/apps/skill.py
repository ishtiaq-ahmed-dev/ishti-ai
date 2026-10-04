"""Apps skill: launch or focus desktop applications - anything installed (found through the Start menu, the way Windows lists
apps), open a site or a search in a named browser ("open ssr movies in brave and search for tenet"), and say whether an app is
installed ("can you see my brave app?")."""
import re
from urllib.parse import quote_plus

from orchestrator.intent_detector import IntentDetector

VERB = re.compile(r"^\s*(?:please\s+)?(?:open|launch|start|run|switch\s+to|focus)\s+(?:the\s+|my\s+)?(?P<app>[a-z0-9 _.+-]+?)(?:\s+app)?\s*[.!]?$", re.I)
MULTI_STEP = re.compile(r"\b(?:and|then|after|turn|switch|set|enable|disable|change|click|type|press|search|go)\b", re.I)  # "open settings and turn on dark mode" is a task for screen control, not an app name
MESSAGING_VERBS = re.compile(r"\b(?:send|message\s+to|write\s+to|text\s+to|reply)\b", re.I)
WHATSAPP = re.compile(r"\bwh?a+t*s*\s?a+p+\b|\bwhastap\b|\bwatsapp\b", re.I)  # the whatsapp skill opens it: one window, the owner's preferred kind
BROWSERS = r"bra[vw]e|chrome|google\s+chrome|edge|microsoft\s+edge|firefox|opera"
# "open ssr movies in brave browser and search for tenet movie" / "search python tutorials in brave" / "open youtube.com in edge"
IN_BROWSER = re.compile(r"^\s*(?:please\s+|can\s+you\s+)?(?:open|go\s+to|visit|search(?:\s+for)?|look\s+up)\s+(?P<what>.+?)\s+(?:in|on|with|using)\s+"
                        r"(?:the\s+|my\s+)?(?P<browser>" + BROWSERS + r")(?:\s+browser)?(?:\s*(?:,|and)\s*(?:then\s+)?search\s+(?:for\s+)?(?P<q>.+?))?\s*[.!?]?$", re.I)
# "can you see my brave app in pc?" / "do I have spotify installed?" / "is discord installed"
HAVE = re.compile(r"^\s*(?:can\s+you\s+(?:see|find)|do\s+i\s+have|is|have\s+i\s+got)\s+(?:my\s+|the\s+)?(?P<app>[\w .+-]{2,30}?)\s*"
                  r"(?:app|application|browser)?\s*(?:installed|on\s+(?:my\s+|this\s+)?(?:pc|computer|laptop)|in\s+(?:my\s+|this\s+)?(?:pc|computer))?\s*\??$", re.I)
SITES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
         "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com", "twitter": "https://x.com", "x": "https://x.com",
         "github": "https://github.com", "linkedin": "https://www.linkedin.com", "netflix": "https://www.netflix.com", "outlook": "https://outlook.live.com/mail/",
         "whatsapp": "https://web.whatsapp.com", "chatgpt": "https://chatgpt.com", "wikipedia": "https://www.wikipedia.org",
         "binance": "https://www.binance.com", "daraz": "https://www.daraz.pk", "amazon": "https://www.amazon.com"}
SITE_SEARCH = {"youtube": "https://www.youtube.com/results?search_query={}", "google": "https://www.google.com/search?q={}",
               "github": "https://github.com/search?q={}", "wikipedia": "https://en.wikipedia.org/w/index.php?search={}",
               "amazon": "https://www.amazon.com/s?k={}", "daraz": "https://www.daraz.pk/catalog/?q={}"}
DOMAIN = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?$", re.I)
# "play the song thaam lo toty na dhagy for me" / "play some music" -> a YouTube search in the owner's browser.
# Never a local file (that is the media skill's) and never a bare "play it" (nothing to search for).
PLAY = re.compile(r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:play|put\s+on)\s+(?:me\s+)?(?:the\s+)?"
                  r"(?:song|music|track|video|singer|band|playlist)?\s*(?P<what>.+?)\s*(?:for\s+me|please)?\s*[.!?]?$", re.I)
NO_PLAY = re.compile(r"[A-Za-z]:[\\/]|\.(?:mp3|wav|m4a|ogg|opus|flac|mp4|mkv|webm|avi)\b", re.I)
PRONOUN_PLAY = {"it", "that", "this", "them", "these", "those", "it again", "that again", "the same"}


def _installed(name):
    from desktop.computer_use import find_installed
    return find_installed(name)


def _have(text):
    m = HAVE.match(text)
    if not m or not re.search(r"\b(?:app|application|browser|installed|pc|computer|laptop)\b", text, re.I):
        return None
    return m


def _play_words(text):
    """The thing to play, or None when this is not a "play <song>" request (a file, a pronoun, an app)."""
    if NO_PLAY.search(text) or VERB.match(text):
        return None
    # A video to play is the video skill's job (it opens the real local file and names it), not a YouTube
    # search - "Play my latest Obscura video" must not be turned into a web search (checklist #171).
    if re.search(r"\b(?:video|clip|footage|movie)\b", text, re.I):
        return None
    m = PLAY.match(text)
    if not m:
        return None
    what = (m.group("what") or "").strip(" .,!?")
    if not what or what.lower() in PRONOUN_PLAY or len(what.split()) > 12:
        return None
    # "play the song X" is unambiguous; a bare "play X" needs a music word or a real multi-word title
    if not re.search(r"\b(?:song|music|track|singer|band|playlist)\b", text, re.I) and len(what.split()) < 2:
        return None
    return what


def match(text):
    if _play_words(text):
        return True
    if WHATSAPP.search(text):
        return False
    if IN_BROWSER.match(text) or _have(text):
        return True
    if re.search(r"\b(?:memory|facts?|profile|reminders?|notes?)\b", text, re.I):
        return False
    if MESSAGING_VERBS.search(text):
        return False
    m = VERB.match(text)
    if not m or MULTI_STEP.search(m.group("app")):
        return False
    return (IntentDetector.classify(re.sub(r"\b(?:the|a|an|my)\s+", "", text, flags=re.I))["mode"] == "APP_CONTROL"
            or bool(_installed(m.group("app"))))


def browser_url(what, q=None):
    """A site name or address -> that site; anything else -> a web search (with the extra search words, if any)."""
    what = what.strip(" .")
    site = SITES.get(what.lower())
    if site and not q:
        return site
    if site and q:  # "open youtube in brave and search for tenet" -> search inside that site where it has a search URL
        return SITE_SEARCH.get(what.lower(), "https://search.brave.com/search?q=" + quote_plus(f"{what} {{}}")).replace("{}", quote_plus(q))
    if DOMAIN.match(what) and not q:
        return what if what.lower().startswith("http") else "https://" + what
    return "https://search.brave.com/search?q=" + quote_plus(" ".join(x for x in (what, q) if x))


def run(text, session):
    if not _have(text) and not _play_words(text):
        from ishti.connectors import try_request
        result = try_request(text, session)
        if result is not None:
            return result
        m = VERB.match(text)
        if m and m["app"].lower().strip() in SITES and not WHATSAPP.search(text):
            from ishti.skills.browser import skill as browser
            return browser.run(text, session)
    from desktop.computer_use import get_computer_use_agent
    agent = get_computer_use_agent()
    if what := _play_words(text):
        from urllib.parse import quote_plus
        url = "https://www.youtube.com/results?search_query=" + quote_plus(what)
        import os
        from ishti import test_mode
        if test_mode.enabled():
            return {"reply": f"(test mode) Would play \"{what}\" on YouTube.", "sources": []}
        os.startfile(url)
        return {"reply": f"Playing \"{what}\" - opened the YouTube results for it in your browser.",
                "sources": [{"title": "YouTube", "url": url}], "verified": True}
    if m := IN_BROWSER.match(text):
        name = re.sub(r"^bra[vw]e$", "brave", m["browser"].lower())
        lnk = _installed(name)
        if not lnk:
            return {"reply": f"I can't find {name.title()} installed on this PC.", "sources": []}
        url = browser_url(m["what"], m["q"])
        import os
        from ishti import test_mode
        if test_mode.enabled():
            return {"reply": f"(test mode) Would open {name.title()} at {url}.", "sources": []}
        os.startfile(lnk, arguments=f'"{url}"')
        return {"reply": f"Opened {name.title()}: {url}", "sources": [], "verified": True}
    if m := _have(text):
        app = m["app"].strip()
        lnk = _installed(app)
        return {"reply": (f"Yes - {app.title()} is installed on this PC. Say \"open {app}\" and I'll start it." if lnk
                          else f"I can't find {app} installed on this PC (it isn't in the Start menu)."), "sources": []}
    if re.search(r"\b(?:memory|facts?|profile|reminders?|notes?)\b", text, re.I) or MESSAGING_VERBS.search(text):
        return None
    m = VERB.match(text)
    if not m or MULTI_STEP.search(m.group("app")):
        return None  # no "open X" in it: not an app request, route it on
    app = (m.group("app") or "").strip()
    if not app:
        return None
    from ishti import prefs
    app = prefs.app_for(app) or app  # "open music" -> the app the owner said to always use for music (Learning > About you)
    prefs.note_use("apps", app)
    if not re.search(r"switch\s+to|focus", text, re.I):
        res = agent.launch_app(app)
        if isinstance(res, dict) and res.get("status") == "success":
            return {"reply": f"Opened {app}.", "sources": []}
    if agent.focus_window(app):
        return {"reply": f"Switched to {app}.", "sources": []}
    return {"reply": f"I couldn't open {app}: it isn't installed (not in the Start menu) or has a different name.", "sources": []}


if __name__ == "__main__":
    assert match("open notepad") and match("open the calculator app")
    assert not match("what is a file")
    m = IN_BROWSER.match("Open ssr movies in brawe browser and search for tenet movie")
    assert m and m["what"] == "ssr movies" and m["q"] == "tenet movie", m and m.groupdict()
    assert browser_url("ssr movies", "tenet movie").endswith("q=ssr+movies+tenet+movie")
    assert browser_url("youtube.com") == "https://youtube.com"
    assert _have("Can you see my brave app in pc ?") and not _have("is it raining")
    assert _play_words("play the song thaam lo toty na dhagy for me") == "thaam lo toty na dhagy"
    assert _play_words("play some music") == "some music"
    assert _play_words("play it") is None and _play_words("play D:\\music\\song.mp3") is None
    assert _play_words("open chrome") is None
    assert match("play the song thaam lo toty na dhagy for me")
    print("[self-check] apps OK - routing, browser targets, installed check, play on YouTube")
