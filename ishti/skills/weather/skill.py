"""Weather skill: wraps tools/web/weather.py (Open-Meteo, verified payload)."""
import re

from ishti.facts import get_facts
from ishti.skills import trace
from tools.web.weather import extract_city, get_weather, which_day

# "rain"/"snow" alone are left to Laya: they also show up in poems and stories.
TRIGGER = re.compile(r"\b(weather|forecast|temperature|how\s+(?:hot|cold|warm))\b|"
                     r"\b(?:will|is|going\s+to|chance\s+of)\b.*\b(?:rain|snow|storm)|"
                     # decision questions that need the forecast: "should I bring a jacket tonight?",
                     # "do I need an umbrella today", "should I wear a coat tomorrow"
                     r"\bshould\s+i\s+(?:bring|take|wear|carry|need)\b.*\b(?:jacket|coat|umbrella|sweater|hoodie|raincoat|cap|scarf|gloves)\b|"
                     r"\b(?:do\s+i|will\s+i)\s+need\s+(?:an?\s+)?(?:umbrella|jacket|coat|sweater)", re.I)
CREATIVE = re.compile(r"^\s*(?:please\s+)?(?:write|compose|draft|create|make|generate)\b", re.I)


def match(text):
    return bool(TRIGGER.search(text)) and not CREATIVE.match(text)


OFF_EARTH = re.compile(r"\b(?:on|in|at|for)\s+(?:the\s+)?(?:mars|moon|venus|mercury|jupiter|saturn|uranus|neptune|pluto|sun|"
                       r"titan|europa|space)\b", re.I)


def run(text, session):
    import difflib
    if (m := OFF_EARTH.search(text)):  # "weather on Mars" used to fall back to the owner's home city
        return {"reply": f"I only have Earth forecasts (Open-Meteo), so I can't tell you the weather {m.group(0).strip()}. "
                         "Ask me about any city on Earth.", "sources": []}
    home = get_facts().get("owner").get("city")
    city = extract_city(text) or home
    if city and home and city.lower() != home.lower() and difflib.SequenceMatcher(None, city.lower(), home.lower()).ratio() >= 0.8:
        city = home  # "Peshwar" (a translation or speech slip) is the owner's own Peshawar
    if not city:
        return {"reply": "Which city? (Tell me 'I live in <city>' once and I'll remember it.)", "sources": []}
    trace(session, "fetch", f"Open-Meteo forecast for {city}", status="start")
    w = get_weather(city, which_day(text))
    trace(session, "fetch", f"forecast for {w.get('city', city)} {w.get('date', '')}", status="done" if w["ok"] else "error")
    if not w["ok"]:
        return {"reply": f"Couldn't get the weather for {city}: {w['error']}", "sources": []}
    return {"reply": w["answer"], "verified": True, "sources": [{"id": 1, "title": "Open-Meteo forecast", "url": w["source"],
                                               "domain": "open-meteo.com", "snippet": w["answer"]}]}


if __name__ == "__main__":
    assert match("weather in London tomorrow")
    assert not match("what is love")
    print("[self-check] weather OK - routing")
