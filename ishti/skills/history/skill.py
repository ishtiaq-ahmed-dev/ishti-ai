"""Search past conversations: "what did we talk about the visa last week", "search my chats for the router password
reset", "when did I ask you about Karachi flights", "have we discussed CardioSense before".

Hermes Agent's session search answers this; ISHTI's memory only keeps the facts it extracted, not where they were said.
Reads the local chat store (runs.db, the web/desktop chats) and the older session files (channel chats). No model call,
nothing leaves the PC, trashed chats are never searched."""
import datetime as dt
import re
import time

I = re.I
_Q = r"(?P<{}>.+?)\s*[?.!]*\s*$"
SEARCH = re.compile(
    r"^\s*(?:what|when)\s+did\s+(?:we|i)\s+(?:talk|chat|speak|discuss|say)\w*\s+(?:about|regarding|on)\s+" + _Q.format("q1")
    + r"|^\s*(?:search|find|look\s+(?:up|through)|check)\s+(?:in\s+)?(?:my\s+|our\s+|the\s+)?(?:past\s+|old\s+|previous\s+|earlier\s+)?"
      r"(?:chats?|conversations?|chat\s+history|history|messages)\s+(?:for|about)\s+" + _Q.format("q2")
    + r"|^\s*(?:find|show|open)\s+(?:me\s+)?the\s+(?:chat|conversation)\s+(?:where|in\s+which|about)\s+(?:we\s+|i\s+)?"
      r"(?:talked\s+about\s+|discussed\s+|asked\s+(?:you\s+)?about\s+)?" + _Q.format("q3")
    + r"|^\s*when\s+did\s+i\s+(?:ask|tell)\s+you\s+about\s+" + _Q.format("q4")
    + r"|^\s*(?:have|did)\s+(?:we|i)\s+(?:ever\s+)?(?:(?:talk(?:ed)?|chat(?:ted)?|spoke|speak)\s+about|discuss(?:ed)?(?:\s+about)?)\s+"
      r"(?P<q5>.+?)(?:\s+before)?\s*[?.!]*\s*$", I)
WHEN = re.compile(r"\s*\b(?P<w>yesterday|today|this\s+week|last\s+week|this\s+month|last\s+month|recently)\b\s*", I)
STOP = frozenset("the and for with you your this that what how can from into about please want need make our my me "
                 "was were did does have has had any some all chat chats conversation conversations talk talked "
                 "discussed said say told asked ask".split())
HEADER = "Found it in"


def _since(word: str | None) -> float:
    now = dt.datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    w = re.sub(r"\s+", " ", (word or "").lower())
    start = {"today": midnight, "yesterday": midnight - dt.timedelta(days=1),
             "this week": midnight - dt.timedelta(days=now.weekday()), "last week": now - dt.timedelta(days=7),
             "this month": midnight.replace(day=1), "last month": now - dt.timedelta(days=31),
             "recently": now - dt.timedelta(days=14)}.get(w)
    return start.timestamp() if start else 0.0


def _ts(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return dt.datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def _rows(words, since):
    """(chat id, title, role, text, ts) from both chat stores."""
    from ishti import runs
    for r in runs.search_messages(words, since=since):
        yield r["chat_id"], r["title"] or "Untitled chat", r["role"], r["text"] or "", float(r["ts"] or 0)
    from orchestrator.memory.conversation import get_conversation_manager
    conv = get_conversation_manager()
    for meta in conv.list_sessions():
        if _ts(meta.get("updated_at")) < since:
            continue
        for m in (conv.load_session(meta["session_id"]) or {}).get("messages", []):
            text = str(m.get("content") or "")
            if any(w in text.lower() for w in words):
                yield meta["session_id"], meta.get("title") or "Untitled chat", m.get("role"), text, _ts(m.get("timestamp"))


def search(query: str, since: float = 0.0, exclude: str = "", limit: int = 5) -> list[dict]:
    """The chats that best match the query's content words: [{title, when, snippet}], best first."""
    words = sorted({w for w in re.findall(r"\w{3,}", query.lower()) if w not in STOP})
    if not words:
        return []
    need = len(words) if len(words) <= 2 else (len(words) + 1) // 2  # short queries need every word
    best = {}
    for chat, title, role, text, ts in _rows(words, since):
        if ts < since or text.strip() == exclude.strip() or (role != "user" and text.startswith(HEADER)):
            continue
        low = text.lower()
        hits = [w for w in words if re.search(rf"\b{re.escape(w)}", low)]
        if len(hits) < need:
            continue
        key = (len(hits), ts)
        if chat not in best or key > best[chat][0]:
            at = low.find(hits[0])
            snippet = " ".join(text[max(0, at - 60):at + 100].split())
            best[chat] = (key, {"title": title, "when": ts, "snippet": snippet})
    ranked = sorted(best.values(), key=lambda item: item[0], reverse=True)
    return [item for _, item in ranked[:limit]]


def match(text):
    return bool(SEARCH.match(text.strip()))


def run(text, session):
    m = SEARCH.match(text.strip())
    if not m:
        return None
    query = next(g for g in m.groups() if g)
    when = WHEN.search(query)
    since = _since(when["w"]) if when else 0.0
    query = WHEN.sub(" ", query).strip()
    found = search(query, since=since, exclude=text)
    period = f" ({when['w'].lower()})" if when else ""
    if not found:
        return {"reply": f"I couldn't find \"{query}\" in our past chats{period}.", "sources": []}
    lines = [f"- {time.strftime('%d %b %Y', time.localtime(f['when'])) if f['when'] else '(no date)'} - "
             f"\"{f['title']}\": ...{f['snippet']}..." for f in found]
    return {"reply": f"{HEADER} {len(found)} chat(s){period}:\n" + "\n".join(lines)
                     + "\n\nOpen the chat from the sidebar to read the whole conversation.", "sources": []}


if __name__ == "__main__":
    assert match("what did we talk about the visa last week") and match("search my chats for router reset")
    assert match("have we discussed CardioSense before?") and not match("what is the weather")
    m = SEARCH.match("when did I ask you about Karachi flights")
    assert next(g for g in m.groups() if g) == "Karachi flights"
    assert _since("yesterday") < _since("today") <= time.time()
    print("history self-check ok")
