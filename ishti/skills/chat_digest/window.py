"""The rolling 24-hour window (4.3 / N2) - and the boundary rule, stated once so it can be tested.

A message belongs to the digest when  start <= ts <= end,  where end = now and start = now - hours*3600.
Exactly 23:59 old -> inside (it is <= 24h old). Exactly 24:01 -> outside. Both ends inclusive is deliberate:
a message that arrives in the same second the digest starts is the owner's newest one, and losing it would be
the worst kind of silent gap.

Timezone: the system's local timezone by default (configurable in data/chat_digest_config.json ->
{"timezone": "Asia/Karachi"}), which is what the report prints next to the start-end stamps.
"""
import datetime as dt
import json
import os

from ishti import ROOT

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


CONFIG_PATH = os.path.join(ROOT, "data", "chat_digest_config.json")
DEFAULTS = {
    "hours": 24,
    "timezone": "",              # "" = system local timezone
    "max_chats": 250,            # beyond this, chats are listed as skipped with the reason (never silently)
    "include_archived": True,    # 4.3: archived/muted chats must be covered, not skipped
    "owner_names": [],           # extra names that mean "me" in the chats (WhatsApp export writes "You")
    "usefulness": {              # the tunable rubric (4.4)
        "action_window_hours": 72,      # a deadline within this window counts as actionable
        "noise_forward_threshold": 3,   # a message repeated this many times reads as a mass forward
    },
}


def config():
    """Defaults + whatever the owner put in data/chat_digest_config.json (never fails the run)."""
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
            if isinstance(user.get("usefulness"), dict):
                cfg["usefulness"] = {**DEFAULTS["usefulness"], **user["usefulness"]}
    except (OSError, ValueError) as _exc:
        swallowed('ishti/skills/chat_digest/window.py:config', _exc)
    return cfg


def tzinfo(name=""):
    if not name:
        return dt.datetime.now().astimezone().tzinfo
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return dt.datetime.now().astimezone().tzinfo


def window(now=None, hours=None, tz=None):
    """{"start","end","hours","tz","start_local","end_local","start_iso","end_iso"} for the rolling window."""
    cfg = config()
    hours = float(hours or cfg["hours"])
    end = float(now if now is not None else dt.datetime.now().timestamp())
    start = end - hours * 3600.0
    zone = tzinfo(tz if tz is not None else cfg.get("timezone", ""))
    return {
        "start": start,
        "end": end,
        "hours": hours,
        "tz": str(zone),
        "start_local": dt.datetime.fromtimestamp(start, zone).strftime("%Y-%m-%d %H:%M"),
        "end_local": dt.datetime.fromtimestamp(end, zone).strftime("%Y-%m-%d %H:%M"),
        "start_iso": dt.datetime.fromtimestamp(start, zone).isoformat(timespec="seconds"),
        "end_iso": dt.datetime.fromtimestamp(end, zone).isoformat(timespec="seconds"),
        "label": f"{dt.datetime.fromtimestamp(start, zone):%Y-%m-%d %H:%M} → "
                 f"{dt.datetime.fromtimestamp(end, zone):%Y-%m-%d %H:%M} ({zone})",
    }


def in_window(ts, win):
    """The one membership test (inclusive both ends)."""
    try:
        return win["start"] <= float(ts) <= win["end"]
    except (TypeError, ValueError):
        return False


def fmt(ts, win=None):
    zone = tzinfo((win or {}).get("tz", "")) if win else dt.datetime.now().astimezone().tzinfo
    return dt.datetime.fromtimestamp(float(ts), zone).strftime("%Y-%m-%d %H:%M")


def age_hours(ts, win):
    return (win["end"] - float(ts)) / 3600.0


if __name__ == "__main__":
    w = window(now=1_000_000.0)
    assert w["start"] == 1_000_000.0 - 86400 and w["end"] == 1_000_000.0
    assert in_window(w["start"], w) and in_window(w["end"], w)          # inclusive both ends
    assert in_window(w["end"] - 59, w)                                  # 23:59 old -> in
    assert not in_window(w["end"] - 86460, w)                           # 24:01 old -> out
    assert not in_window(w["start"] - 1, w) and "→" in w["label"]
    assert abs(age_hours(w["end"] - 59, w) - (59 / 3600)) < 1e-9
    assert isinstance(window(hours=1)["hours"], float) and config()["hours"] == 24
    print("[self-check] chat_digest.window OK - inclusive 24h window, 23:59 in / 24:01 out, timezone label")
