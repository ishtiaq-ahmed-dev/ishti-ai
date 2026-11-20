"""Reminder edge cases from the skill review: a zero interval can't freeze the app, "I did it" closes nothing it
doesn't name, and a bare "stop the alarm" asks which one. Scratch facts store (conftest)."""
import datetime as dt
import time

from ishti.facts import get_facts
from ishti.skills.reminders import skill as reminders


def _add(text, due):
    reminders._db()
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("INSERT INTO reminders(due, text, created) VALUES(?,?,?)", (due, text, time.time()))


def test_zero_interval_is_clamped_and_cannot_spin():
    assert reminders.repeat_of("remind me every 0 minutes to drink water") == "every:60"
    start, now = time.monotonic(), time.time()
    nxt = reminders.next_due(now - 86400, "every:0", now)  # a stored zero from before the guard
    # compared with the same `now`: the next tick can be milliseconds away, so a later time.time() raced it
    assert nxt > now and time.monotonic() - start < 1


def test_pronoun_done_closes_nothing_and_whole_words_only():
    for text in ("submit the report", "visit the doctor", "call mom"):
        _add(text, time.time() + 3600)
    assert reminders._done_rows("I did it") == []
    assert reminders._done_rows("mark call mom as done") and not reminders._done_rows("mark car as done")


def test_bare_cancel_asks_which_one():
    _add("call the bank", time.time() + 600)
    out = reminders.run("stop the alarm", {})
    assert out and "Which one?" in out["reply"]
    assert any("call the bank" in r[2] for r in reminders._pending(reminders._db()))


def test_skill_review_regressions_calc_notes_email_habits():
    from ishti.skills.calc import skill as calc
    from ishti.skills.habits.skill import TRACK_SHORT
    from ishti.skills.notes import skill as notes
    assert "doesn't look like a maths" in calc.run("simplify __import__('os').getpid()", {})["reply"]
    assert not TRACK_SHORT.match("track my order") and TRACK_SHORT.match("track water")
    notes.run("note bank card is in the drawer", {})
    assert "nothing deleted" in notes.run("delete the note about the car", {})["reply"]


def test_dates_weekdays_and_compact_durations():
    now = dt.datetime(2026, 11, 9, 14, 0)  # a Monday
    assert reminders.when("remind me next Friday at 5pm to call Ali", now) == dt.datetime(2026, 11, 13, 17, 0)
    assert reminders.when("remind me in 2 days at 9am to pay rent", now) == dt.datetime(2026, 11, 11, 9, 0)
    assert reminders.when("remind me in 1h30m to stretch", now) == dt.datetime(2026, 11, 9, 15, 30)
    assert reminders.what("remind me next Friday at 5pm to call Ali") == "call Ali"


def test_money_and_city_parsing():
    from ishti.skills.utilities.skill import OPS
    from tools.web.weather import extract_city
    money = dict(OPS)["money"]
    assert money.search("$100 to pkr") and money.search("convert Rs 5000 to dollars") and not money.search("100 to pkr")
    assert extract_city("weather in lahore") == "Lahore" and extract_city("prayer times in Lahore tomorrow") == "Lahore"
    assert extract_city("karachi mein mausam") == "Karachi" and extract_city("weather in the morning") is None


def test_wake_phrase_ignores_the_owners_own_name_and_stt_never_auto_downloads(monkeypatch):
    from ishti import voice
    from ishti.wake import phrase_in
    assert phrase_in("hey ishtiaq how are you") is None and phrase_in("hey ishty") == "hey ishti"
    monkeypatch.setitem(voice._cfg, "whisper_model", r"Z:\no\such\model")
    monkeypatch.setitem(voice._cfg, "whisper_model_fallback", "")
    import pytest
    with pytest.raises(RuntimeError, match="isn't installed"):
        voice.model_source()


def test_live_captions_follow_the_voice(monkeypatch):
    from ishti import talk
    sent = []
    monkeypatch.setattr(talk.Talker, "say", lambda self, text: None)  # no TTS engine in a unit test
    t = talk.RemoteTalker(sent.append)
    t.say("The capital of France is Paris.")
    t.close()
    assert sent[0]["type"] == "caption" and "Paris" in sent[0]["data"]["text"]
