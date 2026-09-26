"""Weather skill: wraps tools/web/weather.py (Open-Meteo, verified payload)."""
import re

from ishti.facts import get_facts
from tools.web.weather import extract_city, get_weather, which_day

# "rain"/"snow" alone are left to Laya: they also show up in poems and stories.
TRIGGER = re.compile(r"\b(weather|forecast|temperature|how\s+(?:hot|cold|warm))\b|"
                     r"\b(?:will|is|going\s+to|chance\s+of)\b.*\b(?:rain|snow|storm)", re.I)
CREATIVE = re.compile(r"^\s*(?:please\s+)?(?:write|compose|draft|create|make|generate)\b", re.I)


def match(text):
    return bool(TRIGGER.search(text)) and not CREATIVE.match(text)


def run(text, session):
    city = extract_city(text) or get_facts().get("owner").get("city")
    if not city:
        return {"reply": "Which city? (Tell me 'I live in <city>' once and I'll remember it.)", "sources": []}
    w = get_weather(city, which_day(text))
    if not w["ok"]:
        return {"reply": f"Couldn't get the weather for {city}: {w['error']}", "sources": []}
    return {"reply": w["answer"], "sources": [{"id": 1, "title": "Open-Meteo forecast", "url": w["source"],
                                               "domain": "open-meteo.com", "snippet": w["answer"]}]}
