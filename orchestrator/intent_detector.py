"""
ISHTI Intent & Depth Detector

Decides two things deterministically, before the LLM is ever consulted:
  mode  - which tool must run (weather / search / research / fetch / browser / local)
  depth - how long the answer may be (SIMPLE / MODERATE / DEEP)

Depth is decided here, not by the model, because the model's instinct is to write a report
for everything (docs/AUDIT.md F7).
"""

import re
from typing import Dict, Any

# Real-time triggers: answering these from model weights is always wrong.
TEMPORAL_TRIGGERS = [
    "today", "now", "currently", "latest", "recent", "this week", "this month",
    "this year", "current price", "current version", "current news", "newest",
    "recently released", "what happened", "what changed", "who is currently",
    "breaking news", "stock price", "crypto price", "weather", "score", "standings",
    "tomorrow", "price of", "version of", "news about", "status of",
    # "live" on its own is not a live-data cue - "I live in Peshawar" is a statement about
    # the owner, not a request for real-time data. Only the unambiguous phrases qualify.
    "live score", "live price", "live status", "live updates", "going live",
]

# DEEP is only ever entered when the owner explicitly asks for research or a report.
RESEARCH_TRIGGERS = [
    "research", "investigate", "deep dive", "in-depth", "comprehensive analysis",
    "write a report", "full report", "detailed report", "state of the art",
    "market analysis", "literature review", "compare in detail",
]

WEATHER_TRIGGERS = ["weather", "temperature", "forecast", "how hot", "how cold", "will it rain"]

GREETINGS = [
    "hi", "hey", "hello", "yo", "salam", "assalam", "how are you", "how's it going",
    "good morning", "good evening", "good afternoon", "what's up", "whats up", "sup",
    "thanks", "thank you", "ok", "okay", "cool", "nice",
]

BROWSER_VISIBLE_TRIGGERS = [
    "show me the browser", "open the browser", "visible browser", "non-headless",
    "non headless", "let me watch", "use visible browser", "show browser", "open browser",
]


def _hits(text: str, phrases) -> list:
    """
    Substring matching turns "I live in Peshawar" into a live-data query and "scorecard"
    into a sports question. Multi-word phrases stay substrings; single words need boundaries.
    """
    found = []
    for phrase in phrases:
        if " " in phrase:
            if phrase in text:
                found.append(phrase)
        elif re.search(r"\b" + re.escape(phrase) + r"\b", text):
            found.append(phrase)
    return found


class IntentDetector:
    @staticmethod
    def classify(prompt: str) -> Dict[str, Any]:
        p = (prompt or "").lower().strip()
        words = p.split()
        url_match = re.search(r"https?://[^\s]+", prompt or "")

        def result(mode, depth, needs_internet, **extra):
            out = {"mode": mode, "depth": depth, "needs_internet": needs_internet,
                   "triggers": _hits(p, TEMPORAL_TRIGGERS)}
            out.update(extra)
            return out

        # 1. Greeting / small talk -> no tools, one sentence.
        stripped = p.strip(" ?!.,")
        if stripped in GREETINGS or (len(words) <= 5 and any(
                stripped.startswith(g) for g in GREETINGS) and not _hits(p, TEMPORAL_TRIGGERS)):
            return result("GREETING", "SIMPLE", False, reason="Casual greeting or small talk.")

        # 2. Explicit URL -> check if it's form filling or read fetch
        if url_match:
            if any(w in p for w in ["fill form", "fill out", "submit form", "fill the form", "login", "log in", "sign in"]):
                return result("BROWSER_FORM", "MODERATE", True, url=url_match.group(0),
                              reason="Interactive browser form filling on URL.")
            return result("WEB_FETCH", "MODERATE", True, url=url_match.group(0),
                          reason="Direct URL in request.")

        # 3. Weather -> real weather API, never a search page.
        if _hits(p, WEATHER_TRIGGERS):
            return result("WEATHER", "SIMPLE", True, reason="Live weather data required.")

        # 3b. Video Editing Intent -> FFmpeg editing engine
        video_cues = [
            "trim video", "cut video", "edit video", "merge video", "concatenate video",
            "extract audio from video", "convert to vertical", "convert to 9:16", "make a short",
            "make a reel", "speed up video", "slow down video", "compress video", "add subtitles to video",
            "video editor", "crop video"
        ]
        if any(c in p for c in video_cues) or ("video" in p and any(w in p for w in ["trim", "cut", "merge", "audio", "vertical", "short", "compress", "speed"])):
            return result("VIDEO_EDIT", "MODERATE", False, reason="Video editing operation requested.")

        # 3c. PC File & Folder Search -> ComputerUseAgent PC search
        pc_search_cues = [
            "search on pc", "search my pc", "search computer", "find on my pc", "find on pc",
            "find file", "search for file", "search file", "locate file", "where is my file",
            "find my download", "search my documents", "search my desktop", "find my video"
        ]
        if any(c in p for c in pc_search_cues) or (("search" in p or "find" in p or "locate" in p) and any(w in p for w in ["on pc", "in pc", "on my computer", "on drive", "in downloads", "in documents", "in desktop"])):
            return result("PC_SEARCH", "SIMPLE", False, reason="PC file search requested.")

        # 3d. System Telemetry & Hardware Status -> ComputerUseAgent telemetry
        telemetry_cues = [
            "system telemetry", "hardware status", "cpu usage", "ram usage", "how much ram",
            "gpu status", "disk space", "storage space", "battery status", "system stats",
            "take screenshot of pc", "desktop screenshot"
        ]
        if any(c in p for c in telemetry_cues):
            return result("PC_TELEMETRY", "SIMPLE", False, reason="PC telemetry/status requested.")

        # 3e. Desktop Application Launch / Control
        app_launch_cues = [
            "open notepad", "launch notepad", "open calculator", "launch calculator",
            "open vs code", "open vscode", "launch code", "open explorer", "open task manager",
            "open settings", "open vlc", "launch app", "open app", "close window", "focus window"
        ]
        if any(c in p for c in app_launch_cues) or (any(p.startswith(w) for w in ["open ", "launch ", "start "]) and any(w in p for w in ["app", "calculator", "notepad", "code", "vscode", "explorer", "vlc", "premiere", "capcut", "blender"])):
            return result("APP_CONTROL", "SIMPLE", False, reason="Desktop application launch requested.")

        # 4. Explicitly requested research/report -> DEEP.
        if _hits(p, RESEARCH_TRIGGERS):
            return result("DEEP_RESEARCH", "DEEP", True,
                          reason="Owner explicitly asked for research or a report.")

        # 5. Visible-browser request -> browser search the owner can watch.
        if _hits(p, BROWSER_VISIBLE_TRIGGERS):
            return result("BROWSER_VISIBLE", "SIMPLE", True,
                          reason="Owner asked to see the browser.")

        # 6. Interactive browser automation / Form filling
        if any(w in p for w in ["fill form", "fill out form", "submit form", "fill the form", "log into", "click on button", "login to"]):
            return result("BROWSER_FORM", "MODERATE", True, reason="Interactive browser form filling.")

        # 7. Live factual lookup -> search, short answer.
        if _hits(p, TEMPORAL_TRIGGERS) or any(
                p.startswith(w) for w in ["search for", "search the", "search on", "look up",
                                          "google", "find info on", "find out"]):
            return result("QUICK_SEARCH", "SIMPLE", True, reason="Live factual query.")

        # 8. Anything else -> local reasoning. An explanation earns a few more sentences;
        #    a short question does not.
        explain_cues = ("explain", "how do", "how does", "how to", "why ", "walk me through",
                        "summarize", "compare", "write ", "draft ", "difference between")
        depth = "MODERATE" if (any(c in p for c in explain_cues) or len(words) > 12) else "SIMPLE"
        return result("LOCAL_KNOWLEDGE", depth, False, reason="No live data required.")


if __name__ == "__main__":
    cases = [
        ("hey how are you", "GREETING", "SIMPLE"),
        ("hey Siri how are you", "GREETING", "SIMPLE"),
        ("what's the weather tomorrow?", "WEATHER", "SIMPLE"),
        ("Search the internet for the latest version of Ollama.", "QUICK_SEARCH", "SIMPLE"),
        ("Research the latest AI news this week.", "DEEP_RESEARCH", "DEEP"),
        ("Open the browser and search for RTX 5070 Ti drivers", "BROWSER_VISIBLE", "SIMPLE"),
        ("Check https://python.org and summarize", "WEB_FETCH", "MODERATE"),
        ("explain how a hash map works", "LOCAL_KNOWLEDGE", "MODERATE"),
        # "live" and "now" as parts of ordinary sentences must not trigger a live lookup
        ("hey bro I live in Peshawar Pakistan", "LOCAL_KNOWLEDGE", "SIMPLE"),
        ("what is the current weather", "WEATHER", "SIMPLE"),
    ]
    for prompt, want_mode, want_depth in cases:
        got = IntentDetector.classify(prompt)
        assert got["mode"] == want_mode, f"{prompt!r} -> {got['mode']}, wanted {want_mode}"
        assert got["depth"] == want_depth, f"{prompt!r} -> {got['depth']}, wanted {want_depth}"
        print(f"  {prompt[:48]:50s} -> {got['mode']:16s} {got['depth']}")
    print("[self-check] intent_detector OK - mode and depth correct on all cases")
