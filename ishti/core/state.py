"""Conversation State Manager (Part D1).

One place, per chat session, that remembers WHAT we are working on:
  - active_topic          e.g. "Sony WH-1000XM5 headphones"
  - topic_history         previous topics (newest last) — disambiguates "the cheapest one"
  - active_task + stack   "research" -> "find the cheapest current price"
  - key_entities          products, people, places, companies mentioned
  - last_searches         [{query, ts, urls}] for "those results"
  - constraints           budget / country / currency / brand / date range
  - open_questions        what the assistant still owes the owner

Design rules:
- plain dicts/JSON only, so state survives an architecture switch (T5), a restart,
  and a move to another machine; never a reference to a skill, model or session.
- storage pluggable (Store protocol): MemoryStore for tests, SQLiteStore default in
  its own file next to the conversation sessions (never inside the chat store).
- text utilities (norm_topic / subject_of / task_of) live HERE so both the state
  updater and the resolver share one understanding of "subject" and "task".
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Protocol

# --------------------------------------------------------------------- shared text utilities

BRAND_WORDS = re.compile(
    r"\b(?:sony|apple|iphone|ipad|samsung|galaxy|google|pixel|xiaomi|oneplus|lenovo|dell|hp|asus|nvidia|amd|"
    r"razer|bose|anker|toyota|honda|suzuki|nike|adidas|rtx|macbook|thinkpad|playstation|xbox|airpods|"
    r"wh-1000xm\d|galaxy\s*buds)\b", re.I)
PRODUCT_CODE = re.compile(r"\b[a-z]{0,10}-?\d{3,6}[a-z]{0,3}\b", re.I)

# Task verbs: a subject-less message that carries one of these is a follow-up on the topic.
TASK = re.compile(
    r"\b(?:deep\s+research|research|investigate|dig\s+into|find\s+out|go\s+deeper|cheapest|lowest|"
    r"best\s+price|price|prices|compare|buy|purchase|order|search|look\s+up|google|summar(?:y|ise|ize)|"
    r"review|reviews|specs?|specifications|deal|deals|cheap|cost)\b", re.I)

PRICE_TASK = re.compile(r"\b(?:cheapest|lowest\s+price|best\s+price|price\s+check|compare\s+prices|find\s+the\s+price|cost)\b", re.I)
RESEARCH_TASK = re.compile(r"\b(?:deep\s+research|research|investigate|dig\s+into|find\s+out\s+more|go\s+deeper)\b", re.I)
AMBIG_ONE = re.compile(r"\b(?:the\s+)?(?:cheapest|best|better)\s+one\b", re.I)

# Leading filler stripped before analysis. Careful: "ishti" is NOT filler — the memory
# skill matches sentences that START with it ("ISHTI is now working").
FILLER = re.compile(r"^(?:\s*(?:please|now|then|also|and|next|first|just|ok(?:ay)?|so|hey)[,!.?]?\s*)+", re.I)

# Task phrases and reference words stripped when looking for the message's own subject.
_TASK_PHRASES = re.compile(
    r"\b(?:please|now|also|and\s+then|then|first|second|next|just)\b|"
    r"\b(?:go|let's|lets)\s+(?:deep\s+)?(?:research|search|check|look)?\b|"
    r"\b(?:deep\s+)?research(?:\s+(?:the|it))?|\bsearch(?:\s+(?:the|on|for|about))?|\blook\s+up|\b(?:the\s+)?(?:web|internet|net|google|online)\b|"
    r"\b(?:find|look|check|get|give|tell|show)\s+(?:me\s+)?(?:the\s+)?|\b(?:and|or)\b|"
    r"\b(?:the\s+)?cheapest(?:\s+current)?\s+(?:price|one)\b|\bbest\s+(?:price|one)\b|\bthe\s+best\s+one\b|\bprice\b|\bprices\b|\bdeals?\b|"
    r"\bcompare\b|\bbuy\b|\bpurchase\b|\border\b|\binvestigate\b|\bdig\s+into\b|\bfind\s+out(?:\s+more)?\b|"
    r"\bgo\s+deeper\b|\bsummar(?:y|ise|ize)\b|\breview(?:s)?\b|\bspecs?\b|\bspecifications\b|\bcost\b|\bcheap\b|\blowest\b|"
    r"\bfor\s+me\b|\babout\s+it\b|\bof\s+it\b|\bon\s+it\b|\bfor\s+that\b|\bof\s+that\b|\bon\s+that\b", re.I)
_REFERENCE_WORDS = re.compile(
    r"\b(?:it|that|this|those|these|them|they|the\s+same|same|one|those|results?|topic|subject)\b", re.I)

REFERENCES = re.compile(
    r"\b(?:it|that|this|those|these|them|they|the\s+same|same\s+one|those\s+results|the\s+results|"
    r"the\s+topic|the\s+subject|go\s+deeper|deeper|dig\s+deeper|more\s+(?:about|on|details)|"
    r"compare\s+them|the\s+cheapest\s+one|the\s+best\s+one|the\s+one|one\s+of\s+them)\b", re.I)

FOLLOWUP_HINTS = re.compile(
    r"\b(?:it|that|this|those|these|them|the\s+same|same\s+one|they|those\s+results|the\s+results|"
    r"the\s+topic|go\s+deeper|deeper|compare\s+them|find\s+out\s+more|more\s+about|the\s+cheapest\s+one|"
    r"the\s+best\s+one|also)\b", re.I)

GREETINGS = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no", "sure", "yo",
             "good morning", "good evening", "good night", "good afternoon", "salam", "aoa", "thanks!",
             "great", "nice", "cool", "bye", "goodbye"}

# questions ABOUT the assistant are never research topics
META_Q = re.compile(r"^\s*(?:what(?:'s| is| are)|who\s+are\s+you|what\s+can|how\s+(?:do|are)\s+you|"
                    r"what\s+do\s+you|can\s+you\s+help)\b", re.I)

PRICES = re.compile(r"\b(?:under|below|less\s+than|cheaper\s+than|max(?:imum)?\s*|budget(?:\s+of)?\s*|around|about|~)\s*"
                    r"(?P<amt>[\d,]+(?:\.\d+)?)\s*(?P<cur>usd|eur|gbp|pkr|rs\.?|rupees|dollars?|pounds|euros|k)?\b", re.I)
CURRENCIES = re.compile(r"\b(?P<cur>usd|eur|gbp|pkr|rupees|dollars|euros|pounds|rs)\b", re.I)
COUNTRIES = re.compile(r"\bin\s+(?P<c>pakistan|usa|us|uk|canada|australia|germany|india|uae|saudi\s+arabia|china|japan)\b"
                       r"|\b(?:ship(?:ped)?|deliver(?:y|ed)?)\s+(?:to\s+)?(?P<c2>pakistan|usa|uk|canada|india|uae)\b", re.I)
DATE_RANGE = re.compile(r"\b(?P<dr>this\s+year|last\s+year|this\s+month|black\s+friday|today|recent(?:ly)?|202\d)\b", re.I)

_CUR_MAP = {"rs": "PKR", "rupees": "PKR", "dollars": "USD", "dollar": "USD", "euros": "EUR", "euro": "EUR",
            "pounds": "GBP", "pound": "GBP", "usd": "USD", "eur": "EUR", "gbp": "GBP", "pkr": "PKR"}
_CTY_MAP = {"US": "US", "USA": "US", "UK": "UK", "PAKISTAN": "Pakistan", "INDIA": "India", "CANADA": "Canada",
            "GERMANY": "Germany", "UAE": "UAE", "AUSTRALIA": "Australia", "SAUDI ARABIA": "Saudi Arabia",
            "CHINA": "China", "JAPAN": "Japan"}


def norm_topic(text: str) -> str:
    """Strip request verbs and fillers so the topic is the THING, not the ask."""
    t = re.sub(r"\s+", " ", text or "").strip()
    for _ in range(3):
        t = FILLER.sub("", t)
    t = re.sub(r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
               r"(?:deep\s+)?(?:research|search(?:\s+(?:the|on)?\s*(?:web|internet|google|online))?|look\s+up|find|"
               r"tell\s+me\s+about|learn\s+about|read\s+about|explore|check(?:\s+out)?|compare|investigate)\s+",
               "", t, flags=re.I)
    t = re.sub(r"^(?:the\s+internet\s+)?(?:for\s+|about\s+|on\s+)", "", t, flags=re.I)
    return t.strip(" .!?,\"'")


def subject_of(text: str) -> str:
    """The message's own subject with task verbs and reference words stripped; '' when
    the message carries none ("cheapest price?" -> ''). Any brand/product code counts."""
    t = FILLER.sub("", (text or "").strip())
    t = _TASK_PHRASES.sub(" ", t)
    t = _REFERENCE_WORDS.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.!?-")
    if not t:
        return ""
    if PRODUCT_CODE.search(t) or BRAND_WORDS.search(t):
        return t
    return t if len(t.split()) >= 2 else ""


def task_of(text: str) -> str | None:
    """The task the message asks for ('find the cheapest current price'...), else None."""
    if PRICE_TASK.search(text):
        return "find the cheapest current price"
    if RESEARCH_TASK.search(text):
        return "research in depth"
    if re.search(r"\bcompare\b", text, re.I):
        return "compare"
    if re.search(r"\b(?:buy|purchase|order)\b", text, re.I):
        return "buy"
    if re.search(r"\bsummar\w*\b", text, re.I):
        return "summarize"
    if re.search(r"\b(?:search|look\s+up|google|find)\b", text, re.I):
        return "search"
    return None


def spot_entities(text: str) -> list[dict]:
    """Deterministic entity spotting: brands and product codes, deduped, in order."""
    out, seen = [], set()
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9]*(?:[-\s][A-Za-z0-9]+)*", text or ""):
        cand = m.group(0).strip()
        low = cand.lower()
        if low in seen or len(cand) < 3:
            continue
        kind = None
        if re.search(r"\d{3,}", cand) and re.search(r"[a-z]", cand, re.I):
            kind = "product"
        elif low in ("sony", "apple", "samsung", "google", "xiaomi", "oneplus", "lenovo", "dell", "hp",
                     "asus", "nvidia", "amd", "razer", "bose", "anker", "toyota", "honda", "suzuki",
                     "nike", "adidas"):
            kind = "company"
        elif low in ("iphone", "galaxy", "pixel", "macbook", "airpods", "playstation", "xbox", "thinkpad"):
            kind = "product"
        if kind:
            seen.add(low)
            out.append({"name": cand, "kind": kind})
    return out


def spot_constraints(text: str) -> dict[str, str]:
    """Deterministic constraint extraction (budget, currency, country, date range)."""
    out: dict[str, str] = {}
    if m := PRICES.search(text or ""):
        cur = (m.group("cur") or "").lower().rstrip(".")
        cur = _CUR_MAP.get(cur, cur.upper())
        out["budget"] = f"{m.group('amt')} {cur}".strip()
    elif m := CURRENCIES.search(text or ""):
        out["currency"] = _CUR_MAP.get(m.group("cur").lower(), m.group("cur").upper())
    if m := COUNTRIES.search(text or ""):
        c = (m.group("c") or m.group("c2") or "").upper()
        out["country"] = _CTY_MAP.get(c, c)
    if m := DATE_RANGE.search(text or ""):
        out["date_range"] = m.group("dr").lower()
    return out


# ----------------------------------------------------------------------------- model

class ConversationState:
    """Pure data — everything JSON-serialisable, no behaviour, no back-references."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.active_topic: str = ""
        self.topic_history: list[str] = []
        self.active_task: str = ""
        self.task_stack: list[str] = []
        self.entities: dict[str, dict] = {}          # name(lower) -> {name, kind, first_seen, turns}
        self.last_searches: list[dict] = []          # newest last: {query, ts, urls}
        self.constraints: dict[str, str] = {}        # budget/country/currency/brand/date_range
        self.open_questions: list[str] = []
        self.ambiguous_refs: list[str] = []          # unresolved "the cheapest one" candidates
        self.updated_at: float = time.time()
        self.turn: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "active_topic": self.active_topic,
            "topic_history": list(self.topic_history),
            "active_task": self.active_task,
            "task_stack": list(self.task_stack),
            "entities": self.entities,
            "last_searches": list(self.last_searches),
            "constraints": dict(self.constraints),
            "open_questions": list(self.open_questions),
            "ambiguous_refs": list(self.ambiguous_refs),
            "updated_at": self.updated_at,
            "turn": self.turn,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConversationState":
        s = cls(d.get("session_id", ""))
        s.active_topic = d.get("active_topic", "")
        s.topic_history = list(d.get("topic_history", []))
        s.active_task = d.get("active_task", "")
        s.task_stack = list(d.get("task_stack", []))
        s.entities = dict(d.get("entities") or {})
        s.last_searches = list(d.get("last_searches") or [])
        s.constraints = dict(d.get("constraints") or {})
        s.open_questions = list(d.get("open_questions") or [])
        s.ambiguous_refs = list(d.get("ambiguous_refs") or [])
        s.updated_at = float(d.get("updated_at") or 0.0)
        s.turn = int(d.get("turn") or 0)
        return s


# --------------------------------------------------------------------- stores

class Store(Protocol):
    def load(self, session_id: str) -> dict | None: ...
    def save(self, session_id: str, data: dict) -> None: ...


class MemoryStore:
    """In-memory store — the default for tests; nothing touches disk."""

    def __init__(self):
        self._d: dict[str, dict] = {}

    def load(self, session_id):
        d = self._d.get(session_id)
        return json.loads(json.dumps(d)) if d else None

    def save(self, session_id, data):
        self._d[session_id] = json.loads(json.dumps(data))


class SQLiteStore:
    """Default store: one JSON blob per session in its own SQLite file."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS convo_state "
                      "(session_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL)")

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def load(self, session_id):
        with self._lock, self._conn() as c:
            row = c.execute("SELECT data FROM convo_state WHERE session_id=?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, session_id, data):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO convo_state (session_id, data, updated) VALUES (?,?,?) "
                      "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                      (session_id, json.dumps(data, ensure_ascii=False), time.time()))


def default_db_path() -> str:
    if os.environ.get("ISHTI_STATE_DB"):
        return os.environ["ISHTI_STATE_DB"]
    from ishti.test_mode import scratch_path
    try:
        from orchestrator.memory.conversation import SESSIONS_DIR
        return scratch_path(os.path.join(os.path.dirname(SESSIONS_DIR), "convo_state.db"))
    except Exception:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return scratch_path(os.path.join(root, "data", "convo_state.db"))


# ----------------------------------------------------------------------------- manager

class ConversationStateManager:
    """Per-session state with update-on-every-turn and a prompt-ready summary."""

    def __init__(self, store: Store | None = None):
        self.store = store or SQLiteStore(default_db_path())
        self._lock = threading.Lock()

    # ---- lifecycle ---------------------------------------------------------
    def get(self, session_id: str) -> ConversationState:
        d = self.store.load(session_id)
        return ConversationState.from_dict(d) if d else ConversationState(session_id)

    def save(self, state: ConversationState) -> None:
        state.updated_at = time.time()
        self.store.save(state.session_id, state.to_dict())

    @contextmanager
    def turn(self, session_id: str) -> Iterator[ConversationState]:
        """Load-mutate-save in one critical section: safe when a follow-up and its
        parent request run at once."""
        with self._lock:
            state = self.get(session_id)
            state.turn += 1
            yield state
            self.save(state)

    # ---- updates -----------------------------------------------------------
    def observe_user_turn(self, state: ConversationState, text: str, llm_extract: dict | None = None) -> None:
        """Update state from a user message. Deterministic extraction always runs; the
        optional `llm_extract` JSON ({topic, task, entities, constraints}) only enriches it."""
        text = (text or "").strip()
        if not text:
            return
        # the state is persisted (state.db) and summarised into later prompts: a typed password became the "active
        # topic" in plain text before the turn's own masking ran
        from ishti.audit import SECRETISH
        text = SECRETISH.sub("[redacted]", text)
        low = text.lower().strip(" .!?")
        is_greeting = low in GREETINGS or bool(META_Q.match(text))
        is_followup = bool(FOLLOWUP_HINTS.search(text))

        # entities
        for ent in spot_entities(text):
            self.note_entity(state, ent["name"], ent["kind"])
        for ent in (llm_extract or {}).get("entities", []):
            if isinstance(ent, dict) and ent.get("name"):
                self.note_entity(state, ent["name"], ent.get("kind", "other"))

        # topic: a NEW subject on the message wins (T2); a pure reference keeps the old one.
        subj = subject_of(text)
        task = task_of(text)
        explicit = (llm_extract or {}).get("topic") or ""
        new_topic = None
        if explicit:
            new_topic = norm_topic(explicit)
        elif subj and (task or strong_subject(subj)) and st_differs(subj, state.active_topic):
            new_topic = norm_topic(subj)
        elif task is None and not is_followup and not is_greeting:
            # no task verbs and no references: a conversational subject ("tell me about falcon")
            # — a task-carrying sentence with no subject is a follow-up ON the topic, never a new one
            cand = norm_topic(text)
            if cand and len(cand.split()) >= 1:
                new_topic = cand
        if new_topic and new_topic.lower() != (state.active_topic or "").lower():
            if state.active_topic:
                state.topic_history.append(state.active_topic)
                state.topic_history = state.topic_history[-5:]
            state.active_topic = new_topic
            state.ambiguous_refs = [a for a in state.ambiguous_refs
                                    if a.lower() not in new_topic.lower()]
        elif new_topic:
            state.active_topic = new_topic

        # task stack
        task_name = (llm_extract or {}).get("task") or (task or "")
        if task_name:
            self.push_task(state, task_name, push=bool((llm_extract or {}).get("push_task", True)))

        # constraints merge (never cleared by follow-ups)
        for k, v in {**spot_constraints(text), **(llm_extract or {}).get("constraints", {})}.items():
            if v:
                state.constraints[k] = str(v)

        # ambiguity candidates: products/companies the anchored topic doesn't cover (T3)
        if not (llm_extract or {}).get("ambiguous"):
            refs = [e["name"] for e in state.entities.values()
                    if e["kind"] in ("product", "company") and st_differs(e["name"], state.active_topic)]
            state.ambiguous_refs = refs[-4:]

    def note_entity(self, state: ConversationState, name: str, kind: str = "other") -> None:
        key = name.lower().strip()
        if not key:
            return
        row = state.entities.get(key) or {"name": name.strip(), "kind": kind,
                                          "first_seen": time.time(), "turns": 0}
        row["turns"] += 1
        if kind != "other":
            row["kind"] = kind
        state.entities[key] = row

    def push_task(self, state: ConversationState, task: str, push: bool = True) -> None:
        task = re.sub(r"\s+", " ", (task or "").strip().lower())
        if not task:
            return
        if push and state.active_task and state.active_task != task:
            state.task_stack.append(state.active_task)
            state.task_stack = state.task_stack[-5:]
        state.active_task = task

    def record_search(self, session_id: str, query: str, urls: list[str] | None = None) -> None:
        """Search skills call this AFTER a search so "those results" has an anchor."""
        with self._lock:
            state = self.get(session_id)
            state.last_searches.append({"query": (query or "").strip(), "ts": time.time(),
                                        "urls": (urls or [])[:8]})
            state.last_searches = state.last_searches[-5:]
            if not state.active_topic:
                state.active_topic = norm_topic(query)
            self.save(state)

    # ---- prompt views -------------------------------------------------------
    @staticmethod
    def summary(state: ConversationState, max_chars: int = 900) -> str:
        """The pinned context block (D3/D4) — goes into every prompt, every tool call,
        every sub-agent. ALWAYS includes topic/task/constraints; compact by design."""
        lines: list[str] = []
        if state.active_topic:
            lines.append(f"ACTIVE TOPIC: {state.active_topic}")
        if state.active_task:
            stack = " -> ".join(state.task_stack[-3:] + [state.active_task]) if state.task_stack else state.active_task
            lines.append(f"ACTIVE TASK: {stack}")
        ents = [e["name"] for e in sorted(state.entities.values(), key=lambda x: -x["turns"])[:8]]
        if ents:
            lines.append(f"KEY ENTITIES: {', '.join(ents)}")
        if state.constraints:
            lines.append("CONSTRAINTS: " + "; ".join(f"{k}: {v}" for k, v in state.constraints.items()))
        if state.last_searches:
            last = state.last_searches[-1]
            urls = " ".join(f"<{u}>" for u in last.get("urls", [])[:3])
            lines.append(f"LAST SEARCH: \"{last['query']}\" {urls}".strip())
        if state.open_questions:
            lines.append("OPEN QUESTIONS: " + "; ".join(state.open_questions[-3:]))
        return "\n".join(lines)[:max_chars]


def st_differs(a: str, b: str) -> bool:
    """True when candidate subject `a` is not already covered by topic `b`."""
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return True
    return a not in b and b not in a


def strong_subject(text: str) -> bool:
    """A brand or product code — strong enough to switch the topic on its own
    ("also look at Samsung S25" switches even without a search verb)."""
    return bool((text or "") and (BRAND_WORDS.search(text) or PRODUCT_CODE.search(text)))


_manager: "ConversationStateManager | None" = None
_manager_lock = threading.Lock()


def get_manager() -> "ConversationStateManager":
    """Process-wide manager (SQLite store). Tests build their own with MemoryStore."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ConversationStateManager(SQLiteStore(default_db_path()))
        return _manager
