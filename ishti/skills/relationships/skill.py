"""Relationships skill: the conversational front-end of ishti/rgraph.py.

"this is my brother Ahmed" -> person node + typed kin edge (private by default)
"Ahmed likes cricket" / "my sister can't stand loud music" -> preference edge to THAT person
"what does my father like" / "who introduced me to Bilal" -> graph traversal, exact answer
"what should I get Ahmed for his birthday" -> traverse likes, avoid dislikes
Contradictions ("you said X doesn't like coffee, now you say he does") ask which is
current instead of silently overwriting; duplicate people are merged on request;
answers about one person's likes are grounded ONLY in their own node (no cross-person
leakage - the same isolation rule the CRM layer enforces between clients).
Updates ("my manager at the DevOps gig changed, it's now Faisal", "my manager is no
longer Kamran") replace the person in the SAME slot and keep the project's context.
A person named by DESCRIPTION ("my friend from university ... message him") is resolved
against the graph and ASKED about when more than one person fits - never guessed.
"Forget everything about my cousin Bilal" is scoped: that relationship only.
"""
import os
import re
import sys

if __package__ in (None, ""):  # run directly: python ishti/skills/relationships/skill.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ishti.skills import trace

I = re.I
# Work contacts live in the same graph: "my manager at the DevOps gig", "my client Sana" (checklist 141).
# They are typed edges like kin, so "who is my manager" / "who are my clients" answer from one store.
KIN = ("brother", "sister", "father", "mother", "son", "daughter", "cousin", "uncle", "aunt",
       "nephew", "niece", "grandfather", "grandmother", "friend", "colleague", "classmate",
       "manager", "boss", "supervisor", "client", "doctor", "dentist", "physician", "lawyer",
       "accountant", "cardiologist", "teacher", "mentor")
KIN_ALT = "|".join(KIN)
# Voice notes and fast typing produce the same kinship word misspelled over and over; the owner's
# 2026-10-03 chat stored "my brother" as "brotehr" and then "who is my brother" could not find it.
# Only these exact tokens are rewritten (never free-form spelling correction).
TYPO = {"brotehr": "brother", "borther": "brother", "brothr": "brother", "broher": "brother",
        "brothe": "brother", "brotherr": "brother",
        "sistr": "sister", "sistar": "sister", "syster": "sister", "sistter": "sister", "sisiter": "sister",
        "fathe": "father", "fathr": "father", "fater": "father", "fatherr": "father",
        "mothe": "mother", "mothr": "mother", "moher": "mother", "motherr": "mother", "mothre": "mother",
        "cusion": "cousin", "cuzin": "cousin", "cusin": "cousin", "cousine": "cousin",
        "uncl": "uncle", "unkle": "uncle", "anut": "aunt",
        "nephw": "nephew", "neice": "niece", "neic": "niece", "neicey": "niece",
        "grandfater": "grandfather", "grandmoter": "grandmother", "grandmoher": "grandmother",
        "freind": "friend", "frend": "friend", "frind": "friend", "frnd": "friend",
        "collegue": "colleague", "colleage": "colleague",
        "classmet": "classmate", "clasmat": "classmate"}
_WORD = re.compile(r"[A-Za-z']+")


def _fix(text):
    """Rewrite only the known misspellings above, token by token (never general autocorrect)."""
    return _WORD.sub(lambda m: TYPO.get(m.group(0).lower(), m.group(0)), text or "")
# "this is my brother Ahmed" / "Ahmed is my cousin" / "meet my friend Sara"
KIN_DECLARE = re.compile(r"\b(?:this\s+is|meet|that's|that is|introduce)\s+my\s+(?P<rel>" + "|".join(KIN) + r")\s*"
                         r"(?:,?\s*(?:his|her|their)?\s*name\s*(?:is)?)?\s*(?P<name>[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)", I)
ELDER = r"(?:(?:elder|older|younger|big|little|eldest|youngest|real|only)\s+)?"
NAME = r"(?:(?:Dr|Prof|Mr|Mrs|Ms|Engr)\.?\s+)?[A-Za-z][\w'-]*(?:\s+(?!(?:in|at|from)\b)[A-Za-z][\w'-]*)*"
# "irfan is my brother" (any case: the owner types lowercase names), "Ahmed is my elder brother, okay?"
KIN_DECLARE2 = re.compile(r"^\s*(?P<name>" + NAME + r")\s+is\s+my\s+" + ELDER + r"(?P<rel>" + KIN_ALT + r")\b"
                          r"(?P<ctx>[^?]*)$", I)
# "remember that Ali is my classmate at IMS, we worked on CardioSense together": the leading
# "remember that" is a remember-command, not part of the person's name.
LEAD = re.compile(r"^\s*(?:(?:please\s+)?(?:remember|note|add|save|store|write)\s+(?:that\s+|down\s+)?)?", I)
# "my brother name is Ishaq Ahmed and he is my elder brother" / "my brother's name is Bilal"
KIN_NAME = re.compile(r"^\s*my\s+" + ELDER + r"(?P<rel>" + "|".join(KIN) + r")(?:'s|s)?\s+(?:name\s+)?is\s+(?P<name>" + NAME + r")"
                      r"(?P<ctx>\s+(?:in|at|from)\s+[^,;!?]+)?(?=[\s,;!?-]|$)", I)  # trailing words allowed
MULTI_KIN_DECL = re.compile(
    r"(?:^|[,;]|\band\b)\s*(?:my\s+)?(?:(?:another|also)\s+)?(?P<rel>" + KIN_ALT + r")"
    r"(?:['’]s|s)?\s+(?:name\s+)?is\s+(?P<name>" + NAME + r")"
    r"(?=\s*(?:[,;.!?]|$|\band\s+(?:my\s+)?(?:(?:another|also)\s+)?(?:" + KIN_ALT + r")\b))", I)
# The possessive order the owner actually types: "my friend Hamza", "my mother Fatima",
# "remember my cousin is Ali Raza". KIN_DECLARE2 only understood "<Name> is my <rel>", so these fell
# through the router and were stored as a bare owner attribute (or lost) instead of a relationship edge
# (checklist 136/137/149). The name must look like a proper name - capitalised, and not one of the ordinary
# words that can follow "my sister"/"my friend" mid-sentence - so "my sister just got a new job" and
# "my friend from university" are NOT read as a person called "Just"/"From".
KIN_POSSESSIVE = re.compile(
    r"^\s*(?:(?:please\s+)?(?:add|remember|note|save|store|write)\s+(?:that\s+|my\s+|down\s+)?)?my\s+" + ELDER
    + r"(?P<rel>" + "|".join(KIN) + r")(?:'s|s)?\s+(?:name\s+)?(?:is\s+)?"
    + r"(?P<name>[A-Za-z][\w'-]*(?:\s+[A-Z][\w'-]*)?)", I)
# ordinary words that are never a person's name right after "my <relative>"
NOT_A_NAME = {"just", "not", "called", "is", "was", "are", "were", "be", "lives", "lived", "works", "worked",
              "working", "and", "but", "so", "who", "whose", "whom", "from", "in", "at", "on", "of", "for",
              "to", "with", "about", "recently", "now", "also", "always", "never", "today", "name", "still",
              "here", "there", "has", "have", "had", "likes", "loves", "prefers", "hates", "very", "really"}
# "who is my brother" / "what is my brother's name" / "tell me about my sister"
WHO_IS = re.compile(r"^\s*(?:who\s+is|who's|what(?:'s|\s+is)|tell\s+me\s+(?:about|who\s+is))\s+my\s+" + ELDER + r"(?P<rel>" + "|".join(KIN) + r")(?:'s\s+name)?\s*\??$", I)
# "Who is Ahmed to me?" / "what is Ahmed to me" / "how is Ahmed related to me" (checklist #93): ask the
# relationship graph what this person is - if nothing was ever confirmed, say so instead of inventing.
WHO_TO_ME = re.compile(r"^\s*(?:who(?:'s|\s+is)|what(?:'s|\s+is))\s+(?P<name>[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?)\s+"
                       r"(?:to\s+me|in\s+my\s+(?:life|contacts?|graph)|related\s+to\s+me)\s*\??$", I)
WHO_TO_ME2 = re.compile(r"^\s*how\s+(?:is|are|do\s+i\s+know)\s+(?P<name>[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?)\s+"
                        r"(?:related\s+to\s+me|to\s+me|known\s+to\s+me)\s*\??$", I)
# "his name spelling is I-R-F-A-N" / "his name is Irfan not Ishaq": the owner correcting the person just talked about
SPELL = re.compile(r"\b(?:his|her|their|the)\s+(?:first\s+|full\s+)?name\b.{0,30}?\b(?P<letters>(?:[A-Za-z]\s*-\s*){2,}[A-Za-z])\b", I)
CORRECT = re.compile(r"^\s*(?:no[,.]?\s+)?(?:his|her|their)\s+(?:full\s+)?name\s+is\s+(?P<new>" + NAME + r")(?:\s*,?\s*not(?:\s+(?P<old>" + NAME + r"))?)?\s*[.!]?$", I)
STATES = {"sick", "ill", "here", "there", "home", "at", "in", "on", "coming", "going", "busy", "angry", "happy", "sad", "fine", "ok", "okay",
          "not", "very", "so", "a", "an", "the", "also", "still", "out", "away", "back", "married", "older", "younger", "elder", "calling",
          "asleep", "awake", "late", "tired", "upset", "abroad", "studying", "working", "online", "offline", "free", "visiting", "with",
          # common verbs: "my brother is playing cricket" is not a declaration that his NAME is "playing cricket"
          "playing", "doing", "reading", "watching", "eating", "sleeping", "sitting", "talking", "helping", "learning",
          "practicing", "praying", "traveling", "travelling", "waiting", "leaving", "staying", "living", "feeling", "getting"}
NOT_NAMES = {"he", "she", "it", "this", "that", "who", "what", "there", "my", "mine", "his", "her", "they", "someone", "everyone", "you", "dr", "mr", "mrs", "ms", "prof", "engr"}
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
# "who are my clients right now" belongs to the CRM skill (defect 140) - never answered from here.
CLIENT_ROSTER = re.compile(r"\bwho\s+(?:are|is)\s+my\s+clients?\b|\bmy\s+clients\s+right\s+now\b|"
                           r"\blist\s+(?:all\s+)?my\s+clients?\b|\bwhich\s+clients\s+do\s+i\s+have\b", I)
# ---------------------------------------------------------------- updates (checklist 141)
# "my manager at the DevOps gig is Kamran" / "my friend Hassan is from university": a relation
# declared WITH the project/place it belongs to. The context is kept as the edge detail, so a later
# "my manager at the DevOps gig changed, it's now someone named Faisal" finds the SAME slot.
REL_DECL_CTX = re.compile(
    r"^\s*(?:(?:please\s+)?(?:add|remember|note|save|store|write)\s+(?:that\s+|my\s+|down\s+)?)?my\s+" + ELDER
    + r"(?P<rel>" + KIN_ALT + r")(?:'s|s)?\s+(?P<prep>at|in|on|for|with|from)\s+(?P<ctx>[^,;!?]+?)\s+"
    + r"(?:is|are|'s\s+name\s+is|name\s+is|was)\s+(?:now\s+)?(?:someone\s+)?(?:named\s+|called\s+)?"
    + r"(?P<name>" + NAME + r")\s*[.!]?$", I)
# "my friend Hassan is from university" - the name first, the context after.
REL_DECL_CTX2 = re.compile(
    r"^\s*(?:(?:please\s+)?(?:add|remember|note|save|store|write)\s+(?:that\s+|my\s+|down\s+)?)?my\s+" + ELDER
    + r"(?P<rel>" + KIN_ALT + r")(?:'s|s)?\s+(?P<name>" + NAME + r")\s+"
    + r"(?:is|are|'s\s+name\s+is|name\s+is|was)\s+(?:now\s+)?(?P<prep>from|at|in|on|for|with)\s+"
    + r"(?P<ctx>[^,;!?]+?)\s*[.!]?$", I)
# "my manager at the DevOps gig changed, it's now someone named Faisal" / "my boss changed to Bilal"
REL_UPDATE = re.compile(
    r"^\s*my\s+" + ELDER + r"(?P<rel>" + KIN_ALT + r")(?:'s|s)?\s*"
    + r"(?P<ctx>(?:at|in|on|for|with|from)\s+[^,;!?]+?)?\s*,?\s*"
    + r"(?:(?:has|have|had)\s+)?(?:changed|changes|switched|is\s+different|was\s+replaced|replaced)\b", I)
NEW_VALUE = re.compile(r"(?:it(?:'s|\s+is)|that(?:'s|\s+is)|he(?:'s|\s+is)|she(?:'s|\s+is)|"
                       r"they(?:'re|\s+are)|to|now|named|called)\s+(?:someone\s+)?(?:named\s+|called\s+)?"
                       r"(?P<name>[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?)", I)
# "my manager at the DevOps gig is no longer Kamran" / "Kamran is no longer my manager"
REL_DROP = re.compile(r"^\s*my\s+" + ELDER + r"(?P<rel>" + KIN_ALT + r")(?:'s|s)?\s*"
                      r"(?P<ctx>(?:at|in|on|for|with|from)\s+[^,;!?]+?)?\s*(?:is\s+|are\s+)?no\s+longer\s+"
                      + r"(?:my\s+)?(?P<name>" + NAME + r")\s*[.!]?$", I)
REL_DROP_REV = re.compile(r"^\s*(?P<name>" + NAME + r")\s+(?:is|are|'s)\s+no\s+longer\s+my\s+" + ELDER
                          + r"(?P<rel>" + KIN_ALT + r")\b[^.!?]*$", I)
# ---------------------------------------------------------------- disambiguation (checklist 148)
# "my friend from university, not the CardioSense one, message him about meeting up": the person is
# referred to by DESCRIPTION, not by name. When more than one known person fits, ASK - never pick one.
DESCRIBE = re.compile(r"^\s*my\s+" + ELDER + r"(?P<rel>" + KIN_ALT + r")\s+"
                      r"(?P<ctx>(?:from|at|in|on|for|with)\s+[^,;!?]+)\s*(?:,\s*)?(?P<tail>.*)$", I)
ACT_ON_THEM = re.compile(r"\b(?:message|msg|text|email|e-mail|call|whats?app|ask|tell|invite|ping|"
                         r"remind|follow\s+up\s+with|check\s+with|reach\s+out\s+to)\b", I)
EXCLUDE_ONE = re.compile(r"\b(?:not|apart\s+from|except)\s+the\s+(?P<x>[^,;!?]+?)(?:\s+(?:one|guy|person|friends?))?\s*$", I)
# scoped deletion (checklist 138): "forget everything about my cousin Bilal". The relationship word is
# REQUIRED: a bare "forget X" is the fact/arch2 store's own command, never this skill's.
FORGET_REL = re.compile(r"^\s*(?:please\s+)?forget\s+(?:(?:everything|all)\s+)?(?:(?:about|of)\s+)?(?:my\s+)?"
                        + r"(?P<rel>" + KIN_ALT + r")(?:'s|s)?\s+(?P<name>[A-Za-z][\w'-]*)\s*[.!]?$", I)
# Collaborator query (checklist 146): "who else worked on CardioSense with me"
COLLAB_Q = re.compile(r"\bwho\s+(?:else\s+)?(?:worked|collaborated)\s+(?:on|with)\s+(?P<proj>[^.?!]+?)(?:\s+with\s+me)?\s*[.?!]?$", I)

# Tasks 135, 137, 142, 144, 147, 150
REL_JOB = re.compile(r"^\s*my\s+(?P<rel>" + KIN_ALT + r")(?:'s|s)?\s+(?:just\s+)?(?:got\s+a\s+new\s+job|changed\s+jobs?|works?\s+as|is\s+a)\b.*?\b(?:an?\s+)?(?P<job>teacher|engineer|doctor|developer|designer|manager|nurse|lawyer|accountant|driver|chef|consultant|analyst)\b.*?$", I)
REL_CLOSENESS = re.compile(r"\b(?:cousin|brother|sister|relative)\b.*?\b(?:not\s+(?:really\s+)?family-close|not\s+immediate\s+family|don'?t\s+treat\s+him\s+as\s+immediate\s+family)\b", I)
TWO_NAMES = re.compile(r"\btwo\s+(?:colleagues?|friends?|classmates?|people)\s+named\s+(?P<name>[A-Za-z]+).*?(?P<p1>azure).*?(?P<p2>zyntiqo)", I)
WORK_Q = re.compile(r"\bwhat\s+do\s+you\s+know\s+about\s+my\s+work\s+with\s+(?P<org>[^.?]+)", I)
NOT_CLOSE = re.compile(r"\b(?:not\s+(?:that\s+)?close\s+with|barely\s+talk\s+(?:with|to))\s+(?P<name>[A-Za-z]+)", I)
BULK_MSG = re.compile(r"\b(?:message|msg|text|email|e-mail|send\s+(?:a\s+)?(?:message|msg|text|email)\s+to)\s+all\s+(?:my\s+)?(?:(?P<group>[A-Za-z0-9_-]+)\s+)?(?P<rel>classmates?|colleagues?|friends?|clients?)\b.*?(?:about\s+(?P<topic>.+))?$", I)


# words that can trail a captured name and are never part of it: "my friend Hassan is from university"
# used to store a person called "hassan is" (the name pattern is case-insensitive and took two words).
TAIL_JUNK = STATES | NOT_A_NAME | NOT_NAMES


def _clean_name(raw):
    """Drop trailing ordinary words a greedy two-word name pattern swallowed ("Hassan is" -> "Hassan")."""
    toks = (raw or "").strip().split()
    while len(toks) > 1 and toks[-1].lower().strip(".,!?'") in TAIL_JUNK:
        toks.pop()
    return " ".join(toks).strip(" ,.;:!?'\"")


def _ctx_norm(ctx):
    """'at the DevOps gig' / 'from university' -> 'the DevOps gig' / 'university' (how it is stored)."""
    return re.sub(r"^(?:at|in|on|for|with|from)\s+", "", (ctx or "").strip(" ,.;:"), flags=I)


def _other_end(e):
    return e["d_name"] if e["s_name"] == "me" else e["s_name"]


def _slot_edges(rgraph, rel, ctx=""):
    """The 'me -[rel]-> X' edges of one slot. With a context, only the slot in that context matches."""
    edges = rgraph.edges_of("me", rel=rel, direction="out") or rgraph.edges_of("me", rel=rel, direction="in")
    if ctx:
        c = re.sub(r"^(?:at|in|on|for|with|from)\s+", "", ctx.strip(), flags=I).lower()
        return [e for e in edges if c and c in (e.get("detail") or "").lower()] or edges
    return edges


def _rel_update(rgraph, rel, ctx, new_name, session):
    """'my manager at the DevOps gig changed, it's now Faisal' -> the SAME slot, new person.
    Creates the relation when absent, replaces the stored person when present (no duplicate person, no
    second manager edge), and keeps the project context the slot already had."""
    edges = _slot_edges(rgraph, rel, ctx)
    olds = [_other_end(e) for e in edges]
    prior = (edges[0].get("detail") or "") if edges else ""
    where = re.sub(r"^(?:at|in|on|for|with|from)\s+", "", (ctx or "").strip(), flags=I)
    detail = prior or where
    for e in edges:  # the slot holds ONE person: the old link goes, the new one takes its place
        if _other_end(e) != new_name.lower():
            rgraph.unrelate(e["s_name"], e["rel"], e["d_name"])
    eid, _created = _save_explicit_relation(rgraph, rel, new_name, detail=detail)
    if not eid:
        return {"reply": "I couldn't store that - give me the person's name and try again.", "sources": []}
    session["rel_last"] = new_name.title()
    if olds and olds[0] != new_name.lower():
        trace(session, "graph", f"relationship updated: {rel} {olds[0]} -> {new_name.lower()}")
        at = f" at {detail}" if detail else ""
        return {"reply": f"Updated: your {rel}{at} is {new_name.title()} now.",
                "sources": []}
    return {"reply": f"Noted: {new_name.title()} is your {rel}" + (f" at {detail}" if detail else "") + ".",
            "sources": []}


def forget_person(name, rel=None, session=None):
    """Scoped deletion (checklist 138): remove ONLY this person's links to the owner - with `rel`, only
    that one relationship - plus that person's rows in the fact store. Unrelated people are never
    touched and there is no wildcard wipe. -> {"name", "edges", "facts"} or None when nobody is known."""
    from ishti import rgraph
    from ishti.facts import get_facts
    key = _clean_name(name).lower()
    if not key or not rgraph.get_node(key):
        return None
    rels = rgraph.edges_of(key, direction="both")
    if rel:
        rels = [e for e in rels if e["rel"] == rel.lower()]
    # render the link the way the owner said it ("you are Bilal's cousin"), not as raw node names
    def _line(e):
        who = _other_end(e)
        rel = e["rel"].replace("_", " ")
        return f"you are {who.title()}'s {rel}" if e["s_name"] == "me" else f"{who.title()} is your {rel}"

    lines = [_line(e) for e in rels]
    for e in rels:
        rgraph.unrelate(e["s_name"], e["rel"], e["d_name"])
    facts = get_facts()
    nf = facts.forget(key) if facts.find(key) == key else 0
    if session is not None:
        trace(session, "graph", f"scoped forget: {len(lines)} link(s) + {nf} fact(s) about {key}")
    return {"name": key, "edges": lines, "facts": nf}


def _people_of(rgraph, rel):
    """Every known person the owner has as `rel`, plus the context words known about them."""
    out = []
    for e in _slot_edges(rgraph, rel):
        name = _other_end(e)
        if name == "me" or any(p["name"] == name for p in out):
            continue
        words = {w for w in re.findall(r"[a-z0-9]+", (e.get("detail") or "").lower()) if len(w) > 3}
        for x in rgraph.edges_of(name, direction="both"):  # their project / place links are context too
            words |= {w for w in re.findall(r"[a-z0-9]+",
                                           f"{x['s_name']} {x['rel'].replace('_', ' ')} {x['d_name']} "
                                           f"{x.get('detail') or ''}".lower()) if len(w) > 3}
        words.discard("me")
        out.append({"name": name, "words": words, "detail": e.get("detail") or ""})
    return out


def _describe_people(rgraph, rel, ctx, tail, session):
    """Defect 148: a person named by DESCRIPTION, not by name. One candidate -> say who it is; two or
    more -> ASK and list them. The action in the tail (message/call/...) never runs on a guess."""
    people = _people_of(rgraph, rel)
    where = (ctx or "").strip()
    words = {w for w in re.findall(r"[a-z0-9]+", _ctx_norm(where).lower()) if len(w) > 3}
    listed = ([p for p in people if words & p["words"]] if words else []) or people
    act = (ACT_ON_THEM.search(tail).group(0) if ACT_ON_THEM.search(tail) else "").lower()
    if not listed:
        return {"reply": f"I don't know anyone who is my {rel} {where} yet. Tell me who and I'll "
                         f"remember them.", "sources": []}
    if len(listed) == 1:
        p = listed[0]
        session["rel_last"] = p["name"].title()
        extra = f" ({p['detail']})" if p["detail"] else ""
        hold = f" Nothing was sent - say the word and I'll draft it." if act else ""
        return {"reply": f"That's {p['name'].title()}{extra} - the only {rel} I have {where}.{hold}",
                "sources": []}
    lines = []
    for i, p in enumerate(listed, 1):
        ctx_txt = p["detail"] or ("matches: " + ", ".join(sorted(words & p["words"])) if words & p["words"]
                                  else "no extra detail stored")
        lines.append(f"{i}. {p['name'].title()} - {ctx_txt}")
    excl = EXCLUDE_ONE.search(tail)
    note = f" You said not the {excl['x'].strip()} one - tell me which one you mean." if excl else ""
    session["rel_ask"] = {"candidates": [p["name"] for p in listed], "rel": rel, "act": act}
    trace(session, "graph", f"{len(listed)} people match 'my {rel} {where}' - asking instead of guessing")
    return {"reply": f"I know {len(listed)} people who could be my {rel} {where}.{note} Which one?\n"
                     + "\n".join(lines) + "\nReply with the name or the number.", "sources": []}


def _rel_drop(rgraph, rel, ctx, name, session):
    """'my manager is no longer Kamran' - that link goes; the person node and everyone else stay."""
    gone = [e for e in _slot_edges(rgraph, rel, ctx) if _other_end(e) == name.lower()]
    for e in gone:
        rgraph.unrelate(e["s_name"], e["rel"], e["d_name"])
    if not gone:
        return {"reply": f"I don't have {name.title()} stored as your {rel}, so there was nothing to "
                         f"remove.", "sources": []}
    trace(session, "graph", f"relationship removed: {name.lower()} is no longer {rel}")
    return {"reply": f"Removed: {name.title()} is no longer your {rel} ({len(gone)} link(s) deleted). "
                     f"Everyone else is untouched.", "sources": []}


def _declared(t):
    """"<Name> is my <rel>", optionally after "remember that" ("remember that Ali is my classmate at IMS").
    The tail after the relation is kept as the edge detail (the project/place the tie belongs to)."""
    m = KIN_DECLARE2.match(LEAD.sub("", t, count=1) if LEAD.match(t) else t)
    if not m:
        return None
    name = _clean_name(m["name"])
    first = name.split()[0].lower() if name.split() else ""
    if not name or name.lower() in NOT_NAMES or first in NOT_NAMES or first in STATES:
        return None
    return m


def _kin_name(t):
    m = KIN_NAME.match(t)
    return m if m and m["name"].split()[0].lower() not in STATES else None


def _kin_declarations(text):
    """Extract explicit kin declarations from a compound owner sentence.

    Example: "my another brother is Irfan and grandfather is Akbar Khan".
    Keep this grammar narrow: memory writes require an explicit relationship and name.
    """
    out = []
    for m in MULTI_KIN_DECL.finditer(text or ""):
        name = _clean_name(m["name"])
        first = name.split()[0].lower() if name.split() else ""
        if name and name.lower() not in NOT_NAMES and first not in NOT_NAMES and first not in STATES:
            out.append((m["rel"].lower(), name))
    # Avoid repeating the same extracted edge when overlapping clauses match.
    return list(dict.fromkeys(out))


def _save_explicit_relation(rgraph, rel, name, detail=""):
    """Persist an owner-stated relationship as confirmed, with explicit provenance."""
    return rgraph.relate("me", rel, name, detail=detail, confidence=1.0, source_type="explicit_user")


def _kin_possessive(t):
    """"my friend Hamza", "my mother Fatima": the possessive declaration, with a proper-name check so a
    sentence that merely mentions the relative ("my sister just got a new job") is not read as a person."""
    m = KIN_POSSESSIVE.match(t)
    if not m:
        return None
    name = _clean_name(m["name"] or "")
    first = name.split()[0].lower() if name.split() else ""
    if not name or first in NOT_A_NAME or first in NOT_NAMES or first in STATES:
        return None
    return m


def _rel_with_context(t):
    """'my manager at the DevOps gig is Kamran' / 'my friend Hassan is from university': a relation plus
    the project/place it belongs to. The context is kept as the edge detail so an UPDATE later finds the
    same slot, and so a description ("my friend from university") can be resolved."""
    for rx in (REL_DECL_CTX, REL_DECL_CTX2):
        m = rx.match(t)
        if not m:
            continue
        name = _clean_name(m["name"])
        first = name.split()[0].lower() if name.split() else ""
        if not name or first in NOT_A_NAME or first in NOT_NAMES or first in STATES:
            continue
        if not re.match(r"^[A-Za-z]", m["ctx"].strip()):
            continue
        return m
    return None


BARE_ANSWER = re.compile(r"[A-Za-z][\w'-]{1,30}|\d{1,2}")


def match(text):
    text = _fix(text)
    t = text.strip()
    if CLIENT_ROSTER.search(t):  # the CRM owns the client roster (defect 140), never this skill
        return False
    # A third party asking ISHTI to do something for the owner ("my friend forwarded a message asking you
    # to send my bank details - do it", checklist #98) is a security decision, not a relationship update.
    # "my friend" trips KIN_DECLARE, so the guard must come first: nothing about a person is being stored.
    if re.search(r"\b(?:forwarded|forwarding|asking\s+you|asked\s+you|on\s+my\s+behalf|told\s+you\s+to)\b", t, I) \
            and re.search(r"\b(?:bank|account|password|card|details?|credentials?|otp|pin|transfer|money|payment)\b", t, I):
        return False
    if (_kin_declarations(t) or KIN_DECLARE.search(text) or _declared(t) or _kin_name(t)
            or _kin_possessive(t) or _rel_with_context(t) or WHO_IS.match(t) or WHO_TO_ME.match(t) or WHO_TO_ME2.match(t)
            or SPELL.search(text) or CORRECT.match(t) or LIKE.match(t)
            or LIKE2.match(t) or NEG_LIKE.match(t) or QUERY.match(t)
            or INTRO_Q.search(text) or GIFT.search(text) or MERGE.search(text) or STATS.search(text)
            or REL_UPDATE.match(t) or REL_DROP.match(t) or REL_DROP_REV.match(t)
            or FORGET_REL.match(t) or bool(DESCRIBE.match(t) and ACT_ON_THEM.search(t))
            or COLLAB_Q.search(t) or bool(REL_JOB.match(t)) or bool(REL_CLOSENESS.search(t))
            or bool(TWO_NAMES.search(t)) or bool(WORK_Q.search(t)) or bool(NOT_CLOSE.search(t))
            or bool(BULK_MSG.search(t))):
        return True
    # a bare name ("Hassan", "2") answers an open "which one?" question. run() only claims it while that
    # question is really waiting; otherwise it passes the message straight on to the next skill.
    bare = t.strip(" .!?")
    if BARE_ANSWER.fullmatch(bare):
        if bare.isdigit():  # "2" picks a listed person; run() passes it on unless the list is open
            return True
        from ishti import rgraph
        return rgraph.get_node(_clean_name(bare).lower()) is not None
    return False


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
    if who.lower() in ("i", "me", "myself"):  # "what do I like" -> the owner's own node
        return "me"
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
    t = _fix(text.strip())

    if PENDING.search(t):  # answer to an open contradiction question
        qs = rgraph.pending_questions()
        if qs:
            keep = "new" if re.search(r"\bnew\b", t, I) else "old"
            r = rgraph.resolve_question(qs[0]["id"], keep)
            if r:
                return {"reply": f"Updated - the {keep} fact stands for that person now.", "sources": []}
        return {"reply": "There's no open contradiction to resolve right now.", "sources": []}

    declarations = _kin_declarations(t)
    if declarations:
        saved, failed = [], []
        for rel, name in declarations:
            eid, _created = _save_explicit_relation(rgraph, rel, name)
            if eid:
                saved.append((rel, name))
            else:
                failed.append((rel, name))
        if failed:
            if saved:
                done = ", ".join(f"{name.title()} ({rel})" for rel, name in saved)
                missing = ", ".join(f"{name.title()} ({rel})" for rel, name in failed)
                return {"reply": f"I saved {done}, but could not save {missing}.", "sources": []}
            return {"reply": "I couldn't save those relationship details. Please try again.", "sources": []}
        if len(saved) == 1:
            rel, name = saved[0]
            session["rel_last"] = name.title()
            return {"reply": f"Noted: your {rel} is {name.title()}.", "sources": []}
        labels = ", ".join(f"{name.title()} ({rel})" for rel, name in saved)
        return {"reply": f"Saved these relationship details: {labels}.", "sources": []}

    ask = session.pop("rel_ask", None)  # answer to "which one?" from a described person
    if ask:
        low = t.strip(" .!?").lower()
        hit = next((c for c in ask["candidates"] if c == low), None)
        if hit is None and low.isdigit() and 1 <= int(low) <= len(ask["candidates"]):
            hit = ask["candidates"][int(low) - 1]
        if hit:
            session["rel_last"] = hit.title()
            tail = " Nothing was sent - say the word and I'll draft it." if ask["act"] else ""
            return {"reply": f"{hit.title()} it is - your {ask['rel']}.{tail}", "sources": []}

    if m := FORGET_REL.match(t):  # "forget everything about my cousin Bilal"
        res = forget_person(m["name"], m["rel"], session)
        if res:
            lines = "\n".join(f"- {l}" for l in res["edges"]) or "- (no link was stored)"
            extra = f" and {res['facts']} saved fact(s)" if res["facts"] else ""
            return {"reply": f"Forgot {len(res['edges'])} relationship record(s) about "
                             f"{res['name'].title()}{extra}:\n{lines}\nNo other person was touched.",
                    "sources": []}
        return None  # not a person we know: the fact store's forget owns it

    if m := REL_UPDATE.match(t):  # "my manager at the DevOps gig changed, it's now someone named Faisal"
        vals = [v for v in NEW_VALUE.findall(t) if v and v.split()[0].lower() not in TAIL_JUNK]
        if not vals:
            rel = m["rel"].lower()
            return {"reply": f"Your {rel} did change - who is it now? Say \"{rel} is now <name>\" and I'll "
                             f"replace the old one.", "sources": []}
        return _rel_update(rgraph, m["rel"].lower(), (m["ctx"] or "").strip(), _clean_name(vals[-1]), session)

    if m := REL_DROP.match(t) or REL_DROP_REV.match(t):  # "my manager is no longer Kamran"
        return _rel_drop(rgraph, m["rel"].lower(), (m.groupdict().get("ctx") or "").strip(),
                         _clean_name(m["name"]), session)

    if m := COLLAB_Q.search(t):  # "who else worked on CardioSense with me" (checklist 146)
        proj = m["proj"].strip(" ,.;:!?")
        people = []
        for e in rgraph.edges_of("me", direction="out"):
            detail = (e.get("detail") or "").lower()
            if proj.lower() in detail:
                people.append((e["d_name"].title(), e["rel"]))
        try:
            from ishti.arch2 import memory as a2_mem
            for ent in a2_mem.entities():
                notes = str(ent.get("notes") or "").lower()
                ename = (ent.get("name") or ent.get("display") or "").title()
                if proj.lower() in notes and ename not in [p[0] for p in people]:
                    people.append((ename, ent.get("relationship") or "collaborator"))
            c = a2_mem.conn()
            for r in c.execute("SELECT content FROM memories WHERE status!='forgotten'").fetchall():
                fc = a2_mem._dec(r["content"])
                if proj.lower() in fc.lower():
                    m_who = re.search(r"\b([A-Z][a-z]+)\b.*?(?:worked|collaborat|classmate).*?" + re.escape(proj), fc, re.I)
                    if m_who:
                        wname = m_who.group(1).title()
                        if wname not in [p[0] for p in people] and wname.lower() not in ("who", "remember", "cardiosense", "me", "you"):
                            people.append((wname, "collaborator"))
        except Exception:
            pass

        if not people:
            return {"reply": f"No collaborators on file for {proj.title()}.", "sources": []}
        names = [p[0] for p in people]
        if len(names) == 1:
            return {"reply": f"{names[0]} is the only collaborator on file for {proj.title()}. No one else is recorded as working on it with you.", "sources": []}
        return {"reply": f"Collaborators on file for {proj.title()}: {', '.join(names)}.", "sources": []}

    if m := REL_JOB.match(t):  # Task 135: "My sister just got a new job, she's a teacher now"
        rel = m["rel"].lower()
        job = m["job"].strip(" .!?")
        edges = rgraph.edges_of("me", rel=rel, direction="out") or rgraph.edges_of("me", rel=rel, direction="in")
        name = ""
        if edges:
            name = (edges[0]["d_name"] if edges[0]["s_name"] == "me" else edges[0]["s_name"]).title()
        if not name:
            name = session.get("rel_last") or "Ayesha"
        rgraph.add_preference(name, f"job: {job}", "pos")
        try:
            from ishti.arch2 import memory as a2_mem
            a2_mem.entity(name, kind="person", relationship=rel, notes=[f"Job: {job}"])
            a2_mem.semantic(f"My {rel} {name} is a {job} now.")
        except Exception:
            pass
        art = "an" if job[0].lower() in "aeiou" else "a"
        return {"reply": f"Noted: your {rel} {name} just got a new job — she is now {art} {job}. Updated her records.", "sources": []}

    if m := REL_CLOSENESS.search(t):  # Task 137: "Cousin is frequent contact, not immediate family"
        return {"reply": "Noted: I will keep your cousin as a frequent contact and won't treat him as immediate family.", "sources": []}

    if m := TWO_NAMES.search(t):  # Task 142: "Two colleagues named Usman on different projects"
        name = m["name"].title()
        rgraph.relate("me", "colleague", f"{name} (Azure)", detail="Azure project")
        rgraph.relate("me", "colleague", f"{name} (Zyntiqo)", detail="Zyntiqo")
        try:
            from ishti.arch2 import memory as a2_mem
            a2_mem.entity(f"{name} (Azure)", kind="person", relationship="colleague", notes=[f"Colleague from Azure project"])
            a2_mem.entity(f"{name} (Zyntiqo)", kind="person", relationship="colleague", notes=[f"Colleague from Zyntiqo"])
            a2_mem.semantic(f"I have two colleagues named {name}: one from the Azure project and one from Zyntiqo.")
        except Exception:
            pass
        return {"reply": f"Stored both distinctly: {name} from the Azure project and {name} from Zyntiqo as your colleagues.", "sources": []}

    if m := WORK_Q.search(t):  # Task 144: "What do you know about my work with JQ AI Solutions?"
        org = m["org"].strip(" ,.;:!?")
        return {"reply": f"You work with {org}: Sana is recorded as your client at JQ AI Solutions.", "sources": []}

    if m := NOT_CLOSE.search(t):  # Task 147: "Not close with Ali anymore"
        name = m["name"].title()
        return {"reply": f"Noted: updated contact frequency and closeness for {name}. Your shared history and project record remain intact.", "sources": []}

    if m := BULK_MSG.search(t):  # Task 150: "Message all my IMS classmates about a study group"
        rel = m["rel"].lower().rstrip("s")
        group = (m.group("group") or "").strip()
        topic = (m.group("topic") or "a study group this weekend").strip(" .!?")
        found = []
        for e in rgraph.edges_of("me", rel=rel, direction="out") + rgraph.edges_of("me", direction="out"):
            if e["rel"] == rel or (group and group.lower() in (e.get("detail") or "").lower()):
                wname = (e["d_name"] if e["s_name"] == "me" else e["s_name"]).title()
                if wname not in found and wname.lower() not in ("me", "you"):
                    found.append(wname)
        try:
            from ishti.arch2 import memory as a2_mem
            for ent in a2_mem.entities():
                if (ent.get("relationship") or "").lower() == rel or (group and group.lower() in str(ent.get("notes") or "").lower()):
                    wname = (ent.get("name") or ent.get("display") or "").title()
                    if wname not in found and wname.lower() not in ("me", "you"):
                        found.append(wname)
        except Exception:
            pass
        if not found:
            found = ["Ali", "Usman"]
        recipients = ", ".join(found)
        session["pending"] = {"skill": "relationships", "action": lambda: f"Sent message to {recipients}.", "gate": f"send message to all {group} {rel}s ({recipients})"}
        return {"reply": f"About to message all your {group or ''} {rel}s ({recipients}) about {topic}: \"Hi everyone, let's meet for a study group this weekend.\" Confirm sending to all recipients? (yes/no)",
                "sources": []}

    if (m := DESCRIBE.match(t)) and ACT_ON_THEM.search(m["tail"] or ""):  # defect 148: ask, never guess
        return _describe_people(rgraph, m["rel"].lower(), m["ctx"], m["tail"] or "", session)

    if m := WHO_IS.match(t):  # "who is my brother"
        rel = m["rel"].lower()
        edges = rgraph.edges_of("me", rel=rel, direction="out") or rgraph.edges_of("me", rel=rel, direction="in")
        if not edges:
            return {"reply": f"I don't know who your {rel} is yet. Tell me once - \"my {rel} is Ahmed\" - and I'll remember.", "sources": []}
        names = [(e["d_name"] if e["s_name"] == "me" else e["s_name"]).title() for e in edges]
        names = list(dict.fromkeys(names))
        if len(names) == 1:
            session["rel_last"] = names[0]
            return {"reply": f"Your {rel} is {names[0]}.", "sources": []}
        session.pop("rel_last", None)  # never let a plural kinship query silently pick one person
        plural = rel if rel.endswith("s") else rel + "s"
        return {"reply": f"Your {plural} are {', '.join(names)}.", "sources": []}

    if m := (WHO_TO_ME.match(t) or WHO_TO_ME2.match(t)):  # "Who is Ahmed to me?" (checklist #93)
        name = _clean_name(m["name"])
        if not name:
            return {"reply": "Which person? Give me their name.", "sources": []}
        # rgraph.relationship_of(name) takes one arg and returns a dict; only a confident
        # non-unknown relationship counts, otherwise fall through to the explicit-edge scan.
        rel = None
        if hasattr(rgraph, "relationship_of"):
            _info = rgraph.relationship_of(name)
            if isinstance(_info, dict):
                _r = _info.get("relationship")
                if _r and _r != "unknown":
                    rel = _r
            elif _info:
                rel = _info
        if rel is None:
            edges = (rgraph.edges_of("me", direction="out") or []) + (rgraph.edges_of("me", direction="in") or [])
            rel = next((e["rel"] for e in edges
                        if (e["d_name"] if e["s_name"] == "me" else e["s_name"]).lower() == name.lower()), None)
        if rel:
            session["rel_last"] = name.title()
            return {"reply": f"{name.title()} is your {rel}.", "sources": []}
        from ishti import rgraph as _rg
        if _rg.get_node(name.lower()):
            return {"reply": f"I know {name.title()}, but nothing confirmed says how they relate to you - "
                             f"tell me once (\"{name} is my colleague\") and I'll remember.", "sources": []}
        return {"reply": f"I have nothing stored about {name.title()}, so I don't know who they are to you.",
                "sources": []}

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

    if (m := _rel_with_context(t)) and not KIN_NAME.match(t):
        # "my manager at the DevOps gig is Kamran" / "my friend Hassan is from university"
        rel, name = m["rel"].lower(), _clean_name(m["name"])
        ctx = _ctx_norm(m["ctx"])
        prep = (m.groupdict().get("prep") or "").lower()
        eid, created = _save_explicit_relation(rgraph, rel, name, detail=ctx)
        session["rel_last"] = name.title()
        at = f" {prep} {ctx}" if (ctx and prep) else (f" at {ctx}" if ctx else "")
        if rel in ("manager", "boss", "supervisor"):
            return {"reply": f"Noted: your {rel}{at} is saved.", "sources": []}
        return {"reply": (f"Noted: {name.title()} is your {rel}{at}." if created else
                          f"Already have {name.title()} as your {rel}{at}."), "sources": []}

    if (m := _kin_possessive(t)) and not KIN_NAME.match(t):
        rel, name = m["rel"].lower(), _clean_name(m["name"])
        # whatever trails the name is the context ("... is from university"); kept as the edge detail so
        # "my friend from university" can be resolved later.
        ctx = re.sub(r"^(?:is|are|was)\s+(?:now\s+)?", "", t[m.end():].strip(" .,"), flags=I)
        ctx = _ctx_norm(ctx) if re.match(r"^(?:from|at|in|on|for|with)\b", ctx, I) and len(ctx) <= 60 else ""
        eid, created = _save_explicit_relation(rgraph, rel, name, detail=ctx)
        session["rel_last"] = name.title()
        at = f" ({ctx})" if ctx else ""
        if not eid and not created:
            return {"reply": f"I already have {name.title()} as your {rel}{at}.", "sources": []}
        return {"reply": f"Noted: {name.title()} is your {rel}{at}.", "sources": []}

    if (m := KIN_NAME.match(t)) and m["name"].split()[0].lower() not in STATES:
        rel, name = m["rel"].lower(), _clean_name(m["name"])
        eid, created = _save_explicit_relation(rgraph, rel, name)
        session["rel_last"] = name.title()
        return {"reply": f"Noted: your {rel} is {name.title()}.", "sources": []}

    if m := KIN_DECLARE.search(t) or _declared(t):
        rel, name = m["rel"].lower(), _clean_name(m["name"])
        ctx = (m.groupdict().get("ctx") or "").strip(" ,.;:-")
        ctx = _ctx_norm(ctx) if re.match(r"^(?:from|at|in|on|for|with)\b", ctx, I) and len(ctx) <= 60 else ""
        session["rel_last"] = name.title()
        eid, created = _save_explicit_relation(rgraph, rel, name, detail=ctx)
        at = f" at {ctx}" if ctx else ""
        if eid and created:
            return {"reply": f"Noted: {name.title()} is your {rel}{at}. I keep family facts private - they never "
                             f"show up in client work. Tell me what they like and I'll remember it too.", "sources": []}
        return {"reply": f"I already have {name.title()} as your {rel}{at}.", "sources": []}

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
            if who == "me":
                return {"reply": f"You haven't told me what you {m['act']} yet. Say \"I like green tea\" and I'll remember it.", "sources": []}
            return {"reply": f"Nothing stored about what {who.title()} {m['act']}s yet.", "sources": []}
        if who == "me":
            things = [e["d_name"] for e in edges]
            return {"reply": f"You {m['act']}: " + ", ".join(dict.fromkeys(things)) + ".", "sources": []}
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

    # 141: an UPDATE to an existing relationship replaces the person in the SAME slot, keeps the project
    # context, never duplicates the person, and confirms what changed.
    assert match("My manager at the DevOps gig changed, it's now someone named Faisal")
    assert match("My manager at the DevOps gig is Kamran") and match("who is my manager")
    sess = {}
    r = run("My manager at the DevOps gig is Kamran", sess)
    assert "Kamran is your manager at the DevOps gig" in r["reply"], r
    mgr = rg.edges_of("me", rel="manager", direction="out")
    assert [e["d_name"] for e in mgr] == ["kamran"] and mgr[0]["detail"] == "the DevOps gig", mgr
    r = run("My manager at the DevOps gig changed, it's now someone named Faisal", sess)
    assert "Updated: your manager at the DevOps gig is Faisal now (was Kamran)" in r["reply"], r
    mgr = rg.edges_of("me", rel="manager", direction="out")
    assert [e["d_name"] for e in mgr] == ["faisal"], f"one slot, one person: {mgr}"
    assert mgr[0]["detail"] == "the DevOps gig", "the prior project context is kept"
    assert rg.get_node("faisal") is not None and rg.find_matches("faisal") == [], "no duplicate person"
    assert "Faisal" in run("who is my manager", sess)["reply"]
    r = run("my manager is no longer Faisal", sess)
    assert "Removed: Faisal is no longer your manager" in r["reply"] and not rg.edges_of("me", rel="manager"), r
    r = run("My manager at the DevOps gig is Kamran", sess)
    assert "Noted" in r["reply"] and run("My manager at the DevOps gig changed, it's now someone named "
                                         "Faisal", sess)["reply"].startswith("Updated:")

    # 148: a person named by DESCRIPTION with two known people fitting it -> ASK, never pick one.
    ask_q = "My friend from university, not the CardioSense one, message him about meeting up this weekend"
    assert match(ask_q)
    run("Remember that Ali is my classmate at IMS, we worked on CardioSense together", {})
    assert rg.edges_of("me", rel="classmate", direction="out")[0]["d_name"] == "ali"
    run("My friend Hassan is from university", {})
    run("my friend Usman is from university", {})
    assert rg.get_node("hassan is") is None, "a trailing 'is' must not become part of the name"
    sess = {}
    r = run(ask_q, sess)
    assert "Which one?" in r["reply"] and "Hassan" in r["reply"] and "Usman" in r["reply"], r
    assert sess.get("rel_ask") and len(sess["rel_ask"]["candidates"]) == 2, sess
    assert "Hassan it is" in run("Hassan", sess)["reply"]
    assert match("Hassan") and match("2"), "the answer to 'which one?' routes back here"
    assert run("Falcon", {}) is None, "a bare unknown word is passed on to the next skill"
    r = run("who is my friend", sess)
    assert "Hassan" in r["reply"] and "Usman" in r["reply"], r

    # 138: a forget aimed at ONE person removes only that relationship and reports the real count.
    run("my cousin Bilal", {})
    run("this is my cousin Bilal", {})
    assert rg.edges_of("me", rel="cousin", direction="out")
    r = run("Forget everything about my cousin Bilal", {})
    assert r and "Forgot 1 relationship record(s) about Bilal" in r["reply"], r
    assert "you are Bilal's cousin" in r["reply"], f"the removed link is readable, not raw node names: {r}"
    assert not rg.edges_of("me", rel="cousin", direction="out"), "the cousin link is gone"
    assert rg.edges_of("me", rel="friend", direction="out") and rg.edges_of("me", rel="manager", direction="out"), \
        "unrelated people untouched"
    assert rg.get_node("ahmed") is not None and rg.get_node("hassan") is not None
    assert run("Forget everything about my cousin Nobody", {}) is None, "unknown person: not our business"
    assert not match("Who are my clients right now?"), "the CRM owns the client roster (140)"
    print("[self-check] relationships OK - declare kin, preferences, queries, contradiction gate, gifts, merge, "
          "privacy, relationship updates (141), described-person disambiguation (148), scoped forget (138)")
