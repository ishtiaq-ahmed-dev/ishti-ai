"""Shared, offline-first tool selection and decision policy.

The legacy router remains the calibrated first signal.  This layer makes its
candidate handling exception-safe, narrows the inventory deterministically,
consults owner-approved routing corrections, and uses the local model only to
break a genuinely ambiguous shortlist.  It never executes a tool.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Iterable


NON_ROUTABLE = frozenset({"broken", "draft", "disabled", "retired"})
_WORD = re.compile(r"[a-z0-9]+")
_EXPLICIT = re.compile(r"^(?:\[skill:(?P<a>[a-z0-9_-]+)\]|/(?:use|skill)\s+(?P<b>[a-z0-9_-]+))\s*", re.I)
_AMBIGUOUS = re.compile(
    r"\b(?:it|them|there|him|her|that one|this one|same thing|usual place|the old file|right tool)\b",
    re.I,
)
_ACTION = re.compile(
    r"\b(?:add|book|buy|cancel|check|compare|create|delete|download|draft|email|export|extract|find|"
    r"get|list|locate|log|look up|make|move|open|plot|put|read|remember|remind|rename|reply|run|save|use|"
    r"scan|search|send|set|show|summari[sz]e|update|write|zip|unzip|latest|today|current|now)\b",
    re.I,
)
_STOP = frozenset(
    "a an and are as at be by for from give how i in is it me my of on or please that the this to what when where which who with you".split()
)
_ALIASES = {
    "mail": "email", "gmail": "email", "forecast": "weather", "temperature": "weather",
    "flight": "travel", "hotel": "travel", "airfare": "travel", "stock": "markets",
    "shares": "markets", "crypto": "markets", "pdf": "docs", "spreadsheet": "docs",
    "graph": "chart", "plot": "chart", "timer": "reminders", "alarm": "reminders",
    "schedule": "calendar", "meeting": "calendar", "whatsapp": "whatsapp",
}
_CUES = (
    ("pdftables", re.compile(r"\b(?:extract|pull|get|export|turn)\b.{0,55}\btable|\bpdf tables?\b|"
                              r"\btabular data\b.{0,45}\b(?:pdf|csv)\b", re.I)),
    ("chart", re.compile(r"\b(?:bar chart|line (?:chart|graph)|histogram|chart|plot|visuali[sz]e)\b", re.I)),
    ("security_scan", re.compile(r"\b(?:security[- ]scan|security scan|scan mcp|audit (?:the )?skill manifests?|"
                                  r"(?:repository|repo).{0,30}(?:committed )?secrets?|shell true|verify false)\b", re.I)),
    ("fileops", re.compile(r"\b(?:rename|zip|unzip|duplicate files?|compare (?:these two |these |two )?(?:files?|folders?)|"
                            r"recycle bin|delete (?:the |a )?(?:old )?file)\b", re.I)),
    ("whatsapp", re.compile(r"\bwhats ?app\b|\b(?:family|work) group messages?\b", re.I)),
    ("writing", re.compile(r"\b(?:rewrite|paraphrase|fix the grammar|bullet points?|blog outline|formal tone|"
                            r"draft .{0,20} from (?:these )?(?:notes|bullets)|summari[sz]e this text)\b", re.I)),
    ("email", re.compile(r"\b(?:gmail|unread (?:mail|email)|search my (?:mail|email)|newest email|email to|"
                          r"read .{0,12}email from|send the approved email)\b", re.I)),
    ("budget", re.compile(r"\b(?:i spent|spending|expenses?|budget limit|dining limit|log (?:salary )?income|"
                           r"monthly .{0,12}limit)\b", re.I)),
    ("study", re.compile(r"\b(?:quiz me|flashcards?|explain .{0,30} simply|study plan|grade my answer|exam prep)\b", re.I)),
    ("travel", re.compile(r"\b(?:flights?|airfare|hotels?|place to stay)\b", re.I)),
    ("shopping", re.compile(r"\b(?:cheapest|compare .{0,20}prices?|verified price|official store|retailers?|"
                             r"where can i buy|genuine .{0,20}(?:battery|ssd|monitor|product))\b", re.I)),
    ("markets", re.compile(r"\b(?:stock price|shares?|crypto|btc|bitcoin|gold price|silver|currency rate|"
                            r"market calls?|ogdc|nvidia price)\b", re.I)),
    ("docs", re.compile(r"\b(?:merge .{0,20}pdfs?|convert .{0,30}(?:word|pdf)|powerpoint|word (?:file|document|report)|"
                         r"summari[sz]e this spreadsheet|compare .{0,20}word documents?)\b", re.I)),
    ("calc", re.compile(r"\b(?:solve|derivative|integral|calculate|percent|prime factors?|simplify|gcd|lcm|"
                         r"how much .{0,20}cost)\b|(?:\d\s*[+*/=-]\s*\d)", re.I)),
    ("calendar", re.compile(r"\b(?:calendar|schedule|free slots?|add (?:a )?(?:meeting|event)|cancel .{0,20}event|ics)\b", re.I)),
    ("reminders", re.compile(r"\b(?:remind|reminder|timer|alarm|wake me)\b", re.I)),
    ("memory", re.compile(r"\b(?:remember that|what do you remember|forget the saved|project .{0,25}(?:status|is in)|"
                           r"update the status of project|where did i store)\b", re.I)),
    ("files", re.compile(r"\b(?:find|locate|where is|search my pc for)\b.{0,70}\b(?:file|folder|pdf|docx|csv|png|"
                          r"downloads?|containing|named|logo|proposal)\b", re.I)),
    ("weather", re.compile(r"\b(?:weather|forecast|temperature|rain|umbrella|how hot|how cold)\b", re.I)),
    ("web_search", re.compile(r"\b(?:search the web|look up|latest|newest|today'?s? .{0,25}news|currently leads|"
                              r"actively maintained|what changed in)\b", re.I)),
)


def _tokens(value: str) -> set[str]:
    out = set()
    for word in _WORD.findall((value or "").lower()):
        if word in _STOP or len(word) < 3:
            continue
        out.add(word)
        if len(word) > 4 and word.endswith("s"):
            out.add(word[:-1])
        if len(word) > 5 and word.endswith("ing"):
            out.add(word[:-3])
        if len(word) > 4 and word.endswith("ed"):
            out.add(word[:-2])
    return out


def _status(skill) -> str:
    try:
        return str(skill.status)
    except Exception:
        return "broken"


def _safe_match(skill, text: str) -> bool:
    try:
        return bool(skill.matches(text))
    except Exception:
        return False


def _cue_names(text: str) -> list[str]:
    return [name for name, pattern in _CUES if pattern.search(text or "")]


def usable_skills(skills: dict, exclude: Iterable[str] = ()) -> dict:
    """Return only skills that are eligible to run at this instant."""
    blocked = set(exclude or ())
    try:
        from ishti.skills import registry
        blocked.update(registry.disabled_names())
    except Exception as exc:
        from ishti.core.logutil import swallowed
        swallowed("ishti/tool_routing.py:usable_skills", exc)
    from ishti.skill_execution import available  # the PC execution gate (owner flags, permissions) stays authoritative
    return {name: skill for name, skill in available(skills).items()
            if name not in blocked and _status(skill) not in NON_ROUTABLE}


def _lexical_score(text: str, name: str, skill) -> float:
    words = _tokens(text)
    manifest = getattr(skill, "manifest", {}) or {}
    route_words = _tokens(str(manifest.get("route", "")))
    desc_words = _tokens(str(manifest.get("description", "")))
    name_words = _tokens(name.replace("_", " "))
    overlap = len(words & route_words) * 0.13 + len(words & desc_words) * 0.035
    overlap += len(words & name_words) * 0.22
    for cue, target in _ALIASES.items():
        if cue in words and target == name:
            overlap += 0.42
    phrase = str(manifest.get("route", "")).lower()
    if any(bit.strip() and len(bit.strip().split()) >= 2 and bit.strip() in text.lower()
           for bit in re.split(r"[,;:]", phrase)):
        overlap += 0.35
    if name in _cue_names(text):
        overlap = max(overlap, 0.92)
    return min(1.0, overlap)


def candidate_shortlist(text: str, skills: dict, *, exclude: Iterable[str] = (), limit: int = 20) -> list[tuple[str, float]]:
    """Rank an exception-safe, lifecycle-filtered 5-20 skill shortlist."""
    live = usable_skills(skills, exclude)
    ranked = []
    for name, skill in live.items():
        score = _lexical_score(text, name, skill)
        if _safe_match(skill, text):
            score = max(score, 1.0)
        if score > 0:
            ranked.append((name, round(score, 3)))
    ranked.sort(key=lambda item: (-item[1], int(getattr(live[item[0]], "manifest", {}).get("priority", 50)), item[0]))
    core = [(name, 0.0) for name in ("chat", "memory", "web_search") if name in live]
    merged = []
    seen = set()
    for item in [*ranked, *core]:
        if item[0] not in seen:
            merged.append(item)
            seen.add(item[0])
    if not merged:
        merged = [(name, 0.0) for name in sorted(live)[:5]]
    return merged[:max(5, min(20, int(limit)))]


def _learned_hint(text: str) -> str | None:
    try:
        from ishti.learning.feedback import routing_hint
        return routing_hint(text)
    except Exception:
        return None


def _rerank(text: str, candidates: list[tuple[str, float]], skills: dict,
            llm_fn: Callable[..., str] | None = None) -> tuple[str | None, float]:
    if not candidates:
        return None, 0.0
    ask = llm_fn
    if ask is None:
        try:
            from ishti import llm
            ask = llm.ask
        except Exception:
            return None, 0.0
    names = [name for name, _ in candidates[:8]]
    choices = "\n".join(
        f"- {name}: {(getattr(skills[name], 'manifest', {}) or {}).get('route', '')}" for name in names
    )
    prompt = (
        "Choose the one best tool for the request from the allowed shortlist. If the target is missing or ambiguous, "
        "choose clarify. Return JSON only: {\"skill\":\"name|clarify\",\"confidence\":0..1}.\n"
        f"Allowed tools:\n{choices}\nRequest: {text}"
    )
    try:
        raw = ask(prompt, system=None, temperature=0, max_tokens=80, response_format={"type": "json_object"})
        data = json.loads(str(raw).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        name = str(data.get("skill") or "").strip()
        confidence = float(data.get("confidence") or 0.0)
        if name == "clarify":
            return None, confidence
        return (name, confidence) if name in names else (None, 0.0)
    except Exception:
        return None, 0.0


def _clarification(text: str, candidates: list[tuple[str, float]]) -> str:
    names = [name.replace("_", " ") for name, score in candidates[:2] if score > 0]
    if _AMBIGUOUS.search(text):
        return "Which exact person, item, file, or destination do you mean?"
    if len(names) >= 2:
        return f"Should I use {names[0]} or {names[1]} for that?"
    return "What exact action and target should I use the tool for?"


def route(text: str, skills: dict, exclude=(), *, llm_fn: Callable[..., str] | None = None) -> dict:
    """Select a skill without executing it; low-confidence actions return a clarification."""
    text = str(text or "").strip()
    live = usable_skills(skills, exclude)
    explicit = _EXPLICIT.match(text)
    if explicit:
        name = (explicit.group("a") or explicit.group("b") or "").lower().replace("-", "_")
        if name in live:
            return {"skill": name, "confidence": 1.0, "source": "explicit_directive", "scores": [(name, 1.0)]}

    if _AMBIGUOUS.search(text) and _ACTION.search(text):
        candidates = candidate_shortlist(text, skills, exclude=exclude)
        return {"skill": "chat", "confidence": 0.0, "source": "clarify",
                "clarify": _clarification(text, candidates), "scores": candidates[:3]}

    hint = _learned_hint(text)
    if hint in live:
        return {"skill": hint, "confidence": 1.0, "source": "preference_hint", "scores": [(hint, 1.0)]}

    cues = [name for name in _cue_names(text) if name in live]
    if cues:
        if "calc" in cues and re.search(r"\b(?:calculate|solve|derivative|integral|simplify)\b", text, re.I):
            cues.insert(0, cues.pop(cues.index("calc")))
        if "reminders" in cues and re.match(r"\s*(?:remind|set (?:a )?(?:timer|alarm)|wake me)\b", text, re.I):
            cues.insert(0, cues.pop(cues.index("reminders")))
        return {"skill": cues[0], "confidence": 0.92, "source": "shortlist",
                "scores": [(name, 0.92) for name in cues[:3]]}

    matches = [(name, skill) for name, skill in live.items() if _safe_match(skill, text)]
    if matches:
        matches.sort(key=lambda item: (int((getattr(item[1], "manifest", {}) or {}).get("priority", 50)), item[0]))
        name = matches[0][0]
        return {"skill": name, "confidence": 1.0, "source": "rule", "scores": [(n, 1.0) for n, _ in matches[:3]]}

    # An explicitly matching disabled skill can be offered, but never run silently.
    cue_set = set(_cue_names(text))
    disabled = [(name, skill) for name, skill in skills.items()
                if name not in set(exclude or ()) and _status(skill) == "disabled"
                and (_safe_match(skill, text) or name in cue_set)]
    if disabled:
        disabled.sort(key=lambda item: (int((getattr(item[1], "manifest", {}) or {}).get("priority", 50)), item[0]))
        return {"skill": disabled[0][0], "confidence": 1.0, "source": "rule", "needs_enable": True,
                "scores": [(disabled[0][0], 1.0)]}

    candidates = candidate_shortlist(text, skills, exclude=exclude)
    positive = [(name, score) for name, score in candidates if score > 0]
    if positive:
        top_name, top_score = positive[0]
        margin = top_score - (positive[1][1] if len(positive) > 1 else 0.0)
        if top_score >= 0.58 and margin >= 0.12:
            return {"skill": top_name, "confidence": round(top_score, 3), "source": "shortlist",
                    "scores": positive[:3]}
        if _ACTION.search(text):
            picked, confidence = _rerank(text, positive, skills, llm_fn=llm_fn)
            if picked and confidence >= 0.65:
                return {"skill": picked, "confidence": round(confidence, 3), "source": "local_rerank",
                        "scores": positive[:3]}
            return {"skill": "chat", "confidence": round(confidence, 3), "source": "clarify",
                    "clarify": _clarification(text, positive), "scores": positive[:3]}

    # Timeless questions and conversation stay with chat.  The local reranker is
    # not woken when no tool cue exists.
    default = "chat" if "chat" in live else (next(iter(live), None))
    return {"skill": default, "confidence": 0.0, "source": "default", "scores": []}
