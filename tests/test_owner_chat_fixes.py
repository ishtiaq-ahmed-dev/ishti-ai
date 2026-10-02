"""Regressions from the owner's real chats: WhatsApp opens once and a new command is never read as a contact name, pronouns resolve
to the last chat, spoken replies drop URLs and stay short, a hypothetical sum is arithmetic (not the expense log)."""
from ishti import router
from ishti.skills.calc import skill as calc
from ishti.skills.whatsapp import skill as wa
from ishti.talk import Sentences, speakable


def test_new_command_is_not_a_contact_answer():
    assert wa.NEW_COMMAND.match("Open WhatsApp Web.") and wa.NEW_COMMAND.match("and send him hi")
    assert not wa.NEW_COMMAND.match("Bhai") and not wa.NEW_COMMAND.match("1")


def test_open_whatsapp_is_one_window_request():
    assert wa.OPEN_ONLY.match("Open WhatsApp Web.").group("kind") == "Web"
    assert wa.OPEN_ONLY.match("open whatsapp").group("kind") is None
    assert not wa.OPEN_ONLY.match("open whatsapp and send hi to ali")


def test_pronoun_resolves_to_last_chat():
    s = {"whatsapp_last": "Ali"}
    assert wa.resolve_contact("him", s) == "Ali" and wa.resolve_contact("and", s) == "Ali"
    assert wa.PRONOUN_SEND.search("And send him hi") and not wa.PRONOUN_SEND.search("send hi to ali")


def test_apps_skill_leaves_whatsapp_and_multistep_tasks_alone():
    from ishti.skills.apps import skill as apps
    assert not apps.match("open whatsapp") and not apps.match("open settings and turn on dark mode")
    assert apps.match("open notepad")


def test_spoken_reply_is_short_and_clean():
    assert speakable("**Top 3** see https://example.com/a?b=1 now") == "Top 3 see now"
    got = []
    Sentences(type("T", (), {"say": lambda self, s: got.append(s)})()).flush("One. Two. Three. Four. Five. Six.")
    assert got == ["One.", "Two.", "Three.", "Four.", "The rest is on your screen."]


def test_hypothetical_sum_is_calc_not_ledger():
    q = "if I spend 250 on groceries and 175 on fuel, how much did I spend"
    assert calc.run(q, {})["reply"] == "250 + 175 = 425"
    from ishti.skills.ledger import skill as ledger
    assert not ledger.match(q) and ledger.match("how much did I spend this month")
    assert calc.run("if I buy 3 apples at 2 and 4 pears at 1.5, what is the total", {}) is None
