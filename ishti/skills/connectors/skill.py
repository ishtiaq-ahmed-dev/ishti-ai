"""Connectors skill: connect any OAuth 2.0 service with a Client ID + Client Secret.

"connect drive client-id ... client-secret ..." / "connect github" / "what's connected" / "disconnect
linkedin". One flow for every provider - the endpoints and scopes come from the registry in
ishti.oauth.PROVIDERS, so adding a service is a dictionary entry, not new code.

Privacy: credentials are written to config.json on this PC; the token goes to data/oauth/<service>.json.
Nothing about them is ever put into a search, an export or any other outbound request.
"""
import re

from ishti import oauth
from ishti.skills import trace

I = re.I
# "connect drive client-id 123.apps.googleusercontent.com client-secret GOCSPX-abc"
CONN = re.compile(r"^\s*(?:please\s+)?(?:connect|authori[sz]e|link|set\s+up|add)\s+"
                  r"(?P<svc>[a-z0-9][a-z0-9 _-]{1,24}?)\s*"
                  r"(?:client[\s_-]?id\s*[:=]?\s*(?P<cid>[\w.\-]+))?\s*"
                  r"(?:client[\s_-]?secret\s*[:=]?\s*(?P<sec>[\w.\-]+))?\s*[.?!]?\s*$", I)
DISC = re.compile(r"^\s*(?:disconnect|revoke|unlink|remove|forget)\s+(?P<svc>[a-z0-9][a-z0-9 _-]{1,24})\s*[.?!]?\s*$", I)
LIST = re.compile(r"^\s*(?:what|which|list|show|check)?\s*(?:is|are|have|do)\s+(?:i\s+)?(?:have\s+)?"
                  r"(?:already\s+)?(?:any\s+|my\s+)?(?:linked|connected|set\s+up)?\s*"
                  r"(?:accounts?|services?|connections?|connectors?|links?)\b"
                  r"|^\s*(?:what|which|list|show)\s+(?:is|are)\s+(?:the\s+)?(?:linked|connected)"
                  r"|^\s*(?:which|what)\s+(?:is|are)\s+(?:actually\s+)?(?:linked|connected|ready)\b"
                  r"|^\s*connected\s+accounts?\b", I)
HELP = re.compile(r"^\s*(?:what|which)\s+(?:services?|accounts?)\s+can\s+i\s+connect", I)

# a service name can be two words ("google drive", "google sheets", "google calendar")
ALIAS = {"google drive": "drive", "gdrive": "drive", "google sheets": "sheets", "sheet": "sheets",
         "google docs": "docs", "google document": "docs", "google calendar": "calendar",
         "contacts": "people", "google contacts": "people", "outlook": "microsoft",
         "outlook mail": "microsoft", "onedrive": "microsoft", "facebook": "facebook",
         "ig": "instagram", "google": "gmail", "mail": "gmail"}


def _name(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "").strip().lower()).strip(" .?!\"'")
    s = ALIAS.get(s, s)
    s = s.replace(" ", "")
    return s if s in oauth.PROVIDERS else (s.split()[0] if s.split() else s)


def match(text):
    t = text.strip()
    if HELP.match(t) or LIST.match(t):
        return True
    if DISC.match(t):
        return _name(DISC.match(t)["svc"]) in oauth.PROVIDERS
    if CONN.match(t):
        svc = _name(CONN.match(t)["svc"])
        return svc in oauth.PROVIDERS
    return False


def run(text, session):
    t = text.strip()
    if HELP.match(t):
        return {"reply": "I can connect: " + ", ".join(oauth.providers())
                         + ". Open Connectors and click Connect to choose your account. Advanced app setup is needed once when the app registration has not been configured.", "sources": []}
    if DISC.match(t):
        svc = _name(DISC.match(t)["svc"])
        if svc not in oauth.PROVIDERS:
            return {"reply": f"I don't know a service called '{svc}'. I know: "
                             f"{', '.join(oauth.providers())}.", "sources": []}
        return {"reply": oauth.disconnect(svc), "sources": []}
    if LIST.match(t):
        rows = [f"- {s}: {oauth.status(s).split(chr(58), 1)[-1].strip()}" for s in oauth.providers()]
        return {"reply": "Connections:\n" + "\n".join(rows), "sources": []}

    m = CONN.match(t)
    if not m:
        return None
    svc = _name(m["svc"])
    if svc not in oauth.PROVIDERS:
        return {"reply": f"I don't have a connector for '{m['svc'].strip()}'. I know: "
                         f"{', '.join(oauth.providers())}.", "sources": []}
    if oauth.PROVIDERS[svc].get("kind") == "token":
        return {"reply": f"{svc} doesn't use OAuth - give me the bot token and I'll store it in config.", "sources": []}

    if m["cid"] and (m["sec"] or svc == "microsoft"):
        saved = oauth.set_credentials(svc, m["cid"], m["sec"] or "")
        return {"reply": saved + f" Say 'connect {svc}' to authorise it in the browser.", "sources": []}

    trace(session, "oauth", f"authorising {svc}", status="start")
    from ishti import connectors
    res = connectors.start(svc)
    trace(session, "oauth", f"{svc}: {'waiting for sign-in' if res.get('waiting') else 'sign-in not started'}",
          status="waiting" if res.get("waiting") else "error")
    return {"reply": res["reply"], "sources": []}


if __name__ == "__main__":
    assert match("connect drive client-id 123.apps client-secret GOCSPX-x")
    assert match("connect github")
    assert match("disconnect linkedin")
    assert match("what services can i connect")
    assert match("what is connected")
    assert not match("connect this project to the network")
    assert _name("Google Drive") == "drive" and _name("outlook mail") == "microsoft"
    assert "drive" in oauth.PROVIDERS and "github" in oauth.PROVIDERS and "instagram" in oauth.PROVIDERS
    assert "sheets" in oauth.PROVIDERS and "linkedin" in oauth.PROVIDERS and "calendar" in oauth.PROVIDERS
    r = run("what services can i connect", {})
    assert "drive" in r["reply"] and "github" in r["reply"], r["reply"]
    # the credential write itself is covered by oauth's own self-check; never write test values into
    # the owner's real config.json from here
    assert CONN.match("connect drive client-id 123.apps client-secret GOCSPX-x")["cid"] == "123.apps"
    assert CONN.match("connect drive client-id 123.apps client-secret GOCSPX-x")["sec"] == "GOCSPX-x"
    print("[self-check] connectors OK - connect/disconnect/list for "
          f"{len(oauth.providers())} providers, alias resolution, credential parsing")
