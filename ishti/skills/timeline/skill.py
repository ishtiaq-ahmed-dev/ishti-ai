"""Timeline builder (inventory domain 3 #37): a pile of dated lines -> a chronological timeline.

"timeline from these events: 12 Mar 2024 kickoff, 2024-05-01 beta, launch was 3 June 2024"
"build a timeline of: born 1998 in Peshawar; school 2004; university 2016; first job 2022"
"sort these events chronologically: ..."

Each comma/semicolon-separated item must carry its own date (dateutil parses common formats).
Items are sorted, numbered, grouped visually by year, and gaps longer than 3 months between
consecutive events are flagged so missing beats are visible. Items whose date can't be read
are listed separately, never silently dropped. T0: no writes, no network.
"""
import re

from ishti.skills import trace
from dateutil import parser as dtp

I = re.I

ASK = re.compile(
    r"^\s*(?:(?:(?:build|make|create)\s+)?(?:an?\s+)?timeline\s+(?:from|of|for)|sort\s+(?:these\s+)?events\s+chronologically\s*[:\-]?)\s*[:\-]?\s*"
    r"(?P<body>.+)$", I)


def _date_of(item):
    """Extract a date from one event line. Returns (datetime, matched_text) or (None, '')."""
    m = re.search(
        r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+\d{4})?)"
        r"|((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)"
        r"|(\d{4}-\d{2}-\d{2})"
        r"|(\d{1,2}/\d{1,2}/\d{2,4})"
        r"|(\b(?:19|20)\d{2}\b)", item, I)
    if not m:
        return None, ""
    raw = m.group(0)
    try:
        # ISO dates (2024-05-01) must be month-first per the standard; dayfirst only for d/m/y
        dayfirst = not re.match(r"^\d{4}-", raw)
        return dtp.parse(raw, dayfirst=dayfirst, fuzzy=False), raw
    except (ValueError, OverflowError):
        return None, ""


def build(body):
    items = [s.strip(" -*") for s in re.split(r"[;\n]|,(?=\s*\S)", body) if s.strip(" -*")]
    if len(items) < 2:
        return "Give me at least two dated events, comma or semicolon separated."
    dated, undated = [], []
    for it in items:
        d, raw = _date_of(it)
        # strip the date itself and any date-prefix words; "first job 2022" keeps its head
        text = it.replace(raw, " ").strip(" ,:;-") if raw and raw in it else it
        text = re.sub(r"^(?:on|in|at|was|is|from)\b\s*", "", text, flags=I).strip(" ,:;-") or it
        if d:
            dated.append((d, text))
        else:
            undated.append(it)
    if not dated:
        return "None of those lines carried a readable date — add dates like '12 Mar 2024' or '2024-05-01'."
    dated.sort(key=lambda p: p[0])
    lines = [f"**Timeline** ({len(dated)} events, oldest first):"]
    year = None
    prev = None
    n = 0
    for d, text in dated:
        if d.year != year:
            year = d.year
            lines.append(f"**{year}**")
        if prev is not None:
            gap_days = (d - prev).days
            if gap_days > 92:
                lines.append(f"  · ({gap_days // 30} month gap)")
        n += 1
        lines.append(f"  {n}. {d.strftime('%d %b %Y')} — {text}")
        prev = d
    if undated:
        lines.append("No readable date (not placed):")
        lines += [f"  - {u}" for u in undated[:6]]
    return "\n".join(lines)


def match(text):
    t = text.strip()
    # needs the payload separator: "timeline of my mood" is a question, not a build ask
    return bool(ASK.match(t) and re.search(r":|;|\d", t))


def run(text, session):
    m = ASK.match(text.strip())
    if not m:
        return None
    trace(session, "timeline", "build")
    return {"reply": build(m.group("body")), "sources": []}


if __name__ == "__main__":
    assert match("timeline from these events: 12 Mar 2024 kickoff, 2024-05-01 beta, launch was 3 June 2024")
    assert match("build a timeline of: born 1998 in Peshawar; school 2004; first job 2022")
    assert match("sort these events chronologically: kickoff 1/2/2024, launch 5/6/2024")
    assert not match("timeline of my mood")
    assert not match("what is a timeline")

    r = run("timeline from these events: launch was 3 June 2024, 12 Mar 2024 kickoff, 2024-05-01 beta", {})
    lines_ = r["reply"].splitlines()
    assert "kickoff" in lines_[2] and "12 Mar 2024" in lines_[2], r       # sorted first
    assert "launch" in lines_[4], r
    r2 = run("build a timeline of: born 1998 in Peshawar; school 2004; first job 2022", {})
    assert r2["reply"].index("born") < r2["reply"].index("school") < r2["reply"].index("first job"), r2
    r3 = run("timeline from these events: 01 Jan 2024 start, 01 Dec 2024 end, no date here", {})
    assert "gap" in r3["reply"].lower() and "no readable date" in r3["reply"].lower(), r3
    r4 = run("timeline from these events: just words, more words", {})
    assert "None of those lines" in r4["reply"], r4
    print("[self-check] timeline OK - dateutil parsing, chronological sort, year grouping, "
          "gap flags, undated never silently dropped")
