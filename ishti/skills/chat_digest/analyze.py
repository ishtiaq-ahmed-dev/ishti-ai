"""Analysis for the 24h digest (4.4): per-chat map, cross-chat rollup, local only, nothing invented.

Four rules this module enforces mechanically, because they are what makes the report trustworthy:

1. SOURCES. Every topic, action item and decision carries the message ids it came from. An item whose
   sources are missing or not in the window is DROPPED (drop_unsourced) - a claim without a source does
   not reach the owner. That is the anti-hallucination gate.
2. RELATIONSHIPS. The relationship column comes from the owner's own graph (ishti.rgraph) and nothing else.
   Not in the graph -> "Unknown (not in my relationship data)". Frequency, tone or a chat's name never
   becomes a kinship/romantic/religious guess.
3. INJECTION. Message text is DATA. A message that tries to instruct ISHTI (ishti.untrusted.scan) is
   flagged, excluded from action/decision extraction, and cannot change the report's structure.
4. SENSITIVE. Health, money, romantic/religious/political and credential-shaped content is reduced to a
   neutral one-line marker and marked export_ok=False (4.4 privacy; excluded from files unless confirmed).

Deterministic first: the rules below run with no model at all, so the digest works offline. When the planner
is available, summarize_llm() can add a prose line per chat - fenced through ishti.untrusted, and its output
is still source-checked.
"""
import datetime as dt
import re

from ishti import untrusted

# ------------------------------------------- lexicons (English, Roman-Urdu, Urdu, Pashto best-effort)
REQUEST = re.compile(
    r"\b(?:please|pls|plz|kindly|can you|could you|need you to|i need|we need|don'?t forget|remind(?:er)?|"
    r"make sure|follow ?up|send me|share|confirm|reply|submit|pay|transfer|call me|collect|"
    r"kardo|kar do|kar dena|krdo|bhej ?do|bhej ?dena|bhejna|chahiye|chahiyay|zaroorat|yaad dilana)\b|"
    r"(?:بھیج|کر دیں|کر دو|چاہیے|ضرورت|یاد دہانی)|(?:لېږل|ورکړه)", re.I)
URGENT = re.compile(r"\b(?:urgent|asap|immediately|right now|deadline|last date|"
                    r"jaldi|foran|abhi|فوری|جلدی)\b", re.I)
DECISION = re.compile(r"\b(?:decided|decision|final(?:ised|ized)?|approved|confirmed|agreed|we will|we'?ll|"
                      r"plan is|scheduled|booked|bought|settled|locked in|"
                      r"tay|faisla|final kar|منظور|فیصلہ)\b", re.I)
SOCIAL = re.compile(r"\b(?:salam|assalam|walaikum|hi|hello|hey|thanks|thank you|shukriya|mubarak|congrats|"
                    r"haha|lol|nice|good morning|good night|gm|gn|شکریہ|مبارک)\b", re.I)
SPAM = re.compile(r"\b(?:forward(?:ed)?|fwd(?:\s+as\s+received)?|click (?:here|below)|offer|discount|sale|"
                  r"limited time|join (?:now|this|our)|subscribe|earn|free|prize|winner|lottery|invite link|"
                  r"register now|giveaway|cashback|promo|buy now|dm me|t\.me|bit\.ly|tinyurl|"
                  r"ڈسکاؤنٹ|آفر|مفت|انعام)\b", re.I)
TOPIC_LEXICON = {
    "work": r"\b(?:office|client|project|meeting|report|invoice|quote|tender|deadline|boss|team|"
            r"kam|kaam)\b",
    "study": r"\b(?:exam|assignment|semester|university|imsciences|class|lecture|quiz|study|thesis|viva)\b",
    "money": r"\b(?:rs\.?|pkr|rupees|rupay|payment|paid|salary|budget|price|cost|paisa)\b",
    "family": r"\b(?:abbu|ammi|baba|mama|chacha|khala|bhai|behan|family|ghar)\b",
    "health": r"\b(?:doctor|hospital|clinic|medicine|dawai|tabiyat|fever|sugar|bp)\b",
    "travel": r"\b(?:flight|ticket|bus|train|trip|travel|hotel|boarding|visa|peshawar|islamabad|"
              r"lahore|karachi)\b",
    "food": r"\b(?:dinner|lunch|breakfast|kabab|biryani|chai|restaurant|recipe)\b",
    "tech": r"\b(?:server|gpu|cuda|ollama|python|repo|deploy|api|backup|driver)\b",
    "news": r"\b(?:news|headline|breaking|election|weather|rain|storm)\b",
}
TOPIC_RE = {k: re.compile(v, re.I) for k, v in TOPIC_LEXICON.items()}

SENSITIVE = {
    "health": r"\b(?:hospital|operation|surgery|diagnos|cancer|medicine|tabiyat|bimar|chemo|therapy)\b",
    "money": r"\b(?:transfer(?:red)?|loan|debt|salary|payment due|emi|installment|udhaar)\b",
    "credentials": r"\b(?:otp|password|passcode|pin code|cnic|card number|cvv|verification code|"
                   r"api[ _-]?key|secret key|access token|bearer)\b",
    "romance": r"\b(?:i love you|miss you|proposal|rishta|shaadi|marriage|engagement|mangni)\b",
    "religion": r"\b(?:blessing|sin|haram|halal|fatwa|namaz|roza)\b",
    "politics": r"\b(?:pti|pmln|election rigging|protest|dharna|govt|military)\b",
}
SENSITIVE_RE = {k: re.compile(v, re.I) for k, v in SENSITIVE.items()}

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December"]
MONTHS = {}
for _i, _m in enumerate(_MONTH_NAMES, start=1):
    MONTHS[_m.lower()] = _i           # "september"
    MONTHS[_m[:3].lower()] = _i       # "sep"  <- "by 30 Sep" is the common WhatsApp wording
    MONTHS[_m.lower().replace("sept", "sep")] = _i
WEEKDAYS = {d.lower(): i for i, d in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                                                "Saturday", "Sunday"])}
DATE_NUM = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b")
# "clause 7.3", "item 4.2", "page 12.1" are document references, not dates - they must never become deadlines.
NOT_A_DATE = re.compile(r"\b(?:clause|section|cl\.|sec\.|para|paragraph|item|rule|point|page|pg|line|version|ver|no|"
                        r"number|step|article|figure|table|chapter|note|annex|schedule)\s*[#.]?\s*$", re.I)
DATE_CTX = re.compile(r"\b(?:by|on|due|before|until|till|latest|deadline|submit|submission|assignment|"
                      r"deliver|submitting|expires?)\s*$", re.I)
DATE_WORD = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\b|\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b")
REL_DAY = re.compile(r"\b(today|tomorrow|tonight|day after tomorrow|next week|aaj|kal|parsoon|nan|sabaa)\b"
                     r"|(آج|کل|پرسوں|نن|سبا)", re.I)
REL_MAP = {"today": 0, "tonight": 0, "aaj": 0, "آج": 0, "kal": 1, "tomorrow": 1, "کل": 1, "سبا": 1,
           "sabaa": 1, "nan": 0, "نن": 0, "parsoon": 2, "day after tomorrow": 2, "پرسوں": 2, "next week": 7}
ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


def script_of(text):
    """'ur' (Arabic script), 'mixed' (script + Latin) or 'latin' - the report notes the script it saw."""
    has_ac = bool(ARABIC_SCRIPT.search(text or ""))
    has_la = bool(re.search(r"[A-Za-z]", text or ""))
    if has_ac and has_la:
        return "mixed"
    return "ur" if has_ac else "latin"


def tokens(text):
    return re.findall(r"[A-Za-z\u0600-\u06FF]{3,}", text or "")

def parse_deadline(text, win):
    """The date a message names, as (label, ts) - or (None, None). 'today/tomorrow/kal/آج/سبا' resolve against
    the digest's own end time (the window end), not against whenever this code happens to run."""
    base = dt.datetime.fromtimestamp(win["end"])
    m = REL_DAY.search(text or "")
    if m:
        key = (m.group(1) or m.group(2) or "").lower()
        days = REL_MAP.get(key, 0)
        d = (base + dt.timedelta(days=days)).date()
        return f"{d.isoformat()} ({d.strftime('%a')})", dt.datetime.combine(d, dt.time(23, 59)).timestamp()
    for m in DATE_NUM.finditer(text or ""):
        before = (text or "")[:m.start()]
        dotted = "." in m.group(0) and "/" not in m.group(0) and "-" not in m.group(0)
        if NOT_A_DATE.search(before) or (dotted and not m.group(3) and not DATE_CTX.search(before)):
            continue                       # a document reference, or a bare decimal - not a deadline
        day, mon = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else base.year
        if year < 100:
            year += 2000
        try:
            d = dt.date(year, mon, day)
            if not m.group(3) and d < base.date() - dt.timedelta(days=1):
                d = d.replace(year=year + 1)
            return f"{d.isoformat()} ({d.strftime('%a')})", dt.datetime.combine(d, dt.time(23, 59)).timestamp()
        except ValueError:
            continue
    m = DATE_WORD.search(text or "")
    if m:
        try:
            day, mon_name = (int(m.group(1)), m.group(2)) if m.group(1) else (int(m.group(4)), m.group(3))
            mon = MONTHS.get(mon_name.lower()) or MONTHS.get(mon_name[:3].lower())
            if mon:
                d = dt.date(base.year, mon, day)
                if d < base.date() - dt.timedelta(days=1):
                    d = d.replace(year=base.year + 1)
                return f"{d.isoformat()} ({d.strftime('%a')})", dt.datetime.combine(d, dt.time(23, 59)).timestamp()
        except (ValueError, TypeError):
            pass
    m = re.search(r"\b(" + "|".join(WEEKDAYS) + r")\b", text or "", re.I)
    if m:
        delta = (WEEKDAYS[m.group(1).lower()] - base.weekday()) % 7 or 7
        d = (base + dt.timedelta(days=delta)).date()
        return f"{d.isoformat()} ({d.strftime('%a')})", dt.datetime.combine(d, dt.time(23, 59)).timestamp()
    return None, None


def amend_priorities(items, busy_chats=()):
    """Sort key used everywhere: the work/study/money/deadlines rubric of 4.4."""
    return sorted(items, key=lambda it: (it.get("_rank", 3), it.get("_ts", 0)))


QUOTED = "[instruction-like text removed by ISHTI]"


def scan_message(m):
    """Injection check for one stored row. -> (clean_text, hits). Hits make the row data-only (never an
    action item) but it still counts towards the chat's message count and topics."""
    text = m.get("text") or ""
    hits = untrusted.scan(text)
    if not hits:
        return text, []
    clean, _removed = untrusted.sanitize(text)
    return (clean or QUOTED), hits


SELF_WORDS = {"me", "you", "myself", "owner", "self"}


def is_owner(sender, rubric):
    """Is this message the owner's own? WhatsApp exports write 'You'; the live read knows the profile name.
    Extra names come from data/chat_digest_config.json -> owner_names."""
    s = (sender or "").strip().lower()
    return s in SELF_WORDS or s in (rubric.get("owner_names") or set())


def classify(messages, rubric):
    """Per-message facts: topic shots, action/decision/sensitive flags, spam, social, injection."""
    out = []
    for m in messages:
        text, hits = scan_message(m)
        row = {
            "msg_id": m["msg_id"], "ts": m["ts"], "sender": m["sender"], "media": m.get("media") or "",
            "media_text": m.get("media_text") or "", "text": text, "flagged": bool(hits), "hits": hits,
            "topics": [k for k, rx in TOPIC_RE.items() if rx.search(text)],
            "request": bool(REQUEST.search(text)), "urgent": bool(URGENT.search(text)),
            "decision": bool(DECISION.search(text)), "spam": bool(SPAM.search(text)),
            "social": bool(SOCIAL.search(text)), "mine": is_owner(m.get("sender"), rubric),
            "sensitive": [k for k, rx in SENSITIVE_RE.items() if rx.search(text)],
            "script": script_of(text),
        }
        row["deadline"], row["deadline_ts"] = (parse_deadline(text, rubric["win"]) if not hits else (None, None))
        out.append(row)
    return out


def merge_duplicates(items):
    """Twenty identical 'please confirm the milestone payment' rows are noise, not twenty tasks. Identical
    (text, deadline) items in one chat collapse into one row that keeps every source id, so nothing is lost
    and the report stays readable (this is what made the first sample PDF three pages long)."""
    out, by_key = [], {}
    for it in items:
        key = (it.get("chat_id"), it.get("text"), it.get("deadline"), it.get("priority"))
        kept = by_key.get(key)
        if kept is None:
            by_key[key] = it
            out.append(it)
            continue
        kept["repeats"] = kept.get("repeats", 1) + 1
        for sid in it.get("src", []):
            if sid not in kept["src"] and len(kept["src"]) < 5:
                kept["src"].append(sid)
    for it in out:
        if it.get("repeats", 1) > 1:
            it["text"] = f"{it['text']} (repeated {it['repeats']}x in this chat)"
    return out


def drop_unsourced(items, valid_ids):
    """THE anti-hallucination gate: an item with no source, or a source not in the window, never reaches the
    owner. Returns (kept, dropped)."""
    kept, dropped = [], []
    for it in items:
        src = [s for s in (it.get("src") or []) if s in valid_ids]
        if src:
            it["src"] = src[:5]
            kept.append(it)
        else:
            dropped.append(it)
    return kept, dropped


def _spam_ratio(rows):
    return (sum(1 for r in rows if r["spam"]) / len(rows)) if rows else 0.0


def _forward_like(rows):
    """A message text repeated by different senders (or the same one) reads as a mass forward."""
    seen = {}
    for r in rows:
        key = re.sub(r"\s+", " ", (r["text"] or "").strip().lower())[:120]
        if key:
            seen[key] = seen.get(key, 0) + 1
    return max(seen.values()) if seen else 0


def usefulness(rows, actions, decisions, win, rubric):
    """High / Medium / Low / Noise with a one-line reason (4.4). Deterministic and tunable."""
    if not rows:
        return "Noise", "no messages in the window"
    horizon = rubric["usefulness"]["action_window_hours"]
    due = [a for a in actions if a.get("deadline_ts") and a["deadline_ts"] - win["end"] <= horizon * 3600]
    if due:
        return "High", f"{len(due)} action(s) with a deadline inside {horizon}h"
    if actions:
        return "High" if any(a.get("priority") == "High" for a in actions) else "Medium", \
               f"{len(actions)} action(s) for me"
    if decisions:
        return "Medium", f"{len(decisions)} decision(s)/info worth knowing"
    if _spam_ratio(rows) >= 0.5 or _forward_like(rows) >= max(3, rubric["usefulness"]["noise_forward_threshold"]):
        return "Noise", "mostly promos/forwards (repeated or promo-shaped text)"
    if all(r["social"] or not r["topics"] for r in rows):
        return "Low", ("social chatter, nothing to act on" if all(r["social"] for r in rows)
                       else "no clear topic, nothing to act on")
    return "Medium", "information shared, nothing to act on"

UNKNOWN_REL = "Unknown (not in my relationship data)"
SENSITIVE_LINE = "Sensitive - open in chat to view"
CHUNK = 40
STOPWORDS = {"the", "and", "you", "for", "that", "this", "with", "have", "will", "from", "your", "are", "was",
             "not", "but", "all", "can", "get", "our", "out", "any", "how", "who", "what", "when", "kya",
             "hai", "hain", "nahi", "bhi", "kar", "karo", "aur", "mein", "main", "aap", "mujhe", "yeh", "wo",
             "please", "thanks", "theek", "acha", "ok", "okay"}


def relationship_for(name, kind):
    """ONLY the owner's confirmed graph, never a guess (4.4). A group name is checked as a node too - it may be
    an organisation/community the owner recorded - but a missing node is always 'Unknown'."""
    if not name:
        return UNKNOWN_REL
    try:
        from ishti import rgraph
        rel = rgraph.relationship_of(name) or {}
    except Exception:
        return UNKNOWN_REL
    kind_rel = (rel.get("relationship") or "").lower()
    if kind_rel in ("", "unknown", "none"):
        return UNKNOWN_REL
    label = kind_rel.replace("_", " ")
    conf = rel.get("confidence")
    if conf is not None:
        try:
            label += f" (confidence {float(conf):.2f})"
        except (TypeError, ValueError):
            pass
    if rel.get("needs_confirmation"):
        label += " - unconfirmed, I can ask you to confirm"
    return label


def _priority(row, win, rubric):
    """Priority from the rubric: a deadline inside 24h or 'urgent' is High; inside the action window or a
    plain request is Medium. The owner's OWN promise ('ok I'll send it today') is recorded, but it never
    outranks someone else waiting on him - that is the bug this rule exists for."""
    horizon = rubric["usefulness"]["action_window_hours"] * 3600
    if row["deadline_ts"] and row["deadline_ts"] - win["end"] <= 86400:
        rank = 0
    elif row["urgent"]:
        rank = 1
    elif row["deadline_ts"] and row["deadline_ts"] - win["end"] <= horizon:
        rank = 2
    elif row["request"]:
        rank = 3
    else:
        rank = 4
    if row.get("mine") and rank < 3:
        rank += 2
    return ("High" if rank <= 1 else "Medium" if rank <= 3 else "Low"), rank


def _keywords(rows, limit=6):
    counts = {}
    for r in rows:
        for t in tokens(r["text"]):
            key = t.lower()
            if key in STOPWORDS or len(key) < 4:
                continue
            counts.setdefault(key, {"n": 0, "src": []})
            counts[key]["n"] += 1
            if r["msg_id"] not in counts[key]["src"]:
                counts[key]["src"].append(r["msg_id"])
    top = sorted(counts.items(), key=lambda kv: (-kv[1]["n"], kv[0]))[:limit]
    return [{"term": k, "n": v["n"], "src": v["src"][:3]} for k, v in top]

def analyze_chat(chat_row, messages, rubric, use_llm=False, session=None):
    """One chat's Table-1 row + its action items, decisions and notes. Pure function of the messages."""
    win = rubric["win"]
    rows = classify(messages, rubric)
    valid = {r["msg_id"] for r in rows}
    people = {}
    for r in rows:
        if r["sender"] and r["sender"].lower() not in ("you", "me", "system"):
            people[r["sender"]] = people.get(r["sender"], 0) + 1

    actions, decisions, sensitive_items, flagged, media_missing = [], [], [], [], 0
    for r in rows:
        if r["flagged"]:                     # data, never instructions: a flagged row cannot become a task
            flagged.append({"src": [r["msg_id"]], "note": (r["hits"] or [""])[0][:160]})
            continue
        if r["media"] and not r["media_text"]:
            media_missing += 1
        if r["sensitive"]:
            sensitive_items.append({"src": [r["msg_id"]], "kinds": r["sensitive"]})
            continue
        if r["decision"]:
            decisions.append({"text": r["text"][:220], "people": [r["sender"]], "src": [r["msg_id"]],
                              "_ts": r["ts"], "chat": chat_row["name"], "sensitive": False})
        if r["request"] or (r["deadline"] and not r["spam"]):
            pri, rank = _priority(r, win, rubric)
            text = r["text"][:220]
            actions.append({"text": ("you said: " + text) if r["mine"] else text, "from": r["sender"],
                            "deadline": r["deadline"] or "", "deadline_ts": r["deadline_ts"],
                            "priority": pri, "src": [r["msg_id"]], "_ts": r["ts"], "_rank": rank,
                            "mine": r["mine"], "sensitive": False,
                            "chat": chat_row["name"], "chat_id": chat_row["chat_id"]})
    actions, dropped_a = drop_unsourced(actions, valid)
    actions = merge_duplicates(actions)
    decisions, dropped_d = drop_unsourced(decisions, valid)
    flagged, _ = drop_unsourced(flagged, valid)

    topics = {}
    for r in rows:
        for t in r["topics"]:
            topics.setdefault(t, []).append(r["msg_id"])
    ordered = sorted(topics.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    topic_lines = [{"text": f"{t} ({len(ids)} message(s))", "src": ids[:3]} for t, ids in ordered]
    kw_lines = _keywords(rows)
    scripts = sorted({r["script"] for r in rows})
    summary = []
    if topic_lines:
        summary.append({"text": "topics - " + ", ".join(f"{t} ({len(ids)})" for t, ids in ordered[:4]),
                        "src": [i for ids in [v for _, v in ordered[:4]] for i in ids[:1]]})
    if kw_lines:
        summary.append({"text": "most used terms - " + ", ".join(k["term"] for k in kw_lines),
                        "src": [i for k in kw_lines for i in k["src"][:1]]})
    llm_line, llm_note = (None, "")
    if use_llm:
        llm_line, llm_note = summarize_llm(chat_row["name"], rows, valid, session)
    rating, why = usefulness(rows, actions, decisions, win, rubric)
    return {
        "chat_id": chat_row["chat_id"], "name": chat_row["name"], "type": chat_row.get("type") or "unknown",
        "archived": bool(chat_row.get("archived")), "muted": bool(chat_row.get("muted")),
        "messages": len(rows), "people": people, "topics": topic_lines, "keywords": kw_lines,
        "summary": summary, "llm_line": llm_line, "llm_note": llm_note,
        "actions": actions, "decisions": decisions, "sensitive_items": len(sensitive_items),
        "export_ok": not sensitive_items, "flagged": flagged, "media_missing": media_missing,
        "scripts": scripts, "chunks": (len(rows) + CHUNK - 1) // CHUNK if rows else 0,
        "relationship": relationship_for(chat_row["name"], chat_row.get("type")),
        "usefulness": rating, "usefulness_why": why,
        "dropped_unsourced": len(dropped_a) + len(dropped_d),
        "sources": sorted({r["msg_id"] for r in rows}),
    }


def llm_available():
    try:
        from ishti import llm
        return bool(llm.model_for("planner"))
    except Exception:
        return False


def summarize_llm(chat_name, rows, valid_ids, session=None):
    """One prose line from the planner about this chat. Fenced as DATA, and its citations are checked against
    the window: a line without a valid [message id] is dropped, not shown (no source = do not include)."""
    try:
        from ishti import llm
        body = untrusted.fence(
            "\n".join(f"[{r['msg_id']}] {r['sender']}: {r['text']}" for r in rows if r["text"]),
            f"whatsapp chat '{chat_name}'")
        out = llm.ask("Summarise this WhatsApp chat in ONE short line for the owner's daily digest. Put the "
                      "message id of every fact in square brackets, like [m12]. No advice. Write nothing that "
                      "is not in the text.\n\n" + body, role="planner", temperature=0.2, max_tokens=120)
    except Exception as exc:
        return None, f"planner unavailable ({type(exc).__name__}) - deterministic summary used"
    cited = [c for c in re.findall(r"\[([^\]]{1,40})\]", out or "") if c in valid_ids]
    if not cited:
        return None, "planner line had no checkable source - dropped"
    return {"text": out.strip()[:300], "src": cited[:5]}, ""

def analyze(win, cfg=None, use_llm=False, session=None):
    """The whole digest analysis: every chat found (Table 1 + Table 4), all action items, all decisions,
    the top-5 and the ignore-safely list. Deterministic unless use_llm=True."""
    from ishti.skills.chat_digest import store
    cfg = cfg or store_config()
    owner = {str(n).strip().lower() for n in (cfg.get("owner_names") or []) if str(n).strip()}
    rubric = {"win": win, "usefulness": cfg["usefulness"], "owner_names": owner}
    found = store.chats()
    prov = store.provenance(win["start"], win["end"])
    coverage, results = [], []
    ordered = sorted(found, key=lambda c: -(c.get("last_seen") or 0))
    budget = int(cfg.get("max_chats", 250))
    for i, ch in enumerate(ordered):
        msgs = store.messages_between(win["start"], win["end"], ch["chat_id"])
        if not cfg.get("include_archived", True) and ch.get("archived"):
            reason = "archived chat skipped by settings (include_archived=false)"
            analyzed = False
        elif i >= budget:
            reason = f"beyond the chat budget ({budget} chats) - raise max_chats to include it"
            analyzed = False
        elif not msgs:
            reason = "no messages in the 24h window"
            analyzed = False
        else:
            reason, analyzed = "", True
        if not analyzed:
            coverage.append({"chat_id": ch["chat_id"], "name": ch["name"], "type": ch.get("type"),
                             "analyzed": False, "reason": reason, "messages": len(msgs),
                             "sources": ", ".join(prov.get(ch["chat_id"], [])) or "-"})
            continue
        res = analyze_chat(ch, msgs, rubric, use_llm=use_llm, session=session)
        res["archived"], res["muted"] = bool(ch.get("archived")), bool(ch.get("muted"))
        results.append(res)
        coverage.append({"chat_id": ch["chat_id"], "name": ch["name"], "type": ch.get("type"),
                         "analyzed": True, "reason": "", "messages": res["messages"],
                         "sources": ", ".join(prov.get(ch["chat_id"], [])) or "-"})
    actions = amend_priorities([a for r in results for a in r["actions"]])
    decisions = sorted([d for r in results for d in r["decisions"]], key=lambda d: -d.get("_ts", 0))
    noisy = [r["name"] for r in results if r["usefulness"] == "Noise"]
    sources_used = sorted({s for v in prov.values() for s in v})
    return {
        "window": win, "coverage": coverage, "chats": results, "actions": actions, "decisions": decisions,
        "top5": actions[:5], "ignore": noisy, "source_used": sources_used,
        "totals": {
            "chats_found": len(found), "chats_analyzed": len(results),
            "chats_skipped": len(found) - len(results),
            "messages": sum(r["messages"] for r in results),
            "actions": len(actions), "decisions": len(decisions),
            "flagged_injections": sum(len(r["flagged"]) for r in results),
            "chats_with_sensitive": sum(1 for r in results if r["sensitive_items"]),
            "media_not_analyzed": sum(r["media_missing"] for r in results),
        },
        "llm_used": bool(use_llm and llm_available()),
    }


def store_config():
    from ishti.skills.chat_digest import window as win_mod
    return win_mod.config()


if __name__ == "__main__":
    import os
    import tempfile

    from ishti.skills.chat_digest import store
    from ishti.skills.chat_digest import window as win_mod
    store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="cd_an_"), "a.db")
    store.close()
    now = dt.datetime(2026, 9, 29, 18, 0, 0).timestamp()
    w = win_mod.window(now=now)
    store.ingest_json({"chats": [
        {"chat_id": "work", "name": "Client Work", "type": "dm", "messages": [
            {"msg_id": "w1", "ts": now - 3600, "sender": "Bilal", "text":
             "Please send the invoice by 30 Sep, client is waiting"},
            {"msg_id": "w2", "ts": now - 1800, "sender": "me", "text": "ok sending today"}]},
        {"chat_id": "spam", "name": "Deals 4 U", "type": "group", "messages": [
            {"msg_id": "s1", "ts": now - 900, "sender": "x", "text": "LIMITED TIME OFFER click here to win free prize"},
            {"msg_id": "s2", "ts": now - 800, "sender": "y", "text": "LIMITED TIME OFFER click here to win free prize"}]},
        {"chat_id": "old", "name": "Old Chat", "type": "group", "messages": [
            {"msg_id": "o1", "ts": now - 100000, "sender": "z", "text": "hello"}]},
    ]})
    res = analyze(w)
    names = {c["name"]: c for c in res["chats"]}
    assert res["totals"]["chats_found"] == 3 and res["totals"]["chats_analyzed"] == 2
    assert [c["reason"] for c in res["coverage"] if not c["analyzed"]] == ["no messages in the 24h window"]
    assert res["actions"], res["actions"]
    assert res["actions"][0]["chat"] == "Client Work" and res["actions"][0]["deadline"].startswith("2026-09-30")
    assert res["actions"][0]["priority"] in ("High", "Medium") and res["actions"][-1]["mine"] is True
    assert res["actions"][-1]["text"].startswith("you said:"), res["actions"][-1]
    assert names["Deals 4 U"]["usefulness"] == "Noise" and "Deals 4 U" in res["ignore"]
    assert names["Client Work"]["relationship"] == "Unknown (not in my relationship data)"
    assert all(a["src"] for a in res["actions"]) and res["totals"]["messages"] == 4
    print("[self-check] chat_digest.analyze OK - coverage reasons, deadline priority, noise rubric, "
          "relationship never guessed, every action sourced")




