"""FIX-13 regression: a confused translator must never replace a real skill reply.

Observed live (owner's language lock = Roman Urdu): the translator model sometimes
ignores the payload and answers "Please provide the Urdu/Hindi text you want me to
translate..." - and that question USED TO REPLACE the notes skill's correct reply,
so the owner saw a request for input instead of their saved note. translate() must
retry once and fall back to the original text when the model keeps stalling.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _fake_llm(responses):
    """llm.ask returns a plain string (dispatcher.translate strips it directly)."""
    calls = []

    def ask(prompt, **kw):
        calls.append(prompt)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    return ask, calls


def test_translate_returns_good_translation(monkeypatch):
    from ishti import dispatcher
    ask, calls = _fake_llm(["آپ کے نوٹس محفوظ ہیں"])
    monkeypatch.setattr("ishti.llm.ask", ask)
    out = dispatcher.translate("Your notes are saved.", "Urdu")
    assert out == "آپ کے نوٹس محفوظ ہیں"
    assert len(calls) == 1


def test_translate_retries_then_falls_back(monkeypatch):
    from ishti import dispatcher
    stall = "Please provide the Urdu/Hindi text you want me to translate. I need the original text."
    ask, calls = _fake_llm([stall, stall])
    monkeypatch.setattr("ishti.llm.ask", ask)
    original = "Noted: buy contact lenses (saved)."
    out = dispatcher.translate(original, "Urdu")
    assert out == original, f"must fall back to the original, got {out!r}"
    assert len(calls) == 2, "exactly one retry before the fallback"


def test_translate_first_try_wins_when_second_would_be_worse(monkeypatch):
    from ishti import dispatcher
    stall = "Please provide the text to translate."
    good = "محفوظ ہو گیا"
    ask, calls = _fake_llm([good, stall])
    monkeypatch.setattr("ishti.llm.ask", ask)
    out = dispatcher.translate("Saved.", "Urdu")
    assert out == good
    assert len(calls) == 1


def test_empty_translator_output_falls_back(monkeypatch):
    from ishti import dispatcher
    ask, _ = _fake_llm(["", ""])
    monkeypatch.setattr("ishti.llm.ask", ask)
    original = "Plain answer."
    assert dispatcher.translate(original, "Urdu") == original
