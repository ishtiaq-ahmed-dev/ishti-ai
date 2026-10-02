"""Which languages ISHTI may use - the owner's switches in Settings -> Language.

English is always on. Urdu and Pashto are OFF until the owner switches them on; "Urdu" includes Roman Urdu (Urdu written in English
letters). With a language off, ISHTI neither listens for it (speech recognition is limited to the enabled languages), nor translates
to or from it, nor lets the language lock ("talk to me in Urdu") take effect, and its system prompt says "reply only in English".
The switches are stored as the owner fact `owner.enabled_languages` (a comma list, e.g. "urdu,pashto"), so they survive restarts.
"""
OPTIONAL = ("urdu", "pashto")
NAMES = {"english": "English", "urdu": "Urdu", "pashto": "Pashto", "roman urdu": "Roman Urdu"}
CODES = {"english": "en", "urdu": "ur", "pashto": "ps"}


def _facts():
    from ishti.facts import get_facts
    return get_facts()


def enabled():
    """The languages in use, English first: {"english"} or e.g. {"english", "urdu"}."""
    try:
        raw = (_facts().get("owner").get("enabled_languages") or "").lower()
    except Exception:
        raw = ""
    return ["english"] + [l for l in OPTIONAL if l in {x.strip() for x in raw.split(",")}]


def allowed(lang):
    """Is this language (any spelling: "Urdu", "roman urdu", "ur") switched on? English always is."""
    name = (lang or "english").strip().lower().split(" (")[0]
    if name in ("ur", "roman urdu"):
        name = "urdu"
    if name == "ps":
        name = "pashto"
    return name == "english" or name in enabled()


def set_enabled(urdu=None, pashto=None):
    """Save the switches (None = leave as is). Also fixes what depended on them: a language lock on a language that is now
    off is dropped, and the speech recogniser's language set follows. -> the new enabled list."""
    now = set(enabled())
    for name, flag in (("urdu", urdu), ("pashto", pashto)):
        if flag is True:
            now.add(name)
        elif flag is False:
            now.discard(name)
    f = _facts()
    keep = [l for l in OPTIONAL if l in now]
    if keep:
        f.set("owner", "enabled_languages", ",".join(keep))
    else:
        f.forget("owner", "enabled_languages")
    lock = (f.get("owner").get("prefers_language") or "").strip()
    if lock and not allowed(lock):
        f.forget("owner", "prefers_language")
    try:
        from ishti import voice
        voice.apply_languages()
    except Exception:
        pass
    return enabled()


def state():
    on = set(enabled())
    return {"languages": [{"id": "english", "name": "English", "enabled": True, "locked": True},
                          {"id": "urdu", "name": "Urdu", "note": "also Roman Urdu (Urdu in English letters)", "enabled": "urdu" in on, "locked": False},
                          {"id": "pashto", "name": "Pashto", "enabled": "pashto" in on, "locked": False}]}


def style_rule():
    """The language line of the system prompt (ishti.llm.STYLE rule 6)."""
    on = enabled()
    if on == ["english"]:
        return "Language: Reply only in English."
    names = [NAMES[l] for l in on]
    return (f"Language: Reply only in {', '.join(names[:-1])} or {names[-1]} - whichever the owner used"
            + (" (Urdu script in Urdu script, Roman Urdu in Roman Urdu)" if "urdu" in on else "") + ". Never answer in any other language.")


if __name__ == "__main__":      # self-check (no files: facts are stubbed)
    store = {}

    class F:
        def get(self, k): return dict(store)
        def set(self, e, a, v): store[a] = v
        def forget(self, e, a): store.pop(a, None)
    _facts = lambda: F()          # noqa: E731
    assert enabled() == ["english"] and not allowed("Urdu") and allowed("English") and allowed("english (default)")
    assert style_rule() == "Language: Reply only in English."
    store["prefers_language"] = "Urdu"
    assert set_enabled(urdu=True) == ["english", "urdu"] and allowed("Roman Urdu") and allowed("ur") and not allowed("Pashto")
    assert "English or Urdu" in style_rule() and store.get("prefers_language") == "Urdu"
    assert set_enabled(urdu=False) == ["english"] and "prefers_language" not in store      # the lock on a switched-off language is dropped
    assert set_enabled(urdu=True, pashto=True) == ["english", "urdu", "pashto"] and "English, Urdu or Pashto" in style_rule()
    print("languages OK")
