"""ARCH2 memory consolidation (spec section 12): after each conversation a lightweight
extraction pass decides what is worth remembering - never everything.

Input: the exchange (request + final answer) + the route taken.
Output (JSON from the local model, validated): new semantic facts, entities+relationships,
new procedures, behaviour observations, episodic events. The model only PROPOSES; the
rules below decide what is stored (deduped, typed, thresholded).

Explicit vs observed (spec 10): anything the owner stated gets source="explicit_user" with
high confidence; everything else is stored as an observation and only becomes a belief via
memory.promote_observations()'s configurable thresholds. Sensitive family inferences are
refused here (spec 8/10): observed chatter can never create a kin edge, only an explicit
statement can.
"""
import json
import re

from ishti.arch2 import memory

PROMPT = """You are the memory extractor of a personal assistant. From this exchange, decide what is worth
remembering LONG-TERM about the owner. Never store transient details, the answer itself, or anything
the owner would not want kept. Family relationships may ONLY be extracted when the owner explicitly
stated them in their message ("Ahmed is my brother"); never inferred from chatter.

Exchange:
Owner: {request}
Assistant: {reply[:800]}

Reply with ONLY a JSON object:
{{"semantic": ["stable preference or fact about the owner"],
  "episodic": ["important event worth keeping: decisions, commitments, meetings, project milestones"],
  "procedural": [{{"name": "workflow name", "steps": ["step 1", "step 2"]}}],
  "entities": [{{"name": "person/place/project name", "kind": "person|organisation|project|app",
                 "relationship": "friend|brother|colleague|... (only if the owner stated it)", "note": "one fact"}}],
  "observations": ["repeated behaviour hints: style corrections, repeated requests"],
  "forget": ["exact content of a memory the owner asked to forget"]}}
Empty lists are fine. If nothing is worth remembering, reply {{}}."""

PROMPT_RE = re.compile(r"\{.*\}", re.S)
NEVER_RELATION = "unknown"


def extract(text: str, reply: str) -> dict:
    """One extraction pass. Never raises: a broken model/JSON returns {} and the turn is just not learned."""
    try:
        from ishti import llm  # lazy: openai import lives here, arch2 must load without it
        out = llm.ask(PROMPT.format(request=text[:1200], reply=reply[:800]),
                      role="main", system=None, temperature=0, max_tokens=600,
                      response_format={"type": "json_object"})
        m = PROMPT_RE.search(out or "")
        d = json.loads(m.group(0)) if m else {}
        return d if isinstance(d, dict) else {}
    except Exception as exc:
        print(f"[arch2] consolidation skipped: {type(exc).__name__}: {exc}")
        return {}


def _strs(v) -> list:
    return [str(x).strip()[:300] for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def apply(d: dict, request: str = "") -> dict:
    """Write the extractor's proposal into memory with the explicit-vs-observed rules. Returns counts."""
    stored = {"semantic": 0, "episodic": 0, "procedural": 0, "entities": 0, "observations": 0, "forgotten": 0}
    if not isinstance(d, dict):
        return stored
    req_low = (request or "").lower()
    for f in _strs(d.get("semantic")):
        explicit = _explicit_in_request(req_low, f)
        r = memory.reconcile(f, source="explicit_user" if explicit else "inference",
                             importance=0.75 if explicit else 0.45,
                             confidence=0.95 if explicit else 0.5)
        stored["semantic"] += 1 if r.get("action") != "NOOP" else 0
    for e in _strs(d.get("episodic")):
        memory.episodic(e)
        stored["episodic"] += 1
    for p in (d.get("procedural") or [])[:3]:
        if isinstance(p, dict) and p.get("name") and p.get("steps"):
            memory.procedural(str(p["name"])[:120], steps=[str(s)[:200] for s in p["steps"]][:12],
                              keywords=[str(p["name"]).lower()])
            stored["procedural"] += 1
    for ent in (d.get("entities") or [])[:5]:
        if not isinstance(ent, dict) or not ent.get("name"):
            continue
        rel = str(ent.get("relationship") or NEVER_RELATION).lower().strip()
        kin = {"brother", "sister", "mother", "father", "son", "daughter", "spouse", "partner",
               "uncle", "aunt", "cousin", "grandfather", "grandmother", "nephew", "niece", "grandchild"}
        if rel in kin:
            # kin only from an explicit statement in THIS request (spec 8/10)
            name_low = str(ent["name"]).lower()
            stated = re.search(rf"\b(?:my|our)\s+{re.escape(rel)}s?\b[^.]{{0,30}}\b{re.escape(name_low)}\b", req_low) \
                or re.search(rf"\b{re.escape(name_low)}\b\s+is\s+my\s+{re.escape(rel)}\b", req_low)
            if not stated:
                rel = NEVER_RELATION
        memory.upsert_entity(str(ent["name"])[:60], kind=str(ent.get("kind") or "person"),
                             relationship=rel or None,
                             confidence=0.95 if rel != NEVER_RELATION else 0.5,
                             note=str(ent.get("note") or "")[:200] or None)
        stored["entities"] += 1
    for o in _strs(d.get("observations")):
        memory.observe(o)
        stored["observations"] += 1
    for f in _strs(d.get("forget")):
        stored["forgotten"] += memory.forget(f)
    return stored


def _explicit_in_request(req_low: str, fact: str) -> bool:
    """A stored fact counts as explicit when the owner's own words carried it:
    'remember that I ...', 'I prefer/like ...', or most of the fact's words present."""
    if not req_low:
        return False
    if "remember that i" in req_low or "i prefer" in req_low or "i like" in req_low or "i use" in req_low:
        return True
    words = [w for w in re.findall(r"[a-z]+", fact.lower()) if len(w) > 3]
    return bool(words) and sum(w in req_low for w in words) >= max(2, len(words) - 1)


def consolidate(request: str, reply: str) -> dict:
    """The one call the dispatcher makes at _finish time. Cheap no-op when the extractor finds nothing."""
    d = extract(request, reply)
    return apply(d, request) if d else {}


# --------------------------------------------------------------------- user commands (spec 18)
WHAT_RE = re.compile(r"\bwhat\s+do\s+you\s+(?:remember|know)\s+about\s+me\b", re.I)
WHO_RE = re.compile(r"\bwho\s+is\s+(?P<who>[a-z][a-z .'-]{1,40})\??", re.I)
FAMILY_RE = re.compile(r"\bwhat\s+do\s+you\s+know\s+about\s+my\s+family\b", re.I)
LEARNED_RE = re.compile(r"\b(?:what\s+preferences\s+have\s+you\s+learned|what\s+have\s+you\s+learned(?:\s+from\s+my\s+behavior|\s+about\s+me)?)\b", re.I)
FORGET_RE = re.compile(r"\bforget\s+(?:everything\s+about\s+)?(?:that\b\.?|about\s+)?(?P<what>.{2,120}?)\.?$", re.I)
REMEMBER_RE = re.compile(r"\b(?:remember|keep\s+in\s+mind)\s+that\s+(?P<fact>.{3,200})", re.I)
NOTHING_RE = re.compile(r"\b(?:don'?t|do\s+not|never)\s+remember\s+this(?:\s+conversation)?\b", re.I)
KIN = {"brother", "sister", "mother", "father", "son", "daughter", "spouse", "partner",
       "uncle", "aunt", "cousin", "grandfather", "grandmother", "grandchild", "family"}


def handle_command(text: str) -> str | None:
    """The spec-18 command set: 'what do you remember about me', 'remember that I ...',
    'forget that', 'forget everything about Ahmed', 'who is Ahmed', 'what do you know
    about my family', 'what have you learned from my behavior', 'don't remember this
    conversation'. Returns the reply, or None when the text is not a memory command."""
    t = (text or "").strip()
    if NOTHING_RE.search(t):
        return ("Understood - I won't keep anything from this conversation. What I saved\n"
                "before it stays; tell me 'forget <thing>' if you want any of it gone too.")
    if FAMILY_RE.search(t):
        rows = memory.search(limit=200)
        family = [r["content"] for r in rows if any(k in r["content"].lower() for k in KIN)]
        lines = [f"- {n}: {rel}" for n, rel in _family_entities()]
        lines += [f"- {c}" for c in family]
        return ("Your family, as I have it:\n" + "\n".join(lines)) if lines \
            else ("I don't have anything about your family yet. Tell me - 'Sara is my sister' -\n"
                  "and I'll remember it (never inferred, only what you say).")
    if WHAT_RE.search(t):
        rows = memory.search(limit=12)
        return ("Here is what I remember about you:\n" + "\n".join(f"- {r['content']}" for r in rows)) \
            if rows else "Nothing yet - tell me things and I'll remember them."
    if LEARNED_RE.search(t):
        sem = memory.search(mtype="semantic", limit=8)
        obs = memory.search(mtype="observation", limit=6)
        lines = [f"- [learned] {r['content']}" for r in sem if r["source"] != "explicit_user"] + \
                [f"- [watching, {r['times_observed']}x so far] {r['content']}" for r in obs]
        return ("Patterns I picked up from your behaviour:\n" + "\n".join(lines)) if lines \
            else "No learned patterns yet - I only store what you tell me so far."
    m = WHO_RE.search(t)
    if m:
        who = m.group("who").strip(" ?.!")
        card = memory.entity_card(who)
        if card:
            return card
        from ishti import rgraph
        n = rgraph.get_node(who)
        if n:
            edges = [rgraph.describe_edge(e) for e in rgraph.edges_of(who)[:6]]
            return "\n".join(edges) if edges else f"I have a node for {who} but no relationships yet."
        return f"I don't know who {who} is yet. Tell me about them and I'll remember."
    m = REMEMBER_RE.search(t)
    if m:
        memory.semantic(m.group("fact").strip().rstrip("."), source="explicit_user",
                        importance=0.8, confidence=0.95)
        return f"Remembered: {m.group('fact').strip().rstrip('.')}"
    m = FORGET_RE.search(t)
    if m:
        what = m.group("what").strip().rstrip(".")
        if what.lower() in ("everything", "all of it", "it all"):
            n = len(memory.search(limit=1000))
            memory.wipe_all()
            return f"Forgot everything ({n} memories)."
        # 'forget everything about X' / 'forget X': the person's entity + their memories
        target = re.sub(r"^(?:everything\s+)?about\s+", "", what, flags=re.I).strip()
        e = memory.find_entity(target)
        forgotten = memory.forget_person(target) if e else 0
        if not forgotten:
            forgotten = memory.forget(what) or memory.forget(
                re.sub(r"^(?:about|that)\s+", "", what, flags=re.I))
        if e and forgotten:
            memory.upsert_entity(target, relationship="unknown", confidence=0.3,
                                 note="the owner asked me to forget about them")
        return "Forgot it." if forgotten else f"I don't have a memory matching '{what}'."
    return None


def _family_entities() -> list:
    """(display name, relationship) for every person tied by a kin word or an rgraph kin edge."""
    from ishti import rgraph
    out = []
    for e in memory.entities(limit=200):
        rel = (e.get("relationship") or "").lower()
        if rel in KIN:
            out.append((e.get("display") or e["name"], rel))
    for node_row in rgraph.conn().execute("SELECT name FROM nodes WHERE kind='person'").fetchall():
        for edge in rgraph.edges_of(node_row["name"], direction="in"):
            if edge["rel"] in KIN and edge["s_name"] == rgraph.OWNER:
                if all(n != node_row["name"] for n, _ in out):
                    out.append((node_row["name"], edge["rel"]))
    return out
