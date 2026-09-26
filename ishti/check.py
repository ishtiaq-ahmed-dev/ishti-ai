"""End-to-end check of the core loop against real Ollama/Laya, with a throwaway memory DB.
    python -m ishti.check            (add --no-laya to skip the router eval)"""
import os
import sys
import tempfile

from ishti import CONFIG

tmp = tempfile.mkdtemp()
CONFIG["memory"].update(db_path=os.path.join(tmp, "t.db"), chroma_dir=os.path.join(tmp, "chroma"))
if "--no-laya" in sys.argv:
    CONFIG["router"]["use_laya"] = False

from ishti import dispatcher as d, router  # noqa: E402  (config must be patched first)

# Labelled routing set. Grows into the fine-tuning set for the Laya typed-decisions checkpoint.
LABELLED = [
    ("tell me a joke", "chat"), ("explain quantum entanglement simply", "chat"),
    ("write a python function to reverse a linked list", "chat"), ("how do I fix a segfault in C", "chat"),
    ("draft an email to my professor asking for an extension", "chat"), ("good morning!", "chat"),
    ("thanks, that helped", "chat"), ("who won the champions league final yesterday", "web_search"),
    ("latest news about nvidia", "web_search"), ("what's the price of gold today", "markets"),
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
    ("take a screenshot", "desktop"), ("set volume to 30", "desktop"), ("install gimp", "desktop"),
    ("lock my pc", "desktop"), ("what is using the most memory", "desktop"),
    ("i study in imsciences", "owner"), ("where i stid ?", "owner"), ("what semester am i in?", "owner"),
    ("why your response is late", "owner"), ("check my whatsap and let me know which chat is unread", "whatsapp"),
]


def main():
    bad = [n for n, s in d.SKILLS.items() if s.status == "broken"]
    assert not bad, f"broken skills: {[(n, d.SKILLS[n].problem) for n in bad]}"
    # parallel requests of one chat: own trace/stream, shared history and pending question
    shared = {"history": [], "pending": None}
    a, b = d.View(shared, trace="A"), d.View(shared, trace="B", on_token=print)
    a["pending"] = {"skill": "video"}
    assert b["pending"] == shared["pending"] and b.get("trace") == "B" and a.get("on_token") is None

    if CONFIG["router"]["use_laya"]:
        router.route("warm up", d.SKILLS)  # first call loads the checkpoint
        hits, ms = 0, []
        for text, want in LABELLED:
            got = router.route(text, d.SKILLS)
            hits += got["skill"] == want
            ms.append(got["ms"])
            if got["skill"] != want:
                print(f"  route miss: {text!r} -> {got['skill']} ({got['source']}), want {want}")
        acc = hits / len(LABELLED)
        print(f"routing: {hits}/{len(LABELLED)} correct, median {sorted(ms)[len(ms) // 2]} ms")
        assert acc >= 0.85, f"routing accuracy {acc:.0%} < 85%"
        assert not router.laya_error, router.laya_error

    sid = "check_" + os.urandom(3).hex()
    loop = [
        (r"Project ISHTI is on D: at D:\ISHTI", "memory", "Saved"),
        ("where is the ishti project?", "memory", r"D:\ISHTI"),
        ("ISHTI is now working", "memory", "is working"),
        ("what's the weather tomorrow?", "weather", "open-meteo"),
        ("what is 17 times 23? reply with just the number", "chat", "391"),
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
    main()
