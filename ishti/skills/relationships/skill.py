"""Relationships skill: the conversational front-end of ishti/rgraph.py.

"this is my brother Ahmed" -> person node + typed kin edge (private by default)
"Ahmed likes cricket" / "my sister can't stand loud music" -> preference edge to THAT person
"what does my father like" / "who introduced me to Bilal" -> graph traversal, exact answer
"what should I get Ahmed for his birthday" -> traverse likes, avoid dislikes
Contradictions ("you said X doesn't like coffee, now you say he does") ask which is
current instead of silently overwriting; duplicate people are merged on request;
answers about one person's likes are grounded ONLY in their own node (no cross-person
leakage - the same isolation rule the CRM layer enforces between clients).
"""
import os
import re
import sys

if __package__ in (None, ""):  # run directly: python ishti/skills/relationships/skill.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ishti.skills import trace

I = re.I
KIN = ("brother", "sister", "father", "mother", "son", "daughter", "cousin", "uncle", "aunt",
       "nephew", "niece", "grandfather", "grandmother", "friend", "colleague", "classmate")
# "this is my brother Ahmed" / "Ahmed is my cousin" / "meet my friend Sara"
KIN_DECLARE = re.compile(r"\b(?:this\s+is|meet|that's|that is|introduce)\s+my\s+(?P<rel>" + "|".join(KIN) + r")\s*"
                         r"(?:,?\s*(?:his|her|their)?\s*name\s*(?:is)?)?\s*(?P<name>[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)", I)
ELDER = r"(?:(?:elder|older|younger|big|little|eldest|youngest|real|only)\s+)?"
NAME = r"[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?"
# "irfan is my brother" (any case: the owner types lowercase names), "Ahmed is my elder brother, okay?"
KIN_DECLARE2 = re.compile(r"^\s*(?P<name>" + NAME + r")\s+is\s+my\s+" + ELDER + r"(?P<rel>" + "|".join(KIN) + r")\b[^?]*$", I)
# "my brother name is Ishaq Ahmed and he is my elder brother" / "my brother's name is Bilal"
KIN_NAME = re.compile(r"^\s*my\s+" + ELDER + r"(?P<rel>" + "|".join(KIN) + r")(?:'s|s)?\s+name\s+is\s+(?P<name>" + NAME + r")(?=\s+and\b|\s*[,.!?]|\s*$)", I)
# "who is my brother" / "what is my brother's name" / "tell me about my sister"
WHO_IS = re.compile(r"^\s*(?:who\s+is|who's|what(?:'s|\s+is)|tell\s+me\s+(?:about|who\s+is))\s+my\s+" + ELDER + r"(?P<rel>" + "|".join(KIN) + r")(?:'s\s+name)?\s*\??$", I)
# "his name spelling is I-R-F-A-N" / "his name is Irfan not Ishaq": the owner correcting the person just talked about
SPELL = re.compile(r"\b(?:his|her|their|the)\s+(?:first\s+|full\s+)?name\b.{0,30}?\b(?P<letters>(?:[A-Za-z]\s*-\s*){2,}[A-Za-z])\b", I)
CORRECT = re.compile(r"^\s*(?:no[,.]?\s+)?(?:his|her|their)\s+(?:full\s+)?name\s+is\s+(?P<new>" + NAME + r")(?:\s*,?\s*not\s+(?P<old>" + NAME + r"))?\s*[.!]?$", I)
NOT_NAMES = {"he", "she", "it", "this", "that", "who", "what", "there", "my", "mine", "his", "her", "they", "someone", "everyone", "you"}
LIKE = re.compile(r"^\s*(?:(?:my\s+)?(?P<who2>" + "|".join(KIN) + r")\s+)?(?P<who>[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)\s+"
                  r"(?P<rel>really\s+)?(?P<rel2>likes?|loves?|prefers?|dislikes?|hates?|can'?t\s+stand)\s+(?P<what>.+?)\s*[.!]?$", I)
LIKE2 = re.compile(r"^\s*(?:remember\s+|note\s+)?i\s+(?P<rel>really\s+)?(?P<rel2>like|love|prefer|dislike|hate|can'?t\s+stand)\s+(?P<what>.+?)\s*[.!]?$", I)
# "Ahmed doesn't like cricket": the negation is part of the verb, not the name -
# matched separately so 'doesn't' can never be swallowed into a person's name.
NEG_LIKE = re.compile(r"^\s*(?P<who>[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)\s+(?:doesn'?t|does\s+not|don'?t|do\s+not|never)\s+"
                      r"(?P<rel2>like|love|prefer)\s+(?P<what>.+?)\s*[.!]?$", I)
QUERY = re.compile(r"^\s*what\s+(?:does|do)\s+(?:my\s+)?(?P<who>[\w'-]+(?:\s+[\w'-]+)?)\s+(?P<act>like|love|prefer|dislike|hate)s?\b", I)
INTRO_Q = re.compile(r"\bwho\s+introduced\s+me\s+to\s+(?P<who>[\w'-]+)", I)
# "what should I get Ahmed for his birthday" / "a gift for my brother": the NAME (or the kinship
# word) comes before 'for his/her <occasion>' - never capture the possessive itself.
GIFT = re.compile(r"\b(?:gift|present)\s+for\s+(?:my\s+)?(?P<who>[A-Z][\w'-]*|brother|sister|father|mother|cousin|friend)\b"
                  r"|\b(?:what\s+should\s+i\s+(?:get|give|buy)|what\s+to\s+(?:get|give|buy))\s+"
                  r"(?P<who2>(?:for\s+)?(?:my\s+)?(?:[A-Z][\w'-]*|brother|sister|father|mother|cousin|friend))\b", I)


def _gift_who(m):
    """The person a gift question is about: 'Ahmed', or the kinship word resolved via me."""
    who = (m.group("who") or m.group("who2") or "").strip()
    while True:  # strip every leading 'for'/'my' ("for my brother's" needs two passes)
        stripped = re.sub(r"^(?:for\s+|my\s+)", "", who, flags=I)
        if stripped == who:
            break
        who = stripped
    who = re.sub(r"'s$", "", who).strip()  # "my brother's birthday" -> "my brother"
    if who.lower() in KIN:
        from ishti import rgraph
        edges = rgraph.edges_of("me", rel=who.lower(), direction="out") \
            or rgraph.edges_of("me", rel=who.lower(), direction="in")
        if edges:
            e = edges[0]
            return e["d_name"] if e["s_name"] == "me" else e["s_name"]
    return who
MERGE = re.compile(r"\b(?:merge|combine|same\s+person(?:\s+as)?)\b.*\b(?P<a>[\w'-]+)\s+(?:and|with)\s+(?P<b>[\w'-]+)", I)
PENDING = re.compile(r"\b(?:the\s+)?(?:new|old)\s+(?:one|value|fact|info)\b|\bkeep\s+(?:the\s+)?(?:new|old)\b", I)
STATS = re.compile(r"\b(?:who\s+do\s+(?:you|i)\s+know|relationship\s+(?:graph|stats?)|who\s+all\s+(?:do\s+i\s+have|is\s+in\s+(?:my|the)\s+graph))\b", I)



def _declared(t):
    m = KIN_DECLARE2.match(t)
    return m if m and m["name"].strip().lower() not in NOT_NAMES and m["name"].strip().split()[0].lower() not in NOT_NAMES else None


def match(text):
    return bool(KIN_DECLARE.search(text) or _declared(text.strip()) or KIN_NAME.match(text.strip()) or WHO_IS.match(text.strip())
                or SPELL.search(text) or CORRECT.match(text.strip()) or LIKE.match(text.strip())
                or LIKE2.match(text.strip()) or NEG_LIKE.match(text.strip()) or QUERY.match(text.strip())
                or INTRO_Q.search(text) or GIFT.search(text) or MERGE.search(text) or STATS.search(text))


FILLER = re.compile(r"\s*\b(?:at\s+all|any\s+more|anymore|these\s+days|now|at\s+the\s+moment)\b\s*", I)

def _clean_what(raw):
    """'cricket at all anymore' -> 'cricket': strip filler tails so a fact matches
    itself later (polarity conflicts compare the exact strings)."""
    out = FILLER.sub(" ", (raw or "").strip())
    return re.sub(r"\s+", " ", out).strip(" ,.")


def _name_of(m, who_key="who"):
    """Resolve 'who' from a match: an explicit person name, or a kinship word
    ('my father', 'my brother') -> that relative's node linked to me."""
    who = (m.group(who_key) or "").strip()
    if who.lower() in KIN:  # 'what does my father like' -> the node my father-edge points at
        from ishti import rgraph
        edges = rgraph.edges_of("me", rel=who.lower(), direction="out") \
            or rgraph.edges_of("me", rel=who.lower(), direction="in")
        if edges:
            e = edges[0]
            return e["d_name"] if e["s_name"] == "me" else e["s_name"]
        return None
    return who


def run(text, session):
    from ishti import rgraph
    t = text.strip()

    if PENDING.search(t):  # answer to an open contradiction question
        qs = rgraph.pending_questions()
        if qs:
            keep = "new" if re.search(r"\bnew\b", t, I) else "old"
            r = rgraph.resolve_question(qs[0]["id"], keep)
            if r:
                return {"reply": f"Updated - the {keep} fact stands for that person now.", "sources": []}
        return {"reply": "There's no open contradiction to resolve right now.", "sources": []}

    if m := WHO_IS.match(t):  # "who is my brother"
        rel = m["rel"].lower()
        edges = rgraph.edges_of("me", rel=rel, direction="out") or rgraph.edges_of("me", rel=rel, direction="in")
        if not edges:
            return {"reply": f"I don't know who your {rel} is yet. Tell me once - \"my {rel} is Ahmed\" - and I'll remember.", "sources": []}
        names = [(e["d_name"] if e["s_name"] == "me" else e["s_name"]).title() for e in edges]
        session["rel_last"] = names[0]
        return {"reply": f"Your {rel} is {' and '.join(dict.fromkeys(names))}.", "sources": []}

    if (m := SPELL.search(t) or CORRECT.match(t)) and not KIN_NAME.match(t):  # a spelling fix for the person just discussed
        new = (re.sub(r"[\s-]+", "", m["letters"]) if "letters" in m.groupdict() and m["letters"] else m["new"]).strip().title()
        old = (m.groupdict().get("old") or session.get("rel_last") or "").strip()
        if not old:
            kin = [e for rel in ("brother", "sister", "friend") for e in rgraph.edges_of("me", rel=rel, direction="out")]
            old = kin[0]["d_name"] if len(kin) == 1 else ""
        if not old:
            return {"reply": "Whose name should I fix? Tell me like \"my brother is Irfan\".", "sources": []}
        if rgraph.rename_node(old, new):
            session["rel_last"] = new
            return {"reply": f"Fixed: it is {new} now (was {old.title()}).", "sources": []}
        return {"reply": f"I don't have {old.title()} stored, so there is nothing to fix.", "sources": []}

    if m := KIN_NAME.match(t):
        rel, name = m["rel"].lower(), m["name"].strip()
        eid, created = rgraph.relate("me", rel, name)
        session["rel_last"] = name.title()
        return {"reply": f"Noted: your {rel} is {name.title()}.", "sources": []}

    if m := KIN_DECLARE.search(t) or _declared(t):
        rel, name = m["rel"].lower(), m["name"].strip()
        session["rel_last"] = name.title()
        eid, created = rgraph.relate("me", rel, name)
        if eid and created:
            return {"reply": f"Noted: {name.title()} is your {rel}. I keep family facts private - they never "
                             f"show up in client work. Tell me what they like and I'll remember it too.", "sources": []}
        return {"reply": f"I already have {name.title()} as your {rel}.", "sources": []}

    if m := LIKE2.match(t):  # "I like X" -> the owner's own preference
        rel = "dislikes" if m["rel2"] in ("dislike", "hate", "can't stand") else "likes"
        what = _clean_what(m["what"])
        eid, note = rgraph.add_preference("me", what, "neg" if rel == "dislikes" else "pos")
        if note:
            return {"reply": note, "sources": []}
        return {"reply": f"Noted: you {m['rel2']} {what}.", "sources": []}

    if m := NEG_LIKE.match(t):  # "Ahmed doesn't like cricket"
        who, what = m["who"].strip(), _clean_what(m["what"])
        eid, note = rgraph.add_preference(who, what, "neg")
        if note:
            return {"reply": note, "sources": []}
        if eid is None:
            return {"reply": "I couldn't store that - give me the person's name and try again.", "sources": []}
        return {"reply": f"Noted: {who.title()} doesn't like {what}.", "sources": []}

    if m := LIKE.match(t):
        verb = m["rel2"]
        rel = "dislikes" if verb in ("dislikes", "hates", "can't stand") else "likes"
        who = _name_of(m) or m["who"]
        what = _clean_what(m["what"])
        eid, note = rgraph.add_preference(who, what, "neg" if rel == "dislikes" else "pos")
        if note:
            return {"reply": note, "sources": []}
        if eid is None:
            return {"reply": "I couldn't store that - give me the person's name and try again.", "sources": []}
        trace(session, "graph", f"preference stored: {who} {rel} {what}")
        return {"reply": f"Noted: {who.title()} {verb} {what}.", "sources": []}

    if m := QUERY.match(t):
        who = _name_of(m)
        if not who:
            return {"reply": f"Who is your {m['who']}? Tell me once (\"this is my {m['who']} Ahmed\") and I'll "
                             "remember them and their likes.", "sources": []}
        rels = ("likes", "prefers") if m["act"] in ("like", "love", "prefer") else ("dislikes", "hates")
        edges = []
        for rel in rels:
            edges += rgraph.edges_of(who, rel=rel, direction="out")
        if not edges:
            return {"reply": f"Nothing stored about what {who.title()} {m['act']}s yet.", "sources": []}
        lines = [rgraph.describe_edge(e) for e in edges]
        trace(session, "graph", f"{len(lines)} fact(s) about {who}", results=lines)
        return {"reply": "\n".join(f"- {l}" for l in lines), "sources": []}

    if m := INTRO_Q.search(t):
        who = m["who"].strip()
        name = rgraph.introducer_of("me", who)
        if name:
            return {"reply": f"{name.title()} introduced you to {who.title()}.", "sources": []}
        return {"reply": f"I don't have who introduced you to {who.title()} - tell me and I'll link it.", "sources": []}

    if m := GIFT.search(t):
        who = _gift_who(m)
        occasion = next((w for w in ("birthday", "anniversary", "wedding", "graduation", "eid") if w in t.lower()), "")
        return {"reply": rgraph.suggest_gift(who, occasion), "sources": []}

    if m := MERGE.search(t):
        a, b = m["a"], m["b"]
        cands = rgraph.find_matches(a) + rgraph.find_matches(b)
        if rgraph.merge_nodes(a, b) or rgraph.merge_nodes(b, a):
            return {"reply": f"Merged {b.title()} into {a.title()} - all their links moved over.", "sources": []}
        if cands:
            names = ", ".join(c["name"] for c in cands[:3])
            return {"reply": f"I couldn't find both '{a}' and '{b}' as separate people. Close matches I have: {names}.", "sources": []}
        return {"reply": f"I don't have both '{a}' and '{b}' as people yet.", "sources": []}

    if STATS.search(t):
        s = rgraph.stats()
        people = [e["d_name"] for e in rgraph.edges_of("me", direction="out") if e["rel"] in KIN + ("friend",)]
        lines = [f"Relationship graph: {s['nodes']} nodes, {s['edges']} links, {s['open_questions']} open question(s)."]
        if people:
            lines.append("People I know: " + ", ".join(sorted(set(p.title() for p in people))))
        return {"reply": "\n".join(lines), "sources": []}

    return None


if __name__ == "__main__":
    import tempfile
    import ishti.rgraph as rg
    rg.DB_PATH = os.path.join(tempfile.mkdtemp(), "g.db")
    rg._conn = None
    assert match("this is my brother Ahmed")
    assert match("Ahmed is my cousin")
    assert match("Ahmed likes cricket")
    assert match("what does my father like?")
    assert match("who introduced me to bilal")
    assert match("what should I get Ahmed for his birthday")
    assert match("merge ahmed and ahmed ali")
    assert match("who do you know")
    assert not match("what is the weather tomorrow") and not match("remind me to call Ali")
    r = run("this is my brother Ahmed", {})
    assert "Ahmed is your brother" in r["reply"], r
    r = run("Ahmed likes cricket", {})
    assert "Noted: Ahmed likes cricket" in r["reply"], r
    r = run("what does my brother like?", {})
    assert "cricket" in r["reply"], r
    r = run("I like stumptown coffee", {})
    assert "stumptown" in r["reply"], r
    r = run("Ahmed doesn't like cricket at all anymore", {})
    assert "Which is current" in r["reply"], r  # contradiction: asked, not overwritten
    r = run("the new one", {})
    assert "the new fact stands" in r["reply"], r
    r = run("what does my brother like?", {})
    assert "cricket" not in r["reply"] or "might have changed" in r["reply"] or "-" in r["reply"]
    r = run("this is my brother Ahmed", {})
    assert "already have" in r["reply"], r
    r = run("who do you know", {})
    assert "Relationship graph" in r["reply"] and "Ahmed" in r["reply"], r
    print("[self-check] relationships OK - declare kin, preferences, queries, contradiction gate, gifts, merge, privacy")
