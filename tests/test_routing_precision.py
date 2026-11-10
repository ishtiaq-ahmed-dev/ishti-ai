"""Routing precision regressions from the offline audit (2026-11): the normalizer may fix typos but never rewrite a real
word, and the cue layer must not claim requests that merely contain one of its words."""
import pytest

from ishti.normalizer import normalize_message


@pytest.mark.parametrize("text", ["whether I should go", "drink water", "calculate a 15 percent tip", "sign the contract",
                                  "browse the web", "I was reminded", "leather jacket", "a feather"])
def test_real_words_survive_normalisation(text):
    assert normalize_message(text) == text


@pytest.mark.parametrize("typo,fixed", [("wats the wether", "weather"), ("my downlaods folder", "downloads"),
                                        ("open whatsap", "whatsapp"), ("calender today", "calendar")])
def test_typos_are_still_fixed(typo, fixed):
    assert fixed in normalize_message(typo)


@pytest.mark.parametrize("text,not_skill", [
    ("what was the plot of Inception?", "chart"), ("I spent the whole day coding", "budget"),
    (r"summarize C:\Users\PC\report.pdf", "folders"), ("open the task manager", "relationships"),
    ("meeting on 2026-10-01", "calc"), ("best friend of Harry Potter", "relationships"),
    ("whether I should go out", "weather")])
def test_cues_do_not_claim_lookalikes(text, not_skill):
    from ishti import dispatcher, tool_routing
    assert tool_routing.route(text, dispatcher.SKILLS).get("skill") != not_skill


@pytest.mark.parametrize("text,skill", [("I spent 500 on lunch", "budget"), (r"list C:\Projects", "folders"),
                                        ("my brother is Ali", "relationships"), ("cheap flights to Dubai", "web_search"),
                                        ("plot my monthly expenses", "chart"), ("5-3", "calc")])
def test_cues_still_claim_the_real_thing(text, skill):
    from ishti import dispatcher, tool_routing
    assert tool_routing.route(text, dispatcher.SKILLS).get("skill") == skill


@pytest.mark.parametrize("text", ["write a haiku about Peshawar rain", "write a story where a robot sets an alarm",
                                  "what is the history of the calendar", "my manager says I talk too fast, any tips?",
                                  "how do flights stay in the air", "how do I say schedule in Urdu",
                                  "what does a 5 percent interest rate mean"])
def test_creative_definition_and_advice_asks_are_conversation(text):
    from ishti import dispatcher, tool_routing
    assert tool_routing.route(text, dispatcher.SKILLS).get("skill") == "chat"


@pytest.mark.parametrize("text,skill", [("is it cold outside", "weather"), ("will it rain tomorrow", "weather"),
                                        ("Show evening flight times to Karachi", "web_search"),
                                        ("what are the 3 newest files in my downloads", "folders"),
                                        ("where is my order", "email"), ("who is my brother", "relationships")])
def test_real_tasks_still_route(text, skill):
    from ishti import dispatcher, tool_routing
    assert tool_routing.route(text, dispatcher.SKILLS).get("skill") == skill


def test_vault_refuses_a_preposition_as_a_password():
    from ishti.skills.security import skill as security
    m = next(rx.search(t) for n, rx in security.OPS for t in ["remember that my wifi password is on the fridge"]
             if n == "vault_remember" and rx.search(t))
    assert security.op_vault_remember(m, "remember that my wifi password is on the fridge", {}) is None
