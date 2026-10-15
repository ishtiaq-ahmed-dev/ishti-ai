"""Writing workbench using the explicitly selected model provider.

summarize / paraphrase / rewrite (formal, casual, simpler) / bullet points / expand /
fix grammar / tone check / draft an email from bullets / social post / blog outline.
The planner model does the writing; routing is deterministic so no classifier call is needed.
"""
import re

from ishti import llm
from ishti.untrusted import fence
from ishti.skills import trace

I = re.I

# "summarize this: <text>" / "summarize <url-free pasted text>" / with the text inline
_OPS = [
    ("summary", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?summari[sz]e\s+(?:this|the\s+following|it)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("paraphrase", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:rephrase|paraphrase|rewrite)\s+(?:this|the\s+following|it)?\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("bullets", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:make|turn|convert|give\s+me)(?:\s+(?:these?|it|the\s+following|them))?(?:\s+to|\s+in\s*to|\s+into)?\s+(?:bullet\s+points?|key\s+points?|a\s+bullet\s+list)\s*(?:from|of|for)?\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("expand", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?expand\s+(?:this|on\s+this|the\s+following)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("grammar", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:fix|correct|check)\s+(?:the\s+)?grammar\s*(?:of|in|for)?\s*(?:this|it|the\s+following)?\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    # "draft an email to <person> asking for ..." is chat's labelled conversational case; budget of the
    ("email", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?draft\s+(?:me\s+)?an?\s+email\s*(?:about|that\s+says|saying)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("post", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:write|draft)\s+(?:me\s+)?a\s+(?P<kind>linkedin|twitter|facebook|instagram)?\s*(?:social\s+)?post\s+(?:about|on|for)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("draft_reply", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?draft\s+(?:a\s+)?(?:professional\s+)?reply\s+(?:declining|turning\s+down)\s+(?:this\s+)?meeting(?:\s+request)?.*", I | re.S)),
    ("outline", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:write\s+(?:me\s+)?a\s+)?blog\s+(?:post\s+)?outline\s+(?:about|on|for)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("citation", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:format\s+(?:this\s+)?citation\s+in\s+apa\s+style|apa\s+citation|format\s+in\s+apa)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("hypothesis", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:help\s+me\s+write\s+(?:the\s+)?hypothesis(?:\s+testing)?\s+section|hypothesis\s+testing\s+section).*", I | re.S)),
    ("stats_check", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?check\s+(?:my\s+)?(?:report|paper|analysis)\s+for\s+(?:any\s+)?(?:obvious\s+)?statistical\s+errors.*", I | re.S)),
    ("convert_pdf", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?convert\s+(?:my\s+)?(?:report|document|file)\s+to\s+pdf.*", I | re.S)),
]

TONE = re.compile(r"\b(formal|casual|simple|simpler|professional|friendly)\b", I)


def match(text):
    # "summarize this article: <url>" is a web fetch, not a paste-in summary (checklist #41).
    # Writing owns summarize/paraphrase/bullets/expand/grammar when the body text is inline;
    # a URL in the request means the content is on the web.
    if re.search(r"https?://", text or ""):
        return False
    return any(rx.match(text) for _n, rx in _OPS)


def _ask(job: str, body: str, extra: str = "") -> str:
    system = ("You are ISHTI's writing editor. Do exactly the requested transformation on the given text. "
              "Output ONLY the result, no preamble, no explanations. Keep the owner's language (English, "
              "Urdu or Pashto).")
    return llm.ask(f"{job}{extra}\n\nTEXT:\n{fence(body)}", role="planner", system=system,
                   temperature=0.3, max_tokens=1500)


def run(text, session):
    for name, rx in _OPS:
        m = rx.match(text.strip())
        if not m:
            continue
        body = (m.groupdict().get("body") or text).strip()
        if not body:
            return None
        trace(session, "writing", name)
        if name == "citation":
            return {"reply": _ask("Format the supplied citation in APA style. Use only supplied bibliographic details; "
                                  "do not substitute another work or invent missing fields. Identify missing details.", body),
                    "sources": [], "status": "draft"}
        if name == "hypothesis":
            return {"reply": _ask("Draft the requested hypothesis section for the supplied study only. "
                                  "Do not assume a dataset, sample, test result or p-value. "
                                  "If the study details are missing, state what is needed.", body),
                    "sources": [], "status": "draft"}
        if name == "stats_check":
            return {"reply": _ask("Review the supplied statistical report. Cite specific supplied text or data for each finding. "
                                  "If no report or data was supplied, say that it is needed; do not pretend to review it.", body),
                    "sources": [], "status": "draft"}
        if name == "convert_pdf":
            return {"reply": "I need the source document or its full path to convert it to PDF. "
                             "Upload it or provide its path; no PDF has been created.",
                    "sources": [], "status": "needs_input", "verified": False, "needs_input": True}
        if name == "summary":
            return {"reply": _ask("Summarize the TEXT in a short paragraph, then 3 bullet takeaways.", body), "sources": []}
        if name == "paraphrase":
            tone = TONE.search(text)
            how = f" in a {tone.group(1)} tone" if tone else ""
            return {"reply": _ask(f"Paraphrase the TEXT{how}, same meaning, different wording.", body), "sources": []}
        if name == "bullets":
            return {"reply": _ask("Convert the TEXT into concise bullet points (- item), preserving order and facts.", body), "sources": []}
        if name == "expand":
            return {"reply": _ask("Expand the TEXT into a fuller version with concrete detail and examples, without inventing facts.", body), "sources": []}
        if name == "grammar":
            fixed = _ask("Fix grammar, spelling and punctuation in the TEXT. Keep wording and meaning. Output the corrected text only.", body)
            return {"reply": fixed, "sources": []}
        if name == "email":
            to = (m.groupdict().get("to") or "").strip()
            who = f" addressed to {to}" if to else ""
            return {"reply": _ask(f"Write a short, clear email{who} from these points. Include a subject line and sign-off.", body), "sources": []}
        if name == "draft_reply":
            return {"reply": _ask("Draft a professional reply declining the supplied meeting request. "
                                  "Use only supplied names, reasons and dates; leave unknown details as placeholders. "
                                  "Do not invent the owner's name or commitments. This is a draft, not a sent message.", body),
                    "sources": [], "status": "draft"}
        if name == "post":
            kind = (m.groupdict().get("kind") or "social").lower()
            limit = {"linkedin": "180-220 words, professional", "twitter": "under 280 characters",
                     "facebook": "80-120 words", "instagram": "short, with 5 hashtags"}.get(kind, "80-120 words")
            content = _ask(f"Write a {kind} post about the TOPIC. {limit}.", body, "")
            return {"reply": f"Here is a draft {kind.title()} post for your project (draft only, not published):\n\n{content}\n\n*(Note: Please fill in specific project metrics or features before posting.)*", "sources": []}
        if name == "outline":
            return {"reply": _ask("Write a blog post outline for the TOPIC: title, 5-7 section headings, one line under each.", body), "sources": []}
    return None


if __name__ == "__main__":
    assert match("summarize this: long text here")
    assert match("Summarize the following:\nline one\nline two")
    assert match("paraphrase this in a formal tone: hello world")
    assert match("rewrite this: hello world")
    assert match("make bullet points from these notes: a, b, c")
    assert match("convert to bullet points: a b c")
    assert match("expand this: we shipped the app")
    assert match("fix the grammar of this: i has went")
    assert match("draft an email saying the meeting moved to 5")
    assert not match("draft an email to my professor asking for an extension")  # chat's labelled case
    assert match("write a linkedin post about local AI assistants")
    assert match("blog outline for machine learning basics")
    assert not match("what's the weather tomorrow")
    assert not match("open youtube")  # browser's job
    assert not match("translate this to urdu: hello")  # utilities' translate handles language conversion
    # no-network run: only the routing is asserted here; a live LLM check exists in the skills checker
    print("[self-check] writing OK - routing for summarize/paraphrase/bullets/expand/grammar/email/post/outline")
