"""The owner's standing preferences for HOW things get done: which app or site to use for which job.

"always use WhatsApp Web", "use Spotify for music", "open notes in Notepad", "from now on use Chrome Profile 9 for calendar".
Stored as owner facts `prefers_app_<job>` (the same `prefers_*` pattern llm.style() already injects and Learning > About you
already lists and lets the owner edit), so one place holds them and the owner can see and change every one.
Skills ask `app_for(job)` before choosing how to act; ISHTI also counts what the owner actually opens (`note_use`) and says so
once a clear habit shows (`habit(job)`), but it never switches on its own - only an explicit statement sets a preference.
"""
import re

PREFIX = "prefers_app_"
# "always use X for Y" / "use X for Y from now on" / "I prefer X for Y" / "open Y in X"
_USE_FOR = re.compile(r"^\s*(?:please\s+)?(?:(?:always|from\s+now\s+on|only)\s+)?(?:use|prefer|pick|choose)\s+(?P<app>.{2,40}?)\s+(?:for|to\s+open|when\s+i\s+say)\s+"
                      r"(?P<job>.{2,40}?)(?:\s+(?:always|from\s+now\s+on|from\s+now))?\s*[.!]?$", re.I)
_I_PREFER = re.compile(r"^\s*i\s+(?:always\s+)?(?:prefer|like|want)\s+(?:to\s+use\s+)?(?P<app>.{2,40}?)\s+for\s+(?P<job>.{2,40}?)\s*[.!]?$", re.I)
# only with "always"/"from now on": a plain "open youtube in brave" is a command to do it now, not a standing preference
_OPEN_IN = re.compile(r"^\s*(?:(?P<always>always)\s+)?open\s+(?P<job>.{2,30}?)\s+(?:in|with)\s+(?P<app>.{2,40}?)(?:\s+(?P<always2>always|from\s+now\s+on))?\s*[.!]?$", re.I)
# whatsapp is its own case: "always use whatsapp web", "check the whatsapp web always when i say open whatsapp", "use whatsapp desktop"
_WHATSAPP = re.compile(r"\b(?:use|check|open|prefer|want|go\s+to|always)\b.{0,40}\bwh?a+t*s*\s?a+p+\s+(?P<kind>web|desktop|app)\b|"
                       r"\bwh?a+t*s*\s?a+p+\s+(?P<kind2>web|desktop)\b.{0,30}\b(?:always|only|every\s+time|from\s+now\s+on)\b", re.I)


def _slug(job):
    return re.sub(r"[^a-z0-9]+", "_", re.sub(r"^(?:my|the|a)\s+", "", job.strip().lower())).strip("_")[:30]


def _facts():
    from ishti.facts import get_facts
    return get_facts()


def app_for(job, default=None):
    """The app/site the owner chose for this job ("music", "whatsapp", "browser", "notes"...), else `default`."""
    try:
        return _facts().get("owner").get(PREFIX + _slug(job)) or default
    except Exception:
        return default


def set_app(job, app):
    _facts().set("owner", PREFIX + _slug(job), app.strip())


def whatsapp_mode():
    """"web" (WhatsApp Web in Chrome Profile 9: the default, because ISHTI can read and send there) or "desktop"."""
    v = (app_for("whatsapp") or "web").lower()
    return "desktop" if v in ("desktop", "app") else "web"


def statement(text):
    """If `text` states a preference, store it and return ISHTI's one-line confirmation; else None."""
    t = text.strip()
    # a plain "open X" is an action to do it NOW, not a standing preference - only "open whatsapp
    # web always" / "from now on" states one (checklist: "open my whatsapp web" must open it)
    if re.match(r"^\s*(?:please\s+)?open\b", t, re.I) and not re.search(
            r"\b(?:always|from\s+now\s+on|only|every\s+time)\b", t, re.I):
        return None
    m = _WHATSAPP.search(t)
    if m and not re.search(r"\b(?:send|message|text|write|tell)\b", t, re.I):
        kind = (m.group("kind") or m.group("kind2") or "web").lower()
        mode = "desktop" if kind in ("desktop", "app") else "web"
        set_app("whatsapp", mode)
        return (f"Got it: whenever you ask for WhatsApp I will use WhatsApp {'Desktop' if mode == 'desktop' else 'Web (in Chrome Profile 9)'} "
                "and never open the other one. You can change this in Learning > About you.")
    for rx in (_USE_FOR, _I_PREFER, _OPEN_IN):
        m = rx.match(t)
        if m and rx is _OPEN_IN and not (m["always"] or m["always2"]):
            continue
        if m:
            app, job = m.group("app").strip(" .,"), m.group("job").strip(" .,")
            if (re.match(r"(?:the|a|an|this|that|it|them|him|her|some|any)\b", app, re.I) or len(app.split()) > 4 or not _slug(job)
                    or re.search(r"\b(?:file|folder|weather|time|result|report)\b", job, re.I)):
                return None  # "use the first result for the report" is not a standing preference
            set_app(job, app)
            return f"Got it: for {job} I will use {app}. You can see and change this in Learning > About you."
    return None


# ---- what the owner actually opens: a habit is reported, never acted on silently
def note_use(job, app):
    try:
        f, key = _facts(), f"usage_{_slug(job)}_{_slug(app)}"
        n = int(f.get("owner").get(key) or 0) + 1
        f.set("owner", key, str(n))
        return n
    except Exception:
        return 0


def habit(job, min_uses=4):
    """(app, uses) when one app clearly dominates what the owner opens for this job and no preference is set; else None."""
    try:
        if app_for(job):
            return None
        owner = _facts().get("owner")
        counts = {k.split("_", 2)[2]: int(v) for k, v in owner.items() if k.startswith(f"usage_{_slug(job)}_")}
        if counts:
            app, n = max(counts.items(), key=lambda kv: kv[1])
            if n >= min_uses and n >= 2 * sum(v for k, v in counts.items() if k != app):
                return app, n
    except Exception:
        return None
    return None


if __name__ == "__main__":  # self-check with a stub facts store
    store = {}

    class F:
        def get(self, e): return dict(store)
        def set(self, e, a, v): store[a] = v
    _facts = lambda: F()  # noqa: E731
    assert whatsapp_mode() == "web"
    assert "WhatsApp Web" in statement("check the whatsapp web always when i say to open whatsap")
    assert whatsapp_mode() == "web"
    assert "Desktop" in statement("always use whatsapp desktop") and whatsapp_mode() == "desktop"
    assert statement("send hi to ali on whatsapp web") is None                                    # an action, not a preference
    assert "Spotify" in statement("always use Spotify for music") and app_for("my music") == "Spotify"
    assert "Notepad" in statement("always open notes in Notepad") and app_for("notes") == "Notepad"
    assert statement("open youtube in brave") is None                                              # a command, not a preference
    assert statement("use the first result for the report") is None
    for _ in range(5):
        note_use("browser", "Chrome")
    note_use("browser", "Edge")
    assert habit("browser") == ("chrome", 5)
    set_app("browser", "Chrome")
    assert habit("browser") is None
    print("prefs OK")
