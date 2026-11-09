"""The brief reads the owner's real sources: Google Calendar events (read-only) merged with the local calendar,
unread Gmail, and jobs waiting for a yes. Faked APIs, no network."""
import datetime as dt

from ishti.skills.digest import skill as digest


def test_calendar_merges_google_and_local_without_duplicates(monkeypatch):
    from ishti import oauth
    from ishti.skills.calendar import skill as cal
    now = dt.datetime.combine(dt.date.today(), dt.time(15, 0))
    monkeypatch.setattr(cal, "occurrences", lambda a, b: [(now, now, "Dentist", "", 1)])
    monkeypatch.setattr(oauth, "connected", lambda s: s == "calendar")
    local_iso = now.astimezone().isoformat()
    monkeypatch.setattr(oauth, "api", lambda *a, **k: {"items": [
        {"summary": "Dentist", "start": {"dateTime": local_iso}},            # same as the local one
        {"summary": "Team standup", "start": {"dateTime": now.replace(hour=9).astimezone().isoformat()}},
        {"summary": "Eid holiday", "start": {"date": dt.date.today().isoformat()}}]})
    assert digest._calendar().splitlines() == ["- all day Eid holiday", "- 09:00 Team standup", "- 15:00 Dentist"]


def test_email_section_only_when_gmail_is_connected(monkeypatch):
    from ishti import gmail_api
    monkeypatch.setattr(gmail_api, "available", lambda service="gmail": False)
    assert digest._email() == ""
    monkeypatch.setattr(gmail_api, "available", lambda service="gmail": True)
    monkeypatch.setattr(gmail_api, "unread", lambda limit=25: [{"from": '"Sara Khan" <sara@example.com>', "subject": "Invoice"}])
    assert digest._email() == "- Sara Khan: Invoice"
