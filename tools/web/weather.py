"""
ISHTI Weather - real data, no scraping, no LLM prose.

Open-Meteo: free, keyless, JSON. Replaces the old path (send "weather tomorrow" to Bing,
get an IP-geolocated Karachi page, ask a 3B model to summarise it). Answer is one sentence
and always names the city it actually used.
"""

import re
from datetime import date, timedelta
from typing import Dict, Any, Optional

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes -> plain English (https://open-meteo.com/en/docs)
WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
    67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
    96: "thunderstorms with hail", 99: "thunderstorms with hail",
}

# ponytail: regex city extraction, not an NER model. Ceiling: misses unusual phrasings,
# in which case we fall back to the owner's profile city (which is the desired default anyway).
_CITY_RE = re.compile(r"\b(?:in|at|for)\s+([A-Z][a-zA-Z\-']+(?:\s+[A-Z][a-zA-Z\-']+)?)")


def extract_city(prompt: str) -> Optional[str]:
    """Pull an explicit city out of the prompt, or None to use the owner's default."""
    m = _CITY_RE.search(prompt)
    if not m:
        return None
    candidate = m.group(1).strip()
    if candidate.lower() in {"the", "my", "today", "tomorrow", "now", "here"}:
        return None
    return candidate


def which_day(prompt: str) -> int:
    """0 = today, 1 = tomorrow. Anything else is out of scope and treated as today."""
    p = prompt.lower()
    if "tomorrow" in p:
        return 1
    if "day after tomorrow" in p:
        return 2
    return 0


def geocode(city: str, timeout: int = 8) -> Optional[Dict[str, Any]]:
    r = requests.get(GEOCODE_URL, params={"name": city, "count": 1, "language": "en"}, timeout=timeout)
    r.raise_for_status()
    hits = r.json().get("results") or []
    return hits[0] if hits else None


def get_weather(city: str, day_offset: int = 0, timeout: int = 10) -> Dict[str, Any]:
    """
    Returns {ok, city, country, date, temp_max, temp_min, condition, source, answer, error}.
    ok is True only when the payload really contains a temperature for the requested city.
    """
    out: Dict[str, Any] = {"ok": False, "city": city, "error": ""}
    try:
        place = geocode(city, timeout=timeout)
        if not place:
            out["error"] = f"No such place: {city}"
            return out

        day_offset = max(0, min(day_offset, 6))
        r = requests.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "timezone": "auto",
                "forecast_days": day_offset + 1,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        daily = r.json().get("daily") or {}

        # Verification, not assumption: the payload must actually carry the day we asked for.
        if len(daily.get("temperature_2m_max", [])) <= day_offset:
            out["error"] = "Forecast payload did not include the requested day"
            return out

        tmax = daily["temperature_2m_max"][day_offset]
        tmin = daily["temperature_2m_min"][day_offset]
        code = daily["weather_code"][day_offset]
        when = {0: "Today", 1: "Tomorrow", 2: "The day after tomorrow"}.get(day_offset, "Soon")
        condition = WMO.get(code, "mixed conditions")

        out.update({
            "ok": tmax is not None,
            "city": place["name"],
            "country": place.get("country", ""),
            "date": daily["time"][day_offset],
            "temp_max": tmax,
            "temp_min": tmin,
            "condition": condition,
            "source": "https://open-meteo.com",
            "answer": f"{when} in {place['name']}: {round(tmax)}°C high, {round(tmin)}°C low, {condition}. (open-meteo.com)",
        })
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out


if __name__ == "__main__":
    assert which_day("what's the weather tomorrow") == 1
    assert which_day("weather today") == 0
    assert extract_city("weather in London tomorrow") == "London"
    assert extract_city("what's the weather tomorrow") is None, "must fall through to owner's city"

    res = get_weather("Peshawar", 1)
    assert res["ok"], res
    assert res["city"].lower().startswith("peshawar"), res
    assert isinstance(res["temp_max"], (int, float)), res
    print(res["answer"])
    print("[self-check] weather OK - city extraction, day offset, live forecast verified")
