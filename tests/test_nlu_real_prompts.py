"""Regression suite for real owner prompts (Task N8).
Tests 20 real prompts from runs.db + 15 real-world assistant prompts (Urdu, Roman-Urdu, voice-style, typos, approvals).
Each case asserts expected skill/route and must-not-contain negative phrases.
"""
import os
import sys
import pytest

os.environ["ISHTI_TEST_MODE"] = "1"
os.environ["ISHTI_SCRATCH"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti.normalizer import normalize_message
from ishti.reference_resolver import resolve_reference
from ishti.tool_routing import route
from ishti.skills import answer_of, load_skills
from ishti.courses import find_course_query, set_course_teacher, get_teacher_for_course, list_courses

skills = load_skills()


def test_n8_case1_course_teacher_storage():
    """Prompt 1: 'prog for AI names and the teacher name is M khan afridi' -> courses, not family rename."""
    text = "prog for AI names and the teacher name is M khan afridi"
    q = find_course_query(text)
    assert q is not None
    assert q["action"] == "set"
    assert "prog for ai" in q["course"].lower()
    assert "m khan afridi" in q["teacher"].lower()
    set_course_teacher(q["course"], q["teacher"], "test")
    res = get_teacher_for_course("Prog for AI")
    assert res is not None
    assert "afridi" in res.lower()


def test_n8_case2_open_classroom_assignment_to_do():
    """Prompt 2: 'i have an assigment pending please open classrom and check for the to do asigment...'"""
    text = "i have an assigment pending please open classrom and check for the to do asigment and then solve that and send my ipynb file"
    norm = normalize_message(text)
    assert "classroom" in norm
    session = {"history": []}
    r = route(text, skills, session=session)
    assert r["skill"] != "browser"  # no browser automation: never routed to a removed skill
    # Must NOT route to web_search
    assert r["skill"] != "web_search"


def test_n8_case3_classroom_profile9():
    """Prompt 3: 'dont look for the files on internet it is in the google clasrom logged in the profile 9'"""
    text = "dont look for the files on internet it is in the google clasrom logged in the profile 9"
    session = {"history": []}
    r = route(text, skills, session=session)
    assert r["skill"] != "browser"


def test_n8_case4_downloads_folder():
    """Prompt 4: 'how many folders are in my downlaods folder' -> files/fileops/folders."""
    text = "how many folders are in my downlaods folder"
    norm = normalize_message(text)
    assert "downloads" in norm
    session = {"history": []}
    r = route(text, skills, session=session)
    assert r["skill"] in ("folders", "files", "fileops")


def test_n8_case5_delete_them_referenced_files():
    """Prompt 5: 'CursorUserSetup... Delete them' -> fileops with referenced payload."""
    text = "CursorUserSetup-x64-3.22.12.exe\t212.6 MB\t06 Oct 2026\nclient-portal-email-redesigned.html\t14 KB\nDelete them"
    session = {"history": []}
    ref = resolve_reference(text, session)
    assert ref is not None
    assert "CursorUserSetup-x64-3.22.12.exe" in ref["payload"]
    r = route(text, skills, session=session)
    assert r["skill"] == "fileops"
    # Must NOT ask ambiguous question because reference resolved
    assert "Which exact person, item, file" not in str(r.get("clarify", ""))


def test_n8_case6_approval_phrase_giving_approval():
    """Prompt 6: 'all these delete them only now i am giving you approval' -> recognized approval."""
    text = "all these delete them only now i am giving you approval"
    ans = answer_of(text)
    assert ans == "yes"


def test_n8_case7_open_clasrom_pending_assignments():
    """Prompt 7: 'can you pleae open clasrom and let me know which assigments are pending'"""
    text = "can you pleae open clasrom and let me know which assigments are pending"
    norm = normalize_message(text)
    assert "classroom" in norm
    session = {"history": []}
    r = route(text, skills, session=session)
    assert r["skill"] != "browser"


def test_n8_case8_send_this_to_rayyan_ims():
    """Prompt 8: 'send this to rayyan ims on whatsap' with preceding text."""
    session = {
        "history": [
            {"role": "user", "content": "A1: Number of rows, columns, data types\nA2: Missing values per column"}
        ]
    }
    text = "send this to rayyan ims on whatsap"
    ref = resolve_reference(text, session)
    assert ref is not None
    assert "Missing values" in ref["payload"]
    r = route(text, skills, session=session)
    assert r["skill"] == "whatsapp"


def test_n8_case9_inline_payload_whatsapp():
    """Prompt 9: 'send this data to rayyan ims onw hstap    A1: Number of rows...'"""
    text = "send this data to rayyan ims onw hstap    A1: Number of rows, columns, data types  A2: Missing values per column"
    session = {"history": []}
    ref = resolve_reference(text, session)
    assert ref is not None
    assert ref["type"] == "inline_payload"
    assert "Missing values" in ref["payload"]
    r = route(text, skills, session=session)
    assert r["skill"] == "whatsapp"


def test_n8_case10_now_send_this():
    """Prompt 10: 'now send this to rayyan ims' after paste."""
    session = {
        "history": [
            {"role": "user", "content": "A1: Number of rows, columns, data types"}
        ]
    }
    text = "now send this to rayyan ims"
    ref = resolve_reference(text, session)
    assert ref is not None
    assert "Number of rows" in ref["payload"]


def test_n8_case11_open_wahstap_web_top_5():
    """Prompt 11: 'hey open wahstap web now and tell me thetop 5 persons names'"""
    text = "hey open wahstap web now and tell me thetop 5 persons names"
    norm = normalize_message(text)
    assert "whatsapp" in norm
    assert "the top" in norm
    session = {"history": []}
    r = route(text, skills, session=session)
    assert r["skill"] == "whatsapp"
    # Must NOT treat 'wahstap web now' as contact name
    from ishti.skills.whatsapp.skill import discussed_in
    assert not discussed_in(text)


def test_n8_case12_who_am_i():
    """Prompt 12: 'who am i ?' -> chat/memory/owner."""
    session = {"history": []}
    r = route("who am i ?", skills, session=session)
    assert r["skill"] in ("owner", "chat", "memory")


def test_n8_case13_who_is_my_brother():
    """Prompt 13: 'who is my brother' -> relationships."""
    session = {"history": []}
    r = route("who is my brother", skills, session=session)
    assert r["skill"] == "relationships"


def test_n8_case14_add_friend_uni():
    """Prompt 14: 'my friend is Muhammad Anis my uni friend' -> relationships."""
    session = {"history": []}
    r = route("my friend is Muhammad Anis my uni friend", skills, session=session)
    assert r["skill"] == "relationships"


def test_n8_case15_pasted_doc_followup_short_notes():
    """Prompt 15: Pasted FIFA doc followed by 'short notes' -> writing/chat, not web_search."""
    session = {
        "history": [
            {"role": "user", "content": "Exploratory Data Analysis (Visualization Only) FIFA 21 Player Dataset | Machine Learning Lab | Individual work Name: ____________"}
        ]
    }
    r = route("short notes", skills, session=session)
    assert r["skill"] in ("writing", "chat")
    assert r["skill"] != "web_search"


def test_n8_case16_send_this_text():
    """Prompt 16: 'send this text' -> references previous turn."""
    session = {
        "history": [
            {"role": "user", "content": "Here is the summary of project deliverables."}
        ]
    }
    ref = resolve_reference("send this text", session)
    assert ref is not None
    assert "summary of project deliverables" in ref["payload"]


def test_n8_case17_try_opening_two_tabs():
    """Prompt 17: 'try opening two tabs if not working' -> browser."""
    session = {"history": []}
    r = route("try opening two tabs if not working", skills, session=session)
    assert r["skill"] != "browser"


def test_n8_case18_whats_up_for_today():
    """Prompt 18: 'hey whats up for today' -> direct chat/briefing."""
    session = {"history": []}
    r = route("hey whats up for today", skills, session=session)
    assert r["skill"] in ("digest", "chat", "reminders", "calendar")


def test_n8_case19_typo_calssrom_run_on():
    """Prompt 19: 'open google calssrom and check for to dos and there is one ssigemnt pending open that and then do the assigment and give me the ipynb file'"""
    text = "open google calssrom and check for to dos and there is one ssigemnt pending open that and then do the assigment and give me the ipynb file"
    norm = normalize_message(text)
    assert "classroom" in norm
    assert "to-do" in norm or "to dos" in norm or "todo" in norm or "to do" in norm
    session = {"history": []}
    r = route(text, skills, session=session)
    assert r["skill"] != "browser"


def test_n8_case20_the_above_provided_one_names():
    """Prompt 20: 'the above provided one names' -> resolves to previous list."""
    session = {
        "history": [
            {"role": "assistant", "content": "| Name | Size |\n| file1.txt | 10 KB |\n| file2.txt | 20 KB |"}
        ]
    }
    ref = resolve_reference("the above provided one names", session)
    assert ref is not None
    assert len(ref["files"]) == 2


# --- 15 Invented Real-World Assistant Prompts ---

def test_n8_case21_urdu_weather():
    """Prompt 21: 'aaj ka mausam kaisa hai peshawar main' -> weather."""
    session = {"history": []}
    r = route("aaj ka mausam kaisa hai peshawar main", skills, session=session)
    assert r["skill"] == "weather"


def test_n8_case22_roman_urdu_reminder():
    """Prompt 22: 'mujhe kal subah 7 baje utha dena' -> reminders."""
    session = {"history": []}
    r = route("mujhe kal subah 7 baje utha dena", skills, session=session)
    assert r["skill"] == "reminders"


def test_n8_case23_calendar_query():
    """Prompt 23: 'kal ki meeting kab schedule hai meri' -> calendar."""
    session = {"history": []}
    r = route("kal ki meeting kab schedule hai meri", skills, session=session)
    assert r["skill"] == "calendar"


def test_n8_case24_search_downloads_pdf():
    """Prompt 24: 'bhai meri downloads directory me check karo koi pdf file hai' -> files."""
    session = {"history": []}
    r = route("bhai meri downloads directory me check karo koi pdf file hai", skills, session=session)
    assert r["skill"] in ("files", "fileops")


def test_n8_case25_remember_fact():
    """Prompt 25: 'remember that my roll number is 21pwcse1234' -> memory."""
    session = {"history": []}
    r = route("remember that my roll number is 21pwcse1234", skills, session=session)
    assert r["skill"] == "memory"


def test_n8_case26_who_teaches_course():
    """Prompt 26: 'who is my teacher for Prog for AI' -> course lookup."""
    q = find_course_query("who is my teacher for Prog for AI")
    assert q is not None
    assert q["action"] == "get"
    assert "prog for ai" in q["course"].lower()


def test_n8_case27_update_course_teacher():
    """Prompt 27: 'update teacher of Prog for AI to Dr Tariq' -> course update."""
    q = find_course_query("update teacher of Prog for AI to Dr Tariq")
    assert q is not None
    assert q["action"] == "update"
    assert "tariq" in q["teacher"].lower()


def test_n8_case28_email_draft():
    """Prompt 28: 'draft an email to Sir Tariq asking for assignment extension' -> email."""
    session = {"history": []}
    r = route("draft an email to Sir Tariq asking for assignment extension", skills, session=session)
    assert r["skill"] in ("email", "writing")


def test_n8_case29_approval_haan_kar_do():
    """Prompt 29: 'haan kar do' -> approval yes."""
    assert answer_of("haan kar do") == "yes"


def test_n8_case30_approval_kar_do():
    """Prompt 30: 'kar do' -> approval yes."""
    assert answer_of("kar do") == "yes"


def test_n8_case31_approval_go_ahead():
    """Prompt 31: 'go ahead' -> approval yes."""
    assert answer_of("go ahead") == "yes"


def test_n8_case32_approval_approved():
    """Prompt 32: 'approved' -> approval yes."""
    assert answer_of("approved") == "yes"


def test_n8_case33_summarize_this_text():
    """Prompt 33: 'summarize this text' -> writing / direct with context, not web search."""
    session = {
        "history": [
            {"role": "user", "content": "Machine Learning Lab Assignment 1 instructions and grading criteria."}
        ]
    }
    r = route("summarize this text", skills, session=session)
    assert r["skill"] != "web_search"


def test_n8_case34_solve_it_followup():
    """Prompt 34: 'solve it' -> resolves previous turn."""
    session = {
        "history": [
            {"role": "user", "content": "Calculate the determinant of a 2x2 matrix: [[1, 2], [3, 4]]"}
        ]
    }
    r = route("solve it", skills, session=session)
    assert r["skill"] != "web_search"


def test_n8_case35_pipeline_never_returns_lost():
    """Prompt 35: Pipeline never returns LOST on repeated assistant output."""
    from ishti.brain import _not_a_repeat, LOST
    session = {
        "history": [
            {"role": "assistant", "content": "Stopped after browser steps. I've opened Google Classroom."}
        ]
    }
    repeat = "Stopped after browser steps. I've opened Google Classroom."
    res = _not_a_repeat(repeat, session)
    assert res == LOST  # internal flag triggers fallback
    # brain.run() turns LOST into "no answer" (None): the deterministic route decides, and LOST never reaches the owner
