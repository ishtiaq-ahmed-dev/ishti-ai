"""
ISHTI Source Relevance Filter.

The research pipeline used to pool whatever the search engine returned and then order the
model to write a "Cross-Verification & Consensus" section over it. Asked for tomorrow's
weather, it cited a dictionary definition of the word "tomorrow" (docs/AUDIT.md F4).

Relevance is decided here, in code, before synthesis - not requested in a prompt.
"""

import re
from typing import Dict, List, Sequence

STOPWORDS = {
    "what", "whats", "when", "where", "which", "who", "why", "how", "the", "a", "an", "is",
    "are", "was", "were", "will", "would", "can", "could", "should", "do", "does", "did",
    "for", "and", "or", "but", "of", "to", "in", "on", "at", "by", "with", "from", "about",
    "me", "my", "you", "your", "i", "it", "this", "that", "these", "those", "please", "tell",
    "find", "search", "look", "up", "get", "give", "latest", "current", "now", "today",
    "tomorrow", "week", "new", "best", "good", "need", "want", "know", "let", "internet",
    # meta-words: they describe the request, not the subject, so they cannot discriminate
    "research", "news", "information", "info", "update", "updates", "article", "articles",
    "report", "story", "stories", "happening", "developments", "recent", "recently",
    "weekly", "top", "things", "stuff", "list", "guide", "overview",
}

# Domains that answer "what does this word mean", never "what is happening".
REFERENCE_DOMAINS = {
    "dictionary.cambridge.org", "merriam-webster.com", "dictionary.com", "thesaurus.com",
    "grammarly.com", "collinsdictionary.com", "vocabulary.com", "wordreference.com",
    "urbandictionary.com", "thefreedictionary.com", "grammar.com", "yourdictionary.com",
}

DEFINITION_CUES = ("what is", "what does", "define", "meaning of", "definition of", "spelling")

# Questions about a moment in time. A site's front page is not evidence for these.
TIME_SCOPED_CUES = ("this week", "today", "latest", "recent", "current", "now", "yesterday",
                    "this month", "breaking", "just announced")


def is_homepage(source: Dict) -> bool:
    """https://github.com/ is a front door, not an article."""
    url = (source.get("url") or "").rstrip("/")
    return bool(re.fullmatch(r"https?://[^/]+", url))


def content_words(text: str) -> set:
    # len >= 2 keeps real short subjects like "ai", "go", "3d"
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 2 and w not in STOPWORDS}


def is_definition_query(query: str) -> bool:
    return any(cue in (query or "").lower() for cue in DEFINITION_CUES)


def matched_words(query: str, source: Dict) -> set:
    blob = " ".join(str(source.get(k, "")) for k in ("title", "snippet", "content", "url"))
    return content_words(query) & content_words(blob)


def score(query: str, source: Dict) -> float:
    """0.0 - 1.0. Fraction of the query's content words present in the source's text."""
    q = content_words(query)
    return len(matched_words(query, source)) / len(q) if q else 0.0


def is_relevant(query: str, source: Dict) -> bool:
    """
    A single incidental word is not relevance: "Recent Files in Windows 11" shares the word
    "recent" with a research query and nothing else. Specific queries must match at least
    two content words; short ones only have one to give.
    """
    q = content_words(query)
    hits = len(matched_words(query, source))
    return hits >= (2 if len(q) >= 3 else 1)


def domain_of(source: Dict) -> str:
    url = source.get("url", "") or ""
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1).lower() if m else (source.get("source", "") or "").lower()


def filter_sources(query, sources: Sequence[Dict], min_score: float = 0.25) -> List[Dict]:
    """
    Keeps only sources that actually address the query, best first.

    `query` may be one string or several (a research run's sub-queries). A source has to
    match ONE query well - scoring against all of them concatenated would demand a single
    page cover every angle, which drops everything.

    Reference/dictionary domains are dropped unless the query really is a definition
    question. Returns [] rather than padding - callers must say "no relevant sources found".
    """
    queries = [query] if isinstance(query, str) else [q for q in query if q]
    joined = " ".join(queries).lower()

    allow_reference = any(is_definition_query(q) for q in queries)
    time_scoped = any(cue in joined for cue in TIME_SCOPED_CUES)
    survivors = [
        s for s in sources
        if (allow_reference or not any(d in domain_of(s) for d in REFERENCE_DOMAINS))
        and not (time_scoped and is_homepage(s))
    ]

    # A one-word query ("AI news") cannot supply two matching words, but full coverage of
    # its single word is still a real signal - and it is the owner's actual question, so it
    # must not be discarded in favour of the narrower sub-queries generated around it.
    usable = [q for q in queries if content_words(q)]
    if not usable:
        return survivors

    kept = sorted(
        ((max(score(q, s) for q in usable), s) for s in survivors
         if any(is_relevant(q, s) for q in usable)),
        key=lambda pair: pair[0], reverse=True,
    )
    return [s for sc, s in kept if sc >= min_score]


if __name__ == "__main__":
    q = "weather forecast Peshawar tomorrow"
    pool = [
        {"title": "Peshawar Weather Forecast", "snippet": "Peshawar forecast highs 32C",
         "url": "https://www.accuweather.com/en/pk/peshawar/1"},
        {"title": "TOMORROW | English meaning", "snippet": "tomorrow definition: the day after today",
         "url": "https://dictionary.cambridge.org/dictionary/english/tomorrow"},
        {"title": "Tommorow vs. Tomorrow", "snippet": "Which spelling is correct",
         "url": "https://www.grammarly.com/blog/tomorrow/"},
        {"title": "ChatGPT", "snippet": "A conversational AI product", "url": "https://openai.com/chatgpt"},
    ]
    kept = filter_sources(q, pool)
    assert len(kept) == 1, [k["title"] for k in kept]
    assert "accuweather" in kept[0]["url"], kept

    # a real definition question may keep a dictionary
    kept2 = filter_sources("what is the definition of entropy",
                           [{"title": "Entropy", "snippet": "entropy definition thermodynamics",
                             "url": "https://www.merriam-webster.com/dictionary/entropy"}])
    assert len(kept2) == 1, kept2

    # broad topic: its single subject word must actually appear in the source
    broad = filter_sources("latest AI news this week", [
        {"title": "AI model ships", "snippet": "announced today", "url": "https://techcrunch.com/x"},
        {"title": "TOMORROW definition", "snippet": "meaning", "url": "https://dictionary.cambridge.org/y"},
        {"title": "Texas prison ruling", "snippet": "air conditioning", "url": "https://z.com/c"},
    ])
    assert len(broad) == 1 and "techcrunch" in broad[0]["url"], broad

    # a front page is not evidence for "what happened this week"
    timed = filter_sources("latest AI news this week", [
        {"title": "GitHub", "snippet": "where the world builds software", "url": "https://github.com/"},
        {"title": "Anthropic ships AI update", "snippet": "new AI model announced Tuesday",
         "url": "https://techcrunch.com/2026/09/22/anthropic"},
    ])
    assert len(timed) == 1 and "techcrunch" in timed[0]["url"], timed

    # the owner's own short question still filters: a source must cover its whole subject
    short = filter_sources(
        ["latest AI news this week", "recent breakthroughs in artificial intelligence"],
        [{"title": "AI model beats benchmark", "snippet": "new AI system", "url": "https://x.com/a"},
         {"title": "Texas prison ruling", "snippet": "air conditioning order", "url": "https://y.com/b"}])
    assert len(short) == 1 and "x.com" in short[0]["url"], short

    # several sub-queries: a source needs to match ONE of them, not all
    multi = filter_sources(
        ["Research the latest AI news this week",
         "recent breakthroughs in artificial intelligence",
         "top AI stories in technology"],
        [{"title": "Anthropic announces new model", "snippet": "artificial intelligence breakthroughs",
          "url": "https://techcrunch.com/2026/09/22/anthropic"},
         {"title": "How to View Recent Files in Windows 11", "snippet": "open file explorer",
          "url": "https://majorgeeks.com/view_recent_files.html"}])
    assert len(multi) == 1 and "techcrunch" in multi[0]["url"], multi

    # nothing relevant -> empty, never padded
    assert filter_sources("latest ollama release version", [
        {"title": "Cooking pasta", "snippet": "boil water", "url": "https://example.com/pasta"}]) == []
    print("[self-check] relevance OK - dictionary noise dropped, definition queries exempt, no padding")
