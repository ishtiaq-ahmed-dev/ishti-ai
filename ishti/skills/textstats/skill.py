"""Text analysis without any model (deterministic, instant, offline).

sentiment (lexicon), keywords (TF after stopwords), readability (Flesch reading ease),
NER-lite (proper nouns, emails, URLs, numbers, dates), counts & reading time.
Heuristics are labelled as such in replies — the planner can do a deeper read on request.
"""
import re
from collections import Counter

I = re.I

POS = set("good great excellent amazing wonderful fantastic love loved lovely happy glad best better beautiful perfect thanks thank helpful brilliant superb outstanding positive success successful enjoy enjoyed delightful awesome nice".split())
NEG = set("bad worse worst terrible awful horrible hate hated sad angry upset poor disappointing disappointed fail failed failure problem problems issue issues bug bugs broken useless annoying frustrating terrible negative wrong error errors slow ugly expensive rude dirty noisy".split())

SENT = re.compile(r"^\s*(?:what(?:'s| is)\s+the\s+)?sentiment\s+(?:of\s+)?(?:this|the\s+following|it)?\s*[:\-]?\s*(?P<body>.+)$", I | re.S)
KEYW = re.compile(r"^\s*(?:what\s+are\s+the\s+)?(?:top\s+)?keywords?\s+(?:of|from|in|for)\s+(?:this|the\s+following|it\s+text)?\s*[:\-]?\s*(?P<body>.+)$|^\s*(?:what\s+are\s+the\s+)?(?:top\s+)?keywords?\s*[:\-]?\s+(?P<body2>.+)$", I | re.S)
READ = re.compile(r"^\s*(?:what(?:'s| is)\s+the\s+)?readability\s+(?:of\s+)?(?:this|the\s+following|it)?\s*[:\-]?\s*(?P<body>.+)$", I | re.S)
COUNT = re.compile(r"^\s*(?:how\s+many\s+words\s+(?:are\s+)?(?:in|of)|word\s+count\s+(?:of|for)|count\s+the\s+words\s+in)\s+(?:this|the\s+following|it)?\s*[:\-]?\s*(?P<body>.+)$", I | re.S)
ANALYZE = re.compile(r"^\s*(?:analyze|analyse)\s+(?:this|the\s+following)\s+text\s*[:\-]?\s*(?P<body>.+)$", I | re.S)
NER = re.compile(r"^\s*(?:what\s+)?(?:entities|names?|people|places|organizations?)\s+(?:are\s+)?(?:in|mentioned\s+in)\s+(?:this|the\s+following|it)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)

STOP = set("the a an and or but if then else for of to in on at by with from as is are was were be been being it its this that these those i you he she we they them his her their our your my me him us what which who whom whose when where why how all any both each few more most other some such no nor not only own same so than too very can will just don should now".split())

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
URL = re.compile(r"https?://\S+")
NUM = re.compile(r"\b\d[\d,.]*\b")
DATE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|yesterday)\b", I)
CAPWORD = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")


def _words(body):
    return re.findall(r"[a-z']{2,}", body.lower())


def match(text):
    t = text.strip()
    return any(rx.match(t) for _n, rx, _f in _HANDLERS)


def _sentiment(body):
    ws = _words(body)
    pos = sum(1 for w in ws if w in POS)
    neg = sum(1 for w in ws if w in NEG)
    total = pos + neg
    if total == 0:
        label = "neutral"
    else:
        score = (pos - neg) / max(len(ws), 1) * 100
        label = "positive" if score > 1 else "negative" if score < -1 else "mixed/neutral"
    return (f"Sentiment (lexicon heuristic): {label} — {pos} positive vs {neg} negative cue words "
            f"in {len(ws)} words.")


def _keywords(body, n=8):
    ws = [w for w in _words(body) if w not in STOP and len(w) > 2]
    if not ws:
        return "No keywords found (text too short)."
    top = Counter(ws).most_common(n)
    return "Top keywords: " + ", ".join(f"{w} ({c})" for w, c in top)


def _readability(body):
    sents = [s for s in re.split(r"[.!?]+", body) if s.strip()]
    ws = _words(body)
    if not ws or not sents:
        return "Not enough text to score readability."
    syll = sum(max(1, len(re.findall(r"[aeiouy]+", w))) for w in ws)
    score = 206.835 - 1.015 * (len(ws) / len(sents)) - 84.6 * (syll / len(ws))
    band = ("very easy" if score >= 90 else "easy" if score >= 80 else "fairly easy" if score >= 70
            else "plain English" if score >= 60 else "fairly difficult" if score >= 50
            else "difficult" if score >= 30 else "very difficult")
    return (f"Flesch reading ease: {score:.0f}/100 ({band}). "
            f"{len(ws)} words, {len(sents)} sentences, ~{max(1, len(ws)//200)} min read.")


def _ner(body):
    ents = {"emails": sorted(set(EMAIL.findall(body))),
            "urls": sorted(set(URL.findall(body))),
            "numbers": sorted(set(NUM.findall(body)))[:10],
            "dates/times": sorted(set(m.group(0) for m in DATE.finditer(body)))[:10],
            "proper nouns": []}
    seen = set()
    for m in CAPWORD.finditer(body):
        w = m.group(0)
        if w.lower() not in STOP and w not in seen and not EMAIL.search(w):
            seen.add(w)
            ents["proper nouns"].append(w)
    ents["proper nouns"] = ents["proper nouns"][:15]
    lines = [f"- {k}: {', '.join(v) if v else '(none)'}" for k, v in ents.items()]
    return "Entities (regex-lite, not a model):\n" + "\n".join(lines)


def _counts(body):
    ws = _words(body)
    sents = len([s for s in re.split(r"[.!?]+", body) if s.strip()])
    return (f"{len(body.split())} words (tokenised: {len(ws)}), {len(body)} characters, "
            f"{sents} sentences, ~{max(1, len(ws)//200)} min reading time.")


_HANDLERS = [("sentiment", SENT, "sentiment_label"), ("keywords", KEYW, "kw_label"),
             ("readability", READ, "read_label"), ("count", COUNT, "count_label"),
             ("entities", NER, "ner_label"), ("analyze", ANALYZE, "analyze_label")]


def run(text, session):
    t = text.strip()
    for _name, rx, fn in _HANDLERS:
        m = rx.match(t)
        if not m:
            continue
        gd = m.groupdict()
        body = (gd.get("body") or gd.get("body2") or "").strip()
        if not body:
            return None
        return {"reply": globals()[fn](body), "sources": []}
    return None


def sentiment_label(body): return _sentiment(body)
def kw_label(body): return _keywords(body)
def read_label(body): return _readability(body)
def count_label(body): return _counts(body)
def ner_label(body): return _ner(body)
def analyze_label(body): return "\n\n".join([_counts(body), _sentiment(body), _keywords(body), _readability(body), _ner(body)])


if __name__ == "__main__":
    sample = "I love this product. It was amazing and helpful. Delivery was slow but support fixed it. Contact ali@example.com or https://shop.pk"
    assert match("sentiment of: I love this")
    assert match("what are the keywords from this text: hello world of war")
    assert match("readability of: The quick brown fox jumps.")
    assert match("how many words in this text: one two three")
    assert match("analyze this text: " + sample)
    assert match("what entities are in this: Ahmed met Sara at IMSciences on 12/03")
    assert not match("what's the weather")
    r = run("sentiment of: I love this amazing product but hate the slow delivery", {})
    assert "mixed" in r["reply"] or "positive" in r["reply"], r
    r2 = run("keywords from: the quick brown fox jumps over the lazy dog the fox is quick", {})
    assert "fox" in r2["reply"], r2
    r3 = run("how many words in this text: one two three four", {})
    assert "words" in r3["reply"] and "1 sentences" in r3["reply"], r3
    r4 = run("analyze this text: " + sample, {})
    assert "ali@example.com" in r4["reply"], r4
    assert "Flesch" in r4["reply"], r4
    print("[self-check] textstats OK - sentiment/keywords/readability/counts/entities, all heuristic, no model")
