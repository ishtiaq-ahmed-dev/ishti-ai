"""Things the local model writes for the owner, saved to Documents\\ISHTI:
flashcards (CSV that Anki imports), a quiz (answers at the end), a study plan, a YouTube lecture summary (audio via
yt-dlp, transcribed locally), a trip itinerary, a general workout plan, a meal plan, a social-media post draft
(drafts only - never posted). Source can be a topic, or a document path (PDF/Word/text) to learn from."""
import csv
import datetime as dt
import os
import re

from ishti import llm
from ishti.arch2.jsonio import parse_json
from ishti.untrusted import guard
from ishti.skills import trace

I = re.I
HOME = os.path.join(os.path.expanduser("~"), "Documents", "ISHTI")
URL = re.compile(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+\S*", I)
OPS = [
    ("lecture", re.compile(r"\b(?:summari[sz]e|notes\s+(?:on|from)|what\s+is\s+this)\b.*(?:youtube\.com|youtu\.be)", I)),
    ("flashcards", re.compile(r"\bflash\s*cards?\b", I)),
    ("quiz", re.compile(r"\b(?:quiz|test)\s+me\b|\b(?:make|create|generate)\s+(?:a\s+)?(?:quiz|mcqs?|practice\s+questions)\b", I)),
    ("study_plan", re.compile(r"\bstudy\s+(?:plan|schedule|timetable)\b|\bplan\s+(?:my\s+)?(?:studies|revision|preparation)\b", I)),
    ("itinerary", re.compile(r"\b(?:itinerary|trip\s+plan|travel\s+plan|plan\s+(?:a|my)\s+(?:trip|tour|visit|vacation|holiday))\b", I)),
    ("workout", re.compile(r"\b(?:workout|exercise|gym|fitness)\s+(?:plan|routine|program(?:me)?|schedule)\b", I)),
    ("meal", re.compile(r"\b(?:meal|diet)\s+(?:plan|chart)\b", I)),
    ("post", re.compile(r"\b(?:write|draft|make)\s+(?:a\s+|an\s+)?(?:linkedin|twitter|x|facebook|instagram|social\s+media)?\s*(?:post|caption|tweet)\b", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in OPS:
        if rx.search(text):
            trace(session, "planner", name.replace("_", " "), status="start")
            out = globals()["op_" + name](text, session)
            trace(session, "planner", "done", status="done")
            return {"reply": out, "sources": []}
    return None


def source(text):
    """Text of a document the request points at (to learn from), else ''."""
    m = re.search(r"[A-Za-z]:\\[^\"'<>|?*\n]+?\.(?:pdf|docx|pptx|txt|md)\b", text, I)
    if m and os.path.isfile(m.group(0)):
        from ishti.skills.docs.skill import text_of
        return text_of(m.group(0), limit=12000)
    return ""


def save(kind, topic, body, ext=".md"):
    os.makedirs(HOME, exist_ok=True)  # home:Documents/ISHTI is in the manifest's write scope
    name = re.sub(r"[^\w -]", "", topic)[:50].strip() or kind
    from ishti import scope
    path = os.path.join(HOME, f"{kind} - {name} {dt.date.today()}{ext}")
    scope.check_write(path)  # fail with ScopeError, not an OS error, if the scope was narrowed
    if ext == ".md":
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    return path


def topic_of(text, kind_words):
    t = re.sub(r"[A-Za-z]:\\\S+", "", text)
    t = re.sub(rf"^.*?\b(?:{kind_words})\b\s*(?:on|about|for|of|from|to)?\s*", "", t, flags=I).strip(" .?")
    return t or "general"


def op_flashcards(text, session):
    topic, src = topic_of(text, "flash\\s*cards?"), source(text)
    n = int((re.search(r"(\d{1,2})\s*(?:flash\s*)?cards?", text, I) or [0, 15])[1])
    raw = llm.ask(f"Make {n} flashcards " + (f"from this material:\n{guard(src[:9000], 'study material')}" if src else f"about: {topic}")
                  + '\nReturn JSON {"cards": [{"front": "<question>", "back": "<short answer>"}]}.',
                  system=None, temperature=0.3, max_tokens=1800, response_format={"type": "json_object"})
    d = parse_json(raw)
    if not d:
        return ("I could not format the flashcards right now - the model returned something I could not parse. "
                "Please try again (rephrasing the topic sometimes helps).")
    cards = [c for c in d.get("cards", []) if isinstance(c, dict) and c.get("front") and c.get("back")]
    if not cards:
        return ("I could not build any flashcards from that - the model returned no usable cards. "
                "Try naming the topic or attaching a document to learn from.")
    path = save("Flashcards", topic, "", ".csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([(c["front"], c["back"]) for c in cards])
    preview = "\n".join(f"- **{c['front']}** → {c['back']}" for c in cards[:6])
    return f"{len(cards)} flashcards saved for Anki (File > Import): {path}\n\n{preview}"


def op_quiz(text, session):
    topic, src = topic_of(text, "quiz|test\\s+me|mcqs?|practice\\s+questions"), source(text)
    n = int((re.search(r"(\d{1,2})\s*(?:questions|mcqs?)", text, I) or [0, 8])[1])
    body = llm.ask(f"Write a {n}-question multiple-choice quiz " + (f"on this material:\n{guard(src[:9000], 'study material')}" if src else f"about {topic}")
                   + "\nNumber the questions, options A-D on separate lines, and put an 'Answers' section with short "
                     "explanations at the very end.", max_tokens=1800)
    path = save("Quiz", topic, body)
    return f"Quiz saved: {path}\n\n{body.split('Answers')[0].strip()[:2500]}\n\n(Answers are at the end of the file - say \"show the answers\" when ready.)"


def op_study_plan(text, session):
    topic = topic_of(text, "study\\s+(?:plan|schedule|timetable)|plan\\s+(?:my\\s+)?(?:studies|revision|preparation)")
    from ishti.facts import get_facts
    me = get_facts().get("owner")
    body = llm.ask(f"Make a realistic day-by-day study plan: {text}\nThe student: {me.get('role', '')}, {me.get('university', '')}, "
                   f"{me.get('semester', '')} semester. Include daily goals, revision days and short breaks; a Markdown table "
                   "per week.", max_tokens=1800)
    return f"Study plan saved: {save('Study plan', topic, body)}\n\n{body[:2500]}"


def op_lecture(text, session):
    import tempfile
    import yt_dlp
    m = URL.search(text)
    if not m:  # routed by similarity without a URL in the text
        return "Paste the YouTube link to summarise - e.g. 'summarize this lecture https://youtu.be/...'"
    url = m.group(0)
    with tempfile.TemporaryDirectory() as work:
        trace(session, "yt-dlp", "downloading the audio", status="start")
        opts = {"format": "bestaudio/best", "outtmpl": os.path.join(work, "a.%(ext)s"), "quiet": True, "noplaylist": True,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]}
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
        audio = next(os.path.join(work, f) for f in os.listdir(work))
        trace(session, "whisper", f"transcribing {info.get('duration', 0) // 60} min locally", status="start")
        from ishti.voice import stt_call, stt_model
        model = stt_model()
        transcript = stt_call(lambda: " ".join(s.text.strip() for s in model.transcribe(
            audio, beam_size=1, vad_filter=True, temperature=0.0, condition_on_previous_text=False)[0]))
    notes = llm.ask(f"Lecture: {info.get('title')}\nTranscript:\n{guard(transcript[:14000], 'the lecture transcript', session)}\n\nWrite study notes: summary, key "
                    "concepts with short explanations, and 5 review questions.", max_tokens=1600)
    path = save("Lecture notes", info.get("title", "lecture"), f"# {info.get('title')}\n{url}\n\n{notes}\n\n---\n## Transcript\n{transcript}")
    return f"Notes on **{info.get('title')}** saved: {path}\n\n{notes[:2500]}"


def _plan(kind, words, text, extra):
    topic = topic_of(text, words)
    body = llm.ask(f"{text}\n{extra}", max_tokens=1800)
    return f"{kind} saved: {save(kind, topic, body)}\n\n{body[:2500]}"


def _facts_about(text):
    """What the web says about the destination - the 14B model alone put Ayubia (Galiyat) in Swat."""
    from tools.web.search import SearxngSearchProvider
    dest = re.sub(r"\b(?:plan|a|my|trip|tour|visit|vacation|holiday|itinerary|to|for|\d+\s*days?|days?|weekend)\b", " ", text, flags=I)
    dest = re.sub(r"\s+", " ", dest).strip(" .") or text
    hits = SearxngSearchProvider().search(f"{dest} tourist places things to do", 6)
    return "\n".join(f"- {h.title}: {h.snippet}" for h in hits)


def op_itinerary(text, session):
    from ishti.facts import get_facts
    home = get_facts().get("owner").get("city", "")
    trace(session, "search", "checking what's actually at the destination", status="start")
    notes = _facts_about(text)
    return _plan("Itinerary", "itinerary|trip\\s+plan|travel\\s+plan|plan\\s+(?:a|my)\\s+(?:trip|tour|visit|vacation|holiday)", text,
                 (f"The owner travels from {home}.\n" if home else "") + f"What web sources say about the destination:\n{notes}\n\n"
                 "Write a day-by-day itinerary: places, timings, travel between them, rough costs in the local currency, "
                 "food to try, and tips. Only include places that really are at or near the destination (use the sources); "
                 "if unsure, leave it out. Say that prices and opening hours should be checked before going.")


def op_workout(text, session):
    return _plan("Workout plan", "workout|exercise|gym|fitness", text,
                 "Write a general weekly workout plan (exercises, sets x reps, rest days, warm-up). General fitness "
                 "guidance only, not medical advice; suggest checking with a doctor if there are health conditions.")


def op_meal(text, session):
    return _plan("Meal plan", "meal|diet", text, "Write a practical weekly meal plan with Pakistani home food where it "
                 "fits, a shopping list at the end. General guidance, not medical or dietetic advice.")


def op_post(text, session):
    platform = (re.search(r"\b(linkedin|twitter|x|facebook|instagram)\b", text, I) or [None, "social media"])[1]
    from ishti.facts import get_facts
    known = []
    for entity, attr, value in get_facts().mentioned_in(text):  # "my ISHTI project": its README says what it is
        known.append(f"{entity} {attr}: {value}")
        for cand in (os.path.join(value, "README.md"), os.path.join(value, ".github", "README.md")) if attr == "location" else ():
            if os.path.isfile(cand):
                with open(cand, encoding="utf-8", errors="ignore") as f:
                    known.append(re.sub(r"\s+", " ", f.read(1500)))
                break
    body = llm.ask(f"Draft a {platform} post: {text}\n" + ("Facts to use (only these, don't invent features; never print local file paths):\n" + "\n".join(known) + "\n" if known else "")
                   + "Match the platform's style and length. Give 2 alternatives.", max_tokens=700)
    return f"Drafts ({platform}) - copy the one you like; I don't post anything myself:\n\n{body}"


if __name__ == "__main__":
    cases = {"make 10 flashcards on photosynthesis": "flashcards", "quiz me on python decorators": "quiz",
             "make a study plan for my final exams in 3 weeks": "study_plan", "summarize https://www.youtube.com/watch?v=abc123": "lecture",
             "plan a trip to swat for 3 days": "itinerary", "give me a workout plan for beginners": "workout",
             "make a meal plan for weight loss": "meal", "write a linkedin post about my ISHTI project": "post"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("what's the weather") and not match("open youtube")
    assert topic_of("make 10 flashcards on photosynthesis", "flash\\s*cards?") == "photosynthesis"
    # save() checks the manifest scope before writing (regression: the 2026-09-30 ScopeError crashed
    # with OSError instead of being answered; the manifest now allows exactly home:Documents/ISHTI)
    from ishti import scope
    from ishti.audit import context as audit_ctx
    assert os.path.normcase(scope._roots({"write": ["home:Documents/ISHTI"]})[0]) == \
        os.path.normcase(os.path.join(os.path.expanduser("~"), "Documents", "ISHTI"))
    with audit_ctx(skill="planners"):
        scope.check_write(os.path.join(HOME, "Flashcards - x.csv"))  # must not raise (the skill's own output folder)
        try:
            scope.check_write(os.path.join(os.path.expanduser("~"), "Downloads", "x.csv"))
            raise SystemExit("scope regression: planners wrote outside its manifest scope")
        except scope.ScopeError:
            pass
    print("[self-check] planners OK - routing, topic extraction, scope")
