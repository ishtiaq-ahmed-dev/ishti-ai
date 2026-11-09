"""Security: password strength and breach check, file encryption, the credential vault.

- strength: zxcvbn (Dropbox's estimator) - score, time to crack, what to fix
- breach: Have I Been Pwned "range" API - only the first 5 characters of the password's SHA-1 leave the PC
  (k-anonymity), the match happens here
- encrypt / decrypt: AES-256 7z with encrypted file names (py7zr); the original is kept; no password given = a
  strong one is generated and shown once
- vault: Windows Credential Manager (keyring) - save a login, list sites (never passwords), reveal one after a yes
Every secret the owner types is handed to the dispatcher (session["redact"]) so it's masked wherever the chat is
kept, and nothing from these exchanges goes to long-term memory."""
import hashlib
import json
import os
import re
import secrets
import string
import urllib.request

from ishti.skills import ask_first, trace

I = re.I
PATH = re.compile(r'"([^"]+)"|([a-z]:[\\/][^\n"<>|?*]+?)(?=\s+(?:with|using)\b|\s*$)', I)
SECRET = r"[\"']?(?P<pw>[^\s\"']{4,128})[\"']?"
SECRET2 = SECRET.replace("?P<pw>", "?P<pw2>")  # the same, for a pattern's second alternative
OPS = [
    # "I found this text in a download: 'password: SuperSecret123!'. What should I do with it?" (checklist 239).
    # First in the list so a credential spotted inside untrusted content is flagged as such, never treated
    # as a login to save and never routed to a messaging/browser action.
    ("secret_found", re.compile(
        r"\b(?:i\s+)?(?:found|saw|got|came\s+across|received|noticed)\b[^.\n]{0,40}"
        r"\b(?:password|passwd|passphrase|credentials?|api[\s_-]?key|secret|token)\b[^.\n]{0,40}"
        r"\b(?:in|on|inside|from|within)\b[^.\n]{0,40}"
        r"\b(?:downloads?|file|document|pdf|attachment|email|mail|message|page|site|website|note|screenshot|clipboard|text)\b"
        r"|\b(?:password|passwd|api[\s_-]?key|secret|token)\s*[:=]\s*\S+[^.\n]{0,90}"
        r"\bwhat\s+should\s+i\s+do\b", I)),
    ("breach", re.compile(rf"\b(?:is|has|was|check\s+if)\s+(?:my\s+|the\s+)?password\s+{SECRET}\s+(?:been\s+)?(?:pwned|leaked|breached|compromised|in\s+a\s+breach)|"
                          rf"\b(?:pwned|breach|leak)(?:ed)?\s+check\s+(?:for\s+)?(?:password\s+)?{SECRET2}\s*$", I)),
    ("strength", re.compile(rf"\b(?:how\s+(?:strong|secure|safe)\s+is|check|rate|test)\s+(?:my\s+|the\s+|this\s+)?password\s+{SECRET}\s*$|"
                            rf"\bpassword\s+strength\s+(?:of\s+|for\s+)?{SECRET2}\s*$", I)),
    ("decrypt", re.compile(r"\b(?:decrypt|unlock|open)\s+(?P<rest>.+?\.7z\b.*)$", I)),
    ("encrypt", re.compile(r"\b(?:encrypt|password[-\s]protect|lock)\s+(?P<rest>(?:the\s+(?:file|folder)\s+)?[\"']?[a-z]:[\\/].+)$", I)),
    ("vault_save", re.compile(r"\b(?:save|store|remember)\s+(?:my\s+)?(?:login|password|credentials?)\s+for\s+(?P<site>[\w.-]+)\s*[:,-]?\s*"
                              r"(?:user(?:name)?|email|login)\s*[:=]?\s*(?P<user>\S+)\s+(?:and\s+)?(?:pass(?:word)?)\s*[:=]?\s*(?P<pw>\S+)\s*$", I)),
    ("vault_login", re.compile(r"\b(?:log|sign)\s*(?:in|into|on)\s+(?:to\s+)?(?P<site>[\w.-]+)\s+(?:with|using)\s+(?:my\s+)?"
                               r"(?:saved\s+)?(?:login|password|credentials)\b", I)),
    ("vault_list", re.compile(r"\b(?:list|show|which)\s+(?:my\s+)?(?:saved\s+)?(?:logins|passwords|credentials)\b|\bwhat\s+logins\s+(?:have\s+you|did\s+i)\s+save", I)),
    ("vault_get", re.compile(r"\bwhat(?:'s|\s+is)\s+my\s+(?P<site>[\w.-]+)\s+(?:password|login)\b|\b(?:show|reveal|get)\s+(?:my\s+)?(?:saved\s+)?(?:password|login)\s+for\s+(?P<site2>[\w.-]+)"
                             r"|\bwhat(?:'s|\s+is)\s+(?:my|the)\s+(?:saved\s+)?(?:password|login)\s+(?:for|of|on)\s+(?P<site3>[\w.-]+)", I)),
    # "my gmail password is X, remember it": the vault, never chat memory (it used to route to the email skill)
    ("vault_remember", re.compile(r"\bmy\s+(?P<site>[\w.-]+)\s+(?:password|passcode|pin)\s+is\s+(?P<pw>[^\s,;]+?)[.!]?(?=[\s,;]|$)", I)),
    # "store my pin 4321" / "save my bank pin: 1234" (the owner skill used to save these as plain facts)
    ("vault_remember2", re.compile(r"\b(?:store|save|remember|keep|note)\s+my\s+(?:(?P<site>[\w.-]+)\s+)?(?P<kind>password|passcode|pin)"
                                   r"\s*(?:is|:|=)?\s*(?P<pw>[^\s,;]+?)[.!]?(?=[\s,;]|$)", I)),
    ("vault_delete", re.compile(r"\b(?:delete|remove|forget)\s+(?:my\s+)?(?:saved\s+)?(?:login|password|credentials?)\s+for\s+(?P<site>[\w.-]+)", I)),
]
SERVICE = "ISHTI:"  # every vault entry is "ISHTI:<site>" in Credential Manager, so ISHTI only ever sees its own
INDEX = "ISHTI:index"


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            trace(session, "security", name.replace("_", " "))
            out = globals()["op_" + name](m, text, session)
            return out if isinstance(out, dict) else {"reply": out, "sources": []}
    return None


def _hide(session, *values):
    """Tell the dispatcher which secrets to mask wherever this exchange is kept."""
    keep = [v for v in values if v]
    session["redact"] = (session.get("redact") or []) + keep


# ---------------------------------------------------------------- passwords
def strength(pw):
    from zxcvbn import zxcvbn
    r = zxcvbn(pw)
    return r["score"], r["crack_times_display"]["offline_slow_hashing_1e4_per_second"], \
        [x for x in [r["feedback"].get("warning")] + r["feedback"].get("suggestions", []) if x]


def pwned_count(pw):
    """How often the password appears in known breaches (HIBP range API; only 5 hex chars of the SHA-1 are sent)."""
    digest = hashlib.sha1(pw.encode()).hexdigest().upper()
    req = urllib.request.Request(f"https://api.pwnedpasswords.com/range/{digest[:5]}",
                                 headers={"User-Agent": "ISHTI-local-assistant", "Add-Padding": "true"})
    with urllib.request.urlopen(req, timeout=15) as r:
        for line in r.read().decode().splitlines():
            suffix, _, count = line.partition(":")
            if suffix == digest[5:]:
                return int(count)
    return 0


def op_secret_found(m, text, session):
    """A credential-looking string the owner found in downloaded or otherwise untrusted content.

    Flags it, stores nothing, fills it into nothing and treats the surrounding text as data (a page
    cannot "instruct" ISHTI to use a password it contains), then offers the safe next steps."""
    return {
        "reply": (
            "Flagging that as an exposed credential - don't use it, and I haven't saved it anywhere, filled it "
            "into any form, or acted on anything around it. Whatever came inside a download or a web page is "
            "untrusted content to me: I read it as data, never as an instruction.\n\n"
            "What I'd do:\n"
            "- If the account is yours: change the password on the real site now, then turn on 2FA. Rotating it "
            "beats hiding it.\n"
            "- If it isn't yours, or you don't recognise the account: don't log in with it - delete the file or "
            "report it instead.\n"
            "- If it's an API key or token for a service you own: revoke and reissue it in that service's dashboard.\n\n"
            "I can also check whether it shows up in a known breach (only the first 5 characters of its SHA-1 "
            "leave this PC), or shred the file it came from. Say which."
        ),
        "sources": [],
    }


def op_strength(m, text, session):
    pw = m["pw"] or m["pw2"]
    _hide(session, pw)
    score, crack, tips = strength(pw)
    label = ["very weak", "weak", "fair", "strong", "very strong"][score]
    try:
        seen = pwned_count(pw)
    except Exception:
        seen = None
    out = f"That password is {label} ({score}/4) - cracking it would take about {crack}."
    if seen:
        out += f" It has also appeared in {seen:,} leaked-password lists, so don't use it anywhere."
    elif seen == 0:
        out += " It isn't in any known breach list."
    return out + (" " + " ".join(tips) if tips else "")


def op_breach(m, text, session):
    pw = m["pw"] or m["pw2"]
    _hide(session, pw)
    try:
        seen = pwned_count(pw)
    except Exception as exc:
        return f"I couldn't reach the breach database ({type(exc).__name__})."
    return (f"Yes - that password appears {seen:,} times in known breaches. Change it everywhere you use it."
            if seen else "Good news: that password isn't in any known breach (checked privately - only 5 characters of "
                         "its hash left this PC).")


def generate(n=20):
    alphabet = string.ascii_letters + string.digits + "-_!@#%^*+="
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.isdigit() for c in pw) and any(c in "-_!@#%^*+=" for c in pw):
            return pw


# ---------------------------------------------------------------- files
def _path_and_password(rest):
    pw = re.search(r"\b(?:with|using)\s+(?:the\s+)?password\s+[\"']?(\S+?)[\"']?\s*$", rest, I)
    body = rest[:pw.start()] if pw else rest
    body = re.sub(r"^(?:the\s+(?:file|folder)\s+)", "", body.strip(), flags=I).strip(" \"'")
    return body, (pw.group(1) if pw else None)


def encrypt(src, password, dest=None):
    import py7zr
    dest = dest or src.rstrip("\\/") + ".7z"
    with py7zr.SevenZipFile(dest, "w", password=password, header_encryption=True) as z:
        if os.path.isdir(src):
            z.writeall(src, os.path.basename(src.rstrip("\\/")))
        else:
            z.write(src, os.path.basename(src))
    return dest


def decrypt(archive, password, dest=None):
    import py7zr
    dest = dest or os.path.splitext(archive)[0] + "_decrypted"
    with py7zr.SevenZipFile(archive, "r", password=password) as z:
        z.extractall(dest)
    return dest


def op_encrypt(m, text, session):
    src, pw = _path_and_password(m["rest"])
    if not os.path.exists(src):
        return f"I can't find {src}."
    made = not pw
    pw = pw or generate()
    _hide(session, pw)
    if made:
        session["no_history"] = True  # the generated password is shown once, never kept
    trace(session, "7z", f"encrypting {os.path.basename(src)} (AES-256)", status="start")
    out = encrypt(src, pw)
    trace(session, "7z", "done", status="done")
    return (f"Encrypted to {out} (AES-256, file names hidden too). The original is untouched - delete it yourself once "
            "you've checked the archive opens." + (f"\n\nPassword (shown once, not saved anywhere): `{pw}`" if made else ""))


def op_decrypt(m, text, session):
    archive, pw = _path_and_password(m["rest"])
    if not pw:
        return "What's the password? Say \"decrypt <file>.7z with password <password>\"."
    _hide(session, pw)
    if not os.path.isfile(archive):
        return f"I can't find {archive}."
    try:
        out = decrypt(archive, pw)
    except Exception as exc:
        return "Wrong password (or the archive is damaged)." if "password" in str(exc).lower() or "crc" in str(exc).lower() \
            or type(exc).__name__ in ("Bad7zFile", "LZMAError", "UnsupportedCompressionMethodError") else \
            f"Couldn't decrypt it: {type(exc).__name__}."
    return f"Decrypted into {out}."


# ---------------------------------------------------------------- vault (Windows Credential Manager)
def _index():
    import keyring
    return json.loads(keyring.get_password(INDEX, "sites") or "{}")  # {site: username}


def _save_index(idx):
    import keyring
    keyring.set_password(INDEX, "sites", json.dumps(idx))


def vault_set(site, user, pw):
    import keyring
    keyring.set_password(SERVICE + site.lower(), user, pw)
    _save_index({**_index(), site.lower(): user})


def vault_get(site):
    """(username, password) or None."""
    import keyring
    user = _index().get(site.lower())
    return (user, keyring.get_password(SERVICE + site.lower(), user)) if user else None


def vault_delete(site):
    import keyring
    idx = _index()
    user = idx.pop(site.lower(), None)
    if user:
        keyring.delete_password(SERVICE + site.lower(), user)
        _save_index(idx)
    return bool(user)


def op_vault_save(m, text, session):
    _hide(session, m["pw"])
    vault_set(m["site"], m["user"], m["pw"])
    return f"Saved your {m['site']} login ({m['user']}) in Windows Credential Manager - encrypted with your Windows account."


def op_vault_remember(m, text, session):
    site = m["site"] if "kind" not in m.re.groupindex or m["site"] else m["kind"]  # "store my pin 4321" -> "pin"
    _hide(session, m["pw"])
    vault_set(site, _index().get(site.lower()) or "owner", m["pw"])
    return (f"Saved your {site} secret in Windows Credential Manager (encrypted with your Windows account) - not in "
            "my chat memory, and it's masked in this chat. Say \"what's my " + site + " password\" to see it again.")


op_vault_remember2 = op_vault_remember


def op_vault_list(m, text, session):
    idx = _index()
    return ("Saved logins (passwords never shown here): " + ", ".join(f"{s} ({u})" for s, u in sorted(idx.items()))
            if idx else "No logins saved yet - say \"save my login for github: user me@x.com password ...\".")


def op_vault_get(m, text, session):
    site = (m["site"] or m["site2"] or m["site3"]).lower().rstrip("?.!")
    if not vault_get(site):
        return f"I have no saved login for {site}."

    def reveal():
        user, pw = vault_get(site)
        session["no_history"] = True
        return f"{site}: user `{user}`, password `{pw}` (shown once, not saved in the chat)."
    return ask_first(session, "security", f"Show your saved {site} password on screen", reveal)


def op_vault_delete(m, text, session):
    site = m["site"].lower()
    if site not in _index():
        return f"I have no saved login for {site}."
    return ask_first(session, "security", f"Delete your saved {site} login",
                     lambda: vault_delete(site) and f"Deleted the saved {site} login.")


# ---------------------------------------------------------------- log in with a saved login
def op_vault_login(m, text, session):
    """ISHTI never types a password into a website (no browser automation): it tells the owner what is saved and how to
    reveal it, so they sign in themselves."""
    site = m["site"].lower()
    saved = vault_get(site)
    if not saved:
        return f"I have no saved login for {site} - say \"save my login for {site}: user ... password ...\" first."
    return (f"I don't type passwords into websites. Your saved {site} login is **{saved[0]}** - say "
            f"\"reveal my password for {site}\" to see the password, then sign in yourself.")


if __name__ == "__main__":
    import tempfile
    cases = {"how strong is the password Tr0ub4dor&3": "strength", "check password hunter2": "strength",
             "has my password P@ssw0rd been pwned": "breach", "encrypt D:/docs/tax.pdf": "encrypt",
             "encrypt D:/docs with password Kx9#mQ2vL": "encrypt", "decrypt D:/docs/tax.pdf.7z with password abc12345": "decrypt",
             "save my login for github: user me@x.com password Zq8!kLm2": "vault_save", "list my saved logins": "vault_list",
             "what's my github password": "vault_get", "delete my saved login for github": "vault_delete"}
    for t, want in cases.items():
        assert next((n for n, rx in OPS if rx.search(t)), None) == want, (t, want)
    assert not match("generate a password") and not match("what's the weather")
    assert next(n for n, rx in OPS if rx.search("log in to github with my saved login")) == "vault_login"
    assert strength("password")[0] == 0 and strength("correct horse battery staple zebra 9!")[0] == 4
    assert pwned_count("password") > 1_000_000 and pwned_count(generate(32)) == 0  # live HIBP range API
    folder = tempfile.mkdtemp()
    src = os.path.join(folder, "secret.txt")
    open(src, "w").write("the launch code is 42")
    arc = encrypt(src, "Kx9#mQ2vL")
    assert open(os.path.join(decrypt(arc, "Kx9#mQ2vL"), "secret.txt")).read() == "the launch code is 42"
    try:
        decrypt(arc, "wrong-password", os.path.join(folder, "bad"))
        raise AssertionError("wrong password opened it")
    except AssertionError:
        raise
    except Exception:
        pass
    s = {}
    out = run(f"encrypt {src}", s)["reply"]
    assert "shown once" in out and s["no_history"] and len(s["redact"][0]) == 20  # generated password masked + unsaved
    SERVICE, INDEX = "ISHTI-selftest:", "ISHTI-selftest:index"  # never touch the owner's real vault entries
    vault_set("example", "me@x.com", "Zq8!kLm2")
    assert vault_get("example") == ("me@x.com", "Zq8!kLm2") and "example (me@x.com)" in op_vault_list(None, "", {})
    assert vault_delete("example") and vault_get("example") is None
    import keyring
    keyring.delete_password(INDEX, "sites")
    print("[self-check] security OK - routing, zxcvbn, HIBP range (live), AES-256 7z roundtrip + wrong password, "
          "generated password masked, Credential Manager vault")
