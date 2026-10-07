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


def test_kin_statements_and_questions_in_any_case():
    from ishti.dispatcher import _calm
    from ishti.skills.relationships import skill as rel
    assert rel._kin_name(_calm("MY BROTHER IS IRFAN AHMED"))["name"].lower() == "irfan ahmed"
    assert rel.match(_calm("Hey who is my brother?")) and not rel.match("my brother is sick")


def test_todo_said_in_passing_goes_to_tasks():
    from ishti.skills.reminders import skill as rem
    assert rem.todo("di have to submit the asisgmeny as well")["what"].startswith("submit")
    assert rem.todo("don't let me forget to pay the electricity bill")["what"] == "pay the electricity bill"
    assert not rem.todo("I have to say, this is nice") and not rem.todo("do I have to pay?")
    assert rem.match("in 2 minutes remind me to drink water") and rem.LIST.match("what's on my list")
    assert rem.CANCEL_ABOUT.match("cancel the reminder to drink water")


def test_echo_of_my_own_status_lines_is_ignored():
    from ishti.dispatcher import ECHO
    assert ECHO.search("Found three sources, opening the top three.") and ECHO.search("Searching the internet for-")
    assert not ECHO.search("search the internet for pakistan news") and not ECHO.search("I found two sources of income")


def test_timeless_question_is_chat_but_dated_one_is_not():
    from ishti.skills.chat import skill as chat
    assert chat.match("What is the capital of Pakistan?") and not chat.match("how many days until eid")
    assert chat.DID_YOU.match("Did you send the message to ishtiaq?") and chat.DID_YOU.match("DID YOU EDITED IT ?")


def test_arch2_action_claim_without_a_tool_is_caught():
    from ishti.dispatcher import ACTION_CLAIM
    assert ACTION_CLAIM.search("- Reminder set: **Drink water** scheduled for Sat")
    assert not ACTION_CLAIM.search("The capital of Pakistan is Islamabad.")


def test_brave_and_installed_apps():
    from ishti.skills.apps import skill as apps
    m = apps.IN_BROWSER.match("Open ssr movies in brawe browser and search for tenet movie")
    assert m["browser"].lower() == "brawe" and apps.browser_url(m["what"], m["q"]).endswith("q=ssr+movies+tenet+movie")


def test_only_owner_words_come_back_from_semantic_recall(monkeypatch):
    import tempfile
    from ishti import semantic

    monkeypatch.setattr(semantic, "embed", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    s = semantic.Semantic(tempfile.mkdtemp())
    s.add("User: my brother is Irfan\nISHTI: Noted: your brother is Ishaq.")
    assert all("ISHTI:" not in h for h in s.recall("who is my brother", max_distance=2.0))


def test_hypothetical_sum_is_calc_not_ledger():
    q = "if I spend 250 on groceries and 175 on fuel, how much did I spend"
    assert calc.run(q, {})["reply"] == "250 + 175 = 425"
    from ishti.skills.ledger import skill as ledger
    assert not ledger.match(q) and ledger.match("how much did I spend this month")
    assert calc.run("if I buy 3 apples at 2 and 4 pears at 1.5, what is the total", {}) is None
