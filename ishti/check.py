"""End-to-end check of the core loop against real Ollama/Laya, with a throwaway memory DB.
    python -m ishti.check"""
import os
import tempfile
import time

from ishti import CONFIG

tmp = tempfile.mkdtemp()
CONFIG["memory"].update(db_path=os.path.join(tmp, "t.db"), chroma_dir=os.path.join(tmp, "chroma"))

from ishti import dispatcher as d  # noqa: E402  (config must be patched first)
from ishti import tool_routing as router  # ishti/router.py was removed; tool_routing is what replaced it

# Labelled routing set. Grows into the fine-tuning set for the Laya typed-decisions checkpoint.
LABELLED = [
    ("tell me a joke", "chat"), ("explain quantum entanglement simply", "chat"),
    ("write a python function to reverse a linked list", "chat"), ("how do I fix a segfault in C", "chat"),
    ("draft an email to my professor asking for an extension", "chat"), ("good morning!", "chat"),
    ("thanks, that helped", "chat"),    ("who won the champions league final yesterday", "web_search"), ("latest news about nvidia", "utilities"), ("what's the price of gold today", "markets"),
    ("paper buy 10 shares of NVDA at 500", "trading"), ("how is my paper account doing", "trading"),
    ("connect my real broker to the bot", "trading"), ("should I buy BTC now", "markets"),
    ("who is the current prime minister of the UK", "web_search"), ("open the task manager", "apps"),
    ("find the invoice pdf on my computer", "files"), ("how much ram am I using", "system"),
    ("launch vs code", "apps"), ("mujhe batao mera project kahan hai", "memory"),
    ("which folder did I keep the thesis in", "memory"), ("where did I save my resume", "memory"),
    ("will it rain tomorrow", "weather"), ("is it cold outside", "weather"),
    ("what's the forecast for the weekend", "weather"), ("what is 17 times 23?", "chat"),
    ("log into my university portal and download the timetable", "browser"),
    ("fill the contact form on my website", "browser"),
    ("research the best local LLMs for coding and write a report", "deep_research"),
    ("do an in-depth investigation into solid-state batteries", "deep_research"),
    ("summarize https://example.com for me", "web_fetch"),
    ("trim the video holiday.mp4 to the first 30 seconds", "video"), ("how full is my disk", "system"),
    ("write a haiku about Peshawar rain", "chat"), ("write a poem about the weather", "chat"),
    ("is it going to snow in Murree this weekend", "weather"),
    ("remind me in 20 minutes to call Ali", "reminders"), ("set a timer for 10 minutes", "reminders"),
    ("list my reminders", "reminders"),
    ("how many pdfs are in my dwonload folder", "folders"), ("arrange the complete downloads folder", "folders"),
    ("what are the largest files on my desktop", "folders"),
    ("open a browser and search for RTX 5070 Ti drivers", "browser"), ("search google for the weather in dubai", "browser"),
    ("open chrome and go to github", "browser"), ("open youtube", "browser"), ("open the task manager", "apps"),
    ("find me the cheapest samsung galaxy s25", "shopping"), ("i want to buy an iphone 16, compare prices", "shopping"),
    ("where can I buy a ps5 slim", "shopping"),
    ("analyze nvidia stock", "markets"), ("usd to pkr rate", "markets"), ("OGDC share price", "markets"),
    (r"transcribe C:\Users\PC\Downloads\talk.mp3", "media"), ("record my screen for 2 minutes", "media"),
    (r"resize C:\Users\PC\Pictures\photo.jpg to 800 px wide", "media"),
    (r"summarize C:\Users\PC\Documents\report.pdf", "docs"), ("make a presentation about solar energy", "docs"),
    ("take a note: call the bank tomorrow", "docs"),
    ("prayer times today", "utilities"), ("convert 500 usd to pkr", "utilities"), ("5 km to miles", "utilities"),
    ("latest news about pakistan", "utilities"), ("generate a strong password", "utilities"),
    ("I spent 500 on groceries", "ledger"), ("show my portfolio", "ledger"), ("how much did I spend this month", "ledger"),
    ("I drank 3 glasses of water", "lifelog"), ("screen time today", "lifelog"), ("check your skills", "meta"), ("what's the status of my tasks", "meta"), ("use the deep male voice", "owner"),
    ("where is my order", "email"),
    ("clear the browser cache", "webtools"), ("delete the IMSciences bookmark", "webtools"),
    ("find duplicate contacts", "contacts"),
    ("when D:/app/server.log contains ERROR, tell me", "automation"), ("run the deploy script D:/proj/deploy.ps1", "dev"),
    ("who said what in D:/calls/meeting.mp3", "media"),
    ("extract the book titles and prices from https://books.toscrape.com into a spreadsheet", "extract"),
   
    ("find flights from Peshawar to Dubai on 15 October", "travel"), ("search hotels in Istanbul from 5 to 8 October", "travel"), ("I studied machine learning for 2 hours", "lifelog"), ("show my learning progress", "lifelog"), ("add a meeting with Ali tomorrow at 3pm to my google calendar", "calendar"), ("check ishti's python packages for updates", "meta"),
    ("when a pdf appears in downloads, summarize it", "automation"), ("list my automations", "automation"), ("create a new fastapi project called shop-api", "dev"), ("restart the print spooler service", "dev"),
    ("start a 25 minute focus sprint on my thesis", "focus"), ("focus history", "focus"),
    ("run an internet speed test", "speedtest"), ("how fast is my internet", "speedtest"),
    ("this is my brother Ahmed", "relationships"), ("what does my father like", "relationships"),
    ("what should I get Ahmed for his birthday", "relationships"),
    ("new client Ahmed via whatsapp", "clients"), ("draft a reply to Ahmed", "clients"),
    ("client dashboard", "clients"), ("invoice for Ahmed: logo design 15000", "clients"),
    ("brain training status", "braintrain"), ("export my training data", "braintrain"),
    ("invoice for Ahmed: logo design 15000", "clients"),
    ("make an invoice for Ali: logo design 15000", "docs"), ("fill D:/forms/admission.pdf with my details", "docs"), ("remind me to pay the electricity bill on the 12th of every month", "reminders"), ("mark my last expense as tax deductible", "ledger"), ("tax report", "ledger"),
    ("remove the background from D:/pics/me.jpg", "media"), ("upscale D:/pics/me.jpg 2x", "media"),
    ("how strong is the password hunter2x", "security"), ("encrypt D:/docs/tax.pdf", "security"), ("list my saved logins", "security"), ("log in to github with my saved login", "security"),
    ("read the text on my screen", "desktop"), ("back up D:/Projects to E:/Backups", "fileops"), ("clipboard history", "desktop"), ("new virtual desktop", "desktop"), ("print queue", "desktop"),
    ("record a macro called login", "desktop"), ("extend my displays", "desktop"), ("what voices do you have", "owner"), ("generate an image of a cat on the moon", "gemini"),
    ("create a video of waves at sunset with gemini", "gemini"), ("make a video from my clips", "video"),
    ("use an online tool to remove the background from D:/x.png", "online_fallback"),
    ("give me my brief", "digest"), ("every morning at 8 give me my brief", "reminders"),
    ("remind me every day at 8pm to take my medicine", "reminders"),
    ("check my email", "email"), ("send an email to ali@example.com about the meeting", "email"),
    ("add a meeting with Ali tomorrow at 3pm", "calendar"), ("what is on my calendar this week", "calendar"),
    ("make 10 flashcards on photosynthesis", "planners"), ("plan a trip to swat for 3 days", "planners"),
    ("install the skill from https://github.com/someone/ishti-skill", "skillsmith"), ("create a skill that tells a random quote", "skillsmith"),
    ("tell me when the ps5 slim gets cheaper", "shopping"),
    ("git status of the ishti project", "gitops"), ("run the tests", "dev"), ("lint the ishti project", "dev"),
    ("download https://example.com/report.pdf", "webtools"), ("watch https://example.com/jobs for changes", "webtools"),
    ("find duplicate files in downloads", "fileops"), ("rename the photos in downloads to trip", "fileops"),
    ("empty the recycle bin", "fileops"),
    ("minimize the notepad window", "desktop"), ("set volume to 30", "desktop"), ("install gimp", "desktop"),
    ("lock my pc", "desktop"), ("what is using the most memory", "desktop"),
    ("i study in imsciences", "owner"), ("where i stid ?", "owner"), ("what semester am i in?", "owner"),
    ("why your response is late", "owner"), ("check my whatsap and let me know which chat is unread", "whatsapp"),
    # 2026-09-29 (Section 4): the 24-hour chat digest - read-only; "check my whatsapp" stays with whatsapp
    ("check the last 24 hours of all my chats and give me a report", "chat_digest"), ("chat digest please", "chat_digest"),
    ("open agent and check the last 24 hours of all chats, groups and communities", "chat_digest"),
    ("make a pdf of the digest", "chat_digest"),
    # batch C (2026-09-28): vocab, recipe, subscriptions, networth, sleeplog, moodlog, stats
    ("add word serendipity: finding something good by accident", "vocab"),
    ("quiz my vocab", "vocab"), ("review my weakest words", "vocab"), ("vocab stats", "vocab"),
    ("recipe from eggs, rice and tomatoes", "recipe"),
    ("give me a recipe for biryani", "recipe"), ("grocery list for karahi", "recipe"),
    ("track subscription netflix 1500 monthly on the 5th", "subscriptions"),
    ("my subscriptions", "subscriptions"), ("upcoming renewals", "subscriptions"),
    ("cancel subscription spotify", "subscriptions"),
    ("track asset bank account 250000", "networth"), ("track liability bike loan 180000", "networth"),
    ("my net worth", "networth"), ("list my assets and liabilities", "networth"),
    ("log sleep 7.5 hours", "sleeplog"), ("my sleep this week", "sleeplog"), ("my sleep debt", "sleeplog"),
    ("rate my mood today 4", "moodlog"), ("mood 2: stressful day", "moodlog"),
    ("my mood this week", "moodlog"), ("mood trend", "moodlog"),
    ("stats D:/data/sales.csv", "stats"), ("correlations in D:/data/sales.csv", "stats"),
    ("find anomalies in D:/data/sales.csv", "stats"), ("cluster D:/data/sales.csv into 3 groups", "stats"),
    # batch D (2026-09-28): redact, devops, gitops, sendlater, mailmerge, pdftables
    ("redact the text: my email is a.b@x.com and card is 4111 1111 1111 1111", "redact"),
    ("redact D:/docs/contract.pdf", "redact"), ("redact show D:/notes.txt", "redact"),
    ("create a github actions workflow for a python app", "devops"),
    ("dockerfile for python", "devops"), ("kubernetes deployment for orders", "devops"),
    ("terraform for aws s3", "devops"), ("gitlab ci for node", "devops"),
    ("git log of D:/proj", "gitops"), ("show branches of D:/proj", "gitops"),
    ("which branch am I on in D:/proj", "gitops"),
    ("commit my changes in D:/proj with message fix readme", "gitops"),
    ("push D:/proj", "gitops"), ("stash my changes", "gitops"),
    ("force push to origin", "gitops"),          # gitops owns the refusal, never the LLM
    ("send an email to ali@corp.com tomorrow at 9 saying report is ready", "sendlater"),
    ("email team@x.org tomorrow saying the build broke", "sendlater"),
    ("list my scheduled emails", "sendlater"), ("cancel the scheduled email to ali@corp.com", "sendlater"),
    ("mailmerge D:/contacts.csv: Dear {name}, invoice {invoice} total {total}.", "mailmerge"),
    ("mailmerge the csv D:/contacts.csv with template 'Hi {name}!'", "mailmerge"),
    ("show my mailmerge drafts", "mailmerge"),
    ("extract tables from D:/docs/invoice.pdf", "pdftables"),
    ("pdf tables to csv D:/data/tables.pdf", "pdftables"),
    # batch E (2026-09-28): forecast, chart, palette, imgquality, abtest, prioritize
    ("forecast D:/data/sales.csv next 6 months", "forecast"),
    ("predict next 7 days from D:/visitors.csv", "forecast"),
    ("trend of D:/data/sales.csv", "forecast"), ("moving average of D:/x.csv over 4", "forecast"),
    ("seasonality in D:/x.csv", "forecast"),
    ("bar chart of D:/data/sales.csv by region with amount", "chart"),
    ("line chart of D:/visitors.csv", "chart"), ("histogram of D:/data/ages.csv", "chart"),
    ("chart the csv D:/x.csv", "chart"), ("show charts", "chart"),
    ("color palette of D:/pics/logo.png", "palette"), ("dominant colors in D:/shots/shot.png", "palette"),
    ("palette of D:/pics/banner.jpg top 4", "palette"),
    ("image quality of D:/pics/shot.jpg", "imgquality"), ("how sharp is D:/photo.png", "imgquality"),
    ("is D:/x.jpg blurry?", "imgquality"), ("resolution of D:/pic.png", "imgquality"),
    ("ab test of 120 out of 1000 vs 95 out of 1020", "abtest"),
    ("is my a/b test significant: A 120/1000, B 95/1020", "abtest"),
    ("analyze the split test 120 of 1000 versus 95 of 1020", "abtest"),
    ("add tasks: submit thesis today, reply to Ali, watch a series", "prioritize"),
    ("prioritize my tasks", "prioritize"), ("what should I do first?", "prioritize"),
    ("my tasks so far", "prioritize"),
    # batch F (2026-09-28): dataprep, vulnscan, optimize, timeline, formula, resumeparse
    ("clean the csv D:/data/messy.csv", "dataprep"), ("data quality of D:/x.csv", "dataprep"),
    ("missing values in D:/x.csv", "dataprep"), ("duplicates in D:/x.csv", "dataprep"),
    ("vulnerability scan of D:/proj", "vulnscan"),
    ("scan my python code for security issues in D:/app", "vulnscan"),
    ("is D:/scripts/tool.py safe", "vulnscan"),
    ("optimize 30A + 20B with A costs 200 max 10, B costs 450 max 6, budget 5000", "optimize"),
    ("maximize 5x + 3y subject to x <= 4, y <= 6", "optimize"),
    ("minimize x**2 - 4x at x near -10 to 10", "optimize"),
    ("timeline from these events: 12 Mar 2024 kickoff, 2024-05-01 beta", "timeline"),
    ("build a timeline of: born 1998 in Peshawar; school 2004; first job 2022", "timeline"),
    ("excel formula for the average of A1:A10 ignoring blanks", "formula"),
    ("what does =SUMIF do", "formula"),
    ("formula to count unique values in D1:D50", "formula"),
    ("parse my resume D:/cv.pdf", "resumeparse"), ("resume to json D:/docs/cv.pdf", "resumeparse"),
    # batch G (2026-09-28): metrics, symptomlog, goaltrack, standup, meetingcost, gantt
    ("log weight 82", "metrics"), ("log blood pressure today 130 over 85", "metrics"),
    ("my weight this month", "metrics"), ("sugar trend", "metrics"), ("metrics summary", "metrics"),
    ("log headache severity 7", "symptomlog"), ("my symptoms this week", "symptomlog"),
    ("headache history", "symptomlog"), ("doctor summary", "symptomlog"),
    ("new goal save 100000 for laptop", "goaltrack"), ("add 5000 to the laptop goal", "goaltrack"),
    ("progress on the laptop goal", "goaltrack"),
    ("set standup yesterday: finished the parser", "standup"), ("give me my standup", "standup"),
    ("standup blockers: waiting on docs", "standup"),
    ("cost of a 60 minute meeting with 8 people at 2500 per hour", "meetingcost"),
    ("yearly cost of a weekly 30 minute meeting with 6 people at 2000 per hour", "meetingcost"),
    ("gantt chart of: design 3d, build 5d after design, test 2d after build", "gantt"),
    # batch H (2026-09-29): imgcrop, meme, audioquality, kanban, okr, riskregister
    ("crop D:/pics/me.jpg to square", "imgcrop"),
    ("center crop D:/pics/banner.png to 16:9", "imgcrop"),
    ("trim the borders of D:/shots/scan.png", "imgcrop"),
    ("meme D:/cat.png top: when the build passes", "meme"),
    ("caption D:/x.png with ship it", "meme"),
    ("make a meme: friday deploys | nothing breaks", "meme"),
    ("audio quality of D:/calls/voice-note.mp3", "audioquality"),
    ("what are the specs of D:/rec/talk.wav", "audioquality"),
    ("kanban add write the report", "kanban"),
    ("move write the report to doing", "kanban"),
    ("my kanban board", "kanban"),
    ("clear kanban done", "kanban"),
    ("set objective grow the channel", "okr"),
    ("add key result 1000 subscribers to grow the channel", "okr"),
    ("update kr 1 of grow the channel to 400", "okr"),
    ("my okrs", "okr"),
    ("log risk data loss likelihood 3 impact 5", "riskregister"),
    ("top risks", "riskregister"),
    ("my risk register", "riskregister"),
    ("mitigate data loss with nightly backups", "riskregister"),
    ("close risk data loss", "riskregister"),
    # batch I (2026-09-29): scanqr, audioclean, chapters, sudoku, bayes, montecarlo
    ("qr code for https://ishti.pk/link", "scanqr"),
    ("generate a qr code for wifi: net/pass", "scanqr"),
    ("scan the qr in D:/shots/ticket.png", "scanqr"),
    ("read the qr D:/imgs/code.png", "scanqr"),
    ("clean up the audio in D:/rec/interview.wav", "audioclean"),
    ("denoise D:/calls/noisy.mp3", "audioclean"),
    ("normalize the loudness of D:/pod/ep1.wav", "audioclean"),
    ("chapters for D:/vids/talk.mp4", "chapters"),
    ("detect scenes in D:/vids/clip.mkv", "chapters"),
    ("solve this sudoku: 530070000600195000098000060800060003400803001700020006060000280000419005000080079", "sudoku"),
    ("bayes: prior 1% sensitivity 99% specificity 95%", "bayes"),
    ("posterior with prior 0.02, sensitivity 0.99, specificity 0.95", "bayes"),
    ("update belief: hit rate 90%, false positive 10%", "bayes"),
    ("monte carlo: portfolio 1000000, yearly return 8% +/- 15%, 20 years", "montecarlo"),
    ("simulate 10000 rolls of 2 dice", "montecarlo"),
    ("monte carlo project finish: 5 tasks, each 5 days +/- 2", "montecarlo"),
    # batch J (2026-09-29): legaldoc, pressrel, doclayout
    ("draft a rental agreement: tenant Ahmed, shop 12, rent 25000 per month, from 2026-10-01 for 11 months", "legaldoc"),
    ("draft an nda between me and Acme Corp for 2 years", "legaldoc"),
    ("draft an employment contract: Ali, 80000 per month, probation 3 months", "legaldoc"),
    ("draft a press release: ISHTI 2.0 launches 2026-10-05 with offline voice", "pressrel"),
    ("write a product description for the walnut-wood clock, 4500 pkr, handmade in chitral", "pressrel"),
    ("generate an faq from: returns accepted within 14 days; warranty 1 year", "pressrel"),
    ("layout of D:/docs/report.pdf", "doclayout"),
    ("analyze the pdf D:/docs/invoice.pdf", "doclayout"),
    # batch K (2026-09-29): pantry, audiomark, highlights, chromakey, infographic
    ("add milk to the pantry, expires 2026-10-05", "pantry"),
    ("add 2 bread to the pantry", "pantry"),
    ("what's expiring soon", "pantry"),
    ("pantry list", "pantry"),
    ("used up the milk", "pantry"),
    ("bookmark 12:34 in D:/talk.mp3: key claim", "audiomark"),
    ("my bookmarks for D:/talk.mp3", "audiomark"),
    ("highlights of D:/stream.mp4", "highlights"),
    ("find the best moments in D:/vlog.mp4", "highlights"),
    ("remove the green screen from D:/take1.mp4", "chromakey"),
    ("key out the background of D:/x.jpg", "chromakey"),
    ("infographic of D:/data/sales.csv", "infographic"),
    ("make an infographic from D:/visitors.csv by region with amount", "infographic"),
    # batch L (2026-09-29): gametheory, sensitivity, argumentcheck
    ("analyze the game: TL 3,2; TR 1,1; BL 0,3; BR 2,0", "gametheory"),
    ("game theory: TL -1,-1; TR -3,0; BL 0,-3; BR -2,-2", "gametheory"),
    ("sensitivity analysis of f(x) = 30*x - 1000, x from 10 to 50 step 5", "sensitivity"),
    ("check my argument: my opponent is an idiot so his plan fails", "argumentcheck"),
    ("fallacy check: either we ban cars or the planet dies", "argumentcheck"),
]


def main():
    bad = [n for n, s in d.SKILLS.items() if s.status == "broken"]
    assert not bad, f"broken skills: {[(n, d.SKILLS[n].problem) for n in bad]}"
    # E conflict resolution: a near-tie between the top two goes to the skill this chat used last.
    # NOTE: this exercises the tiebreak directly with a synthetic "laya"-sourced decision. The live
    # router is ishti/tool_routing.py, which never emits source "laya", so the branch this asserts
    # cannot currently fire in production - see docs/OWNER_LOOP.md. Kept because it pins the
    # function's contract, not because a live request reaches it.
    tie = {"skill": "web_search", "source": "laya", "scores": [("web_search", 0.51), ("weather", 0.49), ("chat", 0.2)]}
    assert d.recency_tiebreak(tie, "weather")["skill"] == "weather" and d.recency_tiebreak(tie, "chat")["skill"] == "web_search"
    assert d.recency_tiebreak({**tie, "scores": [("web_search", 0.7), ("weather", 0.4)]}, "weather")["skill"] == "web_search"
    # C3: only the sources the answer actually cites are listed
    src = [{"id": i, "url": f"https://s{i}.com"} for i in range(1, 6)]
    assert [x["id"] for x in d.main_sources("Rs 314,990 at PriceOye [2]; Daraz lists it [4].", src)] == [2, 4]
    assert len(d.main_sources("No citations here.", src)) == 5 and len(d.main_sources("A fact [9].", src)) == 5
    # parallel requests of one chat: own trace/stream, shared history and pending question
    shared = {"history": [], "pending": None}
    a, b = d.View(shared, trace="A"), d.View(shared, trace="B", on_token=print)
    a["pending"] = {"skill": "video"}
    assert b["pending"] == shared["pending"] and b.get("trace") == "B" and a.get("on_token") is None

    # Routing eval. Deterministic and offline: ishti/tool_routing.py makes no model call, so there is no
    # checkpoint to warm up, no "ms" on the decision and no router error to assert against. It runs
    # unconditionally now, because there is nothing left for --no-laya to skip.
    hits, times = 0, []
    for text, want in LABELLED:
        t0 = time.perf_counter()
        got = router.route(text, d.SKILLS)
        times.append((time.perf_counter() - t0) * 1000)
        hits += got["skill"] == want
        if got["skill"] != want:
            print(f"  route miss: {text!r} -> {got['skill']} ({got['source']}), want {want}")
    acc = hits / len(LABELLED)
    print(f"routing: {hits}/{len(LABELLED)} correct, median {sorted(times)[len(times) // 2]:.2f} ms")
    assert acc >= 0.85, f"routing accuracy {acc:.0%} < 85%"

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the checkout, wherever it lives
    drive = root.split(":")[0] + ":"
    sid = "check_" + os.urandom(3).hex()
    loop = [
        (f"Project ISHTI is on {drive} at {root}", "memory", "Saved"),
        ("where is the ishti project?", "memory", root),
        ("ISHTI is now working", "memory", "is working"),
        ("what's the weather tomorrow?", "weather", "open-meteo"),
        ("what is 17 times 23? reply with just the number", "chat", "391"),
        # STALE AFTER THE ONE-ARCHITECTURE REWRITE: nothing routes a compound request to a skill named
        # "plan" any more - the skill in the tree is "planners", and "plan" survives only as a display
        # branch. Left as-is on purpose rather than guessed at, because the value it should assert can
        # only be read off a live run. See docs/OWNER_LOOP.md; expect this assertion to be the next
        # thing that fails here.
        ("how much ram am I using and then tell me the weather tomorrow", "plan", "open-meteo"),
    ]
    try:
        for text, skill, expect in loop:
            r = d.handle(text, sid)
            print(f"  {text!r} -> {r['skill']} [{r['route']['source']}, {r['latency_ms']} ms]: {r['final_answer'][:90]!r}")
            assert r["skill"] == skill and expect in r["final_answer"], r
    finally:
        d.get_conversation_manager().delete_session(sid)
    check_remote_auth()
    check_situation()
    print("[check] core loop OK - routing, memory, live skill, local LLM, remote auth, situation layer")


def check_situation():
    """Section 0: the universal T2 gate (yes runs, no cancels), the repeat guard, the T3 block."""
    from types import SimpleNamespace
    from ishti.skills import ask_first
    sent, sid = [], "check_sit_" + os.urandom(3).hex()
    t2 = SimpleNamespace(name="zap_send", manifest={"tier": "T2", "priority": 0, "route": "x"}, status="working", problem="",
                         matches=lambda t: t.startswith("zap send"),
                         run=lambda t, s: ask_first(s, "zap_send", f"Send '{t}' to Ali", lambda: sent.append(t) or "Sent."))
    t3 = SimpleNamespace(name="zap_unlock", manifest={"tier": "T3", "priority": 0, "route": "x"}, status="working", problem="",
                         matches=lambda t: t.startswith("zap unlock"), run=lambda t, s: {"reply": "UNLOCKED"})
    d.SKILLS.update(zap_send=t2, zap_unlock=t3)
    try:
        assert d.handle("zap send hello", sid)["final_answer"].endswith("(yes/no)") and not sent
        assert "Cancelled" in d.handle("no", sid)["final_answer"] and not sent
        d.handle("zap send hello", sid)
        assert d.handle("yes", sid)["final_answer"] == "Sent." and sent == ["zap send hello"]
        assert "a moment ago" in d.handle("zap send hello", sid)["final_answer"]  # double trigger
        assert "blocked" in d.handle("zap unlock the door", sid)["final_answer"]
    finally:
        for n in ("zap_send", "zap_unlock"):
            d.SKILLS.pop(n, None)
        d.get_conversation_manager().delete_session(sid)


def check_remote_auth():
    """Tunnel traffic reaches the server from 127.0.0.1, so it must still need the token."""
    from fastapi.testclient import TestClient
    from interface.web_ui import server
    c = TestClient(server.app)  # client host "testclient" = remote
    assert c.get("/api/skills").status_code == 401
    assert c.get("/api/skills", headers={"Authorization": f"Bearer {server.API_TOKEN}"}).status_code == 200
    proxied = type("Conn", (), {"client": type("C", (), {"host": "127.0.0.1"})(), "cookies": {}, "query_params": {},
                                "headers": {"cf-connecting-ip": "203.0.113.9"}})()
    assert not server.authorized(proxied)


if __name__ == "__main__":
    from orchestrator.memory.user_profile import scratch_memory
    with scratch_memory():  # chat sessions, profile and research archives go to a temp dir, even if the run is killed
        main()
