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
    ("latest news about nvidia", "web_search"), ("what's the price of gold today", "web_search"),
    ("who is the current prime minister of the UK", "web_search"), ("open the task manager", "legacy"),
    ("find the invoice pdf on my computer", "legacy"), ("how much ram am I using", "legacy"),
    ("launch vs code", "legacy"), ("mujhe batao mera project kahan hai", "memory"),
    ("which folder did I keep the thesis in", "memory"), ("where did I save my resume", "memory"),
    ("will it rain tomorrow", "weather"), ("is it cold outside", "weather"),
    ("what's the forecast for the weekend", "weather"),
]


def main():
    bad = [n for n, s in d.SKILLS.items() if s.status == "broken"]
    assert not bad, f"broken skills: {[(n, d.SKILLS[n].problem) for n in bad]}"

    if CONFIG["router"]["use_laya"]:
        hits, slow = 0, 0
        for text, want in LABELLED:
            got = router.route(text, d.SKILLS)
            hits += got["skill"] == want
            slow = max(slow, got["ms"])
            if got["skill"] != want:
                print(f"  route miss: {text!r} -> {got['skill']} ({got['source']}), want {want}")
        acc = hits / len(LABELLED)
        print(f"routing: {hits}/{len(LABELLED)} correct, slowest {slow} ms")
        assert acc >= 0.85, f"routing accuracy {acc:.0%} < 85%"
        assert not router.laya_error, router.laya_error

    sid = "check_" + os.urandom(3).hex()
    loop = [
        (r"Project ISHTI is on D: at D:\ISHTI", "memory", "Saved"),
        ("where is the ishti project?", "memory", r"D:\ISHTI"),
        ("ISHTI is now working", "memory", "is working"),
        ("what's the weather tomorrow?", "weather", "open-meteo"),
        ("what is 17 times 23? reply with just the number", "chat", "391"),
    ]
    try:
        for text, skill, expect in loop:
            r = d.handle(text, sid)
            print(f"  {text!r} -> {r['skill']} [{r['route']['source']}, {r['latency_ms']} ms]: {r['final_answer'][:90]!r}")
            assert r["skill"] == skill and expect in r["final_answer"], r
    finally:
        d.get_conversation_manager().delete_session(sid)
    print("[check] core loop OK - routing, memory, live skill, local LLM")


if __name__ == "__main__":
    main()
