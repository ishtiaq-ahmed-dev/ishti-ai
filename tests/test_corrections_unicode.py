"""Routing corrections preserve multilingual identity without migrating owner data."""
import json

import pytest

from ishti import corrections


@pytest.mark.parametrize("text", ["موسم بتاؤ", "زما یادښتونه وښایه", "打开浏览器", "café"])
def test_unicode_correction_is_recalled_only_for_matching_request(text):
    corrections.record(text, "chat", "browser")
    assert corrections.norm(text)
    assert corrections.learned(text) == "browser"
    assert corrections.learned("بالکل الگ سوال") is None


def test_unicode_normalization_equivalent_text():
    corrections.record("ＣＡＦÉ open", "chat", "browser")
    assert corrections.learned("Cafe\u0301 OPEN!") == "browser"


def test_legacy_empty_keys_are_rebuilt_from_original_requests_without_mutation():
    facts = corrections._db()
    with facts.lock, facts.db:
        facts.db.executemany("INSERT INTO corrections VALUES(?,?,?,?,?)", [
            (1, "موسم بتاؤ", "", "chat", "weather"),
            (2, "زما یادښتونه وښایه", "", "chat", "notes"),
        ])
    assert corrections.learned("موسم بتاؤ") == "weather"
    assert corrections.learned("زما یادښتونه وښایه") == "notes"
    assert corrections.learned("غیر متعلق سوال") is None
    assert [row[0] for row in facts.db.execute("SELECT norm FROM corrections")] == ["", ""]


def test_punctuation_never_becomes_a_correction():
    facts = corrections._db()
    with facts.lock, facts.db:
        facts.db.execute("INSERT INTO corrections VALUES(?,?,?,?,?)", (1, "!!!", "", "chat", "browser"))
    corrections.record("???", "chat", "weather")
    assert corrections.learned("...") is None
    assert facts.db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1


def test_exact_tie_abstains_and_unique_plurality_is_stable():
    corrections.record("open my project", "chat", "browser")
    corrections.record("open my project", "chat", "files")
    assert corrections.learned("OPEN MY PROJECT!") is None
    corrections.record("open my project", "chat", "files")
    assert corrections.learned("open my project") == "files"


def test_similar_requests_still_require_two_votes_and_strict_majority():
    rows = [("open the imsciences portal and check my attendance", "browser")]
    query = "open the imsciences portal and check my attendence"
    assert corrections.learned_from_rows(query, rows) is None
    rows.append(("open the imsciences portal and check my attendance today", "browser"))
    assert corrections.learned_from_rows(query, rows) == "browser"
    rows.extend([
        ("open the imsciences portal and check my attendence now", "web_fetch"),
        ("open the imsciences portal and check my attendance please", "web_fetch"),
    ])
    assert corrections.learned_from_rows(query, rows) is None


def test_export_does_not_merge_legacy_unicode_requests(tmp_path, monkeypatch):
    facts = corrections._db()
    with facts.lock, facts.db:
        facts.db.executemany("INSERT INTO corrections VALUES(?,?,?,?,?)", [
            (1, "موسم بتاؤ", "", "chat", "browser"),
            (2, "زما یادښتونه وښایه", "", "chat", "browser"),
            (3, "موسم بتاؤ", "", "chat", "browser"),
        ])
    monkeypatch.setattr(corrections, "EXPORT", str(tmp_path / "corrections.jsonl"))
    path, count = corrections.export()
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert count == 2
    assert {row["request"]: row["count"] for row in rows} == {"موسم بتاؤ": 2, "زما یادښتونه وښایه": 1}
